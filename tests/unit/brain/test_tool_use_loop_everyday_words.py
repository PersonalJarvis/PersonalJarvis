"""Everyday German words must not disarm the tool loop.

Three deterministic guards in ``jarvis/brain/tool_use_loop.py`` keyed on plain
vocabulary, so ordinary work turns were refused:

* the how-to guard fired on any turn that merely OPENED with a question word,
  so the command behind it ("Warum ist Spotify nicht offen, mach es auf")  # i18n-allow
  could never run;
* the meta/debug guard read "fehler", "log", "bug", "provider", "brain" and  # i18n-allow
  "phrase" as talk about the assistant, and its canned acknowledgement ended
  the whole turn even when another tool in the same round had succeeded;
* the research guard blocked every ``cli_*``/MCP tool on a research verb, so a
  request for the user's OWN data ("Analysier meine Cloud-Kosten") could not
  read that data unless the evidence gate had mandated that exact tool.

Each test below pins BOTH directions: the false block is gone and the original
protection still fires.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.brain.spawn_gate import OFFER_WINDOW
from jarvis.brain.tool_use_loop import (
    ToolUseLoop,
    _is_instructional_question,
    _is_meta_debug_intent,
    _should_block_action_as_research,
)
from jarvis.core.protocols import BrainDelta, BrainRequest, ToolResult


class _NamedTool:
    schema: dict[str, Any] = {}

    def __init__(self, name: str) -> None:
        self.name = name


class _McpActionTool:
    name = "supabase/query"
    is_action_tool = True
    schema: dict[str, Any] = {}


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        self.calls.append(tool.name)
        return ToolResult(success=True, output=f"{tool.name} ok")


class _CallsThenAnswerBrain:
    """Emits the given tool calls in round 1, a plain answer afterwards."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self._calls = calls
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            for index, (name, args) in enumerate(self._calls):
                yield BrainDelta(
                    tool_call={"id": f"c{index}", "name": name, "input": args}
                )
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Answer from the tool evidence.")
        yield BrainDelta(finish_reason="stop")


async def _run(
    utterance: str,
    calls: list[tuple[str, dict[str, Any]]],
    tools: dict[str, Any],
) -> tuple[list[str], str]:
    OFFER_WINDOW.disarm()
    brain = _CallsThenAnswerBrain(calls)
    executor = _RecordingExecutor()
    loop = ToolUseLoop(brain, tools, executor)  # type: ignore[arg-type]
    aggregate = await loop.run([], user_utterance=utterance)
    return executor.calls, aggregate.text


# --------------------------------------------------------------------------
# GT-08 — a question opener must not cancel the command behind it
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_question_opener_with_trailing_command_runs_the_command() -> None:
    executed, text = await _run(
        "Warum ist Spotify nicht offen, mach es auf",  # i18n-allow: reported live utterance
        [("open_app", {"app_name": "Spotify"})],
        {"open_app": _NamedTool("open_app")},
    )

    assert executed == ["open_app"], "the imperative behind the question must run"
    assert text == "Answer from the tool evidence."


@pytest.mark.parametrize(
    "utterance",
    [
        "Warum ist Spotify nicht offen? Mach es auf.",  # i18n-allow: same shape, sentence break
        "Warum laeuft der Dienst nicht, starte ihn neu",  # i18n-allow: German command turn
        "Why is Spotify closed? Open it.",
        "Warum ist der Ton weg, stell die Lautstaerke hoch",  # i18n-allow: German command turn
    ],
)
def test_command_after_the_question_stands_the_guard_down(utterance: str) -> None:
    assert _is_instructional_question(utterance) is False


@pytest.mark.parametrize(
    "utterance",
    [
        # The guard's own case: a pure how-to, no command anywhere.
        "Wie kann ich bei Windows reinzoomen?",  # i18n-allow: pinned how-to fixture
        "Wie kann ich Spotify oeffnen?",  # i18n-allow: infinitive, not an imperative
        # The verb form is 1st-person indicative, not a command.
        "Warum geht das nicht und wie starte ich es neu?",  # i18n-allow: German how-to
        "How do I open Spotify?",
        "Why is this failing and how do I restart it?",
        "Was ist ein Container?",  # i18n-allow: definition question
    ],
)
def test_pure_how_to_question_still_blocks(utterance: str) -> None:
    assert _is_instructional_question(utterance) is True


@pytest.mark.asyncio
async def test_pure_how_to_question_still_blocks_the_side_effect_tool() -> None:
    executed, _ = await _run(
        "Wie kann ich bei Windows reinzoomen?",  # i18n-allow: pinned how-to fixture
        [("dispatch_to_harness", {"harness": "computer-use"})],
        {"dispatch_to_harness": _NamedTool("dispatch_to_harness")},
    )

    assert executed == [], "a how-to question must not drive the desktop"


# --------------------------------------------------------------------------
# GT-09 — meta/debug words, and the canned acknowledgement
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        "Zeig mir den Fehler im Log und starte den Dienst neu",  # i18n-allow
        "Im Log steht ein Bug, schau dir das an",  # i18n-allow: everyday German
        "Der Provider in Frankfurt ist ausgefallen",  # i18n-allow: everyday German
        "Debug das Skript fuer mich",  # i18n-allow: everyday German
    ],
)
def test_everyday_words_are_not_meta_conversation(utterance: str) -> None:
    assert _is_meta_debug_intent(utterance) is False


@pytest.mark.parametrize(
    "utterance",
    [
        # Strong markers: assistant machinery, no everyday reading.
        "Warum hat der Provider-Fallback gegriffen?",  # i18n-allow: pinned meta fixture
        "Deine Standardantwort nervt",  # i18n-allow: German meta fixture
        "Der API Key war doch gesetzt",  # i18n-allow: German meta fixture
        # Weak marker plus a turn that points at the assistant.
        "Warum sagst du immer diese Phrase?",  # i18n-allow: German meta fixture
        "Dein Transkript stimmt hinten und vorne nicht",  # i18n-allow: German meta fixture
    ],
)
def test_meta_conversation_is_still_detected(utterance: str) -> None:
    assert _is_meta_debug_intent(utterance) is True


@pytest.mark.asyncio
async def test_meta_utterance_alone_still_ends_on_the_acknowledgement() -> None:
    """The guard's original protection: a meta turn must never be delegated,
    and the user must hear something rather than run into the stream timeout."""
    executed, text = await _run(
        "Der Provider-Fallback hat schon wieder gegriffen",  # i18n-allow: German meta fixture
        [("spawn_worker", {"utterance": "investigate"})],
        {"spawn_worker": _NamedTool("spawn_worker")},
    )

    assert executed == [], "a meta turn must not spawn a worker"
    assert text and "?" in text, "the neutral acknowledgement must be spoken"


@pytest.mark.asyncio
async def test_acknowledgement_never_overwrites_a_successful_tool_result() -> None:
    """The canned line ends the turn, so it may only speak when nothing else in
    the turn produced a result. Previously a round that ran run_shell AND asked
    for a spawn ended on the acknowledgement and threw the shell result away."""
    executed, text = await _run(
        "Der Provider-Fallback hat schon wieder gegriffen",  # i18n-allow: German meta fixture
        [
            ("run_shell", {"cmd": "systemctl status"}),
            ("spawn_worker", {"utterance": "investigate"}),
        ],
        {
            "run_shell": _NamedTool("run_shell"),
            "spawn_worker": _NamedTool("spawn_worker"),
        },
    )

    assert executed == ["run_shell"], "the shell call must still run"
    assert text == "Answer from the tool evidence."


@pytest.mark.asyncio
async def test_explicit_delegation_about_a_meta_topic_still_spawns() -> None:
    """Naming the vehicle out loud is a delegation request, not a complaint."""
    executed, _ = await _run(
        "Spawne einen Agenten und finde raus, warum der Fallback griff",  # i18n-allow
        [("spawn_worker", {"utterance": "investigate"})],
        {"spawn_worker": _NamedTool("spawn_worker")},
    )

    assert executed == ["spawn_worker"]


# --------------------------------------------------------------------------
# GT-10 — a request for the user's OWN data is not literature research
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        "Analysier meine Cloud-Kosten",  # i18n-allow: reported live utterance
        "Fass unsere offenen Rechnungen zusammen",  # i18n-allow: German own-data turn
        "Analyse my cloud spend",
        "Analiza mis costes",  # i18n-allow: Spanish own-data turn
    ],
)
def test_own_data_request_is_not_blocked_as_research(utterance: str) -> None:
    tool = _NamedTool("cli_gcloud")
    assert _should_block_action_as_research(
        tool, "cli_gcloud", utterance, "deep", evidence_required_tool=""
    ) is False


@pytest.mark.parametrize(
    ("utterance", "tool_name"),
    [
        # No possessive: the guard's premise (info ABOUT a topic) holds.
        ("Erklaer mir mal Supabase", "supabase/query"),  # i18n-allow: German research turn
        ("Vergleiche mal die Cloud-Anbieter", "supabase/query"),  # i18n-allow: German research turn
        ("Explain how Postgres replication works", "supabase/query"),
    ],
)
def test_research_without_a_possessive_is_still_blocked(
    utterance: str, tool_name: str
) -> None:
    assert _should_block_action_as_research(
        _McpActionTool(), tool_name, utterance, "deep", evidence_required_tool=""
    ) is True


@pytest.mark.asyncio
async def test_own_data_request_reaches_the_cli_tool() -> None:
    executed, text = await _run(
        "Analysier meine Cloud-Kosten",  # i18n-allow: reported live utterance
        [("cli_gcloud", {"args": "billing list"})],
        {"cli_gcloud": _NamedTool("cli_gcloud")},
    )

    assert executed == ["cli_gcloud"]
    assert text == "Answer from the tool evidence."
