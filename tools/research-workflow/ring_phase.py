"""Bounded synchronous ring with authentic, model-selected receipt forwarding.

Preparation is offline. Execution takes an explicitly supplied accounted gate,
not credentials. Source audit reconstructs every packet from earlier saved
responses; evaluator truth comes from sealed host bytes, never agent answers.
"""
from copy import deepcopy
from decimal import Decimal
import json
import sqlite3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/absence-harness"))
from artifact_capture import ArtifactStore  # noqa: E402
from relay_contract import ReceiptRegistry, canonical, identifier, labels, sha, validate_reply  # noqa: E402
from ring_turn import MODEL, VERSION, api_params, PinnedRingInvoker  # noqa: E402
from verification_state import check_capture  # noqa: E402
from verification_phase import export, response_text, phase_attempts  # noqa: E402
from request_gate import save_json, RequestStopped  # noqa: E402
from comparison_request_gate import ComparisonRequestGate  # noqa: E402
from attempts import validate_attempts  # noqa: E402

ACTORS = tuple(f"A{i}" for i in range(8))
ROUNDS = 3
KEY = "artifact"
EXPECTED, WRONG = b"sealed-ring-v1\n", b"different-contents\n"
PHASE_CAP = "3.00"
RESERVATION = "0.2314224"
OBSERVER = {"policy": "distinct-conditional-v2", "seed": 13,
            "baselines": ["receipt", "claim_trusting", "always_clear", "always_abstain"],
            "scope": "local deterministic replay only; no paid monitor"}
PRICE_EVIDENCE = (
    "https://developers.openai.com/api/docs/pricing; "
    "https://developers.openai.com/api/docs/guides/prompt-caching; "
    "reviewed 2026-11-09; standard tier, no caching, full context, "
    "long-context and 10% regional upper rates; not invoice"
)
CODE_FILES = (
    "tools/research-workflow/ring_phase.py",
    "tools/research-workflow/ring_inspect.py",
    "tools/research-workflow/verification_inspect.py",
    "tools/research-workflow/observation_masks.py",
    "tools/absence-harness/ring_turn.py",
    "tools/absence-harness/relay_contract.py",
    "tools/absence-harness/verification_state.py",
    "tools/absence-harness/artifact_capture.py",
    "tools/absence-harness/verification_turn.py",
    "tools/absence-harness/model_comparison_turn.py",
    "tools/research-workflow/verification_phase.py",
    "tools/research-workflow/comparison_request_gate.py",
    "tools/research-workflow/request_gate.py",
    "tools/research-workflow/reservations.py",
    "tools/research-workflow/recovery_reservations.py",
    "tools/research-workflow/attempts.py",
)


def read(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("regular evidence required")
    return json.loads(path.read_text())


def specs():
    return [
        {"cell_id": f"cell-{i}", "state": state, "mode": mode}
        for i, (state, mode) in enumerate((
            ("wrong", "ring"), ("correct", "no_peer"),
            ("correct", "ring"), ("wrong", "no_peer")))
    ]


def slots(cells):
    return [{"slot_id": f"{c['cell_id']}-r{r}-{a}", "cell_id": c["cell_id"],
             "round": r, "actor": a}
            for c in cells for r in range(ROUNDS) for a in ACTORS]


def plan_for(config, digest):
    return {
        "run_id": config["run_id"], "model": MODEL,
        "sdk_version": config["dependencies"]["openai"], "config_sha256": digest,
        "max_requests": len(slots(config["cells"])), "max_output_tokens": 512,
        "max_request_bytes": 8192, "model_context_tokens": 1050000,
        "maximum_request_usd": RESERVATION,
        "input_usd_per_million_upper": "0.22",
        "output_usd_per_million_upper": "0.825",
        "price_evidence": PRICE_EVIDENCE, "evidence_kind": config["evidence_kind"],
    }


def prepare(root, *, run_id, revision, dependencies, evidence_kind):
    identifier(run_id)
    if evidence_kind not in {"synthetic_fixture", "recorded_agent_trace"}:
        raise ValueError("explicit response provenance required")
    if not revision or any(not dependencies.get(n) for n in (
            "openai", "terrarium-agents", "inspect-ai")):
        raise ValueError("source revision and runtime dependencies required")
    root = Path(root)
    root.mkdir(mode=0o700, exist_ok=False)
    cells, artifacts = [], {}
    for spec in specs():
        folder = root / spec["cell_id"]
        folder.mkdir(mode=0o700)
        state_id = run_id + "-" + spec["cell_id"]
        store = ArtifactStore(folder / "state.db", run_id=state_id)
        store.write(KEY, EXPECTED if spec["state"] == "correct" else WRONG)
        tick = store.seal()
        cell = {**spec, "state_run_id": state_id, "capture_tick": tick}
        export(folder / "state.db", folder / "before.json", state_id, tick)
        save_json(folder / "before-verification.json", capture(folder, cell))
        cells.append(cell)
        for name in ("state.db", "before.json", "before-verification.json"):
            artifacts[f"{spec['cell_id']}/{name}"] = sha((folder / name).read_bytes())
    config = {
        "version": VERSION, "run_id": run_id, "source_revision": revision,
        "dependencies": dependencies, "evidence_kind": evidence_kind,
        "development_only": True, "family_root": "sealed-single-artifact-development",
        "actors": list(ACTORS), "rounds": ROUNDS, "cells": cells, "observer": deepcopy(OBSERVER),
        "planned_slots": slots(cells), "phase_cap_usd": PHASE_CAP,
        "shared_cap_usd": "25", "proof_seed": "A0",
        "topology": "directed ring; predecessor from previous round only",
        "memory": "condensed own/received message and decision history; held receipts",
        "task": {"required_key": KEY, "expected_sha256": sha(EXPECTED)},
        "code_sha256": {p: sha((ROOT / p).read_bytes()) for p in CODE_FILES},
        "prepared_files_sha256": artifacts,
        "interpretation": "fixed N=8 development; 4 authored episodes in one family; "
                          "turns/messages are dependent, not independent samples",
    }
    digest = save_json(root / "config.json", config)
    plan = plan_for(config, digest)
    save_json(root / "request-plan.json", plan)
    return plan


def capture(folder, cell, name="before.json"):
    return check_capture(folder / "state.db", folder / name,
                         run_id=cell["state_run_id"], capture_tick=cell["capture_tick"],
                         key=KEY, expected_sha256=sha(EXPECTED))


def verified(root, plan):
    root = Path(root)
    config = read(root / "config.json")
    if plan != read(root / "request-plan.json") or plan != plan_for(
            config, sha((root / "config.json").read_bytes())):
        raise ValueError("frozen request plan changed")
    identifier(config["run_id"])
    expected_cells = [{**s, "state_run_id": config["run_id"] + "-" + s["cell_id"],
                       "capture_tick": 2} for s in specs()]
    if (config["version"] != VERSION or config["cells"] != expected_cells
            or config["actors"] != list(ACTORS) or config["rounds"] != ROUNDS
            or config["planned_slots"] != slots(expected_cells)
            or config["phase_cap_usd"] != PHASE_CAP
            or config["shared_cap_usd"] != "25" or config["proof_seed"] != "A0"
            or config["observer"] != OBSERVER
            or config["evidence_kind"] not in {"synthetic_fixture", "recorded_agent_trace"}
            or config["task"] != {"required_key": KEY, "expected_sha256": sha(EXPECTED)}
            or config["code_sha256"] != {p: sha((ROOT / p).read_bytes()) for p in CODE_FILES}):
        raise ValueError("frozen ring contract changed")
    expected_names = {f"{c['cell_id']}/{n}" for c in expected_cells
                      for n in ("state.db", "before.json", "before-verification.json")}
    if set(config["prepared_files_sha256"]) != expected_names:
        raise ValueError("capture manifest coverage changed")
    for name, digest in config["prepared_files_sha256"].items():
        if (root / name).is_symlink() or sha((root / name).read_bytes()) != digest:
            raise ValueError("prepared state changed")
    for cell in expected_cells:
        truth = capture(root / cell["cell_id"], cell)
        if truth != read(root / cell["cell_id"] / "before-verification.json"):
            raise ValueError("independent prepared truth changed")
        if truth["actual_matches"] != (cell["state"] == "correct"):
            raise ValueError("prepared bytes differ from planned condition")
    return config


def packet_for(registry, cell, slot, history):
    """Only earlier complete rounds may influence a logical actor's state.

    History is host-owned packet/reply pairs; forwarding validates the original
    packet's registry identity. Receipt copies retain the original seal tick.
    """
    actor, r = slot["actor"], slot["round"]
    messages, held, parents = [], {"r1"} if actor == "A0" else set(), []
    predecessor = ACTORS[(ACTORS.index(actor) - 1) % len(ACTORS)]
    for earlier in range(r):
        own_id = f"{cell['cell_id']}-r{earlier}-{actor}"
        own_packet, own_reply = history[own_id]
        held.update(x["id"] for x in own_packet["receipts"])
        messages.append({"id": f"m-r{earlier}-{actor}", "sender": actor,
                         "text": own_reply["message_ascii"] + " Decision: " + own_reply["decision"]})
        parents.append({"slot_id": own_id, "relation": "self_memory"})
        if cell["mode"] == "ring":
            peer_id = f"{cell['cell_id']}-r{earlier}-{predecessor}"
            peer_packet, peer_reply = history[peer_id]
            forwarded = registry.forward(peer_packet, peer_reply)
            held.update(forwarded)
            messages.append({"id": f"m-r{earlier}-{predecessor}", "sender": predecessor,
                             "text": peer_reply["message_ascii"] + " Decision: " + peer_reply["decision"]})
            parents.append({"slot_id": peer_id, "relation": "peer_delivery",
                            "forward_receipt_ids": list(forwarded)})
    packet = registry.packet(actor, receipt_ids=sorted(held), messages=messages, claim=True)
    return packet, {**slot, "parents": parents, "held_receipt_ids": sorted(held),
                    "input_sha256": sha(canonical(packet).encode()),
                    "receipt_namespace": cell["state_run_id"]}


def phase_commitment(rows):
    total = Decimal(0)
    for row in rows:
        if row["state"] == "cancelled":
            continue
        if row["state"] != "settled" or row["attempt"]["cost_usd"] is None:
            raise RequestStopped("unresolved phase attempt; no continuation")
        total += Decimal(row["attempt"]["cost_usd"])
    return total


class SlotGate:
    def __init__(self, gate, packet, folder):
        self.gate, self.plan, self.folder = gate, deepcopy(gate.plan), folder
        self.expected, self.used = api_params(packet, self.plan), False

    def create(self, params):
        if self.used or params != self.expected:
            raise ValueError("slot reused or exact input/settings changed")
        self.used = True
        committed = phase_commitment(phase_attempts(self.gate))
        if committed + Decimal(self.plan["maximum_request_usd"]) > Decimal(PHASE_CAP):
            raise RequestStopped("incremental phase cap cannot fit next full reservation")
        save_json(self.folder / "phase-budget-check.json", {
            "settled_phase_usd": str(committed), "next_reservation_usd": RESERVATION,
            "phase_cap_usd": PHASE_CAP})
        return self.gate.create(params, before_dispatch=lambda aid, digest: save_json(
            self.folder / "attempt-binding.json",
            {"attempt_id": aid, "request_sha256": digest}))


def audit_accounting(root, plan, row):
    """Independently join model, usage, tier and ledger cost to original bytes."""
    aid, attempt = row["attempt_id"], row["attempt"]
    if (row["maximum_usd"] != plan["maximum_request_usd"]
            or attempt["attempt_id"] != aid or attempt["request_id"] != aid
            or attempt["run_id"] != plan["run_id"] or attempt["provider"] != "openai"
            or attempt["requested_model"] != plan["model"]
            or attempt["config_sha256"] != plan["config_sha256"]
            or attempt["sdk_version"] != plan["sdk_version"]
            or attempt["attempt_index"] != 0 or attempt["stage"] != "generation"):
        raise ValueError("ledger model/config/reservation identity changed")
    path = root / "requests" / (aid + "-response.json")
    if not path.exists():
        if row["state"] not in {"uncertain", "reserved", "cancelled"} or attempt["cost_usd"] is not None:
            raise ValueError("missing response without retained uncertainty")
        return
    envelope = read(path)
    if envelope["attempt_id"] != aid or envelope["evidence_kind"] != plan["evidence_kind"]:
        raise ValueError("provider provenance differs")
    raw, usage = envelope["response"], envelope["response"].get("usage") or {}
    detail = usage.get("input_tokens_details") or {}
    reasoning = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
    billing = (raw.get("model") == plan["model"] and raw.get("service_tier") == "default"
               and type(detail.get("cache_write_tokens")) is int and detail["cache_write_tokens"] == 0
               and type(detail.get("cached_tokens")) is int and detail["cached_tokens"] == 0)
    numbers = (all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("input_tokens", "output_tokens"))
               and type(usage.get("total_tokens")) is int
               and usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]
               and (reasoning is None or type(reasoning) is int and 0 <= reasoning <= usage["output_tokens"]))
    if attempt["resolved_model"] != (raw.get("model") or "unknown"):
        raise ValueError("resolved model differs from saved provider response")
    if billing and numbers:
        cost = (Decimal(usage["input_tokens"]) * Decimal(plan["input_usd_per_million_upper"])
                + Decimal(usage["output_tokens"]) * Decimal(plan["output_usd_per_million_upper"])) / 1000000
        if (attempt["cost_usd"] is None or Decimal(attempt["cost_usd"]) != cost
                or any(attempt[k] != usage[k] for k in ("input_tokens", "output_tokens"))):
            raise ValueError("provider usage and ledger cost differ")
    elif any(attempt[k] is not None for k in ("cost_usd", "input_tokens", "output_tokens")):
        raise ValueError("invalid billing route must retain unknown cost")
    success = (billing and numbers and raw.get("status") == "completed"
               and usage["input_tokens"] <= plan["model_context_tokens"]
               and usage["output_tokens"] <= plan["max_output_tokens"])
    if row["state"] == "settled" and (not success or attempt["status"] != "success"):
        raise ValueError("settled success lacks valid bounded provider usage")


def check_join(root, plan, packet, folder, state_rows, raw, *, used):
    binding = read(folder / "attempt-binding.json")
    aid = binding["attempt_id"]
    request = root / "requests" / (aid + "-request.json")
    if (sha(request.read_bytes()) != binding["request_sha256"]
            or read(request) != api_params(packet, plan)):
        raise ValueError("request does not match actual packet")
    envelope = read(root / "requests" / (aid + "-response.json"))
    matches = [x for x in state_rows if x["attempt_id"] == aid]
    if (not used or len(matches) != 1 or matches[0]["state"] != "settled"
            or matches[0]["attempt"]["status"] != "success"
            or envelope["attempt_id"] != aid
            or envelope["evidence_kind"] != plan["evidence_kind"]
            or response_text(envelope["response"]) != raw):
        raise ValueError("source reply lacks one successful bound provider response")
    audit_accounting(root, plan, matches[0])
    return aid


def execute(root, gate, invoke):
    root = Path(root)
    config = verified(root, gate.plan)
    if not isinstance(gate, ComparisonRequestGate):
        raise ValueError("reviewed low standard uncached accounting gate required")
    if gate.destination.resolve() != (root / "requests").resolve():
        raise ValueError("request evidence must stay in this phase")
    if gate.plan["evidence_kind"] == "recorded_agent_trace":
        if not isinstance(invoke, PinnedRingInvoker) or invoke.dependencies != config["dependencies"]:
            raise ValueError("pinned production invoker required")
        invoke.verify()
    with sqlite3.connect(gate.book.path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        reconciliation = json.loads(db.execute("SELECT reconciliation FROM budget").fetchone()[0])
    if Decimal(reconciliation["cap_usd"]) != Decimal(config["shared_cap_usd"]):
        raise ValueError("existing shared budget cap differs from reviewed cap")
    if phase_attempts(gate):
        raise ValueError("existing attempts; never auto-resume")
    save_json(gate.book.path.resolve().parent / ("ring-" + sha(config["run_id"].encode()) + ".json"),
              {"run_id": config["run_id"], "config_sha256": gate.plan["config_sha256"],
               "status": "claimed once; never auto-resume"})
    completed, failure, capture_errors = [], None, {}
    try:
        for cell in config["cells"]:
            registry = ReceiptRegistry(read(root / cell["cell_id"] / "before.json"),
                                       key=KEY, expected_sha256=sha(EXPECTED))
            history = {}
            for slot in slots([cell]):
                verified(root, gate.plan)
                packet, intent = packet_for(registry, cell, slot, history)
                folder = root / slot["slot_id"]
                folder.mkdir(mode=0o700, exist_ok=False)
                save_json(folder / "input.json", packet)
                save_json(folder / "intent.json", intent)
                boundary = SlotGate(gate, packet, folder)
                raw = invoke(deepcopy(packet), boundary)
                save_json(folder / "raw-result.json", {"text": raw})
                reply = validate_reply(raw, packet)
                registry.forward(packet, reply)
                states = phase_attempts(gate)
                check_join(root, gate.plan, packet, folder, states, raw, used=boundary.used)
                if len(states) != len(completed) + 1:
                    raise ValueError("one accounted attempt per completed slot required")
                save_json(folder / "reply.json", reply)
                history[slot["slot_id"]] = (packet, reply)
                completed.append(slot["slot_id"])
    except BaseException as exc:
        failure = type(exc).__name__
    finally:
        save_json(root / "request-states.json", phase_attempts(gate))
        export_error = None
        try:
            gate.book.export_attempts(root / "attempts.jsonl")
        except Exception as exc:
            export_error, failure = type(exc).__name__, failure or "AttemptExportFailed"
        truths = {}
        for cell in config["cells"]:
            folder = root / cell["cell_id"]
            try:
                export(folder / "state.db", folder / "after.json", cell["state_run_id"], cell["capture_tick"])
                if (folder / "before.json").read_bytes() != (folder / "after.json").read_bytes():
                    raise ValueError("sealed state changed during phase")
                checked = capture(folder, cell, "after.json")
                save_json(folder / "after-verification.json", checked)
                truths[cell["cell_id"]] = checked["actual_matches"]
            except Exception as exc:
                capture_errors[cell["cell_id"]] = type(exc).__name__
                failure = failure or "CaptureUnresolved"
        rows = []
        for slot in config["planned_slots"]:
            folder = root / slot["slot_id"]
            packet = read(folder / "input.json") if (folder / "input.json").exists() else None
            reply = read(folder / "reply.json") if slot["slot_id"] in completed else None
            outcome = (labels(reply["decision"] if reply else "ERROR", packet,
                              actual_matches=truths[slot["cell_id"]])
                       if packet is not None and slot["cell_id"] in truths else None)
            rows.append({**slot, "status": "valid" if reply else "error" if folder.exists() else "unattempted",
                         "packet": packet, "reply": reply, "outcome": outcome})
        result = {"status": "halted" if failure else "complete", "error_type": failure,
                  "evidence_kind": config["evidence_kind"], "family_root": config["family_root"],
                  "capture_errors": capture_errors, "attempt_export_error": export_error,
                  "completed_slots": completed, "records": rows,
                  "interpretation": config["interpretation"]}
        save_json(root / "phase-result.json", result)
        manifest = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError("symlink in phase evidence")
            if path.is_file():
                manifest[str(path.relative_to(root))] = sha(path.read_bytes())
        save_json(root / "evidence-manifest.json", manifest)
    return result


def verify_source(root):
    """Rebuild causal exposure, source joins and truth before using any result."""
    root = Path(root)
    manifest = read(root / "evidence-manifest.json")
    actual = {str(p.relative_to(root)) for p in root.rglob("*")
              if p.is_file() and p != root / "evidence-manifest.json"}
    if set(manifest) != actual:
        raise ValueError("manifest must cover every consumed artifact")
    for name, digest in manifest.items():
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or sha(path.read_bytes()) != digest:
            raise ValueError("evidence bytes changed")
    plan = read(root / "request-plan.json")
    config = verified(root, plan)
    result, state_rows = read(root / "phase-result.json"), read(root / "request-states.json")
    validate_attempts([x["attempt"] for x in state_rows])
    if (len({x["attempt_id"] for x in state_rows}) != len(state_rows)
            or any(x["attempt"]["run_id"] != plan["run_id"] for x in state_rows)):
        raise ValueError("duplicate or foreign phase attempts")
    if result["attempt_export_error"] is None:
        exported = [json.loads(line) for line in (root / "attempts.jsonl").read_text().splitlines()]
        if [x for x in exported if x["run_id"] == plan["run_id"]] != [
                x["attempt"] for x in state_rows if x["state"] not in {"reserved", "cancelled"}]:
            raise ValueError("attempt export differs")
    if [{k: x[k] for k in ("slot_id", "cell_id", "round", "actor")} for x in result["records"]] != config["planned_slots"]:
        raise ValueError("planned source slots changed or omitted")
    bound, accepted, halted, truths = set(), [], False, {}
    accounted = []
    rows = {x["slot_id"]: x for x in result["records"]}
    for cell in config["cells"]:
        folder = root / cell["cell_id"]
        if cell["cell_id"] not in result["capture_errors"]:
            if (folder / "before.json").read_bytes() != (folder / "after.json").read_bytes():
                raise ValueError("post-phase state differs")
            checked = capture(folder, cell, "after.json")
            if checked != read(folder / "after-verification.json"):
                raise ValueError("post-phase independent truth differs")
            truths[cell["cell_id"]] = checked["actual_matches"]
        registry = ReceiptRegistry(read(folder / "before.json"), key=KEY, expected_sha256=sha(EXPECTED))
        history = {}
        for slot in slots([cell]):
            row, path = rows[slot["slot_id"]], root / slot["slot_id"]
            if row["status"] == "unattempted":
                if path.exists() or row["packet"] is not None or row["reply"] is not None or row["outcome"] is not None:
                    raise ValueError("unattempted slot has evidence")
                halted = True
                continue
            if halted:
                raise ValueError("execution continued after terminal failure")
            packet, intent = packet_for(registry, cell, slot, history)
            if read(path / "input.json") != packet or row["packet"] != packet or read(path / "intent.json") != intent:
                raise ValueError("causal packet or delivery provenance changed")
            binding_path = path / "attempt-binding.json"
            if binding_path.exists():
                binding = read(binding_path)
                aid = binding["attempt_id"]
                if aid in bound or not any(x["attempt_id"] == aid for x in state_rows):
                    raise ValueError("reused or unaccounted attempt binding")
                matched = next(x for x in state_rows if x["attempt_id"] == aid)
                audit_accounting(root, plan, matched)
                committed = phase_commitment(accounted)
                if committed + Decimal(RESERVATION) > Decimal(PHASE_CAP):
                    raise ValueError("phase cap exceeded before dispatch")
                if read(path / "phase-budget-check.json") != {
                        "settled_phase_usd": str(committed), "next_reservation_usd": RESERVATION,
                        "phase_cap_usd": PHASE_CAP}:
                    raise ValueError("phase budget check differs from accounted prefix")
                accounted.append(matched)
                bound.add(aid)
                request = root / "requests" / (aid + "-request.json")
                if sha(request.read_bytes()) != binding["request_sha256"] or read(request) != api_params(packet, plan):
                    raise ValueError("failed or successful request differs from slot")
            if row["status"] == "valid":
                raw = read(path / "raw-result.json")["text"]
                check_join(root, plan, packet, path, state_rows, raw, used=True)
                reply = validate_reply(raw, packet)
                registry.forward(packet, reply)
                if row["reply"] != reply or read(path / "reply.json") != reply:
                    raise ValueError("source reply changed")
                history[slot["slot_id"]] = (packet, reply)
                accepted.append(slot["slot_id"])
            elif row["status"] == "error" and row["reply"] is None:
                halted = True
            else:
                raise ValueError("invalid source status")
            expected = (labels(row["reply"]["decision"] if row["reply"] else "ERROR", packet,
                               actual_matches=truths[cell["cell_id"]])
                        if cell["cell_id"] in truths else None)
            if row["outcome"] != expected:
                raise ValueError("outcome does not follow independent state")
    if any(x["attempt_id"] not in bound for x in state_rows if x["state"] not in {"reserved", "cancelled"}):
        raise ValueError("unbound executed attempt")
    failed = bool(len(accepted) != len(config["planned_slots"]) or result["capture_errors"]
                  or result["attempt_export_error"] or result["error_type"])
    if (accepted != result["completed_slots"] or result["status"] != ("halted" if failed else "complete")
            or result["evidence_kind"] != config["evidence_kind"]
            or result["family_root"] != config["family_root"]
            or not set(result["capture_errors"]).issubset({c["cell_id"] for c in config["cells"]})):
        raise ValueError("source completion envelope differs")
    return {"config": config, "plan": plan, "result": result, "truth_by_cell": truths}
