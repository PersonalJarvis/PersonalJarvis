"""Audio device enumeration + name resolution for the Settings device pickers.

The Settings view lets the user pin which OUTPUT device Jarvis's voice plays
on and which MICROPHONE it listens with. Two building blocks live here:

- :func:`list_devices` — the picker's option list: one entry per PHYSICAL
  device. PortAudio enumerates the same endpoint once per host API
  (WASAPI/MME/DirectSound/WDM-KS on Windows), and the WMME backend truncates
  display names to ~31 characters — both artifacts are merged away so the
  user never sees "PRO X" four times. The localized MME/DirectSound virtual
  mapper and WDM-KS entries (BUG-014: PortAudio's blocking API is not
  implemented there) never appear.
- :func:`resolve_device_by_name` — turns a persisted device NAME back into a
  concrete PortAudio index at stream-open time. Names are the only stable
  identifier across reboots and hot-plugs (indices drift — the BUG-014 class
  ``_stabilize_audio_devices`` exists to fight); raw name strings handed to
  PortAudio are ambiguous across host APIs, so the lookup applies the
  direction's host-API preference (output: WASAPI first — mono routing;
  input: MME first — transparent 16 kHz resampling for the wake loop).

Everything degrades quietly: no sounddevice / no PortAudio (headless
``python:3.11-slim``) yields an empty list / ``None``, never an exception.
Nothing here runs on the boot path (AP-26) — callers are the Settings route
and the stream-open resolvers.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Type-checkers see the real module; at runtime the guarded import below
    # binds sd (or None when PortAudio/sounddevice is absent — headless/slim).
    import sounddevice as sd
else:
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001 — sounddevice/PortAudio (libportaudio2) absent
        sd = None  # type: ignore[assignment]

# Host-API policy is NOT re-declared here: the preference tables and the
# WDM-KS denylists live in the player/capture modules next to the BUG-014
# forensics that justify them, and this module imports them so a future
# host-API fix there can never silently drift apart from the picker /
# name-resolution behavior. (player/capture import THIS module only lazily
# inside their resolvers, so there is no import cycle.)
from jarvis.audio.capture import (
    _HOSTAPI_BLOCKLIST as _INPUT_FORBIDDEN_HOSTAPIS,
)
from jarvis.audio.capture import (
    _HOSTAPI_PREFERENCE as _INPUT_HOSTAPI_PREFERENCE,
)
from jarvis.audio.device_select import is_legacy_primary_mapper
from jarvis.audio.player import (
    _FORBIDDEN_OUTPUT_HOSTAPIS as _OUTPUT_FORBIDDEN_HOSTAPIS,
)
from jarvis.audio.player import (
    _HOSTAPI_PREFERENCE as _OUTPUT_HOSTAPI_PREFERENCE,
)

#: The config sentinel for "pick a device automatically" (the default in
#: ``[audio].input_device`` / ``[audio].output_device``).
AUTO_DEVICE = "auto-headset"

# PortAudio's WMME backend truncates device names (32-byte buffer → ~31
# chars). A shorter name that is a strict PREFIX of a longer one and at least
# this long is treated as that device's truncated twin, not a distinct device.
_MME_TRUNCATION_MIN = 30


@dataclass(frozen=True)
class AudioDeviceInfo:
    """One picker entry: the device's full display name + OS-default flag."""

    name: str
    is_default: bool


def _canon(s: str) -> str:
    """Canonical form for name comparison: NFC-normalized + casefolded.

    NFC guards against the same physical device enumerating under two
    Unicode spellings of a diacritic (e.g. a composed vs. decomposed "ö" in
    a localized name) between the time a name was persisted and a later
    stream-open lookup — without it the exact-match branch would miss and
    silently fall back to auto-headset.
    """
    return unicodedata.normalize("NFC", s).casefold()


def _hostapi_name(dev: dict[str, Any], hostapis: list[Any]) -> str:
    idx = dev.get("hostapi", -1)
    if 0 <= idx < len(hostapis):
        return str(hostapis[idx].get("name", ""))
    return ""


def _query_tables() -> tuple[list[Any], list[Any]] | None:
    """The (devices, hostapis) tables, or None when PortAudio is unusable."""
    if sd is None:
        return None
    try:
        return list(sd.query_devices()), list(sd.query_hostapis())
    except Exception:  # noqa: BLE001 — enumeration must never raise (headless)
        return None


def _candidates(
    devices: list[Any], hostapis: list[Any], *, output: bool
) -> list[tuple[int, dict[str, Any], str]]:
    """(index, device, hostapi_name) of every real, usable device in the
    requested direction — mapper and forbidden-host-API entries removed."""
    channel_key = "max_output_channels" if output else "max_input_channels"
    out: list[tuple[int, dict[str, Any], str]] = []
    for idx, dev in enumerate(devices):
        if dev.get(channel_key, 0) <= 0:
            continue
        if not str(dev.get("name", "")).strip():
            continue
        if is_legacy_primary_mapper(idx, hostapis, devices, output=output):
            continue
        hostapi = _hostapi_name(dev, hostapis)
        forbidden = (
            _OUTPUT_FORBIDDEN_HOSTAPIS if output else _INPUT_FORBIDDEN_HOSTAPIS
        )
        if hostapi in forbidden:
            continue
        out.append((idx, dev, hostapi))
    return out


def _hostapi_rank(hostapi: str, *, output: bool) -> int:
    table = _OUTPUT_HOSTAPI_PREFERENCE if output else _INPUT_HOSTAPI_PREFERENCE
    return table.get(hostapi, 99)


def _os_default_index(*, output: bool) -> int | None:
    try:
        idx = sd.default.device[1 if output else 0]
    except Exception:  # noqa: BLE001 — no default configured
        return None
    return idx if isinstance(idx, int) and idx >= 0 else None


def list_devices(*, output: bool) -> list[AudioDeviceInfo]:
    """One picker entry per physical device in the requested direction.

    Dedupes host-API twins by exact name, merges an MME-truncated name into
    its full-name twin, flags the OS-default endpoint and sorts it first
    (then alphabetically). Headless / no PortAudio → ``[]``.
    """
    tables = _query_tables()
    if tables is None:
        return []
    devices, hostapis = tables
    cands = _candidates(devices, hostapis, output=output)
    if not cands:
        return []

    default_idx = _os_default_index(output=output)
    default_name = ""
    if default_idx is not None and 0 <= default_idx < len(devices):
        default_name = str(devices[default_idx].get("name", ""))

    # Exact-name dedupe: keep one representative per display name (the flag
    # only needs the name; twins are equivalent for the picker).
    by_name: dict[str, str] = {}  # canonical -> display name
    for _idx, dev, _hostapi in cands:
        name = str(dev.get("name", ""))
        by_name.setdefault(_canon(name), name)

    # Truncation merge: drop a name that is a >=_MME_TRUNCATION_MIN-char
    # strict prefix of another (the WMME-truncated twin of the full name).
    names = list(by_name.values())
    merged: list[str] = []
    for name in names:
        is_truncated_twin = len(name) >= _MME_TRUNCATION_MIN and any(
            other != name and _canon(other).startswith(_canon(name))
            for other in names
        )
        if not is_truncated_twin:
            merged.append(name)

    def _is_default(name: str) -> bool:
        if not default_name:
            return False
        a, b = _canon(name), _canon(default_name)
        if a == b:
            return True
        # The default endpoint may be enumerated under its truncated MME name
        # while the picker kept the full twin (or vice versa) — but ONLY a
        # >=_MME_TRUNCATION_MIN-char prefix relation can be that truncated
        # twin (same floor as the merge above). Without the floor, a short
        # generic default like "Microphone" would also flag every DISTINCT
        # device that merely extends it ("Microphone Array (Realtek...)").
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        return len(shorter) >= _MME_TRUNCATION_MIN and longer.startswith(shorter)

    entries = [AudioDeviceInfo(name=n, is_default=_is_default(n)) for n in merged]
    entries.sort(key=lambda e: (not e.is_default, e.name.casefold()))
    return entries


def resolve_device_by_name(name: str, *, output: bool) -> int | None:
    """The PortAudio index for a persisted device NAME, or None when absent.

    Exact (case-insensitive) matches win; otherwise a prefix relation in
    either direction counts, so a full persisted name still finds its
    MME-truncated twin and a truncated persisted name its full twin. Among
    matches the direction's host-API preference decides. ``None`` for the
    :data:`AUTO_DEVICE` sentinel, an empty string, an unknown name, or a
    headless host — callers fall back to their auto-headset heuristic.
    """
    target = (name or "").strip()
    if not target or target == AUTO_DEVICE:
        return None
    tables = _query_tables()
    if tables is None:
        return None
    devices, hostapis = tables

    target_cf = _canon(target)
    # Same-device pool: exact matches plus truncation twins (the candidate is
    # a >=_MME_TRUNCATION_MIN-char prefix of the target — the WMME-truncated
    # enumeration of the SAME endpoint). Within the pool the direction's
    # host-API preference decides, so a full persisted name still lands on
    # the MME twin for capture (16 kHz wake-loop contract) and on WASAPI for
    # playback. Extensions (target is a prefix of the candidate — a truncated
    # persisted name, or a distinct longer-named product) are only consulted
    # when nothing in the same-device pool matched, so they can never steal
    # an exact hit.
    same_device: list[tuple[int, str]] = []
    extensions: list[tuple[int, str]] = []
    for idx, dev, hostapi in _candidates(devices, hostapis, output=output):
        dev_cf = _canon(str(dev.get("name", "")))
        if dev_cf == target_cf or (
            len(dev_cf) >= _MME_TRUNCATION_MIN and target_cf.startswith(dev_cf)
        ):
            same_device.append((idx, hostapi))
        elif dev_cf.startswith(target_cf):
            extensions.append((idx, hostapi))

    matches = same_device or extensions
    if not matches:
        return None
    matches.sort(key=lambda m: (_hostapi_rank(m[1], output=output), m[0]))
    return matches[0][0]
