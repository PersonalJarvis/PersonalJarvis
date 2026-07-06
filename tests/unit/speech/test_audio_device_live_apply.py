"""Live-apply of a Settings audio-device pick — no app/pipeline restart.

``SpeechPipeline.set_audio_devices`` mirrors the ``set_wake_plan`` /
``set_tts_volume`` live-switch contract: the output side hot-swaps the
player's device, the input side updates the device every future mic open
reads and re-arms the running wake session via ``_wake_reload_event`` so the
always-on mic reopens on the new device.
"""
from __future__ import annotations

import asyncio

from jarvis.speech.pipeline import SpeechPipeline


class _FakePlayer:
    def __init__(self) -> None:
        self.devices: list[object] = []

    def set_device(self, device: object) -> None:
        self.devices.append(device)


def _pipe() -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._player = _FakePlayer()
    pipe._input_device = "auto-headset"
    pipe._output_device = "auto-headset"
    pipe._wake_reload_event = asyncio.Event()
    return pipe


def test_output_pick_hot_swaps_player_without_touching_input() -> None:
    pipe = _pipe()

    pipe.set_audio_devices(output_device="PRO X Gaming Headset")

    assert pipe._output_device == "PRO X Gaming Headset"
    assert pipe._player.devices == ["PRO X Gaming Headset"]
    assert pipe._input_device == "auto-headset"
    assert not pipe._wake_reload_event.is_set()


def test_input_pick_updates_device_and_rearms_wake_session() -> None:
    pipe = _pipe()

    pipe.set_audio_devices(input_device="Blue Yeti")

    assert pipe._input_device == "Blue Yeti"
    assert pipe._wake_reload_event.is_set()
    assert pipe._player.devices == []  # output side untouched


def test_auto_sentinel_restores_automatic_selection() -> None:
    pipe = _pipe()
    pipe._output_device = "PRO X Gaming Headset"

    pipe.set_audio_devices(output_device="auto-headset")

    assert pipe._output_device == "auto-headset"
    assert pipe._player.devices == ["auto-headset"]


def test_none_leaves_both_sides_unchanged() -> None:
    pipe = _pipe()

    pipe.set_audio_devices()

    assert pipe._input_device == "auto-headset"
    assert pipe._output_device == "auto-headset"
    assert pipe._player.devices == []
    assert not pipe._wake_reload_event.is_set()
