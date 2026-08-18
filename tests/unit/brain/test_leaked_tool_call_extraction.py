"""Envelope extraction for tool calls a provider emitted as response TEXT.

Audit GT-13: the recovery only fired when the ENTIRE response was valid JSON.
A model that wrote "Ich öffne Spotify. {tool_use…}" therefore executed
nothing — the whole-text parse failed, the call was dropped, the turn ended as
plain text, and the maintainer's complaint was the literal "nothing happens".
Half a recovery is worse than none: it produces exactly the silent failure it
was built to prevent.

These tests pin both directions at once: an embedded envelope IS recovered,
and a QUOTED one is still never executed. The second half matters more — a
missed recovery costs one answer, a wrongly executed call is a real side
effect.
"""
from __future__ import annotations

import pytest

from jarvis.brain.tool_call_recovery import extract_leaked_tool_calls

_ENVELOPE = '{"type":"tool_use","name":"open_app","input":{"app":"spotify"}}'
_SECOND_ENVELOPE = '{"type":"tool_use","name":"open_app","input":{"app":"notepad"}}'


def _names_and_inputs(text: str) -> list[tuple[str, dict[str, object]]]:
    return [(call["name"], call["input"]) for call in extract_leaked_tool_calls(text)]


# ---------------------------------------------------------------------------
# Recovered: the envelope is the action, whatever prose surrounds it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("prose before", f"Ich öffne Spotify. {_ENVELOPE}"),
        ("prose after", f"{_ENVELOPE} Ich öffne Spotify."),
        ("prose on both sides", f"Klar! {_ENVELOPE} — läuft."),
        ("fenced after prose", f"Ich öffne Spotify.\n```json\n{_ENVELOPE}\n```"),
        ("bare fence", f"```\n{_ENVELOPE}\n```"),
        ("whole text", _ENVELOPE),
        ("whole-text list", f"[{_ENVELOPE}]"),
        ("newline separated", f"Ich öffne Spotify.\n\n{_ENVELOPE}\n"),
    ],
)
def test_embedded_envelope_is_recovered(label: str, text: str) -> None:
    assert _names_and_inputs(text) == [("open_app", {"app": "spotify"})], label


def test_two_distinct_embedded_envelopes_both_run_in_order() -> None:
    """Each envelope passed the same gates on its own, so each is honoured."""
    text = f"Ich öffne Spotify. {_ENVELOPE} Und den Editor. {_SECOND_ENVELOPE}"

    assert _names_and_inputs(text) == [
        ("open_app", {"app": "spotify"}),
        ("open_app", {"app": "notepad"}),
    ]


def test_repeated_identical_envelope_executes_once() -> None:
    """Same name, same arguments — one action, however often it was written."""
    assert _names_and_inputs(f"{_ENVELOPE} {_ENVELOPE}") == [
        ("open_app", {"app": "spotify"})
    ]


def test_brace_inside_a_json_string_does_not_end_the_envelope() -> None:
    text = (
        'Ich tippe das ein. {"type":"tool_use","name":"type_text",'
        '"input":{"text":"} nicht das Ende \\" auch nicht"}}'
    )

    assert _names_and_inputs(text) == [
        ("type_text", {"text": '} nicht das Ende " auch nicht'})
    ]


# ---------------------------------------------------------------------------
# Never recovered: the envelope is being TALKED ABOUT, not invoked.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "text"),
    [
        (
            "english example marker",
            "For example, a provider might emit "
            '{"type":"tool_use","name":"gmail","input":{}} in prose.',
        ),
        ("german example marker", f"Ein Block sieht zum Beispiel so aus: {_ENVELOPE}"),
        ("spanish example marker", f"Por ejemplo, el modelo emite {_ENVELOPE}"),
        ("names the format", f"Das JSON dafür lautet: {_ENVELOPE}"),
        ("names the mechanism", f"Ein tool_use-Block trägt zwei Felder: {_ENVELOPE}"),
        ("inline code span", f"Der Block `{_ENVELOPE}` gehört dazu."),
        ("quoted string", f'Er antwortete "{_ENVELOPE}" und ging.'),
        ("non-json fence", f"```python\n{_ENVELOPE}\n```"),
        ("truncated fence", f"```json\n{_ENVELOPE}"),
        (
            "paragraph of explanation",
            "Der Aufbau ist einfach: das Feld type sagt, worum es geht, name "
            "trägt den Werkzeugnamen und input die Argumente. Ein "
            f"vollständiger Block sieht dann so aus: {_ENVELOPE} Mehr steckt "
            "nicht dahinter, und mehr braucht es auch gar nicht.",
        ),
    ],
)
def test_quoted_sample_is_never_executed(label: str, text: str) -> None:
    assert extract_leaked_tool_calls(text) == [], label


def test_one_quoted_envelope_stands_down_the_whole_text() -> None:
    """Mixed intent is not a licence to execute the half that looks clean."""
    text = f"Ich öffne Spotify. {_ENVELOPE} Der Block `{_SECOND_ENVELOPE}` erklärt es."

    assert extract_leaked_tool_calls(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Ich habe Spotify geöffnet.",
        '{"app":"spotify","name":"open_app"}',  # plain JSON, no envelope
        '{"type":"tool_use","name":"","input":{}}',  # no tool name
        '{"type":"tool_use","name":"gmail","input":"{bad json"}',
        '{"type":"tool_use","name":"gmail","input":["not","an","object"]}',
    ],
)
def test_non_envelopes_stay_unexecuted(text: str) -> None:
    assert extract_leaked_tool_calls(text) == []


def test_recovered_call_carries_a_stable_content_derived_id() -> None:
    """The loop de-duplicates on the id, so the same call must hash the same."""
    embedded = extract_leaked_tool_calls(f"Ich öffne Spotify. {_ENVELOPE}")
    whole_text = extract_leaked_tool_calls(_ENVELOPE)

    assert embedded[0]["id"] == whole_text[0]["id"]
    assert embedded[0]["id"].startswith("leaked_")
