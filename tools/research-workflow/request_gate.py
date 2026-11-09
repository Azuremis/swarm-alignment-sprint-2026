"""Single-attempt Responses boundary with durable accounting; no credential loader.

Caller supplies a reviewed SDK client (zero retries), bounded plan and shared
BudgetBook. Passing these schema checks is not spending authorization or proof
that supplied price/budget evidence is authentic. Unit tests use scripted SDKs.
"""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

from attempts import money


class RequestStopped(RuntimeError):
    pass


def save_json(path, value):
    payload = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(payload).hexdigest()


class RequestGate:
    def __init__(self, *, sdk, book, plan, destination, clock=None):
        fields = {
            "run_id",
            "model",
            "sdk_version",
            "config_sha256",
            "max_requests",
            "max_output_tokens",
            "max_request_bytes",
            "model_context_tokens",
            "maximum_request_usd",
            "input_usd_per_million_upper",
            "output_usd_per_million_upper",
            "price_evidence",
            "evidence_kind",
        }
        if set(plan) != fields or plan["evidence_kind"] not in {
            "synthetic_fixture",
            "recorded_agent_trace",
        }:
            raise ValueError("complete bounded plan required")
        for field in (
            "max_requests",
            "max_output_tokens",
            "max_request_bytes",
            "model_context_tokens",
        ):
            if type(plan[field]) is not int or plan[field] <= 0:
                raise ValueError("positive integer request limits required")
        for field in (
            "maximum_request_usd",
            "input_usd_per_million_upper",
            "output_usd_per_million_upper",
        ):
            if money(plan[field]) == 0:
                raise ValueError("positive verified upper costs required")
        worst = (
            Decimal(plan["model_context_tokens"])
            * money(plan["input_usd_per_million_upper"])
            + Decimal(plan["max_output_tokens"])
            * money(plan["output_usd_per_million_upper"])
        ) / 1_000_000
        if money(plan["maximum_request_usd"]) < worst:
            raise ValueError(
                "reservation must cover the full model context and output at upper rates"
            )
        if not plan["price_evidence"] or not plan["model"]:
            raise ValueError("model and price evidence required")
        if str(sdk.base_url).rstrip("/") != "https://api.openai.com/v1":
            raise ValueError("explicit official OpenAI endpoint required")
        if sdk.max_retries != 0:
            raise ValueError("SDK retries must be disabled")
        self.sdk, self.book, self.plan = sdk, book, dict(plan)
        digest = hashlib.sha256(
            json.dumps(self.plan, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        self.book.bind_plan(plan["run_id"], digest)
        self.destination = Path(destination)
        self.destination.mkdir(mode=0o700, parents=False, exist_ok=False)
        save_json(self.destination / "plan.json", self.plan)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.Lock()
        self.halted = False

    def create(self, params, *, before_dispatch=None):
        with self.lock:
            return self._create(params, before_dispatch=before_dispatch)

    def _create(self, params, *, before_dispatch=None):
        if (
            self.halted
            or self.sdk.max_retries != 0
            or str(self.sdk.base_url).rstrip("/") != "https://api.openai.com/v1"
        ):
            raise RequestStopped("request route halted or retry configuration changed")
        allowed = {
            "model",
            "input",
            "tools",
            "max_output_tokens",
            "parallel_tool_calls",
            "store",
            "reasoning",
            "text",
        }
        if set(params) - allowed or params.get("model") != self.plan["model"]:
            raise RequestStopped("unexpected request fields or model")
        if params.get("max_output_tokens") != self.plan["max_output_tokens"]:
            raise RequestStopped("output bound changed")
        for tool in params.get("tools", []):
            if tool.get("type") != "function" or tool.get("name") not in {
                "write_artifact",
                "claim_completion",
            }:
                raise RequestStopped("only bounded artifact functions are permitted")
        payload = {**params, "parallel_tool_calls": False, "store": False}
        if (
            len(json.dumps(payload, sort_keys=True, allow_nan=False).encode())
            > self.plan["max_request_bytes"]
        ):
            raise RequestStopped("request byte limit exceeded")
        previous = [
            r
            for r in self.book.inspect()["requests"]
            if r["attempt"]["run_id"] == self.plan["run_id"]
            and r["state"] != "cancelled"
        ]
        if len(previous) >= self.plan["max_requests"]:
            raise RequestStopped("run request ceiling reached")
        identity = uuid.uuid4().hex
        now = self.clock()
        row = {
            "attempt_id": identity,
            "request_id": identity,
            "run_id": self.plan["run_id"],
            "stage": "generation",
            "attempt_index": 0,
            "provider": "openai",
            "requested_model": self.plan["model"],
            "resolved_model": "unknown",
            "sdk_version": self.plan["sdk_version"],
            "config_sha256": self.plan["config_sha256"],
            "status": "started",
            "error_type": None,
            "input_tokens": None,
            "output_tokens": None,
            "cost_usd": None,
            "cost_source": "unknown before request",
            "started_at": now.isoformat(),
            "finished_at": None,
        }
        self.book.reserve(
            row,
            self.plan["maximum_request_usd"],
            now=now,
            run_limit=self.plan["max_requests"],
        )
        try:
            request_digest = save_json(self.destination / (identity + "-request.json"), payload)
            if before_dispatch is not None:
                before_dispatch(identity, request_digest)
        except Exception:
            self.book.cancel_unstarted(identity)
            raise
        self.book.mark_started(identity, now=now)
        try:
            response = self.sdk.responses.create(**payload)
            raw = response.model_dump(mode="json")
            receipt = {
                "attempt_id": identity,
                "provider_request_id": getattr(response, "_request_id", None),
                "response": raw,
                "evidence_kind": self.plan["evidence_kind"],
            }
            digest = save_json(
                self.destination / (identity + "-response.json"), receipt
            )
            row.update(
                resolved_model=raw.get("model") or "unknown",
                finished_at=self.clock().isoformat(),
            )
            usage = raw.get("usage") or {}
            inputs, outputs = usage.get("input_tokens"), usage.get("output_tokens")
            valid = all(type(n) is int and n >= 0 for n in (inputs, outputs))
            row.update(
                status="success" if raw.get("status") == "completed" else "uncertain"
            )
            if valid:
                cost = (
                    Decimal(inputs) * money(self.plan["input_usd_per_million_upper"])
                    + Decimal(outputs)
                    * money(self.plan["output_usd_per_million_upper"])
                ) / 1_000_000
                row.update(
                    input_tokens=inputs,
                    output_tokens=outputs,
                    cost_usd=str(cost),
                    cost_source="usage times reviewed upper rates; not invoice: "
                    + self.plan["price_evidence"],
                )
            if (
                not valid
                or raw.get("status") != "completed"
                or (
                    valid
                    and (
                        inputs > self.plan["model_context_tokens"]
                        or outputs > self.plan["max_output_tokens"]
                    )
                )
            ):
                row["status"] = "uncertain"
            self.book.settle(identity, row, evidence="saved response sha256:" + digest)
            if (
                row["status"] != "success"
                or row["cost_usd"] is None
                or money(row["cost_usd"]) > money(self.plan["maximum_request_usd"])
            ):
                self.halted = True
                raise RequestStopped(
                    "response incomplete, usage unknown or cost bound exceeded"
                )
            return response
        except RequestStopped:
            raise
        except Exception as exc:
            self.halted = True
            # Never log exception strings, headers or a credential-bearing client.
            row.update(
                status="uncertain",
                error_type=type(exc).__name__,
                finished_at=self.clock().isoformat(),
            )
            self.book.settle(
                identity,
                row,
                evidence="request/response processing failed; no automatic retry",
            )
            save_json(
                self.destination / (identity + "-failure.json"),
                {
                    "attempt_id": identity,
                    "error_type": type(exc).__name__,
                    "cost_known": row["cost_usd"] is not None,
                },
            )
            raise RequestStopped(
                "request outcome requires reconciliation; no retry"
            ) from None


def client_class(source_client):
    """Use upstream formatting/tool parsing, bypass dotenv and BOTH retry loops."""

    class AccountedClient(source_client):
        def __init__(self, gate):
            self.gate = gate
            self._capability_model = gate.plan["model"]

        def _responses_create_with_retries(self, api_params):
            return self.gate.create(api_params)

    return AccountedClient
