"""Live end-to-end proof for the spawn-meta worker-prompt fix (2026-06-16).

Reproduces the exact failing case: a RESEARCH sub-agent mission started by an
explicit voice trigger that names the vehicle AND embeds the routing wrapper in
the task ("spawn a sub-agent which will help me find out X"). Before the fix the
worker received "spawn a sub-agent …" as its OWN task, did nothing real, and the
mission died critic_loop_exhausted. After the fix the routing wrapper is stripped
and the worker does the research; the informational/prose-answer net approves it.

Sends the request through the real user path (WS chat -> brain force-spawn ->
worker -> critic) on the running app, then follows the NEW mission in
data/missions.db to its terminal state.

Pass: the new mission reaches APPROVED.

    "C:\\Program Files\\Python311\\python.exe" scripts/verify_subagent_research_meta_live.py
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

WS_URL = "ws://127.0.0.1:47821/ws"
DB = Path(__file__).resolve().parent.parent / "data" / "missions.db"
DEADLINE_S = 720.0

MESSAGE = (
    "Spawn a sub-agent which will help me find out what I have to be aware of "
    "when I move to the USA."
)
TERMINAL = {"APPROVED", "FAILED", "CANCELLED"}


def _newest_after(created_after_ms: int) -> tuple[str, str, int] | None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(
            "SELECT id, state, created_ms FROM missions "
            "WHERE created_ms > ? ORDER BY created_ms DESC LIMIT 1",
            (created_after_ms,),
        ).fetchone()
        return (r["id"], r["state"], r["created_ms"]) if r else None
    finally:
        con.close()


def _now_ms() -> int:
    con = sqlite3.connect(DB)
    try:
        r = con.execute("SELECT MAX(created_ms) FROM missions").fetchone()[0]
        return int(r or 0)
    finally:
        con.close()


async def main() -> int:
    import websockets

    baseline = _now_ms()
    ack = ""
    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.send(
            json.dumps({"type": "message", "kind": "text", "content": MESSAGE})
        )
        print(f"[..] research mission request sent; baseline created_ms={baseline}")
        try:
            async with asyncio.timeout(20.0):
                while not ack:
                    msg = json.loads(await ws.recv())
                    if (
                        msg.get("type") == "event"
                        and msg.get("event_name") == "ResponseGenerated"
                    ):
                        ack = (msg.get("payload", {}) or {}).get("text", "")
        except (TimeoutError, asyncio.TimeoutError):
            pass
    if ack:
        print(f"[..] optimistic ACK: {ack[:140]!r}")

    start = time.monotonic()
    mid = None
    last_state = None
    while time.monotonic() - start < DEADLINE_S:
        await asyncio.sleep(5)
        row = _newest_after(baseline)
        if row is None:
            continue
        mid, state, _ = row
        if state != last_state:
            print(f"    mission {mid[:8]} -> {state} (+{int(time.monotonic()-start)}s)")
            last_state = state
        if state in TERMINAL:
            break

    if mid is None:
        print("[FAIL] no mission was dispatched for the research request.")
        return 1
    if last_state == "APPROVED":
        print(f"[OK] research sub-agent mission {mid[:8]} ran successfully -> APPROVED.")
        return 0
    print(f"[FAIL] mission {mid[:8]} ended {last_state}.")
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
