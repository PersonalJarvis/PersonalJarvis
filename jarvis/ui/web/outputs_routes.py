"""REST-API for the Outputs view (`/api/outputs`).

Lists the per-mission work directories under `<repo_parent>/sub-agents-outputs/`,
optionally enriched with mission status from the Phase-6 `missions.db`. Read-only.

Why a thin filesystem endpoint and not a frontend rewrite to `/api/missions`:
the Outputs view was built around the on-disk work directory layout (slug,
mascot-style utterance preview, "open in Explorer"). Missions are a logical
abstraction one level deeper (the `tasks/<mission_id>/workspace/` subdir).
Keeping the dir listing as the primary index lets users open the worktree
even when the mission row is gone (DB pruned, recovery cleanup).
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import FileResponse, HTMLResponse

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.missions.kontrollierer.deliverable_paths import (
    is_nondeliverable_scratch,
)
from jarvis.missions.state_machine import MissionState, is_terminal
from jarvis.platform import detect_platform
from jarvis.ui.web.artifact_view import VIEW_CSP, render_artifact_html

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/outputs", tags=["outputs"])


_SLUG_RE = re.compile(
    r"^(?P<ts>\d{8}T\d{6})__(?P<utterance>.+?)__(?P<short>[0-9a-f]{6,16})$"
)

# 2026-05-16: Mission-Manager also creates persistent state directories named
# `mission_<8-char-hex>` (the short-prefix of the UUID). These are NOT the
# worktree slug-dirs but they survive longer (worktree dirs get cleaned up
# after each task; mission state dirs persist). The outputs view should list
# both forms. The persistent dir uses the task-id as the "short" and has no
# embedded utterance or timestamp.
_MISSION_DIR_RE = re.compile(r"^mission_(?P<short>[0-9a-f-]{6,40})$")


def _outputs_root(request: Request) -> Path:
    """Resolve the sub-agents-outputs directory.

    Bootstrap stashed the path on `app.state.outputs_root` in
    `_init_mission_stack` (see `server.py:_init_mission_stack`). Falls back to
    `<repo_parent>/sub-agents-outputs` when missing (defensive — same default
    the bootstrap uses).
    """
    cached = getattr(request.app.state, "outputs_root", None)
    if cached is not None:
        return Path(cached)
    here = Path(__file__).resolve()
    return here.parent.parent.parent.parent / "sub-agents-outputs"


def _parse_slug(name: str) -> dict[str, Any]:
    """Pull started_at + utterance + short_id out of the directory name.

    Two patterns are recognised:
      * Worktree slug: `<YYYYmmddTHHMMSS>__<slug>__<short-hex>` — carries
        timestamp + utterance preview + short id.
      * Persistent mission dir: `mission_<short-hex>` — only the short id;
        timestamp comes from mtime, utterance must be fetched from the DB.

    Anything else returns `{started_at: None, utterance: None, short: None}`.
    """
    m = _SLUG_RE.match(name)
    if m:
        ts = m.group("ts")
        try:
            from datetime import datetime, timezone

            dt = datetime.strptime(ts, "%Y%m%dT%H%M%S").replace(
                tzinfo=timezone.utc
            )
            started_at: float | None = dt.timestamp()
        except ValueError:
            started_at = None
        rough = m.group("utterance").replace("-", " ").strip()
        utterance = rough[:1].upper() + rough[1:] if rough else None
        return {
            "started_at": started_at,
            "utterance": utterance,
            "short": m.group("short"),
        }

    m2 = _MISSION_DIR_RE.match(name)
    if m2:
        return {
            "started_at": None,
            "utterance": None,
            "short": m2.group("short"),
        }

    return {"started_at": None, "utterance": None, "short": None}


def _task_ids_in(dir_path: Path) -> list[str]:
    """Returns the names that live under ``<dir>/tasks/``.

    Names are either ``Step.task_id[:13]`` (Kontrollierer-style, fresh
    UUIDv7 per step — NOT mission_id) or slugified task labels
    (``01__refactor-router``, worktree-style). Kept for introspection /
    debugging only — the Outputs view derives mission-status from the
    dir-name prefix instead (see ``_mission_id_prefix_for_dir``).
    """
    tasks = dir_path / "tasks"
    if not tasks.is_dir():
        return []
    try:
        return [child.name for child in tasks.iterdir() if child.is_dir()]
    except OSError as exc:
        logger.debug("outputs: tasks-listing failed for %s: %s", dir_path, exc)
        return []


def _mission_id_prefix_for_dir(dir_name: str) -> str | None:
    """Returns the ``missions.id`` LIKE-prefix for an outputs directory.

    Only persistent ``mission_<short>`` dirs carry a recoverable
    mission-id prefix in their name (``KontrolliererOrchestrator``
    encodes ``mission_id[:13]`` into the dir-name at
    ``orchestrator.py:231``). Returns ``None`` for worktree slug-style
    dirs (``<ts>__<utterance>__<short>``) because their ``short`` token
    is an unrelated ``uuid.uuid4().hex[:8]`` minted in
    ``WorktreeManager.create`` (worktree.py:105) and has no mapping back
    to a mission_id.
    """
    m = _MISSION_DIR_RE.match(dir_name)
    if m is None:
        return None
    short = m.group("short")
    # 6 chars is the floor at which a LIKE-prefix is selective enough for
    # the missions table — matches the guard in ``_mission_status_lookup``.
    if len(short) < 6:
        return None
    return short


async def _mission_status_lookup(
    request: Request, prefixes: list[str]
) -> dict[str, dict[str, Any]]:
    """Map mission-id-prefix → {state, cost_usd, updated_ms, prompt} from missions.db.

    2026-05-18 (Audit-3 H1 fix): pre-2026-05-18 this function was called
    with the names of ``tasks/<task_id>/`` subdirectories under each output
    dir. Those names are ``Step.task_id`` UUID prefixes — a FRESH UUIDv7
    minted per worker step in ``Kontrollierer._run_task_with_critic_loop``
    (orchestrator.py:1073) — **not** the mission_id. The Mission-Manager
    persists ``mission_id`` (the orchestrator-level UUID) in
    ``missions.id``, so ``WHERE id LIKE '<task_id_prefix>%'`` never matched
    and the Outputs view rendered every ``mission_<short>/`` directory as
    ``status="unknown"`` even when the DB row was sitting right there.

    The correct prefix source is the directory-name itself: persistent
    mission dirs are named ``mission_<mission_id[:13]>`` (see
    ``KontrolliererOrchestrator._run_mission``, orchestrator.py:231), so
    the dir's ``short`` group from ``_MISSION_DIR_RE`` IS the mission-id
    prefix. Callers now pass those prefixes here.

    Worktree slug dirs ``<ts>__<utterance>__<short>`` embed a random
    ``uuid.uuid4().hex[:8]`` from ``WorktreeManager.create`` — that token
    is decoupled from any mission UUID by design, so no DB enrichment is
    possible for them; the caller passes a ``None``-style empty list for
    that case and the view falls back to the on-disk metadata.

    Returns ``{}`` on missing manager or DB error. Best-effort enrichment —
    the Outputs view stays functional even if Phase-6 isn't booted.
    """
    if not prefixes:
        return {}
    mgr = getattr(request.app.state, "mission_manager", None)
    if mgr is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for prefix in prefixes:
        # 6 chars is the floor we keep — below that the prefix would match
        # too many rows. The dir-name convention is currently 13 chars
        # (BUG-LIVE-10 forced the bump from 8 to 13 chars) but older
        # ``mission_<8-char>`` dirs may still be on disk.
        if len(prefix) < 6:
            continue
        try:
            cur = await mgr.store.conn.execute(
                "SELECT id, state, cost_usd, updated_ms, created_ms, prompt "
                "FROM missions WHERE id LIKE ? ORDER BY updated_ms DESC LIMIT 1",
                (f"{prefix}%",),
            )
            row = await cur.fetchone()
            await cur.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "outputs: mission DB lookup failed for %s: %s", prefix, exc
            )
            continue
        if row is None:
            continue
        record = {
            "state": row[1],
            "cost_usd": float(row[2] or 0.0),
            "updated_ms": int(row[3] or 0),
            "created_ms": int(row[4] or 0),
            "prompt": row[5],
            "full_id": row[0],
        }
        # Index by both the short prefix the caller passed AND the full
        # uuid — downstream code can look up either form.
        out[prefix] = record
        out[row[0]] = record
    return out


_STATE_TO_STATUS: dict[str, str] = {
    "PENDING": "running",
    "PLANNING": "running",
    "RUNNING": "running",
    # MissionState (state_machine.py) — real DB labels
    "CRITIQUING": "running",
    "LOOPING": "running",
    # Legacy aliases kept for back-compat with older missions on disk
    "CRITIC_REVIEW": "running",
    "AWAITING_CORRECTION": "running",
    "APPROVED": "success",
    "FAILED": "error",
    # Deliberate user action, not a failure — gets its own badge in the UI.
    "CANCELLED": "cancelled",
    "TIMED_OUT": "error",
    "ESCALATED": "error",
    "ORCHESTRATOR_CRASH": "error",
}


# Terminal mission-state *values* (string form). A re-run child counts as a
# "live" continuation only while its state is NOT in this set. Single source of
# truth = the state machine (mirrors ``missions_routes._TERMINAL_STATE_VALUES``).
_TERMINAL_STATE_VALUES: frozenset[str] = frozenset(
    s.value for s in MissionState if is_terminal(s)
)


async def _live_continuation_map(request: Request) -> dict[str, str]:
    """Map a terminal parent-mission-id → its still-running re-run child id.

    The Outputs "Continue"/"Restart" buttons re-dispatch a terminal mission's
    prompt as a NEW mission linked back via ``parent_mission_id`` (stored only
    in the child's ``MissionDispatched`` event payload — the ``missions`` header
    has no parent column). A terminal source card stays re-runnable forever, so
    without this signal it keeps offering "Continue" even after it has already
    been continued and that child is actively running. The user then sees a
    cancelled card sitting next to its own live continuation — both showing the
    identical stored prompt — and cannot tell whether "the mission" is running
    (forensic 2026-06-28, missions 019f0fa6 → 019f0fac).

    Returns ``{parent_id: child_id}`` for every parent whose NEWEST re-run child
    is still non-terminal (live). Best-effort: ``{}`` on a missing manager, an
    absent ``mission_events`` table, or any DB error — the Outputs view stays
    functional (mirrors ``_mission_status_lookup``). This is the read-side twin
    of the rerun endpoint's own ``find_child_missions`` liveness guard.
    """
    mgr = getattr(request.app.state, "mission_manager", None)
    if mgr is None:
        return {}
    try:
        cur = await mgr.store.conn.execute(
            """
            SELECT e.mission_id, m.state, e.payload_json
            FROM mission_events e
            JOIN missions m ON m.id = e.mission_id
            WHERE e.event_type = 'MissionDispatched'
            ORDER BY m.created_ms DESC
            """
        )
        rows = await cur.fetchall()
        await cur.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("outputs: continuation lookup failed: %s", exc)
        return {}

    out: dict[str, str] = {}
    for child_id, child_state, payload_json in rows:
        if str(child_state) in _TERMINAL_STATE_VALUES:
            continue  # only a LIVE child resolves the run/not-run ambiguity
        try:
            parent = json.loads(payload_json or "{}").get("parent_mission_id")
        except (ValueError, TypeError):
            parent = None
        if not parent:
            continue
        # Rows are newest-first; keep the first (newest) live child per parent.
        out.setdefault(str(parent), str(child_id))
    return out


@router.get("")
async def list_outputs(request: Request) -> dict[str, Any]:
    """List output directories newest-first.

    Each entry mirrors `OutputSummary` in `useOutputs.ts`:
        {slug, utterance, status, mission_id?, summary?, started_at?,
         completed_at?, duration_s?, github_url?, error?}

    `mission_id` is the full missions.id when the dir resolved to a DB row
    (None otherwise) — the frontend needs it to POST
    `/api/missions/{id}/cancel` for the hold-to-abort affordance.
    """
    root = _outputs_root(request)
    if not root.is_dir():
        return {"sessions": []}

    try:
        entries = sorted(
            (p for p in root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )
    except OSError as exc:
        logger.warning("outputs: listdir failed for %s: %s", root, exc)
        raise HTTPException(
            status_code=500, detail=f"Outputs-root nicht lesbar: {exc}"
        ) from exc

    sessions: list[dict[str, Any]] = []
    # Map dir-name → mission-id prefix derived from the dir-name itself.
    # Persistent ``mission_<short>`` dirs encode the mission_id prefix
    # directly (see ``_parse_slug``). Worktree slug dirs embed an unrelated
    # ``uuid.uuid4().hex[:8]`` token instead — no mission lookup is
    # possible for those, so we leave them as on-disk metadata only.
    dir_to_mission_prefix: dict[str, str | None] = {}
    all_mission_prefixes: list[str] = []
    for entry in entries:
        prefix = _mission_id_prefix_for_dir(entry.name)
        dir_to_mission_prefix[entry.name] = prefix
        if prefix is not None:
            all_mission_prefixes.append(prefix)

    mission_status = await _mission_status_lookup(request, all_mission_prefixes)
    # Resolve, in one pass, which terminal missions already have a still-running
    # re-run child — so a "Continue"/"Restart" card can be shown as live instead
    # of lying with an idle, re-runnable affordance (forensic 2026-06-28).
    continuation = await _live_continuation_map(request)

    for entry in entries:
        # Skip worktree-slug-only dirs (``<ts>__<utterance>__<short>``). Their
        # ``short`` is an unrelated ``uuid.uuid4().hex[:8]`` from
        # ``WorktreeManager.create`` and has no mission-id mapping, so they
        # would render as ``status="unknown"`` cards next to their canonical
        # ``mission_<short>`` sibling — that's the "two sub-agents for one
        # task" UX bug reported on 2026-05-26. The worktree dir is temporary
        # scaffolding (cleaned in ``Kontrollierer._run_task_with_critic_loop``
        # finally); power users can still address it via
        # ``/api/outputs/{slug}/artifacts`` directly.
        if dir_to_mission_prefix.get(entry.name) is None:
            continue
        parsed = _parse_slug(entry.name)
        # Persistent mission_* dirs have no timestamp in the slug — fall back
        # to mtime so the sidebar still sorts newest-first sensibly.
        if parsed["started_at"] is None:
            try:
                parsed["started_at"] = entry.stat().st_mtime
            except OSError:
                pass
        prefix = dir_to_mission_prefix.get(entry.name)
        # Look up by the mission-id prefix encoded in the dir-name. For
        # worktree slug-style dirs this is ``None`` and the row stays
        # unenriched — matches the legacy behaviour for those dirs.
        mission_row: dict[str, Any] | None = (
            mission_status.get(prefix) if prefix is not None else None
        )

        summary: dict[str, Any] = {
            "slug": entry.name,
            "utterance": parsed["utterance"],
            "status": "unknown",
            "mission_id": None,
            "started_at": parsed["started_at"],
            "completed_at": None,
            "duration_s": None,
            "github_url": None,
            "error": None,
            # When this (terminal) mission has already been continued/restarted
            # and that re-run is still live, the full id + slug of the running
            # child — so the UI shows "running", not a redundant "Continue".
            "active_child_id": None,
            "active_child_slug": None,
        }
        if mission_row is not None:
            status = _STATE_TO_STATUS.get(str(mission_row["state"]), "unknown")
            summary["status"] = status
            summary["mission_id"] = mission_row.get("full_id")
            full_id = mission_row.get("full_id")
            child_id = continuation.get(str(full_id)) if full_id else None
            if child_id:
                summary["active_child_id"] = child_id
                summary["active_child_slug"] = f"mission_{child_id[:13]}"
            summary["utterance"] = (
                summary["utterance"] or mission_row.get("prompt")
            )
            # Running missions tick wall-clock from created_ms: right after
            # dispatch created_ms == updated_ms, so updated-minus-created
            # rendered a frozen "RUNNING 0.0s" until the next mission event
            # (live mission 019eae15-5a31). The frontend polls every 3 s,
            # so now-minus-created ticks without a client-side timer. A
            # still-running mission also has no completion timestamp —
            # the card then falls back to started_at for its time label.
            running = status == "running"
            if mission_row["updated_ms"] and not running:
                summary["completed_at"] = mission_row["updated_ms"] / 1000.0
            if mission_row["created_ms"]:
                end_ms = (
                    time.time() * 1000.0 if running else mission_row["updated_ms"]
                )
                if end_ms:
                    summary["duration_s"] = max(
                        0.0,
                        (end_ms - mission_row["created_ms"]) / 1000.0,
                    )
        sessions.append(summary)

    return {"sessions": sessions}


@router.get("/capabilities")
def outputs_capabilities(request: Request) -> dict[str, Any]:
    """Report whether native file actions (reveal / open-with-default-app) work here.

    True only on a local desktop run (set by the launcher); False on a headless VPS
    where opening a file would target the *server's* desktop, not the user's. The
    frontend hides the native buttons when this is False; the routes 404 too.
    """
    native = bool(getattr(request.app.state, "native_file_actions", False))
    return {"native_file_actions": native, "platform": detect_platform()}


@router.get("/{slug}/plan")
async def get_output_plan(slug: str, request: Request) -> dict[str, Any]:
    """Return the plan + steps for a single session — empty stub for now.

    The `OutputsView` shows a `PlanStepList` in the right pane. Welle-4 hasn't
    plumbed plan/step persistence back through the Mission stack yet, so this
    intentionally returns `{plan: null, steps: []}` instead of 404 — the view
    treats that as "no plan available" and the user sees the session metadata
    plus the "open in Explorer" button.
    """
    root = _outputs_root(request)
    target = root / slug
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"unknown slug: {slug}")
    return {"plan": None, "steps": []}


# Soft size limits for the artifact preview endpoint — full bytes are
# served by `{slug}/files/{path}/raw`; the listing endpoint inlines only a
# short preview so the UI sidebar doesn't have to fetch each file.
_ARTIFACT_PREVIEW_BYTES: int = 4096
_ARTIFACT_MAX_LISTING: int = 200

# Cap the /view render path so a huge artifact can't block the event loop on the
# synchronous read or spike memory while rendering. Larger files use /download.
_VIEW_MAX_BYTES: int = 2 * 1_048_576


def _is_deliverable_relpath(rel_parts: tuple[str, ...]) -> bool:
    """True iff a mission-relative path is a genuine worker deliverable.

    Genuine deliverables live under ``tasks/<task_id>/artifacts/files/<rel>`` —
    the only subtree :meth:`KontrolliererOrchestrator._archive_task_artifacts`
    writes real worker output into (orchestrator.py:1218), and the exact subtree
    :func:`deliver_to_user_folder` mirrors to the user's Downloads. This is an
    **allowlist**, not a denylist: a future tool that seeds new state into the
    mission run_dir can never silently leak back into the Outputs view.

    Everything outside that subtree is internal scaffolding the user must never
    see as an "output":

      * ``claude_config/``   — the isolated ``CLAUDE_CONFIG_DIR`` seeded by
        :func:`build_worker_env` (sessions, settings.json, policy-limits.json,
        projects/*.jsonl, .claude.json, .last-cleanup, backups/). Live report
        2026-05-30: a trivial "make an HTML file" mission showed 10+ of these.
      * ``.codex/``          — ``CODEX_HOME``.
      * ``openclaw_state/``  — the OpenClaw state dir.
      * ``tasks/*/artifacts/diff*.patch`` and ``tasks/*/logs/*`` — forensic
        worker diffs + subprocess logs (kept on disk + reachable via the
        "open in Explorer" button, but not a deliverable).
      * ``reflections.md``   — episodic critic memory at the mission root.
    """
    # Shortest valid deliverable: tasks/<id>/artifacts/files/<name> (5 parts).
    if len(rel_parts) < 5:
        return False
    if not (
        rel_parts[0] == "tasks"
        and rel_parts[2] == "artifacts"
        and rel_parts[3] == "files"
    ):
        return False
    # Defence-in-depth (2026-06-21): hide tool-scratch the archive's --ignored
    # union may have re-imported on a PRE-FIX mission — a browser/QA worker's
    # gitignored Chrome user-data profiles (mission_019eeb34-bb67: 199 cache
    # blobs buried 2 real deliverables here). Shares the orchestrator's archive
    # predicate (single source of truth, anti-drift). ``rel_parts[4:]`` is the
    # path BELOW ``tasks/<id>/artifacts/files/`` — the deliverable-relative part.
    return not is_nondeliverable_scratch("/".join(rel_parts[4:]))


def _resolve_artifact_target(request: Request, slug: str, path: str) -> Path:
    """Resolve + allowlist-validate an artifact file path, or raise HTTPException.

    Mirrors the sandbox of the `/raw` handler: the slug stays inside the outputs
    root, the resolved file stays inside the session dir, and the relative path is
    a genuine deliverable (tasks/<id>/artifacts/files/**). Raises 404 (never 403,
    never confirming a scaffolding file exists) on any violation. Used by the
    download/view/reveal/open-native routes.
    """
    root = _outputs_root(request).resolve()
    base = (root / slug).resolve()
    try:
        base.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid slug") from exc
    target = (base / path).resolve()
    try:
        rel_parts = target.relative_to(base).parts
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown file: {path}"
        ) from exc
    if not _is_deliverable_relpath(rel_parts):
        raise HTTPException(status_code=404, detail=f"unknown file: {path}")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"unknown file: {path}")
    return target


def _is_text_filename(name: str) -> bool:
    """Heuristic — file extensions whose contents are safe to inline-preview."""
    lower = name.lower()
    return lower.endswith((
        ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml",
        ".log", ".patch", ".diff", ".py", ".ts", ".tsx", ".js", ".jsx",
        ".html", ".css", ".csv", ".env", ".cfg", ".ini", ".sh", ".ps1",
    )) or "." not in lower


@router.get("/{slug}/artifacts")
async def list_output_artifacts(slug: str, request: Request) -> dict[str, Any]:
    """List a mission's genuine deliverables — the files the worker wrote.

    Only the canonical deliverable subtree is surfaced:

        <mission_dir>/tasks/<task_id>/artifacts/files/<rel-path>

    This is exactly what :meth:`KontrolliererOrchestrator._archive_task_artifacts`
    extracts from the final diff (orchestrator.py:1218) and what
    :func:`deliver_to_user_folder` mirrors to the user's Downloads — so the
    Outputs view and the Downloads folder now agree on "what is an output".

    Deliberately EXCLUDED (internal scaffolding, see `_is_deliverable_relpath`):
    the isolated ``claude_config/`` (CLAUDE_CONFIG_DIR), ``.codex/`` (CODEX_HOME),
    ``openclaw_state/``, the forensic ``diff*.patch`` / ``logs/`` buckets, and
    ``reflections.md``. They stay on disk and remain reachable via the
    "open in Explorer" button; they are just not "results".

    Returned shape:
        {
          "files": [
            {"path": "tasks/019e3288/artifacts/files/HelloBot.html",
             "size": 1237, "mtime": 1778963665.0, "is_text": true,
             "preview": "<html>...\\n..."},
            ...
          ]
        }
    """
    root = _outputs_root(request).resolve()
    target = (root / slug).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid slug") from exc
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"unknown slug: {slug}")

    files: list[dict[str, Any]] = []
    try:
        for child in target.rglob("*"):
            if not child.is_file():
                continue
            # Surface ONLY genuine deliverables (tasks/<id>/artifacts/files/**).
            # Everything else in the mission run_dir is internal scaffolding the
            # user must never see as an "output": the isolated claude_config/
            # (CLAUDE_CONFIG_DIR), .codex/ (CODEX_HOME), openclaw_state/, the
            # forensic diff*.patch / logs/, and reflections.md. See
            # _is_deliverable_relpath for the full rationale (live report
            # 2026-05-30: a "make an HTML file" mission buried HelloBot.html
            # under 10+ claude_config rows).
            rel_parts = child.relative_to(target).parts
            if not _is_deliverable_relpath(rel_parts):
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            rel = "/".join(rel_parts)
            entry: dict[str, Any] = {
                "path": rel,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "is_text": _is_text_filename(child.name),
                "preview": None,
            }
            if entry["is_text"] and stat.st_size <= _ARTIFACT_PREVIEW_BYTES * 4:
                try:
                    preview = child.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    if len(preview) > _ARTIFACT_PREVIEW_BYTES:
                        preview = preview[:_ARTIFACT_PREVIEW_BYTES] + "\n…"
                    entry["preview"] = preview
                except OSError:
                    pass
            files.append(entry)
            if len(files) >= _ARTIFACT_MAX_LISTING:
                break
    except OSError as exc:
        logger.warning("outputs: rglob failed for %s: %s", target, exc)
        raise HTTPException(
            status_code=500, detail=f"artifact listing failed: {exc}"
        ) from exc

    files.sort(key=lambda f: f["mtime"], reverse=True)
    return {"files": files}


@router.get("/{slug}/files/{path:path}/raw")
async def get_output_artifact_raw(
    slug: str, path: str, request: Request
) -> dict[str, Any]:
    """Return the full UTF-8 decoded contents of a single artifact file.

    Sandboxed to `<outputs_root>/<slug>/`. Returns JSON `{path, size,
    text, truncated}`. Binary files are flagged via `is_text=false` in the
    listing endpoint; calling this on a non-text file returns a base64
    placeholder rather than raw bytes (UI can't render anyway).
    """
    root = _outputs_root(request).resolve()
    base = (root / slug).resolve()
    try:
        base.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid slug") from exc
    target = (base / path).resolve()
    try:
        rel_parts = target.relative_to(base).parts
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="path escapes session dir"
        ) from exc
    # Defense-in-depth: serve ONLY genuine deliverables, mirroring the listing
    # endpoint's `_is_deliverable_relpath` allowlist. The listing no longer
    # surfaces claude_config/.codex/diff/log paths, so the UI never links to
    # them — but a crafted or stale path must not be able to fetch the isolated
    # CLAUDE_CONFIG_DIR's session / .claude.json contents either. 404 (not 403)
    # so the endpoint doesn't confirm the scaffolding file even exists.
    if not _is_deliverable_relpath(rel_parts):
        raise HTTPException(status_code=404, detail=f"unknown file: {path}")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"unknown file: {path}")

    try:
        size = target.stat().st_size
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"stat failed: {exc}"
        ) from exc

    # 1 MiB hard ceiling on inline text fetch — anything bigger should be
    # opened on the desktop (the UI offers that path).
    max_inline = 1_048_576
    truncated = size > max_inline
    try:
        if _is_text_filename(target.name):
            text = target.read_text(
                encoding="utf-8", errors="replace"
            )[:max_inline]
        else:
            text = f"<binary file, {size} bytes — open on desktop>"
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"read failed: {exc}"
        ) from exc
    return {
        "path": path,
        "size": size,
        "text": text,
        "truncated": truncated,
    }


@router.get("/{slug}/files/{path:path}/download")
async def download_output_artifact(
    slug: str, path: str, request: Request, disposition: str = "attachment"
) -> FileResponse:
    """Serve a single artifact file for download or inline viewing.

    ``disposition=attachment`` (default) → browser saves to Downloads;
    ``disposition=inline`` → browser renders natively (PDF/HTML/image/text).
    Streams the file (no 1 MiB inline ceiling). Sandboxed via the deliverable
    allowlist in ``_resolve_artifact_target``.
    """
    if disposition not in ("attachment", "inline"):
        disposition = "attachment"
    target = _resolve_artifact_target(request, slug, path)
    media_type, _ = mimetypes.guess_type(target.name)
    headers = {"X-Content-Type-Options": "nosniff"}
    # Serving an HTML artifact inline renders it in the app origin; lock it down
    # with the same no-script CSP the /view route uses so a worker-authored .html
    # can't execute JS against the app.
    if disposition == "inline" and (media_type or "").startswith("text/html"):
        headers["Content-Security-Policy"] = VIEW_CSP
    return FileResponse(
        target,
        media_type=media_type or "application/octet-stream",
        filename=target.name,
        content_disposition_type=disposition,
        headers=headers,
    )


@router.get("/{slug}/files/{path:path}/view")
async def view_output_artifact(
    slug: str, path: str, request: Request
) -> HTMLResponse:
    """Render a text/markdown artifact as a standalone styled HTML page.

    Markdown is rendered to HTML; other text is shown escaped in <pre>. Carries a
    strict no-script CSP so artifact content can't execute JS in the app origin.
    The frontend only routes text/markdown here (binaries use /download?inline).
    """
    target = _resolve_artifact_target(request, slug, path)
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"stat failed: {exc}") from exc
    if size > _VIEW_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail="file too large for browser view — use download",
        )
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"read failed: {exc}"
        ) from exc
    return HTMLResponse(
        render_artifact_html(target.name, text),
        headers={
            "Content-Security-Policy": VIEW_CSP,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{slug}/files/{path:path}/reveal")
async def reveal_output_artifact(
    slug: str, path: str, request: Request
) -> dict[str, Any]:
    """Open the OS file manager with the artifact selected. Local desktop only."""
    if not getattr(request.app.state, "native_file_actions", False):
        raise HTTPException(status_code=404, detail="native file actions unavailable")
    target = _resolve_artifact_target(request, slug, path)
    from jarvis.platform import open_path

    opened = await asyncio.to_thread(open_path.reveal_in_folder, target)
    return {"opened": bool(opened), "path": str(target)}


@router.post("/{slug}/files/{path:path}/open-native")
async def open_output_artifact_native(
    slug: str, path: str, request: Request
) -> dict[str, Any]:
    """Open the artifact with the OS default application. Local desktop only."""
    if not getattr(request.app.state, "native_file_actions", False):
        raise HTTPException(status_code=404, detail="native file actions unavailable")
    target = _resolve_artifact_target(request, slug, path)
    from jarvis.platform import open_path

    opened = await asyncio.to_thread(open_path.open_file, target)
    return {"opened": bool(opened), "path": str(target)}


# --- "Open with" chooser ----------------------------------------------------
# The user picks which app opens an artifact (an editor like VS Code, the OS
# default app, or a browser). Every launch resolves the app to an absolute
# executable and starts a real subprocess (open_path.open_file/open_file_with)
# — NOT os.startfile(bare_name), which is a silent ShellExecute no-op from the
# pythonw background server (the "open button does nothing" root cause).

# Curated editor candidates shown in the chooser, in priority order. The id is
# the voice/app alias the cross-platform resolver understands; the label is the
# English display name (the frontend localises the structural pieces).
_OPENER_EDITORS: list[tuple[str, str]] = [
    ("code", "VS Code"),
    ("cursor", "Cursor"),
    ("subl", "Sublime Text"),
    ("notepad++", "Notepad++"),
    ("zed", "Zed"),
    ("windsurf", "Windsurf"),
]

# Browsers tried, in order, for the "browser" opener (first installed wins).
_BROWSER_CANDIDATES: tuple[str, ...] = (
    "chrome", "msedge", "firefox", "brave", "opera", "vivaldi",
)


def _known_opener_ids() -> set[str]:
    """The closed set of opener ids the server will launch — the security
    boundary. A client may only pick one of these, never a raw executable path,
    so an ``open-with`` request can't turn into arbitrary code execution."""
    return {"default", "browser"} | {oid for oid, _ in _OPENER_EDITORS}


def _macos_app_present(display_name: str) -> bool:
    """True if a macOS ``.app`` bundle with *display_name* exists. Best-effort —
    only consulted on darwin where the resolver yields an ``open_a`` target."""
    for base in ("/Applications", os.path.expanduser("~/Applications")):
        if os.path.isdir(os.path.join(base, f"{display_name}.app")):
            return True
    return False


def _resolve_installed(app_id: str) -> tuple[str, str] | None:
    """Resolve *app_id* to a launch ``(kind, value)`` IFF it is actually
    installed, else None. Distinguishes a real resolution (executable / a
    Start-Menu ``.lnk`` / a present macOS ``.app``) from the resolver's
    raw-name ``startfile`` fallback, which just means "not found"."""
    from jarvis.plugins.tool.app_resolver import resolve_app_launch_target

    target = resolve_app_launch_target(app_id)
    if target.kind == "executable":
        return (target.kind, target.value)
    if target.kind == "startfile" and target.value.lower().endswith(".lnk"):
        return (target.kind, target.value)
    if target.kind == "open_a" and _macos_app_present(target.value):
        return (target.kind, target.value)
    return None


def _resolve_browser_target() -> tuple[str, str] | None:
    """Resolve the first installed browser to a launch ``(kind, value)``, or
    None. Launching ``browser_exe <file>`` renders an HTML/PDF artifact in a
    real browser window without the WebView download trap or the strict CSP."""
    for cand in _BROWSER_CANDIDATES:
        resolved = _resolve_installed(cand)
        if resolved is not None:
            return resolved
    return None


def _resolve_opener(opener_id: str) -> tuple[str, str] | None:
    """Map an opener id to a launch ``(kind, value)``, or None if unavailable.

    ``default`` → the sentinel ``("default", "")`` (caller uses ``open_file``);
    ``browser`` → the first installed browser; an editor key → that editor iff
    installed. Unknown ids resolve to None."""
    if opener_id == "default":
        return ("default", "")
    if opener_id == "browser":
        return _resolve_browser_target()
    if opener_id in {oid for oid, _ in _OPENER_EDITORS}:
        return _resolve_installed(opener_id)
    return None


def _available_openers() -> list[dict[str, str]]:
    """The openers actually launchable on this host, for the chooser dialog."""
    out: list[dict[str, str]] = [{"id": "default", "label": "System default app"}]
    if _resolve_browser_target() is not None:
        out.append({"id": "browser", "label": "Browser"})
    for oid, label in _OPENER_EDITORS:
        if _resolve_installed(oid) is not None:
            out.append({"id": oid, "label": label})
    return out


class OpenWithBody(BaseModel):
    opener: str


class PreferredOpenerBody(BaseModel):
    opener: str = ""


@router.get("/openers")
def list_openers(request: Request) -> dict[str, Any]:
    """List the apps that can open an artifact here (editors + default + browser).

    Empty on a headless VPS (no local desktop apps): the frontend then falls
    back to opening the render URL in the current browser tab.
    """
    if not getattr(request.app.state, "native_file_actions", False):
        return {"openers": []}
    return {"openers": _available_openers()}


@router.get("/preferred-opener")
def get_preferred_opener(request: Request) -> dict[str, Any]:
    """Return the remembered opener id (``""`` = ask via the chooser)."""
    cfg = getattr(request.app.state, "config", None)
    opener = ""
    if cfg is not None:
        opener = getattr(getattr(cfg, "ui", None), "preferred_opener", "") or ""
    return {"opener": opener}


@router.put("/preferred-opener")
def put_preferred_opener(
    body: PreferredOpenerBody, request: Request
) -> dict[str, Any]:
    """Persist the remembered opener id (or ``""`` to clear it).

    Validated against the closed opener set — never a raw path. Persists to
    ``[ui] preferred_opener`` and hot-updates the in-memory config so the next
    open uses it without a restart.
    """
    opener = (body.opener or "").strip()
    if opener and opener not in _known_opener_ids():
        raise HTTPException(status_code=400, detail=f"unknown opener: {opener}")
    from jarvis.core import config_writer

    try:
        config_writer.set_preferred_opener(opener)
    except Exception as exc:  # noqa: BLE001
        logger.warning("preferred-opener persist failed: %s", exc)
    cfg = getattr(request.app.state, "config", None)
    ui = getattr(cfg, "ui", None) if cfg is not None else None
    if ui is not None:
        try:
            ui.preferred_opener = opener
        except Exception as exc:  # noqa: BLE001
            logger.debug("in-memory preferred_opener update skipped: %s", exc)
    return {"opener": opener}


@router.post("/{slug}/files/{path:path}/open-with")
async def open_artifact_with(
    slug: str, path: str, body: OpenWithBody, request: Request
) -> dict[str, Any]:
    """Open an artifact in the chosen app. Local desktop only.

    ``opener`` must be a known id (``default`` | ``browser`` | an editor key) —
    never a raw path, so this can't launch an arbitrary client-supplied binary.
    The app is resolved to an absolute executable and started via a real
    subprocess so a window actually appears.
    """
    if not getattr(request.app.state, "native_file_actions", False):
        raise HTTPException(status_code=404, detail="native file actions unavailable")
    opener = (body.opener or "").strip()
    if opener not in _known_opener_ids():
        raise HTTPException(status_code=400, detail=f"unknown opener: {opener}")
    target = _resolve_artifact_target(request, slug, path)
    resolved = _resolve_opener(opener)
    if resolved is None:
        raise HTTPException(
            status_code=409, detail=f"opener not available: {opener}"
        )
    from jarvis.platform import open_path

    kind, value = resolved
    if kind == "default":
        opened = await asyncio.to_thread(open_path.open_file, target)
    else:
        opened = await asyncio.to_thread(
            open_path.open_file_with, target, kind, value
        )
    return {"opened": bool(opened), "opener": opener}


@router.post("/{slug}/open")
async def open_output(slug: str, request: Request) -> dict[str, Any]:
    """Open the session folder in the OS file explorer (Windows: explorer.exe).

    Best-effort, non-blocking — frontend ignores errors. Restricts the path
    inside `outputs_root` so a crafted slug can't open arbitrary folders.
    """
    root = _outputs_root(request).resolve()
    target = (root / slug).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid slug") from exc
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"unknown slug: {slug}")
    if os.name == "nt":
        try:
            subprocess.Popen(  # noqa: S603,S607,ASYNC220 — explorer.exe path is os-fixed; Popen returns immediately (no event-loop block)
                ["explorer.exe", str(target)],
                creationflags=NO_WINDOW_CREATIONFLAGS,
                close_fds=True,
            )
        except OSError as exc:
            logger.warning("outputs: explorer.exe failed for %s: %s", target, exc)
            raise HTTPException(
                status_code=500, detail=f"open failed: {exc}"
            ) from exc
        return {"opened": True, "path": str(target)}
    return {"opened": False, "path": str(target), "reason": "non-Windows host"}
