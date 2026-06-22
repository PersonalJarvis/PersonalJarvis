"""Computer-Use must reach a vision-capable provider — grok is one.

Live forensic 2026-06-21 18:41: a "open Chrome with computer use …" command
DID dispatch to the screenshot harness (routing fixed), but the harness gave up:

    exit 2 · [cu] giving up after 3 model failures … ComputerUseLoop provider
    chain failed: 3 provider(s) skipped — no vision; claude-api(haiku):
    incomplete chunked read; openrouter(opus-4.8): Kein O…

Root cause: ``screenshot_only_loop._call_brain`` skips every provider whose
``supports_vision`` is False when a screenshot is attached. The user's only
provider with a live key — **grok** — was flagged ``supports_vision=False``
(an over-cautious "untested" guard), so the loop skipped it and, with every
other vision provider keyless/billing-dead, failed with "no vision". grok-4.3
in fact reads images fine via the OpenAI-compat ``image_url`` path (verified
end-to-end against the live xAI API), so the fix is to flag grok vision-capable.

These tests pin the fix and reproduce the loop scenario hermetically; the live
probe (self-skips without a key) is the strict end-to-end proof.
"""
from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.protocols import (
    BrainDelta,
    BrainMessage,
    ImageBlock,
    Observation,
)
from jarvis.harness.screenshot_only_loop import CULoopError, _call_brain
from jarvis.plugins.brain.grok import GrokBrain

# Reuse the loop-test fakes (FakeBrain shim, ctx builder, host isolation fixture).
from tests.unit.harness.test_cu_loop_robustness import (  # noqa: E402
    FakeBrain,
    _StreamingBrain,
    make_ctx,
)

# Pull in the autouse _isolate_host fixture so these tests never touch the
# real desktop (UIA / monitor probing) either.
from tests.unit.harness.test_cu_loop_robustness import _isolate_host  # noqa: E402,F401


# ---------------------------------------------------------------------------
# 1. The fix itself — grok must declare vision so the CU loop will try it.
# ---------------------------------------------------------------------------


def test_grok_brain_declares_vision_support() -> None:
    """grok reads images; the CU loop's blind-skip gate keys off this flag.

    Before the fix this was False, so the CU loop skipped grok — the only
    provider with a live key — and failed with "no vision".
    """
    assert GrokBrain.supports_vision is True


# ---------------------------------------------------------------------------
# 2. The image actually reaches the wire for grok (not silently dropped).
# ---------------------------------------------------------------------------


def test_grok_image_is_routed_to_openai_image_url_not_dropped() -> None:
    """With grok's real ``supports_vision`` flag, an attached screenshot is
    encoded as an OpenAI ``image_url`` data-URI — NOT dropped to plain text.
    A dropped image is exactly what would make grok plan blind."""
    from jarvis.plugins.brain._openai_base import _to_openai_messages

    block = ImageBlock(mime="image/png", data_b64="AAAA", source_hash="h")
    msgs = (BrainMessage(role="user", content="what is on screen?", images=(block,)),)

    out = _to_openai_messages(msgs, None, supports_vision=GrokBrain.supports_vision)

    user_msg = next(m for m in out if m["role"] == "user")
    assert isinstance(user_msg["content"], list), (
        "grok image was dropped to plain text — it would plan blind"
    )
    img = next(b for b in user_msg["content"] if b.get("type") == "image_url")
    assert img["image_url"]["url"] == "data:image/png;base64,AAAA"


# ---------------------------------------------------------------------------
# 3. Reproduce the 18:41 loop scenario: blind active provider + grok fallback.
#    Before the fix grok was skipped → CULoopError("provider chain failed:
#    … no vision"). After the fix the loop reaches grok and uses its plan.
# ---------------------------------------------------------------------------


class _BlindThenGrokManager:
    """Chain = [antigravity(blind)×2, grok]. ``_get_brain('grok')`` returns the
    REAL GrokBrain so the loop reads grok's real ``supports_vision`` flag."""

    active_provider = "antigravity"

    def __init__(self, grok_brain: GrokBrain) -> None:
        self.blind = _StreamingBrain(
            text='{"action": "click", "x": 1, "y": 1}', supports_vision=False,
        )
        self.grok = grok_brain
        self.requested: list[tuple[str, str | None]] = []

    def _build_fallback_chain(self, level: str) -> list[tuple[str, str | None]]:
        # Mirrors the live chain shape: the blind active brain appears twice
        # (fast + deep model), then grok as the vision-capable fallback.
        return [
            ("antigravity", "gemini-3.5-flash"),
            ("antigravity", "gemini-3.1-pro-preview"),
            ("grok", "grok-4.3"),
        ]

    def _get_brain(self, name: str, model: str | None = None) -> Any:
        self.requested.append((name, model))
        if name == "antigravity":
            return self.blind
        if name == "grok":
            return self.grok
        raise AssertionError(f"unexpected provider {name!r}")


async def test_cu_loop_reaches_grok_when_active_provider_is_blind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact 18:41 failure shape: antigravity (blind) leads, grok is the
    only vision-capable provider with a key. With grok flagged vision-capable
    the loop must skip the blind provider and dispatch the screenshot to grok."""
    grok = GrokBrain(model="grok-4.3")

    # Stub grok's network call: it must be REACHED (proving the fix) but must
    # not hit api.x.ai in a unit test. An async-generator complete() shaped
    # like the real one.
    async def _fake_complete(req: Any):  # type: ignore[no-untyped-def]
        # The screenshot must have survived to the request (not dropped).
        assert any(getattr(m, "images", ()) for m in req.messages), (
            "grok was dispatched WITHOUT the screenshot — it would plan blind"
        )
        yield BrainDelta(content='{"action": "done"}')
        yield BrainDelta(finish_reason="stop")

    monkeypatch.setattr(grok, "complete", _fake_complete)

    manager = _BlindThenGrokManager(grok)
    ctx = make_ctx(FakeBrain())
    ctx.brain_manager = manager
    obs = Observation(
        trace_id=uuid4(), timestamp_ns=time.time_ns(),
        screenshot_path=None, screenshot_hash="x",
    )
    img = ImageBlock(mime="image/jpeg", data_b64="QQ==", source_hash="x")

    raw = await _call_brain(
        ctx, observation=obs, user_goal="open chrome with computer use",
        history_text="", images_override=[img],
    )

    assert raw == '{"action": "done"}', "the CU loop did not reach grok"
    assert manager.blind.calls == 0, "the blind active provider must be skipped"
    # grok must have been the provider that answered.
    assert ("grok", "grok-4.3") in manager.requested


# ---------------------------------------------------------------------------
# 4. Strict live proof: the REAL GrokBrain reads a real image via the real
#    xAI API. Self-skips when no grok key is configured (CI / no-credential).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3b. The sibling CLI brain (codex) must report vision per RUNTIME path: the
#     ChatGPT-CLI path drops images (blind) → supports_vision must be False so
#     the CU loop skips it and reaches grok; the API-key path can see → True.
#     A static True made CU dispatch a screenshot to the blind CLI brain.
# ---------------------------------------------------------------------------


def test_codex_is_blind_on_the_cli_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.plugins.brain.codex import CodexBrain

    monkeypatch.setattr(CodexBrain, "_api_key", lambda self: None)
    brain = CodexBrain(model="gpt-5.5")
    assert brain.supports_vision is False, (
        "codex on the ChatGPT-CLI path drops images — it must report blind so "
        "the CU loop skips it and reaches a vision-capable provider"
    )


def test_codex_sees_on_the_api_key_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.plugins.brain.codex import CodexBrain

    monkeypatch.setattr(CodexBrain, "_api_key", lambda self: "sk-test-key")
    brain = CodexBrain(model="gpt-5.5")
    assert brain.supports_vision is True, (
        "with an API key codex uses the vision-capable API path"
    )


# ---------------------------------------------------------------------------
# 5. PROVIDER-AGNOSTIC proof: CU is not grok-specific. For EACH vision-capable
#    provider set as the active/leading brain, ``_call_brain`` must dispatch the
#    screenshot to THAT provider. The selection is capability-gated, never
#    provider-name-gated — grok is used today only because it is the one with a
#    live key, not because the loop prefers it.
# ---------------------------------------------------------------------------


_VISION_PROVIDERS = ("claude-api", "openrouter", "openai", "gemini", "grok")


class _SingleProviderManager:
    """BrainManager-shaped fake whose chain leads with one named provider.

    ``_get_brain`` returns the SAME vision-capable streaming brain regardless of
    name, so the test proves selection is driven purely by the chain order +
    capability gate — not by any provider-name special-case in ``_call_brain``.
    """

    def __init__(self, lead_provider: str, brain: _StreamingBrain) -> None:
        self.active_provider = lead_provider
        self._lead = lead_provider
        self._brain = brain
        self.requested: list[tuple[str, str | None]] = []

    def _build_fallback_chain(self, level: str) -> list[tuple[str, str | None]]:
        # The active provider leads; a second distinct vision provider follows
        # so the test also proves the LEADER is the one dispatched (not a
        # blind fallthrough to position 1).
        other = "gemini" if self._lead != "gemini" else "claude-api"
        return [(self._lead, f"{self._lead}-model"), (other, f"{other}-model")]

    def _get_brain(self, name: str, model: str | None = None) -> _StreamingBrain:
        self.requested.append((name, model))
        return self._brain


@pytest.mark.parametrize("lead_provider", _VISION_PROVIDERS)
async def test_cu_dispatches_screenshot_to_active_vision_provider(
    lead_provider: str,
) -> None:
    """For EACH of the 5 vision-capable providers, when it is the active/leading
    brain the CU loop dispatches the screenshot to IT — not to grok, not to any
    hardcoded provider. This is the provider-agnosticism guarantee."""
    brain = _StreamingBrain(text='{"action": "done"}', supports_vision=True)
    manager = _SingleProviderManager(lead_provider, brain)
    ctx = make_ctx(FakeBrain())
    ctx.brain_manager = manager
    obs = Observation(
        trace_id=uuid4(), timestamp_ns=time.time_ns(),
        screenshot_path=None, screenshot_hash="x",
    )
    img = ImageBlock(mime="image/jpeg", data_b64="QQ==", source_hash="x")

    raw = await _call_brain(
        ctx, observation=obs, user_goal="open chrome with computer use",
        history_text="", images_override=[img],
    )

    assert raw == '{"action": "done"}'
    # The leading provider was the FIRST (and only) brain dispatched.
    assert brain.calls == 1
    assert manager.requested[0] == (lead_provider, f"{lead_provider}-model")


@pytest.mark.parametrize("vision_provider", _VISION_PROVIDERS)
async def test_cu_falls_through_blind_active_to_any_vision_provider(
    vision_provider: str,
) -> None:
    """A blind active provider (antigravity-like, ``supports_vision=False``)
    must be skipped and the screenshot must fall through to WHICHEVER
    vision-capable provider is next — proving the fallthrough is generic, not
    grok-specific."""
    blind = _StreamingBrain(
        text='{"action": "click", "x": 1, "y": 1}', supports_vision=False,
    )
    seeing = _StreamingBrain(text='{"action": "done"}', supports_vision=True)

    class _BlindThenVisionManager:
        active_provider = "antigravity"

        def __init__(self) -> None:
            self.requested: list[tuple[str, str | None]] = []

        def _build_fallback_chain(self, level: str) -> list[tuple[str, str | None]]:
            return [
                ("antigravity", "agy-cli"),
                (vision_provider, f"{vision_provider}-model"),
            ]

        def _get_brain(self, name: str, model: str | None = None) -> _StreamingBrain:
            self.requested.append((name, model))
            return blind if name == "antigravity" else seeing

    manager = _BlindThenVisionManager()
    ctx = make_ctx(FakeBrain())
    ctx.brain_manager = manager
    obs = Observation(
        trace_id=uuid4(), timestamp_ns=time.time_ns(),
        screenshot_path=None, screenshot_hash="x",
    )
    img = ImageBlock(mime="image/jpeg", data_b64="QQ==", source_hash="x")

    raw = await _call_brain(
        ctx, observation=obs, user_goal="open chrome", history_text="",
        images_override=[img],
    )

    assert raw == '{"action": "done"}'
    assert blind.calls == 0, "the blind active provider must never be dispatched"
    assert seeing.calls == 1, "the screenshot must reach the vision-capable brain"
    assert (vision_provider, f"{vision_provider}-model") in manager.requested


@pytest.mark.integration
async def test_grok_reads_an_image_live() -> None:
    """End-to-end: drive the production ``GrokBrain.complete`` with an attached
    image against the live xAI endpoint and assert grok actually SEES it.

    This is the verification the over-cautious comment asked for — it exercises
    the exact path the Computer-Use loop uses (GrokBrain → _openai_base
    image_url). Skips cleanly when no grok key is present.
    """
    import base64
    import io

    from jarvis.core import config as cfg

    ep = cfg.resolve_provider_endpoint("grok", vendor_default_base_url="https://api.x.ai/v1")
    if not ep.credential:
        pytest.skip("no grok API key configured — live vision proof skipped")

    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        pytest.skip("Pillow not available to build the test image")

    img = Image.new("RGB", (96, 96), (220, 20, 20))  # solid red
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    from jarvis.brain.streaming import aggregate
    from jarvis.core.protocols import BrainRequest

    block = ImageBlock(mime="image/png", data_b64=b64, source_hash="red")
    req = BrainRequest(
        messages=(BrainMessage(
            role="user",
            content="What single color fills this image? Answer with one word.",
            images=(block,),
        ),),
        system=None,
        temperature=0.0,
        max_tokens=20,
        stream=True,
    )

    brain = GrokBrain(model="grok-4.3")
    agg = await aggregate(brain.complete(req))
    text = (agg.text or "").strip().lower()

    assert "red" in text, f"grok did not see the red image (got: {text!r})"
