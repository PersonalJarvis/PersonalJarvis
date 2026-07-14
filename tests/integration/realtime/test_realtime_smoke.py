"""Task 10 — end-to-end smoke test for the realtime provider.

Self-skips cleanly when no OpenAI key is configured (CI / a fresh install
without a key must never fail here — AP-23). With a key present it opens a
real duplex session, drains one event with a timeout, and closes cleanly.
"""

from __future__ import annotations

import pytest

from jarvis.core.config import get_provider_secret

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_can_open_duplex_session_returns_a_bool():
    """Keyless-safe: the capability probe must never raise, key or no key."""
    from jarvis.plugins.realtime.openai_realtime import OpenAIRealtimeProvider

    prov = OpenAIRealtimeProvider(api_key=get_provider_secret("openai"))
    assert isinstance(await prov.can_open_duplex_session(), bool)


@pytest.mark.skipif(not get_provider_secret("openai"), reason="no OpenAI key")
@pytest.mark.asyncio
async def test_open_and_close_a_real_session():
    from jarvis.plugins.realtime.openai_realtime import OpenAIRealtimeProvider
    from jarvis.realtime.protocol import RealtimeSessionConfig

    prov = OpenAIRealtimeProvider(api_key=get_provider_secret("openai"))
    assert await prov.can_open_duplex_session() is True
    sess = await prov.open_session(RealtimeSessionConfig(instructions="Say hi.", language="en"))

    # ``open_session`` now waits for ``session.updated``. Reaching this point
    # proves that the server accepted the complete schema; merely draining the
    # initial ``session.created`` event used to hide the following API error.
    try:
        assert sess.session_id
    finally:
        await sess.close()
