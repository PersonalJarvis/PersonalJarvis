"""Canonical SQLite path resolution for every wiki consumer."""
from __future__ import annotations

from pathlib import Path

from jarvis.core.paths import repo_root


def resolve_wiki_db_path(data_dir: str | Path | None = None) -> Path:
    """Return one absolute ``jarvis.db`` path independent of process CWD."""
    raw = Path(data_dir) if data_dir is not None else Path("data")
    directory = raw if raw.is_absolute() else repo_root() / raw
    return (directory / "jarvis.db").resolve(strict=False)


__all__ = ["resolve_wiki_db_path"]
