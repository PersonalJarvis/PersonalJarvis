"""The person's per-action policy: allow, ask, block — or the action's default.

Stored as ``DATA_DIR/state/app_action_policy.json`` (``{action_id: mode}``),
written atomically; not ``jarvis.toml``, because it is per-user runtime state
edited from the Jarvis-actions page, and a missing or broken file simply means
"every action uses its default".
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Final, Literal, get_args

from .catalog import ActionEntry, default_tier

log = logging.getLogger(__name__)

__all__ = ["Mode", "effective_tier", "load_policy", "policy_path", "set_mode"]

Mode = Literal["allow", "ask", "block"]
MODES: Final[tuple[str, ...]] = get_args(Mode)
_FILE_NAME: Final[str] = "app_action_policy.json"
_lock = threading.Lock()


def policy_path() -> Path:
    from jarvis.core import config as cfg_mod  # lazy: tests monkeypatch DATA_DIR

    return Path(cfg_mod.DATA_DIR) / "state" / _FILE_NAME


def load_policy() -> dict[str, str]:
    path = policy_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # No policy file yet means every action keeps its default.
        return {}
    except (OSError, ValueError):
        log.warning("app-actions: unreadable policy %s — using defaults", path, exc_info=True)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v in MODES}


def set_mode(action_id: str, mode: str | None) -> dict[str, str]:
    """Set (or with ``None`` clear) one action's mode; returns the whole policy."""
    if mode is not None and mode not in MODES:
        raise ValueError(f"mode must be one of {MODES} or null")
    with _lock:
        policy = load_policy()
        if mode is None:
            policy.pop(action_id, None)
        else:
            policy[action_id] = mode
        path = policy_path()
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            temporary.write_text(json.dumps(policy, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return policy


def effective_tier(entry: ActionEntry, policy: dict[str, str] | None = None) -> str:
    """The risk tier the executor applies: the person's mode wins over the default.

    ``allow`` runs without a question but is still logged (``monitor``).
    """
    mode = (load_policy() if policy is None else policy).get(entry.id)
    if mode == "allow":
        return "monitor"
    if mode == "ask":
        return "ask"
    if mode == "block":
        return "block"
    return default_tier(entry)
