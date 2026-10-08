"""Offline comparison of allocation/TLS overlap and the actual audio-release frame.

Run from the checkout to measure; the script may live in another checkout.
All provider peers and credentials are fakes. No microphone or network opens.
Delays are controlled experiments, not claims about provider latency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path.cwd()))

import httpx  # noqa: E402
import pytest  # noqa: E402

from jarvis.core.bus import EventBus  # noqa: E402
from jarvis.core.events import RealtimeSessionReady, SystemStateChanged  # noqa: E402
from jarvis.live import session as host  # noqa: E402
from jarvis.live.config import LiveConfig  # noqa: E402
from jarvis.plugins.realtime import _live_transport as transport  # noqa: E402
from tests.fakes.fake_subscription_live_wire import FakeSubscriptionLiveWire  # noqa: E402


async def measure(samples: int) -> dict:
    started = time.perf_counter()
    await transport.websocket_options()
    cold_tls_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    await transport.websocket_options()
    warm_tls_ms = (time.perf_counter() - started) * 1000
    results: dict[str, list[float]] = {"transport_ms": [], "audio_release_ms": []}
    for _ in range(samples):
        wire = FakeSubscriptionLiveWire()

        async def response(request, _wire=wire):
            await asyncio.sleep(0.3)
            return _wire._respond(request)

        def client(**kwargs):
            return httpx.AsyncClient(transport=httpx.MockTransport(response), **kwargs)

        async def options():
            await asyncio.sleep(0.2)
            return {}  # Fake connector: no TLS or network handshake.

        async def connect(*args, _wire=wire, **kwargs):
            await asyncio.sleep(0.35)
            return await _wire.connect(*args, **kwargs)

        provider = wire.provider()
        provider._http_client_factory = client
        provider._websocket_connect = connect
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(transport, "websocket_options", options)
            started = time.perf_counter()
            connection = await provider.open_session(wire.config())
            results["transport_ms"].append((time.perf_counter() - started) * 1000)
            await connection.close()

        bus = EventBus()
        ready_at = 0.0
        wire = FakeSubscriptionLiveWire()
        provider = wire.provider()
        provider.requires_close_ack = False

        async def observe(event):
            if isinstance(event, RealtimeSessionReady) or (
                isinstance(event, SystemStateChanged) and event.new_state == "LISTENING"
            ):
                await asyncio.sleep(0.25)

        async def send(message):
            nonlocal ready_at
            if message.get("type") == "audio_ready":
                ready_at = time.perf_counter()

        async def take_input(self, message):
            return None  # No physical audio device in an offline comparison.

        async def immediate_options():
            return {}

        bus.subscribe(RealtimeSessionReady, observe)
        bus.subscribe(SystemStateChanged, observe)
        with tempfile.TemporaryDirectory(prefix="jarvis-readiness-") as directory:
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(transport, "websocket_options", immediate_options)
                patch.setattr(host, "get_supervisor_tool_gateway", lambda: SimpleNamespace())
                patch.setattr(host, "user_data_dir", lambda: Path(directory))
                patch.setattr(host, "_identity", lambda config: "")
                patch.setattr(host.LiveTools, "declarations", lambda self, **kw: [])
                patch.setattr(host.LiveVoiceSession, "_take_startup_input", take_input)
                patch.setattr(host.LiveVoiceSession, "_adopt_desktop_session", lambda self: None)
                session = host.LiveVoiceSession(
                    session_id="offline-readiness", send_binary=send, send_json=send,
                    providers=[provider], bus=bus,
                    config=SimpleNamespace(
                        brain=SimpleNamespace(reply_language="en"),
                        live=LiveConfig(configured=True, backend_model="test",
                                        model="gpt-live-1-codex", voice="cove"),
                    ),
                )
                started = time.perf_counter()
                try:
                    await session.handle_control({
                        "type": "audio_start", "sample_rate": 48000,
                        "webrtc_offer_sdp": wire.config().offer_sdp,
                    })
                    assert ready_at, "No audio-release frame was sent"
                    results["audio_release_ms"].append((ready_at - started) * 1000)
                finally:
                    await session.end()
    return {
        "samples": samples,
        "network_requests": 0,
        "physical_audio_verified": False,
        "measured_local_tls_ms": {"cold": round(cold_tls_ms, 2), "warm": round(warm_tls_ms, 2)},
        "controlled_delays_ms": {
            "allocation": 300, "local_tls": 200, "attachment": 350,
            "readiness_observer": 250, "indicator_observer": 250,
        },
        "median_ms": {key: round(statistics.median(values), 2)
                      for key, values in results.items()},
        "measurements_ms": {key: [round(value, 2) for value in values]
                            for key, values in results.items()},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.samples <= 30:
        parser.error("samples must be between 1 and 30")
    print(json.dumps(asyncio.run(measure(args.samples)), indent=2))
