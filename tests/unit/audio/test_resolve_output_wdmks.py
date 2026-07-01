"""Regression: WDM-KS output devices must never be auto-selected (BUG-014).

2026-05-24 recurrence: the user heard nothing while the brain answered and
TTS synthesized fine. The runtime log showed::

    AudioPlayer using device: Speakers (Realtek HD Audio output) (idx=22)
    ACK playback failed: ... [PaErrorCode -9999]
        'Blocking API not supported yet' [Windows WDM-KS error -9999]

Root cause: ``_resolve_output_device`` filtered a WDM-KS device only when the
SAME name existed on a safe host API. "Speakers (Realtek HD Audio output)" is
a WDM-KS-ONLY name (no MME/WASAPI twin), so it survived the filter, won on
name rank ("Realtek HD Audio"), and crashed at OutputStream open. The fix
excludes WDM-KS unconditionally whenever any safe output device exists.
"""
from __future__ import annotations

import jarvis.audio.player as player_mod


def _patch_devices(monkeypatch, hostapis, devices) -> None:
    monkeypatch.setattr(player_mod.sd, "query_devices", lambda: devices)
    monkeypatch.setattr(player_mod.sd, "query_hostapis", lambda: hostapis)


def test_wdmks_only_device_is_never_selected(monkeypatch) -> None:
    """A WDM-KS-only device with the best name rank must NOT be picked when
    any safe (non-WDM-KS) output device exists."""
    hostapis = [
        {"name": "MME"},
        {"name": "Windows WASAPI"},
        {"name": "Windows WDM-KS"},
    ]
    devices = [
        # Safe device, weaker name rank ("Realtek" only).
        {"name": "Realtek Digital Output (Realtek High Definition Audio)",
         "max_output_channels": 2, "hostapi": 1},
        # The trap: WDM-KS-only, stronger name rank ("Realtek HD Audio"),
        # 8 channels. Old filter let it through -> -9999 crash.
        {"name": "Speakers (Realtek HD Audio output)",
         "max_output_channels": 8, "hostapi": 2},
        # Another safe device.
        {"name": "Speakers (NVIDIA Broadcast)",
         "max_output_channels": 2, "hostapi": 1},
    ]
    _patch_devices(monkeypatch, hostapis, devices)

    resolved = player_mod._resolve_output_device("auto-headset")

    assert resolved != 1, (
        "resolver picked the WDM-KS-only device — BUG-014 recurrence "
        "(PaErrorCode -9999 at OutputStream open, user hears nothing)"
    )
    assert isinstance(resolved, int)
    picked_hostapi = hostapis[devices[resolved]["hostapi"]]["name"]
    assert picked_hostapi not in player_mod._FORBIDDEN_OUTPUT_HOSTAPIS


def test_safe_usb_headset_preferred_over_wdmks_realtek(monkeypatch) -> None:
    """When the user's real device (USB Audio, the Windows default) is present
    on WASAPI, it must win over any WDM-KS device."""
    hostapis = [
        {"name": "MME"},
        {"name": "Windows WASAPI"},
        {"name": "Windows WDM-KS"},
    ]
    devices = [
        {"name": "Lautsprecher (AB13X USB Audio)",
         "max_output_channels": 2, "hostapi": 0},   # MME twin
        {"name": "Lautsprecher (AB13X USB Audio)",
         "max_output_channels": 2, "hostapi": 1},   # WASAPI twin (preferred)
        {"name": "Speakers (Realtek HD Audio output)",
         "max_output_channels": 8, "hostapi": 2},   # WDM-KS trap
    ]
    _patch_devices(monkeypatch, hostapis, devices)

    resolved = player_mod._resolve_output_device("auto-headset")

    assert isinstance(resolved, int)
    assert devices[resolved]["name"] == "Lautsprecher (AB13X USB Audio)"
    assert hostapis[devices[resolved]["hostapi"]]["name"] == "Windows WASAPI"


def test_wdmks_used_only_when_no_safe_device_exists(monkeypatch) -> None:
    """Edge case: if the ONLY output device is WDM-KS, fall back to it rather
    than returning nothing — better a risky device than silence-by-omission."""
    hostapis = [{"name": "MME"}, {"name": "Windows WASAPI"}, {"name": "Windows WDM-KS"}]
    devices = [
        {"name": "Speakers (Realtek HD Audio output)",
         "max_output_channels": 8, "hostapi": 2},
    ]
    _patch_devices(monkeypatch, hostapis, devices)

    resolved = player_mod._resolve_output_device("auto-headset")

    assert resolved == 0
