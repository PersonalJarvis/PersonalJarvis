"""Aggregation CLI for the per-turn latency JSONL log (LATENCY_REPORT_001).

Usage::

    python -m jarvis.tools.latency_report               # last 50 turns
    python -m jarvis.tools.latency_report --last 100
    python -m jarvis.tools.latency_report --since 2026-05-26T18:00:00
    python -m jarvis.tools.latency_report --markdown    # markdown table
    python -m jarvis.tools.latency_report --json        # raw aggregation

Reads ``state/latency_log.jsonl`` (override with ``--path``) and prints:

  * per-stage median / p95 / max
  * derived per-stage durations (vad_to_stt_first, brain_ttft, tts_ttfb, ...)
  * bottleneck ranking by absolute ms and relative share of TTFW

Designed to run on a €5 / month VPS — stdlib only, no pandas, no plotting.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# Order of stages on the report row — mirrors the LATENCY_REPORT_001 t0..t9 spec.
_STAGE_ORDER: tuple[str, ...] = (
    "stt_first_partial",     # t1
    "stt_finalize",          # t2
    "intent_decision",       # between t2-t3
    "brain_request_sent",    # t3
    "ack_first_token",       # ack-brain (optional)
    "ack_first_audio",       # ack-brain (optional)
    "brain_first_token",     # t4
    "brain_last_token",      # t5
    "tts_request_sent",      # t6
    "tts_first_chunk",       # t7
    "brain_first_audio",     # variant of t8
    "turn_to_first_audio",   # t8 — canonical TTFW
    "tts_stream_done",       # t9
)

# Bottleneck ranking uses the derived per-stage durations (relative deltas),
# not the cumulative offsets — otherwise every stage looks bigger than the one
# before it. Keep in sync with telemetry.latency_log._DURATION_PAIRS.
_DURATION_KEYS: tuple[str, ...] = (
    "vad_to_stt_first",
    "stt_streaming",
    "stt_to_brain_request",
    "brain_ttft",
    "brain_streaming",
    "brain_to_tts_request",
    "tts_ttfb",
    "tts_to_audio_out",
    "tts_tail",
)


@dataclass(slots=True)
class StageStats:
    name: str
    samples: int
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None
    mean_ms: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "samples": self.samples,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "max_ms": self.max_ms,
            "mean_ms": self.mean_ms,
        }


@dataclass(slots=True)
class Aggregation:
    turns: int
    ttfw_p50: float | None
    ttfw_p95: float | None
    ttfw_max: float | None
    total_p50: float | None
    total_p95: float | None
    stage_stats: list[StageStats]
    duration_stats: list[StageStats]
    bottlenecks: list[tuple[str, float, float]]  # (label, abs_p50_ms, rel_share)


def main(argv: list[str] | None = None) -> int:
    """Entry point — returns an exit code."""
    # CLAUDE.md "Windows specifics": new CLI modules must reconfigure stdout
    # to UTF-8 or stick to ASCII. The report uses → and · so UTF-8 is needed.
    # ``reconfigure`` is a no-op on POSIX where stdout is already utf-8.
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args(argv)
    path = Path(args.path)
    if not path.exists():
        sys.stderr.write(f"No latency log at {path} — has [latency].log_jsonl been enabled?\n")
        return 2
    rows = list(_iter_rows(path, last=args.last, since=args.since))
    if not rows:
        sys.stderr.write("No rows after filter — nothing to aggregate.\n")
        return 3
    agg = _aggregate(rows)
    if args.json:
        sys.stdout.write(_render_json(agg))
    elif args.markdown:
        sys.stdout.write(_render_markdown(agg, path=path))
    else:
        sys.stdout.write(_render_text(agg, path=path))
    return 0


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m jarvis.tools.latency_report",
        description="Aggregate the per-turn voice latency JSONL log.",
    )
    parser.add_argument(
        "--path",
        default="state/latency_log.jsonl",
        help="JSONL path (default: state/latency_log.jsonl).",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=50,
        help="Aggregate the last N rows (default: 50, 0 = all).",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="ISO-8601 timestamp; keep rows whose iso_timestamp is >= this.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit raw aggregation as JSON.",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Emit a markdown report (paste into LATENCY_REPORT_001.md).",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Row I/O
# ---------------------------------------------------------------------------
def _iter_rows(
    path: Path,
    *,
    last: int = 0,
    since: str | None = None,
) -> Iterable[dict[str, Any]]:
    """Yield rows matching the filters. Newest-first when ``last`` > 0."""
    since_iso: str | None = None
    if since:
        # Validate by parsing; fall back to raw string compare if parse fails
        # (ISO-8601 strings sort lexicographically when the same offset is used).
        try:
            datetime.fromisoformat(since)
            since_iso = since
        except ValueError:
            sys.stderr.write(f"--since: not a valid ISO timestamp: {since!r}\n")
            since_iso = since
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since_iso is not None:
                ts = row.get("iso_timestamp", "")
                if ts < since_iso:
                    continue
            rows.append(row)
    if last and last > 0:
        rows = rows[-last:]
    return rows


# ---------------------------------------------------------------------------
# Aggregation math
# ---------------------------------------------------------------------------
def _percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (0..100). Empty list → math.nan."""
    if not values:
        return math.nan
    if p <= 0:
        return float(min(values))
    if p >= 100:
        return float(max(values))
    sorted_vals = sorted(values)
    rank = (p / 100.0) * (len(sorted_vals) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return float(sorted_vals[low])
    frac = rank - low
    return float(sorted_vals[low] + frac * (sorted_vals[high] - sorted_vals[low]))


def _stats(name: str, values: list[float]) -> StageStats:
    n = len(values)
    if n == 0:
        return StageStats(name=name, samples=0, p50_ms=None, p95_ms=None, max_ms=None, mean_ms=None)
    return StageStats(
        name=name,
        samples=n,
        p50_ms=round(_percentile(values, 50), 3),
        p95_ms=round(_percentile(values, 95), 3),
        max_ms=round(max(values), 3),
        mean_ms=round(sum(values) / n, 3),
    )


def _aggregate(rows: list[dict[str, Any]]) -> Aggregation:
    stages: dict[str, list[float]] = defaultdict(list)
    durations: dict[str, list[float]] = defaultdict(list)
    ttfws: list[float] = []
    totals: list[float] = []
    for row in rows:
        for stage_name, value in (row.get("stages_ms") or {}).items():
            if value is None:
                continue
            stages[stage_name].append(float(value))
        for dur_name, value in (row.get("durations_ms") or {}).items():
            if value is None:
                continue
            durations[dur_name].append(float(value))
        ttfw = row.get("ttfw_ms")
        if ttfw is not None:
            ttfws.append(float(ttfw))
        total = row.get("total_ms")
        if total is not None:
            totals.append(float(total))

    stage_stats = [_stats(name, stages.get(name, [])) for name in _STAGE_ORDER]
    duration_stats = [_stats(name, durations.get(name, [])) for name in _DURATION_KEYS]
    # Bottleneck ranking: sort by p50, attach relative share of TTFW p50.
    ttfw_p50 = round(_percentile(ttfws, 50), 3) if ttfws else None
    bottlenecks: list[tuple[str, float, float]] = []
    for ds in duration_stats:
        if ds.p50_ms is None:
            continue
        rel = (ds.p50_ms / ttfw_p50) if ttfw_p50 and ttfw_p50 > 0 else float("nan")
        bottlenecks.append((ds.name, ds.p50_ms, rel))
    bottlenecks.sort(key=lambda t: t[1], reverse=True)

    return Aggregation(
        turns=len(rows),
        ttfw_p50=ttfw_p50,
        ttfw_p95=round(_percentile(ttfws, 95), 3) if ttfws else None,
        ttfw_max=round(max(ttfws), 3) if ttfws else None,
        total_p50=round(_percentile(totals, 50), 3) if totals else None,
        total_p95=round(_percentile(totals, 95), 3) if totals else None,
        stage_stats=stage_stats,
        duration_stats=duration_stats,
        bottlenecks=bottlenecks,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _fmt(value: float | None, suffix: str = "") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.1f}{suffix}"


def _render_text(agg: Aggregation, *, path: Path) -> str:
    lines: list[str] = []
    lines.append(f"latency_report from {path} — {agg.turns} turns")
    lines.append("=" * 72)
    lines.append(
        f"TTFW (turn → first audio):  p50={_fmt(agg.ttfw_p50, 'ms'):>10}  "
        f"p95={_fmt(agg.ttfw_p95, 'ms'):>10}  max={_fmt(agg.ttfw_max, 'ms'):>10}"
    )
    lines.append(
        f"Total (turn → tts done):    p50={_fmt(agg.total_p50, 'ms'):>10}  "
        f"p95={_fmt(agg.total_p95, 'ms'):>10}"
    )
    lines.append("")
    lines.append("Per-stage offset from turn anchor (cumulative ms, lower = earlier):")
    lines.append(f"  {'stage':<22} {'n':>5} {'p50':>10} {'p95':>10} {'max':>10}")
    for s in agg.stage_stats:
        lines.append(
            f"  {s.name:<22} {s.samples:>5} "
            f"{_fmt(s.p50_ms):>10} {_fmt(s.p95_ms):>10} {_fmt(s.max_ms):>10}"
        )
    lines.append("")
    lines.append("Per-stage duration (interval between adjacent marks, ms):")
    lines.append(f"  {'segment':<24} {'n':>5} {'p50':>10} {'p95':>10} {'max':>10}")
    for s in agg.duration_stats:
        lines.append(
            f"  {s.name:<24} {s.samples:>5} "
            f"{_fmt(s.p50_ms):>10} {_fmt(s.p95_ms):>10} {_fmt(s.max_ms):>10}"
        )
    lines.append("")
    lines.append("Bottleneck ranking (by p50 absolute, with share of TTFW p50):")
    for label, abs_ms, rel in agg.bottlenecks:
        rel_str = f"{rel * 100:>5.1f}%" if not math.isnan(rel) else "  n/a "
        lines.append(f"  {label:<24} {abs_ms:>10.1f} ms   ({rel_str})")
    lines.append("")
    return "\n".join(lines) + "\n"


def _render_markdown(agg: Aggregation, *, path: Path) -> str:
    lines: list[str] = []
    lines.append(f"### Latency aggregation ({agg.turns} turns from `{path}`)")
    lines.append("")
    lines.append(
        f"- **TTFW**: p50 = {_fmt(agg.ttfw_p50, ' ms')} · p95 = "
        f"{_fmt(agg.ttfw_p95, ' ms')} · max = {_fmt(agg.ttfw_max, ' ms')}"
    )
    lines.append(
        f"- **Total**: p50 = {_fmt(agg.total_p50, ' ms')} · p95 = "
        f"{_fmt(agg.total_p95, ' ms')}"
    )
    lines.append("")
    lines.append("#### Per-stage duration (segment between two marks)")
    lines.append("")
    lines.append("| Segment | n | p50 ms | p95 ms | max ms |")
    lines.append("|---|---:|---:|---:|---:|")
    for s in agg.duration_stats:
        lines.append(
            f"| `{s.name}` | {s.samples} | {_fmt(s.p50_ms)} | "
            f"{_fmt(s.p95_ms)} | {_fmt(s.max_ms)} |"
        )
    lines.append("")
    lines.append("#### Bottleneck ranking (p50 share of TTFW)")
    lines.append("")
    lines.append("| Segment | p50 ms | share |")
    lines.append("|---|---:|---:|")
    for label, abs_ms, rel in agg.bottlenecks:
        rel_str = f"{rel * 100:.1f}%" if not math.isnan(rel) else "n/a"
        lines.append(f"| `{label}` | {abs_ms:.1f} | {rel_str} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def _render_json(agg: Aggregation) -> str:
    payload = {
        "turns": agg.turns,
        "ttfw_p50": agg.ttfw_p50,
        "ttfw_p95": agg.ttfw_p95,
        "ttfw_max": agg.ttfw_max,
        "total_p50": agg.total_p50,
        "total_p95": agg.total_p95,
        "stages": [s.as_dict() for s in agg.stage_stats],
        "durations": [s.as_dict() for s in agg.duration_stats],
        "bottlenecks": [
            {"name": name, "p50_ms": abs_ms, "share_of_ttfw": rel}
            for name, abs_ms, rel in agg.bottlenecks
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
