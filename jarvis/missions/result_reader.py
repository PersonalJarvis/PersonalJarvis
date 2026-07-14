"""Bounded, sandboxed reader for completed Jarvis-Agent deliverables."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

from jarvis.missions.kontrollierer.deliverable_paths import (
    is_nondeliverable_scratch,
)

_MISSION_ID_RE = re.compile(r"^[0-9a-f-]{6,64}$", re.IGNORECASE)
_MAX_FILES: Final[int] = 16
_MAX_FILE_CHARS: Final[int] = 12_000
_MAX_TOTAL_CHARS: Final[int] = 24_000
_MAX_READ_BYTES: Final[int] = 96_000
_TEXT_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".adoc",
        ".cfg",
        ".css",
        ".csv",
        ".diff",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".jsonl",
        ".jsx",
        ".log",
        ".markdown",
        ".md",
        ".org",
        ".patch",
        ".ps1",
        ".py",
        ".rst",
        ".sh",
        ".text",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)


def _deliverable_relative_path(parts: tuple[str, ...]) -> str | None:
    """Return the user-deliverable-relative path for an archived task file."""
    if len(parts) < 5 or not (
        parts[0] == "tasks"
        and parts[2] == "artifacts"
        and parts[3] == "files"
    ):
        return None
    deliverable = "/".join(parts[4:])
    if not deliverable or is_nondeliverable_scratch(deliverable):
        return None
    return deliverable


def read_mission_artifacts(
    outputs_root: Path,
    mission_id: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Read safe textual deliverables for one mission within strict bounds.

    The directory is derived from the already-authorized mission id rather than
    from a caller-supplied URI. Every resolved file must remain under the
    configured outputs root and the canonical
    ``tasks/*/artifacts/files`` subtree. Binary files are listed but not inlined.
    """
    clean_id = str(mission_id or "").strip()
    if not _MISSION_ID_RE.fullmatch(clean_id):
        return ([], False)

    root = Path(outputs_root).resolve()
    mission_dir = (root / f"mission_{clean_id[:13]}").resolve()
    try:
        mission_dir.relative_to(root)
    except ValueError:
        return ([], False)
    if not mission_dir.is_dir():
        return ([], False)

    artifacts: list[dict[str, Any]] = []
    total_chars = 0
    truncated = False
    try:
        candidates = sorted(
            mission_dir.glob("tasks/*/artifacts/files/**/*"),
            key=lambda path: path.as_posix().lower(),
        )
    except OSError:
        return ([], False)

    for candidate in candidates:
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        try:
            parts = resolved.relative_to(mission_dir).parts
        except ValueError:
            continue
        deliverable_path = _deliverable_relative_path(parts)
        if deliverable_path is None:
            continue
        if len(artifacts) >= _MAX_FILES:
            truncated = True
            break

        try:
            size = resolved.stat().st_size
        except OSError:
            continue
        is_text = resolved.suffix.lower() in _TEXT_SUFFIXES or not resolved.suffix
        entry: dict[str, Any] = {
            "path": "/".join(parts),
            "deliverable_path": deliverable_path,
            "size": size,
            "is_text": is_text,
            "content": None,
            "truncated": False,
        }
        if is_text and total_chars < _MAX_TOTAL_CHARS:
            try:
                with resolved.open("rb") as handle:
                    raw = handle.read(_MAX_READ_BYTES + 1)
            except OSError:
                raw = b""
            text = raw[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
            remaining = _MAX_TOTAL_CHARS - total_chars
            limit = min(_MAX_FILE_CHARS, remaining)
            entry["content"] = text[:limit]
            entry["truncated"] = len(raw) > _MAX_READ_BYTES or len(text) > limit
            total_chars += len(entry["content"])
            truncated = truncated or bool(entry["truncated"])
        elif is_text:
            entry["truncated"] = True
            truncated = True
        artifacts.append(entry)

    return (artifacts, truncated)


__all__ = ["read_mission_artifacts"]
