"""Option A (2026-06-15): the artefact-vs-answer discriminator that decides
whether a heavy-research request is offloaded to a sub-agent MISSION or answered
INLINE via the router's search_web tool.

A research request whose deliverable is an ANSWER (a comparison / overview /
recommendation / summary) stays INLINE — the Worker->Critic mission pipeline
grades BUILT ARTIFACTS via git diff and is hostile to an answer-only research
turn (empty-diff veto -> critic_loop_exhausted, live mission 019ecb56). Only a
request that builds a FILE / report / document offloads to a mission.

These tests pin ``BrainManager._research_wants_artifact`` directly. The method is
pure over the module-level regexes (it never touches ``self``), so it is called
with ``self=None`` — no BrainManager wiring needed. This keeps the committed
coverage decoupled from the ``_should_force_spawn`` integration tests in
test_routing.py (which a parallel session is concurrently editing). The
end-to-end routing path (heavy research + artefact -> spawn; answer-only ->
inline) is exercised there.
"""
from __future__ import annotations

import pytest

from jarvis.brain.manager import BrainManager


def _wants_artifact(text: str) -> bool:
    """Call the REAL method (self is unused, so None is safe)."""
    return BrainManager._research_wants_artifact(None, text)  # type: ignore[arg-type]


# Deliverable is an ANSWER -> inline (not an artefact).
_ANSWER_ONLY = [
    "Research the leading AI language models and compare their strengths.",
    "Analyze my spending over the last year and compare it to the prior year.",
    "Research the top five vector databases and compare them over the next quarter",
    "Recherchiere die KI-Durchbrueche und gib mir einen Ueberblick.",  # i18n-allow
]

# Deliverable is a BUILT FILE / report -> mission.
_ARTIFACT = [
    "Research and compare vector databases, then write a report into compare.md",
    "Research the AI landscape and write a detailed report document.",
    "Save the analysis into a file named summary.txt",
    "Recherchiere die KI-News und erstelle einen Bericht.",  # i18n-allow
]


@pytest.mark.parametrize("text", _ANSWER_ONLY)
def test_answer_only_research_is_not_an_artifact(text: str) -> None:
    assert _wants_artifact(text) is False


@pytest.mark.parametrize("text", _ARTIFACT)
def test_research_that_builds_a_file_is_an_artifact(text: str) -> None:
    assert _wants_artifact(text) is True


def test_named_file_alone_is_an_artifact_without_a_build_verb() -> None:
    # The file IS the deliverable, even with no explicit build verb.
    assert _wants_artifact("the findings, into ai_news.md") is True


def test_build_verb_without_a_document_noun_is_not_an_artifact() -> None:
    # "write a summary" is an answer, not a file/report deliverable.
    assert _wants_artifact("research X and write a short summary") is False


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_blank_is_not_an_artifact(text: str) -> None:
    assert _wants_artifact(text) is False
