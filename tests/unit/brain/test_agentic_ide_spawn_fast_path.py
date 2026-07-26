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

from pathlib import Path

import pytest

from jarvis.agentic_ide import recents, session as session_mod
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
def _isolated_recents(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
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
