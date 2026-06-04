"""Tests for the pointer-context resolver (AI Pointer step 5).

Composes cursor position -> element-at-point -> optional ROI crop into a single
``PointerContext`` and renders a compact prompt block. Crop is added only when
the element is unlabeled (a raster graphic). All dependencies are injectable.
"""

from __future__ import annotations

import time

from jarvis.core.protocols import ImageBlock
from jarvis.pointer.context import (
    PointerContext,
    resolve_pointer_context,
    resolve_pointer_context_async,
)
from jarvis.vision.pointer_types import PointerElement


class _Cursor:
    def __init__(self, pos):
        self._pos = pos

    def position(self):
        return self._pos


class _Resolver:
    def __init__(self, element):
        self._element = element

    def at(self, x, y):
        return self._element


def _block() -> ImageBlock:
    return ImageBlock(mime="image/jpeg", data_b64="QUJD")


def test_unavailable_when_no_cursor() -> None:
    ctx = resolve_pointer_context(cursor_backend=_Cursor(None), resolver=_Resolver(None))
    assert ctx.available is False
    assert ctx.reason == "no_cursor"
    assert ctx.render() == ""


def test_labeled_element_also_crops() -> None:
    # 2026-06-02: always-crop on a pointer turn — even a labeled element gets a
    # crop, so "lies das / was steht da" works on text the a11y name misses
    # (a terminal word under a pane labeled only with the terminal title).
    calls: list[tuple[int, int]] = []

    def crop_fn(x, y):
        calls.append((x, y))
        return _block()

    el = PointerElement(name="Save", role="Button", bounds=(0, 0, 10, 10), app_name="x")
    ctx = resolve_pointer_context(
        cursor_backend=_Cursor((10, 20)),
        resolver=_Resolver(el),
        crop_fn=crop_fn,
    )
    assert ctx.available is True
    assert ctx.element is el
    assert ctx.crop is not None
    assert calls == [(10, 20)]  # crop captured even for a labeled element
    assert "Save" in ctx.render()
    assert "Button" in ctx.render()


def test_unlabeled_element_triggers_crop() -> None:
    el = PointerElement(name="", role="Image", value="")
    ctx = resolve_pointer_context(
        cursor_backend=_Cursor((30, 40)),
        resolver=_Resolver(el),
        crop_fn=lambda x, y: _block(),
    )
    assert ctx.available is True
    assert ctx.crop is not None
    assert ctx.crop.mime == "image/jpeg"


def test_no_element_no_crop_is_unavailable() -> None:
    ctx = resolve_pointer_context(
        cursor_backend=_Cursor((1, 2)),
        resolver=_Resolver(None),
        crop_fn=lambda x, y: None,
    )
    assert ctx.available is False
    assert ctx.reason == "no_element"


def test_no_element_with_crop_is_available() -> None:
    ctx = resolve_pointer_context(
        cursor_backend=_Cursor((1, 2)),
        resolver=_Resolver(None),
        crop_fn=lambda x, y: _block(),
    )
    assert ctx.available is True
    assert ctx.crop is not None
    assert ctx.render() != ""


def test_render_empty_when_unavailable() -> None:
    ctx = PointerContext(available=False, reason="no_cursor")
    assert ctx.render() == ""


def test_render_demands_direct_immediate_answer() -> None:
    # Voice hardening: the block must push for a direct, immediate spoken answer
    # so the router does not defer ("Ich schaue gleich nach") instead of answering.
    out = PointerContext(
        available=True, element=PointerElement(name="Save", role="Button")
    ).render().lower()
    assert "directly" in out
    assert "do not say you will look" in out


def test_render_crop_disarms_tool_refusal() -> None:
    # When a crop is attached, the block must tell the brain the crop IS its
    # vision so it does not refuse "Was siehst du hier?" with "I lack a tool".
    crop = ImageBlock(mime="image/jpeg", data_b64="QQ==")
    el = PointerElement(name="", role="Pane")
    out = PointerContext(available=True, element=el, crop=crop).render().lower()
    assert "do not say you lack a tool" in out
    assert "attached" in out
    assert "read the text at the centre" in out


def test_render_bounds_grounding_fields() -> None:
    # A pathological long window title/app must not leak verbatim into the prompt.
    long = "secret/path/" + "x" * 300
    el = PointerElement(name="y" * 300, role="Document", app_name=long)
    out = PointerContext(available=True, element=el).render()
    assert long not in out
    assert "y" * 300 not in out
    assert "Document" in out  # role still grounds the brain


async def test_resolve_async_times_out() -> None:
    class _SlowCursor:
        def position(self):
            time.sleep(0.3)
            return (1, 1)

    ctx = await resolve_pointer_context_async(
        cursor_backend=_SlowCursor(),
        resolver=_Resolver(None),
        crop_fn=lambda x, y: None,
        timeout_s=0.05,
    )
    assert ctx.available is False
    assert ctx.reason == "timeout"


async def test_resolve_async_returns_context() -> None:
    el = PointerElement(name="Link", role="Hyperlink")
    ctx = await resolve_pointer_context_async(
        cursor_backend=_Cursor((5, 5)),
        resolver=_Resolver(el),
        crop_fn=lambda x, y: None,
        timeout_s=1.0,
    )
    assert ctx.available is True
    assert ctx.element is el
