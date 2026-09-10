"""External providers are verified by their own public protocol, not by a generic token."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from jarvis.tasks import external_auth
from jarvis.tasks.schema import TriggerWebhook


@pytest.mark.parametrize("provider", ["github", "linear", "slack", "stripe"])
def test_provider_signatures_and_replay_windows(monkeypatch, provider):
    key = "local-fixture-value"
    now = 1_800_000_000
    monkeypatch.setattr(external_auth, "get_secret", lambda _: key)
    row = {"id": "fixture", "created_at_ns": 1}
    payload = {"event_id": "sample", "webhookTimestamp": now * 1000}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signed = raw
    if provider == "slack":
        signed = f"v0:{now}:".encode() + raw
    if provider == "stripe":
        signed = f"{now}.".encode() + raw
    signature = hmac.new(key.encode(), signed, hashlib.sha256).hexdigest()
    names = {
        "github": "X-Hub-Signature-256",
        "linear": "Linear-Signature",
        "slack": "X-Slack-Signature",
        "stripe": "Stripe-Signature",
    }
    values = {
        "github": "sha256=" + signature,
        "linear": signature,
        "slack": "v0=" + signature,
        "stripe": f"t={now},v1={signature}",
    }
    headers = httpx.Headers(
        {names[provider]: values[provider], "X-Slack-Request-Timestamp": str(now)}
    )
    trigger = TriggerWebhook(provider=provider)
    assert external_auth.verify_provider(row, trigger, raw, headers, now=now)
    assert not external_auth.verify_provider(row, trigger, raw + b" ", headers, now=now)
    if provider != "github":
        assert not external_auth.verify_provider(row, trigger, raw, headers, now=now + 301)
    assert not external_auth.verify_provider(
        row, trigger, raw, httpx.Headers({"Authorization": "Bearer " + key}), now=now
    )


def test_google_oidc_verifies_signature_audience_email_and_expiry(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.auth import crypt, jwt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    signer = crypt.RSASigner.from_string(private, key_id="fixture")
    monkeypatch.setattr(
        external_auth,
        "_google_request",
        lambda *args, **kwargs: SimpleNamespace(
            status=200, data=json.dumps({"fixture": public.decode()}).encode(), headers={}
        ),
    )
    now = int(time.time())
    audience = "https://receiver.example/hooks/fixture"
    account = "triggers@fixture.iam.gserviceaccount.com"
    claims = {
        "iss": "https://accounts.google.com",
        "aud": audience,
        "iat": now,
        "exp": now + 300,
        "email": account,
        "email_verified": True,
        "sub": "123",
    }
    token = jwt.encode(signer, claims).decode()
    assert external_auth.verify_google_token(token, audience, account)
    assert not external_auth.verify_google_token(token, "wrong", account)
    assert not external_auth.verify_google_token(
        token, audience, "other@fixture.iam.gserviceaccount.com"
    )
    expired = jwt.encode(signer, {**claims, "iat": now - 600, "exp": now - 300}).decode()
    assert not external_auth.verify_google_token(expired, audience, account)
    assert not external_auth.verify_google_token(token, "", account)


def test_gmail_pubsub_notification_is_normalized_after_verification():
    data = base64.b64encode(
        json.dumps({"emailAddress": "reader@example.test", "historyId": "42"}).encode()
    ).decode()
    payload = external_auth.normalize_provider(
        "gmail", {"message": {"messageId": "event-7", "data": data}, "subscription": "fixture"}
    )
    assert payload["message_id"] == "event-7"
    assert payload["data"]["historyId"] == "42"
    with pytest.raises(ValueError):
        external_auth.normalize_provider("gmail", {"message": {"data": "invalid"}})


def test_gmail_settings_require_a_service_identity():
    with pytest.raises(ValueError, match="service account"):
        TriggerWebhook(provider="gmail", service_account="reader@example.test")
