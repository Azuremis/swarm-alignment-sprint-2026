"""One dated-evidence turn through the pinned runtime; no tools or retries.

This adapter loads no credential and opens no provider connection itself. The
caller must supply an approved RequestGate; offline checks use a fake transport.
"""

import asyncio
from types import SimpleNamespace

from dated_contract import canonical, validate_packet
from precedence_policy import policy_text
from verification_turn import (
    NoTools,
    PinnedInvoker as LegacyPinnedInvoker,
    verification_client,
)


def actor_role(actor):
    if actor == "solo":
        return "solo"
    if actor in {"R1", "R2", "R3", "R4"}:
        return "reporter"
    if actor == "terminal":
        return "aggregator"
    raise ValueError("unknown planned dated-evidence actor")


def api_params(packet, plan, role, policy_variant="original"):
    validate_packet(packet)
    if role != actor_role(packet["actor"]):
        raise ValueError("actor/role mismatch")
    if plan["model"] != "gpt-5.6-luna" or plan["max_output_tokens"] != 512:
        raise ValueError("reviewed model/output bound required")
    return {
        "model": plan["model"],
        "max_output_tokens": plan["max_output_tokens"],
        "parallel_tool_calls": False,
        "store": False,
        "reasoning": {"effort": "none"},
        "input": [
            {"type": "message", "role": "system", "content": policy_text(role, policy_variant)},
            {"type": "message", "role": "user", "content": canonical(packet)},
        ],
    }


class PacketPrompts:
    def __init__(self, packet, role, policy_variant="original"):
        self.packet = validate_packet(packet)
        if role != actor_role(packet["actor"]):
            raise ValueError("actor/role mismatch")
        self.role = role
        self.policy = policy_text(role, policy_variant)

    def get_system_prompt(self):
        return self.policy

    def get_user_prompt(self, **kwargs):
        return canonical(self.packet)


def dated_client(source_client):
    # Reuse only the no-tools/one-turn client boundary, never its old prompts.
    class Client(verification_client(source_client)):
        def _responses_create_with_retries(self, params):
            if "store" in params and params["store"] is not False:
                raise ValueError("provider persistence is forbidden")
            return self.slot_gate.create({**params, "store": False})

    return Client


def run_turn(packet, slot_gate, *, base_agent, source_client, source_protocol):
    prompts = PacketPrompts(
        packet, slot_gate.role, getattr(slot_gate, "policy_variant", "original")
    )
    client = dated_client(source_client)(slot_gate)
    agent = base_agent(
        client,
        name=packet["actor"],
        model_name=slot_gate.plan["model"],
        max_conversation_steps=1,
        environment_name="DatedEvidence",
        generation_params={
            "max_tokens": slot_gate.plan["max_output_tokens"],
            "parallel_tool_calls": False,
            "reasoning_effort": "none",
        },
    )
    agent.toolset_discovery = NoTools()
    protocol = source_protocol(config={})
    result = asyncio.run(
        protocol.agent_execution_turn(
            agent, packet["actor"], {}, SimpleNamespace(prompts=prompts), 0
        )
    )
    if result.get("conversation_steps") != 1 or result.get("tools_executed") != 0:
        raise ValueError("exactly one tool-free dated response required")
    return result["response"]


class PinnedDatedInvoker(LegacyPinnedInvoker):
    """Same reviewed Colosseum/Terrarium identities, separately versioned inputs."""

    def __call__(self, packet, slot_gate):
        self.verify()
        return run_turn(
            packet,
            slot_gate,
            base_agent=self.base_agent,
            source_client=self.source_client,
            source_protocol=self.source_protocol,
        )
