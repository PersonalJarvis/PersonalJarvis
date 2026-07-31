"""Screen Context settings routes validate and persist one atomic patch."""

from __future__ import annotations

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
