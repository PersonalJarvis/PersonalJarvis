"""The shared Google plumbing, driven fully offline through MockTransport.

These tests pin the family-wide guarantees every Google adapter leans on:
bounded rate-limit retries that honour ``Retry-After``, credential failures
that become actionable sentences and never echo the token, and the text
helpers (body cap, HTML stripping, base64url) that keep imports memory-shaped.
"""

from __future__ import annotations

import httpx
import pytest

from jarvis.ultrawiki.adapters import _google as g


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected Google plugin, without touching the host's keyring."""

    class _Tokens:
        access = "ya29.test"
        needs_reauth = False

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )


# ---------------------------------------------------------------------------
# Rate limits — bounded waits, honest surrender
# ---------------------------------------------------------------------------


async def test_a_throttled_request_waits_and_then_succeeds():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        payload = await g.google_get_json(
            client, "https://example.test/x", "tok", "Gmail"
        )
    assert payload == {"ok": True}
    assert len(calls) == 2


async def test_a_persistently_throttled_request_gives_up_honestly():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(g.GoogleAdapterError, match="rate limit"):
            await g.google_get(client, "https://example.test/x", "tok", "Gmail")
    # Bounded: the retry budget is spent, never a spin.
    assert len(calls) == 3


async def test_a_quota_shaped_403_counts_as_throttling_not_as_a_scope_problem():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"errors": [{"reason": "rateLimitExceeded"}]}},
            headers={"Retry-After": "0"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(g.GoogleAdapterError, match="rate limit"):
            await g.google_get(client, "https://example.test/x", "tok", "Drive")


# ---------------------------------------------------------------------------
# Credential failures — actionable, token never echoed
# ---------------------------------------------------------------------------


async def test_a_rejected_token_says_what_to_do_and_never_echoes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(g.GoogleAdapterError) as excinfo:
            await g.google_get(client, "https://example.test/x", "ya29.secret", "Gmail")
    message = str(excinfo.value)
    assert "Reconnect" in message
    assert "ya29.secret" not in message


async def test_a_tolerated_status_comes_back_to_the_caller():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await g.google_get(
            client, "https://example.test/x", "tok", "Drive", tolerate=(404,)
        )
    assert response.status_code == 404


def test_an_unconnected_plugin_refuses_with_the_store_as_the_fix(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            return None

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    with pytest.raises(g.GoogleAdapterError, match="not connected"):
        g.google_token("gmail", "Gmail")


def test_an_expired_connection_is_named_as_such(monkeypatch):
    class _Tokens:
        access = "ya29.old"
        needs_reauth = True

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    with pytest.raises(g.GoogleAdapterError, match="expired"):
        g.google_token("google_drive", "Google Drive")


def test_a_broken_credential_store_is_a_sentence_not_a_stack_trace(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            raise OSError("keyring backend unavailable")

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    with pytest.raises(g.GoogleAdapterError, match="credential store"):
        g.google_token("google_calendar", "Google Calendar")


# ---------------------------------------------------------------------------
# Text helpers — the memory-shaped scope
# ---------------------------------------------------------------------------


def test_an_oversized_body_is_capped_and_marked():
    body, truncated = g.cap_body("x" * (g.BODY_CAP + 10))
    assert truncated is True
    assert body.endswith(g.TRUNCATION_MARKER)
    assert len(body) == g.BODY_CAP + len(g.TRUNCATION_MARKER)


def test_a_small_body_passes_untouched():
    body, truncated = g.cap_body("hello")
    assert (body, truncated) == ("hello", False)


def test_html_is_stripped_to_readable_text_without_scripts():
    markup = (
        "<html><head><style>p{color:red}</style></head><body>"
        "<script>alert('x')</script><p>First&nbsp;line</p><p>Second <b>line</b></p>"
        "</body></html>"
    )
    text = g.strip_html(markup)
    assert "First line" in text
    assert "Second line" in text
    assert "alert" not in text
    assert "color" not in text
    assert "<" not in text


def test_base64url_junk_degrades_to_empty_never_a_crash():
    assert g.decode_base64url("!!not base64!!") == ""
    assert g.decode_base64url("") == ""
    # Unpadded base64url (Gmail's shape) round-trips.
    assert g.decode_base64url("aGVsbG8") == "hello"


def test_a_numeric_cursor_becomes_a_day_earlier_cutoff():
    ns = g.to_ns("2026-03-04T12:00:00Z")
    cutoff = g.cursor_cutoff(str(ns))
    assert cutoff is not None
    assert cutoff.strftime("%Y-%m-%dT%H:%M:%SZ") == "2026-03-03T12:00:00Z"


def test_a_backfill_external_id_checkpoint_is_not_a_cursor():
    assert g.cursor_cutoff("18c0f00aa1") is None
    assert g.cursor_cutoff(None) is None
    assert g.cursor_cutoff("") is None
