"""Semantic replay joins and observer separation on authored temporary fixtures."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from verification_inspect import prepare, run_replay, reload
from relay_contract import sha
from observation_masks import DISTINCT, LEGACY
import test_verification_phase as fixtures


class InspectTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PhaseTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare()
        from verification_phase import execute_phase

        execute_phase(self.fixture.phase, self.fixture.gate, self.fixture.invoke)
        self.root = self.fixture.phase
        self.review = {
            "manifest_sha256": "",
            "reviewed_by": "authored offline fixture",
            "scope": "software contracts only",
            "rights": "authored",
        }
        self.rebind()

    def rebind(self):
        paths = [
            p
            for p in self.root.rglob("*")
            if p.is_file() and p.name != "evidence-manifest.json"
        ]
        manifest = {str(p.relative_to(self.root)): sha(p.read_bytes()) for p in paths}
        (self.root / "evidence-manifest.json").write_text(json.dumps(manifest))
        self.review["manifest_sha256"] = sha(
            (self.root / "evidence-manifest.json").read_bytes()
        )

    def test_replay_has_opaque_joins_no_evaluator_metadata_and_error_flow(self):
        samples, records, flow = prepare(self.root, self.review, seed=13)
        self.assertEqual(len(samples), 48)
        self.assertEqual(len(flow["records"]), 4)
        self.assertTrue(all(not s.target and not s.metadata for s in samples))
        self.assertTrue(
            all(
                "actual_matches" not in s.input and "fixture-phase" not in s.input
                for s in samples
            )
        )
        _, other_records, _ = prepare(self.root, self.review, seed=13)
        self.assertFalse(set(records) & set(other_records))
        with tempfile.TemporaryDirectory() as t:
            destination = Path(t) / "replay"
            with (
                patch(
                    "inspect_ai._util.appdirs.user_data_path",
                    side_effect=lambda n: Path(t) / n / "data",
                ),
                patch(
                    "inspect_ai._util.appdirs.user_cache_path",
                    side_effect=lambda n: Path(t) / n / "cache",
                ),
            ):
                rows = run_replay(self.root, self.review, destination)
            self.assertEqual(len(rows), 48)
            path = next((destination / "inspect").glob("*.eval"))
            with self.assertRaises(ValueError):
                reload(path, other_records)

    def test_multiple_phases_share_one_book_without_replay_contamination(self):
        from verification_phase import prepare_phase, execute_phase
        from request_gate import RequestGate

        self.fixture.phase = self.fixture.root / "phase-two"
        plan = prepare_phase(
            self.fixture.phase,
            phase="controls",
            run_id="fixture-two",
            evidence_kind="synthetic_fixture",
            source_revision="scripted-code",
            dependencies={"openai": "scripted-sdk"},
        )
        gate = RequestGate(
            sdk=self.fixture.sdk,
            book=self.fixture.book,
            plan=plan,
            destination=self.fixture.phase / "requests",
        )
        result = execute_phase(self.fixture.phase, gate, self.fixture.invoke)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(self.fixture.book.inspect()["requests"]), 8)
        self.root = self.fixture.phase
        self.rebind()
        samples, _, _ = prepare(self.root, self.review, seed=13)
        self.assertEqual(len(samples), 48)

    def test_failed_inspect_retains_review_inputs_failure_and_manifest(self):
        with tempfile.TemporaryDirectory() as t:
            destination = Path(t) / "failed"
            with patch(
                "verification_inspect.eval",
                side_effect=RuntimeError("authored failure"),
            ):
                with self.assertRaises(RuntimeError):
                    run_replay(self.root, self.review, destination)
            manifest = json.loads((destination / "manifest.json").read_text())
            self.assertEqual(
                json.loads((destination / "failure.json").read_text())["error_type"],
                "RuntimeError",
            )
            self.assertIn("evaluator-records.json", manifest)
            self.assertTrue(
                all(
                    sha((destination / name).read_bytes()) == digest
                    for name, digest in manifest.items()
                )
            )
            self.assertTrue(
                all(
                    (destination / name).stat().st_mode & 0o777 == 0o600
                    for name in manifest
                )
            )

    def test_distinct_policy_preserves_legacy_views_and_retains_ineligibility(self):
        legacy, legacy_records, old_flow = prepare(self.root, self.review, seed=13)
        samples, records, flow = prepare(
            self.root, self.review, seed=13, policy=DISTINCT
        )
        self.assertEqual(len(legacy), 48)
        self.assertEqual(len(samples), 56)
        self.assertNotIn("observation_preflight", old_flow)
        original = {
            (r["slot_id"], r["condition"], r["monitor"]): s.input
            for s, r in zip(legacy, legacy_records.values(), strict=True)
        }
        for sample, record in zip(samples, records.values(), strict=True):
            key = (record["slot_id"], record["condition"], record["monitor"])
            if key in original:
                self.assertEqual(sample.input, original[key])
            self.assertFalse(sample.metadata or sample.target)
            for prohibited in (
                "observation_policy",
                "removed_atoms",
                "distinct_candidate_count",
                "actual_matches",
                "fixture-phase",
                "distinct-conditional-v2",
            ):
                self.assertNotIn(prohibited, sample.input)
        slots = flow["observation_preflight"]["slots"]
        self.assertEqual(len(slots), 4)
        self.assertEqual(
            [s["status"] for s in slots],
            [
                "structurally_ineligible",
                "eligible",
                "structurally_ineligible",
                "eligible",
            ],
        )
        self.assertEqual([s["distinct_candidate_count"] for s in slots], [0, 1, 0, 1])
        self.assertEqual(
            [s["distinct_ineligible_reason"] for s in slots],
            ["no_receipts", None, "no_receipts", None],
        )
        self.assertEqual(
            flow["observation_preflight"]["source_manifest_sha256"],
            self.review["manifest_sha256"],
        )

    def test_distinct_preflight_is_saved_before_eval_even_if_replay_fails(self):
        with tempfile.TemporaryDirectory() as t:
            destination = Path(t) / "failed"

            def fail_after_preflight(*args, **kwargs):
                preflight = json.loads(
                    (destination / "mask-preflight.json").read_text()
                )
                self.assertEqual(preflight["policy"], DISTINCT)
                self.assertEqual(preflight["seed"], 13)
                self.assertEqual(len(preflight["slots"]), 4)
                raise RuntimeError("authored failure after saved preflight")

            with patch("verification_inspect.eval", side_effect=fail_after_preflight):
                with self.assertRaises(RuntimeError):
                    run_replay(self.root, self.review, destination, policy=DISTINCT)
            manifest = json.loads((destination / "manifest.json").read_text())
            self.assertIn("mask-preflight.json", manifest)
            self.assertEqual(
                manifest["mask-preflight.json"],
                sha((destination / "mask-preflight.json").read_bytes()),
            )
            self.assertFalse((destination / "receipt.json").exists())

    def test_distinct_inspect_replay_keeps_metadata_and_source_observer_identities(
        self,
    ):
        with tempfile.TemporaryDirectory() as t:
            destination = Path(t) / "replay"
            with (
                patch(
                    "inspect_ai._util.appdirs.user_data_path",
                    return_value=Path(t) / "data",
                ),
                patch(
                    "inspect_ai._util.appdirs.user_cache_path",
                    return_value=Path(t) / "cache",
                ),
            ):
                rows = run_replay(self.root, self.review, destination, policy=DISTINCT)
            self.assertEqual(len(rows), 56)
            self.assertTrue(all(r["decision"] != "ERROR" for r in rows))
            self.assertTrue(all(r["observation_policy"] == DISTINCT for r in rows))
            self.assertEqual(
                sum(r["condition"] == "distinct_conditional_loss" for r in rows), 8
            )
            self.assertTrue(
                all(
                    r["distinct_ineligible_reason"] == "no_receipts"
                    for r in rows
                    if not r["distinct_comparison_available"]
                )
            )
            receipt = json.loads((destination / "receipt.json").read_text())
            preflight = json.loads((destination / "mask-preflight.json").read_text())
            self.assertEqual(receipt["observation_policy"], DISTINCT)
            self.assertEqual(
                receipt["replay_code_sha256"], preflight["replay_code_sha256"]
            )
            self.assertEqual(
                receipt["source_manifest_sha256"], self.review["manifest_sha256"]
            )
            self.assertEqual(
                receipt["mask_preflight_sha256"],
                sha((destination / "mask-preflight.json").read_bytes()),
            )
            self.assertEqual(receipt["remote_calls"], 0)
            self.assertEqual(
                set(receipt["replay_code_sha256"]),
                {"verification_inspect.py", "observation_masks.py"},
            )
            manifest = json.loads((destination / "manifest.json").read_text())
            self.assertTrue(
                all(
                    sha((destination / n).read_bytes()) == d
                    for n, d in manifest.items()
                )
            )

    def test_default_policy_does_not_add_conditional_views_or_change_flow(self):
        implicit, records, flow = prepare(self.root, self.review, seed=13)
        explicit, other, other_flow = prepare(
            self.root, self.review, seed=13, policy=LEGACY
        )
        self.assertEqual([s.input for s in implicit], [s.input for s in explicit])
        self.assertEqual(list(records.values()), list(other.values()))
        self.assertEqual(flow, other_flow)
        self.assertNotIn("observation_preflight", flow)
        self.assertTrue(all("observation_policy" not in r for r in records.values()))

    def test_relay_preflight_retains_proof_only_and_proof_free_comparisons(self):
        from verification_phase import execute_phase

        fixture = fixtures.PhaseTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare(phase="relay")
        execute_phase(fixture.phase, fixture.gate, fixture.invoke)
        self.root = fixture.phase
        self.rebind()
        samples, records, flow = prepare(
            self.root, self.review, seed=13, policy=DISTINCT
        )
        self.assertEqual(len(samples), 160)
        self.assertEqual(len(records), 160)
        slots = flow["observation_preflight"]["slots"]
        self.assertEqual(len(slots), 12)
        self.assertEqual(sum(s["status"] == "eligible" for s in slots), 4)
        self.assertEqual(
            sum(s["distinct_ineligible_reason"] == "only_receipts" for s in slots), 2
        )
        self.assertEqual(
            sum(s["distinct_ineligible_reason"] == "no_receipts" for s in slots), 6
        )
        self.assertEqual(
            {s["distinct_candidate_count"] for s in slots if s["status"] == "eligible"},
            {1},
        )

    def test_source_unavailable_slots_remain_in_new_preflight(self):
        from verification_phase import execute_phase

        fixture = fixtures.PhaseTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare()
        fixture.failure = TimeoutError("authored unavailable source")
        result = execute_phase(fixture.phase, fixture.gate, fixture.invoke)
        self.assertEqual(result["status"], "halted")
        self.root = fixture.phase
        self.rebind()
        _, _, flow = prepare(self.root, self.review, seed=13, policy=DISTINCT)
        slots = flow["observation_preflight"]["slots"]
        self.assertEqual(len(slots), 4)
        self.assertEqual(sum(s["status"] == "source_unavailable" for s in slots), 3)
        self.assertTrue(
            all(
                s["distinct_ineligible_reason"] == "no_source_packet"
                for s in slots
                if s["status"] == "source_unavailable"
            )
        )

    def test_unresolved_outcome_is_not_mislabelled_as_absent_source_packet(self):
        path = self.root / "phase-result.json"
        result = json.loads(path.read_text())
        result["capture_errors"] = {"cell-1": "authored unresolved capture"}
        result["status"] = "halted"
        path.write_text(json.dumps(result))
        self.rebind()
        _, _, flow = prepare(self.root, self.review, seed=13, policy=DISTINCT)
        slot = flow["observation_preflight"]["slots"][1]
        self.assertEqual(slot["status"], "source_unavailable")
        self.assertEqual(
            slot["distinct_ineligible_reason"], "independent_outcome_unresolved"
        )

    def test_v2_invalid_seed_rejected_before_source_intake(self):
        for seed in (True, -1, 2**64, "13"):
            with self.assertRaisesRegex(ValueError, "seed"):
                prepare(self.root / "missing", {}, seed=seed, policy=DISTINCT)

    def test_code_change_during_replay_cannot_produce_success_receipt(self):
        first = {"verification_inspect.py": "a" * 64, "observation_masks.py": "b" * 64}
        later = {**first, "observation_masks.py": "c" * 64}
        with tempfile.TemporaryDirectory() as t:
            destination = Path(t) / "changed"
            with (
                patch(
                    "verification_inspect.replay_code_hashes",
                    side_effect=[first, later],
                ),
                patch("verification_inspect.eval", return_value=[]),
            ):
                with self.assertRaisesRegex(ValueError, "implementation changed"):
                    run_replay(self.root, self.review, destination, policy=DISTINCT)
            preflight = json.loads((destination / "mask-preflight.json").read_text())
            self.assertEqual(preflight["replay_code_sha256"], first)
            self.assertIn(
                "does not attest prospective", preflight["registration_scope"]
            )
            self.assertTrue((destination / "failure.json").exists())
            self.assertFalse((destination / "receipt.json").exists())

    def test_source_envelope_cannot_misrepresent_response_provenance(self):
        path = self.root / "phase-result.json"
        result = json.loads(path.read_text())
        result["evidence_kind"] = "recorded_agent_trace"
        path.write_text(json.dumps(result))
        self.rebind()
        with self.assertRaisesRegex(ValueError, "source flow envelope"):
            prepare(self.root, self.review, seed=13)

    def test_rebound_hashes_cannot_hide_swapped_blind_cell_outcomes(self):
        path = self.root / "phase-result.json"
        result = json.loads(path.read_text())
        result["records"][0]["cell_id"], result["records"][2]["cell_id"] = (
            result["records"][2]["cell_id"],
            result["records"][0]["cell_id"],
        )
        path.write_text(json.dumps(result))
        self.rebind()
        with self.assertRaisesRegex(ValueError, "slot/cell/actor"):
            prepare(self.root, self.review, seed=13)

    def test_omitted_consumed_file_cannot_escape_review(self):
        path = self.root / "evidence-manifest.json"
        manifest = json.loads(path.read_text())
        manifest.pop("phase-result.json")
        path.write_text(json.dumps(manifest))
        self.review["manifest_sha256"] = sha(path.read_bytes())
        with self.assertRaisesRegex(ValueError, "cover every"):
            prepare(self.root, self.review, seed=13)

    def test_rebound_request_or_intent_tampering_fails(self):
        path = self.root / "cell-0-solo/intent.json"
        intent = json.loads(path.read_text())
        intent["receipt_namespace"] = "another-state"
        path.write_text(json.dumps(intent))
        self.rebind()
        with self.assertRaisesRegex(ValueError, "intent/capture"):
            prepare(self.root, self.review, seed=13)
