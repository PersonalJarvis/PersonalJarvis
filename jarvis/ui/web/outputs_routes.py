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

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

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
        }
        if mission_row is not None:
            status = _STATE_TO_STATUS.get(str(mission_row["state"]), "unknown")
            summary["status"] = status
            summary["mission_id"] = mission_row.get("full_id")
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
    return (
        rel_parts[0] == "tasks"
        and rel_parts[2] == "artifacts"
        and rel_parts[3] == "files"
    )


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
            subprocess.Popen(  # noqa: S603,S607 — explorer.exe path is os-fixed
                ["explorer.exe", str(target)],
                close_fds=True,
            )
        except OSError as exc:
            logger.warning("outputs: explorer.exe failed for %s: %s", target, exc)
            raise HTTPException(
                status_code=500, detail=f"open failed: {exc}"
            ) from exc
        return {"opened": True, "path": str(target)}
    return {"opened": False, "path": str(target), "reason": "non-Windows host"}
