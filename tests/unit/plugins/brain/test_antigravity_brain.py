"""AntigravityBrain — OAuth-only brain that drives the official Google CLI.

The subprocess is faked; no real CLI or network is touched.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.plugins.brain import antigravity as agmod
from jarvis.plugins.brain.antigravity import AntigravityBrain, _parse_cli_answer
from jarvis.google_cli.resolver import GoogleCli


def _req(text: str = "Hello") -> BrainRequest:
    return BrainRequest(messages=(BrainMessage(role="user", content=text),))


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
