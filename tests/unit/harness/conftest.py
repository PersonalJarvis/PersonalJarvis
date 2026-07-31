"""Shared isolation for Computer-Use loop tests.

The mission-start hooks ``_ensure_target_on_primary`` (move the foreground window
to primary) and ``_focus_task_target_window`` (raise the goal's named app from
another monitor) query + MOVE/RAISE the real desktop's windows. Any test that
drives the loop (``_run_screenshot_loop`` / ``run_cu_loop``) would otherwise fire
them against the host — a real, host-dependent side effect. Stub both to no-ops
for every harness test; their own behavior is unit-tested directly in
``test_cu_ensure_on_primary.py`` / ``test_cu_focus_task_target.py`` (which capture
the real functions at import time to bypass this stub).
"""
from __future__ import annotations

import pytest

from jarvis.harness import screenshot_only_loop as _loop


@pytest.fixture(autouse=True)
def _no_real_window_move(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_loop, "_ensure_target_on_primary", lambda _ctx: None)
    monkeypatch.setattr(
        _loop, "_focus_task_target_window", lambda _ctx, _task: None,
    )
