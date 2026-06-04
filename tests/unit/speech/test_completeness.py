"""Unit tests for the utterance-completeness classifier.

Spec: docs/superpowers/specs/2026-05-25-utterance-completeness-design.md

The classifier sits in front of the main agent and decides whether a finalized
transcript is a COMPLETE actionable instruction, an INCOMPLETE (dangling)
fragment, or an ABRUPT_ABORT (self-cancel). Bias: "when in doubt, execute" —
the default verdict is COMPLETE, so the fast local-action path is never
regressed. Pure function, stdlib-only, no mocks.
"""
from __future__ import annotations

import pytest

from jarvis.speech.completeness import (
    Completeness,
    CompletenessVerdict,
    classify_completeness,
)


# ---------------------------------------------------------------------------
# COMPLETE — must pass through to the brain / fast-path (the critical class)
# ---------------------------------------------------------------------------
COMPLETE_CASES = [
    # Clear imperatives (must not regress the local-action fast path)
    "Öffne Chrome",
    "Öffne mir den Browser",
    "Spiel Spotify ab",
    "Mach notepad auf",
    "Starte Firefox",
    # Separable-verb particles ending the command (NOT dangling)
    "Mach das Fenster zu",
    "Mach das Licht an",
    "Turn it off",
    # Definite article / demonstrative pronouns ending the command
    "Mach das",
    "Lass das",
    "Nimm die rote",
    "Gib mir den Bericht",
    # Preposition-tail collisions — complete questions ending in a preposition
    "Was ist das für",
    "What is this for",
    # Normal questions / statements
    "Wie spät ist es",
    "What time is it",
    "Open the terminal",
    "Schreib eine Mail an Tom dass ich später komme",
    # Historic false-positive that once froze the pipeline mute
    "Kannst du das fixen",
    # Bias: grammatically odd but ambiguous → execute
    "das Wetter heute",
    "Browser",
]


@pytest.mark.parametrize("text", COMPLETE_CASES)
def test_complete_utterances_classify_complete(text: str) -> None:
    verdict = classify_completeness(text)
    assert verdict.label is Completeness.COMPLETE, (
        f"{text!r} should be COMPLETE (got {verdict.label} / {verdict.reason})"
    )


@pytest.mark.parametrize(
    "text",
    ["Öffne Chrome.", "Wie spät ist es?", "Mach das jetzt!", "Open the terminal."],
)
def test_terminal_punctuation_is_complete(text: str) -> None:
    assert classify_completeness(text).label is Completeness.COMPLETE


# ---------------------------------------------------------------------------
# INCOMPLETE — dangling fragments (trailing conjunction / article / subordinator)
# ---------------------------------------------------------------------------
INCOMPLETE_CASES = [
    "Öffne mal eine",      # indefinite article
    "Ich brauche einen",   # indefinite article
    "Kauf Milch und",      # conjunction
    "Ich glaube dass",     # conjunction (subordinating)
    "Jarvis wenn",         # subordinator
    "wenn",                # bare subordinator
    "falls",               # bare subordinator
    "Send a mail to",      # EN preposition "to" (kept — never a complete tail)
    "I want to",
    "Open the",            # EN article
]


@pytest.mark.parametrize("text", INCOMPLETE_CASES)
def test_dangling_fragments_classify_incomplete(text: str) -> None:
    verdict = classify_completeness(text)
    assert verdict.label is Completeness.INCOMPLETE, (
        f"{text!r} should be INCOMPLETE (got {verdict.label} / {verdict.reason})"
    )


# ---------------------------------------------------------------------------
# ABRUPT_ABORT — explicit self-cancel phrases (narrow, high precision)
# ---------------------------------------------------------------------------
ABORT_CASES = [
    "nein, egal",
    "ach egal",
    "vergiss es",
    "vergiss das",
    "ach, lass gut sein",
    "schon gut",
    "ne doch nicht",
    "never mind",
    "nevermind",
    "forget it",
    "scratch that",
    "no wait",
]


@pytest.mark.parametrize("text", ABORT_CASES)
def test_abort_phrases_classify_abrupt_abort(text: str) -> None:
    verdict = classify_completeness(text)
    assert verdict.label is Completeness.ABRUPT_ABORT, (
        f"{text!r} should be ABRUPT_ABORT (got {verdict.label} / {verdict.reason})"
    )


@pytest.mark.parametrize(
    "text",
    [
        "nein",                       # bare "no" is a valid answer, not an abort
        "no",
        "ich vergiss es nie",         # contains "vergiss es" but is a full sentence
        "mach das doch nicht so laut",  # contains "doch nicht" mid-sentence
    ],
)
def test_abort_lookalikes_are_not_aborts(text: str) -> None:
    assert classify_completeness(text).label is not Completeness.ABRUPT_ABORT


# ---------------------------------------------------------------------------
# C-signals — acoustic / timing fusion (endpoint_reason from the VAD)
# ---------------------------------------------------------------------------
def test_cut_off_at_max_utterance_is_incomplete() -> None:
    """A sentence chopped at the max-utterance cap with no terminal
    punctuation is a cut-off the regex alone would miss."""
    verdict = classify_completeness(
        "ich möchte dass du die Datei",
        endpoint_reason="max_utterance",
    )
    assert verdict.label is Completeness.INCOMPLETE
    assert verdict.reason == "cut_off"


def test_same_text_without_cut_off_signal_is_complete() -> None:
    """Without the C-signal the same content-word-ending text defaults to
    COMPLETE (demonstrates the bias and that C adds value)."""
    verdict = classify_completeness("ich möchte dass du die Datei")
    assert verdict.label is Completeness.COMPLETE


def test_terminal_punctuation_overrides_cut_off_signal() -> None:
    verdict = classify_completeness(
        "Öffne Chrome.",
        endpoint_reason="max_utterance",
    )
    assert verdict.label is Completeness.COMPLETE


def test_normal_endpoint_reason_does_not_force_incomplete() -> None:
    verdict = classify_completeness("Öffne Chrome", endpoint_reason="silence")
    assert verdict.label is Completeness.COMPLETE


# ---------------------------------------------------------------------------
# Empty / whitespace — defensive
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_empty_is_incomplete(text: str) -> None:
    verdict = classify_completeness(text)
    assert verdict.label is Completeness.INCOMPLETE
    assert verdict.reason == "empty"


# ---------------------------------------------------------------------------
# Verdict shape & fail-open contract
# ---------------------------------------------------------------------------
def test_verdict_is_frozen_with_reason() -> None:
    verdict = classify_completeness("Öffne Chrome")
    assert isinstance(verdict, CompletenessVerdict)
    assert isinstance(verdict.reason, str) and verdict.reason
    with pytest.raises((AttributeError, Exception)):
        verdict.label = Completeness.INCOMPLETE  # type: ignore[misc]


def test_completeness_enum_is_string_backed() -> None:
    # str-backed so a value can be logged / put on an event payload cheaply
    assert Completeness.COMPLETE.value == "complete"
    assert Completeness.INCOMPLETE.value == "incomplete"
    assert Completeness.ABRUPT_ABORT.value == "abrupt_abort"


# ---------------------------------------------------------------------------
# Regression guard — representative literals from the routing / local-action
# surfaces must all classify COMPLETE so the fast path is never starved.
# ---------------------------------------------------------------------------
ROUTING_LITERALS_THAT_MUST_STAY_COMPLETE = [
    "öffne chrome",
    "starte firefox",
    "mach notepad auf",
    "öffne zwei terminals",
    "spiel spotify ab",
    "wie spät ist es",
    "wechsel auf gemini",     # provider switch — must reach voice_command_gate
    "stopp",                  # task cancel — must reach voice_command_gate
    "denk gründlich nach",    # depth override
    "wo bist du",             # orb reset
    "klick auf den button",   # visual target
]


@pytest.mark.parametrize("text", ROUTING_LITERALS_THAT_MUST_STAY_COMPLETE)
def test_routing_literals_are_not_swallowed(text: str) -> None:
    assert classify_completeness(text).label is Completeness.COMPLETE, (
        f"{text!r} must stay COMPLETE so it reaches its gate/fast-path"
    )
