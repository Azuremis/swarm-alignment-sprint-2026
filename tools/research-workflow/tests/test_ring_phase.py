"""Scripted causal ring, immutable state, source audit and spending stops."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from reservations import BudgetBook
from comparison_request_gate import ComparisonRequestGate
from ring_phase import prepare, execute, verify_source, packet_for, ACTORS, slots, specs, phase_commitment
from ring_turn import api_params
from relay_contract import ReceiptRegistry, canonical, receipt_rule, sha
from request_gate import RequestStopped
from verification_phase import response_text


def answer(packet, forward=True):
    held = [r["id"] for r in packet["receipts"]]
    return canonical({"decision": receipt_rule(packet), "cited_message_ids": [],
                      "cited_receipt_ids": held, "forward_receipt_ids": held if forward else [],
                      "message_ascii": "Scripted fixture only; no model ran."})


class RingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        now = datetime.now(timezone.utc)
        self.book = BudgetBook.create(self.root / "book.db", {
            "provider": "openai", "cap_usd": "25", "prior_spend_usd": "0", "other_reservations_usd": "0",
            "verified_at": now.isoformat(), "verified_by": "authored fixture",
            "billing_evidence_sha256": "a" * 64, "ledger_boundary": "synthetic only",
            "all_consumers_reconciled": True}, now=now)
        self.calls, self.change, self.usage = 0, None, (100, 50)
        self.sdk = SimpleNamespace(base_url="https://api.openai.com/v1", max_retries=0,
                                   responses=SimpleNamespace(create=self.respond))
        self.phase = self.root / "phase"
        plan = prepare(self.phase, run_id="ring-fixture", revision="offline-test",
                       dependencies={"openai": "scripted", "inspect-ai": "scripted", "terrarium-agents": "scripted"},
                       evidence_kind="synthetic_fixture")
        self.gate = ComparisonRequestGate(sdk=self.sdk, book=self.book, plan=plan,
                                          destination=self.phase / "requests")

    def respond(self, **params):
        self.calls += 1
        packet = json.loads(params["input"][1]["content"])
        text = answer(packet)
        if self.change:
            text = self.change(packet, text)
        inputs, outputs = self.usage
        raw = {"status": "completed", "model": "gpt-6-luna", "service_tier": "default",
               "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}],
               "usage": {"input_tokens": inputs, "output_tokens": outputs, "total_tokens": inputs+outputs,
                         "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}}
        return SimpleNamespace(model_dump=lambda **kw: raw, _request_id=f"scripted-{self.calls}")

    @staticmethod
    def invoke(packet, boundary):
        return response_text(boundary.create(api_params(packet, boundary.plan)).model_dump())

    def rebind(self):
        paths = [p for p in self.phase.rglob("*") if p.is_file() and p.name != "evidence-manifest.json"]
        (self.phase / "evidence-manifest.json").write_text(json.dumps({str(p.relative_to(self.phase)): sha(p.read_bytes()) for p in paths}))

    def test_round_barrier_two_hop_and_control_blinding(self):
        result = execute(self.phase, self.gate, self.invoke)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(self.calls, 96)
        checked = verify_source(self.phase)
        rows = {r["slot_id"]: r for r in checked["result"]["records"]}
        for c in range(4):
            for r in range(3):
                proof_actors = [a for a in ACTORS if rows[f"cell-{c}-r{r}-{a}"]["packet"]["receipts"]]
                self.assertEqual(proof_actors, list(ACTORS[:r+1]) if c in (0, 2) else ["A0"])
                for a in ACTORS:
                    packet = rows[f"cell-{c}-r{r}-{a}"]["packet"]
                    self.assertEqual(len(packet["messages"]), r * (2 if c in (0, 2) else 1))
                    self.assertNotIn("actual_matches", canonical(packet))
                    self.assertTrue(all(int(m["id"].split("-")[1][1:]) < r for m in packet["messages"]))
                    self.assertTrue(all(x["capture_tick"] == 2 for x in packet["receipts"]))
        for a in ACTORS[1:]:
            self.assertEqual(rows[f"cell-1-r2-{a}"]["packet"], rows[f"cell-3-r2-{a}"]["packet"])
            self.assertNotEqual(rows[f"cell-1-r2-{a}"]["outcome"]["actual_matches"], rows[f"cell-3-r2-{a}"]["outcome"]["actual_matches"])
        with self.assertRaises(ValueError):
            execute(self.phase, self.gate, self.invoke)
        self.assertEqual(self.calls, 96)

    def test_model_choice_not_host_copies_determines_delivery(self):
        self.change = lambda p, t: answer(p, forward=False)
        result = execute(self.phase, self.gate, self.invoke)
        self.assertEqual(result["status"], "complete")
        self.assertTrue(all(not r["packet"]["receipts"] for r in result["records"] if r["actor"] != "A0"))
        verify_source(self.phase)

    def test_invalid_output_halts_retains_full_denominator(self):
        self.change = lambda p, t: "not JSON" if self.calls == 9 else t
        result = execute(self.phase, self.gate, self.invoke)
        self.assertEqual(self.calls, 9)
        self.assertEqual(result["status"], "halted")
        self.assertEqual(len(result["records"]), 96)
        self.assertEqual(result["records"][8]["status"], "error")
        self.assertEqual(result["records"][9]["status"], "unattempted")
        self.assertIsNone(result["records"][8]["outcome"]["false_certification"])
        verify_source(self.phase)

    def test_phase_cap_reserves_full_context_and_never_overspends(self):
        self.usage = (1050000, 512)
        result = execute(self.phase, self.gate, self.invoke)
        self.assertEqual(self.calls, 12)
        self.assertEqual(result["status"], "halted")
        self.assertLessEqual(phase_commitment(self.book.inspect()["requests"]), 3)
        verify_source(self.phase)

    def test_mutation_of_sealed_state_stops_before_next_call(self):
        def mutate(p, t):
            with sqlite3.connect(self.phase / "cell-0/state.db") as db:
                db.execute("UPDATE artifacts SET contents=?", (b"tampered",))
            return t
        self.change = mutate
        result = execute(self.phase, self.gate, self.invoke)
        self.assertEqual(self.calls, 1)
        self.assertEqual(result["status"], "halted")
        self.assertIn("cell-0", result["capture_errors"])
        with self.assertRaises(ValueError):
            verify_source(self.phase)

    def test_audit_rejects_rehashed_parent_output_or_packet_change(self):
        execute(self.phase, self.gate, self.invoke)
        path = self.phase / "cell-0-r2-A2/input.json"
        packet = json.loads(path.read_text())
        packet["messages"][0]["text"] = "invented ancestry"
        path.write_text(json.dumps(packet))
        self.rebind()
        with self.assertRaisesRegex(ValueError, "causal packet"):
            verify_source(self.phase)

    def test_audit_rejects_rehashed_provider_usage_model_cache_or_tier(self):
        execute(self.phase, self.gate, self.invoke)
        binding = json.loads((self.phase / "cell-0-r0-A0/attempt-binding.json").read_text())
        path = self.phase / "requests" / (binding["attempt_id"] + "-response.json")
        original = path.read_text()
        for kind in ("model", "usage", "cache", "tier"):
            with self.subTest(kind=kind):
                envelope = json.loads(original)
                raw = envelope["response"]
                if kind == "model":
                    raw["model"] = "different-model"
                elif kind == "usage":
                    raw["usage"]["input_tokens"] += 100000
                    raw["usage"]["total_tokens"] += 100000
                elif kind == "cache":
                    raw["usage"]["input_tokens_details"]["cache_write_tokens"] = 1
                else:
                    raw["service_tier"] = "priority"
                path.write_text(json.dumps(envelope))
                self.rebind()
                with self.assertRaises(ValueError):
                    verify_source(self.phase)
        path.write_text(original)
        self.rebind()
        verify_source(self.phase)

    def test_audit_rejects_rehashed_phase_budget_check(self):
        execute(self.phase, self.gate, self.invoke)
        path = self.phase / "cell-0-r0-A0/phase-budget-check.json"
        path.write_text(json.dumps({"settled_phase_usd": "999", "next_reservation_usd": "999", "phase_cap_usd": "0.01"}))
        self.rebind()
        with self.assertRaisesRegex(ValueError, "phase budget check"):
            verify_source(self.phase)

    def test_larger_shared_cap_is_not_silently_adopted(self):
        with sqlite3.connect(self.book.path) as db:
            row = json.loads(db.execute("SELECT reconciliation FROM budget").fetchone()[0])
            row["cap_usd"] = "50"
            db.execute("UPDATE budget SET reconciliation=?", (json.dumps(row),))
        with self.assertRaisesRegex(ValueError, "shared budget cap"):
            execute(self.phase, self.gate, self.invoke)
        self.assertEqual(self.calls, 0)

    def test_forged_receipt_forward_is_not_accepted(self):
        self.change = lambda p, t: canonical({**json.loads(t), "forward_receipt_ids": ["invented"]})
        result = execute(self.phase, self.gate, self.invoke)
        self.assertEqual(self.calls, 1)
        self.assertEqual(result["status"], "halted")
        verify_source(self.phase)

    def test_recorded_source_requires_pinned_adapter(self):
        config = json.loads((self.phase / "config.json").read_text())
        config["evidence_kind"] = "recorded_agent_trace"
        from ring_phase import plan_for
        (self.phase / "config.json").write_text(json.dumps(config))
        plan = plan_for(config, sha((self.phase / "config.json").read_bytes()))
        (self.phase / "request-plan.json").write_text(json.dumps(plan))
        self.gate.plan = plan
        with self.assertRaisesRegex(ValueError, "pinned production"):
            execute(self.phase, self.gate, self.invoke)
        self.assertEqual(self.calls, 0)

    def test_unresolved_phase_spend_blocks(self):
        with self.assertRaises(RequestStopped):
            phase_commitment([{"state": "uncertain", "attempt": {"cost_usd": None}}])


class PacketTests(unittest.TestCase):
    def test_maximum_printable_message_packets_fit_request_byte_bound(self):
        from ring_phase import plan_for
        cell = {**specs()[0], "state_run_id": "fixture"}
        registry = ReceiptRegistry({"run_id": "fixture", "decision_tick": 2, "artifacts": {}},
                                   key="artifact", expected_sha256="a"*64)
        plan = plan_for({"run_id": "bounded", "dependencies": {"openai": "scripted"},
                         "cells": [cell], "evidence_kind": "synthetic_fixture"}, "a"*64)
        history, sizes = {}, []
        for slot in slots([cell]):
            packet, _ = packet_for(registry, cell, slot, history)
            reply = json.loads(answer(packet))
            reply["message_ascii"] = '"' * 256
            history[slot["slot_id"]] = (packet, reply)
            sizes.append(len(json.dumps(api_params(packet, plan), sort_keys=True).encode()))
        self.assertLessEqual(max(sizes), 8192)

    def test_actual_peer_text_changes_descendant_context(self):
        cell = {**specs()[0], "state_run_id": "fixture"}
        def run(message):
            registry = ReceiptRegistry({"run_id": "fixture", "decision_tick": 2, "artifacts": {}},
                                       key="artifact", expected_sha256="a"*64)
            history = {}
            for slot in slots([cell]):
                packet, _ = packet_for(registry, cell, slot, history)
                reply = json.loads(answer(packet))
                if slot["round"] == 0 and slot["actor"] == "A0":
                    reply["message_ascii"] = message
                history[slot["slot_id"]] = (packet, reply)
            return history
        left, right = run("first choice"), run("different choice")
        self.assertNotEqual(left["cell-0-r1-A1"][0], right["cell-0-r1-A1"][0])
        self.assertEqual(left["cell-0-r0-A1"][0], right["cell-0-r0-A1"][0])
        changed = deepcopy(left["cell-0-r1-A1"][0])
        self.assertNotIn("state_run_id", changed)
