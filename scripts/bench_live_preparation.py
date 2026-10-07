"""Fresh-process Live preparation benchmark with zero network/audio activity.

Run once per process; repeat interleaved with --baseline-dir for a comparison.
The baseline directory contains saved jarvis/realtime/factory.py and
jarvis/plugins/realtime/openai_subscription_live.py from the compared tree.
HTTP allocation and Python/SSL imports are real. Provider peers are fake.
This measures local cold-start work, not provider or microphone latency.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import json
import socket
import ssl
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load(name: str, root: Path | None):
    if root is None:
        return importlib.import_module(name)
    spec = importlib.util.spec_from_file_location(name, root / (name.replace(".", "/") + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def measure(baseline: Path | None) -> dict:
    from jarvis.realtime import factory as installed_factory

    factory = load("jarvis.realtime.factory", baseline) if baseline else installed_factory
    wire = load("jarvis.plugins.realtime.openai_subscription_live", baseline)
    provider_type = wire.OpenAISubscriptionLiveProvider
    # Isolate selected-provider preparation from account and registry discovery.
    factory._explicit_provider_ids = lambda config: [provider_type.name]
    factory.load = lambda *args, **kwargs: provider_type
    factory._realtime_is_the_configured_voice_mode = lambda config: True
    credentials_read = 0
    requests = 0
    sdp = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline benchmark attempted network I/O")

    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.getaddrinfo = forbidden

    started = time.perf_counter()
    await factory.realtime_warm_selected_transports(SimpleNamespace())
    warm_ms = (time.perf_counter() - started) * 1000
    assert credentials_read == 0 and requests == 0

    async def credentials(**kwargs):
        nonlocal credentials_read
        credentials_read += 1
        return SimpleNamespace(access_token="offline-fake", account_id="offline-fake")  # noqa: S106

    def client_factory(**kwargs):
        import httpx

        # Real TLS/proxy/client initialization; only the request method is fake.
        client = httpx.AsyncClient(**kwargs)

        @asynccontextmanager
        async def stream(*args, **kwargs):
            nonlocal requests
            requests += 1
            yield httpx.Response(201, text=sdp, headers={"location": "/v1/live/rtc_offline"})

        client.stream = stream
        return client

    class Socket:
        async def send(self, value):
            return None

        async def close(self):
            return None

    async def connect(*args, **kwargs):
        # asyncio's real ssl=True path builds this context on the event loop.
        # A prepared context avoids that work while retaining its trust policy.
        context = kwargs.get("ssl") or ssl.create_default_context()
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        return Socket()

    start = time.perf_counter()
    importlib.import_module("jarvis.live.subscription")
    imported = time.perf_counter()
    provider = provider_type(credentials=credentials, http_client_factory=client_factory,
                             websocket_connect=connect)
    connection = await provider.open_session(SimpleNamespace(offer_sdp=sdp, session={}))
    ready = time.perf_counter()
    await connection.close()
    return {
        "mode": "baseline" if baseline else "changed",
        "network_requests": 0,
        "fake_allocations": requests,
        "credential_reads": credentials_read,
        "gated_local_warm_ms": round(warm_ms, 2),
        "first_call_import_ms": round((imported - start) * 1000, 2),
        "first_call_local_transport_ms": round((ready - imported) * 1000, 2),
        "first_call_local_total_ms": round((ready - start) * 1000, 2),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(measure(args.baseline_dir))))
