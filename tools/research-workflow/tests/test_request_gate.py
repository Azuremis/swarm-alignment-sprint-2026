"""All requests and prices here are scripted; no SDK or credential is required."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from reservations import BudgetBook
from request_gate import RequestGate, RequestStopped, client_class
from test_attempts import reconciliation


def plan(**kwargs):
    return dict(
        run_id="run1",
        model="scripted-model",
        sdk_version="scripted-sdk",
        config_sha256="a" * 64,
        max_requests=3,
        max_output_tokens=256,
        max_request_bytes=8192,
        model_context_tokens=1000,
        maximum_request_usd="0.01",
        input_usd_per_million_upper="1",
        output_usd_per_million_upper="2",
        price_evidence="authored prices, not billing evidence",
        evidence_kind="synthetic_fixture",
        **kwargs,
    )


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 11, 8, 12, 1, tzinfo=timezone.utc)
        self.book = BudgetBook.create(
            self.root / "budget.db", reconciliation(), now=self.now
        )
        self.calls = 0
        self.raw = {
            "model": "scripted-model-v1",
            "status": "completed",
            "usage": {"input_tokens": 20, "output_tokens": 10},
            "output": [],
        }
        self.failure = None
        self.sdk = SimpleNamespace(
            max_retries=0,
            base_url="https://api.openai.com/v1/",
            responses=SimpleNamespace(create=self.respond),
        )
        self.gate = RequestGate(
            sdk=self.sdk,
            book=self.book,
            plan=plan(),
            destination=self.root / "requests",
            clock=lambda: self.now,
        )
        self.params = {
            "model": "scripted-model",
            "input": "authored task",
            "max_output_tokens": 256,
            "tools": [],
        }

    def respond(self, **kwargs):
        self.calls += 1
        self.assertEqual(self.book.inspect()["requests"][-1]["state"], "started")
        self.assertFalse(kwargs["store"])
        self.assertFalse(kwargs["parallel_tool_calls"])
        if self.failure:
            raise self.failure
        return SimpleNamespace(
            model_dump=lambda **kwargs: self.raw, _request_id="scripted-provider-id"
        )

    def test_persisted_start_response_provenance_and_upper_cost(self):
        self.gate.create(self.params)
        row = self.book.inspect()["requests"][0]
        self.assertEqual(row["state"], "settled")
        self.assertEqual(row["attempt"]["cost_usd"], "0.00004")
        self.assertIn("not invoice", row["attempt"]["cost_source"])
        response = next((self.root / "requests").glob("*-response.json"))
        self.assertEqual(
            json.loads(response.read_text())["provider_request_id"],
            "scripted-provider-id",
        )
        self.assertEqual(response.stat().st_mode & 0o777, 0o600)

    def test_timeout_retains_unknown_charge_and_never_retries(self):
        self.failure = TimeoutError("sensitive exception text must not be saved")
        with self.assertRaises(RequestStopped):
            self.gate.create(self.params)
        with self.assertRaises(RequestStopped):
            self.gate.create(self.params)
        self.assertEqual(self.calls, 1)
        row = self.book.inspect()["requests"][0]
        self.assertEqual(row["state"], "uncertain")
        self.assertIsNone(row["attempt"]["cost_usd"])
        self.assertNotIn(
            "sensitive",
            "".join(p.read_text() for p in (self.root / "requests").glob("*.json")),
        )

    def test_missing_usage_and_incomplete_status_block_next_request(self):
        for field, value in [("usage", None), ("status", "incomplete")]:
            with self.subTest(field=field):
                self.setUp()
                self.raw[field] = value
                with self.assertRaises(RequestStopped):
                    self.gate.create(self.params)
                self.assertEqual(
                    self.book.inspect()["requests"][0]["state"], "uncertain"
                )
                with self.assertRaises(RequestStopped):
                    self.gate.create(self.params)
                self.assertEqual(self.calls, 1)

    def test_bound_changes_and_remote_tools_fail_before_dispatch(self):
        for params in [
            dict(self.params, model="other"),
            dict(self.params, max_output_tokens=257),
            dict(self.params, input="x" * 9000),
            dict(self.params, previous_response_id="remote"),
            dict(self.params, tools=[{"type": "web_search"}]),
        ]:
            with self.assertRaises(RequestStopped):
                self.gate.create(params)
        self.assertEqual(self.calls, 0)

    def test_sdk_retries_or_endpoint_change_fail_closed(self):
        self.sdk.max_retries = 2
        with self.assertRaises(RequestStopped):
            self.gate.create(self.params)
        self.sdk.max_retries = 0
        self.sdk.base_url = "https://example.invalid/v1"
        with self.assertRaises(RequestStopped):
            self.gate.create(self.params)
        self.assertEqual(self.calls, 0)

    def test_request_ceiling_survives_new_gate_instance(self):
        for _ in range(3):
            self.gate.create(self.params)
        again = RequestGate(
            sdk=self.sdk,
            book=self.book,
            plan=plan(),
            destination=self.root / "second",
            clock=lambda: self.now,
        )
        with self.assertRaises(RequestStopped):
            again.create(self.params)
        self.assertEqual(self.calls, 3)

    def test_disk_failure_after_response_leaves_uncertain_charge(self):
        import request_gate

        original = request_gate.save_json

        def save(path, value):
            if str(path).endswith("-response.json"):
                raise OSError("disk failure")
            return original(path, value)

        with patch.object(request_gate, "save_json", side_effect=save):
            with self.assertRaises(RequestStopped):
                self.gate.create(self.params)
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.book.inspect()["requests"][0]["state"], "uncertain")

    def test_full_context_reservation_cannot_be_understated(self):
        altered = plan()
        altered["maximum_request_usd"] = "0.00001"
        with self.assertRaises(ValueError):
            RequestGate(
                sdk=self.sdk,
                book=self.book,
                plan=altered,
                destination=self.root / "bad",
            )
        self.assertEqual(self.calls, 0)

    def test_source_constructor_and_retry_loop_are_bypassed(self):
        class Source:
            def __init__(self):
                raise AssertionError("dotenv/SDK construction must not run")

            def _responses_create_with_retries(self, params):
                raise AssertionError("outer retry loop must not run")

        adapter = client_class(Source)(self.gate)
        adapter._responses_create_with_retries(self.params)
        self.assertEqual(self.calls, 1)

    def test_recovery_cannot_expand_or_change_bound_plan(self):
        altered = plan()
        altered["max_requests"] = 4
        with self.assertRaisesRegex(ValueError, "plan changed"):
            RequestGate(
                sdk=self.sdk,
                book=self.book,
                plan=altered,
                destination=self.root / "expanded",
            )
        self.assertEqual(self.calls, 0)

    def test_output_usage_beyond_cap_is_unresolved(self):
        self.raw["usage"]["output_tokens"] = 257
        with self.assertRaises(RequestStopped):
            self.gate.create(self.params)
        self.assertEqual(self.book.inspect()["requests"][0]["state"], "uncertain")
