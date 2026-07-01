"""Central app directory convention.

Personal Jarvis writes user data (skills, memory, logs, session file) to a
single canonical location — analogous to OpenClaw's ``~/.claude/``. On Windows
this is ``%LOCALAPPDATA%\\Jarvis``; when the ENV variable is absent (portable
scripts, tests) we fall back to ``~/.jarvis/``.

This module replaces the scattered ``Path.home() / ".jarvis"`` literals and the
local ``_app_data_dir()`` in ``jarvis.ui.shell.single_instance``. All consumers
go through the getters defined here.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def user_data_dir() -> Path:
    """App data directory in the user context.

    - Windows: ``%LOCALAPPDATA%\\Jarvis`` (standard convention for local app data).
    - Fallback: ``~/.jarvis`` — relevant when ``LOCALAPPDATA`` is not set
      (e.g. in some CI environments, Unix dev setups, test runs).

    Does NOT create the directory — use ``ensure_user_dirs()`` for that.
    """
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "Jarvis"
    return Path.home() / ".jarvis"


def user_skills_dir() -> Path:
    """Directory for user skills (also contains copied built-ins after the first run)."""
    return user_data_dir() / "skills"


def user_memory_dir() -> Path:
    """SQLite recall store and ChromaDB archival data land here."""
    return user_data_dir() / "memory"


def user_logs_dir() -> Path:
    """Flight recorder JSONL and other log files."""
    return user_data_dir() / "logs"


def user_clis_dir() -> Path:
    """Directory for user-defined custom CLI specs and CLI usage artefacts."""
    return user_data_dir() / "clis"


def cli_usage_db_path() -> Path:
    """SQLite database containing CLI invocation metadata (CLI integration)."""
    return user_data_dir() / "cli_usage.db"


def cli_custom_catalog_path() -> Path:
    """JSON file containing user-registered custom CLI specs."""
    return user_clis_dir() / "custom.json"


def skill_link_health_db_path() -> Path:
    """SQLite database caching reachability status for skill URLs (homepage/source/docs)."""
    return user_data_dir() / "data" / "skill_link_health.sqlite"


def docs_index_db_path() -> Path:
    """SQLite FTS5 index for the documentation section (Phase Docs-Tier-1)."""
    return user_data_dir() / "data" / "docs_index.sqlite"


def skill_prefs_path() -> Path:
    """JSON sidecar storing per-skill on/off overrides and the custom list order.

    Kept OUTSIDE ``user_skills_dir()`` (which the registry watcher observes for
    ``*.md`` changes) so a preference write never triggers a skill hot-reload.
    """
    return user_data_dir() / "data" / "skill_prefs.json"


def repo_root() -> Path:
    """Path to the repository root (local Personal Jarvis working tree).

    Derived from the location of this file: ``jarvis/core/paths.py`` ->
    ``parents[2]``. With an editable install (``pip install -e .``) this
    points to the repo working tree, not to the site-packages mirror — which
    is intentional for doc discovery.
    """
    return Path(__file__).resolve().parents[2]


def default_doc_roots() -> list[Path]:
    """Default discovery roots for the documentation registry.

    ``docs/`` is the canonical documentation tree. The ``.exists()`` filter is
    kept as a defensive guard so a missing root never breaks the registry, and
    so additional roots can be appended later (optionally overridable via a
    ``[docs]`` section in ``jarvis.toml``).
    """
    root = repo_root()
    candidates = [
        root / "docs",
    ]
    return [p for p in candidates if p.exists()]


def board_db_path() -> Path:
    """SQLite database with aggregated board stats (Personal Mastery Dashboard).

    Phase A reads flight recorder JSONL from ``user_logs_dir()`` and writes
    aggregated safe fields here. The database is strictly local — no sync, no
    cloud upload.
    """
    return user_data_dir() / "data" / "board" / "personal.db"


def user_outputs_dir() -> Path:
    """Canonical output directory for sub-agent sessions."""
    d = user_data_dir() / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def desktop_mirror_dir() -> Path:
    """Desktop junction root for direct access to sub-agent outputs."""
    return Path.home() / "Desktop" / "Jarvis-Output"


def ensure_session_output_dir(slug: str) -> tuple[Path, Path]:
    """Create the canonical session directory and (on Windows) a Desktop junction.

    Returns:
        (canonical, mirror) — on Windows ``mirror`` is a directory junction into
        ``canonical`` for one-click Desktop access. On macOS/Linux (including a
        headless server with no Desktop) the junction is skipped and ``mirror``
        equals ``canonical``, so callers always receive a usable path and no
        stray ``~/Desktop/Jarvis-Output`` folder is created.
    """
    import subprocess

    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    canonical = user_outputs_dir() / slug
    canonical.mkdir(parents=True, exist_ok=True)

    # The Desktop mirror is a Windows directory junction (``mklink /J``) — a
    # convenience, never a core path. Skip it entirely off Windows so a headless
    # VPS or a Mac never grows an empty ``~/Desktop/Jarvis-Output`` directory.
    if sys.platform != "win32":
        return canonical, canonical

    mirror_root = desktop_mirror_dir()
    mirror_root.mkdir(parents=True, exist_ok=True)
    mirror = mirror_root / slug
    if not mirror.exists():
        try:
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(mirror), str(canonical)],
                check=False,
                capture_output=True,
                timeout=5.0,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except Exception:  # noqa: BLE001
            pass  # Mirror is nice-to-have, not a fatal failure
    return canonical, mirror


def ensure_user_dirs() -> Path:
    """Create all user directories. Idempotent — safe to call on every start.

    Returns the root directory (``user_data_dir()``) so callers can use it
    immediately.
    """
    root = user_data_dir()
    for p in (root, user_skills_dir(), user_memory_dir(), user_logs_dir(), user_clis_dir()):
        p.mkdir(parents=True, exist_ok=True)
    return root


__all__ = [
    "user_data_dir",
    "user_skills_dir",
    "user_memory_dir",
    "user_logs_dir",
    "user_clis_dir",
    "cli_usage_db_path",
    "cli_custom_catalog_path",
    "skill_link_health_db_path",
    "skill_prefs_path",
    "docs_index_db_path",
    "repo_root",
    "default_doc_roots",
    "board_db_path",
    "ensure_user_dirs",
    "user_outputs_dir",
    "desktop_mirror_dir",
    "ensure_session_output_dir",
]
