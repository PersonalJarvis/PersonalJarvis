"""The user's invariant: connected plugins survive app close / PC restart and
are NEVER auto-removed — only an explicit user DELETE removes one.

This is the regression guard for the "plugins disappear after restart" bug
class (project_bug_oauth_plugin_disconnect_after_restart)."""

from datetime import UTC, datetime, timedelta

import pytest

from jarvis.marketplace.refresh_scheduler import refresh_due_tokens
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore


class _InvalidGrantHandler:
    plugin_id = "stripe"

    async def refresh(self, current):
        raise RuntimeError("revoked")  # auth server returned invalid_grant


@pytest.mark.asyncio
async def test_dcr_and_pat_plugins_survive_restart_and_a_revoked_refresh():
    backend = InMemoryBackend()  # stands in for Credential Manager

    # --- session 1: connect a DCR plugin (with refresh) + a PAT plugin (none)
    s1 = TokenStore(backend)
    near = datetime.now(UTC) + timedelta(seconds=60)
    s1.save(
        "stripe",
        Tokens(access="x", refresh="r", expires_at=near, extra={"client_id": "c"}),
    )
    s1.save("github", Tokens(access="ghp_static"))  # PAT, no refresh

    # --- "restart": a brand-new store over the same backend (keyring survives)
    s2 = TokenStore(backend)
    assert s2.load("stripe") is not None
    assert s2.load("github") is not None

    # --- a refresh cycle where the DCR refresh is rejected
    outcomes = await refresh_due_tokens(
        ["stripe", "github"], s2, lambda pid: _InvalidGrantHandler()
    )

    # PAT skipped (no refresh token); DCR revoked-but-kept.
    assert outcomes["github"] == "skipped"
    assert s2.load("github") is not None, "PAT plugin must never be touched"
    assert s2.load("stripe") is not None, "revoked DCR plugin must NOT vanish"
    assert s2.load("stripe").needs_reauth is True
