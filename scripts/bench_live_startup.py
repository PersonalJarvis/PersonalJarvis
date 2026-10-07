"""Offline startup benchmark: real TLS preparation and simulated network delays.

Run this script from either checkout to compare that checkout's provider code.
No credentials are read and no network requests or microphones are opened.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path.cwd()))

import httpx  # noqa: E402
import pytest  # noqa: E402
from websockets.asyncio import client as ws_client  # noqa: E402

from jarvis.plugins.realtime import openai_live as wire  # noqa: E402


async def measure(samples: int) -> dict:
    allocations = []
    for _ in range(samples):
        started = time.perf_counter()
        client = httpx.AsyncClient(timeout=25)
        allocations.append((time.perf_counter() - started) * 1000)
        await client.aclose()

    # Fixed network delays make the dependency graph comparable. This is not
    # a measurement of OpenAI latency or of a physical audio device.
    rest_s, attach_s, media_s = 0.25, 0.60, 0.40
    requests = []

    class Http:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            requests.append("create" if not url.endswith("hangup") else "hangup")
            await asyncio.sleep(rest_s)
            return SimpleNamespace(status_code=200, json=lambda: {
                "session": {"id": "offline-test"}, "transport": {"sdp": "offline-answer"},
            })

    async def attach(*args, **kwargs):
        await asyncio.sleep(attach_s)
        return SimpleNamespace()

    elapsed = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(httpx, "AsyncClient", lambda **kwargs: Http())
        patch.setattr(ws_client, "connect", attach)
        for _ in range(samples):
            media = None

            async def ready(sdp):
                nonlocal media
                assert sdp == "offline-answer"
                assert media is None
                media = asyncio.create_task(asyncio.sleep(media_s))

            started = time.perf_counter()
            result = await wire.OpenAILiveProvider(api_key="offline-fake").open_session(
                SimpleNamespace(session={}, offer_sdp="offline-offer", on_transport_ready=ready)
            )
            if media is None:  # The pre-change provider returns SDP only after attach.
                await ready(result.answer_sdp)
            await media
            elapsed.append((time.perf_counter() - started) * 1000)
    return {
        "samples": samples,
        "network_requests": 0,
        "http_client_first_ms": round(allocations[0], 2),
        "http_client_warm_median_ms": round(statistics.median(allocations[1:] or allocations), 2),
        "simulated_delays_ms": {"rest": rest_s * 1000, "attach": attach_s * 1000,
                                "media": media_s * 1000},
        "simulated_transport_usable_median_ms": round(statistics.median(elapsed), 2),
        "simulated_transport_samples_ms": [round(value, 2) for value in elapsed],
        "fake_session_creations": requests.count("create"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.samples <= 30:
        parser.error("samples must be between 1 and 30")
    print(json.dumps(asyncio.run(measure(args.samples)), indent=2))
