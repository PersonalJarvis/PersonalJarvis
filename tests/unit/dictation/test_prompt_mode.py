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
    closing_thanks,
    compose_prompt,
    ends_with_thanks,
    ensure_closing_thanks,
    guess_language,
    normalize_prompt_text,
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
    "Also rename the save button to submit.\n\n"
    "Thanks!"
)
# The same prompt without the closing line — what a model that skipped the
# last rule hands back, and what the code must finish.
GOOD_WITHOUT_THANKS = GOOD.removesuffix("\n\nThanks!")


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


def test_the_prompt_asks_for_a_courteous_spoken_register() -> None:
    system = build_prompt_mode_prompt()
    assert "SOUND LIKE A PERSON WRITING TO A COLLEAGUE" in system
    assert "Courteous, not commanding" in system
    # Measured live 2026-08-27: the fast chain answered a "du" transcript with
    # "Bitte prüfen Sie" — the formal form the user never used.
    assert "Address the agent the way the user did" in system
    assert "never switch to the formal one" in system
    assert "Everyday words" in system
    assert "No sets of three" in system


def test_the_prompt_describes_the_shape_of_a_professional_request() -> None:
    """v3 (2026-08-27): situation, goal, limits, thanks — as paragraphs, no
    labels, and the observed symptom is the part that must survive."""
    system = build_prompt_mode_prompt()
    assert "WHAT A GOOD REQUEST CONTAINS" in system
    assert "with no labels in front of them" in system
    assert "keep every concrete detail" in system
    assert "one short line of thanks on its own line" in system
    assert "FIX WHAT EXISTS" in system
    assert "SAY EACH THING ONCE" in system
    assert "no typographic hyphens" in system


def test_the_prompt_carries_one_worked_example_per_language_it_ships_for() -> None:
    """A fast model follows a shown shape far better than a described one;
    one German and one English pair, each ending on its thanks line."""
    system = build_prompt_mode_prompt()
    assert system.count("Transcript: ") == 2
    assert system.count("Message:\n") == 2
    assert "\nDanke!\n" in system
    assert system.rstrip().endswith("(none)") or "Thanks!" in system


def test_the_prompt_version_names_the_v3_revision() -> None:
    assert prompt_mode.PROMPT_MODE_PROMPT_VERSION == 3


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


# --------------------------------------------------------------------------- #
# Typography — what a fast model leaves behind and the code takes out
# --------------------------------------------------------------------------- #


def test_non_breaking_hyphens_inside_words_become_hyphen_minus() -> None:
    """Measured live 2026-08-27: "Jarvis‑Bar" and "Self‑Hosted" came back with
    U+2011, which no grep for the spoken spelling ever matches."""
    assert normalize_prompt_text("Jarvis‑Bar and Self‑Hosted") == (
        "Jarvis-Bar and Self-Hosted"
    )
    assert normalize_prompt_text("Prompt‐Modus") == "Prompt-Modus"
    assert normalize_prompt_text("Prompt–Modus") == "Prompt-Modus"


def test_a_spaced_dash_is_left_alone() -> None:
    """An aside the model wrote as " – " is punctuation, not an identifier."""
    assert normalize_prompt_text("fix it – today") == "fix it – today"


def test_special_spaces_trailing_spaces_and_blank_runs_are_cleaned() -> None:
    raw = "first line  \nsecond line \n\n\n\nthird line\t\n"
    assert normalize_prompt_text(raw) == "first line\nsecond line\n\nthird line"


async def test_normalisation_runs_before_the_protected_term_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A protected spelling the model wrote with a U+2011 still counts as
    present, and the delivered text carries the plain hyphen."""
    raw = RAW.replace("AuthHandler", "Auth-Handler")
    answer = GOOD.replace("AuthHandler", "Auth‑Handler")
    _install(monkeypatch, FakeChain(answer=answer))
    out = await compose_prompt(raw, cfg=_cfg(), protected_terms=["Auth-Handler"])
    assert out.status == STATUS_PROMPTED
    assert "Auth-Handler" in out.text and "‑" not in out.text


# --------------------------------------------------------------------------- #
# The closing thanks — asked of the model, guaranteed by the code
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "closing",
    ["Danke!", "Vielen Dank im Voraus!", "Thanks!", "Thank you.", "Thanks a lot.", "¡Gracias!"],
)
def test_a_short_closing_line_of_thanks_is_recognised(closing: str) -> None:
    assert ends_with_thanks(f"Fix the login page.\n\n{closing}") is True


@pytest.mark.parametrize(
    "last_line",
    [
        "Please fix the login page.",
        "Danke für den Hinweis, aber die Seite ist trotzdem kaputt und zeigt nichts an.",
        "",
    ],
)
def test_a_line_that_merely_mentions_thanks_is_not_a_closing(last_line: str) -> None:
    assert ends_with_thanks(f"Fix the login page.\n\n{last_line}") is False


def test_a_missing_closing_is_appended_in_the_resolved_language() -> None:
    assert ensure_closing_thanks("Bitte repariere die Seite.", language="de") == (
        "Bitte repariere die Seite.\n\nDanke!"
    )
    assert ensure_closing_thanks("Please fix the page.", language="en") == (
        "Please fix the page.\n\nThanks!"
    )
    assert ensure_closing_thanks("Arregla la página.", language="es") == (
        "Arregla la página.\n\n¡Gracias!"
    )


def test_an_existing_closing_is_never_doubled() -> None:
    assert ensure_closing_thanks(GOOD, language="de") == GOOD


def test_an_unresolved_language_is_read_off_the_text() -> None:
    german = "Bitte bring die Indikatoren in Ordnung, sie zeigen nicht den echten Zustand."
    assert guess_language(german, "auto") == "de"
    assert guess_language("Please fix the login page.", "") == "en"
    assert guess_language("anything", "de-DE") == "de"
    assert ensure_closing_thanks(german, language="auto").endswith("\n\nDanke!")


def test_a_language_the_table_does_not_know_closes_in_english() -> None:
    assert closing_thanks("xx") == "Thanks!"
    assert ensure_closing_thanks("Napraw stronę logowania.", language="pl").endswith("Thanks!")


async def test_a_model_that_skipped_the_thanks_still_delivers_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, FakeChain(answer=GOOD_WITHOUT_THANKS))
    out = await compose_prompt(RAW, cfg=_cfg(), language="en")
    assert out.status == STATUS_PROMPTED
    assert out.text == GOOD


async def test_the_thanks_never_masks_a_truncated_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Appending "Thanks!" to a prompt that stopped mid-sentence would make the
    truncation guard blind; the guard runs first and the words come back raw."""
    _install(monkeypatch, FakeChain(answer="Fix the login page so that"))
    out = await compose_prompt(RAW, cfg=_cfg(), language="en")
    assert (out.status, out.text, out.reason) == ("rejected_drift", RAW, "truncated")
