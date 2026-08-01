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
    monkeypatch.setattr(
        "jarvis.screen_context.ports.capture_permission_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "jarvis.screen_context.ports.accessibility_permission_error",
        lambda: None,
    )

    response = TestClient(_app()).get("/api/screen-context/status")

    assert response.status_code == 200
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
