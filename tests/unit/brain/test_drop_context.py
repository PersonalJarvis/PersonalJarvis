"""Unit tests for the drag-drop content classifier/composer (``drop_context``).

The pure ``classify_and_compose`` turns dropped OS items (files, dragged text)
into (directive_text, ImageBlocks) for a proactive ``MessageSent`` brain turn.
Images ride the multimodal path; text/code is inlined (bounded); unknown binary
is named only. See docs/superpowers/specs/2026-06-21-dragdrop-files-into-context-design.md.
"""
from __future__ import annotations

import base64

from jarvis.brain.drop_context import (
    DROP_SOURCE_LAYER,
    DroppedItem,
    classify_and_compose,
)


def test_drop_source_layer_constant() -> None:
    """The wire-format marker is stable and namespaced."""
    assert DROP_SOURCE_LAYER == "ui.drop"


def test_image_item_becomes_imageblock_and_directive_note() -> None:
    item = DroppedItem(name="photo.png", mime="image/png", data=b"\x89PNGfake-bytes")
    text, images = classify_and_compose([item])

    assert len(images) == 1
    assert images[0].mime == "image/png"
    assert base64.b64decode(images[0].data_b64) == b"\x89PNGfake-bytes"
    assert "photo.png" in text
    # The directive must instruct the model to react, not stay silent.
    assert text.strip() != ""


def test_text_item_is_inlined_into_directive() -> None:
    body = "def hello():\n    return 'hi'\n"
    item = DroppedItem(name="snippet.py", mime="text/x-python", data=body.encode())
    text, images = classify_and_compose([item])

    assert images == ()
    assert "snippet.py" in text
    assert "return 'hi'" in text


def test_text_item_classified_by_extension_when_mime_is_generic() -> None:
    """A forged/empty octet-stream MIME still inlines for a known text suffix."""
    item = DroppedItem(
        name="notes.md", mime="application/octet-stream", data=b"# Title\nhello"
    )
    text, images = classify_and_compose([item])

    assert images == ()
    assert "hello" in text


def test_pdf_without_extraction_is_noted_not_crashed() -> None:
    item = DroppedItem(name="report.pdf", mime="application/pdf", data=b"%PDF-1.4 noise")
    text, images = classify_and_compose([item])

    assert images == ()
    assert "report.pdf" in text  # named, even if content not extracted


def test_unknown_binary_is_named_only() -> None:
    item = DroppedItem(name="archive.zip", mime="application/zip", data=b"PK\x03\x04rest")
    text, images = classify_and_compose([item])

    assert images == ()
    assert "archive.zip" in text
    # Raw binary is never decoded into the directive.
    assert "PK\x03\x04" not in text


def test_empty_drop_yields_no_turn() -> None:
    text, images = classify_and_compose([], dragged_text=None)
    assert text == ""
    assert images == ()


def test_dragged_text_only_is_inlined() -> None:
    text, images = classify_and_compose([], dragged_text="https://example.com/x")
    assert images == ()
    assert "https://example.com/x" in text


def test_text_file_is_capped() -> None:
    huge = "A" * 50_000
    item = DroppedItem(name="big.txt", mime="text/plain", data=huge.encode())
    text, _images = classify_and_compose([item], max_text_chars=1000)

    assert "big.txt" in text
    # The 50k body must be truncated well below its original size.
    assert text.count("A") <= 1100


def test_oversized_image_is_capped() -> None:
    """A large dropped image is downscaled below the per-image budget so it can't
    blow the LLM API's per-image limit (spec §3 — reuse cap_image_b64)."""
    import io
    import os

    from PIL import Image

    buf = io.BytesIO()
    # Random noise so PNG cannot compress it away — a genuinely large payload.
    Image.frombytes("RGB", (1200, 1200), os.urandom(1200 * 1200 * 3)).save(
        buf, format="PNG"
    )
    big = buf.getvalue()
    assert len(big) > 60_000  # the source really is over the test budget

    item = DroppedItem(name="huge.png", mime="image/png", data=big)
    _text, images = classify_and_compose([item], max_image_bytes=50_000)

    assert len(images) == 1
    decoded = base64.b64decode(images[0].data_b64)
    # The cap engaged: the over-budget PNG was re-encoded (JPEG) and shrank.
    assert images[0].mime == "image/jpeg"
    assert len(decoded) < len(big), "capped image must be smaller than the original"


def test_small_image_within_budget_is_untouched() -> None:
    item = DroppedItem(name="tiny.png", mime="image/png", data=b"\x89PNGsmall")
    _text, images = classify_and_compose([item], max_image_bytes=10_000_000)
    assert base64.b64decode(images[0].data_b64) == b"\x89PNGsmall"


def test_multiple_items_mixed() -> None:
    items = [
        DroppedItem(name="a.png", mime="image/png", data=b"img"),
        DroppedItem(name="b.txt", mime="text/plain", data=b"plain body"),
    ]
    text, images = classify_and_compose(items)

    assert len(images) == 1
    assert "a.png" in text
    assert "b.txt" in text
    assert "plain body" in text
