#!/usr/bin/env python3
"""Observable live verification: instant HTTP ack + real LLM answer over SSE."""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8008"


async def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    session = "verify-" + uuid.uuid4().hex[:6]
    prompt = "Erstelle eine kurze, gut verstaendliche Erklaerung von Optimistic Execution."  # i18n-allow

    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as client:
        h = (await client.get("/api/health")).json()
        print(f"[health] {h}", flush=True)

        answer_box: dict = {}
        answer_seen = asyncio.Event()

        async def reader() -> None:
            async with client.stream("GET", "/api/stream", params={"session_id": session}) as r:
                event = None
                async for raw in r.aiter_lines():
                    line = raw.rstrip("\r")
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        try:
                            payload = json.loads(line[5:].strip())
                        except Exception:
                            payload = {"text": line[5:].strip()}
                        print(f"[sse:{event}] {payload}", flush=True)
                        if event == "answer":
                            answer_box["text"] = payload.get("text", "")
                            answer_seen.set()
                            return
                        event = None

        rtask = asyncio.create_task(reader())
        await asyncio.sleep(0.3)

        t0 = time.perf_counter()
        r = await client.post("/api/utterance", json={"text": prompt, "session_id": session})
        dt = (time.perf_counter() - t0) * 1000.0
        print(f"[POST] status={r.status_code} ack_latency={dt:.1f}ms ack={r.json()['ack']!r}", flush=True)

        try:
            await asyncio.wait_for(answer_seen.wait(), timeout=45.0)
            print(f"[RESULT] PASS — real LLM answer over SSE: {answer_box['text']!r}", flush=True)
            rtask.cancel()
            return 0
        except TimeoutError:
            print("[RESULT] FAIL — no answer over SSE within 45s", flush=True)
            rtask.cancel()
            return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
