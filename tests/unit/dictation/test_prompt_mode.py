"""Prompt Mode — a dictation comes out as the prompt a coding agent should get.

Three things are worth pinning, and none of them is "the model writes well":

1. **The doctrine is SHARED, not copied.** The Agentic IDE's composer and this
   pass must ask for the same thing — goal not route, English, no
   verification ritual, no reasoning echo — so the two rule texts are imported
   from the blueprint and asserted to be the very same strings. A second copy
   is how the two surfaces drift.
2. **Fail-open in every direction.** Off, empty, no writer, a writer that
   dies, a writer that is slow, a writer that answers with debris: each one
   hands the user their own words back with a status that says why. Nothing
   here may raise, and nothing may lose text.
3. **The guards are structural.** The polish pass's drift bands would reject
   every correct prompt (the words are SUPPOSED to move), so this pass judges
   the shape of the answer: a heading, a finished last line, the protected
   spellings still present.

No network: the writer is a fake Brain from ``tests/fakes``-style classes
defined here, never ``unittest.mock``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.agentic_ide import prompt_blueprint
from jarvis.core.config import DictationConfig
from jarvis.core.config_writer import DICTATION_SETTING_KEYS
from jarvis.core.protocols import BrainDelta, BrainRequest
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
    "it shows a blank screen i think it's in AuthHandler can you fix it"
)
GOOD = (
    "## Task\n"
    "Fix the login page so that a wrong password shows the error message "
    "instead of a blank screen.\n\n"
    "## Context\n"
    "- The user believes the cause is in AuthHandler.\n\n"
    "## Done when\n"
    "- A wrong password shows the error message again."
)


@dataclass
class FakeWriter:
    """A Brain that answers with a fixed text, or fails the way asked."""

    answer: str = GOOD
    name: str = "fake-writer"
    model: str = "fake-model"
    raise_exc: BaseException | None = None
    delay_s: float = 0.0
    requests: list[BrainRequest] = field(default_factory=list)

    async def complete(self, request: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(request)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.raise_exc is not None:
            raise self.raise_exc
        # Two chunks, so the collector is exercised as a stream.
        half = len(self.answer) // 2
        yield BrainDelta(content=self.answer[:half])
        yield BrainDelta(content=self.answer[half:])
        yield BrainDelta(finish_reason="stop")


def _cfg(**overrides: Any) -> DictationConfig:
    return DictationConfig(prompt_mode=True, **overrides)


# --------------------------------------------------------------------------- #
# The doctrine is shared with the Agentic IDE composer
# --------------------------------------------------------------------------- #


def test_the_prompt_carries_the_blueprints_rules_verbatim() -> None:
    system = build_prompt_mode_prompt()
    assert prompt_blueprint.GOAL_NOT_IMPLEMENTATION_RULE in system
    assert prompt_blueprint.FORBIDDEN_SUBJECTS_RULE in system
    # And the composer still carries the same two texts — one source, two
    # surfaces. If this fails, someone edited one copy.
    composer = prompt_blueprint.system_prompt("implement")
    assert prompt_blueprint.GOAL_NOT_IMPLEMENTATION_RULE in composer
    assert prompt_blueprint.FORBIDDEN_SUBJECTS_RULE in composer


def test_the_prompt_demands_english_and_the_task_heading() -> None:
    system = build_prompt_mode_prompt()
    assert "ENGLISH, whatever language the user spoke" in system
    assert "## Task" in system
    assert "INVENTING is forbidden" in system


def test_the_prompt_never_presumes_a_workspace() -> None:
    """No @files, no tree, no pane: the dictation has none of them."""
    system = build_prompt_mode_prompt()
    assert "## Key files" not in system
    assert "WORKSPACE" not in system
    assert "@path" not in system


def test_protected_terms_are_listed_in_the_shared_block() -> None:
    system = build_prompt_mode_prompt(["Jarvis", "AuthHandler", "", "jarvis"])
    assert "<protected terms" in system
    assert "AuthHandler" in system
    # De-duplicated case-insensitively, like the polish block.
    assert system.count("Jarvis") + system.count("jarvis") == 1


# --------------------------------------------------------------------------- #
# Structural guards
# --------------------------------------------------------------------------- #


def test_a_good_prompt_passes() -> None:
    assert prompt_guard_reason(RAW, GOOD) == ""


def test_a_headingless_answer_is_not_a_prompt() -> None:
    assert prompt_guard_reason(RAW, "Error: invalid model selection") == "not_a_prompt"
    assert prompt_guard_reason(RAW, "") == "not_a_prompt"


def test_a_prompt_that_stops_mid_sentence_is_truncated() -> None:
    assert prompt_guard_reason(RAW, "## Task\nFix the login page so that") == "truncated"


def test_a_dropped_protected_spelling_is_rejected() -> None:
    without = GOOD.replace("AuthHandler", "the auth handler")
    assert prompt_guard_reason(RAW, without, protected=["AuthHandler"]) == "lost_protected_term"
    # A protected term the user never SAID cannot be lost.
    assert prompt_guard_reason(RAW, without, protected=["Nova"]) == ""


# --------------------------------------------------------------------------- #
# The pass itself
# --------------------------------------------------------------------------- #


async def test_a_prompt_is_delivered_with_the_prompted_status() -> None:
    writer = FakeWriter()
    out = await compose_prompt(RAW, cfg=_cfg(), writer=writer)
    assert out.status == STATUS_PROMPTED
    assert out.text == GOOD
    assert out.provider == "fake-writer"
    assert out.model == "fake-model"
    assert out.latency_ms >= 0


async def test_the_transcript_travels_fenced_in_the_user_message() -> None:
    writer = FakeWriter()
    await compose_prompt(RAW, cfg=_cfg(), writer=writer)
    (request,) = writer.requests
    assert request.system is not None and "## Task" in request.system
    assert RAW not in request.system, "the transcript leaked into the system prompt"
    body = request.messages[0].content
    assert "<<<BEGIN TRANSCRIPT>>>" in body and RAW in body


async def test_the_writer_is_asked_to_think() -> None:
    """Unlike the pane composer this call is allowed a graded effort."""
    writer = FakeWriter()
    await compose_prompt(RAW, cfg=_cfg(), writer=writer)
    assert writer.requests[0].reasoning_effort == "medium"


async def test_off_returns_the_raw_text_without_a_call() -> None:
    writer = FakeWriter()
    out = await compose_prompt(RAW, cfg=DictationConfig(), writer=writer)
    assert (out.status, out.text) == ("off", RAW)
    assert writer.requests == []


async def test_empty_input_is_skipped() -> None:
    out = await compose_prompt("   ", cfg=_cfg(), writer=FakeWriter())
    assert out.status == "skipped_short"


async def test_a_dead_writer_costs_the_prompt_not_the_words() -> None:
    writer = FakeWriter(raise_exc=RuntimeError("429 quota"))
    out = await compose_prompt(RAW, cfg=_cfg(), writer=writer)
    assert (out.status, out.text, out.reason) == ("provider_error", RAW, "RuntimeError")


async def test_a_slow_writer_hits_the_ceiling() -> None:
    writer = FakeWriter(delay_s=0.5)
    out = await compose_prompt(RAW, cfg=_cfg(), writer=writer, timeout_s=0.05)
    assert (out.status, out.text, out.reason) == ("timeout", RAW, "deadline")


async def test_debris_from_the_writer_is_rejected() -> None:
    writer = FakeWriter(answer="Error: requires --effort")
    out = await compose_prompt(RAW, cfg=_cfg(), writer=writer)
    assert (out.status, out.text, out.reason) == ("rejected_drift", RAW, "not_a_prompt")


async def test_a_prompt_ending_on_a_reference_is_finished_cleanly() -> None:
    writer = FakeWriter(
        answer="## Task\nFix the login flow.\n\n## Context\n- Start in @src/auth.py"
    )
    out = await compose_prompt(RAW, cfg=_cfg(), writer=writer)
    assert out.status == STATUS_PROMPTED
    assert not prompt_blueprint.ends_on_reference(out.text)
    assert out.text.startswith("## Task")


async def test_no_writer_on_this_host_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.dictation import prompt_mode

    monkeypatch.setattr(prompt_mode, "_resolve_writer", lambda budget: (None, ""))
    out = await compose_prompt(RAW, cfg=_cfg())
    assert (out.status, out.text, out.reason) == ("unavailable", RAW, "no_writer")


# --------------------------------------------------------------------------- #
# Config + vocabulary
# --------------------------------------------------------------------------- #


def test_ships_off_with_a_seconds_grade_ceiling() -> None:
    cfg = DictationConfig()
    assert cfg.prompt_mode is False
    assert cfg.prompt_mode_timeout_ms == 20_000
    assert prompt_mode_enabled(cfg) is False


@pytest.mark.parametrize(
    ("value", "expected_ms"),
    [(10, 2_000), (999_999, 120_000), ("nonsense", 20_000), (None, 20_000)],
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
