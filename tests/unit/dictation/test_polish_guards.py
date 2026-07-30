"""The polish pass's drift guards — one crafted violation per reason code.

The polish pass is the only place in dictation where a model is allowed to
touch the user's words, so the guards are the feature's safety envelope: every
one of them exists because a formatter model is documented to fail in exactly
that direction. This file pins each of them with a hand-built input that trips
that guard and no other, plus the cases that must NOT be rejected — because a
guard that rejects everything is indistinguishable from the feature being off,
and would be discovered only as "the polish never does anything".

The two asymmetries get their own tests. They are not oversights:

* ``"seven"`` -> ``"7"`` must PASS, even though a word vanished;
* a language the detector cannot classify must never be vetoed as a
  translation, because the detector knows de/en/es and the recogniser knows
  about a hundred languages.
"""

from __future__ import annotations

from jarvis.core.turn_language import detect_text_language
from jarvis.dictation.polish_guards import (
    DRIFT_REASONS,
    drift_reason,
    normalize_for_compare,
    rare_tokens,
)
from jarvis.dictation.polish_prompt import RAW_CLOSE_DELIMITER

# The shipped defaults from the design (§C.4). Kept here as literals rather than
# imported so a silent widening of the band shows up as a failing test.
MAX_SHRINK = 0.55
MAX_GROWTH = 1.20


def verdict(
    raw: str,
    polished: str,
    *,
    language: str = "en",
    protected: tuple[str, ...] = (),
) -> str:
    return drift_reason(
        raw,
        polished,
        language=language,
        protected=protected,
        max_shrink=MAX_SHRINK,
        max_growth=MAX_GROWTH,
    )


# --------------------------------------------------------------------------- #
# Every guard fires on its own crafted violation
# --------------------------------------------------------------------------- #


def test_an_empty_answer_is_rejected() -> None:
    """A model that returns whitespace has not formatted anything."""
    assert verdict("we should ship the release on Friday", "   ") == "empty"


def test_a_here_is_the_corrected_text_preamble_is_rejected() -> None:
    """The classic failure: the model answers ABOUT the text instead of with it."""
    raw = "we should ship the release on Friday and tell the team"
    polished = (
        "Here is the corrected text: We should ship the release on Friday "
        "and tell the team."
    )
    assert verdict(raw, polished) == "meta_output"


def test_a_preamble_the_speaker_actually_dictated_is_not_meta_output() -> None:
    """People say "Sure, ..." out loud; punishing them for the model's habit
    would make the guard hostile to normal speech."""
    raw = "Sure, I will send the contract over before the end of the day"
    polished = "Sure, I will send the contract over before the end of the day."
    assert verdict(raw, polished) == ""


def test_echoing_the_transcript_delimiter_is_rejected() -> None:
    """A repeated fence means the untrusted material was read as conversation."""
    raw = "please forward the invoice to the finance team this afternoon"
    polished = (
        "Please forward the invoice to the finance team this afternoon.\n"
        f"{RAW_CLOSE_DELIMITER}"
    )
    assert verdict(raw, polished) == "meta_output"


def test_a_markdown_fence_the_speaker_never_dictated_is_rejected() -> None:
    raw = "please forward the invoice to the finance team this afternoon"
    polished = (
        "```\nPlease forward the invoice to the finance team this afternoon.\n```"
    )
    assert verdict(raw, polished) == "meta_output"


def test_a_summarised_answer_is_rejected_as_a_shrink() -> None:
    """Below the shrink floor the model summarised rather than formatted."""
    raw = (
        "I think we should probably move the meeting to the morning because "
        "the afternoon is really quite full"
    )
    assert verdict(raw, "Move the meeting to the morning.") == "ratio_shrink"


def test_answering_the_transcript_is_rejected_as_growth() -> None:
    """Growth is the tell that the model treated the dictation as a question."""
    raw = "when is the next team meeting"
    polished = (
        "The next team meeting is on Thursday at three in the main conference "
        "room, right after the weekly planning session."
    )
    assert verdict(raw, polished) == "ratio_growth"


def test_a_translated_answer_is_rejected() -> None:
    """Translation is the loudest possible meaning change, so it gets its own
    reason code rather than surfacing as a generic lost-token complaint."""
    # The German source below is the content UNDER TEST (§1 list #4).
    raw = "Ich schreibe dir gleich eine Nachricht mit den Details"  # i18n-allow
    polished = "I will send you a message with the details shortly"
    assert detect_text_language(raw) == "de"
    assert detect_text_language(polished) == "en"
    assert verdict(raw, polished, language="de") == "language_flip"


def test_a_dropped_number_is_rejected() -> None:
    raw = "the invoice for 1200 euros is due on the 15th of March"
    polished = "The invoice for 1200 euros is due in March."
    assert verdict(raw, polished) == "lost_number"


def test_a_dropped_protected_term_is_rejected() -> None:
    """The user's own dictionary spellings are the terms we know they care about."""
    raw = "please deploy the Kubernetes cluster before the demo on Friday"
    polished = "Please deploy the cluster before the demo on Friday."
    assert verdict(raw, polished, protected=("Kubernetes",)) == "lost_term"


def test_a_dropped_proper_noun_is_rejected_even_without_a_dictionary_entry() -> None:
    """The failure the reference tool's own docs warn about: a rewrite model
    "correcting" an unfamiliar word into a familiar one. Nobody had to declare
    the name in advance for it to be worth keeping."""
    raw = "I met Anushka at the conference in Lisbon on Tuesday morning"
    polished = "I met her at the conference in Lisbon on Tuesday morning."
    assert verdict(raw, polished) == "lost_term"


def test_a_misheard_word_may_be_corrected_without_counting_as_a_loss() -> None:
    """The guard's own worst failure: protecting the recognizer's mistakes.

    A misheard word is ALWAYS rare — being misheard is what makes it rare — so
    a rarity filter that treats "this token is gone" as "a word was lost"
    refuses every repair, and refuses hardest on the transcripts that need one
    most. Measured on the live history: 30 of 71 polish runs (42 %) were thrown
    away this way. Each raw line below is a real one.
    """
    for raw, polished in (
        # A word cut short by the recognizer.
        ("Wieso legt die Javas Deskto App so extrem rum",  # i18n-allow: real transcript under test
         "Wieso laggt die Jarvis Desktop App so extrem rum?"),  # i18n-allow
        # Two spoken words run together into one non-word.
        ("Ich haboogle Drive und so nicht mal offen",  # i18n-allow
         "Ich hab Google Drive und so nicht mal offen."),  # i18n-allow
        ("Ich hab die Promme nicht mal", "Ich hab die Prompts nicht mal."),  # i18n-allow
    ):
        assert verdict(raw, polished, language="de") == "", (
            f"a repair of {raw!r} must be delivered, not refused"
        )


def test_a_word_replaced_by_an_unrelated_one_is_still_a_loss() -> None:
    """Letting repairs through must not let rewrites through with them.

    The line between the two is whether anything in the answer stands where the
    word did. ``Anushka`` -> ``Anuschka`` is a spelling; ``Anushka`` -> ``Sarah``
    is a different person.
    """
    raw = "I met Anushka at the conference in Lisbon on Tuesday morning"
    assert verdict(raw, "I met Sarah at the conference in Lisbon on Tuesday "
                        "morning.") == "lost_term"
    assert verdict(raw, "I met Anuschka at the conference in Lisbon on Tuesday "
                        "morning.") == ""


def test_every_reason_this_module_can_return_is_a_declared_one() -> None:
    """A guard that invents a code the history and the UI never heard of is the
    five-layer drift this repo has hit four times (AP-4)."""
    samples = (
        ("we should ship the release on Friday", "   "),
        ("we should ship the release on Friday", "Here is the corrected text: ok"),
        (
            "I think we should probably move the meeting to the morning because "
            "the afternoon is really quite full",
            "Move it.",
        ),
        ("when is the next team meeting", "The next team meeting is on Thursday "
         "at three in the main conference room after the planning session."),
        ("the invoice for 1200 euros is due on the 15th of March",
         "The invoice for 1200 euros is due in March."),
        ("I met Anushka at the conference in Lisbon on Tuesday morning",
         "I met her at the conference in Lisbon on Tuesday morning."),
    )
    for raw, polished in samples:
        reason = verdict(raw, polished)
        assert reason in DRIFT_REASONS, (raw, polished, reason)


# --------------------------------------------------------------------------- #
# The transformations that must survive the guards
# --------------------------------------------------------------------------- #


def test_a_spoken_number_normalised_to_a_numeral_passes() -> None:
    """"seven" -> "7" is the documented, wanted normalization. The number guard
    is one-directional precisely so this stays legal, and the spoken number
    words count as common vocabulary so the rarity guard does not mourn them."""
    raw = "we need seven copies for the meeting tomorrow morning"
    polished = "We need 7 copies for the meeting tomorrow morning."
    assert verdict(raw, polished) == ""


def test_punctuation_and_capitalisation_repair_passes() -> None:
    """The whole point of the feature. If this ever fails, the pass is inert."""
    raw = (
        "so we should probably move the meeting to the morning and tell the "
        "team about it"
    )
    polished = (
        "So we should probably move the meeting to the morning, and tell the "
        "team about it."
    )
    assert verdict(raw, polished) == ""


def test_a_thousands_separator_is_formatting_not_a_lost_number() -> None:
    """"1,000" and "1000" are the same number; a formatter may add the grouping."""
    raw = "the budget is 1000 euros for the whole quarter"
    polished = "The budget is 1,000 euros for the whole quarter."
    assert verdict(raw, polished) == ""


def test_a_language_the_detector_cannot_classify_is_never_vetoed() -> None:
    """The detector knows de/en/es. A Polish dictation must not be rejected as
    a translation just because we cannot say what language it is — and the
    rarity guard must stand down there too, since without a word list every
    single token would look rare."""
    raw = "Jutro wysylam raport dla zespolu projektowego"
    polished = "Jutro wysylam raport dla zespolu projektowego."
    assert detect_text_language(raw) == "unknown"
    assert rare_tokens(raw, language="pl") == frozenset()
    assert verdict(raw, polished, language="pl") == ""


# --------------------------------------------------------------------------- #
# The helpers the guards are built out of
# --------------------------------------------------------------------------- #


def test_normalize_for_compare_collapses_whitespace_but_keeps_punctuation() -> None:
    """Punctuation is what the pass ADDS, so normalising it away would report a
    successful repunctuation as "unchanged" and throw the result on the floor."""
    assert normalize_for_compare("  a   b\n c  ") == "a b c"
    assert normalize_for_compare("hello there") != normalize_for_compare("Hello there.")


def test_rare_tokens_keeps_names_and_ignores_ordinary_words() -> None:
    tokens = rare_tokens("deploy the Kubernetes cluster on Tuesday", language="en")
    assert "kubernetes" in tokens
    assert "cluster" in tokens
    # Ordinary vocabulary and short tokens are not "rare".
    assert "the" not in tokens
    assert "should" not in rare_tokens("we should deploy", language="en")


def test_rare_tokens_treats_spoken_numbers_as_ordinary_words() -> None:
    assert "seven" not in rare_tokens("we need seven copies", language="en")


def test_rare_tokens_is_empty_for_a_language_we_hold_no_word_list_for() -> None:
    """Empty means "this guard has no opinion here", which is the only honest
    answer for ~95 of the 100 recognition languages."""
    assert rare_tokens("Jutro wysylam raport", language="pl") == frozenset()
    assert rare_tokens("Jutro wysylam raport", language="") == frozenset()


def test_the_rare_token_lookup_follows_the_TEXT_not_its_label() -> None:
    """A mislabelled transcript must not make every ordinary word "rare".

    The recognizer uploads a dictation in segments, and on a short segment
    Whisper sometimes re-decides the language — so a German-tagged row can hold
    an English transcript (and vice versa). Looking the frequency list up by the
    LABEL then means checking English words against the German vocabulary, where
    every one of them is unknown: the guard fires on any wording change at all,
    the polish is thrown away, and the row says `rejected_drift` with nothing
    explaining it. That was 12 of the last 40 rows in the live history
    (2026-07-29).

    The fix is narrow: when the detector has an opinion about the text, it
    outranks the tag for THIS lookup. The tag still decides nothing else.
    """
    raw = "so i think we should probably ship the report on tuesday and tell them"
    polished = "I think we should ship the report on Tuesday and tell them."

    # Correctly labelled: passes, and always did.
    assert (
        drift_reason(
            raw, polished, language="en", protected=(), max_shrink=0.55, max_growth=1.20
        )
        == ""
    )
    # MIS-labelled as German by the recognizer — same text, same edit.
    assert (
        drift_reason(
            raw, polished, language="de", protected=(), max_shrink=0.55, max_growth=1.20
        )
        == ""
    ), "an English transcript tagged German must not be judged by the German list"


def test_a_genuinely_lost_word_is_still_caught_under_a_wrong_label() -> None:
    """Following the text is not the same as disarming the guard."""
    raw = "please send the Kubernetes report to Marlowe before the meeting starts"
    dropped = "Please send the report to Marlowe before the meeting starts."

    assert (
        drift_reason(
            raw, dropped, language="de", protected=(), max_shrink=0.55, max_growth=1.20
        )
        == "lost_term"
    )
