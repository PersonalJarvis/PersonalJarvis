"""Five-layer parity guard for the mission error-class vocabulary (AP-4).

The 2026-07-06 incident produced a mission failure whose cause (dead provider
auth) was invisible in the app: ``MissionFailed.error_class`` existed but was
never populated, and no UI/voice layer consumed it. This test locks the new
closed vocabulary across Python <-> TypeScript so the BUG-008 enum-drift class
cannot recur. (Voice-table and locale-file parity are asserted in
``test_error_class_full_parity.py`` once those layers land.)
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.missions.events import MISSION_ERROR_CLASSES

_REPO = Path(__file__).resolve().parents[2]


def _ts_error_classes() -> set[str]:
    ts = _REPO / "jarvis" / "ui" / "web" / "frontend" / "src" / "types" / "missions.ts"
    text = ts.read_text(encoding="utf-8")
    m = re.search(r"export type MissionErrorClass\s*=([^;]+);", text)
    assert m, "MissionErrorClass union not found in missions.ts"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_error_class_tokens_python_ts_parity() -> None:
    py = set(MISSION_ERROR_CLASSES)
    ts = _ts_error_classes()
    assert py == ts, f"error_class drift — python-only={py - ts}, ts-only={ts - py}"


def test_expected_tokens_present() -> None:
    assert MISSION_ERROR_CLASSES == frozenset(
        {"provider_auth", "provider_quota", "provider_unreachable", "worker_timeout"}
    )


def test_new_event_fields_default_none() -> None:
    """Old stored events (no new fields) must keep validating."""
    from jarvis.missions.events import MissionFailed, WorkerKilled

    mf = MissionFailed(reason="task_error", last_state="CRITIQUING")
    assert mf.error_class is None and mf.error_detail is None
    assert mf.failed_provider is None
    wk = WorkerKilled(worker_id="w1", reason="worker_error")
    assert wk.error_class is None and wk.error_detail is None
