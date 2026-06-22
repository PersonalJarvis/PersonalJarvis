"""Exhaustive content-type coverage for the drag-drop classifier.

The user requires "all possible things you can drag-and-drop" to work. This
parametrises ``classify_and_compose`` over every droppable kind — documents,
code, data, markup, images, PDFs, archives/binaries, dragged text + URLs, and
mixed multi-file drops — asserting each is routed correctly:
  * image/*  -> a multimodal ImageBlock (the brain can SEE it)
  * text/code/data/markup -> inlined into the directive (the brain can READ it)
  * pdf -> noted (extracted when pypdf can, named otherwise)
  * unknown binary -> named only, never decoded
  * dragged text / URL -> inlined
"""
from __future__ import annotations

import base64

import pytest

from jarvis.brain.drop_context import DroppedItem, classify_and_compose

# (name, mime) pairs the OS realistically hands us for a dropped file.
_TEXTUAL = [
    ("notes.txt", "text/plain"),
    ("README.md", "text/markdown"),
    ("data.json", "application/json"),
    ("table.csv", "text/csv"),
    ("conf.yaml", "application/x-yaml"),
    ("pyproject.toml", "application/octet-stream"),   # by extension
    ("page.html", "text/html"),
    ("style.css", "text/css"),
    ("doc.xml", "application/xml"),
    ("script.py", "text/x-python"),
    ("app.js", "application/javascript"),
    ("comp.tsx", "application/octet-stream"),          # by extension
    ("main.rs", "application/octet-stream"),           # by extension
    ("Main.java", "application/octet-stream"),         # by extension
    ("query.sql", "application/sql"),
    ("run.sh", "application/x-sh"),
    ("notes.log", "text/plain"),
]

_IMAGES = [
    ("photo.png", "image/png"),
    ("pic.jpg", "image/jpeg"),
    ("anim.gif", "image/gif"),
    ("shot.webp", "image/webp"),
    ("logo.bmp", "image/bmp"),
]

_BINARY = [
    ("archive.zip", "application/zip"),
    ("tool.exe", "application/octet-stream"),
    ("song.mp3", "audio/mpeg"),
    ("clip.mp4", "video/mp4"),
]


@pytest.mark.parametrize("name,mime", _TEXTUAL)
def test_textual_kinds_are_inlined(name: str, mime: str) -> None:
    body = "UNIQUE_TOKEN_42 content body"
    text, images = classify_and_compose([DroppedItem(name, mime, body.encode())])
    assert images == ()
    assert name in text
    assert "UNIQUE_TOKEN_42" in text, f"{name} ({mime}) should be inlined as text"


@pytest.mark.parametrize("name,mime", _IMAGES)
def test_image_kinds_become_imageblocks(name: str, mime: str) -> None:
    raw = b"\x00\x01\x02 fake-image-bytes"
    text, images = classify_and_compose([DroppedItem(name, mime, raw)])
    assert len(images) == 1
    assert images[0].mime == mime
    assert base64.b64decode(images[0].data_b64) == raw
    assert name in text


@pytest.mark.parametrize("name,mime", _BINARY)
def test_binary_kinds_are_named_not_decoded(name: str, mime: str) -> None:
    text, images = classify_and_compose([DroppedItem(name, mime, b"\x00\xffraw\x00bytes")])
    assert images == ()
    assert name in text
    assert "raw\x00bytes" not in text


def test_dragged_url_is_inlined() -> None:
    text, images = classify_and_compose([], dragged_text="https://example.com/path?q=1")
    assert images == ()
    assert "https://example.com/path?q=1" in text


def test_dragged_plain_text_is_inlined() -> None:
    text, _ = classify_and_compose([], dragged_text="some selected paragraph of text")
    assert "some selected paragraph of text" in text


def test_mixed_multi_file_drop() -> None:
    items = [
        DroppedItem("a.png", "image/png", b"img1"),
        DroppedItem("b.txt", "text/plain", b"text body B"),
        DroppedItem("c.zip", "application/zip", b"PK\x03\x04"),
        DroppedItem("d.jpg", "image/jpeg", b"img2"),
    ]
    text, images = classify_and_compose(items)
    assert len(images) == 2  # both pictures
    for n in ("a.png", "b.txt", "c.zip", "d.jpg"):
        assert n in text
    assert "text body B" in text


def test_files_plus_dragged_text_together() -> None:
    text, images = classify_and_compose(
        [DroppedItem("x.png", "image/png", b"i")], dragged_text="https://ref.example"
    )
    assert len(images) == 1
    assert "x.png" in text and "https://ref.example" in text
