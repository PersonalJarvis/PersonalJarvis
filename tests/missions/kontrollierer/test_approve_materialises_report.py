"""Wiring test: _approve_mission materialises a report for a text-answer mission.

The "always a document" guarantee lives in
``deliverable.materialize_answer_document`` (unit-tested in
``test_answer_document.py``). This test proves the Kontrollierer actually CALLS
it on the approve path — an informational mission that left a text answer but no
file deliverable must end with a Markdown report in its archive subtree, so the
Outputs view is never empty. A code mission that already wrote a file must NOT
get a duplicate report.

``deliver_to_user_folder`` is stubbed so the test never writes into the real
``~/Downloads/Jarvis-Outputs``; everything else runs through the real method.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import jarvis.missions.kontrollierer.deliverable as deliv_mod
import jarvis.missions.kontrollierer.orchestrator as orch_mod
from jarvis.missions.events import MissionApproved
from jarvis.missions.kontrollierer.orchestrator import Kontrollierer

_LONG_ANSWER = (
    "To relocate to San Francisco you need a work visa, housing secured early, "
    "a US bank account, and an SSN as soon as you arrive. Budget for a high cost "
    "of living and start the visa process months ahead of the move."
)


class _FakePlan:
    def __init__(self, expected_output: str = "") -> None:
        self.expected_output = expected_output


class _FakeStore:
    def __init__(self) -> None:
        self.published: list[object] = []

    async def append_and_publish(self, env: object) -> None:
        self.published.append(env)


class _FakeManager:
    def __init__(self) -> None:
        self.store = _FakeStore()


class _FakeBudget:
    def mission_cost(self, mission_id: str) -> float:  # noqa: ARG002
        return 0.0


def _build_orch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, stub_deliver: bool = True
) -> Kontrollierer:
    """A bare Kontrollierer with only the state _approve_mission touches.

    ``stub_deliver`` replaces ``deliver_to_user_folder`` with a no-op so a unit
    test never writes into the real ``~/Downloads/Jarvis-Outputs``. The
    integration test sets it False to exercise the real mirror path (with
    ``resolve_deliverables_dir`` redirected to a tmp dir instead).
    """
    o = object.__new__(Kontrollierer)
    o._isolation_root = tmp_path / "iso"
    o._isolation_root.mkdir(parents=True, exist_ok=True)
    o._task_answers = {}
    o._mission_failure_context = {}
    o._state_locks = {}
    o._manager = _FakeManager()
    o._budget = _FakeBudget()

    async def _noop_transition(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(o, "_safe_transition", _noop_transition)
    if stub_deliver:
        monkeypatch.setattr(orch_mod, "deliver_to_user_folder", lambda *a, **k: [])
    return o


@pytest.fixture
def orch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kontrollierer:
    """A bare Kontrollierer with deliver_to_user_folder stubbed out."""
    return _build_orch(tmp_path, monkeypatch, stub_deliver=True)


def _mission_dir(o: Kontrollierer, mission_id: str) -> Path:
    d = o._isolation_root / f"mission_{mission_id[:13]}"
    (d / "tasks" / "019edf4c-task1").mkdir(parents=True, exist_ok=True)
    return d


async def test_approve_materialises_report_for_text_answer(orch: Kontrollierer) -> None:
    """Informational mission (text answer, no file) → a report document appears."""
    mission_id = "019edf4c-f827aaaa"
    mdir = _mission_dir(orch, mission_id)
    orch._task_answers[mission_id] = [_LONG_ANSWER]

    await orch._approve_mission(
        mission_id, _FakePlan("a relocation guide"), prompt="Relocate to San Francisco"
    )

    reports = list(mdir.rglob("artifacts/files/*.md"))
    assert reports, "approve must materialise a report for a text-answer mission"
    assert "work visa" in reports[0].read_text(encoding="utf-8")


async def test_approve_no_duplicate_report_when_file_deliverable_exists(
    orch: Kontrollierer,
) -> None:
    """A real file deliverable already present → no extra report is written."""
    mission_id = "019edf4c-f827bbbb"
    mdir = _mission_dir(orch, mission_id)
    files = mdir / "tasks" / "019edf4c-task1" / "artifacts" / "files"
    files.mkdir(parents=True, exist_ok=True)
    (files / "landing.html").write_text("<html/>", encoding="utf-8")
    orch._task_answers[mission_id] = [_LONG_ANSWER]

    await orch._approve_mission(
        mission_id, _FakePlan(), prompt="build a landing page"
    )

    assert not list(files.glob("*.md")), "must not add a report next to a real file"
    assert (files / "landing.html").is_file()


def _approved_event(store: _FakeStore) -> MissionApproved:
    """The MissionApproved payload the approve path published (last one)."""
    for env in reversed(store.published):
        payload = getattr(env, "payload", None)
        if isinstance(payload, MissionApproved):
            return payload
    raise AssertionError("no MissionApproved event was published")


async def test_approve_builds_english_summary_en_for_file_deliverable(
    orch: Kontrollierer,
) -> None:
    """summary_en must be genuinely English (de/en diverge) for a file mission.

    Forensic 2026-06-24: the announcer picks summary_en for an English-dispatched
    mission, but the orchestrator recycled the German-only deliverable summary
    into BOTH fields, so an English request read its completion confirmation back
    in German. With no worker answer (answer_summary empty) the summary comes
    straight from the deliverable builders — the deterministic German leak.
    """
    mission_id = "019edf4c-f827dddd"
    mdir = _mission_dir(orch, mission_id)
    files = mdir / "tasks" / "019edf4c-task1" / "artifacts" / "files"
    files.mkdir(parents=True, exist_ok=True)
    (files / "landing.html").write_text("<html/>", encoding="utf-8")
    # No _task_answers → answer_summary is empty → the deliverable builders run.

    await orch._approve_mission(mission_id, _FakePlan(), prompt="build a landing page")

    approved = _approved_event(orch._manager.store)  # type: ignore[attr-defined]
    assert "landing.html" in approved.summary_en
    assert "saved" in approved.summary_en
    assert "Fertig" not in approved.summary_en and "gespeichert" not in approved.summary_en, (  # i18n-allow (asserts no German leaked into the English summary)
        f"summary_en must be English, got {approved.summary_en!r}"
    )
    # The German field still speaks German — the two diverge as designed.
    assert "Fertig" in approved.summary_de and "Datei" in approved.summary_de  # i18n-allow (German value under summary_de field)


async def test_approve_mirrors_materialised_report_to_user_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: the materialised report is also delivered to the user folder.

    Runs the REAL deliver_to_user_folder (not stubbed), with the deliverables
    dir redirected to a tmp path, and asserts the report lands BOTH in the
    archive subtree AND in the user-visible folder — guarding the materialise →
    deliver ordering against a future regression.
    """
    o = _build_orch(tmp_path, monkeypatch, stub_deliver=False)
    delivery_dir = tmp_path / "user-outputs"
    delivery_dir.mkdir()
    monkeypatch.setattr(
        deliv_mod, "resolve_deliverables_dir", lambda override=None: delivery_dir
    )

    mission_id = "019edf4c-f827cccc"
    mdir = _mission_dir(o, mission_id)
    o._task_answers[mission_id] = [_LONG_ANSWER]

    await o._approve_mission(
        mission_id, _FakePlan(), prompt="Relocate to San Francisco"
    )

    reports = list(mdir.rglob("artifacts/files/*.md"))
    assert reports, "report must exist in the archive subtree"
    assert (delivery_dir / reports[0].name).is_file(), (
        "the materialised report must be mirrored into the user folder"
    )
