"""Precision mode — the optional word-choice pass on top of the formatter.

The mode is one prompt clause plus one matched guard, and the reason it needs
its own test file is that those two must never be separated. The precision
prompt licenses substitutions (``the program is broken`` -> ``the application
is faulty``) that the ORDINARY guard rejects as ``lost_term``. Ship the prompt
without the guard and the feature rejects nearly every one of its own correct
answers; ship the guard without the prompt and an ordinary polish silently
loses its strongest protection. So the pairing is asserted directly, in both
directions, rather than left to a reader of two modules.

The second theme is what precision mode is NOT allowed to cost. Dropping
rare-token preservation is a real trade, and everything that still stands after
it — protected terms, quantities, the language, the word-count band, the
meta-output check — is pinned here so a later prompt revision cannot quietly
widen the hole.

No network: the pipeline tests use the same recording fake as
``test_polish_pipeline.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.core.config import DictationConfig
from jarvis.core.config_writer import DICTATION_SETTING_KEYS
from jarvis.dictation import polish
from jarvis.dictation.polish import polish_transcript, precision_enabled
from jarvis.dictation.polish_client import POLISH_FAMILIES, PolishFamily
from jarvis.dictation.polish_guards import (
    DRIFT_REASONS,
    PRECISION_DRIFT_REASONS,
    drift_reason,
    precision_drift_reason,
)
from jarvis.dictation.polish_prompt import (
    build_polish_prompt,
    precision_block,
)
from jarvis.dictation.translate_prompt import build_translate_prompt

GROQ: PolishFamily = POLISH_FAMILIES[0]

#: The substitution the mode exists for, and the exact shape the ordinary guard
#: refuses: two uncommon words (``program``, ``broken``) are gone and nothing in
#: the answer is a repaired spelling of either.
SHARPEN_RAW = "the program is broken and we need to make a decision about it tomorrow"
SHARPEN_OUT = "The application is faulty, and we need to decide about it tomorrow."

# The bands the pass actually uses in each mode. Stated here rather than
# imported so a change to either one has to be made deliberately in two places.
POLISH_BAND = {"max_shrink": 0.55, "max_growth": 1.20}
PRECISION_BAND = {"max_shrink": 0.45, "max_growth": 1.35}


def _precision_verdict(raw: str, out: str, *, language: str = "en", **kw: Any) -> str:
    protected = kw.pop("protected", ())
    return precision_drift_reason(
        raw, out, language=language, protected=list(protected), **PRECISION_BAND
    )


# --------------------------------------------------------------------------- #
# The prompt clause
# --------------------------------------------------------------------------- #


def test_the_clause_is_absent_unless_it_is_asked_for() -> None:
    """The default must be the strict formatter, for every existing caller."""
    plain = build_polish_prompt(language="en", style="neutral", protected_terms=())

    assert "PRECISION MODE IS ON" not in plain
    # The word-choice licence must not leak in through some other line.
    assert "sharpen" not in plain.lower()


def test_the_clause_appears_when_it_is_asked_for() -> None:
    sharpened = build_polish_prompt(
        language="en", style="neutral", protected_terms=(), precision=True
    )

    assert "PRECISION MODE IS ON" in sharpened
    # The hard rules it rides on top of are still there — precision EXTENDS the
    # formatter contract, it does not replace it.
    assert "MEANING NEVER CHANGES" in sharpened
    assert "OUTPUT LANGUAGE = INPUT LANGUAGE" in sharpened


def test_the_ornate_register_is_forbidden_by_name() -> None:
    """The documented failure direction, pinned literally.

    A model told only "prefer plain words" still writes "utilize". Naming the
    swaps is what stops it, so the naming is the behaviour under test — not an
    incidental wording choice a later edit may drop.
    """
    clause = precision_block()

    for forbidden in ("utilize", "facilitate", "commence"):
        assert forbidden in clause, f"the {forbidden!r} example carries the rule"
    assert "PLAIN, NOT ORNATE" in clause
    # And the licence is bounded by the thing that outranks it.
    assert "MEANING STILL NEVER CHANGES" in clause


def test_one_switch_means_one_thing_across_both_passes() -> None:
    """The translate prompt appends the SAME clause, not a paraphrase of it.

    Two copies is how the setting ends up meaning something subtly different
    depending on whether a translation was involved — invisible to the user,
    because both paths report the same status on the same history row.
    """
    translated = build_translate_prompt(
        target_language="en", style="neutral", protected_terms=(), precision=True
    )
    polished = build_polish_prompt(
        language="de", style="neutral", protected_terms=(), precision=True
    )

    clause = precision_block()
    assert clause in translated
    assert clause in polished


def test_the_translate_prompt_is_unchanged_when_precision_is_off() -> None:
    assert "PRECISION MODE IS ON" not in build_translate_prompt(
        target_language="en", style="neutral", protected_terms=()
    )


# --------------------------------------------------------------------------- #
# The guard pairing — the whole reason this file exists
# --------------------------------------------------------------------------- #


def test_the_ordinary_guard_rejects_what_precision_mode_produces() -> None:
    """The failure this feature would have shipped with, stated out loud.

    If this ever starts passing, the rare-token check has been weakened for
    EVERY dictation and the precision guard has lost its reason to exist.
    """
    assert (
        drift_reason(SHARPEN_RAW, SHARPEN_OUT, language="en", protected=[], **POLISH_BAND)
        == "lost_term"
    )


def test_the_precision_guard_accepts_it() -> None:
    assert _precision_verdict(SHARPEN_RAW, SHARPEN_OUT) == ""


def test_precision_reasons_are_a_subset_of_the_ordinary_ones() -> None:
    """Same vocabulary, never a code the history and the UI have not heard of."""
    assert set(PRECISION_DRIFT_REASONS) <= set(DRIFT_REASONS)


# --------------------------------------------------------------------------- #
# What precision mode may NOT cost
# --------------------------------------------------------------------------- #


def test_a_protected_term_is_still_untouchable() -> None:
    """Names, the wake word and the STT dictionary come from the USER.

    They are not frequency data, so no amount of "sharpen the wording"
    licenses replacing one.
    """
    assert (
        _precision_verdict(
            "call Anushka about the deployment tomorrow please",
            "Call her about the deployment tomorrow.",
            protected=["Anushka"],
        )
        == "lost_term"
    )


def test_a_spoken_quantity_may_not_be_dropped() -> None:
    """The gap the digit check cannot see.

    ``three`` is common vocabulary, so the rare-token filter waved it through
    even before precision mode removed that filter — and there was never a digit
    for ``lost_number`` to miss. With substitution licensed, this is how a
    quantity walks out of a transcript unnoticed.
    """
    assert (
        _precision_verdict(
            "send it to three people on the team by friday morning",
            "Send it to the team by Friday morning.",
        )
        == "lost_number"
    )


def test_a_quantity_written_as_a_numeral_is_not_a_loss() -> None:
    """ "seven" -> "7" is the normalization the whole feature exists to make."""
    assert (
        _precision_verdict(
            "send it to three people on the team by friday morning",
            "Send it to 3 people on the team by Friday morning.",
        )
        == ""
    )


@pytest.mark.parametrize(
    ("raw", "out"),
    [
        # An ordinal legitimately disappears when a sentence is restructured.
        (
            "the first thing we really need here is basically a proper backup plan",
            "What we need first is a proper backup plan for the server.",
        ),
        # "one" is far more often an article or an impersonal pronoun than a 1.
        (
            "one of the things we really need here is kind of a proper backup plan",
            "One thing we need is a proper backup plan for the server.",
        ),
    ],
)
def test_ambiguous_number_words_never_veto_an_answer(raw: str, out: str) -> None:
    """Silence beats a veto — the module's rule, applied to the quantity check.

    Insisting on words that are only sometimes numbers would reject correct
    sharpenings, and a rejected polish costs the user a usable transcript.
    """
    assert _precision_verdict(raw, out) != "lost_number"


def test_precision_is_not_a_licence_to_translate() -> None:
    # i18n-allow: a German transcript is the INPUT DATA this check needs — the
    # guard can only catch a language flip if one language is not English.
    assert (
        _precision_verdict(
            "wir sollten den Bericht am Dienstag verschicken",  # i18n-allow
            "We should send the report on Tuesday.",
            language="de",
        )
        == "language_flip"
    )


def test_a_model_that_starts_talking_is_still_caught() -> None:
    assert (
        _precision_verdict(
            "should we ship the report on tuesday or wednesday",
            "Here is the improved version: We should ship on Wednesday.",
        )
        == "meta_output"
    )


def test_sharpening_may_not_become_writing() -> None:
    """The band is wider than the polish one, and still a band."""
    assert (
        _precision_verdict(
            "ship it tuesday",
            "We should ship the report on Tuesday because the team agreed that "
            "this is the best possible moment for absolutely everyone involved.",
        )
        == "ratio_growth"
    )


def test_the_command_settlement_holds_under_the_precision_band() -> None:
    """The precision guard shares the command-word settlement with the
    ordinary one, and here it matters MORE: the rare-token backstop is
    already traded away, so the band is the only thing standing between a
    commanded bullet list and a padded or summarised answer. Four items
    rather than three, because the precision shrink floor (0.45) is looser
    than the polish one and a three-item summary would slip past it."""
    # The German source below is the content UNDER TEST (§1 list #4).
    raw = "Stichpunkt Milch Stichpunkt Eier Stichpunkt Butter Stichpunkt Brot"  # i18n-allow
    bullets = "- Milch\n- Eier\n- Butter\n- Brot"  # i18n-allow
    assert _precision_verdict(raw, bullets, language="de") == ""
    summary = "Milch und mehr"  # i18n-allow
    assert _precision_verdict(raw, summary, language="de") == "ratio_shrink"

    padded = "Please send the report today mark mark mark mark mark mark mark."
    assert (
        _precision_verdict("please send the report today", padded)
        == "ratio_growth"
    )


def test_a_language_with_no_quantity_table_is_a_no_op() -> None:
    """~96 of the recognition languages have no table; none of them get a veto."""
    assert (
        _precision_verdict(
            "wyslij to do trzech osob z zespolu do piatku",
            "Wyslij to do zespolu do piatku w tym tygodniu.",
            language="pl",
        )
        != "lost_number"
    )


def test_the_ordinary_pass_did_not_inherit_the_quantity_check() -> None:
    """Precision-only, deliberately.

    The ordinary prompt forbids replacing words at all, so this failure needs a
    prompt violation to reach the guard in the first place — and adding the
    check there would change the verdict on transcripts that have been passing
    correctly for months.
    """
    assert (
        drift_reason(
            "send it to three people on the team by friday morning",
            "Send it to the team by Friday morning.",
            language="en",
            protected=[],
            **POLISH_BAND,
        )
        == ""
    )


# --------------------------------------------------------------------------- #
# The switch
# --------------------------------------------------------------------------- #


def test_the_switch_ships_off() -> None:
    """It trades a guard, so it is never inherited from a default."""
    assert DictationConfig().polish_precision is False
    assert precision_enabled(DictationConfig()) is False


def test_an_older_config_reads_as_off() -> None:
    """A jarvis.toml that predates the feature must not acquire it silently."""

    class _Ancient:
        polish = True

    assert precision_enabled(_Ancient()) is False


def test_the_switch_survives_a_restart() -> None:
    """A key missing from the writer allowlist is a setting the UI loses.

    The PUT validates against ``DictationConfig`` and then persists key by key
    through ``set_dictation_setting``, which refuses anything not listed — so
    the two have to agree or the save returns 200 and changes nothing.
    """
    assert "polish_precision" in DICTATION_SETTING_KEYS
    assert "polish_precision" in DictationConfig.model_fields


def test_the_rest_body_declares_the_key() -> None:
    """FastAPI DROPS body keys a model does not declare — silently."""
    from jarvis.ui.web.dictation_routes import SettingsBody

    assert "polish_precision" in SettingsBody.model_fields


# --------------------------------------------------------------------------- #
# End to end, against a fake client
# --------------------------------------------------------------------------- #


@dataclass
class _Cfg:
    """Only the keys the pass reads — everything goes through ``getattr``."""

    polish: bool = True
    polish_precision: bool = False
    polish_provider: str = "auto"
    polish_model: str = ""
    polish_timeout_ms: int = 1200
    polish_max_input_chars: int = 0
    polish_min_words: int = 4
    polish_max_output_tokens: int = 1200
    polish_temperature: float = 0.0
    polish_drift_max_shrink: float = 0.55
    polish_drift_max_growth: float = 1.20
    polish_style: str = "neutral"


@dataclass
class _FakeClient:
    reply: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_output_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> str | None:
        self.calls.append({"system": system, "user": user})
        return self.reply


@pytest.fixture(autouse=True)
def _fresh_state() -> None:
    polish.reset_polish_state()


def _wire(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(polish, "resolve_polish_chain", lambda cfg: (GROQ,))
    monkeypatch.setattr(polish, "build_polish_client", lambda family, *, model: client)


async def test_the_switch_reaches_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(reply=SHARPEN_OUT)
    _wire(monkeypatch, client)

    await polish_transcript(SHARPEN_RAW, language="en", cfg=_Cfg(polish_precision=True))

    assert "PRECISION MODE IS ON" in client.calls[0]["system"]


async def test_the_switch_off_leaves_the_prompt_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(reply=SHARPEN_OUT)
    _wire(monkeypatch, client)

    await polish_transcript(SHARPEN_RAW, language="en", cfg=_Cfg())

    assert "PRECISION MODE IS ON" not in client.calls[0]["system"]


async def test_a_sharpened_answer_is_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The end-to-end proof that prompt and guard were switched together.

    With the mode on, the same answer that the ordinary pass rejects arrives at
    the user. This is the test that fails if a future edit turns the prompt on
    and leaves the guard behind.
    """
    _wire(monkeypatch, _FakeClient(reply=SHARPEN_OUT))

    outcome = await polish_transcript(SHARPEN_RAW, language="en", cfg=_Cfg(polish_precision=True))

    assert outcome.status == "applied"
    assert outcome.text == SHARPEN_OUT


async def test_the_same_answer_is_refused_with_the_mode_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """And the user keeps their own words — the contract is unchanged."""
    _wire(monkeypatch, _FakeClient(reply=SHARPEN_OUT))

    outcome = await polish_transcript(SHARPEN_RAW, language="en", cfg=_Cfg())

    assert outcome.status == "rejected_drift"
    assert outcome.reason == "lost_term"
    assert outcome.text == SHARPEN_RAW


async def test_precision_applies_to_a_translation_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One switch, one meaning, whichever pass runs."""
    client = _FakeClient(reply="We should send the report on Tuesday.")
    _wire(monkeypatch, client)

    outcome = await polish_transcript(
        # i18n-allow: the transcript being translated OUT of — a translation
        # test needs a source language, and it cannot be the target.
        "also wir sollten den Bericht am Dienstag verschicken",  # i18n-allow
        language="de",
        cfg=_Cfg(polish_precision=True),
        translate_to="en",
    )

    assert "PRECISION MODE IS ON" in client.calls[0]["system"]
    assert outcome.status == "translated"


async def test_a_precision_run_still_never_loses_the_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one-sentence contract, re-asserted for the looser mode.

    Relaxing a guard may not relax the promise: a rejected answer still returns
    the speaker's own words, not the model's.
    """
    _wire(monkeypatch, _FakeClient(reply="Call her about the deployment tomorrow."))

    outcome = await polish_transcript(
        "call Anushka about the deployment tomorrow please",
        language="en",
        cfg=_Cfg(polish_precision=True),
        protected_terms=["Anushka"],
    )

    assert outcome.status == "rejected_drift"
    assert outcome.reason == "lost_term"
    assert outcome.text == "call Anushka about the deployment tomorrow please"
