"""Open a usable recording encoder, with software fallback before any packets."""

from __future__ import annotations

import logging
from fractions import Fraction
from typing import Any

log = logging.getLogger(__name__)
# Fine wall-clock timestamps avoid dropping valid frames when GUI ticks jitter.
# 60000 is divisible by 30/60/120 and within MPEG-4's time-base denominator limit.
VIDEO_TIME_BASE = Fraction(1, 60000)


def open_video(
    av: Any, path: Any, size: tuple[int, int], fps: int, bitrate: int, *, hdr: bool = False
):
    """Codec presence alone is insufficient: open it with the actual source size.

    ``hdr`` opens a 10-bit BT.2020 PQ encoder for P010 frames
    (:mod:`jarvis.appshot.hdr_recording`); otherwise an 8-bit one for sRGB.
    """
    if hdr:
        from jarvis.appshot.hdr_recording import HDR_CANDIDATES, tag_hdr10

        return _open_first(av, path, size, fps, bitrate, HDR_CANDIDATES, tag_hdr10)
    return _open_first(av, path, size, fps, bitrate, _SDR_CANDIDATES, _tag_srgb)


def _tag_srgb(context: Any) -> None:
    """Screen pixels are sRGB: say so, so players do not guess another space.

    The matrix stays as each encoder sets it (NVENC signals the BT.601
    conversion it applies to RGB input; swscale converts with BT.601 too).
    """
    context.color_primaries = 1  # BT.709 = sRGB primaries
    context.color_trc = 13  # IEC 61966-2-1 (sRGB)
    context.color_range = 1  # limited, what every 8-bit path here writes
    if getattr(context, "colorspace", 2) in (0, 2):  # RGB / unspecified
        context.colorspace = 5  # BT.470BG = the BT.601 matrix swscale uses


_SDR_CANDIDATES = (
    ("h264_nvenc", "rgba", {"preset": "p1", "tune": "ull", "rc": "vbr"}),
    ("h264_amf", "rgba", {"quality": "speed", "rc": "vbr_peak"}),
    ("h264_qsv", "nv12", {"preset": "veryfast"}),
    ("h264_videotoolbox", "yuv420p", {"realtime": "1"}),
    ("libx264", "yuv420p", {"preset": "ultrafast", "tune": "zerolatency"}),
    ("mpeg4", "yuv420p", {}),
)


def _open_first(
    av: Any, path: Any, size: tuple[int, int], fps: int, bitrate: int, candidates: Any, tag: Any
):
    for codec, pixel_format, options in candidates:
        if codec not in av.codecs_available:
            continue
        container = av.open(str(path), "w", format="mp4")
        try:
            stream = container.add_stream(codec, rate=fps, options=options)
            stream.width, stream.height = size
            stream.pix_fmt = pixel_format
            stream.bit_rate = bitrate
            stream.codec_context.time_base = VIDEO_TIME_BASE
            stream.codec_context.thread_count = 2
            stream.codec_context.max_b_frames = 0
            tag(stream.codec_context)
            stream.codec_context.open()
            return container, stream
        except Exception:
            # Missing drivers, exhausted hardware sessions, or unsupported sizes.
            # Retry only before capture writes a header or any audio/video packet.
            log.debug("appshot: encoder %s unavailable; trying the next codec", codec,
                      exc_info=True)
            container.close()
    raise RuntimeError("No video encoder could be opened for this recording.")
