"""Reconnect reasons, and the daily self-heal probe that undoes a wrong flag.

Two failures motivated this: a connection could die and never say WHY (the
reason lived only in a rotating log), and once flagged it was never retried, so
a provider outage misread as a hard rejection stranded a perfectly good grant
until the user noticed by hand.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from jarvis.marketplace.refresh_scheduler import (
    FAILED,
    REFRESHED,
    REVOKED,
    SKIPPED,
    refresh_plugin_token,
)
from jarvis.marketplace.token_store import (
    REAUTH_CLIENT_MISSING,
    REAUTH_CLIENT_REJECTED,
    REAUTH_PROVIDER_REJECTED,
    REAUTH_ROTATION_LOST,
    InMemoryBackend,
    Tokens,
    TokenStore,
)

DAY = 86_400


def _store() -> TokenStore:
    return TokenStore(InMemoryBackend())


def _expired(refresh: str | None = "r0") -> Tokens:
    return Tokens(
        access="a0",
        refresh=refresh,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )


class _Handler:
    """Minimal AuthHandler: either returns fresh tokens or raises."""

    def __init__(self, new_tokens: Tokens | None = None, raise_exc: Exception | None = None):
        self._new = new_tokens
        self._raise = raise_exc
        self.calls = 0

    async def refresh(self, current: Tokens) -> Tokens:
        self.calls += 1
        if self._raise is not None:
            raise self._raise
        assert self._new is not None
        return self._new

    async def start(self, plugin_spec):  # noqa: ANN001, ANN201 (protocol stub)
        raise NotImplementedError

    async def await_completion(self, session):  # noqa: ANN001, ANN201
        raise NotImplementedError

    def auth_header(self, tokens: Tokens) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens.access}"}


# ---------------------------------------------------------------------------
# The reason is recorded, and it is the right one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("refresh HTTP 400: invalid_grant", REAUTH_PROVIDER_REJECTED),
        ("revoked", REAUTH_PROVIDER_REJECTED),
        ("token has been expired or revoked", REAUTH_PROVIDER_REJECTED),
        ("refresh HTTP 401: invalid_client", REAUTH_CLIENT_REJECTED),
        ("unauthorized_client", REAUTH_CLIENT_REJECTED),
        ("client was not found", REAUTH_CLIENT_REJECTED),
        # The DCR handler's message matches TWO markers; the specific one wins.
        (
            "refresh: no stored client_id — reconnect required to heal this connection",
            REAUTH_CLIENT_MISSING,
        ),
    ],
)
@pytest.mark.asyncio
async def test_records_the_reason_it_was_flagged(error: str, expected: str) -> None:
    store = _store()
    store.save("p", _expired())
    handler = _Handler(raise_exc=RuntimeError(error))

    attempt = await refresh_plugin_token("p", store, lambda pid: handler)

    assert attempt.outcome == REVOKED
    saved = store.load("p")
    assert saved is not None
    assert saved.needs_reauth is True
    assert saved.reauth_reason == expected
    assert saved.reauth_at is not None


@pytest.mark.asyncio
async def test_transient_failure_records_no_reason_and_stays_connected() -> None:
    """A DNS blip must not look like a revocation — it is retried, not flagged."""
    store = _store()
    store.save("p", _expired())
    handler = _Handler(raise_exc=RuntimeError("[Errno 11001] getaddrinfo failed"))

    attempt = await refresh_plugin_token("p", store, lambda pid: handler)

    assert attempt.outcome == FAILED
    saved = store.load("p")
    assert saved is not None
    assert saved.needs_reauth is False
    assert saved.reauth_reason is None


@pytest.mark.asyncio
async def test_reauth_at_survives_repeated_failures() -> None:
    """The timestamp means "when it died", not "when we last checked"."""
    store = _store()
    died = datetime.now(UTC) - timedelta(days=9)
    store.save(
        "p",
        replace(
            _expired(),
            needs_reauth=True,
            reauth_reason=REAUTH_PROVIDER_REJECTED,
            reauth_at=died,
            reauth_retry_at=died,
        ),
    )
    handler = _Handler(raise_exc=RuntimeError("invalid_grant"))

    await refresh_plugin_token("p", store, lambda pid: handler, reauth_retry_seconds=DAY)

    saved = store.load("p")
    assert saved is not None
    assert saved.reauth_at == died
    # ...while the retry stamp moved forward, putting the next probe a day out.
    assert saved.reauth_retry_at is not None
    assert saved.reauth_retry_at > died


# ---------------------------------------------------------------------------
# The self-heal probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flagged_connection_is_not_probed_before_the_interval() -> None:
    store = _store()
    store.save(
        "p",
        replace(
            _expired(),
            needs_reauth=True,
            reauth_reason=REAUTH_PROVIDER_REJECTED,
            reauth_at=datetime.now(UTC) - timedelta(days=3),
            reauth_retry_at=datetime.now(UTC) - timedelta(hours=2),
        ),
    )
    handler = _Handler(new_tokens=Tokens(access="a1", refresh="r1"))

    attempt = await refresh_plugin_token("p", store, lambda pid: handler, reauth_retry_seconds=DAY)

    assert attempt.outcome == SKIPPED
    assert handler.calls == 0


@pytest.mark.asyncio
async def test_flagged_connection_heals_itself_once_the_interval_passes() -> None:
    """The whole point: a wrongly-flagged grant comes back without the user."""
    store = _store()
    store.save(
        "p",
        replace(
            _expired(),
            needs_reauth=True,
            reauth_reason=REAUTH_CLIENT_REJECTED,
            reauth_at=datetime.now(UTC) - timedelta(days=10),
            reauth_retry_at=datetime.now(UTC) - timedelta(days=2),
        ),
    )
    handler = _Handler(new_tokens=Tokens(access="a1", refresh="r1"))

    attempt = await refresh_plugin_token("p", store, lambda pid: handler, reauth_retry_seconds=DAY)

    assert attempt.outcome == REFRESHED
    assert handler.calls == 1
    saved = store.load("p")
    assert saved is not None
    assert saved.access == "a1"
    # The entire reconnect story is cleared, not just the flag — a leftover
    # reason would keep explaining a failure that no longer exists.
    assert saved.needs_reauth is False
    assert saved.reauth_reason is None
    assert saved.reauth_at is None
    assert saved.reauth_retry_at is None


@pytest.mark.asyncio
async def test_rotation_lost_is_never_probed_again() -> None:
    """Replaying a retired token can cost the whole account. Never retry it."""
    store = _store()
    store.save(
        "p",
        replace(
            _expired(),
            needs_reauth=True,
            reauth_reason=REAUTH_ROTATION_LOST,
            reauth_at=datetime.now(UTC) - timedelta(days=400),
            reauth_retry_at=None,
        ),
    )
    handler = _Handler(new_tokens=Tokens(access="a1", refresh="r1"))

    attempt = await refresh_plugin_token("p", store, lambda pid: handler, reauth_retry_seconds=DAY)

    assert attempt.outcome == SKIPPED
    assert handler.calls == 0


@pytest.mark.asyncio
async def test_self_heal_is_opt_in() -> None:
    """Without the interval a flagged plugin behaves exactly as it used to."""
    store = _store()
    store.save("p", replace(_expired(), needs_reauth=True))
    handler = _Handler(new_tokens=Tokens(access="a1", refresh="r1"))

    attempt = await refresh_plugin_token("p", store, lambda pid: handler)

    assert attempt.outcome == SKIPPED
    assert handler.calls == 0


@pytest.mark.asyncio
async def test_probe_is_stamped_before_the_provider_call() -> None:
    """A hung or crashing provider call must not re-probe every five minutes."""
    store = _store()
    store.save(
        "p",
        replace(
            _expired(),
            needs_reauth=True,
            reauth_reason=REAUTH_PROVIDER_REJECTED,
            reauth_at=datetime.now(UTC) - timedelta(days=5),
        ),
    )
    stamps: list[datetime | None] = []

    class _Recording(_Handler):
        async def refresh(self, current: Tokens) -> Tokens:
            # What is on disk at the moment the provider is called.
            saved = store.load("p")
            stamps.append(saved.reauth_retry_at if saved else None)
            raise RuntimeError("boom")

    await refresh_plugin_token("p", store, lambda pid: _Recording(), reauth_retry_seconds=DAY)

    assert stamps and stamps[0] is not None, "retry stamp must be persisted before the call"


@pytest.mark.asyncio
async def test_flagged_token_without_refresh_is_never_probed() -> None:
    store = _store()
    store.save("p", replace(_expired(refresh=None), needs_reauth=True))
    handler = _Handler(new_tokens=Tokens(access="a1"))

    attempt = await refresh_plugin_token("p", store, lambda pid: handler, reauth_retry_seconds=DAY)

    assert attempt.outcome == SKIPPED
    assert handler.calls == 0


@pytest.mark.asyncio
async def test_legacy_flag_without_timestamps_is_probed_once() -> None:
    """Gmail/Drive/Linear were flagged before these fields existed."""
    store = _store()
    store.save("p", replace(_expired(), needs_reauth=True))
    handler = _Handler(new_tokens=Tokens(access="a1", refresh="r1"))

    attempt = await refresh_plugin_token("p", store, lambda pid: handler, reauth_retry_seconds=DAY)

    assert attempt.outcome == REFRESHED
    assert handler.calls == 1


# ---------------------------------------------------------------------------
# Storage round-trip
# ---------------------------------------------------------------------------


def test_reason_survives_a_storage_round_trip() -> None:
    flagged_at = datetime.now(UTC) - timedelta(days=2)
    tokens = Tokens(
        access="a",
        refresh="r",
        needs_reauth=True,
        reauth_reason=REAUTH_PROVIDER_REJECTED,
        reauth_at=flagged_at,
        reauth_retry_at=flagged_at,
    )

    restored = Tokens.from_json(tokens.to_json())

    assert restored == tokens


def test_a_token_written_before_these_fields_still_loads() -> None:
    legacy = '{"access":"a","refresh":"r","expires_at":null,"extra":{},"needs_reauth":true}'

    restored = Tokens.from_json(legacy)

    assert restored.needs_reauth is True
    assert restored.reauth_reason is None
    assert restored.reauth_at is None
    assert restored.reauth_retry_at is None
