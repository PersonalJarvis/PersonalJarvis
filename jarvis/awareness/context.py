"""Context — data model and resolver for the Phase A4 Working Set.

A ``Context`` represents a "slot" in the RAM LRU cache of the
``WorkingSet``: project root + task label + last episode ID + timestamp.
Multiple parallel contexts (e.g. VS Code + Slack + browser) are tracked
independently; when a known context is reactivated the snapshot returns
its last episode instead of a generic "most-recent" episode.

Resolver heuristic (Plan §8):
    1. Process in IDE_SET (code.exe, cursor.exe, windsurf.exe)
       → ``project_root`` via ``psutil.Process.cwd()``
    2. Browser process → parse hostname from window title
    3. Terminal process → shell cwd via psutil
    4. Fallback → ``process_name`` as ``project_root``

``task_label`` = first 5 words of the window title (plan definition).

Hard negatives (from spec):
- NO ``psutil`` import at module top-level (CI/Linux flaky) — lazy import
  inside the resolver, with ``try/except`` around every ``psutil`` operation.
- NO persistence — the WorkingSet is RAM-only (the DB holds everything).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.awareness.state import FrameSnapshot

logger = logging.getLogger(__name__)


# ---- Heuristic tables -------------------------------------------------------

# IDEs/editors with a well-defined project root via cwd().
IDE_SET: frozenset[str] = frozenset({
    "code.exe", "cursor.exe", "windsurf.exe",
    "rider64.exe", "pycharm64.exe", "idea64.exe",
    "devenv.exe",    # Visual Studio
    "Code.exe",       # case variation on Windows
})

# Browser processes — we parse the hostname from the window title.
BROWSER_SET: frozenset[str] = frozenset({
    "chrome.exe", "msedge.exe", "firefox.exe",
    "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe",
})

# Terminal shells — cwd gives the working directory.
TERMINAL_SET: frozenset[str] = frozenset({
    "WindowsTerminal.exe", "wezterm-gui.exe", "alacritty.exe",
    "ConEmu64.exe", "cmd.exe", "pwsh.exe", "powershell.exe",
    "bash.exe", "zsh.exe",
})

# Browser window titles typically look like:
#   "Google Search - Mozilla Firefox"
#   "GitHub - PR #123 — Brave"
#   "https://example.com — Chromium"
# We search for a URL/domain in the title (best-effort).
_URL_RE = re.compile(r"https?://([^/\s]+)")
_DOMAIN_RE = re.compile(r"\b([a-z0-9-]+(?:\.[a-z0-9-]+)+)\b", re.IGNORECASE)
_BROWSER_SUFFIX_RE = re.compile(
    r"\s*[-—–|]\s*(Mozilla\s+Firefox|Google\s+Chrome|Microsoft\s+Edge|"
    r"Chromium|Brave|Opera|Vivaldi|Arc)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Context:
    """A "slot" in the Working Set.

    Fields:
        project_root      Derived from cwd / hostname / process_name. Also
                          serves as the identity key for LRU lookup (uniqueness).
        task_label        Human-readable label (max ~5 words from the window title).
        last_episode_id   FK to ``awareness_episodes.id``. None until the first
                          episode in this context has been persisted.
        last_seen_ns      time.time_ns() of the last activity — used by the LRU
                          for eviction.
        process_name      Originating process (debug + heuristic).
    """
    project_root: str
    task_label: str
    last_episode_id: int | None = None
    last_seen_ns: int = field(default_factory=lambda: time.time_ns())
    process_name: str = ""


def resolve_context(frame: FrameSnapshot) -> Context:
    """Heuristic mapping of ``FrameSnapshot`` to ``Context``.

    Heuristic order (Plan §8):
        1. IDE  → ``project_root`` via ``psutil.Process.cwd()``
        2. Browser → hostname from window title
        3. Terminal → shell cwd via psutil
        4. Fallback → ``process_name`` as ``project_root``

    Every ``psutil`` operation is wrapped in ``try/except`` with fallback to
    ``process_name``. Lazy import (CI/Linux may lack psutil or
    ``psutil.cwd()`` may hang on UAC / disconnected drives).
    """
    process = frame.active_process_name
    title = frame.active_window_title
    pid = frame.active_pid

    project_root: str | None = None

    if process in IDE_SET:
        project_root = _safe_cwd(pid)
    elif process in BROWSER_SET:
        project_root = _hostname_from_title(title)
    elif process in TERMINAL_SET:
        project_root = _safe_cwd(pid)

    # Fallback to process_name — ALWAYS a valid identity key, even
    # when all three heuristics fail.
    if not project_root:
        project_root = process or "unknown"

    task_label = _short_label(title)

    return Context(
        project_root=project_root,
        task_label=task_label,
        process_name=process,
    )


# ---- Helpers ---------------------------------------------------------------


def _safe_cwd(pid: int) -> str | None:
    """Look up ``psutil.Process(pid).cwd()`` with fail-silent semantics.

    Lazy import: psutil is optional on Linux/CI. On any failure
    (Permission, NoSuchProcess, AccessDenied, OSError, ImportError):
    returns None — the caller falls back to process_name.
    """
    if pid <= 0:
        return None
    try:
        import psutil  # noqa: PLC0415

        return psutil.Process(pid).cwd()
    except Exception:    # noqa: BLE001
        return None


def _hostname_from_title(title: str) -> str | None:
    """Best-effort hostname parser from a browser window title.

    Expected format examples:
        "GitHub - awesome-repo - Mozilla Firefox"  → None (no URL/domain)
        "https://example.com - Chrome"              → "example.com"
        "Stack Overflow - Brave"                    → None
    """
    if not title:
        return None
    # Strip the browser suffix so we don't match "firefox" as a domain.
    cleaned = _BROWSER_SUFFIX_RE.sub("", title)
    # Try an explicit URL match first.
    m = _URL_RE.search(cleaned)
    if m:
        return m.group(1)
    # Otherwise: explicit domain (with dot) in the title.
    m = _DOMAIN_RE.search(cleaned)
    if m:
        return m.group(1)
    return None


def _short_label(title: str, *, max_words: int = 5) -> str:
    """First ``max_words`` words of the title as a compact label.

    Plan §8: "task_label = first 5 words of the window title minus..."
    Whitespace is stripped and the result is capped at max_words.
    """
    if not title:
        return ""
    words = title.split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words])
