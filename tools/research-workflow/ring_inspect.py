"""Local deterministic Inspect audit of frozen ring packets, not paid monitors."""
import json
from pathlib import Path
import socket
import uuid
from unittest.mock import patch

from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.solver import generate
from relay_contract import canonical, sha
from verification_inspect import BASELINES, baseline, reload
from observation_masks import DISTINCT, select_views
from request_gate import save_json
from ring_phase import verify_source, OBSERVER

SEED = 13


def prepare(root, review):
    root = Path(root)
    if set(review) != {"manifest_sha256", "reviewed_by", "scope", "rights"} or any(
            not isinstance(v, str) or not v.strip() for v in review.values()):
        raise ValueError("explicit source review required")
    if sha((root / "evidence-manifest.json").read_bytes()) != review["manifest_sha256"]:
        raise ValueError("review does not bind source bytes")
    checked = verify_source(root)
    config, result = checked["config"], checked["result"]
    if config["observer"] != OBSERVER or SEED != OBSERVER["seed"] or DISTINCT != OBSERVER["policy"] or list(BASELINES) != OBSERVER["baselines"]:
        raise ValueError("observer differs from pre-source frozen contract")
    samples, records, selections = [], {}, []
    batch = uuid.uuid4().hex
    for row in result["records"]:
        if row["packet"] is None or row["cell_id"] not in checked["truth_by_cell"]:
            selections.append({"slot_id": row["slot_id"], "status": "source_unavailable"})
            continue
        views, selection = select_views(row["packet"], SEED, policy=DISTINCT)
        selections.append({"slot_id": row["slot_id"], **selection})
        for condition, packet in views.items():
            for monitor in BASELINES:
                text = canonical({"monitor": monitor, "packet": packet})
                opaque = f"{batch}-{len(samples):06d}"
                samples.append(Sample(id=opaque, input=text))
                records[opaque] = {
                    "slot_id": row["slot_id"], "cell_id": row["cell_id"],
                    "round": row["round"], "actor": row["actor"],
                    "family_root": config["family_root"], "phase_run_id": config["run_id"],
                    "source_status": row["status"], "evidence_kind": config["evidence_kind"],
                    "actual_matches": checked["truth_by_cell"][row["cell_id"]],
                    "condition": condition, "monitor": monitor,
                    "input_sha256": sha(text.encode()), "observation_policy": DISTINCT,
                    "distinct_comparison_available": selection["distinct_comparison_available"],
                    "distinct_ineligible_reason": selection["distinct_ineligible_reason"],
                    "proof_and_random_identical": views["proof_loss"] == views["random_loss"],
                }
    return samples, records, result, selections


def run_replay(root, review, destination):
    samples, records, result, selections = prepare(root, review)
    if not samples:
        raise ValueError("no independently captured source packet available")
    destination = Path(destination)
    destination.mkdir(mode=0o700, exist_ok=False)
    save_json(destination / "review.json", review)
    save_json(destination / "evaluator-records.json", records)
    save_json(destination / "source-flow.json", result)
    save_json(destination / "mask-preflight.json", {
        "policy": DISTINCT, "seed": SEED, "slots": selections,
        "scope": "fixed observer replay; not a new interacting population"})
    folder = Path(__file__).resolve().parent
    hashes = {p: sha((folder / p).read_bytes()) for p in (
        "ring_inspect.py", "verification_inspect.py", "observation_masks.py")}

    def respond(messages, tools, tool_choice, config):
        value = json.loads(messages[-1].content)
        return ModelOutput.from_content(model="mockllm", content=baseline(value["monitor"], value["packet"]))

    def deny(*args, **kwargs):
        raise AssertionError("local Inspect audit cannot use network")

    try:
        with patch.object(socket.socket, "connect", deny), patch.object(socket.socket, "connect_ex", deny), patch.object(socket, "create_connection", deny):
            logs = eval(Task(dataset=samples, solver=generate(), name="ring_evidence_baselines"),
                        model=get_model("mockllm/model", custom_outputs=respond),
                        log_dir=str(destination / "inspect"), display="none")
        if hashes != {p: sha((folder / p).read_bytes()) for p in hashes}:
            raise ValueError("observer code changed")
        path = Path(logs[0].location)
        rows = reload(path, records)
        save_json(destination / "decisions.json", rows)
        save_json(destination / "receipt.json", {
            "source_manifest_sha256": review["manifest_sha256"], "inspect_sha256": sha(path.read_bytes()),
            "samples": len(samples), "remote_calls": 0, "source_status": result["status"],
            "observer_code_sha256": hashes, "scope": "dependent views; deterministic baselines, not model judges"})
        return rows
    except BaseException as exc:
        save_json(destination / "failure.json", {"error_type": type(exc).__name__})
        raise
    finally:
        artifacts = {}
        for path in sorted(destination.rglob("*")):
            if path.is_symlink():
                raise ValueError("replay symlink forbidden")
            path.chmod(0o700 if path.is_dir() else 0o600)
            if path.is_file():
                artifacts[str(path.relative_to(destination))] = sha(path.read_bytes())
        save_json(destination / "manifest.json", artifacts)
