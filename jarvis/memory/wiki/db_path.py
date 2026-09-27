"""Canonical SQLite path resolution for every wiki consumer."""

from __future__ import annotations

from pathlib import Path

from jarvis.core.frozen import is_frozen
from jarvis.core.paths import repo_root, runtime_root


def resolve_wiki_db_path(data_dir: str | Path | None = None) -> Path:
    """Return one absolute ``jarvis.db`` path independent of process CWD."""
    raw = Path(data_dir) if data_dir is not None else Path("data")
    base = runtime_root() if is_frozen() else repo_root()
    directory = raw if raw.is_absolute() else base / raw
    return (directory / "jarvis.db").resolve(strict=False)


__all__ = ["resolve_wiki_db_path"]
