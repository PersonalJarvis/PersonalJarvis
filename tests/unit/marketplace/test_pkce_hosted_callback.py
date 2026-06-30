"""H3 (open-source AP-22 / headless VPS): the PKCE-loopback OAuth flow
(Gmail/Google Drive/Calendar, Slack, Asana) hardcoded a 127.0.0.1 callback server,
so a remote browser on a VPS got an unreachable loopback redirect → CallbackTimeout.
It must route through ``make_callback_server`` so a configured
``public_callback_base_url`` yields a publicly-reachable hosted rendezvous — while
the desktop loopback still passes the plugin's registered fixed port + callback path.
"""
from __future__ import annotations

import jarvis.marketplace.hosted_callback as hc


def test_make_callback_server_forwards_callback_path_to_loopback(monkeypatch):
    monkeypatch.setattr(hc, "get_public_callback_base_url", lambda: "")
    captured: dict = {}

    class _FakeLoopback:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(hc, "OAuthCallbackServer", _FakeLoopback)
    hc.make_callback_server("state-1", timeout_seconds=300, fixed_port=3118, callback_path="/slack/oauth")
    assert captured.get("callback_path") == "/slack/oauth"
    assert captured.get("port") == 3118


def test_make_callback_server_hosted_when_public_base_url_set(monkeypatch):
    monkeypatch.setattr(hc, "get_public_callback_base_url", lambda: "https://jarvis.example.com")
    srv = hc.make_callback_server("state-1", timeout_seconds=300, fixed_port=3118, callback_path="/slack/oauth")
    assert type(srv).__name__ == "HostedCallbackServer"
