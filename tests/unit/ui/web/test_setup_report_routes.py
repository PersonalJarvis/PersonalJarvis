"""REST-route tests for the cross-device setup report.

The report exists to name why one install behaves differently from another
(CLAUDE.md §3 device-parity triage), so the tests pin its three contracts:

1. Share-safety: credentials appear strictly as presence BOOLEANS — a report
   must be pasteable into an issue without scrubbing.
2. Degradation naming: every non-ok section surfaces in ``degradations`` and
   in the diffable ``summary`` lines; silently-fine sections do not.
3. Honesty under a cold cache: a section probe that does not complete in time
   yields ``sections_complete: false`` and still answers 200 — never a hang,
   never a guess.

The probe helpers are monkeypatched: the real ones read the OS keyring and the
live config, which a unit test must not touch (fakes over mocks per repo
convention — these are plain stub functions).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web import setup_report_routes as sr


def _stub_probes(
    monkeypatch: Any,
    *,
    sections: dict[str, dict[str, str]] | None,
) -> None:
    monkeypatch.setattr(
        sr,
        "_behavior_snapshot",
        lambda request: {
            "wake_word_enabled": True,
            "wake_phrase_set": True,
            "wake_engine": "vosk_kws",
            "reply_language": "auto",
        },
    )
    monkeypatch.setattr(
        sr,
        "_active_tiers",
        lambda request: {
            "brain": "gemini",
            "stt": "faster-whisper",
            "tts": "elevenlabs",
            "realtime": None,
            "computer_use": "gemini",
        },
    )
    monkeypatch.setattr(
        sr,
        "_credential_presence",
        lambda request: {"gemini": True, "openai": False, "elevenlabs": True},
    )
    monkeypatch.setattr(
        sr,
        "_install_snapshot",
        lambda: {"managed": True, "profile": "full"},
    )

    async def _sections(request: Any) -> dict[str, dict[str, str]] | None:
        return sections

    monkeypatch.setattr(sr, "_section_snapshot", _sections)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(sr.router)
    return TestClient(app)


_SECTIONS = {
    "brain": {"status": "ok", "reason": "ok", "detail": "Gemini: ok"},
    "tts": {
        "status": "needs_setup",
        "reason": "not_configured",
        "detail": "Gemini Flash TTS: no key set",
    },
}


def test_report_names_degradations_and_keeps_credentials_boolean(monkeypatch):
    _stub_probes(monkeypatch, sections=_SECTIONS)
    body = _client().get("/api/setup-report").json()

    assert body["version"]
    assert body["sections_complete"] is True
    # Contract 1 — share-safe: presence booleans only, never a value.
    assert body["credentials"] and all(isinstance(v, bool) for v in body["credentials"].values())
    # Contract 2 — the quiet degradation gets named; the healthy tier stays out.
    assert body["degradations"] == ["tts: not_configured (Gemini Flash TTS: no key set)"]
    assert any(line.startswith("degraded tts:") for line in body["summary"])
    assert not any("brain: ok" in line and "degraded" in line for line in body["summary"])
    # Summary lines are the device-diff surface: active tiers + key inventory.
    assert "tier brain: gemini" in body["summary"]
    assert "keys present: elevenlabs, gemini" in body["summary"]


def test_text_format_renders_the_summary_lines(monkeypatch):
    _stub_probes(monkeypatch, sections=_SECTIONS)
    resp = _client().get("/api/setup-report", params={"format": "text"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "keys present: elevenlabs, gemini" in resp.text
    assert "share-safe" in resp.text


def test_cold_section_probe_degrades_honestly(monkeypatch):
    # Contract 3 — a timed-out health probe must not hang, 500, or fabricate.
    _stub_probes(monkeypatch, sections=None)
    body = _client().get("/api/setup-report").json()

    assert body["sections_complete"] is False
    assert body["sections"] == {}
    assert body["degradations"] == []
    assert any("warming up" in line for line in body["summary"])
