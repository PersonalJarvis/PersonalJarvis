"""Unit-Tests für ImageBlock + BrainMessage.images (Phase Wave-1, B1).

Sichert:
- ImageBlock ist frozen (keine Mutation nach Konstruktion).
- source_hash defaulted auf leer-String.
- BrainMessage.images defaulted auf leeres Tuple.
- BrainMessage speichert übergebene Images als Tuple.
- Backwards-Compat: alte positional-arg Aufrufe (role, content) bleiben funktional.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from jarvis.core.protocols import BrainMessage, ImageBlock


def test_brain_message_default_no_images() -> None:
    """BrainMessage ohne images-Argument hat leeres Tuple."""
    msg = BrainMessage(role="user", content="hi")
    assert msg.images == ()
    assert isinstance(msg.images, tuple)


def test_image_block_is_frozen() -> None:
    """ImageBlock ist immutable — Mutation wirft FrozenInstanceError."""
    block = ImageBlock(mime="image/png", data_b64="abc")
    with pytest.raises(FrozenInstanceError):
        block.mime = "image/jpeg"  # type: ignore[misc]


def test_image_block_source_hash_defaults_empty() -> None:
    """source_hash ist optional und defaulted auf leeren String."""
    block = ImageBlock(mime="image/png", data_b64="abc")
    assert block.source_hash == ""


def test_brain_message_with_images_stores_tuple() -> None:
    """Übergebene Images werden als Tuple in BrainMessage gespeichert."""
    block = ImageBlock(mime="image/png", data_b64="xyz", source_hash="hash-1")
    msg = BrainMessage(role="user", content="schau dir das an", images=(block,))
    assert isinstance(msg.images, tuple)
    assert len(msg.images) == 1
    assert msg.images[0] is block
    assert msg.images[0].source_hash == "hash-1"


def test_brain_message_backwards_compat_positional() -> None:
    """Bestehende positional-arg Aufrufe BrainMessage("user", "hi") klappen weiter."""
    msg = BrainMessage("user", "hi")
    assert msg.role == "user"
    assert msg.content == "hi"
    assert msg.images == ()
    assert msg.tool_call_id is None
    assert msg.name is None
