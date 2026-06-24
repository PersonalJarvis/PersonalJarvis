"""``items_from_paths`` — read dropped file PATHS (overlay drop) into DroppedItems.

The web dock posts bytes (multipart); the native overlay drop hands file PATHS.
This shared reader normalises paths → DroppedItem(name, mime, bytes) with a total
byte cap, so both surfaces feed the same ``ingest_drop``.
"""
from __future__ import annotations

from jarvis.brain.drop_context import DroppedItem, items_from_paths


def test_reads_a_real_file(tmp_path) -> None:
    p = tmp_path / "hello.txt"
    p.write_bytes(b"hi there")
    items = items_from_paths([str(p)])
    assert items == [DroppedItem(name="hello.txt", mime="text/plain", data=b"hi there")]


def test_guesses_image_mime_from_extension(tmp_path) -> None:
    p = tmp_path / "pic.png"
    p.write_bytes(b"\x89PNGxx")
    items = items_from_paths([str(p)])
    assert len(items) == 1
    assert items[0].mime == "image/png"


def test_skips_nonexistent_paths(tmp_path) -> None:
    real = tmp_path / "a.txt"
    real.write_bytes(b"a")
    items = items_from_paths([str(tmp_path / "ghost.txt"), str(real)])
    assert [i.name for i in items] == ["a.txt"]


def test_total_byte_cap_stops_reading(tmp_path) -> None:
    a = tmp_path / "a.bin"
    a.write_bytes(b"x" * 1000)
    b = tmp_path / "b.bin"
    b.write_bytes(b"y" * 1000)
    items = items_from_paths([str(a), str(b)], max_total_bytes=1500)
    # First file fits; the second would breach the cap → not included.
    assert [i.name for i in items] == ["a.bin"]


def test_empty_paths_yield_no_items() -> None:
    assert items_from_paths([]) == []
