"""One verification response via pinned Colosseum/Terrarium; no tools or retries."""

import asyncio
import hashlib
import importlib.metadata
import inspect
from pathlib import Path
from types import SimpleNamespace
from relay_contract import POLICY, canonical, validate_packet

SOURCE_SHA256 = "3fad6a5e79f5fe48ffac50d3f46a1c4f379f77c431db4983341fa59208486c11"


class NoTools:
    def get_tools_for_environment(self, *args):
        return []

    def get_tools_for_blackboard(self, *args):
        return []


class PacketPrompts:
    def __init__(self, packet):
        self.packet = validate_packet(packet)

    def get_system_prompt(self):
        return POLICY

    def get_user_prompt(self, **kwargs):
        return canonical(self.packet)


def api_params(packet, plan):
    validate_packet(packet)
    return {
        "model": plan["model"],
        "max_output_tokens": plan["max_output_tokens"],
        "parallel_tool_calls": False,
        "reasoning": {"effort": "none"},
        "input": [
            {"type": "message", "role": "system", "content": POLICY},
            {"type": "message", "role": "user", "content": canonical(packet)},
        ],
    }


def verification_client(source_client):
    class Client(source_client):
        def __init__(self, slot_gate):
            self.slot_gate = slot_gate
            self._capability_model = slot_gate.plan["model"]

        def _responses_create_with_retries(self, params):
            return self.slot_gate.create(params)

        async def get_external_tools(self):
            return []

        async def process_tool_calls(self, response, context, execute_tool_callback):
            if any(
                getattr(item, "type", None) not in {"message", "reasoning"}
                for item in response.output
            ):
                raise ValueError(
                    "unexpected tool or other output; no execution permitted"
                )
            return 0, context, []

    return Client


def run_turn(packet, slot_gate, *, base_agent, source_client, source_protocol):
    """Fresh agent and empty protocol for every turn; host owns all delivery."""
    prompts = PacketPrompts(packet)
    client = verification_client(source_client)(slot_gate)
    agent = base_agent(
        client,
        name=packet["actor"],
        model_name=slot_gate.plan["model"],
        max_conversation_steps=1,
        environment_name="SealedVerification",
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
        raise ValueError("exactly one tool-free response required")
    return result["response"]


class PinnedInvoker:
    """Production adapter with source and installed-version checks, no key setup."""

    def __init__(self, *, base_agent, source_client, source_protocol, dependencies):
        self.base_agent, self.source_client, self.source_protocol = (
            base_agent,
            source_client,
            source_protocol,
        )
        self.dependencies = dict(dependencies)
        self.verify()

    def verify(self):
        path = inspect.getsourcefile(self.source_protocol)
        if (
            not path
            or hashlib.sha256(Path(path).read_bytes()).hexdigest() != SOURCE_SHA256
        ):
            raise ValueError("pinned Colosseum protocol source differs")
        for name in ("openai", "terrarium-agents", "inspect-ai"):
            if self.dependencies.get(name) != importlib.metadata.version(name):
                raise ValueError("installed runtime differs from reviewed phase")
        if (
            self.base_agent.__module__ != "terrarium.agents.base"
            or self.source_client.__module__ != "terrarium.llm.clients.openai_client"
        ):
            raise ValueError("reviewed source agent/client classes required")

    def __call__(self, packet, slot_gate):
        self.verify()
        return run_turn(
            packet,
            slot_gate,
            base_agent=self.base_agent,
            source_client=self.source_client,
            source_protocol=self.source_protocol,
        )
