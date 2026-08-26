"""The "Local models" overview: one payload, painted from disk first.

Opening the section used to mean four cold requests (server, roles,
inventory, shortlist) that each ran their own probes, and two to three
seconds of nothing on screen. This module gives the section ONE payload —
:func:`build_overview` composes the four from the shared inventory snapshot
(:mod:`jarvis.brain.ollama_inventory`) — and keeps the last good one on disk
so the next open paints at once:

* :func:`get_overview` answers ``(payload, source)``. A fresh in-memory memo
  is ``"live"``. Otherwise the disk snapshot is returned immediately as
  ``"cache"`` and ONE background refresh is scheduled (its task reference is
  kept, never created at import — AP-26); a snapshot older than a day is
  skipped in favour of a live build, and ``fresh=True`` forces one.
* :func:`load_snapshot` / :func:`save_snapshot` keep
  ``DATA_DIR/local_models_snapshot.json`` (atomic ``.part`` + ``os.replace``).

The dict builders here (:func:`model_row`, :func:`role_row`, ...) are the
ONE place the wire shape of the section's rows is spelled out; the routes
wrap them in their Pydantic models (AP-4), so the single endpoints and the
overview cannot drift apart.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from jarvis.brain import ollama_inventory as inventory
from jarvis.brain import ollama_pull, ollama_roles, ollama_runtime
from jarvis.brain.ollama_inventory import (
    OllamaModelInfo,
    OllamaRunningModel,
    OllamaServerError,
    same_model,
)
from jarvis.plugins.brain.ollama import normalize_server_root

log = logging.getLogger(__name__)

__all__ = [
    "SNAPSHOT_FILE_NAME",
    "DEFAULT_MAX_AGE_S",
    "STALE_AFTER_S",
    "build_overview",
    "forget",
    "get_overview",
    "inventory_payload",
    "load_snapshot",
    "model_row",
    "role_row",
    "roles_payload",
    "running_row",
    "save_snapshot",
    "server_payload",
    "snapshot_path",
]

SNAPSHOT_FILE_NAME = "local_models_snapshot.json"

#: How long an in-memory overview counts as live. The section refetches on
#: this cadence too, so two opens within it cost nothing.
DEFAULT_MAX_AGE_S = 15.0

#: A disk snapshot older than this is not worth painting: a day is long
#: enough for a server to have been reinstalled or its downloads pruned.
STALE_AFTER_S = 24 * 3600.0

# ── Row builders (the wire shape, spelled out once) ──────────────────────


def model_row(
    info: OllamaModelInfo, running: dict[str, OllamaRunningModel], used_by: list[str]
) -> dict[str, Any]:
    """One inventory row: the download's facts plus what ``/api/ps`` says."""
    live = next((r for key, r in running.items() if same_model(key, info.name)), None)
    return {
        "name": info.name,
        "size_bytes": info.size_bytes,
        "digest": info.digest,
        "modified_at": info.modified_at,
        "family": info.family,
        "parameter_size": info.parameter_size,
        "quantization_level": info.quantization_level,
        "context_length": info.context_length,
        "capabilities": list(info.capabilities),
        "license": info.license,
        "probed": info.probed,
        "used_by": used_by,
        "loaded": live is not None,
        "size_vram_bytes": live.size_vram_bytes if live else 0,
        "expires_at": live.expires_at if live else "",
        "running_context_length": live.context_length if live else None,
    }


def running_row(model: OllamaRunningModel) -> dict[str, Any]:
    return asdict(model)


def role_row(state: ollama_roles.RoleState) -> dict[str, Any]:
    spec = state.spec
    return {
        "id": spec.id,
        "label_key": spec.label_key,
        "config_key": spec.config_key,
        "current": state.current,
        "installed": state.installed,
        "required": list(spec.required),
        "recommended_capabilities": list(spec.recommended),
        "qualifying": list(state.qualifying),
        "recommended": state.recommended,
        "recommended_reason": state.recommended_reason,
        "writable": spec.writable,
        "advanced": spec.advanced,
        "note": state.note,
        "context_tokens": state.context_tokens,
        "context_source": state.context_source,
    }


# ── Payloads (what the single endpoints answer) ──────────────────────────


async def _snapshot(root: str) -> tuple[inventory.InventorySnapshot | None, str | None]:
    """The shared snapshot, or ``(None, sentence)`` when the server is down."""
    try:
        return await inventory.cached_snapshot(root), None
    except OllamaServerError as exc:
        return None, str(exc)


def _inventory_from(
    provider_id: str,
    root: str,
    cfg: Any,
    snapshot: inventory.InventorySnapshot | None,
    error: str | None,
) -> dict[str, Any]:
    if snapshot is None:
        return {
            "provider": provider_id,
            "server": root,
            "models": [],
            "running": [],
            "disk_bytes": 0,
            "loaded_vram_bytes": 0,
            "error": error,
        }
    running = {r.name: r for r in snapshot.running}
    return {
        "provider": provider_id,
        "server": root,
        "models": [
            model_row(m, running, ollama_roles.roles_using(cfg, m.name) if cfg else [])
            for m in snapshot.models
        ],
        "running": [running_row(r) for r in snapshot.running],
        "disk_bytes": sum(m.size_bytes for m in snapshot.models),
        "loaded_vram_bytes": sum(r.size_vram_bytes for r in snapshot.running),
        "error": None,
    }


async def inventory_payload(provider_id: str, root: str, cfg: Any) -> dict[str, Any]:
    """Every download with its facts, what is loaded, and the disk total —
    the body of ``GET .../inventory``."""
    snapshot, error = await _snapshot(root)
    return _inventory_from(provider_id, root, cfg, snapshot, error)


async def _roles_from(
    provider_id: str,
    root: str,
    cfg: Any,
    snapshot: inventory.InventorySnapshot | None,
    error: str | None,
    shortlist: list[dict[str, Any]] | None,
    machine: ollama_roles.Machine | None = None,
) -> dict[str, Any]:
    models = list(snapshot.models) if snapshot is not None else []
    states, _unused = await ollama_roles.list_roles(
        root, cfg, models=models, shortlist=shortlist, machine=machine
    )
    return {
        "provider": provider_id,
        "server": root,
        "roles": [role_row(s) for s in states],
        "error": error,
    }


async def roles_payload(provider_id: str, root: str, cfg: Any) -> dict[str, Any]:
    """Every role with its pick, what qualifies, and the recommendation —
    the body of ``GET .../roles``."""
    snapshot, error = await _snapshot(root)
    return await _roles_from(provider_id, root, cfg, snapshot, error, None)


async def _server_from(status: dict[str, object], fallback_root: str) -> dict[str, Any]:
    root = str(status.get("base_url") or fallback_root)
    running: list[dict[str, Any]] = []
    disk = 0
    error: str | None = None
    if status.get("running"):
        snapshot, error = await _snapshot(root)
        if snapshot is not None:
            disk = sum(m.size_bytes for m in snapshot.models)
            running = [running_row(r) for r in snapshot.running]
        else:
            log.info("local-models: inventory unavailable at %s: %s", root, error)
    return {
        "installed": bool(status.get("installed")),
        "binary": str(status.get("binary") or ""),
        "running": bool(status.get("running")),
        "version": str(status.get("version") or ""),
        "detail": str(status.get("detail") or ""),
        "base_url": root,
        "host_kind": str(status.get("host_kind") or "local"),
        "models_dir": str(status.get("models_dir") or ""),
        "running_models": running,
        "disk_bytes": disk,
        "loaded_vram_bytes": sum(int(r.get("size_vram_bytes") or 0) for r in running),
        "error": error,
    }


async def server_payload(root: str) -> dict[str, Any]:
    """Runtime picture plus what is loaded and the disk total — the body of
    ``GET .../server``. The runtime probe is synchronous and runs off-loop."""
    status = await asyncio.to_thread(ollama_runtime.runtime_status)
    return await _server_from(status, root)


async def _recommendations() -> dict[str, Any]:
    try:
        return await ollama_pull.recommendations()
    except Exception as exc:  # noqa: BLE001 — the shortlist is advisory, the overview is not
        log.warning("local-models: shortlist unavailable: %s", exc)
        return {"models": [], "error": str(exc)}


async def build_overview(root: str, cfg: Any, *, provider_id: str = "ollama") -> dict[str, Any]:
    """Compose server, roles, inventory and the shortlist from ONE sweep.

    The runtime probe, the shortlist (registry + hardware) and the inventory
    snapshot run concurrently; the payload carries ``fetched_at`` (epoch
    seconds) so a reader can say how old it is.
    """
    root = normalize_server_root(root)
    status, recommended, (snapshot, error) = await asyncio.gather(
        asyncio.to_thread(ollama_runtime.runtime_status),
        _recommendations(),
        _snapshot(root),
    )
    shortlist = recommended.get("models") if isinstance(recommended, dict) else None
    roles = await _roles_from(
        provider_id,
        root,
        cfg,
        snapshot,
        error,
        shortlist if isinstance(shortlist, list) else [],
        machine=ollama_roles.machine_from(recommended),
    )
    server = await _server_from(status, root)
    return {
        "server": server,
        "roles": roles,
        "inventory": _inventory_from(provider_id, root, cfg, snapshot, error),
        "recommended": recommended,
        "fetched_at": time.time(),
    }


# ── Disk snapshot ────────────────────────────────────────────────────────


def _data_dir() -> Path:
    env_dir = os.environ.get("JARVIS_DATA_DIR")
    if env_dir and env_dir.strip():
        return Path(env_dir.strip())
    from jarvis.core.config import DATA_DIR  # lazy (AP-26)

    return DATA_DIR


def snapshot_path() -> Path:
    return _data_dir() / SNAPSHOT_FILE_NAME


def load_snapshot(path: Path | None = None) -> dict[str, Any] | None:
    """The saved overview ``{"root", "fetched_at", "payload"}`` or ``None``.

    A missing file is the normal first-run state; an unreadable or malformed
    one is logged and treated the same — the next live build overwrites it.
    """
    target = path or snapshot_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        log.warning("local-models: snapshot %s unreadable", target, exc_info=True)
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("local-models: snapshot %s is not JSON; ignoring it", target)
        return None
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("payload"), dict)
        or not isinstance(data.get("fetched_at"), (int, float))
    ):
        log.warning("local-models: snapshot %s has an unexpected shape; ignoring it", target)
        return None
    return data


def save_snapshot(root: str, payload: dict[str, Any], path: Path | None = None) -> None:
    """Persist ``payload`` atomically (``.part`` + ``os.replace``).

    A crash mid-write leaves the previous snapshot intact; a write failure
    is logged and swallowed on purpose — the overview was already answered
    live, the cache is only the next open's head start.
    """
    target = path or snapshot_path()
    part = target.with_suffix(target.suffix + ".part")
    data = {"root": root, "fetched_at": float(payload.get("fetched_at") or time.time())}
    data["payload"] = payload
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        part.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(part, target)
    except OSError:
        log.warning("local-models: could not save the snapshot to %s", target, exc_info=True)
        try:
            part.unlink(missing_ok=True)
        except OSError:
            log.debug("local-models: stale .part left at %s", part, exc_info=True)


# ── Stale-while-revalidate ───────────────────────────────────────────────

_memo: dict[str, tuple[float, dict[str, Any]]] = {}  # root -> (monotonic, payload)
#: The one background refresh per root; the reference is kept so the task
#: cannot be garbage-collected mid-flight, and a second open joins it.
_refresh_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}


def _reset_for_tests() -> None:
    """Drop the in-memory overview memo and refresh bookkeeping (tests only)."""
    _memo.clear()
    _refresh_tasks.clear()


def forget(root: str) -> None:
    """Drop the memoised overview for ``root`` after a write.

    A role pick, a tune, an unload or a delete changes what the next open
    must show; without this the memo answered the OLD payload for up to
    :data:`DEFAULT_MAX_AGE_S` after the write and the row the user had just
    changed stayed where it was. The disk snapshot is left alone — the next
    live build overwrites it.
    """
    _memo.pop(normalize_server_root(root), None)


def _fresh(root: str, max_age_s: float) -> dict[str, Any] | None:
    hit = _memo.get(root)
    if hit is not None and time.monotonic() - hit[0] < max_age_s:
        return hit[1]
    return None


async def _refresh(root: str, cfg: Any, provider_id: str) -> dict[str, Any]:
    """Build live, memoise, save to disk (off-loop); returns the payload."""
    payload = await build_overview(root, cfg, provider_id=provider_id)
    _memo[root] = (time.monotonic(), payload)
    await asyncio.to_thread(save_snapshot, root, payload)
    return payload


def _refresh_done(root: str, task: asyncio.Task[dict[str, Any]]) -> None:
    _refresh_tasks.pop(root, None)
    if task.cancelled():
        log.info("local-models: background refresh for %s was cancelled", root)
        return
    exc = task.exception()
    if exc is not None:
        log.warning("local-models: background refresh for %s failed: %s", root, exc)


def _schedule_refresh(root: str, cfg: Any, provider_id: str) -> asyncio.Task[dict[str, Any]]:
    """ONE refresh per root: a second open while one runs joins it."""
    running = _refresh_tasks.get(root)
    if running is not None and not running.done():
        return running
    task = asyncio.create_task(
        _refresh(root, cfg, provider_id), name=f"local-models-overview-refresh-{root}"
    )
    _refresh_tasks[root] = task
    task.add_done_callback(functools.partial(_refresh_done, root))
    return task


async def get_overview(
    root: str,
    cfg: Any,
    *,
    provider_id: str = "ollama",
    max_age_s: float = DEFAULT_MAX_AGE_S,
    fresh: bool = False,
) -> tuple[dict[str, Any], str]:
    """``(payload, source)`` — ``"live"`` or ``"cache"``.

    Order of preference: a live payload younger than ``max_age_s`` in memory;
    the disk snapshot (younger than :data:`STALE_AFTER_S`, same server) —
    returned at once with ONE background refresh scheduled; a live build,
    which also seeds memory and disk. ``fresh`` skips straight to the build.
    When the build itself fails (a bug, not an offline server — that is a
    normal payload) and a disk snapshot exists, the snapshot is the answer.
    """
    root = normalize_server_root(root)
    disk: dict[str, Any] | None = None
    if not fresh:
        if (hit := _fresh(root, max_age_s)) is not None:
            return hit, "live"
        disk = await asyncio.to_thread(load_snapshot)
        if disk is not None and disk.get("root") != root:
            disk = None
        if disk is not None and time.time() - float(disk["fetched_at"]) < STALE_AFTER_S:
            _schedule_refresh(root, cfg, provider_id)
            return dict(disk["payload"]), "cache"
    try:
        return await _refresh(root, cfg, provider_id), "live"
    except Exception:
        if disk is None:
            raise
        log.warning(
            "local-models: live overview failed; answering the disk snapshot", exc_info=True
        )
        return dict(disk["payload"]), "cache"
