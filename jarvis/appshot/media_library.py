"""One gallery for kept screenshots and already finalized screen recordings.

Videos remain in the recorder's durable directory: no copying, migration or
dependence on the last live recording session. Namespaced IDs keep video
actions separate from screenshot edits. Video packages are loaded on demand.
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass
from functools import lru_cache

from jarvis.appshot import library, recording

log = logging.getLogger(__name__)
MAX_ENTRIES = library.MAX_ENTRIES
_PREFIX = "recording_"
_FALLBACK_POSTER = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="270" viewBox="0 0 480 270">'
    b'<rect width="480" height="270" rx="8" fill="#71717a"/>'
    b'<path d="M220 105v60l50-30z" fill="white"/></svg>'
)


@dataclass(frozen=True, slots=True)
class RecordingItem(library.LibraryItem):
    duration_s: float = 0.0

    def to_json(self) -> dict[str, object]:
        return {**library.LibraryItem.to_json(self), "duration_s": self.duration_s}


@lru_cache(maxsize=512)
def _metadata(path: str, modified_ns: int, size: int) -> tuple[int, int, float]:
    del modified_ns, size  # Cache identity: replacing a file invalidates its metadata.
    try:
        import av

        with av.open(path) as container:
            stream = container.streams.video[0]
            duration = (
                float(stream.duration * stream.time_base)
                if stream.duration is not None and stream.time_base is not None
                else float(container.duration or 0) / 1_000_000
            )
            duration = max(0.0, duration) if math.isfinite(duration) else 0.0
            return stream.width, stream.height, duration
    except Exception:
        # A missing optional decoder or a damaged file must not hide the rest
        # of the gallery. The original remains available for download.
        log.debug("appshot library: recording metadata unavailable", exc_info=True)
        return 0, 0, 0.0


def _video_item(recording_id: str) -> RecordingItem | None:
    path = recording.recording_file(recording_id)
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        log.debug("appshot library: recording disappeared during listing", exc_info=True)
        return None
    if not stat.st_size:
        return None
    width, height, duration = _metadata(str(path), stat.st_mtime_ns, stat.st_size)
    return RecordingItem(
        id=f"{_PREFIX}{recording_id}", variant="original", path=path, mime="video/mp4",
        width=width, height=height, label="Screen recording", app_name="", trigger="recording",
        taken_at=stat.st_mtime, edited_at=0.0, has_edit=False, duration_s=duration,
    )


def list_items() -> list[library.LibraryItem]:
    items = library.list_items()
    for path in recording.recording_dir().glob("*.mp4"):
        item = _video_item(path.stem)
        if item is not None:
            items.append(item)
    # Stable sort retains each screenshot's edited/original ordering.
    return sorted(items, key=lambda item: item.taken_at, reverse=True)


def get_item(item_id: str, variant: library.Variant) -> library.LibraryItem | None:
    if item_id.startswith(_PREFIX):
        return _video_item(item_id[len(_PREFIX):]) if variant == "original" else None
    return library.get_item(item_id, variant)


@lru_cache(maxsize=64)
def _poster(path: str, modified_ns: int, size: int) -> tuple[bytes, str]:
    del modified_ns, size
    try:
        import av

        with av.open(path) as container:
            stream = container.streams.video[0]
            stream.codec_context.thread_count = 1
            frame = next(container.decode(stream))
            ratio = min(1.0, library.THUMB_EDGE / max(frame.width, frame.height))
            frame = frame.reformat(
                width=max(1, round(frame.width * ratio)),
                height=max(1, round(frame.height * ratio)),
                format="rgb24",
            )
            out = io.BytesIO()
            frame.to_image().save(out, "JPEG", quality=82)
            return out.getvalue(), "image/jpeg"
    except Exception:
        log.debug("appshot library: recording preview unavailable", exc_info=True)
        return _FALLBACK_POSTER, "image/svg+xml"


def thumbnail(item: library.LibraryItem) -> tuple[bytes, str]:
    if item.mime != "video/mp4":
        return library.thumbnail(item)
    stat = item.path.stat()
    return _poster(str(item.path), stat.st_mtime_ns, stat.st_size)


def delete(item_id: str, variant: library.Variant) -> bool:
    if not item_id.startswith(_PREFIX):
        return library.delete(item_id, variant)
    if variant != "original":
        return False
    path = recording.recording_file(item_id[len(_PREFIX):])
    if path is None:
        return False
    try:
        path.unlink()
    except FileNotFoundError:  # Deleted between resolution and removal.
        return False
    _metadata.cache_clear()
    _poster.cache_clear()
    return True


def clear() -> int:
    removed = library.clear()
    # Only finalized, valid recorder IDs; partial files and foreign entries stay.
    for path in recording.recording_dir().glob("*.mp4"):
        removed += int(delete(f"{_PREFIX}{path.stem}", "original"))
    return removed
