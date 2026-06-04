"""Integration: deictic-gated AI-Pointer push into BrainManager.generate().

Verifies the wiring at manager.py: a deictic utterance ("was ist das da?")
rides the resolved pointer block on turn_context and attaches the crop image; an
unrelated utterance ("wie ist das Wetter?") never reaches the cursor resolver.
"""

from __future__ import annotations

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.brain.streaming import StreamingAggregate
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import ImageBlock
from jarvis.pointer import turn as pturn
from jarvis.pointer.context import PointerContext
from jarvis.vision.pointer_types import PointerElement


class _FakeNamed:
    def __init__(self, name: str) -> None:
        self.name = name
        self.schema: dict = {}


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.tools_seen: list[dict] = []

    async def dispatch(self, user_text, *, images=(), turn_context="", **_kwargs):
        self.calls.append({"user_text": user_text, "images": images, "turn_context": turn_context})
        agg = StreamingAggregate()
        agg.text = "ok"
        agg.finish_reason = "stop"
        return agg


def _manager(
    tools: dict | None = None, vision_images: tuple = ()
) -> tuple[BrainManager, _Recorder]:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    manager = BrainManager(config=cfg, bus=EventBus(), tools=tools or {})
    rec = _Recorder()
    manager._build_fallback_chain = lambda _level: [("fake", "fake-model")]  # type: ignore[method-assign]
    manager._get_brain = lambda _name, _model: object()  # type: ignore[method-assign]

    async def _civ(**_kw):
        return vision_images

    manager._collect_vision_images = _civ  # type: ignore[method-assign]

    def _bd(_brain, *, tools_override=None):
        rec.tools_seen.append(dict(tools_override or {}))
        return rec

    manager._build_dispatcher = _bd  # type: ignore[method-assign]
    return manager, rec


@pytest.mark.asyncio
async def test_deictic_utterance_injects_pointer(monkeypatch) -> None:
    crop = ImageBlock(mime="image/jpeg", data_b64="QQ==")

    async def fake_resolve(*, timeout_s=0.25, crop_radius=64):
        return PointerContext(
            available=True, x=1, y=2, element=PointerElement(name="", role="Image"), crop=crop
        )

    monkeypatch.setattr(pturn, "resolve_pointer_context_async", fake_resolve)

    manager, rec = _manager()
    await manager.generate("was ist das da?", use_history=False)

    call = rec.calls[0]
    assert "AI Pointer" in call["turn_context"]
    assert crop in call["images"]


@pytest.mark.asyncio
async def test_unrelated_utterance_skips_pointer(monkeypatch) -> None:
    resolved = {"called": False}

    async def fake_resolve(*, timeout_s=0.25, crop_radius=64):
        resolved["called"] = True
        return PointerContext(available=True, element=PointerElement(name="x", role="Button"))

    monkeypatch.setattr(pturn, "resolve_pointer_context_async", fake_resolve)

    manager, rec = _manager()
    await manager.generate("wie ist das Wetter heute?", use_history=False)

    call = rec.calls[0]
    assert "AI Pointer" not in call["turn_context"]
    assert call["images"] == ()
    assert resolved["called"] is False  # gate vetoed — no cursor work


@pytest.mark.asyncio
async def test_push_turn_suppresses_inspect_pointer_tool(monkeypatch) -> None:
    """When the deictic push fires, the redundant inspect-pointer PULL tool is
    hidden for that turn — so the brain answers from the inline context instead
    of calling the tool and then failing to verbalize (the live 'verlegt' bug)."""

    async def fake_resolve(*, timeout_s=0.12, crop_radius=64):
        return PointerContext(
            available=True, x=1, y=2, element=PointerElement(name="Save", role="Button")
        )

    monkeypatch.setattr(pturn, "resolve_pointer_context_async", fake_resolve)

    manager, rec = _manager(tools={"inspect-pointer": _FakeNamed("inspect-pointer"), "other": _FakeNamed("other")})
    await manager.generate("was ist das da?", use_history=False)

    tools = rec.tools_seen[0]
    assert "inspect-pointer" not in tools  # suppressed on the push turn
    assert "other" in tools  # unrelated tools untouched


@pytest.mark.asyncio
async def test_non_push_turn_keeps_inspect_pointer_tool(monkeypatch) -> None:
    """On a non-deictic turn the push does not fire, so the tool stays available."""

    async def fake_resolve(*, timeout_s=0.12, crop_radius=64):
        raise AssertionError("resolver must not run on a non-deictic turn")

    monkeypatch.setattr(pturn, "resolve_pointer_context_async", fake_resolve)

    manager, rec = _manager(tools={"inspect-pointer": _FakeNamed("inspect-pointer"), "other": _FakeNamed("other")})
    await manager.generate("erzaehl mir einen Witz", use_history=False)

    tools = rec.tools_seen[0]
    assert "inspect-pointer" in tools  # available when the push did not fire


# ---------------------------------------------------------------------------
# Grounding: a pointer turn must be scoped to the CURSOR region — the brain must
# not guess the pointing target from the full-screen permanent-vision image (the
# live "described something completely elsewhere" bug, 2026-06-02).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pointer_turn_replaces_fullscreen_with_cursor_crop(monkeypatch) -> None:
    crop = ImageBlock(mime="image/jpeg", data_b64="Q1JPUA==")
    fullscreen = ImageBlock(mime="image/png", data_b64="RlVMTA==")

    async def fake_resolve(*, timeout_s=0.12, crop_radius=64):
        return PointerContext(
            available=True, element=PointerElement(name="", role="Image"), crop=crop
        )

    monkeypatch.setattr(pturn, "resolve_pointer_context_async", fake_resolve)

    manager, rec = _manager(vision_images=(fullscreen,))
    await manager.generate("was ist das hier?", use_history=False)

    # The full-screen permanent-vision image is replaced by the tight cursor crop.
    assert rec.calls[0]["images"] == (crop,)


@pytest.mark.asyncio
async def test_pointer_turn_drops_inspect_keeps_screenshot(monkeypatch) -> None:
    # inspect-pointer is dropped (its call produced an empty answer), but the
    # screenshot tool is KEPT — removing it made the router refuse "Was siehst
    # du hier?" with "I lack a tool". The crop+prompt steer it to the crop.
    async def fake_resolve(*, timeout_s=0.12, crop_radius=110):
        return PointerContext(
            available=True, element=PointerElement(name="Save", role="Button")
        )

    monkeypatch.setattr(pturn, "resolve_pointer_context_async", fake_resolve)

    manager, rec = _manager(
        tools={
            "screenshot": _FakeNamed("screenshot"),
            "inspect-pointer": _FakeNamed("inspect-pointer"),
            "other": _FakeNamed("other"),
        }
    )
    await manager.generate("was ist das hier?", use_history=False)

    tools = rec.tools_seen[0]
    assert "screenshot" in tools  # kept → no "I lack a tool" refusal on "siehst du"
    assert "inspect-pointer" not in tools  # dropped → no empty-answer pull
    assert "other" in tools


@pytest.mark.asyncio
async def test_non_pointer_turn_keeps_fullscreen_and_screenshot(monkeypatch) -> None:
    fullscreen = ImageBlock(mime="image/png", data_b64="RlVMTA==")
    manager, rec = _manager(
        tools={"screenshot": _FakeNamed("screenshot"), "other": _FakeNamed("other")},
        vision_images=(fullscreen,),
    )
    await manager.generate("erzaehl mir einen Witz", use_history=False)

    assert rec.calls[0]["images"] == (fullscreen,)  # full-screen vision untouched
    assert "screenshot" in rec.tools_seen[0]
