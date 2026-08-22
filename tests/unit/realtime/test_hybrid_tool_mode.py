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
    assert "call it NOW, in this response" in joined
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
    rendered.insert(
        0, ("higgsfield/balance", {"name": "higgsfield_balance", "description": "h" * 50})
    )
    total = sum(len(str(d)) for _n, d in rendered)
    kept, dropped = _apply_declaration_budget(rendered, total - 1)
    # The lowest-priority family first (namespaced plugin/MCP tools), then
    # connected CLIs, then the coding workspace — longest member first.
    assert dropped == ["higgsfield/balance"]
    kept, dropped = _apply_declaration_budget(rendered, total - 100)
    assert dropped == ["higgsfield/balance", "cli_gcloud"]
    assert [name for name, _d in kept] == [
        "agentic-ide-fanout",
        "agentic-ide-focus",
        "wiki-recall",
        "search_web",
    ]
    kept, dropped = _apply_declaration_budget(rendered, 50)
    assert dropped[:4] == [
        "higgsfield/balance",
        "cli_gcloud",
        "agentic-ide-fanout",
        "agentic-ide-focus",
    ]
    assert kept == [] or all(name in {"wiki-recall", "search_web"} for name, _d in kept)
    # No budget keeps everything.
    kept, dropped = _apply_declaration_budget(rendered, 0)
    assert dropped == [] and len(kept) == 6


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
    from jarvis.core.bus import EventBus
    from jarvis.core.config import JarvisConfig, VoiceConfig
    from jarvis.plugins.realtime.gemini_live import _sanitize_declarations
    from jarvis.realtime.tools import _VALID_WIRE_NAME, CHARS_PER_TOKEN

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


# --- Execute-side turn-shape guards (live lesson 2026-08-19 12:50) ----------


@pytest.fixture(autouse=True)
def _music_services_are_local(monkeypatch):
    """Execute-time music reroute reads the token store; unit tests must not."""
    monkeypatch.setattr(
        "jarvis.core.music_service.connected_music_services", lambda: ()
    )
    monkeypatch.setattr(
        "jarvis.core.music_service.preferred_music_service", lambda: "auto"
    )


def _descriptor_with_tier(
    name: str, tier: str, *, risk_tier_for_args=None
) -> SupervisorToolDescriptor:
    return SupervisorToolDescriptor(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object", "properties": {}},
        risk_tier=tier,  # type: ignore[arg-type]
        is_action_tool=False,
        risk_tier_for_args=risk_tier_for_args,
    )


def _music_read_tier(args: dict) -> str:
    action = str((args or {}).get("action") or "now_playing").strip() or "now_playing"
    if action in {
        "now_playing",
        "search",
        "list_playlists",
        "playlist_tracks",
        "liked_songs",
        "list_devices",
    }:
        return "safe"
    return "monitor"


class _RecordingGateway:
    def __init__(self, descriptors):
        self._items = tuple(descriptors)
        self.executed: list[str] = []
        self.arguments: list[object] = []

    def catalog(self):
        return self._items

    async def execute(self, name, arguments, _request):
        self.executed.append(name)
        self.arguments.append(arguments)
        return ToolResult(success=True, output="ok")

    async def publish_guard_denied(self, *_a, **_k):
        return None


def _guarded_bridge():
    gateway = _RecordingGateway(
        [
            _descriptor_with_tier("app-restart", "ask"),
            _descriptor_with_tier("higgsfield/balance", "monitor"),
            _descriptor_with_tier("google_calendar", "monitor"),
            _descriptor_with_tier("spotify", "monitor"),
            _descriptor_with_tier("search_web", "safe"),
        ]
    )
    bridge = RealtimeToolBridge(gateway=gateway, language="de", compact=True)
    return gateway, bridge


async def _call(bridge, wire_name, user_text):
    await bridge.handle_user_transcript(user_text)
    _name, result = await bridge.execute(wire_name=wire_name, arguments={})
    return result


@pytest.mark.asyncio
async def test_ask_tier_tool_needs_an_action_order():
    gateway, bridge = _guarded_bridge()
    # The live incident: a remark/question must never start app-restart.
    wire = "app_restart_" + _wire_suffix("app-restart")
    remark = "Was oh mein Gott, wieso lag es so rum?"  # i18n-allow: live utterance
    result = await _call(bridge, wire, remark)
    assert result["success"] is False and "was not run" in result["error"]
    assert gateway.executed == []
    # An order does.
    result = await _call(bridge, wire, "Starte die App neu.")  # i18n-allow: German fixture
    assert result["success"] is True
    assert gateway.executed == ["app-restart"]


@pytest.mark.asyncio
async def test_plugin_tool_needs_its_service_named():
    gateway, bridge = _guarded_bridge()
    wire = "higgsfield_balance_" + _wire_suffix("higgsfield/balance")
    result = await _call(bridge, wire, "Was genau, wie ist mein X-Shield?")  # i18n-allow: live
    assert result["success"] is False and "did not mention the higgsfield" in result["error"]
    assert gateway.executed == []
    result = await _call(bridge, wire, "Wie ist mein Guthaben bei Higgsfield?")  # i18n-allow
    assert result["success"] is True
    assert gateway.executed == ["higgsfield/balance"]


@pytest.mark.asyncio
async def test_monitor_tool_needs_an_order_a_tasking_or_a_world_request():
    gateway, bridge = _guarded_bridge()
    # Smalltalk: no side effect.
    result = await _call(bridge, "spotify", "Was geht ab?")  # i18n-allow: German fixture
    assert result["success"] is False and "was not run" in result["error"]
    # A data request about the user's world is allowed on a monitor tool.
    calendar_question = "Was steht heute in meinem Kalender?"  # i18n-allow: German fixture
    result = await _call(bridge, "google_calendar", calendar_question)
    assert result["success"] is True
    # An order is allowed.
    result = await _call(bridge, "spotify", "Spiel mir was von Ed Sheeran.")  # i18n-allow: fixture
    assert result["success"] is True
    # Safe tools are never refused by the shape guard.
    result = await _call(bridge, "search_web", "Was geht ab?")  # i18n-allow: German fixture
    assert result["success"] is True
    assert gateway.executed == ["google_calendar", "spotify", "search_web"]


@pytest.mark.asyncio
async def test_vertex_toolset_prefix_executes_the_declared_hybrid_tool():
    """Live 2026-08-19 16:07: vertex-live called ``default:run_shell``.

    The prefix is a transport artifact. Hybrid execute must still run the
    declared tool instead of answering "not available in this session."
    """
    gateway, bridge = _guarded_bridge()
    result = await _call(bridge, "default:search_web", "Was geht ab?")  # i18n-allow
    assert result["success"] is True
    assert gateway.executed == ["search_web"]


@pytest.mark.asyncio
async def test_unnamed_music_call_is_rerouted_to_the_preferred_service(monkeypatch):
    """Live 2026-08-19: preference YouTube Music, Spotify not connected, the
    hybrid model still called ``spotify`` for an unnamed play-something-I-like
    request. Execute-time reroute is the correctness boundary."""
    from jarvis.core import music_service as ms

    monkeypatch.setattr(ms, "preferred_music_service", lambda: "youtube_music")
    monkeypatch.setattr(
        ms, "connected_music_services", lambda: ("spotify", "youtube_music")
    )
    gateway = _RecordingGateway(
        [
            _descriptor_with_tier("spotify", "monitor"),
            _descriptor_with_tier("youtube_music", "monitor"),
        ]
    )
    bridge = RealtimeToolBridge(gateway=gateway, language="de", compact=True)
    utterance = "Mach einfach irgendwie Schönes, was mir gefällt."  # i18n-allow
    result = await _call(bridge, "spotify", utterance)
    assert result["success"] is True
    assert gateway.executed == ["youtube_music"]
    assert gateway.arguments and gateway.arguments[0].get("type") == "liked"


@pytest.mark.asyncio
async def test_named_music_service_is_not_rerouted(monkeypatch):
    from jarvis.core import music_service as ms

    monkeypatch.setattr(ms, "preferred_music_service", lambda: "youtube_music")
    monkeypatch.setattr(
        ms, "connected_music_services", lambda: ("spotify", "youtube_music")
    )
    gateway = _RecordingGateway(
        [
            _descriptor_with_tier("spotify", "monitor"),
            _descriptor_with_tier("youtube_music", "monitor"),
        ]
    )
    bridge = RealtimeToolBridge(gateway=gateway, language="de", compact=True)
    named = "spiel das auf Spotify"  # i18n-allow: spoken-input sample
    result = await _call(bridge, "spotify", named)
    assert result["success"] is True
    assert gateway.executed == ["spotify"]


@pytest.mark.asyncio
async def test_disconnected_music_tool_reroutes_to_the_connected_sibling(
    monkeypatch,
):
    from jarvis.core import music_service as ms

    monkeypatch.setattr(ms, "preferred_music_service", lambda: "auto")
    monkeypatch.setattr(ms, "connected_music_services", lambda: ("youtube_music",))
    gateway = _RecordingGateway(
        [
            _descriptor_with_tier("spotify", "monitor"),
            _descriptor_with_tier("youtube_music", "monitor"),
        ]
    )
    bridge = RealtimeToolBridge(gateway=gateway, language="de", compact=True)
    result = await _call(bridge, "spotify", "Spiel mir ein schönes Lied.")  # i18n-allow
    assert result["success"] is True
    assert gateway.executed == ["youtube_music"]


@pytest.mark.asyncio
async def test_now_playing_question_runs_the_music_read():
    """Live 2026-08-19 17:46: 'Welches Lied' while a track was playing.

    youtube_music is statically monitor because play mutates. now_playing
    is safe per args. The shape guard used the static tier and treated the
    question as smalltalk — ActionDenied, canned failure spoken.
    """
    gateway = _RecordingGateway(
        [
            _descriptor_with_tier(
                "youtube_music", "monitor", risk_tier_for_args=_music_read_tier
            ),
            _descriptor_with_tier("spotify", "monitor"),
        ]
    )
    bridge = RealtimeToolBridge(gateway=gateway, language="de", compact=True)
    result = await _call(bridge, "youtube_music", "Welches Lied")  # i18n-allow
    assert result["success"] is True
    assert gateway.executed == ["youtube_music"]

    result = await _call(bridge, "youtube_music", "What song is this")
    assert result["success"] is True

    result = await _call(
        bridge, "youtube_music", "Que cancion es esta?"  # i18n-allow
    )
    assert result["success"] is True

    result = await _call(bridge, "spotify", "Was geht ab?")  # i18n-allow
    assert result["success"] is False and "was not run" in result["error"]
    assert gateway.executed == ["youtube_music", "youtube_music", "youtube_music"]


@pytest.mark.asyncio
async def test_now_playing_question_does_not_start_playback():
    gateway = _RecordingGateway(
        [
            _descriptor_with_tier(
                "youtube_music", "monitor", risk_tier_for_args=_music_read_tier
            ),
        ]
    )
    bridge = RealtimeToolBridge(gateway=gateway, language="de", compact=True)
    await bridge.handle_user_transcript("Welches Lied")  # i18n-allow
    _name, result = await bridge.execute(
        wire_name="youtube_music", arguments={"action": "play"}
    )
    assert result["success"] is False
    assert "was not run" in result["error"]
    assert gateway.executed == []


def _wire_suffix(name: str) -> str:
    import hashlib

    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]


# --- A read is not an action (live forensic 2026-08-20 15:35) ---------------


def _shell_descriptor() -> SupervisorToolDescriptor:
    """The REAL ``run_shell`` hooks, so this guard cannot drift from the tool."""
    from jarvis.plugins.tool.run_shell import RunShellTool

    tool = RunShellTool()
    return SupervisorToolDescriptor(
        name="run_shell",
        description="Run a shell command.",
        input_schema=tool.schema,
        risk_tier="monitor",
        risk_tier_for_args=tool.risk_tier_for_args,
        describe_args=tool.describe_args,
    )


async def _shell_call(bridge, user_text: str, command: str):
    await bridge.handle_user_transcript(user_text)
    _name, result = await bridge.execute(
        wire_name="run_shell", arguments={"command": command}
    )
    return result


def _shell_bridge():
    gateway = _RecordingGateway([_shell_descriptor()])
    return gateway, RealtimeToolBridge(gateway=gateway, language="de", compact=True)


@pytest.mark.asyncio
async def test_a_question_about_the_machine_may_read_the_machine():
    """The 15:35 transcript verbatim.

    "ob mein PC gerade überhitzt, überlastet ist" is a question, so the shape
    guard refused ``run_shell`` — judging the call by the worst command that
    tool could ever carry. The user asked twice and heard "Aktionen gehen
    gerade nicht" both times. A READ answers the question; it starts nothing.
    """
    gateway, bridge = _shell_bridge()
    asked = (
        "Nein, ich möchte fragen, ob mein PC gerade irgendwie "  # i18n-allow: live utterance
        "komplett überhitzt, überlastet ist."  # i18n-allow: live utterance
    )
    result = await _shell_call(bridge, asked, "systeminfo")
    assert result["success"] is True

    again = "Wie sieht es mit meinem PC aus? Lagt er gerade rum?"  # i18n-allow: live utterance
    result = await _shell_call(
        bridge, again, "Get-Process | Sort-Object CPU -Descending"
    )
    assert result["success"] is True
    assert gateway.executed == ["run_shell", "run_shell"]


@pytest.mark.asyncio
async def test_a_question_still_starts_nothing_that_changes_the_machine():
    """The read exemption is per CALL, not per tool: the same question must
    not carry a command that deletes, moves or pushes anything."""
    gateway, bridge = _shell_bridge()
    question = "Ist der Rechner überlastet?"  # i18n-allow: German speech-input fixture

    result = await _shell_call(bridge, question, "Remove-Item C:/temp -Recurse")
    assert result["success"] is False and "was not run" in result["error"]

    result = await _shell_call(bridge, question, "git push")
    assert result["success"] is False and "was not run" in result["error"]
    assert gateway.executed == []


@pytest.mark.asyncio
async def test_smalltalk_still_reads_nothing():
    """A read needs a reason in the user's words — the imperative is what it
    no longer needs. "Was geht ab?" asks for neither."""
    gateway, bridge = _shell_bridge()
    result = await _shell_call(bridge, "Was geht ab?", "systeminfo")  # i18n-allow
    assert result["success"] is False and "was not run" in result["error"]
    assert gateway.executed == []


@pytest.mark.asyncio
async def test_an_explicit_order_still_runs_a_destructive_command():
    """The order path is untouched — the executor's confirmation owns it."""
    gateway, bridge = _shell_bridge()
    order = "Lösch bitte den Ordner Urlaub."  # i18n-allow: German speech-input fixture
    result = await _shell_call(bridge, order, "Remove-Item C:/Urlaub -Recurse")
    assert result["success"] is True
    assert gateway.executed == ["run_shell"]


# ---------------------------------------------------------------------------
# Budget: connected music connectors are the last to go; the set re-fits
# ---------------------------------------------------------------------------


def test_connected_music_connectors_are_the_last_to_go(monkeypatch):
    """Live 2026-08-22 18:16: ``spotify`` and ``youtube_music`` were both
    dropped under the 8k-token wire budget as part of the "everything else,
    biggest first" bucket, and "mach Musik an" had no native function to land
    on. A connector the user connected on purpose outranks every family."""
    from jarvis.core import music_service as ms

    monkeypatch.setattr(ms, "connected_music_services", lambda: ("youtube_music",))
    rendered = [
        ("youtube_music", {"name": "youtube_music", "description": "y" * 900}),
        ("spotify", {"name": "spotify", "description": "s" * 900}),
        ("wiki-recall", {"name": "wiki-recall", "description": "w" * 100}),
        ("search_web", {"name": "search_web", "description": "q" * 100}),
    ]
    # Room for roughly one big declaration and the two small ones.
    kept, dropped = _apply_declaration_budget(
        rendered, 1_250, keep_last=("youtube_music",)
    )
    assert dropped == ["spotify"]
    assert [name for name, _d in kept] == ["youtube_music", "wiki-recall", "search_web"]
    # Tighter still: the small unconnected tools go before the connected one.
    kept, dropped = _apply_declaration_budget(
        rendered, 1_000, keep_last=("youtube_music",)
    )
    assert "youtube_music" in [name for name, _d in kept]
    assert "youtube_music" not in dropped
    # The bridge reads the connected set itself.
    gateway_tools = (
        _descriptor("youtube_music", 900),
        _descriptor("spotify", 900),
        _descriptor("wiki-recall", 100),
    )

    class _Gateway:
        def catalog(self):
            return gateway_tools

        async def execute(self, *_a, **_k):
            return ToolResult(success=True, output="ok")

    # Uncompacted on purpose: the compact renderer caps every description at
    # 450 characters, which would put the whole set under this budget.
    bridge = RealtimeToolBridge(
        gateway=_Gateway(), language="en", compact=False, declaration_budget_chars=1_300
    )
    assert "spotify" in bridge.dropped_names
    assert "youtube_music" not in bridge.dropped_names


def test_bridge_refits_the_declared_set_to_a_new_budget():
    """The session builds the bridge before it knows the provider; the budget
    of the provider about to open is applied at connect (ADR-0035 §4)."""

    class _Gateway:
        def catalog(self):
            return (
                _descriptor("agentic-ide-fanout", 2_000),
                _descriptor("wiki-recall", 100),
            )

        async def execute(self, *_a, **_k):
            return ToolResult(success=True, output="ok")

    bridge = RealtimeToolBridge(
        gateway=_Gateway(), language="en", compact=True, declaration_budget_chars=600
    )
    assert bridge.dropped_names == ("agentic-ide-fanout",)
    assert bridge.declaration_budget_chars == 600
    # Same budget → nothing to do.
    assert bridge.set_declaration_budget(600) is False
    # A wider budget lets the coding workspace back in.
    assert bridge.set_declaration_budget(0) is True
    assert bridge.dropped_names == ()
    assert any(d["name"].startswith("wiki_recall") for d in bridge.declarations)
    assert any(d["name"].startswith("agentic_ide_fanout") for d in bridge.declarations)
    # And the tighter one takes it out again.
    assert bridge.set_declaration_budget(600) is True
    assert bridge.dropped_names == ("agentic-ide-fanout",)
