"""Tests for the latency_report aggregation CLI (LATENCY_REPORT_001).

Covers:
  * percentile math (linear interpolation)
  * stage / duration aggregation
  * --last filter (newest-first slicing)
  * --since filter (ISO-8601 lexicographic compare)
  * markdown vs text vs json output modes
"""
from __future__ import annotations

import io
import json
import math
import sys
from pathlib import Path

import pytest

from jarvis.tools import latency_report


def _write_rows(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False))
            fh.write("\n")


def _row(turn: int, ttfw: float, stages: dict[str, float] | None = None) -> dict:
    return {
        "turn_id": f"{turn:032x}",
        "iso_timestamp": f"2026-05-26T18:00:{turn:02d}+00:00",
        "anchor_ns": turn * 1_000_000,
        "stages_ms": stages or {"turn_to_first_audio": ttfw, "tts_stream_done": ttfw + 200},
        "durations_ms": {"brain_ttft": ttfw - 200, "tts_ttfb": 200},
        "ttfw_ms": ttfw,
        "total_ms": ttfw + 200,
        "stt_input_audio_ms": None,
        "brain_input_tokens": None,
        "brain_output_tokens": None,
        "tts_input_chars": None,
        "errors": [],
    }


def test_percentile_linear_interpolation() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert latency_report._percentile(values, 50) == 5.5
    assert latency_report._percentile(values, 95) == pytest.approx(9.55, rel=1e-3)
    assert latency_report._percentile(values, 0) == 1.0
    assert latency_report._percentile(values, 100) == 10.0


def test_percentile_empty_list_is_nan() -> None:
    assert math.isnan(latency_report._percentile([], 50))


def test_aggregate_computes_per_stage_and_bottlenecks(tmp_path: Path) -> None:
    rows = [_row(i, ttfw=1000.0 + i * 100) for i in range(10)]
    path = tmp_path / "log.jsonl"
    _write_rows(path, rows)
    parsed = list(latency_report._iter_rows(path, last=0))
    agg = latency_report._aggregate(parsed)
    assert agg.turns == 10
    assert agg.ttfw_p50 == 1450.0  # median of 1000..1900 step 100
    assert agg.ttfw_p95 == pytest.approx(1855.0, rel=1e-3)
    # brain_ttft = ttfw - 200, so p50 should be 1250.0.
    by_name = {s.name: s for s in agg.duration_stats}
    assert by_name["brain_ttft"].p50_ms == 1250.0
    assert by_name["tts_ttfb"].p50_ms == 200.0
    # Bottlenecks are sorted by p50 descending — brain_ttft wins at 1250 ms.
    top = agg.bottlenecks[0]
    assert top[0] == "brain_ttft"
    assert top[1] == 1250.0


def test_last_filter_keeps_newest(tmp_path: Path) -> None:
    rows = [_row(i, ttfw=100.0 + i) for i in range(20)]
    path = tmp_path / "log.jsonl"
    _write_rows(path, rows)
    kept = list(latency_report._iter_rows(path, last=5))
    assert len(kept) == 5
    # Last 5 are turns 15..19.
    assert [r["turn_id"] for r in kept] == [f"{i:032x}" for i in range(15, 20)]


def test_since_filter_drops_older_rows(tmp_path: Path) -> None:
    rows = [_row(i, ttfw=100.0) for i in range(10)]
    path = tmp_path / "log.jsonl"
    _write_rows(path, rows)
    kept = list(
        latency_report._iter_rows(
            path,
            last=0,
            since="2026-05-26T18:00:05+00:00",
        )
    )
    # Rows with second >= 05 → turns 5..9 = 5 rows.
    assert len(kept) == 5


def test_main_text_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_row(i, ttfw=1000.0) for i in range(5)]
    path = tmp_path / "log.jsonl"
    _write_rows(path, rows)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    exit_code = latency_report.main(["--path", str(path)])
    output = buf.getvalue()
    assert exit_code == 0
    assert "TTFW" in output
    assert "brain_ttft" in output
    assert "5 turns" in output


def test_main_markdown_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_row(i, ttfw=1000.0) for i in range(3)]
    path = tmp_path / "log.jsonl"
    _write_rows(path, rows)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    exit_code = latency_report.main(["--path", str(path), "--markdown"])
    output = buf.getvalue()
    assert exit_code == 0
    assert "###" in output  # markdown heading
    assert "|---|" in output  # markdown table


def test_main_json_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_row(i, ttfw=1000.0) for i in range(3)]
    path = tmp_path / "log.jsonl"
    _write_rows(path, rows)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    exit_code = latency_report.main(["--path", str(path), "--json"])
    payload = json.loads(buf.getvalue())
    assert exit_code == 0
    assert payload["turns"] == 3
    assert "bottlenecks" in payload


def test_main_returns_2_on_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err)
    code = latency_report.main(["--path", str(tmp_path / "nope.jsonl")])
    assert code == 2
    assert "No latency log" in err.getvalue()


def test_main_returns_3_when_filter_drops_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "log.jsonl"
    _write_rows(path, [_row(0, ttfw=100.0)])
    err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err)
    code = latency_report.main(["--path", str(path), "--since", "2099-01-01T00:00:00+00:00"])
    assert code == 3
