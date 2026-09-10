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


def _wiki_request() -> BrainRequest:
    return BrainRequest(
        messages=(BrainMessage(role="user", content="Source content here."),),
        system="Return ONLY a single JSON array. No prose before or after.",
    )


def test_structured_mode_forwards_the_json_contract_verbatim():
    """Wiki-tier callers need their JSON contract to reach the CLI model —
    the conversational wrapper made structured output impossible by
    instruction (live 2026-07-18: every extraction died with 'no JSON array
    found in response')."""
    brain = AntigravityBrain(structured_prompts=True)
    prompt = brain._render_prompt(_wiki_request())
    assert "Return ONLY a single JSON array" in prompt
    assert "Source content here." in prompt
    assert "one to three short sentences" not in prompt


def test_voice_mode_keeps_the_conversational_flattening():
    brain = AntigravityBrain()
    prompt = brain._render_prompt(_wiki_request())
    assert "one to three short sentences" in prompt
    assert "Return ONLY a single JSON array" not in prompt


def test_cli_prompt_includes_standing_instructions_without_heavy_router_prompt():
    req = BrainRequest(
        messages=(
            BrainMessage(
                role="user",
                content="Was ist das wertvollste Unternehmen?",  # i18n-allow
            ),
        ),
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
            BrainMessage(
                role="assistant",
                content="schef, alles laeuft.",  # i18n-allow
            ),
            BrainMessage(
                role="user",
                content="Du musst das nicht mehr sagen.",  # i18n-allow
            ),
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

    async def communicate(self, input=None) -> tuple[bytes, bytes]:  # noqa: A002 - subprocess API
        self.input = input
        return self._stdout, self._stderr

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


@pytest.mark.asyncio
async def test_complete_yields_answer(monkeypatch):
    monkeypatch.setattr(
        agmod,
        "resolve_google_cli",
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
        agmod,
        "resolve_google_cli",
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
        agmod,
        "resolve_google_cli",
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


def test_build_argv_agy_uses_stdin_and_read_only_json():
    """The current CLI reads stdin in print mode without a PTY."""
    from jarvis.plugins.brain.antigravity import _build_argv

    cli = GoogleCli(kind="agy", argv_prefix=["agy"])
    argv = _build_argv(cli, "hello", "gemini-3.1-pro-preview")
    assert argv[0] == "agy"
    assert "--print" not in argv
    assert "hello" not in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--mode") + 1] == "plan"
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


# ---- agy with piped UTF-8 input -------------------------------------------


def _agy_cli() -> GoogleCli:
    return GoogleCli(kind="agy", argv_prefix=["agy.exe"])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["agy", "gemini"])
async def test_complete_sends_large_unicode_prompt_on_stdin(monkeypatch, kind):
    monkeypatch.setattr(
        agmod,
        "resolve_google_cli",
        lambda: GoogleCli(kind=kind, argv_prefix=[kind]),
    )
    captured = {}
    proc = _FakeProc(b'{"response": "ok"}')

    async def spawn(*argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = kwargs["cwd"]
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    text = "Evidence: \U0001f600\u00e9\n" * 60000
    brain = AntigravityBrain(structured_prompts=True)
    request = _req(text)
    chunks = [d async for d in brain.complete(request)]
    assert "".join(d.content or "" for d in chunks) == "ok"
    assert proc.input.decode("utf-8") == brain._render_prompt(request)
    assert len(proc.input) > 743147
    assert sum(map(len, captured["argv"])) < 1000
    assert not await asyncio.to_thread(Path(captured["cwd"]).exists)
    assert any(d.finish_reason == "stop" for d in chunks)


@pytest.mark.asyncio
async def test_complete_agy_drops_key_and_hardens_path(monkeypatch):
    """The child env drops GEMINI_API_KEY and (on Windows) carries System32."""
    monkeypatch.setenv("GEMINI_API_KEY", "should-not-leak")
    monkeypatch.setattr(agmod, "resolve_google_cli", _agy_cli)
    captured: dict[str, object] = {}

    async def _fake_exec(*argv, **kwargs):
        captured["env"] = kwargs["env"]
        return _FakeProc(b'{"response": "ok"}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
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

    async def _fake_exec(*argv, **kwargs):
        captured["env"] = kwargs["env"]
        return _FakeProc(b'{"response": "ok"}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
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

    async def _fake_exec(*args, **kwargs):
        return _FakeProc(b'{"response": ""}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    brain = AntigravityBrain()
    with pytest.raises(RuntimeError):
        async for _ in brain.complete(_req()):
            pass


@pytest.mark.asyncio
async def test_complete_agy_spawn_failure_raises(monkeypatch):
    monkeypatch.setattr(agmod, "resolve_google_cli", _agy_cli)

    async def spawn(*args, **kwargs):
        raise OSError("CLI not available")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError, match="could not be launched"):
        async for _ in AntigravityBrain().complete(_req()):
            pass


def test_cli_timeout_accepts_a_caller_budget() -> None:
    """Background callers may extend the internal CLI cap (wiki judge tier)."""
    from jarvis.plugins.brain import antigravity as antigravity_module
    from jarvis.plugins.brain.antigravity import AntigravityBrain

    assert AntigravityBrain(cli_timeout_s=180.0)._cli_timeout_s == 180.0
    assert AntigravityBrain()._cli_timeout_s == antigravity_module._CLI_TIMEOUT_S


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_stalled_stdin_is_killed_on_timeout_or_cancellation(monkeypatch, cancel):
    monkeypatch.setattr(agmod, "resolve_google_cli", _agy_cli)
    started = asyncio.Event()

    class StalledProc(_FakeProc):
        pid = None

        def __init__(self):
            super().__init__(b"")
            self.pid = None
            self.killed = False
            self.waited = False

        async def communicate(self, input=None):  # noqa: A002 - subprocess API
            started.set()
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True
            return -1

    proc = StalledProc()

    async def spawn(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    async def run():
        return [d async for d in AntigravityBrain(cli_timeout_s=0.1).complete(_req())]

    task = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), timeout=2)
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await asyncio.wait_for(task, timeout=2)
    assert proc.killed and proc.waited


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,output",
    [
        (1, b'{"response":"failure text"}'),
        (0, b'{"status":"ERROR","response":"failure text"}'),
        (0, b'{"error":{"message":"failure"},"response":"failure text"}'),
    ],
)
async def test_failed_cli_response_is_never_an_answer(monkeypatch, code, output):
    monkeypatch.setattr(agmod, "resolve_google_cli", _agy_cli)

    async def spawn(*args, **kwargs):
        return _FakeProc(output, returncode=code)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError):
        async for _ in AntigravityBrain().complete(_req()):
            pass
