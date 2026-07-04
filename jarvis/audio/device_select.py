"""Locale-independent audio-device classification shared by the output player
(:mod:`jarvis.audio.player`) and the microphone capture
(:mod:`jarvis.audio.capture`) auto-selection resolvers.

The one piece of device selection that used to depend on a *localized* Windows
display name lived in the two blocklists: the MME "Microsoft Sound Mapper" and
the DirectSound "Primary Sound Driver" virtual routing devices. Their display
name is translated by Windows into each UI language, so a fixed localized
substring only masked the mapper on one UI language and let it through on every
other locale.

They are, however, reliably identifiable **structurally**, without their name:
PortAudio's WMME and DirectSound backends always enumerate their virtual
mapper / primary device as the FIRST direction-matching entry of the host
API's device list, before any real endpoint (a documented enumeration
invariant on Windows). :func:`is_legacy_primary_mapper` uses exactly that, so
the auto-selection never routes to the OS-default sink that ``auto-headset``
exists to bypass — on any Windows UI language.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# Host APIs whose PortAudio backend prepends a virtual routing device
# ("Microsoft Sound Mapper - Output/Input" on MME, "Primary Sound Driver" on
# DirectSound) to their device list. WASAPI and WDM-KS expose no such mapper.
_LEGACY_MAPPER_HOSTAPIS: frozenset[str] = frozenset({"MME", "Windows DirectSound"})


def is_legacy_primary_mapper(
    device_index: int,
    hostapis: Sequence[Any],
    devices: Sequence[Any],
    *,
    output: bool,
) -> bool:
    """Return True if ``device_index`` is the MME/DirectSound virtual mapper.

    Locale-independent replacement for matching a translated display name
    (localized by Windows per UI language). ``output`` selects
    the direction: the output mapper is the first *output-capable* device of an
    MME/DirectSound host API, the input mapper the first *input-capable* one.

    Fails safe: any partial device table (e.g. a test fake whose ``hostapis``
    entries carry no ``devices`` member list) returns False, so a device is
    never mis-classified as a mapper on incomplete data.
    """
    if not (0 <= device_index < len(devices)):
        return False
    dev = devices[device_index]
    hostapi_idx = _get(dev, "hostapi", -1)
    if not (0 <= hostapi_idx < len(hostapis)):
        return False
    hostapi = hostapis[hostapi_idx]
    if _get(hostapi, "name", "") not in _LEGACY_MAPPER_HOSTAPIS:
        return False
    member_indices = _get(hostapi, "devices", None)
    if not member_indices:
        # No membership list (partial/fake table) — cannot classify structurally.
        return False
    channel_key = "max_output_channels" if output else "max_input_channels"
    for member_idx in member_indices:
        if not (0 <= member_idx < len(devices)):
            continue
        if _get(devices[member_idx], channel_key, 0) > 0:
            # First direction-matching device of the host API == the mapper.
            return member_idx == device_index
    return False


def _get(obj: Any, key: str, default: Any) -> Any:
    """Read ``key`` from a sounddevice mapping-like object (dict or DeviceList
    entry), falling back to ``default`` when absent."""
    try:
        return obj.get(key, default)  # type: ignore[no-any-return]
    except AttributeError:
        return getattr(obj, key, default)
