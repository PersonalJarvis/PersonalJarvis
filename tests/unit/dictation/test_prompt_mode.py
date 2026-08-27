"""Prompt Mode — a dictation comes out as the prompt the user asked for.

The thing this file mostly exists to prevent is the arc v2-v4 already ran
once: a real defect gets seen, a rule is added, the rule narrows what the
writer may produce, and eleven thousand characters later the instruction is a
list of prohibitions handed to a model that was perfectly capable without
them. So:

1. **The instruction stays short and stays permissive.** Its length is
   asserted. A prompt may be markdown, may have headings, sections and a role
   line — that is what a good prompt looks like, and v3's ``markdown``
   rejection was this feature refusing its own purpose.
2. **The worked example IS the specification.** It is the maintainer's own
   reference run (2026-08-27), and a shown run outweighs a written rule —
   measured on this very prompt, where a rule about language lost to an
   example in the other language.
3. **The guards are damage and fidelity, never shape.** Empty, truncated, a
   literal the user spoke gone missing, a prompt collapsed to a fraction of
   the transcript. Nothing about style, structure or register.
4. **Fail-open in every direction.** Off, empty, no provider, a provider that
   dies, one that is slow, an answer that is debris: each hands the user their
   own words back with a status that says why.

No network: the chain walk is replaced by a fake through ``monkeypatch``,
never ``unittest.mock``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.core.config import DictationConfig
from jarvis.core.config_writer import DICTATION_SETTING_KEYS
from jarvis.dictation import polish, prompt_mode
from jarvis.dictation.polish import POLISH_STATUSES
from jarvis.dictation.prompt_mode import (
    STATUS_PROMPTED,
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
#: What the writer is supposed to produce: a structured prompt, markdown and
#: all. Under v3 this exact answer was rejected as ``markdown``.
GOOD = (
    "# Role\n"
    "You are an experienced web developer working on this application's "
    "authentication flow.\n\n"
    "# Task\n"
    "1. The login page is broken again: typing a wrong password shows a blank "
    "screen instead of the error message. The user suspects AuthHandler. Find "
    "the cause and fix it so the error message is shown again.\n"
    "2. Rename the save button to submit.\n\n"
    "# Output format\n"
    "Present the diff per file, with a short note on what caused the blank "
    "screen."
)
ANSWER = f"<prompt>\n{GOOD}\n</prompt>"


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
# The instruction: short, permissive, and taught by its example
# --------------------------------------------------------------------------- #


def test_the_instruction_stays_short() -> None:
    """v4's was 13 207 characters of mostly prohibitions and produced worse
    prompts than a plain "write me a prompt" did. A number is a blunt guard,
    but it is the one that catches the failure this revision undid: the
    instruction growing a rule per defect until it fences the writer in."""
    assert len(prompt_mode._SYSTEM_PROMPT) < 6_000


def test_the_instruction_says_what_the_job_is() -> None:
    system = build_prompt_mode_prompt()
    assert "turned automatically into a finished, professional prompt" in system
    assert "Write the prompt it is asking for" in system


def test_nothing_in_the_instruction_forbids_the_shape_of_a_good_prompt() -> None:
    """The v3 rules, each of which forbade part of the maintainer's own
    reference output. None of them may come back."""
    system = build_prompt_mode_prompt()
    for banned in (
        "PLAIN TEXT",
        "No markdown",
        "no headings",
        "no bullet marks",
        "no code fences",
        "SOUND LIKE",
        "Everyday words",
    ):
        assert banned not in system, banned
    # ...and it says the opposite out loud.
    assert "headings, lists, markdown, sections" in system


def test_the_instruction_keeps_what_was_never_about_shape() -> None:
    """Fidelity to the transcript and no closing line: the two things the
    maintainer actually asked for."""
    system = build_prompt_mode_prompt()
    assert "goes in exactly as spoken" in system
    assert "does not exist" in system
    assert "left open rather than guessed at" in system
    assert "no closing line of thanks" in system


def test_the_language_rule_is_loud_because_the_example_is_german() -> None:
    """Measured live 2026-08-27: a quiet "write it in the language of the
    transcript" lost to an English instruction and produced an English prompt
    for a German dictation, twice."""
    system = build_prompt_mode_prompt()
    assert "WRITE IT IN THE LANGUAGE THE USER SPOKE" in system
    assert "These instructions are in English and that says nothing" in system
    assert "includes the headings and the section names" in system


def test_the_worked_example_is_the_maintainers_reference_run() -> None:
    """One run, shown whole: his dictated transcript and the prompt that was
    written for it. It carries what a page of rules used to."""
    system = build_prompt_mode_prompt()
    assert system.count("Transcript: ") == 1
    assert "kannst du mir bitte ein Prompt schreiben" in system
    assert "Du bist der spezialisierte Prompt-Engineering-Core" in system
    # The example output is exactly the shape v3 would have thrown away.
    assert "### ARBEITSABLAUF" in system
    assert "**Rolle / Systemkontext:**" in system


def test_the_prompt_version_names_the_v5_revision() -> None:
    assert prompt_mode.PROMPT_MODE_PROMPT_VERSION == 5


def test_protected_terms_are_listed_in_the_shared_block() -> None:
    system = build_prompt_mode_prompt(["Jarvis", "AuthHandler", "", "jarvis"])
    assert "<protected terms" in system
    assert "AuthHandler" in system


# --------------------------------------------------------------------------- #
# Getting the prompt out of the answer
# --------------------------------------------------------------------------- #


def test_the_fenced_prompt_is_what_is_delivered() -> None:
    assert extract_prompt_block(ANSWER) == GOOD


def test_an_introduction_in_front_of_the_fence_is_dropped() -> None:
    """The one reason the fence exists. The reference run's own first line was
    "Hier ist ein System-Prompt für deinen Auto-Prompting-Modus" — a model
    handed this job likes to introduce its work."""
    chatty = f"Hier ist ein System-Prompt für deinen Auto-Prompting-Modus.\n\n{ANSWER}"
    assert extract_prompt_block(chatty) == GOOD


def test_the_last_opening_tag_wins() -> None:
    """A model that names the tag while introducing itself would otherwise
    hand over the introduction."""
    chatty = f"I will now fill the <prompt> tag as asked.\n{ANSWER}"
    assert extract_prompt_block(chatty) == GOOD


def test_a_missing_closing_tag_still_yields_the_prompt() -> None:
    """It ran out of budget. What it wrote comes out and faces the truncation
    guard, which is the thing that decides whether it is usable."""
    assert extract_prompt_block(f"<prompt>\n{GOOD}") == GOOD


def test_an_answer_with_no_fence_is_taken_whole() -> None:
    """The model simply wrote the prompt, which is a fine thing to have done."""
    assert extract_prompt_block(GOOD) == GOOD


def test_an_empty_answer_yields_nothing() -> None:
    assert extract_prompt_block("   ") == ""


# --------------------------------------------------------------------------- #
# Guards: damage and fidelity, never shape
# --------------------------------------------------------------------------- #


def test_a_structured_markdown_prompt_passes() -> None:
    """The headline of v5. Under v3 this same answer was ``markdown`` and the
    user got their raw transcript instead of the prompt they asked for."""
    assert prompt_guard_reason(RAW, GOOD) == ""


#: The same brief in five shapes. Only the formatting differs — each carries
#: AuthHandler and enough of the transcript to clear the fidelity guards, so a
#: rejection here can only be about shape, which is the thing v5 stopped
#: judging.
_SHAPES = [
    "# Role\nYou are a web developer working on the login flow.\n\n# Task\n"
    "Fix the blank screen AuthHandler shows on a wrong password, so the error "
    "message appears again, and rename the save button to submit.",
    "- The login page shows a blank screen on a wrong password instead of the "
    "error message; the user suspects AuthHandler. Find the cause and fix it.\n"
    "- Rename the save button to submit.",
    "**Task**: fix the blank screen that AuthHandler shows on a wrong password "
    "so the error message appears again, and **rename** the save button to "
    "submit.",
    "1. Fix the blank screen on a wrong password, which the user suspects is in "
    "AuthHandler, so the error message shows again.\n"
    "2. Rename the save button to submit.",
    "Fix the blank screen on a wrong password so the error message shows again. "
    "The user suspects this file:\n\n```\nAuthHandler\n```\n\n"
    "Then rename the save button to submit.",
]


@pytest.mark.parametrize("answer", _SHAPES)
def test_no_shape_is_rejected_any_more(answer: str) -> None:
    assert prompt_guard_reason(RAW, answer) == ""


def test_an_empty_prompt_is_rejected() -> None:
    assert prompt_guard_reason(RAW, "   ") == "empty"


def test_a_prompt_that_stops_mid_sentence_is_truncated() -> None:
    assert prompt_guard_reason(RAW, "Fix the AuthHandler login page so that") == "truncated"


def test_a_dropped_protected_spelling_is_rejected() -> None:
    without = GOOD.replace("AuthHandler", "the auth handler")
    assert prompt_guard_reason(RAW, without, protected=["AuthHandler"]) == "lost_protected_term"
    assert prompt_guard_reason(RAW, without, protected=["Nova"]) == ""


def test_the_literals_of_a_transcript_are_its_identifiers_numbers_and_quotes() -> None:
    spoken = (
        'change src/auth/handler.ts and AuthHandler so the retry_count is 30 '
        'and the button says "Sign in"'
    )
    found = {token.casefold() for token in transcript_literals(spoken)}
    assert {"src/auth/handler.ts", "authhandler", "retry_count", "30", "sign in"} <= found


def test_an_apostrophe_is_never_read_as_an_opening_quote() -> None:
    """"it's" and "don't" in one sentence would otherwise turn everything
    between them into a literal the prompt has to reproduce verbatim."""
    assert transcript_literals("it's broken and i don't know why") == []


def test_ordinary_words_are_not_literals() -> None:
    """A prompt is a rewrite. A guard over every noun would reject every
    honest one, which is the opposite of the point."""
    assert transcript_literals("please fix the login page it is broken") == []


def test_a_prompt_that_drops_the_files_the_user_named_is_rejected() -> None:
    spoken = (
        "please fix src/auth/handler.ts and also AuthHandler and the retry_count "
        "setting and the timeout_ms value, they are all wrong since yesterday"
    )
    vague = (
        "# Task\nFix the authentication code. Some settings in there are wrong "
        "since yesterday and need to be corrected so that logging in works again."
    )
    assert len(lost_literals(spoken, vague)) >= 4
    assert prompt_guard_reason(spoken, vague) == "dropped_detail"


def test_one_missing_literal_is_a_slip_not_a_rejection() -> None:
    """A recognizer writes the same spoken name two ways in one breath;
    rejecting on one of those costs a good prompt."""
    spoken = (
        "please fix src/auth/handler.ts and AuthHandler and the retry_count "
        "setting, the login has been broken since yesterday afternoon"
    )
    almost = (
        "# Task\nFix src/auth/handler.ts and the retry_count setting. The login "
        "has been broken since yesterday afternoon and needs to work again."
    )
    assert lost_literals(spoken, almost) == ["AuthHandler"]
    assert prompt_guard_reason(spoken, almost) == ""


def test_a_prompt_collapsed_to_a_fraction_of_the_transcript_is_rejected() -> None:
    """A written prompt is normally LONGER than the dictation, so this only
    fires on a writer that summarised instead of writing."""
    spoken = (
        "so the indicators that show whether a session is working or finished "
        "are wrong again, a working session shows up as finished and the other "
        "way round, and they never update live either, that has been the case "
        "since the last update and it makes the whole overview useless to me"
    )
    thin = "Fix the session indicators."
    assert looks_thinned(spoken, thin) is True
    assert prompt_guard_reason(spoken, thin) == "dropped_context"


def test_a_short_transcript_is_never_measured_for_thinning() -> None:
    assert looks_thinned("fix the typo on the login page", "Fix the typo.") is False


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
    assert len(chain.calls) == 1, "one call — no scratchpad pass, no second opinion"


async def test_the_transcript_travels_fenced_in_the_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = _install(monkeypatch, FakeChain())
    await compose_prompt(RAW, cfg=_cfg())
    (call,) = chain.calls
    assert RAW not in call["system"], "the transcript leaked into the system prompt"
    assert "<<<BEGIN TRANSCRIPT>>>" in call["user"] and RAW in call["user"]


def test_the_budget_fits_a_written_out_prompt() -> None:
    """Pinned by reading the source, like ``translating=True`` below: the fake
    chain replaces ``_call_chain`` itself, which is where these are passed on.
    The reference output alone is about 700 tokens, and a cut-off prompt is
    rejected as truncated and costs the whole pass."""
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


async def test_a_truncated_answer_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeChain(answer="<prompt>\n# Task\nFix the AuthHandler so that"))
    out = await compose_prompt(RAW, cfg=_cfg())
    assert (out.status, out.text, out.reason) == ("rejected_drift", RAW, "truncated")


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
    """The ceiling is a safety net, not a target: the fast chain answers in
    2-6 s depending on the provider's hardware, and the bound only has to keep
    a long prompt on a slower family from being cut off."""
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
    assert normalize_prompt_text("Jarvis‑Bar and Self‑Hosted") == "Jarvis-Bar and Self-Hosted"
    assert normalize_prompt_text("Prompt‐Modus") == "Prompt-Modus"
    assert normalize_prompt_text("Prompt–Modus") == "Prompt-Modus"


def test_a_spaced_dash_is_left_alone() -> None:
    """An aside written as " – " is punctuation, not an identifier."""
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
# The closing line — asked away in the instruction, cut in code
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "closing",
    ["Danke!", "Vielen Dank!", "Thanks!", "Thank you.", "¡Gracias!", "Best regards",
     "Cheers", "Viele Grüße"],
)
def test_a_short_courtesy_line_is_recognised(closing: str) -> None:
    assert ends_with_sign_off(f"# Task\nFix the login page.\n\n{closing}") is True


@pytest.mark.parametrize(
    "last_line",
    [
        "Present the diff per file.",
        "Danke für den Hinweis, aber die Seite ist trotzdem kaputt und zeigt nichts an.",
        "",
    ],
)
def test_a_line_that_merely_mentions_thanks_is_not_a_sign_off(last_line: str) -> None:
    assert ends_with_sign_off(f"# Task\nFix the login page.\n\n{last_line}") is False


def test_a_trailing_thanks_is_cut() -> None:
    assert strip_closing_sign_off("# Task\nFix the page.\n\nDanke!") == "# Task\nFix the page."


@pytest.mark.parametrize(
    ("written", "kept"),
    [
        ("Setze retry_count auf 10. Danke dir.", "Setze retry_count auf 10."),
        ("Fix it. Thanks a lot!", "Fix it."),
        ("Please have a look. Thanks in advance.", "Please have a look."),
    ],
)
def test_a_thanks_tacked_onto_the_last_sentence_is_cut(written: str, kept: str) -> None:
    """Measured live 2026-08-27 on gpt-oss: the model did not put its thanks on
    a line of its own, it appended it to the last paragraph — where a
    line-anchored guard never sees it. The user had said "danke dir" at the end
    of the dictation, and the writer carried it through."""
    assert ends_with_sign_off(written) is True
    assert strip_closing_sign_off(written) == kept


def test_a_stacked_sign_off_is_cut_to_the_last_piece_of_substance() -> None:
    assert strip_closing_sign_off("Fix the page.\n\nViele Grüße\n\nDanke!") == "Fix the page."


def test_a_prompt_without_a_sign_off_is_untouched() -> None:
    assert strip_closing_sign_off(GOOD) == GOOD


def test_a_one_line_message_is_never_stripped_to_nothing() -> None:
    """A transcript that IS a thank-you note keeps its only line."""
    assert strip_closing_sign_off("Danke!") == "Danke!"


async def test_a_model_that_still_signs_off_is_trimmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = ANSWER.replace(f"{GOOD}\n</prompt>", f"{GOOD}\n\nDanke!\n</prompt>")
    _install(monkeypatch, FakeChain(answer=signed))
    out = await compose_prompt(RAW, cfg=_cfg(), language="de")
    assert out.status == STATUS_PROMPTED
    assert out.text == GOOD


async def test_the_trim_never_masks_a_truncated_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The truncation guard runs first, so a prompt that stopped mid-sentence
    is caught as damage rather than tidied into looking finished."""
    _install(monkeypatch, FakeChain(answer="# Task\nFix the AuthHandler page so that"))
    out = await compose_prompt(RAW, cfg=_cfg(), language="en")
    assert (out.status, out.text, out.reason) == ("rejected_drift", RAW, "truncated")
