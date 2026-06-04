"""Live verification: publish a synthetic VoiceMuteToggleRequested
against the running Jarvis backend and check the log for the
authoritative state-change log line.

Runs out-of-process via the admin REST API (no in-process bus access
needed). We POST to a simple endpoint that re-publishes on the local
bus. If no such endpoint exists yet, we drop to a direct WS bridge.

For now: just verify the log line emerges when the user performs the
gesture themselves — print the live tail of jarvis_desktop.log and
exit. The user double-clicks the orb twice and we see the line.
"""

from __future__ import annotations

import time
from pathlib import Path

LOG = Path("data/jarvis_desktop.log")


def tail_for(needle: str, timeout_s: float = 30.0, since_ts: float | None = None) -> str | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            text = LOG.read_text(encoding="utf-8", errors="replace")
        except OSError:
            time.sleep(0.5)
            continue
        for line in reversed(text.splitlines()):
            if needle in line:
                if since_ts is None:
                    return line
                # Lines start with timestamp "YYYY-MM-DD HH:MM:SS.mmm".
                ts_str = line[:23]
                try:
                    ts = time.mktime(time.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S"))
                except ValueError:
                    continue
                if ts >= since_ts:
                    return line
        time.sleep(1.0)
    return None


def main() -> int:
    print("orb-mute verifier — waiting for user to double-double-click the orb …")
    start = time.time()
    line = tail_for("🔇 Voice mute", timeout_s=60.0, since_ts=start)
    if line is None:
        print("TIMEOUT — no '🔇 Voice mute' line in the last 60 s.")
        print("Hint: double-click the orb TWICE (two double-clicks within ~450 ms).")
        return 1
    print(f"FOUND: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
