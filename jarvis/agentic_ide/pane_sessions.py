"""Which conversation each Claude Code pane is on RIGHT NOW (Herdr's rule).

A pane is resumed after a reboot through its conversation id. The id it was
launched with goes stale the moment the user runs ``/clear`` or ``/resume`` in
the pane (or compaction rolls it over), so it is re-read from a Claude Code
``SessionStart`` hook (:mod:`.claude_session_hook`) that fires on every one of
those, whatever its source — the fix Herdr shipped for the same bug
(herdrdev/herdr#4059).

Wiring, all at spawn time and all additive:

* ``--settings <file>`` loads the hook as an EXTRA settings source; the user's
  own settings and hooks stay exactly as they are,
* ``JARVIS_PANE_ID`` / ``JARVIS_PANE_SESSIONS_DIR`` in the pane's environment
  tell the hook whom it is reporting for and where to write.

Only Claude Code has such a hook; other CLIs keep the id Jarvis discovered for
them (``agent_sessions``).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from loguru import logger

from .claude_session_hook import DIR_ENV, PANE_ENV


def _state_dir() -> Path:
    from jarvis.core.instance import current_instance
    from jarvis.core.paths import user_data_dir

    suffix = current_instance().state_file_suffix
    return user_data_dir() / "agentic_ide" / f"pane_sessions{suffix}"


def _interpreter() -> Path:
    """A console interpreter: the hook reads stdin, which pythonw does not have."""
    exe = Path(sys.executable)
    console = exe.with_name("python.exe")
    return console if exe.name.lower() == "pythonw.exe" and console.exists() else exe


def settings_file() -> Path:
    """The extra settings file carrying the hook, written when it changes."""
    script = Path(__file__).with_name("claude_session_hook.py")
    # Forward slashes: Claude Code runs hook commands through bash on some
    # installs and cmd on others; a quoted forward-slash path works in both.
    command = f'"{_interpreter().as_posix()}" "{script.as_posix()}"'
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": command, "timeout": 10}]}]
        }
    }
    target = _state_dir().parent / "claude_session_hook.settings.json"
    text = json.dumps(settings, indent=2)
    try:
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + f".{os.getpid()}.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, target)
    except OSError as exc:
        logger.warning("Agentic IDE: session hook settings not written: {}", exc)
    return target


def launch_wiring(pane_id: str) -> tuple[tuple[str, ...], dict[str, str]]:
    """Extra argv and environment that make a Claude pane report its conversation."""
    return (
        ("--settings", str(settings_file())),
        {PANE_ENV: pane_id, DIR_ENV: str(_state_dir())},
    )


def latest_session(pane_id: str) -> tuple[str, float] | None:
    """The conversation id the pane last started, and when; None if never reported."""
    name = "".join(ch for ch in pane_id if ch.isalnum() or ch in "-_")[:80]
    if not name:
        return None
    try:
        data = json.loads((_state_dir() / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):  # never reported (or a torn write): keep what we have
        return None
    session_id = str(data.get("session_id") or "").strip() if isinstance(data, dict) else ""
    if not session_id:
        return None
    try:
        at = float(data.get("at") or 0.0)
    except (TypeError, ValueError):  # a malformed stamp only costs the ordering hint
        at = 0.0
    return session_id, at


def forget(pane_id: str) -> None:
    """Drop a closed pane's record."""
    name = "".join(ch for ch in pane_id if ch.isalnum() or ch in "-_")[:80]
    if not name:
        return
    try:
        (_state_dir() / f"{name}.json").unlink()
    except FileNotFoundError:  # never reported: nothing to drop
        return
    except OSError as exc:
        logger.debug("Agentic IDE: pane session record not removed: {}", exc)


__all__ = ["forget", "latest_session", "launch_wiring", "settings_file"]
