"""Task 7 — the realtime session factory for the browser /ws/audio path.

``build_realtime_session`` returns ``None`` (=> caller runs the classic path)
when [voice].mode != "realtime" or no OpenAI key is present; otherwise it
builds an OpenAIRealtimeProvider-backed RealtimeVoiceSession. The provider
gate goes through the module-level ``get_provider_secret`` name so a test can
monkeypatch it without touching real credential storage.
"""

from __future__ import annotations

from types import SimpleNamespace

from jarvis.realtime.factory import build_realtime_session


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
