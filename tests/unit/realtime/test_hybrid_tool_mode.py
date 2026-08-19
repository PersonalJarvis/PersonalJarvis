"""ADR-0035: hybrid realtime tool mode — the live model calls every Jarvis
tool itself, the Tool Model is for computer use.

Guards: the declared set (catalog minus the computer-use vehicles, plus
``jarvis_action`` and ``end_call``), the narrowed ``jarvis_action``, the
deterministic hand-over (explicit computer use forces the delegate, the
live mis-routes of 2026-08-18 do not), the declaration budget, and the
execute-side exclusion of the computer-use vehicles.
"""

# ruff: noqa: F811 - pytest fixtures re-exported from test_session are re-bound by name
from __future__ import annotations

import asyncio

import pytest

from jarvis.brain.cu_gate import CU_VEHICLE_TOOL_NAMES, is_explicit_computer_use_turn
from jarvis.core.protocols import AudioChunk, SupervisorToolDescriptor, ToolResult
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.tools import (
    COMPACT_DESCRIPTION_CHARS,
    RealtimeToolBridge,
    _apply_declaration_budget,
)
from tests.unit.realtime.test_session import (
    FakeBrain,
    FakeProvider,
    _session,
    _StubExecutor,
    _StubTool,
    _tool_names,
    wire_supervisor_gateway,  # noqa: F401 - pytest fixture re-export
)


class _CalendarTool(_StubTool):
    name = "google_calendar"
    description = "Read and write the user's Google Calendar. " * 40
    risk_tier = "monitor"
    schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "What to do with the calendar. " * 20,
            }
        },
        "required": ["action"],
    }


class _ComputerUseTool(_StubTool):
    name = "computer_use"
    description = "Drive the user's live desktop."
    risk_tier = "monitor"


class _SearchTool(_StubTool):
    name = "search_web"
    description = "Search the public web."
    risk_tier = "safe"


def _hybrid_brain():
    brain = FakeBrain(replies=("delegated",))
    brain._tools = {
        "open_app": _StubTool(),
        "google_calendar": _CalendarTool(),
        "computer_use": _ComputerUseTool(),
        "search_web": _SearchTool(),
    }
    return brain


@pytest.mark.asyncio
async def test_hybrid_declares_catalog_minus_cu_plus_jarvis_action(
    wire_supervisor_gateway,
):
    brain = _hybrid_brain()
    wire_supervisor_gateway(brain, _StubExecutor())
    provider = FakeProvider([RealtimeEvent(type="turn_complete")])
    sess = _session(provider, brain=brain, tool_mode="hybrid")

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    names = _tool_names(provider.opened_with)
    assert "open_app" in names
    assert "google_calendar" in names
    assert "search_web" in names
    assert "jarvis_action" in names
    assert "end_call" in names
    assert not (set(names) & CU_VEHICLE_TOOL_NAMES), names
    # jarvis_action is the narrowed hybrid declaration, not the delegate one.
    action = next(d for d in provider.opened_with.tools if d["name"] == "jarvis_action")
    assert "call your own matching function directly" in action["description"]
    assert "operating the user's computer on screen" in action["description"]
    # The role directive is the hybrid one: functions for the user's world.
    instructions = provider.opened_with.instructions
    assert "is reached with YOUR OWN functions" in instructions
    assert "jarvis_action is reserved for operating the computer on screen" in instructions
    # Compact rendering: the 40x description is cut, the schema survives.
    calendar = next(d for d in provider.opened_with.tools if d["name"] == "google_calendar")
    assert len(calendar["description"]) <= COMPACT_DESCRIPTION_CHARS + 1
    assert calendar["parameters"]["required"] == ["action"]


@pytest.mark.asyncio
async def test_hybrid_falls_back_to_delegate_when_a_provider_cannot_declare_tools(
    wire_supervisor_gateway,
):
    class _HandoffProvider(FakeProvider):
        supports_direct_tools = False

    brain = _hybrid_brain()
    wire_supervisor_gateway(brain, _StubExecutor())
    provider = _HandoffProvider([RealtimeEvent(type="turn_complete")])
    sess = _session(provider, brain=brain, tool_mode="hybrid")

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")

    assert sess._tool_mode == "delegate"
    assert _tool_names(provider.opened_with) == ["jarvis_action", "end_call"]


@pytest.mark.asyncio
async def test_hybrid_bridge_never_executes_a_computer_use_vehicle(
    wire_supervisor_gateway,
):
    brain = _hybrid_brain()
    executor = _StubExecutor()
    wire_supervisor_gateway(brain, executor)
    provider = FakeProvider([RealtimeEvent(type="turn_complete")])
    sess = _session(provider, brain=brain, tool_mode="hybrid")
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    bridge = sess._tool_bridge
    assert bridge is not None
    name, result = await bridge.execute(wire_name="computer_use", arguments={})
    assert result["success"] is False
    assert "not available" in result["error"]
    name, result = await bridge.execute(wire_name="open_app", arguments={})
    assert name == "open_app"
    assert result["success"] is True
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_hybrid_leaves_planner_action_turns_to_the_live_model(
    wire_supervisor_gateway,
):
    """A planner 'orchestrator' verdict steers the model to its functions;
    the delegate is not imposed (ADR-0035 §2)."""
    brain = _hybrid_brain()
    wire_supervisor_gateway(brain, _StubExecutor())
    spoken = AudioChunk(pcm=b"\x01\x02" * 8, sample_rate=24_000, timestamp_ns=0)
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Was steht heute in meinem Kalender?",  # i18n-allow: fixture
                is_final=True,
            ),
            RealtimeEvent(
                type="output_transcript_delta",
                text="Your calendar is empty today.",
                is_final=True,
            ),
            RealtimeEvent(type="audio_delta", audio=spoken),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=brain, tool_mode="hybrid")
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()

    # No deterministic delegate was dispatched for the calendar turn ...
    assert brain.calls == []
    assert not sess._delegate_turns
    # ... and the turn-mode line told the model to use its own functions.
    joined = "\n".join(
        str(update["instructions"] or "") for update in provider.session.session_updates
    )
    assert "Call the matching function of your own NOW" in joined
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_hybrid_forces_the_delegate_for_explicit_computer_use(
    wire_supervisor_gateway,
):
    brain = _hybrid_brain()
    wire_supervisor_gateway(brain, _StubExecutor())
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Klick auf den blauen Button im Browser.",  # i18n-allow: fixture
                is_final=True,
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = _session(provider, brain=brain, tool_mode="hybrid")
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.sleep(0.3)

    assert sess._delegate_required_for_turn is True
    assert sess._delegate_cu_dispatches == 1
    assert [call[0] for call in brain.calls] == [
        "Klick auf den blauen Button im Browser."  # i18n-allow: German fixture
    ]
    await sess.end(reason="test")


@pytest.mark.parametrize(
    "utterance",
    [
        "Klick auf den Button.",  # i18n-allow: German speech-input fixture
        "Mach das Fenster zu.",  # i18n-allow: German speech-input fixture
        "Open the browser and log in to GitHub.",
        "Kannst du bitte mit Computer use den letzten Tab wieder öffnen?",  # i18n-allow: fixture
    ],
)
def test_explicit_computer_use_turns(utterance: str) -> None:
    assert is_explicit_computer_use_turn(utterance) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "Öffne Spotify.",  # i18n-allow: German speech-input fixture
        "Starte die Morgenroutine.",  # i18n-allow: German speech-input fixture
        "Spiel mir bitte ein cooles Lied.",  # i18n-allow: German speech-input fixture
        "Was siehst du auf meinem Bildschirm?",  # i18n-allow: German speech-input fixture
        "Hallo, sprich mal mit mir.",  # i18n-allow: German speech-input fixture
        "Wer waren die 10 berühmtesten Wissenschaftler?",  # i18n-allow: German fixture
        "Was geht ab?",  # i18n-allow: German speech-input fixture
    ],
)
def test_non_desktop_turns_are_not_forced_to_the_delegate(utterance: str) -> None:
    assert is_explicit_computer_use_turn(utterance) is False


def _descriptor(name: str, size: int) -> SupervisorToolDescriptor:
    return SupervisorToolDescriptor(
        name=name,
        description="x" * size,
        input_schema={"type": "object", "properties": {}},
        risk_tier="safe",
        is_action_tool=False,
    )


def test_declaration_budget_drops_deterministically_and_keeps_the_rest() -> None:
    rendered = [
        ("agentic-ide-fanout", {"name": "agentic-ide-fanout", "description": "a" * 400}),
        ("agentic-ide-focus", {"name": "agentic-ide-focus", "description": "a" * 100}),
        ("cli_gcloud", {"name": "cli_gcloud", "description": "c" * 300}),
        ("wiki-recall", {"name": "wiki-recall", "description": "w" * 200}),
        ("search_web", {"name": "search_web", "description": "s" * 100}),
    ]
    total = sum(len(str(d)) for _n, d in rendered)
    kept, dropped = _apply_declaration_budget(rendered, total - 1)
    # The lowest-priority family first, the longest member of it first.
    assert dropped == ["agentic-ide-fanout"]
    assert [name for name, _d in kept] == [
        "agentic-ide-focus",
        "cli_gcloud",
        "wiki-recall",
        "search_web",
    ]
    kept, dropped = _apply_declaration_budget(rendered, 50)
    assert dropped[:3] == ["agentic-ide-fanout", "agentic-ide-focus", "cli_gcloud"]
    assert kept == [] or all(name in {"wiki-recall", "search_web"} for name, _d in kept)
    # No budget keeps everything.
    kept, dropped = _apply_declaration_budget(rendered, 0)
    assert dropped == [] and len(kept) == 5


def test_bridge_budget_hides_dropped_tools_and_reports_them() -> None:
    class _Gateway:
        def catalog(self):
            return (
                _descriptor("agentic-ide-fanout", 2_000),
                _descriptor("wiki-recall", 100),
                _descriptor("computer_use", 100),
            )

        async def execute(self, *_a, **_k):
            return ToolResult(success=True, output="ok")

    bridge = RealtimeToolBridge(
        gateway=_Gateway(),
        language="en",
        excluded_tool_names=CU_VEHICLE_TOOL_NAMES,
        compact=True,
        declaration_budget_chars=600,
    )
    names = [d["name"] for d in bridge.declarations]
    assert len(names) == 1 and names[0].startswith("wiki_recall")
    assert bridge.dropped_names == ("agentic-ide-fanout",)
    assert "computer_use" not in names
    assert bridge.declaration_chars <= 600


def test_router_catalog_renders_for_every_realtime_wire() -> None:
    """ADR-0035 §8: every router tool survives the compact rendering, the
    wire-name rule, the Gemini schema sanitizer, and the default budget."""
    from jarvis.brain.factory import _load_tools_for_tier
    from jarvis.core.config import JarvisConfig, VoiceConfig
    from jarvis.core.bus import EventBus
    from jarvis.plugins.realtime.gemini_live import _sanitize_declarations
    from jarvis.realtime.tools import CHARS_PER_TOKEN, _VALID_WIRE_NAME

    tools = _load_tools_for_tier(
        "router",
        bus=EventBus(),
        executor=None,
        harness_manager=None,
        user_profile=None,
        people=None,
        config=JarvisConfig(),
    )
    assert "computer_use" in tools, "the CU vehicle must exist to be excluded"
    budget_chars = VoiceConfig().realtime_tool_declaration_budget_tokens * CHARS_PER_TOKEN
    bridge = RealtimeToolBridge(
        tools=tools,
        executor=None,
        language="en",
        excluded_tool_names=CU_VEHICLE_TOOL_NAMES,
        compact=True,
        declaration_budget_chars=budget_chars,
    )
    declarations = bridge.declarations
    assert declarations, "the router catalog rendered no declarations"
    names = [d["name"] for d in declarations]
    assert "computer_use" not in names
    assert len(names) == len(set(names))
    for declaration in declarations:
        assert _VALID_WIRE_NAME.fullmatch(declaration["name"]), declaration["name"]
        assert isinstance(declaration["parameters"], dict)
        assert len(declaration["description"]) <= COMPACT_DESCRIPTION_CHARS + 1
    assert bridge.declaration_chars <= budget_chars
    sanitized = _sanitize_declarations(tuple(declarations))
    assert len(sanitized) == len(declarations)
    for entry in sanitized:
        params = entry.get("parameters") or {}
        properties = params.get("properties") or {}
        for required in params.get("required") or ():
            assert required in properties, (entry["name"], required)
