"""Authored responses and accounting only; no provider or real budget is used."""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from reservations import BudgetBook
from request_gate import RequestGate
from verification_phase import prepare_phase, execute_phase, response_text
from verification_turn import api_params
from relay_contract import canonical, receipt_rule


def answer(packet):
    held = [r["id"] for r in packet["receipts"]]
    return canonical(
        {
            "decision": receipt_rule(packet),
            "cited_message_ids": [],
            "cited_receipt_ids": held,
            "forward_receipt_ids": held,
            "message_ascii": "Authored scripted reply; no model was run.",
        }
    )


class PhaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime.now(timezone.utc)
        reconciliation = {
            "provider": "openai",
            "cap_usd": "25",
            "prior_spend_usd": "0",
            "other_reservations_usd": "0",
            "verified_at": self.now.isoformat(),
            "verified_by": "authored offline fixture",
            "billing_evidence_sha256": "a" * 64,
            "ledger_boundary": "synthetic only",
            "all_consumers_reconciled": True,
        }
        self.book = BudgetBook.create(
            self.root / "book.db", reconciliation, now=self.now
        )
        self.count = 0
        self.change = None
        self.failure = None
        self.sdk = SimpleNamespace(
            base_url="https://api.openai.com/v1",
            max_retries=0,
            responses=SimpleNamespace(create=self.respond),
        )

    def prepare(self, phase="controls", kind="synthetic_fixture"):
        self.phase = self.root / "phase"
        plan = prepare_phase(
            self.phase,
            phase=phase,
            run_id="fixture-phase",
            evidence_kind=kind,
            source_revision="scripted-code",
            dependencies={"openai": "scripted-sdk"},
        )
        self.gate = RequestGate(
            sdk=self.sdk, book=self.book, plan=plan, destination=self.phase / "requests"
        )
        return plan

    def respond(self, **params):
        self.count += 1
        rows = self.book.inspect()["requests"]
        self.assertEqual(rows[-1]["state"], "started")
        aid = rows[-1]["attempt_id"]
        bindings = list(self.phase.glob("cell-*-*/attempt-binding.json"))
        self.assertTrue(
            any(json.loads(p.read_text())["attempt_id"] == aid for p in bindings)
        )
        packet = json.loads(params["input"][1]["content"])
        if self.failure:
            raise self.failure
        text = answer(packet)
        if self.change:
            text = self.change(packet, text)
        raw = {
            "status": "completed",
            "model": "scripted-luna",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        return SimpleNamespace(
            model_dump=lambda **kwargs: raw, _request_id=f"fake-{self.count}"
        )

    @staticmethod
    def invoke(packet, boundary):
        response = boundary.create(api_params(packet, boundary.plan))
        return response_text(response.model_dump())

    def test_four_controls_blind_equality_provenance_and_dual_labels(self):
        self.prepare()
        result = execute_phase(self.phase, self.gate, self.invoke)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(self.count, 4)
        self.assertEqual(
            [r["reply"]["decision"] for r in result["records"]],
            ["ABSTAIN", "CLEAR", "ABSTAIN", "ALERT"],
        )
        self.assertEqual(result["records"][0]["packet"], result["records"][2]["packet"])
        self.assertNotEqual(
            result["records"][0]["outcome"]["actual_matches"],
            result["records"][2]["outcome"]["actual_matches"],
        )
        manifest = json.loads((self.phase / "evidence-manifest.json").read_text())
        self.assertIn("attempts.jsonl", manifest)
        self.assertTrue(all((self.phase / name).is_file() for name in manifest))
        self.assertTrue(
            all(
                r["attempt"]["attempt_index"] == 0
                for r in self.book.inspect()["requests"]
            )
        )
        with self.assertRaises(ValueError):
            execute_phase(self.phase, self.gate, self.invoke)
        self.assertEqual(self.count, 4)

    def test_relay_delivers_only_allowed_parents_and_host_proof(self):
        self.prepare("relay")
        result = execute_phase(self.phase, self.gate, self.invoke)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(self.count, 12)
        terminal = [r for r in result["records"] if r["actor"] == "C"]
        self.assertEqual(
            [r["reply"]["decision"] for r in terminal],
            ["CLEAR", "ABSTAIN", "ALERT", "ABSTAIN"],
        )
        self.assertEqual(terminal[1]["packet"], terminal[3]["packet"])
        self.assertEqual([m["id"] for m in terminal[0]["packet"]["messages"]], ["m-B"])
        self.assertEqual([r["id"] for r in terminal[0]["packet"]["receipts"]], ["r1"])
        self.assertEqual(terminal[0]["packet"]["receipts"][0]["capture_tick"], 2)
        self.assertNotIn("actual_matches", canonical(terminal[0]["packet"]))

    def test_invalid_response_halts_and_preserves_every_planned_slot(self):
        self.prepare("relay")
        self.change = lambda packet, text: "not JSON" if self.count == 2 else text
        result = execute_phase(self.phase, self.gate, self.invoke)
        self.assertEqual(self.count, 2)
        self.assertEqual(result["status"], "halted")
        self.assertEqual(len(result["records"]), 12)
        self.assertEqual(
            [r["status"] for r in result["records"][:3]],
            ["valid", "error", "unattempted"],
        )
        self.assertTrue((self.phase / "cell-0-B/raw-result.json").exists())
        self.assertEqual(result["records"][0]["outcome"]["decision"], "ABSTAIN")
        self.assertEqual(result["records"][1]["outcome"]["decision"], "ERROR")
        with self.assertRaises(ValueError):
            execute_phase(self.phase, self.gate, self.invoke)

    def test_unknown_charge_stops_all_later_cells_no_retry(self):
        self.prepare()
        self.failure = TimeoutError("must not copy sensitive failure text")
        result = execute_phase(self.phase, self.gate, self.invoke)
        self.assertEqual(self.count, 1)
        self.assertEqual(result["status"], "halted")
        self.assertEqual(self.book.inspect()["requests"][0]["state"], "uncertain")
        self.assertNotIn(
            "sensitive failure", (self.phase / "phase-result.json").read_text()
        )

    def test_fabricated_adapter_reply_cannot_replace_saved_model_text(self):
        self.prepare()

        def fabricated(packet, boundary):
            self.invoke(packet, boundary)
            value = json.loads(answer(packet))
            value["decision"] = "CLEAR"
            return canonical(value)

        result = execute_phase(self.phase, self.gate, fabricated)
        self.assertEqual(self.count, 1)
        self.assertEqual(result["status"], "halted")
        self.assertFalse(result["completed_slots"])

    def test_exact_request_guard_and_durable_phase_claim_before_dispatch(self):
        self.prepare()

        def changed(packet, boundary):
            params = api_params(packet, boundary.plan)
            params["reasoning"] = {"effort": "low"}
            return boundary.create(params)

        result = execute_phase(self.phase, self.gate, changed)
        self.assertEqual(result["status"], "halted")
        self.assertEqual(self.count, 0)
        clone = self.root / "clone"
        clone.mkdir()
        for p in self.phase.iterdir():
            if p.name in {"config.json", "request-plan.json"}:
                shutil.copy(p, clone / p.name)
            elif p.name in {"cell-0", "cell-1", "cell-2", "cell-3"}:
                (clone / p.name).mkdir()
                for name in ["state.db", "before.json", "before-verification.json"]:
                    shutil.copy(p / name, clone / p.name / name)
        gate = RequestGate(
            sdk=self.sdk,
            book=self.book,
            plan=self.gate.plan,
            destination=clone / "requests",
        )
        with self.assertRaises(FileExistsError):
            execute_phase(clone, gate, self.invoke)
        self.assertEqual(self.count, 0)

    def test_swapped_same_content_capture_rejected_before_request(self):
        self.prepare()
        shutil.copy(
            self.phase / "cell-2/before.json", self.phase / "cell-1/before.json"
        )
        with self.assertRaises(ValueError):
            execute_phase(self.phase, self.gate, self.invoke)
        self.assertEqual(self.count, 0)

    def test_receipt_forwarding_forgery_halts(self):
        self.prepare("relay")

        def change(packet, text):
            row = json.loads(text)
            row["forward_receipt_ids"] = ["r1"]
            return canonical(row)

        self.change = change
        result = execute_phase(self.phase, self.gate, self.invoke)
        self.assertEqual(result["status"], "halted")
        self.assertEqual(self.count, 1)  # A did not hold r1.

    def test_state_mutation_never_becomes_a_resolved_outcome(self):
        self.prepare()

        def mutate(packet, boundary):
            raw = self.invoke(packet, boundary)
            with sqlite3.connect(self.phase / "cell-0/state.db") as db:
                db.execute("UPDATE artifacts SET contents=?", (b"tampered",))
            return raw

        result = execute_phase(self.phase, self.gate, mutate)
        self.assertEqual(result["status"], "halted")
        self.assertEqual(result["records"][0]["capture_status"], "unresolved")
        self.assertIsNone(result["records"][0]["outcome"])

    def test_attempt_export_failure_preserves_halt_and_completed_slots(self):
        self.prepare()
        with patch.object(self.book, "export_attempts", side_effect=OSError):
            result = execute_phase(self.phase, self.gate, self.invoke)
        self.assertEqual(result["attempt_export_error"], "OSError")
        self.assertEqual(len(result["records"]), 4)
        self.assertTrue((self.phase / "finalization-started.json").exists())
        self.assertEqual(self.count, 4)

    def test_recorded_source_requires_pinned_invoker(self):
        self.prepare(kind="recorded_agent_trace")
        with self.assertRaises(ValueError):
            execute_phase(self.phase, self.gate, self.invoke)
        self.assertEqual(self.count, 0)

    def test_binding_failure_cancels_unstarted_attempt(self):
        self.prepare()
        packet = {"x": "authored"}
        with self.assertRaises(OSError):
            self.gate.create(
                {
                    "model": self.gate.plan["model"],
                    "input": canonical(packet),
                    "max_output_tokens": 512,
                },
                before_dispatch=lambda *args: (_ for _ in ()).throw(OSError()),
            )
        self.assertEqual(self.count, 0)
        self.assertEqual(self.book.inspect()["requests"][0]["state"], "cancelled")
