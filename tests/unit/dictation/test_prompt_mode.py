"""Prompt Mode — a dictation comes out as the prompt a coding agent should get.

Four things are worth pinning, and none of them is "the model writes well":

1. **The doctrine is SHARED, not copied.** The Agentic IDE's composer and this
   pass must ask for the same thing — goal not route, no verification
   ritual, no reasoning echo — so the two rule texts are imported from the
   blueprint and asserted to be the very same strings.
2. **The maintainer's bar (2026-08-24): 5-7 s at most, plain text, the transcript's
   language, a spoken register, and several tasks dictated at once all
   survive.** Each of those is a rule in the prompt or a guard on the answer,
   and each is asserted here so a later prompt revision cannot quietly drop
   one.
3. **Fail-open in every direction.** Off, empty, no provider, a provider that
   dies, a provider that is slow, an answer that is debris: each one hands the
   user their own words back with a status that says why.
4. **The guards are structural.** The polish pass's drift bands would reject
   every correct prompt (the words are SUPPOSED to move), so this pass judges
   the shape of the answer.

No network: the chain walk is replaced by a fake through ``monkeypatch``,
never ``unittest.mock``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.agentic_ide import prompt_blueprint
from jarvis.core.config import DictationConfig
from jarvis.core.config_writer import DICTATION_SETTING_KEYS
from jarvis.dictation import polish, prompt_mode
from jarvis.dictation.polish import POLISH_STATUSES
from jarvis.dictation.prompt_mode import (
    STATUS_PROMPTED,
    build_prompt_mode_prompt,
    compose_prompt,
    prompt_guard_reason,
    prompt_mode_enabled,
    timeout_budget_s,
)
from jarvis.ui.web.dictation_routes import SettingsBody

RAW = (
    "okay so um the login page is broken again when you type a wrong password "
    "it shows a blank screen i think it's in AuthHandler can you fix it and also "
    "rename the save button to submit"
)
GOOD = (
    "The login page is broken again. When you type a wrong password it shows a "
    "blank screen instead of the error message. I think it's in AuthHandler, "
    "please fix that.\n\n"
    "Also rename the save button to submit."
)


class FakeChain:
    """Stands in for the polish family chain walk."""

    def __init__(
        self,
        answer: str | None = GOOD,
        *,
        raise_exc: BaseException | None = None,
        delay_s: float = 0.0,
        error: str = "",
        on_device_only: bool = False,
    ) -> None:
        self.answer = answer
        self.raise_exc = raise_exc
        self.delay_s = delay_s
        self.error = error
        self.on_device_only = on_device_only
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, cfg: Any, **kw: Any) -> str | None:
        self.calls.append(kw)
        attempt = kw["attempt"]
        attempt.provider = "fake-family"
        attempt.model = "fake-model"
        attempt.error = self.error
        attempt.on_device_only = self.on_device_only
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.answer


@pytest.fixture(autouse=True)
def _fresh_breaker() -> None:
    polish.reset_polish_state()


def _cfg(**overrides: Any) -> DictationConfig:
    return DictationConfig(prompt_mode=True, **overrides)


def _install(monkeypatch: pytest.MonkeyPatch, chain: FakeChain) -> FakeChain:
    monkeypatch.setattr(prompt_mode, "_call_chain", chain)
    return chain


# --------------------------------------------------------------------------- #
# The doctrine is shared with the Agentic IDE composer
# --------------------------------------------------------------------------- #


def test_the_prompt_carries_the_blueprints_rules_verbatim() -> None:
    system = build_prompt_mode_prompt()
    assert prompt_blueprint.GOAL_NOT_IMPLEMENTATION_RULE in system
    assert prompt_blueprint.FORBIDDEN_SUBJECTS_RULE in system
    composer = prompt_blueprint.system_prompt("implement")
    assert prompt_blueprint.GOAL_NOT_IMPLEMENTATION_RULE in composer
    assert prompt_blueprint.FORBIDDEN_SUBJECTS_RULE in composer


# --------------------------------------------------------------------------- #
# The maintainer's bar, as rules in the prompt
# --------------------------------------------------------------------------- #


def test_the_prompt_asks_for_plain_text_in_the_spoken_language() -> None:
    system = build_prompt_mode_prompt()
    assert "PLAIN TEXT, SAME LANGUAGE" in system
    assert "No markdown" in system
    assert "language the transcript is in" in system
    # v1 asked for English and a markdown skeleton; neither may come back.
    assert "ENGLISH" not in system
    assert "## Task" not in system


def test_the_prompt_asks_for_a_spoken_register() -> None:
    system = build_prompt_mode_prompt()
    assert "SOUND LIKE A PERSON TALKING" in system
    assert "Everyday words" in system
    assert "No sets of three" in system


def test_the_prompt_keeps_every_task_dictated_at_once() -> None:
    system = build_prompt_mode_prompt()
    assert "SEVERAL TASKS AT ONCE" in system
    assert "Every one of them survives, in the order spoken" in system
    assert "Never merge two tasks into one" in system


def test_the_prompt_never_presumes_a_workspace() -> None:
    system = build_prompt_mode_prompt()
    assert "## Key files" not in system
    assert "WORKSPACE" not in system
    assert "@path" not in system


def test_protected_terms_are_listed_in_the_shared_block() -> None:
    system = build_prompt_mode_prompt(["Jarvis", "AuthHandler", "", "jarvis"])
    assert "<protected terms" in system
    assert "AuthHandler" in system
    assert system.count("Jarvis") + system.count("jarvis") == 1


# --------------------------------------------------------------------------- #
# Structural guards
# --------------------------------------------------------------------------- #


def test_a_good_prompt_passes() -> None:
    assert prompt_guard_reason(RAW, GOOD) == ""


def test_an_empty_answer_is_rejected() -> None:
    assert prompt_guard_reason(RAW, "   ") == "empty"


@pytest.mark.parametrize(
    "answer",
    [
        "## Task\nFix the login page.",
        "Fix the login page.\n\n```\nAuthHandler\n```",
        "**Task**: fix the login page.",
        "- fix the login page\n- rename the button",
        "1. fix the login page\n2. rename the button",
    ],
)
def test_markdown_is_rejected(answer: str) -> None:
    """v1 answered with headings; the answer is pasted as a chat message."""
    assert prompt_guard_reason(RAW, answer) == "markdown"


def test_a_prompt_that_stops_mid_sentence_is_truncated() -> None:
    assert prompt_guard_reason(RAW, "Fix the login page so that") == "truncated"


def test_a_dropped_protected_spelling_is_rejected() -> None:
    without = GOOD.replace("AuthHandler", "the auth handler")
    assert prompt_guard_reason(RAW, without, protected=["AuthHandler"]) == "lost_protected_term"
    assert prompt_guard_reason(RAW, without, protected=["Nova"]) == ""


# --------------------------------------------------------------------------- #
# The pass itself
# --------------------------------------------------------------------------- #


async def test_a_prompt_is_delivered_with_the_prompted_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = _install(monkeypatch, FakeChain())
    out = await compose_prompt(RAW, cfg=_cfg())
    assert out.status == STATUS_PROMPTED
    assert out.text == GOOD
    assert (out.provider, out.model) == ("fake-family", "fake-model")
    assert len(chain.calls) == 1


async def test_the_transcript_travels_fenced_in_the_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = _install(monkeypatch, FakeChain())
    await compose_prompt(RAW, cfg=_cfg())
    (call,) = chain.calls
    assert RAW not in call["system"], "the transcript leaked into the system prompt"
    assert "<<<BEGIN TRANSCRIPT>>>" in call["user"] and RAW in call["user"]


async def test_off_returns_the_raw_text_without_a_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = _install(monkeypatch, FakeChain())
    out = await compose_prompt(RAW, cfg=DictationConfig())
    assert (out.status, out.text) == ("off", RAW)
    assert chain.calls == []


async def test_empty_input_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeChain())
    out = await compose_prompt("   ", cfg=_cfg())
    assert out.status == "skipped_short"


async def test_no_provider_on_this_host_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, FakeChain(answer=None, error="no_credential"))
    out = await compose_prompt(RAW, cfg=_cfg())
    assert (out.status, out.text, out.reason) == ("unavailable", RAW, "no_credential")


async def test_a_dead_chain_costs_the_prompt_not_the_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, FakeChain(answer=None, error="provider_error"))
    out = await compose_prompt(RAW, cfg=_cfg())
    assert (out.status, out.text, out.reason) == ("provider_error", RAW, "provider_error")


async def test_an_on_device_only_chain_reports_local_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, FakeChain(answer=None, error="local_unreachable", on_device_only=True))
    out = await compose_prompt(RAW, cfg=_cfg())
    assert (out.status, out.text) == ("local_only", RAW)


async def test_a_slow_chain_hits_the_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeChain(delay_s=0.5))
    out = await compose_prompt(RAW, cfg=_cfg(), timeout_s=0.05)
    assert (out.status, out.text, out.reason) == ("timeout", RAW, "deadline")


async def test_debris_from_the_chain_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeChain(answer="## Task\nfix it"))
    out = await compose_prompt(RAW, cfg=_cfg())
    assert (out.status, out.text, out.reason) == ("rejected_drift", RAW, "markdown")


async def test_a_raising_chain_is_a_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeChain(raise_exc=RuntimeError("boom")))
    out = await compose_prompt(RAW, cfg=_cfg())
    assert (out.status, out.text, out.reason) == ("provider_error", RAW, "unexpected")


async def test_the_chain_is_asked_for_the_stronger_fast_model() -> None:
    """The real walk passes ``translating=True`` — pinned by reading the source,
    because the fake above replaces exactly that call."""
    import inspect

    assert "translating=True" in inspect.getsource(prompt_mode._call_chain)


# --------------------------------------------------------------------------- #
# Config + vocabulary
# --------------------------------------------------------------------------- #


def test_ships_off_with_a_six_second_ceiling() -> None:
    cfg = DictationConfig()
    assert cfg.prompt_mode is False
    assert cfg.prompt_mode_timeout_ms == 6_000
    assert prompt_mode_enabled(cfg) is False


@pytest.mark.parametrize(
    ("value", "expected_ms"),
    [(10, 2_000), (999_999, 10_000), ("nonsense", 6_000), (None, 6_000)],
)
def test_the_ceiling_is_clamped_never_rejected(value: Any, expected_ms: int) -> None:
    """AP-16: a typo in jarvis.toml must never cost a boot."""
    cfg = DictationConfig(prompt_mode_timeout_ms=value)
    assert cfg.prompt_mode_timeout_ms == expected_ms
    assert timeout_budget_s(cfg) == expected_ms / 1000


def test_the_keys_persist_and_reach_the_settings_route() -> None:
    for key in ("prompt_mode", "prompt_mode_timeout_ms"):
        assert key in DICTATION_SETTING_KEYS, key
        assert key in DictationConfig.model_fields, key
        assert key in SettingsBody.model_fields, key


def test_prompted_is_part_of_the_shared_status_vocabulary() -> None:
    assert STATUS_PROMPTED in POLISH_STATUSES
