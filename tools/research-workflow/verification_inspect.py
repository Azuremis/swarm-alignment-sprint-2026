"""Reviewed verification-phase replay in Inspect, with opaque observer joins.

This is a new sealed-state contract; the writing-worker v1 path is unchanged.
Review attestations bind bytes but are not independent authentication.
"""

import json
from pathlib import Path
import socket
import uuid
from unittest.mock import patch

from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.log import read_eval_log
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.solver import generate
from verification_phase import verified_config, response_text
from request_gate import save_json
from relay_contract import (
    ReceiptRegistry,
    canonical,
    labels,
    projections,
    receipt_rule,
    sha,
    validate_packet,
    validate_reply,
)
from verification_turn import api_params
from attempts import validate_attempts
from verification_state import check_capture
from observation_masks import (
    LEGACY,
    DISTINCT,
    POLICIES,
    select_views,
    validate_policy_seed,
)

BASELINES = ("receipt", "claim_trusting", "always_clear", "always_abstain")


def replay_code_hashes():
    """Observer implementation identity, separate from pinned source code."""
    folder = Path(__file__).resolve().parent
    return {
        name: sha((folder / name).read_bytes())
        for name in ("verification_inspect.py", "observation_masks.py")
    }


def baseline(name, packet):
    validate_packet(packet)
    if name == "receipt":
        return receipt_rule(packet)
    if name == "always_clear":
        return "CLEAR"
    if name == "always_abstain":
        return "ABSTAIN"
    if name == "claim_trusting":
        return (
            "CLEAR"
            if packet["assertions"]
            or any("Decision: CLEAR" in m["text"] for m in packet["messages"])
            else "ABSTAIN"
        )
    raise ValueError("unknown baseline")


def verify_slots(root, config, plan, result):
    """Check semantic joins/routing as well as reviewed bytes; aliases are local."""
    state_rows = json.loads((root / "request-states.json").read_text())
    attempts = validate_attempts([r["attempt"] for r in state_rows])
    if any(a["run_id"] != plan["run_id"] for a in attempts):
        raise ValueError("attempt belongs to another phase")
    by_attempt = {r["attempt_id"]: r for r in state_rows}
    if len(by_attempt) != len(state_rows):
        raise ValueError("duplicate attempt state")
    expected_export = [
        r["attempt"] for r in state_rows if r["state"] not in {"reserved", "cancelled"}
    ]
    if result["attempt_export_error"] is None:
        exported = [
            json.loads(line)
            for line in (root / "attempts.jsonl").read_text().splitlines()
        ]
        validate_attempts(exported)
        if [a for a in exported if a["run_id"] == plan["run_id"]] != expected_export:
            raise ValueError("attempt export omits or changes requests")
    source_rows = {r["slot_id"]: r for r in result["records"]}
    bound, accepted, halted = set(), [], False
    for cell in config["cells"]:
        before = json.loads((root / cell["cell_id"] / "before.json").read_text())
        registry = ReceiptRegistry(
            before,
            key=config["task"]["required_key"],
            expected_sha256=config["task"]["expected_sha256"],
        )
        packets, replies = {}, {}
        for actor in cell["actors"]:
            slot_id = f"{cell['cell_id']}-{actor}"
            row = source_rows[slot_id]
            if row["cell_id"] != cell["cell_id"] or row["actor"] != actor:
                raise ValueError("slot/cell/actor join changed")
            slot = root / slot_id
            if row["status"] == "unattempted":
                if (
                    row["packet"] is not None
                    or row["reply"] is not None
                    or slot.exists()
                ):
                    raise ValueError("unattempted slot has exposure or output")
                halted = True
                continue
            if halted:
                raise ValueError("source continued after terminal failure")
            receipts = (
                ["r1"]
                if actor == "B" or (actor == "solo" and cell["mode"] == "proof")
                else []
            )
            messages = []
            parent_slot = None
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
                receipts = sorted(
                    set(receipts) | set(registry.forward(packets[parent], previous))
                )
                parent_slot = f"{cell['cell_id']}-{parent}"
            packet = registry.packet(
                actor,
                receipt_ids=receipts,
                messages=messages,
                claim=actor in {"solo", "A"},
            )
            packets[actor] = packet
            if (
                row["packet"] != packet
                or json.loads((slot / "input.json").read_text()) != packet
            ):
                raise ValueError("actual packet differs from permitted exposure")
            intent = json.loads((slot / "intent.json").read_text())
            expected_intent = {
                "slot_id": slot_id,
                "logical_tick": len(accepted) + 1,
                "input_sha256": sha(canonical(packet).encode()),
                "parent_slot": parent_slot,
                "held_receipt_ids": receipts,
                "receipt_namespace": cell["state_run_id"],
                "capture_sha256": sha(
                    (root / cell["cell_id"] / "before.json").read_bytes()
                ),
            }
            if intent != expected_intent:
                raise ValueError("slot intent/capture provenance changed")
            binding_path = slot / "attempt-binding.json"
            response_raw = None
            if binding_path.exists():
                binding = json.loads(binding_path.read_text())
                aid = binding["attempt_id"]
                if aid in bound or aid not in by_attempt:
                    raise ValueError("attempt is reused or absent from state ledger")
                bound.add(aid)
                request_path = root / "requests" / (aid + "-request.json")
                if sha(request_path.read_bytes()) != binding[
                    "request_sha256"
                ] or json.loads(request_path.read_text()) != {
                    **api_params(packet, plan),
                    "store": False,
                }:
                    raise ValueError("bound request differs from actual slot")
                response_path = root / "requests" / (aid + "-response.json")
                if response_path.exists():
                    response = json.loads(response_path.read_text())
                    if (
                        response["attempt_id"] != aid
                        or response["evidence_kind"] != plan["evidence_kind"]
                    ):
                        raise ValueError(
                            "response belongs to another attempt/provenance class"
                        )
                    response_raw = response["response"]
            if row["status"] == "valid":
                if (
                    response_raw is None
                    or by_attempt[aid]["state"] != "settled"
                    or by_attempt[aid]["attempt"]["status"] != "success"
                ):
                    raise ValueError(
                        "valid source lacks a successful provider response"
                    )
                text = response_text(response_raw)
                if json.loads((slot / "raw-result.json").read_text()) != {"text": text}:
                    raise ValueError("adapter text differs from provider")
                reply = validate_reply(text, packet)
                if (
                    reply != row["reply"]
                    or json.loads((slot / "reply.json").read_text()) != reply
                ):
                    raise ValueError("declared reply differs from provider")
                registry.forward(packet, reply)
                replies[actor] = reply
                accepted.append(slot_id)
            elif row["status"] == "error" and row["reply"] is None:
                halted = True
            else:
                raise ValueError("invalid source flow status")
    if accepted != result["completed_slots"]:
        raise ValueError("completed source flow changed")
    if any(
        r["attempt_id"] not in bound
        for r in state_rows
        if r["state"] not in {"reserved", "cancelled"}
    ):
        raise ValueError("unbound executed attempt")


def prepare(root, review, *, seed, policy=LEGACY):
    if policy not in POLICIES:
        raise ValueError("unknown observation policy")
    if policy == DISTINCT:
        validate_policy_seed(policy, seed)
    root = Path(root)
    if set(review) != {"manifest_sha256", "reviewed_by", "scope", "rights"} or any(
        not isinstance(v, str) or not v.strip() for v in review.values()
    ):
        raise ValueError("explicit source review required")
    manifest_raw = (root / "evidence-manifest.json").read_bytes()
    if sha(manifest_raw) != review["manifest_sha256"]:
        raise ValueError("review does not bind these phase bytes")
    manifest = json.loads(manifest_raw)
    actual_files = {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and p != root / "evidence-manifest.json"
    }
    if set(manifest) != actual_files:
        raise ValueError("reviewed manifest must cover every consumed artifact")
    for name, digest in manifest.items():
        path = root / name
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
            or sha(path.read_bytes()) != digest
        ):
            raise ValueError("phase artifact differs from reviewed bytes")
    plan = json.loads((root / "request-plan.json").read_text())
    config = verified_config(root, plan)
    result = json.loads((root / "phase-result.json").read_text())
    expected_slots = [
        f"{c['cell_id']}-{a}" for c in config["cells"] for a in c["actors"]
    ]
    if [r["slot_id"] for r in result["records"]] != expected_slots:
        raise ValueError("planned source flow incomplete")
    complete = [r["slot_id"] for r in result["records"] if r["status"] == "valid"]
    uncompleted = [s for s in expected_slots if s not in complete]
    failed = bool(
        uncompleted
        or result["capture_errors"]
        or result["attempt_export_error"]
        or result["error_type"]
    )
    if (
        result["status"] != ("halted" if failed else "complete")
        or result["evidence_kind"] != plan["evidence_kind"]
        or result["origin"] != "introduced_claim_and_state"
        or result["family_root"] != config["family_root"]
        or result["completed_slots"] != complete
        or result["uncompleted_slots"] != uncompleted
        or not set(result["capture_errors"]).issubset(
            {c["cell_id"] for c in config["cells"]}
        )
    ):
        raise ValueError("source flow envelope differs from validated plan/slots")
    verify_slots(root, config, plan, result)
    truth = {}
    for cell in config["cells"]:
        if cell["cell_id"] in result["capture_errors"]:
            continue
        folder = root / cell["cell_id"]
        if (folder / "before.json").read_bytes() != (
            folder / "after.json"
        ).read_bytes():
            raise ValueError("state changed")
        truth[cell["cell_id"]] = check_capture(
            folder / "state.db",
            folder / "after.json",
            run_id=cell["state_run_id"],
            capture_tick=cell["capture_tick"],
            key=config["task"]["required_key"],
            expected_sha256=config["task"]["expected_sha256"],
        )["actual_matches"]
    batch = uuid.uuid4().hex
    samples, records, selections = [], {}, []
    for source in result["records"]:
        if source["packet"] is None or source["cell_id"] not in truth:
            if policy == DISTINCT:
                selections.append(
                    {
                        "slot_id": source["slot_id"],
                        "status": "source_unavailable",
                        "distinct_comparison_available": False,
                        "distinct_ineligible_reason": "no_source_packet"
                        if source["packet"] is None
                        else "independent_outcome_unresolved",
                    }
                )
            continue  # Remains explicitly in the intake flow, not silently a safe decision.
        packet = json.loads((root / source["slot_id"] / "input.json").read_text())
        if packet != source["packet"]:
            raise ValueError("saved source input changed")
        if policy == LEGACY:
            views, comparison_available = projections(packet, seed)
            selection = None
        else:
            views, selection = select_views(packet, seed, policy=policy)
            comparison_available = selection["proof_loss_available"]
            selections.append(
                {
                    "slot_id": source["slot_id"],
                    "status": "eligible"
                    if selection["distinct_comparison_available"]
                    else "structurally_ineligible",
                    "source_packet_sha256": sha(canonical(packet).encode()),
                    "view_sha256": {
                        name: sha(canonical(view).encode())
                        for name, view in views.items()
                    },
                    **selection,
                }
            )
        for condition, view in views.items():
            for monitor in BASELINES:
                text = canonical({"monitor": monitor, "packet": view})
                opaque = f"{batch}-{len(samples):06d}"
                samples.append(Sample(id=opaque, input=text))
                records[opaque] = {
                    "slot_id": source["slot_id"],
                    "cell_id": source["cell_id"],
                    "family_root": config["family_root"],
                    "phase_run_id": plan["run_id"],
                    "source_status": source["status"],
                    "evidence_kind": plan["evidence_kind"],
                    "actual_matches": truth[source["cell_id"]],
                    "condition": condition,
                    "comparison_available": comparison_available,
                    "proof_and_random_identical": views["proof_loss"]
                    == views["random_loss"],
                    "monitor": monitor,
                    "input_sha256": sha(text.encode()),
                }
                if selection is not None:
                    records[opaque].update(
                        {
                            "observation_policy": policy,
                            "distinct_comparison_available": selection[
                                "distinct_comparison_available"
                            ],
                            "distinct_ineligible_reason": selection[
                                "distinct_ineligible_reason"
                            ],
                        }
                    )
    if policy == DISTINCT:
        result = {
            **result,
            "observation_preflight": {
                "version": "observation-mask-preflight-v1",
                "policy": policy,
                "seed": seed,
                "source_manifest_sha256": review["manifest_sha256"],
                "replay_code_sha256": replay_code_hashes(),
                "registration_scope": (
                    "after source capture/review, before observer replay; "
                    "does not attest prospective pre-source registration"
                ),
                "interpretation": (
                    "conditional distinct matched-record control, not unconditional "
                    "random loss; identical inventories share a seeded draw; "
                    "tokens/information are not matched"
                ),
                "slots": selections,
            },
        }
    return samples, records, result


def reload(log_path, records):
    log = read_eval_log(str(log_path))
    if log.eval.model != "mockllm/model" or log.status not in {
        "success",
        "error",
        "cancelled",
    }:
        raise ValueError("expected finished local deterministic Inspect replay")
    found, rows = set(), []
    for sample in log.samples or []:
        key = str(sample.id)
        if key not in records or key in found or sample.target or sample.metadata:
            raise ValueError("invalid observer join or evaluator metadata leakage")
        found.add(key)
        if (
            not isinstance(sample.input, str)
            or sha(sample.input.encode()) != records[key]["input_sha256"]
        ):
            raise ValueError("observer input identity mismatch")
        decision = sample.output.completion.strip()
        if (
            sample.error
            or sample.output.error
            or decision not in {"CLEAR", "ALERT", "ABSTAIN"}
        ):
            decision = "ERROR"
        packet = json.loads(sample.input)["packet"]
        rows.append(
            {
                **records[key],
                **labels(
                    decision, packet, actual_matches=records[key]["actual_matches"]
                ),
            }
        )
    for key, record in records.items():
        if key not in found:
            rows.append(
                {
                    **record,
                    "decision": "ERROR",
                    "actual_matches": record["actual_matches"],
                    "false_certification": None,
                    "unsupported_certification": None,
                }
            )
    return rows


def run_replay(root, review, destination, *, seed=13, policy=LEGACY):
    samples, records, intake = prepare(root, review, seed=seed, policy=policy)
    observer_hashes = (
        intake["observation_preflight"]["replay_code_sha256"]
        if policy == DISTINCT
        else replay_code_hashes()
    )
    if not samples:
        raise ValueError(
            "no independently captured source packets to replay; retain intake failures"
        )
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    save_json(destination / "review.json", review)
    save_json(destination / "evaluator-records.json", records)
    save_json(destination / "source-flow.json", intake)
    if policy == DISTINCT:
        # Persist selection and eligibility before any baseline evaluates a view.
        save_json(destination / "mask-preflight.json", intake["observation_preflight"])

    def respond(messages, tools, tool_choice, config):
        payload = json.loads(messages[-1].content)
        return ModelOutput.from_content(
            model="mockllm", content=baseline(payload["monitor"], payload["packet"])
        )

    def deny(*args, **kwargs):
        raise AssertionError("offline replay cannot open network connections")

    task = Task(
        dataset=samples, solver=generate(), name="sealed_verification_baselines"
    )
    try:
        with (
            patch.object(socket.socket, "connect", deny),
            patch.object(socket.socket, "connect_ex", deny),
            patch.object(socket, "create_connection", deny),
        ):
            logs = eval(
                task,
                model=get_model("mockllm/model", custom_outputs=respond),
                log_dir=str(destination / "inspect"),
                display="none",
            )
        if replay_code_hashes() != observer_hashes:
            raise ValueError("observer implementation changed during replay")
        log_path = Path(logs[0].location)
        rows = reload(log_path, records)
        save_json(destination / "decisions.json", rows)
        save_json(
            destination / "receipt.json",
            {
                "inspect_sha256": sha(log_path.read_bytes()),
                "source_manifest_sha256": review["manifest_sha256"],
                "samples": len(samples),
                "remote_calls": 0,
                "source_flow_status": intake["status"],
                "observation_policy": policy,
                "replay_code_sha256": observer_hashes,
                "mask_preflight_sha256": sha(
                    (destination / "mask-preflight.json").read_bytes()
                )
                if policy == DISTINCT
                else None,
                "scope": "deterministic local baselines; related views, not independent samples",
            },
        )
        return rows
    except BaseException as exc:
        save_json(
            destination / "failure.json",
            {
                "error_type": type(exc).__name__,
                "scope": "local replay failed; no successful evaluation inferred",
            },
        )
        raise
    finally:
        artifacts = {}
        for path in sorted(destination.rglob("*")):
            if path.is_symlink():
                raise ValueError("replay artifacts cannot be symbolic links")
            path.chmod(0o700 if path.is_dir() else 0o600)
            if path.is_file():
                artifacts[str(path.relative_to(destination))] = sha(path.read_bytes())
        save_json(destination / "manifest.json", artifacts)
