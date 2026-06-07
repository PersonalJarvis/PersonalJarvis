"""Sidecar store for per-skill UI preferences: on/off overrides + list order.

Why a separate JSON file instead of the ``SKILL.md`` frontmatter:

- **Order spans skills** — a user's custom list order has no home in a single
  skill's frontmatter; it is inherently a cross-skill list.
- **Builtins** are re-copied on first run by ``bootstrap.py`` and editing a
  builtin's ``SKILL.md`` also needs the admin password; an overlay file sidesteps
  both — on/off survives the re-copy and needs no password.
- **Persistence** — the old in-memory ``_flip_state`` was wiped by every registry
  hot-reload, so an enabled skill silently reverted on restart. A sidecar that is
  re-applied on every reload makes on/off actually stick.

Storage mirrors ``socials_routes.py``: one atomic JSON file
(``tempfile`` + ``os.replace`` + ``fsync``, guarded by a ``threading.Lock``) under
``user_data_dir()/data/skill_prefs.json``. Cloud-first: no native dependency,
works headless.

This module only *records* the user's choice. It NEVER decides whether a skill
may run — the registry applies these overrides and enforces the AP-15 guard
(a ``DRAFT`` skill is never forced on).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.paths import skill_prefs_path

log = logging.getLogger(__name__)

# The two legal on/off override values. Anything else in the file is ignored on
# load — the store stays forward/backward tolerant of a corrupt or hand-edited
# file rather than raising.
STATE_ON = "active"
STATE_OFF = "disabled"
_LEGAL_STATES = frozenset({STATE_ON, STATE_OFF})

# Serializes the read-modify-write of skill_prefs.json. Callers are sync (FastAPI
# threadpool / registry reload), so a threading.Lock is the right primitive.
_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class SkillPrefs:
    """Parsed preferences: a custom display ``order`` and on/off ``state`` overrides."""

    order: list[str] = field(default_factory=list)
    state: dict[str, str] = field(default_factory=dict)


# ----------------------------------------------------------------------
# Atomic storage (mirrors socials_routes.py)
# ----------------------------------------------------------------------


def _read_raw() -> dict[str, Any]:
    path = skill_prefs_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — corrupt file degrades to empty
        log.warning("skill_prefs: could not parse %s — %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write_raw(order: list[str], state: dict[str, str]) -> None:
    """Atomic write: tempfile in the parent dir → ``os.replace``."""
    path = skill_prefs_path()
    payload = json.dumps(
        {"version": 1, "order": order, "state": state},
        ensure_ascii=False,
        indent=2,
    )
    dir_ = path.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".skill_prefs.", suffix=".json", dir=str(dir_))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ----------------------------------------------------------------------
# Load
# ----------------------------------------------------------------------


def load_prefs() -> SkillPrefs:
    """Load the full preferences, sanitised. Missing/corrupt file → empty prefs."""
    raw = _read_raw()
    order_raw = raw.get("order")
    order = [s for s in order_raw if isinstance(s, str)] if isinstance(order_raw, list) else []
    state_raw = raw.get("state")
    state: dict[str, str] = {}
    if isinstance(state_raw, dict):
        for name, value in state_raw.items():
            if isinstance(name, str) and value in _LEGAL_STATES:
                state[name] = value
    return SkillPrefs(order=order, state=state)


def load_state_overrides() -> dict[str, str]:
    """Convenience for the registry: ``{skill_name: "active" | "disabled"}``."""
    return load_prefs().state


def load_order() -> list[str]:
    """The user's custom skill order (skill names). Empty when never set."""
    return load_prefs().order


# ----------------------------------------------------------------------
# Mutate (read-modify-write under the lock)
# ----------------------------------------------------------------------


def set_state(name: str, on: bool) -> None:
    """Record the user's on/off choice for ``name`` (persists across restarts)."""
    with _LOCK:
        prefs = load_prefs()
        state = dict(prefs.state)
        state[name] = STATE_ON if on else STATE_OFF
        _write_raw(prefs.order, state)


def set_order(names: list[str]) -> None:
    """Persist the user's custom list order (skill names, in display order)."""
    clean = [s for s in names if isinstance(s, str)]
    with _LOCK:
        prefs = load_prefs()
        _write_raw(clean, prefs.state)


def remove_skill(name: str) -> None:
    """Prune a skill from BOTH the order and the state overrides (on delete)."""
    with _LOCK:
        prefs = load_prefs()
        order = [s for s in prefs.order if s != name]
        state = {k: v for k, v in prefs.state.items() if k != name}
        _write_raw(order, state)


__all__ = [
    "STATE_ON",
    "STATE_OFF",
    "SkillPrefs",
    "load_prefs",
    "load_state_overrides",
    "load_order",
    "set_state",
    "set_order",
    "remove_skill",
]
