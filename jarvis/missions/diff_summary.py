"""Turn a mission's archived worker diff into a per-file ledger.

Every mission archives what its worker changed as a unified diff under
``tasks/<task_id>/artifacts/diff.patch`` (``diff.iter<N>.patch`` per critic
round). That file is the most concrete record of what an agent actually did —
more concrete than its own report, which is prose — but a 60 KB patch is not
something a person reads. This module reduces it to what a person asks:
which files, new or changed, how much.

Pure text parsing, bounded reads, no IO beyond the mission directory. The
``WorkerDraftReady`` event carries the same diff capped at 8 000 characters
(orchestrator), which is why the ledger reads the file on disk instead.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

_MISSION_ID_RE = re.compile(r"^[0-9a-f-]{6,64}$", re.IGNORECASE)
_MAX_READ_BYTES: Final[int] = 4 * 1_048_576
_MAX_FILES: Final[int] = 400

_GIT_HEADER = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")
_RENAME_FROM = re.compile(r"^rename from (?P<path>.+)$")
_RENAME_TO = re.compile(r"^rename to (?P<path>.+)$")


def summarize_unified_diff(text: str) -> dict[str, Any]:
    """Reduce one unified diff to ``{files, additions, deletions, truncated_files}``.

    Each file: ``path`` (the new path), ``previous_path`` for a rename,
    ``status`` in ``added | modified | deleted | renamed``, ``additions``,
    ``deletions`` and ``binary``. Sections that are not ``diff --git`` blocks
    (a worker-authored evidence pseudo-diff, for example) are skipped rather
    than miscounted.
    """
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_hunk = False
    truncated_files = False

    for raw in text.splitlines():
        header = _GIT_HEADER.match(raw)
        if header:
            if len(files) >= _MAX_FILES:
                truncated_files = True
                current = None
                in_hunk = False
                continue
            current = {
                "path": header.group("b"),
                "previous_path": None,
                "status": "modified",
                "additions": 0,
                "deletions": 0,
                "binary": False,
            }
            files.append(current)
            in_hunk = False
            continue
        if raw.startswith("diff "):
            # Some other diff flavour (``diff --desktop-action-evidence``):
            # not a file, and its lines must not be counted against one.
            current = None
            in_hunk = False
            continue
        if current is None:
            continue
        if not in_hunk:
            if raw.startswith("new file mode"):
                current["status"] = "added"
            elif raw.startswith("deleted file mode"):
                current["status"] = "deleted"
            elif raw.startswith("Binary files"):
                current["binary"] = True
            elif m := _RENAME_FROM.match(raw):
                current["previous_path"] = m.group("path")
                current["status"] = "renamed"
            elif m := _RENAME_TO.match(raw):
                current["path"] = m.group("path")
                current["status"] = "renamed"
            elif raw.startswith("@@"):
                in_hunk = True
            continue
        if raw.startswith("@@"):
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            current["additions"] += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            current["deletions"] += 1

    return {
        "files": files,
        "additions": sum(f["additions"] for f in files),
        "deletions": sum(f["deletions"] for f in files),
        "truncated_files": truncated_files,
    }


def _final_patch(artifacts_dir: Path) -> Path | None:
    """``diff.patch`` when present, else the highest ``diff.iter<N>.patch``."""
    final = artifacts_dir / "diff.patch"
    if final.is_file():
        return final
    best: tuple[int, Path] | None = None
    try:
        for candidate in artifacts_dir.glob("diff.iter*.patch"):
            m = re.search(r"iter(\d+)", candidate.name)
            if not m:
                continue
            n = int(m.group(1))
            if best is None or n > best[0]:
                best = (n, candidate)
    except OSError:
        return None
    return best[1] if best else None


def read_mission_changes(outputs_root: Path, mission_id: str) -> dict[str, Any]:
    """The file ledger for one mission across all of its tasks.

    Mirrors :func:`jarvis.missions.result_reader.read_mission_artifacts`: the
    directory is derived from the already-authorized mission id, never from a
    caller-supplied path, and must stay under the outputs root.
    """
    empty: dict[str, Any] = {
        "tasks": [],
        "files": [],
        "additions": 0,
        "deletions": 0,
        "truncated": False,
    }
    clean_id = str(mission_id or "").strip()
    if not _MISSION_ID_RE.fullmatch(clean_id):
        return empty
    root = Path(outputs_root).resolve()
    mission_dir = (root / f"mission_{clean_id[:13]}").resolve()
    try:
        mission_dir.relative_to(root)
    except ValueError:
        return empty
    if not mission_dir.is_dir():
        return empty

    tasks: list[dict[str, Any]] = []
    merged: list[dict[str, Any]] = []
    truncated = False
    try:
        task_dirs = sorted(p for p in (mission_dir / "tasks").iterdir() if p.is_dir())
    except OSError:
        return empty

    for task_dir in task_dirs:
        patch = _final_patch(task_dir / "artifacts")
        if patch is None:
            continue
        try:
            with patch.open("rb") as handle:
                raw = handle.read(_MAX_READ_BYTES + 1)
        except OSError:
            continue
        was_cut = len(raw) > _MAX_READ_BYTES
        summary = summarize_unified_diff(raw[:_MAX_READ_BYTES].decode("utf-8", errors="replace"))
        truncated = truncated or was_cut or bool(summary["truncated_files"])
        tasks.append(
            {
                "task_id": task_dir.name,
                "patch": patch.name,
                "files": summary["files"],
                "additions": summary["additions"],
                "deletions": summary["deletions"],
                "truncated": was_cut or bool(summary["truncated_files"]),
            }
        )
        merged.extend(summary["files"])

    return {
        "tasks": tasks,
        "files": merged,
        "additions": sum(f["additions"] for f in merged),
        "deletions": sum(f["deletions"] for f in merged),
        "truncated": truncated,
    }


__all__ = ["read_mission_changes", "summarize_unified_diff"]
