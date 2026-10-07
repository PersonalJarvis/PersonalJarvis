"""Hermes agents as profiles of one Jarvis-owned data root.

A data root per agent made every agent's first turn build a ~700 MB Python
environment for about two minutes, and Hermes' Windows home maintenance put
every agent folder on the user's PATH. These tests pin the layout that
prevents both, the one-time cleanup of the old PATH entries, and the move of
an agent's conversation out of its old folder.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import pytest

from jarvis.agent_runtimes import base, hermes, path_cleanup
from jarvis.agent_runtimes.base import RuntimeStatus, RuntimeTurn
from jarvis.agent_runtimes.model_map import ModelRoute

_HERMES_PROFILE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(base, "runtimes_root", lambda: tmp_path / "agent_runtimes")
    monkeypatch.setattr(base, "legacy_runtimes_root", lambda: tmp_path / "legacy_runtimes")
    return tmp_path


def _hermes_data_root(hermes_home: Path, native_home: Path) -> Path:
    """Hermes' own rule (``hermes_constants.get_default_hermes_root``)."""
    try:
        hermes_home.resolve().relative_to(native_home.resolve())
        return native_home
    except ValueError:
        return hermes_home.parent.parent if hermes_home.parent.name == "profiles" else hermes_home


def test_profile_names_are_valid_and_never_collide():
    keys = ["hermit", "Hermit", "hermit~runs", "hermit-runs", "agent-148d4d71", "a" * 80, "é"]
    names = [hermes.profile_name(key) for key in keys]
    assert all(_HERMES_PROFILE_NAME.match(name) for name in names), names
    assert len(set(names)) == len(names)
    assert hermes.profile_name("agent-148d4d71") == "agent-148d4d71"  # readable when it can be


def test_every_agent_shares_one_jarvis_data_root(roots):
    chat = hermes.profile_home("hermit")
    runs = hermes.profile_home("hermit~runs")
    other = hermes.profile_home("scout")
    native = roots / "home" / ".hermes"
    data_roots = {_hermes_data_root(home, native) for home in (chat, runs, other)}
    # One dependency environment for all of them, and never the person's own root.
    assert data_roots == {hermes.hermes_root()}
    assert native not in data_roots


def test_the_shared_root_never_reaches_the_users_path(roots):
    root = hermes.prepare_root()
    config = json.loads((root / "config.yaml").read_text(encoding="utf-8"))
    assert config["cli"]["expose_on_path"] is False  # Hermes honours it on POSIX
    # Windows: Hermes stages launchers in <root>/bin and registers that folder;
    # a file there makes the step fail before it touches the registry.
    assert (root / "bin").is_file()


def test_a_bin_folder_hermes_already_made_is_replaced_by_the_blocker(roots):
    root = hermes.hermes_root()
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "hermes.cmd").write_text("@echo off\n", encoding="utf-8")
    hermes.prepare_root()
    assert (root / "bin").is_file()


def test_an_agents_old_conversation_moves_into_its_profile(roots):
    legacy = roots / "legacy_runtimes" / "hermes" / "hermit"
    (legacy / "sessions").mkdir(parents=True)
    (legacy / "state.db").write_bytes(b"sqlite")
    (legacy / "sessions" / "s1.json").write_text("{}", encoding="utf-8")
    (legacy / "installs" / "abc").mkdir(parents=True)  # the old 700 MB environment
    home = hermes.profile_home("hermit")
    assert (home / "state.db").read_bytes() == b"sqlite"
    assert (home / "sessions" / "s1.json").is_file()
    assert not legacy.exists()


def test_an_existing_profile_conversation_is_never_overwritten(roots):
    home = hermes.profile_home("hermit")
    (home / "state.db").write_bytes(b"current")
    legacy = roots / "legacy_runtimes" / "hermes" / "hermit"
    legacy.mkdir(parents=True)
    (legacy / "state.db").write_bytes(b"old")
    hermes.profile_home("hermit")
    assert (home / "state.db").read_bytes() == b"current"


def test_a_turn_runs_with_its_profile_as_hermes_home(roots):
    turn = RuntimeTurn(
        agent_id="hermit",
        agent_name="Hermit",
        session_id="society:hermit",
        workspace=roots,
        route=ModelRoute("ollama", "qwen3", "http://127.0.0.1:11434/v1", "chat_completions", None),
        resume=None,
        auto_approve=True,
    )
    launch = asyncio.run(hermes.HermesRuntime()._launch("hermes", turn, lambda: None))
    home = Path(launch.env["HERMES_HOME"])
    assert home.parent == hermes.hermes_root() / "profiles"
    assert (home / "config.yaml").is_file() and (home / "SOUL.md").is_file()


def test_the_setup_job_prepares_the_shared_root_once_per_build(roots, monkeypatch):
    monkeypatch.setattr(hermes, "_binary", lambda: "hermes")
    runtime = hermes.HermesRuntime()
    argv, env = runtime.prepare_command() or ([], {})
    assert argv == ["hermes", "profile", "list"]  # passes Hermes' launch preparation
    assert Path(env["HERMES_HOME"]).parent == hermes.hermes_root() / "profiles"
    status = RuntimeStatus("hermes", "Hermes", installed=True, ready=True, build="v1 abc")
    assert runtime.needs_prepare(status)
    runtime.mark_prepared(status)
    assert not runtime.needs_prepare(status)
    newer = RuntimeStatus("hermes", "Hermes", installed=True, ready=True, build="v1 def")
    assert runtime.needs_prepare(newer)


def test_a_stale_agent_launcher_on_path_never_answers(tmp_path, monkeypatch):
    stale = tmp_path / "Jarvis" / "agent_runtimes" / "hermes" / "agent-1" / "bin"
    real = tmp_path / "hermes" / "bin"
    for folder in (stale, real):
        folder.mkdir(parents=True)
        launcher = folder / ("hermes.cmd" if os.name == "nt" else "hermes")
        launcher.write_text("", encoding="utf-8")
        launcher.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(stale), str(real)]))
    import jarvis.core.path_augment as path_augment

    monkeypatch.setattr(path_augment, "ensure_cli_paths", lambda: [])
    found = hermes._binary()
    assert found is not None and Path(found).parent == real


# -------------------------------------------------------------- PATH cleanup


class _Store:
    def __init__(self, value: str, kind: int = 2) -> None:
        self.value, self.kind, self.writes = value, kind, 0

    def read(self) -> tuple[str, int] | None:
        return self.value, self.kind

    def write(self, value: str, kind: int) -> None:
        self.value, self.kind, self.writes = value, kind, self.writes + 1


def test_the_cleanup_removes_only_jarvis_hermes_folders():
    keep = [
        r"C:\Users\Ada\AppData\Local\hermes\bin",
        r"%LOCALAPPDATA%\Programs\nodejs",
        r"C:\Users\Ada\.local\bin",
    ]
    drop = [
        r"C:\Users\Ada\AppData\Local\Jarvis\agent_runtimes\hermes\agent-148d4d71\bin",
        r"C:\Users\ADA~1\AppData\Local\Temp\jarvis-hermes-e2e-x\agent_runtimes\hermes\e2e\bin",
        r"C:\Users\Ada\AppData\Local\Jarvis\agent_runtimes-dev\hermes-home\bin",
    ]
    store = _Store(";".join([drop[0], keep[0], drop[1], keep[1], drop[2], keep[2]]))
    environ = {"PATH": os.pathsep.join([*drop, *keep]) if os.name == "nt" else "/usr/bin"}
    removed = path_cleanup.clean_user_path(store=store, environ=environ)
    assert removed == drop
    assert store.value == ";".join(keep) and store.kind == 2  # REG_EXPAND_SZ kept
    if os.name == "nt":
        assert environ["PATH"] == os.pathsep.join(keep)


def test_a_clean_path_is_never_rewritten():
    store = _Store(r"C:\Windows;C:\Users\Ada\AppData\Local\hermes\bin")
    assert path_cleanup.clean_user_path(store=store, environ={}) == []
    assert store.writes == 0
