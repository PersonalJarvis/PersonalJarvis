"""Shared defaults adopt owned storage without copying sessions or changing busy accounts."""

import asyncio
import os
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock, Timeout

from jarvis.society.browser.live import LiveSessions, LiveUpdates
from jarvis.society.browser.profiles import BrowserProfiles
from tests.fakes.fake_browser_profiles import ProfileSession


def agent(aid="lead", domains=()):
    return SimpleNamespace(agent_id=aid, browser_mode="own", browser_allowed_domains=domains)


def owned_folder(tmp_path, aid="lead"):
    folder = tmp_path / "society" / aid / "browser-profile"
    folder.mkdir(parents=True)
    (folder / "saved-session-marker").write_bytes(b"unchanged owned storage")
    return folder


def test_read_paths_do_not_bootstrap_or_consume_queued_adoption(tmp_path):
    folder = owned_folder(tmp_path)
    profiles = BrowserProfiles(tmp_path)
    before = profiles.resolve(agent())
    snapshot = profiles.snapshot([{"agent_id": "lead", "name": "Lead"}], set())

    profiles.queue_share_all_from_agent("lead")

    assert profiles.resolve(agent()) == before
    assert profiles.snapshot(snapshot["agents"], set()) == snapshot
    assert not snapshot["profiles"] and snapshot["default_profile_id"] is None
    assert list(folder.iterdir()) == [folder / "saved-session-marker"]


def test_new_install_gets_one_shared_default_for_current_and_future_agents(tmp_path):
    profiles = BrowserProfiles(tmp_path)
    assert profiles.ensure_shared_default(agent())
    binding = profiles.resolve(agent())
    assert binding.profile_id and binding.key == binding.profile_id
    assert binding.path == tmp_path / "society" / "browser-profiles" / binding.profile_id
    assert not binding.path.exists()  # The worker, not the registry, opens storage.
    restarted = BrowserProfiles(tmp_path)
    assert not restarted.ensure_shared_default(agent("future"))
    assert restarted.resolve(agent("future")).access_key == binding.access_key


def test_bootstrap_adopts_requesting_owned_folder_and_retains_domain_identity(tmp_path):
    folder = owned_folder(tmp_path)
    profiles = BrowserProfiles(tmp_path)
    source = agent(domains=("*.example.com", "x.com"))
    before = profiles.resolve(source)

    assert profiles.ensure_shared_default(source)

    shared = profiles.resolve(source)
    assert shared.profile_id and shared.access_key == before.access_key
    assert shared.path == folder and shared.key == "agent-lead"
    assert (folder / "saved-session-marker").read_bytes() == b"unchanged owned storage"
    assert not (tmp_path / "society" / "browser-profiles").exists()
    follower = profiles.resolve(agent("scout", domains=("news.example.com",)))
    assert follower.path == folder and follower.domains == ("news.example.com",)


@pytest.mark.parametrize("adopt", [False, True], ids=["fresh", "adopted"])
def test_shared_storage_preserves_full_agent_navigation_patterns(tmp_path, adopt):
    if adopt:
        owned_folder(tmp_path)
    profiles = BrowserProfiles(tmp_path)
    source = agent(domains=("https://example.com/*", "*.x.com", "http*://127.0.0.1"))
    before = profiles.resolve(source)

    profiles.ensure_shared_default(source)

    current = profiles.resolve(source)
    assert current.domains == before.domains
    if adopt:
        assert current.access_key == before.access_key


def test_bootstrap_preserves_explicit_overrides_and_existing_default(tmp_path):
    profiles = BrowserProfiles(tmp_path)
    assert profiles.ensure_shared_default(agent("lead"))
    chosen = profiles.create("Other account", "managed", [])["id"]
    profiles.assign("private", "own", None)
    profiles.assign("named", "profile", chosen)
    assert not profiles.ensure_shared_default(agent("private"))
    assert not profiles.ensure_shared_default(agent("named"))
    assert not profiles.ensure_shared_default(agent("lead"))
    assert profiles.resolve(agent("private")).profile_id is None
    assert profiles.resolve(agent("named")).profile_id == chosen
    profiles.share(chosen, "all", [], {"lead"})
    assert not profiles.ensure_shared_default(agent("future"))
    assert profiles.resolve(agent("future")).profile_id == chosen


def test_explicit_selected_and_revoked_configuration_never_reinitialize(tmp_path):
    profiles = BrowserProfiles(tmp_path)
    selected = profiles.create("Selected", "managed", [])["id"]
    profiles.share(selected, "selected", ["scout"], {"lead", "scout"})
    assert not profiles.needs_shared_default()
    assert not profiles.ensure_shared_default(agent("future"))
    assert profiles.resolve(agent("future")).profile_id is None
    profiles.share(selected, "all", [], {"lead", "scout"})
    profiles.remove(selected)
    assert not profiles.ensure_shared_default(agent("future"))
    with pytest.raises(ValueError, match="choose a profile"):
        profiles.resolve(agent("future"))


def test_legacy_selected_configuration_without_marker_is_not_reset(tmp_path):
    profiles = BrowserProfiles(tmp_path)
    profiles.path.parent.mkdir(parents=True)
    with sqlite3.connect(profiles.path) as db:
        db.executescript(
            "CREATE TABLE profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, "
            "domains TEXT NOT NULL, token_hash TEXT NOT NULL DEFAULT '', "
            "installation_id TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);"
            "CREATE TABLE bindings (agent_id TEXT PRIMARY KEY, "
            "mode TEXT NOT NULL, profile_id TEXT);"
            "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);"
            "INSERT INTO profiles VALUES('selected','Selected only','managed','[]','','',1);"
            "INSERT INTO bindings VALUES('lead','profile','selected');"
        )
    agents = [{"agent_id": aid, "name": aid} for aid in ("lead", "future")]
    before = profiles.snapshot(agents, set())

    assert not profiles.needs_shared_default()
    assert not profiles.ensure_shared_default(agent("future"))

    assert profiles.snapshot(agents, set()) == before
    assert profiles.resolve(agent("lead")).profile_id == "selected"
    assert profiles.resolve(agent("future")).profile_id is None


def test_queued_adoption_shares_original_storage_and_clears_all_overrides(tmp_path):
    folder = owned_folder(tmp_path, "source")
    profiles = BrowserProfiles(tmp_path)
    other = profiles.create("Other account", "managed", [])["id"]
    profiles.assign("source", "own", None)
    profiles.assign("lead", "profile", other)
    profiles.queue_share_all_from_agent("source")

    assert profiles.ensure_shared_default(agent("lead"))

    for aid in ("source", "lead", "future"):
        binding = BrowserProfiles(tmp_path).resolve(agent(aid))
        assert binding.path == folder and binding.key == "agent-source"
    snapshot = profiles.snapshot([{"agent_id": "lead", "name": "Lead"}], set())
    assert snapshot["bindings"]["lead"]["mode"] == "inherit"
    assert (folder / "saved-session-marker").read_bytes() == b"unchanged owned storage"


def test_source_opt_out_never_reuses_the_shared_account_even_after_revocation(tmp_path):
    shared_folder = owned_folder(tmp_path)
    profiles = BrowserProfiles(tmp_path)
    profiles.ensure_shared_default(agent())
    shared = profiles.resolve(agent())
    profiles.assign("lead", "own", None)
    private = profiles.resolve(agent())
    assert private.profile_id is None and private.key != shared.key
    assert private.path == tmp_path / "society" / "lead" / "browser-profile-private"
    assert not private.path.exists()
    assert profiles.resolve(agent("scout")).path == shared_folder
    profiles.remove(shared.profile_id)
    assert profiles.resolve(agent()).access_key == private.access_key
    assert (shared_folder / "saved-session-marker").exists()


@pytest.mark.parametrize("source", ["../outside", "a/b", "", "a\\b"])
def test_adoption_rejects_non_server_identity(source, tmp_path):
    with pytest.raises(ValueError, match="identity"):
        BrowserProfiles(tmp_path).queue_share_all_from_agent(source)


def test_pending_adoption_requires_existing_owned_storage(tmp_path):
    profiles = BrowserProfiles(tmp_path)
    with pytest.raises(ValueError, match="unavailable"):
        profiles.queue_share_all_from_agent("missing")
    assert not profiles.path.exists()


def test_adoption_rejects_an_external_directory_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "society" / "lead"
    parent.mkdir(parents=True)
    try:
        os.symlink(outside, parent / "browser-profile", target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory links is unavailable")
    with pytest.raises(ValueError, match="link or junction"):
        BrowserProfiles(tmp_path).queue_share_all_from_agent("lead")


def test_adoption_rejects_reparse_point_metadata(tmp_path, monkeypatch):
    folder = owned_folder(tmp_path)
    original = Path.lstat

    def reparse(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == folder:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(ValueError, match="link or junction"):
        BrowserProfiles(tmp_path).queue_share_all_from_agent("lead")


def install_existing(live, value, binding):
    session = ProfileSession(value.agent_id)
    session.profile_binding = binding
    session.profile_agent = value
    live.sessions[value.agent_id] = session
    return session


async def test_busy_other_account_prevents_adoption_then_idle_migration_preserves_source(tmp_path):
    owned_folder(tmp_path, "source")
    owned_folder(tmp_path, "other")
    live = LiveSessions(tmp_path)
    source_agent, other_agent = agent("source"), agent("other")
    source = install_existing(live, source_agent, live.profiles.resolve(source_agent))
    other = install_existing(live, other_agent, live.profiles.resolve(other_agent))
    source.state.update(manual=True, login_mode=True)
    source.subscribers.add(LiveUpdates())
    original_access = source.profile_binding.access_key
    lock_path = tmp_path / "society" / "browser-locks" / "agent-source.lock"
    lock_path.parent.mkdir(parents=True)
    lease = FileLock(str(lock_path), thread_local=False)
    lease.acquire(timeout=0)
    source.profile_lease = lease
    live.profiles.queue_share_all_from_agent("source")
    await other.run_lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="other browser session"):
            await live.ensure(source_agent, window_view=True)
        assert live.profiles.needs_shared_default()
        assert live.profiles.resolve(source_agent).profile_id is None
        assert not source.closed and not other.closed
        other.run_lock.release()

        result = await live.ensure(source_agent, window_view=True)

        assert result is source and not source.closed
        assert source.profile_lease is lease and lease.is_locked
        assert source.profile_binding.access_key == original_access
        assert source.profile_binding.profile_id
        assert other.closed and "other" not in live.sessions
        assert not live.profiles.needs_shared_default()
        with pytest.raises(Timeout):
            FileLock(str(lock_path), thread_local=False).acquire(timeout=0)
        with pytest.raises(RuntimeError, match="in use"):
            await live.ensure(agent("future"))
    finally:
        if other.run_lock.locked():
            other.run_lock.release()
        await live.close()


def test_explicit_configuration_after_queue_cancels_pending_adoption(tmp_path):
    owned_folder(tmp_path)
    profiles = BrowserProfiles(tmp_path)
    profiles.queue_share_all_from_agent("lead")
    chosen = profiles.create("Selected instead", "managed", [])["id"]
    profiles.share(chosen, "selected", ["lead"], {"lead"})
    assert not profiles.ensure_shared_default(agent("future"))
    assert profiles.resolve(agent()).profile_id == chosen


@pytest.mark.parametrize("cancel_during_commit", [False, True])
async def test_migration_reserves_idle_run_admission_until_invalidation(
    tmp_path, monkeypatch, cancel_during_commit
):
    owned_folder(tmp_path, "source")
    owned_folder(tmp_path, "other")
    live = LiveSessions(tmp_path)
    source_agent, other_agent = agent("source"), agent("other")
    other_agent.model = "fixture"
    source = install_existing(live, source_agent, live.profiles.resolve(source_agent))
    other = install_existing(live, other_agent, live.profiles.resolve(other_agent))
    live.profiles.queue_share_all_from_agent("source")
    claim_waiting, release_claim = asyncio.Event(), asyncio.Event()
    db_waiting, release_db = threading.Event(), threading.Event()
    original = live.profiles.ensure_shared_default

    async def ensured(value):
        return other

    async def claim_wait(session, chat_session_id):
        claim_waiting.set()
        await release_claim.wait()

    def delayed_commit(*args, **kwargs):
        db_waiting.set()
        if not release_db.wait(timeout=5):
            raise RuntimeError("Fixture commit was not released")
        return original(*args, **kwargs)

    async def prepare():
        async with live.profile_start_lock:
            await live._prepare_shared_default(source_agent)

    monkeypatch.setattr(live, "ensure", ensured)
    monkeypatch.setattr("jarvis.society.browser.live.claim_browser", claim_wait)
    monkeypatch.setattr(live.profiles, "ensure_shared_default", delayed_commit)
    run = asyncio.create_task(
        live.run(other_agent, task="fixture", max_steps=1, llm=None, action=None)
    )
    migration = None
    try:
        await asyncio.wait_for(claim_waiting.wait(), timeout=1)
        migration = asyncio.create_task(prepare())
        assert await asyncio.to_thread(db_waiting.wait, 2)
        assert other.run_lock.locked()
        if cancel_during_commit:
            migration.cancel()
            await asyncio.sleep(0)
            assert not migration.done() and other.run_lock.locked()
        release_claim.set()
        with pytest.raises(RuntimeError, match="busy"):
            await run
        assert not any(op == "run" for op, *_ in other.commands)
        release_db.set()
        if cancel_during_commit:
            with pytest.raises(asyncio.CancelledError):
                await migration
        else:
            await migration
        assert other.closed and not source.closed
    finally:
        release_claim.set()
        release_db.set()
        for task in (run, migration):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (run, migration) if task), return_exceptions=True)
        await live.close()


async def test_run_waiting_past_migration_cannot_use_the_closed_identity(tmp_path, monkeypatch):
    live = LiveSessions(tmp_path)
    value = agent("other")
    value.model = "fixture"
    session = install_existing(live, value, live.profiles.resolve(value))

    async def ensured(agent):
        return session

    async def replaced_during_claim(current, chat_session_id):
        current.closed = True
        live.sessions.pop(value.agent_id)

    monkeypatch.setattr(live, "ensure", ensured)
    monkeypatch.setattr("jarvis.society.browser.live.claim_browser", replaced_during_claim)
    try:
        with pytest.raises(RuntimeError, match="profile changed"):
            await live.run(value, task="fixture", max_steps=1, llm=None, action=None)
        assert not any(op == "run" for op, *_ in session.commands)
    finally:
        await live.close()
