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
from jarvis.ui.web.mission_inject import MISSION_INJECT_SOURCE_LAYER


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


class _FakeNavigateTool:
    name = "navigate"
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


# ---------------------------------------------------------------------------
# Greeting-prefixed QUESTION bug (live forensic 2026-06-10 23:13,
# data/jarvis_desktop.log): "Hey, what's the weather like today?" matched the
# smalltalk allowlist via the leading "hey"; the greeting-prefix guard only
# rescued action COMMANDS, so this information question stayed smalltalk, all
# tools (incl. search_web) were hidden, and the brain hit the anti-silence
# refusal. A greeting prefix must never change the classification of the
# remainder — a non-smalltalk remainder keeps the turn a real request, action
# verb or not.
# ---------------------------------------------------------------------------

GREETING_PREFIXED_QUESTIONS = [
    "Hey, what's the weather like today? Please give me an honest review and "
    "tell me what's the weather.",
    "Hey, what's the weather like today?",
    "Hallo, wie ist das Wetter morgen?",  # i18n-allow: German voice fixture
    "Hi, who won the match yesterday?",
]


@pytest.mark.parametrize("utterance", GREETING_PREFIXED_QUESTIONS)
def test_greeting_prefixed_question_is_not_smalltalk(utterance: str) -> None:
    """A greeting prefix in front of a real information question must NOT
    classify the turn as smalltalk — otherwise search_web & friends are hidden
    and the brain speaks the anti-silence refusal."""
    manager, _executor = _manager_with_spawn()
    assert manager._is_smalltalk(utterance) is False, (
        f"greeting-prefixed question {utterance!r} wrongly classified as smalltalk"
    )


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
    # Regression — Voice-Session 2026-05-11 17:22 (Bug-Report von Ruben):
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


@pytest.mark.asyncio
async def test_force_spawn_passes_turn_language() -> None:
    """Force-spawn must hand the turn language to the spawn tool so the
    spoken acknowledgement is composed in the user's language (2026-06-10
    dynamic spawn-announcement redesign)."""
    manager, executor = _manager_with_spawn()
    await manager._force_spawn_worker(SPAWN_INPUTS[0])  # German input
    _tool, args, _utt = executor.calls[0]
    assert args["language"] == "de"

    manager_en, executor_en = _manager_with_spawn()
    await manager_en._force_spawn_worker(
        "Spawn a subagent that checks the GitHub repo please"
    )
    _tool, args_en, _utt = executor_en.calls[0]
    assert args_en["language"] == "en"


@pytest.mark.asyncio
async def test_force_spawn_honours_reply_language_pin() -> None:
    """A pinned reply language (brain.reply_language) must override the
    utterance heuristic for the spoken spawn acknowledgement."""
    manager, executor = _manager_with_spawn()
    manager.set_reply_language("en")
    await manager._force_spawn_worker(SPAWN_INPUTS[0])  # German input
    _tool, args, _utt = executor.calls[0]
    assert args["language"] == "en"


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
    ([brain.worker].provider) providers set independently."""
    from jarvis.core.config import BrainTierConfig

    executor = _RecordingExecutor()
    config = JarvisConfig()
    config.brain.primary = brain_primary
    config.brain.routing.force_spawn_mode = force_spawn_mode
    config.brain.worker = (
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
    "brain_primary", ["mistral", "openai", "openai-codex", "openrouter", "ollama"]
)
def test_force_spawn_follows_worker_provider_not_talker(brain_primary: str) -> None:
    """With a configured claude-api worker, switching the talker to
    mistral/openai/codex must NOT block force-spawn — viability follows the WORKER,
    not the talker."""
    manager, _executor = _manager_with_worker_provider(
        brain_primary=brain_primary, worker_provider="claude-api",
    )
    assert manager._should_force_spawn("Bau eine Landingpage") is True, (
        f"talker={brain_primary!r} wrongly blocked force-spawn despite a viable "
        f"claude-api worker"
    )


def test_force_spawn_codex_worker_with_codex_talker() -> None:
    """A legacy Codex primary + Codex worker must still force-spawn."""
    manager, _executor = _manager_with_worker_provider(
        brain_primary="openai-codex", worker_provider="openai-codex",
    )
    assert manager._should_force_spawn("Bau eine Landingpage") is True


@pytest.mark.parametrize("brain_primary", ["mistral", "openai", "ollama"])
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


@pytest.mark.parametrize(
    "utterance",
    [
        # The exact live transcript (realtime voice turn 2026-07-14 09:05,
        # trace c82aa1a6): force-spawned a heavy mission that then FAILED,
        # while the realtime model hallucinated a notebook list.
        "Kannst du mir bitte mal gucken, alle all meine Notebooks auflisten?",
        "Liste bitte alle meine Notebooks auf.",
        "Zeig mir alle meine Notebooks",
        "Can you list all my notebooks?",
    ],
)
def test_mcp_covered_request_does_not_force_spawn(utterance: str) -> None:
    """A request an installed MCP tool covers stays INLINE in every supported
    language — the router/tool model calls the MCP tool directly; a mission
    spawn for a plain read-only lookup is the 2026-07-14 live bug. German
    phrasings used to slip past ``resolve_intent`` because MCP capability
    verbs were extracted from the English tool description only, while the
    generic CLI verb "gucken" tripped ``has_action_intent`` — together the
    exact ``_is_generic_subagent_work`` spawn predicate."""
    from jarvis.core.capabilities import Capability, get_registry
    from jarvis.mcp.adapter import _objects_from_tool_name, _verbs_from_description

    reg = get_registry()
    snapshot = dict(reg._caps)  # noqa: SLF001 — test fixture state restore
    reg.register(
        Capability(
            id="cli.gcloud",
            source="cli",
            verbs=("zeig", "list", "guck", "gucke", "gucken"),  # i18n-allow: input vocabulary
            objects=("gcp", "gcloud", "projekt", "projekte"),  # i18n-allow: input vocabulary
            description="Google Cloud CLI.",
            risk_tier="safe",
            requires_evidence=False,
        )
    )
    reg.register(
        Capability(
            id="mcp.notebooklm-mcp/notebook_list",
            source="mcp",
            verbs=_verbs_from_description(
                "List all notebooks in the user's NotebookLM account."
            ),
            objects=_objects_from_tool_name("notebooklm-mcp/notebook_list"),
            description="List all notebooks.",
            risk_tier="monitor",
            requires_evidence=True,
        )
    )
    try:
        manager, _executor = _manager_with_spawn(force_spawn_mode="strict")
        assert manager._should_force_spawn(utterance) is False, (
            f"MCP-covered request {utterance!r} wrongly force-spawned a worker"
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
    Without it the brain has no honest desktop path: spawn-worker runs in an
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


def test_mcp_tools_in_router_tools() -> None:
    """``mcp-tools`` must live in ROUTER_TOOLS (2026-06-18).

    The router can call any tool exposed by a connected MCP server (e.g.
    notebooklm-mcp) only when ``mcp-tools`` is in the frozenset — the
    loader filters entry-points against ROUTER_TOOLS. It is a virtual loader
    (MCPToolAdapter per MCP tool), default risk_tier ``monitor``, a direct
    safe/risk-gated action, **never a spawn**, so it never enters a worker
    tool-set (AP-5/AP-14). See ADR-0011 amendment "MCP-Tools Virtual Loader".
    """
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "mcp-tools" in ROUTER_TOOLS


def test_mcp_tools_is_router_only_not_a_spawn() -> None:
    """mcp-tools is a direct safe-gated loader; it must never become a
    spawn-style tool in a worker set (AP-5/AP-14, D9 recursion guard)."""
    import jarvis.brain.factory as factory_mod
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "mcp-tools" in ROUTER_TOOLS
    # Welle 4 deleted the Sub-Jarvis tier; no worker tool-set may resurrect.
    assert not hasattr(factory_mod, "SUB_TOOLS")


def test_factory_wires_mcp_tool_into_router_set() -> None:
    """End-to-end wiring: a connected MCP server's tool reaches the router schema.

    With a live MCPRegistry exposing a running client, ``_load_tools_for_tier``
    must expand the ``mcp-tools`` virtual loader so the slash-named MCP tool
    (``notebooklm-mcp/notebook_list``) appears in the router tool dict. This is
    the exact gap that made connected MCP servers uncallable on the voice path
    (voice session 2026-06-18): the server ran with 32 tools but none reached the
    brain. A missing entry-point, a missing ROUTER_TOOLS membership, or a broken
    loader would silently drop the tool here.
    """
    from jarvis.core import capabilities, runtime_refs

    class _FakeSpec:
        name = "notebooklm-mcp"

    class _FakeClient:
        spec = _FakeSpec()
        _tools_cache = [
            {"name": "notebook_list", "description": "List notebooks", "inputSchema": {}},
        ]

    class _FakeRegistry:
        def active_clients(self):
            return {"notebooklm-mcp": _FakeClient()}

    runtime_refs.set_mcp_registry(_FakeRegistry())
    try:
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

        assert "notebooklm-mcp/notebook_list" in tools
        tool = tools["notebooklm-mcp/notebook_list"]
        assert tool.name == "notebooklm-mcp/notebook_list"
        assert tool.risk_tier == "monitor"
    finally:
        runtime_refs._reset_for_tests()
        capabilities._reset_registry_for_tests()


def test_router_prompt_mentions_plugin_inline_reads():
    from jarvis.brain.router import SYSTEM_PROMPT

    low = SYSTEM_PROMPT.lower()
    assert "plugin" in low
    assert "spawn_worker" in low or "spawn-worker" in low


def test_apply_plugin_relevance_drops_cardless_mcp_on_unrelated_turn() -> None:
    """Manager-level guard for the over-trigger: a connected MCP server with no
    usage card (notebooklm-mcp) must not be exposed to the router on a turn that
    does not signal it. Forensic (voice session): a plain flight question
    reflexively fired ``notebooklm-mcp/chat_configure``, wasting ~35s before the
    turn timed out. Keyword-only relevance gate (AP-9), provider-agnostic.
    """

    class _T:
        def __init__(self, name: str) -> None:
            self.name = name

    mgr = BrainManager.__new__(BrainManager)
    tools = {
        "search_web": _T("search_web"),
        "notebooklm-mcp/chat_configure": _T("notebooklm-mcp/chat_configure"),
        "notebooklm-mcp/notebook_list": _T("notebooklm-mcp/notebook_list"),
    }
    out = mgr._apply_plugin_relevance(
        "Was ist der kürzeste Flug von München nach Bora Bora?", tools
    )
    assert "search_web" in out  # native tool kept
    assert not any(n.startswith("notebooklm-mcp/") for n in out)

    # Same MCP server, now explicitly named -> kept (the clear keep case).
    named = mgr._apply_plugin_relevance(
        "frag das NotebookLM nach meinen Quellen", tools
    )
    assert "notebooklm-mcp/notebook_list" in named


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


def test_factory_wires_registry_command_tools_into_router_set() -> None:
    """End-to-end wiring for the Command-Registry tools: entry-point +
    ROUTER_TOOLS gate the `app-command` VIRTUAL LOADER, which expands into one
    flat tool per registry command (forensic 2026-07-11: the earlier umbrella
    tool failed live — the LLM called `provider-test` as a tool name). A
    missing entry-point line or a failing registry import would silently drop
    every command from the router schema."""
    from jarvis.brain.factory import _load_tools_for_tier
    from jarvis.commands.registry import get_registry

    tools = _load_tools_for_tier(
        "router",
        bus=EventBus(),
        executor=None,
        harness_manager=None,
        user_profile=None,
        people=None,
        config=JarvisConfig(),
    )

    # The loader itself never reaches the LLM tool set — only its expansion.
    assert "app-command" not in tools
    for cmd in get_registry():
        assert cmd.id in tools, f"registry command {cmd.id} missing from router set"
        assert tools[cmd.id].risk_tier == ("ask" if cmd.dangerous else "monitor")
    # The exact live failure: `provider-test` must BE a callable tool now.
    assert "provider-test" in tools


def test_factory_native_tool_wins_registry_name_collision_in_any_entrypoint_order(
    monkeypatch,
) -> None:
    """Native tools deterministically outrank same-named virtual commands."""
    import importlib.metadata

    from jarvis.brain.factory import _load_tools_for_tier
    from jarvis.plugins.tool.app_command import AppCommandTool
    from jarvis.plugins.tool.wiki_ingest import WikiIngestTool

    class FakeEntryPoint:
        def __init__(self, name, target):
            self.name = name
            self._target = target

        def load(self):
            return self._target

    app_command = FakeEntryPoint("app-command", AppCommandTool)
    wiki_ingest = FakeEntryPoint("wiki-ingest", WikiIngestTool)

    for ordered in (
        [app_command, wiki_ingest],
        [wiki_ingest, app_command],
    ):
        monkeypatch.setattr(
            importlib.metadata,
            "entry_points",
            lambda *, group, ordered=ordered: list(ordered),
        )
        tools = _load_tools_for_tier(
            "router",
            bus=EventBus(),
            executor=None,
            harness_manager=None,
            user_profile=None,
            people=None,
            config=JarvisConfig(),
        )

        assert type(tools["wiki-ingest"]) is WikiIngestTool
        assert "session-latest-turn" in tools


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


def test_dispatch_to_harness_not_in_router_tools() -> None:
    """``dispatch-to-harness`` must NOT be an LLM-visible router tool.

    Phantom-harness regression (forensic 2026-06-28): the tool's raw ``harness``
    parameter let the brain request the retired ``harness="openclaw"`` — an
    unregistered harness (Welle-4 removal) — which surfaced a raw "Harness not available"
    KeyError to voice. Heavy sub-agent work is ``spawn-worker``; live desktop
    work is ``computer-use``. The tool class still exists for the INTERNAL
    local-action fast path, but it must never be router-selectable again.
    """
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "dispatch-to-harness" not in ROUTER_TOOLS


@pytest.mark.asyncio
async def test_subagent_request_forces_spawn_worker() -> None:
    """An explicit "start a subagent" request forces spawn_worker, never a harness.

    Structural guarantee #2 (the #1 guarantee is that dispatch-to-harness is no
    longer router-visible): a cleanly recognised subagent trigger must route to
    spawn_worker deterministically, before any free LLM tool choice.
    """
    manager, executor = _manager_with_spawn()
    utterance = "Starte einen Subagenten, der mir einen Lernzettel schreibt"

    assert manager._should_force_spawn(utterance), (
        "explicit subagent request did not trigger the force-spawn heuristic"
    )
    result = await manager._force_spawn_worker(utterance)
    assert result is not None
    assert len(executor.calls) == 1, (
        f"expected exactly one spawn call, got {len(executor.calls)}"
    )
    tool, _args, user_utterance = executor.calls[0]
    assert tool.name == "spawn_worker"
    assert user_utterance == utterance  # verbatim, never paraphrased


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
    ist nach der Jarvis-Agent-Bridge-Migration geloescht (siehe
    docs/jarvis-agents-bridge.md §11) — Recursion-Schutz wird jetzt auf
    Mission-Manager-Ebene durchgesetzt (Worker hat keinen Spawn-Tool-
    Zugriff).
    """
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "spawn-worker" in ROUTER_TOOLS


def test_multi_spawn_not_in_router_tools() -> None:
    """The dead legacy fan-out tool must not compete with spawn-worker."""
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "multi-spawn" not in ROUTER_TOOLS


def test_dispatch_with_review_not_in_router_tools() -> None:
    """Review belongs to the canonical Mission Manager lifecycle."""
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "dispatch-with-review" not in ROUTER_TOOLS


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


# ---------------------------------------------------------------------------
# Opinion / advice / recommendation / decision questions are CONVERSATION, not
# work — they must be answered inline, NEVER force-spawned to a worker, even
# when they contain an everyday word that collides with an action verb in the
# universal catalogue. Live bug 2026-06-19 (voice session 11:53, San-Francisco
# emigration turn): "Hey du, ich hab ne Frage ... was würdest du mir empfehlen?"
# force-spawned a worker because has_action_intent matched the NOUN "Frage"
# (-> verb "frag"/"frage") and the FILLER particle "halt" (-> verb "halt"), so
# _is_generic_subagent_work classified a pure chat turn as generic sub-agent
# work. The answer then returned out-of-band via the MissionAnnouncer (and never
# reached the session transcript).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        # the real bug utterance (abbreviated) — "Frage" + "möchte" + advice ask
        "Hey du, ich hab ne Frage, ich möchte auswandern. Was würdest du mir empfehlen?",
        # noun "Frage" collides with the action verb "frag"
        "Ich hab da mal eine Frage: was hältst du davon?",
        # filler particle "halt" collides with the action verb "halt"
        "Ich hab mir das halt echt überlegt, was meinst du dazu?",
        # Live bug 2026-07-16 (voice session 11:49): adjectives between the
        # article and "Frage" blinded the opener guard, the noun tripped
        # has_action_intent, and a one-search knowledge question dispatched a
        # full Opus mission ("Da kümmert sich gerade ein Jarvis Agent drum").
        "Du, ich hab mal eine ganz generelle Frage, wie viel Geld ähm hat "
        "eigentlich Elon Musk gerade aktuell?",
    ],
)
def test_opinion_advice_questions_do_not_force_spawn(utterance: str) -> None:
    """Opinion/advice questions are talk, not work — answered inline even when an
    everyday word ('Frage' -> 'frag', filler 'halt') collides with an action
    verb (live bug 2026-06-19, emigration turn). Reproduces the real strict-mode
    path with a seeded registry, where has_action_intent fires and
    _is_generic_subagent_work would otherwise force-spawn."""
    from jarvis.core.capabilities import get_registry
    from jarvis.core.capabilities_seed import seed_registry

    reg = get_registry()
    snapshot = dict(reg._caps)  # noqa: SLF001 — test fixture state restore
    seed_registry(reg)
    try:
        manager, _executor = _manager_with_spawn(force_spawn_mode="strict")
        assert manager._should_force_spawn(utterance) is False, (
            f"opinion/advice question {utterance!r} wrongly force-spawned a worker"
        )
    finally:
        reg._caps.clear()  # noqa: SLF001
        reg._caps.update(snapshot)  # noqa: SLF001


@pytest.mark.parametrize(
    "utterance",
    [
        # advice / recommendation (DE)
        "Was würdest du mir empfehlen?",
        "Was rätst du mir?",
        # opinion (DE)
        "Was hältst du davon?",
        "Wie siehst du das?",
        "Was ist deine Meinung dazu?",
        # decision help (DE)
        "Soll ich nach San Francisco oder nach Melbourne ziehen?",
        # conversational opener (DE)
        "Ich hab da mal eine Frage.",
        # advice / opinion (EN)
        "What would you recommend?",
        "Should I move to San Francisco or Melbourne?",
        "I have a question.",
        # advice / opinion (ES)
        "¿Qué me recomiendas?",
        "Tengo una pregunta.",
    ],
)
def test_opinion_advice_predicate_recognises_questions(utterance: str) -> None:
    """The predicate flags opinion/advice/decision questions across de/en/es."""
    from jarvis.brain.manager import _is_opinion_advice_question

    assert _is_opinion_advice_question(utterance) is True, (
        f"opinion/advice question {utterance!r} not recognised"
    )


@pytest.mark.parametrize(
    "utterance",
    [
        "Bau mir eine Landingpage.",
        "öffne Chrome.",
        "Lies die Datei jarvis.toml.",
        "Installier Notepad++.",
        "Mach einen Screenshot.",
    ],
)
def test_opinion_advice_predicate_ignores_commands(utterance: str) -> None:
    """Genuine action commands are NOT opinion questions — they still spawn."""
    from jarvis.brain.manager import _is_opinion_advice_question

    assert _is_opinion_advice_question(utterance) is False, (
        f"action command {utterance!r} wrongly matched the opinion-question guard"
    )


@pytest.mark.parametrize(
    "utterance",
    [
        # Verb-noun collisions the noun masking does NOT cover ("Buch" ->
        # catalogue verb "buch", "Post" -> "post" after a masculine
        # determiner): the question form itself must stand the generic-work
        # force-spawn down.
        "Kannst du mir sagen, was in dem Buch steht?",
        "Wer hat diesen Post geschrieben?",
    ],
)
def test_question_form_never_reaches_generic_work_spawn(utterance: str) -> None:
    """A turn in QUESTION form is conversation — the strict-mode generic-work
    path (has_action_intent + no capability) must never force-spawn it (live
    bug 2026-07-16, voice session 11:49: a plain knowledge question dispatched
    a full worker). Explicit triggers and artifact builds are checked before
    this stand-down and still spawn."""
    from jarvis.core.capabilities import get_registry
    from jarvis.core.capabilities_seed import seed_registry

    reg = get_registry()
    snapshot = dict(reg._caps)  # noqa: SLF001 — test fixture state restore
    seed_registry(reg)
    try:
        manager, _executor = _manager_with_spawn(force_spawn_mode="strict")
        assert manager._should_force_spawn(utterance) is False, (
            f"question-form turn {utterance!r} wrongly force-spawned a worker"
        )
    finally:
        reg._caps.clear()  # noqa: SLF001
        reg._caps.update(snapshot)  # noqa: SLF001


def test_explicit_trigger_question_still_spawns() -> None:
    """Naming the vehicle stays unambiguous even in question form — the AD-S9
    hoist runs BEFORE the question-form stand-down (user mandate 2026-06-15)."""
    from jarvis.core.capabilities import get_registry
    from jarvis.core.capabilities_seed import seed_registry

    reg = get_registry()
    snapshot = dict(reg._caps)  # noqa: SLF001 — test fixture state restore
    seed_registry(reg)
    try:
        manager, _executor = _manager_with_spawn(force_spawn_mode="strict")
        utterance = (
            "Kannst du einen Subagenten spawnen, der recherchiert, wie viel "
            "Geld Elon Musk gerade hat?"
        )
        assert manager._should_force_spawn(utterance) is True, (
            "an explicitly named vehicle must spawn even in question form"
        )
    finally:
        reg._caps.clear()  # noqa: SLF001
        reg._caps.update(snapshot)  # noqa: SLF001


@pytest.mark.parametrize(
    "utterance",
    [
        "Ich hab mir das halt echt überlegt, weil das so kompliziert ist.",
        "Das ist bei mir zuhause halt einfach immer so gewesen.",
    ],
)
def test_filler_particle_halt_does_not_force_spawn(utterance: str) -> None:
    """The German discourse particle 'halt' is a filler, not a stop command — a
    pure statement carrying it (and no real action verb) must NOT force-spawn a
    worker. Live bug 2026-06-19: 'halt' tripped has_action_intent ->
    _is_generic_subagent_work in the strict-mode force-spawn gate."""
    from jarvis.core.capabilities import get_registry
    from jarvis.core.capabilities_seed import seed_registry

    reg = get_registry()
    snapshot = dict(reg._caps)  # noqa: SLF001 — test fixture state restore
    seed_registry(reg)
    try:
        manager, _executor = _manager_with_spawn(force_spawn_mode="strict")
        assert manager._should_force_spawn(utterance) is False, (
            f"filler-only utterance {utterance!r} wrongly force-spawned a worker"
        )
    finally:
        reg._caps.clear()  # noqa: SLF001
        reg._caps.update(snapshot)  # noqa: SLF001


# ---------------------------------------------------------------------------
# Explicit spawn DECLINE — the user literally says "don't spawn a subagent /
# talk to me directly". The explicit-trigger hoist in _should_force_spawn is
# negation-blind: it substring-matches the trigger word ("Subagent"/"spawn")
# and force-spawns, doing the exact OPPOSITE of what the user asked. A decline
# must be a HARD stand-down that overrides the hoist. Live bug 2026-06-19
# (voice session 18:41, Turn 2): "Nee, ich möchte, dass du keinen Subagent
# dafür spawnst. Ich möchte, dass du direkt mit mir sprichst." -> force-spawn
# match='Subagent'.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        # the real Turn-2 utterance
        "Nee, ich möchte, dass du keinen Subagent dafür spawnst. "
        "Ich möchte, dass du direkt mit mir sprichst.",
        # negated subagent / spawn (DE)
        "Spawn bitte keinen Subagenten.",
        "Bitte keinen Sub-Agent dafür.",
        "Ich will nicht, dass du das spawnst.",
        "Mach das ohne Subagent.",
        # talk-to-me-directly (DE)
        "Sprich einfach direkt mit mir.",
        "Antworte mir direkt.",
        # EN
        "Don't spawn a subagent.",
        "No subagent please, just talk to me.",
        "Talk to me directly.",
        # ES
        "No uses un subagente.",
        "Háblame directamente.",
    ],
)
def test_spawn_decline_predicate_recognises_declines(utterance: str) -> None:
    """The predicate flags explicit 'do not spawn / talk to me directly' across
    de/en/es so the negation-blind trigger hoist cannot override the user."""
    from jarvis.brain.manager import _is_spawn_decline

    assert _is_spawn_decline(utterance) is True, (
        f"spawn decline {utterance!r} not recognised"
    )


@pytest.mark.parametrize(
    "utterance",
    [
        # explicit spawn REQUESTS must NOT be read as declines
        "Spawne einen Subagenten und sag mir, was du empfehlen würdest.",
        "Starte einen Sub-Agent für die Recherche.",
        "Spawn a subagent and tell me what you'd recommend.",
        "Delegiere das an einen Worker.",
        # compound: a directness preamble that STILL asks for a spawn in the
        # same clause must NOT be swallowed (review MAJOR-1).
        "Just tell me, spawn a subagent to analyse the logs.",
        "Just answer me — delegate this to a worker.",
        # plain commands with no spawn token at all
        "Bau mir eine Landingpage.",
        "öffne Chrome.",
    ],
)
def test_spawn_decline_predicate_ignores_requests(utterance: str) -> None:
    """A genuine spawn request (or a plain command) is NOT a decline — it must
    still be allowed to spawn."""
    from jarvis.brain.manager import _is_spawn_decline

    assert _is_spawn_decline(utterance) is False, (
        f"spawn request {utterance!r} wrongly matched the decline guard"
    )


@pytest.mark.parametrize(
    "utterance",
    [
        "Nee, ich möchte, dass du keinen Subagent dafür spawnst. "
        "Ich möchte, dass du direkt mit mir sprichst.",
        "Spawn bitte keinen Subagenten, antworte mir einfach direkt.",
        "Don't spawn a subagent, just talk to me.",
    ],
)
def test_spawn_decline_overrides_explicit_trigger_hoist(utterance: str) -> None:
    """A turn that NAMES the vehicle ('Subagent'/'spawn') but NEGATES it must
    NOT force-spawn — the decline guard overrides the explicit-trigger hoist.
    Reproduces the live strict-mode path with a seeded registry."""
    from jarvis.core.capabilities import get_registry
    from jarvis.core.capabilities_seed import seed_registry

    reg = get_registry()
    snapshot = dict(reg._caps)  # noqa: SLF001 — test fixture state restore
    seed_registry(reg)
    try:
        manager, _executor = _manager_with_spawn(force_spawn_mode="strict")
        assert manager._should_force_spawn(utterance) is False, (
            f"explicit spawn DECLINE {utterance!r} wrongly force-spawned a worker"
        )
    finally:
        reg._caps.clear()  # noqa: SLF001
        reg._caps.update(snapshot)  # noqa: SLF001


def test_explicit_trigger_with_coaching_framing_still_spawns() -> None:
    """The explicit-trigger hoist runs BEFORE the coaching guard, so a turn that
    NAMES the vehicle ('Spawne einen Subagenten') AND carries a coaching framing
    still force-spawns as asked — the user's explicit request wins. Pins the
    ordering invariant (review MINOR-3)."""
    from jarvis.brain.manager import _is_spawn_decline
    from jarvis.core.capabilities import get_registry
    from jarvis.core.capabilities_seed import seed_registry

    utterance = "Spawne einen Subagenten, der mir hilft, besser zu fragen."
    assert _is_spawn_decline(utterance) is False, (
        "an explicit spawn request must not be read as a decline"
    )
    reg = get_registry()
    snapshot = dict(reg._caps)  # noqa: SLF001 — test fixture state restore
    seed_registry(reg)
    try:
        manager, _executor = _manager_with_spawn(force_spawn_mode="strict")
        assert manager._should_force_spawn(utterance) is True, (
            "explicit 'Spawne einen Subagenten ...' must still force-spawn even "
            "with a coaching framing — the trigger hoist precedes the coaching guard"
        )
    finally:
        reg._caps.clear()  # noqa: SLF001
        reg._caps.update(snapshot)  # noqa: SLF001


# ---------------------------------------------------------------------------
# Conversational coaching — "help me [get better at a soft / cognitive /
# conversational skill]" is CONVERSATION, answered inline (Jarvis asks the
# user smart questions), NEVER a heavy-worker spawn. It trips the action-verb
# catalogue when the coaching object is itself a verb ("intelligent zu fragen"
# -> "frag"/"frage"). Live bug 2026-06-19 (voice session 18:41, Turn 1):
# "Hilf mir aber dabei, intelligent zu fragen. Für mich ist Fragen einer der
# Schlüssel für Erfolg, verstehst du?" -> matched action verbs ['frag','frage']
# -> has_action_intent -> _is_generic_subagent_work -> force-spawn.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        # the real Turn-1 utterance (umlaut + ASCII STT variants)
        "Hilf mir aber dabei, intelligent zu fragen. Für mich ist Fragen "
        "einer der Schlüssel für Erfolg, verstehst du?",
        "Hilf mir aber dabei, intelligent zu fragen. Fuer mich ist Fragen "
        "einer der Schluessel fuer Erfolg, verstehst du?",
        # other coaching phrasings (DE)
        "Hilf mir, bessere Fragen zu stellen.",
        "Hilf mir dabei, klarer zu denken.",
        "Bring mir bei, mich besser auszudrücken.",
        # EN
        "Help me ask better questions.",
        "Teach me to think more clearly.",
        # ES (review MINOR-1: sibling guards cover es, this one must too)
        "Ayúdame a formular mejores preguntas.",
        "Enséñame a pensar con más claridad.",
    ],
)
def test_conversational_coaching_predicate_recognises(utterance: str) -> None:
    """The predicate flags 'help me get better at a conversational / cognitive
    skill' coaching requests across de/en."""
    from jarvis.brain.manager import _is_conversational_coaching

    assert _is_conversational_coaching(utterance) is True, (
        f"coaching request {utterance!r} not recognised"
    )


@pytest.mark.parametrize(
    "utterance",
    [
        # genuine artifact / action work — NOT conversational coaching
        "Hilf mir, eine E-Mail an Anna zu schreiben und zu senden.",
        "Hilf mir, den Bug in der Pipeline zu fixen.",
        "Bau mir eine Landingpage.",
        "Help me build a landing page.",
    ],
)
def test_conversational_coaching_predicate_ignores_real_work(utterance: str) -> None:
    """A 'help me' request whose object is a concrete artifact / action is real
    work — it must NOT match the coaching guard."""
    from jarvis.brain.manager import _is_conversational_coaching

    assert _is_conversational_coaching(utterance) is False, (
        f"real-work request {utterance!r} wrongly matched the coaching guard"
    )


@pytest.mark.parametrize(
    "utterance",
    [
        "Hilf mir aber dabei, intelligent zu fragen. Für mich ist Fragen "
        "einer der Schlüssel für Erfolg, verstehst du?",
        "Hilf mir, bessere Fragen zu stellen.",
        "Help me ask better questions.",
    ],
)
def test_conversational_coaching_does_not_force_spawn(utterance: str) -> None:
    """Conversational coaching is talk, not work — answered inline even when the
    coaching object collides with an action verb ('fragen' -> 'frag'/'frage').
    Reproduces the real strict-mode path with a seeded registry."""
    from jarvis.core.capabilities import get_registry
    from jarvis.core.capabilities_seed import seed_registry

    reg = get_registry()
    snapshot = dict(reg._caps)  # noqa: SLF001 — test fixture state restore
    seed_registry(reg)
    try:
        manager, _executor = _manager_with_spawn(force_spawn_mode="strict")
        assert manager._should_force_spawn(utterance) is False, (
            f"coaching request {utterance!r} wrongly force-spawned a worker"
        )
    finally:
        reg._caps.clear()  # noqa: SLF001
        reg._caps.update(snapshot)  # noqa: SLF001


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
async def test_internal_realtime_reply_can_defer_response_event_to_session() -> None:
    """Realtime delegation may suppress only its internal brain reply event."""
    bus = EventBus()
    seen: list[ResponseGenerated] = []

    async def _capture(ev: ResponseGenerated) -> None:
        seen.append(ev)

    bus.subscribe(ResponseGenerated, _capture)
    manager, _executor = _manager_with_local_actions_and_bus(bus)

    internal_result = await manager.generate("Open Spotify", publish_response=False)
    classic_result = await manager.generate("Open Calculator")

    assert internal_result == "ok"
    assert classic_result == "ok"
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
    # CU missions get a generous OUTER backstop (>=180 s) so a multi-step mission
    # is not aborted by the 30 s harness_timeout_s; the harness has its own
    # per-step + step-budget guards, and the mission is offloaded so the longer
    # cap never blocks the spoken turn (2026-06-09 general-CU restore).
    assert args["timeout_s"] == max(
        manager._config.local_action.harness_timeout_s, 180.0
    )
    assert args["timeout_s"] >= 180.0
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
    """ROUTER_TOOLS matches the exact model-visible surface in ADR-0011.

    Heavy work has one delegation action: ``spawn-worker``. The retired
    ``dispatch-to-harness``, ``multi-spawn``, and ``dispatch-with-review``
    paths must remain absent. Direct external-system actions are admitted only
    through capability-gated flat loaders, and no worker receives a supervisor
    spawn action. Every extension requires an ADR-0011 amendment plus updates
    to this exact-set guard and ``test_recursive_tools_only_in_router``.
    """
    from jarvis.brain.factory import ROUTER_TOOLS

    expected = frozenset(
        {
            # Mandat-Phase-3 Baseline (Master-Plan §22)
            "run-shell",
            "screen-snapshot",
            "spawn-worker",
            # NB: ``dispatch-to-harness`` was REMOVED from the LLM-visible router
            # set on 2026-06-28 (ADR-0011 amendment "dispatch-to-harness removal").
            # Its raw ``harness`` param let the brain request a phantom, retired
            # ``harness="openclaw"`` (unregistered, Welle-4 removal), surfacing a
            # raw "Harness not available" KeyError to voice. Heavy work →
            # spawn-worker, desktop → computer-use. It must NOT reappear here;
            # the negative guard ``test_dispatch_to_harness_not_in_router_tools``
            # pins that.
            # AI Pointer (pull path): resolve the element under the mouse cursor
            # via the OS accessibility tree. Read-only safe-tier, direct action,
            # never a spawn (AP-5/AP-14). See docs/plans/ai-pointer/DESIGN.md.
            "inspect-pointer",
            # UI navigation (2026-06-02): switch the active sidebar section by
            # voice/chat. Pure UI action (risk safe), publishes NavigateSidebar,
            # never a spawn (AP-5/AP-14). See ADR-0011 amendment "Navigate tool".
            "navigate",
            # Command Registry executor (2026-07-09): one curated app command
            # through the same REST endpoint the UI uses (in-process ASGI).
            # Enum-constrained + schema-validated; dangerous -> risk "ask".
            # Never a spawn (AP-5/AP-14). ADR-0011 amendment "app-command tool".
            "app-command",
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
            # Grounded vault listing (2026-07-14): ground-truth answer for
            # "what is in my wiki" in one round. Read-only, never a spawn.
            # ADR-0011 amendment "wiki-list tool".
            "wiki-list",
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
            # Google Calendar Marketplace plugin (2026-06-27): native bridge tool
            # whose bot logic is a Node script (calendar_bot.mjs). Same rationale
            # as gmail — no MCP server block, so it must be router-visible
            # directly. Full autonomy (writes are monitor, never ask).
            "google_calendar",
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
            # Inline web search (2026-06-10, user mandate): the router answers
            # news/knowledge/research QUESTIONS inline instead of spawning a
            # multi-minute worker mission for a single lookup. Read-only
            # DuckDuckGo call, risk safe, never a spawn (AP-5/AP-14). See
            # ADR-0011 amendment "Inline web search".
            "search-web",
            # MCP servers as live brain tools (2026-06-18): virtual loader that
            # expands to one MCPToolAdapter per tool of every connected and
            # running MCP server. Reads client._tools_cache synchronously —
            # no network I/O. Default risk_tier "monitor". Router-tier only —
            # never a spawn (AP-5/AP-14). See ADR-0011 amendment
            # "MCP-Tools Virtual Loader".
            "mcp-tools",
        }
    )
    assert ROUTER_TOOLS == expected, (
        f"ROUTER_TOOLS {sorted(ROUTER_TOOLS)} weicht ab vom erwarteten "
        f"{sorted(expected)}. Persona-Mandat Phase 3 + Master-Plan §22 + "
        "ADR-0011 (inkl. Phase-7/8/Awareness-Erweiterungen + Welle-4-Migration). "
        "Direkt-Aktionen wie open_app/type_text/whoami DUERFEN NICHT "
        "hinzu — die gehoeren an die Jarvis-Agent-Bridge. Sanktionierte Ausnahmen "
        "sind deterministische Tools mit eigenem ADR-0011-Eintrag "
        "(wiki-ingest, update-profile, search-web)."
    )


def test_search_web_in_router_tools() -> None:
    """``search-web`` must live in ROUTER_TOOLS (2026-06-10 user mandate).

    A news/knowledge question ("was sind die aktuellsten News?") must be
    answerable INLINE by the router. Without this entry the router has no
    web path at all and the system prompt's research doctrine degenerates
    into "spawn a worker mission for every question" — the exact
    over-spawning the user reported. See ADR-0011 amendment "Inline web
    search".
    """
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "search-web" in ROUTER_TOOLS


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
        result = manager._check_unsupported_intent("Schick bitte eine E-Mail an Harald")
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
            "schick eine Email an Harald",
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
        return "## Ueber den User\n- **Name:** Ruben"


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
        "Schick eine Email an harald@example.com mit dem Betreff Hallo",
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


# ---------------------------------------------------------------------------
# Heavy-research force-spawn (live bug 2026-06-14, a long-haul trip-research turn):
# a multi-step research/analysis request must OFFLOAD to a background mission
# instead of running inline on the deep brain (where it blew the ~20 s voice
# budget and was beheaded → silence). Conjunctive gate: a research/analysis
# VERB must be present AND a heaviness signal. Length alone never spawns, so a
# quick "recherchier das mal kurz" stays inline.
# ---------------------------------------------------------------------------

_HEAVY_RESEARCH_SHOULD_SPAWN = [
    # The live failure (two research verbs + horizon marker + length).
    (
        "Ich möchte, dass du mir dabei hilfst, zu recherchieren, was ich für "
        "eine Reise von Lissabon nach Tokio brauche, und analysiere auch den "
        "Wetterbericht der nächsten zwei Wochen."
    ),
    # Two verbs (analysieren + vergleichen) → multi-clause.
    "Analysiere die letzten zwölf Monate meiner Ausgaben und vergleiche sie mit dem Vorjahr",
    # English: two verbs + horizon marker.
    "Research the top five vector databases and compare them over the next quarter",
    # One verb + requirements marker ("brauche").
    "Recherchiere ausführlich, was ich für meinen Umzug in eine andere Stadt alles brauche",
]

_HEAVY_RESEARCH_STAYS_INLINE = [
    "Was ist das Wetter in Melbourne?",  # no research verb → fast lookup
    "Wie spät ist es in Sydney?",  # no research verb
    "Recherchier das mal kurz",  # verb but no scope (short, 1 verb, no marker)
    "Analysier kurz den Satz",  # verb but no scope
    "Wie geht's dir?",  # smalltalk, no verb
]


@pytest.mark.parametrize("utterance", _HEAVY_RESEARCH_SHOULD_SPAWN)
def test_is_heavy_research_should_spawn(utterance: str) -> None:
    manager, _ = _manager_with_spawn(force_spawn_mode="strict")
    assert manager._is_heavy_research(utterance) is True


@pytest.mark.parametrize("utterance", _HEAVY_RESEARCH_STAYS_INLINE)
def test_is_heavy_research_stays_inline(utterance: str) -> None:
    manager, _ = _manager_with_spawn(force_spawn_mode="strict")
    assert manager._is_heavy_research(utterance) is False


# ---------------------------------------------------------------------------
# User mandate (2026-06-15): "When I say subagent, it HAS to spawn a subagent."
#
# An EXPLICIT heavy-work trigger ("subagent", "spawn", "jarvis-agent",
# "openclaw" legacy alias, "delegate", …) names the execution vehicle — it is
# an UNAMBIGUOUS spawn request and must outrank the disambiguation guards
# that exist only to suppress AMBIGUOUS, implicit spawns: the
# instructional/pointer/navigation/smalltalk/open-app
# guards in ``_should_force_spawn`` AND, end-to-end through ``generate()``, the
# capability "I can't do that" refusal and the navigation fast-path. The bug:
# those guards were checked BEFORE the explicit-trigger check, so a phrasing
# that also tripped one of them was silently NOT spawned ("sometimes saying
# subagent doesn't spawn a subagent").
# ---------------------------------------------------------------------------

# Each of these contains an explicit trigger AND trips a disambiguation guard
# that today returns False before the trigger is ever evaluated:
#   - "Starte/Öffne OpenClaw"  → is_open_app_intent (start\w*/open\w* + no veto)
#   - subagent + "zeig … Socials" → match_navigation_intent (section hit)
_EXPLICIT_TRIGGER_OVERRIDES_GUARDS = [
    "Starte OpenClaw",
    "Öffne OpenClaw",  # i18n-allow: German voice fixture (routing content under test)
    "Spawne einen Subagenten und zeig mir die Socials",  # i18n-allow: German voice fixture
    "Kannst du einen Subagenten spawnen und mir die Socials zeigen?",  # i18n-allow
]


@pytest.mark.parametrize("utterance", _EXPLICIT_TRIGGER_OVERRIDES_GUARDS)
def test_explicit_trigger_outranks_disambiguation_guards(utterance: str) -> None:
    """An explicit force-spawn trigger must force-spawn even when the utterance
    also looks like an app-open or a UI-navigation command. The disambiguation
    guards only suppress AMBIGUOUS implicit spawns — naming the vehicle is
    unambiguous (User mandate 2026-06-15)."""
    manager, _ = _manager_with_spawn(force_spawn_mode="strict")
    assert manager._should_force_spawn(utterance) is True, (
        f"explicit-trigger utterance {utterance!r} did NOT force-spawn — a "
        "disambiguation guard swallowed the explicit request"
    )


def _seeded_strict_manager_with_local_actions() -> tuple[BrainManager, _RecordingExecutor]:
    """Strict-mode manager over the REAL seeded registry, with spawn_worker AND
    the local-action tools wired — the exact production gate path for the
    end-to-end ``generate()`` mandate tests."""
    from jarvis.core.capabilities import get_registry
    from jarvis.core.capabilities_seed import seed_registry

    seed_registry(get_registry())
    executor = _RecordingExecutor()
    config = JarvisConfig()
    config.brain.routing.force_spawn_mode = "strict"
    manager = BrainManager(
        config=config,
        bus=EventBus(),
        tools={"spawn_worker": _FakeTool()},
        local_action_tools={
            "open_app": _FakeOpenAppTool(),
            "type_text": _FakeTypeTextTool(),
            "hotkey": _FakeHotkeyTool(),
        },
        tool_executor=executor,  # type: ignore[arg-type]
    )
    manager._vision_provider = _VisionShouldNotRun()
    return manager, executor


def _spawn_calls(executor: _RecordingExecutor) -> list[Any]:
    return [c for c in executor.calls if getattr(c[0], "name", "") == "spawn_worker"]


# Explicit subagent requests whose TASK also needs an external integration the
# worker cannot reach (book a trip, send mail). Today the capability gate
# refuses them ("Das kann ich noch nicht") BEFORE force-spawn, so the explicit
# "subagent" mention never spawns. Per the mandate, the explicit trigger wins:
# spawn the universal worker (it does its best / reports honestly).
_EXPLICIT_SUBAGENT_OVER_REFUSAL = [
    "Spawn a subagent to book a trip to Berlin",
    "Spawne einen Subagenten und schick eine Email an meinen Chef",  # i18n-allow
]


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", _EXPLICIT_SUBAGENT_OVER_REFUSAL)
async def test_explicit_subagent_spawns_over_capability_refusal(utterance: str) -> None:
    """End-to-end through ``generate()``: an explicit subagent request must
    dispatch spawn_worker even when the task looks like an unsupported external
    integration — it must NOT be swallowed by the capability refusal gate."""
    manager, executor = _seeded_strict_manager_with_local_actions()
    await manager.generate(utterance)
    assert _spawn_calls(executor), (
        f"explicit subagent request {utterance!r} did NOT spawn a worker — the "
        "capability refusal gate swallowed it"
    )


@pytest.mark.asyncio
async def test_explicit_subagent_outranks_navigation_fast_path() -> None:
    """End-to-end through ``generate()``: a nav-tail combo that names an explicit
    subagent trigger ('Spawne einen Subagenten UND zeig mir die Socials') must
    dispatch spawn_worker — the deterministic navigation fast-path must stand
    down for an explicit trigger (mirrors AD-S9: the named vehicle wins)."""
    from jarvis.core.capabilities import get_registry
    from jarvis.core.capabilities_seed import seed_registry

    seed_registry(get_registry())
    executor = _RecordingExecutor()
    config = JarvisConfig()
    config.brain.routing.force_spawn_mode = "strict"
    manager = BrainManager(
        config=config,
        bus=EventBus(),
        tools={"spawn_worker": _FakeTool(), "navigate": _FakeNavigateTool()},
        local_action_tools={"open_app": _FakeOpenAppTool()},
        tool_executor=executor,  # type: ignore[arg-type]
    )
    manager._vision_provider = _VisionShouldNotRun()
    await manager.generate(
        "Spawne einen Subagenten und zeig mir die Socials"  # i18n-allow: German voice fixture
    )
    assert _spawn_calls(executor), (
        "explicit subagent + navigation tail did NOT spawn — the navigation "
        "fast-path swallowed the explicit trigger"
    )
    assert not any(
        getattr(c[0], "name", "") == "navigate" for c in executor.calls
    ), "navigation fast-path ran instead of standing down for the explicit trigger"


@pytest.mark.asyncio
async def test_external_task_without_trigger_still_refuses() -> None:
    """No-regression: WITHOUT an explicit trigger, an unsupported external task
    must STILL be refused honestly (no spawn). Only the explicit 'subagent'/
    'spawn' mention bypasses the refusal — the gate is not weakened globally."""
    manager, executor = _seeded_strict_manager_with_local_actions()
    reply = await manager.generate(
        "Buche mir einen Flug nach Berlin"  # i18n-allow: German voice fixture
    )
    assert not _spawn_calls(executor), (
        "an unsupported external task WITHOUT an explicit trigger must not spawn"
    )
    assert reply, "expected an honest refusal reply, got empty"


def test_heavy_research_force_spawns_only_when_it_builds_an_artifact() -> None:
    """Option A (2026-06-15): heavy research force-spawns a mission ONLY when it
    asks for a BUILT ARTIFACT (a file/report the Worker->Critic pipeline can
    verify via git diff). Answer-only research routes INLINE via the router's
    search_web tool.

    Reverses the WS3a contract (2026-06-14): that offloaded the answer-only
    long-haul trip-research prompt to a mission to dodge the ~20s no-first-frame voice
    ceiling. That ceiling is now re-armed from `_brain_thinking_heartbeat`, so
    inline research no longer beheads the turn — and the Worker->Critic pipeline
    can't grade an answer-only research turn anyway (empty-diff veto ->
    critic_loop_exhausted, live mission 019ecb56)."""
    manager, _ = _manager_with_spawn(force_spawn_mode="strict")
    # Answer-only heavy research -> INLINE (the reversal).
    answer_only = _HEAVY_RESEARCH_SHOULD_SPAWN[0]
    assert manager._is_heavy_research(answer_only) is True
    assert manager._should_force_spawn(answer_only) is False, (
        "answer-only heavy research must route inline (Option A)"
    )
    # Heavy research that BUILDS a file/report -> still a mission (no explicit
    # phrase; spawns purely via the heavy-research + artefact path).
    artifact = (
        "Research and compare the top five vector databases, then write a "
        "detailed comparison report into a file named compare.md"
    )
    assert manager._is_heavy_research(artifact) is True
    assert manager._research_wants_artifact(artifact) is True
    assert manager._should_force_spawn(artifact) is True, (
        "research that builds a verifiable artefact must still force-spawn"
    )


def test_quick_weather_lookup_does_not_force_spawn() -> None:
    """A quick weather lookup must STILL stay inline (no false spawn)."""
    manager, _ = _manager_with_spawn(force_spawn_mode="strict")
    assert manager._should_force_spawn("Was ist das Wetter in Melbourne?") is False


def test_heavy_research_disabled_flag_restores_inline() -> None:
    """With the kill switch off, heavy research is no longer force-spawned."""
    manager, _ = _manager_with_spawn(force_spawn_mode="strict")
    manager._config.brain.routing.heavy_research_enabled = False
    assert manager._is_heavy_research(_HEAVY_RESEARCH_SHOULD_SPAWN[0]) is False


def test_research_question_answer_deliverable_routes_inline_not_spawned() -> None:
    """Option A (2026-06-15): a research QUESTION whose deliverable is an ANSWER
    (a comparison / overview / recommendation) must be answered INLINE via the
    router's search_web tool, NOT offloaded to a sub-agent mission.

    The Worker->Critic pipeline verifies BUILT ARTIFACTS via git diff; it is
    structurally hostile to an answer-only research turn — it cannot grade a
    spoken answer or independently verify a web citation, so the request hits the
    empty-diff veto and loops to critic_loop_exhausted (live mission 019ecb56:
    "research the AI news of the last years" failed at 1042s). It is STILL heavy
    research (the detector keeps firing), but the spawn DECISION must send an
    answer-only request inline. Only an explicit mission phrase (handled earlier
    in _should_force_spawn) or an artifact/file request offloads to a mission."""
    manager, _ = _manager_with_spawn(force_spawn_mode="strict")
    # Two research verbs (research + compare) -> detected as heavy research, but
    # the deliverable is an ANSWER: no file, no build verb, no explicit phrase.
    prompt = "Research the leading AI language models and compare their strengths."
    assert manager._is_heavy_research(prompt) is True, (
        "precondition: a 2-verb research request IS detected as heavy research"
    )
    assert manager._should_force_spawn(prompt) is False, (
        "answer-deliverable research must route inline, not force-spawn a mission"
    )


# ---------------------------------------------------------------------------
# Drag-dropped mission recap (ui.web.ws.mission_inject) — must be DISCUSSED
# inline, NEVER re-dispatched as a new mission.
#
# Live doom-loop 2026-06-16 (missions.db 019ed04e / 019ed051): the user dragged
# a finished/failed mission card onto the JarvisDock to get a recap. The recap
# directive embeds the dropped card's OWN text verbatim, so a title that
# contains a spawn trigger ("sub-agent") or an action verb ("Write …") leaks
# that trigger back into the directive -> the router force-spawned a NEW mission
# whose deliverable is a conversational recap (no file) -> empty diff ->
# critic_loop_exhausted -> FAILED. Each failed mission the user dragged to
# understand spawned another failed mission: every recent mission "failed".
#
# A dropped-card recap is a CONVERSATION, never new work (mission_inject.py:
# "a dropped mission is discussed, never re-dispatched"). The router must
# exempt the ``ui.web.ws.mission_inject`` source from force-spawn regardless of
# what the quoted title contains.
# ---------------------------------------------------------------------------

MISSION_INJECT_SOURCE = MISSION_INJECT_SOURCE_LAYER


def test_mission_inject_source_layer_parity() -> None:
    """Anti-drift: the producer's source_layer must be in the router's exempt
    set, else a recap silently force-spawns again (multi-layer string drift)."""
    from jarvis.brain.manager import _NON_SPAWN_SOURCE_LAYERS

    assert MISSION_INJECT_SOURCE_LAYER in _NON_SPAWN_SOURCE_LAYERS


def test_drop_source_layer_parity() -> None:
    """Anti-drift: a dropped file/content directive (ui.drop) must be exempt
    from force-spawn — it is reacted to inline, never auto-dispatched as a
    worker (parity with mission_inject; AP-5/AP-14, anti-doom-loop)."""
    from jarvis.brain.drop_context import DROP_SOURCE_LAYER
    from jarvis.brain.manager import _NON_SPAWN_SOURCE_LAYERS

    assert DROP_SOURCE_LAYER in _NON_SPAWN_SOURCE_LAYERS


def test_dropped_file_directive_is_never_force_spawned() -> None:
    """A drop directive carrying an action verb must still be discussed inline
    when stamped with the ui.drop source marker."""
    from jarvis.brain.drop_context import DROP_SOURCE_LAYER

    manager, _ = _manager_with_spawn(force_spawn_mode="permissive")
    directive = "Open this file and fix the bug you find in it."
    # Precondition: WITHOUT the drop source this DOES force-spawn.
    assert manager._should_force_spawn(directive) is True
    # WITH the drop source it must be answered inline, never spawned.
    assert (
        manager._should_force_spawn(directive, source_layer=DROP_SOURCE_LAYER)
        is False
    ), "a dropped-file directive must be reacted to inline, never re-dispatched"


def test_dropped_mission_recap_is_never_force_spawned() -> None:
    """A mission.inject recap directive must not trip the force-spawn heuristic."""
    from jarvis.ui.web.mission_inject import compose_mission_inject_text

    manager, _ = _manager_with_spawn()
    # A dropped card whose own text carries a spawn trigger — the verbatim title
    # leaks "sub-agent" into the composed recap directive.
    recap = compose_mission_inject_text(
        {
            "utterance": "spawn a sub-agent that writes a 200-word story to a file",
            "status": "error",
        }
    )
    assert recap is not None
    # Precondition: WITHOUT the inject source this directive DOES force-spawn,
    # so the exemption (not a weak trigger) is what suppresses it.
    assert manager._should_force_spawn(recap) is True, (
        "precondition: the quoted spawn trigger makes this directive force-spawn"
    )
    # WITH the inject source it must be answered inline, never spawned.
    assert (
        manager._should_force_spawn(recap, source_layer=MISSION_INJECT_SOURCE)
        is False
    ), "a dropped-card recap must be discussed inline, never re-dispatched"


@pytest.mark.asyncio
async def test_force_spawn_worker_skips_dropped_mission_recap() -> None:
    """The deterministic dispatch path must not spawn a worker for a recap."""
    from jarvis.ui.web.mission_inject import compose_mission_inject_text

    manager, executor = _manager_with_spawn()
    recap = compose_mission_inject_text(
        {
            "utterance": "spawn a sub-agent that writes a 200-word story to a file",
            "status": "error",
        }
    )
    result = await manager._force_spawn_worker(
        recap, source_layer=MISSION_INJECT_SOURCE
    )
    assert result is None, "no mission for a dropped-card recap"
    assert executor.calls == [], "zero spawn_worker dispatches for a recap turn"


# ---------------------------------------------------------------------------
# Knowledge-question spawn-hide (forensic 2026-06-27, voice session 08:35):
# "Welche Unternehmen haben so viel Speicherplatz?" was a pure factual question,
# yet the router-LLM reflexively CHOSE spawn_worker and announced "ich ziehe
# einen Experten hinzu". The deterministic force-spawn gate correctly stands
# down on such a turn (it only FORCES spawns, it never CONSTRAINS the LLM's own
# spawn reflex). The fix mirrors the smalltalk tool-hide: on a plain knowledge
# question the spawn tools are removed from the per-turn LLM surface, so the
# model cannot grab spawn_worker against its own prompt rule. Read/search tools
# stay visible so the question is still answerable inline.
# ---------------------------------------------------------------------------


class _FakeSearchTool:
    name = "search_web"
    schema: dict[str, Any] = {}


def _manager_with_spawn_and_search() -> BrainManager:
    executor = _RecordingExecutor()
    manager = BrainManager(
        config=JarvisConfig(),
        bus=EventBus(),
        tools={"spawn_worker": _FakeTool(), "search_web": _FakeSearchTool()},
        tool_executor=executor,  # type: ignore[arg-type]
    )
    return manager


PLAIN_KNOWLEDGE_QUESTIONS = [
    "Welche Unternehmen haben so viel Speicherplatz?",
    "Welche Firmen besitzen so viele Rechenzentren?",
    "Wie viele Menschen leben in Australien?",
    "Which companies own that much storage?",
    "Was ist der groesste Cloud-Anbieter der Welt?",
]


@pytest.mark.parametrize("utterance", PLAIN_KNOWLEDGE_QUESTIONS)
def test_plain_knowledge_question_hides_spawn_worker(utterance: str) -> None:
    """A pure factual/knowledge question removes spawn_worker from the per-turn
    LLM tool surface, while keeping the read/search tools to answer inline."""
    manager = _manager_with_spawn_and_search()
    gated = manager._hide_spawn_on_knowledge_question(dict(manager._tools), utterance)
    assert "spawn_worker" not in gated, (
        f"spawn_worker must be hidden on a plain knowledge question: {utterance!r}"
    )
    assert "search_web" in gated, "search_web must stay visible to answer inline"


BUILD_OR_ACTION_REQUESTS = [
    "Bau mir eine HTML-Uebersicht der groessten Cloud-Anbieter",
    "Schreib mir einen Bericht ueber Rechenzentren in eine Datei",
]


@pytest.mark.parametrize("utterance", BUILD_OR_ACTION_REQUESTS)
def test_build_request_keeps_spawn_worker(utterance: str) -> None:
    """A request that BUILDS a deliverable keeps spawn_worker available — the
    artifact gate must not be stripped by the knowledge-question hide."""
    manager = _manager_with_spawn_and_search()
    gated = manager._hide_spawn_on_knowledge_question(dict(manager._tools), utterance)
    assert "spawn_worker" in gated, (
        f"spawn_worker must stay for a build request: {utterance!r}"
    )


def test_explicit_subagent_question_keeps_spawn_worker() -> None:
    """Even in question form, an explicitly named heavy-work vehicle keeps the
    spawn tool — the user named the vehicle, respect it (AD-S9)."""
    manager = _manager_with_spawn_and_search()
    gated = manager._hide_spawn_on_knowledge_question(
        dict(manager._tools),
        "Kannst du einen Subagenten spawnen der die groessten Cloud-Anbieter recherchiert?",
    )
    assert "spawn_worker" in gated, (
        "an explicit 'Subagent' trigger must never be hidden, even in question form"
    )


# ---------------------------------------------------------------------------
# Signalless-turn action-hide (forensic 2026-06-27, voice session): the German
# smalltalk "Was geht ab?" was mis-transcribed by STT as "Lask it up!" [en]
# confidence 0.509 — missing BOTH the smalltalk allowlist AND the whisper-junk
# seed lists — so the action tools stayed visible and gemini, reading a
# 30k-token context full of the PREVIOUS "open Discord, bridge-mine channel"
# command, re-ran that exact computer_use plan on a turn that asked for nothing.
# A short turn with no actionable signal of its own must not reach computer_use
# / spawn so it cannot inherit the prior turn's desktop action.
# ---------------------------------------------------------------------------


class _FakeCuTool:
    name = "computer_use"
    schema: dict[str, Any] = {}


def _manager_with_cu_spawn_search() -> BrainManager:
    executor = _RecordingExecutor()
    return BrainManager(
        config=JarvisConfig(),
        bus=EventBus(),
        tools={
            "computer_use": _FakeCuTool(),
            "spawn_worker": _FakeTool(),
            "search_web": _FakeSearchTool(),
        },
        tool_executor=executor,  # type: ignore[arg-type]
    )


# GENERAL rule (user mandate 2026-06-27 — "this must apply to ALL questions"):
# ANY turn with no action signal of its own — short or long, with or without a
# trailing "?" — loses computer_use/spawn so it cannot inherit the previous
# turn's CU action. Mis-transcriptions, smalltalk, and plain questions all
# qualify. (The real "Was geht ab?" is ALSO caught upstream by the smalltalk
# gate; here we prove the filter itself no longer exempts it.)
NO_ACTION_SIGNAL_TURNS = [
    "Lask it up!",                                   # the live STT junk
    "Mask it up.",                                   # sibling STT-junk variant
    "Was geht ab?",                                  # i18n-allow: the original question
    "Wie viele Menschen leben in Australien?",       # i18n-allow: long factual question
    "Which company owns the most data centers in the world right now?",
    "Erzaehl mir einen Witz",                        # i18n-allow: chit-chat, no action
]


@pytest.mark.parametrize("utterance", NO_ACTION_SIGNAL_TURNS)
def test_no_action_signal_turn_hides_computer_use_and_spawn(utterance: str) -> None:
    """ANY turn with no action signal of its own (question, remark, or
    mis-transcription — regardless of length or a trailing '?') cannot reach
    computer_use/spawn, so the LLM cannot inherit the previous turn's CU action.
    The read-only search tool stays visible so the turn is still answerable."""
    manager = _manager_with_cu_spawn_search()
    gated = manager._hide_action_tools_on_signalless_turn(
        dict(manager._tools), utterance
    )
    assert "computer_use" not in gated, (
        f"computer_use must be hidden on a no-action-signal turn: {utterance!r}"
    )
    assert "spawn_worker" not in gated, (
        f"spawn_worker must be hidden on a no-action-signal turn: {utterance!r}"
    )
    assert "search_web" in gated, "read-only search_web must stay visible"


ACTIONABLE_TURNS = [
    "Oeffne Discord fuer mich",            # explicit open-app intent
    "Klick auf den Play-Button",           # PC-control verb
    "Mach das am Bildschirm",              # names the screen surface
    "Was siehst du auf meinem Bildschirm?",  # i18n-allow: a VISUAL question keeps CU (Bildschirm)
    "Bau mir eine HTML-Uebersicht der groessten Cloud-Anbieter",  # artifact build
]


@pytest.mark.parametrize("utterance", ACTIONABLE_TURNS)
def test_actionable_turn_keeps_computer_use(utterance: str) -> None:
    """A turn that DOES carry an action signal — an action verb, a named app, the
    screen surface, or an artifact-build request — keeps computer_use. This holds
    even for a question ('Was siehst du auf meinem Bildschirm?'): naming the
    screen is action-intent, so the heavy tools stay available."""
    manager = _manager_with_cu_spawn_search()
    gated = manager._hide_action_tools_on_signalless_turn(
        dict(manager._tools), utterance
    )
    assert "computer_use" in gated, (
        f"computer_use must stay for an actionable turn: {utterance!r}"
    )


# ---------------------------------------------------------------------------
# PC-control run-skill hide (forensic 2026-07-02, voice session 20:28): the
# explicit desktop request "ein Terminal oeffnen, Cloud-Code oeffnen, … und
# fuer mich ein Prompt geben …" (STT-garbled "Claude Code") ALSO mentioned
# looking for bugs, so the SKILLS-FIRST router rule ("when in doubt, call the
# skill") let the semantically-similar cloud-debug skill hijack the turn:
# run-skill returned its mission directive, the model followed neither it nor
# computer_use, and spoke the dictated capability refusal ("mir fehlt dafuer
# das passende Werkzeug"). The vehicle the user NAMES (the desktop) must
# outrank a loose skill CONTENT match — run-skill leaves the surface on such a
# turn so computer_use stays authoritative.
# ---------------------------------------------------------------------------


class _FakeRunSkillTool:
    name = "run-skill"
    schema: dict[str, Any] = {}


def _manager_with_cu_runskill_search() -> BrainManager:
    executor = _RecordingExecutor()
    return BrainManager(
        config=JarvisConfig(),
        bus=EventBus(),
        tools={
            "computer_use": _FakeCuTool(),
            "run-skill": _FakeRunSkillTool(),
            "search_web": _FakeSearchTool(),
        },
        tool_executor=executor,  # type: ignore[arg-type]
    )


# The live incident transcript (STT-garbled: "Cloud-Code" = Claude Code) plus
# simpler members of the same class: the user names the DESKTOP as vehicle.
PC_CONTROL_TURNS_THAT_MUST_NOT_REACH_RUN_SKILL = [
    (
        # i18n-allow: live incident transcript under test
        "Kannst du bitte für mich mal für mich ein Terminal öffnen, Cloud-Code "
        "öffnen, in den Jarvis-Vorordnern, in das Jarvis-Directly-Renavigieren "
        "und für mich ein Prompt geben, und zwar, dass er mal einen kompletten "
        "Deep-Dive machen soll und gucken, ob es irgendwelche Bugs gibt. Er soll "
        "nur ein Report schreiben und keine einzige Datei verändern oder löschen "
        "oder sowas etc."
    ),
    "Oeffne ein Terminal und starte Claude Code",  # i18n-allow: German voice command under test
    "Klick auf den Play-Button",                   # i18n-allow: German voice command under test
    "Open a terminal and type npm install",
]


@pytest.mark.parametrize("utterance", PC_CONTROL_TURNS_THAT_MUST_NOT_REACH_RUN_SKILL)
def test_pc_control_turn_hides_run_skill_keeps_computer_use(utterance: str) -> None:
    """An explicit desktop request (open an app/terminal, click, type) must not
    be hijackable by a semantically-similar skill: run-skill leaves the surface,
    computer_use stays."""
    manager = _manager_with_cu_runskill_search()
    gated = manager._hide_run_skill_on_pc_control_turn(
        dict(manager._tools), utterance
    )
    assert "run-skill" not in gated, (
        f"run-skill must be hidden on a pc-control turn: {utterance!r}"
    )
    assert "computer_use" in gated, "computer_use must stay authoritative"
    assert "search_web" in gated, "unrelated tools must be untouched"


NON_PC_CONTROL_SKILL_TURNS = [
    "Wie sieht mein Tag aus?",          # i18n-allow: morning-routine skill trigger under test
    "Finde den Bug im Login-Test",      # i18n-allow: cloud-debug-shaped task, no desktop vehicle
    "What does my day look like?",
]


@pytest.mark.parametrize("utterance", NON_PC_CONTROL_SKILL_TURNS)
def test_non_pc_control_turn_keeps_run_skill(utterance: str) -> None:
    """A turn without a desktop-vehicle signal keeps run-skill — skills stay
    first-class for the kind of task they exist for."""
    manager = _manager_with_cu_runskill_search()
    gated = manager._hide_run_skill_on_pc_control_turn(
        dict(manager._tools), utterance
    )
    assert "run-skill" in gated, (
        f"run-skill must stay on a non-pc-control turn: {utterance!r}"
    )


def test_explicit_skill_request_keeps_run_skill_even_on_pc_control_turn() -> None:
    """The user literally naming a skill is its own vehicle — it must never be
    vetoed, even when the same turn opens an app (mirrors AD-S9 for spawn)."""
    manager = _manager_with_cu_runskill_search()
    gated = manager._hide_run_skill_on_pc_control_turn(
        dict(manager._tools),
        # i18n-allow: German voice command under test
        "Oeffne Chrome und nutz den Skill browser-tabs",
    )
    assert "run-skill" in gated, "an explicit skill request must keep run-skill"


def test_run_skill_stays_when_computer_use_absent() -> None:
    """On a host without the CU harness the gate must stand down — hiding
    run-skill there would leave the desktop request with NO handler at all."""
    executor = _RecordingExecutor()
    manager = BrainManager(
        config=JarvisConfig(),
        bus=EventBus(),
        tools={"run-skill": _FakeRunSkillTool(), "search_web": _FakeSearchTool()},
        tool_executor=executor,  # type: ignore[arg-type]
    )
    gated = manager._hide_run_skill_on_pc_control_turn(
        # i18n-allow: German voice command under test
        dict(manager._tools), "Oeffne ein Terminal und starte Claude Code"
    )
    assert "run-skill" in gated, (
        "without computer_use in the surface the gate must not hide run-skill"
    )


def test_pc_control_run_skill_gate_is_fault_tolerant() -> None:
    """Any fault (non-dict surface) returns the tools unchanged — a gate bug
    must never blind the brain."""
    manager = _manager_with_cu_runskill_search()
    sentinel = object()
    assert manager._hide_run_skill_on_pc_control_turn(sentinel, "Oeffne Chrome") is sentinel  # type: ignore[arg-type]  # i18n-allow: German voice command under test
