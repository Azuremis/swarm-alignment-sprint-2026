"""One sealed-receipt ring turn through the reviewed low-reasoning transport.

Logical actor memory lives in the packet; each invocation is a fresh framework
agent. No hidden shared context, tools, credential setup, or retries.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

from model_comparison_turn import FIXED, comparison_client
from relay_contract import POLICY, canonical, validate_packet
from verification_turn import NoTools, PacketPrompts, PinnedInvoker

VERSION = "interacting-ring-v1"
MODEL = "gpt-6-luna"


def api_params(packet, plan):
    validate_packet(packet)
    if plan["model"] != MODEL or plan["max_output_tokens"] != 512:
        raise ValueError("fixed ring model and output bound required")
    return {
        "model": MODEL, "max_output_tokens": 512, **deepcopy(FIXED),
        "input": [
            {"type": "message", "role": "system", "content": POLICY},
            {"type": "message", "role": "user", "content": canonical(packet)},
        ],
    }


def run_turn(packet, slot_gate, *, base_agent, source_client, source_protocol):
    prompts = PacketPrompts(packet)
    agent = base_agent(
        comparison_client(source_client)(slot_gate), name=packet["actor"],
        model_name=MODEL, max_conversation_steps=1,
        environment_name="SealedReceiptRing",
        generation_params={"max_tokens": 512, "parallel_tool_calls": False,
                           "reasoning_effort": "low"},
    )
    agent.toolset_discovery = NoTools()
    result = asyncio.run(source_protocol(config={}).agent_execution_turn(
        agent, packet["actor"], {}, SimpleNamespace(prompts=prompts), 0))
    if result.get("conversation_steps") != 1 or result.get("tools_executed") != 0:
        raise ValueError("one tool-free response required")
    return result["response"]


class PinnedRingInvoker(PinnedInvoker):
    def __call__(self, packet, slot_gate):
        self.verify()
        return run_turn(packet, slot_gate, base_agent=self.base_agent,
                        source_client=self.source_client,
                        source_protocol=self.source_protocol)
