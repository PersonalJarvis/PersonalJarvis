"""Tests for the per-turn AI-Pointer push decision (AI Pointer step 7).

``resolve_turn_pointer`` is the deictic gate + resolve that the brain calls once
per turn. It returns (prompt_block, crop_image) only when the utterance points at
the cursor AND an element is resolved — otherwise ("", None), so unrelated turns
("how's the weather?") never get cursor context (the "no garbage" contract).
"""

from __future__ import annotations

from jarvis.core.protocols import ImageBlock
from jarvis.pointer.context import PointerContext
from jarvis.pointer.turn import resolve_turn_pointer
from jarvis.vision.pointer_types import PointerElement


def _ctx(available=True, **kw):
    return PointerContext(available=available, **kw)


async def test_disabled_returns_empty() -> None:
    called = {"resolver": False}

    async def resolver(timeout_s):
        called["resolver"] = True
        return _ctx(element=PointerElement(name="x", role="Button"))

    block, crop = await resolve_turn_pointer(
        "was ist das da?", enabled=False, gate=lambda t: True, resolver=resolver
    )
    assert block == ""
    assert crop is None
    assert called["resolver"] is False  # disabled short-circuits before resolve


async def test_non_deictic_does_not_resolve() -> None:
    called = {"resolver": False}

    async def resolver(timeout_s):
        called["resolver"] = True
        return _ctx()

    block, crop = await resolve_turn_pointer(
        "wie ist das Wetter?", enabled=True, gate=lambda t: False, resolver=resolver
    )
    assert (block, crop) == ("", None)
    assert called["resolver"] is False  # gate vetoes — no cursor work at all


async def test_deictic_available_returns_block_and_crop() -> None:
    crop_block = ImageBlock(mime="image/jpeg", data_b64="QQ==")
    el = PointerElement(name="", role="Image")

    async def resolver(timeout_s):
        return _ctx(x=10, y=20, element=el, crop=crop_block)

    block, crop = await resolve_turn_pointer(
        "was ist das da?", enabled=True, gate=lambda t: True, resolver=resolver
    )
    assert "AI Pointer" in block
    assert crop is crop_block


async def test_deictic_unavailable_returns_empty() -> None:
    async def resolver(timeout_s):
        return _ctx(available=False, reason="no_cursor")

    block, crop = await resolve_turn_pointer(
        "was ist das?", enabled=True, gate=lambda t: True, resolver=resolver
    )
    assert (block, crop) == ("", None)


async def test_resolver_error_is_swallowed() -> None:
    async def resolver(timeout_s):
        raise RuntimeError("boom")

    block, crop = await resolve_turn_pointer(
        "was ist das da?", enabled=True, gate=lambda t: True, resolver=resolver
    )
    assert (block, crop) == ("", None)


async def test_headless_host_fast_skips_before_thread_dispatch(monkeypatch) -> None:
    import jarvis.platform.capabilities as caps_mod
    import jarvis.pointer.turn as turn_mod
    from tests.fakes.fake_capabilities import fake_headless_capabilities

    called = {"resolver": False}

    async def fake_default(*, timeout_s, crop_radius):
        called["resolver"] = True
        return _ctx()

    # has_cursor=False (headless VPS / Wayland): the gate fires but the default
    # path must short-circuit before dispatching the worker thread.
    monkeypatch.setattr(caps_mod, "detect_capabilities", fake_headless_capabilities)
    monkeypatch.setattr(turn_mod, "resolve_pointer_context_async", fake_default)

    block, crop = await resolve_turn_pointer("was ist das da?", enabled=True)
    assert (block, crop) == ("", None)
    assert called["resolver"] is False


async def test_crop_radius_forwarded_to_default_resolver(monkeypatch) -> None:
    import jarvis.pointer.turn as turn_mod

    seen: dict[str, object] = {}

    async def fake_default(*, timeout_s, crop_radius):
        seen["timeout_s"] = timeout_s
        seen["crop_radius"] = crop_radius
        return _ctx(element=PointerElement(name="x", role="Button"))

    monkeypatch.setattr(turn_mod, "resolve_pointer_context_async", fake_default)

    await resolve_turn_pointer(
        "was ist das da?", enabled=True, gate=lambda t: True, timeout_s=0.4, crop_radius=96
    )
    assert seen == {"timeout_s": 0.4, "crop_radius": 96}
