"""What a photo says about itself, read without an imaging library.

The reason this is worth a stdlib parser: a file's modification time is the day
it was *copied*, so a holiday album restored from a backup collapses onto one
meaningless afternoon and a search for "summer 2019" finds nothing. The camera
wrote the real date into the file; these tests pin that it comes back out — on
a `python:3.11-slim` container with no Pillow, no GPU and no image stack.

Every fixture is assembled byte by byte for the same reason: a binary JPEG
checked into the repo would be one more thing that behaves differently on
another platform.
"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

from jarvis.ultrawiki.extract import extract_text
from jarvis.ultrawiki.media import (
    MediaRef,
    media_metadata,
    open_media,
    ref_from_metadata,
)

# ---------------------------------------------------------------------------
# A tiny TIFF/EXIF writer — the inverse of the parser under test
# ---------------------------------------------------------------------------

_ASCII, _SHORT, _LONG, _RATIONAL = 2, 3, 4, 5


class _TiffBuilder:
    """Assembles a little-endian TIFF block with IFD0, Exif and GPS sub-IFDs.

    Offsets are computed rather than hand-counted so a new tag in one test
    cannot silently corrupt the layout of another.
    """

    def __init__(self) -> None:
        self.ifd0: list[tuple[int, int, object]] = []
        self.exif: list[tuple[int, int, object]] = []
        self.gps: list[tuple[int, int, object]] = []

    def add(self, where: str, tag: int, field_type: int, value: object) -> None:
        getattr(self, where).append((tag, field_type, value))

    def _encode(self, field_type: int, value: object) -> tuple[int, bytes]:
        if field_type == _ASCII:
            raw = str(value).encode("ascii") + b"\x00"
            return len(raw), raw
        if field_type == _SHORT:
            return 1, struct.pack("<H", int(value)) + b"\x00\x00"
        if field_type == _LONG:
            return 1, struct.pack("<I", int(value))
        if field_type == _RATIONAL:
            parts = list(value)  # type: ignore[arg-type]
            return len(parts), b"".join(
                struct.pack("<II", int(round(part * 10000)), 10000) for part in parts
            )
        raise AssertionError(f"unsupported field type {field_type}")

    def _ifd(self, entries: list[tuple[int, int, object]], start: int) -> tuple[bytes, bytes]:
        """One directory plus its overflow area, laid out from ``start``."""
        directory_size = 2 + 12 * len(entries) + 4
        overflow = bytearray()
        body = bytearray(struct.pack("<H", len(entries)))
        for tag, field_type, value in sorted(entries, key=lambda item: item[0]):
            count, raw = self._encode(field_type, value)
            if len(raw) <= 4:
                payload = raw.ljust(4, b"\x00")
            else:
                payload = struct.pack("<I", start + directory_size + len(overflow))
                overflow += raw
            body += struct.pack("<HHI", tag, field_type, count) + payload
        body += struct.pack("<I", 0)
        return bytes(body), bytes(overflow)

    def build(self) -> bytes:
        header = b"II" + struct.pack("<HI", 42, 8)
        # Sub-IFDs are laid out after IFD0, so their offsets must be known
        # before IFD0 is encoded: measure IFD0 with placeholder pointers first.
        probe = list(self.ifd0)
        if self.exif:
            probe.append((0x8769, _LONG, 0))
        if self.gps:
            probe.append((0x8825, _LONG, 0))
        ifd0_body, ifd0_overflow = self._ifd(probe, 8)
        cursor = 8 + len(ifd0_body) + len(ifd0_overflow)

        entries = list(self.ifd0)
        exif_block = gps_block = b""
        if self.exif:
            body, overflow = self._ifd(self.exif, cursor)
            exif_block = body + overflow
            entries.append((0x8769, _LONG, cursor))
            cursor += len(exif_block)
        if self.gps:
            body, overflow = self._ifd(self.gps, cursor)
            gps_block = body + overflow
            entries.append((0x8825, _LONG, cursor))
            cursor += len(gps_block)

        ifd0_body, ifd0_overflow = self._ifd(entries, 8)
        return header + ifd0_body + ifd0_overflow + exif_block + gps_block


def _jpeg_with_exif(tiff: bytes) -> bytes:
    """A JPEG whose APP1 segment carries ``tiff``, followed by fake scan data."""
    payload = b"Exif\x00\x00" + tiff
    segment = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return b"\xff\xd8" + segment + b"\xff\xda\x00\x08" + b"\x00" * 64


def _photo(**fields: object) -> bytes:
    builder = _TiffBuilder()
    if "camera" in fields:
        make, _, model = str(fields["camera"]).partition(" ")
        builder.add("ifd0", 0x010F, _ASCII, make)
        builder.add("ifd0", 0x0110, _ASCII, model)
    if "taken" in fields:
        builder.add("exif", 0x9003, _ASCII, fields["taken"])
    if "gps" in fields:
        latitude, longitude = fields["gps"]  # type: ignore[misc]
        builder.add("gps", 0x0001, _ASCII, "N" if latitude >= 0 else "S")
        builder.add("gps", 0x0002, _RATIONAL, _dms(abs(latitude)))
        builder.add("gps", 0x0003, _ASCII, "E" if longitude >= 0 else "W")
        builder.add("gps", 0x0004, _RATIONAL, _dms(abs(longitude)))
    return _jpeg_with_exif(builder.build())


def _dms(value: float) -> list[float]:
    degrees = int(value)
    minutes_full = (value - degrees) * 60
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60
    return [float(degrees), float(minutes), round(seconds, 4)]


# ---------------------------------------------------------------------------
# Capture time — the reason this module exists
# ---------------------------------------------------------------------------


def test_the_capture_date_survives_being_copied():
    """The camera's date, not the filesystem's."""
    meta = media_metadata(_photo(taken="2019:08:14 17:03:22"), "image")
    assert meta["captured_at"] == "2019-08-14T17:03:22Z"


def test_the_camera_is_named_when_the_file_says_so():
    meta = media_metadata(_photo(camera="Apple iPhone15,3", taken="2024:01:02 03:04:05"), "image")
    assert meta["camera"] == "Apple iPhone15,3"
    assert meta["captured_at"] == "2024-01-02T03:04:05Z"


def test_where_the_photo_was_taken_comes_back_as_signed_degrees():
    """Hemisphere letters, not negative numbers, is how EXIF stores it."""
    meta = media_metadata(_photo(taken="2020:05:01 12:00:00", gps=(48.8584, 2.2945)), "image")
    assert abs(meta["latitude"] - 48.8584) < 0.001
    assert abs(meta["longitude"] - 2.2945) < 0.001


def test_a_southern_western_location_is_negative():
    meta = media_metadata(_photo(taken="2020:05:01 12:00:00", gps=(-22.9519, -43.2105)), "image")
    assert meta["latitude"] < 0
    assert meta["longitude"] < 0


# ---------------------------------------------------------------------------
# Never raising is a hard requirement: this runs inside an import walk
# ---------------------------------------------------------------------------


def test_a_photo_without_exif_yields_nothing_rather_than_failing():
    assert media_metadata(b"\xff\xd8\xff\xdb\x00C\x00" + b"\x00" * 100, "image") == {}


def test_truncated_and_garbage_exif_is_survived():
    """A partially downloaded photo is a normal thing to find in a folder."""
    full = _photo(taken="2019:08:14 17:03:22", gps=(1.0, 2.0))
    for cut in (8, 20, 40, 64, len(full) // 2, len(full) - 3):
        assert isinstance(media_metadata(full[:cut], "image"), dict)
    garbage = b"\xff\xd8\xff\xe1\xff\xffExif\x00\x00II*\x00\xff\xff\xff\xff"
    assert media_metadata(garbage, "image") == {}


def test_a_declared_entry_count_far_past_the_data_cannot_walk_off_the_end():
    """A malformed count is the one field that could turn this into a scanner."""
    tiff = bytearray(_photo(taken="2019:08:14 17:03:22"))
    index = tiff.find(b"II*\x00")
    struct.pack_into("<H", tiff, index + 8, 60000)  # IFD0 claims 60k entries
    assert isinstance(media_metadata(bytes(tiff), "image"), dict)


def test_sound_and_video_report_nothing_rather_than_guessing():
    """Duration lives in format-specific headers; a guess would be a claim
    without a source, and the transcript is the content that matters."""
    assert media_metadata(b"OggS\x00\x02", "audio") == {}
    assert media_metadata(b"\x00\x00\x00\x18ftypisom", "video") == {}


# ---------------------------------------------------------------------------
# The extractor hands the metadata through
# ---------------------------------------------------------------------------


def test_extract_text_attaches_the_capture_date_to_a_photo():
    """A picture is findable by WHEN long before any model describes it."""
    result = extract_text(_photo(taken="2021:12:24 18:30:00"), filename="IMG_1.jpg")
    assert result.media_kind == "image"
    assert result.ok is False
    assert result.meta["captured_at"] == "2021-12-24T18:30:00Z"


# ---------------------------------------------------------------------------
# Getting back to the bytes later
# ---------------------------------------------------------------------------


def test_a_reference_survives_a_round_trip_through_item_metadata():
    ref = MediaRef(kind="zip-entry", path="C:/take.zip", entry="Photos/a.jpg")
    restored = ref_from_metadata(dict(ref.as_metadata()))
    assert restored == ref
    assert restored.display_name == "a.jpg"


def test_an_item_written_before_references_existed_simply_has_none():
    """Older items must read as "cannot enrich", never as a crash."""
    assert ref_from_metadata({}) is None
    assert ref_from_metadata({"media_ref_kind": "cloud", "media_ref_path": "x"}) is None
    assert ref_from_metadata({"media_ref_kind": "file"}) is None


def test_a_file_that_was_deleted_between_capture_and_enrichment_is_not_an_error(tmp_path: Path):
    assert open_media(MediaRef(kind="file", path=str(tmp_path / "gone.jpg"))) is None


def test_a_photo_inside_an_archive_is_reopened_and_the_archive_is_released(tmp_path: Path):
    """The handle matters: one leak per photo exhausts descriptors partway
    through a Takeout import, and on Windows it locks the archive."""
    archive_path = tmp_path / "take.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Photos/a.jpg", b"\xff\xd8payload")
    archive_path.write_bytes(buffer.getvalue())

    stream = open_media(MediaRef(kind="zip-entry", path=str(archive_path), entry="Photos/a.jpg"))
    assert stream is not None
    with stream:
        assert stream.read() == b"\xff\xd8payload"
    # The archive is closed, so the file can be replaced — which is what a
    # leaked handle would prevent on Windows.
    archive_path.write_bytes(b"replaced")


def test_a_missing_entry_inside_a_real_archive_is_not_an_error(tmp_path: Path):
    archive_path = tmp_path / "take.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Photos/a.jpg", b"x")
    archive_path.write_bytes(buffer.getvalue())
    assert open_media(MediaRef(kind="zip-entry", path=str(archive_path), entry="nope.jpg")) is None
