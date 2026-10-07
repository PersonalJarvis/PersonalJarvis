"""Exercise real PCM writes, stream teardown and queued output mute boundaries."""

import asyncio

import numpy as np

from jarvis.audio.player import AudioPlayer
from jarvis.core.protocols import AudioChunk
from jarvis.realtime.desktop import DesktopRealtimePlayback


class Stream:
    latency = 0.02

    def __init__(self):
        self.writes = []
        self.aborted = False
        self.closed = False
        self.after_write = lambda: None

    def write(self, samples):
        self.writes.append(samples.copy())
        self.after_write()
        return False

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True


def player_and_streams(monkeypatch):
    player = AudioPlayer.__new__(AudioPlayer)
    player._volume = 0.25
    player._device_logged = True
    player._bus = None
    player._play_lock = None
    player._active_stream = None
    player._active_source_rate = None
    player._active_device_rate = None
    player._init_progress()
    streams = []

    def open_stream(rate):
        stream = Stream()
        streams.append(stream)
        return stream, rate

    monkeypatch.setattr(player, "_open_output_stream", open_stream)
    return player, streams


def chunk(value):
    return AudioChunk(pcm=np.full(4800, value, dtype=np.int16).tobytes(),
                      sample_rate=24000, timestamp_ns=0)


def test_mute_discards_an_already_scaled_long_write_and_native_buffer(monkeypatch):
    player, _ = player_and_streams(monkeypatch)
    stream = Stream()
    player._active_stream = stream
    stream.after_write = lambda: player.set_muted(True)
    player._write_samples(stream, np.full(24000, 8000, dtype=np.int16), 24000, 24000)
    assert len(stream.writes) == 1
    assert stream.aborted and stream.closed
    assert player._volume == 0.25
    player.set_muted(False)
    resumed = Stream()
    player._write_samples(resumed, np.full(1440, 4000, dtype=np.int16), 24000, 24000)
    assert len(resumed.writes) == 1
    assert np.any(resumed.writes[0])


async def test_active_tts_is_consumed_while_muted_and_only_fresh_pcm_resumes(monkeypatch):
    player, streams = player_and_streams(monkeypatch)
    consumed = []

    async def speech():
        yield chunk(1000)
        player.set_muted(True)
        consumed.append("work continues")
        yield chunk(2000)
        assert streams[0].aborted
        player.set_muted(False)
        yield chunk(3000)

    await player.play_chunks(speech())
    assert consumed == ["work continues"]
    assert len(streams) == 2
    assert np.max(streams[0].writes[0]) < np.min(streams[1].writes[0])
    assert sum(len(w) for s in streams for w in s.writes) == 9600


async def test_muted_player_does_not_open_output_and_volume_changes_do_not_unmute(monkeypatch):
    player, streams = player_and_streams(monkeypatch)
    player.set_muted(True)
    player.set_volume(0.7)

    async def speech():
        for _ in range(3):
            yield chunk(1000)

    assert await player.play_chunks(speech()) is False
    assert streams == []
    assert player.output_muted


async def test_realtime_queue_never_replays_pcm_from_before_or_during_mute(monkeypatch):
    player, _ = player_and_streams(monkeypatch)
    entered = asyncio.Event()
    release = asyncio.Event()
    heard = []

    async def play(chunks):
        entered.set()
        await release.wait()
        async for audio in chunks:
            heard.append(audio.pcm)
        return True

    monkeypatch.setattr(player, "play_chunks", play)
    playback = DesktopRealtimePlayback(player, prebuffer_ms=0)
    await playback.send_binary(b"old")
    await entered.wait()
    player.set_muted(True)
    await playback.send_binary(b"muted")
    player.set_muted(False)
    await playback.send_binary(b"fresh")
    release.set()
    await playback.finish_turn()
    assert heard == [b"fresh"]


async def test_mute_during_slow_device_open_closes_late_stream(monkeypatch):
    player, streams = player_and_streams(monkeypatch)
    open_stream = player._open_output_stream

    def racing_open(rate):
        result = open_stream(rate)
        player.set_muted(True)
        player.set_muted(False)
        return result

    monkeypatch.setattr(player, "_open_output_stream", racing_open)

    async def speech():
        yield chunk(1000)

    assert await player.play_chunks(speech()) is False
    assert streams[0].closed
    assert streams[0].writes == []
