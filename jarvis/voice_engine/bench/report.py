"""Print the newest result of every suite and configuration as Markdown."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _key(report: dict[str, Any]) -> str:
    suite = report.get("suite", "?")
    parts = [suite]
    for field in ("model", "engine", "tts", "voice", "num_gpu", "with_tools"):
        value = report.get(field)
        if value not in (None, False, ""):
            parts.append(f"{field}={value}")
    return " ".join(parts)


def latest_reports(root: Path) -> dict[str, dict[str, Any]]:
    newest: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A half-written or foreign file in the results folder is skipped,
            # not fatal: the summary still covers every readable report.
            continue
        report["_path"] = str(path)
        newest[_key(report)] = report
    return newest


def _fmt(stats: Any) -> str:
    if isinstance(stats, dict) and "p50" in stats:
        p50, p95 = stats.get("p50"), stats.get("p95")
        if p50 is None:
            return "–"
        return f"{p50:.0f} / {p95:.0f}" if p95 is not None else f"{p50:.0f}"
    return "–" if stats is None else str(stats)


def print_summary(root: Path) -> None:
    for key, report in sorted(latest_reports(root).items()):
        load = report.get("load_before", {})
        print(f"\n## {key}\n")
        print(f"_{report.get('created_utc')} · CPU load before {load.get('cpu_percent', '?')} %_\n")
        if "summary" in report:
            for name, stats in report["summary"].items():
                print(f"- {name}: {_fmt(stats)}")
        for group, values in (report.get("groups") or {}).items():
            cells = ", ".join(f"{k} {_fmt(v)}" for k, v in values.items())
            print(f"- {group}: {cells}")
        if report.get("load_s"):
            print(f"- load_s: {report['load_s']}")
