"""Tests for materialize_answer_document — the always-a-document guarantee.

Background (live forensic 2026-06-19): the Outputs view lists ONLY genuine
deliverable files under ``tasks/<id>/artifacts/files/``. A code/file task writes
one (HTML, .py, …) so it shows up; a pure research/Q&A task delivers its answer
as TEXT (spoken back) and only NON-DETERMINISTICALLY wrote a ``.md`` report — so
the same "relocate to SF" question produced a file once and an empty Outputs card
the next time. ``materialize_answer_document`` closes that gap: when a mission
left no real file deliverable, the worker's text answer is written as a Markdown
report into the canonical deliverable subtree, so EVERY successful mission shows
a document.

The archive layout (laid down by ``Kontrollierer._archive_task_artifacts``):

    <mission_dir>/tasks/<task_id[:13]>/artifacts/files/<rel-path>
"""
from __future__ import annotations

from pathlib import Path

from jarvis.missions.kontrollierer.deliverable import (
    build_deliverable_summary,
    materialize_answer_document,
)

_LONG_ANSWER = (
    "To relocate to San Francisco you need to secure a work visa (typically an "
    "H-1B or O-1), find housing well before arriving because the market moves "
    "fast, open a US bank account, and get an SSN as soon as you land."
)


def _task_dir(mission_dir: Path, task_id: str = "019edf4c-f827") -> Path:
    """Create + return the per-task dir the archive step would have made."""
    d = mission_dir / "tasks" / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_no_answers_writes_nothing(tmp_path: Path) -> None:
    """Empty answer list → no document, returns None (nothing to report)."""
    _task_dir(tmp_path)
    assert materialize_answer_document(tmp_path, answers=[], prompt="x") is None
    assert not list((tmp_path).rglob("*.md"))


def test_thin_answer_writes_nothing(tmp_path: Path) -> None:
    """A trivially short answer is not a report — returns None."""
    _task_dir(tmp_path)
    out = materialize_answer_document(tmp_path, answers=["ok"], prompt="x")
    assert out is None
    assert not list(tmp_path.rglob("*.md"))


def test_substantive_answer_is_materialised(tmp_path: Path) -> None:
    """A real text answer becomes a Markdown report carrying the answer text."""
    _task_dir(tmp_path)
    out = materialize_answer_document(
        tmp_path, answers=[_LONG_ANSWER], prompt="what do I need to move to SF?"
    )
    assert out is not None
    assert out.is_file()
    assert out.suffix == ".md"
    body = out.read_text(encoding="utf-8")
    assert "secure a work visa" in body


def test_report_lands_in_deliverable_subtree(tmp_path: Path) -> None:
    """The report must sit in tasks/<id>/artifacts/files/ so Outputs lists it."""
    _task_dir(tmp_path)
    out = materialize_answer_document(
        tmp_path, answers=[_LONG_ANSWER], prompt="relocate to SF"
    )
    assert out is not None
    rel = out.relative_to(tmp_path).parts
    assert rel[0] == "tasks"
    assert rel[2] == "artifacts"
    assert rel[3] == "files"


def test_report_is_found_by_build_deliverable_summary(tmp_path: Path) -> None:
    """End-to-end: after materialising, the readback summary names the report."""
    _task_dir(tmp_path)
    out = materialize_answer_document(
        tmp_path, answers=[_LONG_ANSWER], prompt="relocate to SF"
    )
    assert out is not None
    summary = build_deliverable_summary(tmp_path)
    assert out.name in summary
    assert "Datei" in summary


def test_existing_file_deliverable_suppresses_report(tmp_path: Path) -> None:
    """A real artifact already exists → never write a duplicate report."""
    files = _task_dir(tmp_path) / "artifacts" / "files"
    files.mkdir(parents=True)
    (files / "landing.html").write_text("<html/>", encoding="utf-8")
    out = materialize_answer_document(
        tmp_path, answers=[_LONG_ANSWER], prompt="build a landing page"
    )
    assert out is None
    # The genuine deliverable is untouched and no extra .md was created.
    assert (files / "landing.html").is_file()
    assert not list(files.glob("*.md"))


def test_report_reuses_existing_task_dir(tmp_path: Path) -> None:
    """The report joins the archive's existing task dir, not a new sibling."""
    _task_dir(tmp_path, task_id="019edf4c-f827")
    out = materialize_answer_document(
        tmp_path, answers=[_LONG_ANSWER], prompt="relocate to SF"
    )
    assert out is not None
    # Position-exact: the task id must be the tasks/<id>/ segment, not just
    # present somewhere in the path.
    assert out.relative_to(tmp_path).parts[1] == "019edf4c-f827"


def test_report_created_when_no_task_dir_exists(tmp_path: Path) -> None:
    """No tasks/ at all (Edit-only / odd path) → a synthetic deliverable dir."""
    out = materialize_answer_document(
        tmp_path, answers=[_LONG_ANSWER], prompt="relocate to SF"
    )
    assert out is not None
    rel = out.relative_to(tmp_path).parts
    assert rel[0] == "tasks" and rel[2] == "artifacts" and rel[3] == "files"
    assert out.is_file()


def test_title_from_prompt_in_report(tmp_path: Path) -> None:
    """The user's request becomes the report's H1 title."""
    _task_dir(tmp_path)
    out = materialize_answer_document(
        tmp_path,
        answers=[_LONG_ANSWER],
        prompt="What do I need to do to relocate to San Francisco?",
    )
    assert out is not None
    body = out.read_text(encoding="utf-8")
    first_line = body.splitlines()[0]
    assert first_line.startswith("# ")
    assert "relocate to San Francisco" in first_line


def test_slug_from_prompt_in_filename(tmp_path: Path) -> None:
    """The filename is derived from the prompt, lowercased + hyphenated."""
    _task_dir(tmp_path)
    out = materialize_answer_document(
        tmp_path, answers=[_LONG_ANSWER], prompt="Relocate to San Francisco"
    )
    assert out is not None
    assert out.name == "relocate-to-san-francisco.md"


def test_filename_is_ascii_safe(tmp_path: Path) -> None:
    """German umlauts are transliterated so the filename stays portable."""
    _task_dir(tmp_path)
    # German prompt is the umlaut-transliteration test fixture.
    out = materialize_answer_document(
        tmp_path, answers=[_LONG_ANSWER], prompt="Auswandern nach München"  # i18n-allow
    )
    assert out is not None
    assert out.name == "auswandern-nach-muenchen.md"
    # No raw non-ASCII in the filename.
    assert out.name.encode("ascii", "strict")


def test_empty_or_symbol_only_prompt_falls_back_to_report_name(tmp_path: Path) -> None:
    """A prompt that slugifies to nothing → a stable default filename."""
    _task_dir(tmp_path)
    out = materialize_answer_document(
        tmp_path, answers=[_LONG_ANSWER], prompt="???"
    )
    assert out is not None
    assert out.name == "report.md"


def test_quality_directive_preamble_stripped_from_title_and_filename(
    tmp_path: Path,
) -> None:
    """The spawn_worker quality-directive preamble must not become the title.

    The mission prompt is ``<quality-directive>\\n\\n<real request>`` — the title
    and filename must reflect the real user request, not the standing directive
    (live forensic 2026-06-19: backfilling old missions showed every report would
    otherwise be titled 'Deliver a complete, polished, production-quality …').
    """
    _task_dir(tmp_path)
    prompt = (
        "Deliver a complete, polished, production-quality result that fully "
        'satisfies the request. A skeleton or "content follows" shell is a '
        "FAILURE.\n\nWhat do I need to relocate to San Francisco?"
    )
    out = materialize_answer_document(tmp_path, answers=[_LONG_ANSWER], prompt=prompt)
    assert out is not None
    assert out.name == "what-do-i-need-to-relocate-to-san-francisco.md"
    first_line = out.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "# What do I need to relocate to San Francisco?"
    assert "production-quality" not in first_line


def test_multiple_answers_are_joined(tmp_path: Path) -> None:
    """A multi-task mission's answers all appear in the single report."""
    _task_dir(tmp_path)
    out = materialize_answer_document(
        tmp_path,
        answers=[_LONG_ANSWER, "Also remember to ship your belongings early."],
        prompt="relocate",
    )
    assert out is not None
    body = out.read_text(encoding="utf-8")
    assert "secure a work visa" in body
    assert "ship your belongings" in body


def test_idempotent_second_call_writes_no_duplicate(tmp_path: Path) -> None:
    """Re-running approve must not produce a second report file."""
    _task_dir(tmp_path)
    first = materialize_answer_document(
        tmp_path, answers=[_LONG_ANSWER], prompt="relocate to SF"
    )
    assert first is not None
    second = materialize_answer_document(
        tmp_path, answers=[_LONG_ANSWER], prompt="relocate to SF"
    )
    # The first report now counts as an existing deliverable → no duplicate.
    assert second is None
    assert len(list(tmp_path.rglob("*.md"))) == 1


def test_missing_mission_dir_is_safe(tmp_path: Path) -> None:
    """A non-existent mission dir must not crash — returns None."""
    bogus = tmp_path / "does-not-exist"
    assert materialize_answer_document(
        bogus, answers=[_LONG_ANSWER], prompt="x"
    ) is None
