"""Live end-to-end proof for the drag-drop recap doom-loop fix (2026-06-16).

Drives the RUNNING app over its WebSocket exactly as the JarvisDock does when a
mission/output card is dropped onto it: sends a ``mission.inject`` command whose
dropped-card text carries a spawn trigger ("sub-agent") + an action verb
("writes"). BEFORE the fix this force-spawned a NEW mission whose only
deliverable was a conversational recap (no file) -> empty diff ->
critic_loop_exhausted -> FAILED (the doom-loop: every failed mission the user
dragged in to discuss spawned another failed mission).

AFTER the fix the router exempts ``ui.web.ws.mission_inject`` from force-spawn,
so the recap is answered INLINE and NO new mission is dispatched.

Pass criteria (both):
  1. NO new row appears in data/missions.db within the observation window.
  2. A ResponseGenerated (the inline recap) is received over the WS.

    "C:\\Program Files\\Python311\\python.exe" scripts/verify_recap_inject_live.py
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

WS_URL = "ws://127.0.0.1:47821/ws"
DB = Path(__file__).resolve().parent.parent / "data" / "missions.db"
OBSERVE_S = 30.0

PAYLOAD = {
    "utterance": "spawn a sub-agent that writes a 200-word origin story for a "
    "lighthouse keeper named Bo",
    "status": "error",
    "error": "critic_loop_exhausted",
}


def _mission_count() -> int:
    con = sqlite3.connect(DB)
    try:
        return con.execute("SELECT COUNT(*) FROM missions").fetchone()[0]
    finally:
        con.close()


async def main() -> int:
    import websockets

    before = _mission_count()
    print(f"[..] missions before: {before}")

    dispatched: list[str] = []
    response_text = ""
    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.send(
            json.dumps(
                {"type": "command", "action": "mission.inject", "payload": PAYLOAD}
            )
        )
        print("[..] mission.inject sent; observing events…")
        try:
            async with asyncio.timeout(OBSERVE_S):
                while True:
                    raw = await ws.recv()
                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if msg.get("type") != "event":
                        continue
                    name = msg.get("event_name", "")
                    if name in ("MissionDispatched", "MissionStateChanged",
                                "MissionPlanReady", "WorkerSpawned"):
                        dispatched.append(name)
                        print(f"    !! mission event: {name}")
                    elif name == "ResponseGenerated":
                        txt = (msg.get("payload", {}) or {}).get("text", "")
                        if txt and not response_text:
                            response_text = txt
                            print(f"    inline reply ({len(txt)} chars): {txt[:160]!r}")
        except (TimeoutError, asyncio.TimeoutError):
            pass

    after = _mission_count()
    print(f"[..] missions after: {after} (delta={after - before})")

    no_spawn = after == before and not dispatched
    answered = bool(response_text.strip())
    if no_spawn and answered:
        print("[OK] recap answered INLINE; NO new mission dispatched — doom-loop fixed.")
        return 0
    if not no_spawn:
        print("[FAIL] a new mission was dispatched/changed — recap still force-spawns.")
    if not answered:
        print("[WARN] no inline ResponseGenerated observed within the window.")
    return 1


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        code = 2
    sys.stdout.flush()
    import os

    os._exit(code)
