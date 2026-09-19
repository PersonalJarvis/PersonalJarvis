from __future__ import annotations

import base64
import io

from PIL import Image

from jarvis.core.protocols import BrainMessage, BrainRequest, ImageBlock
from jarvis.plugins.brain import local_openai


def _png(w: int, h: int) -> ImageBlock:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, "PNG")
    return ImageBlock(mime="image/png", data_b64=base64.b64encode(buf.getvalue()).decode())


def _size(img: ImageBlock) -> tuple[int, int]:
    return Image.open(io.BytesIO(base64.b64decode(img.data_b64))).size


def test_large_screenshot_is_capped_for_the_local_vision_model() -> None:
    req = BrainRequest(messages=(BrainMessage(role="user", content="?", images=(_png(1920, 1080),)),))
    out = local_openai._shrink_images(req)
    img = out.messages[0].images[0]
    assert max(_size(img)) == local_openai.LOCAL_IMAGE_MAX_SIDE
    assert img.mime == "image/jpeg"


def test_small_image_and_text_only_requests_are_untouched() -> None:
    small = _png(300, 200)
    req = BrainRequest(messages=(BrainMessage(role="user", content="?", images=(small,)),))
    assert local_openai._shrink_images(req).messages[0].images[0] is small
    text_only = BrainRequest(messages=(BrainMessage(role="user", content="hi"),))
    assert local_openai._shrink_images(text_only) is text_only
