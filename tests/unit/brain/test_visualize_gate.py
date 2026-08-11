"""Tests for the explicit-request gate in front of the ``visualize`` tool.

The contract is asymmetric on purpose. A missed request costs one extra turn —
the user repeats themselves and gets their picture. An unasked-for picture is
the failure this gate exists to prevent (maintainer mandate 2026-08-11: "only
when someone says they want to understand it visually"), so the negative list
is the important half of this file and carries the real regressions:

* the utterance that opens the EXISTING gallery must never draw a new picture
  (that is ``navigate``'s job, and both share the word "Visualisierung"),
* a question about the word is not a request for the thing,
* an ordinary turn that merely mentions a chart is not a request either.

Every German/Spanish literal here is speech-input vocabulary under test.
"""
from __future__ import annotations

import pytest

from jarvis.brain.visualize_gate import wants_visualization

# Turns that ask, in the user's own words, to be shown something as a picture.
_WANTS = [
    # explicit drawing verb — enough on its own
    "visualisier mir das mal",  # i18n-allow: German speech-input test vocabulary
    "visualisiere das bitte",  # i18n-allow: German speech-input test vocabulary
    "kannst du mir das visualisieren",  # i18n-allow: German speech-input test vocabulary
    "visualize this for me",
    "visualise it",
    "veranschaulich mir den ablauf",  # i18n-allow: German speech-input test vocabulary
    "skizzier mir kurz die architektur",  # i18n-allow: German speech-input test vocabulary
    "mach eine mindmap daraus",  # i18n-allow: German speech-input test vocabulary
    "draw me a flowchart of the deploy",
    "ein flussdiagramm dazu waere super",  # i18n-allow: German speech-input test vocabulary
    "visualízamelo por favor",  # i18n-allow: Spanish speech-input test vocabulary
    # build/show verb + visual noun — a request only in combination
    "erklär mir das bildlich",  # i18n-allow: German speech-input test vocabulary
    "zeig mir das grafisch",  # i18n-allow: German speech-input test vocabulary
    "stell das mal visuell dar",  # i18n-allow: German speech-input test vocabulary
    "mach mir ein diagramm von den schritten",  # i18n-allow: German speech-input test vocabulary
    "gib mir eine grafik dazu",  # i18n-allow: German speech-input test vocabulary
    "show me that visually",
    "can you explain this as a diagram",
    "turn this into a timeline",
    "hazme un diagrama de esto",  # i18n-allow: Spanish speech-input test vocabulary
    # a nav verb followed by "of/for" is still a request for a NEW picture
    "zeig mir eine visualisierung von den zahlen",  # i18n-allow: DE test vocabulary
    "show me a visualization of the results",
]

# Turns that must leave the tool out of the set entirely.
_DOES_NOT_WANT = [
    # navigation to the gallery that already exists — navigate's job
    "zeig mir die visualisierungen",  # i18n-allow: German speech-input test vocabulary
    "öffne die visualisierung",  # i18n-allow: German speech-input test vocabulary
    "geh zu den visualisierungen",  # i18n-allow: German speech-input test vocabulary
    "open the visualization section",
    "show the visualizations",
    "switch to visuals",
    "muestrame las visualizaciones",  # i18n-allow: Spanish speech-input test vocabulary
    # a question about the word, answered with words
    "was ist eine visualisierung",  # i18n-allow: German speech-input test vocabulary
    "was bedeutet datenvisualisierung",  # i18n-allow: German speech-input test vocabulary
    "what is a flowchart",
    "explain what a mindmap is",
    # ordinary turns that merely mention something chart-shaped
    "der chart ist heute rot",  # i18n-allow: German speech-input test vocabulary
    "wie ist der bitcoin chart gerade",  # i18n-allow: German speech-input test vocabulary
    "das diagramm im bericht war falsch",  # i18n-allow: German speech-input test vocabulary
    # the everyday turns this gate keeps cheap
    "wie spät ist es",  # i18n-allow: German speech-input test vocabulary
    "was haben wir gerade besprochen",  # i18n-allow: German speech-input test vocabulary
    "schreib mir eine mail an ruben",  # i18n-allow: German speech-input test vocabulary
    "erklär mir wie tcp funktioniert",  # i18n-allow: German speech-input test vocabulary
    "explain how the router picks a tool",
    "summarize the last three emails",
    "bau mir eine flask app",  # i18n-allow: German speech-input test vocabulary
    "mach das fenster zu",  # i18n-allow: German speech-input test vocabulary
    "",
    "   ",
]


@pytest.mark.parametrize("text", _WANTS)
def test_explicit_requests_open_the_gate(text: str) -> None:
    assert wants_visualization(text) is True, text


@pytest.mark.parametrize("text", _DOES_NOT_WANT)
def test_everything_else_keeps_the_gate_shut(text: str) -> None:
    assert wants_visualization(text) is False, text


def test_navigation_beats_the_drawing_verb() -> None:
    """The shared word must resolve to navigation, not to a new picture.

    "Visualisierung" is both the section name and the thing being asked for.
    Rule order (navigation first) is what keeps ``navigate`` reachable, so it
    is pinned here rather than left to the parametrized lists.
    """
    assert wants_visualization("zeig mir die visualisierungen") is False  # i18n-allow: input vocab
    assert wants_visualization("visualisier mir die zahlen") is True  # i18n-allow: input vocab


def test_a_visual_noun_alone_is_not_a_request() -> None:
    """Without a producing verb, "diagram" is just a word in a sentence."""
    assert wants_visualization("das diagramm") is False  # i18n-allow: input vocab
    assert wants_visualization("timeline") is False
