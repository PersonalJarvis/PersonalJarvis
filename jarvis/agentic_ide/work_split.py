"""Turn ONE order into N distinct assignments, one per coding agent.

Handing five agents the same sentence is not a fan-out. They open the same
files, reach five overlapping conclusions, and burn five subscriptions doing
one agent's work — and on a shared worktree they will also fight over the same
edits. What the maintainer asked for on 2026-07-26 is the other thing: "split
the deep dive across areas and say exactly what each one has to do."

That is a judgement call about a specific repository, so it is a model call —
but one that must degrade all the way down, because a downloader whose only
credential is for some other provider still has to be able to run a fleet (§3).
Two layers, same shape as ``prompt_composer``:

1. **Model split** — one bounded call to ``resolve_quality_brain`` returning
   JSON. Deliberately not the full frontier chain: that chain ends in small
   fast models so a core path never dies, and a split written by one of those
   reads perfectly plausible while cutting the repository along seams that do
   not exist.
2. **Deterministic split** — by top-level directory, round-robin. Crude, but it
   is grounded in what is actually on disk and it never overlaps, which is the
   property that matters most.

The validation is stricter than "did it parse", because both ways of getting
this wrong are silent. Too few assignments means an agent sits idle while the
user was told the fleet is working — the partial-delivery failure this whole
change set exists to stop. Duplicate areas means two agents racing on one file.
Either one sends the whole answer to the deterministic layer rather than
shipping a fleet that looks briefed and is not.

The assignments are handed to ``fanout.deliver`` as its ``assignments`` map;
each pane's prompt is then composed from ITS slice, so the per-agent brief still
gets the file references and repository conventions the composer adds.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

#: Bound for the one splitting call. Shorter than the composer's: this produces
#: a short JSON plan rather than a full brief, and it runs BEFORE any prompt is
#: composed — every second here is a second added in front of N compositions.
SPLIT_TIMEOUT_S = 25.0

#: A fleet larger than this is not a split any model plans usefully, and the
#: deterministic layer runs out of real directories long before it. Above the
#: cap the areas repeat by index, which stays honest (each agent still gets its
#: own slice) without pretending the repository has 40 seams.
MAX_PLANNED_AREAS = 24


@dataclass(frozen=True, slots=True)
class Assignment:
    """One agent's slice of the order."""

    area: str
    """Short label of what this agent owns ("the wake pipeline")."""

    task: str
    """What this agent must do — always carries the original goal, because a
    slice without the goal is an agent doing something adjacent."""

    files: tuple[str, ...] = ()
    done_when: str = ""


@dataclass(frozen=True, slots=True)
class WorkSplit:
    """The plan for a fleet, plus how it came to be."""

    assignments: tuple[Assignment, ...] = ()
    split_by: str = "fallback"
    """``llm`` when a model planned it, ``fallback`` when the directory split
    did. Reported rather than hidden: a deterministic split is a coarser plan
    and the user is entitled to know which one their fleet is running."""

    note: str = ""
    """Why the fallback was used, when it was. Empty on the happy path."""


def _resolve_splitter() -> Any:
    """The model that plans a split, or None when only fast tiers are up."""
    from jarvis.brain.resolver import resolve_quality_brain
    from jarvis.core.config import load_config

    try:
        return resolve_quality_brain(load_config())
    except Exception:  # noqa: BLE001 - resolution must never break a turn
        logger.info("Agentic IDE work splitter could not be resolved", exc_info=True)
        return None


_SYSTEM_PROMPT = (
    "You divide one software task across several coding agents working in the "
    "SAME repository at the same time.\n\n"
    "Return ONLY a JSON array, one object per agent, with these keys:\n"
    '  "area"      - two to five words naming the part of the repository this '
    "agent owns\n"
    '  "task"      - one paragraph telling THIS agent what to do, restating '
    "the overall goal so it can work without seeing the others\n"
    '  "files"     - repo-relative paths from the list you were given, or []\n'
    '  "done_when" - one sentence describing this agent\'s finished state\n\n'
    "Rules:\n"
    "- Exactly as many objects as there are agents. Never fewer.\n"
    "- The areas must not overlap. Two agents editing the same files is the "
    "failure this split exists to prevent.\n"
    "- Slice along real seams of THIS repository, using the directories and "
    "files you were given. Never invent a path.\n"
    "- Together the areas must cover the whole goal; a part nobody owns is a "
    "part nobody does.\n"
    "- No prose, no code fence, no commentary. The array only."
)


def _user_block(
    *, instruction: str, count: int, folder: str, areas: list[str], profile: list[str]
) -> str:
    lines = [
        f"Overall goal: {instruction}",
        f"Number of agents: {count}",
        f"Repository: {Path(folder).name}",
    ]
    if profile:
        lines.append("Project: " + "; ".join(profile[:4]))
    if areas:
        lines.append("Top-level directories: " + ", ".join(areas))
    return "\n".join(lines)


async def _llm_split(*, brain: Any, system_prompt: str, user_block: str) -> str:
    """One bounded call that plans the division of labour."""
    from jarvis.core.protocols import BrainMessage, BrainRequest

    request = BrainRequest(
        messages=(BrainMessage(role="user", content=user_block),),
        system=system_prompt,
        # Planning, not writing: the same repository and the same goal should
        # produce the same division of labour twice in a row.
        temperature=0.1,
        # Headroom for the thinking budget as well as the answer, same reason
        # as the composer: a plan cut off mid-array validates as malformed and
        # throws away the whole call.
        max_tokens=4000,
        stream=True,
        reasoning_effort="medium",
    )
    chunks: list[str] = []
    async for delta in brain.complete(request):
        if delta.content:
            chunks.append(delta.content)
    return "".join(chunks).strip()


def _strip_fence(text: str) -> str:
    """Unwrap a fenced block. Models add one far too often to treat as failure."""
    stripped = (text or "").strip()
    fence = re.match(r"^```[a-zA-Z]*\s*\n(.*?)\n?```$", stripped, re.DOTALL)
    return fence.group(1).strip() if fence else stripped


def _parse(payload: str, *, count: int, instruction: str) -> list[Assignment] | None:
    """Validated assignments, or None when the answer cannot be trusted.

    Returning None rather than repairing the answer is deliberate. A plan that
    is short by one agent, or that gives two agents the same area, is not a
    plan with a small flaw — it is a fleet that will either idle or collide, and
    the deterministic split is strictly better than either.
    """
    try:
        data = json.loads(_strip_fence(payload))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list) or len(data) != count:
        return None

    out: list[Assignment] = []
    seen_areas: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            return None
        area = str(item.get("area") or "").strip()
        task = str(item.get("task") or "").strip()
        if not area or not task:
            return None
        key = area.casefold()
        if key in seen_areas:
            return None
        seen_areas.add(key)
        raw_files = item.get("files")
        files = tuple(
            str(f).strip()
            for f in (raw_files if isinstance(raw_files, list) else [])
            if str(f).strip()
        )
        out.append(
            Assignment(
                area=area,
                task=task,
                files=files,
                done_when=str(item.get("done_when") or "").strip(),
            )
        )
    return out


def _top_level_areas(folder: str) -> list[str]:
    """Directories of the workspace worth handing to an agent.

    Reuses the same skip list the file index walks with, so a fleet is never
    pointed at ``node_modules`` or ``.git``.
    """
    from .folders import _SKIP_DIRS

    try:
        root = Path(folder).expanduser()
        names = sorted(
            entry.name
            for entry in root.iterdir()
            if entry.is_dir()
            and entry.name not in _SKIP_DIRS
            and not entry.name.startswith(".")
        )
    except OSError:
        return []
    return names


def _fallback(instruction: str, *, folder: str, count: int, note: str) -> WorkSplit:
    """Split by top-level directory, round-robin, without a model.

    Coarse on purpose. What it guarantees is what matters when nothing better
    is available: every agent gets a slice, no two get the same one, and every
    slice names a directory that actually exists.
    """
    areas = _top_level_areas(folder)
    assignments: list[Assignment] = []
    for index in range(count):
        if areas:
            # Round-robin so a repo with fewer directories than agents still
            # gives everyone something: the second pass adds a distinct angle
            # rather than a duplicate area.
            owned = areas[index % len(areas)]
            pass_number = index // len(areas)
            area = owned if pass_number == 0 else f"{owned} (pass {pass_number + 1})"
            where = f"Focus on the `{owned}` part of the repository."
        else:
            # Nothing readable on disk. Numbered slices keep the contract (one
            # distinct assignment per agent) and the prompt composer still
            # grounds each one in real files.
            area = f"part {index + 1} of {count}"
            where = (
                f"You are agent {index + 1} of {count}. Take the "
                f"{_ordinal(index + 1)} part of the work and agree the "
                "boundary with the others by what you find."
            )
        assignments.append(
            Assignment(
                area=area,
                task=(
                    f"{instruction}\n\n{where} Stay inside it: the other agents "
                    "are working on the rest of this repository at the same "
                    "time, so anything outside your area is theirs."
                ),
            )
        )
    return WorkSplit(
        assignments=tuple(assignments), split_by="fallback", note=note
    )


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


async def split(
    instruction: str,
    *,
    session: Any,
    count: int,
    brain: Any = None,
    timeout_s: float = SPLIT_TIMEOUT_S,
) -> WorkSplit:
    """Divide ``instruction`` across ``count`` agents.

    ``brain`` pins the planning model explicitly; left None a quality-tier model
    is resolved and the deterministic split takes over when none is reachable.

    Never raises. Every failure path lands on the directory split, because
    "the fleet got a coarser plan" is a far better outcome than "your five
    agents did nothing".
    """
    wanted = int(count)
    if wanted <= 0:
        return WorkSplit()

    said = (instruction or "").strip()
    folder = str(getattr(session, "folder", "") or "")

    if wanted == 1:
        # Splitting one way is not splitting. Saving the provider call here is
        # not just thrift: the single-agent path is the common one, and it must
        # not gain the latency of a planning round it cannot use.
        return WorkSplit(
            assignments=(Assignment(area="the whole task", task=said),),
            split_by="single",
        )

    if wanted > MAX_PLANNED_AREAS:
        logger.info(
            "Agentic IDE work split: {} agents is past the planning cap ({}) "
            "— splitting deterministically",
            wanted,
            MAX_PLANNED_AREAS,
        )
        return _fallback(
            said, folder=folder, count=wanted, note="fleet larger than the planning cap"
        )

    planner = brain if brain is not None else _resolve_splitter()
    if planner is None:
        return _fallback(
            said,
            folder=folder,
            count=wanted,
            note="no quality-tier provider reachable",
        )

    profile_lines: list[str] = []
    try:
        profile = getattr(session, "profile", None)
        if profile is not None:
            profile_lines = list(profile.summary_lines())
    except Exception:  # noqa: BLE001 - a profile is context, never required
        profile_lines = []

    try:
        areas = await asyncio.to_thread(_top_level_areas, folder)
        payload = await asyncio.wait_for(
            _llm_split(
                brain=planner,
                system_prompt=_SYSTEM_PROMPT,
                user_block=_user_block(
                    instruction=said,
                    count=wanted,
                    folder=folder,
                    areas=areas,
                    profile=profile_lines,
                ),
            ),
            timeout=timeout_s,
        )
    except TimeoutError:
        return _fallback(
            said,
            folder=folder,
            count=wanted,
            note=f"the planner timed out after {timeout_s:g}s",
        )
    except Exception as exc:  # noqa: BLE001 - any provider failure degrades
        logger.info("Agentic IDE work split fell back: {}", exc)
        return _fallback(
            said,
            folder=folder,
            count=wanted,
            note=f"the planner was unavailable ({type(exc).__name__})",
        )

    parsed = _parse(payload, count=wanted, instruction=said)
    if parsed is None:
        logger.info(
            "Agentic IDE work split: planner answer rejected "
            "(wanted {} distinct assignments) — splitting deterministically",
            wanted,
        )
        return _fallback(
            said,
            folder=folder,
            count=wanted,
            note="the planner's answer was incomplete or overlapping",
        )

    logger.info(
        "Agentic IDE work split: {} areas planned ({})",
        len(parsed),
        ", ".join(a.area for a in parsed),
    )
    return WorkSplit(assignments=tuple(parsed), split_by="llm")


__all__ = [
    "MAX_PLANNED_AREAS",
    "SPLIT_TIMEOUT_S",
    "Assignment",
    "WorkSplit",
    "split",
]
