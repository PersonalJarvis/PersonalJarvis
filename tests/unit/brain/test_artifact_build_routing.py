"""Build-a-deliverable requests must route to a sub-agent MISSION, not the brain.

Live bug 2026-06-21 (voice session 19:13): "I would like you to build me an HTML
file ... visualize ... how I can prepare my vacation to Melbourne" fell through to
the deep brain (Antigravity, a tool-incapable CLI) which — unable to call any tool
— just asked permission ("should I create the file on your disk?") instead of
building anything. A build-a-deliverable request (HTML/website/app/report/
document/visualization/file) is a sub-agent mission: the Worker->Critic pipeline
verifies the built artifact via git diff.

The fix must be PROVIDER-INDEPENDENT (the deterministic force-spawn gate), because
a tool-incapable talker (Codex/Antigravity over a subscription CLI) cannot spawn
via an LLM tool_call at all — the gate is the only spawn path for it. It must NOT
over-trigger: a build verb is not a screen action, so "open/show the file" stays
Computer-Use and pure questions/answers stay inline.

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


def test_build_an_html_file_routes_to_a_mission() -> None:
    assert _manager()._should_force_spawn(FAILING) is True, (
        "a build-an-HTML-file request must force-spawn a sub-agent mission, not "
        "fall through to the (tool-incapable) talker that just asks permission"
    )


# A build-a-deliverable request is a mission — DE + EN, even without a research verb.
_BUILD_DELIVERABLE = [
    "Build me a website about my startup",
    "Create a PDF report about the AI market",
    "Bau mir eine Webseite über meinen Urlaub",
    "Erstell mir ein interaktives Dashboard mit den wichtigsten Zahlen",
    "Write me an HTML page that visualizes my expenses",
    "Generate a landing page for the product launch",
]


@pytest.mark.parametrize("utterance", _BUILD_DELIVERABLE)
def test_build_deliverable_force_spawns(utterance: str) -> None:
    assert _manager()._should_force_spawn(utterance) is True, (
        f"build-a-deliverable request {utterance!r} should route to a mission"
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
