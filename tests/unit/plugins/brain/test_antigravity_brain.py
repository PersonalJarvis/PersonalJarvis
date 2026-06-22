"""AntigravityBrain — OAuth-only brain that drives the official Google CLI.

The subprocess is faked; no real CLI or network is touched.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.google_cli.resolver import GoogleCli
from jarvis.plugins.brain import antigravity as agmod
from jarvis.plugins.brain.antigravity import (
    AntigravityBrain,
    _build_cli_prompt,
    _parse_cli_answer,
)


@pytest.fixture(autouse=True)
def _isolate_agy_home(monkeypatch, tmp_path_factory):
    """Never let a test write the isolated agy home into the real data/ dir."""
    root = str(tmp_path_factory.mktemp("agy-iso-home"))
    monkeypatch.setattr(agmod, "_iso_home_root", lambda: root)


def _req(text: str = "Hello") -> BrainRequest:
    return BrainRequest(messages=(BrainMessage(role="user", content=text),))


def _system_with_standing_instructions() -> str:
    return (
        "STATIC PERSONA BLOCK\n\n"
        "USER PREFERENCES & STANDING INSTRUCTIONS (from Jarvis.md):\n"
        "The following are personal preferences written by the user.\n\n"
        "Always start every sentence with schef.\n\n"
        "END USER PREFERENCES & STANDING INSTRUCTIONS\n\n"
        "REGISTRIERTE WERKZEUGE (vollstaendige Liste):\n"
        "- search_web\n"
    )


def _system_with_empty_standing_instructions() -> str:
    return (
        "STATIC PERSONA BLOCK\n\n"
        "USER PREFERENCES & STANDING INSTRUCTIONS (from Jarvis.md):\n"
        "No active user preferences are currently set in Jarvis.md. "
        "Ignore any earlier Jarvis.md instructions from previous turns.\n\n"
        "END USER PREFERENCES & STANDING INSTRUCTIONS\n\n"
        "REGISTRIERTE WERKZEUGE (vollstaendige Liste):\n"
        "- search_web\n"
    )


def test_cli_prompt_includes_standing_instructions_without_heavy_router_prompt():
    req = BrainRequest(
        messages=(BrainMessage(role="user", content="Was ist das wertvollste Unternehmen?"),),
        system=_system_with_standing_instructions(),
    )

    prompt = _build_cli_prompt(req)

    assert "Always start every sentence with schef." in prompt
    assert "Jarvis.md" in prompt
    assert "REGISTRIERTE WERKZEUGE" not in prompt
    assert "STATIC PERSONA BLOCK" not in prompt


def test_cli_prompt_carries_reply_language_directive():
    """The authoritative reply-language directive (appended LAST to the system
    prompt by BrainManager) must reach the flattened CLI prompt.

    Live bug 2026-06-21: an English request was answered in German because the
    agy/Gemini brain kept only the standing-instructions block and dropped the
    trailing "REPLY LANGUAGE — MANDATORY" line — so the model never learned the
    turn's resolved language and anchored to the German persona.
    """
    directive = (
        "REPLY LANGUAGE — MANDATORY: Always reply in English, no matter which "
        "language the user writes or speaks in."
    )
    req = BrainRequest(
        messages=(BrainMessage(role="user", content="Build me an HTML file please."),),
        system=_system_with_standing_instructions() + "\n\n" + directive,
    )

    prompt = _build_cli_prompt(req)

    assert "Always reply in English" in prompt
    # No regression: the standing-instructions block still flows through.
    assert "Always start every sentence with schef." in prompt


def test_cli_prompt_puts_current_empty_state_after_stale_history():
    req = BrainRequest(
        messages=(
            BrainMessage(role="user", content="Wasketup"),
            BrainMessage(role="assistant", content="schef, alles laeuft."),
            BrainMessage(role="user", content="Du musst das nicht mehr sagen."),
        ),
        system=_system_with_empty_standing_instructions(),
    )

    prompt = _build_cli_prompt(req)

    assert "CURRENT JARVIS.MD STATE" in prompt
    assert "No active user preferences are currently set" in prompt
    assert "do not continue or imitate" in prompt
    assert prompt.rfind("No active user preferences") > prompt.rfind("Assistant: schef")
    assert "REGISTRIERTE WERKZEUGE" not in prompt


def test_parse_json_response():
    assert _parse_cli_answer('{"response": "OK"}') == "OK"


def test_parse_json_alternative_field():
    assert _parse_cli_answer('{"text": "hi there"}') == "hi there"


def test_parse_raw_text_fallback():
    assert _parse_cli_answer("just plain text") == "just plain text"


def test_parse_empty():
    assert _parse_cli_answer("") == ""
    assert _parse_cli_answer("{}") == ""


class _FakeStdin:
    def write(self, data: bytes) -> None:  # noqa: D401
        pass

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdin = _FakeStdin()
        self.pid = 4321
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


@pytest.mark.asyncio
async def test_complete_yields_answer(monkeypatch):
    monkeypatch.setattr(
        agmod, "resolve_google_cli",
        lambda: GoogleCli(kind="gemini", argv_prefix=["gemini"]),
    )

    async def _fake_exec(*args, **kwargs):
        return _FakeProc(b'{"response": "Servus!"}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    brain = AntigravityBrain()
    chunks = [d async for d in brain.complete(_req())]
    texts = "".join(d.content for d in chunks if d.content)
    assert "Servus!" in texts
    assert any(d.finish_reason == "stop" for d in chunks)


@pytest.mark.asyncio
async def test_complete_raises_without_cli(monkeypatch):
    monkeypatch.setattr(agmod, "resolve_google_cli", lambda: None)
    brain = AntigravityBrain()
    with pytest.raises(RuntimeError):
        async for _ in brain.complete(_req()):
            pass


@pytest.mark.asyncio
async def test_argv_trusts_the_ephemeral_workdir(monkeypatch):
    """The CLI must trust its own throwaway workdir, else it self-degrades.

    Forensic 2026-06-20: the brain spawned the Gemini CLI in a fresh
    ``tempfile.mkdtemp`` folder with ``--approval-mode plan``. Because that
    folder is not a trusted workspace, the CLI logged "Approval mode overridden
    to 'default' because the current folder is not trusted", then "Failed to
    parse default sandbox policy" and exited rc=1 with an empty answer — forcing
    a fallback to a different provider on every turn. ``--skip-trust`` trusts the
    workspace for the session so ``plan`` (read-only) mode survives and no
    sandbox policy is loaded.
    """
    monkeypatch.setattr(
        agmod, "resolve_google_cli",
        lambda: GoogleCli(kind="gemini", argv_prefix=["gemini"]),
    )
    captured: dict[str, object] = {}

    async def _fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        return _FakeProc(b'{"response": "ok"}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    brain = AntigravityBrain()
    async for _ in brain.complete(_req()):
        pass
    argv = captured["argv"]
    assert "--skip-trust" in argv
    # Read-only conversational brain: approval mode stays "plan" (assert the
    # value of the flag, not just that the word appears somewhere in argv).
    assert argv[argv.index("--approval-mode") + 1] == "plan"


@pytest.mark.asyncio
async def test_complete_scrubs_api_key_env(monkeypatch):
    """The child must not inherit GEMINI_API_KEY (so the subscription login wins)."""
    monkeypatch.setenv("GEMINI_API_KEY", "should-not-leak")
    monkeypatch.setattr(
        agmod, "resolve_google_cli",
        lambda: GoogleCli(kind="gemini", argv_prefix=["gemini"]),
    )
    captured: dict[str, object] = {}

    async def _fake_exec(*args, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakeProc(b'{"response": "ok"}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    brain = AntigravityBrain()
    async for _ in brain.complete(_req()):
        pass
    env = captured["env"]
    assert env is not None
    assert "GEMINI_API_KEY" not in env


def test_build_argv_agy_uses_print_and_model():
    """agy 1.0.9 has --print/--model but NOT the gemini-CLI flags --approval-mode
    / -o json / --skip-trust (forensic 2026-06-20, live `agy --help`)."""
    from jarvis.plugins.brain.antigravity import _build_argv

    cli = GoogleCli(kind="agy", argv_prefix=["agy"])
    argv = _build_argv(cli, "hello", "gemini-3.1-pro-preview")
    assert argv[0] == "agy"
    assert "--print" in argv
    assert "hello" in argv
    assert "--model" in argv
    assert "gemini-3.1-pro-preview" in argv
    assert "--approval-mode" not in argv
    assert "-o" not in argv
    assert "--skip-trust" not in argv


def test_build_argv_gemini_keeps_skip_trust():
    """The Gemini-CLI branch is unchanged (read-only plan + skip-trust + json)."""
    from jarvis.plugins.brain.antigravity import _build_argv

    cli = GoogleCli(kind="gemini", argv_prefix=["gemini"])
    argv = _build_argv(cli, "hello", "gemini-3.5-flash")
    assert "--print" in argv or "-p" in argv
    assert "--skip-trust" in argv
    assert "plan" in argv


# ---- agy over ConPTY -------------------------------------------------------
# agy is a TUI tool: a plain pipe yields 0 bytes (the brain then sees no answer).
# It must be driven over a pseudo-terminal via pty_runner.run_cli_over_pty.


def _agy_cli() -> GoogleCli:
    return GoogleCli(kind="agy", argv_prefix=["agy.exe"])


def _fake_pty_result(text: str = "", *, error: str | None = None, timed_out: bool = False):
    from jarvis.google_cli.pty_runner import PtyRunResult

    return PtyRunResult(
        text=text, raw=text, exit_status=0, timed_out=timed_out, error=error
    )


@pytest.mark.asyncio
async def test_complete_agy_drives_pty_runner(monkeypatch):
    """kind='agy' must go through the PTY runner, not create_subprocess_exec."""
    monkeypatch.setattr(agmod, "resolve_google_cli", _agy_cli)
    captured: dict[str, object] = {}

    def _fake_run(argv, *, timeout_s, cwd=None, env=None, **kw):
        captured["argv"] = list(argv)
        return _fake_pty_result("Servus von agy!")

    monkeypatch.setattr(agmod, "run_cli_over_pty", _fake_run)
    # Guard: the pipe path must NOT be taken for agy.
    async def _boom(*a, **k):
        raise AssertionError("agy must not use create_subprocess_exec")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)

    brain = AntigravityBrain()
    chunks = [d async for d in brain.complete(_req())]
    texts = "".join(d.content for d in chunks if d.content)
    assert "Servus von agy!" in texts
    assert any(d.finish_reason == "stop" for d in chunks)
    assert captured["argv"][0] == "agy.exe"
    assert "--print" in captured["argv"]


@pytest.mark.asyncio
async def test_complete_agy_drops_key_and_hardens_path(monkeypatch):
    """The child env drops GEMINI_API_KEY and (on Windows) carries System32."""
    monkeypatch.setenv("GEMINI_API_KEY", "should-not-leak")
    monkeypatch.setattr(agmod, "resolve_google_cli", _agy_cli)
    captured: dict[str, object] = {}

    def _fake_run(argv, *, timeout_s, cwd=None, env=None, **kw):
        captured["env"] = env
        return _fake_pty_result("ok")

    monkeypatch.setattr(agmod, "run_cli_over_pty", _fake_run)
    brain = AntigravityBrain()
    async for _ in brain.complete(_req()):
        pass
    env = captured["env"]
    assert env is not None
    assert "GEMINI_API_KEY" not in env
    import sys as _sys

    if _sys.platform == "win32":
        assert "System32" in env.get("PATH", "")


# ---- isolated, hook/mcp-free CLI home (lag fix) ----------------------------
# agy reads the user's ~/.gemini/settings.json, which on this machine carries
# dozens of duplicated BridgeSpace PowerShell SessionStart/BeforeAgent hooks +
# mcpServers; agy boots ALL of them per --print turn (13s). Pointing HOME at an
# isolated home with a hook/mcp-free settings.json (and the copied OAuth creds)
# drops a turn to ~8s and kills the npm MCP boot (verified live 2026-06-21).


def _fake_real_gemini(tmp_path) -> str:
    real = tmp_path / "real" / ".gemini"
    real.mkdir(parents=True)
    (real / "oauth_creds.json").write_text('{"access_token":"a","refresh_token":"r"}')
    (real / "google_accounts.json").write_text('"user@example.com"')
    (real / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {"SessionStart": [{"command": "powershell ...", "type": "command"}] * 5},
                "mcpServers": {"github": {"command": "npx"}},
                "security": {"auth": {"selectedType": "oauth-personal"}},
                "model": {"name": "gemini-3.1-pro-preview"},
            }
        )
    )
    return str(real)


def test_isolated_home_strips_hooks_and_mcp(tmp_path):
    real = _fake_real_gemini(tmp_path)
    dest = str(tmp_path / "iso")
    home = agmod._ensure_isolated_home(
        real_dir=real,
        dest_root=dest,
        model="Gemini 3.5 Flash (Medium)",
    )
    g = os.path.join(home, ".gemini")
    settings = json.load(open(os.path.join(g, "settings.json"), encoding="utf-8"))
    assert "hooks" not in settings  # no per-turn PowerShell hook storm
    assert "mcpServers" not in settings  # no per-turn npm MCP boot
    assert settings["model"]["name"] == "Gemini 3.5 Flash (Medium)"
    assert settings["security"]["auth"]["selectedType"] == "oauth-personal"
    # OAuth login carried over so agy stays signed in under the redirected HOME
    assert os.path.isfile(os.path.join(g, "oauth_creds.json"))
    assert os.path.isfile(os.path.join(g, "google_accounts.json"))


def test_isolated_home_drops_copied_creds_after_logout(tmp_path):
    # First sync copies the login in; then the real creds vanish (logout removes
    # ~/.gemini/oauth_creds.json). The next sync must drop the stale iso copy, or
    # agy would stay signed in under the redirected HOME despite the logout.
    real = _fake_real_gemini(tmp_path)
    dest = str(tmp_path / "iso")
    agmod._ensure_isolated_home(real_dir=real, dest_root=dest, model="gemini-3.5-flash")
    iso_creds = os.path.join(dest, ".gemini", "oauth_creds.json")
    assert os.path.isfile(iso_creds)  # carried in on first sync

    os.remove(os.path.join(real, "oauth_creds.json"))  # simulate logout
    agmod._ensure_isolated_home(real_dir=real, dest_root=dest, model="gemini-3.5-flash")
    assert not os.path.isfile(iso_creds)  # stale copy dropped -> agy logged out too


@pytest.mark.asyncio
async def test_complete_agy_redirects_home_to_isolated(monkeypatch, tmp_path):
    real = _fake_real_gemini(tmp_path)
    iso_root = str(tmp_path / "iso")
    monkeypatch.setattr(agmod, "_real_gemini_dir", lambda: real)
    monkeypatch.setattr(agmod, "_iso_home_root", lambda: iso_root)
    monkeypatch.setattr(agmod, "resolve_google_cli", _agy_cli)
    captured: dict[str, object] = {}

    def _fake_run(argv, *, timeout_s, cwd=None, env=None, **kw):
        captured["env"] = env
        return _fake_pty_result("ok")

    monkeypatch.setattr(agmod, "run_cli_over_pty", _fake_run)
    brain = AntigravityBrain()
    async for _ in brain.complete(_req()):
        pass
    env = captured["env"]
    assert env.get("USERPROFILE") == iso_root  # HOME redirected off the real one
    assert env.get("HOME") == iso_root
    settings_path = Path(iso_root) / ".gemini" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "hooks" not in settings and "mcpServers" not in settings


@pytest.mark.asyncio
async def test_complete_agy_empty_answer_raises(monkeypatch):
    monkeypatch.setattr(agmod, "resolve_google_cli", _agy_cli)
    monkeypatch.setattr(agmod, "run_cli_over_pty", lambda *a, **k: _fake_pty_result(""))
    brain = AntigravityBrain()
    with pytest.raises(RuntimeError):
        async for _ in brain.complete(_req()):
            pass


@pytest.mark.asyncio
async def test_complete_agy_pty_unavailable_raises(monkeypatch):
    monkeypatch.setattr(agmod, "resolve_google_cli", _agy_cli)
    monkeypatch.setattr(
        agmod,
        "run_cli_over_pty",
        lambda *a, **k: _fake_pty_result("", error="No pseudo-terminal backend available"),
    )
    brain = AntigravityBrain()
    with pytest.raises(RuntimeError):
        async for _ in brain.complete(_req()):
            pass
