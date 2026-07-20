"""Unit tests for the played-output echo-reference envelope (BUG-101).

The barge-in detector separates the assistant's own speaker echo from a real
user interruption by correlating candidate audio against this process-local
record of what the speakers just emitted. These tests pin the module contract
(record/snapshot windowing, reset) and that ``AudioPlayer._write_samples``
actually feeds it for every played block.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

import jarvis.audio.echo_reference as echo_reference
from jarvis.audio.player import AudioPlayer
from tests.unit.audio.test_player_persistent_stream import _make_player


@pytest.fixture(autouse=True)
def _clean_reference():
    echo_reference.reset()
    yield
    echo_reference.reset()


def test_record_and_snapshot_window() -> None:
    now = time.monotonic()
    echo_reference.record(0.05, 0.06, timestamp=now - 10.0)
    echo_reference.record(0.07, 0.06, timestamp=now - 1.0)
    echo_reference.record(0.09, 0.06, timestamp=now)

    recent = echo_reference.snapshot(3.0)
    assert [entry[2] for entry in recent] == [0.07, 0.09]
    assert echo_reference.snapshot(30.0)[0][2] == 0.05


def test_record_ignores_nonpositive_duration_and_reset_clears() -> None:
    echo_reference.record(0.5, 0.0)
    assert echo_reference.snapshot(5.0) == []
    echo_reference.record(0.5, 0.06)
    assert len(echo_reference.snapshot(5.0)) == 1
    echo_reference.reset()
    assert echo_reference.snapshot(5.0) == []


def test_write_samples_records_played_envelope(monkeypatch) -> None:
    player, _events = _make_player(monkeypatch)
    monkeypatch.setattr(
        player,
        "_write_samples",
        AudioPlayer._write_samples.__get__(player, AudioPlayer),
    )

    class _AcceptingStream:
        def write(self, _samples) -> bool:
            return False

    # 200 ms of clearly audible int16 audio, no resample (source == device).
    arr = np.full(4_800, 8_000, dtype=np.dtype("<i2"))
    player._write_samples(_AcceptingStream(), arr, 24_000, 24_000)

    entries = echo_reference.snapshot(5.0)
    assert entries, "played audio must land in the echo reference"
    assert sum(entry[1] for entry in entries) == pytest.approx(0.2, abs=0.02)
    assert all(entry[2] > 0.1 for entry in entries)
