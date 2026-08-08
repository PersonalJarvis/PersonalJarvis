"""A pane on an added subscription runs the user's OWN CLI setup.

The bug these pin down was invisible and total: switching a pane to a second
subscription redirects the CLI's config directory, that directory carries the
whole user level of the CLI, and so the pane silently lost every skill, every
plugin, every hook and the user's global instructions. It still answered — it
just answered as a different, emptier product than the same CLI in a terminal.

So what is asserted here is not "a function ran" but the three properties that
decide whether the fix is real:

1. the user's setup ARRIVES in the redirected directory (and keeps arriving
   after they add something new),
2. the account's IDENTITY is never touched on the way — no credential, no
   conversation record, and no value the account set for itself,
3. every host can do it: a symlink where symlinks work, a junction on Windows
   where they need a privilege, and a copy where neither is possible.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jarvis import agent_accounts, agent_config_parity
from jarvis.agent_config_parity import ensure_parity


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway store, account root and "user's own config" — never the real ones."""
    monkeypatch.setattr(agent_accounts, "_store_path", lambda: tmp_path / "accounts.json")
    monkeypatch.setattr(agent_accounts, "_accounts_root", lambda: tmp_path / "dirs")
    monkeypatch.setattr(
        agent_accounts, "_native_dir", lambda platform: tmp_path / "native" / platform
    )
    return tmp_path


@pytest.fixture
def native(tmp_path: Path) -> Path:
    """The user's own Claude Code config directory, with a real setup in it."""
    home = tmp_path / "native" / "claude"
    (home / "skills" / "git-rescue").mkdir(parents=True)
    (home / "skills" / "git-rescue" / "SKILL.md").write_text("# rescue\n", encoding="utf-8")
    (home / "agents").mkdir()
    (home / "agents" / "code-reviewer.md").write_text("reviewer\n", encoding="utf-8")
    (home / "CLAUDE.md").write_text("Answer in plain language.\n", encoding="utf-8")
    (home / "settings.json").write_text(
        json.dumps({"model": "opus", "enabledPlugins": {"github@official": True}}),
        encoding="utf-8",
    )
    # Identity and conversation record — present so a test can prove they are
    # NOT what gets shared.
    (home / ".credentials.json").write_text('{"token": "user-secret"}', encoding="utf-8")
    (home / "projects").mkdir()
    (home / "projects" / "a.jsonl").write_text("{}\n", encoding="utf-8")
    return home


def _account(label: str = "Second seat") -> agent_accounts.AgentAccount:
    return agent_accounts.create_account("claude", label)


def _links_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host that refuses every kind of link — Windows without Developer Mode
    and without the junction API, or a filesystem that has neither."""

    def _no_symlink(*_args: object, **_kwargs: object) -> None:
        raise OSError("symlinks are not available here")

    monkeypatch.setattr(os, "symlink", _no_symlink)
    monkeypatch.setattr(agent_config_parity, "_link", lambda *_a, **_k: None)


# ------------------------------------------------------------- the setup arrives


def test_an_added_account_sees_the_users_skills(native: Path) -> None:
    """The reported bug, as one assertion: 93 skills in a terminal, none in a pane."""
    account = _account()
    report = ensure_parity("claude", account.id)
    assert "skills" in report.shared
    assert (account.config_dir / "skills" / "git-rescue" / "SKILL.md").is_file()


def test_the_users_subagents_settings_and_memory_arrive_too(native: Path) -> None:
    """Skills were the symptom; the whole user level is the fix."""
    account = _account()
    ensure_parity("claude", account.id)
    assert (account.config_dir / "agents" / "code-reviewer.md").is_file()
    assert (account.config_dir / "CLAUDE.md").read_text(encoding="utf-8").startswith("Answer")
    settings = json.loads((account.config_dir / "settings.json").read_text(encoding="utf-8"))
    assert settings["enabledPlugins"] == {"github@official": True}


def test_a_skill_added_later_shows_up_without_a_second_copy(native: Path) -> None:
    """Shared, not snapshotted — a link means today's install is in yesterday's pane.

    On a host that had to copy instead, the re-run is what carries it, which is
    why parity is refreshed before every spawn rather than once per account.
    """
    account = _account()
    ensure_parity("claude", account.id)
    (native / "skills" / "brand-new").mkdir()
    (native / "skills" / "brand-new" / "SKILL.md").write_text("# new\n", encoding="utf-8")
    ensure_parity("claude", account.id)
    assert (account.config_dir / "skills" / "brand-new" / "SKILL.md").is_file()


def test_a_global_settings_change_follows_through(native: Path) -> None:
    account = _account()
    ensure_parity("claude", account.id)
    (native / "settings.json").write_text(json.dumps({"model": "sonnet"}), encoding="utf-8")
    ensure_parity("claude", account.id)
    settings = json.loads((account.config_dir / "settings.json").read_text(encoding="utf-8"))
    assert settings == {"model": "sonnet"}


def test_the_user_scope_mcp_servers_are_merged_without_the_identity(native: Path) -> None:
    """Connectors live in a document that also holds the login — one key, not the file."""
    (native / ".claude.json").write_text(
        json.dumps({"mcpServers": {"chrome": {"command": "chrome-mcp"}}, "userID": "user-1"}),
        encoding="utf-8",
    )
    account = _account()
    account.config_dir.mkdir(parents=True, exist_ok=True)
    (account.config_dir / ".claude.json").write_text(
        json.dumps({"userID": "account-2", "oauthAccount": {"emailAddress": "b@example.com"}}),
        encoding="utf-8",
    )
    report = ensure_parity("claude", account.id)
    assert report.shared[".claude.json#mcpServers"] == "merged"
    doc = json.loads((account.config_dir / ".claude.json").read_text(encoding="utf-8"))
    assert doc["mcpServers"] == {"chrome": {"command": "chrome-mcp"}}
    # The account's own identity is still the account's own.
    assert doc["userID"] == "account-2"
    assert doc["oauthAccount"] == {"emailAddress": "b@example.com"}


def test_codex_shares_its_config_skills_rules_and_prompts(tmp_path: Path) -> None:
    """The same promise for the other CLI — never a Claude-only fix (AP-21).

    The entry names are the ones a real Codex home carries (measured
    2026-07-27): its skills, its standing rules, its plugins and its one
    config file, which is where its MCP servers live.
    """
    home = tmp_path / "native" / "codex"
    for folder, marker in (
        ("prompts", "review.md"),
        ("skills", "recap"),
        ("rules", "default.rules"),
    ):
        (home / folder).mkdir(parents=True)
        (home / folder / marker).write_text("x\n", encoding="utf-8")
    (home / "config.toml").write_text(
        '[mcp_servers.chrome]\ncommand = "chrome-mcp"\n', encoding="utf-8"
    )
    # Identity, in both places Codex keeps it.
    (home / "auth.json").write_text('{"token": "codex-secret"}', encoding="utf-8")
    (home / "secrets").mkdir()
    (home / "secrets" / "mcp_oauth.age").write_text("encrypted", encoding="utf-8")

    account = agent_accounts.create_account("codex", "Second plan")
    report = ensure_parity("codex", account.id)

    assert "config.toml" in report.shared
    assert (account.config_dir / "prompts" / "review.md").is_file()
    assert (account.config_dir / "skills" / "recap").is_file()
    assert (account.config_dir / "rules" / "default.rules").is_file()
    assert "mcp_servers" in (account.config_dir / "config.toml").read_text(encoding="utf-8")
    # An allowlist is a promise about what it does NOT do: a login and an
    # encrypted credential store are not on it, so no code path can reach them.
    assert not (account.config_dir / "auth.json").exists()
    assert not (account.config_dir / "secrets").exists()


# ------------------------------------------------------------------ drift guard


def test_every_cli_that_can_be_redirected_has_a_setup_allowlist() -> None:
    """A CLI gains accounts, and its panes silently lose the user's setup again.

    That is how this bug arrives a second time: redirecting a config dir is one
    ``AccountSpec`` on a registry entry, while carrying the user's setup across
    is a table somebody has to remember. The two must not be able to drift
    apart — so a platform with an override and no allowlist fails here rather
    than in somebody's terminal six months from now.

    This is also the gate that decides whether a NEW coding CLI may offer
    several seats at all: if nothing of its setup can be shared safely (a CLI
    that keeps its configuration and its key in one file), it must not declare
    an override in the first place.
    """
    for platform in agent_accounts.platforms():
        assert agent_accounts.env_var(platform)
        entries = agent_config_parity.USER_SETUP.get(platform)
        assert entries, f"{platform} can be redirected but shares nothing back"
        # Every entry is one the CLI reads; nothing that identifies the account.
        names = {entry.name for entry in entries}
        assert not names & {
            ".credentials.json",
            "auth.json",
            "secrets",
            ".claude.json",
            "projects",
            "sessions",
            "history.jsonl",
        }


def test_a_merged_key_never_names_a_whole_identity_document() -> None:
    """Merging is the concession made for documents that hold the login too.

    It stays a concession: one key at a time, never the file, and never a key
    that carries the identity itself.
    """
    for merges in agent_config_parity.MERGED_KEYS.values():
        for merged in merges:
            assert merged.key not in {"oauthAccount", "userID", "projects"}


# ------------------------------------------------------------- identity is safe


def test_the_login_and_the_conversation_record_are_never_shared(native: Path) -> None:
    """The one thing that must never travel: a copied OAuth token dies in place,
    and a shared conversation record would hand one account's history to another.
    """
    account = _account()
    ensure_parity("claude", account.id)
    assert not (account.config_dir / ".credentials.json").exists()
    assert not (account.config_dir / "projects").exists()


def test_a_value_the_account_set_for_itself_is_left_alone(native: Path) -> None:
    """A pane may change its own settings; the next spawn must not undo that.

    And the other half, which is what makes this a fix rather than a trade: the
    keys the account is MISSING still arrive. An account whose settings file a
    pane once touched would otherwise keep its theme and lose every plugin — the
    exact half-fixed state this was measured in on 2026-07-27.
    """
    account = _account()
    ensure_parity("claude", account.id)
    own = account.config_dir / "settings.json"
    own.write_text(json.dumps({"model": "haiku", "theme": "dark"}), encoding="utf-8")
    report = ensure_parity("claude", account.id)
    settings = json.loads(own.read_text(encoding="utf-8"))
    assert settings["model"] == "haiku"  # the pane's own choice, untouched
    assert settings["theme"] == "dark"
    assert settings["enabledPlugins"] == {"github@official": True}  # ...and the user's
    assert report.shared["settings.json"] == "merged"


def test_a_nested_setting_the_account_lacks_is_filled_in(native: Path) -> None:
    """Top-level only would drop the user's permissions behind an existing key."""
    (native / "settings.json").write_text(
        json.dumps({"permissions": {"defaultMode": "plan", "allow": ["Bash(ls:*)"]}}),
        encoding="utf-8",
    )
    account = _account()
    own = account.config_dir / "settings.json"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text(json.dumps({"permissions": {"defaultMode": "acceptEdits"}}), encoding="utf-8")
    ensure_parity("claude", account.id)
    permissions = json.loads(own.read_text(encoding="utf-8"))["permissions"]
    assert permissions["defaultMode"] == "acceptEdits"  # the account's own
    assert permissions["allow"] == ["Bash(ls:*)"]  # the user's, no longer missing


def test_the_users_memory_file_is_not_merged_line_by_line(native: Path) -> None:
    """Plain text has no keys — an account file of its own stays its own."""
    account = _account()
    own = account.config_dir / "CLAUDE.md"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text("Account house style.\n", encoding="utf-8")
    report = ensure_parity("claude", account.id)
    assert own.read_text(encoding="utf-8") == "Account house style.\n"
    assert any(name == "CLAUDE.md" for name, _why in report.skipped)


def test_a_merged_file_is_not_replaced_wholesale_on_the_next_run(native: Path) -> None:
    """The hybrid is nobody's copy, so it must never be mistaken for a mirror."""
    account = _account()
    own = account.config_dir / "settings.json"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text(json.dumps({"model": "haiku"}), encoding="utf-8")
    ensure_parity("claude", account.id)
    second = ensure_parity("claude", account.id)
    assert json.loads(own.read_text(encoding="utf-8"))["model"] == "haiku"
    assert second.shared["settings.json"] == "current"


def test_an_account_skill_of_its_own_survives_the_merge(native: Path) -> None:
    """What the account has stays; only what it is MISSING is shared in."""
    account = _account()
    theirs = account.config_dir / "skills" / "account-only"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("account", encoding="utf-8")
    ensure_parity("claude", account.id)
    assert (theirs / "SKILL.md").is_file()
    assert (account.config_dir / "skills" / "git-rescue" / "SKILL.md").is_file()


def test_a_plugin_tree_the_cli_half_filled_is_replaced_as_a_whole(native: Path) -> None:
    """Which plugins are ACTIVE lives in state files beside the marketplaces they
    name, so the user's marketplaces next to the account's own state list nothing
    the CLI can load. What was there is kept beside it, not deleted.
    """
    (native / "plugins" / "marketplaces" / "official").mkdir(parents=True)
    (native / "plugins" / "installed_plugins.json").write_text("{}", encoding="utf-8")
    account = _account()
    stale = account.config_dir / "plugins" / "marketplaces" / "official"
    stale.mkdir(parents=True)
    ensure_parity("claude", account.id)
    assert (account.config_dir / "plugins" / "installed_plugins.json").exists()
    assert (account.config_dir / ("plugins" + agent_config_parity.SUPERSEDED_SUFFIX)).is_dir()


def test_the_superseded_copy_is_only_moved_aside_once(native: Path) -> None:
    """It runs before every spawn — a folder per spawn would be a leak."""
    (native / "plugins").mkdir(parents=True)
    account = _account()
    (account.config_dir / "plugins").mkdir(parents=True)
    ensure_parity("claude", account.id)
    ensure_parity("claude", account.id)
    stacked = list(account.config_dir.glob("plugins*" + agent_config_parity.SUPERSEDED_SUFFIX))
    assert len(stacked) == 1


# ----------------------------------------------------------------- no redirect


def test_the_builtin_login_is_left_completely_alone(native: Path) -> None:
    """Nothing was redirected, so nothing was lost, so nothing is provisioned.

    The promise to everyone holding a single subscription: their panes spawn
    exactly as they did before any of this existed.
    """
    report = ensure_parity("claude", agent_accounts.builtin_id("claude"))
    assert report.redirected is False
    assert report.shared == {}


def test_an_unknown_account_is_a_no_op(native: Path) -> None:
    assert ensure_parity("claude", "claude:ghost").redirected is False
    assert ensure_parity("claude", None).redirected is False


def test_a_machine_with_no_setup_of_its_own_is_not_a_failure(tmp_path: Path) -> None:
    """A fresh install has no user-level config — there is nothing to share.

    This is also the headless container case (the compose deployment): there is
    no home-directory CLI setup to carry, so this writes nothing at all rather
    than creating an empty scaffold of folders nobody asked for.
    """
    account = _account()
    report = ensure_parity("claude", account.id)
    assert report.redirected is True
    assert report.shared == {}
    assert report.skipped == ()
    # Not one entry of its own making — not even the bookkeeping file.
    assert not (account.config_dir / agent_config_parity.STATE_FILE).exists()
    assert not (account.config_dir / "skills").exists()


# ------------------------------------------------------------------- every host


def test_a_host_without_links_copies_the_users_setup_instead(
    native: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows without Developer Mode, or a filesystem with no reparse points."""
    _links_unavailable(monkeypatch)
    account = _account()
    report = ensure_parity("claude", account.id)
    assert report.shared["skills"] == "copied"
    assert (account.config_dir / "skills" / "git-rescue" / "SKILL.md").is_file()


def test_a_tree_that_must_not_be_copied_is_reported_instead_of_faked(
    native: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copied plugin tree goes stale invisibly — "missing, and said so" is better."""
    (native / "plugins" / "marketplaces").mkdir(parents=True)
    _links_unavailable(monkeypatch)
    account = _account()
    report = ensure_parity("claude", account.id)
    assert "plugins" not in report.shared
    assert any(name == "plugins" for name, _why in report.skipped)


def test_running_it_twice_changes_nothing(native: Path) -> None:
    """It runs before every spawn, so a second run must be a handful of stats."""
    account = _account()
    ensure_parity("claude", account.id)
    second = ensure_parity("claude", account.id)
    assert set(second.shared) >= {"skills", "settings.json", "CLAUDE.md"}
    assert set(second.shared.values()) == {"current"}


def test_a_link_that_was_replaced_by_a_copy_is_re_shared(native: Path) -> None:
    """Self-healing: several components write into a config dir with an atomic
    replace, which silently turns a share into a private copy."""
    account = _account()
    ensure_parity("claude", account.id)
    shared_file = account.config_dir / "CLAUDE.md"
    shared_file.write_text("Answer in plain language.\n", encoding="utf-8")
    (native / "CLAUDE.md").write_text("New house style.\n", encoding="utf-8")
    ensure_parity("claude", account.id)
    assert shared_file.read_text(encoding="utf-8") == "New house style.\n"


def test_a_dangling_link_is_repaired_rather_than_inherited(
    native: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user deleted the folder we pointed at; the pane must not keep a stub."""
    account = _account()
    ensure_parity("claude", account.id)
    target = account.config_dir / "skills"
    if not os.path.lexists(target):  # pragma: no cover - defensive
        pytest.skip("nothing was shared on this host")
    # Re-point the user's own skills at a fresh folder, leaving the old link
    # dangling exactly as a deletion would.
    monkeypatch.setattr(agent_config_parity, "_link", lambda *_a, **_k: None)
    ensure_parity("claude", account.id)
    assert (native / "skills").is_dir()


def test_panes_provisioning_one_account_at_once_do_not_race(native: Path) -> None:
    """Panes attach CONCURRENTLY — a restored workspace re-attaches all of them.

    Eight threads on one account directory, doing the very thing eight panes do.
    Every one must come back with the same answer and the directory must end up
    whole, rather than two of them half-writing the same file.
    """
    import threading

    account = _account()
    results: list[dict[str, str]] = []
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def _provision() -> None:
        try:
            start.wait(timeout=5)
            results.append(ensure_parity("claude", account.id).shared)
        except BaseException as exc:  # noqa: BLE001 - the assertion is below
            errors.append(exc)

    threads = [threading.Thread(target=_provision) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors
    assert len(results) == 8
    # Same entries every time — no thread saw a half-provisioned directory.
    assert {tuple(sorted(r)) for r in results} == {tuple(sorted(results[0]))}
    assert (account.config_dir / "skills" / "git-rescue" / "SKILL.md").is_file()
    assert (
        json.loads(
            (account.config_dir / agent_config_parity.STATE_FILE).read_text(encoding="utf-8")
        )["version"]
        == agent_config_parity.STATE_VERSION
    )


def test_the_setup_lock_is_one_per_account_and_re_entrant(tmp_path: Path) -> None:
    """One account serializes; two accounts still prepare in parallel.

    Re-entrant because the registry holds it across the trust seeding AND the
    parity run, and the parity run takes it again on the same thread.
    """
    first, second = tmp_path / "one", tmp_path / "two"
    assert agent_config_parity.setup_lock(first) is agent_config_parity.setup_lock(first)
    assert agent_config_parity.setup_lock(first) is not agent_config_parity.setup_lock(second)
    lock = agent_config_parity.setup_lock(first)
    with lock, lock:
        pass  # a plain Lock would deadlock here


def test_it_never_raises_on_a_host_that_refuses_everything(
    native: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pane must open even when nothing can be provisioned for it."""
    _links_unavailable(monkeypatch)
    monkeypatch.setattr(agent_config_parity, "_copy", lambda *_a, **_k: None)
    account = _account()
    report = ensure_parity("claude", account.id)
    assert report.redirected is True
    assert report.skipped  # honestly reported, not silently empty


# ------------------------------------------------------- the first-run markers


def test_the_onboarding_markers_are_carried_so_a_pane_skips_the_wizard(
    native: Path,
) -> None:
    """2026-08-08: a fully signed-in added account still booted every pane into
    "Select login method", because the wizard is keyed on these markers rather
    than on the credentials — and the user read that as a failed login."""
    (native / ".claude.json").write_text(
        json.dumps(
            {
                "hasCompletedOnboarding": True,
                "lastOnboardingVersion": "2.1.220",
                "userID": "user-1",
            }
        ),
        encoding="utf-8",
    )
    account = _account()
    ensure_parity("claude", account.id)
    doc = json.loads((account.config_dir / ".claude.json").read_text(encoding="utf-8"))
    assert doc["hasCompletedOnboarding"] is True
    assert doc["lastOnboardingVersion"] == "2.1.220"
    assert "userID" not in doc  # identity stays the account's own


def test_a_marker_the_account_wrote_itself_is_never_overwritten(
    native: Path,
) -> None:
    (native / ".claude.json").write_text(
        json.dumps({"lastOnboardingVersion": "2.1.220"}), encoding="utf-8"
    )
    account = _account()
    (account.config_dir / ".claude.json").write_text(
        json.dumps({"lastOnboardingVersion": "2.1.226"}), encoding="utf-8"
    )
    ensure_parity("claude", account.id)
    doc = json.loads((account.config_dir / ".claude.json").read_text(encoding="utf-8"))
    assert doc["lastOnboardingVersion"] == "2.1.226"
