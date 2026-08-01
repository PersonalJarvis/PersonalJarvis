"""Build-a-deliverable requests without an explicit delegation trigger stay inline.

History: the 2026-06-21 fix (voice session 19:13, "build me an HTML file ...")
made build-a-deliverable requests force-spawn a mission deterministically,
because a tool-incapable talker could not spawn via an LLM tool_call. The
maintainer mandate 2026-07-21 SUPERSEDES that implicit spawn: a background
agent starts ONLY on an explicit ask (``force_spawn_phrases``: "spawn",
"Subagent", "deep dive", the depth markers, …). A build command without
delegation wording is handled by the router LLM, which answers inline or
OFFERS a background agent (jarvis.brain.spawn_gate) — the user's confirming
yes unlocks exactly one spawn.

The artifact DISCRIMINATOR (``_research_wants_artifact``) stays intact — it
still powers the spawn-tool visibility gates — and an explicit trigger wrapped
around a build request still force-spawns.

Deterministic — no LLM, no real brain.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


class _FakeTool:
    name = "spawn_worker"
    schema: dict[str, Any] = {}


class _Inert:
    async def execute(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("no exec in a classification test")


def _manager() -> BrainManager:
    config = JarvisConfig()
    config.brain.routing.force_spawn_mode = "strict"  # production default
    return BrainManager(
        config=config,
        bus=EventBus(),
        tools={"spawn_worker": _FakeTool()},
        tool_executor=_Inert(),  # type: ignore[arg-type]
    )


# The exact failing utterance (English, no research verb, "HTML file" as a word).
FAILING = (
    "I would like you to build me an HTML file. I would like you to write all "
    "important stuff in it. I would really make it visualized, visual, so I can "
    "see everything in a great visualization. I would like you to help me with "
    "this HTML file how I can prepare my vacation to Melbourne."
)


def test_build_an_html_file_no_longer_force_spawns_implicitly() -> None:
    assert _manager()._should_force_spawn(FAILING) is False, (
        "a build request without an explicit delegation trigger must not "
        "force-spawn (mandate 2026-07-21) — the router LLM answers inline or "
        "offers delegation"
    )


# A build-a-deliverable request WITHOUT delegation wording — DE + EN. Since
# 2026-07-21 these stay inline (explicit-only strict mode).
_BUILD_DELIVERABLE = [
    "Build me a website about my startup",
    "Create a PDF report about the AI market",
    "Bau mir eine Webseite über meinen Urlaub",  # i18n-allow
    "Erstell mir ein interaktives Dashboard mit den wichtigsten Zahlen",  # i18n-allow
    "Write me an HTML page that visualizes my expenses",
    "Generate a landing page for the product launch",
]


@pytest.mark.parametrize("utterance", _BUILD_DELIVERABLE)
def test_build_deliverable_stays_inline_without_trigger(utterance: str) -> None:
    assert _manager()._should_force_spawn(utterance) is False, (
        f"build request {utterance!r} must not force-spawn without an "
        "explicit delegation trigger (mandate 2026-07-21)"
    )


@pytest.mark.parametrize(
    "utterance",
    [
        "Spawne einen Agenten, der mir eine Website über mein Startup baut",  # i18n-allow
        "Do a deep dive and build me a website about my startup",
    ],
)
def test_build_with_explicit_trigger_still_force_spawns(utterance: str) -> None:
    assert _manager()._should_force_spawn(utterance) is True, (
        f"explicit delegation wording in {utterance!r} must still force-spawn"
    )


# Must NOT over-trigger: these are NOT build-a-deliverable missions.
_NOT_A_BUILD_MISSION = [
    # knowledge / instructional — answered inline
    "What is an HTML file?",
    "Wie erstelle ich eine HTML-Datei?",
    # an ANSWER deliverable (pinned by the artefact discriminator) — inline
    "research X and write a short summary",
    # smalltalk
    "Hallo",
    "Danke",
]


@pytest.mark.parametrize("utterance", _NOT_A_BUILD_MISSION)
def test_non_build_requests_do_not_force_spawn(utterance: str) -> None:
    assert _manager()._should_force_spawn(utterance) is False, (
        f"{utterance!r} is not a build-a-deliverable mission and must not spawn"
    )


def test_artifact_discriminator_recognises_html_file() -> None:
    """The shared artifact detector must treat 'an HTML file' as a deliverable —
    without requiring a literal dotted extension."""
    assert BrainManager._research_wants_artifact(None, FAILING) is True  # type: ignore[arg-type]
    # but a bare answer-summary is still NOT an artifact (discriminator contract)
    assert (
        BrainManager._research_wants_artifact(None, "write a short summary")  # type: ignore[arg-type]
        is False
    )
