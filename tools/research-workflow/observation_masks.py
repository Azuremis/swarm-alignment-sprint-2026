"""Versioned, outcome-blind observation controls; metadata is evaluator-only.

The conditional control is not unconditional random dropout. It samples uniformly
from matched-record deletion sets other than deleting all receipts. Record count
matching does not match tokens, information, or evidential strength. Equal channel
inventories deliberately share a seeded draw; these are not independent masks.
"""

from copy import deepcopy
from math import comb
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "absence-harness"))
from relay_contract import projections, validate_packet  # noqa: E402

LEGACY = "legacy-unconditional-v1"
DISTINCT = "distinct-conditional-v2"
POLICIES = (LEGACY, DISTINCT)
FIELDS = ("assertions", "messages", "receipts")
MAX_SEED = 2**64 - 1


def validate_policy_seed(policy, seed):
    if policy not in POLICIES:
        raise ValueError("unknown observation policy")
    if type(seed) is not int or seed < 0:
        raise ValueError("fixed nonnegative integer seed required")
    if policy == DISTINCT and seed > MAX_SEED:
        raise ValueError("v2 seed must be an integer in [0, 2**64-1]")


def _unrank_combination(n, k, rank):
    """Lexicographic subset unranking without enumerating the sample space."""
    if not 0 <= k <= n or not 0 <= rank < comb(n, k):
        raise ValueError("combination rank outside sample space")
    selected, start = [], 0
    for remaining in range(k, 0, -1):
        for position in range(start, n - remaining + 1):
            count = comb(n - position - 1, remaining - 1)
            if rank < count:
                selected.append(position)
                start = position + 1
                break
            rank -= count
    return selected


def _remove(packet, atoms, selected):
    removed = {atoms[i] for i in selected}
    view = deepcopy(packet)
    for field in FIELDS:
        view[field] = [
            item for i, item in enumerate(view[field]) if (field, i) not in removed
        ]
    return view


def select_views(packet, seed, *, policy=LEGACY):
    """Return visible packets and a separate selection/eligibility attestation.

    No outcome label is accepted. Selection depends only on channel/index counts
    and seed. All-receipts deletion is the last lexicographic k-subset because
    receipt atoms are placed last. Drawing a rank below that last rank samples
    every other subset once, with no rejection loop or post-result seed search.
    """
    validate_policy_seed(policy, seed)
    validate_packet(packet)
    # Keep the historical seed validation and three views exactly as implemented.
    views, proof_available = projections(packet, seed)
    atoms = [(field, i) for field in FIELDS for i in range(len(packet[field]))]
    n, k = len(atoms), len(packet["receipts"])
    total = comb(n, k)
    unconditional = sorted(random.Random(seed).sample(range(n), k))
    eligible = k > 0 and n > k
    reason = None if eligible else "no_receipts" if k == 0 else "only_receipts"
    metadata = {
        "policy": policy,
        "seed": seed,
        "seed_scope": "reset-per-packet; identical inventories share a draw",
        "selection_uses": "channel/index inventory and seed only",
        "matching": "deleted record counts only; tokens/information not matched",
        "inventory": {field: len(packet[field]) for field in FIELDS},
        "record_count": n,
        "deleted_record_count": k,
        "proof_loss_available": proof_available,
        "unconditional_candidate_count": total,
        "unconditional_mask_probability": {"numerator": 1, "denominator": total},
        "proof_and_random_identical": views["proof_loss"] == views["random_loss"],
        "removed_atoms": {
            "full": [],
            "proof_loss": [["receipts", i] for i in range(k)],
            "random_loss": [list(atoms[i]) for i in unconditional],
        },
    }
    if policy == DISTINCT:
        count = total - 1 if eligible else 0
        metadata.update(
            {
                "distinct_comparison_available": eligible,
                "distinct_ineligible_reason": reason,
                "distinct_candidate_count": count,
                "distinct_mask_probability": {"numerator": 1, "denominator": count}
                if eligible
                else None,
                "conditioning": "uniform k-subsets excluding all-receipts deletion",
                "excluded_unconditional_probability": {
                    "numerator": 1,
                    "denominator": total,
                },
            }
        )
        if eligible:
            rank = random.Random(seed).randrange(count)
            selected = _unrank_combination(n, k, rank)
            view = _remove(packet, atoms, selected)
            assert view != views["proof_loss"]
            assert len(view["receipts"]) >= 1
            views["distinct_conditional_loss"] = view
            metadata["distinct_selected_rank"] = rank
            metadata["removed_atoms"]["distinct_conditional_loss"] = [
                list(atoms[i]) for i in selected
            ]
    return views, metadata
