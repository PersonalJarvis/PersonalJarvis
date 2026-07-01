"""Unit tests for ImageBlock + BrainMessage.images (Phase Wave-1, B1).

Covers:
- ImageBlock is frozen (no mutation after construction).
- source_hash defaults to an empty string.
- BrainMessage.images defaults to an empty tuple.
- BrainMessage stores passed-in images as a tuple.
- Backwards compat: old positional-arg calls (role, content) still work.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from jarvis.core.protocols import BrainMessage, ImageBlock


def test_brain_message_default_no_images() -> None:
    """BrainMessage without an images argument has an empty tuple."""
    msg = BrainMessage(role="user", content="hi")
    assert msg.images == ()
    assert isinstance(msg.images, tuple)


def test_image_block_is_frozen() -> None:
    """ImageBlock is immutable — mutation raises FrozenInstanceError."""
    block = ImageBlock(mime="image/png", data_b64="abc")
    with pytest.raises(FrozenInstanceError):
        block.mime = "image/jpeg"  # type: ignore[misc]


def test_image_block_source_hash_defaults_empty() -> None:
    """source_hash is optional and defaults to an empty string."""
    block = ImageBlock(mime="image/png", data_b64="abc")
    assert block.source_hash == ""


def test_brain_message_with_images_stores_tuple() -> None:
    """Passed-in images are stored as a tuple in BrainMessage."""
    block = ImageBlock(mime="image/png", data_b64="xyz", source_hash="hash-1")
    msg = BrainMessage(role="user", content="schau dir das an", images=(block,))
    assert isinstance(msg.images, tuple)
    assert len(msg.images) == 1
    assert msg.images[0] is block
    assert msg.images[0].source_hash == "hash-1"


def test_brain_message_backwards_compat_positional() -> None:
    """Existing positional-arg calls BrainMessage("user", "hi") still work."""
    msg = BrainMessage("user", "hi")
    assert msg.role == "user"
    assert msg.content == "hi"
    assert msg.images == ()
    assert msg.tool_call_id is None
    assert msg.name is None
