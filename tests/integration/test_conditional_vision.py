"""Integration: the conditional-vision gate is wired into BrainManager (Wave 1).

Proves the wiring end-to-end (not just the pure gate function): a smalltalk
turn drops the screenshot, a screen-reference turn keeps it. Uses a fake vision
provider backed by a real temp PNG so the read+cap path runs for real.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from jarvis.brain.manager import BrainManager
from jarvis.core.config import load_config


def _make_obs(tmp_path) -> SimpleNamespace:
    from PIL import Image

    p = tmp_path / "screen.png"
    Image.new("RGB", (320, 240), (10, 20, 30)).save(p, format="PNG")
    return SimpleNamespace(
        screenshot_path=str(p),
        screenshot_hash="deadbeef" * 4,
        timestamp_ns=0,
        window_title="Test Window",
    )


class _FakeVision:
    is_paused = False

    def __init__(self, obs: SimpleNamespace) -> None:
        self._obs = obs

    async def current(self) -> SimpleNamespace:
        return self._obs


def _manager(obs: SimpleNamespace) -> BrainManager:
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    m._vision_provider = _FakeVision(obs)
    cfg = load_config()
    cfg.performance.conditional_vision = True  # deterministic regardless of toml
    m._config = cfg
    m._active_name = "gemini"
    m._bus = None
    return m


async def test_smalltalk_turn_skips_screenshot(tmp_path) -> None:
    m = _manager(_make_obs(tmp_path))
    imgs = await m._collect_vision_images(
        trace_id=uuid4(), user_text="wie spät ist es", is_smalltalk=True  # i18n-allow
    )
    assert imgs == ()


async def test_screen_question_keeps_screenshot(tmp_path) -> None:
    m = _manager(_make_obs(tmp_path))
    imgs = await m._collect_vision_images(
        trace_id=uuid4(), user_text="was siehst du hier", is_smalltalk=False
    )
    assert len(imgs) == 1
    assert imgs[0].data_b64


async def test_marker_keeps_screenshot_even_if_smalltalk(tmp_path) -> None:
    m = _manager(_make_obs(tmp_path))
    imgs = await m._collect_vision_images(
        trace_id=uuid4(), user_text="schau mal das hier", is_smalltalk=True
    )
    assert len(imgs) == 1


async def test_conversation_recall_skips_screenshot(tmp_path) -> None:
    """The reported bug: a 'what did we discuss?' turn must NOT attach a screenshot,
    so the conversation history is the brain's context, not the current screen.
    This is a content question (is_smalltalk=False) yet carries no visual marker."""
    m = _manager(_make_obs(tmp_path))
    imgs = await m._collect_vision_images(
        trace_id=uuid4(),
        user_text="was haben wir gerade besprochen?",
        is_smalltalk=False,
    )
    assert imgs == ()
