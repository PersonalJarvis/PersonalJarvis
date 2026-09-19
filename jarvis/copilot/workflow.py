"""Workflow: observe which apps the user works in, then propose automation.

Observation is explicit (start/stop command) and minimal: every few seconds the
foreground app and window title, filtered by the awareness privacy filter
(blocked apps and browsers keep only the app name). No keystrokes, no clicks,
no screenshots. Samples stay in memory; stopping analyses them and writes only
the report to ``<user_data_dir>/workflow/``. Nothing is automated: candidates
are proposals, building one is a separate, user-approved step.
"""

from __future__ import annotations

import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis.core.paths import user_data_dir

POLL_S = 3.0
MAX_HOURS = 8.0
MIN_REPEATS = 2


@dataclass
class Segment:
    app: str
    title: str
    start: float
    end: float

    @property
    def seconds(self) -> float:
        return self.end - self.start


def _foreground() -> tuple[str, str] | None:
    """(process name, window title) of the foreground window; None off Windows."""
    import sys

    if sys.platform != "win32":
        return None
    import ctypes  # noqa: PLC0415

    import psutil  # noqa: PLC0415

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        name = psutil.Process(pid.value).name()
    except Exception:  # noqa: BLE001 - a vanished process is just skipped
        return None
    return name, buf.value


class Observer:
    """Polls the foreground window on a daemon thread until stopped."""

    def __init__(self, privacy: Any = None, poll_s: float = POLL_S) -> None:
        self._privacy = privacy
        self._poll_s = poll_s
        self.segments: list[Segment] = []
        self.started = time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="jarvis-workflow-observe", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> list[Segment]:
        self._stop.set()
        self._thread.join(timeout=self._poll_s + 2)
        return self.segments

    def add(self, app: str, title: str, now: float) -> None:
        if self._privacy is not None:
            allowed, _ = self._privacy.is_allowed(window_title=title, process_name=app)
            if not allowed:
                title = ""
        last = self.segments[-1] if self.segments else None
        if last and last.app == app and last.title == title and now - last.end <= self._poll_s * 3:
            last.end = now
        else:
            self.segments.append(Segment(app, title, now, now))

    def _run(self) -> None:
        deadline = self.started + MAX_HOURS * 3600
        while not self._stop.is_set() and time.time() < deadline:
            fg = _foreground()
            if fg is not None:
                self.add(fg[0], fg[1], time.time())
            self._stop.wait(self._poll_s)


def _norm_app(app: str) -> str:
    return re.sub(r"\.exe$", "", app, flags=re.I).lower()


@dataclass
class Candidate:
    steps: tuple[str, ...]
    repeats: int
    seconds_each: float
    approach: str

    @property
    def total_seconds(self) -> float:
        return self.repeats * self.seconds_each


_APPROACH = {
    "excel": "a Python/PowerShell script for the spreadsheet steps (openpyxl / Excel COM)",
    "powerpnt": "the Material deck generator or python-pptx",
    "explorer": "a PowerShell script for the file moves/renames",
    "outlook": "an Outlook rule or a mail draft template (sending stays manual)",
    "code": "a CLI task or script in the project",
    "windowsterminal": "a saved PowerShell/CLI script",
    "powershell": "a saved PowerShell script",
}


def _approach(steps: tuple[str, ...]) -> str:
    for app in steps:
        for key, how in _APPROACH.items():
            if key in app:
                return how
    return "a script via the app's API or CLI; UI automation only as a last resort"


@dataclass
class Analysis:
    observed_s: float
    by_app: list[tuple[str, float]]
    candidates: list[Candidate] = field(default_factory=list)


def analyse(segments: list[Segment], min_repeats: int = MIN_REPEATS) -> Analysis:
    """Time per app, and repeated app sequences (length 2-4) as candidates."""
    segs = [s for s in segments if s.seconds >= 0]
    observed = (segs[-1].end - segs[0].start) if segs else 0.0
    per_app: Counter[str] = Counter()
    for s in segs:
        per_app[_norm_app(s.app)] += max(s.seconds, 1.0)
    apps = []
    for s in segs:
        a = _norm_app(s.app)
        if not apps or apps[-1][0] != a:
            apps.append([a, max(s.seconds, 1.0)])
        else:
            apps[-1][1] += max(s.seconds, 1.0)
    found: dict[tuple[str, ...], list[float]] = {}
    for n in (4, 3, 2):
        for i in range(len(apps) - n + 1):
            key = tuple(a for a, _ in apps[i : i + n])
            if len(set(key)) < 2:
                continue
            found.setdefault(key, []).append(sum(sec for _, sec in apps[i : i + n]))
    cands: list[Candidate] = []
    for key, times in sorted(found.items(), key=lambda kv: (-len(kv[1]), -len(kv[0]))):
        if len(times) < min_repeats:
            continue
        if any(set(key) <= set(c.steps) and c.repeats >= len(times) for c in cands):
            continue  # already covered by a longer sequence seen as often
        cands.append(Candidate(key, len(times), sum(times) / len(times), _approach(key)))
    cands.sort(key=lambda c: -c.total_seconds)
    return Analysis(observed, per_app.most_common(8), cands[:5])


_TEXT = {
    "en": {
        "observed": "Observed {t}.",
        "per_app": "Time per app:",
        "cands": "Automation candidates (proposals only, nothing was automated):",
        "none": "- none yet: no sequence repeated in this observation",
        "cand": "- {steps}: {n}x, ~{each} each ({total} total). Suggested: {how}.",
        "min": "{n} min",
        "sec": "{n} s",
    },
    "ja": {
        "observed": "\u89b3\u5bdf\u6642\u9593\uff1a{t}\u3002",
        "per_app": "\u30a2\u30d7\u30ea\u3054\u3068\u306e\u6642\u9593\uff1a",
        "cands": (
            "\u81ea\u52d5\u5316\u306e\u5019\u88dc\uff08\u63d0\u6848\u306e\u307f\u3002"
            "\u4f55\u3082\u81ea\u52d5\u5316\u3057\u3066\u3044\u307e\u305b\u3093\uff09"
            "\uff1a"
        ),
        "none": (
            "- \u307e\u3060\u3042\u308a\u307e\u305b\u3093\uff1a\u3053\u306e\u89b3"
            "\u5bdf\u3067\u306f\u304f\u308a\u8fd4\u3057\u306e\u6d41\u308c\u306f\u898b"
            "\u3064\u304b\u308a\u307e\u305b\u3093\u3067\u3057\u305f"
        ),
        "cand": (
            "- {steps}\uff1a{n}\u56de\u30011\u56de\u3042\u305f\u308a\u7d04{each}"
            "\uff08\u5408\u8a08{total}\uff09\u3002\u65b9\u6cd5\u306e\u6848\uff1a{how}"
            "\u3002"
        ),
        "min": "{n}\u5206",
        "sec": "{n}\u79d2",
    },
}


def report(a: Analysis, lang: str = "en") -> str:
    tx = _TEXT.get(lang, _TEXT["en"])

    def m(sec: float) -> str:
        if sec >= 60:
            return tx["min"].format(n=f"{sec / 60:.0f}")
        return tx["sec"].format(n=f"{sec:.0f}")

    lines = [tx["observed"].format(t=m(a.observed_s)), "", tx["per_app"]]
    lines += [f"- {app}: {m(sec)}" for app, sec in a.by_app]
    lines += ["", tx["cands"]]
    if not a.candidates:
        lines.append(tx["none"])
    for c in a.candidates:
        lines.append(
            tx["cand"].format(
                steps=" -> ".join(c.steps),
                n=c.repeats,
                each=m(c.seconds_each),
                total=m(c.total_seconds),
                how=c.approach,
            )
        )
    return "\n".join(lines)


def save_report(text: str) -> Path:
    path = user_data_dir() / "workflow"
    path.mkdir(parents=True, exist_ok=True)
    out = path / f"{datetime.now().strftime('%Y%m%d-%H%M')}-report.md"
    out.write_text(text, encoding="utf-8")
    return out


__all__ = ["Analysis", "Candidate", "Observer", "Segment", "analyse", "report", "save_report"]
