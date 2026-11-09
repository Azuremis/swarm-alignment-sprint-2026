"""Authored inventory tests; no model calls or empirical performance claims."""

from copy import deepcopy
from itertools import combinations
from math import comb
import random
import unittest
from unittest.mock import patch

from observation_masks import (
    DISTINCT,
    FIELDS,
    LEGACY,
    MAX_SEED,
    _unrank_combination,
    select_views,
)
from relay_contract import VERSION, projections, sha, validate_packet


def packet(assertions=1, messages=1, receipts=1):
    result = {
        "version": VERSION,
        "actor": "observer",
        "task": {
            "state_id": "state",
            "capture_tick": 2,
            "required_key": "artifact",
            "expected_sha256": sha(b"expected"),
        },
        "assertions": [
            {"id": f"a{i}", "sender": "source", "text": "Completion asserted"}
            for i in range(assertions)
        ],
        "messages": [
            {"id": f"m{i}", "sender": "peer", "text": "Repeated claim"}
            for i in range(messages)
        ],
        "receipts": [
            {
                "id": f"r{i}",
                "state_id": "state",
                "capture_tick": 2,
                "key": "artifact",
                "sha256": sha(b"expected"),
                "sealed": True,
                "origin": "host",
            }
            for i in range(receipts)
        ],
    }
    return validate_packet(result)


class ObservationMaskTests(unittest.TestCase):
    def test_legacy_views_remain_exact_including_unbounded_historical_seed(self):
        for values in [(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 1, 2)]:
            for seed in (0, 13, 2**100):
                p = packet(*values)
                views, meta = select_views(p, seed)
                expected, available = projections(p, seed)
                self.assertEqual(views, expected)
                self.assertEqual(meta["policy"], LEGACY)
                self.assertEqual(meta["proof_loss_available"], available)

    def test_unranking_exhaustively_matches_all_small_combinations(self):
        for n in range(1, 9):
            for k in range(n + 1):
                actual = [
                    tuple(_unrank_combination(n, k, rank)) for rank in range(comb(n, k))
                ]
                self.assertEqual(actual, list(combinations(range(n), k)))

    def test_conditional_ranks_cover_each_allowed_mask_once_without_rerolls(self):
        p = packet(1, 2, 2)
        count = comb(5, 2) - 1
        actual = []
        for rank in range(count):
            with patch.object(random.Random, "randrange", return_value=rank) as draw:
                views, meta = select_views(p, 13, policy=DISTINCT)
                draw.assert_called_once_with(count)
            removed = meta["removed_atoms"]["distinct_conditional_loss"]
            atoms = [(field, i) for field in FIELDS for i in range(len(p[field]))]
            actual.append(tuple(atoms.index(tuple(item)) for item in removed))
            self.assertNotEqual(views["proof_loss"], views["distinct_conditional_loss"])
            self.assertEqual(meta["distinct_candidate_count"], count)
            self.assertEqual(
                meta["distinct_mask_probability"],
                {"numerator": 1, "denominator": count},
            )
        self.assertEqual(actual, list(combinations(range(5), 2))[:-1])

    def test_record_counts_and_retained_bytes_order_are_preserved(self):
        for values in [(1, 0, 1), (1, 2, 2), (0, 1, 8), (8, 8, 8)]:
            p = packet(*values)
            before = deepcopy(p)
            views, meta = select_views(p, MAX_SEED, policy=DISTINCT)
            for name in ("proof_loss", "random_loss", "distinct_conditional_loss"):
                self.assertEqual(
                    sum(len(views[name][f]) for f in FIELDS), sum(values) - values[2]
                )
                removed = {tuple(item) for item in meta["removed_atoms"][name]}
                for field in FIELDS:
                    self.assertEqual(
                        views[name][field],
                        [
                            x
                            for i, x in enumerate(p[field])
                            if (field, i) not in removed
                        ],
                    )
                self.assertEqual(views[name]["task"], p["task"])
            self.assertGreaterEqual(
                len(views["distinct_conditional_loss"]["receipts"]), 1
            )
            self.assertEqual(p, before)
            views["full"]["task"]["state_id"] = "mutated"
            self.assertEqual(p, before)
            self.assertNotEqual(views["proof_loss"]["task"]["state_id"], "mutated")

    def test_empty_no_proof_and_all_proof_are_explicitly_ineligible(self):
        for values, reason in [
            ((0, 0, 0), "no_receipts"),
            ((1, 2, 0), "no_receipts"),
            ((0, 0, 1), "only_receipts"),
            ((0, 0, 8), "only_receipts"),
        ]:
            views, meta = select_views(packet(*values), 13, policy=DISTINCT)
            self.assertEqual(set(views), {"full", "proof_loss", "random_loss"})
            self.assertFalse(meta["distinct_comparison_available"])
            self.assertEqual(meta["distinct_ineligible_reason"], reason)
            self.assertEqual(meta["distinct_candidate_count"], 0)
            self.assertIsNone(meta["distinct_mask_probability"])

    def test_single_receipt_has_distinct_control_even_when_legacy_collides(self):
        p = packet(1, 0, 1)
        views, meta = select_views(p, 13, policy=DISTINCT)
        self.assertEqual(views["proof_loss"], views["random_loss"])
        self.assertEqual(views["distinct_conditional_loss"]["receipts"], p["receipts"])
        self.assertEqual(views["distinct_conditional_loss"]["assertions"], [])
        self.assertEqual(
            meta["distinct_mask_probability"], {"numerator": 1, "denominator": 1}
        )
        self.assertEqual(
            meta["excluded_unconditional_probability"],
            {"numerator": 1, "denominator": 2},
        )

    def test_multiple_receipts_need_not_have_enough_nonproof_records(self):
        views, meta = select_views(packet(0, 1, 8), 7, policy=DISTINCT)
        self.assertEqual(meta["distinct_candidate_count"], 8)
        self.assertEqual(len(views["distinct_conditional_loss"]["receipts"]), 1)
        self.assertEqual(len(views["distinct_conditional_loss"]["messages"]), 0)

    def test_selection_invariant_to_text_ids_digest_actor_and_task_values(self):
        original = packet(1, 2, 2)
        changed = deepcopy(original)
        changed["actor"] = "other"
        changed["task"]["expected_sha256"] = sha(b"new target")
        for field in FIELDS:
            for i, item in enumerate(changed[field]):
                item["id"] = f"changed_{field}_{i}"
                if field == "receipts":
                    item["sha256"] = sha(b"wrong") if i else None
                else:
                    item["text"] = "Contradictory claim " * 10
                    item["sender"] = "other"
        for seed in (0, 1, 13, MAX_SEED):
            first, meta = select_views(original, seed, policy=DISTINCT)
            second, other = select_views(changed, seed, policy=DISTINCT)
            self.assertEqual(meta, other)
            self.assertNotEqual(first["full"], second["full"])
            self.assertEqual(
                select_views(original, seed, policy=DISTINCT), (first, meta)
            )

    def test_v2_seed_bounds_and_unknown_policy_fail_closed(self):
        for invalid in (True, False, -1, 1.5, "13", None, MAX_SEED + 1):
            with self.assertRaises(ValueError):
                select_views(packet(), invalid, policy=DISTINCT)
        for seed in (0, MAX_SEED):
            select_views(packet(), seed, policy=DISTINCT)
        with self.assertRaises(ValueError):
            select_views(packet(), 13, policy="unknown")


if __name__ == "__main__":
    unittest.main()
