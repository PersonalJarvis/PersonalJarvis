"""Self-driving feature-verification harness for a LIVE Jarvis instance.

Connects to the running app's WebSocket (the same channel the chat UI uses),
injects a user message, and records every bus event the server forwards back.
This drives the real Router-Brain -> Worker-Critic -> Mission pipeline against
the actually-running process, so it verifies live behaviour, not a fresh
in-process copy.

Usage:
    python scripts/feature_verify_probe.py "How are you?" --secs 25

Output: NDJSON of captured frames to stdout, plus a compact summary.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

try:
    import websockets
except Exception as exc:  # noqa: BLE001
    print(f"NO_WEBSOCKETS_LIB: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(3) from exc


def _port() -> int:
    try:
        d = json.loads(Path("data/.jarvis-running").read_text(encoding="utf-8"))
        return int(d.get("port", 47821))
    except Exception:  # noqa: BLE001
        return 47821


async def run_probe(prompt: str, secs: float, idle_stop: float) -> list[dict]:
    url = f"ws://127.0.0.1:{_port()}/ws"
    frames: list[dict] = []
    async with websockets.connect(url, max_size=None, open_timeout=10) as ws:
        # 1) welcome frame
        welcome_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        frames.append({"_dir": "recv", **_safe_json(welcome_raw)})
        # 1b) wait until the app is QUIET (no in-flight turn) before sending,
        #     so we don't capture a previous turn's tail (one-turn-lag bug).
        quiet_deadline = time.monotonic() + 30.0
        while time.monotonic() < quiet_deadline:
            try:
                await asyncio.wait_for(ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                break  # 2s of silence -> quiescent
        # 2) inject the user message
        await ws.send(json.dumps({"type": "message", "content": prompt}))
        # 3) collect forwarded events with turn-end detection
        deadline = time.monotonic() + secs
        last_evt = time.monotonic()
        seen_active = False   # THINKING/SPEAKING observed
        settle_until = 0.0    # after turn settles, linger briefly for trailing frames
        while time.monotonic() < deadline:
            remaining = min(deadline - time.monotonic(), idle_stop)
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                if time.monotonic() - last_evt >= idle_stop:
                    break
                continue
            last_evt = time.monotonic()
            frame = {"_dir": "recv", **_safe_json(raw)}
            frames.append(frame)
            # turn-end detection via supervisor state machine
            if frame.get("event_name") == "SystemStateChanged":
                ns = str((frame.get("payload") or {}).get("new_state", "")).upper()
                if ns in ("THINKING", "SPEAKING"):
                    seen_active = True
                    settle_until = 0.0
                elif ns in ("IDLE", "LISTENING", "READY") and seen_active:
                    # turn settled: linger 4s for trailing reply/mission frames
                    settle_until = time.monotonic() + 4.0
            if settle_until and time.monotonic() >= settle_until:
                break
    return frames


def _safe_json(raw) -> dict:  # noqa: ANN001
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {"_raw": obj}
    except Exception:  # noqa: BLE001
        return {"_raw": str(raw)[:500]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--secs", type=float, default=25.0, help="max capture window")
    ap.add_argument("--idle", type=float, default=8.0, help="stop after this idle gap")
    args = ap.parse_args()

    frames = asyncio.run(run_probe(args.prompt, args.secs, args.idle))

    # --- extract a per-run verdict -------------------------------------
    reply = ""
    reply_lang = ""
    tier = provider = model = ""
    events: list[str] = []
    actions: list[str] = []
    # Anchor on the LAST BrainTurnStarted so a stray prior-turn ResponseGenerated
    # captured before our turn began cannot be mistaken for our reply.
    last_bts = -1
    for i, f in enumerate(frames):
        if f.get("event_name") == "BrainTurnStarted":
            last_bts = i
    for i, f in enumerate(frames):
        en = f.get("event_name")
        if not en:
            continue
        events.append(en)
        p = f.get("payload") or {}
        if en == "ResponseGenerated" and p.get("text") and i >= last_bts:
            reply = str(p.get("text"))
            reply_lang = str(p.get("language") or "")
        elif en == "BrainTurnStarted":
            tier = str(p.get("intent_level") or "")
            provider = str(p.get("provider") or "")
            model = str(p.get("model") or "")
        elif en in ("ActionProposed", "ActionExecuted"):
            tn = str(p.get("tool") or p.get("action") or p.get("name") or "")
            if tn:
                actions.append(f"{en}:{tn}")
        elif en in ("MissionDispatched", "MissionApproved", "MissionFailed",
                     "AnnouncementRequested", "SkillInvoked"):
            actions.append(en)

    print("VERDICT " + json.dumps({
        "prompt": args.prompt,
        "reply": reply,
        "reply_lang": reply_lang,
        "tier": tier,
        "provider": provider,
        "model": model,
        "actions": actions,
        "event_names": sorted(set(events)),
    }, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
