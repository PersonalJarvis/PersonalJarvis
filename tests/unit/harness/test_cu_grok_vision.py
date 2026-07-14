"""Computer-Use must reach a vision-capable provider — provider-agnostically.

Live forensic 2026-06-21 18:41: a "open Chrome with computer use …" command
DID dispatch to the screenshot harness (routing fixed), but the harness gave up:

    exit 2 · [cu] giving up after 3 model failures … ComputerUseLoop provider
    chain failed: 3 provider(s) skipped — no vision; claude-api(haiku):
    incomplete chunked read; openrouter(opus-4.8): Kein O…  # i18n-allow: verbatim forensic log excerpt from the live incident, not translatable without falsifying the record

Root cause: ``screenshot_only_loop._call_brain`` skips every provider whose
``supports_vision`` is False when a screenshot is attached, and the one live
vision-capable provider had been filtered out of the chain by a stale
``_dead_providers`` flag. CU must not be permanently disabled by a transient
dead-flag on its only eyes.

The fix is PROVIDER-AGNOSTIC (AP-21): the CU loop dispatches the screenshot to
whichever vision-capable provider leads the chain, falls through a blind active
provider to the next vision-capable one, and — as a last resort —
``computer_use_planner.iter_last_resort_vision`` tries every registered
vision-capable provider IGNORING the transient dead/cooldown flags. The only
gate anywhere is ``supports_vision`` — never a provider name.

(Grok was the live example in the original forensic; it remains
as a brain provider. These tests therefore pin the generic mechanism across the
remaining vision-capable providers, which is what AP-21 actually mandates.)
"""
from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.protocols import (
    ImageBlock,
    Observation,
)
from jarvis.harness.screenshot_only_loop import CULoopError, _call_brain

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
# 1. The sibling CLI brain (codex) must report vision per RUNTIME path: the
#    ChatGPT-CLI path drops images (blind) → supports_vision must be False so
#    the CU loop skips it and reaches a vision-capable provider; the API-key
#    path can see → True. A static True made CU dispatch a screenshot to the
#    blind CLI brain.
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
# 2. PROVIDER-AGNOSTIC proof: CU is not pinned to any provider. For EACH
#    vision-capable provider set as the active/leading brain, ``_call_brain``
#    must dispatch the screenshot to THAT provider. The selection is
#    capability-gated, never provider-name-gated.
# ---------------------------------------------------------------------------


_VISION_PROVIDERS = ("claude-api", "openrouter", "openai", "gemini")


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
    """For EACH vision-capable provider, when it is the active/leading brain the
    CU loop dispatches the screenshot to IT — not to any hardcoded provider.
    This is the provider-agnosticism guarantee."""
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
    """A blind active provider (codex-like, ``supports_vision=False``)
    must be skipped and the screenshot must fall through to WHICHEVER
    vision-capable provider is next — proving the fallthrough is generic, not
    pinned to one provider."""
    blind = _StreamingBrain(
        text='{"action": "click", "x": 1, "y": 1}', supports_vision=False,
    )
    seeing = _StreamingBrain(text='{"action": "done"}', supports_vision=True)

    class _BlindThenVisionManager:
        active_provider = "codex"

        def __init__(self) -> None:
            self.requested: list[tuple[str, str | None]] = []

        def _build_fallback_chain(self, level: str) -> list[tuple[str, str | None]]:
            return [
                ("codex", "gpt-5.5"),
                (vision_provider, f"{vision_provider}-model"),
            ]

        def _get_brain(self, name: str, model: str | None = None) -> _StreamingBrain:
            self.requested.append((name, model))
            return blind if name == "codex" else seeing

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


# ---------------------------------------------------------------------------
# 3. STALE DEAD-FLAG RESILIENCE (live forensic 2026-06-21 18:41, exit 2):
#    "[cu] giving up after 3 model failures … provider chain failed: 3
#    provider(s) skipped — no vision". The ONE live, vision-capable brain was
#    NOT even in the tail: it had been filtered out of the chain entirely by a
#    stale ``_dead_providers`` flag (a recurring "spuriously flagged dead" bug).
#    With every other vision provider keyless/throttled and the live one
#    filtered out, CU had no vision brain and gave up. CU must not be
#    permanently disabled by a stale dead-flag on its only eyes.
# ---------------------------------------------------------------------------


async def test_cu_reaches_vision_brain_despite_stale_dead_flag() -> None:
    """The blind active provider leads and the one vision-capable provider
    (here gemini) was filtered out of the chain by a stale ``_dead_providers``
    flag, so the normal chain reaches NO vision brain. As a last resort CU must
    try every registered vision-capable provider IGNORING the transient dead/
    cooldown flags, and reach gemini — instead of failing "no vision"."""
    seeing = _StreamingBrain(text='{"action": "done"}', supports_vision=True)
    blind = _StreamingBrain(
        text='{"action": "click", "x": 1, "y": 1}', supports_vision=False,
    )

    class _Registry:
        def available(self) -> list[str]:
            return ["codex", "gemini"]

    class _DeadFlaggedVisionManager:
        active_provider = "codex"

        def __init__(self) -> None:
            self._dead_providers = {"gemini"}  # stale flag on the only vision brain
            self._registry = _Registry()
            self.requested: list[tuple[str, str | None]] = []

        def _build_fallback_chain(self, level: str) -> list[tuple[str, str | None]]:
            # The real builder filters dead providers OUT, so gemini is gone and
            # only the blind active provider survives → no vision brain in chain.
            return [("codex", "gpt-5.5"), ("codex", "gpt-5.5-pro")]

        def _fast_model(self, name: str) -> str | None:
            return {"codex": "gpt-5.5", "gemini": "gemini-3.1-pro-preview"}.get(name)

        def _get_brain(self, name: str, model: str | None = None) -> Any:
            self.requested.append((name, model))
            if name == "codex":
                return blind
            if name == "gemini":
                return seeing
            raise AssertionError(f"unexpected provider {name!r}")

    manager = _DeadFlaggedVisionManager()
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

    assert raw == '{"action": "done"}', (
        "CU gave up 'no vision' despite a live, vision-capable provider behind a "
        "stale dead-flag"
    )
    assert blind.calls == 0, "the blind active provider must never plan a screenshot"
    assert seeing.calls == 1, "the last-resort must reach the vision-capable brain"
    assert ("gemini", "gemini-3.1-pro-preview") in manager.requested


async def test_cu_still_fails_honestly_when_no_vision_provider_exists() -> None:
    """When there is genuinely NO vision-capable provider anywhere (active blind,
    none else registered), CU still fails honestly with a 'vision' message — the
    last-resort must not invent a provider, loop, or hang."""
    blind = _StreamingBrain(
        text='{"action": "click", "x": 1, "y": 1}', supports_vision=False,
    )

    class _Registry:
        def available(self) -> list[str]:
            return ["codex"]

    class _OnlyBlindManager:
        active_provider = "codex"

        def __init__(self) -> None:
            self._dead_providers: set[str] = set()
            self._registry = _Registry()

        def _build_fallback_chain(self, level: str) -> list[tuple[str, str | None]]:
            return [("codex", "gpt-5.5")]

        def _fast_model(self, name: str) -> str | None:
            return "gpt-5.5"

        def _get_brain(self, name: str, model: str | None = None) -> Any:
            return blind

    manager = _OnlyBlindManager()
    ctx = make_ctx(FakeBrain())
    ctx.brain_manager = manager
    obs = Observation(
        trace_id=uuid4(), timestamp_ns=time.time_ns(),
        screenshot_path=None, screenshot_hash="x",
    )
    img = ImageBlock(mime="image/jpeg", data_b64="QQ==", source_hash="x")

    with pytest.raises(CULoopError) as excinfo:
        await _call_brain(
            ctx, observation=obs, user_goal="open chrome",
            history_text="", images_override=[img],
        )
    assert "vision" in str(excinfo.value).lower()
    assert blind.calls == 0
