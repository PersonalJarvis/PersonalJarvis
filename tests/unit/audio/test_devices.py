"""Device enumeration + name resolution for the Settings audio-device pickers.

Guards for :mod:`jarvis.audio.devices`:

1. **Picker list quality** — one entry per physical device: host-API twins
   (WASAPI/MME/DirectSound expose the same endpoint) are deduped, the
   MME-truncated name variant merges into the full name, the localized
   MME/DirectSound virtual mapper and WDM-KS entries (BUG-014: blocking API
   unsupported) never appear, the OS-default endpoint is flagged and sorts
   first.
2. **Name resolution** — a persisted device NAME resolves to a concrete
   PortAudio index with the direction's host-API preference (output: WASAPI
   first; input: MME first), tolerating the MME truncation; an unknown name
   yields None so callers can fall back to auto-headset.
3. **Headless safety** — no sounddevice / a failing query degrades to an
   empty list / None, never raises (python:3.11-slim contract, CLAUDE.md §3).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.audio import devices as dv


def _patch_tables(monkeypatch, hostapis, devices, *, default=(-1, -1)) -> None:
    monkeypatch.setattr(dv.sd, "query_devices", lambda: devices)
    monkeypatch.setattr(dv.sd, "query_hostapis", lambda: hostapis)
    monkeypatch.setattr(dv.sd, "default", SimpleNamespace(device=default))


# --------------------------------------------------------------------------- #
# Windows-like table: one headset + one Realtek board, each on 4 host APIs,   #
# plus the virtual mappers. Mirrors a real PortAudio enumeration.             #
# --------------------------------------------------------------------------- #
# Localized Windows device names under test (matching data, not prose).
_FULL_HEADSET_OUT = "Lautsprecher (Logitech PRO X Gaming Headset)"  # i18n-allow
_MME_HEADSET_OUT = _FULL_HEADSET_OUT[:31]  # MME truncates at ~31 chars
_FULL_HEADSET_IN = "Mikrofon (Logitech PRO X Gaming Headset)"  # i18n-allow
_MME_HEADSET_IN = _FULL_HEADSET_IN[:31]


def _windows_tables() -> tuple[list[dict], list[dict]]:
    hostapis = [
        {"name": "MME", "devices": [0, 1, 2, 3]},
        {"name": "Windows DirectSound", "devices": [4, 5]},
        {"name": "Windows WASAPI", "devices": [6, 7, 8]},
        {"name": "Windows WDM-KS", "devices": [9]},
    ]
    devices = [
        # -- MME (names truncated, mappers first — the real enumeration order) --
        {"name": "Microsoft Sound Mapper - Output", "hostapi": 0,
         "max_output_channels": 2, "max_input_channels": 0},      # 0: out-mapper
        {"name": "Microsoft Sound Mapper - Input", "hostapi": 0,
         "max_output_channels": 0, "max_input_channels": 2},      # 1: in-mapper
        {"name": _MME_HEADSET_OUT, "hostapi": 0,
         "max_output_channels": 2, "max_input_channels": 0},      # 2
        {"name": _MME_HEADSET_IN, "hostapi": 0,
         "max_output_channels": 0, "max_input_channels": 1},      # 3
        # -- DirectSound (mapper first) --
        {"name": "Primary Sound Driver", "hostapi": 1,
         "max_output_channels": 2, "max_input_channels": 0},      # 4: mapper
        {"name": _FULL_HEADSET_OUT, "hostapi": 1,
         "max_output_channels": 2, "max_input_channels": 0},      # 5
        # -- WASAPI (full names) --
        {"name": _FULL_HEADSET_OUT, "hostapi": 2,
         "max_output_channels": 2, "max_input_channels": 0},      # 6
        {"name": _FULL_HEADSET_IN, "hostapi": 2,
         "max_output_channels": 0, "max_input_channels": 1},      # 7
        {"name": "Speakers (Realtek HD Audio)", "hostapi": 2,
         "max_output_channels": 2, "max_input_channels": 0},      # 8
        # -- WDM-KS (structurally unusable, BUG-014) --
        {"name": "Speakers (Realtek HD Audio output)", "hostapi": 3,
         "max_output_channels": 2, "max_input_channels": 0},      # 9
    ]
    return hostapis, devices


def test_output_list_dedupes_twins_and_hides_mapper_and_wdmks(monkeypatch) -> None:
    hostapis, devices = _windows_tables()
    _patch_tables(monkeypatch, hostapis, devices)

    names = [d.name for d in dv.list_devices(output=True)]

    # One entry per physical device: the truncated MME twin merged into the
    # full name, the mappers and the WDM-KS-only entry are gone.
    assert names.count(_FULL_HEADSET_OUT) == 1
    assert _MME_HEADSET_OUT not in names
    assert "Microsoft Sound Mapper - Output" not in names
    assert "Primary Sound Driver" not in names
    assert "Speakers (Realtek HD Audio output)" not in names
    assert "Speakers (Realtek HD Audio)" in names
    assert len(names) == 2


def test_input_list_contains_only_real_mics(monkeypatch) -> None:
    hostapis, devices = _windows_tables()
    _patch_tables(monkeypatch, hostapis, devices)

    names = [d.name for d in dv.list_devices(output=False)]

    assert names == [_FULL_HEADSET_IN]


def test_os_default_is_flagged_and_sorts_first(monkeypatch) -> None:
    hostapis, devices = _windows_tables()
    # OS default output = the Realtek board (idx 8).
    _patch_tables(monkeypatch, hostapis, devices, default=(-1, 8))

    out = dv.list_devices(output=True)

    assert out[0].name == "Speakers (Realtek HD Audio)"
    assert out[0].is_default is True
    assert all(not d.is_default for d in out[1:])


def test_short_generic_default_does_not_flag_prefix_extensions(monkeypatch) -> None:
    """A default mic named just "Microphone" must NOT also flag the distinct
    "Microphone Array (...)" device — only a >=30-char prefix relation can be
    an MME-truncation twin of the same endpoint (review finding 2026-07-06)."""
    hostapis = [{"name": "Windows WASAPI", "devices": [0, 1]}]
    devices = [
        {"name": "Microphone", "hostapi": 0,
         "max_output_channels": 0, "max_input_channels": 1},
        {"name": "Microphone Array (Realtek(R) Audio)", "hostapi": 0,
         "max_output_channels": 0, "max_input_channels": 2},
    ]
    _patch_tables(monkeypatch, hostapis, devices, default=(0, -1))

    entries = dv.list_devices(output=False)

    flags = {e.name: e.is_default for e in entries}
    assert flags == {
        "Microphone": True,
        "Microphone Array (Realtek(R) Audio)": False,
    }


def test_list_devices_headless_returns_empty(monkeypatch) -> None:
    def fail_query():
        raise RuntimeError("portaudio unavailable")

    monkeypatch.setattr(dv.sd, "query_devices", fail_query)

    assert dv.list_devices(output=True) == []
    assert dv.list_devices(output=False) == []


def test_list_devices_without_sounddevice_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(dv, "sd", None)

    assert dv.list_devices(output=True) == []
    assert dv.list_devices(output=False) == []


# --------------------------------------------------------------------------- #
# Name resolution                                                              #
# --------------------------------------------------------------------------- #
def test_resolve_output_name_prefers_wasapi_twin(monkeypatch) -> None:
    hostapis, devices = _windows_tables()
    _patch_tables(monkeypatch, hostapis, devices)

    # The full name exists on DirectSound (idx 5) and WASAPI (idx 6) —
    # output preference picks WASAPI.
    assert dv.resolve_device_by_name(_FULL_HEADSET_OUT, output=True) == 6


def test_resolve_input_name_prefers_mme_twin(monkeypatch) -> None:
    hostapis, devices = _windows_tables()
    _patch_tables(monkeypatch, hostapis, devices)

    # The mic exists as the truncated MME twin (idx 3) and on WASAPI (idx 7).
    # Input preference picks MME (16 kHz resampling, wake-loop contract), and
    # the truncated name must still match the full persisted name.
    assert dv.resolve_device_by_name(_FULL_HEADSET_IN, output=False) == 3


def test_resolve_never_returns_wdmks(monkeypatch) -> None:
    hostapis, devices = _windows_tables()
    _patch_tables(monkeypatch, hostapis, devices)

    # "Speakers (Realtek HD Audio output)" exists ONLY on WDM-KS → not
    # resolvable; the substring also matches the safe WASAPI Realtek entry.
    resolved = dv.resolve_device_by_name(
        "Speakers (Realtek HD Audio)", output=True
    )
    assert resolved == 8


def test_resolve_unknown_name_returns_none(monkeypatch) -> None:
    hostapis, devices = _windows_tables()
    _patch_tables(monkeypatch, hostapis, devices)

    assert dv.resolve_device_by_name("Jabra Evolve2 65", output=True) is None


def test_resolve_is_case_insensitive(monkeypatch) -> None:
    hostapis, devices = _windows_tables()
    _patch_tables(monkeypatch, hostapis, devices)

    assert (
        dv.resolve_device_by_name(_FULL_HEADSET_OUT.upper(), output=True) == 6
    )


def test_resolve_headless_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(dv, "sd", None)

    assert dv.resolve_device_by_name("anything", output=True) is None


@pytest.mark.parametrize("value", ["", "   ", "auto-headset"])
def test_resolve_auto_or_empty_returns_none(monkeypatch, value) -> None:
    hostapis, devices = _windows_tables()
    _patch_tables(monkeypatch, hostapis, devices)

    assert dv.resolve_device_by_name(value, output=True) is None


# --------------------------------------------------------------------------- #
# Resolver integration: a persisted NAME in [audio].*_device resolves to an   #
# index at stream-open time; an unplugged name falls back to auto-headset     #
# instead of bricking playback / the wake loop.                               #
# --------------------------------------------------------------------------- #
def _patch_all(monkeypatch, hostapis, devices) -> None:
    from types import SimpleNamespace as _NS

    import jarvis.audio.capture as cap
    import jarvis.audio.player as pl

    for mod in (dv, pl, cap):
        monkeypatch.setattr(mod.sd, "query_devices", lambda: devices)
        monkeypatch.setattr(mod.sd, "query_hostapis", lambda: hostapis)
        monkeypatch.setattr(mod.sd, "default", _NS(device=(-1, -1)))


def test_player_resolves_configured_name_to_index(monkeypatch) -> None:
    import jarvis.audio.player as pl

    hostapis, devices = _windows_tables()
    _patch_all(monkeypatch, hostapis, devices)

    assert pl._resolve_output_device(_FULL_HEADSET_OUT) == 6


def test_player_falls_back_to_auto_when_named_device_is_gone(monkeypatch) -> None:
    import jarvis.audio.player as pl

    hostapis, devices = _windows_tables()
    _patch_all(monkeypatch, hostapis, devices)

    # The unplugged headset name must not brick playback: the resolver falls
    # back to the auto-headset heuristic (which lands on a real, safe sink).
    resolved = pl._resolve_output_device("Jabra Evolve2 65")
    assert resolved in (5, 6, 8)


def test_capture_resolves_configured_name_to_index(monkeypatch) -> None:
    import jarvis.audio.capture as cap

    hostapis, devices = _windows_tables()
    _patch_all(monkeypatch, hostapis, devices)

    assert cap._resolve_input_device(_FULL_HEADSET_IN) == 3


def test_capture_falls_back_to_auto_when_named_device_is_gone(monkeypatch) -> None:
    import jarvis.audio.capture as cap

    hostapis, devices = _windows_tables()
    _patch_all(monkeypatch, hostapis, devices)

    resolved = cap._resolve_input_device("Blue Yeti")
    assert resolved in (3, 7)
