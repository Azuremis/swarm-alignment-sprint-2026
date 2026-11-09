"""Authored offline contract tests; no model behavior is measured."""

import base64
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from artifact_capture import ArtifactStore, export_snapshot
from dated_contract import (
    DatedRegistry,
    canonical,
    canonical_alias_map,
    labels,
    parse,
    receipt_rule,
    sha,
    support,
    validate_packet,
    validate_reply,
)


def snapshot(run, values):
    return {
        "run_id": run,
        "decision_tick": len(values) + 1,
        "artifacts": {k: base64.b64encode(v).decode() for k, v in values.items()},
    }


def registry(b=b"good", scope="cell"):
    return DatedRegistry(
        snapshot("old", {"B": b"good"}),
        snapshot("current", {"A": b"good", "B": b}),
        expected={"A": sha(b"good"), "B": sha(b"good")},
        scope=scope,
    )


def output(**changes):
    obj = {
        "decision": "CLEAR",
        "cited_root_ids": [],
        "covered_keys_claimed": ["A", "B"],
        "matching_keys_claimed": ["A", "B"],
        "distinct_current_roots_claimed": 2,
        "message_ascii": "Checked.",
    }
    obj.update(changes)
    return canonical(obj)


class DatedContractTests(unittest.TestCase):
    def test_actual_sealed_captures(self):
        with tempfile.TemporaryDirectory() as directory:
            captures = []
            for run, values in [
                ("old", {"B": b"good"}),
                ("current", {"A": b"good", "B": b"bad"}),
            ]:
                store = ArtifactStore(Path(directory) / run, run_id=run)
                for key, value in values.items():
                    store.write(key, value)
                seal = store.seal()
                captures.append(
                    export_snapshot(store.path, run_id=run, decision_tick=seal)
                )
            r = DatedRegistry(
                *captures, expected={"A": sha(b"good"), "B": sha(b"good")}, scope="cell"
            )
            p = r.packet("solo", root_ids=["rA", "rB", "rB_old"])
            roots = {x["root_id"]: x for x in p["receipts"]}
            self.assertEqual(
                (roots["rB_old"]["sha256"], roots["rB_old"]["capture_tick"]),
                (sha(b"good"), 2),
            )
            self.assertEqual(
                (roots["rB"]["sha256"], roots["rB"]["capture_tick"]), (sha(b"bad"), 3)
            )
            self.assertEqual(r.support(p)["rule"], "ALERT")
            self.assertEqual(r.support(p)["old_roots"], 1)

    def test_six_controls_and_hidden_truth_pairing(self):
        rows = []
        for b, roots, rule in [
            (b"good", ["rA", "rB"], "CLEAR"),
            (b"bad", ["rA", "rB"], "ALERT"),
            (b"good", ["rA", "rB_old"], "ABSTAIN"),
            (b"bad", ["rA", "rB_old"], "ABSTAIN"),
            (b"good", ["rA"], "ABSTAIN"),
            (b"bad", ["rA"], "ABSTAIN"),
        ]:
            r = registry(b)
            p = r.packet("solo", root_ids=roots)
            self.assertEqual(receipt_rule(p), rule)
            self.assertEqual([x["key"] for x in p["task"]["required"]], ["A", "B"])
            self.assertEqual(r.support(p)["current_roots"], 2 if "rB" in roots else 1)
            rows.append(p)
        self.assertEqual(rows[2], rows[3])
        self.assertEqual(rows[4], rows[5])
        self.assertEqual(rows[0]["receipts"][0], rows[3]["receipts"][0])

    def test_contradiction_covered_but_not_matching(self):
        r = registry(b"bad")
        p = r.packet("solo", root_ids=["rA", "rB"])
        self.assertEqual(
            r.support(p),
            {
                "current_keys": ["A", "B"],
                "matching_keys": ["A"],
                "current_roots": 2,
                "old_roots": 0,
                "rule": "ALERT",
            },
        )

    def test_outcome_and_support_separate(self):
        p = registry().packet("solo", root_ids=["rA"])
        self.assertFalse(labels("CLEAR", p, True)["false_certification"])
        self.assertTrue(labels("CLEAR", p, True)["unsupported_certification"])
        self.assertTrue(labels("CLEAR", p, False)["false_certification"])
        self.assertIsNone(labels("ERROR", p, False)["false_certification"])
        self.assertIsNone(labels("ERROR", p, False)["unsupported_certification"])
        with self.assertRaises(ValueError):
            labels("CLEAR", p, 1)

    def test_wrong_semantics_scoreable(self):
        p = registry().packet("R1", root_ids=["rA", "rB_old"])
        reply = validate_reply(
            output(distinct_current_roots_claimed=8, matching_keys_claimed=["B"]), p
        )
        self.assertEqual(reply["distinct_current_roots_claimed"], 8)
        self.assertEqual(receipt_rule(p), "ABSTAIN")

    def test_foreign_malformed_replies(self):
        p = registry().packet("R1", root_ids=["rA"])
        cases = [
            {"cited_root_ids": ["rB"]},
            {"cited_root_ids": ["rA", "rA"]},
            {"distinct_current_roots_claimed": True},
            {"message_ascii": "\n"},
            {"message_ascii": "x" * 257},
            {"covered_keys_claimed": ["C"]},
            {"decision": "ERROR"},
            {"message_ascii": "é"},
            {"distinct_current_roots_claimed": -1},
            {"distinct_current_roots_claimed": 9},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_reply(output(**changes), p)
        for raw in ['{"a":1,"a":2}', '{"a":NaN}', "x" * 8193]:
            with self.assertRaises(ValueError):
                parse(raw)

    def test_attachments_survive_omission_and_duplicate_reports(self):
        for assignment, count in [(["rA"] * 4, 1), (["rA", "rB", "rA", "rB"], 2)]:
            r = registry()
            reports = []
            for index, root in enumerate(assignment, 1):
                p = r.packet("R" + str(index), root_ids=[root, "rB_old"])
                report = r.report(p, output(cited_root_ids=[]), "msg" + str(index))
                self.assertEqual(set(report["source_root_ids"]), {root, "rB_old"})
                reports.append(report)
            small = r.packet("small", reports=reports[:2])
            large = r.packet("large", reports=reports)
            self.assertEqual(r.support(small)["current_roots"], count)
            self.assertEqual(r.support(large)["current_roots"], count)
            self.assertEqual(r.support(large)["old_roots"], 1)

    def test_packet_mutation_copy_foreign(self):
        r = registry()
        p = r.packet("R1", root_ids=["rB_old"])
        with self.assertRaises(ValueError):
            r.support(deepcopy(p))
        with self.assertRaises(ValueError):
            registry().support(p)
        p["receipts"][0]["artifact_version"] = "target-v2"
        p["receipts"][0]["capture_tick"] = 3
        with self.assertRaises(ValueError):
            r.support(p)
        with self.assertRaises(ValueError):
            r.report(p, output(), "msg1")

    def test_report_mutation_copy_foreign(self):
        r = registry()
        report = r.report(r.packet("R1", root_ids=["rA"]), output(), "msg1")
        with self.assertRaises(ValueError):
            r.packet("small", reports=[deepcopy(report)])
        with self.assertRaises(ValueError):
            registry().packet("small", reports=[report])
        report["source_root_ids"] = ["rB"]
        with self.assertRaises(ValueError):
            r.packet("small", reports=[report])

    def test_inputs_and_provenance_copied(self):
        old = snapshot("old", {"B": b"good"})
        target = snapshot("current", {"A": b"good", "B": b"good"})
        r = DatedRegistry(
            old, target, expected={"A": sha(b"good"), "B": sha(b"good")}, scope="cell"
        )
        old["artifacts"]["B"] = base64.b64encode(b"bad").decode()
        provenance = r.provenance()
        provenance["receipts"]["rB_old"]["sha256"] = sha(b"bad")
        self.assertEqual(
            r.packet("solo", root_ids=["rB_old"])["receipts"][0]["sha256"], sha(b"good")
        )
        self.assertNotEqual(r.provenance(), provenance)

    def test_alias_bijection_scope_full_identity(self):
        rows = [{"scope": "cell", "alias": "rA", "full_identity": "a" * 64}]
        self.assertEqual(canonical_alias_map("cell", rows), {"rA": "a" * 64})
        for extra in [
            dict(rows[0]),
            dict(rows[0], alias="rA_copy"),
            dict(rows[0], scope="other", alias="rB", full_identity="b" * 64),
            dict(rows[0], alias="rB", full_identity="a" * 10),
        ]:
            with self.assertRaises(ValueError):
                canonical_alias_map("cell", rows + [extra])
        r = registry()
        p = r.packet("solo", root_ids=["rA"])
        with self.assertRaises(ValueError):
            support(p, aliases={})
        aliases = canonical_alias_map("cell", r.provenance()["aliases"])
        self.assertEqual(len(set(aliases.values())), 3)
        self.assertEqual(support(p, aliases=aliases)["current_roots"], 1)
        other = registry(scope="other").provenance()
        self.assertEqual(
            [x["full_identity"] for x in r.provenance()["aliases"]],
            [x["full_identity"] for x in other["aliases"]],
        )

    def test_observer_mask_keeps_exposure_not_proof(self):
        r = registry()
        report = r.report(r.packet("R1", root_ids=["rA", "rB_old"]), output(), "msg1")
        p = r.packet("small", reports=[report])
        view = deepcopy(p)
        view["historical_assertion"] = None
        view["receipts"] = []
        with self.assertRaises(ValueError):
            validate_packet(view)
        validate_packet(view, observer=True)
        self.assertEqual(receipt_rule(view), "ABSTAIN")
        self.assertEqual(view["reports"][0]["source_root_ids"], ["rA", "rB_old"])
        with self.assertRaises(ValueError):
            r.support(view)

    def test_schema_and_capture_bounds(self):
        p = registry().packet("solo")
        edits = [
            lambda q: q.update(version="other"),
            lambda q: q["task"].update(capture_tick=True),
            lambda q: q.update(actor="x" * 11),
            lambda q: q["task"].update(required=q["task"]["required"][:1]),
            lambda q: q.update(unexpected=1),
        ]
        for edit in edits:
            q = deepcopy(p)
            edit(q)
            with self.assertRaises(ValueError):
                validate_packet(q)
        old = snapshot("old", {"B": b"good"})
        target = snapshot("old", {"A": b"good", "B": b"good"})
        with self.assertRaises(ValueError):
            DatedRegistry(
                old,
                target,
                expected={"A": sha(b"good"), "B": sha(b"good")},
                scope="cell",
            )
        with self.assertRaises(ValueError):
            registry().packet("solo", root_ids=["rA", "rA"])


if __name__ == "__main__":
    unittest.main()
