"""What a photo, recording or video says about itself before any model sees it.

A picture that arrives with nothing but a filename is nearly useless to a
memory system: the file's modification time is the day it was *copied*, not the
day it was taken, so a holiday album restored from a backup collapses onto one
meaningless afternoon. The camera already wrote the truth into the file — this
module reads it back.

Two jobs, both deliberately small:

* **Self-description** (:func:`media_metadata`) — capture time, camera, GPS,
  dimensions, duration. Enough that the item is findable by time and place
  *immediately*, before the enrichment stage has described anything.
* **A way back to the bytes** (:class:`MediaRef`) — a photo is stored as an
  item the moment it is seen, but describing it happens later, in a different
  process, possibly days later. The reference is how that later stage opens the
  same file again.

**Standard library only, and it never raises.** EXIF is a TIFF directory
embedded in a JPEG segment; ``struct`` reads it in eighty lines. Refusing to
add an imaging library to the base install is not frugality for its own sake —
it is what keeps ingestion working on a `python:3.11-slim` VPS with no image
stack at all (charter §3).

The trade that buys: JPEG and HEIC/AVIF are read, and every other format
returns an empty dict rather than a wrong guess. A PNG's textual chunks and a
video's duration are deliberately not parsed here — the enrichment stage's
description and transcript are the content that matters for those, and half a
metadata parser would be a claim without a source.
"""

from __future__ import annotations

import logging
import struct
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

log = logging.getLogger(__name__)

__all__ = [
    "EXIF_SCAN_BYTES",
    "MediaRef",
    "media_metadata",
    "open_media",
    "ref_from_metadata",
]

#: Bytes read looking for the EXIF block. The segment sits at the very front of
#: every camera file; anything past this is a file that has none.
_EXIF_SCAN_BYTES = 512 * 1024

#: Hard ceiling on one EXIF directory, so a malformed count field cannot walk
#: this parser through megabytes of unrelated bytes.
_MAX_EXIF_ENTRIES = 512

#: TIFF tags read out of IFD0 / the Exif sub-IFD. Everything else is ignored:
#: a knowledge base wants when, where and what took it — not the white balance.
_TAG_DATETIME_ORIGINAL = 0x9003
_TAG_DATETIME_DIGITIZED = 0x9004
_TAG_DATETIME = 0x0132
_TAG_MAKE = 0x010F
_TAG_MODEL = 0x0110
_TAG_ORIENTATION = 0x0112
_TAG_EXIF_IFD = 0x8769
_TAG_GPS_IFD = 0x8825
_TAG_IMAGE_WIDTH = 0x0100
_TAG_IMAGE_HEIGHT = 0x0101
_TAG_PIXEL_X = 0xA002
_TAG_PIXEL_Y = 0xA003

_GPS_LAT_REF = 0x0001
_GPS_LAT = 0x0002
_GPS_LON_REF = 0x0003
_GPS_LON = 0x0004

#: TIFF field type → (bytes per component, struct code). Only the types that
#: actually carry the tags above.
_FIELD_TYPES: dict[int, tuple[int, str]] = {
    1: (1, "B"),  # BYTE
    2: (1, "s"),  # ASCII
    3: (2, "H"),  # SHORT
    4: (4, "I"),  # LONG
    5: (8, ""),  # RATIONAL — two LONGs, handled separately
    7: (1, "s"),  # UNDEFINED
    9: (4, "i"),  # SLONG
    10: (8, ""),  # SRATIONAL
}


# ---------------------------------------------------------------------------
# Locating the bytes again
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MediaRef:
    """Where one media file lives, durably enough to reopen it later.

    Two shapes cover everything ingestion currently produces:

    * ``kind="file"`` — a path on disk (a photo library, a chosen folder).
    * ``kind="zip-entry"`` — one entry inside an archive that is still on disk
      (a Takeout download, a WhatsApp export with its attachments).

    A cloud object is deliberately NOT a third shape here. Re-fetching from a
    provider needs that provider's client and credentials, which belong to the
    adapter, not to this module; an adapter that wants enrichment passes its
    bytes directly instead. Inventing a half-working remote reference would
    only produce items that promise a description they can never fetch.
    """

    kind: str
    path: str
    entry: str = ""

    def as_metadata(self) -> dict[str, str]:
        """The form stored on a ``RawItem`` so a later stage can rebuild this."""
        payload = {"media_ref_kind": self.kind, "media_ref_path": self.path}
        if self.entry:
            payload["media_ref_entry"] = self.entry
        return payload

    @property
    def display_name(self) -> str:
        """The filename a person would recognise, archive entry included."""
        return Path(self.entry or self.path).name


def ref_from_metadata(metadata: dict[str, Any]) -> MediaRef | None:
    """Rebuild a :class:`MediaRef` from a stored item, or ``None``.

    Returns ``None`` rather than raising for every incomplete shape: an item
    written by an older build simply has no reference, which the caller reads
    as "cannot enrich this one" and moves on.
    """
    kind = str(metadata.get("media_ref_kind") or "")
    path = str(metadata.get("media_ref_path") or "")
    if kind not in ("file", "zip-entry") or not path:
        return None
    return MediaRef(kind=kind, path=path, entry=str(metadata.get("media_ref_entry") or ""))


class _ArchiveEntryStream:
    """An archive entry that closes its archive when the entry is closed.

    ``ZipFile.open`` hands back a stream that does NOT own the archive, so
    closing it leaves the archive's file handle open. One leaked handle per
    enriched photo would exhaust the process's descriptors partway through a
    Takeout import — on Windows it would also keep the archive locked against
    the user deleting it.
    """

    def __init__(self, archive: zipfile.ZipFile, stream: IO[bytes]) -> None:
        self._archive = archive
        self._stream = stream

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def __enter__(self) -> _ArchiveEntryStream:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def close(self) -> None:
        try:
            self._stream.close()
        finally:
            self._archive.close()


def open_media(ref: MediaRef) -> Any:
    """A fresh binary stream for ``ref``, or ``None`` when it is gone.

    Deletion between capture and enrichment is normal — a user empties their
    downloads folder, an archive is moved — so a missing file is an ordinary
    outcome the caller reports honestly, never an error that stops a queue.
    """
    try:
        if ref.kind == "file":
            return Path(ref.path).open("rb")
        if ref.kind == "zip-entry":
            archive = zipfile.ZipFile(ref.path)
            try:
                return _ArchiveEntryStream(archive, archive.open(ref.entry, "r"))
            except Exception:
                archive.close()
                raise
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        log.debug("media: %s is no longer readable (%s)", ref.path, type(exc).__name__)
    return None


# ---------------------------------------------------------------------------
# EXIF
# ---------------------------------------------------------------------------


def _exif_block(head: bytes) -> bytes:
    """The TIFF block out of a JPEG APP1 segment, or ``b""``.

    Walks the JPEG marker chain rather than searching for the string ``Exif``:
    those four bytes occur inside image data often enough that a naive search
    finds them in files that carry no EXIF at all.
    """
    if head[:2] != b"\xff\xd8":  # not a JPEG
        return b""
    offset = 2
    limit = len(head)
    while offset + 4 <= limit:
        if head[offset] != 0xFF:
            return b""
        marker = head[offset + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        if marker == 0xDA:  # start of scan: image data begins, no more headers
            return b""
        size = struct.unpack_from(">H", head, offset + 2)[0]
        if size < 2:
            return b""
        segment = head[offset + 4 : offset + 2 + size]
        if marker == 0xE1 and segment[:6] == b"Exif\x00\x00":
            return segment[6:]
        offset += 2 + size
    return b""


def _heif_exif_block(head: bytes) -> bytes:
    """The TIFF block inside an ISO-BMFF (HEIC/AVIF) file, or ``b""``.

    A full HEIF box walk would need the item-property machinery; the payload is
    still a plain TIFF header, so it is located directly. This is the one place
    a byte search is acceptable: the marker is a TIFF header immediately after
    an ``Exif`` box name, which image data does not reproduce.
    """
    for marker in (b"Exif\x00\x00II*\x00", b"Exif\x00\x00MM\x00*"):
        index = head.find(marker)
        if index >= 0:
            return head[index + 6 :]
    for tiff in (b"II*\x00", b"MM\x00*"):
        index = head.find(b"Exif" + tiff)
        if index >= 0:
            return head[index + 4 :]
    return b""


class _Tiff:
    """A minimal TIFF directory reader — bounds-checked, never raising."""

    def __init__(self, block: bytes) -> None:
        self.block = block
        self.order = ">"
        self.ok = False
        if len(block) < 8:
            return
        if block[:2] == b"II":
            self.order = "<"
        elif block[:2] == b"MM":
            self.order = ">"
        else:
            return
        if struct.unpack_from(self.order + "H", block, 2)[0] != 42:
            return
        self.first_ifd = struct.unpack_from(self.order + "I", block, 4)[0]
        self.ok = True

    def entries(self, offset: int) -> dict[int, Any]:
        """Every tag of one directory as ``{tag: value}``. Empty on anything odd."""
        found: dict[int, Any] = {}
        if not self.ok or offset <= 0 or offset + 2 > len(self.block):
            return found
        try:
            count = struct.unpack_from(self.order + "H", self.block, offset)[0]
        except struct.error:
            return found
        for index in range(min(count, _MAX_EXIF_ENTRIES)):
            base = offset + 2 + index * 12
            if base + 12 > len(self.block):
                break
            try:
                tag, field_type, length = struct.unpack_from(self.order + "HHI", self.block, base)
            except struct.error:
                break
            value = self._value(field_type, length, base + 8)
            if value is not None:
                found[tag] = value
        return found

    def _value(self, field_type: int, length: int, value_offset: int) -> Any:
        spec = _FIELD_TYPES.get(field_type)
        if spec is None or length == 0 or length > 4096:
            return None
        width, code = spec
        total = width * length
        if total > 4:
            try:
                start = struct.unpack_from(self.order + "I", self.block, value_offset)[0]
            except struct.error:
                return None
        else:
            start = value_offset
        if start < 0 or start + total > len(self.block):
            return None
        raw = self.block[start : start + total]
        if field_type in (2, 7):
            return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
        if field_type in (5, 10):
            values = []
            for step in range(length):
                try:
                    num, den = struct.unpack_from(
                        self.order + ("II" if field_type == 5 else "ii"),
                        self.block,
                        start + step * 8,
                    )
                except struct.error:
                    return None
                values.append(num / den if den else 0.0)
            return values[0] if length == 1 else values
        try:
            values = list(struct.unpack_from(self.order + code * length, self.block, start))
        except struct.error:
            return None
        return values[0] if length == 1 else values


def _exif_datetime(value: str) -> str:
    """``"2019:08:14 17:03:22"`` → ISO-8601. ``""`` when it is not a date.

    EXIF stores no time zone, so the reading is treated as UTC and the item
    says which field it came from — a claim of precision the file does not
    carry would be worse than a slightly-off hour.
    """
    text = str(value or "").strip().rstrip("\x00")
    for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
        try:
            moment = datetime.strptime(text, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    return ""


def _gps_degrees(values: Any, reference: Any) -> float | None:
    """Degrees/minutes/seconds plus a hemisphere letter as a signed float."""
    if not isinstance(values, list) or len(values) < 3:
        return None
    try:
        degrees = float(values[0]) + float(values[1]) / 60 + float(values[2]) / 3600
    except (TypeError, ValueError):
        return None
    if str(reference or "").upper().startswith(("S", "W")):
        degrees = -degrees
    return round(degrees, 6)


def _image_metadata(head: bytes) -> dict[str, Any]:
    """Capture time, camera and location out of one image's EXIF."""
    block = _exif_block(head) or _heif_exif_block(head)
    if not block:
        return {}
    tiff = _Tiff(block)
    if not tiff.ok:
        return {}
    root = tiff.entries(tiff.first_ifd)
    exif = tiff.entries(int(root.get(_TAG_EXIF_IFD) or 0))
    gps = tiff.entries(int(root.get(_TAG_GPS_IFD) or 0))

    meta: dict[str, Any] = {}
    for tag, source in (
        (_TAG_DATETIME_ORIGINAL, exif),
        (_TAG_DATETIME_DIGITIZED, exif),
        (_TAG_DATETIME, root),
    ):
        stamp = _exif_datetime(source.get(tag, ""))
        if stamp:
            meta["captured_at"] = stamp
            break

    make = str(root.get(_TAG_MAKE) or "").strip()
    model = str(root.get(_TAG_MODEL) or "").strip()
    camera = " ".join(part for part in (make, model) if part)
    if camera:
        meta["camera"] = camera[:120]
    if root.get(_TAG_ORIENTATION):
        meta["orientation"] = int(root[_TAG_ORIENTATION])

    width = exif.get(_TAG_PIXEL_X) or root.get(_TAG_IMAGE_WIDTH)
    height = exif.get(_TAG_PIXEL_Y) or root.get(_TAG_IMAGE_HEIGHT)
    if isinstance(width, int) and isinstance(height, int):
        meta["width"], meta["height"] = width, height

    latitude = _gps_degrees(gps.get(_GPS_LAT), gps.get(_GPS_LAT_REF))
    longitude = _gps_degrees(gps.get(_GPS_LON), gps.get(_GPS_LON_REF))
    if latitude is not None and longitude is not None:
        meta["latitude"], meta["longitude"] = latitude, longitude
    return meta


def media_metadata(head: bytes, kind: str) -> dict[str, Any]:
    """Everything a media file states about itself. Never raises, may be empty.

    ``head`` is the leading bytes of the file (at least a few hundred KB for a
    photo, since EXIF sits before the image data). ``kind`` is the media kind
    from :mod:`jarvis.ultrawiki.extract`.
    """
    if kind != "image":
        # Audio and video duration lives in format-specific headers that vary
        # far more than EXIF does; the transcript the enrichment stage produces
        # is the useful content, and inventing a half-right duration here would
        # be a claim without a source.
        return {}
    try:
        return _image_metadata(head)
    except Exception:  # noqa: BLE001 — a malformed camera file is not an error
        log.debug("media: EXIF could not be read", exc_info=True)
        return {}


#: Bytes a caller should hand to :func:`media_metadata` for an image.
EXIF_SCAN_BYTES = _EXIF_SCAN_BYTES
