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


def test_a_paragraph_split_alone_survives_every_guard() -> None:
    """A transcript split into paragraphs is the same words with "\\n\\n" added.
    No token vanishes, no count moves — nothing here is the guards' business."""
    raw = (
        "the offer went out this morning and I already got a reply next week "
        "we should talk about the invoice and the timeline"
    )
    polished = (
        "The offer went out this morning, and I already got a reply.\n\n"
        "Next week we should talk about the invoice and the timeline."
    )
    assert verdict(raw, polished) == ""


def test_a_counted_enumeration_may_become_a_numbered_list() -> None:
    """"erstens, zweitens, drittens" -> "1. 2. 3." is the transformation the
    prompt licenses. The spoken ordinal is a number word that may vanish into
    the marker replacing it — the same one-directional trade as "seven" -> "7"
    — and the added digits are legal by the same asymmetry. Before the adverb
    forms joined the German number table, every counted list was rejected as
    ``lost_term`` and the feature looked permanently inert in German."""
    # The German source below is the content UNDER TEST (§1 list #4).
    raw = (
        "wir machen erstens das Angebot fertig zweitens schicken wir die "  # i18n-allow
        "Rechnung und drittens bestätigen wir den Termin"  # i18n-allow
    )
    polished = (
        "1. Wir machen das Angebot fertig.\n"  # i18n-allow
        "2. Wir schicken die Rechnung.\n"  # i18n-allow
        "3. Wir bestätigen den Termin."  # i18n-allow
    )
    assert detect_text_language(raw) == "de"
    assert verdict(raw, polished, language="de") == ""

    # The same trade in the other two table languages.
    raw_en = (
        "firstly prepare the offer secondly send the invoice thirdly "
        "confirm the appointment"
    )
    polished_en = (
        "1. Prepare the offer.\n2. Send the invoice.\n3. Confirm the appointment."
    )
    assert verdict(raw_en, polished_en) == ""

    # The Spanish source below is the content UNDER TEST (§1 list #4).
    raw_es = (
        "primero revisamos la oferta segundo mandamos la factura tercero "  # i18n-allow
        "confirmamos la cita"  # i18n-allow
    )
    polished_es = (
        "1. Revisamos la oferta.\n"  # i18n-allow
        "2. Mandamos la factura.\n"  # i18n-allow
        "3. Confirmamos la cita."  # i18n-allow
    )
    assert verdict(raw_es, polished_es, language="es") == ""


def test_an_uncounted_enumeration_may_become_a_bulleted_list() -> None:
    """A plain spoken listing may come back one item per line with "-" bullets;
    the bullet mark is punctuation, not a token, so no guard has an opinion."""
    # The German source below is the content UNDER TEST (§1 list #4).
    raw = "wir brauchen dafür noch Milch Eier und Butter vom Markt"  # i18n-allow
    polished = (
        "Wir brauchen dafür noch vom Markt:\n"  # i18n-allow
        "- Milch\n"  # i18n-allow
        "- Eier\n"  # i18n-allow
        "- Butter"  # i18n-allow
    )
    assert verdict(raw, polished, language="de") == ""


def test_a_spoken_bullet_command_becomes_a_bullet_line() -> None:
    """The guaranteed path to a bullet list: the speaker names the marker
    before every item. The command word vanishes into the "- " that replaces
    it — licensed by ``_FORMAT_COMMAND_WORDS`` — and the word count HALVES,
    which the band only survives because commands are counted out of the
    ratio on both sides. Before both fixes this dictation was rejected twice
    over: ``lost_term`` for the vanished command, ``ratio_shrink`` for the
    words it took with it."""
    # The German source below is the content UNDER TEST (§1 list #4).
    raw = "Stichpunkt Milch Stichpunkt Eier Stichpunkt Butter"  # i18n-allow
    polished = "- Milch\n- Eier\n- Butter"  # i18n-allow
    assert verdict(raw, polished, language="de") == ""


def test_a_spoken_punctuation_command_becoming_its_mark_passes() -> None:
    """The latent twin of the bullet-command bug, present since v1: the prompt
    has always licensed "comma" -> ",", but the vanished command word was a
    rare token to the guard and the conversion was rejected as ``lost_term``.
    """
    # The German source below is the content UNDER TEST (§1 list #4).
    raw = "wir treffen uns um drei Komma bring bitte die Unterlagen mit"  # i18n-allow
    polished = "Wir treffen uns um 3, bring bitte die Unterlagen mit."  # i18n-allow
    assert verdict(raw, polished, language="de") == ""


def test_a_content_homonym_of_a_command_cancels_on_both_sides() -> None:
    """Command words are subtracted from BOTH sides of the ratio. A speaker
    discussing an actual "Punkt" keeps the word in raw and answer alike; were
    only the raw side adjusted, this unchanged-but-punctuated answer would
    score 17/14 = 1.21 and be rejected as ``ratio_growth``."""
    # The German source below is the content UNDER TEST (§1 list #4).
    raw = (
        "der erste Punkt ist gut der zweite Punkt ist besser und der "  # i18n-allow
        "dritte Punkt ist am besten"  # i18n-allow
    )
    polished = (
        "Der erste Punkt ist gut, der zweite Punkt ist besser, und der "  # i18n-allow
        "dritte Punkt ist am besten."  # i18n-allow
    )
    assert verdict(raw, polished, language="de") == ""


def test_a_summariser_earns_no_discount_from_spoken_commands() -> None:
    """The command discount must be EARNED by produced list lines. A model
    that swallowed a commanded dictation into prose shows none, gets the
    undiscounted ratio, and is caught exactly as before the discount existed
    — without this gate, the vanished command words would subsidise the
    vanished content words."""
    # The German source below is the content UNDER TEST (§1 list #4).
    raw = "Stichpunkt Milch Stichpunkt Eier Stichpunkt Butter"  # i18n-allow
    polished = "Milch und mehr"  # i18n-allow
    assert verdict(raw, polished, language="de") == "ratio_shrink"


def test_padding_with_command_vocabulary_is_still_growth() -> None:
    """Only occurrences the RAW side also carries are settled. Command
    vocabulary the model ADDED is never discounted, so an answer padded with
    it counts as the growth it is."""
    raw = "please send the report today"
    polished = "Please send the report today mark mark mark mark mark mark."
    assert verdict(raw, polished) == "ratio_growth"


def test_a_two_word_marker_is_discounted_per_produced_line() -> None:
    """A two-word marker ("next point" and its German twin below) spends TWO
    command words on every produced line, which is why the discount is capped
    per line rather than granted per word — and why the cap is two, not one."""
    # The German source below is the content UNDER TEST (§1 list #4).
    raw = "nächster Punkt Milch nächster Punkt Eier nächster Punkt Butter"  # i18n-allow
    polished = "- Milch\n- Eier\n- Butter"  # i18n-allow
    assert verdict(raw, polished, language="de") == ""

    raw_en = "we need next point apples next point oranges next point bananas"
    polished_en = "We need:\n- Apples\n- Oranges\n- Bananas"
    assert verdict(raw_en, polished_en) == ""


# --------------------------------------------------------------------------- #
# The helpers the guards are built out of
# --------------------------------------------------------------------------- #


def test_normalize_for_compare_collapses_spaces_but_keeps_punctuation() -> None:
    """Punctuation is what the pass ADDS, so normalising it away would report a
    successful repunctuation as "unchanged" and throw the result on the floor."""
    assert normalize_for_compare("  a   b \t c  ") == "a b c"
    assert normalize_for_compare("hello there") != normalize_for_compare("Hello there.")


def test_normalize_for_compare_keeps_line_breaks() -> None:
    """Line structure is ALSO what the pass adds — paragraphs and list items are
    often the same words with only "\\n" inserted. The first version collapsed
    those into spaces, reported the answer as "unchanged", and threw the only
    formatting the user asked for on the floor. How a break is spelled still
    normalises to one form: the comparison cares that the text was split, not
    which convention split it."""
    flat = "the offer is ready next we send the invoice"
    split = "The offer is ready.\n\nNext we send the invoice."
    assert normalize_for_compare(flat) != normalize_for_compare(split)
    assert normalize_for_compare("a\r\nb") == normalize_for_compare("a\nb")
    assert normalize_for_compare("a \n\n b") == normalize_for_compare("a\nb")


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


# --------------------------------------------------------------------------- #
# lost_verb — the loss the speaker cannot see
# --------------------------------------------------------------------------- #
#
# Reported live: "sometimes it cuts the 'is' out of the sentence". Measured on
# the local history, 33 of 68 polished dictations had lost at least one word
# and exactly ONE had ever been rejected — a copula is too short for the
# rare-token filter, too common for the frequency list, and too small for the
# word-count band. The inputs below are taken from that history, not invented.


def test_a_deleted_copula_is_rejected() -> None:
    """The reported bug, verbatim from the live history."""
    raw = "what I've meant is this expanded term when you go with your mouse"
    cut = "What I meant this expanded term when you go with your mouse."

    assert verdict(raw, cut) == "lost_verb"


def test_a_deleted_modal_is_rejected() -> None:
    """"I can see them" and "I see them" are not the same claim."""
    raw = "I can see them in my download folder"
    cut = "I see them in my downloads folder."

    assert verdict(raw, cut) == "lost_verb"


def test_a_deleted_negation_is_rejected() -> None:
    """The loss that inverts the sentence rather than merely damaging it."""
    raw = "the release is not ready for the demo on Friday"
    cut = "The release is ready for the demo on Friday."

    assert verdict(raw, cut) == "lost_verb"


def test_a_deleted_german_copula_is_rejected() -> None:
    """The same defect in the other language — also a live row."""
    raw = "wir wollen das verbessern weil ist die Transkription"  # i18n-allow: live row
    cut = "Wir wollen das verbessern, weil die Transkription."  # i18n-allow: live row

    assert verdict(raw, cut, language="de") == "lost_verb"


def test_a_corrected_verb_is_not_a_lost_one() -> None:
    """Why the check is positional and not a word count.

    Subject-verb agreement repair is what the pass is FOR, and it removes an
    "is" exactly like the defect above does. Counting occurrences cannot tell
    the two apart; asking whether anything stands where the word stood can.
    """
    raw = "the reasoning and which actions is called like if you use it"
    fixed = "The reasoning and which actions are called, like if you use it."

    assert verdict(raw, fixed) == ""


def test_a_german_agreement_repair_is_not_a_lost_verb() -> None:
    raw = "die Qualität meines Streams ist irgendwie so schlecht"  # i18n-allow: live row
    fixed = "Die Qualität meiner Streams sind irgendwie schlecht."  # i18n-allow: live row

    assert verdict(raw, fixed, language="de") == ""


def test_a_reworded_clause_around_a_verb_is_not_a_lost_verb() -> None:
    """A rewrite is licensed; only a clean excision is not.

    "will catch up" -> "catches up" moves the tense into the main verb, and
    "what I have meaning" -> "what I mean" repairs a mis-transcription. Both
    lose a listed word inside a REPLACE, and rejecting them would hand the
    user back the broken sentence they dictated.
    """
    assert (
        verdict(
            "if Grock will catch up in this pace they will be the frontier lap",
            "If Grok catches up at this pace, they will be the frontier lab.",
        )
        == ""
    )
    assert (
        verdict(
            "if you're not sure what I have meaning then just ask me a question",
            "If you are not sure what I mean, ask me a question.",
        )
        == ""
    )


def test_precision_mode_does_not_trade_the_verb_check_away() -> None:
    """Precision licenses word CHOICE, never removing the clause's verb.

    Precision mode switches the rare-token guard off, so if this check were
    skipped there too, the strongest protection and the one that replaces it
    would both be gone at once — on the setting that most needs a backstop.
    """
    from jarvis.dictation.polish_guards import (
        PRECISION_DRIFT_REASONS,
        precision_drift_reason,
    )

    raw = "what I've meant is this expanded term when you go with your mouse"
    cut = "What I meant this expanded term when you go with your mouse."

    assert (
        precision_drift_reason(
            raw,
            cut,
            language="en",
            protected=(),
            max_shrink=MAX_SHRINK,
            max_growth=MAX_GROWTH,
        )
        == "lost_verb"
    )
    assert "lost_verb" in PRECISION_DRIFT_REASONS
    assert "lost_verb" in DRIFT_REASONS


def test_a_language_with_no_verb_table_has_no_opinion() -> None:
    """Silence beats a veto — the rule every other word list here follows.

    Asserted on the check itself rather than through ``drift_reason``, because
    a Polish sentence trips the rare-token guard first and would hide the
    answer this test is about.
    """
    from jarvis.dictation.polish_guards import _lost_essential_word

    assert not _lost_essential_word(
        "jutro nie wysylam raportu", "Jutro wysylam raportu.", language="pl"
    )
    assert not _lost_essential_word("we are not ready", "We ready.", language="")


def test_a_false_start_may_still_take_its_verb_with_it() -> None:
    """The licence the check must not revoke.

    "Remove false starts" is one of the pass's stated jobs, and an abandoned
    fragment nearly always contains a verb — so a check that fires on any
    deleted verb would forbid the edit the prompt asks for. The span deleted
    here is ["i", "will", "i", "mean"], and the pronoun in it is what says
    "a construction went away" rather than "a word was taken out".
    """
    raw = "i will, i mean i would rather send it tomorrow morning"
    fixed = "I would rather send it tomorrow morning."

    assert verdict(raw, fixed) == ""


def test_a_reduced_relative_clause_is_not_a_lost_verb() -> None:
    """"the people who are in charge" -> "the people in charge" is a tightening.

    Same shape as the false start: the deleted span is ["who", "are"], the
    relative pronoun leaves with the verb, and the meaning is untouched.
    """
    raw = "send it to the three people who are in charge of it tomorrow"
    fixed = "Send it to the 3 people in charge of it tomorrow."

    assert verdict(raw, fixed) == ""
