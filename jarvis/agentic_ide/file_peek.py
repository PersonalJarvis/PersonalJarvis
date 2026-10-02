"""A short look inside the files a brief is about to point at.

The tree index matches spoken words against file NAMES. That finds the right
neighbourhood, but a writer that has only seen names can do no better than
"the ranking logic" and cannot tell which of five similarly named files
actually holds the behaviour. A developer handing work to a colleague glances
into the two or three files that matter first; this module is that glance.

What it does, per composition, all in one worker thread and all bounded:

* **Reads the name candidates** (a small pool, never the repository) and
  re-ranks them by how many of the task's own words occur in their CONTENT, so
  the file that really implements "the wake timeout" beats the one that merely
  has "wake" in its name.
* **Excerpts the top few**: the outline (module docstring, signatures, first
  docstring lines, constants) plus the lines that mention the task's words,
  with line numbers. That is enough for the writer to name a real symbol and
  state where a value lives, without pasting the module.

How deep it looks depends on the task (``task_kind``). An investigation, a
review or a question is ABOUT the code, so the writer gets more files and more
lines; an implementation gets the entry points; an undetermined request gets a
light glance. Nothing here executes workspace code (``ast.parse`` only), every
read is size-bounded, and the whole pass carries a wall-clock deadline so a
slow network share costs excerpts, never the turn.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .code_skeleton import outline_source, read_source
from .file_index import tokenize
from .task_kind import (
    KIND_IMPLEMENT,
    KIND_INVESTIGATE,
    KIND_NEUTRAL,
    KIND_QUESTION,
    KIND_REVIEW,
)


@dataclass(frozen=True, slots=True)
class PeekPlan:
    """How far to look for one kind of task."""

    pool: int
    """Name candidates read and re-ranked by content."""
    files: int
    """How many of the re-ranked files get an excerpt."""
    chars_per_file: int
    total_chars: int
    hit_lines: int
    """Task-relevant lines quoted per file."""


# Sized against the writer's budget: every excerpt character is input the fast
# writer reads before it answers, and the spoken path waits for that answer.
#
# The pool is wide on purpose. Names alone put the right file outside a narrow
# pool: measured 2026-10-01, "why does prompting a terminal take so long in
# the agentic ide composer" ranked ``prompt_composer.py`` 24th by name, behind
# every frontend file in a folder called ``agentic``. Reading 30 bounded files
# costs about a tenth of a second; the content then decides.
_DEEP = PeekPlan(pool=30, files=4, chars_per_file=2_400, total_chars=8_000, hit_lines=8)
_PLANS: dict[str, PeekPlan] = {
    KIND_INVESTIGATE: _DEEP,
    KIND_REVIEW: _DEEP,
    KIND_QUESTION: _DEEP,
    KIND_IMPLEMENT: PeekPlan(
        pool=24, files=3, chars_per_file=2_000, total_chars=5_500, hit_lines=5
    ),
    KIND_NEUTRAL: PeekPlan(
        pool=12, files=2, chars_per_file=1_500, total_chars=3_000, hit_lines=4
    ),
}

# The whole pass, reads included. Disk on a local SSD answers in milliseconds;
# this bound exists for a network share or a cold spinning disk.
PEEK_DEADLINE_S = 0.4
# Outlining the top files after reading: measured 9-40 ms per file warm.
EXCERPT_WINDOW_S = 0.25

_MAX_LINE_CHARS = 160

# The whole outline is built before it is focused, so the task's function is
# found even at the end of a 7 000-line module. Text only, never sent as is.
_FULL_OUTLINE_CHARS = 120_000

# Files that style, configure or store data rather than behave. They mention
# every word under the sun ("index", "terminal") and are rarely where a coding
# task starts, so they rank below code unless the task names their kind.
_LOW_VALUE_SUFFIXES = frozenset(
    {".css", ".scss", ".json", ".html", ".txt", ".csv", ".svg", ".lock", ".yaml", ".yml"}
)
_LOW_VALUE_FACTOR = 0.4

# Lines that name a dependency or print a message, not behaviour — they match
# the task's words constantly ("from .wake import ...", a log line naming the
# subsystem) and tell the writer nothing.
_IMPORT_PREFIXES = (
    "import ",
    "from ",
    "#include",
    "using ",
    "require(",
    "logger.",
    "log.",
    "logging.",
    "console.",
    "print(",
)


def plan_for(kind: str) -> PeekPlan:
    """The depth for ``kind``; unknown kinds get the light glance."""
    return _PLANS.get(kind, _PLANS[KIND_NEUTRAL])


@dataclass(slots=True)
class Peek:
    """What the glance found."""

    ranked: list[str] = field(default_factory=list)
    """The candidates, best first, after looking inside them."""
    excerpts: dict[str, str] = field(default_factory=dict)
    """Path → outline plus task-relevant lines, for the top files only."""


def _hit_lines(source: str, wanted: set[str], limit: int) -> list[str]:
    """Up to ``limit`` numbered lines that mention the most task words."""
    if limit <= 0 or not wanted:
        return []
    scored: list[tuple[int, int, str]] = []
    for number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(_IMPORT_PREFIXES):
            continue
        lowered = stripped.lower()
        hits = sum(1 for token in wanted if token in lowered)
        if hits:
            scored.append((hits, number, stripped[:_MAX_LINE_CHARS]))
    best = sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]
    return [f"L{number}: {text}" for _hits, number, text in sorted(best, key=lambda i: i[1])]


def _focused_outline(outline: str, wanted: set[str], room: int) -> str:
    """``outline`` cut to ``room``, keeping the declarations the task names.

    A plain cut keeps the head of a big module — its constants — and drops
    the function the task is about, which sits a thousand lines further down.
    So the outline is split into entries (a declaration plus its docstring
    line), the module docstring and every entry naming a task word are kept
    first, and the rest fill whatever room is left, all in file order.
    """
    if len(outline) <= room:
        return outline
    if room <= 0:
        return ""
    entries: list[str] = []
    for line in outline.splitlines():
        if entries and line.lstrip().startswith('"""') and not entries[-1].startswith('"""'):
            entries[-1] += "\n" + line
        else:
            entries.append(line)

    def _hits(entry: str) -> int:
        lowered = entry.lower()
        return sum(1 for token in wanted if token in lowered)

    # Bare constants (``NAME = ...``) say little unless the task names them.
    order = sorted(
        range(len(entries)),
        key=lambda i: (
            i != 0,
            -_hits(entries[i]),
            entries[i].rstrip().endswith("= ..."),
            i,
        ),
    )
    kept: set[int] = set()
    used = 0
    for i in order:
        cost = len(entries[i]) + 1
        if used + cost > room:
            continue
        kept.add(i)
        used += cost
    return "\n".join(entries[i] for i in sorted(kept))


def _excerpt(rel: str, source: str, wanted: set[str], plan: PeekPlan, budget: int) -> str:
    """Outline first, then the lines that mention the task, within ``budget``."""
    cap = min(plan.chars_per_file, budget)
    if cap <= 0:
        return ""
    lines = _hit_lines(source, wanted, plan.hit_lines)
    hits_block = ("\nLines mentioning the task:\n" + "\n".join(lines)) if lines else ""
    # The relevant lines are the part only a look inside can give, so they keep
    # their room and the outline takes what is left.
    room = max(cap - len(hits_block), 0)
    full = outline_source(rel, source, max_chars=_FULL_OUTLINE_CHARS)
    outline = _focused_outline(full, wanted, room)
    return (outline + hits_block).strip()[:cap]


def peek(
    root: str,
    instruction: str,
    candidates: Sequence[str],
    kind: str,
    *,
    deadline_s: float = PEEK_DEADLINE_S,
) -> Peek:
    """Look inside ``candidates`` for ``instruction``. Blocking; never raises.

    Run it in a worker thread. ``candidates`` arrive name-ranked from the file
    index; the result keeps that order as a prior and lets content decide
    between files the names could not separate.
    """
    plan = plan_for(kind)
    pool = list(dict.fromkeys(candidates))[: plan.pool]
    if not pool:
        return Peek()
    wanted = tokenize(instruction)
    stop_at = time.monotonic() + max(deadline_s, 0.0)

    sources: dict[str, str] = {}
    present: dict[str, set[str]] = {}
    for rel in pool:
        if time.monotonic() >= stop_at:
            break
        try:
            source = read_source(root, rel)
        except Exception:  # noqa: BLE001 - a broken file costs its excerpt only
            source = ""
        if not source:
            continue
        sources[rel] = source
        lowered = source.lower()
        present[rel] = {token for token in wanted if token in lowered}

    # A word every file in the pool contains separates nothing; a word only
    # two files contain is what the task is actually about.
    seen_in = {token: sum(1 for hits in present.values() if token in hits) for token in wanted}
    named_kinds = {token.lstrip(".") for token in wanted}
    scores: dict[str, float] = {}
    for position, rel in enumerate(pool):
        if rel not in sources:
            continue
        content = sum(math.log(1 + len(sources) / seen_in[token]) for token in present[rel])
        # Name order is a real signal (the user often names the file), so it
        # stays as a prior worth about one rare content word.
        prior = 1.5 * (len(pool) - position) / len(pool)
        score = content + prior
        suffix = PurePosixPath(rel).suffix.lower()
        if suffix in _LOW_VALUE_SUFFIXES and suffix.lstrip(".") not in named_kinds:
            score *= _LOW_VALUE_FACTOR
        scores[rel] = score

    read = sorted(sources, key=lambda rel: -scores[rel])
    unread = [rel for rel in pool if rel not in sources]
    result = Peek(ranked=read + unread)

    # Excerpting is CPU on text already in memory. A cold disk that ate the
    # read deadline must not also cost every excerpt — that left a brief with
    # the right files and nothing seen inside them — so it gets its own window.
    excerpt_stop = max(stop_at, time.monotonic() + EXCERPT_WINDOW_S)
    remaining = plan.total_chars
    for rel in read[: plan.files]:
        if remaining <= 0 or time.monotonic() >= excerpt_stop:
            break
        try:
            text = _excerpt(rel, sources[rel], wanted, plan, remaining)
        except Exception:  # noqa: BLE001 - a parser edge case costs one excerpt
            text = ""
        if text:
            result.excerpts[rel] = text
            remaining -= len(text)
    return result


__all__ = ["EXCERPT_WINDOW_S", "PEEK_DEADLINE_S", "Peek", "PeekPlan", "peek", "plan_for"]
