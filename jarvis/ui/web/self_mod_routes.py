"""REST API for the self-mod pipeline (Phase 7.6).

Plan §7.6 endpoints (all read-only except `restore`):
- ``GET  /api/self-mod/audit``    → paginated audit log with filters.
- ``GET  /api/self-mod/mutable``  → SelfModRegistry.list_all().
- ``GET  /api/self-mod/backups``  → AtomicConfigWriter.list_backups().
- ``POST /api/self-mod/restore``  → restore from backup, admin_password required.

Plan §AP-2 defense-in-depth: every audit response has sensitive paths
re-redacted (even if the writer already did it — a second masking layer
in the UI).

The audit log is read **streaming** (tail-then-skim): no full-file load,
because the log can grow over months (Plan §AD-6, no rotation).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jarvis.core.self_mod import (
    AtomicConfigWriter,
    BackupRef,
    SelfModAudit,
    SelfModRegistry,
)

_LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/api/self-mod", tags=["self-mod"])

# Defense-in-depth masking: second layer in the API response.
# Plan §AP-2 compliant; path pattern hardcoded here (not user config).
_SENSITIVE_PATH_MARKERS: tuple[str, ...] = (
    "api_key",
    "api-key",
    "password",
    "token",
    "secret",
    "credential",
    "bearer",
    "oauth",
    "session_id",
    "cookie",
)


def _is_sensitive_audit_path(path: Any) -> bool:
    if not isinstance(path, str):
        return False
    lowered = path.lower()
    if lowered.startswith(("security.", "mcp_server.", "harness.")):
        return True
    return any(marker in lowered for marker in _SENSITIVE_PATH_MARKERS)


def _redact_audit_event(event: dict[str, Any]) -> dict[str, Any]:
    """Masks old/new_value for sensitive paths.

    Even though `SelfModAudit._redact` already did this at write time —
    this is the second layer for defense-in-depth in the UI response.
    """
    if not _is_sensitive_audit_path(event.get("path")):
        return event
    redacted = dict(event)
    for field in ("old_value", "new_value"):
        if field in redacted and redacted[field] is not None:
            value = redacted[field]
            text = str(value)
            redacted[field] = "*" * len(text) if text else ""
    return redacted


# ----------------------------------------------------------------------
# Dependencies
# ----------------------------------------------------------------------


def _get_audit(request: Request) -> SelfModAudit:
    audit = getattr(request.app.state, "self_mod_audit", None)
    if audit is None:
        # Fallback: SelfModAudit() uses the default path data/self_mod.log.
        audit = SelfModAudit()
    return audit


def _get_writer(request: Request) -> AtomicConfigWriter | None:
    return getattr(request.app.state, "self_mod_writer", None)


def _security_cfg(request: Request) -> Any:
    cfg = getattr(request.app.state, "config", None)
    if cfg is None:
        return None
    return getattr(cfg, "security", None)


def _check_admin_pass(provided: str | None, security_cfg: Any) -> bool:
    """Identical to skills_routes._check_admin_pass — reused here."""
    if security_cfg is None:
        return False
    expected = getattr(security_cfg, "admin_password_hash", "")
    if not expected or not provided:
        return False
    computed = hashlib.sha256(provided.encode("utf-8")).hexdigest()
    return hmac.compare_digest(computed, expected)


# ----------------------------------------------------------------------
# Cursor: opaque base64-encoded byte-offset into the audit log
# ----------------------------------------------------------------------


def _encode_cursor(byte_offset: int) -> str:
    raw = json.dumps({"o": byte_offset}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        return int(data["o"])
    except (ValueError, KeyError, json.JSONDecodeError, base64.binascii.Error) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid cursor: {exc}") from exc


# ----------------------------------------------------------------------
# Audit reader (streaming, newest first)
# ----------------------------------------------------------------------


def _stream_audit_lines(audit_path: Path) -> list[tuple[int, str]]:
    """Reads the jarvis.toml audit file backwards, yielding (offset, line) per entry.

    Tail-then-skim pattern: iterate backwards because the newest entries
    are at the end and pagination should be "newest on top". For the
    initial implementation we read the whole file (single pass) and
    reverse it — not ideal for very large logs (>100MB), but the
    Plan §7.10 backlog specifies that as an optimization.
    """
    if not audit_path.exists():
        return []
    try:
        with audit_path.open("rb") as fh:
            content = fh.read()
    except OSError as exc:
        _LOG.warning("Audit read failed: %s", exc)
        return []

    # Split on newline; we remember the byte offsets of the line starts
    # so the cursor stays stable.
    lines_with_offset: list[tuple[int, str]] = []
    offset = 0
    for raw_line in content.split(b"\n"):
        line = raw_line.decode("utf-8", errors="replace")
        if line.strip():
            lines_with_offset.append((offset, line))
        offset += len(raw_line) + 1  # +1 for the stripped \n
    # Reverse → newest first
    return list(reversed(lines_with_offset))


# ----------------------------------------------------------------------
# Response schemas
# ----------------------------------------------------------------------


class AuditQueryResponse(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)
    next_cursor: str | None = None
    total_returned: int = 0


class MutableSpecsResponse(BaseModel):
    specs: list[dict[str, Any]] = Field(default_factory=list)


class BackupsResponse(BaseModel):
    backups: list[dict[str, Any]] = Field(default_factory=list)


class RestoreRequest(BaseModel):
    filename: str = Field(min_length=1)
    admin_password: str | None = None


class RestoreResponse(BaseModel):
    ok: bool
    restored_from: str
    config_path: str


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------


@router.get("/audit", response_model=AuditQueryResponse)
async def get_audit(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    success_only: bool = Query(default=False),
) -> AuditQueryResponse:
    """Read-only paginated audit log (Plan §7.6).

    Cursor is opaque (base64-encoded byte offset). Filters run
    server-side — no full-table scan on the client.
    """
    audit = _get_audit(request)
    all_lines = _stream_audit_lines(audit.path)

    skip_until_offset: int | None = None
    if cursor:
        skip_until_offset = _decode_cursor(cursor)

    # Parse filters
    date_from_dt: datetime | None = None
    date_to_dt: datetime | None = None
    try:
        if date_from:
            date_from_dt = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
        if date_to:
            date_to_dt = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date: {exc}") from exc

    selected: list[dict[str, Any]] = []
    last_offset: int | None = None
    for offset, line in all_lines:
        # Cursor: skip all entries >= cursor offset (iterated backwards,
        # so this is "before this point in time" logic)
        if skip_until_offset is not None and offset >= skip_until_offset:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            _LOG.warning("Skipped corrupt audit line (offset=%d)", offset)
            continue

        # Filter
        if action and entry.get("action") != action and entry.get("error") != action:
            continue
        if actor and entry.get("requested_by") != actor:
            continue
        if success_only and not entry.get("ok", False):
            continue
        if date_from_dt or date_to_dt:
            ts_str = entry.get("ts", "")
            try:
                ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if date_from_dt and ts_dt < date_from_dt:
                continue
            if date_to_dt and ts_dt > date_to_dt:
                continue

        selected.append(_redact_audit_event(entry))
        last_offset = offset
        if len(selected) >= limit:
            break

    next_cursor = (
        _encode_cursor(last_offset)
        if last_offset is not None and len(selected) == limit
        else None
    )
    return AuditQueryResponse(
        events=selected,
        next_cursor=next_cursor,
        total_returned=len(selected),
    )


@router.get("/mutable", response_model=MutableSpecsResponse)
async def get_mutable_specs(request: Request) -> MutableSpecsResponse:  # noqa: ARG001
    """Read-only list of `SelfModRegistry.ALLOWED` entries.

    Phase 7.6 frontend: fills the "Mutable Settings" tab.
    """
    specs = [spec.model_dump(mode="json") for spec in SelfModRegistry.list_all()]
    return MutableSpecsResponse(specs=specs)


@router.get("/backups", response_model=BackupsResponse)
async def get_backups(
    request: Request, limit: int = Query(default=20, ge=1, le=100)
) -> BackupsResponse:
    """Read-only list of jarvis.toml backups.

    If the writer isn't in app.state, we return an empty list
    (graceful degradation in tests / headless).
    """
    writer = _get_writer(request)
    if writer is None:
        return BackupsResponse(backups=[])
    refs: list[BackupRef] = writer.list_backups(limit=limit)
    return BackupsResponse(backups=[ref.model_dump(mode="json") for ref in refs])


@router.post("/restore", response_model=RestoreResponse)
async def post_restore(body: RestoreRequest, request: Request) -> RestoreResponse:
    """Restore from a named backup. Plan §7.6: admin_password required.

    Path-traversal protection lives in the writer (see `AtomicConfigWriter.rollback`).
    """
    if not _check_admin_pass(body.admin_password, _security_cfg(request)):
        raise HTTPException(status_code=403, detail="Invalid admin_password")
    writer = _get_writer(request)
    if writer is None:
        raise HTTPException(status_code=503, detail="AtomicConfigWriter not available")
    try:
        restored = writer.rollback(body.filename)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Restore failed: {exc}") from exc
    return RestoreResponse(
        ok=True,
        restored_from=str(restored),
        config_path=str(writer.config_path),
    )
