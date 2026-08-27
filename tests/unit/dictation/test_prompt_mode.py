"""Prompt Mode — a dictation comes out as the prompt a coding agent should get.

Five things are worth pinning, and none of them is "the model writes well":

1. **The doctrine is SHARED, not copied.** The Agentic IDE's composer and this
   pass must ask for the same thing — goal not route, no verification
   ritual, no reasoning echo — so the two rule texts are imported from the
   blueprint and asserted to be the very same strings.
2. **The writer THINKS before it answers (v4, 2026-08-27).** A model spends
   time by emitting tokens, so the answer is two blocks: a written pass over
   the transcript, then the message. The pass is parsed off and thrown away,
   and a model that skips it is the v3 defect this revision exists to fix.
3. **The maintainer's bar: plain text, the transcript's language, the
   speaker's own words, several tasks dictated at once all surviving, and
   nothing after the last piece of substance.** Each is a rule in the prompt
   or a guard on the answer, asserted here so a later revision cannot quietly
   drop one.
4. **Fail-open in every direction.** Off, empty, no provider, a provider that
   dies, a provider that is slow, an answer that is debris: each one hands the
   user their own words back with a status that says why.
5. **The guards are structural and factual, never stylistic.** The polish
   pass's drift bands would reject every correct prompt (the words are
   SUPPOSED to move), so this pass judges the shape of the answer and what it
   LOST — a literal the user spoke, or half the context they gave.

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
    analysis_word_count,
    build_prompt_mode_prompt,
    compose_prompt,
    ends_with_sign_off,
    extract_prompt_block,
    looks_thinned,
    lost_literals,
    normalize_prompt_text,
    prompt_guard_reason,
    prompt_mode_enabled,
    strip_closing_sign_off,
    timeout_budget_s,
    transcript_literals,
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
ANALYSIS = (
    "1. TASKS (2): fix the blank screen; rename the save button to submit.\n"
    "2. LITERALS: login page, AuthHandler, save, submit.\n"
    "3. SITUATION: a wrong password shows a blank screen.\n"
    "4. LIMITS: none stated.\n"
    "5. WORDING TO KEEP: broken again, I think it's in.\n"
    "6. NOT SAID: which file exactly."
)
#: What a well-behaved model returns: the working pass, then the message.
ANSWER = f"<analysis>\n{ANALYSIS}\n</analysis>\n<prompt>\n{GOOD}\n</prompt>"


class FakeChain:
    """Stands in for the polish family chain walk."""

    def __init__(
        self,
        answer: str | None = ANSWER,
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
# v4: the writer thinks before it answers
# --------------------------------------------------------------------------- #


def test_the_prompt_demands_an_analysis_before_the_message() -> None:
    """The whole point of v4. v3 asked for the message directly and a fast
    model wrote it in under a second — no pass over the transcript at all."""
    system = build_prompt_mode_prompt()
    assert "ANSWER IN TWO BLOCKS" in system
    assert "NEVER SKIP THE FIRST" in system
    assert "<analysis>" in system and "</analysis>" in system
    assert "<prompt>" in system and "</prompt>" in system
    assert "PHASE 1 - THE ANALYSIS" in system
    assert "PHASE 2 - THE MESSAGE" in system


def test_the_analysis_names_all_six_points_it_must_work_through() -> None:
    system = build_prompt_mode_prompt()
    for point in ("1. TASKS", "2. LITERALS", "3. THE SITUATION", "4. LIMITS",
                  "5. WORDING TO KEEP", "6. NOT SAID"):
        assert point in system, point
    assert "before you compose a single sentence" in system


def test_only_the_message_block_is_delivered() -> None:
    assert extract_prompt_block(ANSWER) == GOOD


def test_a_forgotten_opening_tag_still_yields_the_message() -> None:
    """A fast model drops the opening tag often enough to matter; everything
    after the analysis is the message either way."""
    ragged = f"<analysis>\n{ANALYSIS}\n</analysis>\n{GOOD}"
    assert extract_prompt_block(ragged) == GOOD


def test_a_missing_closing_tag_still_yields_the_message() -> None:
    """The answer ran out of budget mid-message. What it wrote comes out and
    faces the truncation guard, which is the thing that decides."""
    cut = f"<analysis>\n{ANALYSIS}\n</analysis>\n<prompt>\n{GOOD}"
    assert extract_prompt_block(cut) == GOOD


def test_an_answer_with_no_blocks_at_all_is_taken_whole() -> None:
    """The v3 shape. It is not thrown away — it faces the same guards it
    always faced, so a good message still gets through."""
    assert extract_prompt_block(GOOD) == GOOD


def test_the_analysis_is_measured_so_a_skipped_one_is_visible() -> None:
    assert analysis_word_count(ANSWER) > 20
    assert analysis_word_count(GOOD) == 0


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


def test_the_prompt_makes_the_speakers_own_words_the_default() -> None:
    """v4's second half: v3 reworded phrasing the user had chosen, down to
    the small words, and called it polish."""
    system = build_prompt_mode_prompt()
    assert "THE USER'S OWN WORDS ARE THE DEFAULT" in system
    assert "REWRITING IS THE EXCEPTION" in system
    assert "including the small ones" in system
    assert "A synonym you find more elegant is a change in meaning" in system


def test_the_prompt_asks_for_the_speakers_own_register() -> None:
    system = build_prompt_mode_prompt()
    assert "SOUND LIKE THE PERSON WHO SPOKE" in system
    # Measured live 2026-08-27: the fast chain answered a "du" transcript with
    # "Bitte prüfen Sie" — the formal form the user never used.
    assert "Address the agent the way the user did" in system
    assert "never switch to the formal one" in system
    assert "Everyday words" in system
    assert "No sets of three" in system
    # Courtesy is mirrored, never manufactured.
    assert "not added where they did not" in system


def test_the_prompt_forbids_a_closing_line() -> None:
    """v3 guaranteed a line of thanks in code; v4 removes it. The reader is a
    coding agent, and the maintainer asked for it gone (2026-08-27)."""
    system = build_prompt_mode_prompt()
    assert "NOTHING AFTER THE LAST PIECE OF SUBSTANCE" in system
    assert "No closing line of thanks" in system
    assert "no sign-off of any kind" in system
    # The v3 rule and its worked examples must not survive anywhere.
    assert "one short line of thanks on its own line" not in system
    assert "\nDanke!\n" not in system


def test_the_prompt_describes_the_shape_of_the_message() -> None:
    system = build_prompt_mode_prompt()
    assert "WHAT THE MESSAGE CONTAINS" in system
    assert "with no labels in front of them" in system
    assert "every concrete detail of it intact" in system
    assert "FIX WHAT EXISTS" in system
    assert "SAY EACH THING ONCE" in system
    assert "no typographic hyphens" in system


def test_the_prompt_shows_one_worked_two_block_answer() -> None:
    """A fast model follows a shown shape far better than a described one, and
    what has to be shown now is the SHAPE OF THE ANSWER, not of the message."""
    system = build_prompt_mode_prompt()
    assert system.count("Transcript: ") == 1
    assert "Answer:\n<analysis>" in system
    assert system.rstrip().endswith("</prompt>") or "</prompt>" in system


def test_the_prompt_version_names_the_v4_revision() -> None:
    assert prompt_mode.PROMPT_MODE_PROMPT_VERSION == 4


def test_the_prompt_keeps_every_task_dictated_at_once() -> None:
    system = build_prompt_mode_prompt()
    assert "SEVERAL TASKS AT ONCE" in system
    assert "Every task from point 1 of the analysis survives" in system
    assert "Never merge two" in system


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
    """v1 answered with headings; the answer is pasted as a chat message. It
    is also what a leaked analysis looks like, which is the v4 way to fail."""
    assert prompt_guard_reason(RAW, answer) == "markdown"


def test_a_prompt_that_stops_mid_sentence_is_truncated() -> None:
    assert prompt_guard_reason(RAW, "Fix the login page so that") == "truncated"


def test_a_dropped_protected_spelling_is_rejected() -> None:
    without = GOOD.replace("AuthHandler", "the auth handler")
    assert prompt_guard_reason(RAW, without, protected=["AuthHandler"]) == "lost_protected_term"
    assert prompt_guard_reason(RAW, without, protected=["Nova"]) == ""


# --------------------------------------------------------------------------- #
# Fidelity guards — what the message LOST, never how it reads
# --------------------------------------------------------------------------- #


def test_the_literals_of_a_transcript_are_its_identifiers_numbers_and_quotes() -> None:
    spoken = (
        'change src/auth/handler.ts and AuthHandler so the retry_count is 30 '
        'and the button says "Sign in"'
    )
    found = {token.casefold() for token in transcript_literals(spoken)}
    assert "src/auth/handler.ts" in found
    assert "authhandler" in found
    assert "retry_count" in found
    assert "30" in found
    assert "sign in" in found


def test_an_apostrophe_is_never_read_as_an_opening_quote() -> None:
    """"it's" and "don't" in one sentence would otherwise turn everything
    between them into a literal the message has to reproduce verbatim."""
    assert transcript_literals("it's broken and i don't know why") == []


def test_ordinary_words_are_not_literals() -> None:
    """The message is allowed to rephrase prose. A guard over every noun would
    reject every honest rewrite, which is the opposite of the point."""
    assert transcript_literals("please fix the login page it is broken") == []


def test_a_message_that_drops_the_files_the_user_named_is_rejected() -> None:
    spoken = (
        "please fix src/auth/handler.ts and also AuthHandler and the retry_count "
        "setting and the timeout_ms value, they are all wrong since yesterday"
    )
    vague = (
        "Please fix the authentication code, some settings in there are wrong "
        "since yesterday and need to be corrected so that logging in works again."
    )
    assert len(lost_literals(spoken, vague)) >= 4
    assert prompt_guard_reason(spoken, vague) == "dropped_detail"


def test_one_missing_literal_is_a_slip_not_a_rejection() -> None:
    """A recognizer writes the same spoken name two ways in one breath;
    rejecting on one of those costs a good message."""
    spoken = (
        "please fix src/auth/handler.ts and AuthHandler and the retry_count "
        "setting, the login has been broken since yesterday afternoon"
    )
    almost = (
        "Please fix src/auth/handler.ts and the retry_count setting. The login "
        "has been broken since yesterday afternoon and needs to work again."
    )
    assert lost_literals(spoken, almost) == ["AuthHandler"]
    assert prompt_guard_reason(spoken, almost) == ""


def test_a_message_collapsed_to_a_fraction_of_the_transcript_is_rejected() -> None:
    """The failure the maintainer reported: it reads well and quietly leaves
    out half of what was said."""
    spoken = (
        "so the indicators that show whether a session is working or finished "
        "are wrong again, a working session shows up as finished and the other "
        "way round, and they never update live either, that has been the case "
        "since the last update and it makes the whole overview useless to me"
    )
    thin = "Please fix the session indicators."
    assert looks_thinned(spoken, thin) is True
    assert prompt_guard_reason(spoken, thin) == "dropped_context"


def test_a_short_transcript_is_never_measured_for_thinning() -> None:
    """"fix the typo on the login page" is a complete request at seven words,
    and its message is allowed to be shorter still."""
    assert looks_thinned("fix the typo on the login page", "Please fix the typo.") is False


def test_a_tightened_message_is_not_a_thinned_one() -> None:
    """A dictation is 25-40 % filler. Cutting that is the job, not the defect."""
    assert looks_thinned(RAW, GOOD) is False
    assert prompt_guard_reason(RAW, GOOD) == ""


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


async def test_the_analysis_never_reaches_the_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It is a working pass, not an answer. A user who sees "1. TASKS (2)" in
    their text field has been handed the model's scratchpad."""
    _install(monkeypatch, FakeChain())
    out = await compose_prompt(RAW, cfg=_cfg())
    assert "TASKS" not in out.text
    assert "<analysis>" not in out.text and "<prompt>" not in out.text


async def test_the_transcript_travels_fenced_in_the_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = _install(monkeypatch, FakeChain())
    await compose_prompt(RAW, cfg=_cfg())
    (call,) = chain.calls
    assert RAW not in call["system"], "the transcript leaked into the system prompt"
    assert "<<<BEGIN TRANSCRIPT>>>" in call["user"] and RAW in call["user"]


def test_the_budget_covers_the_analysis_as_well_as_the_message() -> None:
    """Both blocks come out of ONE token budget, so it has to be generous
    enough that a full inventory does not cut the message off — a message that
    stops mid-sentence is rejected and costs the whole pass.

    Pinned by reading the source, like ``translating=True`` below: the fake
    chain replaces ``_call_chain`` itself, which is where these are passed on.
    """
    import inspect

    source = inspect.getsource(prompt_mode._call_chain)
    assert "max_output_tokens=_MAX_OUTPUT_TOKENS" in source
    assert prompt_mode._MAX_OUTPUT_TOKENS >= 3_000
    assert prompt_mode._TEMPERATURE == 0.0


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


async def test_an_answer_that_is_only_an_analysis_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model worked and then forgot to write the message. Nothing follows
    the analysis, so nothing is delivered — the user gets their own words, not
    the model's numbered worksheet."""
    _install(monkeypatch, FakeChain(answer=f"<analysis>\n{ANALYSIS}\n</analysis>"))
    out = await compose_prompt(RAW, cfg=_cfg())
    assert (out.status, out.text, out.reason) == ("rejected_drift", RAW, "empty")


async def test_an_analysis_written_without_its_tags_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other way to leak a worksheet: no tags at all, so the whole answer
    is taken as the message and the markdown guard catches the numbering."""
    _install(monkeypatch, FakeChain(answer=ANALYSIS))
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


def test_ships_off_with_a_twelve_second_ceiling() -> None:
    """v4 raised it from 6 s: the call now carries a written pass over the
    transcript in front of the message, and a real dictation spends 6-9 s on
    the pair."""
    cfg = DictationConfig()
    assert cfg.prompt_mode is False
    assert cfg.prompt_mode_timeout_ms == 12_000
    assert prompt_mode_enabled(cfg) is False


@pytest.mark.parametrize(
    ("value", "expected_ms"),
    [(10, 4_000), (999_999, 20_000), ("nonsense", 12_000), (None, 12_000)],
)
def test_the_ceiling_is_clamped_never_rejected(value: Any, expected_ms: int) -> None:
    """AP-16: a typo in jarvis.toml must never cost a boot."""
    cfg = DictationConfig(prompt_mode_timeout_ms=value)
    assert cfg.prompt_mode_timeout_ms == expected_ms
    assert timeout_budget_s(cfg) == expected_ms / 1000


def test_the_floor_leaves_room_for_the_analysis() -> None:
    """Below the floor the analysis cannot finish, so the pass would only ever
    time out — which is strictly worse than the switch being off."""
    assert timeout_budget_s(DictationConfig(prompt_mode_timeout_ms=500)) >= 4.0


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
    raw = "first line  \nsecond line \n\n\n\nthird line\t\n"
    assert normalize_prompt_text(raw) == "first line\nsecond line\n\nthird line"


async def test_normalisation_runs_before_the_protected_term_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A protected spelling the model wrote with a U+2011 still counts as
    present, and the delivered text carries the plain hyphen."""
    raw = RAW.replace("AuthHandler", "Auth-Handler")
    answer = ANSWER.replace("AuthHandler", "Auth‑Handler")
    _install(monkeypatch, FakeChain(answer=answer))
    out = await compose_prompt(raw, cfg=_cfg(), protected_terms=["Auth-Handler"])
    assert out.status == STATUS_PROMPTED
    assert "Auth-Handler" in out.text and "‑" not in out.text


# --------------------------------------------------------------------------- #
# The closing line — asked away in the prompt, cut in code
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "closing",
    ["Danke!", "Vielen Dank!", "Thanks!", "Thank you.", "Thanks a lot.", "¡Gracias!",
     "Best regards", "Cheers", "Viele Grüße"],
)
def test_a_short_courtesy_line_is_recognised(closing: str) -> None:
    assert ends_with_sign_off(f"Fix the login page.\n\n{closing}") is True


@pytest.mark.parametrize(
    "last_line",
    [
        "Please fix the login page.",
        "Danke für den Hinweis, aber die Seite ist trotzdem kaputt und zeigt nichts an.",
        "",
    ],
)
def test_a_line_that_merely_mentions_thanks_is_not_a_sign_off(last_line: str) -> None:
    assert ends_with_sign_off(f"Fix the login page.\n\n{last_line}") is False


def test_a_trailing_thanks_is_cut() -> None:
    assert strip_closing_sign_off("Bitte repariere die Seite.\n\nDanke!") == (
        "Bitte repariere die Seite."
    )
    assert strip_closing_sign_off("Please fix the page.\n\nThanks!") == "Please fix the page."


def test_a_stacked_sign_off_is_cut_to_the_last_piece_of_substance() -> None:
    """A model that writes "Danke!" under "Viele Grüße" has written two."""
    assert strip_closing_sign_off("Bitte repariere die Seite.\n\nViele Grüße\n\nDanke!") == (
        "Bitte repariere die Seite."
    )


def test_a_message_without_a_sign_off_is_untouched() -> None:
    assert strip_closing_sign_off(GOOD) == GOOD


def test_a_one_line_message_is_never_stripped_to_nothing() -> None:
    """A transcript that IS a thank-you note keeps its only line."""
    assert strip_closing_sign_off("Danke!") == "Danke!"


async def test_a_model_that_still_signs_off_is_trimmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt asks for no closing line; this is the guarantee, the way
    ``ensure_closing_thanks`` was the guarantee of the opposite in v3."""
    signed = ANSWER.replace(f"{GOOD}\n</prompt>", f"{GOOD}\n\nDanke!\n</prompt>")
    _install(monkeypatch, FakeChain(answer=signed))
    out = await compose_prompt(RAW, cfg=_cfg(), language="de")
    assert out.status == STATUS_PROMPTED
    assert out.text == GOOD
    assert not out.text.endswith("Danke!")


async def test_the_trim_never_masks_a_truncated_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The truncation guard runs first, so a message that stopped mid-sentence
    is caught as damage rather than tidied into looking finished."""
    _install(monkeypatch, FakeChain(answer="Fix the login page so that"))
    out = await compose_prompt(RAW, cfg=_cfg(), language="en")
    assert (out.status, out.text, out.reason) == ("rejected_drift", RAW, "truncated")
