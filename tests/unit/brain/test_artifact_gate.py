"""Tests for the explicit-request gate in front of the ``create_artifact`` tool.

The contract is asymmetric on purpose. A missed request costs one extra turn —
the user repeats themselves and gets their artifact. An unasked-for artifact
is the failure this gate exists to prevent, so the negative list is the
important half of this file and carries the real regressions:

* only the literal word builds — "Artefakt"/"Artifact"/"Artefacto" said to
  Jarvis or its agents, or pinned via the Add menu (which bypasses this gate
  through the turn override's extra hands);
* visual verbs and page shapes alone ("visualisier", "Dashboard",
  "Diagramm", "Seite", "Bericht") never build;
* the utterance that opens the EXISTING section must never build a new page
  (that is ``navigate``'s job, and both share the word "Artefakt"),
* a question about the word is not a request for the thing.

Every German/Spanish literal here is speech-input vocabulary under test.
"""

from __future__ import annotations

import pytest

from jarvis.brain.artifact_gate import wants_artifact

# Turns that ask, literally, for an artifact — the only words that build.
_WANTS = [
    "mach mir ein artefakt daraus",  # i18n-allow: German speech-input test vocabulary
    "ich möchte ein artefakt davon",  # i18n-allow: German speech-input test vocabulary
    "ein artefakt bitte",  # i18n-allow: German speech-input test vocabulary
    "build me an artifact for this",
    "turn that into an artifact",
    "create an artifact from this",
    "hazme un artefacto con esto",  # i18n-allow: Spanish speech-input test vocabulary
    "visualisier mir das als artefakt",  # i18n-allow: German speech-input test vocabulary
    "mach mir ein dashboard als artefakt",  # i18n-allow: German speech-input test vocabulary
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
    "zeig mir die artefakte",  # i18n-allow: German speech-input test vocabulary
    "open the artifacts section",
    "geh zu den artefakten",  # i18n-allow: German speech-input test vocabulary
    # a question about the word, answered with words
    "was ist eine visualisierung",  # i18n-allow: German speech-input test vocabulary
    "was bedeutet datenvisualisierung",  # i18n-allow: German speech-input test vocabulary
    "what is a flowchart",
    "explain what a mindmap is",
    "was ist ein artefakt",  # i18n-allow: German speech-input test vocabulary
    "what is an artifact",
    # visual verbs and page shapes alone never build — literal word or Add pin only
    "visualisier mir das mal",  # i18n-allow: German speech-input test vocabulary
    "visualisiere das bitte",  # i18n-allow: German speech-input test vocabulary
    "kannst du mir das visualisieren",  # i18n-allow: German speech-input test vocabulary
    "visualize this for me",
    "veranschaulich mir den ablauf",  # i18n-allow: German speech-input test vocabulary
    "skizzier mir kurz die architektur",  # i18n-allow: German speech-input test vocabulary
    "mach eine mindmap daraus",  # i18n-allow: German speech-input test vocabulary
    "draw me a flowchart of the deploy",
    "mach mir ein diagramm von den schritten",  # i18n-allow: German speech-input test vocabulary
    "gib mir eine grafik dazu",  # i18n-allow: German speech-input test vocabulary
    "show me that visually",
    "turn this into a timeline",
    "zeig mir eine visualisierung von den zahlen",  # i18n-allow: DE test vocabulary
    "show me a visualization of the results",
    "bau mir ein dashboard mit den zahlen",  # i18n-allow: German speech-input test vocabulary
    "make an infographic of the results",
    "mach mir eine html-seite dazu",  # i18n-allow: German speech-input test vocabulary
    "erstell mir einen bericht als seite",  # i18n-allow: German speech-input test vocabulary
    "build me a report page on this",
    # ordinary turns that merely mention something chart-shaped
    "der chart ist heute rot",  # i18n-allow: German speech-input test vocabulary
    "wie ist der bitcoin chart gerade",  # i18n-allow: German speech-input test vocabulary
    "das diagramm im bericht war falsch",  # i18n-allow: German speech-input test vocabulary
    "die seite war heute langsam",  # i18n-allow: German speech-input test vocabulary
    "the report is due on friday",
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
    assert wants_artifact(text) is True, text


@pytest.mark.parametrize("text", _DOES_NOT_WANT)
def test_everything_else_keeps_the_gate_shut(text: str) -> None:
    assert wants_artifact(text) is False, text


def test_navigation_beats_the_literal_word() -> None:
    """The shared word must resolve to navigation, not to a new page.

    "Artefakt" is both the section name and the thing being asked for. Rule
    order (navigation first) is what keeps ``navigate`` reachable, so it is
    pinned here rather than left to the parametrized lists.
    """
    assert wants_artifact("zeig mir die visualisierungen") is False  # i18n-allow: input vocab
    assert wants_artifact("zeig mir die artefakte") is False  # i18n-allow: input vocab
    assert wants_artifact("visualisier mir die zahlen") is False  # i18n-allow: input vocab
    assert wants_artifact("zeig mir ein artefakt von den zahlen") is True  # i18n-allow: input vocab


def test_a_visual_noun_alone_is_not_a_request() -> None:
    """Visual words without the literal word never build."""
    assert wants_artifact("das diagramm") is False  # i18n-allow: input vocab
    assert wants_artifact("timeline") is False
    assert wants_artifact("die webseite") is False  # i18n-allow: input vocab
    assert wants_artifact("visualisier mir das") is False  # i18n-allow: input vocab
    assert wants_artifact("mach mir ein dashboard") is False  # i18n-allow: input vocab
