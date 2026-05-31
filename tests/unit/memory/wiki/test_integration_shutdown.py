"""Lifecycle tests for ``WikiIntegrationHandle.shutdown``.

Regression for the ``_voice_bridge`` leak: the bridge used to be
monkey-patched onto the dataclass handle (not a declared field), so
``shutdown()`` never stopped it and its ``TranscriptFinal`` /
``ResponseGenerated`` bus subscriptions leaked on every teardown — and
piled up across a bootstrap → shutdown → bootstrap cycle (tests, re-config).
"""
from __future__ import annotations

import pytest

from jarvis.memory.wiki.integration import WikiIntegrationHandle


class _FakeBridge:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_shutdown_stops_voice_bridge() -> None:
    bridge = _FakeBridge()
    handle = WikiIntegrationHandle(
        _unsubscribe_idle=lambda: None,
        _worker_stop=None,
        _voice_bridge=bridge,
    )

    await handle.shutdown()

    assert bridge.stopped is True, "shutdown() must stop the VoiceFactBridge"


@pytest.mark.asyncio
async def test_shutdown_without_voice_bridge_is_noop() -> None:
    """The bridge is optional; shutdown must not fail when none was attached."""
    handle = WikiIntegrationHandle(
        _unsubscribe_idle=lambda: None,
        _worker_stop=None,
    )
    await handle.shutdown()  # must not raise


@pytest.mark.asyncio
async def test_shutdown_tolerates_failing_bridge_stop() -> None:
    """A bridge.stop() that raises must not abort the rest of teardown."""
    class _Boom:
        def stop(self) -> None:
            raise RuntimeError("bridge boom")

    handle = WikiIntegrationHandle(
        _unsubscribe_idle=lambda: None,
        _worker_stop=None,
        _voice_bridge=_Boom(),
    )
    await handle.shutdown()  # must not raise
