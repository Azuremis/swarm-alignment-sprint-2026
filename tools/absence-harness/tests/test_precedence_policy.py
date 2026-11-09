"""Prompt manipulation checks, not model performance tests."""

from copy import deepcopy
import unittest

from dated_contract import POLICY
from dated_turn import api_params, PacketPrompts
from precedence_policy import EXPLICIT, EXPLICIT_RULE, ORIGINAL_RULE, policy_text
from test_dated_turn import packet


class PrecedencePolicyTests(unittest.TestCase):
    def test_original_roles_remain_byte_identical(self):
        for role, text in POLICY.items():
            self.assertEqual(policy_text(role), text)

    def test_only_two_rule_sentences_change(self):
        original = POLICY["reporter"]
        variant = policy_text("reporter", EXPLICIT)
        self.assertEqual(variant.replace(EXPLICIT_RULE, ORIGINAL_RULE), original)
        self.assertEqual(variant.count(EXPLICIT_RULE), 1)
        self.assertIn("even when another required key has no current proof", variant)

    def test_user_packet_settings_and_prompt_adapter_agree(self):
        p = packet("R1")
        before = deepcopy(p)
        plan = {"model": "gpt-5.6-luna", "max_output_tokens": 512}
        original = api_params(p, plan, "reporter")
        variant = api_params(p, plan, "reporter", EXPLICIT)
        self.assertEqual(p, before)
        self.assertEqual(variant["input"][1], original["input"][1])
        prompts = PacketPrompts(p, "reporter", EXPLICIT)
        self.assertEqual(prompts.get_system_prompt(), variant["input"][0]["content"])
        self.assertEqual(prompts.get_user_prompt(), variant["input"][1]["content"])
        variant["input"][0] = original["input"][0]
        self.assertEqual(variant, original)

    def test_unplanned_roles_and_variants_fail_closed(self):
        for role, variant in [("solo", EXPLICIT), ("aggregator", EXPLICIT),
                              ("reporter", "unplanned"), ("unknown", "original")]:
            with self.assertRaises(ValueError):
                policy_text(role, variant)
