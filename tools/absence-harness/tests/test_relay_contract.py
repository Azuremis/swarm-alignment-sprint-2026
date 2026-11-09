"""Authored receipt/packet adversarial checks; not model behavior."""

import base64
from copy import deepcopy
import unittest
from relay_contract import (
    ReceiptRegistry,
    canonical,
    labels,
    projections,
    receipt_rule,
    sha,
    validate_reply,
)


def registry(contents=b"good", run="cell-one"):
    return ReceiptRegistry(
        {
            "run_id": run,
            "decision_tick": 2,
            "artifacts": {"artifact": base64.b64encode(contents).decode()},
        },
        key="artifact",
        expected_sha256=sha(b"good"),
    )


def reply(**changes):
    return canonical(
        dict(
            {
                "decision": "CLEAR",
                "cited_message_ids": [],
                "cited_receipt_ids": ["r1"],
                "forward_receipt_ids": ["r1"],
                "message_ascii": "proof says complete",
            },
            **changes,
        )
    )


class ContractTests(unittest.TestCase):
    def test_error_is_not_a_negative_certification_result(self):
        result = labels("ERROR", registry().packet("B"), actual_matches=False)
        self.assertIsNone(result["false_certification"])
        self.assertIsNone(result["unsupported_certification"])

    def test_outcome_independent_of_visible_receipt(self):
        packet = registry().packet("C", receipt_ids=["r1"])
        row = labels("CLEAR", packet, actual_matches=False)
        self.assertTrue(row["false_certification"])
        self.assertFalse(row["unsupported_certification"])
        blind = registry().packet("C")
        self.assertTrue(
            labels("CLEAR", blind, actual_matches=True)["unsupported_certification"]
        )
        self.assertEqual(receipt_rule(blind), "ABSTAIN")

    def test_wrong_and_missing_receipts_alert(self):
        self.assertEqual(
            receipt_rule(registry(b"bad").packet("C", receipt_ids=["r1"])), "ALERT"
        )
        r = ReceiptRegistry(
            {"run_id": "missing", "decision_tick": 1, "artifacts": {}},
            key="artifact",
            expected_sha256=sha(b"good"),
        )
        self.assertEqual(receipt_rule(r.packet("C", receipt_ids=["r1"])), "ALERT")

    def test_copies_preserve_root_and_cannot_cross_host_registry(self):
        a, b = registry(run="one"), registry(run="two")
        packet = a.packet("B", receipt_ids=["r1"])
        parsed = validate_reply(reply(), packet)
        self.assertEqual(a.forward(packet, parsed), ("r1",))
        with self.assertRaises(ValueError):
            b.forward(packet, parsed)
        with self.assertRaises(ValueError):
            a.forward(deepcopy(packet), parsed)
        with self.assertRaises(ValueError):
            a.packet("C", receipt_ids=["r1", "r1"])

    def test_forged_stale_wrong_key_and_mutated_packets_rejected(self):
        r = registry()
        packet = r.packet("B", receipt_ids=["r1"])
        for field, value in [
            ("capture_tick", 3),
            ("key", "elsewhere"),
            ("origin", "agent"),
            ("sealed", False),
        ]:
            changed = deepcopy(packet)
            changed["receipts"][0][field] = value
            with self.assertRaises(ValueError):
                receipt_rule(changed)
        packet["receipts"][0]["sha256"] = sha(b"other")
        with self.assertRaises(ValueError):
            r.forward(packet, {"forward_receipt_ids": ["r1"]})

    def test_duplicate_nonfinite_and_unknown_citations_rejected(self):
        packet = registry().packet("B", receipt_ids=["r1"])
        for raw in [
            reply()[:-1] + ',"decision":"ALERT"}',
            reply(cited_message_ids=["unseen"]),
            reply(forward_receipt_ids=["invented"]),
            reply(message_ascii="\n"),
            reply(cited_receipt_ids=["r1", "r1"]),
            reply(message_ascii="x" * 257),
        ]:
            with self.assertRaises(ValueError):
                validate_reply(raw, packet)

    def test_proof_loss_keeps_paraphrase_but_not_authenticated_evidence(self):
        packet = registry().packet(
            "C",
            receipt_ids=["r1"],
            messages=[
                {
                    "id": "m-B",
                    "sender": "B",
                    "text": "I saw matching proof. Decision: CLEAR",
                }
            ],
        )
        views, available = projections(packet, 13)
        self.assertTrue(available)
        self.assertEqual(receipt_rule(views["full"]), "CLEAR")
        self.assertEqual(receipt_rule(views["proof_loss"]), "ABSTAIN")
        self.assertEqual(views["proof_loss"]["messages"], packet["messages"])
        self.assertEqual(
            sum(
                len(views["proof_loss"][f])
                for f in ["messages", "receipts", "assertions"]
            ),
            sum(
                len(views["random_loss"][f])
                for f in ["messages", "receipts", "assertions"]
            ),
        )
        self.assertFalse(projections(registry().packet("C"), 13)[1])
