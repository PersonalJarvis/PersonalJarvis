"""Tests for build_worker_env — strict allowlist + fixed defaults + optional keys."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis.missions.isolation.env import build_worker_env


# --- Allowlist strikt ---------------------------------------------------------


def test_blacklist_secret_var_does_not_leak(tmp_path: Path) -> None:
    """Variablen ausserhalb der Whitelist landen NIE im Worker-Env."""
    fake_env = {
        "PATH": "/usr/bin",
        "AWS_SECRET_ACCESS_KEY": "leak-me-please",
        "GH_TOKEN": "ghp_xxx",
        "MY_PRIVATE_VAR": "should-not-leak",
    }
    with patch.dict("os.environ", fake_env, clear=True):
        env = build_worker_env(run_dir=tmp_path)

    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "GH_TOKEN" not in env
    assert "MY_PRIVATE_VAR" not in env


def test_allowlist_vars_passed_through(tmp_path: Path) -> None:
    fake_env = {
        "PATH": "/usr/bin:/bin",
        "SystemRoot": r"C:\Windows",
        "TEMP": r"C:\Users\X\AppData\Local\Temp",
        "USERPROFILE": r"C:\Users\X",
        "LOCALAPPDATA": r"C:\Users\X\AppData\Local",
        "APPDATA": r"C:\Users\X\AppData\Roaming",
        "RANDOM_OTHER": "ignored",
    }
    with patch.dict("os.environ", fake_env, clear=True):
        env = build_worker_env(run_dir=tmp_path)

    for key in ("SystemRoot", "TEMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        assert env[key] == fake_env[key]
    # PATH is forwarded but may be additively repaired on Windows (essential
    # System32 / Node.js dirs appended when missing). The original entries are
    # always preserved, in order, at the front — never dropped or reordered.
    assert env["PATH"].startswith(fake_env["PATH"])


def test_appdata_is_passed_through(tmp_path: Path) -> None:
    """APPDATA must reach the worker — npm-installed CLIs (gemini, claude)
    resolve their tool/skill bundles via %APPDATA%/Roaming/npm. Without it,
    the gemini CLI falls back to a stripped-down generalist agent that has
    NO file-writing tools, and the worker silently produces an empty diff.
    Verified live 2026-05-13.
    """
    fake_env = {"APPDATA": r"C:\Users\X\AppData\Roaming"}
    with patch.dict("os.environ", fake_env, clear=True):
        env = build_worker_env(run_dir=tmp_path)
    assert env["APPDATA"] == fake_env["APPDATA"]


def test_missing_system_var_is_simply_omitted(tmp_path: Path) -> None:
    """If e.g. LOCALAPPDATA is missing from os.environ, it's also missing from the output."""
    with patch.dict("os.environ", {"PATH": "/bin"}, clear=True):
        env = build_worker_env(run_dir=tmp_path)
    assert "PATH" in env
    assert "LOCALAPPDATA" not in env
    assert "SystemRoot" not in env


# --- FIX-Defaults -------------------------------------------------------------


def test_fix_defaults_are_set(tmp_path: Path) -> None:
    with patch.dict("os.environ", {}, clear=True):
        env = build_worker_env(run_dir=tmp_path)

    assert env["NO_COLOR"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert env["CODEX_HOME"] == str(tmp_path / ".codex")


def test_fix_defaults_override_inherited_values(tmp_path: Path) -> None:
    """Even if NO_COLOR/PYTHONIOENCODING are set in os.environ, the fixed defaults win."""
    fake_env = {
        "NO_COLOR": "0",
        "PYTHONIOENCODING": "cp1252",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "0",
    }
    with patch.dict("os.environ", fake_env, clear=True):
        env = build_worker_env(run_dir=tmp_path)
    # The fixed defaults win because they are not in the allowlist and
    # are set explicitly after the allowlist.
    assert env["NO_COLOR"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_codex_home_is_run_dir_subpath(tmp_path: Path) -> None:
    run_dir = tmp_path / "missions" / "abc"
    with patch.dict("os.environ", {}, clear=True):
        env = build_worker_env(run_dir=run_dir)
    assert env["CODEX_HOME"] == str(run_dir / ".codex")


def test_plugin_skills_pre_seed_never_materialises_plain_directory(
    tmp_path: Path,
) -> None:
    """2026-05-17 (CRIT-3 from audit-team 10): the pre-seed used to
    materialise a plain *directory* at
    ``<MISSION_STATE_DIR>/plugin-skills/browser-automation/`` so
    the openclaw worker's first-spawn symlink would short-circuit on EEXIST.
    Live forensics (Audit-2 + Audit-6) then showed that trade actually
    swapped EPERM for EINVAL because the openclaw worker later does
    ``readlink()`` on the same path and a directory is not a symbolic
    link. The kernel's EINVAL fired ~12×/hour and crashed the very
    Critic spawn we tried to protect.

    The new contract: the pre-seed either creates a *real symlink* (if
    privilege is available) or leaves the target *missing*. A plain
    directory at the target is exactly the trap the CRIT-3 fix
    removes; whatever materialises must be a symlink.
    """
    run_dir = tmp_path / "missions" / "abc"
    with patch.dict("os.environ", {}, clear=True):
        env = build_worker_env(run_dir=run_dir)
    state_dir = Path(env["MISSION_STATE_DIR"])
    target = state_dir / "plugin-skills" / "browser-automation"
    if target.exists():
        assert target.is_symlink(), (
            "EINVAL trap regression: pre-seed materialised a non-symlink at "
            f"{target}"
        )


def test_plugin_skills_pre_seed_is_idempotent(tmp_path: Path) -> None:
    """Repeated env-builder calls for the same mission must not blow up
    or change the target state. Either both calls succeed in creating
    a symlink (same link path), or both calls leave the target missing.
    The forbidden middle state is exactly the EINVAL trap from CRIT-3."""
    import os

    run_dir = tmp_path / "missions" / "idempotency"
    with patch.dict("os.environ", {}, clear=True):
        env1 = build_worker_env(run_dir=run_dir)
        target = (
            Path(env1["MISSION_STATE_DIR"]) / "plugin-skills"
            / "browser-automation"
        )
        existed_first = target.exists()
        was_symlink_first = target.is_symlink() if existed_first else None
        link_target_first = (
            os.readlink(target) if was_symlink_first else None
        )
        env2 = build_worker_env(run_dir=run_dir)

    assert Path(env2["MISSION_STATE_DIR"]) == Path(env1["MISSION_STATE_DIR"])
    assert target.exists() == existed_first, (
        "second call changed target existence"
    )
    if existed_first:
        assert target.is_symlink() == was_symlink_first
        if was_symlink_first:
            assert os.readlink(target) == link_target_first, (
                "idempotency violation: symlink target changed between calls"
            )


def test_worker_state_dir_is_run_dir_subpath(tmp_path: Path) -> None:
    """Regression: `MISSION_STATE_DIR` must sit directly under `run_dir`
    (the mission root), not under any deeper `tasks/<id>/logs/` path. The
    SubJarvisWorker materializes `openclaw.json` here, and the openclaw CLI
    reads `agents.defaults.workspace` from that file to redirect file_write
    tools into the per-mission git worktree. If the state-dir is one level
    too deep, the openclaw worker can't find the config, falls back to its
    global default workspace (`~/.openclaw/workspace`), and `_capture_diff`
    of the actual worktree returns empty — the mission is then rejected as
    a no-op. The previous derivation `log_dir.parent` produced exactly that
    one-level-too-deep path."""
    run_dir = tmp_path / "missions" / "run-001"
    with patch.dict("os.environ", {}, clear=True):
        env = build_worker_env(run_dir=run_dir)
    state_dir = Path(env["MISSION_STATE_DIR"])
    # Acceptance criterion from the forensic report: the parent of the
    # state-dir must be the mission root itself.
    assert state_dir.parent.name == run_dir.name
    assert state_dir == run_dir / "openclaw_state"


# --- Optional API-Keys --------------------------------------------------------


def test_anthropic_key_only_when_provided(tmp_path: Path) -> None:
    with patch.dict("os.environ", {}, clear=True):
        env_no = build_worker_env(run_dir=tmp_path)
        env_yes = build_worker_env(run_dir=tmp_path, anthropic_api_key="sk-ant-test")
    assert "ANTHROPIC_API_KEY" not in env_no
    assert env_yes["ANTHROPIC_API_KEY"] == "sk-ant-test"


def test_openai_key_only_when_provided(tmp_path: Path) -> None:
    with patch.dict("os.environ", {}, clear=True):
        env_no = build_worker_env(run_dir=tmp_path)
        env_yes = build_worker_env(run_dir=tmp_path, openai_api_key="sk-openai-test")
    assert "OPENAI_API_KEY" not in env_no
    assert env_yes["OPENAI_API_KEY"] == "sk-openai-test"


def test_empty_string_key_is_treated_as_missing(tmp_path: Path) -> None:
    """`anthropic_api_key=""` is semantically "not set"."""
    with patch.dict("os.environ", {}, clear=True):
        env = build_worker_env(
            run_dir=tmp_path, anthropic_api_key="", openai_api_key=""
        )
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env


def test_does_not_inherit_anthropic_key_from_parent_env(tmp_path: Path) -> None:
    """Selbst wenn ANTHROPIC_API_KEY in os.environ steht: ohne Parameter NIE im Output."""
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "leak"}, clear=True):
        env = build_worker_env(run_dir=tmp_path)
    assert "ANTHROPIC_API_KEY" not in env


# --- _seed_jarvis_agent_plugin_skills (CRIT-3 from 2026-05-17 audit) -----------
#
# Before the 2026-05-17 fix, this helper materialised a plain *directory*
# at <state_dir>/plugin-skills/browser-automation/. The openclaw worker then
# called os.readlink() against that path and got EINVAL because a directory
# is not a symbolic link. The "fix" against EPERM had become its own bug.
# Now the helper either creates a real symlink (Developer Mode user) or
# leaves the path missing (default user) so the openclaw worker's own EPERM
# branch runs consistently.


def test_seed_does_nothing_when_no_source_available(
    tmp_path: Path, monkeypatch
) -> None:
    """No npm-installed openclaw source on disk -> helper is a no-op."""
    from jarvis.missions.isolation import env as env_mod

    state_dir = tmp_path / "state"
    # Empty candidate list -> nothing to point a symlink at.
    monkeypatch.setattr(env_mod, "_JARVIS_AGENT_BROWSER_SKILL_CANDIDATES", ())
    env_mod._seed_jarvis_agent_plugin_skills(state_dir)

    target = state_dir / "plugin-skills" / "browser-automation"
    assert not target.exists(), (
        "no source -> helper must not materialise anything"
    )


def test_seed_creates_real_symlink_when_privilege_available(
    tmp_path: Path, monkeypatch
) -> None:
    """With a usable source and a working os.symlink, the target ends up
    as a symlink (not a directory) -- the exact thing the openclaw worker's
    readlink() call expects."""
    from jarvis.missions.isolation import env as env_mod
    import os

    # Synthesise a fake source dir layout: <source>/SKILL.md
    source = tmp_path / "fake_worker_cli" / "skills" / "browser-automation"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# real skill\n", encoding="utf-8")

    monkeypatch.setattr(
        env_mod, "_JARVIS_AGENT_BROWSER_SKILL_CANDIDATES", (str(source),),
    )

    state_dir = tmp_path / "state"
    try:
        env_mod._seed_jarvis_agent_plugin_skills(state_dir)
    except OSError:
        pytest.skip("os.symlink unsupported in this environment")

    target = state_dir / "plugin-skills" / "browser-automation"
    if not target.exists():
        # User lacks SeCreateSymbolicLinkPrivilege on Windows -- skip.
        pytest.skip(
            "no symlink privilege on this host (Developer Mode disabled)"
        )

    assert target.is_symlink(), (
        f"target must be a symlink, but is {target.stat()!s}"
    )
    # Read the link back -- must resolve to the source.
    assert Path(os.readlink(target)).resolve() == source.resolve()


def test_seed_eperm_keeps_target_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """When os.symlink raises EPERM/EACCES (default Windows user), the
    helper must NOT fall back to creating a directory -- that was the
    EINVAL trap. Target stays missing so the openclaw worker sees the same
    file-not-found state it would see without our intervention."""
    from jarvis.missions.isolation import env as env_mod
    import os

    source = tmp_path / "fake_worker_cli"
    source.mkdir()
    (source / "SKILL.md").write_text("# real\n", encoding="utf-8")
    monkeypatch.setattr(
        env_mod, "_JARVIS_AGENT_BROWSER_SKILL_CANDIDATES", (str(source),),
    )

    def boom(*args, **kwargs):
        raise PermissionError(1, "no privilege")
    monkeypatch.setattr(os, "symlink", boom)

    state_dir = tmp_path / "state"
    env_mod._seed_jarvis_agent_plugin_skills(state_dir)

    target = state_dir / "plugin-skills" / "browser-automation"
    # The CRIT-3 contract: must NOT materialise a directory just because
    # the symlink failed. Anything that materialises here re-introduces
    # the EINVAL trap on the openclaw worker's later readlink().
    assert not target.exists(), (
        f"EPERM path must leave target missing; got {target} "
        f"as_dir={target.is_dir() if target.exists() else 'n/a'}"
    )


def test_seed_idempotent_existing_symlink_is_left_alone(
    tmp_path: Path, monkeypatch
) -> None:
    """Second invocation must not break a working symlink from the first."""
    from jarvis.missions.isolation import env as env_mod
    import os

    source = tmp_path / "src"
    source.mkdir()
    (source / "SKILL.md").write_text("# real\n", encoding="utf-8")
    monkeypatch.setattr(
        env_mod, "_JARVIS_AGENT_BROWSER_SKILL_CANDIDATES", (str(source),),
    )

    state_dir = tmp_path / "state"
    try:
        env_mod._seed_jarvis_agent_plugin_skills(state_dir)
    except OSError:
        pytest.skip("os.symlink unsupported")

    target = state_dir / "plugin-skills" / "browser-automation"
    if not target.is_symlink():
        pytest.skip("no symlink privilege")
    first_link = os.readlink(target)

    # Second call -- must be a no-op.
    env_mod._seed_jarvis_agent_plugin_skills(state_dir)
    assert target.is_symlink(), "second call must not destroy the symlink"
    assert os.readlink(target) == first_link, (
        "symlink target changed across calls"
    )


def test_seed_cleans_up_stale_directory_from_old_buggy_runs(
    tmp_path: Path, monkeypatch
) -> None:
    """If the user has missions left over from before the CRIT-3 fix,
    those have a stale directory at the target path. On the next mission
    we must clean it up and (try to) replace it with a symlink, so the
    EINVAL on readlink() stops repeating."""
    from jarvis.missions.isolation import env as env_mod

    source = tmp_path / "src"
    source.mkdir()
    (source / "SKILL.md").write_text("# real\n", encoding="utf-8")
    monkeypatch.setattr(
        env_mod, "_JARVIS_AGENT_BROWSER_SKILL_CANDIDATES", (str(source),),
    )

    state_dir = tmp_path / "state"
    stale = state_dir / "plugin-skills" / "browser-automation"
    stale.mkdir(parents=True)
    (stale / "leftover.txt").write_text("from old buggy run\n", encoding="utf-8")

    env_mod._seed_jarvis_agent_plugin_skills(state_dir)

    # Either the stale dir is gone (cleaned + symlink succeeded) or it
    # is gone (cleaned + symlink failed -- still better than leaving
    # the EINVAL trap in place). Critical assertion: the stale
    # leftover.txt must NOT be visible anymore.
    if stale.exists():
        # If anything survives, it must be a symlink, not the old dir.
        assert stale.is_symlink(), (
            "stale directory must be replaced with a symlink, not kept "
            "(otherwise EINVAL on readlink reappears)"
        )


# --- BUG-LIVE-FIX 2026-05-18: OAuth-vs-API-key slot routing -------------


def test_oauth_token_routed_to_oauth_slot_not_api_key(tmp_path: Path) -> None:
    """OAuth tokens (sk-ant-oat01-...) must land in
    ANTHROPIC_OAUTH_TOKEN, never in ANTHROPIC_API_KEY. claude --print
    strictly validates ANTHROPIC_API_KEY as a classic key and rejects
    OAuth tokens with "Invalid API key". Live repro on 2026-05-18
    mission_019e3c1a-948e: stream.jsonl contained exactly one
    result-is_error=True frame with that error message.
    """
    with patch.dict("os.environ", {}, clear=True):
        env = build_worker_env(
            run_dir=tmp_path,
            anthropic_api_key="sk-ant-oat01-deadbeef-cafe-1234567890",
        )
    assert env.get("ANTHROPIC_OAUTH_TOKEN") == "sk-ant-oat01-deadbeef-cafe-1234567890"
    assert "ANTHROPIC_API_KEY" not in env, (
        "OAuth token must not leak into the API-key slot; claude --print "
        "validates the slot and rejects oat-format values there"
    )


def test_classic_api_key_routed_to_api_key_slot(tmp_path: Path) -> None:
    """Classic API keys (sk-ant-api03-...) still go into
    ANTHROPIC_API_KEY -- not every consumer reads OAUTH_TOKEN."""
    with patch.dict("os.environ", {}, clear=True):
        env = build_worker_env(
            run_dir=tmp_path,
            anthropic_api_key="sk-ant-api03-classicclassic1234567890",
        )
    assert env.get("ANTHROPIC_API_KEY") == "sk-ant-api03-classicclassic1234567890"
    assert "ANTHROPIC_OAUTH_TOKEN" not in env


def test_no_anthropic_key_leaves_both_slots_unset(tmp_path: Path) -> None:
    with patch.dict("os.environ", {}, clear=True):
        env = build_worker_env(run_dir=tmp_path)
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_OAUTH_TOKEN" not in env


# --- ROOT-CAUSE FIX 2026-05-29: isolate the worker's claude config so it does
# NOT inherit the user's global plugin SessionStart hooks ------------------
#
# Live forensics (data/missions.db): 100% of recently FAILED missions had a
# stream.jsonl of exactly 253 bytes / 1 line ending at
# {"subtype":"hook_started","hook_name":"SessionStart:startup"} with NO
# completion — the superpowers plugin's SessionStart hook (async:false, a
# Windows run-hook.cmd -> Git-bash polyglot) intermittently HANGS under the
# headless CREATE_NO_WINDOW worker spawn, blocking claude the full 630s hard
# cap -> WorkerKilled(timeout) -> empty diff -> task_error (103 of 277 fails).
# Workers/critics spawn `claude --print` as the same OS user, so without an
# isolated CLAUDE_CONFIG_DIR they load ~/.claude plugins + hooks. The fix:
# point every mission CLI at a clean per-run config dir (no plugins, no hooks)
# and authenticate via CLAUDE_CODE_OAUTH_TOKEN (ANTHROPIC_OAUTH_TOKEN alone is
# NOT honoured by `claude --print` 2.1.156 once CLAUDE_CONFIG_DIR is set —
# verified live: it answers "Not logged in · Please run /login").


def test_claude_config_dir_is_isolated_under_run_dir(tmp_path: Path) -> None:
    """The worker must get a dedicated CLAUDE_CONFIG_DIR under run_dir so it
    never loads the user's global ~/.claude plugins/hooks."""
    run_dir = tmp_path / "missions" / "run-cfg"
    with patch.dict("os.environ", {}, clear=True):
        env = build_worker_env(run_dir=run_dir)
    assert "CLAUDE_CONFIG_DIR" in env, (
        "worker claude must be pinned to an isolated config dir (else it "
        "inherits the user's global plugin SessionStart hooks)"
    )
    cfg = Path(env["CLAUDE_CONFIG_DIR"])
    assert cfg.is_dir()
    # Must live under the mission run_dir (cleaned up with the mission).
    assert str(cfg).startswith(str(run_dir))


def test_isolated_claude_config_has_no_hooks_or_plugins(tmp_path: Path) -> None:
    """Regression guard for the SessionStart-hook hang: the seeded settings
    must disable ALL hooks and plugins so no inherited hook can block the
    worker's claude at startup."""
    import json

    run_dir = tmp_path / "missions" / "run-hooks"
    with patch.dict("os.environ", {}, clear=True):
        env = build_worker_env(run_dir=run_dir)
    settings_path = Path(env["CLAUDE_CONFIG_DIR"]) / "settings.json"
    assert settings_path.is_file(), "isolated config dir must carry a settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings.get("hooks") == {}, "isolated worker config must define NO hooks"
    assert settings.get("enabledPlugins") == {}, (
        "isolated worker config must enable NO plugins (they carry the hanging "
        "SessionStart hook)"
    )


def test_oauth_token_also_set_as_claude_code_oauth_token(tmp_path: Path) -> None:
    """With an isolated CLAUDE_CONFIG_DIR, `claude --print` authenticates via
    CLAUDE_CODE_OAUTH_TOKEN (the headless OAuth env var). ANTHROPIC_OAUTH_TOKEN
    stays set for the openclaw worker/other consumers, but it alone is NOT
    enough — the worker would otherwise fail with "Not logged in"."""
    oat = "sk-ant-oat01-deadbeef-cafe-1234567890"
    with patch.dict("os.environ", {}, clear=True):
        env = build_worker_env(run_dir=tmp_path, anthropic_api_key=oat)
    assert env.get("CLAUDE_CODE_OAUTH_TOKEN") == oat
    # Keep the legacy slot too (existing contract / the openclaw worker).
    assert env.get("ANTHROPIC_OAUTH_TOKEN") == oat
    assert "ANTHROPIC_API_KEY" not in env


def test_home_and_xdg_passed_through_for_posix_auth(tmp_path: Path) -> None:
    """Cross-platform (deep-dive 2026-05-29): on macOS/Linux, claude and codex
    resolve their credential files via $HOME (and $XDG_CONFIG_HOME), not
    %USERPROFILE%. The worker env fully REPLACES os.environ, so without HOME in
    the allowlist a POSIX worker can't find ~/.claude/.credentials.json /
    ~/.codex/auth.json and fails auth. Passing them through is harmless on
    Windows (usually unset there)."""
    fake_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/jarvis",
        "USER": "jarvis",
        "LOGNAME": "jarvis",
        "XDG_CONFIG_HOME": "/home/jarvis/.config",
        "TMPDIR": "/home/jarvis/.tmp",
    }
    with patch.dict("os.environ", fake_env, clear=True):
        env = build_worker_env(run_dir=tmp_path)
    assert env["HOME"] == "/home/jarvis"
    assert env["USER"] == "jarvis"
    assert env["LOGNAME"] == "jarvis"
    assert env["XDG_CONFIG_HOME"] == "/home/jarvis/.config"
    assert env["TMPDIR"] == "/home/jarvis/.tmp"


def test_posix_user_is_derived_when_gui_environment_omits_it(
    tmp_path: Path,
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX account fallback is not used on Windows")
    fake_env = {"PATH": "/usr/bin:/bin", "HOME": "/home/jarvis"}
    with (
        patch.dict("os.environ", fake_env, clear=True),
        patch(
            "jarvis.missions.isolation.env.getpass.getuser",
            return_value="derived-user",
        ),
    ):
        env = build_worker_env(run_dir=tmp_path)

    assert env["USER"] == "derived-user"


def test_classic_api_key_does_not_set_claude_code_oauth_token(tmp_path: Path) -> None:
    """A classic API key is not an OAuth token — CLAUDE_CODE_OAUTH_TOKEN stays
    unset, ANTHROPIC_API_KEY carries it."""
    with patch.dict("os.environ", {}, clear=True):
        env = build_worker_env(
            run_dir=tmp_path, anthropic_api_key="sk-ant-api03-classic1234567890"
        )
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env.get("ANTHROPIC_API_KEY") == "sk-ant-api03-classic1234567890"


# --- DEGRADED-LAUNCH PATH REPAIR (live incident 2026-06-20) -----------------
#
# jarvis was launched by an agent runtime (hermes-agent) with a PATH that did
# NOT contain the Node.js dir. build_worker_env forwarded that broken PATH
# verbatim, so the codex worker's `codex.CMD` shim resolved bare `node` via PATH,
# cmd.exe failed "'node' is not recognized" and exited 1 in ~25 ms — every
# mission died `task_error` ("Der Worker ist abgebrochen."). The env builder must  # i18n-allow: quotes the actual German TTS readback phrase
# ADDITIVELY repair the worker PATH (never reorder/drop) so essential System32 /
# Node.js dirs are always present, and forward ComSpec/PATHEXT so .cmd shims and
# `chcp` resolve regardless of how jarvis itself was launched.

import sys  # noqa: E402

from jarvis.missions.isolation.env import _repair_windows_worker_path  # noqa: E402


def test_repair_path_appends_system32_when_missing() -> None:
    repaired = _repair_windows_worker_path(
        r"C:\some\dir", environ={"SystemRoot": r"C:\Windows"}, node_exe=None
    )
    parts = [p.rstrip("\\").lower() for p in repaired.split(";")]
    assert r"c:\windows\system32" in parts, repaired


def test_repair_path_appends_node_dir_when_missing() -> None:
    repaired = _repair_windows_worker_path(
        r"C:\some\dir",
        environ={"SystemRoot": r"C:\Windows"},
        node_exe=r"C:\Program Files\nodejs\node.exe",
    )
    assert r"c:\program files\nodejs" in repaired.lower(), repaired


def test_repair_path_is_additive_and_preserves_leading_entries() -> None:
    repaired = _repair_windows_worker_path(
        r"C:\keep\me;C:\and\me", environ={"SystemRoot": r"C:\Windows"}, node_exe=None
    )
    assert repaired.startswith(r"C:\keep\me;C:\and\me"), repaired


def test_repair_path_does_not_duplicate_existing_system32() -> None:
    p = r"C:\Windows\System32;C:\other"
    repaired = _repair_windows_worker_path(
        p, environ={"SystemRoot": r"C:\Windows"}, node_exe=None
    )
    count = sum(
        1 for x in repaired.split(";") if x.rstrip("\\").lower() == r"c:\windows\system32"
    )
    assert count == 1, repaired


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PATH repair only")
def test_build_worker_env_repairs_broken_path_and_forwards_comspec(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end: a degraded inherited PATH (no System32, no node) yields a
    worker PATH that DOES contain both, plus ComSpec/PATHEXT forwarded."""
    from jarvis.missions.isolation import env as env_mod

    monkeypatch.setattr(
        env_mod, "resolve_node_executable",
        lambda: r"C:\Program Files\nodejs\node.exe",
    )
    fake = {
        "PATH": r"C:\Users\X\AppData\Local\hermes\bin",
        "SystemRoot": r"C:\Windows",
        "APPDATA": r"C:\Users\X\AppData\Roaming",
        "ComSpec": r"C:\Windows\System32\cmd.exe",
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
    }
    with patch.dict("os.environ", fake, clear=True):
        env = build_worker_env(run_dir=tmp_path)

    pl = env["PATH"].lower()
    assert r"\windows\system32" in pl, env["PATH"]
    assert r"\nodejs" in pl, env["PATH"]
    assert env["PATH"].startswith(fake["PATH"]), "original PATH must stay at front"
    assert env.get("ComSpec") == r"C:\Windows\System32\cmd.exe"
    assert env.get("PATHEXT") == ".COM;.EXE;.BAT;.CMD"


def test_build_worker_env_leaves_posix_path_untouched(
    tmp_path: Path, monkeypatch
) -> None:
    """Cross-platform doctrine: the Windows-shaped PATH repair must NOT run on
    POSIX (a Linux VPS PATH must arrive verbatim)."""
    from jarvis.missions.isolation import env as env_mod

    monkeypatch.setattr(env_mod, "_worker_path_repair_is_windows", lambda: False)
    with patch.dict("os.environ", {"PATH": "/usr/bin:/bin"}, clear=True):
        env = build_worker_env(run_dir=tmp_path)
    assert env["PATH"] == "/usr/bin:/bin"
