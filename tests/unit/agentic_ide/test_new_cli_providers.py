"""The three CLI providers added alongside Claude Code and Codex.

Each of them exercises a different half of the registry contract, which is why
they are worth testing together rather than one file per product:

* **OpenCode** is a plain standalone CLI whose sessions live in a database, so
  it proves the discover-after-launch path works against something that is not
  a directory of files.
* **Kimi Code** ships as two generations under one binary name, so it proves the
  generation probe reaches the launch and history paths.
* **GLM** is not a CLI at all — it is the Claude Code binary pointed at another
  vendor — so it proves a launch profile inherits behaviour without duplicating
  it, and that the environment it depends on is never half-applied.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from jarvis.agentic_ide import agent_sessions, drops
from jarvis.agentic_ide.fleet_actions import _ready_for_prompt
from jarvis.workspace import agents as workspace_agents

# --------------------------------------------------------------- the registry


def test_every_new_provider_is_launchable_and_installable() -> None:
    """A registered entry must answer the three questions a pane asks of it."""
    for name in ("opencode", "kimi", "glm"):
        entry = workspace_agents.get_agent(name)
        assert entry is not None, f"{name} is not registered"
        assert entry.is_coding_agent
        assert entry.executable, f"{name} resolves to no binary"
        assert workspace_agents.install_command(name)


def test_a_two_part_version_string_is_not_read_as_no_version() -> None:
    """"kimi, version 1.3" must parse — a blank version reads as a broken install."""
    import re

    entry = workspace_agents.get_agent("kimi")
    assert entry is not None and entry.spec is not None
    match = re.search(entry.spec.version_parse_regex, "kimi, version 1.3")
    assert match is not None and match.group(1) == "1.3"
    # The three-part form still works, so an upgrade does not lose the version.
    three = re.search(entry.spec.version_parse_regex, "kimi, version 1.3.7")
    assert three is not None and three.group(1) == "1.3.7"


def test_a_product_name_can_never_become_a_pane_call_sign() -> None:
    """Saying "Kimi" must address the CLI, not a pane that happens to be called that."""
    reserved = workspace_agents.reserved_call_signs()
    for spoken in ("kimi", "opencode", "glm", "claude", "codex"):
        assert spoken in reserved
    from jarvis.agentic_ide import names

    assert not {n.lower() for n in names.NAME_POOL} & reserved


# ------------------------------------------------------------------ OpenCode


def _opencode_db(root: Path) -> Path:
    """A database with the real schema's load-bearing columns."""
    data = root / "xdg" / "opencode"
    data.mkdir(parents=True, exist_ok=True)
    db = data / "opencode.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT, "
        "parent_id TEXT, directory TEXT NOT NULL, time_created INTEGER NOT NULL)"
    )
    con.commit()
    con.close()
    return db


def _add_session(
    db: Path, session_id: str, directory: str, created_s: float, parent: str | None = None
) -> None:
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO session (id, project_id, parent_id, directory, time_created) "
        "VALUES (?, 'p', ?, ?, ?)",
        (session_id, parent, directory, int(created_s * 1000)),
    )
    con.commit()
    con.close()


def test_opencode_finds_the_session_its_own_pane_created(
    _agent_history_in_tmp: Path, tmp_path: Path
) -> None:
    db = _opencode_db(_agent_history_in_tmp)
    started = time.time()
    _add_session(db, "ses_new", str(tmp_path), started + 1)
    handle = agent_sessions.discover("opencode", str(tmp_path), started)
    assert handle is not None
    assert handle.kind == "opencode_session"
    assert handle.id == "ses_new"
    assert agent_sessions.resume_argv("opencode", handle) == ("--session", "ses_new")
    assert agent_sessions.has_conversation("opencode", handle) is True


def test_opencode_ignores_another_folder_and_an_older_session(
    _agent_history_in_tmp: Path, tmp_path: Path
) -> None:
    db = _opencode_db(_agent_history_in_tmp)
    started = time.time()
    _add_session(db, "ses_elsewhere", str(tmp_path / "other"), started + 1)
    _add_session(db, "ses_before", str(tmp_path), started - 600)
    assert agent_sessions.discover("opencode", str(tmp_path), started) is None


def test_opencode_never_resumes_a_subagents_own_thread(
    _agent_history_in_tmp: Path, tmp_path: Path
) -> None:
    """A pane must reopen the conversation, not a fragment of its own last run."""
    db = _opencode_db(_agent_history_in_tmp)
    started = time.time()
    _add_session(db, "ses_child", str(tmp_path), started + 1, parent="ses_parent")
    assert agent_sessions.discover("opencode", str(tmp_path), started) is None


def test_two_opencode_panes_in_one_folder_get_different_conversations(
    _agent_history_in_tmp: Path, tmp_path: Path
) -> None:
    """The failure this claim check exists for: both panes taking session one."""
    db = _opencode_db(_agent_history_in_tmp)
    started = time.time()
    _add_session(db, "ses_a", str(tmp_path), started + 1)
    _add_session(db, "ses_b", str(tmp_path), started + 2)
    first = agent_sessions.discover("opencode", str(tmp_path), started)
    assert first is not None
    second = agent_sessions.discover(
        "opencode", str(tmp_path), started, taken=[first.id]
    )
    assert second is not None
    assert second.id != first.id


def test_a_missing_opencode_database_starts_fresh_instead_of_raising(
    tmp_path: Path,
) -> None:
    assert agent_sessions.discover("opencode", str(tmp_path), time.time()) is None
    handle = agent_sessions.ResumeHandle("opencode_session", "ses_x", time.time())
    assert agent_sessions.has_conversation("opencode", handle) is False


# ----------------------------------------------------------------- Kimi Code


def test_kimi_finds_the_session_directory_its_pane_created(
    _agent_history_in_tmp: Path, tmp_path: Path
) -> None:
    started = time.time()
    key = agent_sessions._kimi_folder_key(str(tmp_path))
    assert key is not None
    folder = _agent_history_in_tmp / "kimi" / "sessions" / key / "sess-1"
    folder.mkdir(parents=True)
    (folder / "context.jsonl").write_text("{}\n", encoding="utf-8")
    handle = agent_sessions.discover("kimi", str(tmp_path), started - 5)
    assert handle is not None
    assert handle.kind == "kimi_session"
    assert handle.id == "sess-1"
    assert agent_sessions.resume_argv("kimi", handle) == ("--session", "sess-1")


def test_kimi_skips_a_session_folder_with_nothing_in_it(
    _agent_history_in_tmp: Path, tmp_path: Path
) -> None:
    """An opened-but-never-used pane leaves an empty folder; resuming it kills the pane."""
    key = agent_sessions._kimi_folder_key(str(tmp_path))
    assert key is not None
    (_agent_history_in_tmp / "kimi" / "sessions" / key / "sess-empty").mkdir(parents=True)
    assert agent_sessions.discover("kimi", str(tmp_path), time.time() - 5) is None


def test_kimi_folder_key_is_derived_from_the_native_path(tmp_path: Path) -> None:
    """The only link between a session and its folder — so it must be stable."""
    import hashlib

    expected = hashlib.md5(
        str(tmp_path.resolve()).encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    assert agent_sessions._kimi_folder_key(str(tmp_path)) == expected


def test_the_kimi_generation_probe_reads_the_data_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two generations, one binary name — and the wrong flags on the wrong one."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert workspace_agents.kimi_generation() is None
    (tmp_path / ".kimi").mkdir()
    assert workspace_agents.kimi_generation() == workspace_agents.KIMI_LEGACY
    (tmp_path / ".kimi-code").mkdir()
    assert workspace_agents.kimi_generation() == workspace_agents.KIMI_CURRENT


# ------------------------------------------------------------------- GLM


def test_glm_runs_the_borrowed_binary_and_inherits_its_resume() -> None:
    """It is a launch profile, not a CLI: nothing about it may be a second copy."""
    entry = workspace_agents.get_agent("glm")
    assert entry is not None
    assert entry.executable == "claude"
    assert entry.adapter_key == "claude"
    argv, handle = agent_sessions.launch_extra("glm")
    assert handle is not None and handle.kind == "claude_session"
    assert argv[0] == "--session-id"
    assert agent_sessions.resume_argv("glm", handle) == ("--resume", handle.id)
    # A handle from a genuinely different CLI is still refused.
    foreign = agent_sessions.ResumeHandle("codex_rollout", handle.id, time.time())
    assert agent_sessions.resume_argv("glm", foreign) is None


def test_a_glm_pane_without_a_key_refuses_to_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wrong-vendor trap: a half-set environment answers, and bills, elsewhere."""
    from jarvis.agentic_ide import session as ide_session

    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *_a, **_k: None)
    with pytest.raises(ide_session.SessionError):
        ide_session.agent_spawn_overlay("glm")


def test_a_configured_glm_pane_carries_the_endpoint_and_hides_a_stray_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.agentic_ide import session as ide_session

    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *_a, **_k: "tok")  # noqa: S105
    overlay = ide_session.agent_spawn_overlay("glm")
    assert overlay["ANTHROPIC_BASE_URL"].startswith("http")
    assert overlay["ANTHROPIC_AUTH_TOKEN"] == "tok"  # noqa: S105
    assert int(overlay["API_TIMEOUT_MS"]) >= 60_000
    # Empty means "remove from the child": a host key would otherwise outrank
    # the token and the pane would quietly answer from the other vendor.
    assert overlay["ANTHROPIC_API_KEY"] == ""
    # A plain Claude Code pane in the same workspace is untouched.
    assert ide_session.agent_spawn_overlay("claude") == {}


def test_the_glm_environment_factory_reports_not_configured_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jarvis.core.config.get_secret", lambda *_a, **_k: None
    )
    assert workspace_agents.glm_spawn_env() is None


def test_the_glm_environment_pins_no_model_the_user_did_not_choose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vendor docs disagree on the ids, so a guessed default fails at request time."""
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *_a, **_k: "tok")  # noqa: S105
    env = workspace_agents.glm_spawn_env()
    assert env is not None
    assert env["ANTHROPIC_AUTH_TOKEN"] == "tok"  # noqa: S105
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in env


# ------------------------------------------------- per-CLI pane behaviour


def test_a_dropped_file_is_written_the_way_each_cli_reads_it() -> None:
    at_style = drops.reference("/w/a b.py", agent="claude")
    assert at_style == '"/w/a b.py"'  # a space would end the @reference early
    assert drops.reference("/w/a.py", agent="claude") == "@/w/a.py"
    assert drops.reference("/w/a.py", agent="opencode") == "@/w/a.py"
    assert drops.reference("/w/a.py", agent="codex") == '"/w/a.py"'
    # An entry nobody has registered gets the form that works everywhere.
    assert drops.reference("/w/a.py", agent="nope") == '"/w/a.py"'


class _Pane:
    def __init__(self, agent: str, lines: tuple[str, ...] = ()) -> None:
        self.agent = agent
        self.status = "live"
        self.pty_id = "pty-1"
        self._lines = lines

    @property
    def transcript(self) -> Any:
        lines = self._lines

        class _T:
            @staticmethod
            def tail(_n: int) -> tuple[str, ...]:
                return lines

        return _T()


def test_a_booting_pane_of_a_new_cli_is_not_prompted_yet() -> None:
    """Waiting is the default; a CLI opts out, it does not opt in.

    The cheap mistake and the expensive one are not symmetric: opting in
    silently means every CLI registered later loses its first prompt with no
    error anywhere, while opting out silently costs one CLI a little speed.
    """
    assert _ready_for_prompt(_Pane("opencode", ("Loading...",))) is False
    assert _ready_for_prompt(_Pane("opencode", ("> ",))) is True
    assert _ready_for_prompt(_Pane("kimi", ("starting",))) is False
    # The one measured exception keeps its fast path.
    assert _ready_for_prompt(_Pane("claude", ("anything",))) is True
