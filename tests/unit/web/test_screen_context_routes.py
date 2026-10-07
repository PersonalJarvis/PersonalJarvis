"""Screen Context settings routes validate and persist one atomic patch."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web import screen_context_routes


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(screen_context_routes.router)
    return app


def test_settings_patch_uses_one_atomic_writer_call(monkeypatch) -> None:
    calls: list[dict] = []
    resets: list[bool] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_screen_context_settings",
        lambda values: calls.append(dict(values)),
    )
    monkeypatch.setattr(
        screen_context_routes,
        "_reset_service",
        lambda: resets.append(True),
    )

    response = TestClient(_app()).put(
        "/api/screen-context/settings",
        json={
            "enabled": True,
            "denylist": ["Password Manager"],
            "sensitive_patterns": [r"customer:CUST-[0-9]+"],
        },
    )

    assert response.status_code == 200
    assert calls == [
        {
            "enabled": True,
            "denylist": ["Password Manager"],
            "sensitive_patterns": [r"customer:CUST-[0-9]+"],
        }
    ]
    assert resets == [True]


def test_invalid_sensitive_pattern_is_rejected_before_write(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_screen_context_settings",
        lambda values: calls.append(dict(values)),
    )

    response = TestClient(_app()).put(
        "/api/screen-context/settings",
        json={"sensitive_patterns": ["broken:(unterminated"]},
    )

    assert response.status_code == 400
    assert calls == []


def test_pathological_sensitive_pattern_is_rejected_before_write(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_screen_context_settings",
        lambda values: calls.append(dict(values)),
    )

    response = TestClient(_app()).put(
        "/api/screen-context/settings",
        json={"sensitive_patterns": [r"unsafe:(a+)+$"]},
    )

    assert response.status_code == 400
    assert calls == []


def test_receipt_metadata_never_exposes_app_or_window_title() -> None:
    from jarvis.screen_context.models import (
        CaptureTarget,
        ScreenContext,
        TargetKind,
        TargetReason,
        WindowFacts,
    )

    context = ScreenContext(
        image=b"jpeg",
        mime="image/jpeg",
        size=(10, 10),
        target=CaptureTarget(
            kind=TargetKind.WINDOW,
            bbox=(0, 0, 10, 10),
            reason=TargetReason.FOCUSED_WINDOW,
            window=WindowFacts(app_name="secret.exe", title="private document"),
        ),
    )

    metadata = screen_context_routes._context_metadata(context, "opaque")

    assert "secret.exe" not in str(metadata)
    assert "private document" not in str(metadata)


def test_status_requires_the_complete_visual_context_path(monkeypatch) -> None:
    service = SimpleNamespace(
        settings=SimpleNamespace(enabled=True, ocr_enabled=False, ttl_s=120.0),
        displays=SimpleNamespace(
            monitors=lambda: [
                {"name": "virtual"},
                {"name": "primary"},
            ]
        ),
        cursor=SimpleNamespace(position=lambda: (10, 10)),
        held_count=0,
    )
    monkeypatch.setattr(screen_context_routes, "_get_service", lambda _request: service)
    monkeypatch.setattr(
        screen_context_routes,
        "_capture_backend_capability",
        lambda: (True, ""),
    )
    monkeypatch.setattr(
        screen_context_routes,
        "_indicator_capability",
        lambda: (True, ""),
    )
    monkeypatch.setattr(
        screen_context_routes,
        "_vision_capability",
        lambda: (False, "No vision-capable provider is configured."),
    )
    monkeypatch.setattr(
        screen_context_routes,
        "_ocr_capability",
        lambda _enabled: (False, "Optional OCR is switched off."),
    )
    probe_kwargs: list[dict] = []

    def capture_probe(**kwargs):
        probe_kwargs.append(kwargs)

    monkeypatch.setattr("jarvis.screen_context.ports.capture_permission_error", capture_probe)
    monkeypatch.setattr(
        "jarvis.screen_context.ports.accessibility_permission_error",
        lambda: None,
    )

    response = TestClient(_app()).get("/api/screen-context/status")

    assert response.status_code == 200
    assert probe_kwargs == [{"deep": False}]  # a GET never runs the window-title oracle
    payload = response.json()
    assert payload["available"] is False
    assert payload["components"]["capture"]["ready"] is True
    assert payload["components"]["vision"]["ready"] is False
    assert "vision" in payload["blocked_reason"].lower()


def test_classify_resolves_language_when_api_caller_omits_locale(monkeypatch) -> None:
    from jarvis.screen_context.models import IntentVerdict, VisualIntent

    seen: list[str] = []

    class FakeService:
        def classify(self, _text: str, *, locale: str):
            seen.append(locale)
            return IntentVerdict(intent=VisualIntent.AMBIGUOUS, locale=locale)

    monkeypatch.setattr(
        screen_context_routes,
        "_get_service",
        lambda _request: FakeService(),
    )
    monkeypatch.setattr(
        "jarvis.core.config.load_config",
        lambda: SimpleNamespace(
            brain=SimpleNamespace(reply_language="auto"),
            stt=SimpleNamespace(language="auto"),
        ),
    )

    response = TestClient(_app()).post(
        "/api/screen-context/classify",
        json={"text": "Was ist das?"},  # i18n-allow: German input fixture
    )

    assert response.status_code == 200
    assert seen == ["de"]


def test_single_capture_discard_never_returns_pixels(monkeypatch) -> None:
    class FakeService:
        def discard(self, capture_id: str) -> bool:
            return capture_id == "held-once"

    monkeypatch.setattr(
        screen_context_routes,
        "_get_service",
        lambda _request: FakeService(),
    )

    response = TestClient(_app()).delete("/api/screen-context/held-once")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "discarded": 1}
    assert "image" not in response.text


def _status_service() -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(enabled=True, ocr_enabled=False, ttl_s=120.0),
        displays=SimpleNamespace(monitors=lambda: [{"name": "virtual"}, {"name": "primary"}]),
        cursor=SimpleNamespace(position=lambda: (10, 10)),
        held_count=0,
    )


def _status_capabilities(monkeypatch) -> None:
    monkeypatch.setattr(screen_context_routes, "_get_service", lambda _r: _status_service())
    monkeypatch.setattr(screen_context_routes, "_capture_backend_capability", lambda: (True, ""))
    monkeypatch.setattr(screen_context_routes, "_indicator_capability", lambda: (True, ""))
    monkeypatch.setattr(screen_context_routes, "_vision_capability", lambda: (True, ""))
    monkeypatch.setattr(screen_context_routes, "_ocr_capability", lambda _e: (False, ""))
    monkeypatch.setattr("jarvis.screen_context.ports._is_wayland", lambda: False)


def test_status_never_prompts_and_describes_the_missing_permission(monkeypatch) -> None:
    """GET /status reads the permission state; asking is a gesture's job.

    The REAL permission service runs on ``FakeTCC`` (a model of macOS privacy, nothing
    here ran on a real Mac): the call log proves a status poll made macOS ask nothing.
    """
    from tests.fakes.fake_tcc import FakeTCC, install_port

    tcc = FakeTCC()
    install_port(monkeypatch, tcc.port("darwin"))
    _status_capabilities(monkeypatch)
    client = TestClient(_app())

    for _ in range(3):  # polling must not turn into asking
        payload = client.get("/api/screen-context/status").json()

    assert payload["available"] is False
    permission = payload["components"]["permission"]
    assert permission["ready"] is False
    # The sentence of the issue, not the repr of its dataclass.
    assert permission["detail"].startswith("Screen Recording access is off for Personal Jarvis")
    assert "CapturePermissionIssue" not in str(payload)
    assert payload["blocked_reason"] == permission["detail"]
    tcc.assert_no_prompts()


def test_status_is_ready_and_silent_off_macos(monkeypatch) -> None:
    from tests.fakes.fake_tcc import install_port, make_non_darwin_port

    port, tcc = make_non_darwin_port("linux")
    install_port(monkeypatch, port)
    _status_capabilities(monkeypatch)

    payload = TestClient(_app()).get("/api/screen-context/status").json()

    assert payload["components"]["permission"] == {"ready": True, "detail": ""}
    tcc.assert_silent()
