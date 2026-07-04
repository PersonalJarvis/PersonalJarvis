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

from types import SimpleNamespace

import pytest

import jarvis.audio.capture as cap
import jarvis.audio.player as pl
from jarvis.audio.device_select import is_legacy_primary_mapper


@pytest.fixture(autouse=True)
def _neutralize_os_default(monkeypatch) -> None:
    """Simulate "no OS-selected default device" by default so the priority /
    mapper / generic tests here stay deterministic on any host. The "your device
    first" tests below re-patch ``sd.default`` to a specific system default;
    without this fixture the resolver would read the REAL machine's default and
    the outcome would depend on the test host."""
    monkeypatch.setattr(pl.sd, "default", SimpleNamespace(device=(-1, -1)))
    monkeypatch.setattr(cap.sd, "default", SimpleNamespace(device=(-1, -1)))


def _set_os_default(monkeypatch, *, in_idx: int = -1, out_idx: int = -1) -> None:
    """Point ``sd.default.device`` (input, output) at the given fake-table indices."""
    monkeypatch.setattr(pl.sd, "default", SimpleNamespace(device=(in_idx, out_idx)))
    monkeypatch.setattr(cap.sd, "default", SimpleNamespace(device=(in_idx, out_idx)))


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


# --------------------------------------------------------------------------- #
# 4. "Your device first" — the OS-selected default drives auto-headset         #
# --------------------------------------------------------------------------- #
def test_output_os_default_wins_via_its_best_hostapi_twin(monkeypatch) -> None:
    """A real OS-default speaker beats the generic list AND is taken on its best
    host-API twin (WASAPI), even when it was named by an MME twin — proving the
    default is injected as a NAME, not a raw index."""
    hostapis = [{"name": "MME", "devices": [0, 1, 2]},
                {"name": "Windows WASAPI", "devices": [3, 4]}]
    devices = [
        {"name": "Microsoft Sound Mapper - Output", "max_output_channels": 2,
         "max_input_channels": 0, "hostapi": 0},                 # idx0: MME mapper
        {"name": "Speakers (PRO X)", "max_output_channels": 2,
         "max_input_channels": 0, "hostapi": 0},                 # idx1: generic pick
        {"name": "Desk Speakers (Focusrite)", "max_output_channels": 2,
         "max_input_channels": 0, "hostapi": 0},                 # idx2: OS default (MME)
        {"name": "Speakers (PRO X)", "max_output_channels": 2,
         "max_input_channels": 0, "hostapi": 1},                 # idx3
        {"name": "Desk Speakers (Focusrite)", "max_output_channels": 2,
         "max_input_channels": 0, "hostapi": 1},                 # idx4: WASAPI twin
    ]
    _patch_out(monkeypatch, hostapis, devices)
    _set_os_default(monkeypatch, out_idx=2)  # system default = Focusrite (MME)

    # Focusrite (the OS default) beats the generic PRO X and is taken on WASAPI.
    assert pl._resolve_output_device("auto-headset") == 4


def test_output_junk_os_default_falls_back_to_headset(monkeypatch) -> None:
    """A monitor/HDMI OS default is rejected; the real headset is chosen."""
    hostapis = [{"name": "Windows WASAPI", "devices": [0, 1]}]
    devices = [
        {"name": "M28U (NVIDIA High Definition Audio)", "max_output_channels": 2,
         "max_input_channels": 0, "hostapi": 0},   # OS default = monitor via HDMI
        {"name": "Speakers (PRO X)", "max_output_channels": 2,
         "max_input_channels": 0, "hostapi": 0},
    ]
    _patch_out(monkeypatch, hostapis, devices)
    _set_os_default(monkeypatch, out_idx=0)  # junk default (blocked HDMI name)

    assert pl._resolve_output_device("auto-headset") == 1


def test_input_virtual_os_default_falls_back_to_real_mic(monkeypatch) -> None:
    """The exact maintainer case: the OS default mic is a virtual NVIDIA
    Broadcast, so auto-headset must pick the real headset mic instead of feeding
    the wake loop digital silence."""
    hostapis = [{"name": "MME", "devices": [0, 1]}]
    devices = [
        {"name": "Microphone (NVIDIA Broadcast)", "max_input_channels": 2,
         "max_output_channels": 0, "hostapi": 0},   # OS default = virtual mic
        {"name": "Microphone (PRO X)", "max_input_channels": 1,
         "max_output_channels": 0, "hostapi": 0},    # real headset mic
    ]
    _patch_in(monkeypatch, hostapis, devices)
    _set_os_default(monkeypatch, in_idx=0)  # virtual mic as system default

    assert cap._resolve_input_device("auto-headset") == 1


def test_input_real_os_default_wins_over_generic(monkeypatch) -> None:
    """A real OS-default mic beats a generic-list mic that would otherwise win."""
    hostapis = [{"name": "MME", "devices": [0, 1]}]
    devices = [
        {"name": "Microphone (PRO X)", "max_input_channels": 1,
         "max_output_channels": 0, "hostapi": 0},        # generic default winner
        {"name": "Podcast Mic (Focusrite)", "max_input_channels": 1,
         "max_output_channels": 0, "hostapi": 0},         # user's OS default
    ]
    _patch_in(monkeypatch, hostapis, devices)
    _set_os_default(monkeypatch, in_idx=1)

    assert cap._resolve_input_device("auto-headset") == 1


def test_user_priority_still_beats_os_default(monkeypatch) -> None:
    """An explicit user priority outranks even the OS-selected default."""
    hostapis = [{"name": "Windows WASAPI", "devices": [0, 1]}]
    devices = [
        {"name": "Desk Speakers (Focusrite)", "max_output_channels": 2,
         "max_input_channels": 0, "hostapi": 0},   # OS default
        {"name": "Bose QuietComfort Ultra", "max_output_channels": 2,
         "max_input_channels": 0, "hostapi": 0},    # user-named
    ]
    _patch_out(monkeypatch, hostapis, devices)
    _set_os_default(monkeypatch, out_idx=0)

    # No user priority -> the OS default (Focusrite) wins.
    assert pl._resolve_output_device("auto-headset") == 0
    # User names Bose -> it beats even the OS default.
    assert pl._resolve_output_device("auto-headset", ["Bose"]) == 1


def test_os_default_helper_rejects_junk_and_accepts_real(monkeypatch) -> None:
    """The helper itself: rejects a blocked/virtual/mapper default, accepts a
    real one — the guard that makes the fallback safe."""
    hostapis = [{"name": "MME", "devices": [0, 1, 2]}]
    devices = [
        {"name": "Microsoft Sound Mapper - Input", "max_input_channels": 2,
         "max_output_channels": 0, "hostapi": 0},                 # idx0: mapper
        {"name": "Microphone (NVIDIA Broadcast)", "max_input_channels": 2,
         "max_output_channels": 0, "hostapi": 0},                 # idx1: virtual
        {"name": "Podcast Mic (Focusrite)", "max_input_channels": 1,
         "max_output_channels": 0, "hostapi": 0},                 # idx2: real
    ]
    # idx0 mapper -> None; idx1 virtual -> None; idx2 real -> its name.
    monkeypatch.setattr(cap.sd, "default", SimpleNamespace(device=(0, -1)))
    assert cap._os_default_input_name(devices, hostapis) is None
    monkeypatch.setattr(cap.sd, "default", SimpleNamespace(device=(1, -1)))
    assert cap._os_default_input_name(devices, hostapis) is None
    monkeypatch.setattr(cap.sd, "default", SimpleNamespace(device=(2, -1)))
    assert cap._os_default_input_name(devices, hostapis) == "Podcast Mic (Focusrite)"
