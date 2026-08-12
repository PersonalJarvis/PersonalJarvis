"""The spoken "open five more terminals" path, end to end inside the brain.

Why this is deterministic code and not a router tool: the utterance opens with
the very word ("spawne" / "spawn") that the force-spawn heuristic reads as
"dispatch a background agent". Left to the LLM, five requested panes become one
invisible mission worker in a throwaway git worktree — the 2026-07-25 defect
class, one layer up. These tests pin the three things that make the feature real:

* the panes actually appear (and how many, when the workspace cap intervenes),
* the reply names them, in the language of the turn, and never overstates,
* the open UI is told, because the workspace view fetches its state once on
  mount and would otherwise show a stale grid while the agents are running.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import recents
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import MAX_TERMINALS, Registry
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from tests.fakes.fake_pty_manager import FakePtyManager


class FakeBus:
    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish(self, event: object) -> None:
        self.published.append(event)

    def subscribe(self, *_a: object, **_kw: object) -> None:
        return None

    def subscribe_all(self, *_a: object, **_kw: object) -> None:
        return None


def _event_names(bus: FakeBus) -> list[str]:
    return [type(e).__name__ for e in bus.published]


@pytest.fixture(autouse=True)
def _isolated_recents(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the recents file out of the developer's real data directory.

    Opening a workspace records it as "most recently used", and that file lives
    under the per-user data dir — the SAME file the running app reads. Without
    this, a test run rewrites the maintainer's recent-workspace list with
    throwaway pytest folders (measured 2026-07-25: all eight entries were
    ``pytest-of-…`` paths), and the voice path that opens "the most recent
    workspace" then starts coding agents in a deleted temp directory.
    """
    from jarvis.agentic_ide import recents

    store = tmp_path_factory.mktemp("recents") / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    """A real registry on a fake pseudo-terminal, wired in place of the global one."""
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    reg = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "get_registry", lambda: reg)
    return reg


@pytest.fixture
def manager() -> tuple[BrainManager, FakeBus]:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    bus = FakeBus()
    mgr = BrainManager(config=cfg, bus=EventBus(), tools={})
    mgr._bus = bus  # type: ignore[assignment]
    # A pinned reply language keeps the assertions about wording independent of
    # the host's configured locale (AP-23: never test against the maintainer's
    # own config).
    mgr._reply_language = "en"
    return mgr, bus


async def _open(registry: Registry, folder: Path, count: int, agent: str = "claude"):
    return await registry.start(
        str(folder), [{"agent": agent} for _ in range(count)]
    )


# ------------------------------------------------------------------ happy path
async def test_a_spoken_request_opens_panes_and_names_them(
    manager: tuple[BrainManager, FakeBus], registry: Registry, tmp_path: Path
) -> None:
    mgr, bus = manager
    await _open(registry, tmp_path, 2)

    reply = await mgr._run_agentic_ide_spawn_fast_path("Spawn three more terminals")

    assert reply is not None
    assert registry.session is not None
    assert len(registry.session.terminals) == 5
    # The three NEW call-signs are spoken back: they are how the user addresses
    # the panes in the next sentence.
    new_names = [t.name for t in registry.session.terminals[2:]]
    for name in new_names:
        assert name in reply
    assert "3" in reply

    # Both notifications go out: the grid refresh AND bringing the view forward
    # (which is what starts the agents — a pane's PTY spawns when it mounts).
    assert _event_names(bus) == ["AgenticIdeTerminalsAdded", "NavigateSidebar"]
    assert bus.published[0].names == tuple(new_names)
    assert bus.published[1].section == "agentic-ide"


async def test_a_mixed_fleet_opens_both_kinds_of_agent(
    manager: tuple[BrainManager, FakeBus], registry: Registry, tmp_path: Path
) -> None:
    """The maintainer's ask: "5 Codex and 3 Claudes in one task" (2026-07-26).

    The detector used to read the first number and the first agent, so this
    sentence opened five Codex panes and dropped the three Claude ones without
    telling anyone.
    """
    mgr, _bus = manager
    await _open(registry, tmp_path, 1, agent="claude")

    reply = await mgr._run_agentic_ide_spawn_fast_path(
        "Open 5 Codex terminals and 3 Claude Code terminals"
    )

    assert reply is not None
    assert registry.session is not None
    opened = [t.agent for t in registry.session.terminals[1:]]
    assert opened.count("codex") == 5
    assert opened.count("claude") == 3


async def test_a_named_agent_is_honoured_over_the_inherited_one(
    manager: tuple[BrainManager, FakeBus], registry: Registry, tmp_path: Path
) -> None:
    mgr, _bus = manager
    await _open(registry, tmp_path, 1, agent="codex")

    await mgr._run_agentic_ide_spawn_fast_path("Open two Claude Code terminals")

    assert registry.session is not None
    assert [t.agent for t in registry.session.terminals] == ["codex", "claude", "claude"]


async def test_live_recombined_pipeline_turn_opens_five_claude_panes(
    manager: tuple[BrainManager, FakeBus], registry: Registry, tmp_path: Path
) -> None:
    """The production failure must stop before the background-worker route."""
    mgr, bus = manager
    await _open(registry, tmp_path, 3, agent="claude")
    utterance = (
        "Was geht ab? Du geile Sau! Geil, kannst du bitte "
        "5 Cloth-Code-Terminal spawnen?"
    )  # i18n-allow: production transcript under test

    reply = await mgr._run_agentic_ide_spawn_fast_path(utterance)

    assert reply is not None
    assert registry.session is not None
    assert len(registry.session.terminals) == 8
    assert {term.agent for term in registry.session.terminals[3:]} == {"claude"}
    assert _event_names(bus) == ["AgenticIdeTerminalsAdded", "NavigateSidebar"]


async def test_spawn_and_prompt_queues_exactly_the_new_codex_fleet(
    manager: tuple[BrainManager, FakeBus],
    registry: Registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr, _bus = manager
    await _open(registry, tmp_path, 1, agent="claude")
    received: list[tuple[list[str], str]] = []
    called = asyncio.Event()

    async def _brief(_session: object, names: list[str], utterance: str) -> None:
        received.append((names, utterance))
        called.set()

    monkeypatch.setattr(mgr, "_brief_spawned_agentic_ide_fleet", _brief)
    utterance = (
        "Spawn five Codex terminals and prompt each one to start 50 subagents "
        "for a read-only platform review"
    )

    reply = await mgr._run_agentic_ide_spawn_fast_path(utterance)
    await asyncio.wait_for(called.wait(), timeout=1)

    assert registry.session is not None
    new_terms = registry.session.terminals[1:]
    assert len(new_terms) == 5
    assert {term.agent for term in new_terms} == {"codex"}
    assert received == [([term.name for term in new_terms], utterance)]
    assert reply is not None
    assert "as soon as the terminals are ready" in reply


async def test_queued_fleet_receives_the_task_not_the_spawn_scaffolding(
    manager: tuple[BrainManager, FakeBus],
    registry: Registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.agentic_ide import fanout, fleet_actions

    mgr, _bus = manager
    session = await _open(registry, tmp_path, 2, agent="codex")
    names = [term.name for term in session.terminals]
    captured: dict[str, object] = {}

    async def _ready(_session: object, wanted: list[str]) -> tuple[str, ...]:
        return tuple(wanted)

    async def _deliver(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(delivered=(object(), object()))

    monkeypatch.setattr(fleet_actions, "wait_for_prompt_ready", _ready)
    monkeypatch.setattr(fanout, "deliver", _deliver)
    utterance = (
        "Spawn two Codex terminals and prompt each one to perform a read-only "
        "platform compatibility review"
    )

    await mgr._brief_spawned_agentic_ide_fleet(session, names, utterance)

    assert captured["terminals"] == tuple(names)
    assert captured["instruction"] == "perform a read-only platform compatibility review"
    assert "Spawn two Codex terminals" not in str(captured["instruction"])


async def test_a_brief_by_kind_routes_each_kind_its_own_task(
    manager: tuple[BrainManager, FakeBus],
    registry: Registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"prompt the claudes to X and the codex to Y" must not mash X and Y.

    The maintainer's live failure (2026-08-12): every pane of a mixed fleet
    received the ENTIRE enumeration as its own task, and each agent picked
    whatever slice it liked.
    """
    from jarvis.agentic_ide import fanout, fleet_actions

    mgr, _bus = manager
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"}, {"agent": "claude"}, {"agent": "codex"}]
    )
    names = [term.name for term in session.terminals]
    captured: dict[str, object] = {}

    async def _ready(_session: object, wanted: list[str]) -> tuple[str, ...]:
        return tuple(wanted)

    async def _deliver(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(delivered=(object(),) * 3)

    monkeypatch.setattr(fleet_actions, "wait_for_prompt_ready", _ready)
    monkeypatch.setattr(fanout, "deliver", _deliver)
    utterance = (
        "Open two claude terminals and one codex terminal. Prompt the claudes "
        "to fix the failing login tests and prompt the codex to update the "
        "developer docs."
    )

    await mgr._brief_spawned_agentic_ide_fleet(session, names, utterance)

    assert captured["terminals"] == tuple(names)
    assert captured["assignments"] == {
        names[0]: "fix the failing login tests",
        names[1]: "fix the failing login tests",
        names[2]: "update the developer docs",
    }


async def test_a_kind_without_a_brief_is_not_briefed_at_all(
    manager: tuple[BrainManager, FakeBus],
    registry: Registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"prompt the claudes …" leaves the codex pane blank — as asked."""
    from jarvis.agentic_ide import fanout, fleet_actions

    mgr, _bus = manager
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"}, {"agent": "codex"}]
    )
    names = [term.name for term in session.terminals]
    captured: dict[str, object] = {}

    async def _ready(_session: object, wanted: list[str]) -> tuple[str, ...]:
        return tuple(wanted)

    async def _deliver(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(delivered=(object(),))

    monkeypatch.setattr(fleet_actions, "wait_for_prompt_ready", _ready)
    monkeypatch.setattr(fanout, "deliver", _deliver)
    utterance = (
        "Open one claude terminal and one codex terminal. Prompt the claude "
        "to fix the failing login tests."
    )

    await mgr._brief_spawned_agentic_ide_fleet(session, names, utterance)

    assert captured["terminals"] == (names[0],)
    assert captured["assignments"] == {names[0]: "fix the failing login tests"}


async def test_an_enumerated_division_reaches_the_split_planner(
    manager: tuple[BrainManager, FakeBus],
    registry: Registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"one fixes macOS, one fixes Linux" plans a split instead of one brief."""
    from jarvis.agentic_ide import fanout, fleet_actions, work_split

    mgr, _bus = manager
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"}, {"agent": "claude"}]
    )
    names = [term.name for term in session.terminals]
    captured: dict[str, object] = {}
    planned: dict[str, object] = {}

    async def _ready(_session: object, wanted: list[str]) -> tuple[str, ...]:
        return tuple(wanted)

    async def _deliver(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(delivered=(object(),) * 2)

    async def _split(instruction: str, **kwargs: object) -> object:
        planned["instruction"] = instruction
        planned["count"] = kwargs.get("count")
        return work_split.WorkSplit(
            assignments=(
                work_split.Assignment(area="macOS", task="fix the macOS bug"),
                work_split.Assignment(area="Linux", task="fix the Linux bug"),
            ),
            split_by="llm",
        )

    monkeypatch.setattr(fleet_actions, "wait_for_prompt_ready", _ready)
    monkeypatch.setattr(fanout, "deliver", _deliver)
    monkeypatch.setattr(work_split, "split", _split)
    utterance = (
        "Spawn two new terminals and prompt them, one fixes a bug on macOS "
        "and one fixes a bug on Linux"
    )

    await mgr._brief_spawned_agentic_ide_fleet(session, names, utterance)

    assert planned["count"] == 2
    assert captured["assignments"] == {
        names[0]: "fix the macOS bug",
        names[1]: "fix the Linux bug",
    }


async def test_without_a_named_agent_the_new_panes_inherit(
    manager: tuple[BrainManager, FakeBus], registry: Registry, tmp_path: Path
) -> None:
    """"Two more terminals" in a Codex workspace means two more Codex panes."""
    mgr, _bus = manager
    await _open(registry, tmp_path, 1, agent="codex")

    await mgr._run_agentic_ide_spawn_fast_path("Two more terminals please")

    assert registry.session is not None
    assert [t.agent for t in registry.session.terminals] == ["codex", "codex", "codex"]


# ------------------------------------------------------------------ the limits
async def test_a_capped_batch_says_how_many_actually_opened(
    manager: tuple[BrainManager, FakeBus], registry: Registry, tmp_path: Path
) -> None:
    """The maintainer's live case: nine panes open, five requested, three appear."""
    mgr, _bus = manager
    await _open(registry, tmp_path, MAX_TERMINALS - 3)

    reply = await mgr._run_agentic_ide_spawn_fast_path("Spawn five more terminals")

    assert reply is not None
    assert "only room for 3" in reply
    assert registry.session is not None
    assert len(registry.session.terminals) == MAX_TERMINALS


async def test_a_full_workspace_says_so_instead_of_failing_silently(
    manager: tuple[BrainManager, FakeBus], registry: Registry, tmp_path: Path
) -> None:
    mgr, bus = manager
    await _open(registry, tmp_path, MAX_TERMINALS)

    reply = await mgr._run_agentic_ide_spawn_fast_path("Open two more terminals")

    assert reply is not None
    assert "full" in reply.lower()
    assert str(MAX_TERMINALS) in reply
    assert bus.published == []  # nothing changed, so nothing is announced


# ----------------------------------------------------------- no open workspace
async def test_no_workspace_opens_the_most_recent_folder_and_names_it(
    manager: tuple[BrainManager, FakeBus],
    registry: Registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The folder is an ASSUMPTION, so the reply has to say which one it took."""
    mgr, bus = manager
    project = tmp_path / "my-project"
    project.mkdir()
    monkeypatch.setattr(
        recents,
        "load",
        lambda **_kw: [
            recents.RecentWorkspace(
                path=str(project),
                name="my-project",
                terminals=2,
                agents={"codex": 2},
                last_used=1.0,
            )
        ],
    )

    reply = await mgr._run_agentic_ide_spawn_fast_path("Spawn two terminals")

    assert reply is not None
    assert "my-project" in reply
    assert registry.session is not None
    assert registry.session.folder == str(project)
    # The remembered agent split is replayed rather than defaulting to Claude —
    # reopening a Codex project must not silently switch the agent.
    assert [t.agent for t in registry.session.terminals] == ["codex", "codex"]
    assert _event_names(bus) == ["AgenticIdeTerminalsAdded", "NavigateSidebar"]


async def test_no_workspace_and_no_recents_is_an_honest_refusal(
    manager: tuple[BrainManager, FakeBus],
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr, bus = manager
    monkeypatch.setattr(recents, "load", lambda **_kw: [])

    reply = await mgr._run_agentic_ide_spawn_fast_path("Spawn two terminals")

    assert reply is not None
    assert "no workspace" in reply.lower()
    assert "Agentic IDE" in reply
    assert registry.session is None
    assert bus.published == []


# ------------------------------------------------------------- standing aside
@pytest.mark.parametrize(
    "utterance",
    [
        "Spawn a subagent that reviews the wake path",
        "How many terminals can I open?",
        "What is Alex doing?",
        "Tell Alex to run the tests",
    ],
)
async def test_turns_that_are_not_pane_requests_fall_through(
    manager: tuple[BrainManager, FakeBus],
    registry: Registry,
    tmp_path: Path,
    utterance: str,
) -> None:
    """``None`` means "not mine" — the normal routing path runs untouched."""
    mgr, _bus = manager
    await _open(registry, tmp_path, 2)

    assert await mgr._run_agentic_ide_spawn_fast_path(utterance) is None
    assert registry.session is not None
    assert len(registry.session.terminals) == 2


@pytest.mark.parametrize(
    ("pinned", "expected"),
    [
        ("de", "neue Terminals"),  # i18n-allow: asserted German reply
        ("en", "new terminals"),
        ("es", "terminales nuevas"),
    ],
)
async def test_the_reply_follows_the_pinned_language(
    manager: tuple[BrainManager, FakeBus],
    registry: Registry,
    tmp_path: Path,
    pinned: str,
    expected: str,
) -> None:
    """Every supported locale gets a real sentence, resolved through ONE resolver.

    The language pin is the DURABLE guarantee, so it is what is pinned here. A
    detection-based assertion would be flaky for a reason worth writing down:
    "spawne zwei neue Terminals" is four words of which three are loanwords
    shared with English, so no detector can be relied on to call it German —
    which is precisely why the pin exists.
    """
    mgr, _bus = manager
    mgr._reply_language = pinned
    await _open(registry, tmp_path, 1)

    reply = await mgr._run_agentic_ide_spawn_fast_path("Spawn two more terminals")

    assert reply is not None
    assert expected in reply


async def test_a_clearly_german_turn_is_answered_in_german_without_a_pin(
    manager: tuple[BrainManager, FakeBus], registry: Registry, tmp_path: Path
) -> None:
    """With ``auto``, an unmistakably German sentence still lands in German."""
    mgr, _bus = manager
    mgr._reply_language = "auto"
    await _open(registry, tmp_path, 1)

    reply = await mgr._run_agentic_ide_spawn_fast_path(
        "Öffne mir bitte noch zwei zusätzliche Terminals"  # i18n-allow: spoken input under test
    )
    assert reply is not None
    assert "neue Terminals" in reply  # i18n-allow: asserted German reply


# --------------------------------------------------------------------------- #
# Live voice regression 2026-07-27: one pane, and it gets the work             #
# --------------------------------------------------------------------------- #


async def test_a_conditional_spawn_opens_one_pane_and_briefs_it(
    manager: tuple[BrainManager, FakeBus],
    registry: Registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole reported failure, pinned at the layer that produced it.

    The user described the work, named a pane that was not running, and made
    the spawn a fallback: "let Lee do a deep dive on this and fix it — if there
    is no terminal by that name, open one and prompt it right there". The task
    sits in FRONT of the spawn clause, and only what followed it was read, so a
    blank pane opened, "ready" was spoken, and nobody was briefed. What the user
    said next was "you did nothing" — followed by four more panes as the router
    tried to reach a call-sign that could never exist.
    """
    mgr, _bus = manager
    await _open(registry, tmp_path, 2)
    received: list[tuple[list[str], str]] = []
    called = asyncio.Event()

    async def _brief(_session: object, names: list[str], utterance: str) -> None:
        received.append((names, utterance))
        called.set()

    monkeypatch.setattr(mgr, "_brief_spawned_agentic_ide_fleet", _brief)
    utterance = (
        "Kannst du bitte dazu Lee einen Deep Dive machen lassen? Und das fixen. "
        "Wenn es kein Terminal gibt, welches so heißt, spawn ein neues und lass "
        "es dann direkt da rein"
    )  # i18n-allow: production transcript under test

    reply = await mgr._run_agentic_ide_spawn_fast_path(utterance)
    await asyncio.wait_for(called.wait(), timeout=1)

    assert registry.session is not None
    opened = registry.session.terminals[2:]
    assert len(opened) == 1, "one pane was asked for — the four extra ones are the bug"
    assert received == [([opened[0].name], utterance)]
    assert reply is not None
    assert opened[0].name in reply, "the pane's real call-sign is spoken back"


async def test_the_conditional_brief_carries_the_task_and_not_the_fallback(
    manager: tuple[BrainManager, FakeBus],
    registry: Registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the new pane is told has to be the work, not the spawn wording."""
    from jarvis.agentic_ide import fanout, fleet_actions

    mgr, _bus = manager
    session = await _open(registry, tmp_path, 1)
    names = [term.name for term in session.terminals]
    captured: dict[str, object] = {}

    async def _ready(_session: object, wanted: list[str]) -> tuple[str, ...]:
        return tuple(wanted)

    async def _deliver(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(delivered=(object(),))

    monkeypatch.setattr(fleet_actions, "wait_for_prompt_ready", _ready)
    monkeypatch.setattr(fanout, "deliver", _deliver)
    utterance = (
        "Have Lee investigate the huge empty area in the layout and fix it. "
        "If there is no terminal called that, open a new one and prompt it there"
    )

    await mgr._brief_spawned_agentic_ide_fleet(session, names, utterance)

    instruction = str(captured["instruction"])
    assert "empty area" in instruction
    assert "open a new one" not in instruction, "the fallback is not the work"


# ------------------------------------------------- asking which CLI was meant
# Maintainer directive 2026-07-28: "Wenn das sich irgendwie unklar ist mit dem
# Namen, ob jetzt zum Beispiel Claude Code oder Codex gemeint ist ... dann soll
# er einfach nachfragen."  # i18n-allow: quoted maintainer directive
#
# Speech recognition writes a product name by ear, and the spellings it invents
# are open-ended — no alias table finishes that job. What the table cannot place
# used to be guessed away silently: the count went to the pane noun and panes
# opened on whatever CLI happened to be inherited, so the user asked for two of
# one agent and got two of another, with nothing said.


async def test_an_unclear_cli_name_is_asked_about_rather_than_guessed(
    manager: tuple[BrainManager, FakeBus], registry: Registry, tmp_path: Path
) -> None:
    mgr, _bus = manager
    await _open(registry, tmp_path, 1)

    reply = await mgr._run_agentic_ide_spawn_fast_path("open two Klohd terminals")

    assert reply is not None
    assert "Klohd" in reply
    assert "Claude Code" in reply
    # Nothing was opened on a guess.
    assert registry.session is not None
    assert len(registry.session.terminals) == 1


async def test_naming_the_cli_in_the_answer_opens_the_whole_fleet(
    manager: tuple[BrainManager, FakeBus], registry: Registry, tmp_path: Path
) -> None:
    mgr, _bus = manager
    await _open(registry, tmp_path, 1, agent="claude")

    await mgr._run_agentic_ide_spawn_fast_path("open two Klohd terminals and one Codex")
    reply = await mgr._run_agentic_ide_spawn_fast_path("Codex")

    assert reply is not None
    assert registry.session is not None
    # One pane was already open; the answered fleet adds three.
    agents = [t.agent for t in registry.session.terminals]
    assert len(agents) == 4
    assert agents.count("codex") == 3, (
        "the answer has to fill in the group that was unclear AND keep the one "
        "that was not"
    )


async def test_yes_answers_a_question_that_offered_one_name(
    manager: tuple[BrainManager, FakeBus], registry: Registry, tmp_path: Path
) -> None:
    mgr, _bus = manager
    await _open(registry, tmp_path, 1)

    asked = await mgr._run_agentic_ide_spawn_fast_path("open two Klaudi terminals")
    assert asked is not None and "Claude Code" in asked

    await mgr._run_agentic_ide_spawn_fast_path("yes")

    assert registry.session is not None
    assert len(registry.session.terminals) == 3


async def test_the_question_is_spent_when_the_user_moves_on(
    manager: tuple[BrainManager, FakeBus], registry: Registry, tmp_path: Path
) -> None:
    """A forgotten question must never open panes during a later turn."""
    mgr, _bus = manager
    await _open(registry, tmp_path, 1)

    await mgr._run_agentic_ide_spawn_fast_path("open two Klohd terminals")
    moved_on = await mgr._run_agentic_ide_spawn_fast_path(
        "actually never mind, what does the wake word do again"
    )

    assert moved_on is None
    assert registry.session is not None
    assert len(registry.session.terminals) == 1
    # And the answer is gone with it: a "Codex" now is a fresh turn, not the
    # missing half of a question nobody is holding any more.
    assert getattr(mgr, "_pending_cli_choice", None) is None


async def test_a_clearly_named_cli_never_asks(
    manager: tuple[BrainManager, FakeBus], registry: Registry, tmp_path: Path
) -> None:
    """The question must not become the new nagging (clarify mandate)."""
    mgr, _bus = manager
    await _open(registry, tmp_path, 1)

    reply = await mgr._run_agentic_ide_spawn_fast_path(
        "open two Codex terminals and one Claude Code terminal"
    )

    assert reply is not None
    assert "did you mean" not in reply.lower()
    assert registry.session is not None
    assert len(registry.session.terminals) == 4
