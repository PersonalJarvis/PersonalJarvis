"""The store, the switch, and the environment behind multi-subscription support.

What these pin down is not "does the JSON round-trip" but the four things that
would silently send work to the wrong plan:

* the built-in account is synthetic and always there, even with no store;
* switching the default cannot leave a pane pointing at a vanished account;
* the built-in account REMOVES the override rather than pinning a path
  (the macOS Keychain depends on that difference);
* ``spawn_env`` inherits, because a replaced environment has no ``PATH``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis import agent_accounts
from jarvis.agent_accounts import AccountError


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway store + account root, never the developer's own."""
    monkeypatch.setattr(agent_accounts, "_store_path", lambda: tmp_path / "accounts.json")
    monkeypatch.setattr(agent_accounts, "_accounts_root", lambda: tmp_path / "dirs")
    monkeypatch.setattr(
        agent_accounts, "_native_dir", lambda platform: tmp_path / "native" / platform
    )
    return tmp_path


# ------------------------------------------------------------------ built-in


def test_every_platform_offers_its_builtin_account_with_no_store() -> None:
    """A fresh install has one usable account per CLI and no file at all."""
    for platform in agent_accounts.platforms():
        accounts = agent_accounts.list_accounts(platform)
        assert len(accounts) == 1
        assert accounts[0].builtin is True
        assert accounts[0].id == agent_accounts.builtin_id(platform)
        assert agent_accounts.active_account(platform).id == accounts[0].id


def test_the_builtin_account_can_be_neither_renamed_nor_removed() -> None:
    """It is the CLI's own login; this feature does not get to take it away."""
    builtin = agent_accounts.builtin_id("claude")
    with pytest.raises(AccountError):
        agent_accounts.rename_account(builtin, "Something else")
    with pytest.raises(AccountError):
        agent_accounts.delete_account(builtin)


# --------------------------------------------------------------------- store


def test_an_added_account_survives_a_reread_and_keeps_the_builtin_first() -> None:
    account = agent_accounts.create_account("claude", "Second seat")
    listed = agent_accounts.list_accounts("claude")
    assert [a.builtin for a in listed] == [True, False]
    assert listed[1].id == account.id
    assert listed[1].label == "Second seat"
    assert listed[1].config_dir.is_dir()


def test_accounts_of_one_platform_never_leak_into_the_other() -> None:
    agent_accounts.create_account("claude", "Claude seat")
    agent_accounts.create_account("codex", "Codex seat")
    assert [a.label for a in agent_accounts.list_accounts("claude")][1:] == ["Claude seat"]
    assert [a.label for a in agent_accounts.list_accounts("codex")][1:] == ["Codex seat"]


def test_an_unreadable_store_degrades_to_the_builtin_account(tmp_path: Path) -> None:
    """A truncated or hand-edited file must not break the switcher."""
    (tmp_path / "accounts.json").write_text("{ not json", encoding="utf-8")
    assert [a.builtin for a in agent_accounts.list_accounts("claude")] == [True]


def test_a_newer_schema_version_is_ignored_rather_than_half_understood(
    tmp_path: Path,
) -> None:
    """Half-reading a future build's file would spawn against the wrong login."""
    (tmp_path / "accounts.json").write_text(
        json.dumps(
            {
                "version": agent_accounts.SCHEMA_VERSION + 1,
                "accounts": [
                    {
                        "id": "claude:zzz",
                        "platform": "claude",
                        "label": "From the future",
                        "config_dir": str(tmp_path / "future"),
                    }
                ],
                "active": {"claude": "claude:zzz"},
            }
        ),
        encoding="utf-8",
    )
    assert [a.builtin for a in agent_accounts.list_accounts("claude")] == [True]
    assert agent_accounts.active_account("claude").builtin is True


def test_a_malformed_entry_is_skipped_without_losing_its_neighbours(
    tmp_path: Path,
) -> None:
    good = agent_accounts.create_account("claude", "Good one")
    raw = json.loads((tmp_path / "accounts.json").read_text(encoding="utf-8"))
    raw["accounts"].insert(0, {"id": "broken"})  # no platform, no config_dir
    (tmp_path / "accounts.json").write_text(json.dumps(raw), encoding="utf-8")
    listed = agent_accounts.list_accounts("claude")
    assert [a.id for a in listed] == [agent_accounts.builtin_id("claude"), good.id]


def test_an_account_needs_a_name() -> None:
    with pytest.raises(AccountError):
        agent_accounts.create_account("claude", "   ")


def test_the_per_platform_cap_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_accounts, "MAX_ACCOUNTS_PER_PLATFORM", 2)
    agent_accounts.create_account("claude", "One")
    agent_accounts.create_account("claude", "Two")
    with pytest.raises(AccountError):
        agent_accounts.create_account("claude", "Three")


# -------------------------------------------------------------------- switch


def test_switching_changes_which_account_new_panes_get() -> None:
    second = agent_accounts.create_account("claude", "Second seat")
    assert agent_accounts.active_account("claude").builtin is True
    agent_accounts.set_active("claude", second.id)
    assert agent_accounts.active_account("claude").id == second.id


def test_switching_one_platform_leaves_the_other_alone() -> None:
    second = agent_accounts.create_account("claude", "Second seat")
    agent_accounts.set_active("claude", second.id)
    assert agent_accounts.active_account("codex").builtin is True


def test_an_account_of_the_wrong_platform_cannot_be_made_active() -> None:
    codex_account = agent_accounts.create_account("codex", "Codex seat")
    with pytest.raises(AccountError):
        agent_accounts.set_active("claude", codex_account.id)


def test_a_deleted_active_account_falls_back_to_the_builtin_one() -> None:
    """Never "no account": there would be no directory to spawn against."""
    second = agent_accounts.create_account("claude", "Second seat")
    agent_accounts.set_active("claude", second.id)
    agent_accounts.delete_account(second.id)
    assert agent_accounts.active_account("claude").builtin is True


def test_a_pinned_id_that_vanished_by_hand_edit_falls_back_too(tmp_path: Path) -> None:
    (tmp_path / "accounts.json").write_text(
        json.dumps(
            {
                "version": agent_accounts.SCHEMA_VERSION,
                "accounts": [],
                "active": {"claude": "claude:ghost"},
            }
        ),
        encoding="utf-8",
    )
    assert agent_accounts.active_account("claude").builtin is True


def test_forgetting_keeps_the_files_unless_asked() -> None:
    """Forgetting is reversible; erasing a login is not."""
    account = agent_accounts.create_account("claude", "Second seat")
    directory = account.config_dir
    (directory / ".credentials.json").write_text("{}", encoding="utf-8")
    agent_accounts.delete_account(account.id)
    assert directory.is_dir()


def test_removing_with_files_erases_the_directory() -> None:
    account = agent_accounts.create_account("claude", "Second seat")
    directory = account.config_dir
    agent_accounts.delete_account(account.id, remove_files=True)
    assert not directory.exists()


def test_renaming_keeps_the_directory_and_the_id() -> None:
    account = agent_accounts.create_account("codex", "Old name")
    renamed = agent_accounts.rename_account(account.id, "New name")
    assert renamed.id == account.id
    assert renamed.config_dir == account.config_dir
    assert agent_accounts.list_accounts("codex")[1].label == "New name"


# ----------------------------------------------------------------- spawn env


def test_the_builtin_account_changes_nothing_about_the_environment() -> None:
    """Load-bearing twice over.

    Pinning the default path is not the same as leaving the variable unset: on
    macOS the true default is the Keychain, and an explicit path would send the
    CLI looking for a file that platform never writes. And clearing an override
    the app was STARTED with would move the default account off the login the
    rest of the machine uses.
    """
    assert agent_accounts.env_overrides("claude", agent_accounts.builtin_id("claude")) == {}
    assert agent_accounts.env_overrides("codex", agent_accounts.builtin_id("codex")) == {}


def test_an_added_account_pins_its_own_directory() -> None:
    account = agent_accounts.create_account("codex", "Second seat")
    assert agent_accounts.env_overrides("codex", account.id) == {
        "CODEX_HOME": str(account.config_dir)
    }


def test_spawn_env_inherits_rather_than_replaces() -> None:
    """A bare {VAR: dir} would strip PATH and the agent binary would vanish."""
    account = agent_accounts.create_account("claude", "Second seat")
    env = agent_accounts.spawn_env(
        "claude", account.id, base={"PATH": "/usr/bin", "TERM": "xterm"}
    )
    assert env["PATH"] == "/usr/bin"
    assert env["TERM"] == "xterm"
    assert env["CLAUDE_CONFIG_DIR"] == str(account.config_dir)


def test_spawn_env_for_the_builtin_account_keeps_an_inherited_override() -> None:
    """On a profile-managed host that override IS the session's default login.

    Clearing it would silently move the default account onto ``~/.claude``,
    which on such a host is the stale copy nothing refreshes any more.
    """
    env = agent_accounts.spawn_env(
        "claude",
        agent_accounts.builtin_id("claude"),
        base={"PATH": "/usr/bin", "CLAUDE_CONFIG_DIR": "/managed/profile"},
    )
    assert env["CLAUDE_CONFIG_DIR"] == "/managed/profile"
    assert env["PATH"] == "/usr/bin"


def test_the_builtin_account_reports_an_inherited_override_as_its_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """What the card shows must be the directory the CLI will really read."""
    monkeypatch.undo()  # drop the _native_dir stand-in for this one case
    monkeypatch.setattr(agent_accounts, "_store_path", lambda: tmp_path / "accounts.json")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "managed-codex"))
    assert agent_accounts.active_account("codex").config_dir == tmp_path / "managed-codex"


def test_an_unknown_account_id_resolves_to_the_platform_default() -> None:
    """A stale id in a resumed workspace must reopen the pane, not kill it."""
    assert agent_accounts.config_dir_for("claude", "claude:ghost") == (
        agent_accounts._native_dir("claude")
    )
    assert agent_accounts.resolve("claude:ghost") is None


def test_an_account_id_of_the_other_platform_is_not_honoured() -> None:
    """A Claude directory means nothing to Codex."""
    claude_account = agent_accounts.create_account("claude", "Claude seat")
    assert agent_accounts.env_overrides("codex", claude_account.id) == {}


# ------------------------------------------------------------ inherited mode


def _write_native(platform: str, name: str, text: str) -> Path:
    """Put a global settings file where the CLI's own default directory is."""
    path = agent_accounts._native_dir(platform) / name  # noqa: SLF001 — test seam
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _claude_mode(directory: Path) -> str | None:
    try:
        raw = json.loads((directory / "settings.json").read_text(encoding="utf-8"))
    except OSError:
        return None
    return raw.get("permissions", {}).get("defaultMode")


def test_a_pane_on_an_added_account_starts_in_the_globally_equipped_mode() -> None:
    """The bug: a redirected config dir left every pane in manual mode.

    The mode lives in the user-level settings, and pointing CLAUDE_CONFIG_DIR at
    the account's own folder moves those settings along with the login — so the
    CLI fell back to its built-in default however the user had equipped it.
    """
    _write_native(
        "claude",
        "settings.json",
        json.dumps(
            {
                "permissions": {"defaultMode": "bypassPermissions", "allow": ["Read"]},
                "skipDangerousModePermissionPrompt": True,
                "model": "opus",
            }
        ),
    )
    account = agent_accounts.create_account("claude", "Second seat")

    assert agent_accounts.inherit_default_mode("claude", account.id) is True

    settings = json.loads((account.config_dir / "settings.json").read_text(encoding="utf-8"))
    assert settings["permissions"]["defaultMode"] == "bypassPermissions"
    assert settings["skipDangerousModePermissionPrompt"] is True
    # Only the mode travels. The rest of the global file is none of our business.
    assert "allow" not in settings["permissions"]
    assert "model" not in settings


def test_codex_inherits_its_own_pair_of_mode_settings() -> None:
    """Same defect, different file format — and the answer must not be Claude's."""
    _write_native(
        "codex",
        "config.toml",
        'model = "gpt-5"\napproval_policy = "never"\nsandbox_mode = "danger-full-access"\n',
    )
    account = agent_accounts.create_account("codex", "Second seat")

    assert agent_accounts.inherit_default_mode("codex", account.id) is True

    written = (account.config_dir / "config.toml").read_text(encoding="utf-8")
    assert 'approval_policy = "never"' in written
    assert 'sandbox_mode = "danger-full-access"' in written
    assert "gpt-5" not in written


def test_an_existing_account_file_keeps_everything_it_already_had() -> None:
    """Seeding a mode must never look like a settings reset."""
    _write_native(
        "claude",
        "settings.json",
        json.dumps({"permissions": {"defaultMode": "acceptEdits"}}),
    )
    account = agent_accounts.create_account("claude", "Second seat")
    (account.config_dir / "settings.json").write_text(
        json.dumps({"theme": "dark"}), encoding="utf-8"
    )

    agent_accounts.inherit_default_mode("claude", account.id)

    settings = json.loads((account.config_dir / "settings.json").read_text(encoding="utf-8"))
    assert settings["theme"] == "dark"
    assert settings["permissions"]["defaultMode"] == "acceptEdits"


def test_the_builtin_account_is_left_completely_alone() -> None:
    """Nothing was redirected, so nothing was lost — and nothing may be written."""
    _write_native(
        "claude",
        "settings.json",
        json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}),
    )
    builtin = agent_accounts.builtin_id("claude")
    before = (agent_accounts._native_dir("claude") / "settings.json").read_text(  # noqa: SLF001
        encoding="utf-8"
    )

    assert agent_accounts.inherit_default_mode("claude", builtin) is False
    assert (agent_accounts._native_dir("claude") / "settings.json").read_text(  # noqa: SLF001
        encoding="utf-8"
    ) == before


def test_no_global_preference_means_the_cli_keeps_its_own_default() -> None:
    """The stated fallback: manual mode, but only when nothing says otherwise."""
    account = agent_accounts.create_account("claude", "Second seat")
    assert agent_accounts.inherit_default_mode("claude", account.id) is False
    assert not (account.config_dir / "settings.json").exists()

    _write_native("claude", "settings.json", json.dumps({"model": "opus"}))
    assert agent_accounts.inherit_default_mode("claude", account.id) is False


def test_a_mode_chosen_for_this_account_by_hand_outranks_the_global_one() -> None:
    """Two accounts, two ways of working. The per-account choice wins and stays."""
    _write_native(
        "claude",
        "settings.json",
        json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}),
    )
    account = agent_accounts.create_account("claude", "Careful seat")
    (account.config_dir / "settings.json").write_text(
        json.dumps({"permissions": {"defaultMode": "plan"}}), encoding="utf-8"
    )

    assert agent_accounts.inherit_default_mode("claude", account.id) is False
    assert _claude_mode(account.config_dir) == "plan"


def test_changing_the_global_mode_follows_through_to_an_untouched_account() -> None:
    """What the mirror record buys: inherited values track, typed ones do not."""
    _write_native(
        "claude",
        "settings.json",
        json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}),
    )
    account = agent_accounts.create_account("claude", "Second seat")
    agent_accounts.inherit_default_mode("claude", account.id)

    _write_native(
        "claude",
        "settings.json",
        json.dumps({"permissions": {"defaultMode": "acceptEdits"}}),
    )
    assert agent_accounts.inherit_default_mode("claude", account.id) is True
    assert _claude_mode(account.config_dir) == "acceptEdits"


def test_inheriting_twice_writes_nothing_the_second_time() -> None:
    """It runs on every pane spawn, so a no-op has to stay a no-op."""
    _write_native(
        "claude",
        "settings.json",
        json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}),
    )
    account = agent_accounts.create_account("claude", "Second seat")
    assert agent_accounts.inherit_default_mode("claude", account.id) is True
    assert agent_accounts.inherit_default_mode("claude", account.id) is False


def test_a_broken_global_settings_file_never_fails_the_spawn() -> None:
    """A pane opening in the CLI's fallback beats a pane that does not open."""
    _write_native("claude", "settings.json", "{ not json at all")
    account = agent_accounts.create_account("claude", "Second seat")
    assert agent_accounts.inherit_default_mode("claude", account.id) is False


def test_a_broken_account_settings_file_is_left_untouched() -> None:
    """Overwriting a file we cannot parse would destroy whatever it holds."""
    _write_native(
        "claude",
        "settings.json",
        json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}),
    )
    account = agent_accounts.create_account("claude", "Second seat")
    broken = account.config_dir / "settings.json"
    broken.write_text("{ half a file", encoding="utf-8")

    assert agent_accounts.inherit_default_mode("claude", account.id) is False
    assert broken.read_text(encoding="utf-8") == "{ half a file"


# -------------------------------------------------------------------- status


def test_an_account_with_no_login_reports_itself_as_not_signed_in() -> None:
    account = agent_accounts.create_account("claude", "Fresh seat")
    snapshot = agent_accounts.describe(account)
    assert snapshot.connected is False
    assert "Not signed in" in snapshot.message


def test_a_signed_in_claude_account_is_described_from_its_own_directory() -> None:
    """Not from the machine-wide search — that would show a neighbour's login."""
    account = agent_accounts.create_account("claude", "Work seat")
    (account.config_dir / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "sk-ant-oat-test",
                    "subscriptionType": "max",
                    # Far future in epoch milliseconds, so this never expires
                    # into a flaky test.
                    "expiresAt": 99_999_999_999_000,
                }
            }
        ),
        encoding="utf-8",
    )
    (account.config_dir / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"emailAddress": "seat-two@example.com"}}),
        encoding="utf-8",
    )
    snapshot = agent_accounts.describe(account)
    assert snapshot.connected is True
    assert snapshot.mode == "subscription"
    assert snapshot.email == "seat-two@example.com"
    assert snapshot.tier == "max"


def test_a_described_account_never_carries_a_token() -> None:
    account = agent_accounts.create_account("claude", "Work seat")
    (account.config_dir / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "sk-ant-oat-supersecret",
                    "subscriptionType": "max",
                    "expiresAt": 99_999_999_999_000,
                }
            }
        ),
        encoding="utf-8",
    )
    serialized = json.dumps(agent_accounts.describe(account).to_dict())
    assert "supersecret" not in serialized


def test_an_expired_access_token_beside_a_refresh_token_is_still_signed_in() -> None:
    """A stale bearer between sessions is not a signed-out account.

    Claude Code renews the access token the moment a terminal runs on the
    directory, so for most of an idle day every healthy account holds an
    expired bearer AND a live refresh token. Reporting that as disconnected
    painted the whole switcher red and sent the user through pointless
    re-logins (the "permanently logged out" report, 2026-07-31).
    """
    account = agent_accounts.create_account("claude", "Idle seat")
    (account.config_dir / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "sk-ant-oat-old",
                    "refreshToken": "sk-ant-ort-live",
                    "subscriptionType": "max",
                    "expiresAt": 1_000,  # long dead
                }
            }
        ),
        encoding="utf-8",
    )
    snapshot = agent_accounts.describe(account)
    assert snapshot.connected is True
    assert snapshot.mode == "subscription"
    serialized = json.dumps(snapshot.to_dict())
    assert "sk-ant-ort-live" not in serialized


def test_an_expired_claude_login_is_not_reported_as_connected() -> None:
    account = agent_accounts.create_account("claude", "Stale seat")
    (account.config_dir / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "sk-ant-oat-old",
                    "subscriptionType": "max",
                    "expiresAt": 1_000,  # long dead
                }
            }
        ),
        encoding="utf-8",
    )
    snapshot = agent_accounts.describe(account)
    assert snapshot.connected is False
    assert snapshot.mode == "expired"
    # The advice matters: a stale ACCESS token refreshes itself on the next run.
    assert "refreshes itself" in snapshot.message


def test_a_signed_in_codex_account_is_described_from_its_own_directory() -> None:
    account = agent_accounts.create_account("codex", "Second plan")
    (account.config_dir / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "abc", "refresh_token": "def"}}),
        encoding="utf-8",
    )
    snapshot = agent_accounts.describe(account)
    assert snapshot.connected is True
    assert snapshot.mode == "subscription"


def test_a_codex_account_holding_only_an_api_key_says_so() -> None:
    account = agent_accounts.create_account("codex", "Key seat")
    (account.config_dir / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": "sk-test"}), encoding="utf-8"
    )
    snapshot = agent_accounts.describe(account)
    assert snapshot.connected is True
    assert snapshot.mode == "api_key"


# ------------------------------------------------- same subscription twice


def _sign_in(directory: Path, email: str, *, expires_at: int = 4_102_444_800_000) -> None:
    """Give *directory* a live Claude login belonging to *email*."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "sk-ant-oat01-test",
                    "subscriptionType": "max",
                    "expiresAt": expires_at,
                }
            }
        ),
        encoding="utf-8",
    )
    (directory / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"emailAddress": email}}), encoding="utf-8"
    )


def test_two_accounts_on_one_subscription_are_flagged_not_shown_as_two_plans() -> None:
    """The 2026-07-27 report: both rows green, one plan's usage draining twice.

    A browser with a live claude.com session approves the second sign-in against
    the account already signed in, without ever showing a code — so the user
    ends up with two entries naming one subscription and no way to tell.
    """
    first = agent_accounts.create_account("claude", "Seat A")
    second = agent_accounts.create_account("claude", "Seat B")
    _sign_in(first.config_dir, "same@example.com")
    _sign_in(second.config_dir, "same@example.com")

    by_label = {s.account.label: s for s in agent_accounts.snapshots("claude")}
    assert by_label["Seat A"].connected and by_label["Seat B"].connected
    # The first occurrence stays clean; the duplicate names who it collides with.
    assert by_label["Seat A"].warning is None
    assert by_label["Seat B"].warning is not None
    assert "Seat A" in by_label["Seat B"].warning
    assert "sign out" in by_label["Seat B"].warning.lower()


def test_two_genuinely_different_subscriptions_are_never_flagged() -> None:
    """The whole point of the feature must not trip its own warning."""
    first = agent_accounts.create_account("claude", "Personal")
    second = agent_accounts.create_account("claude", "Work")
    _sign_in(first.config_dir, "one@example.com")
    _sign_in(second.config_dir, "two@example.com")

    assert all(s.warning is None for s in agent_accounts.snapshots("claude"))


def test_accounts_without_a_readable_email_are_never_grouped() -> None:
    """"Both unknown" is not evidence of sameness — it is absence of evidence."""
    first = agent_accounts.create_account("claude", "Seat A")
    second = agent_accounts.create_account("claude", "Seat B")
    for account in (first, second):
        (account.config_dir / ".credentials.json").write_text(
            json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "sk-ant-oat01-test",
                        "subscriptionType": "max",
                        "expiresAt": 4_102_444_800_000,
                    }
                }
            ),
            encoding="utf-8",
        )

    snaps = agent_accounts.snapshots("claude")
    assert all(s.connected for s in snaps if not s.account.builtin)
    assert all(s.warning is None for s in snaps)


def test_the_warning_survives_the_wire_format() -> None:
    """It has to reach the row that renders it, not stop at the dataclass."""
    first = agent_accounts.create_account("claude", "Seat A")
    second = agent_accounts.create_account("claude", "Seat B")
    _sign_in(first.config_dir, "same@example.com")
    _sign_in(second.config_dir, "same@example.com")

    payload = [s.to_dict() for s in agent_accounts.snapshots("claude")]
    duplicate = next(p for p in payload if p["label"] == "Seat B")
    assert "warning" in duplicate and duplicate["warning"]
    # And it must never carry the thing it was reading next to.
    assert not any("sk-ant-oat" in json.dumps(p) for p in payload)
