"""The brain's window into the login vault — and how narrow it stays.

The load-bearing test in this file is the last one: no action, on any input,
may put a stored password into a tool result. Everything else here is ordinary
behaviour; that one is the safety property the whole feature rests on.
"""

from __future__ import annotations

import uuid

import pytest

from jarvis.brain.factory import ROUTER_TOOLS
from jarvis.core.protocols import ExecutionContext
from jarvis.logins import store as store_module
from jarvis.logins.store import Credential, CredentialStore, LoginStatus
from jarvis.marketplace.token_store import InMemoryBackend
from jarvis.missions.workers.worker_tool_broker import _FORBIDDEN_EXACT
from jarvis.plugins.tool.credentials import CredentialsTool

_PASSWORD = "correct-horse-battery-staple"  # noqa: S105
_TOTP = "JBSWY3DPEHPK3PXP"  # noqa: S105
_NOTES = "# GitHub\n\nDeveloper account. A code is mailed on a new device."


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> CredentialStore:
    store = CredentialStore(backend=InMemoryBackend())
    store.save(
        Credential(
            service_id="github",
            label="GitHub",
            domains=("github.com",),
            username="someone@example.com",
            password=_PASSWORD,
            notes=_NOTES,
            totp_secret=_TOTP,
        )
    )
    monkeypatch.setattr(store_module, "default_store", lambda: store)
    return store


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid.uuid4(), user_utterance="", config={}, memory_read=None
    )


class TestPlacement:
    def test_identity_and_risk_tier(self) -> None:
        assert CredentialsTool.name == "credentials"
        # Metadata reads and a status stamp on the user's own record. A
        # confirmation nag here would only train the user to click through.
        assert CredentialsTool.risk_tier == "safe"

    def test_the_router_can_reach_it(self) -> None:
        """Without this the model's only move at a login form is to ask the
        user to say a password out loud — the exact thing §7 forbids."""
        assert "credentials" in ROUTER_TOOLS

    def test_it_is_not_banned_from_mission_workers(self) -> None:
        """An unattended errand is precisely where hitting a login wall hurts."""
        assert "credentials" not in _FORBIDDEN_EXACT


@pytest.mark.asyncio
class TestFind:
    async def test_a_stored_login_is_reported_with_its_handle(
        self, vault: CredentialStore
    ) -> None:
        result = await CredentialsTool().execute(
            {"action": "find", "url": "https://github.com/login"}, _ctx()
        )

        assert result.success is True
        assert "service_id: github" in result.output
        assert "someone@example.com" in result.output

    async def test_the_user_notes_come_along(self, vault: CredentialStore) -> None:
        """The notes are the point — they carry what the login actually needs."""
        result = await CredentialsTool().execute(
            {"action": "find", "url": "github.com"}, _ctx()
        )

        assert "A code is mailed on a new device." in result.output

    async def test_the_placeholder_to_use_is_spelled_out(
        self, vault: CredentialStore
    ) -> None:
        result = await CredentialsTool().execute(
            {"action": "find", "url": "github.com"}, _ctx()
        )

        assert 'SECRET("github", "password")' in result.output

    async def test_a_subdomain_finds_the_parent_entry(
        self, vault: CredentialStore
    ) -> None:
        result = await CredentialsTool().execute(
            {"action": "find", "url": "https://gist.github.com/x"}, _ctx()
        )

        assert "service_id: github" in result.output

    async def test_an_unknown_site_says_so_and_forbids_asking(
        self, vault: CredentialStore
    ) -> None:
        result = await CredentialsTool().execute(
            {"action": "find", "url": "https://example.com/login"}, _ctx()
        )

        assert result.success is True
        assert "No stored login" in result.output
        # The failure mode this guards: the model politely asks the user to
        # dictate a password, which puts it straight into the transcript.
        assert "do not ask" in result.output.lower()

    async def test_a_rejected_entry_is_flagged(self, vault: CredentialStore) -> None:
        vault.mark_used("github", LoginStatus.REJECTED)

        result = await CredentialsTool().execute(
            {"action": "find", "url": "github.com"}, _ctx()
        )

        assert "refused" in result.output

    async def test_find_without_a_target_fails_clearly(
        self, vault: CredentialStore
    ) -> None:
        result = await CredentialsTool().execute({"action": "find"}, _ctx())

        assert result.success is False
        assert result.error is not None


@pytest.mark.asyncio
class TestList:
    async def test_an_empty_vault_says_so(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            store_module,
            "default_store",
            lambda: CredentialStore(backend=InMemoryBackend()),
        )

        result = await CredentialsTool().execute({"action": "list"}, _ctx())

        assert result.success is True
        assert "No logins are stored yet" in result.output

    async def test_stored_logins_are_listed(self, vault: CredentialStore) -> None:
        result = await CredentialsTool().execute({"action": "list"}, _ctx())

        assert "GitHub" in result.output
        assert "someone@example.com" in result.output


@pytest.mark.asyncio
class TestReport:
    async def test_a_successful_login_marks_the_entry_proven(
        self, vault: CredentialStore
    ) -> None:
        result = await CredentialsTool().execute(
            {"action": "report", "service_id": "github", "outcome": "worked"}, _ctx()
        )

        assert result.success is True
        assert vault.load("github").status is LoginStatus.OK  # type: ignore[union-attr]

    async def test_a_refused_login_marks_the_entry_rejected(
        self, vault: CredentialStore
    ) -> None:
        result = await CredentialsTool().execute(
            {"action": "report", "service_id": "github", "outcome": "rejected"}, _ctx()
        )

        assert result.success is True
        assert vault.load("github").status is LoginStatus.REJECTED  # type: ignore[union-attr]
        # A wrong password must not turn into a retry loop against a real site.
        assert "do not keep retrying" in result.output.lower()

    async def test_an_unknown_service_cannot_be_reported(
        self, vault: CredentialStore
    ) -> None:
        result = await CredentialsTool().execute(
            {"action": "report", "service_id": "nope", "outcome": "worked"}, _ctx()
        )

        assert result.success is False
        assert result.error is not None

    @pytest.mark.parametrize("outcome", ["probably fine", "", "ok-ish"])
    async def test_only_the_two_defined_outcomes_are_accepted(
        self, vault: CredentialStore, outcome: str
    ) -> None:
        """An open string would drift into "probably fine" and the
        confirmation rule would quietly rot."""
        result = await CredentialsTool().execute(
            {"action": "report", "service_id": "github", "outcome": outcome}, _ctx()
        )

        assert result.success is False
        assert vault.load("github").status is LoginStatus.UNKNOWN  # type: ignore[union-attr]


@pytest.mark.asyncio
class TestFailureModes:
    @pytest.mark.parametrize("action", ["reveal", "", "delete", "get_password"])
    async def test_undefined_actions_are_refused(
        self, vault: CredentialStore, action: str
    ) -> None:
        result = await CredentialsTool().execute({"action": action}, _ctx())

        assert result.success is False
        assert result.error is not None

    async def test_an_unopenable_vault_degrades_honestly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom() -> CredentialStore:
            raise RuntimeError("keychain locked")

        monkeypatch.setattr(store_module, "default_store", _boom)

        result = await CredentialsTool().execute(
            {"action": "find", "url": "github.com"}, _ctx()
        )

        assert result.success is False
        assert result.error is not None
        assert "locked" in result.error.lower()


@pytest.mark.asyncio
class TestTheSecretNeverLeaves:
    """The property the whole feature rests on, checked across every action."""

    @pytest.mark.parametrize(
        "args",
        [
            {"action": "find", "url": "https://github.com/login"},
            {"action": "find", "url": "github.com"},
            {"action": "find", "service_id": "github"},
            {"action": "list"},
            {"action": "report", "service_id": "github", "outcome": "worked"},
            {"action": "report", "service_id": "github", "outcome": "rejected"},
            {"action": "find", "url": "unknown.example"},
        ],
    )
    async def test_no_action_returns_a_stored_secret(
        self, vault: CredentialStore, args: dict[str, str]
    ) -> None:
        result = await CredentialsTool().execute(args, _ctx())

        blob = f"{result.output}\n{result.error}"
        assert _PASSWORD not in blob
        assert _TOTP not in blob

    async def test_the_schema_offers_no_way_to_ask_for_one(self) -> None:
        """A future 'reveal' action would undo the split this design exists for."""
        actions = CredentialsTool.schema["properties"]["action"]["enum"]

        assert set(actions) == {"find", "list", "report"}
