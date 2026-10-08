"""Light edits of a finished screen recording: trim it and change its speed.

The video editor plays the original file and only sends the chosen range and
speed. This module writes that as a new recording next to the original (the
original is never touched), so it can be saved, dragged or opened like any
other recording. PyAV is imported lazily: a base install without the video
extra still boots, and the routes answer with a clear error instead.
"""

from __future__ import annotations

import logging
import threading
import uuid
from fractions import Fraction
from typing import Any

from jarvis.appshot.recording import recording_dir, recording_file

log = logging.getLogger(__name__)

#: Playback speeds the editor offers; anything else is refused.
SPEEDS = (0.5, 1.0, 1.5, 2.0)
#: A trim this close to the original's ends counts as "not trimmed".
_EDGE_S = 0.05

_lock = threading.Lock()
#: ``(source id, start, end, speed)`` → the clip already written for it.
_clips: dict[tuple[str, float, float, float], str] = {}


class ClipError(ValueError):
    """The edit cannot be written; the message is safe to show the user."""


def probe_duration(recording_id: str) -> float:
    """Length of a finished recording in seconds."""
    path = recording_file(recording_id)
    if path is None:
        raise FileNotFoundError(recording_id)
    import av

    with av.open(str(path)) as container:
        if container.duration:
            return container.duration / 1_000_000
        stream = container.streams.video[0]
        if stream.duration and stream.time_base:
            return float(stream.duration * stream.time_base)
    return 0.0


def export_clip(recording_id: str, start_s: float, end_s: float, speed: float) -> str:
    """Write ``[start_s, end_s]`` at ``speed`` as a new recording; returns its id.

    An unchanged range at normal speed returns ``recording_id`` itself, and an
    edit already written in this session is reused instead of encoded twice.
    """
    if speed not in SPEEDS:
        raise ClipError("This playback speed is not supported.")
    duration = probe_duration(recording_id)
    start = max(0.0, min(start_s, duration))
    end = max(0.0, min(end_s, duration))
    if end - start < 0.2:
        raise ClipError("The selected part of the video is too short.")
    if start <= _EDGE_S and end >= duration - _EDGE_S and speed == 1.0:
        return recording_id
    key = (recording_id, round(start, 2), round(end, 2), speed)
    with _lock:
        cached = _clips.get(key)
        if cached is not None and recording_file(cached) is not None:
            return cached
        clip_id = uuid.uuid4().hex
        _write_clip(recording_id, clip_id, start, end, speed)
        _clips[key] = clip_id
        return clip_id


#: Transfer characteristics of HDR video: SMPTE ST 2084 (PQ) and ARIB STD-B67 (HLG).
_HDR_TRANSFERS = (16, 18)


def _clip_encoder(av: Any, source: Any) -> tuple[str, str, dict[str, str]]:
    """An encoder that keeps the recording's colour: 10-bit for HDR, 8-bit otherwise."""
    if int(getattr(source, "color_trc", 0) or 0) in _HDR_TRANSFERS:
        for codec, pixel_format, options in (
            ("libsvtav1", "yuv420p10le", {"preset": "8", "crf": "26"}),
            ("libx265", "yuv420p10le", {"preset": "veryfast", "crf": "20"}),
            ("av1_nvenc", "p010le", {"preset": "p5", "rc": "vbr", "cq": "24"}),
            ("hevc_nvenc", "p010le", {"preset": "p5", "rc": "vbr", "cq": "22",
                                      "profile": "main10"}),
        ):
            if codec in av.codecs_available:
                return codec, pixel_format, options
    if "libx264" in av.codecs_available:
        return "libx264", "yuv420p", {"preset": "veryfast", "crf": "20"}
    return "mpeg4", "yuv420p", {}


def _copy_colour_tags(source: Any, target: Any) -> None:
    """The clip says the same colour space as its recording, so players match it."""
    for name in ("color_primaries", "color_trc", "colorspace", "color_range"):
        value = getattr(source, name, None)
        if value:
            setattr(target, name, value)


def _write_clip(source_id: str, clip_id: str, start: float, end: float, speed: float) -> None:
    import av

    source = recording_file(source_id)
    if source is None:
        raise FileNotFoundError(source_id)
    target = recording_dir() / f"{clip_id}.mp4"
    partial = target.with_suffix(".partial")
    try:
        with av.open(str(source)) as reader, av.open(str(partial), "w", format="mp4") as writer:
            video = reader.streams.video[0]
            # VFR timestamp jitter can make base_rate report 120/60000 for a
            # 60 FPS recording. Preserve the codec's declared frame rate instead.
            fps = video.codec_context.framerate or video.average_rate or Fraction(60)
            time_base = 1 / fps
            video.thread_type = "AUTO"
            codec, pixel_format, options = _clip_encoder(av, video.codec_context)
            stream = writer.add_stream(codec, rate=fps, options=options)
            stream.width = video.codec_context.width
            stream.height = video.codec_context.height
            stream.pix_fmt = pixel_format
            stream.codec_context.max_b_frames = 0
            _copy_colour_tags(video.codec_context, stream.codec_context)
            audio = None
            if reader.streams.audio:
                audio = writer.add_stream("aac", rate=48000)
                audio.layout = "stereo"
                audio.bit_rate = 192000

            def encode(frame: Any, pts: int) -> None:
                out = frame.reformat(format=pixel_format)
                out.pts = pts
                out.time_base = time_base
                for packet in stream.encode(out):
                    writer.mux(packet)

            # Seek to the keyframe before the cut, then decode up to it: the
            # frame on screen at ``start`` opens the clip.
            reader.seek(max(0, int(start / video.time_base)), stream=video, backward=True)
            held = None
            last_pts = -1
            for frame in reader.decode(video):
                if frame.pts is None:
                    continue
                at = float(frame.pts * video.time_base)
                if at <= start:
                    held = frame
                    continue
                if at > end:
                    break
                if last_pts < 0 and held is not None:
                    encode(held, 0)
                    last_pts = 0
                pts = round((at - start) / speed * fps)
                if pts <= last_pts:
                    continue  # Faster than real time: this frame is skipped.
                encode(frame, pts)
                last_pts = pts
                held = frame
            if held is None:
                raise ClipError("The selected part of the video has no frames.")
            if last_pts < 0:
                encode(held, 0)
                last_pts = 0
            # Hold the last picture until the chosen end, like the recorder does.
            final = max(0, round((end - start) / speed * fps) - 1)
            if final > last_pts:
                encode(held, final)
            for packet in stream.encode(None):
                writer.mux(packet)
            if audio is not None:
                from jarvis.appshot.recording_audio_edit import write_audio

                write_audio(source, writer, audio, start, end, speed)
        partial.replace(target)
    except (ClipError, FileNotFoundError):
        raise
    except Exception as exc:
        log.exception("appshot: the edited video could not be written")
        raise ClipError("The edited video could not be written.") from exc
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            log.warning("appshot: partial clip cleanup failed", exc_info=True)


__all__ = ["SPEEDS", "ClipError", "export_clip", "probe_duration"]
