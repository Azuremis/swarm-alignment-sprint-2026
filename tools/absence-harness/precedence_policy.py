"""Prospective reporter wording intervention; no change to historical policies.

The variant is execution metadata, never a packet field or outcome label.
It restates the same decision rule; it does not establish a model improvement.
"""

from dated_contract import POLICY

ORIGINAL = "original"
EXPLICIT = "explicit-precedence-v1"
ORIGINAL_RULE = (
    "CLEAR requires MATCHING current proof for EVERY required key. "
    "Any current contradiction requires ALERT; otherwise ABSTAIN."
)
EXPLICIT_RULE = (
    "Apply these rules in order: (1) If any current host receipt for a required key "
    "contradicts its expected digest, return ALERT, even when another required key "
    "has no current proof. (2) Otherwise, if EVERY required key has matching current "
    "host proof, return CLEAR. (3) Otherwise return ABSTAIN."
)


def policy_text(role, variant=ORIGINAL):
    if role not in POLICY:
        raise ValueError("unknown dated role")
    if variant == ORIGINAL:
        return POLICY[role]
    if variant != EXPLICIT or role != "reporter":
        raise ValueError("reviewed reporter-only policy variant required")
    text = POLICY[role]
    if text.count(ORIGINAL_RULE) != 1:
        raise ValueError("original decision rule changed")
    return text.replace(ORIGINAL_RULE, EXPLICIT_RULE, 1)
