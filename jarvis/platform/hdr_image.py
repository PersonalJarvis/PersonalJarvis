"""Colour-exact still images: HDR PNG masters and their SDR stand-ins.

A full-depth capture arrives as scRGB — linear light, BT.709 primaries,
1.0 = 80 nits, values above 1 for highlights and below 0 for colours outside
sRGB. Two things are made from it:

* the **master**, lossless: a 16-bit PNG in BT.2020 primaries with the PQ
  transfer (HDR10's signal), tagged with a ``cICP`` chunk (PNG, third
  edition) and a ``cLLi`` chunk with the content's light levels. SDR white
  is placed at PQ's reference white, 203 nits (ITU-R BT.2408), because that
  is the level every PQ viewer shows at the viewing screen's own SDR white;
  highlights keep their ratio to it. Writing the monitor's absolute SDR white
  instead (e.g. 284 nits) makes white pages glare 1.4x above SDR white.
* the **SDR stand-in**, 8-bit sRGB, for everything that cannot take HDR — the
  model's image, the clipboard, chat. SDR white maps to sRGB white, so normal
  UI looks the same as on screen; only highlights above SDR white clip.

SDR captures keep 8 bits and gain the monitor's ICC profile instead
(:func:`encode_png8`), which is the exact description of their pixels.

Pure numpy + zlib (+ Pillow for 8-bit); no platform code.
"""

from __future__ import annotations

import functools
import struct
import zlib
from typing import Any

#: BT.709 linear -> BT.2020 linear (both D65), ITU-R BT.2087.
_BT709_TO_BT2020 = (
    (0.627404, 0.329283, 0.043313),
    (0.069097, 0.919540, 0.011362),
    (0.016391, 0.088013, 0.895595),
)
_PQ_M1, _PQ_M2 = 0.1593017578125, 78.84375
_PQ_C1, _PQ_C2, _PQ_C3 = 0.8359375, 18.8515625, 18.6875
#: scRGB 1.0 in nits.
SCRGB_NITS = 80.0
#: Where SDR white sits in a PQ signal (ITU-R BT.2408 "graphics white").
PQ_REFERENCE_WHITE_NITS = 203.0
#: cICP: BT.2020 primaries, PQ transfer, RGB (identity matrix), full range.
CICP_BT2100_PQ = (9, 16, 0, 1)


def pq_encode(nits: Any) -> Any:
    """SMPTE ST 2084: absolute luminance (cd/m²) -> PQ signal 0..1."""
    import numpy as np  # noqa: PLC0415

    y = np.clip(np.asarray(nits, dtype=np.float32) / 10000.0, 0.0, 1.0)
    p = np.power(y, _PQ_M1)
    return np.power((_PQ_C1 + _PQ_C2 * p) / (1.0 + _PQ_C3 * p), _PQ_M2)


def pq_decode(signal: Any) -> Any:
    """PQ signal 0..1 -> absolute luminance in cd/m²."""
    import numpy as np  # noqa: PLC0415

    e = np.power(np.clip(np.asarray(signal, dtype=np.float32), 0.0, 1.0), 1.0 / _PQ_M2)
    return 10000.0 * np.power(np.maximum(e - _PQ_C1, 0.0) / (_PQ_C2 - _PQ_C3 * e), 1.0 / _PQ_M1)


def scrgb_to_bt2020_nits(scrgb: Any) -> Any:
    """scRGB ``(h, w, 3|4)`` -> linear BT.2020 in nits, ``float32 (h, w, 3)``."""
    import numpy as np  # noqa: PLC0415

    rgb = np.asarray(scrgb)[..., :3].astype(np.float32)
    matrix = np.asarray(_BT709_TO_BT2020, dtype=np.float32) * SCRGB_NITS
    out = rgb @ matrix.T
    np.maximum(out, 0.0, out=out)  # BT.2020 holds every visible scRGB colour
    return out


def _reference_scale(sdr_white_nits: float) -> float:
    """Factor that moves the capture's SDR white onto PQ reference white."""
    return PQ_REFERENCE_WHITE_NITS / max(1.0, float(sdr_white_nits))


def scrgb_to_pq16(scrgb: Any, sdr_white_nits: float = PQ_REFERENCE_WHITE_NITS) -> Any:
    """scRGB -> BT.2020 PQ, ``uint16 (h, w, 3)`` full range — the PNG master's pixels.

    ``sdr_white_nits`` is the monitor's SDR white when the capture was taken;
    it lands at :data:`PQ_REFERENCE_WHITE_NITS`.
    """
    import numpy as np  # noqa: PLC0415

    nits = scrgb_to_bt2020_nits(scrgb)
    nits *= _reference_scale(sdr_white_nits)
    signal = pq_encode(nits)
    return np.rint(signal * 65535.0).astype(np.uint16)


def light_levels(
    scrgb: Any, sdr_white_nits: float = PQ_REFERENCE_WHITE_NITS
) -> tuple[float, float]:
    """``(MaxCLL, MaxFALL)`` in nits of the PQ master (see :func:`scrgb_to_pq16`)."""
    nits = scrgb_to_bt2020_nits(scrgb)
    nits *= _reference_scale(sdr_white_nits)
    peak = nits.max(axis=-1)
    return float(peak.max(initial=0.0)), float(peak.mean()) if peak.size else 0.0


def scrgb_to_srgb8(scrgb: Any, sdr_white_nits: float = SCRGB_NITS) -> Any:
    """scRGB -> 8-bit sRGB ``(h, w, 3)``, SDR white at full white, highlights clipped."""
    import numpy as np  # noqa: PLC0415

    source = np.asarray(scrgb)
    if source.dtype == np.float16:
        # Desktop Duplication hands over float16: one table lookup per value
        # gives the exact result of the float path ~10x faster (a 4K frame in
        # well under 100 ms, fast enough to freeze the screen for the picker).
        table = _srgb8_table(round(float(sdr_white_nits), 3))
        return table[source[..., :3].view(np.uint16)]
    return _scrgb_to_srgb8_float(source, sdr_white_nits)


@functools.lru_cache(maxsize=4)
def _srgb8_table(sdr_white_nits: float) -> Any:
    """``uint8[65536]``: every float16 bit pattern -> its 8-bit sRGB value."""
    import numpy as np  # noqa: PLC0415

    values = np.arange(65536, dtype=np.uint32).astype(np.uint16).view(np.float16)
    values = np.nan_to_num(values.astype(np.float32), nan=0.0, posinf=1e4, neginf=0.0)
    return _scrgb_to_srgb8_float(values, sdr_white_nits, channels=False)


def _scrgb_to_srgb8_float(scrgb: Any, sdr_white_nits: float, *, channels: bool = True) -> Any:
    import numpy as np  # noqa: PLC0415

    rgb = np.asarray(scrgb)
    rgb = (rgb[..., :3] if channels else rgb).astype(np.float32)
    rgb *= SCRGB_NITS / max(1.0, float(sdr_white_nits))
    np.clip(rgb, 0.0, 1.0, out=rgb)
    low = rgb * 12.92
    high = 1.055 * np.power(rgb, 1.0 / 2.4) - 0.055
    out = np.where(rgb <= 0.0031308, low, high)
    return np.rint(out * 255.0).astype(np.uint8)


def srgb8_to_scrgb(rgb8: Any, sdr_white_nits: float = SCRGB_NITS) -> Any:
    """8-bit sRGB -> scRGB at the given SDR white (for drawing on an HDR master)."""
    import numpy as np  # noqa: PLC0415

    c = np.asarray(rgb8)[..., :3].astype(np.float32) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, np.power((c + 0.055) / 1.055, 2.4))
    return lin * (float(sdr_white_nits) / SCRGB_NITS)


def composite_over_scrgb(base: Any, overlay_rgba8: Any, sdr_white_nits: float) -> Any:
    """Lay an 8-bit sRGB RGBA drawing (markings) over an scRGB image, in linear light.

    The drawing shows at SDR white, as UI on the HDR desktop does.
    """
    import numpy as np  # noqa: PLC0415

    out = np.asarray(base)[..., :3].astype(np.float32)
    overlay = np.asarray(overlay_rgba8)
    alpha = overlay[..., 3:4].astype(np.float32) / 255.0
    mask = alpha[..., 0] > 0
    if mask.any():
        colour = srgb8_to_scrgb(overlay[mask], sdr_white_nits)
        a = alpha[mask]
        out[mask] = out[mask] * (1.0 - a) + colour * a
    return out


# ---------------------------------------------------------------------------
# PNG writing
# ---------------------------------------------------------------------------


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _scanlines_up(raw: Any) -> bytes:
    """PNG rows with the "Up" filter: each byte minus the byte above (screens are flat)."""
    import numpy as np  # noqa: PLC0415

    rows = np.ascontiguousarray(raw).reshape(raw.shape[0], -1)
    filtered = np.empty_like(rows)
    filtered[0] = rows[0]
    np.subtract(rows[1:], rows[:-1], out=filtered[1:])
    lines = np.empty((rows.shape[0], rows.shape[1] + 1), dtype=np.uint8)
    lines[:, 0] = 2
    lines[0, 0] = 0
    lines[:, 1:] = filtered
    return lines.tobytes()


def encode_png16(
    rgb16: Any,
    *,
    cicp: tuple[int, int, int, int] = CICP_BT2100_PQ,
    light: tuple[float, float] | None = None,
    level: int = 6,
) -> bytes:
    """A 16-bit RGB PNG with ``cICP`` (and ``cLLi`` when ``light`` is given)."""
    import numpy as np  # noqa: PLC0415

    pixels = np.asarray(rgb16, dtype=np.uint16)
    height, width = pixels.shape[:2]
    big_endian = pixels.astype(">u2").view(np.uint8).reshape(height, width * 6)
    header = struct.pack(">IIBBBBB", width, height, 16, 2, 0, 0, 0)
    chunks = [_chunk(b"IHDR", header), _chunk(b"cICP", bytes(cicp))]
    if light is not None:
        max_cll, max_fall = (max(0, min(0xFFFFFFFF, round(v * 10000))) for v in light)
        chunks.append(_chunk(b"cLLi", struct.pack(">II", max_cll, max_fall)))
    data = zlib.compress(_scanlines_up(big_endian), level)
    chunks.append(_chunk(b"IDAT", data))
    chunks.append(_chunk(b"IEND", b""))
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def encode_png8(rgb8: Any, icc_profile: bytes | None = None) -> bytes:
    """An 8-bit RGB PNG carrying the monitor's ICC profile when there is one."""
    import io  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    image = rgb8 if isinstance(rgb8, Image.Image) else Image.fromarray(rgb8)
    buffer = io.BytesIO()
    options: dict[str, Any] = {"format": "PNG", "compress_level": 6}
    if icc_profile:
        options["icc_profile"] = icc_profile
    image.save(buffer, **options)
    return buffer.getvalue()


def read_png_cicp(data: bytes) -> tuple[int, int, int, int] | None:
    """The ``cICP`` tag of a PNG, or ``None`` (tests and the library use this)."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    pos = 8
    while pos + 8 <= len(data):
        (length,) = struct.unpack_from(">I", data, pos)
        kind = data[pos + 4 : pos + 8]
        if kind == b"cICP" and length == 4:
            return tuple(data[pos + 8 : pos + 12])  # type: ignore[return-value]
        if kind == b"IDAT":
            return None
        pos += 12 + length
    return None
