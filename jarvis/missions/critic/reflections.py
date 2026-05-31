"""Reflection memory for the Worker-Critic loop.

Pattern from Reflexion (Shinn et al., NeurIPS 2023): the last N Critic
verdicts are persisted as episodic memory and prepended to the next worker
prompt. The worker reads them on resume — it knows what has already failed
and does not repeat it.

Layout decision (Plan §"Decision 2"):
- `reflections.md` lives in the **mission root**, NOT in the task workspace.
- Mission root: `sub-agents-outputs/<run-dir>/`
- Task workspace: `sub-agents-outputs/<run-dir>/tasks/<NN>__<slug>/workspace/`
- Worker cwd is the task workspace; relative path to the mission root is
  `../../reflections.md`.

Format is Markdown (human-readable for forensics):

```markdown
## Iteration 0 — 2026-04-26T15:42:11Z
**Summary:** Worker hat is_palindrome implementiert aber empty-string-Edge-Case fehlt.
**Evidence:**
- src/palindrome.py:7
- test_palindrome.py:14
```
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


logger = logging.getLogger(__name__)


REFLECTIONS_FILENAME = "reflections.md"
DEFAULT_LAST_N = 3


class Reflection(BaseModel):
    """A single reflection entry (one Critic iteration)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    iteration: int
    ts_iso: str
    summary: str
    evidence: list[str] = Field(default_factory=list)


def reflections_path_for_mission(mission_dir: Path) -> Path:
    """Path to `reflections.md` for a given mission."""
    return mission_dir / REFLECTIONS_FILENAME


def reflections_path_for_worker(worktree: Path) -> Path:
    """Path to `reflections.md` from a worker's perspective in the task workspace.

    Worker cwd: `<mission_dir>/tasks/<NN>__<slug>/workspace/`
    Reflections: `<mission_dir>/reflections.md`
    Relative ascent: workspace -> task-dir -> tasks -> mission-root.
    """
    return (worktree / ".." / ".." / ".." / REFLECTIONS_FILENAME).resolve()


# Markdown block header pattern for last_n parsing (backwards).
_HEADER_RE = re.compile(
    r"^## Iteration (?P<iter>\d+) — (?P<ts>[^\n]+)$",
    re.MULTILINE,
)


class ReflectionMemory:
    """File-backed reflection memory for a mission.

    `append()` is write-then-fsync (small files; no lock needed because
    the mission orchestrator is single-threaded per mission). `last_n()` parses
    the Markdown sections backwards.
    """

    def __init__(self, mission_dir: Path) -> None:
        self._path = reflections_path_for_mission(mission_dir)
        # Create the mission root if it does not yet exist (the orchestrator
        # normally does this on mission start, but be defensive).
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        iteration: int,
        summary: str,
        evidence: list[str] | None = None,
    ) -> None:
        """Append a reflection block to the end of the file."""
        ts_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        evidence = evidence or []

        block_lines: list[str] = [
            "",
            f"## Iteration {iteration} — {ts_iso}",
            f"**Summary:** {summary.strip()}",
        ]
        if evidence:
            block_lines.append("**Evidence:**")
            for ev in evidence:
                block_lines.append(f"- {ev}")
        block_lines.append("")

        block = "\n".join(block_lines)

        # Append + fsync — reflections must survive a crash-restart.
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(block)
            fh.flush()
            try:
                import os  # noqa: PLC0415

                os.fsync(fh.fileno())
            except OSError:
                logger.debug("fsync(reflections.md) skipped (filesystem may not support it)")

    def last_n(self, n: int = DEFAULT_LAST_N) -> list[Reflection]:
        """Return the last n reflection entries (most recent last).

        When n exceeds the number of existing entries, all are returned.
        Returns [] when the file does not exist or is empty.
        """
        if not self._path.exists():
            return []
        text = self._path.read_text(encoding="utf-8")
        if not text.strip():
            return []

        # Find header positions, then take n from the end.
        headers = list(_HEADER_RE.finditer(text))
        if not headers:
            return []

        wanted = headers[-n:] if n > 0 else []

        out: list[Reflection] = []
        for i, m in enumerate(wanted):
            block_start = m.start()
            # Block end = start of the next header OR EOF.
            try:
                # next header in the complete list
                global_idx = headers.index(m)
                block_end = (
                    headers[global_idx + 1].start()
                    if global_idx + 1 < len(headers)
                    else len(text)
                )
            except ValueError:
                block_end = len(text)

            block = text[block_start:block_end]
            iteration = int(m.group("iter"))
            ts_iso = m.group("ts").strip()

            summary = ""
            evidence: list[str] = []
            for line in block.splitlines():
                if line.startswith("**Summary:**"):
                    summary = line[len("**Summary:**") :].strip()
                elif line.startswith("- "):
                    evidence.append(line[2:].strip())

            out.append(
                Reflection(
                    iteration=iteration,
                    ts_iso=ts_iso,
                    summary=summary,
                    evidence=evidence,
                )
            )

        return out

    def render_for_worker_prompt(self, n: int = DEFAULT_LAST_N) -> str:
        """Render the last n reflections as a plain-text block for the worker.

        Prepended to the worker prompt. Returns an empty string when memory is
        empty (no "no prior reflections" boilerplate — saves tokens).
        """
        last = self.last_n(n)
        if not last:
            return ""

        lines: list[str] = ["Prior Critic Feedback (last {} iterations):".format(len(last))]
        for refl in last:
            lines.append("")
            lines.append(f"[Iteration {refl.iteration}] {refl.summary}")
            if refl.evidence:
                lines.append("  Evidence: " + "; ".join(refl.evidence))
        return "\n".join(lines)


__all__ = [
    "DEFAULT_LAST_N",
    "REFLECTIONS_FILENAME",
    "Reflection",
    "ReflectionMemory",
    "reflections_path_for_mission",
    "reflections_path_for_worker",
]
