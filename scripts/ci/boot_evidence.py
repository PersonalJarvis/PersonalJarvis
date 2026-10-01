"""Validate captured restart observations without launching another application.

The collector owns the actual process and records elapsed parent perf-counter
time from spawn for HTTP HTML delivery and the two stdout sentinels. Child
sentinel numbers have different origins and must never replace receipt times.
This module validates evidence; it does not create observations or certify that
local voice readiness implies a successful conversation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from uuid import UUID

SOURCE_FILES = (
    "jarvis/ui/desktop_app.py",
    "jarvis/ui/web/server.py",
    "jarvis/ui/web/launcher.py",
    "jarvis/ui/deferred_setup.py",
)


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return float(value)


def read_restart_evidence(path: Path, source_root: Path) -> tuple[dict, bool]:
    """Fail closed on stale source, missing anchors or ambiguous measurements."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("measurement") != "controlled-desktop-restart":
        raise ValueError("Expected controlled-desktop-restart evidence schema 1")
    UUID(data["run_id"])
    if data.get("launch_mode") not in {"legacy", "fastboot"}:
        raise ValueError("Record the resolved launcher path")
    if type(data.get("pid")) is not int or data["pid"] <= 0:
        raise ValueError("A real child process PID is required")
    if data.get("clock") != "parent-perf-counter-from-spawn":
        raise ValueError("Anchors must use parent elapsed time from process spawn")
    if data.get("capture") != "owned-process-stdout":
        raise ValueError("Use stdout from the owned process, not a shared application log")
    if Path(data["source_root"]).resolve() != source_root.resolve():
        raise ValueError("Evidence belongs to a different checkout")
    expected_source = (source_root / "jarvis" / "__init__.py").resolve()
    if Path(data["jarvis_file"]).resolve() != expected_source:
        raise ValueError("The observed process imported a different checkout")
    if not Path(data["python"]).is_absolute():
        raise ValueError("Record the child's absolute interpreter path")
    for relative in SOURCE_FILES:
        actual = hashlib.sha256((source_root / relative).read_bytes()).hexdigest()
        if data.get("source_sha256", {}).get(relative) != actual:
            raise ValueError(f"Evidence is stale or missing a source hash: {relative}")
    started = datetime.fromisoformat(data["started_at"])
    finished = datetime.fromisoformat(data["finished_at"])
    if started.tzinfo is None or finished.tzinfo is None or finished < started:
        raise ValueError("Ordered timezone-aware start/finish timestamps are required")
    elapsed_limit = (finished - started).total_seconds() * 1000 + 1000
    voice = data.get("voice_enabled")
    if type(voice) is not bool:
        raise ValueError("Record whether local voice was enabled for this boot")
    if not voice and data.get("voice_skip_reason") not in {
        "no-audio-device", "permission-not-granted", "explicitly-disabled"
    }:
        raise ValueError("A voice skip requires an explicit recorded reason")

    observations = data.get("observations", [])
    names = [item.get("name") for item in observations]
    required = ["shell_html_served", "app_interactive"]
    if voice:
        required.append("local_voice_usable")
    anchors: dict[str, float] = {}
    for name in required:
        if names.count(name) != 1:
            raise ValueError(f"Exactly one {name} observation is required")
        row = observations[names.index(name)]
        elapsed = _number(row.get("elapsed_ms"), name)
        if elapsed > elapsed_limit:
            raise ValueError(f"{name} falls outside the recorded capture interval")
        if name == "shell_html_served":
            if row.get("http_status") != 200 or row.get("html_verified") is not True:
                raise ValueError("Shell anchor requires a verified HTTP 200 HTML response")
        else:
            sentinel = "APP_INTERACTIVE_MS" if name == "app_interactive" else "VOICE_USABLE_MS"
            line = row.get("stdout_line", "")
            if not re.fullmatch(rf"{sentinel}=\d+(?:\.\d+)?", line):
                raise ValueError(f"{name} requires its original process stdout sentinel")
        anchors[name] = elapsed
    return {
        "median_wall_ms": anchors["shell_html_served"],
        "median_app_interactive_wall_ms": anchors["app_interactive"],
        "median_voice_usable_wall_ms": anchors.get("local_voice_usable"),
        "run_id": data["run_id"],
        "pid": data["pid"],
    }, voice
