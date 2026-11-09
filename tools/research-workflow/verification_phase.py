"""Prepare and execute explicitly approved verification phases through one gate.

Preparation never instantiates a provider, RequestGate or budget book. Execution
requires an externally supplied, reviewed gate and source callable. No credential
loader or live CLI. A completed/error/interrupted phase is never auto-resumed.
"""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/absence-harness"))
from artifact_capture import ArtifactStore  # noqa: E402
from relay_contract import ReceiptRegistry, canonical, labels, sha, validate_reply  # noqa: E402
from verification_state import check_capture  # noqa: E402
from verification_turn import api_params, SOURCE_SHA256, PinnedInvoker  # noqa: E402
from request_gate import save_json  # noqa: E402

EXPECTED = b"small-group-v1\n"
WRONG = b"other-contents\n"
KEY = "artifact"
CODE_FILES = [
    "tools/absence-harness/relay_contract.py",
    "tools/absence-harness/verification_state.py",
    "tools/absence-harness/verification_turn.py",
    "tools/absence-harness/artifact_capture.py",
    "tools/research-workflow/verification_phase.py",
    "tools/research-workflow/request_gate.py",
    "tools/research-workflow/reservations.py",
    "tools/research-workflow/attempts.py",
]


def cell_specs(phase):
    if phase == "controls":
        # Fixed before execution; no order selected from observed responses.
        return [
            {"state": state, "mode": mode, "actors": ["solo"]}
            for state, mode in [
                ("wrong", "blind"),
                ("correct", "proof"),
                ("correct", "blind"),
                ("wrong", "proof"),
            ]
        ]
    if phase == "relay":
        return [
            {"state": state, "mode": mode, "actors": ["A", "B", "C"]}
            for state, mode in [
                ("correct", "relay"),
                ("wrong", "no_peer"),
                ("wrong", "relay"),
                ("correct", "no_peer"),
            ]
        ]
    raise ValueError("only reviewed reduced phases are implemented")


def export(database, destination, run_id, tick):
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/absence-harness/artifact_capture.py"),
            str(database),
            str(destination),
            "--run-id",
            run_id,
            "--decision-tick",
            str(tick),
        ],
        check=True,
        capture_output=True,
    )


def prepare_phase(
    destination, *, phase, run_id, evidence_kind, source_revision, dependencies
):
    """Authored task states and an exact manifest, not agent-generated evidence."""
    if evidence_kind not in {"synthetic_fixture", "recorded_agent_trace"}:
        raise ValueError("explicit intended response provenance required")
    if (
        not source_revision
        or not isinstance(dependencies, dict)
        or not dependencies.get("openai")
    ):
        raise ValueError("source revision and dependency versions required")
    from relay_contract import identifier

    identifier(run_id)
    root = Path(destination)
    root.mkdir(parents=False, exist_ok=False, mode=0o700)
    cells, files = [], {}
    for index, spec in enumerate(cell_specs(phase)):
        cell_id = f"cell-{index}"
        folder = root / cell_id
        folder.mkdir(mode=0o700)
        state_id = f"{run_id}-{cell_id}"
        store = ArtifactStore(folder / "state.db", run_id=state_id)
        store.write(KEY, EXPECTED if spec["state"] == "correct" else WRONG)
        tick = store.seal()
        export(folder / "state.db", folder / "before.json", state_id, tick)
        verification = check_capture(
            folder / "state.db",
            folder / "before.json",
            run_id=state_id,
            capture_tick=tick,
            key=KEY,
            expected_sha256=sha(EXPECTED),
        )
        save_json(folder / "before-verification.json", verification)
        for name in ("state.db", "before.json", "before-verification.json"):
            path = folder / name
            files[str(path.relative_to(root))] = sha(path.read_bytes())
        cells.append(
            {"cell_id": cell_id, "state_run_id": state_id, "capture_tick": tick, **spec}
        )
    config = {
        "version": "verification-phase-v1",
        "phase": phase,
        "run_id": run_id,
        "preparation_kind": "experimenter_prepared_states; no model execution",
        "intended_response_evidence_kind": evidence_kind,
        "development_only": True,
        "family_root": "existing-exact-byte-development",
        "source_revision": source_revision,
        "dependencies": dependencies,
        "colosseum_protocol_sha256": SOURCE_SHA256,
        "task": {"required_key": KEY, "expected_sha256": sha(EXPECTED)},
        "cells": cells,
        "code_sha256": {p: sha((ROOT / p).read_bytes()) for p in CODE_FILES},
        "prepared_files_sha256": files,
        "request_settings": {
            "reasoning": "none",
            "tools": [],
            "max_output_tokens": 512,
            "max_request_bytes": 8192,
            "retries": 0,
        },
    }
    config_digest = save_json(root / "config.json", config)
    plan = {
        "run_id": run_id,
        "model": "gpt-5.6-luna",
        "sdk_version": dependencies["openai"],
        "config_sha256": config_digest,
        "max_requests": 4 if phase == "controls" else 12,
        "max_output_tokens": 512,
        "max_request_bytes": 8192,
        "model_context_tokens": 1050000,
        "maximum_request_usd": "0.60",
        "input_usd_per_million_upper": "0.50",
        "output_usd_per_million_upper": "1.80",
        "price_evidence": "https://developers.openai.com/api/docs/models/gpt-5.6-luna; "
        "reviewed 2026-11-08; conservative long-context/cache-write rates; not invoice",
        "evidence_kind": evidence_kind,
    }
    save_json(root / "request-plan.json", plan)
    return plan


def verified_config(root, gate_plan):
    root = Path(root)
    raw = (root / "config.json").read_bytes()
    if (
        sha(raw) != gate_plan["config_sha256"]
        or json.loads((root / "request-plan.json").read_text()) != gate_plan
    ):
        raise ValueError("execution plan differs from prepared phase")
    config = json.loads(raw)
    if (
        config["version"] != "verification-phase-v1"
        or config["run_id"] != gate_plan["run_id"]
    ):
        raise ValueError("phase identity changed")
    if set(config["code_sha256"]) != set(CODE_FILES):
        raise ValueError("code manifest coverage changed")
    if config["task"] != {"required_key": KEY, "expected_sha256": sha(EXPECTED)}:
        raise ValueError("task contract changed")
    settings = {
        "reasoning": "none",
        "tools": [],
        "max_output_tokens": 512,
        "max_request_bytes": 8192,
        "retries": 0,
    }
    limits = {
        "model": "gpt-5.6-luna",
        "max_output_tokens": 512,
        "max_request_bytes": 8192,
        "model_context_tokens": 1050000,
        "maximum_request_usd": "0.60",
        "input_usd_per_million_upper": "0.50",
        "output_usd_per_million_upper": "1.80",
        "sdk_version": config["dependencies"]["openai"],
        "evidence_kind": config["intended_response_evidence_kind"],
    }
    if config["request_settings"] != settings or any(
        gate_plan[k] != v for k, v in limits.items()
    ):
        raise ValueError("bounded request contract changed")
    expected_specs = cell_specs(config["phase"])
    if [
        {k: c[k] for k in ("state", "mode", "actors")} for c in config["cells"]
    ] != expected_specs:
        raise ValueError("cell schedule changed")
    for index, cell in enumerate(config["cells"]):
        if (
            cell["cell_id"] != f"cell-{index}"
            or cell["state_run_id"] != f"{config['run_id']}-cell-{index}"
            or cell["capture_tick"] != 2
        ):
            raise ValueError("cell/capture identity changed")
    expected_files = {
        f"{c['cell_id']}/{n}"
        for c in config["cells"]
        for n in ("state.db", "before.json", "before-verification.json")
    }
    if set(config["prepared_files_sha256"]) != expected_files:
        raise ValueError("prepared capture manifest coverage changed")
    if gate_plan["max_requests"] != sum(len(c["actors"]) for c in config["cells"]):
        raise ValueError("one phase ceiling must cover all slots")
    for name, digest in config["code_sha256"].items():
        if sha((ROOT / name).read_bytes()) != digest:
            raise ValueError("executing code differs from prepared manifest")
    for name, digest in config["prepared_files_sha256"].items():
        if sha((root / name).read_bytes()) != digest:
            raise ValueError("prepared state changed")
    for cell in config["cells"]:
        check_capture(
            root / cell["cell_id"] / "state.db",
            root / cell["cell_id"] / "before.json",
            run_id=cell["state_run_id"],
            capture_tick=cell["capture_tick"],
            key=config["task"]["required_key"],
            expected_sha256=config["task"]["expected_sha256"],
        )
    return config


def response_text(raw):
    """Extract only the saved completed response, without fabricated fallbacks."""
    if raw.get("status") != "completed" or not isinstance(raw.get("output"), list):
        raise ValueError("completed provider response required")
    text = []
    for item in raw["output"]:
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            raise ValueError("unexpected provider output type")
        for part in item.get("content", []):
            if part.get("type") != "output_text" or not isinstance(
                part.get("text"), str
            ):
                raise ValueError("refusal or invalid response content")
            text.append(part["text"])
    return "".join(text)


class SlotGate:
    """One exact request; binding is persisted before mark_started and SDK dispatch."""

    def __init__(self, gate, packet, slot):
        self.gate, self.plan, self.slot = gate, deepcopy(gate.plan), slot
        self.expected, self.used = api_params(packet, self.plan), False

    def create(self, params):
        if self.used or params != self.expected:
            raise ValueError("slot request reused or input/settings changed")
        self.used = True

        def bind(attempt_id, request_sha256):
            save_json(
                self.slot / "attempt-binding.json",
                {"attempt_id": attempt_id, "request_sha256": request_sha256},
            )

        return self.gate.create(params, before_dispatch=bind)


def phase_attempts(gate):
    return [
        r
        for r in gate.book.inspect()["requests"]
        if r["attempt"]["run_id"] == gate.plan["run_id"]
    ]


def execute_phase(root, gate, invoke):
    """Caller owns approval, fresh reconciliation and reviewed source dependencies.

    invoke(packet, SlotGate) must use the pinned one-turn adapter. Any terminal
    failure persists a halt; a phase lock beside the SAME book prevents replays
    through a new output directory. Only an explicit future recovery review can
    resolve it; this code never deletes the lock or repeats a response.
    """
    root = Path(root)
    config = verified_config(root, gate.plan)
    if gate.destination.resolve() != (root / "requests").resolve():
        raise ValueError("request evidence must stay inside this phase")
    if gate.plan["evidence_kind"] == "recorded_agent_trace":
        if (
            not isinstance(invoke, PinnedInvoker)
            or invoke.dependencies != config["dependencies"]
        ):
            raise ValueError("reviewed pinned runtime required for recorded source")
        invoke.verify()
    if phase_attempts(gate):
        raise ValueError("phase already has attempts; reconcile, never auto-resume")
    marker = gate.book.path.resolve().parent / (
        "verification-phase-" + sha(gate.plan["run_id"].encode()) + ".json"
    )
    save_json(
        marker,
        {
            "run_id": gate.plan["run_id"],
            "config_sha256": gate.plan["config_sha256"],
            "destination": str(root.resolve()),
            "status": "claimed; never release on timeout",
        },
    )
    planned = [f"{c['cell_id']}-{a}" for c in config["cells"] for a in c["actors"]]
    completed, outputs, error = [], [], None
    try:
        for cell in config["cells"]:
            folder = root / cell["cell_id"]
            before = json.loads((folder / "before.json").read_text())
            registry = ReceiptRegistry(
                before,
                key=config["task"]["required_key"],
                expected_sha256=config["task"]["expected_sha256"],
            )
            packets, replies = {}, {}
            for actor in cell["actors"]:
                slot_id = f"{cell['cell_id']}-{actor}"
                slot = root / slot_id
                slot.mkdir(mode=0o700, exist_ok=False)
                messages, receipts = [], []
                if actor == "solo":
                    receipts = ["r1"] if cell["mode"] == "proof" else []
                elif actor == "B":
                    receipts = ["r1"]
                if cell["mode"] == "relay" and actor in {"B", "C"}:
                    parent = "A" if actor == "B" else "B"
                    previous = replies[parent]
                    messages = [
                        {
                            "id": "m-" + parent,
                            "sender": parent,
                            "text": previous["message_ascii"]
                            + " Decision: "
                            + previous["decision"],
                        }
                    ]
                    receipts += list(registry.forward(packets[parent], previous))
                    receipts = sorted(set(receipts))
                packet = registry.packet(
                    actor,
                    receipt_ids=receipts,
                    messages=messages,
                    claim=actor in {"solo", "A"},
                )
                packets[actor] = packet
                save_json(slot / "input.json", packet)
                save_json(
                    slot / "intent.json",
                    {
                        "slot_id": slot_id,
                        "logical_tick": len(completed) + 1,
                        "input_sha256": sha(canonical(packet).encode()),
                        "parent_slot": f"{cell['cell_id']}-{'A' if actor == 'B' else 'B'}"
                        if cell["mode"] == "relay" and actor in {"B", "C"}
                        else None,
                        "held_receipt_ids": receipts,
                        "receipt_namespace": cell["state_run_id"],
                        "capture_sha256": sha((folder / "before.json").read_bytes()),
                    },
                )
                boundary = SlotGate(gate, packet, slot)
                raw = invoke(deepcopy(packet), boundary)
                save_json(slot / "raw-result.json", {"text": raw})
                reply = validate_reply(raw, packet)
                registry.forward(packet, reply)
                binding = json.loads((slot / "attempt-binding.json").read_text())
                attempt_id = binding["attempt_id"]
                saved_request = gate.destination / (attempt_id + "-request.json")
                saved_response = gate.destination / (attempt_id + "-response.json")
                if sha(saved_request.read_bytes()) != binding["request_sha256"]:
                    raise ValueError("request binding hash differs")
                if json.loads(saved_request.read_text()) != {
                    **boundary.expected,
                    "store": False,
                }:
                    raise ValueError("saved request differs from reviewed slot input")
                envelope = json.loads(saved_response.read_text())
                if (
                    envelope["attempt_id"] != attempt_id
                    or envelope["evidence_kind"] != gate.plan["evidence_kind"]
                    or response_text(envelope["response"]) != raw
                ):
                    raise ValueError("source text differs from saved provider response")
                attempts = phase_attempts(gate)
                matching = [
                    r for r in attempts if r["attempt_id"] == binding["attempt_id"]
                ]
                if (
                    not boundary.used
                    or len(attempts) != len(completed) + 1
                    or len(matching) != 1
                    or matching[0]["state"] != "settled"
                    or matching[0]["attempt"]["status"] != "success"
                ):
                    raise ValueError(
                        "slot must join exactly one settled successful attempt"
                    )
                save_json(slot / "reply.json", reply)
                replies[actor] = reply
                completed.append(slot_id)
            check_capture(
                folder / "state.db",
                folder / "before.json",
                run_id=cell["state_run_id"],
                capture_tick=cell["capture_tick"],
                key=config["task"]["required_key"],
                expected_sha256=config["task"]["expected_sha256"],
            )
    except BaseException as exc:
        error = type(exc).__name__  # no potentially sensitive provider exception text
    finally:
        save_json(
            root / "finalization-started.json",
            {
                "error_type": error,
                "completed_slots": completed,
                "uncompleted_slots": [s for s in planned if s not in completed],
            },
        )
        export_error = None
        save_json(root / "request-states.json", phase_attempts(gate))
        try:
            gate.book.export_attempts(root / "attempts.jsonl")
        except Exception as exc:
            export_error = type(exc).__name__
            error = error or "AttemptExportFailed"
        capture_errors = {}
        for cell in config["cells"]:
            folder = root / cell["cell_id"]
            verified = None
            try:
                export(
                    folder / "state.db",
                    folder / "after.json",
                    cell["state_run_id"],
                    cell["capture_tick"],
                )
                if (folder / "before.json").read_bytes() != (
                    folder / "after.json"
                ).read_bytes():
                    raise ValueError("immutable state changed during verification")
                verified = check_capture(
                    folder / "state.db",
                    folder / "after.json",
                    run_id=cell["state_run_id"],
                    capture_tick=cell["capture_tick"],
                    key=config["task"]["required_key"],
                    expected_sha256=config["task"]["expected_sha256"],
                )
                save_json(folder / "after-verification.json", verified)
            except Exception as exc:
                capture_errors[cell["cell_id"]] = type(exc).__name__
                error = error or "CaptureUnresolved"
            for actor in cell["actors"]:
                slot_id = f"{cell['cell_id']}-{actor}"
                slot = root / slot_id
                packet = (
                    json.loads((slot / "input.json").read_text())
                    if (slot / "input.json").exists()
                    else None
                )
                reply = (
                    json.loads((slot / "reply.json").read_text())
                    if slot_id in completed
                    else None
                )
                decision = reply["decision"] if reply else "ERROR"
                outputs.append(
                    {
                        "slot_id": slot_id,
                        "cell_id": cell["cell_id"],
                        "actor": actor,
                        "status": "valid"
                        if reply
                        else "error"
                        if slot.exists()
                        else "unattempted",
                        "packet": packet,
                        "reply": reply,
                        "capture_status": "verified" if verified else "unresolved",
                        "outcome": labels(
                            decision, packet, actual_matches=verified["actual_matches"]
                        )
                        if verified and packet is not None
                        else None,
                    }
                )
        result = {
            "status": "halted" if error else "complete",
            "error_type": error,
            "capture_errors": capture_errors,
            "attempt_export_error": export_error,
            "evidence_kind": gate.plan["evidence_kind"],
            "origin": "introduced_claim_and_state",
            "family_root": config["family_root"],
            "completed_slots": completed,
            "uncompleted_slots": [s for s in planned if s not in completed],
            "records": outputs,
            "scope": "development only; no independent-node or swarm-scale inference",
        }
        save_json(root / "phase-result.json", result)
        manifest = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError("symlink in phase evidence")
            if path.is_file():
                manifest[str(path.relative_to(root))] = sha(path.read_bytes())
        save_json(root / "evidence-manifest.json", manifest)
    return result
