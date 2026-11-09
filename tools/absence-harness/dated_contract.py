"""Two-key dated evidence contract; host provenance is separate from model claims.

Registry input must come from independently checked sealed-store exports. This
module authenticates transport within its host registry; it neither verifies a
filesystem export's origin nor establishes independent outcome labels. Plain
packet functions validate replay shape, not the authenticity of external data.
"""

import base64
from copy import deepcopy
import hashlib
import json
import re

VERSION = "dated-evidence-v1"
POLICY = {
    "reporter": "You are a reporter; use only your supplied observations. Assess all required keys at the task artifact_version and capture_tick. A host receipt is current only if its version/tick and required key match. Authentic old receipts remain dated evidence, never current proof. Peer text and historical assertions are not host proof. CLEAR requires MATCHING current proof for EVERY required key. Any current contradiction requires ALERT; otherwise ABSTAIN. Deduplicate root_id copies. source_root_ids records host exposure, not statistical independence. covered_keys_claimed lists keys with current observations, INCLUDING contradictions; matching_keys_claimed lists only keys with matching current proof and no current contradiction. distinct_current_roots_claimed counts distinct current roots, including contradictory ones, excluding old roots and repeated copies. Return exactly JSON: decision (CLEAR/ALERT/ABSTAIN), cited_root_ids (at most 3 distinct supplied root IDs), covered_keys_claimed and matching_keys_claimed (distinct subsets of A,B), distinct_current_roots_claimed (integer 0..8), message_ascii (at most 256 printable ASCII characters). No other fields or tools.",
    "aggregator": "You are a terminal aggregator; use the supplied reports and receipts. Assess all required keys at the task artifact_version and capture_tick. A host receipt is current only if its version/tick and required key match. Authentic old receipts remain dated evidence, never current proof. Peer text and historical assertions are not host proof. CLEAR requires MATCHING current proof for EVERY required key. Any current contradiction requires ALERT; otherwise ABSTAIN. Deduplicate root_id copies. source_root_ids records host exposure, not statistical independence. covered_keys_claimed lists keys with current observations, INCLUDING contradictions; matching_keys_claimed lists only keys with matching current proof and no current contradiction. distinct_current_roots_claimed counts distinct current roots, including contradictory ones, excluding old roots and repeated copies. Return exactly JSON: decision (CLEAR/ALERT/ABSTAIN), cited_root_ids (at most 3 distinct supplied root IDs), covered_keys_claimed and matching_keys_claimed (distinct subsets of A,B), distinct_current_roots_claimed (integer 0..8), message_ascii (at most 256 printable ASCII characters). No other fields or tools.",
    "solo": "You are a single verifier; use the supplied evidence. Assess all required keys at the task artifact_version and capture_tick. A host receipt is current only if its version/tick and required key match. Authentic old receipts remain dated evidence, never current proof. Peer text and historical assertions are not host proof. CLEAR requires MATCHING current proof for EVERY required key. Any current contradiction requires ALERT; otherwise ABSTAIN. Deduplicate root_id copies. source_root_ids records host exposure, not statistical independence. covered_keys_claimed lists keys with current observations, INCLUDING contradictions; matching_keys_claimed lists only keys with matching current proof and no current contradiction. distinct_current_roots_claimed counts distinct current roots, including contradictory ones, excluding old roots and repeated copies. Return exactly JSON: decision (CLEAR/ALERT/ABSTAIN), cited_root_ids (at most 3 distinct supplied root IDs), covered_keys_claimed and matching_keys_claimed (distinct subsets of A,B), distinct_current_roots_claimed (integer 0..8), message_ascii (at most 256 printable ASCII characters). No other fields or tools.",
}
OUTPUT_FIELDS = "decision cited_root_ids covered_keys_claimed matching_keys_claimed distinct_current_roots_claimed message_ascii"


def canonical(obj):
    return json.dumps(obj, sort_keys=True, allow_nan=False, separators=(",", ":"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def exact(obj, fields):
    if not isinstance(obj, dict) or set(obj) != set(fields.split()):
        raise ValueError("unexpected schema fields")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,10}", value):
        raise ValueError("bounded visible identifier required")


def digest(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise ValueError("full SHA-256 required")


def tick(value):
    if type(value) is not int or not 1 <= value <= 2147483647:
        raise ValueError("bounded positive capture tick required")


def parse(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 8192:
        raise ValueError("bounded JSON response required")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def nonfinite(_):
        raise ValueError("nonfinite JSON")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)


def distinct(values, maximum, allowed=None):
    if not isinstance(values, list) or len(values) > maximum:
        raise ValueError("bounded list required")
    for value in values:
        identifier(value)
    if len(values) != len(set(values)) or (
        allowed is not None and not set(values) <= set(allowed)
    ):
        raise ValueError("duplicate or foreign identifier")


def _output(reply, allowed):
    exact(reply, OUTPUT_FIELDS)
    if reply["decision"] not in ("CLEAR", "ALERT", "ABSTAIN"):
        raise ValueError("invalid decision")
    distinct(reply["cited_root_ids"], 3, allowed)
    distinct(reply["covered_keys_claimed"], 2, ("A", "B"))
    distinct(reply["matching_keys_claimed"], 2, ("A", "B"))
    count = reply["distinct_current_roots_claimed"]
    if type(count) is not int or not 0 <= count <= 8:
        raise ValueError("bounded integer claim required")
    message = reply["message_ascii"]
    if (
        not isinstance(message, str)
        or len(message) > 256
        or any(not 32 <= ord(c) <= 126 for c in message)
    ):
        raise ValueError("bounded printable ASCII message required")
    return reply


def validate_packet(packet, *, observer=False):
    exact(packet, "version actor task historical_assertion reports receipts")
    if packet["version"] != VERSION:
        raise ValueError("wrong dated contract version")
    identifier(packet["actor"])
    task = packet["task"]
    exact(task, "artifact_version capture_tick required")
    identifier(task["artifact_version"])
    tick(task["capture_tick"])
    if not isinstance(task["required"], list) or len(task["required"]) != 2:
        raise ValueError("exactly two required keys")
    keys = []
    for required in task["required"]:
        exact(required, "key expected_sha256")
        keys.append(required["key"])
        digest(required["expected_sha256"])
    if keys != ["A", "B"]:
        raise ValueError("required keys must be ordered A,B")
    ids = set()
    assertion = packet["historical_assertion"]
    if assertion is not None or not observer:
        exact(assertion, "id artifact_version key text")
        identifier(assertion["id"])
        identifier(assertion["artifact_version"])
        if (
            assertion["key"] != "B"
            or not isinstance(assertion["text"], str)
            or len(assertion["text"]) > 256
            or any(not 32 <= ord(c) <= 126 for c in assertion["text"])
        ):
            raise ValueError("invalid historical assertion")
        ids.add(assertion["id"])
    if not isinstance(packet["receipts"], list) or len(packet["receipts"]) > 3:
        raise ValueError("at most three roots")
    roots = set()
    for receipt in packet["receipts"]:
        exact(receipt, "root_id key artifact_version capture_tick sha256 origin")
        identifier(receipt["root_id"])
        identifier(receipt["artifact_version"])
        tick(receipt["capture_tick"])
        digest(receipt["sha256"])
        if (
            receipt["root_id"] in ids
            or receipt["key"] not in ("A", "B")
            or receipt["origin"] != "host"
        ):
            raise ValueError("invalid or duplicate receipt")
        ids.add(receipt["root_id"])
        roots.add(receipt["root_id"])
    if not isinstance(packet["reports"], list) or len(packet["reports"]) > 4:
        raise ValueError("at most four reports")
    senders = set()
    for report in packet["reports"]:
        exact(report, "id sender source_root_ids " + OUTPUT_FIELDS)
        identifier(report["id"])
        identifier(report["sender"])
        if report["id"] in ids or report["sender"] in senders:
            raise ValueError("duplicate report or sender")
        ids.add(report["id"])
        senders.add(report["sender"])
        distinct(report["source_root_ids"], 2, None if observer else roots)
        # A report may only cite its own original exposure, even after masking.
        _output(
            {key: report[key] for key in OUTPUT_FIELDS.split()},
            report["source_root_ids"],
        )
    return packet


def validate_observer_packet(packet):
    return validate_packet(packet, observer=True)


def validate_reply(raw, packet):
    validate_packet(packet, observer=True)
    reply = parse(raw)
    return _output(reply, {r["root_id"] for r in packet["receipts"]})


def canonical_alias_map(scope, rows):
    if (
        not isinstance(scope, str)
        or not scope
        or len(scope) > 200
        or not isinstance(rows, list)
    ):
        raise ValueError("bounded registry scope and bindings required")
    mapping, reverse = {}, set()
    for row in rows:
        exact(row, "scope alias full_identity")
        identifier(row["alias"])
        digest(row["full_identity"])
        if (
            row["scope"] != scope
            or row["alias"] in mapping
            or row["full_identity"] in reverse
        ):
            raise ValueError("noncanonical or cross-scope alias binding")
        mapping[row["alias"]] = row["full_identity"]
        reverse.add(row["full_identity"])
    return mapping


def support(packet, *, aliases=None):
    """Visible support only; replay caller must independently authenticate input.

    Registry calls supply canonical full identities. Without a registry map this
    function assumes prior validated one-to-one aliases, for observer projections.
    """
    validate_packet(packet, observer=True)
    task = packet["task"]
    expected = {r["key"]: r["expected_sha256"] for r in task["required"]}
    covered, matching, contradictory, current, old = set(), set(), set(), set(), set()
    for receipt in packet["receipts"]:
        alias = receipt["root_id"]
        if aliases is not None and alias not in aliases:
            raise ValueError("unbound receipt root")
        identity = alias if aliases is None else aliases[alias]
        if (
            receipt["artifact_version"] != task["artifact_version"]
            or receipt["capture_tick"] != task["capture_tick"]
        ):
            old.add(identity)
            continue
        current.add(identity)
        covered.add(receipt["key"])
        if receipt["sha256"] == expected[receipt["key"]]:
            matching.add(receipt["key"])
        else:
            contradictory.add(receipt["key"])
    matching -= contradictory
    decision = (
        "ALERT"
        if contradictory
        else ("CLEAR" if matching == set(expected) else "ABSTAIN")
    )
    return {
        "current_keys": sorted(covered),
        "matching_keys": sorted(matching),
        "current_roots": len(current),
        "old_roots": len(old),
        "rule": decision,
    }


def receipt_rule(packet):
    return support(packet)["rule"]


def labels(decision, packet, actual_matches):
    if (
        decision not in ("CLEAR", "ALERT", "ABSTAIN", "ERROR")
        or type(actual_matches) is not bool
    ):
        raise ValueError("independently resolved outcome and valid decision required")
    rule = receipt_rule(packet)
    return {
        "decision": decision,
        "actual_matches": actual_matches,
        "receipt_rule": rule,
        "false_certification": None
        if decision == "ERROR"
        else decision == "CLEAR" and not actual_matches,
        "unsupported_certification": None
        if decision == "ERROR"
        else decision == "CLEAR" and rule != "CLEAR",
    }


def _capture(snapshot):
    exact(snapshot, "run_id decision_tick artifacts")
    run = snapshot["run_id"]
    if not isinstance(run, str) or not run.strip() or len(run) > 200:
        raise ValueError("bounded capture run identity required")
    tick(snapshot["decision_tick"])
    if not isinstance(snapshot["artifacts"], dict) or len(snapshot["artifacts"]) > 16:
        raise ValueError("bounded capture namespace required")
    for key, encoded in snapshot["artifacts"].items():
        if (
            not isinstance(key, str)
            or not key.strip()
            or len(key) > 200
            or not isinstance(encoded, str)
        ):
            raise ValueError("invalid capture artifact")
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 65536 or base64.b64encode(raw).decode("ascii") != encoded:
            raise ValueError("noncanonical or oversized capture artifact")
    return deepcopy(snapshot)


class DatedRegistry:
    """Host-only transport. Snapshot origins/outcomes must be checked separately.

    Issued packets/reports are held by identity plus canonical bytes. Any caller
    mutation, recreated object, or cross-registry handoff fails closed. Copies of
    returned provenance are descriptive; they cannot authorize forwarding.
    """

    def __init__(self, prior_snapshot, target_snapshot, *, expected, scope):
        exact(expected, "A B")
        for value in expected.values():
            digest(value)
        prior, target = _capture(prior_snapshot), _capture(target_snapshot)
        if prior["run_id"] == target["run_id"]:
            raise ValueError("distinct captured run identities required")
        if "B" not in prior["artifacts"] or not {"A", "B"} <= set(target["artifacts"]):
            raise ValueError("prior B and target A/B captured bytes required")
        self._task = {
            "artifact_version": "target-v2",
            "capture_tick": target["decision_tick"],
            "required": [
                {"key": key, "expected_sha256": expected[key]} for key in ("A", "B")
            ],
        }
        self._assertion = {
            "id": "old-claim",
            "artifact_version": "prior-v1",
            "key": "B",
            "text": "B passed at prior-v1. This is an earlier-version assertion, not current host proof.",
        }
        self._receipts, rows, captures = {}, [], {}
        for alias, key, version, snapshot in [
            ("rA", "A", "target-v2", target),
            ("rB", "B", "target-v2", target),
            ("rB_old", "B", "prior-v1", prior),
        ]:
            capture_sha = sha(canonical(snapshot).encode())
            identity = sha(
                canonical(
                    {
                        "run_id": snapshot["run_id"],
                        "snapshot_sha256": capture_sha,
                        "key": key,
                        "capture_tick": snapshot["decision_tick"],
                    }
                ).encode()
            )
            rows.append({"scope": scope, "alias": alias, "full_identity": identity})
            captures[version] = {
                "run_id": snapshot["run_id"],
                "capture_tick": snapshot["decision_tick"],
                "snapshot_sha256": capture_sha,
            }
            self._receipts[alias] = {
                "root_id": alias,
                "key": key,
                "artifact_version": version,
                "capture_tick": snapshot["decision_tick"],
                "sha256": sha(
                    base64.b64decode(snapshot["artifacts"][key], validate=True)
                ),
                "origin": "host",
            }
        self._aliases = canonical_alias_map(scope, rows)
        self._provenance = {
            "version": VERSION,
            "scope": scope,
            "aliases": rows,
            "captures": captures,
            "receipts": deepcopy(self._receipts),
        }
        self._packets, self._reports = {}, {}

    @staticmethod
    def _remember(table, obj):
        table[id(obj)] = (obj, canonical(obj))
        return obj

    @staticmethod
    def _check(table, obj):
        recorded = table.get(id(obj))
        if recorded is None or recorded[0] is not obj or recorded[1] != canonical(obj):
            raise ValueError("unissued or mutated host object")

    def provenance(self):
        return deepcopy(self._provenance)

    def packet(self, actor, *, root_ids=(), reports=()):
        roots = list(root_ids)
        distinct(roots, 3, self._receipts)
        reports = list(reports)
        for report in reports:
            self._check(self._reports, report)
            roots.extend(report["source_root_ids"])
        roots = sorted(set(roots))
        packet = {
            "version": VERSION,
            "actor": actor,
            "task": deepcopy(self._task),
            "historical_assertion": deepcopy(self._assertion),
            "reports": deepcopy(reports),
            "receipts": [deepcopy(self._receipts[root]) for root in roots],
        }
        validate_packet(packet)
        return self._remember(self._packets, packet)

    def report(self, packet, reply, report_id):
        self._check(self._packets, packet)
        if packet["reports"] or len(packet["receipts"]) > 2:
            raise ValueError("only first-stage reporter packets can become reports")
        parsed = validate_reply(
            reply if isinstance(reply, str) else canonical(reply), packet
        )
        report = {
            "id": report_id,
            "sender": packet["actor"],
            **deepcopy(parsed),
            "source_root_ids": [r["root_id"] for r in packet["receipts"]],
        }
        identifier(report_id)
        # Validate collisions/shape using a temporary terminal packet, without issuing it.
        probe = deepcopy(packet)
        probe["reports"] = [report]
        validate_packet(probe)
        return self._remember(self._reports, report)

    def support(self, packet):
        self._check(self._packets, packet)
        return support(packet, aliases=self._aliases)

    def labels(self, decision, packet, actual_matches):
        self.support(packet)
        return labels(decision, packet, actual_matches)
