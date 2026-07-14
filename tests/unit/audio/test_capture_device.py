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


def test_explicit_index_is_not_resolved(monkeypatch) -> None:
    def fail_query():
        raise AssertionError("query_devices should not be called")

    monkeypatch.setattr(capture.sd, "query_devices", fail_query)

    assert capture._resolve_input_device(3) == 3


def test_explicit_name_resolves_to_index(monkeypatch) -> None:
    """A concrete NAME (persisted by the Settings device picker) resolves to
    its PortAudio index at open time — names are the stable identifier across
    reboots; raw name strings handed to PortAudio are ambiguous across host
    APIs (the pre-2026-07 pass-through contract)."""
    from jarvis.audio import devices as dv

    devices = [
        {
            "name": "Microphone (Manual)",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "hostapi": 0,
        },
    ]
    hostapis = [{"name": "Windows WASAPI"}]
    for mod in (capture, dv):
        monkeypatch.setattr(mod.sd, "query_devices", lambda: devices)
        monkeypatch.setattr(mod.sd, "query_hostapis", lambda: hostapis)

    assert capture._resolve_input_device("Microphone (Manual)") == 0


def test_resolve_cache_serves_handover_and_invalidates_on_failure(monkeypatch) -> None:
    """The wake→session mic handover must not re-enumerate devices.

    Live forensic 2026-07-11: resolving "auto-headset" cost ~0.4s on every
    MicrophoneCapture construction — the dominant share of the gap in which
    the user's first words after "Hey Jarvis" were not captured. A capture
    whose stream delivers frames touches the resolve cache; a construction
    within the freshness window reuses the proven device WITHOUT calling
    query_devices. Any open failure or stall invalidates, restoring the full
    fresh resolve (hot-plug behaviour unchanged).
    """
    devices = [
        {
            "name": "Microphone (Logitech PRO X Gaming Headset)",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "hostapi": 0,
        },
    ]
    hostapis = [{"name": "MME"}]
    calls = {"n": 0}

    def counting_query():
        calls["n"] += 1
        return devices

    monkeypatch.setattr(capture.sd, "query_devices", counting_query)
    monkeypatch.setattr(capture.sd, "query_hostapis", lambda: hostapis)
    capture._invalidate_resolve_cache()

    # First resolve: full enumeration.
    assert capture._resolve_input_device("auto-headset") == 0
    n_after_first = calls["n"]
    assert n_after_first >= 1

    # A live stream touches the cache (what the watchdog tick does)...
    capture._touch_resolve_cache("auto-headset", (), 0)
    # ...so a construction right after skips enumeration entirely.
    cap = capture.MicrophoneCapture(device="auto-headset")
    assert cap._device == 0
    assert calls["n"] == n_after_first

    # An open failure invalidates: the next construction resolves fresh.
    capture._invalidate_resolve_cache()
    cap2 = capture.MicrophoneCapture(device="auto-headset")
    assert cap2._device == 0
    assert calls["n"] > n_after_first


def test_resolve_cache_expires_after_freshness_window(monkeypatch) -> None:
    capture._invalidate_resolve_cache()
    capture._touch_resolve_cache("auto-headset", (), 3)
    assert capture._cached_resolve("auto-headset", ()) == 3
    # Age the entry past the freshness window (bind the real clock FIRST —
    # the lambda must not call through its own patched self).
    real_monotonic = capture.time.monotonic
    monkeypatch.setattr(
        capture.time,
        "monotonic",
        lambda: real_monotonic() + capture._RESOLVE_CACHE_FRESH_S + 1,
    )
    assert capture._cached_resolve("auto-headset", ()) is None
