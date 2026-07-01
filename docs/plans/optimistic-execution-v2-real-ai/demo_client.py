#!/usr/bin/env python3
"""Live test client for the Phase-2 server — real HTTP, real LLM over SSE.

Start the server first (in another terminal), e.g.:
    python -m optimistic.server

Then run this client:
    python demo_client.py "Erklaer Optimistic Execution in einem Satz."
    python demo_client.py --oops   # the missing-info self-correction scenario

It opens the SSE stream, POSTs the utterance, prints the INSTANT ack, then prints
the real LLM answer as it arrives asynchronously over SSE. With --oops it also
drives the VAD endpoints to surface the organic correction at the turn boundary.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid

import httpx

DEFAULT_BASE = "http://127.0.0.1:8008"


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: S110 - best-effort console UTF-8
            pass

    parser = argparse.ArgumentParser(description="Live client for Optimistic Execution v2")
    parser.add_argument("prompt", nargs="?", default="Erklaere Optimistic Execution in genau einem Satz.")
    parser.add_argument("--oops", action="store_true", help="run the missing-info correction scenario")
    parser.add_argument("--base", default=DEFAULT_BASE)
    args = parser.parse_args()

    session_id = "demo-" + uuid.uuid4().hex[:6]
    prompt = "Schreib Max eine Mail, dass sich das Projekt verschiebt" if args.oops else args.prompt  # i18n-allow: test content — user voice utterance DE

    async with httpx.AsyncClient(base_url=args.base, timeout=180.0) as client:
        health = (await client.get("/api/health")).json()
        print("=" * 70)
        print(f"[server] backend={health['backend']}  model={health['model']}  base_url={health['base_url']}")
        print("=" * 70)

        answer_seen = asyncio.Event()

        async def read_stream() -> None:
            async with client.stream("GET", "/api/stream", params={"session_id": session_id}) as resp:
                event = None
                async for raw in resp.aiter_lines():
                    line = raw.rstrip("\r")
                    if line.startswith("event:"):
                        event = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        data = line[len("data:"):].strip()
                        try:
                            payload = json.loads(data)
                        except Exception:
                            payload = {"text": data}
                        if event == "ack":
                            print(f"  [sse:ack]        {payload.get('text')}")
                        elif event == "worker_started":
                            print(f"  [sse:worker]     started mission {payload.get('mission_id')}")
                        elif event == "answer":
                            print(f"  [sse:answer]     {payload.get('text')}")
                            answer_seen.set()
                            return
                        elif event == "correction":
                            print(f"  [sse:correction] {payload.get('text')}")
                        event = None

        reader = asyncio.create_task(read_stream())
        await asyncio.sleep(0.2)  # let the stream subscribe

        if args.oops:
            await client.post("/api/vad/speech_started", json={"session_id": session_id})

        t0 = time.perf_counter()
        r = await client.post("/api/utterance", json={"text": prompt, "session_id": session_id})
        dt_ms = (time.perf_counter() - t0) * 1000.0
        print(f"\n[you]    {prompt}")
        print(f"[jarvis] (instant HTTP ack, {dt_ms:.1f} ms)  {r.json()['ack']}\n")

        if args.oops:
            await asyncio.sleep(1.5)  # let the worker discover the missing info
            resp = (await client.post("/api/vad/speech_ended", json={"session_id": session_id})).json()
            for c in resp.get("corrections", []):
                print(f"[jarvis] (organic correction @ turn-boundary)  {c}")
            reader.cancel()
        else:
            try:
                await asyncio.wait_for(answer_seen.wait(), timeout=180.0)
            except TimeoutError:
                print("  (timed out waiting for the answer)")
            reader.cancel()

        try:
            await reader
        except (asyncio.CancelledError, Exception):
            pass

        print("\n" + "=" * 70)
        print(" Instant ack came back over HTTP; the real LLM answer arrived async over SSE.")
        print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
