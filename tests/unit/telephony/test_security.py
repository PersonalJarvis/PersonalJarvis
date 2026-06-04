"""Signature validation + per-call secret + URL helper tests."""

from __future__ import annotations

from twilio.request_validator import RequestValidator

from jarvis.telephony.security import (
    constant_time_equals,
    generate_call_secret,
    public_url_for,
    public_wss_url,
    validate_twilio_signature,
)


def test_generate_call_secret_is_unique_and_urlsafe():
    a = generate_call_secret()
    b = generate_call_secret()
    assert a != b
    assert all(c.isalnum() or c in "-_" for c in a)
    assert len(a) >= 24


def test_constant_time_equals():
    assert constant_time_equals("abc", "abc")
    assert not constant_time_equals("abc", "abd")
    assert not constant_time_equals("abc", "")
    assert constant_time_equals("", "")


def test_validate_signature_accepts_real_signature():
    token = "abcdef1234567890"
    url = "https://jarvis.example.com/api/telephony/voice"
    params = {"CallSid": "CA1", "From": "+4930", "To": "+4940"}
    sig = RequestValidator(token).compute_signature(url, params)
    assert validate_twilio_signature(auth_token=token, signature=sig, url=url, params=params)


def test_validate_signature_rejects_tampered_params():
    token = "abcdef1234567890"
    url = "https://jarvis.example.com/api/telephony/voice"
    params = {"CallSid": "CA1", "From": "+4930"}
    sig = RequestValidator(token).compute_signature(url, params)
    tampered = {"CallSid": "CA1", "From": "+666"}
    assert not validate_twilio_signature(auth_token=token, signature=sig, url=url, params=tampered)


def test_validate_signature_rejects_missing_inputs():
    assert not validate_twilio_signature(auth_token=None, signature="x", url="https://x", params={})
    assert not validate_twilio_signature(auth_token="t", signature=None, url="https://x", params={})
    assert not validate_twilio_signature(auth_token="t", signature="x", url="", params={})


def test_public_url_for_normalises_slashes():
    assert (
        public_url_for("https://x.com/", "api/telephony/voice")
        == "https://x.com/api/telephony/voice"
    )
    assert (
        public_url_for("https://x.com", "/api/telephony/voice")
        == "https://x.com/api/telephony/voice"
    )


def test_public_wss_url_swaps_scheme():
    assert (
        public_wss_url("https://x.com", "/api/telephony/media") == "wss://x.com/api/telephony/media"
    )
    assert (
        public_wss_url("http://localhost:8765", "/api/telephony/media")
        == "ws://localhost:8765/api/telephony/media"
    )
