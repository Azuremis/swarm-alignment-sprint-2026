"""Dated adapter boundary checks; no network, SDK or model execution."""

import base64
from copy import deepcopy
from types import SimpleNamespace
import unittest

from dated_contract import DatedRegistry, sha
from dated_turn import api_params, dated_client, actor_role, PacketPrompts


def packet(actor="solo"):
    def enc(b):
        return base64.b64encode(b).decode()

    return DatedRegistry(
        {"run_id": "test-prior", "decision_tick": 2, "artifacts": {"B": enc(b"b")}},
        {
            "run_id": "test-current",
            "decision_tick": 3,
            "artifacts": {"A": enc(b"a"), "B": enc(b"b")},
        },
        expected={"A": sha(b"a"), "B": sha(b"b")},
        scope="fixture",
    ).packet(actor, root_ids=["rA", "rB_old"])


class DatedTurnTests(unittest.TestCase):
    def test_role_binding_and_no_tools_persistence_or_hidden_fields(self):
        plan = {"model": "gpt-5.6-luna", "max_output_tokens": 512}
        for actor, role in [
            ("solo", "solo"),
            ("R1", "reporter"),
            ("terminal", "aggregator"),
        ]:
            p = packet(actor)
            before = deepcopy(p)
            params = api_params(p, plan, role)
            self.assertEqual(p, before)
            self.assertFalse(params["store"])
            self.assertFalse(params["parallel_tool_calls"])
            self.assertNotIn("tools", params)
            self.assertEqual(params["reasoning"], {"effort": "none"})
            self.assertEqual(
                params["input"][0]["content"],
                PacketPrompts(p, role).get_system_prompt(),
            )
            with self.assertRaises(ValueError):
                api_params(p, plan, "invalid")
        for hidden_slot in ("unplanned", "small", "large"):
            with self.assertRaises(ValueError):
                actor_role(hidden_slot)

    def test_changed_model_or_output_rejected(self):
        for p in [
            {"model": "different", "max_output_tokens": 512},
            {"model": "gpt-5.6-luna", "max_output_tokens": 1024},
        ]:
            with self.assertRaises(ValueError):
                api_params(packet(), p, "solo")

    def test_client_bypasses_base_retry_method_and_adds_store_false(self):
        class Base:
            def __init__(self):
                raise AssertionError("base credential loader must not run")

            def _responses_create_with_retries(self, params):
                raise AssertionError("base retries must not run")

        calls = []
        gate = SimpleNamespace(
            plan={"model": "gpt-5.6-luna"},
            create=lambda p: calls.append(p) or "response",
        )
        client = dated_client(Base)(gate)
        self.assertEqual(
            client._responses_create_with_retries({"model": "gpt-5.6-luna"}), "response"
        )
        self.assertEqual(calls, [{"model": "gpt-5.6-luna", "store": False}])
        with self.assertRaises(ValueError):
            client._responses_create_with_retries({"store": True})
        self.assertEqual(len(calls), 1)
