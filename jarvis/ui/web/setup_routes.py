"""FastAPI routes for the Phase B9 Obsidian Setup Wizard (Sub-Agent 3).

Endpoints powering the Desktop App's "Obsidian" setup card:

* ``GET  /api/setup/obsidian/status``    — detect Obsidian install + check
  whether the Jarvis vault is registered. Never 5xx — UI must stay
  responsive even when ``obsidian.json`` is corrupt or pywin32 is missing.
* ``GET  /api/setup/obsidian/vaults``    — list the user's already
  registered Obsidian vaults, for the connect vault-choice picker
  (spec A6).
* ``POST /api/setup/obsidian/register``  — register a vault for Jarvis's
  wiki. ``mode="separate"`` (default, backward compatible) registers the
  Jarvis-owned vault in ``obsidian.json`` — creating a fresh index when
  Obsidian was installed but never launched — mirroring
  :class:`jarvis.setup.obsidian.RegisterResult` semantics: ``added`` and
  ``already_registered`` => 200, ``config_missing`` => 409 (defensive
  only; the separate-mode writer bootstraps a missing config instead),
  ``rolled_back`` => 500. ``mode="existing"`` instead repoints
  ``[wiki_integration].vault_root`` INTO ``<existing_vault>/Jarvis`` so
  every wiki write stays contained inside the user's own vault.

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
    find_registered_vault,
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
    can drive its toast / banner from one place. ``active_vault_root`` and
    ``restart_required`` (spec A6) are populated for both ``mode``s so the
    UI always has a stable field to show, regardless of which vault choice
    the user made. ``active_vault_root`` is only guaranteed on the HTTP 200
    responses returned here; the HTTP 409/500 error branches raise an
    ``HTTPException`` whose detail payload does not carry it.
    """

    status: Literal["added", "already_registered", "config_missing", "rolled_back"]
    vault_uuid: str | None = None
    backup_path: str | None = None
    error: str | None = None
    active_vault_root: str | None = None
    restart_required: bool = False


class ObsidianRegisterRequest(BaseModel):
    """Request body for ``POST /api/setup/obsidian/register`` (spec A6).

    ``mode="separate"`` (the default — an absent body means this too) is
    today's behavior, unchanged: register the Jarvis-owned vault in
    Obsidian's own vault index. ``mode="existing"`` instead writes INTO the
    user's own already-registered vault: Jarvis's vault root is repointed
    to ``<existing_vault_path>/Jarvis`` so every wiki write stays
    contained inside that subtree (containment by construction).
    """

    mode: Literal["separate", "existing"] = "separate"
    existing_vault_path: str | None = None


class ObsidianVaultInfo(BaseModel):
    """One entry in the vault picker list (spec A6)."""

    path: str
    name: str


class ObsidianVaultListResponse(BaseModel):
    """Response payload for ``GET /api/setup/obsidian/vaults``."""

    ok: bool
    config_exists: bool
    vaults: list[ObsidianVaultInfo]


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


def _read_vaults_for_request(request: Request):
    """Read the request-scoped Obsidian index without touching real test state."""
    override = getattr(request.app.state, "obsidian_config_path", None)
    if override is None:
        return read_obsidian_vaults()
    return read_obsidian_vaults(Path(override))


def _write_vault_root_config(values: dict) -> None:
    """Persist ``[wiki_integration].vault_root`` atomically (AP-7).

    Thin seam over :mod:`jarvis.core.config_writer` so tests can stub the
    disk write without touching the real ``jarvis.toml``. ``values`` is
    ``{"wiki_integration": {"vault_root": "<abs path>"}}`` — the shape the
    "existing vault" register flow (spec A6) writes.
    """
    from jarvis.core import config_writer

    vault_root = values["wiki_integration"]["vault_root"]
    config_writer.set_wiki_vault_root(vault_root)


def _reindex_vault_fts(request: Request, jarvis_root: Path) -> None:
    """Rebuild the FTS search index against a newly-connected vault (spec A6).

    Switching to an existing vault repoints ``vault_root`` into
    ``<vault>/Jarvis`` and asks for a restart — but the boot-time index
    (``server._init_wiki_boot_index``) only builds when the FTS table is
    EMPTY, so on restart it would skip and ``wiki-recall`` / the pre-answer
    injector would keep serving the PREVIOUS vault's stale rows. Reindexing
    here makes search reflect the new vault immediately.

    Best-effort and fail-open: a reindex failure must never 500 the register
    call (the vault switch itself already succeeded), but it must not be
    silent either — it is logged and recorded on the wiki health surface.
    """
    import sqlite3

    from jarvis.memory.wiki.fts_index import rebuild_index

    try:
        config = getattr(request.app.state, "config", None)
        from jarvis.memory.wiki.db_path import resolve_wiki_db_path

        data_dir = getattr(getattr(config, "memory", None), "data_dir", "./data")
        db_path = resolve_wiki_db_path(data_dir)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        try:
            indexed = rebuild_index(jarvis_root, conn)
            log.info(
                "obsidian_register: reindexed FTS for %d page(s) from %s",
                indexed,
                jarvis_root,
            )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — reindex must never break the switch
        log.warning("obsidian_register: FTS reindex after vault switch failed: %s", exc)
        try:
            from jarvis.memory.wiki.health import health

            health.record_chain_failure(f"vault-switch reindex failed: {exc}")
        except Exception:  # noqa: BLE001
            log.debug("obsidian_register: health record of reindex failure failed")


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
        vaults_state = _read_vaults_for_request(request)
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


@router.get("/obsidian/vaults", response_model=ObsidianVaultListResponse)
def obsidian_vaults(request: Request) -> ObsidianVaultListResponse:
    """List the user's registered Obsidian vaults for the connect picker (spec A6).

    ``app.state.obsidian_config_path`` is an override hook for tests (and
    any future multi-user host) — production requests leave it unset and
    fall back to the platform default via :func:`read_obsidian_vaults`.
    Never raises a 5xx — a corrupt ``obsidian.json`` degrades to an empty
    list with ``ok=False`` so the picker UI stays responsive.
    """
    override = getattr(request.app.state, "obsidian_config_path", None)
    try:
        state = read_obsidian_vaults(override)
    except ValueError as exc:
        log.warning("obsidian_vaults: corrupt obsidian.json: %s", exc)
        return ObsidianVaultListResponse(ok=False, config_exists=True, vaults=[])
    return ObsidianVaultListResponse(
        ok=True,
        config_exists=state.config_exists,
        vaults=[
            ObsidianVaultInfo(path=str(v.path), name=v.path.name)
            for v in state.vaults
        ],
    )


@router.post("/obsidian/register", response_model=ObsidianRegisterResponse)
def obsidian_register(
    request: Request,
    body: ObsidianRegisterRequest | None = None,
    dry_run: bool = Query(default=False),
) -> ObsidianRegisterResponse:
    """Register a vault for Jarvis's wiki (spec A6: vault choice).

    ``body`` is optional and backward compatible: no body (or an explicit
    ``{"mode": "separate"}``) is today's unchanged behavior — register the
    Jarvis-owned vault in Obsidian's own vault index.

    ``mode="existing"`` instead writes INTO the user's own vault: creates
    ``<existing_vault_path>/Jarvis`` and repoints
    ``[wiki_integration].vault_root`` there via :func:`_write_vault_root_config`
    (AP-7). Containment by construction — every subsequent wiki write is
    physically confined to that subtree, no ``AtomicWriter`` change needed.
    ``restart_required=True`` because the running curator/FTS index still
    targets the old vault until ``POST /api/settings/restart-app``.

    Status mapping:
      * ``added``               -> HTTP 200
      * ``already_registered``  -> HTTP 200 (``separate`` mode only)
      * ``config_missing``      -> HTTP 409 for ``separate`` (defensive:
        the writer now bootstraps a missing ``obsidian.json`` itself, so
        this branch only fires on a future regression); HTTP 200 for
        ``existing`` (the given path does not exist — a user-input error
        the UI shows inline, not a server fault)
      * ``rolled_back``         -> HTTP 500 (write failure, restored)

    Unexpected exceptions are translated to HTTP 500 with a
    ``rolled_back`` payload so the UI shows a consistent error toast.
    """
    req = body or ObsidianRegisterRequest()

    if req.mode == "existing":
        # Fail closed: an omitted/empty path must be rejected, never coerced
        # to ``Path("")`` -> ``Path(".")`` (the server's own CWD, which IS a
        # directory) — that would silently repoint the vault into the working
        # directory instead of surfacing the user-input error.
        raw_path = (req.existing_vault_path or "").strip()
        target_vault = Path(raw_path) if raw_path else None
        if target_vault is None or not target_vault.is_dir():
            return ObsidianRegisterResponse(
                status="config_missing",
                error="existing vault path not found",
                active_vault_root=str(_resolve_vault_path(request)),
                restart_required=False,
            )
        try:
            vaults_state = _read_vaults_for_request(request)
            registered_parent = find_registered_vault(vaults_state.vaults, target_vault)
        except ValueError as exc:
            return ObsidianRegisterResponse(
                status="config_missing",
                error=str(exc),
                active_vault_root=str(_resolve_vault_path(request)),
                restart_required=False,
            )
        if registered_parent is None:
            return ObsidianRegisterResponse(
                status="config_missing",
                error="existing vault is not registered in Obsidian",
                active_vault_root=str(_resolve_vault_path(request)),
                restart_required=False,
            )
        jarvis_root = target_vault / "Jarvis"
        # ``dry_run`` previews the would-be vault root without touching disk
        # or config — same contract the ``separate`` branch gives the flag.
        if not dry_run:
            jarvis_root.mkdir(parents=True, exist_ok=True)
            _write_vault_root_config(
                {"wiki_integration": {"vault_root": str(jarvis_root)}}
            )
            # Reindex search against the new vault NOW so it never serves the
            # previous vault's stale rows (spec A6); the restart realigns the
            # curator/watcher. Best-effort — never fails the switch.
            _reindex_vault_fts(request, jarvis_root)
        return ObsidianRegisterResponse(
            status="added",
            active_vault_root=str(jarvis_root),
            restart_required=True,
        )

    vault_path = _resolve_vault_path(request)

    try:
        override = getattr(request.app.state, "obsidian_config_path", None)
        if override is None:
            result = register_vault(vault_path, dry_run=dry_run)
        else:
            result = register_vault(
                vault_path,
                config_path=Path(override),
                dry_run=dry_run,
            )
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
            active_vault_root=str(vault_path),
            restart_required=False,
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
    "ObsidianRegisterRequest",
    "ObsidianRegisterResponse",
    "ObsidianVaultInfo",
    "ObsidianVaultListResponse",
    "SetupStateResponse",
]
