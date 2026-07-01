"""Multimodal encoding tests for brain providers (wave-1 B3).

Tests that BrainMessage.images is correctly translated into the
respective provider API format:
- Anthropic: `{"type": "image", "source": {"type": "base64", ...}}`
- Gemini:    `{"inline_data": {"mime_type": ..., "data": ...}}`
- OpenAI:    `{"type": "image_url", "image_url": {"url": "data:<mime>;base64,..."}}`

Backwards compat: messages without images must behave identically to
before wave-1 B3 (string content stays a string).
"""
from __future__ import annotations

import pytest

from jarvis.core.protocols import BrainMessage, ImageBlock


@pytest.fixture
def sample_block() -> ImageBlock:
    return ImageBlock(mime="image/png", data_b64="AAAA", source_hash="abc")


def test_anthropic_encodes_image_as_base64_source(sample_block):
    from jarvis.plugins.brain._anthropic_base import _to_anthropic_messages

    msgs = (BrainMessage(role="user", content="what is that?", images=(sample_block,)),)
    out = _to_anthropic_messages(msgs)
    assert len(out) == 1
    content = out[0]["content"]
    assert isinstance(content, list), f"expected a block list, got {type(content)}"
    # A text block must be present
    text_block = next((b for b in content if b.get("type") == "text"), None)
    assert text_block is not None, f"no text block in {content}"
    assert text_block["text"] == "what is that?"
    # The image block must be correctly encoded
    img = next(b for b in content if b.get("type") == "image")
    assert img["source"]["type"] == "base64"
    assert img["source"]["media_type"] == "image/png"
    assert img["source"]["data"] == "AAAA"


def test_anthropic_no_images_passes_string_through():
    from jarvis.plugins.brain._anthropic_base import _to_anthropic_messages

    msgs = (BrainMessage(role="user", content="hi"),)
    out = _to_anthropic_messages(msgs)
    # Backwards compat: with no images, content stays a string
    assert out[0]["content"] == "hi"


def test_gemini_encodes_image_as_inline_data(sample_block):
    from jarvis.plugins.brain.gemini import _to_gemini_contents

    msgs = (BrainMessage(role="user", content="question", images=(sample_block,)),)
    contents = _to_gemini_contents(msgs)
    assert len(contents) == 1
    parts = contents[0]["parts"]
    # Text part + inline_data part
    text_part = next((p for p in parts if isinstance(p, dict) and "text" in p), None)
    assert text_part is not None and text_part["text"] == "question"
    inline = next((p for p in parts if isinstance(p, dict) and "inline_data" in p), None)
    assert inline is not None, f"no inline_data in parts: {parts}"
    assert inline["inline_data"]["mime_type"] == "image/png"
    assert inline["inline_data"]["data"] == "AAAA"


def test_openai_encodes_image_as_image_url(sample_block):
    from jarvis.plugins.brain._openai_base import _to_openai_messages

    msgs = (BrainMessage(role="user", content="question", images=(sample_block,)),)
    out = _to_openai_messages(msgs, None)
    # The first element may be a system entry; find the user message
    user_msg = next(m for m in out if m["role"] == "user")
    content = user_msg["content"]
    assert isinstance(content, list), f"expected a block list, got {type(content)}"
    # Text block
    text_block = next((b for b in content if b.get("type") == "text"), None)
    assert text_block is not None and text_block["text"] == "question"
    # image_url as a data URI
    img = next(b for b in content if b.get("type") == "image_url")
    assert img["image_url"]["url"].startswith("data:image/png;base64,")
    assert img["image_url"]["url"].endswith(",AAAA")


def test_openai_no_images_passes_string_through():
    """Backwards compat for OpenAI — string stays a string without images."""
    from jarvis.plugins.brain._openai_base import _to_openai_messages

    msgs = (BrainMessage(role="user", content="hi"),)
    out = _to_openai_messages(msgs, None)
    user_msg = next(m for m in out if m["role"] == "user")
    assert user_msg["content"] == "hi"


def test_openai_vision_unsupported_drops_images(sample_block, caplog):
    """When supports_vision=False → images are dropped + a WARN is logged."""
    import logging

    from jarvis.plugins.brain._openai_base import _to_openai_messages

    msgs = (BrainMessage(role="user", content="question", images=(sample_block,)),)
    with caplog.at_level(logging.WARNING, logger="jarvis.plugins.brain._openai_base"):
        out = _to_openai_messages(msgs, None, supports_vision=False)
    user_msg = next(m for m in out if m["role"] == "user")
    # Content is now a plain-text string (images dropped)
    assert user_msg["content"] == "question"
    assert any("Vision-Support" in rec.message for rec in caplog.records), (
        f"expected a WARN log, got {[r.message for r in caplog.records]}"
    )
