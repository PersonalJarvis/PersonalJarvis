"""Jarvis Control API — the authenticated local facade (``/api/control/*``).

This is a THIN router. It owns no persistence logic of its own; it delegates to
the already-production-ready layers and only adds (a) a per-user Bearer guard,
(b) a uniform response envelope, and (c) a unified audit trail:

- config read/write  -> ``AtomicConfigWriter`` via ``PendingMutationStore``
                        (the 11-step atomic pipeline; SAFE auto-applies, ASK
                        defers to a confirm round-trip; ConfigReloaded fires so
                        ``brain.reply_language`` hot-reloads with no restart).
- providers          -> ``jarvis.brain.app_control`` (live apply + 3-layer persist).
- secrets / keys     -> ``cfg.set_secret/get_secret/delete_secret`` guarded by
                        the same ``ALLOWED_SECRET_KEYS`` whitelist the UI uses.
- control-API key    -> ``jarvis.core.control_key`` (reveal / rotate).

"Everything the user can do" is reached by COMPOSITION: a generic config
read/write over the self-mod allowlist + thin verbs for providers/secrets/
language. The existing same-origin UI routes (``/api/settings/*``) are untouched.
"""
from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from jarvis.core import config as cfg_mod
from jarvis.core import control_key
from jarvis.core.config import resolve_config_path
from jarvis.core.self_mod import (
    AllowlistViolationError,
    AtomicConfigWriter,
    AuditActor,
    AuditSource,
    MutationRequest,
    PreValidateError,
    SecretAccessError,
    SelfModAudit,
    SelfModRegistry,
)
from jarvis.core.self_mod.pending import PendingMutationStore
from jarvis.ui.web.control_auth import (
    require_control_key,
    require_control_key_or_loopback,
)

log = logging.getLogger("jarvis.control")

router = APIRouter(prefix="/api/control", tags=["control"])

# Secret keys the API may set/rotate — identical whitelist to the desktop UI.
# Imported from the wizard's declared secret slots (single source of truth).
from jarvis.setup.wizard import SECRETS as _WIZARD_SECRETS  # noqa: E402

ALLOWED_SECRET_KEYS: frozenset[str] = frozenset(s.key for s in _WIZARD_SECRETS)


# ----------------------------------------------------------------------
# Shared per-app writer + pending store (so confirm() finds a created entry)
# ----------------------------------------------------------------------


def _control_writer(request: Request) -> AtomicConfigWriter:
    """Lazily build + cache one AtomicConfigWriter on app.state.

    Wired with the app's EventBus so a SAFE-tier write fires ``ConfigReloaded``
    and the language hot-reload subscriber picks it up. The audit is shared with
    the rest of self-mod so voice/UI/REST/CLI mutations land in one log.
    """
    writer = getattr(request.app.state, "control_writer", None)
    if writer is not None:
        return writer
    bus = getattr(request.app.state, "bus", None)
    audit = getattr(request.app.state, "self_mod_audit", None) or SelfModAudit()
    writer = AtomicConfigWriter(
        config_path=resolve_config_path(), bus=bus, audit=audit
    )
    request.app.state.control_writer = writer
    return writer


def _pending_store(request: Request) -> PendingMutationStore:
    """Lazily build + cache one PendingMutationStore on app.state.

    Single instance across requests — an ASK-tier confirm arrives in a later
    request and must find the pending entry created in an earlier one.
    """
    store = getattr(request.app.state, "control_pending_store", None)
    if store is not None:
        return store
    store = PendingMutationStore(writer=_control_writer(request), auto_confirm_safe=True)
    request.app.state.control_pending_store = store
    return store


async def _emit(request: Request, event: Any) -> None:
    bus = getattr(request.app.state, "bus", None)
    if bus is None:
        brain = getattr(request.app.state, "brain", None)
        bus = getattr(brain, "_bus", None) if brain is not None else None
    if bus is None:
        return
    try:
        await bus.publish(event)
    except Exception as exc:  # noqa: BLE001 — never fail a mutation on a bus hiccup
        log.warning("control: event publish failed: %s", exc)


def _running_cfg() -> Any:
    from jarvis.brain.app_control import resolve_running_cfg

    return resolve_running_cfg()


def _pending_envelope(pending: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "applied": pending.applied,
        "needs_confirmation": pending.needs_confirmation,
        "pending_id": str(pending.id),
        "path": pending.path,
        "old_value": pending.old_value,
        "new_value": pending.new_value,
        "risk_tier": pending.risk_tier,
        "requires_restart": pending.requires_restart,
        "backup_path": pending.backup_path,
        "description": pending.description,
    }


# ----------------------------------------------------------------------
# Request bodies
# ----------------------------------------------------------------------


class ConfigWriteBody(BaseModel):
    path: str = Field(..., min_length=1, description="Dotted config path, e.g. brain.primary")
    value: Any = Field(..., description="New value (str/int/float/bool)")
    reason: str | None = Field(default=None, description="Optional reason for the audit log")


class PendingIdBody(BaseModel):
    pending_id: str = Field(..., min_length=1)


class LanguageBody(BaseModel):
    # Constrained so a bad value (e.g. "zh") is rejected with 422 at the boundary
    # instead of being written to jarvis.toml and silently normalised to "auto".
    reply_language: Literal["auto", "de", "en", "es"]


class SecretBody(BaseModel):
    value: str = Field(..., min_length=1, description="Raw secret value (API key, token, ...)")


class SwitchBody(BaseModel):
    provider: str = Field(..., min_length=1)
    persist: bool = Field(default=True)


class RotateBody(BaseModel):
    confirm: bool = Field(default=False)


# ----------------------------------------------------------------------
# Auth probe + discovery
# ----------------------------------------------------------------------


@router.get("/auth/probe", dependencies=[Depends(require_control_key)])
async def auth_probe() -> dict[str, Any]:
    """200 iff the Bearer key is valid (cheap check for CLIs / agents)."""
    return {"ok": True}


@router.get("/allowlist", dependencies=[Depends(require_control_key)])
async def get_allowlist() -> dict[str, Any]:
    """Machine-readable list of mutable settings so an agent can validate a
    request before sending it (instead of parsing Python source)."""
    return {"specs": [spec.model_dump() for spec in SelfModRegistry.list_all()]}


# ----------------------------------------------------------------------
# Generic config read / write
# ----------------------------------------------------------------------


@router.get("/config", dependencies=[Depends(require_control_key)])
async def get_config(path: str, request: Request) -> dict[str, Any]:
    if SelfModRegistry.is_forbidden(path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Path '{path}' belongs to a protected section and cannot be read.",
        )
    spec = SelfModRegistry.get_spec(path)
    try:
        value = _control_writer(request).read_value(path)
    except Exception as exc:  # noqa: BLE001 — a read failure must not 500
        log.warning("control: read of %r failed: %s", path, exc)
        value = None
    return {
        "path": path,
        "value": value,
        "in_allowlist": spec is not None,
        "risk_tier": spec.risk_tier if spec else None,
        "needs_restart": spec.needs_restart if spec else None,
        "description": spec.description if spec else None,
    }


def _apply_config_write(
    store: PendingMutationStore, path: str, value: Any, reason: str | None
) -> dict[str, Any]:
    request_obj = MutationRequest(
        path=path,
        new_value=value,
        actor=AuditActor.USER,
        source=AuditSource.UI,
        reason=reason,
    )
    try:
        pending = store.create(request_obj)
    except SecretAccessError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except AllowlistViolationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PreValidateError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return _pending_envelope(pending)


@router.put("/config", dependencies=[Depends(require_control_key)])
async def put_config(body: ConfigWriteBody, request: Request) -> dict[str, Any]:
    store = _pending_store(request)
    return _apply_config_write(store, body.path, body.value, body.reason)


@router.post("/config/confirm", dependencies=[Depends(require_control_key)])
async def confirm_config(body: PendingIdBody, request: Request) -> dict[str, Any]:
    store = _pending_store(request)
    try:
        mutation_id = UUID(body.pending_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="pending_id is not a valid id") from exc
    try:
        result = store.confirm(mutation_id)
    except KeyError as exc:
        raise HTTPException(status_code=410, detail="pending mutation expired or unknown") from exc
    return {
        "ok": result.ok,
        "applied": result.ok,
        "old_value": result.old_value,
        "new_value": result.new_value,
        "rolled_back": result.rolled_back,
        "backup_path": result.backup_path,
        "error": result.error_message,
    }


@router.post("/config/reject", dependencies=[Depends(require_control_key)])
async def reject_config(body: PendingIdBody, request: Request) -> dict[str, Any]:
    store = _pending_store(request)
    try:
        mutation_id = UUID(body.pending_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="pending_id is not a valid id") from exc
    store.reject(mutation_id, reason="rejected_via_control_api", source=AuditSource.UI)
    return {"ok": True, "pending_id": body.pending_id}


# ----------------------------------------------------------------------
# Language convenience verb
# ----------------------------------------------------------------------


@router.put("/language", dependencies=[Depends(require_control_key)])
async def put_language(body: LanguageBody, request: Request) -> dict[str, Any]:
    """Switch the language. A concrete code (de/en/es) sets BOTH the reply
    language (what Jarvis speaks) AND the interface language (what the user
    sees) so the whole experience switches; "auto" only affects replies (it
    mirrors the input language). Both are SAFE -> applied immediately, and the
    interface switches live in the open UI via the ConfigReloaded broadcast."""
    store = _pending_store(request)
    result = _apply_config_write(
        store, "brain.reply_language", body.reply_language, "control-api language switch"
    )
    if body.reply_language in ("de", "en", "es"):
        ui = _apply_config_write(
            store, "ui.language", body.reply_language, "control-api language switch"
        )
        result["ui_language"] = {"applied": ui.get("applied"), "value": body.reply_language}
    return result


# ----------------------------------------------------------------------
# Providers
# ----------------------------------------------------------------------


@router.get("/providers", dependencies=[Depends(require_control_key)])
async def get_providers() -> dict[str, Any]:
    from jarvis.brain.app_control import build_settings_snapshot

    return build_settings_snapshot(_running_cfg())


@router.put("/providers/{tier}", dependencies=[Depends(require_control_key)])
async def put_provider(tier: str, body: SwitchBody) -> dict[str, Any]:
    from jarvis.brain.app_control import apply_provider_switch

    result = await apply_provider_switch(
        tier, body.provider, cfg=_running_cfg(), persist=body.persist
    )
    if not result.get("ok"):
        kind = result.get("error_kind")
        code = 404 if kind in {"unknown_tier", "unknown_provider"} else 409
        raise HTTPException(status_code=code, detail=result.get("error") or "switch failed")
    return result


# ----------------------------------------------------------------------
# Secrets / provider keys
# ----------------------------------------------------------------------


def _secret_preview(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) >= 8:
        return f"{value[:3]}…{value[-3:]}"
    return "…"


@router.get("/secrets", dependencies=[Depends(require_control_key)])
async def list_secrets() -> dict[str, Any]:
    items = []
    for key in sorted(ALLOWED_SECRET_KEYS):
        value = cfg_mod.get_secret(key)
        items.append(
            {"key": key, "configured": bool(value), "preview": _secret_preview(value)}
        )
    return {"secrets": items}


@router.put("/secrets/{key}", dependencies=[Depends(require_control_key)])
async def set_secret_value(key: str, body: SecretBody, request: Request) -> dict[str, Any]:
    if key not in ALLOWED_SECRET_KEYS:
        raise HTTPException(status_code=404, detail=f"Unknown secret key: {key}")
    if not cfg_mod.set_secret(key, body.value):
        raise HTTPException(status_code=500, detail="Keyring write failed")
    from jarvis.core.events import SecretConfigured

    await _emit(request, SecretConfigured(key=key, action="set"))
    return {"ok": True, "key": key}


@router.delete("/secrets/{key}", dependencies=[Depends(require_control_key)])
async def delete_secret_value(key: str, request: Request) -> dict[str, Any]:
    if key not in ALLOWED_SECRET_KEYS:
        raise HTTPException(status_code=404, detail=f"Unknown secret key: {key}")
    cfg_mod.delete_secret(key)
    from jarvis.core.events import SecretConfigured

    await _emit(request, SecretConfigured(key=key, action="delete"))
    return {"ok": True, "key": key}


# ----------------------------------------------------------------------
# Control-API key reveal + rotate (loopback OR Bearer)
# ----------------------------------------------------------------------


@router.get("/api-key", dependencies=[Depends(require_control_key_or_loopback)])
async def get_api_key(request: Request) -> dict[str, Any]:
    key = control_key.get_control_key()
    # Audit every reveal (the loopback exemption means a same-host process can
    # read the key without the Bearer — worth a trail on a shared VPS).
    client = getattr(request, "client", None)
    log.info("control: API key revealed to %s", getattr(client, "host", "unknown"))
    return {"key": key, "masked": control_key.mask_control_key(key)}


@router.post("/api-key/rotate", dependencies=[Depends(require_control_key_or_loopback)])
async def rotate_api_key(body: RotateBody) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Rotation requires confirm=true; it invalidates the current key.",
        )
    try:
        key = control_key.rotate_control_key()
    except RuntimeError as exc:
        # Both stores rejected the new key — the old one is still active.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, "key": key, "masked": control_key.mask_control_key(key)}
