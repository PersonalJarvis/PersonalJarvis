"""Tests for log_summarizer (head/tail/grep + optional Haiku triage)."""
from __future__ import annotations

import asyncio

import pytest

from jarvis.missions.critic.log_summarizer import (
    DEFAULT_MAX_CHARS,
    HEAD_LINES,
    TAIL_LINES,
    TRIAGE_THRESHOLD_CHARS,
    summarize_log,
)


# --- Empty / trivial cases ---


@pytest.mark.asyncio
async def test_empty_log_returns_empty_string() -> None:
    out = await summarize_log("")
    assert out == ""


@pytest.mark.asyncio
async def test_whitespace_only_log_returns_empty() -> None:
    out = await summarize_log("   \n\n  \t\n")
    assert out == ""


@pytest.mark.asyncio
async def test_short_log_returned_with_section_markers() -> None:
    log = "line1\nline2\nline3\n"
    out = await summarize_log(log)
    assert "HEAD" in out
    assert "line1" in out
    assert "line3" in out


# --- Head/Tail behavior ---


@pytest.mark.asyncio
async def test_long_log_includes_first_30_lines() -> None:
    lines = [f"L{i}" for i in range(200)]
    log = "\n".join(lines)
    out = await summarize_log(log, max_chars=10_000)
    for i in range(HEAD_LINES):
        assert f"L{i}" in out


@pytest.mark.asyncio
async def test_long_log_includes_last_50_lines() -> None:
    lines = [f"L{i}" for i in range(200)]
    log = "\n".join(lines)
    out = await summarize_log(log, max_chars=10_000)
    for i in range(200 - TAIL_LINES, 200):
        assert f"L{i}" in out


# --- Error grep ---


@pytest.mark.asyncio
async def test_error_grep_finds_traceback() -> None:
    lines = ["normal"] * 100 + ["Traceback (most recent call last):"] + ["normal"] * 100
    log = "\n".join(lines)
    out = await summarize_log(log, max_chars=10_000)
    assert "Traceback" in out
    assert "ERRORS" in out


@pytest.mark.asyncio
async def test_error_grep_finds_exception_keyword() -> None:
    log = "ok\n" * 100 + "ValueError: foo\n" + "ok\n" * 100
    out = await summarize_log(log, max_chars=10_000)
    assert "ValueError: foo" in out


@pytest.mark.asyncio
async def test_error_grep_dedups_repeated_lines() -> None:
    log = "Error: same\n" * 5 + "ok\n" * 50
    out = await summarize_log(log, max_chars=10_000)
    error_section_start = out.index("ERRORS")
    error_section = out[error_section_start:]
    # only ONE "Error: same" entry in the error block (dedup)
    error_block_lines = error_section.split("\n")
    error_count = sum(1 for line in error_block_lines if line == "Error: same")
    assert error_count == 1


# --- Hard char-cap ---


@pytest.mark.asyncio
async def test_max_chars_truncates_with_marker() -> None:
    log = "x" * 100_000
    out = await summarize_log(log, max_chars=500)
    assert len(out) <= 500 + 100  # +Marker-Suffix
    assert "truncated" in out


@pytest.mark.asyncio
async def test_default_max_chars_is_4kb() -> None:
    assert DEFAULT_MAX_CHARS == 4_000


# --- Triage-fn integration ---


@pytest.mark.asyncio
async def test_triage_fn_called_on_large_log() -> None:
    called: list[str] = []

    async def fake_triage(text: str) -> str:
        called.append(text[:20])
        return "TRIAGED_SUMMARY"

    log = "x" * (TRIAGE_THRESHOLD_CHARS + 1)
    out = await summarize_log(log, triage_fn=fake_triage, max_chars=1000)
    assert called, "triage_fn was not called"
    assert "TRIAGED_SUMMARY" in out


@pytest.mark.asyncio
async def test_triage_fn_not_called_below_threshold() -> None:
    called: list[str] = []

    async def fake_triage(text: str) -> str:
        called.append(text)
        return "TRIAGED"

    log = "x" * (TRIAGE_THRESHOLD_CHARS - 100)
    await summarize_log(log, triage_fn=fake_triage)
    assert called == []


@pytest.mark.asyncio
async def test_triage_fn_crash_falls_back_to_pure_python() -> None:
    """Triage must NEVER block the Critic — fallback to head/tail/grep."""

    async def crashing_triage(_text: str) -> str:
        raise RuntimeError("brain manager kaputt")

    log = "head\n" * 10 + "tail\n" * 10
    log = log + "x" * (TRIAGE_THRESHOLD_CHARS + 1)
    out = await summarize_log(log, triage_fn=crashing_triage, max_chars=10_000)
    # Fallback is head/tail rendering (no crash, no empty string).
    assert "HEAD" in out


# --- Sanity ---


def test_module_constants_sane() -> None:
    assert HEAD_LINES > 0
    assert TAIL_LINES > 0
    assert TRIAGE_THRESHOLD_CHARS > DEFAULT_MAX_CHARS
