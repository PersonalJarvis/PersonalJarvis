"""Canonical evidence receipts minted only by the trusted Swarm runtime.

An artifact's name and JSON content confer no authority. Only storage-assigned
provenance identifies a runtime receipt; its source attempt never changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit

from jarvis.core.swarm_types import SwarmActor, SwarmController

KINDS = frozenset({"execution", "http", "dependency", "verification"})
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")


def canonical(value: Any) -> str:
    """Encode deterministic JSON without non-finite numbers or custom objects."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def value_hash(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _text(payload: dict[str, Any], key: str, maximum: int = 1000) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"Receipt requires bounded {key}")
    return value


def _hash(payload: dict[str, Any], key: str) -> str:
    value = _text(payload, key, 64)
    if not _HASH.fullmatch(value):
        raise ValueError(f"Receipt requires a SHA256 {key}")
    return value


def _execution(payload: dict[str, Any]) -> dict[str, Any]:
    script = _text(payload, "script", 100_000)
    if "inputs" not in payload or not isinstance(payload.get("execution"), dict):
        raise ValueError("Execution receipt requires inputs and execution results")
    execution = payload["execution"]
    if (
        type(execution.get("exit_code")) is not int
        or "output" not in execution
        or not isinstance(execution.get("stdout"), str)
        or not isinstance(execution.get("stderr"), str)
    ):
        raise ValueError("Execution receipt requires output, streams and an integer exit code")
    payload.update(
        script_sha256=hashlib.sha256(script.encode("utf-8")).hexdigest(),
        input_sha256=value_hash(payload["inputs"]),
        output_sha256=value_hash(execution["output"]),
        stdout_sha256=hashlib.sha256(execution["stdout"].encode("utf-8")).hexdigest(),
        stderr_sha256=hashlib.sha256(execution["stderr"].encode("utf-8")).hexdigest(),
        exit_sha256=value_hash(execution["exit_code"]),
        execution_sha256=value_hash(execution),
    )
    return payload


def _payload(kind: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Receipt payload must be a JSON object")
    payload = json.loads(canonical(value))
    if kind == "execution":
        return _execution(payload)
    if kind == "http":
        url = urlsplit(_text(payload, "url", 8192))
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("HTTP receipt requires a source URL without credentials")
        if type(payload.get("status")) is not int or not 100 <= payload["status"] <= 599:
            raise ValueError("HTTP receipt requires a valid status")
        _hash(payload, "body_sha256")
    elif kind == "dependency":
        for key in ("package", "version", "registry", "entrypoint"):
            _text(payload, key)
        _hash(payload, "sha256")
        if "files" in payload:
            payload["files_sha256"] = value_hash(payload["files"])
    elif kind == "verification":
        if payload.get("kind") not in {"review", "javascript"}:
            raise ValueError("Verification receipt requires its verification kind")
        if type(payload.get("accepted")) is not bool:
            raise ValueError("Verification receipt requires a boolean decision")
        _text(payload, "verifier_id", 200)
        _hash(payload, "contract_hash")
        if "execution" in payload:
            payload["execution"] = _execution(payload["execution"])
    return payload


def build_receipt(
    controller: SwarmController,
    actor: SwarmActor,
    kind: str,
    payload: dict[str, Any],
    subject_ids: list[str],
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Freeze observed facts and public scope bindings; never copy credentials."""
    if kind not in KINDS:
        raise ValueError("Unknown runtime receipt kind")
    if (
        not isinstance(subject_ids, (list, tuple))
        or len(subject_ids) > 128
        or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in subject_ids)
        or len(set(subject_ids)) != len(subject_ids)
    ):
        raise ValueError("Receipt subjects must be distinct bounded artifact IDs")
    if trace_id is not None and (not isinstance(trace_id, str) or not 1 <= len(trace_id) <= 200):
        raise ValueError("Receipt trace ID must be a bounded string")
    receipt: dict[str, Any] = dict(
        receipt_version=1,
        origin="runtime-receipt",
        kind=kind,
        team_id=actor.team_id,
        source_agent_id=actor.agent_id,
        source_task_id=actor.task_id,
        source_attempt=actor.task_fence,
        controller_fence=controller.fence,
        subject_ids=list(subject_ids),
        trace_id=trace_id,
        payload=_payload(kind, payload),
    )
    if kind == "verification" and receipt["payload"]["verifier_id"] == actor.agent_id:
        raise ValueError("A verification receipt must identify an independent verifier")
    encoded = canonical(receipt)
    if len(encoded.encode("utf-8")) > 10_000_000:
        raise ValueError("Runtime receipt exceeds the artifact size limit")
    if any(token and token in encoded for token in (controller.token, actor.token)):
        raise ValueError("Runtime receipts cannot contain execution credentials")
    return receipt


def provenance(receipt: dict[str, Any]) -> dict[str, Any]:
    """Public immutable metadata retained alongside the content-addressed bytes."""
    return {
        "origin": "runtime-receipt",
        "receipt_kind": receipt["kind"],
        "receipt_version": receipt["receipt_version"],
        **{
            key: receipt[key]
            for key in (
                "team_id",
                "source_agent_id",
                "source_task_id",
                "source_attempt",
                "controller_fence",
                "subject_ids",
                "trace_id",
            )
        },
        "receipt_sha256": hashlib.sha256(canonical(receipt).encode("utf-8")).hexdigest(),
        **(
            {"output_sha256": receipt["payload"]["output_sha256"]}
            if receipt["kind"] == "execution"
            else {}
        ),
    }


def is_runtime_receipt(
    artifact: dict[str, Any], kind: str | None = None, actor: SwarmActor | None = None
) -> bool:
    """Check trusted storage metadata, including stale-receipt eligibility.

    Call this on metadata read from the authorized store, never worker JSON.
    Reading receipt bytes must still use the store's size/hash-verified reader.
    """
    source = artifact.get("provenance", {})
    if not isinstance(source, dict):
        return False
    if (
        source.get("origin") != "runtime-receipt"
        or source.get("receipt_version") != 1
        or source.get("receipt_kind") not in KINDS
        or (kind is not None and source.get("receipt_kind") != kind)
        or source.get("receipt_sha256") != artifact.get("sha256")
        or source.get("team_id") != artifact.get("team_id")
        or source.get("source_agent_id") != artifact.get("owner_id")
        or source.get("source_task_id") != artifact.get("task_id")
        or source.get("source_attempt") != artifact.get("attempt_fence")
    ):
        return False
    return actor is None or (
        actor.team_id == artifact.get("team_id")
        and actor.agent_id == artifact.get("owner_id")
        and actor.task_id == artifact.get("task_id")
        and actor.task_fence == artifact.get("attempt_fence")
    )
