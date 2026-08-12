"""Which subscription a pane actually runs on.

The switch is only worth anything if the pane's environment really carries it,
so these drive the registry end to end and read the environment the PTY was
handed — not the intent, the result.

The other half is the trap this feature could create: a user switches the
default while agents are running. A pane that silently followed would resume a
conversation on a plan whose history has never seen it, and the agent would come
back amnesiac with no explanation. Pinning the account at pane CREATION is what
prevents that, and it is pinned down here.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from jarvis import agent_accounts
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry, SessionError
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def _launched_from_a_plain_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assert the ACCOUNT, on a spawn nothing else is interfering with.

    Several assertions here read "no environment was passed at all", which is the
    promise to every user who never opens the account switcher. That promise holds
    for an app started from a plain terminal; an app started from a coding-agent
    session hands its panes an environment on purpose, with that session's markers
    removed (``test_parent_session_env``). Without this fixture the same suite
    would pass or fail depending on which terminal it was launched from — and CI
    runs it from one of each.
    """
    for marker in session_mod.PARENT_AGENT_SESSION_VARS:
        monkeypatch.delenv(marker, raising=False)


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return Registry(pty_manager=fake_pty)


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _attach(registry: Registry, name: str) -> None:
    await registry.attach(name, 80, 24, _noop, _noop_exit)


def _env_of(fake_pty: FakePtyManager, index: int = 0) -> dict[str, str] | None:
    return fake_pty.spawns[index]["env"]


# ------------------------------------------------------------------- pinning


async def test_a_pane_is_pinned_to_the_active_account_when_it_is_created(
    registry: Registry, tmp_path: Path
) -> None:
    second = agent_accounts.create_account("claude", "Second seat")
    agent_accounts.set_active("claude", second.id)
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    assert registry.session.terminals[0].account == second.id


async def test_a_pane_may_be_opened_on_an_account_that_is_not_the_active_one(
    registry: Registry, tmp_path: Path
) -> None:
    """This is what makes two subscriptions usable AT THE SAME TIME."""
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(
        str(tmp_path),
        [{"agent": "claude"}, {"agent": "claude", "account": second.id}],
    )
    panes = registry.session.terminals
    assert panes[0].account == agent_accounts.builtin_id("claude")
    assert panes[1].account == second.id


async def test_switching_the_default_does_not_move_a_running_pane(
    registry: Registry, tmp_path: Path
) -> None:
    """The whole reason the account is pinned at creation rather than read live."""
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    pinned = registry.session.terminals[0].account
    second = agent_accounts.create_account("claude", "Second seat")
    agent_accounts.set_active("claude", second.id)
    assert registry.session.terminals[0].account == pinned


async def test_a_new_pane_after_the_switch_gets_the_new_account(
    registry: Registry, tmp_path: Path
) -> None:
    """A terminal opened with no anchor is a NEW terminal, not a split.

    It therefore follows the active account. Inheriting from whatever pane
    happened to be last (the first version) made the switch reach nothing a user
    could predict: flipping to the second seat and opening a terminal still
    billed the first one.
    """
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    second = agent_accounts.create_account("claude", "Second seat")
    agent_accounts.set_active("claude", second.id)
    added = await registry.add_terminal(agent="claude", anchor=None)
    assert added.account == second.id


async def test_a_switch_on_the_subscriptions_page_reaches_the_workspace(
    registry: Registry, tmp_path: Path
) -> None:
    """The store is the ONE source of the active account — no registry shadow.

    The app has two switch surfaces: the workspace's own settings (which go
    through the registry) and the Subscriptions page (which writes the store
    directly). An in-memory copy inside the registry made the first switch
    permanent: once used, a later switch on the Subscriptions page never
    reached new panes, which kept opening on the seat the user had just left.
    """
    first = agent_accounts.create_account("claude", "First seat")
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.set_active_account("claude", first.id)
    # The Subscriptions page writes the store directly, not via the registry.
    agent_accounts.set_active("claude", second.id)
    assert registry.active_account_id("claude") == second.id
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    assert registry.session.terminals[0].account == second.id


async def test_a_split_stays_on_the_account_its_anchor_runs_on(
    registry: Registry, tmp_path: Path
) -> None:
    """Splitting must not quietly move the work onto a different bill."""
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude", "account": second.id}])
    anchor = registry.session.terminals[0]
    split = await registry.add_terminal(anchor=anchor.name, direction="down")
    assert split.account == second.id


async def test_a_split_onto_a_different_cli_does_not_inherit_the_account(
    registry: Registry, tmp_path: Path
) -> None:
    """A Claude account id means nothing to Codex."""
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude", "account": second.id}])
    anchor = registry.session.terminals[0]
    split = await registry.add_terminal(anchor=anchor.name, agent="codex")
    assert split.account == agent_accounts.builtin_id("codex")


async def test_an_unknown_account_falls_back_instead_of_failing_the_pane(
    registry: Registry, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude", "account": "claude:ghost"}])
    assert registry.session.terminals[0].account == agent_accounts.builtin_id("claude")


# ------------------------------------------------------- the workspace switch


async def test_the_workspace_starts_on_the_stored_default(registry: Registry) -> None:
    """Nothing chosen here yet — so the answer is the machine's own default."""
    assert registry.active_account_id("claude") == agent_accounts.builtin_id("claude")
    assert registry.active_account_id("codex") == agent_accounts.builtin_id("codex")


async def test_switching_in_the_workspace_moves_the_next_terminal(
    registry: Registry, tmp_path: Path
) -> None:
    """The whole point of the settings panel, as one assertion."""
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    await registry.set_active_account("claude", second.id)
    assert registry.active_account_id("claude") == second.id
    added = await registry.add_terminal(agent="claude")
    assert added.account == second.id


async def test_switching_in_the_workspace_leaves_running_panes_alone(
    registry: Registry, tmp_path: Path
) -> None:
    """A running agent must never be moved onto a plan that has not seen it."""
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    pinned = registry.session.terminals[0].account
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.set_active_account("claude", second.id)
    assert registry.session.terminals[0].account == pinned


async def test_the_switch_survives_a_restart(registry: Registry) -> None:
    """Written through to the stored default, so a fresh registry agrees.

    Two surfaces can switch this — the workspace and the app's account page —
    and a workspace-only pin would let them disagree the moment one was used.
    """
    second = agent_accounts.create_account("codex", "Second plan")
    await registry.set_active_account("codex", second.id)
    assert agent_accounts.active_account("codex").id == second.id
    assert Registry().active_account_id("codex") == second.id


async def test_switching_to_an_account_of_another_cli_is_refused(
    registry: Registry,
) -> None:
    """A Claude account id means nothing to Codex — say so instead of guessing."""
    second = agent_accounts.create_account("claude", "Second seat")
    with pytest.raises(SessionError):
        await registry.set_active_account("codex", second.id)
    with pytest.raises(SessionError):
        await registry.set_active_account("claude", "claude:ghost")


async def test_a_batch_of_terminals_opens_on_the_active_account(
    registry: Registry, tmp_path: Path
) -> None:
    """ "Open five more" is five new terminals, so all five follow the switch."""
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    await registry.set_active_account("claude", second.id)
    created, capped = await registry.add_terminals(3, agent="claude")
    assert capped is False
    assert [t.account for t in created] == [second.id] * 3


async def test_a_split_of_a_default_pane_follows_the_switch(
    registry: Registry, tmp_path: Path
) -> None:
    """A pane that merely followed the default has no seat worth propagating.

    Splits used to inherit their anchor's account unconditionally, and in a
    workspace whose panes all shared one seat that made the switcher
    unreachable: every new pane was a split, every split resurrected the seat
    the user had just left, and the switch changed nothing anyone could see —
    the 2026-08-12 report, "I changed my subscriptions twice and it doesn't
    change". Only a DELIBERATELY chosen seat is inherited (the test below).
    """
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    anchor = registry.session.terminals[0]
    await registry.set_active_account("claude", second.id)
    split = await registry.add_terminal(anchor=anchor.name, direction="down")
    assert split.account == second.id


async def test_a_split_of_a_deliberately_seated_pane_keeps_that_seat(
    registry: Registry, tmp_path: Path
) -> None:
    """An explicitly chosen seat survives both the split and a later switch."""
    second = agent_accounts.create_account("claude", "Second seat")
    third = agent_accounts.create_account("claude", "Third seat")
    await registry.start(str(tmp_path), [{"agent": "claude", "account": second.id}])
    anchor = registry.session.terminals[0]
    await registry.set_active_account("claude", third.id)
    split = await registry.add_terminal(anchor=anchor.name, direction="down")
    assert split.account == second.id


async def test_the_pin_travels_through_generations_of_splits(
    registry: Registry, tmp_path: Path
) -> None:
    """Splitting a split of a chosen seat is still "another one of these"."""
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude", "account": second.id}])
    first_split = await registry.add_terminal(
        anchor=registry.session.terminals[0].name, direction="down"
    )
    second_split = await registry.add_terminal(anchor=first_split.name, direction="down")
    assert second_split.account == second.id


async def test_the_state_names_the_active_account_in_words(
    registry: Registry, tmp_path: Path
) -> None:
    """An id is not something anybody can read back — the UI shows the label."""
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    await registry.set_active_account("claude", second.id)
    claude = next(row for row in registry.state()["accounts"] if row["agent"] == "claude")
    assert claude["active_account"] == second.id
    assert claude["active_label"] == "Second seat"
    assert claude["display_name"] == "Claude Code"
    # Built-in plus the added one — what tells a UI there is anything to choose.
    assert claude["account_count"] == 2


async def test_a_terminal_opened_after_the_switch_spawns_on_that_config_dir(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """End to end: the switch has to reach the child environment, or it is talk."""
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    await registry.set_active_account("claude", second.id)
    added = await registry.add_terminal(agent="claude")
    await _attach(registry, added.name)
    env = _env_of(fake_pty, -1)
    assert env is not None
    assert env["CLAUDE_CONFIG_DIR"] == str(second.config_dir)


# --------------------------------------------------------------- the spawn


async def test_a_pane_on_an_added_account_spawns_with_that_config_dir(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude", "account": second.id}])
    await _attach(registry, registry.session.terminals[0].name)
    env = _env_of(fake_pty)
    assert env is not None
    assert env["CLAUDE_CONFIG_DIR"] == str(second.config_dir)


async def test_a_pane_on_the_builtin_account_spawns_exactly_as_it_always_did(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """No environment is passed at all — plain inheritance, byte for byte.

    This is the promise to everyone who never opens the switcher: adding the
    feature changed nothing about how their panes start. Read together with
    ``_launched_from_a_plain_shell`` above, which is what makes "at all" a
    statement about this code rather than about the terminal running the suite.
    """
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    await _attach(registry, registry.session.terminals[0].name)
    assert _env_of(fake_pty) is None


async def test_the_spawn_environment_keeps_PATH(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A replaced environment would leave the agent binary unresolvable."""
    second = agent_accounts.create_account("codex", "Second plan")
    await registry.start(str(tmp_path), [{"agent": "codex", "account": second.id}])
    await _attach(registry, registry.session.terminals[0].name)
    env = _env_of(fake_pty)
    assert env is not None
    assert env.get("PATH")
    assert env["CODEX_HOME"] == str(second.config_dir)


async def test_a_pane_on_an_added_account_still_runs_the_users_own_setup(
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The switch must not cost the user their CLI.

    Redirecting the config dir moves the CLI's whole user level with it, so
    without provisioning a pane on a second subscription opens with none of the
    user's skills, plugins, hooks or global instructions — a quieter, different
    product than the same CLI in a terminal. Asserted at the spawn, because the
    provisioning is only worth anything if it happens before the agent starts.
    """
    own = Path(agent_accounts.native_dir("claude"))
    (own / "skills" / "git-rescue").mkdir(parents=True)
    (own / "skills" / "git-rescue" / "SKILL.md").write_text("# rescue\n", encoding="utf-8")
    (own / "CLAUDE.md").write_text("Answer in plain language.\n", encoding="utf-8")

    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude", "account": second.id}])
    await _attach(registry, registry.session.terminals[0].name)

    assert (second.config_dir / "skills" / "git-rescue" / "SKILL.md").is_file()
    assert (second.config_dir / "CLAUDE.md").is_file()
    # ...and the login it was opened for is still the only identity in there.
    assert not (second.config_dir / ".credentials.json").exists()


async def test_a_pane_pre_trusts_the_folder_in_its_own_account_directory(
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CLI reads trust from the directory it was pointed at.

    Seeded only into the machine's default, an added-account pane opens on the
    "do you trust this directory?" dialog — and a dialog nobody can answer from
    voice or the prompt bar is an agent that never starts.
    """
    from jarvis.workspace import trust

    seen: list[tuple[str, list[str], dict[str, list[Path]] | None]] = []

    def _record(root: Path, agents: list[str], **kwargs: object) -> list[object]:
        seen.append((str(root), agents, kwargs.get("config_dirs")))  # type: ignore[arg-type]
        return []

    monkeypatch.setattr(trust, "ensure_trusted", _record)
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude", "account": second.id}])
    await _attach(registry, registry.session.terminals[0].name)

    seeded = [call for call in seen if call[2]]
    assert seeded, "the pane's own account directory was never pre-trusted"
    assert seeded[0][2] == {"claude": [second.config_dir]}
    # Once per folder and account, however many panes attach to it.
    await _attach(registry, registry.session.terminals[0].name)
    assert len([call for call in seen if call[2]]) == 1


async def test_the_trust_seeding_and_the_setup_run_under_one_lock(
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both write Claude Code's .claude.json, and panes attach concurrently.

    Held apart, the second read-modify-write is built on a document read before
    the first one landed and drops it — a lost trust entry is a dialog the user
    has to click, a lost merge is a pane without its connectors.
    """
    from jarvis import agent_config_parity
    from jarvis.workspace import trust

    real_lock = agent_config_parity.setup_lock
    events: list[str] = []

    def _tracking_lock(path: Path) -> object:
        events.append("lock")
        return real_lock(path)

    def _record(_root: Path, _agents: list[str], **kwargs: object) -> list[object]:
        # The workspace open seeds the machine's own config; only the per-account
        # seeding is the one that has to happen under the lock.
        events.append("trust:account" if kwargs.get("config_dirs") else "trust:machine")
        return []

    monkeypatch.setattr(agent_config_parity, "setup_lock", _tracking_lock)
    monkeypatch.setattr(trust, "ensure_trusted", _record)
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude", "account": second.id}])
    await _attach(registry, registry.session.terminals[0].name)

    assert "trust:account" in events
    assert events.index("lock") < events.index("trust:account")


async def test_same_account_setup_waiters_do_not_fill_the_default_executor(
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the async gate owner may enter blocking account preparation."""
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(
        str(tmp_path),
        [
            {"agent": "claude", "account": second.id},
            {"agent": "claude", "account": second.id},
        ],
    )
    real_prepare = registry._prepare_spawn
    release = threading.Event()
    entered = threading.Event()
    counter_lock = threading.Lock()
    active = 0
    peak = 0

    def _blocking_prepare(*args: object) -> dict[str, str] | None:
        nonlocal active, peak
        with counter_lock:
            active += 1
            peak = max(peak, active)
        entered.set()
        try:
            release.wait(timeout=2.0)
            return real_prepare(*args)  # type: ignore[arg-type]
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setattr(registry, "_prepare_spawn", _blocking_prepare)
    tasks = [
        asyncio.create_task(_attach(registry, term.name)) for term in registry.session.terminals
    ]
    for _ in range(100):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert entered.is_set()
    await asyncio.sleep(0.05)
    assert peak == 1
    release.set()
    await asyncio.gather(*tasks)


async def test_a_pane_on_the_builtin_login_is_not_pre_trusted_twice(
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing was redirected, so the workspace open already covered it."""
    from jarvis.workspace import trust

    seen: list[dict[str, list[Path]] | None] = []

    def _record(_root: Path, _agents: list[str], **kwargs: object) -> list[object]:
        seen.append(kwargs.get("config_dirs"))  # type: ignore[arg-type]
        return []

    monkeypatch.setattr(trust, "ensure_trusted", _record)
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    await _attach(registry, registry.session.terminals[0].name)
    assert [call for call in seen if call] == []


async def test_two_panes_can_run_two_different_subscriptions_at_once(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The point of holding two plans, expressed as one assertion."""
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(
        str(tmp_path),
        [{"agent": "claude"}, {"agent": "claude", "account": second.id}],
    )
    for term in registry.session.terminals:
        await _attach(registry, term.name)
    first_env, second_env = _env_of(fake_pty, 0), _env_of(fake_pty, 1)
    # The default pane inherits untouched; the second is pinned to its own seat.
    assert first_env is None
    assert second_env is not None
    assert second_env["CLAUDE_CONFIG_DIR"] == str(second.config_dir)


# ------------------------------------------------------------------- resume


async def test_a_pane_looks_for_its_conversation_in_its_own_account(
    registry: Registry, tmp_path: Path
) -> None:
    """The silent-amnesia bug: the right handle, searched in the wrong folder."""
    from jarvis.agentic_ide.agent_sessions import ResumeHandle, has_conversation

    second = agent_accounts.create_account("claude", "Second seat")
    handle = ResumeHandle(
        kind="claude_session", id="11111111-2222-3333-4444-555555555555", captured_at=0.0
    )
    projects = second.config_dir / "projects" / "some-repo"
    projects.mkdir(parents=True, exist_ok=True)
    (projects / f"{handle.id}.jsonl").write_text("{}\n", encoding="utf-8")

    # Found when asked about the account that holds it...
    assert has_conversation("claude", handle, second.config_dir) is True
    # ...and honestly absent from the default account, which never saw it.
    assert has_conversation("claude", handle, tmp_path / "elsewhere") is False


async def test_a_remembered_pane_carries_its_account_through_the_store() -> None:
    """Deliberately at the pane level, not the snapshot wrapper's.

    What has to survive is the pane's account: the wrapper around it has
    changed shape before and will again, while "this pane ran on that seat" is
    the fact the reopen depends on.
    """
    from jarvis.agentic_ide import resume_store

    second = agent_accounts.create_account("codex", "Second plan")
    pane = resume_store.SnapshotTerminal(
        key="alex", name="Alex", agent="codex", account=second.id, account_pinned=True
    )
    restored = resume_store.SnapshotTerminal.from_dict(pane.to_dict())
    assert restored is not None
    assert restored.account == second.id
    # The pin rides along: a deliberately chosen seat must still be worth
    # propagating to splits after the app restarts.
    assert restored.account_pinned is True


async def test_an_older_snapshot_without_an_account_still_reopens() -> None:
    """A build that predates the switcher must keep resuming."""
    from jarvis.agentic_ide import resume_store

    restored = resume_store.SnapshotTerminal.from_dict(
        {"key": "alex", "name": "Alex", "agent": "claude", "column": 0, "slot": 0}
    )
    assert restored is not None
    assert restored.account is None
    # No pin on older snapshots: an unpinned pane's splits follow the
    # switcher, which is the safe direction to fail in.
    assert restored.account_pinned is False
