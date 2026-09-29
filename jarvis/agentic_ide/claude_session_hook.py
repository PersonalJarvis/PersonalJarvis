"""Claude Code ``SessionStart`` hook: remember which conversation a pane is on.

Why this exists: a pane is resumed after a reboot through the conversation id
it was launched with (``--session-id``). But that id is not stable for the
life of the pane — ``/clear`` starts a new conversation, ``/resume`` switches
to another, and compaction can roll over to a fresh one. Resuming the launch
id afterwards brings back an OLD conversation, which is exactly the failure
Herdr hit and fixed (herdrdev/herdr#4059) by recording the id from this hook
on EVERY session start, whatever its source.

Claude Code runs this script with the hook payload as JSON on stdin; the pane
it belongs to is named by ``JARVIS_PANE_ID`` in the environment Jarvis gave the
CLI, and ``JARVIS_PANE_SESSIONS_DIR`` says where to write. One small JSON file
per pane, written atomically; the registry reads it before saving and before
resuming (:mod:`jarvis.agentic_ide.session`).

Deliberately stdlib-only and run as a plain script (not ``python -m``), so it
starts in tens of milliseconds and cannot slow the CLI's startup down, and it
never fails loudly: a hook that errors would print into the agent's pane.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PANE_ENV = "JARVIS_PANE_ID"
DIR_ENV = "JARVIS_PANE_SESSIONS_DIR"


def _safe_name(pane_id: str) -> str:
    return "".join(ch for ch in pane_id if ch.isalnum() or ch in "-_")[:80]


def record(pane_id: str, directory: Path, payload: dict) -> Path | None:
    """Write the pane's current conversation id; None when there is nothing to write."""
    session_id = str(payload.get("session_id") or "").strip()
    name = _safe_name(pane_id)
    if not session_id or not name:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{name}.json"
    tmp = directory / f"{name}.{os.getpid()}.tmp"
    tmp.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "source": str(payload.get("source") or ""),
                "transcript_path": str(payload.get("transcript_path") or ""),
                "at": time.time(),
            }
        ),
        encoding="utf-8",
    )
    os.replace(tmp, target)
    return target


def main() -> int:
    pane_id = os.environ.get(PANE_ENV, "")
    directory = os.environ.get(DIR_ENV, "")
    if not pane_id or not directory:
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if isinstance(payload, dict):
            record(pane_id, Path(directory), payload)
    except Exception as exc:  # noqa: BLE001 - a hook must never break the agent's start
        # Quiet on purpose, but not silent: stderr lands in Claude Code's own
        # hook log (``--debug``), never on the pane.
        print(f"jarvis session hook: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
