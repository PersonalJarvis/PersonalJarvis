"""``is_harmless_scrub_residue`` splits the two turns the residue guard merges.

The guard in ``scrub_for_voice`` returns the canned error phrase whenever a
filter fired and almost nothing survived. Non-streaming voice call sites need
to know WHY it fired: harmless prose removal (stay silent) or a scrubbed
machine leak (speak the error phrase). The classification itself is reused from
``jarvis.realtime.scrub_gate`` so both the streaming and the non-streaming
verdict cannot drift apart — these tests pin the reuse against real
``scrub_for_voice`` output, not against a hand-built ``ScrubResult``.
"""
from __future__ import annotations

import pytest

from jarvis.brain.output_filter import FALLBACK_PHRASES, ScrubResult, scrub_for_voice
from jarvis.brain.scrub_verdict import is_harmless_scrub_residue

HARMLESS = [
    ("Tolle Frage!", "de"),  # i18n-allow: German filler opener under test
    ("Als KI kann ich das nicht.", "de"),  # i18n-allow: German self-reference under test
    ("Great question.", "en"),
    ("As an AI.", "en"),
    ("Sir,", "en"),
    ("**...**", "de"),
    ("—", "de"),
]

REAL_LEAKS = [
    (
        "<function_calls><invoke name='read_file'>"
        "<parameter name='path'>x</parameter></invoke></function_calls>",
        "de",
    ),
    ('{"tool_call": {"name": "read_file", "arguments": {"path": "x"}}}', "de"),
    (
        'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\n'
        '    raise ValueError("boom")',
        "de",
    ),
]


@pytest.mark.parametrize(("text", "language"), HARMLESS)
def test_prose_only_residue_is_harmless(text: str, language: str) -> None:
    result = scrub_for_voice(text, language=language)
    # Precondition: without the verdict this text WOULD be spoken as an error.
    assert result.cleaned == FALLBACK_PHRASES[language]
    assert is_harmless_scrub_residue(result) is True


@pytest.mark.parametrize(("text", "language"), REAL_LEAKS)
def test_scrubbed_leak_is_not_harmless(text: str, language: str) -> None:
    result = scrub_for_voice(text, language=language)
    assert result.cleaned == FALLBACK_PHRASES[language]
    assert is_harmless_scrub_residue(result) is False


def test_ordinary_answer_is_not_a_residue_case() -> None:
    # i18n-allow: German voice answer under test
    result = scrub_for_voice("Es sind achtzehn Grad.", language="de")
    assert is_harmless_scrub_residue(result) is False


def test_unclassified_action_stays_blocking() -> None:
    """A scrub action nobody classified must keep the error phrase (fail-closed)."""
    result = ScrubResult(
        cleaned=FALLBACK_PHRASES["de"],
        actions=["removed_something_brand_new", "replaced_with_fallback_residue"],
        fallback_used=True,
    )
    assert is_harmless_scrub_residue(result) is False
