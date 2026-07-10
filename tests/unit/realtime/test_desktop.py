from __future__ import annotations

import asyncio

import pytest

from jarvis.realtime.desktop import DesktopRealtimePlayback


class FakePlayer:
    def __init__(self) -> None:
        self.chunks = []
        self.stopped = 0

    async def play_chunks(self, chunks) -> None:
        async for chunk in chunks:
            self.chunks.append(chunk)

    def stop(self) -> None:
        self.stopped += 1


@pytest.mark.asyncio
async def test_turn_uses_one_stream_and_drains_in_order():
    player = FakePlayer()
    playback = DesktopRealtimePlayback(player)

    await playback.send_binary(b"\x01\x00" * 8)
    await playback.send_binary(b"\x02\x00" * 8)
    await playback.finish_turn()

    assert [chunk.pcm for chunk in player.chunks] == [
        b"\x01\x00" * 8,
        b"\x02\x00" * 8,
    ]
    assert all(chunk.sample_rate == 24_000 for chunk in player.chunks)


@pytest.mark.asyncio
async def test_cancel_stops_player_and_discards_queued_audio():
    gate = asyncio.Event()

    class SlowPlayer(FakePlayer):
        async def play_chunks(self, chunks) -> None:
            async for chunk in chunks:
                await gate.wait()
                self.chunks.append(chunk)

    player = SlowPlayer()
    playback = DesktopRealtimePlayback(player)
    await playback.send_binary(b"\x01\x00" * 8)

    await playback.cancel()

    assert player.stopped == 1
    assert player.chunks == []


@pytest.mark.asyncio
async def test_handshake_sample_rate_is_used_for_the_next_turn():
    player = FakePlayer()
    playback = DesktopRealtimePlayback(player)

    playback.set_sample_rate(16_000)
    await playback.send_binary(b"\x01\x00" * 8)
    await playback.finish_turn()

    assert [chunk.sample_rate for chunk in player.chunks] == [16_000]
