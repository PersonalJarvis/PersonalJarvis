"""Gmail REST tool: reads the marketplace keyring token and calls the Gmail
REST API directly (Node-free, stays under the marketplace token model)."""

import base64

import httpx
import pytest

from jarvis.plugins.tool.gmail_rest import GmailRestTool


@pytest.mark.asyncio
async def test_list_messages_uses_bearer_from_provider():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer at_123"
        assert "/messages" in str(req.url)
        return httpx.Response(200, json={"messages": [{"id": "m1"}]})

    tool = GmailRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
    )
    out = await tool.list_messages(max_results=1)
    assert out["messages"][0]["id"] == "m1"


@pytest.mark.asyncio
async def test_send_message_posts_base64_raw():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        captured["url"] = str(req.url)
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"id": "sent1"})

    tool = GmailRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
    )
    out = await tool.send_message(to="a@b.com", subject="Hi", body="Hello")
    assert out["id"] == "sent1"
    assert "/messages/send" in captured["url"]
    decoded = base64.urlsafe_b64decode(captured["body"]["raw"]).decode()
    assert "To: a@b.com" in decoded and "Hello" in decoded


@pytest.mark.asyncio
async def test_execute_returns_error_when_not_connected():
    tool = GmailRestTool(access_token_provider=lambda: None)
    result = await tool.execute({"action": "list_messages"}, ctx=None)
    assert result.success is False
    assert "connect" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_list_messages_refreshes_on_401_then_retries():
    # An expired access token returns 401. The tool must refresh once and retry
    # the call — self-healing instead of surfacing "Freigabe abgelaufen"
    # (live 2026-06-07 Gmail bug).
    calls = {"http": 0, "refresh": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["http"] += 1
        if calls["http"] == 1:
            return httpx.Response(401, json={"error": {"code": 401}})
        return httpx.Response(200, json={"messages": [{"id": "m9"}]})

    async def refresher() -> bool:
        calls["refresh"] += 1
        return True

    tool = GmailRestTool(
        access_token_provider=lambda: "at_dead",
        transport=httpx.MockTransport(handler),
        token_refresher=refresher,
    )
    out = await tool.list_messages(max_results=1)
    assert out["messages"][0]["id"] == "m9"
    assert calls["refresh"] == 1
    assert calls["http"] == 2  # retried exactly once after a successful refresh


@pytest.mark.asyncio
async def test_returns_reconnect_error_when_refresh_fails():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 401}})

    async def refresher() -> bool:
        return False  # un-healable (e.g. invalid_client / revoked)

    tool = GmailRestTool(
        access_token_provider=lambda: "at_dead",
        transport=httpx.MockTransport(handler),
        token_refresher=refresher,
    )
    out = await tool.list_messages()
    assert "error" in out
    assert "reconnect" in out["error"].lower()


@pytest.mark.asyncio
async def test_send_message_refreshes_on_401_then_retries():
    calls = {"http": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["http"] += 1
        if calls["http"] == 1:
            return httpx.Response(401, json={"error": {"code": 401}})
        return httpx.Response(200, json={"id": "sent9"})

    async def refresher() -> bool:
        return True

    tool = GmailRestTool(
        access_token_provider=lambda: "at_dead",
        transport=httpx.MockTransport(handler),
        token_refresher=refresher,
    )
    out = await tool.send_message(to="a@b.com", subject="x", body="y")
    assert out["id"] == "sent9"
    assert calls["http"] == 2


@pytest.mark.asyncio
async def test_non_401_error_is_not_retried():
    # A 500 is not an auth problem — do not refresh, do not retry; let it surface.
    calls = {"http": 0, "refresh": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["http"] += 1
        return httpx.Response(500, json={"error": "boom"})

    async def refresher() -> bool:
        calls["refresh"] += 1
        return True

    tool = GmailRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
        token_refresher=refresher,
    )
    result = await tool.execute({"action": "list_messages"}, ctx=None)
    assert result.success is False
    assert calls["refresh"] == 0
    assert calls["http"] == 1


def test_tool_contract_shape():
    tool = GmailRestTool()
    assert tool.name == "gmail"
    assert tool.risk_tier == "ask"  # conservative static fallback; send is consequential
    assert "schema" in dir(tool)


# ----------------------------------------------------------------------
# Per-action risk (forensic 2026-06-19, session dc533e39): "Was habe ich
# heute auf dem Plan?" → morning-routine → gmail action=list_messages  # i18n-allow: simulated German user utterance, forensic quote
# (read) was forced through the ask-tier confirm and Jarvis spoke "Soll  # i18n-allow: verbatim quote of real German runtime voice output
# ich die E-Mail wirklich senden?". Only send_message is consequential;  # i18n-allow: verbatim quote of real German runtime voice output
# reads must stay safe so the morning briefing can check unread mail
# without a spurious send confirmation.
# ----------------------------------------------------------------------


def test_risk_tier_for_args_read_actions_are_safe():
    tool = GmailRestTool(access_token_provider=lambda: "x")
    assert tool.risk_tier_for_args({"action": "list_messages"}) == "safe"
    assert tool.risk_tier_for_args({"action": "get_message"}) == "safe"


def test_risk_tier_for_args_send_is_ask():
    tool = GmailRestTool(access_token_provider=lambda: "x")
    assert tool.risk_tier_for_args({"action": "send_message"}) == "ask"


def test_risk_tier_for_args_default_action_is_safe():
    # No action key → defaults to list_messages (a read) → safe.
    tool = GmailRestTool(access_token_provider=lambda: "x")
    assert tool.risk_tier_for_args({}) == "safe"


def test_risk_tier_for_args_unknown_action_is_conservative():
    # An unrecognised action stays conservative (ask), never silently safe.
    tool = GmailRestTool(access_token_provider=lambda: "x")
    assert tool.risk_tier_for_args({"action": "purge_everything"}) == "ask"


# ----------------------------------------------------------------------
# Read output must be SLIM, not raw MIME (live bug 2026-07-01 "Was steht
# alles in meinen E-Mails drin?"): get_message with format=full returns
# ~23k chars of raw headers (ARC-Seal, DKIM), full MIME tree and base64
# body per message. Feeding that into the model context slowed the turn to
# ~20 s and added no answer value. Project to sender/subject/date/snippet +
# a decoded, length-capped plain-text body.
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_message_returns_slim_projection_not_raw_mime():
    body_text = "Hallo Ruben, hier ist der Kern der Nachricht. " * 5  # i18n-allow
    raw_body_b64 = base64.urlsafe_b64encode(body_text.encode("utf-8")).decode("ascii")
    fat = {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["UNREAD", "INBOX"],
        "snippet": "Hallo Ruben, hier ist der Kern",  # i18n-allow
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "Chef <chef@firma.de>"},
                {"name": "To", "value": "ruben@example.com"},
                {"name": "Subject", "value": "Quartalszahlen"},
                {"name": "Date", "value": "Wed, 01 Jul 2026 12:58:21 +0000"},
                {"name": "ARC-Seal", "value": "i=1; a=rsa-sha256; " + "A" * 4000},
                {"name": "DKIM-Signature", "value": "v=1; a=rsa-sha256; " + "B" * 4000},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": raw_body_b64}},
                {"mimeType": "text/html", "body": {"data": raw_body_b64}},
            ],
        },
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fat)

    tool = GmailRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
    )
    result = await tool.execute({"action": "get_message", "message_id": "m1"}, ctx=None)
    assert result.success is True
    out = result.output
    assert out["from"] == "Chef <chef@firma.de>"
    assert out["subject"] == "Quartalszahlen"
    assert out["date"].startswith("Wed, 01 Jul 2026")
    assert out["snippet"] == "Hallo Ruben, hier ist der Kern"  # i18n-allow
    assert "Kern der Nachricht" in out["body"]  # i18n-allow

    import json as _json

    serialized = _json.dumps(out)
    # The raw signature noise must be gone entirely.
    assert "ARC-Seal" not in serialized
    assert "DKIM-Signature" not in serialized
    assert "AAAA" not in serialized and "BBBB" not in serialized
    # And the projection is a fraction of the raw payload.
    assert len(serialized) < len(_json.dumps(fat)) // 2


@pytest.mark.asyncio
async def test_get_message_caps_a_long_body():
    long_body = "wort " * 2000  # ~10k chars decoded
    raw_body_b64 = base64.urlsafe_b64encode(long_body.encode("utf-8")).decode("ascii")
    fat = {
        "id": "m2",
        "threadId": "t2",
        "labelIds": ["INBOX"],
        "snippet": "x",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "Subject", "value": "Long"}],
            "body": {"data": raw_body_b64},
        },
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fat)

    tool = GmailRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
    )
    result = await tool.execute({"action": "get_message", "message_id": "m2"}, ctx=None)
    assert result.success is True
    body = result.output["body"]
    assert len(body) <= 2100  # cap (2000) + short truncation marker
    assert "truncated" in body
