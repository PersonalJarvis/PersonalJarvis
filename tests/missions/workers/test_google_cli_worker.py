"""GoogleCliWorker drives the official Antigravity ``agy`` CLI as a Phase-6 worker.

agy emits 0 bytes over a plain pipe, so the worker MUST drive it over a PTY (via
``run_cli_over_pty``) with agy's write-capable flag ``--dangerously-skip-permissions``
— never the gemini-CLI pipe path with gemini flags. When the resolver falls back
to the Gemini CLI (kind != "agy") it delegates to the proven GeminiWorker.
"""
from __future__ import annotations

import pytest

import jarvis.missions.workers.google_cli_worker as mod
from jarvis.google_cli.pty_runner import PtyRunResult
from jarvis.google_cli.resolver import GoogleCli
from jarvis.missions.workers.google_cli_worker import (
    GoogleCliWorker,
    _build_agy_worker_argv,
    _build_agy_worker_env,
)


class _FakeJob:
    def __init__(self) -> None:
        self.assigned: list[int] = []

    def assign(self, pid: int) -> None:
        self.assigned.append(pid)


def test_agy_worker_argv_is_print_plus_skip_permissions(tmp_path) -> None:
    argv = _build_agy_worker_argv("agy.exe", "Create a file\nThen stop", tmp_path)
    assert argv[0] == "agy.exe"
    assert "--print" in argv
    assert "--dangerously-skip-permissions" in argv  # auto-approve so it can write
    # The per-mission git worktree MUST be added as agy's active workspace.
    # Without it agy has "no active workspace" in --print mode and writes every
    # deliverable into its home-relative brain/<session>/ (or scratch/<project>/)
    # dir — the worktree stays empty, the Critic sees an empty `git diff HEAD`,
    # and EVERY antigravity mission fails `critic_loop_exhausted`
    # (forensic 2026-06-27, mission_019f07cb et al.).
    assert "--add-dir" in argv
    assert argv[argv.index("--add-dir") + 1] == str(tmp_path)
    # gemini-CLI flags must NOT appear — agy doesn't understand them.
    assert "--yolo" not in argv
    assert "--output-format" not in argv
    assert "-o" not in argv
    # newlines collapsed into the single --print argument.
    assert "\n" not in argv[argv.index("--print") + 1]


def test_agy_worker_env_drops_api_keys_and_redirects_home(monkeypatch) -> None:
    monkeypatch.setattr(mod, "ensure_isolated_home", lambda **k: "/iso")
    env = _build_agy_worker_env({"GEMINI_API_KEY": "x", "GOOGLE_API_KEY": "y", "PATH": "/p"})
    assert "GEMINI_API_KEY" not in env  # OAuth subscription wins
    assert "GOOGLE_API_KEY" not in env
    assert env["HOME"] == "/iso"  # hook/mcp-free isolated home


@pytest.mark.asyncio
async def test_spawn_agy_uses_pty_and_assigns_job(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        mod, "resolve_google_cli", lambda: GoogleCli(kind="agy", argv_prefix=["agy.exe"])
    )
    monkeypatch.setattr(mod, "ensure_isolated_home", lambda **k: str(tmp_path / "iso"))
    # User is signed in (OAuth) → agy runs over the subscription, not the key.
    monkeypatch.setattr(mod, "_oauth_login_present", lambda *_a: True)
    captured: dict = {}

    def _fake_run(argv, *, timeout_s, cwd=None, env=None, on_spawn=None, **kw):
        captured["argv"] = list(argv)
        captured["cwd"] = cwd
        captured["env"] = env
        if on_spawn:
            on_spawn(4321)
        return PtyRunResult(
            text="done writing files", raw="raw", exit_status=0, timed_out=False, error=None
        )

    monkeypatch.setattr(mod, "run_cli_over_pty", _fake_run)
    job = _FakeJob()
    worker = GoogleCliWorker()
    events = [
        e
        async for e in worker.spawn(
            "Build hello.py",
            worktree=tmp_path,
            env={"GEMINI_API_KEY": "x", "PATH": "/p"},
            job=job,
            worker_id="w1",
            log_dir=tmp_path / "logs",
        )
    ]
    assert "--dangerously-skip-permissions" in captured["argv"]
    # The worktree is handed to agy as its active workspace (--add-dir) so files
    # land where the Critic's git diff can see them, not in agy's brain/scratch.
    assert "--add-dir" in captured["argv"]
    assert str(tmp_path) in captured["argv"]
    assert captured["cwd"] == str(tmp_path)
    assert "GEMINI_API_KEY" not in captured["env"]  # OAuth, not the key
    assert job.assigned == [4321]  # PID assigned to the Job Object via on_spawn
    kinds = [type(e).__name__ for e in events]
    assert "ClaudeSystemInit" in kinds and "ClaudeResult" in kinds
    result = next(e for e in events if type(e).__name__ == "ClaudeResult")
    assert result.is_error is False
    assert "done writing files" in result.result


@pytest.mark.asyncio
async def test_spawn_agy_timeout_is_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        mod, "resolve_google_cli", lambda: GoogleCli(kind="agy", argv_prefix=["agy.exe"])
    )
    monkeypatch.setattr(mod, "ensure_isolated_home", lambda **k: str(tmp_path / "iso"))
    monkeypatch.setattr(mod, "_oauth_login_present", lambda *_a: True)
    monkeypatch.setattr(
        mod,
        "run_cli_over_pty",
        lambda *a, **k: PtyRunResult(text="", raw="", exit_status=None, timed_out=True, error=None),
    )
    worker = GoogleCliWorker()
    events = [
        e
        async for e in worker.spawn(
            "x", worktree=tmp_path, env={}, job=_FakeJob(), worker_id="w", log_dir=tmp_path / "l"
        )
    ]
    result = next(e for e in events if type(e).__name__ == "ClaudeResult")
    assert result.is_error is True


@pytest.mark.asyncio
async def test_spawn_gemini_kind_delegates_to_gemini_worker(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        mod, "resolve_google_cli", lambda: GoogleCli(kind="gemini", argv_prefix=["gemini"])
    )
    called = {"gemini": False}

    async def _fake_gemini_spawn(self, prompt, **kw):
        called["gemini"] = True
        yield "GEMINI_EVENT"

    monkeypatch.setattr(mod.GeminiWorker, "spawn", _fake_gemini_spawn)
    worker = GoogleCliWorker()
    events = [
        e
        async for e in worker.spawn(
            "x", worktree=tmp_path, env={}, job=_FakeJob(), worker_id="w", log_dir=tmp_path / "l"
        )
    ]
    assert called["gemini"] is True  # Gemini CLI uses the pipe path, not the PTY
    assert "GEMINI_EVENT" in events


@pytest.mark.asyncio
async def test_spawn_agy_with_gemini_key_but_no_oauth_bills_via_gemini(
    monkeypatch, tmp_path
) -> None:
    """Antigravity dual billing: agy is installed, but the user has NO Google
    OAuth login and a Gemini API key IS set → bill per token via the proven
    GeminiWorker, NOT the agy PTY path (which would coerce OAuth)."""
    monkeypatch.setattr(
        mod, "resolve_google_cli", lambda: GoogleCli(kind="agy", argv_prefix=["agy.exe"])
    )
    monkeypatch.setattr(mod, "_oauth_login_present", lambda *_a: False)  # not signed in

    called = {"gemini": False}

    async def _fake_gemini_spawn(self, prompt, **kw):
        called["gemini"] = True
        yield "GEMINI_EVENT"

    monkeypatch.setattr(mod.GeminiWorker, "spawn", _fake_gemini_spawn)
    # run_cli_over_pty must NOT be called on this path — make it explode if it is.
    monkeypatch.setattr(
        mod, "run_cli_over_pty",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("agy PTY path must not run")),
    )
    worker = GoogleCliWorker()
    events = [
        e
        async for e in worker.spawn(
            "x",
            worktree=tmp_path,
            env={"GEMINI_API_KEY": "AIza-fake", "PATH": "/p"},
            job=_FakeJob(),
            worker_id="w",
            log_dir=tmp_path / "l",
        )
    ]
    assert called["gemini"] is True  # billed via the Gemini API worker
    assert "GEMINI_EVENT" in events
