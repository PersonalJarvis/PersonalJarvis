"""Task 7 — the realtime session factory for the browser /ws/audio path.

``build_realtime_session`` returns ``None`` (=> caller runs the classic path)
when [voice].mode != "realtime" or no key is present in ANY realtime family;
otherwise it builds a provider-backed RealtimeVoiceSession. The provider
resolution (``_resolve_realtime_provider``) is key-aware and crosses families
(OpenAI Realtime <-> Gemini Live, AP-22) by preferring
``[brain.realtime].provider`` and falling back to whichever family actually
has a key. The provider gate goes through the module-level
``get_provider_secret`` name so a test can monkeypatch it without touching
real credential storage.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.realtime.factory import _resolve_realtime_provider, build_realtime_session


def _cfg(mode: str = "realtime", provider: str = "openai-realtime") -> SimpleNamespace:
    return SimpleNamespace(
        voice=SimpleNamespace(mode=mode),
        brain=SimpleNamespace(
            reply_language="en", realtime=SimpleNamespace(provider=provider)
        ),
    )


def test_returns_none_when_mode_is_pipeline(monkeypatch):
    import jarvis.realtime.factory as f

    monkeypatch.setattr(f, "get_provider_secret", lambda _p: "sk-x")
    cfg = SimpleNamespace(
        voice=SimpleNamespace(mode="pipeline"), brain=SimpleNamespace(reply_language="en")
    )
    assert (
        build_realtime_session(cfg=cfg, bus=None, session_id="s", send_binary=None, send_json=None)
        is None
    )


def test_returns_none_when_no_openai_key(monkeypatch):
    import jarvis.realtime.factory as f

    monkeypatch.setattr(f, "get_provider_secret", lambda _p: None)
    cfg = SimpleNamespace(
        voice=SimpleNamespace(mode="realtime"), brain=SimpleNamespace(reply_language="en")
    )
    assert (
        build_realtime_session(cfg=cfg, bus=None, session_id="s", send_binary=None, send_json=None)
        is None
    )


def test_builds_session_when_realtime_and_keyed(monkeypatch):
    import jarvis.realtime.factory as f

    monkeypatch.setattr(f, "get_provider_secret", lambda _p: "sk-x")
    cfg = SimpleNamespace(
        voice=SimpleNamespace(mode="realtime"), brain=SimpleNamespace(reply_language="en")
    )
    sess = build_realtime_session(
        cfg=cfg, bus=None, session_id="s", send_binary=lambda b: None, send_json=lambda m: None
    )
    assert sess is not None
    assert sess.session_id == "s"


@pytest.mark.parametrize(
    "configured,keys,expect",
    [
        ("openai-realtime", {"openai"}, "openai-realtime"),
        ("gemini-live", {"gemini"}, "gemini-live"),
        ("openai-realtime", {"gemini"}, "gemini-live"),  # cross-family (AP-22)
        ("gemini-live", {"openai"}, "openai-realtime"),  # cross-family (AP-22)
        ("openai-realtime", {"openai", "gemini"}, "openai-realtime"),  # configured wins
        ("openai-realtime", set(), None),  # neither key -> None (classic pipeline)
    ],
)
def test_resolve_realtime_provider_key_aware_selection(monkeypatch, configured, keys, expect):
    import jarvis.realtime.factory as f

    monkeypatch.setattr(f, "get_provider_secret", lambda name: "k" if name in keys else "")
    resolved = _resolve_realtime_provider(_cfg(provider=configured))
    if expect is None:
        assert resolved is None
    else:
        assert resolved is not None
        assert resolved.name == expect


def test_build_realtime_session_returns_none_when_no_realtime_key_in_any_family(monkeypatch):
    import jarvis.realtime.factory as f

    monkeypatch.setattr(f, "get_provider_secret", lambda _name: "")
    cfg = _cfg()
    assert (
        build_realtime_session(cfg=cfg, bus=None, session_id="s", send_binary=None, send_json=None)
        is None
    )
