from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.audio import capture


@pytest.fixture(autouse=True)
def _no_os_default(monkeypatch) -> None:
    """Pin "no OS-selected default device" so these pure-heuristic tests do not
    depend on the real test host's default microphone — the resolver now consults
    it for the "your device first" contract."""
    monkeypatch.setattr(capture.sd, "default", SimpleNamespace(device=(-1, -1)))


def test_auto_headset_prefers_real_microphone_over_loopback(monkeypatch) -> None:
    devices = [
        {
            "name": "Stereo Mix (Realtek Audio)",
            "max_input_channels": 2,
            "max_output_channels": 0,
            "hostapi": 0,
        },
        {
            "name": "Monitor of Speakers (Loopback)",
            "max_input_channels": 2,
            "max_output_channels": 0,
            "hostapi": 0,
        },
        {
            "name": "Microphone (Logitech PRO X Gaming Headset)",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "hostapi": 0,
        },
    ]
    hostapis = [{"name": "Windows WASAPI"}]

    monkeypatch.setattr(capture.sd, "query_devices", lambda: devices)
    monkeypatch.setattr(capture.sd, "query_hostapis", lambda: hostapis)

    assert capture._resolve_input_device("auto-headset") == 2


def test_auto_headset_prefers_mme_for_same_microphone_name(monkeypatch) -> None:
    """When the same mic exposes itself under both WASAPI and MME, MME wins.

    Reason (2026-04-26 forensics, see ``_HOSTAPI_PREFERENCE`` docstring in
    ``jarvis/audio/capture.py``): WASAPI on a Logitech PRO X silently
    killed the wake loop because the 48 kHz native rate did not survive
    PortAudio's blocking open at 16 kHz. MME / DirectSound resample
    transparently, so the resolver deliberately ranks them ahead of
    WASAPI for always-on capture. WDM-KS is in the blocklist (BUG-014
    twin) and never even reaches the rank step. This test pins that
    policy so a future "tidy-up" of the preference dict cannot silently
    re-introduce the wake-loop kill.
    """
    devices = [
        {
            "name": "Microphone (USB Audio Device)",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "hostapi": 1,
        },
        {
            "name": "Microphone (USB Audio Device)",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "hostapi": 0,
        },
    ]
    hostapis = [{"name": "Windows WASAPI"}, {"name": "MME"}]

    monkeypatch.setattr(capture.sd, "query_devices", lambda: devices)
    monkeypatch.setattr(capture.sd, "query_hostapis", lambda: hostapis)

    # idx=0 is on hostapi=1 → hostapis[1] = "MME"; idx=1 is on
    # hostapi=0 → hostapis[0] = "Windows WASAPI". MME wins per policy.
    assert capture._resolve_input_device("auto-headset") == 0


def test_auto_headset_falls_back_to_default_when_query_fails(monkeypatch) -> None:
    def fail_query():
        raise RuntimeError("portaudio unavailable")

    monkeypatch.setattr(capture.sd, "query_devices", fail_query)

    assert capture._resolve_input_device("auto-headset") is None


def test_explicit_input_device_is_not_resolved(monkeypatch) -> None:
    def fail_query():
        raise AssertionError("query_devices should not be called")

    monkeypatch.setattr(capture.sd, "query_devices", fail_query)

    assert capture._resolve_input_device("Microphone (Manual)") == "Microphone (Manual)"
    assert capture._resolve_input_device(3) == 3
