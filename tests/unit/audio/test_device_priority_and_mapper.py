"""Locale-robust auto-headset selection + user device-name priority.

Two guarantees pinned here:

1. **Locale robustness.** The MME "Sound Mapper" / DirectSound "Primary Sound
   Driver" virtual routers carry a Windows-*localized* display name, so the old
   German-only substring let the mapper through on
   English/French/… Windows and the resolver could route to the OS-default sink
   that ``auto-headset`` exists to bypass. They are now skipped STRUCTURALLY
   (``jarvis.audio.device_select.is_legacy_primary_mapper``) — the first
   direction-matching device of an MME/DirectSound host API — regardless of the
   translated name.

2. **Personalization.** ``[audio].output_device_priority`` /
   ``input_device_priority`` let any user float their own device to the top by
   name, ahead of the generic built-in headset list, without editing code. An
   empty priority reproduces the generic behavior exactly.
"""
from __future__ import annotations

import pytest

import jarvis.audio.capture as cap
import jarvis.audio.player as pl
from jarvis.audio.device_select import is_legacy_primary_mapper


def _patch_out(monkeypatch, hostapis, devices) -> None:
    monkeypatch.setattr(pl.sd, "query_devices", lambda: devices)
    monkeypatch.setattr(pl.sd, "query_hostapis", lambda: hostapis)


def _patch_in(monkeypatch, hostapis, devices) -> None:
    monkeypatch.setattr(cap.sd, "query_devices", lambda: devices)
    monkeypatch.setattr(cap.sd, "query_hostapis", lambda: hostapis)


# --------------------------------------------------------------------------- #
# 1. Locale robustness — output                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "mapper_name",
    [
        "Primary Sound Driver",             # EN
        "Primärer Soundtreiber",            # DE, i18n-allow: localized name under test
        "Pilote son principal",             # FR
        "Controlador primario de sonido",   # ES
        "Driver audio primario",            # IT
    ],
)
def test_localized_output_mapper_is_skipped_structurally(monkeypatch, mapper_name) -> None:
    """The DirectSound primary mapper must be skipped on ANY UI language.

    Table: DirectSound (better host-API rank) exposes its virtual primary
    device FIRST, then a real endpoint; MME exposes another real endpoint. All
    real devices carry an unrecognized name, so ranking falls to host-API
    preference — if the mapper were NOT skipped it would win the DirectSound
    tiebreak. Asserting the REAL DirectSound device is chosen proves the
    structural skip (a name-based filter would miss every non-German name here).
    """
    hostapis = [
        {"name": "Windows DirectSound", "devices": [0, 1]},
        {"name": "MME", "devices": [2]},
    ]
    devices = [
        {"name": mapper_name, "max_output_channels": 2,
         "max_input_channels": 0, "hostapi": 0},                 # idx0: mapper
        {"name": "Line Out (Obscure Interface)", "max_output_channels": 2,
         "max_input_channels": 0, "hostapi": 0},                 # idx1: real DSound
        {"name": "Line Out (Obscure Interface)", "max_output_channels": 2,
         "max_input_channels": 0, "hostapi": 1},                 # idx2: real MME
    ]
    _patch_out(monkeypatch, hostapis, devices)

    resolved = pl._resolve_output_device("auto-headset")

    assert resolved == 1, (
        f"localized mapper {mapper_name!r} leaked through the resolver "
        "(regex-blind locale regression)"
    )


def test_localized_input_recording_mapper_is_skipped_structurally(monkeypatch) -> None:
    """The DirectSound *recording* primary mapper is skipped too.

    No fixed substring ever covered the recording mapper (its localized name);
    the structural check catches it as the first input-capable DirectSound
    device.
    """
    hostapis = [
        {"name": "Windows DirectSound", "devices": [0, 1]},
        {"name": "MME", "devices": [2]},
    ]
    devices = [
        {"name": "Primary Sound Capture Driver", "max_input_channels": 2,
         "max_output_channels": 0, "hostapi": 0},                # idx0: in-mapper
        {"name": "Line In (Obscure Interface)", "max_input_channels": 2,
         "max_output_channels": 0, "hostapi": 0},                # idx1: real DSound
        {"name": "Line In (Obscure Interface)", "max_input_channels": 2,
         "max_output_channels": 0, "hostapi": 1},                # idx2: real MME
    ]
    _patch_in(monkeypatch, hostapis, devices)

    assert cap._resolve_input_device("auto-headset") == 1


# --------------------------------------------------------------------------- #
# 2. Personalization — user priority beats the generic default                #
# --------------------------------------------------------------------------- #
def test_output_user_priority_beats_generic_headset(monkeypatch) -> None:
    """A user-named device outranks a device the generic list would pick."""
    hostapis = [{"name": "Windows WASAPI", "devices": [0, 1]}]
    devices = [
        # Matches the generic default ("PRO X") — wins with no user priority.
        {"name": "Speakers (PRO X)", "max_output_channels": 2,
         "max_input_channels": 0, "hostapi": 0},
        # The user's own device — not in the generic list at all.
        {"name": "Bose QuietComfort Ultra", "max_output_channels": 2,
         "max_input_channels": 0, "hostapi": 0},
    ]
    _patch_out(monkeypatch, hostapis, devices)

    # Empty priority → generic behavior: the PRO X wins.
    assert pl._resolve_output_device("auto-headset") == 0
    assert pl._resolve_output_device("auto-headset", []) == 0
    # User names their Bose → it wins over the generic PRO X match.
    assert pl._resolve_output_device("auto-headset", ["Bose"]) == 1


def test_input_user_priority_overrides_generic_and_deprioritize(monkeypatch) -> None:
    """A user-named mic wins even over the virtual-mic deprioritize penalty."""
    hostapis = [{"name": "Windows WASAPI", "devices": [0, 1]}]
    devices = [
        # Real headset mic — matches the generic default ("PRO X").
        {"name": "Microphone (PRO X)", "max_input_channels": 1,
         "max_output_channels": 0, "hostapi": 0},
        # Virtual mic — normally pushed BEHIND every real mic (deprioritized).
        {"name": "Microphone (NVIDIA Broadcast)", "max_input_channels": 2,
         "max_output_channels": 0, "hostapi": 0},
    ]
    _patch_in(monkeypatch, hostapis, devices)

    # Empty priority → the real headset mic wins, virtual is deprioritized.
    assert cap._resolve_input_device("auto-headset") == 0
    # User explicitly names the virtual mic → honored despite the penalty.
    assert cap._resolve_input_device("auto-headset", ["NVIDIA Broadcast"]) == 1


# --------------------------------------------------------------------------- #
# 3. Structural helper — direct + fail-safe                                    #
# --------------------------------------------------------------------------- #
def test_mapper_helper_fails_safe_without_membership_list() -> None:
    """A partial host-API table (no ``devices`` member list, as in older test
    fakes) must never mis-classify a device as a mapper."""
    hostapis = [{"name": "MME"}]  # no "devices" key
    devices = [{"name": "Whatever", "max_output_channels": 2, "hostapi": 0}]
    assert is_legacy_primary_mapper(0, hostapis, devices, output=True) is False


def test_mapper_helper_only_flags_mme_and_directsound() -> None:
    """WASAPI/WDM-KS expose no virtual mapper — their first device is real."""
    hostapis = [{"name": "Windows WASAPI", "devices": [0]}]
    devices = [{"name": "Speakers", "max_output_channels": 2, "hostapi": 0}]
    assert is_legacy_primary_mapper(0, hostapis, devices, output=True) is False
