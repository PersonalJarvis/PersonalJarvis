"""Unit tests for ``jarvis.memory.wiki.log_writer.LogWriter``.

Covers the contract documented in ``schema.md``:
* Append-only, never edits existing entries.
* Atomic on Windows (tempfile + os.replace) — a crash mid-write leaves
  the previous content intact.
* Rejects unknown verbs and empty fields.
* Renders the documented Markdown shape.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from jarvis.memory.wiki.log_writer import VALID_VERBS, LogWriter


pytestmark = pytest.mark.asyncio


_FIXED_TIMESTAMP = datetime(2026, 5, 11, 19, 42)


def _make_writer(log_path: Path) -> LogWriter:
    return LogWriter(log_path, clock=lambda: _FIXED_TIMESTAMP)


async def test_append_creates_file_when_missing(tmp_path: Path) -> None:
    log_path = tmp_path / "log.md"
    writer = _make_writer(log_path)
    await writer.append_log_entry(
        verb="ingest",
        subject="first entry",
        pages_touched=["entities/the maintainer"],
        source="unit test",
        summary="a first entry was appended to a fresh log.",
    )
    content = log_path.read_text(encoding="utf-8")
    assert "## [2026-05-11 19:42] ingest | first entry" in content
    assert "- pages touched: [[entities/the maintainer]]" in content
    assert "- source: unit test" in content
    assert "- summary: a first entry was appended to a fresh log." in content


async def test_append_preserves_existing_content(tmp_path: Path) -> None:
    """Appending must keep the existing log header and every prior entry."""
    log_path = tmp_path / "log.md"
    log_path.write_text(
        "# Wiki Log\n\nExisting preamble and content.\n",
        encoding="utf-8",
    )
    writer = _make_writer(log_path)
    await writer.append_log_entry(
        verb="update",
        subject="follow-up",
        pages_touched=["concepts/awareness-layer"],
        source="follow-up source",
        summary="appended after a real preamble.",
    )
    content = log_path.read_text(encoding="utf-8")
    assert content.startswith("# Wiki Log\n\nExisting preamble and content.\n")
    assert "## [2026-05-11 19:42] update | follow-up" in content


async def test_two_appends_render_in_order(tmp_path: Path) -> None:
    log_path = tmp_path / "log.md"
    writer = _make_writer(log_path)
    await writer.append_log_entry(
        verb="create",
        subject="first",
        pages_touched=["entities/a"],
        source="src",
        summary="first one.",
    )
    await writer.append_log_entry(
        verb="update",
        subject="second",
        pages_touched=["entities/a"],
        source="src",
        summary="second one.",
    )
    content = log_path.read_text(encoding="utf-8")
    first_idx = content.index("create | first")
    second_idx = content.index("update | second")
    assert first_idx < second_idx


async def test_pages_touched_renders_existing_wikilink_form_unchanged(
    tmp_path: Path,
) -> None:
    """Inputs already wrapped in ``[[...]]`` round-trip verbatim."""
    log_path = tmp_path / "log.md"
    writer = _make_writer(log_path)
    await writer.append_log_entry(
        verb="ingest",
        subject="s",
        pages_touched=["[[entities/x]]", "[[concepts/y]]"],
        source="s",
        summary="t.",
    )
    content = log_path.read_text(encoding="utf-8")
    assert "- pages touched: [[entities/x]], [[concepts/y]]" in content


async def test_pages_touched_wraps_bare_slugs(tmp_path: Path) -> None:
    log_path = tmp_path / "log.md"
    writer = _make_writer(log_path)
    await writer.append_log_entry(
        verb="ingest",
        subject="s",
        pages_touched=["the maintainer", "awareness-layer.md", "[[wiki-curator]]"],
        source="s",
        summary="t.",
    )
    content = log_path.read_text(encoding="utf-8")
    assert (
        "- pages touched: [[the maintainer]], [[awareness-layer]], [[wiki-curator]]"
        in content
    )


async def test_empty_pages_touched_renders_placeholder(tmp_path: Path) -> None:
    log_path = tmp_path / "log.md"
    writer = _make_writer(log_path)
    await writer.append_log_entry(
        verb="ingest",
        subject="s",
        pages_touched=[],
        source="src",
        summary="no pages were touched but the entry is still required.",
    )
    content = log_path.read_text(encoding="utf-8")
    assert "- pages touched: (none)" in content


async def test_rejects_unknown_verb(tmp_path: Path) -> None:
    log_path = tmp_path / "log.md"
    writer = _make_writer(log_path)
    with pytest.raises(ValueError, match="verb"):
        await writer.append_log_entry(
            verb="frobnicate",
            subject="s",
            pages_touched=[],
            source="s",
            summary="s.",
        )


async def test_rejects_empty_subject(tmp_path: Path) -> None:
    log_path = tmp_path / "log.md"
    writer = _make_writer(log_path)
    with pytest.raises(ValueError, match="subject"):
        await writer.append_log_entry(
            verb="ingest",
            subject="   ",
            pages_touched=[],
            source="s",
            summary="s.",
        )


async def test_rejects_empty_source(tmp_path: Path) -> None:
    log_path = tmp_path / "log.md"
    writer = _make_writer(log_path)
    with pytest.raises(ValueError, match="source"):
        await writer.append_log_entry(
            verb="ingest",
            subject="s",
            pages_touched=[],
            source="",
            summary="s.",
        )


async def test_rejects_empty_summary(tmp_path: Path) -> None:
    log_path = tmp_path / "log.md"
    writer = _make_writer(log_path)
    with pytest.raises(ValueError, match="summary"):
        await writer.append_log_entry(
            verb="ingest",
            subject="s",
            pages_touched=[],
            source="s",
            summary="   ",
        )


async def test_all_documented_verbs_are_accepted(tmp_path: Path) -> None:
    log_path = tmp_path / "log.md"
    writer = _make_writer(log_path)
    for verb in sorted(VALID_VERBS):
        await writer.append_log_entry(
            verb=verb,
            subject=f"subject for {verb}",
            pages_touched=[],
            source="test",
            summary=f"verifying verb {verb}.",
        )
    content = log_path.read_text(encoding="utf-8")
    for verb in VALID_VERBS:
        assert f" {verb} | subject for {verb}" in content


async def test_crash_mid_write_leaves_original_untouched(tmp_path: Path) -> None:
    """Atomicity contract — a raise immediately before ``os.replace`` must
    leave the on-disk log identical to its pre-append state and clean up
    the tempfile."""
    log_path = tmp_path / "log.md"
    original = "# Wiki Log\n\nOriginal content that must survive.\n"
    log_path.write_text(original, encoding="utf-8")

    writer = _make_writer(log_path)

    class BoomError(RuntimeError):
        pass

    def boom() -> None:
        raise BoomError("simulated crash before replace")

    writer._pre_replace_hook = boom  # type: ignore[method-assign]

    with pytest.raises(BoomError):
        await writer.append_log_entry(
            verb="ingest",
            subject="never lands",
            pages_touched=["entities/x"],
            source="s",
            summary="this entry must not appear on disk.",
        )

    # Original content is intact.
    assert log_path.read_text(encoding="utf-8") == original
    # No leftover tempfile in the parent directory.
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".log.md.")]
    assert leftovers == []


async def test_missing_parent_directory_raises(tmp_path: Path) -> None:
    log_path = tmp_path / "nonexistent" / "log.md"
    writer = _make_writer(log_path)
    with pytest.raises(FileNotFoundError):
        await writer.append_log_entry(
            verb="ingest",
            subject="s",
            pages_touched=[],
            source="s",
            summary="s.",
        )


async def test_summary_collapses_internal_whitespace(tmp_path: Path) -> None:
    """The schema renders summary on one logical line; collapse newlines."""
    log_path = tmp_path / "log.md"
    writer = _make_writer(log_path)
    await writer.append_log_entry(
        verb="ingest",
        subject="s",
        pages_touched=[],
        source="src",
        summary="  multi\n\nline\nsummary  with  spaces  ",
    )
    content = log_path.read_text(encoding="utf-8")
    assert "- summary: multi line summary with spaces" in content


async def test_entry_format_matches_schema_grep_pattern(tmp_path: Path) -> None:
    """``grep ^## \\[`` must pick up every entry — guard the leading shape."""
    log_path = tmp_path / "log.md"
    writer = _make_writer(log_path)
    await writer.append_log_entry(
        verb="ingest",
        subject="grep target",
        pages_touched=["entities/x"],
        source="s",
        summary="s.",
    )
    lines = log_path.read_text(encoding="utf-8").splitlines()
    headers = [ln for ln in lines if ln.startswith("## [")]
    assert len(headers) == 1
    assert headers[0] == "## [2026-05-11 19:42] ingest | grep target"


async def test_log_path_property(tmp_path: Path) -> None:
    log_path = tmp_path / "log.md"
    writer = _make_writer(log_path)
    assert writer.log_path == log_path
