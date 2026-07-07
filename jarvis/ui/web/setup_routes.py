"""FastAPI routes for the Phase B9 Obsidian Setup Wizard (Sub-Agent 3).

Two endpoints power the Desktop App's "Obsidian" setup card:

* ``GET  /api/setup/obsidian/status``    — detect Obsidian install + check
  whether the Jarvis vault is registered. Never 5xx — UI must stay
  responsive even when ``obsidian.json`` is corrupt or pywin32 is missing.
* ``POST /api/setup/obsidian/register``  — register the Jarvis vault in
  ``obsidian.json``. Mirrors :class:`jarvis.setup.obsidian.RegisterResult`
  semantics: ``added`` and ``already_registered`` => 200, ``config_missing``
  => 409, ``rolled_back`` => 500.

The vault root is read from ``app.state.config.wiki_integration.vault_root``
(same surface as :mod:`jarvis.ui.web.wiki_routes`) and resolved through the
canonical :func:`jarvis.memory.wiki.vault_root.resolve_vault_root` (spec
A7) — a relative path anchors to the repo root, never the process CWD. No
mutation of app state happens here.

This module owns only the HTTP surface. All detection + write logic lives
in :mod:`jarvis.setup.obsidian` (Sub-Agents 1 + 2). This file is a pure
adapter.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from jarvis.memory.wiki.vault_root import resolve_vault_root
from jarvis.setup.obsidian import (
    detect_obsidian,
    is_vault_registered,
    read_obsidian_vaults,
    register_vault,
)
from jarvis.setup.state import has_seen_obsidian_setup, mark_obsidian_seen

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/setup", tags=["setup"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class ObsidianStatusResponse(BaseModel):
    """Status payload for ``GET /api/setup/obsidian/status``.

    ``recommended_action`` is the field the UI uses to pick the CTA
    button: install Obsidian, register the vault, or "everything is fine".
    ``note`` is only set when detection raised an exception that was
    suppressed so the UI stays responsive.
    """

    installed: bool
    version: str | None
    config_exists: bool
    vault_registered: bool
    vault_path: str
    recommended_action: Literal["ok", "install_obsidian", "register_vault"]
    note: str | None = None


class ObsidianRegisterResponse(BaseModel):
    """Response payload for ``POST /api/setup/obsidian/register``.

    Mirrors :class:`jarvis.setup.obsidian.RegisterResult` 1:1 so the UI
    can drive its toast / banner from one place.
    """

    status: Literal["added", "already_registered", "config_missing", "rolled_back"]
    vault_uuid: str | None = None
    backup_path: str | None = None
    error: str | None = None


class SetupStateResponse(BaseModel):
    """Response payload for ``GET /api/setup/state``.

    Reports which one-shot setup wizards the current user has already
    explicitly completed. Used by the Desktop App to decide whether
    to auto-open the Obsidian wizard on first wiki-tab visit.
    """

    obsidian_setup_seen: bool


# ---------------------------------------------------------------------------
# Vault-root resolution (mirrors wiki_routes._resolve_vault_root)
# ---------------------------------------------------------------------------
def _resolve_vault_path(request: Request) -> Path:
    """Return the absolute vault path the wizard should target.

    Resolves through the canonical
    :func:`jarvis.memory.wiki.vault_root.resolve_vault_root` (spec A7) —
    same resolver used by :mod:`jarvis.ui.web.wiki_routes` — so a relative
    ``vault_root`` anchors to the repo root, never the process CWD. Falls
    back to the resolver's default vault location when no config is wired
    up, so the route stays useful in minimal test apps.
    """
    config = getattr(request.app.state, "config", None)
    raw: Path | str | None = None
    if config is not None:
        wiki_cfg = getattr(config, "wiki_integration", None)
        if wiki_cfg is not None:
            raw = getattr(wiki_cfg, "vault_root", None)

    return resolve_vault_root(raw).path


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/obsidian/status", response_model=ObsidianStatusResponse)
def obsidian_status(request: Request) -> ObsidianStatusResponse:
    """Detect Obsidian + report whether the Jarvis vault is registered.

    Never raises a 5xx — any unexpected detection error is captured in
    the ``note`` field with a benign default payload so the UI keeps
    rendering the wizard card.
    """
    vault_path = _resolve_vault_path(request)
    vault_str = str(vault_path)

    try:
        detection = detect_obsidian()
        vaults_state = read_obsidian_vaults()
        registered = is_vault_registered(vaults_state.vaults, vault_path)
    except Exception as exc:  # noqa: BLE001 — UI must keep working
        log.warning("setup_route_status_failed: %s", exc, exc_info=True)
        return ObsidianStatusResponse(
            installed=False,
            version=None,
            config_exists=False,
            vault_registered=False,
            vault_path=vault_str,
            recommended_action="ok",
            note=f"detection error: {exc}",
        )

    if not detection.installed:
        recommended: Literal["ok", "install_obsidian", "register_vault"] = "install_obsidian"
    elif not registered:
        recommended = "register_vault"
    else:
        recommended = "ok"

    return ObsidianStatusResponse(
        installed=detection.installed,
        version=detection.version,
        config_exists=vaults_state.config_exists,
        vault_registered=registered,
        vault_path=vault_str,
        recommended_action=recommended,
    )


@router.post("/obsidian/register", response_model=ObsidianRegisterResponse)
def obsidian_register(
    request: Request,
    dry_run: bool = Query(default=False),
) -> ObsidianRegisterResponse:
    """Register the Jarvis vault in ``obsidian.json``.

    Status mapping:
      * ``added``               -> HTTP 200
      * ``already_registered``  -> HTTP 200
      * ``config_missing``      -> HTTP 409 (Obsidian was never started)
      * ``rolled_back``         -> HTTP 500 (write failure, restored)

    Unexpected exceptions are translated to HTTP 500 with a
    ``rolled_back`` payload so the UI shows a consistent error toast.
    """
    vault_path = _resolve_vault_path(request)

    try:
        result = register_vault(vault_path, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 — translate to 500 with envelope
        log.warning("setup_route_register_failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "rolled_back",
                "vault_uuid": None,
                "backup_path": None,
                "error": str(exc),
            },
        ) from exc

    backup_str = str(result.backup_path) if result.backup_path is not None else None

    if result.status in ("added", "already_registered"):
        return ObsidianRegisterResponse(
            status=result.status,
            vault_uuid=result.vault_uuid,
            backup_path=backup_str,
            error=result.error,
        )

    if result.status == "config_missing":
        raise HTTPException(
            status_code=409,
            detail={
                "status": "config_missing",
                "vault_uuid": None,
                "backup_path": None,
                "error": result.error,
            },
        )

    # status == "rolled_back"
    raise HTTPException(
        status_code=500,
        detail={
            "status": "rolled_back",
            "vault_uuid": result.vault_uuid,
            "backup_path": backup_str,
            "error": result.error,
        },
    )


# ---------------------------------------------------------------------------
# First-run setup-state flags (Phase B9.7 / Sub-Agent 6)
# ---------------------------------------------------------------------------
@router.get("/state", response_model=SetupStateResponse)
async def get_setup_state(request: Request) -> SetupStateResponse:
    """Return the persistent one-shot wizard flags.

    Never raises a 5xx — a missing or corrupt state file is reported
    as ``obsidian_setup_seen=False`` so the UI can decide its own
    first-run behaviour without paying a crash cost.
    """
    try:
        seen = has_seen_obsidian_setup()
    except Exception as exc:  # noqa: BLE001 — UI must keep working
        log.warning("setup_route_get_state_failed: %s", exc, exc_info=True)
        seen = False
    return SetupStateResponse(obsidian_setup_seen=seen)


@router.post("/state/obsidian-seen")
async def post_obsidian_seen(request: Request) -> dict[str, bool]:
    """Mark the Obsidian setup wizard as explicitly completed.

    Returns ``{"ok": True}`` on success and on failure alike — the
    setup flag is best-effort UX, not a load-bearing invariant.
    A failure is logged but never surfaced as a 5xx.
    """
    try:
        mark_obsidian_seen()
    except Exception as exc:  # noqa: BLE001 — UI must keep working
        log.warning("setup_route_mark_seen_failed: %s", exc, exc_info=True)
        return {"ok": False}
    return {"ok": True}


__all__ = [
    "router",
    "ObsidianStatusResponse",
    "ObsidianRegisterResponse",
    "SetupStateResponse",
]
