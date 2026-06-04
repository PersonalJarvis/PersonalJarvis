"""Wave 2: a tool that returns an image artifact feeds it back as an ImageBlock,
so a vision provider can see it on the brain's next iteration."""
from __future__ import annotations

from jarvis.brain.tool_use_loop import _images_from_artifacts
from jarvis.core.protocols import ImageBlock


def test_image_artifact_becomes_image_block() -> None:
    arts = ({"type": "image", "mime": "image/jpeg", "data": "QUJD"},)
    blocks = _images_from_artifacts(arts)
    assert len(blocks) == 1
    assert isinstance(blocks[0], ImageBlock)
    assert blocks[0].mime == "image/jpeg"
    assert blocks[0].data_b64 == "QUJD"


def test_default_mime_when_missing() -> None:
    blocks = _images_from_artifacts(({"type": "image", "data": "QUJD"},))
    assert len(blocks) == 1
    assert blocks[0].mime == "image/jpeg"


def test_text_only_artifacts_yield_no_images() -> None:
    assert _images_from_artifacts(()) == []
    assert _images_from_artifacts(None) == []
    assert _images_from_artifacts(("some text note",)) == []
    assert _images_from_artifacts(({"type": "image"},)) == []  # no data -> skip
    assert _images_from_artifacts(({"type": "text", "data": "x"},)) == []
