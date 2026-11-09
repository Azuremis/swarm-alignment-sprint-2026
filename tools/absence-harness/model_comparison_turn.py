"""Versioned low-reasoning component adapter; no credentials or provider setup."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

from dated_contract import canonical, validate_packet
from dated_turn import PacketPrompts
from precedence_policy import policy_text
from verification_turn import NoTools, PinnedInvoker, verification_client

VERSION = "matched-low-component-v1"
MODELS = ("gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol", "gpt-6-astra")
FIXED = {"reasoning": {"effort": "low"}, "store": False,
         "parallel_tool_calls": False, "service_tier": "default",
         "prompt_cache_options": {"mode": "explicit"}}


def api_params(packet, plan, variant):
    validate_packet(packet)
    if packet["actor"] != "R1" or plan["model"] not in MODELS or plan["max_output_tokens"] != 512:
        raise ValueError("fixed comparison actor, model and output bound required")
    return {"model": plan["model"], "max_output_tokens": 512, **deepcopy(FIXED),
            "input": [{"type": "message", "role": "system", "content": policy_text("reporter", variant)},
                      {"type": "message", "role": "user", "content": canonical(packet)}]}


def comparison_client(source_client):
    class Client(verification_client(source_client)):
        def _responses_create_with_retries(self, params):
            # The pinned Terrarium release strips reasoning for non-GPT-5 names.
            # Bind low explicitly at this separately versioned transport boundary.
            for key, value in FIXED.items():
                if key in params and params[key] != value:
                    raise ValueError("comparison setting changed")
            return self.slot_gate.create({**params, **deepcopy(FIXED)})
    return Client


def run_turn(packet, slot_gate, *, base_agent, source_client, source_protocol):
    prompts = PacketPrompts(packet, "reporter", slot_gate.policy_variant)
    client = comparison_client(source_client)(slot_gate)
    agent = base_agent(client, name="R1", model_name=slot_gate.plan["model"],
                       max_conversation_steps=1, environment_name="DatedEvidence",
                       generation_params={"max_tokens": 512, "parallel_tool_calls": False,
                                          "reasoning_effort": "low"})
    agent.toolset_discovery = NoTools()
    result = asyncio.run(source_protocol(config={}).agent_execution_turn(
        agent, "R1", {}, SimpleNamespace(prompts=prompts), 0))
    if result.get("conversation_steps") != 1 or result.get("tools_executed") != 0:
        raise ValueError("one tool-free response required")
    return result["response"]


class PinnedComparisonInvoker(PinnedInvoker):
    def __call__(self, packet, slot_gate):
        self.verify()
        return run_turn(packet, slot_gate, base_agent=self.base_agent,
                        source_client=self.source_client, source_protocol=self.source_protocol)
