"""Phase-3-Routing-Tests (Persona-Delegation-Mandat).

Verifiziert die deterministische Force-Spawn-Heuristik im BrainManager:

- 5 Smalltalk-Inputs duerfen NIE einen Sub-Jarvis-Spawn ausloesen.
- 5 Spawn-Inputs MUESSEN genau einen Spawn-Call mit User-Utterance verbatim
  ausloesen (kein Paraphrasieren, kein Summarize).

Tests laufen rein deterministisch — kein LLM, kein echtes Brain. Sie testen
``BrainManager._should_force_spawn`` (Pattern-Klassifikation) und
``BrainManager._force_spawn_worker`` (Tool-Dispatch via FakeExecutor).

Vor dem Phase-3-Fix sind die Spawn-Cases ROT (Heuristik faengt sie nicht).
Nach dem Fix sollen alle 10 Cases gruen sein.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import ResponseGenerated
from jarvis.core.protocols import ToolResult


class _FakeTool:
    name = "spawn_worker"
    schema: dict[str, Any] = {}


class _FakeDispatchTool:
    name = "dispatch_to_harness"
    schema: dict[str, Any] = {}


class _FakeOpenAppTool:
    name = "open_app"
    schema: dict[str, Any] = {}


class _FakeTypeTextTool:
    name = "type_text"
    schema: dict[str, Any] = {}


class _FakeHotkeyTool:
    name = "hotkey"
    schema: dict[str, Any] = {}


class _VisionShouldNotRun:
    async def current(self) -> Any:
        raise AssertionError("local-action fast path must not collect vision")


class _RecordingExecutor:
    """Faengt jeden execute()-Call ein — Tests pruefen calls + utterance verbatim."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any], str]] = []

    async def execute(
        self,
        tool: Any,
        args: dict[str, Any],
        *,
        user_utterance: str = "",
        trace_id: Any = None,
        **_: Any,
    ) -> ToolResult:
        self.calls.append((tool, args, user_utterance))
        return ToolResult(success=True, output="ok")


class _CooldownCostMeter:
    def __init__(self) -> None:
        self.task_checks: list[Any] = []

    def is_in_cooldown(self) -> bool:
        return True

    def over_task_budget(self, trace_id: Any) -> bool:
        self.task_checks.append(trace_id)
        return False

    def over_daily_budget(self) -> bool:
        return False


def _manager_with_spawn(
    *,
    force_spawn_mode: str = "permissive",
) -> tuple[BrainManager, _RecordingExecutor]:
    """Build a BrainManager wired to the recording executor for spawn tests.

    SPAWN_INPUTS predate the strict-mode mandate (User-Mandate 2026-05-14)
    and rely on the legacy verb/marker heuristic. Default this fixture to
    ``permissive`` so heuristic-coverage tests keep working; strict-mode
    behaviour is exercised by its own targeted tests (e.g.
    ``test_router_tools_is_pure_dispatcher_set``).
    """
    executor = _RecordingExecutor()
    config = JarvisConfig()
    config.brain.routing.force_spawn_mode = force_spawn_mode
    manager = BrainManager(
        config=config,
        bus=EventBus(),
        tools={"spawn_worker": _FakeTool()},
        tool_executor=executor,  # type: ignore[arg-type]
    )
    return manager, executor


def _manager_with_spawn_and_computer_use() -> tuple[BrainManager, _RecordingExecutor]:
    executor = _RecordingExecutor()
    manager = BrainManager(
        config=JarvisConfig(),
        bus=EventBus(),
        tools={
            "spawn_worker": _FakeTool(),
            "dispatch_to_harness": _FakeDispatchTool(),
        },
        tool_executor=executor,  # type: ignore[arg-type]
    )
    return manager, executor


def _manager_with_local_actions(
    *,
    force_spawn_mode: str = "permissive",
) -> tuple[BrainManager, _RecordingExecutor]:
    """Mirror of ``_manager_with_spawn`` with local-action tools wired.

    Defaults to ``permissive`` so heavy-build inputs like
    ``"Bau eine Landingpage"`` exercise the legacy verb heuristic.
    """
    executor = _RecordingExecutor()
    config = JarvisConfig()
    config.brain.routing.force_spawn_mode = force_spawn_mode
    manager = BrainManager(
        config=config,
        bus=EventBus(),
        tools={"spawn_worker": _FakeTool()},
        local_action_tools={
            "open_app": _FakeOpenAppTool(),
            "type_text": _FakeTypeTextTool(),
            "hotkey": _FakeHotkeyTool(),
            "dispatch_to_harness": _FakeDispatchTool(),
        },
        tool_executor=executor,  # type: ignore[arg-type]
    )
    manager._vision_provider = _VisionShouldNotRun()
    return manager, executor


def _manager_with_local_actions_and_bus(
    bus: EventBus,
) -> tuple[BrainManager, _RecordingExecutor]:
    executor = _RecordingExecutor()
    manager = BrainManager(
        config=JarvisConfig(),
        bus=bus,
        tools={"spawn_worker": _FakeTool()},
        local_action_tools={
            "open_app": _FakeOpenAppTool(),
            "type_text": _FakeTypeTextTool(),
            "hotkey": _FakeHotkeyTool(),
            "dispatch_to_harness": _FakeDispatchTool(),
        },
        tool_executor=executor,  # type: ignore[arg-type]
    )
    manager._vision_provider = _VisionShouldNotRun()
    return manager, executor


# ---------------------------------------------------------------------------
# 5 Smalltalk-Inputs — duerfen 0 Spawn-Calls ausloesen.
# ---------------------------------------------------------------------------

SMALLTALK_INPUTS = [
    "Hallo",
    "Wie geht's?",
    "Was ist die Hauptstadt von Frankreich?",
    "Danke",
    "Auf Wiedersehen",
]


@pytest.mark.parametrize("utterance", SMALLTALK_INPUTS)
def test_smalltalk_does_not_force_spawn(utterance: str) -> None:
    """Smalltalk-Inputs duerfen die Force-Spawn-Heuristik NICHT triggern."""
    manager, _executor = _manager_with_spawn()
    assert not manager._should_force_spawn(utterance), (
        f"Smalltalk {utterance!r} hat Force-Spawn-Heuristik getriggert"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", SMALLTALK_INPUTS)
async def test_smalltalk_dispatches_zero_spawn_calls(utterance: str) -> None:
    """Smalltalk darf den Spawn-Executor NIE aufrufen."""
    manager, executor = _manager_with_spawn()
    result = await manager._force_spawn_worker(utterance)
    assert result is None, f"Smalltalk {utterance!r} lieferte Spawn-Result {result!r}"
    assert len(executor.calls) == 0, (
        f"Smalltalk {utterance!r} hat {len(executor.calls)} Spawn-Calls ausgeloest (erwartet 0)"
    )


# ---------------------------------------------------------------------------
# Greeting-prefixed command bug (live forensic 2026-06-07,
# data/jarvis_desktop.log 18:19:07): the user said "Hallo, öffne ihn für mich".
# The smalltalk allowlist substring-matched the leading "Hallo", so the whole
# turn was classified as smalltalk. That hid the action/dispatch tools from the
# LLM, which then hit the anti-silence fallback and spoke
# "Das kann ich gerade nicht ausführen — mir fehlt dafür das passende Werkzeug."
# A greeting/politeness prefix must NOT turn a real command into smalltalk.
# ---------------------------------------------------------------------------

GREETING_PREFIXED_COMMANDS = [
    "Hallo, öffne ihn für mich",
    "Hallo, öffne ihn für mich.",
    "Hey Jarvis, baue eine Landingpage",
    "Hi, lies die Datei jarvis.toml",
    "Moin, mach einen Screenshot",
    "Danke, öffne mir Chrome",
    "Okay, zeig mir die Logs",
]


@pytest.mark.parametrize("utterance", GREETING_PREFIXED_COMMANDS)
def test_greeting_prefixed_command_is_not_smalltalk(utterance: str) -> None:
    """A greeting/politeness prefix in front of a real action command must NOT
    classify the turn as smalltalk — otherwise the action tools are hidden and
    the brain speaks the 'Das kann ich gerade nicht ausführen' refusal."""
    manager, _executor = _manager_with_spawn()
    assert manager._is_smalltalk(utterance) is False, (
        f"greeting-prefixed command {utterance!r} wrongly classified as smalltalk"
    )


PURE_SMALLTALK_INCL_GREETING = [
    "Hallo",
    "Hallo, wie geht's?",
    "Hey, was machst du?",
    "Moin, wie geht es dir?",
    "Danke",
    "Guten Morgen",
    "Hallo, was ist die Hauptstadt von Frankreich?",
]


@pytest.mark.parametrize("utterance", PURE_SMALLTALK_INCL_GREETING)
def test_pure_smalltalk_still_smalltalk(utterance: str) -> None:
    """Pure smalltalk — including a greeting followed by more smalltalk — must
    STILL be classified as smalltalk so the anti-fake-spawn guard is intact."""
    manager, _executor = _manager_with_spawn()
    assert manager._is_smalltalk(utterance) is True, (
        f"pure smalltalk {utterance!r} wrongly classified as a command"
    )


@pytest.mark.parametrize(
    "utterance",
    [
        # NOTE: not an "open app" command — those route to computer-use, never a
        # force-spawn (see test_open_app_intent_does_not_force_spawn_*). These
        # are genuine heavy-worker commands that must survive the greeting prefix.
        "Hallo, schreib mir einen Bericht über die Logs",
        "Hey Jarvis, baue eine Landingpage",
        "Moin, lies die Datei jarvis.toml",
    ],
)
def test_greeting_prefixed_command_force_spawns_permissive(utterance: str) -> None:
    """In permissive mode a greeting-prefixed action verb must reach the
    force-spawn heuristic instead of being silenced as smalltalk."""
    manager, _executor = _manager_with_spawn(force_spawn_mode="permissive")
    assert manager._should_force_spawn(utterance) is True, (
        f"greeting-prefixed command {utterance!r} did not force-spawn"
    )


# ---------------------------------------------------------------------------
# AI Pointer: a deictic "what is this?" is a Q&A, never a heavy-worker spawn —
# even in permissive mode where the verb "zeige" would otherwise match.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        "was ist das da?",
        "was ist das hier?",
        "worauf zeige ich gerade?",
        "wo ich hinzeige, was ist das?",
    ],
)
def test_pointing_intent_never_force_spawns(utterance: str) -> None:
    """A deictic pointer question must answer inline, never trigger a spawn —
    even in permissive mode (where 'zeige' is an action verb)."""
    manager, _executor = _manager_with_spawn(force_spawn_mode="permissive")
    assert manager._should_force_spawn(utterance) is False, (
        f"pointing question {utterance!r} wrongly force-spawned a worker"
    )


# ---------------------------------------------------------------------------
# 5 Spawn-Inputs — muessen genau 1 Spawn-Call mit Utterance verbatim ausloesen.
# ---------------------------------------------------------------------------

SPAWN_INPUTS = [
    "Lies die Datei jarvis.toml",
    "Installier Notepad++",
    "Bau eine Landingpage",
    "Wie viele PRs sind in jarvis-repo offen?",
    "Mach einen Screenshot und sag mir was du siehst",
    # Regression — Voice-Session 2026-05-11 17:22 (Bug-Report von Alex):
    # "Kannst du bitte einen Subagenten spawnen, welcher..." fiel ohne
    # Match auf den LLM-Pfad zurueck (40s Gemini-Stream-Timeout). "spawn"
    # als Verb-Trigger plus "Subagent"-Marker fangen beide unabhaengig.
    "Kannst du bitte einen Subagenten spawnen, welcher Recherche macht?",
    "Spawn mir einen Subagenten fuer die Recherche",
    "Starte einen Subagenten der die Logs analysiert",
]


@pytest.mark.parametrize("utterance", SPAWN_INPUTS)
def test_spawn_inputs_force_spawn(utterance: str) -> None:
    """Spawn-Inputs MUESSEN die Force-Spawn-Heuristik triggern."""
    manager, _executor = _manager_with_spawn()
    assert manager._should_force_spawn(utterance), (
        f"Spawn-Input {utterance!r} hat Force-Spawn-Heuristik NICHT getriggert"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", SPAWN_INPUTS)
async def test_spawn_inputs_dispatch_one_call_verbatim(utterance: str) -> None:
    """Jeder Spawn-Input muss genau 1 Spawn-Call mit Utterance VERBATIM ausloesen."""
    manager, executor = _manager_with_spawn()
    result = await manager._force_spawn_worker(utterance)
    assert result is not None, f"Spawn-Input {utterance!r} lieferte kein Spawn-Result"
    assert len(executor.calls) == 1, (
        f"Spawn-Input {utterance!r} hat {len(executor.calls)} Spawn-Calls ausgeloest (erwartet 1)"
    )
    _tool, args, captured_utterance = executor.calls[0]
    # Utterance verbatim — kein Paraphrasieren, kein Summarize.
    assert args["utterance"] == utterance, (
        f"Spawn-Args utterance {args['utterance']!r} != input {utterance!r}"
    )
    assert captured_utterance == utterance, (
        f"Executor user_utterance {captured_utterance!r} != input {utterance!r}"
    )


# ---------------------------------------------------------------------------
# Konsistenz-Asserts: Recursion-Schutz und exaktes Pure-Dispatcher-Set.
#
# Mandat-Wortlaut "kein Tool versehentlich in beiden Listen" ist eine
# Schutzmassnahme gegen Duplikate, KEIN hartes Disjunkt-Soll: Tools wie
# run-shell oder screen-snapshot sind fuer beide Tiers sinnvoll
# (Router-Direct-Action + Sub-Worker). Die wirklich harte Regel ist:
# Welle-4-Migration: spawn-worker ist nur in ROUTER_TOOLS verdrahtet —
# Recursion-Schutz greift jetzt auf Mission-Manager-Ebene (Worker-Sandbox
# hat keinen spawn-worker-Tool-Zugriff).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Live-TOML-Drift-Guard — die echte jarvis.toml ueberschreibt die Code-Defaults
# der Force-Spawn-Listen. Wenn die TOML-Liste hinter den Code-Defaults zurueck-
# faellt (Drift), bricht die Heuristik fuer Live-Saetze ein. Bug-Report
# Voice-Session 2026-05-11 17:22: TOML-Liste hatte kein "spawn"/"subagent" →
# 45-58s Stream-Timeout statt < 2s Spawn-Bestaetigung.
# ---------------------------------------------------------------------------


def _live_manager_with_toml() -> tuple[BrainManager, _RecordingExecutor]:
    """BrainManager, gebaut aus der echten Production-jarvis.toml.

    Bestehende Tests nutzen ``JarvisConfig()`` (Code-Defaults). Damit ist der
    TOML-vs-Code-Drift unsichtbar. Dieser Helper laedt die echte Config-Datei
    und macht den Drift sichtbar.
    """
    from pathlib import Path

    from jarvis.core.config import load_config

    repo_root = Path(__file__).resolve().parents[3]
    cfg_path = repo_root / "jarvis.toml"
    cfg = load_config(cfg_path)
    # Pin a worker-viable provider so the force-spawn viability guard
    # (_heavy_worker_provider_viable) never pre-empts the regex matcher this test
    # exercises. Since 2026-06-07 that guard follows the WORKER
    # ([brain.sub_jarvis].provider), not brain.primary — the live jarvis.toml
    # configures a claude-api worker, so this pin is belt-and-suspenders for runs
    # against a toml without a [brain.sub_jarvis] block.
    cfg.brain.primary = "gemini"

    executor = _RecordingExecutor()
    manager = BrainManager(
        config=cfg,
        bus=EventBus(),
        tools={"spawn_worker": _FakeTool()},
        tool_executor=executor,  # type: ignore[arg-type]
    )
    return manager, executor


@pytest.mark.parametrize(
    "utterance",
    [
        "Kannst du bitte einen Subagenten spawnen, welcher Recherche macht?",
        "Spawn mir einen Subagenten",
        "Starte einen Subagenten der die Logs analysiert",
        "Delegier das an einen Subagenten",
    ],
)
def test_live_toml_force_spawn_for_subagent_phrases(utterance: str) -> None:
    """Production-jarvis.toml MUSS Spawn-Imperative + Subagent-Marker matchen.

    Regression fuer Voice-Session 2026-05-11 17:22:09. Vorher fehlten
    "spawn"/"starte"/"delegier" als Verben und "subagent" als Marker in
    der TOML-Override-Liste — Code-Defaults haetten gematcht, aber die
    TOML ersetzte sie vollstaendig (kein Merge).
    """
    manager, _executor = _live_manager_with_toml()
    assert manager._should_force_spawn(utterance), (
        f"Live-TOML-Heuristik triggert NICHT auf {utterance!r}. "
        f"jarvis.toml [brain.routing] hat Drift gegenueber Code-Defaults."
    )


# ---------------------------------------------------------------------------
# Provider-coupling time bomb (manager.py BUG-017 workaround 2026-05-13): the
# force-spawn guard returned False whenever brain.primary was not in
# {claude-api, gemini}. But the heavy worker is selected from
# [brain.sub_jarvis].provider and runs INDEPENDENTLY of brain.primary
# (jarvis/missions/init.py::_select_subagent_worker_kind). Coupling force-spawn
# to the TALKER provider silenced every action request the moment the user
# switched brain.primary to grok / openai / codex — re-introducing the
# "Das kann ich nicht ausführen" refusal via the LLM fallback path.
# ---------------------------------------------------------------------------


def _manager_with_worker_provider(
    *,
    brain_primary: str,
    worker_provider: str | None,
    force_spawn_mode: str = "permissive",
) -> tuple[BrainManager, _RecordingExecutor]:
    """Manager with the talker (brain.primary) and the heavy-worker
    ([brain.sub_jarvis].provider) providers set independently."""
    from jarvis.core.config import BrainTierConfig

    executor = _RecordingExecutor()
    config = JarvisConfig()
    config.brain.primary = brain_primary
    config.brain.routing.force_spawn_mode = force_spawn_mode
    config.brain.sub_jarvis = (
        BrainTierConfig(provider=worker_provider)
        if worker_provider is not None
        else None
    )
    manager = BrainManager(
        config=config,
        bus=EventBus(),
        tools={"spawn_worker": _FakeTool()},
        tool_executor=executor,  # type: ignore[arg-type]
    )
    return manager, executor


@pytest.mark.parametrize(
    "brain_primary", ["grok", "openai", "openai-codex", "openrouter", "ollama"]
)
def test_force_spawn_follows_worker_provider_not_talker(brain_primary: str) -> None:
    """With a configured claude-api worker, switching the talker to
    grok/openai/codex must NOT block force-spawn — viability follows the WORKER,
    not the talker."""
    manager, _executor = _manager_with_worker_provider(
        brain_primary=brain_primary, worker_provider="claude-api",
    )
    assert manager._should_force_spawn("Bau eine Landingpage") is True, (
        f"talker={brain_primary!r} wrongly blocked force-spawn despite a viable "
        f"claude-api worker"
    )


def test_force_spawn_codex_worker_with_codex_talker() -> None:
    """A Codex talker + Codex worker must still force-spawn (the user's
    Codex-as-brain switch must not disable delegation)."""
    manager, _executor = _manager_with_worker_provider(
        brain_primary="openai-codex", worker_provider="openai-codex",
    )
    assert manager._should_force_spawn("Bau eine Landingpage") is True


@pytest.mark.parametrize("brain_primary", ["grok", "openai", "ollama"])
def test_force_spawn_blocked_when_no_worker_and_nonviable_talker(
    brain_primary: str,
) -> None:
    """Regression guard for the legacy no-worker-configured path: with NO
    [brain.sub_jarvis].provider set, the factory may fall back to the Gemini API
    worker, so the conservative talker check still blocks a non-viable talker."""
    manager, _executor = _manager_with_worker_provider(
        brain_primary=brain_primary, worker_provider=None,
    )
    assert manager._should_force_spawn("Bau eine Landingpage") is False


# ---------------------------------------------------------------------------
# Open-app intent must route to computer-use, NEVER a sub-agent force-spawn
# (live bug 2026-06-08, data/jarvis_desktop.log 17:37): the conversational
# "Ich möchte, dass du mir Hamis Agent öffnest, also …" force-spawned a worker
# because the registry's resolve_intent matches verbs strictly (\boeffne\b — only
# the base form) while has_action_intent matches conjugations (\boeffne\w*\b),
# so "öffnest" registered as "action with no capability" -> generic sub-agent
# work. A worker runs in an isolated git worktree and has no desktop, so opening
# an app is ALWAYS computer-use.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        "Ich möchte, dass du mir Hermes Agent öffnest, also",
        "öffne für mich Hermes Agent",
        "kannst du mir den Steam Client aufmachen",
    ],
)
def test_open_app_intent_does_not_force_spawn_with_seeded_registry(
    utterance: str,
) -> None:
    """Defense-in-depth: even with a seeded registry (where a conjugated open
    verb resolves no capability), an open-app command must NOT force-spawn."""
    from jarvis.core.capabilities import get_registry
    from jarvis.core.capabilities_seed import seed_registry

    reg = get_registry()
    snapshot = dict(reg._caps)  # noqa: SLF001 — test fixture state restore
    seed_registry(reg)
    try:
        manager, _executor = _manager_with_spawn()  # strict mode (production)
        assert manager._should_force_spawn(utterance) is False, (
            f"open-app command {utterance!r} wrongly force-spawned a worker"
        )
    finally:
        reg._caps.clear()  # noqa: SLF001
        reg._caps.update(snapshot)  # noqa: SLF001


def test_cli_tools_in_router_tools() -> None:
    """``cli-tools`` (the virtual CLI loader) must live in ROUTER_TOOLS.

    CLI-Integration (2026-05-24): the production router brain reaches the
    CLI subsystem only when ``cli-tools`` is in this frozenset — the loader
    filters entry-points against ROUTER_TOOLS. Without this line the brain
    never sees any ``cli_<name>`` tool (the original root cause).
    """
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "cli-tools" in ROUTER_TOOLS


def test_computer_use_in_router_tools() -> None:
    """``computer-use`` must live in ROUTER_TOOLS (Wave 1, 2026-05-29).

    The router reaches the live desktop ONLY when this entry-point name is in
    the frozenset — the loader filters entry-points against ROUTER_TOOLS.
    Without it the brain has no honest desktop path: spawn-openclaw runs in an
    isolated worktree (cannot touch the desktop) and the dispatch-to-harness
    indirection was never described as desktop control, so the model refused or
    invented a tool for "öffne ein Terminal".
    """
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "computer-use" in ROUTER_TOOLS


def test_gmail_and_vercel_native_tools_in_router_tools() -> None:
    """The two REST-backed marketplace plugins (gmail, vercel) must be
    router-visible directly — neither has a usable MCP server, so without this
    a connected Gmail/Vercel is not callable by voice/chat (the original bug
    this whole pairing effort fixed). Read-only/ask-gated, never a spawn."""
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "gmail" in ROUTER_TOOLS
    assert "vercel" in ROUTER_TOOLS


def test_navigate_in_router_tools() -> None:
    """``navigate`` must live in ROUTER_TOOLS (2026-06-02).

    The router can move the desktop UI to a sidebar section (e.g. "zeig die
    Socials", "open settings") only when this entry-point name is in the
    frozenset — the loader filters entry-points against ROUTER_TOOLS. It is a
    pure UI action (risk ``safe``) that publishes ``NavigateSidebar``; a direct
    safe-gated action, never a spawn, so it never enters a worker tool-set
    (AP-5/AP-14). See ADR-0011 amendment "Navigate tool".
    """
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "navigate" in ROUTER_TOOLS


def test_plugin_tools_in_router_set():
    from jarvis.brain.factory import ROUTER_TOOLS
    assert "plugin-tools" in ROUTER_TOOLS


def test_plugin_tools_is_router_only_not_a_spawn():
    """plugin-tools is a direct safe-gated loader; it must never become a
    spawn-style tool in a worker set (AP-5/AP-14, D9 recursion guard)."""
    import jarvis.brain.factory as factory_mod
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "plugin-tools" in ROUTER_TOOLS
    # Welle 4 deleted the Sub-Jarvis tier; no worker tool-set may resurrect.
    assert not hasattr(factory_mod, "SUB_TOOLS")


def test_router_prompt_mentions_plugin_inline_reads():
    from jarvis.brain.router import SYSTEM_PROMPT

    low = SYSTEM_PROMPT.lower()
    assert "plugin" in low
    assert "spawn_worker" in low or "spawn-worker" in low


def test_factory_wires_computer_use_tool_into_router_set() -> None:
    """End-to-end wiring: entry-point + ROUTER_TOOLS + factory branch connect.

    A built router tier must actually expose a callable ``computer_use`` tool —
    this catches a missing entry-point registration or a missing construction
    branch in ``_load_tools_for_tier`` (either would silently drop the tool from
    the router schema and re-introduce the refusal bug).
    """
    from jarvis.brain.factory import _load_tools_for_tier

    tools = _load_tools_for_tier(
        "router",
        bus=EventBus(),
        executor=None,
        harness_manager=None,
        user_profile=None,
        people=None,
        config=JarvisConfig(),
    )

    assert "computer_use" in tools
    assert tools["computer_use"].name == "computer_use"


def test_inspect_pointer_in_router_tools() -> None:
    """``inspect-pointer`` (AI Pointer pull path) must live in ROUTER_TOOLS."""
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "inspect-pointer" in ROUTER_TOOLS


def test_factory_wires_inspect_pointer_into_router_set() -> None:
    """End-to-end wiring: entry-point + ROUTER_TOOLS + default construction.

    A missing entry-point or a stray construction branch would silently drop the
    AI-Pointer tool from the router schema.
    """
    from jarvis.brain.factory import _load_tools_for_tier

    tools = _load_tools_for_tier(
        "router",
        bus=EventBus(),
        executor=None,
        harness_manager=None,
        user_profile=None,
        people=None,
        config=JarvisConfig(),
    )

    assert "inspect-pointer" in tools
    assert tools["inspect-pointer"].name == "inspect-pointer"
    assert tools["inspect-pointer"].risk_tier == "safe"


def test_factory_wires_navigate_into_router_set() -> None:
    """End-to-end wiring for the navigate tool: entry-point + ROUTER_TOOLS +
    bus construction. A missing entry-point or construction branch would
    silently drop UI navigation from the router schema."""
    from jarvis.brain.factory import _load_tools_for_tier

    tools = _load_tools_for_tier(
        "router",
        bus=EventBus(),
        executor=None,
        harness_manager=None,
        user_profile=None,
        people=None,
        config=JarvisConfig(),
    )

    assert "navigate" in tools
    assert tools["navigate"].name == "navigate"
    assert tools["navigate"].risk_tier == "safe"


def test_inspect_pointer_is_not_a_spawn_in_local_action_set() -> None:
    """The AI-Pointer tool is a router-tier read, never in the worker fast-path."""
    from jarvis.brain.factory import _load_local_action_tools

    local_tools = _load_local_action_tools(
        bus=EventBus(),
        harness_manager=None,
        config=JarvisConfig(),
    )
    assert "inspect-pointer" not in local_tools
    assert "inspect_pointer" not in local_tools


def test_computer_use_tool_is_not_a_spawn_in_local_action_set() -> None:
    """The computer-use tool is a direct action, never in the worker fast-path.

    It must not appear in ``_load_local_action_tools`` — that set is the
    deterministic, LLM-invisible path. computer-use is router-tier only; placing
    a router dispatch tool in the worker set risks D9 recursion (AP-5/AP-14).
    """
    from jarvis.brain.factory import _load_local_action_tools

    local_tools = _load_local_action_tools(
        bus=EventBus(),
        harness_manager=None,
        config=JarvisConfig(),
    )
    assert "computer_use" not in local_tools
    assert "computer-use" not in local_tools


def test_router_tools_stays_frozenset() -> None:
    """ROUTER_TOOLS must remain a frozenset (latency/immutability contract)."""
    from jarvis.brain.factory import ROUTER_TOOLS

    assert isinstance(ROUTER_TOOLS, frozenset)


def test_no_spawn_tool_leaked_into_worker_set() -> None:
    """No spawn/dispatch/run-skill tool may appear in a worker tool-set.

    The Sub-Jarvis tier (and ``SUB_TOOLS``) was deleted in Welle 4. The only
    other tool-set the brain loads is the deterministic local-action fast-path
    (``_load_local_action_tools``). A spawn-tool there would let a worker /
    fast-path re-enter the supervisor (D9 recursion, AP-5/AP-14). The CLI
    loader (``cli-tools``) is a router-only virtual loader and must likewise
    never appear there.
    """
    from jarvis.brain.factory import _load_local_action_tools

    cfg = JarvisConfig()
    local_tools = _load_local_action_tools(
        bus=EventBus(),
        harness_manager=None,
        config=cfg,
    )
    forbidden = {
        "spawn_worker",
        "spawn-worker",
        "dispatch_with_review",
        "dispatch-with-review",
        "run_skill",
        "run-skill",
        "cli_tools_loader",
        "cli-tools",
        "multi_spawn",
        "multi-spawn",
    }
    leaked = forbidden & set(local_tools)
    assert not leaked, (
        f"Spawn/recursive tools leaked into the local-action worker set: {leaked}. "
        "These belong to the router tier only (D9 recursion guard, AP-5/AP-14)."
    )


def test_spawn_worker_in_router_tools() -> None:
    """``spawn-worker`` muss in ROUTER_TOOLS verdrahtet sein.

    Welle-4-Migration: vorher pruefte der Test zusaetzlich dass das alte
    Tool NICHT in ``SUB_TOOLS`` landet (D9-Recursion-Schutz). ``SUB_TOOLS``
    ist nach der OpenClaw-Bridge-Migration geloescht (siehe
    docs/openclaw-bridge.md §11) — Recursion-Schutz wird jetzt auf
    Mission-Manager-Ebene durchgesetzt (Worker hat keinen Spawn-Tool-
    Zugriff).
    """
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "spawn-worker" in ROUTER_TOOLS


@pytest.mark.parametrize(
    "utterance",
    [
        "Schreib ABC in das ChatGPT Eingabefeld",
        "Klick auf den Senden Button",
        "Tippe hallo in das aktive Fenster",
    ],
)
def test_pc_control_inputs_do_not_force_spawn_when_harness_available(utterance: str) -> None:
    """Lokale Screen-Bedienung muss zum Computer-Use-Harness durchkommen."""
    manager, _executor = _manager_with_spawn_and_computer_use()
    assert not manager._should_force_spawn(utterance)


@pytest.mark.parametrize(
    "utterance",
    [
        "Wie kann ich bei Windows reinzoomen?",
        "Wie kann ich Chrome oeffnen?",
        "How do I zoom in on Windows?",
    ],
)
def test_instructional_questions_do_not_force_spawn(utterance: str) -> None:
    """How-to-Fragen sind Wissensfragen, auch wenn sie Aktionswoerter enthalten."""
    manager, _executor = _manager_with_spawn_and_computer_use()
    assert not manager._should_force_spawn(utterance)


@pytest.mark.asyncio
async def test_local_direct_open_app_fast_path_uses_hidden_tool_once() -> None:
    """Greeting+action must execute open_app without vision or provider calls."""
    manager, executor = _manager_with_local_actions()

    def _provider_should_not_run(*_: Any, **__: Any) -> Any:
        raise AssertionError("local-action fast path must not call a Brain provider")

    manager._get_brain = _provider_should_not_run  # type: ignore[method-assign]

    result = await manager.generate("Hey Jarvis, kannst du Spotify aufmachen?")

    assert result == "ok"
    assert len(executor.calls) == 1
    tool, args, user_utterance = executor.calls[0]
    assert tool.name == "open_app"
    assert args == {"app_name": "spotify"}
    assert user_utterance == "Hey Jarvis, kannst du Spotify aufmachen?"


@pytest.mark.asyncio
async def test_local_direct_open_app_fast_path_handles_stt_auch_misshear() -> None:
    """Regression: STT may hear 'Mach Spotify auf' as 'Mach Spotify auch'."""
    manager, executor = _manager_with_local_actions()

    result = await manager.generate("Mach Spotify auch")

    assert result == "ok"
    assert len(executor.calls) == 1
    tool, args, user_utterance = executor.calls[0]
    assert tool.name == "open_app"
    assert args == {"app_name": "spotify"}
    assert user_utterance == "Mach Spotify auch"


@pytest.mark.asyncio
async def test_local_fast_path_publishes_response_generated() -> None:
    """Fast-path responses must keep normal response bus side effects."""
    bus = EventBus()
    seen: list[ResponseGenerated] = []

    async def _capture(ev: ResponseGenerated) -> None:
        seen.append(ev)

    bus.subscribe(ResponseGenerated, _capture)
    manager, _executor = _manager_with_local_actions_and_bus(bus)

    result = await manager.generate("Mach Spotify auf")

    assert result == "ok"
    assert [event.text for event in seen] == ["ok"]


@pytest.mark.asyncio
async def test_local_visual_click_fast_path_dispatches_computer_use() -> None:
    """Visual target commands offload to the computer-use harness (Wave-4).

    The spoken turn ACKs immediately instead of blocking up to ~31 s on the
    harness; the harness runs as a background task whose result is announced
    later. So ``generate`` returns the ACK, and the dispatch is observable only
    after the background task has run.
    """
    manager, executor = _manager_with_local_actions()

    result = await manager.generate("Klick auf den Senden Button")

    # Immediate ACK (not the inline harness result "ok").
    assert "Bildschirm" in result
    assert result != "ok"

    # The harness dispatch happens in the background — await it, then assert.
    await asyncio.gather(*getattr(manager, "_cu_background_tasks", set()))
    assert len(executor.calls) == 1
    tool, args, user_utterance = executor.calls[0]
    assert tool.name == "dispatch_to_harness"
    assert args["harness"] == "screenshot"
    assert args["prompt"] == "Klick auf den Senden Button"
    assert args["timeout_s"] == manager._config.local_action.harness_timeout_s
    assert user_utterance == "Klick auf den Senden Button"


@pytest.mark.asyncio
async def test_local_computer_use_respects_cost_cooldown() -> None:
    """Visual local commands can invoke paid planner work and must honor cooldown."""
    manager, executor = _manager_with_local_actions()
    manager._cost_meter = _CooldownCostMeter()

    result = await manager.generate("Klick auf den Senden Button")

    assert "Cost-Cooldown aktiv" in result
    assert executor.calls == []


def test_factory_local_action_tools_include_scripted_primitives() -> None:
    """Factory must load every tool name emitted by scripted local plans."""
    from jarvis.brain.factory import _load_local_action_tools

    cfg = JarvisConfig()
    tools = _load_local_action_tools(
        bus=EventBus(),
        harness_manager=None,
        config=cfg,
    )

    assert {"open_app", "type_text", "hotkey", "dispatch_to_harness"} <= set(tools)


@pytest.mark.asyncio
async def test_how_to_open_question_does_not_launch_app() -> None:
    """How-to wording containing 'oeffnen' must not trigger local open_app."""
    manager, executor = _manager_with_local_actions()
    manager._tools = {}

    result = await manager._run_local_action_fast_path("Wie kann ich Chrome oeffnen?")

    assert result is None
    assert executor.calls == []


@pytest.mark.asyncio
async def test_heavy_build_still_can_force_spawn() -> None:
    """Local action gate must not swallow heavyweight build/delegation work."""
    manager, executor = _manager_with_local_actions()
    manager._config.brain.primary = "gemini"

    result = await manager._force_spawn_worker("Bau eine Landingpage")

    assert result == "ok"
    assert len(executor.calls) == 1
    tool, args, user_utterance = executor.calls[0]
    assert tool.name == "spawn_worker"
    assert args["utterance"] == "Bau eine Landingpage"
    assert user_utterance == "Bau eine Landingpage"


def test_router_tools_is_pure_dispatcher_set() -> None:
    """ROUTER_TOOLS deckt sich mit Mandat-Phase-3 + Phase-7/8/Awareness +
    Welle-4-OpenClaw-Migration.

    Mandat-Phase-3 / Master-Plan §22 Z. 1617 (Baseline 2026-04-22):
      vier Tools — run-shell, screen-snapshot, multi-spawn, spawn-worker.

    Welle-4-Migration: ``spawn-worker`` umbenannt auf ``spawn-worker``
    (siehe docs/openclaw-bridge.md §11). Heavy-Worker laeuft als externer
    Subprocess via Mission-Manager.

    Phase-5-Endstand (ADR-0011, 2026-04-25) hat ``dispatch-to-harness``
    re-introduziert: Hauptjarvis braucht den direkten Harness-Pfad, weil
    sonst jeder Screen-Observe-+-Sofort-Dispatch-Use-Case einen Spawn
    forcieren wuerde (Latenz-Killer).

    Phase 8.4 (Plan §6.4 Quality-Gate-Pipeline, 2026-04-26) hat
    ``dispatch-with-review`` ergaenzt: Hauptjarvis ruft die Review-Pipeline
    direkt auf.

    Phase 7.3 (Self-Mod, 2026-04-25) registriert drei zusaetzliche Tools
    direkt im Loader (NICHT via entry_points, NICHT in dieser Frozenset),
    die ausschliesslich Hauptjarvis-Tier zugaenglich sind (Plan §AD-2).

    Awareness-Plan §5 fuegt ``awareness-snapshot`` hinzu: synchroner
    State-Read im Critical-Path, kein Brain-/IO-Roundtrip.

    Awareness-Plan §7 (Phase A3) adds ``awareness-recall``: read-only
    BM25 search over the recent episode log. Originally planned as a
    sub-tier tool; Welle 4 removed that tier so it lives here. Still
    read-only, still safe, still no LLM/IO beyond a single SQLite query.

    Skills-Brain-Integration fuegt ``run-skill`` hinzu: Brain-callable
    executor for installed user skills. Available to BOTH tiers; D9-
    recursion-protection is structural (SkillRunner is constructed without
    a tool_registry that would expose run-skill recursively).

    Phase B5 (recall-tool, 2026-05-12) adds ``wiki-recall``: read-only
    keyword search over the long-term Obsidian wiki vault. Router-tier
    only (AP-D9); no LLM call, no network, no mutation. The brain calls
    this when the user asks "what do we know about X" or references a
    past project, person, or decision by name.

    Bei jeder weiteren Erweiterung: Begruendung in ADR-0011 (Sektion
    "Subsequent Phase-7/8 Extensions") nachpflegen + diesen Test +
    ``test_recursive_tools_only_in_router`` updaten.
    """
    from jarvis.brain.factory import ROUTER_TOOLS

    expected = frozenset(
        {
            # Mandat-Phase-3 Baseline (Master-Plan §22)
            "run-shell",
            "screen-snapshot",
            "multi-spawn",
            "spawn-worker",
            # Phase-5-Endstand (ADR-0011 + Re-Introduction Begruendung im Code)
            "dispatch-to-harness",
            # Phase 8.4 (Quality-Gate, Recursion-geschuetzt analog spawn-worker)
            "dispatch-with-review",
            # AI Pointer (pull path): resolve the element under the mouse cursor
            # via the OS accessibility tree. Read-only safe-tier, direct action,
            # never a spawn (AP-5/AP-14). See docs/plans/ai-pointer/DESIGN.md.
            "inspect-pointer",
            # UI navigation (2026-06-02): switch the active sidebar section by
            # voice/chat. Pure UI action (risk safe), publishes NavigateSidebar,
            # never a spawn (AP-5/AP-14). See ADR-0011 amendment "Navigate tool".
            "navigate",
            "awareness-snapshot",
            # Awareness Phase A3 (BM25 search over recent episode log).
            "awareness-recall",
            # Skills-Brain-Integration (also in SUB_TOOLS — structural D9 protection)
            "run-skill",
            # Phase B5 (recall-tool): read-only keyword search over the wiki vault.
            "wiki-recall",
            # Phase B5 follow-up (commit 825b1f94a): full-page reader (read-only)
            # + deterministic ingest (write via WikiCurator). Both router-tier only.
            "wiki-page-read",
            "wiki-ingest",
            # CLI-Integration (2026-05-24): virtual loader that expands to one
            # cli_<name> tool per connected & usable CLI. Router-tier only — a
            # cli_<name> tool is a direct safe-gated action, never a recursive
            # spawn, so it never enters a worker tool-set (AP-5/AP-14). See
            # ADR-0011 amendment "CLI-Integration".
            "cli-tools",
            # Marketplace plugins as live brain tools (2026-06-01): virtual
            # loader that expands to one MCPToolAdapter per connected plugin
            # tool. Direct safe/risk-gated action, never a spawn — must not
            # enter any worker tool-set (AP-5/AP-14).
            "plugin-tools",
            # Gmail Marketplace plugin (2026-06-01): native REST tool backed by
            # the marketplace OAuth token. Gmail has no MCP server block, so it
            # must be router-visible directly.
            "gmail",
            # Vercel Marketplace plugin (2026-06-07): native REST tool, same
            # rationale as gmail — its rest_wrapper transport produced zero MCP
            # tools, so it must be router-visible directly. Read-only.
            "vercel",
            # Computer-Use (Wave 1, 2026-05-29): first-class tool to drive the
            # live desktop. Router-tier only — a direct safe-gated action (the
            # loop gates each action via ToolExecutor, ADR-0008), never a spawn,
            # so it never enters a worker tool-set (AP-5/AP-14). See ADR-0011
            # amendment "Computer-Use Router Tool".
            "computer-use",
            # Profile-write (2026-05-30): deterministic writer for the structured
            # USER.md profile clusters (the Knowledge-matrix + system-prompt
            # source). The sanctioned structured-profile analogue of wiki-ingest
            # — added because the legacy auto-curator is soft-disabled (B4) and
            # the WikiCurator only writes prose. Direct safe-gated write, never a
            # spawn → never in a worker set (AP-5/AP-14). See ADR-0011 amendment
            # "Profile-Write Router Tool".
            "update-profile",
            # App-Control Tools (2026-05-31): describe-app-settings (safe,
            # read-only overview), switch-provider + manage-mcp-server (ask,
            # echo-confirm). Direct safe/ask-gated actions, never spawns
            # (AP-5/AP-14); raw secret values never accepted (AP-2). See
            # ADR-0011 amendment "App-Control Tools".
            "describe-app-settings",
            "switch-provider",
            "manage-mcp-server",
            # Masked key preview (2026-05-31, user mandate): first 3 + last 3
            # chars of a stored key, never the full value. monitor-tier, narrow
            # safe exception to AP-2. See ADR-0011 amendment "App-Control Tools".
            "reveal-key-preview",
            # Chunk B jarvis-contacts (2026-06-02): the three contact-action
            # tools. contact-lookup (safe, read) resolves a name -> e-mail/
            # phone/address; contact-upsert (monitor, deterministic write) saves
            # a contact by voice; call-contact (ask, echo-confirm) places a real
            # outbound call via the telephony engine. All direct safe/monitor/
            # ask-gated actions, never spawns (AP-5/AP-14). See ADR-0011
            # amendment "Contacts Tools".
            "contact-lookup",
            "contact-upsert",
            "call-contact",
        }
    )
    assert ROUTER_TOOLS == expected, (
        f"ROUTER_TOOLS {sorted(ROUTER_TOOLS)} weicht ab vom erwarteten "
        f"{sorted(expected)}. Persona-Mandat Phase 3 + Master-Plan §22 + "
        "ADR-0011 (inkl. Phase-7/8/Awareness-Erweiterungen + Welle-4-Migration). "
        "Direkt-Aktionen wie open_app/type_text/search_web/whoami DUERFEN NICHT "
        "hinzu — die gehoeren an die OpenClaw-Bridge. Sanktionierte Ausnahmen "
        "sind deterministische Schreib-Tools mit eigenem ADR-0011-Eintrag "
        "(wiki-ingest, update-profile)."
    )


def test_update_profile_in_router_tools() -> None:
    """``update-profile`` must live in ROUTER_TOOLS (2026-05-30).

    It is the deterministic, brain-driven writer for the structured USER.md
    profile that the Knowledge matrix and the per-turn system prompt read. The
    legacy background Curator that used to fill those clusters is soft-disabled
    (B4); the active WikiCurator only writes free-form prose. Without this
    entry-point in the frozenset the loader drops the tool and the brain has no
    way to persist a stated personal fact (the matrix re-freezes).
    """
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "update-profile" in ROUTER_TOOLS


def test_gmail_marketplace_tool_in_router_tools() -> None:
    """Connected Gmail must become a callable brain tool.

    Gmail is not MCP-backed; it uses the in-repo REST tool and reads the
    marketplace OAuth token at execution time. If this entry is missing, the
    active voice brain drops the tool while the Plugins UI shows Gmail
    connected.
    """
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "gmail" in ROUTER_TOOLS


def test_factory_wires_gmail_tool_into_router_set() -> None:
    """End-to-end wiring: entry-point + ROUTER_TOOLS expose ``gmail``."""
    from jarvis.brain.factory import _load_tools_for_tier

    tools = _load_tools_for_tier(
        "router",
        bus=EventBus(),
        executor=None,
        harness_manager=None,
        user_profile=None,
        people=None,
        config=JarvisConfig(),
    )

    assert "gmail" in tools
    assert tools["gmail"].name == "gmail"


def test_factory_wires_update_profile_tool_into_router_set() -> None:
    """End-to-end wiring: entry-point + ROUTER_TOOLS + factory branch connect.

    A built router tier must expose a callable ``update_profile`` tool. This
    catches a missing entry-point registration (``pip install -e .``) or a
    missing construction branch in ``_load_tools_for_tier`` — either would
    silently drop the tool and re-introduce the frozen-matrix bug.
    """
    from jarvis.brain.factory import _load_tools_for_tier

    tools = _load_tools_for_tier(
        "router",
        bus=EventBus(),
        executor=None,
        harness_manager=None,
        user_profile=None,
        people=None,
        config=JarvisConfig(),
    )

    assert "update_profile" in tools
    assert tools["update_profile"].name == "update_profile"


# --- H2 (2026-05-17 audit): Whisper-FP exact vs prefix buckets ------------
#
# Before the split, _WHISPER_FALSE_POSITIVE_SEEDS lived in a single
# frozenset and the filter matched both exact-equality and
# startswith-seed-plus-space. That dropped legitimate utterances like
# "You there?" because the seed "you" matched everything starting with
# "You ". The split moves single-token seeds into _WHISPER_FP_EXACT_ONLY
# (only whole-utterance match) and leaves multi-word seeds in
# _WHISPER_FP_PREFIX_OK (startswith still allowed because such phrases
# are distinctive). Disjointness is asserted at import time.


@pytest.mark.parametrize(
    "utterance",
    [
        "you",
        "You",
        "you.",
        "You!",
        "you?",
        "musik",
        "Musik.",
        "[musik]",
        "applaus",
        "[applaus]",
        "subscribe",
        "tschüss",
        "untertitel",
        "Untertitelung",
        "thank you",
        "Thank you.",
    ],
)
def test_whisper_fp_exact_only_seeds_still_filter_when_alone(
    utterance: str,
) -> None:
    """Each single-token seed must still suppress force-spawn when it
    arrives as the entire utterance (after lowercasing + punctuation
    strip). This is the original BUG-LIVE-04 protection — must not
    regress."""
    manager, _executor = _manager_with_spawn_and_computer_use()
    assert not manager._should_force_spawn(utterance), (
        f"Whisper FP exact-only seed {utterance!r} must still be filtered "
        "when it is the entire utterance"
    )


@pytest.mark.parametrize(
    "utterance",
    [
        # English single-word starts that the old startswith match would have
        # silently filtered. All must reach the spawn path now.
        "You there?",
        "You know what, never mind.",
        "Subscribe me to the daily digest please",
        # German short queries that begin with a FP seed.
        "Musik lauter machen",
        "Musik leiser bitte",
        "Applaus für die Band einspielen",
        "Untertitel im VLC anzeigen lassen",
        "Tschüss sagen zu Laura per Email",
        "Thank you note in Word erstellen",
    ],
)
def test_whisper_fp_exact_only_seeds_do_not_filter_when_part_of_phrase(
    utterance: str,
) -> None:
    """Legitimate user utterances that *begin* with a single-token FP
    seed but continue past it must NOT be filtered. This is the H2 fix
    the audit caught — the old greedy startswith match swallowed these
    silently."""
    manager, _executor = _manager_with_spawn_and_computer_use()
    # We only assert the filter doesn't pre-empt; whether the manager
    # actually forces a spawn depends on the verb/intent heuristic and
    # is asserted by the existing routing tests. The CRITICAL guarantee
    # here is that the Whisper-FP filter does not silently drop these
    # phrases. We verify that by checking the filter result against the
    # behaviour on a non-FP utterance of similar shape.
    baseline = "Tonhoehe lauter machen"  # no FP seed, otherwise similar
    # Both should land on the same branch of the heuristic (either both
    # force or neither). The bug was that the FP utterance returned
    # False unconditionally while the baseline went through verb match.
    seed_result = manager._should_force_spawn(utterance)
    baseline_result = manager._should_force_spawn(baseline)
    assert seed_result == baseline_result, (
        f"{utterance!r} produced {seed_result} but the non-FP baseline "
        f"{baseline!r} produced {baseline_result} — the FP filter is "
        "still eating legitimate phrases"
    )


@pytest.mark.parametrize(
    "utterance",
    [
        "vielen dank",
        "vielen dank fürs zuschauen",
        "Vielen Dank fürs Zuschauen!",
        "Vielen Dank für Ihre Aufmerksamkeit.",
        "bis zum nächsten Mal",
        "Bis zum nächsten Mal!",
        "thanks for watching",
        "Thanks for watching this video.",
        "see you next time",
        "Ich verstehe es nicht.",
        "untertitelung des zdf für funk",
        "Untertitelung des ZDF für funk, 2017",
    ],
)
def test_whisper_fp_prefix_seeds_still_match_startswith(
    utterance: str,
) -> None:
    """Multi-word seeds keep their startswith match — these phrases are
    distinctive enough that any utterance starting with them is almost
    certainly TV/jingle audio, not a real command. Prefix bucket
    behaviour must survive the split."""
    manager, _executor = _manager_with_spawn_and_computer_use()
    assert not manager._should_force_spawn(utterance), (
        f"Whisper FP prefix seed in {utterance!r} must keep filtering"
    )


def test_whisper_fp_buckets_are_disjoint() -> None:
    """Compile-time invariant: every seed lives in exactly one bucket.
    The module-level assert catches duplicates at import; this test
    locks it for the test runner."""
    from jarvis.brain.manager import (
        _WHISPER_FP_EXACT_ONLY,
        _WHISPER_FP_PREFIX_OK,
    )

    overlap = _WHISPER_FP_EXACT_ONLY & _WHISPER_FP_PREFIX_OK
    assert overlap == set(), f"Whisper FP buckets must be disjoint, overlap={overlap}"


def test_whisper_fp_combined_union_matches_legacy_alias() -> None:
    """The legacy `_WHISPER_FALSE_POSITIVE_SEEDS` alias must equal the
    union of both buckets so external introspection (telemetry, eval)
    sees the complete catalogue."""
    from jarvis.brain.manager import (
        _WHISPER_FALSE_POSITIVE_SEEDS,
        _WHISPER_FP_EXACT_ONLY,
        _WHISPER_FP_PREFIX_OK,
    )

    assert _WHISPER_FALSE_POSITIVE_SEEDS == (_WHISPER_FP_EXACT_ONLY | _WHISPER_FP_PREFIX_OK)


# ---------------------------------------------------------------------------
# Agent-C: capability-coupling tests
# ---------------------------------------------------------------------------


def test_check_unsupported_intent_returns_none_when_module_absent(monkeypatch) -> None:
    """_check_unsupported_intent must return None gracefully when the
    CapabilityRegistry module is not yet deployed (graceful-no-op contract).

    We simulate the module-absent scenario by patching ``get_registry`` to
    raise ImportError, mirroring what the late-import guard in
    ``manager._check_unsupported_intent`` is expected to catch.
    """
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "jarvis.core.capabilities" or name.startswith("jarvis.core.capabilities."):
            raise ImportError("simulated: capabilities module not deployed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    manager, _executor = _manager_with_spawn()
    result = manager._check_unsupported_intent("Schick eine Email an test@example.com")
    assert result is None, (
        "_check_unsupported_intent must return None when capabilities module absent"
    )


def test_check_unsupported_intent_returns_none_for_smalltalk() -> None:
    """Smalltalk utterances must never trigger the unsupported-intent gate,
    even when the CapabilityRegistry is present and the text contains
    action verbs in casual context."""
    import sys
    import types

    # Build a minimal mock registry that always reports action intent True
    # but resolve_intent returns None — simulates "action verb detected but
    # no capability registered".  Even so, smalltalk must win.
    mock_reg = types.SimpleNamespace(
        # Populated registry (seed ran at boot); this scenario is "action
        # recognised but no capability maps to it", NOT "registry never seeded".
        all=lambda: (object(),),
        has_action_intent=lambda _t: True,
        resolve_intent=lambda _t: None,
        render_for_prompt=lambda lang="de": "",
    )
    mock_module = types.ModuleType("jarvis.core.capabilities")
    mock_module.get_registry = lambda: mock_reg  # type: ignore[attr-defined]

    original = sys.modules.get("jarvis.core.capabilities")
    sys.modules["jarvis.core.capabilities"] = mock_module
    try:
        manager, _executor = _manager_with_spawn()
        # These are clearly smalltalk — must not trigger refusal.
        for utterance in ["Hallo", "Wie geht's?", "Danke"]:
            result = manager._check_unsupported_intent(utterance)
            assert result is None, (
                f"Smalltalk {utterance!r} must not trigger unsupported-intent gate"
            )
    finally:
        if original is not None:
            sys.modules["jarvis.core.capabilities"] = original
        else:
            sys.modules.pop("jarvis.core.capabilities", None)


def test_check_unsupported_intent_fires_for_unregistered_action() -> None:
    """When has_action_intent=True and resolve_intent=None and the utterance
    is not smalltalk, _check_unsupported_intent must return a non-empty
    refusal string in DE (for German-flavoured utterances)."""
    import sys
    import types

    mock_reg = types.SimpleNamespace(
        # Populated registry (seed ran at boot); this scenario is "action
        # recognised but no capability maps to it", NOT "registry never seeded".
        all=lambda: (object(),),
        has_action_intent=lambda _t: True,
        resolve_intent=lambda _t: None,
        render_for_prompt=lambda lang="de": "",
    )
    mock_module = types.ModuleType("jarvis.core.capabilities")
    mock_module.get_registry = lambda: mock_reg  # type: ignore[attr-defined]

    original = sys.modules.get("jarvis.core.capabilities")
    sys.modules["jarvis.core.capabilities"] = mock_module
    try:
        manager, _executor = _manager_with_spawn()
        result = manager._check_unsupported_intent("Schick bitte eine E-Mail an Sam")
        assert result is not None, (
            "_check_unsupported_intent must return refusal for unregistered action"
        )
        assert len(result) > 10, "Refusal must be a non-trivial sentence"
        # Must not be a fake confirmation.
        assert "kann ich noch nicht" in result or "can't do that" in result.lower(), (
            f"Refusal text must contain expected phrase, got: {result!r}"
        )
    finally:
        if original is not None:
            sys.modules["jarvis.core.capabilities"] = original
        else:
            sys.modules.pop("jarvis.core.capabilities", None)


def test_check_unsupported_intent_passes_through_when_capability_resolves() -> None:
    """When resolve_intent returns a non-None capability, the gate must
    return None (allow the intent to proceed normally)."""
    import sys
    import types

    fake_capability = object()  # any truthy value represents a resolved capability
    mock_reg = types.SimpleNamespace(
        all=lambda: (object(),),  # populated registry (seed ran at boot)
        has_action_intent=lambda _t: True,
        resolve_intent=lambda _t: fake_capability,
        render_for_prompt=lambda lang="de": "- tool.open-app: opens an application",
    )
    mock_module = types.ModuleType("jarvis.core.capabilities")
    mock_module.get_registry = lambda: mock_reg  # type: ignore[attr-defined]

    original = sys.modules.get("jarvis.core.capabilities")
    sys.modules["jarvis.core.capabilities"] = mock_module
    try:
        manager, _executor = _manager_with_spawn()
        result = manager._check_unsupported_intent("Öffne Chrome")
        assert result is None, "_check_unsupported_intent must return None when capability resolves"
    finally:
        if original is not None:
            sys.modules["jarvis.core.capabilities"] = original
        else:
            sys.modules.pop("jarvis.core.capabilities", None)


def test_check_unsupported_intent_returns_none_when_registry_empty() -> None:
    """An EMPTY (never-seeded) registry must NOT fire the unsupported-intent
    refusal — even though ``has_action_intent`` is True and ``resolve_intent``
    is None.

    Regression for the 2026-05-25 live bug: ``seed_registry()`` was never
    called at boot, so the production registry stayed empty.
    ``has_action_intent`` matches the STATIC ``_UNIVERSAL_ACTION_VERBS`` (seed
    independent) → True, while ``resolve_intent`` finds nothing → None, so the
    gate spoke "Das kann ich noch nicht. Mir fehlt dafür ein Werkzeug …" for
    EVERY action utterance (including "Kannst du mir einen Subagent spawnen …"),
    pre-empting the deterministic force-spawn path. The gate must treat an empty
    registry as "not ready yet" and step aside, mirroring the guard in
    ``local_action_gate.match_local_action`` (registry must be populated).
    """
    import sys
    import types

    mock_reg = types.SimpleNamespace(
        all=lambda: (),  # EMPTY — registry was never seeded
        has_action_intent=lambda _t: True,
        resolve_intent=lambda _t: None,
        render_for_prompt=lambda lang="de": "",
    )
    mock_module = types.ModuleType("jarvis.core.capabilities")
    mock_module.get_registry = lambda: mock_reg  # type: ignore[attr-defined]

    original = sys.modules.get("jarvis.core.capabilities")
    sys.modules["jarvis.core.capabilities"] = mock_module
    try:
        manager, _executor = _manager_with_spawn()
        result = manager._check_unsupported_intent(
            "Kannst du mir einen Subagent spawnen, der eine Datei macht"
        )
        assert result is None, (
            "empty/unseeded registry must NOT fire the unsupported-intent "
            "refusal — it pre-empts force-spawn and breaks every action command"
        )
    finally:
        if original is not None:
            sys.modules["jarvis.core.capabilities"] = original
        else:
            sys.modules.pop("jarvis.core.capabilities", None)


# ---------------------------------------------------------------------------
# 2026-06-01: Sub-agent as the UNIVERSAL CAPABILITY for generic work.
#
# A work request with an action verb but no registered MCP capability and no
# SPECIFIC external integration (email/calendar/spotify/...) is sub-agent-worthy
# and must spawn NATIVELY — even in strict mode, WITHOUT the user saying
# "Subagent"/"spawn" — instead of being refused with "Das kann ich noch nicht".
# Only specific external integrations stay refused: a generic claude-cli worker
# cannot send an email or play Spotify, but it CAN analyse/build/fix/code and
# drive git/gh. Live forensic 2026-06-01 (voice turn 21:45:24): a sub-agent task
# was refused, then only spawned once the user said "Subagent" explicitly.
# ---------------------------------------------------------------------------


def _strict_manager_with_mock_registry(
    *, has_action: bool = True, resolves: bool = False, populated: bool = True
):
    """A strict-mode manager with a mocked capability registry in sys.modules.

    The mock returns a constant ``has_action_intent`` / ``resolve_intent`` so the
    SPAWN-vs-REFUSE decision is driven purely by the real external-integration
    detector running on the actual utterance text.
    """
    import sys
    import types

    mock_reg = types.SimpleNamespace(
        all=lambda: ((object(),) if populated else ()),
        has_action_intent=lambda _t: has_action,
        resolve_intent=lambda _t: (object() if resolves else None),
        render_for_prompt=lambda lang="de": "",
    )
    mock_module = types.ModuleType("jarvis.core.capabilities")
    mock_module.get_registry = lambda: mock_reg  # type: ignore[attr-defined]
    sys.modules["jarvis.core.capabilities"] = mock_module
    manager, _executor = _manager_with_spawn(force_spawn_mode="strict")
    return manager


def test_generic_work_spawns_natively_in_strict_mode() -> None:
    """A generic work task (build/analyse/fix) with no capability and no
    external integration must spawn a sub-agent natively in STRICT mode —
    without the user uttering an explicit 'Subagent'/'spawn' trigger."""
    original = __import__("sys").modules.get("jarvis.core.capabilities")
    try:
        manager = _strict_manager_with_mock_registry()
        for utterance in (
            "baue mir ein Python-Skript das die Java-Support Logs analysiert",
            "fix den Bug in der Authentifizierung",
            "implementier eine Funktion die CSV nach JSON konvertiert",
        ):
            assert manager._should_force_spawn(utterance), (
                f"generic work {utterance!r} must spawn natively in strict mode"
            )
    finally:
        if original is not None:
            __import__("sys").modules["jarvis.core.capabilities"] = original
        else:
            __import__("sys").modules.pop("jarvis.core.capabilities", None)


def test_generic_work_not_refused_as_unsupported() -> None:
    """The same generic work task must NOT be refused with 'kann ich noch
    nicht' — the sub-agent is its universal capability."""
    original = __import__("sys").modules.get("jarvis.core.capabilities")
    try:
        manager = _strict_manager_with_mock_registry()
        result = manager._check_unsupported_intent(
            "baue mir ein Python-Skript das die Logs analysiert"
        )
        assert result is None, (
            "generic work must not be refused as unsupported — route to sub-agent"
        )
    finally:
        if original is not None:
            __import__("sys").modules["jarvis.core.capabilities"] = original
        else:
            __import__("sys").modules.pop("jarvis.core.capabilities", None)


def test_external_integration_without_capability_stays_unsupported() -> None:
    """A SPECIFIC external integration (email/calendar/spotify) with no
    registered capability must STILL be refused and must NOT spawn — a generic
    worker cannot fulfil it. Preserves the anti-hallucination contract."""
    original = __import__("sys").modules.get("jarvis.core.capabilities")
    try:
        manager = _strict_manager_with_mock_registry()
        for utterance in (
            "schick eine Email an Sam",
            "trag einen Termin in meinen Kalender ein",
            "spiel Musik auf Spotify",
        ):
            assert manager._check_unsupported_intent(utterance) is not None, (
                f"external integration {utterance!r} must stay unsupported"
            )
            assert not manager._should_force_spawn(utterance), (
                f"external integration {utterance!r} must not spawn a worker"
            )
    finally:
        if original is not None:
            __import__("sys").modules["jarvis.core.capabilities"] = original
        else:
            __import__("sys").modules.pop("jarvis.core.capabilities", None)


def test_coding_task_mentioning_integration_is_not_refused() -> None:
    """A coding task that merely MENTIONS an integration name as a topic /
    data-type (email validator, calendar parser, Spotify-like player) is generic
    sub-agent work — NOT a real dispatch. It must spawn, never be refused. The
    integration name alone is not enough; a real dispatch verb must be present
    (code-review MAJOR, 2026-06-01)."""
    original = __import__("sys").modules.get("jarvis.core.capabilities")
    try:
        manager = _strict_manager_with_mock_registry()
        for utterance in (
            "implementier eine Funktion die Email-Adressen validiert",
            "baue einen Parser fuer Kalender-Dateien im ICS-Format",
            "schreib Code der Spotify-Playlists aus einer JSON-Datei liest",
        ):
            assert manager._check_unsupported_intent(utterance) is None, (
                f"coding task {utterance!r} must not be refused (mentions an "
                "integration name only as data, not a dispatch target)"
            )
            assert manager._should_force_spawn(utterance), (
                f"coding task {utterance!r} must spawn a sub-agent"
            )
    finally:
        if original is not None:
            __import__("sys").modules["jarvis.core.capabilities"] = original
        else:
            __import__("sys").modules.pop("jarvis.core.capabilities", None)


def test_is_generic_subagent_work_false_on_empty_registry() -> None:
    """An empty/unseeded registry must NOT let _is_generic_subagent_work spawn —
    otherwise a boot before seed_registry() would spawn-storm (mirrors the
    _check_unsupported_intent empty-registry guard)."""
    original = __import__("sys").modules.get("jarvis.core.capabilities")
    try:
        manager = _strict_manager_with_mock_registry(populated=False)
        assert not manager._is_generic_subagent_work("baue mir ein Skript"), (
            "empty registry must not spawn — explicit trigger is the sole signal"
        )
    finally:
        if original is not None:
            __import__("sys").modules["jarvis.core.capabilities"] = original
        else:
            __import__("sys").modules.pop("jarvis.core.capabilities", None)


def test_github_work_is_not_treated_as_external_integration() -> None:
    """git/GitHub work is sub-agent-fulfillable (the worker has git + gh), so a
    'commit and push' / 'open a PR' task must spawn, never be refused."""
    original = __import__("sys").modules.get("jarvis.core.capabilities")
    try:
        manager = _strict_manager_with_mock_registry()
        utterance = "committe die Aenderungen und mach einen GitHub Pull Request"
        assert manager._check_unsupported_intent(utterance) is None
        assert manager._should_force_spawn(utterance)
    finally:
        if original is not None:
            __import__("sys").modules["jarvis.core.capabilities"] = original
        else:
            __import__("sys").modules.pop("jarvis.core.capabilities", None)


class _FakeProfileForPrompt:
    """Minimal UserProfile stand-in: only render_for_prompt is exercised."""

    def render_for_prompt(self, *, max_chars: int = 2000) -> str:
        return "## Ueber den User\n- **Name:** Alex"


class _FakeUpdateProfileTool:
    name = "update_profile"
    schema: dict[str, Any] = {}


def test_system_prompt_includes_profile_write_directive_when_tool_wired() -> None:
    """When update_profile is in the tool set and a user profile exists, the
    system prompt must instruct the brain to persist durable personal facts.

    Without this directive the (now sole) write path is dead: the legacy auto-
    curator is soft-disabled, so the brain only learns structured facts if it
    actively calls update_profile — which it will not do reliably unless told.
    """
    manager = BrainManager(
        config=JarvisConfig(),
        bus=EventBus(),
        tools={"update_profile": _FakeUpdateProfileTool()},
        tool_executor=_RecordingExecutor(),  # type: ignore[arg-type]
        user_profile=_FakeProfileForPrompt(),
    )
    prompt = manager._build_system_prompt()
    assert "PROFIL-PFLEGE" in prompt
    assert "update_profile" in prompt


def test_system_prompt_omits_profile_directive_when_tool_absent() -> None:
    """No update_profile tool → no directive (never instruct a tool that is not
    wired; that would contradict the hard 'do not invent tools' rule)."""
    manager = BrainManager(
        config=JarvisConfig(),
        bus=EventBus(),
        tools={"spawn_worker": _FakeTool()},
        tool_executor=_RecordingExecutor(),  # type: ignore[arg-type]
        user_profile=_FakeProfileForPrompt(),
    )
    prompt = manager._build_system_prompt()
    assert "PROFIL-PFLEGE" not in prompt


def test_system_prompt_contains_no_invent_tools_rule() -> None:
    """The system prompt must contain the hard 'do not invent tools' rule
    in both DE and EN regardless of whether the capability registry is
    deployed (the fallback block also carries the rule)."""
    manager, _executor = _manager_with_spawn()
    prompt = manager._build_system_prompt()
    assert "Erfinde keine Tools" in prompt, (
        "System prompt must contain DE 'Erfinde keine Tools' rule"
    )
    assert "Do not invent tools" in prompt, (
        "System prompt must contain EN 'Do not invent tools' rule"
    )


@pytest.mark.parametrize(
    "forbidden_phrase",
    [
        "mache ich",
        "wird erledigt",
        "ist gesendet",
        "ist eingetragen",
        "kümmere mich",
    ],
)
def test_ack_brain_persona_de_forbids_action_promise_phrases(
    forbidden_phrase: str,
) -> None:
    """PERSONA_PROMPT_DE must explicitly list each action-promise phrase as
    forbidden so the ack-brain cannot emit fake confirmations."""
    from jarvis.brain.ack_brain.persona_prompt import PERSONA_PROMPT_DE

    assert forbidden_phrase in PERSONA_PROMPT_DE, (
        f"PERSONA_PROMPT_DE must list forbidden action-promise phrase: {forbidden_phrase!r}"
    )


@pytest.mark.parametrize(
    "forbidden_phrase",
    [
        "I'll do that",
        "will be sent",
        "will be scheduled",
        "consider it done",
    ],
)
def test_ack_brain_persona_en_forbids_action_promise_phrases(
    forbidden_phrase: str,
) -> None:
    """PERSONA_PROMPT_EN must explicitly list each action-promise phrase as
    forbidden so the ack-brain cannot emit fake confirmations."""
    from jarvis.brain.ack_brain.persona_prompt import PERSONA_PROMPT_EN

    assert forbidden_phrase in PERSONA_PROMPT_EN, (
        f"PERSONA_PROMPT_EN must list forbidden action-promise phrase: {forbidden_phrase!r}"
    )


# ---------------------------------------------------------------------------
# Chunk B (jarvis-contacts) — contact tool registration + routing discipline.
#
# Three new router-tier tools: contact-lookup (safe), contact-upsert (monitor),
# call-contact (ask). All append-only on the shared seams (ROUTER_TOOLS,
# pyproject entry-points, this test, ADR-0011). The PFLICHT routing tests prove
# the BUG-class `project_bug_subagent_not_natively_recognized` does NOT bite:
# "schreib eine Mail an Christoph" and "ruf Christoph an" must stay router-tier
# (no false refuse, no contextless worker spawn).
# ---------------------------------------------------------------------------


def test_contact_tools_in_router_tools() -> None:
    """The three contact tools must live in ROUTER_TOOLS — the loader filters
    entry-points against this frozenset, so a missing entry silently drops the
    tool from the router schema."""
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "contact-lookup" in ROUTER_TOOLS
    assert "contact-upsert" in ROUTER_TOOLS
    assert "call-contact" in ROUTER_TOOLS


def test_contact_tools_not_in_local_action_set() -> None:
    """Contact tools are router-tier reads/writes/actions, never in the
    deterministic local-action worker fast-path (D9 recursion guard,
    AP-5/AP-14)."""
    from jarvis.brain.factory import _load_local_action_tools

    local_tools = _load_local_action_tools(
        bus=EventBus(),
        harness_manager=None,
        config=JarvisConfig(),
    )
    for name in ("contact-lookup", "contact-upsert", "call-contact"):
        assert name not in local_tools


def test_factory_wires_contact_tools_into_router_set() -> None:
    """End-to-end wiring: entry-point + ROUTER_TOOLS + factory construction
    branch connect, with the contracted risk tiers. A missing entry-point
    registration (pip install -e .) or construction branch would silently drop
    the tool from the router schema."""
    from jarvis.brain.factory import _load_tools_for_tier

    tools = _load_tools_for_tier(
        "router",
        bus=EventBus(),
        executor=None,
        harness_manager=None,
        user_profile=None,
        people=None,
        config=JarvisConfig(),
    )

    assert tools["contact-lookup"].name == "contact-lookup"
    assert tools["contact-lookup"].risk_tier == "safe"
    assert tools["contact-upsert"].name == "contact-upsert"
    assert tools["contact-upsert"].risk_tier == "monitor"
    assert tools["call-contact"].name == "call-contact"
    assert tools["call-contact"].risk_tier == "ask"


def test_capability_seed_registers_contact_capabilities() -> None:
    """The contact tools must be registered as capabilities so the gate routes
    a named-person action to them instead of refusing/spawning. resolve_intent
    must map a call/mail/save-by-name utterance to the contact surface."""
    from jarvis.core.capabilities import get_registry
    from jarvis.core.capabilities_seed import seed_registry

    reg = get_registry()
    seed_registry(reg)

    call_cap = reg.resolve_intent("ruf Christoph an")
    assert call_cap is not None and call_cap.id == "tool.call-contact"

    save_cap = reg.resolve_intent("merk dir Christophs Nummer ist 0151 12345678")
    assert save_cap is not None and save_cap.id == "tool.contact-upsert"


def test_contact_capabilities_do_not_resolve_external_hard_negatives() -> None:
    """Adding contact capabilities must NOT make the canonical hard-negatives
    resolve — they must stay UNSUPPORTED (resolve_intent None) so the
    anti-hallucination contract (test_capability_coupling_e2e) is preserved."""
    from jarvis.core.capabilities import get_registry
    from jarvis.core.capabilities_seed import seed_registry

    reg = get_registry()
    seed_registry(reg)
    for utterance in (
        "Schick eine Email an sam@gmx.de mit dem Betreff Hallo",
        "Trag einen Termin morgen 10 Uhr ein",
        "Sende eine WhatsApp an Mama",
        "Bestelle eine Pizza",
        "Poste auf X dass ich heute frei habe",
    ):
        assert reg.resolve_intent(utterance) is None, (
            f"contact capabilities must not resolve hard-negative {utterance!r}"
        )


# --- PFLICHT-Tests: mail-by-name + call-by-name stay router-tier ------------
#
# Built against the REAL seeded CapabilityRegistry (the conftest snapshot/
# restores it). The gate (`_check_unsupported_intent`) must NOT refuse and the
# force-spawn heuristic (`_should_force_spawn`) must NOT spawn — the router
# brain then reaches contact-lookup + gmail / call-contact natively.


def _strict_manager_with_seeded_registry() -> BrainManager:
    """A strict-mode manager (production default) over the REAL seeded
    registry — exactly the production gate path for these utterances."""
    from jarvis.core.capabilities import get_registry
    from jarvis.core.capabilities_seed import seed_registry

    seed_registry(get_registry())
    manager, _executor = _manager_with_spawn(force_spawn_mode="strict")
    return manager


def test_mail_by_name_stays_router_tier() -> None:
    """'schreib eine Mail an Christoph' must NOT be refused and must NOT spawn a
    contextless worker — it stays router-tier so the brain calls contact-lookup
    then gmail. BUG-class project_bug_subagent_not_natively_recognized."""
    manager = _strict_manager_with_seeded_registry()
    utterance = "schreib eine Mail an Christoph"
    assert manager._check_unsupported_intent(utterance) is None, (
        "mail-by-name must not be refused as unsupported"
    )
    assert manager._should_force_spawn(utterance) is False, (
        "mail-by-name must not force-spawn a contextless worker"
    )


def test_call_by_name_stays_router_tier() -> None:
    """'ruf Christoph an' must NOT be refused and must NOT spawn — it stays
    router-tier so the brain calls call-contact. Without the call-contact
    capability this utterance force-spawns a generic worker (the live bug)."""
    manager = _strict_manager_with_seeded_registry()
    utterance = "ruf Christoph an"
    assert manager._check_unsupported_intent(utterance) is None, (
        "call-by-name must not be refused as unsupported"
    )
    assert manager._should_force_spawn(utterance) is False, (
        "call-by-name must not force-spawn a contextless worker"
    )


def test_voice_save_contact_stays_router_tier() -> None:
    """'merk dir, Christophs Nummer ist …' must stay router-tier so the brain
    calls contact-upsert — never refused, never spawned."""
    manager = _strict_manager_with_seeded_registry()
    utterance = "merk dir, Christophs Nummer ist 0151 12345678"
    assert manager._check_unsupported_intent(utterance) is None
    assert manager._should_force_spawn(utterance) is False


@pytest.mark.parametrize(
    "utterance",
    [
        "ruf Christoph an",
        "ruf Laura an",
        "call Christoph",
    ],
)
def test_call_by_name_resolves_to_call_contact_capability(utterance: str) -> None:
    """The call-by-name surface resolves to the call-contact capability (not a
    generic worker), which is exactly what flips _is_generic_subagent_work from
    spawn to no-spawn."""
    from jarvis.core.capabilities import get_registry
    from jarvis.core.capabilities_seed import seed_registry

    reg = get_registry()
    seed_registry(reg)
    cap = reg.resolve_intent(utterance)
    assert cap is not None and cap.id == "tool.call-contact"
