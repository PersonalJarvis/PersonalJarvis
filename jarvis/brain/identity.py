"""Who the assistant is, said the same way on every surface.

A live call answered "I'm Personal Jarvis" although the user's assistant is
called George (2026-10-02): the typed brain stated the wake-word name, but
the realtime and GPT-Live instructions hardcoded the product name and never
read SOUL.md. This module is the one place the identity block is built:

* the **name directive** — the name comes from the wake word
  (``assistant_name.resolve_assistant_name``) and "Personal Jarvis" is
  stated as the app's name, never the assistant's;
* the **character** — ``data/workspace/SOUL.md`` rendered by
  :class:`jarvis.memory.soul.Soul`, including what the learning loop wrote
  into it.

``identity_block`` runs on the voice path, so it never writes and never
waits: the rendered SOUL.md is cached and only re-read when the file's
modification time moves (one ``stat`` per call at most every two seconds).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Final

from jarvis.brain.assistant_name import DEFAULT_ASSISTANT_NAME, resolve_assistant_name

log = logging.getLogger(__name__)

PRODUCT_NAME: Final[str] = "Personal Jarvis"
#: Tells a model that holds the ``update_soul`` tool to keep SOUL.md current.
SOUL_UPDATE_DIRECTIVE: Final[str] = (
    "Keep your character file current yourself: when the user tells you how you should be "
    "or present yourself, or corrects it, save that right away with the update_soul tool "
    "as one short third-person note (replace a note on the same subject instead of adding "
    "a near-duplicate). Facts about the user do not belong there."
)
#: Minimum seconds between two mtime checks of SOUL.md from a prompt path.
_STAT_INTERVAL_S: Final[float] = 2.0

_cache_lock = threading.Lock()
#: ``(path, compact) -> (mtime, checked_at, rendered)``
_cache: dict[tuple[str, bool], tuple[float, float, str]] = {}


def name_directive(name: str) -> str:
    """The identity sentence every surface puts first."""
    name = " ".join((name or "").split())
    if not name or name == DEFAULT_ASSISTANT_NAME:
        return (
            f"You are the user's personal assistant inside the {PRODUCT_NAME} app. You have "
            "no personal name yet (the user gives you one by choosing a wake word), so "
            f"introduce yourself as their assistant in {PRODUCT_NAME}, never as a model or "
            "provider."
        )
    others = "" if name.casefold() == "jarvis" else ", as Jarvis"
    return (
        f"YOUR NAME IS {name.upper()}. You are {name}, the user's personal assistant. "
        f"{PRODUCT_NAME} is the name of the app you run inside, not your name. When asked "
        f"who or what you are, answer as {name} (for example: \"I'm {name}, your assistant "
        f"in {PRODUCT_NAME}\") and sign as {name}; never introduce yourself as "
        f"{PRODUCT_NAME}{others} or by a model or provider name."
    )


def soul_path() -> Path:
    """The live SOUL.md (``<DATA_DIR>/workspace/SOUL.md``), resolved at call time."""
    from jarvis.core import config as core_config
    from jarvis.memory.workspace import SOUL_MD

    return core_config.DATA_DIR / "workspace" / SOUL_MD


def character_block(*, path: Path | None = None, compact: bool = False) -> str:
    """SOUL.md rendered for a prompt; ``""`` when missing or unreadable. Cached."""
    target = Path(path) if path is not None else soul_path()
    key = (str(target), compact)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None and now - cached[1] < _STAT_INTERVAL_S:
        return cached[2]
    try:
        mtime = target.stat().st_mtime
    except OSError:  # no SOUL.md yet means no identity block
        return ""
    if cached is not None and cached[0] == mtime:
        with _cache_lock:
            _cache[key] = (mtime, now, cached[2])
        return cached[2]
    try:
        from jarvis.memory.soul import Soul

        text = Soul.parse(target, target.read_text(encoding="utf-8")).render_for_prompt(
            compact=compact
        )
    except Exception:  # noqa: BLE001 — a broken SOUL.md must never break a prompt build
        log.warning("SOUL.md could not be rendered; serving the last good text", exc_info=True)
        return cached[2] if cached is not None else ""
    with _cache_lock:
        _cache[key] = (mtime, now, text)
    return text


def identity_block(
    config: Any, *, compact: bool = False, path: Path | None = None, maintain: bool = False
) -> str:
    """Name directive plus character, for the top of any system prompt.

    ``maintain`` adds the instruction to keep SOUL.md current with the
    ``update_soul`` tool; only a model that actually holds that tool (the
    live voice tool set) gets it.
    """
    try:
        name = resolve_assistant_name(config)
    except Exception:  # noqa: BLE001 — the name falls back, the prompt still builds
        log.debug("assistant name unavailable", exc_info=True)
        name = DEFAULT_ASSISTANT_NAME
    parts = [
        name_directive(name),
        character_block(path=path, compact=compact),
        SOUL_UPDATE_DIRECTIVE if maintain else "",
    ]
    return "\n\n".join(part for part in parts if part)


def invalidate_cache() -> None:
    """Forget rendered SOUL.md text (tests; writers rely on the mtime instead)."""
    with _cache_lock:
        _cache.clear()
