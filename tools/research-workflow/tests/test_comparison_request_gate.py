"""Comparison budget-policy checks, scripted only."""
from copy import deepcopy
from comparison_request_gate import ComparisonRequestGate
from request_gate import RequestStopped
import test_request_gate as fixtures
from test_request_gate import plan


class ComparisonGateTests(fixtures.GateTests):
    def setUp(self):
        super().setUp()
        self.gate = ComparisonRequestGate(sdk=self.sdk, book=self.book, plan=plan(),
                                         destination=self.root / "comparison", clock=lambda: self.now)
        self.params.update({"input": [{"type": "message", "role": "system", "content": "s"},
                                     {"type": "message", "role": "user", "content": "u"}],
                            "reasoning": {"effort": "low"}, "store": False,
                            "parallel_tool_calls": False, "service_tier": "default",
                            "prompt_cache_options": {"mode": "explicit"}})
        self.params.pop("tools")
        self.raw.update(service_tier="default", model="scripted-model")
        self.raw["usage"]["total_tokens"] = 30
        self.raw["usage"]["input_tokens_details"] = {"cache_write_tokens": 0, "cached_tokens": 0}

    def test_billing_policy_mutations_stop_before_call(self):
        for field, value in [("service_tier", "auto"), ("prompt_cache_options", {"mode": "implicit"}),
                             ("reasoning", {"effort": "none"}), ("store", True), ("tools", [])]:
            changed = deepcopy(self.params)
            changed[field] = value
            with self.assertRaises(RequestStopped):
                self.gate.create(changed)
        changed = deepcopy(self.params)
        changed["input"][0]["prompt_cache_breakpoint"] = True
        with self.assertRaises(RequestStopped):
            self.gate.create(changed)
        self.assertEqual(self.calls, 0)

    def test_missing_or_changed_billing_detail_retains_uncertainty(self):
        for update in [{"model": "unknown"}, {"service_tier": "priority"}, {"usage": {"input_tokens": 20, "output_tokens": 10}},
                       {"usage": {"input_tokens": 20, "output_tokens": 10,
                                  "input_tokens_details": {"cache_write_tokens": 1, "cached_tokens": 0}}}]:
            self.setUp()
            self.raw.update(update)
            with self.assertRaises(RequestStopped):
                self.gate.create(self.params)
            self.assertEqual(self.calls, 1)
            row = self.book.inspect()["requests"][0]
            self.assertEqual(row["state"], "uncertain")
            self.assertIsNone(row["attempt"]["cost_usd"])

    def test_persisted_start_response_provenance_and_upper_cost(self):
        self.gate.create(self.params)
        row = self.book.inspect()["requests"][0]
        self.assertEqual(row["state"], "settled")
        self.assertEqual(row["attempt"]["cost_usd"], "0.00004")

    def test_disk_failure_after_response_leaves_uncertain_charge(self):
        from unittest.mock import patch
        with patch("comparison_request_gate.save_json", side_effect=OSError):
            with self.assertRaises(OSError):
                self.gate.create(self.params)
        self.assertEqual(self.calls, 0)

    def test_request_ceiling_survives_new_gate_instance(self):
        for _ in range(3):
            self.gate.create(self.params)
        again = ComparisonRequestGate(sdk=self.sdk, book=self.book, plan=plan(),
                                      destination=self.root / "second", clock=lambda: self.now)
        with self.assertRaises(RequestStopped):
            again.create(self.params)
        self.assertEqual(self.calls, 3)
