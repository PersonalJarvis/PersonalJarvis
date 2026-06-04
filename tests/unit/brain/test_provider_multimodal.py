"""Multimodal-Encoding-Tests für Brain-Provider (Wave-1 B3).

Testet dass BrainMessage.images korrekt in das jeweilige Provider-
API-Format übersetzt wird:
- Anthropic: `{"type": "image", "source": {"type": "base64", ...}}`
- Gemini:    `{"inline_data": {"mime_type": ..., "data": ...}}`
- OpenAI:    `{"type": "image_url", "image_url": {"url": "data:<mime>;base64,..."}}`

Backwards-Compat: Messages ohne images müssen sich identisch zu vor
Wave-1 B3 verhalten (string content bleibt string).
"""
from __future__ import annotations

import pytest

from jarvis.core.protocols import BrainMessage, ImageBlock


@pytest.fixture
def sample_block() -> ImageBlock:
    return ImageBlock(mime="image/png", data_b64="AAAA", source_hash="abc")


def test_anthropic_encodes_image_as_base64_source(sample_block):
    from jarvis.plugins.brain._anthropic_base import _to_anthropic_messages

    msgs = (BrainMessage(role="user", content="was ist das?", images=(sample_block,)),)
    out = _to_anthropic_messages(msgs)
    assert len(out) == 1
    content = out[0]["content"]
    assert isinstance(content, list), f"erwarte Blocks-Liste, got {type(content)}"
    # Text-Block muss vorhanden sein
    text_block = next((b for b in content if b.get("type") == "text"), None)
    assert text_block is not None, f"kein text-Block in {content}"
    assert text_block["text"] == "was ist das?"
    # Image-Block muss korrekt enkodiert sein
    img = next(b for b in content if b.get("type") == "image")
    assert img["source"]["type"] == "base64"
    assert img["source"]["media_type"] == "image/png"
    assert img["source"]["data"] == "AAAA"


def test_anthropic_no_images_passes_string_through():
    from jarvis.plugins.brain._anthropic_base import _to_anthropic_messages

    msgs = (BrainMessage(role="user", content="hi"),)
    out = _to_anthropic_messages(msgs)
    # Backwards-compat: wenn keine images, content bleibt string
    assert out[0]["content"] == "hi"


def test_gemini_encodes_image_as_inline_data(sample_block):
    from jarvis.plugins.brain.gemini import _to_gemini_contents

    msgs = (BrainMessage(role="user", content="frage", images=(sample_block,)),)
    contents = _to_gemini_contents(msgs)
    assert len(contents) == 1
    parts = contents[0]["parts"]
    # Text-Part + inline_data-Part
    text_part = next((p for p in parts if isinstance(p, dict) and "text" in p), None)
    assert text_part is not None and text_part["text"] == "frage"
    inline = next((p for p in parts if isinstance(p, dict) and "inline_data" in p), None)
    assert inline is not None, f"kein inline_data in parts: {parts}"
    assert inline["inline_data"]["mime_type"] == "image/png"
    assert inline["inline_data"]["data"] == "AAAA"


def test_openai_encodes_image_as_image_url(sample_block):
    from jarvis.plugins.brain._openai_base import _to_openai_messages

    msgs = (BrainMessage(role="user", content="frage", images=(sample_block,)),)
    out = _to_openai_messages(msgs, None)
    # Erstes Element ist evtl. ein system-Entry; such die user-Message
    user_msg = next(m for m in out if m["role"] == "user")
    content = user_msg["content"]
    assert isinstance(content, list), f"erwarte Blocks-Liste, got {type(content)}"
    # Text-Block
    text_block = next((b for b in content if b.get("type") == "text"), None)
    assert text_block is not None and text_block["text"] == "frage"
    # image_url als Data-URI
    img = next(b for b in content if b.get("type") == "image_url")
    assert img["image_url"]["url"].startswith("data:image/png;base64,")
    assert img["image_url"]["url"].endswith(",AAAA")


def test_openai_no_images_passes_string_through():
    """Backwards-Compat für OpenAI — string bleibt string ohne images."""
    from jarvis.plugins.brain._openai_base import _to_openai_messages

    msgs = (BrainMessage(role="user", content="hi"),)
    out = _to_openai_messages(msgs, None)
    user_msg = next(m for m in out if m["role"] == "user")
    assert user_msg["content"] == "hi"


def test_openai_vision_unsupported_drops_images(sample_block, caplog):
    """Wenn supports_vision=False → images werden verworfen + WARN geloggt."""
    import logging

    from jarvis.plugins.brain._openai_base import _to_openai_messages

    msgs = (BrainMessage(role="user", content="frage", images=(sample_block,)),)
    with caplog.at_level(logging.WARNING, logger="jarvis.plugins.brain._openai_base"):
        out = _to_openai_messages(msgs, None, supports_vision=False)
    user_msg = next(m for m in out if m["role"] == "user")
    # Content ist jetzt plain-text-String (images gedroppt)
    assert user_msg["content"] == "frage"
    assert any("Vision-Support" in rec.message for rec in caplog.records), (
        f"erwarte WARN-Log, got {[r.message for r in caplog.records]}"
    )
