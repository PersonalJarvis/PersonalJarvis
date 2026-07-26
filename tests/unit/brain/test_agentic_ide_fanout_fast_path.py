"""Speaking to SEVERAL panes at once, end to end inside the brain.

The 2026-07-26 09:18 failure had two halves, and fixing only one of them would
have been worse than fixing neither:

* **Delivery.** "Iris und Bruno beide in Deep Dive geben" briefed Iris. The
  detector stopped at its first match, so Bruno was never a candidate.
* **Reporting.** The readback named the one pane that got it, the realtime
  provider re-used both names from the question, and the user was told two
  agents were working on a codebase audit when one was. A fan-out that delivers
  to two of three panes and reports success is exactly this bug again, so the
  reply is pinned here as hard as the delivery: every pane that did NOT get the
  prompt must appear in the spoken answer.

The composer is faked throughout. It is a quality-tier provider call by design
(10-21 s), and what these tests are about is the fan-out around it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import prompt_composer
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.prompt_composer import ComposedPrompt
from jarvis.agentic_ide.session import Registry
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def _isolated_recents(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never rewrite the developer's real recent-workspace list from a test."""
    from jarvis.agentic_ide import recents

    store = tmp_path_factory.mktemp("recents") / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)


@pytest.fixture(autouse=True)
def _fake_composer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deterministic stand-in for the quality-tier prompt writer."""

    async def fake_compose(utterance: str, **kwargs: object) -> ComposedPrompt:
        name = kwargs["terminal_name"]
        instruction = kwargs.get("instruction") or utterance
        return ComposedPrompt(
            text=f"## Task for {name}\n{instruction}",
            files=["jarvis/core/bus.py"],
            composed_by="llm",
        )

    monkeypatch.setattr(prompt_composer, "compose", fake_compose)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    reg = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "get_registry", lambda: reg)
    return reg


@pytest.fixture
def manager() -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    mgr = BrainManager(config=cfg, bus=EventBus(), tools={})
    # Pinned so the wording assertions do not depend on the host's locale
    # (AP-23: never test against the maintainer's own configuration).
    mgr._reply_language = "en"
    return mgr


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _open(registry: Registry, folder: Path, count: int) -> None:
    """Open ``count`` panes AND bring their agents live.

    A pane's PTY only spawns when the workspace view mounts it, so a session
    straight out of ``start`` has nothing to type into — attaching is what the
    real UI does and what makes a prompt deliverable.
    """
    await registry.start(str(folder), [{"agent": "claude"} for _ in range(count)])
    assert registry.session is not None
    for term in list(registry.session.terminals):
        await registry.attach(term.name, 100, 30, _noop, _noop_exit)


def _names(registry: Registry) -> list[str]:
    assert registry.session is not None
    return [t.name for t in registry.session.terminals]


def _sent_to(registry: Registry, name: str) -> bool:
    """True when this pane actually received a prompt."""
    assert registry.session is not None
    term = registry.session.find(name)
    assert term is not None
    return term.prompts_sent > 0


async def test_two_addressed_panes_both_receive_the_prompt(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 2)
    first, second = _names(registry)

    reply = await manager._run_agentic_ide_fast_path(
        f"Tell {first} and {second} to analyse the whole codebase"
    )

    assert reply is not None
    assert _sent_to(registry, first)
    assert _sent_to(registry, second)


async def test_the_reply_names_every_pane_that_got_it(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 2)
    first, second = _names(registry)

    reply = await manager._run_agentic_ide_fast_path(
        f"Tell {first} and {second} to analyse the whole codebase"
    )

    assert reply is not None
    assert first in reply
    assert second in reply


async def test_a_pane_that_is_not_running_is_named_as_not_reached(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """The half of the live bug that made a partial delivery sound complete."""
    await _open(registry, tmp_path, 2)
    first, second = _names(registry)
    assert registry.session is not None
    dead = registry.session.find(second)
    assert dead is not None
    dead.status = "exited"
    dead.pty_id = None

    reply = await manager._run_agentic_ide_fast_path(
        f"Tell {first} and {second} to analyse the whole codebase"
    )

    assert reply is not None
    assert _sent_to(registry, first)
    # Both names appear — but the dead one must be reported as NOT reached, so
    # the sentence cannot be read as "both are working".
    assert second in reply
    lowered = reply.lower()
    assert any(word in lowered for word in ("not", "could not", "n't"))


async def test_a_single_addressed_pane_keeps_the_singular_reply(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """One pane is by far the common case and must not gain fan-out wording."""
    await _open(registry, tmp_path, 2)
    first, second = _names(registry)

    reply = await manager._run_agentic_ide_fast_path(
        f"Tell {first} to run the test suite"
    )

    assert reply is not None
    assert first in reply
    assert second not in reply
    assert not _sent_to(registry, second)


def _prompt_of(registry: Registry, name: str) -> str:
    assert registry.session is not None
    term = registry.session.find(name)
    assert term is not None
    return term.last_prompt or ""


async def test_a_split_request_gives_each_pane_its_own_brief(
    manager: BrainManager,
    registry: Registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The maintainer's actual ask: divide the deep dive across the fleet.

    The planner is left unreachable on purpose, so this also proves the feature
    works for a downloader with no quality-tier key at all (§3) — the
    deterministic directory split has to carry it.
    """
    from jarvis.agentic_ide import work_split

    monkeypatch.setattr(work_split, "_resolve_splitter", lambda: None)
    for sub in ("jarvis", "docs", "tests"):
        (tmp_path / sub).mkdir()
    await _open(registry, tmp_path, 2)
    first, second = _names(registry)

    reply = await manager._run_agentic_ide_fast_path(
        f"Tell {first} and {second} to analyse the codebase and "
        "split the work between you"
    )

    assert reply is not None
    assert _sent_to(registry, first)
    assert _sent_to(registry, second)
    # Different briefs, not the same sentence twice.
    assert _prompt_of(registry, first) != _prompt_of(registry, second)


async def test_without_a_split_request_both_panes_get_the_same_brief(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """"Both of you run the tests" is ONE order, not a division of labour."""
    await _open(registry, tmp_path, 2)
    first, second = _names(registry)

    await manager._run_agentic_ide_fast_path(
        f"Tell {first} and {second} to run the test suite"
    )

    # The fake composer echoes the instruction, so identical instructions mean
    # identical prompts apart from the pane's own name.
    one = _prompt_of(registry, first).replace(first, "X")
    two = _prompt_of(registry, second).replace(second, "X")
    assert one == two


async def test_a_split_is_never_planned_for_a_single_pane(
    manager: BrainManager,
    registry: Registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One agent cannot divide work with anyone — planning would be pure latency."""
    from jarvis.agentic_ide import work_split

    async def explode(*_a: object, **_kw: object) -> object:
        raise AssertionError("no split may be planned for one pane")

    monkeypatch.setattr(work_split, "split", explode)
    await _open(registry, tmp_path, 2)
    first, _second = _names(registry)

    reply = await manager._run_agentic_ide_fast_path(
        f"Tell {first} to analyse the codebase and split the work between areas"
    )

    assert reply is not None
    assert _sent_to(registry, first)


async def test_nobody_reachable_is_never_reported_as_sent(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 2)
    first, second = _names(registry)
    assert registry.session is not None
    for name in (first, second):
        term = registry.session.find(name)
        assert term is not None
        term.status = "exited"
        term.pty_id = None

    reply = await manager._run_agentic_ide_fast_path(
        f"Tell {first} and {second} to analyse the whole codebase"
    )

    assert reply is not None
    lowered = reply.lower()
    assert any(word in lowered for word in ("not", "could not", "n't"))
