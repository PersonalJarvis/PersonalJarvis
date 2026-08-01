"""Desktop-only capability injection for subscription Realtime signalling."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from jarvis.ui.desktop_app import DesktopApp


def test_desktop_injects_distinct_realtime_broker_capability(monkeypatch) -> None:
    scripts: list[str] = []
    window = SimpleNamespace(evaluate_js=scripts.append)
    app = DesktopApp.__new__(DesktopApp)
    app.session_token = "ordinary-ui-session"  # noqa: S105 - synthetic test token
    app.realtime_transport_broker_token = (
        "desktop-broker-capability"  # noqa: S105 - synthetic test token
    )
    monkeypatch.setattr(app, "_start_desktop_integration_repair", lambda: None)
    monkeypatch.setitem(
        sys.modules,
        "jarvis.ui.icon_utils",
        SimpleNamespace(
            project_icon_path=lambda: "unused",
            set_window_icon_by_title=lambda *_args: None,
        ),
    )

    app._inject_token(window)

    assert len(scripts) == 1
    assert "ordinary-ui-session" in scripts[0]
    assert "desktop-broker-capability" in scripts[0]
    assert "__JARVIS_EMBEDDED_DESKTOP" in scripts[0]
    assert "__JARVIS_REALTIME_BROKER_TOKEN" in scripts[0]
