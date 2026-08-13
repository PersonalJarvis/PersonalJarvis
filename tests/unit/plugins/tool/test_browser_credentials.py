"""The browser tool's half of the vault guarantee.

Kept in its own file rather than appended to ``test_browser.py`` so the two
concerns stay separable: that file pins why the tool exists, this one pins that
a stored password reaches the browser and nothing else.

The claim under test is narrow and checkable: the value appears in the bytes on
the subprocess pipe, and in neither the tool-call arguments nor the tool result.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from jarvis.core.protocols import ExecutionContext
from jarvis.logins import store as store_module
from jarvis.logins.store import Credential, CredentialStore, LoginStatus
from jarvis.marketplace.token_store import InMemoryBackend
from jarvis.plugins.tool.browser import BrowserTool

_PASSWORD = "correct-horse-battery-staple"  # noqa: S105

_LOGIN_SCRIPT = (
    'type_into("#user", SECRET("github", "username"))\n'
    'type_into("#pass", SECRET("github", "password"))\n'
    'click("Sign in")\n'
)


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> CredentialStore:
    """An in-memory vault installed at the single seam every caller uses."""
    store = CredentialStore(backend=InMemoryBackend())
    store.save(
        Credential(
            service_id="github",
            label="GitHub",
            domains=("github.com",),
            username="someone@example.com",
            password=_PASSWORD,
        )
    )
    monkeypatch.setattr(store_module, "default_store", lambda: store)
    return store


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid.uuid4(), user_utterance="", config={}, memory_read=None
    )


class _FakeProcess:
    """Stands in for browser-harness. Records what was piped to it."""

    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode
        self.piped: bytes = b""

    async def communicate(self, data: bytes) -> tuple[bytes, bytes | None]:
        self.piped = data
        return self._stdout, None

    def kill(self) -> None:  # pragma: no cover - only the timeout path uses it
        pass

    async def wait(self) -> int:  # pragma: no cover
        return self.returncode


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Pretend browser-harness is installed, and capture the spawned process."""
    monkeypatch.setattr(
        "jarvis.plugins.tool.browser._resolve_binary", lambda: "/usr/bin/browser-harness"
    )
    captured: dict[str, Any] = {"proc": None, "spawned": 0}

    def _install(stdout: bytes = b"done", returncode: int = 0) -> None:
        proc = _FakeProcess(stdout, returncode)
        captured["proc"] = proc

        async def _fake_exec(*_args: object, **_kwargs: object) -> _FakeProcess:
            captured["spawned"] += 1
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)

    captured["install"] = _install
    _install()
    return captured


class TestConfirmationTier:
    def test_an_unproven_login_asks_first(self, vault: CredentialStore) -> None:
        tool = BrowserTool()

        tier = tool.risk_tier_for_args({"goal": "sign in", "script": _LOGIN_SCRIPT})

        assert tier == "ask"

    def test_a_proven_login_runs_silently(self, vault: CredentialStore) -> None:
        vault.mark_used("github", LoginStatus.OK)
        tool = BrowserTool()

        tier = tool.risk_tier_for_args({"goal": "sign in", "script": _LOGIN_SCRIPT})

        assert tier is None

    def test_a_rejected_login_asks_again(self, vault: CredentialStore) -> None:
        vault.mark_used("github", LoginStatus.OK)
        vault.mark_used("github", LoginStatus.REJECTED)
        tool = BrowserTool()

        tier = tool.risk_tier_for_args({"goal": "sign in", "script": _LOGIN_SCRIPT})

        assert tier == "ask"

    def test_a_script_without_credentials_is_not_escalated(
        self, vault: CredentialStore
    ) -> None:
        tool = BrowserTool()

        tier = tool.risk_tier_for_args(
            {"goal": "read the page", "script": "print(page_info())"}
        )

        assert tier is None

    def test_an_unreadable_vault_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A vault that cannot be read must ask, never proceed quietly."""

        def _boom() -> CredentialStore:
            raise RuntimeError("keychain locked")

        monkeypatch.setattr(store_module, "default_store", _boom)
        tool = BrowserTool()

        tier = tool.risk_tier_for_args({"goal": "sign in", "script": _LOGIN_SCRIPT})

        assert tier == "ask"


@pytest.mark.asyncio
class TestInjection:
    async def test_the_password_reaches_the_subprocess(
        self, vault: CredentialStore, harness: dict[str, Any]
    ) -> None:
        tool = BrowserTool()

        await tool.execute({"goal": "sign in", "script": _LOGIN_SCRIPT}, _ctx())

        piped = harness["proc"].piped.decode("utf-8")
        assert _PASSWORD in piped
        assert "SECRET(" not in piped

    async def test_the_tool_arguments_keep_only_the_placeholder(
        self, vault: CredentialStore, harness: dict[str, Any]
    ) -> None:
        """What gets logged and replayed is the args dict — it must stay clean."""
        tool = BrowserTool()
        args = {"goal": "sign in", "script": _LOGIN_SCRIPT}

        await tool.execute(args, _ctx())

        assert _PASSWORD not in str(args)
        assert 'SECRET("github", "password")' in args["script"]

    async def test_an_unknown_service_fails_before_spawning(
        self, vault: CredentialStore, harness: dict[str, Any]
    ) -> None:
        tool = BrowserTool()

        result = await tool.execute(
            {"goal": "sign in", "script": 'x = SECRET("nosuchsite", "password")'},
            _ctx(),
        )

        assert result.success is False
        assert result.error is not None
        assert "No stored login" in result.error
        assert harness["spawned"] == 0

    async def test_a_script_without_placeholders_is_piped_unchanged(
        self, vault: CredentialStore, harness: dict[str, Any]
    ) -> None:
        tool = BrowserTool()
        script = "print(page_info())"

        await tool.execute({"goal": "read", "script": script}, _ctx())

        assert harness["proc"].piped.decode("utf-8") == script


@pytest.mark.asyncio
class TestOutputScrubbing:
    async def test_a_printed_password_is_redacted_from_the_result(
        self, vault: CredentialStore, harness: dict[str, Any]
    ) -> None:
        """The skill forbids printing a credential field; this is the backstop."""
        harness["install"](stdout=f"field now reads {_PASSWORD}".encode())
        tool = BrowserTool()

        result = await tool.execute(
            {"goal": "sign in", "script": _LOGIN_SCRIPT}, _ctx()
        )

        assert result.output is not None
        assert _PASSWORD not in result.output
        assert "[redacted]" in result.output

    async def test_the_failure_path_is_scrubbed_too(
        self, vault: CredentialStore, harness: dict[str, Any]
    ) -> None:
        """A crashing script's traceback can quote the line it died on."""
        harness["install"](
            stdout=f'Traceback: type_into("#pass", "{_PASSWORD}")'.encode(),
            returncode=1,
        )
        tool = BrowserTool()

        result = await tool.execute(
            {"goal": "sign in", "script": _LOGIN_SCRIPT}, _ctx()
        )

        assert result.success is False
        assert result.output is not None
        assert _PASSWORD not in result.output

    async def test_ordinary_output_survives_intact(
        self, vault: CredentialStore, harness: dict[str, Any]
    ) -> None:
        harness["install"](stdout=b"Signed in as someone@example.com")
        tool = BrowserTool()

        result = await tool.execute(
            {"goal": "sign in", "script": _LOGIN_SCRIPT}, _ctx()
        )

        # The username is deliberately not redacted — it is not a secret, and
        # blanking it would shred the very output the user wants to read.
        assert result.output == "Signed in as someone@example.com"
