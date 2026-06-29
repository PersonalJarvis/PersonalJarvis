"""Achievement catalog (Phase B).

Each achievement is an ``AchievementSpec`` with:

- ``id``: primary key in the ``achievements`` table — stable, case-sensitive.
- ``title`` / ``description``: user-facing labels.
- ``tier``: ``"mastery"`` (intrinsic), ``"reflection"`` (passive), ``"social"``
  (Phase D).
- ``evaluator``: callable ``(event, ctx) -> UnlockDecision | None`` — receives
  the live event plus an ``AchievementContext`` (DB + in-memory state) and
  returns ``None`` when no unlock is due, or an ``UnlockDecision`` with evidence.

## Design decisions (Plan §0 hard negatives)

- No ``daily_login_streak``/``late_night_warrior`` awards — the catalog
  rewards output, not unhealthy usage patterns.
- No time-window-specific "most today" awards — that drives status anxiety,
  which we explicitly want to avoid.
- ``ten_x_engineer`` measures weekly OpenClaw runtime; this is an output
  metric ("how much Jarvis completed autonomously"), not "the user was online
  for many hours".

## Persistence pattern

The evaluator uses ``INSERT OR IGNORE`` on ``achievements.id`` — this makes
the evaluator functions implicitly idempotent. It saves extra ``SELECT``
guards and is race-safe even under parallel event bursts.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from jarvis.core.events import Event

log = logging.getLogger(__name__)

Tier = Literal["mastery", "reflection", "social"]


@dataclass(frozen=True)
class UnlockDecision:
    """Return value of an ``evaluator`` callback that wants to unlock an achievement."""
    evidence: dict[str, Any] = field(default_factory=dict)


class AchievementContext(Protocol):
    """What an evaluator needs at runtime.

    The Protocol approach allows tests with a simple stub — no
    ``BoardAggregator`` setup needed, just inject a few numbers.
    """

    def ever_seen_tools(self) -> set[str]: ...
    def tools_for_trace(self, trace_id: str) -> set[str]: ...
    def successful_tasks_total(self) -> int: ...
    def openclaw_success_total(self) -> int: ...
    def mcp_success_total(self) -> int: ...
    def hours_saved_last_7d(self) -> float: ...
    def first_event_date_iso(self) -> str | None: ...


@dataclass(frozen=True)
class AchievementSpec:
    id: str
    title: str
    description: str
    tier: Tier
    evaluator: Callable[[Event, AchievementContext], UnlockDecision | None]


# ----------------------------------------------------------------------
# Evaluators
# ----------------------------------------------------------------------

def _eval_first_mcp(event: Event, ctx: AchievementContext) -> UnlockDecision | None:
    """Unlocks on the first successful MCP tool call.

    ``HarnessResult`` has ``exit_code`` (not ``success``) — the plan pseudo-
    code in §5-B was imprecise here. ``exit_code == 0`` is the success signal.
    """
    if type(event).__name__ != "HarnessCompleted":
        return None
    harness = getattr(event, "harness", "")
    result = getattr(event, "result", None)
    exit_code = int(getattr(result, "exit_code", -1)) if result is not None else -1
    if harness != "mcp-remote" or exit_code != 0:
        return None
    return UnlockDecision(evidence={"harness": harness})


def _eval_openclaw_summoner(event: Event, ctx: AchievementContext) -> UnlockDecision | None:
    """First successful OpenClaw spawn."""
    if type(event).__name__ != "JarvisAgentTaskCompleted":
        return None
    if not bool(getattr(event, "success", False)):
        return None
    return UnlockDecision(evidence={"openclaw_total": ctx.openclaw_success_total()})


def _make_tool_count_eval(threshold: int) -> Callable[[Event, AchievementContext], UnlockDecision | None]:
    def _eval(event: Event, ctx: AchievementContext) -> UnlockDecision | None:
        if type(event).__name__ != "ActionExecuted":
            return None
        if not bool(getattr(event, "success", False)):
            return None
        tools = ctx.ever_seen_tools()
        if len(tools) >= threshold:
            return UnlockDecision(evidence={"unique_tools": len(tools)})
        return None
    return _eval


def _eval_triple_combo(event: Event, ctx: AchievementContext) -> UnlockDecision | None:
    """3 different tools within a single ``trace_id``.

    The ``AchievementContext`` holds an LRU map trace_id→set(tool_name)
    in memory — Plan §4 "in one session". ``trace_id`` is the natural
    session equivalent in our model (one voice-turn round).
    """
    if type(event).__name__ != "ActionExecuted":
        return None
    if not bool(getattr(event, "success", False)):
        return None
    trace_id = getattr(event, "trace_id", None)
    if trace_id is None:
        return None
    tools = ctx.tools_for_trace(trace_id.hex if hasattr(trace_id, "hex") else str(trace_id))
    if len(tools) >= 3:
        return UnlockDecision(evidence={"trace_id": str(trace_id), "tools": sorted(tools)})
    return None


def _make_task_count_eval(threshold: int) -> Callable[[Event, AchievementContext], UnlockDecision | None]:
    def _eval(event: Event, ctx: AchievementContext) -> UnlockDecision | None:
        name = type(event).__name__
        if name not in ("TaskCompleted", "JarvisAgentTaskCompleted"):
            return None
        if name == "JarvisAgentTaskCompleted" and not bool(getattr(event, "success", False)):
            return None
        total = ctx.successful_tasks_total()
        if total >= threshold:
            return UnlockDecision(evidence={"successful_tasks": total})
        return None
    return _eval


def _eval_ten_x_engineer(event: Event, ctx: AchievementContext) -> UnlockDecision | None:
    """10+ OpenClaw hours in the last 7 days (output metric).

    Deliberately NOT "user was online for 10 h" — ``hours_saved_estimate``
    sums over OpenClaw runtimes during which the user was typically doing
    something else.
    """
    if type(event).__name__ != "JarvisAgentTaskCompleted":
        return None
    if ctx.hours_saved_last_7d() >= 10.0:
        return UnlockDecision(evidence={"hours_saved_7d": round(ctx.hours_saved_last_7d(), 1)})
    return None


def _eval_one_year(event: Event, ctx: AchievementContext) -> UnlockDecision | None:
    """365 days since first activity.

    The evaluator triggers on *every* event — we check ``first_event_date``
    cheaply via an in-memory cache.
    """
    first = ctx.first_event_date_iso()
    if first is None:
        return None
    from datetime import date
    try:
        first_date = date.fromisoformat(first)
    except ValueError:
        return None
    days = (date.today() - first_date).days
    if days >= 365:
        return UnlockDecision(evidence={"days_since_first": days})
    return None


# ----------------------------------------------------------------------
# Katalog
# ----------------------------------------------------------------------

ACHIEVEMENTS: list[AchievementSpec] = [
    AchievementSpec(
        id="first_mcp",
        title="Erste MCP-Verbindung",
        description="Ein MCP-Tool lief erfolgreich.",
        tier="mastery",
        evaluator=_eval_first_mcp,
    ),
    AchievementSpec(
        id="openclaw_summoner",
        title="Jarvis-Agent Summoner",
        description="Erster erfolgreicher Jarvis-Agent-Spawn.",
        tier="mastery",
        evaluator=_eval_openclaw_summoner,
    ),
    AchievementSpec(
        id="tool_dabbler",
        title="Tool-Dabbler",
        description="5 verschiedene Tools erfolgreich benutzt.",
        tier="mastery",
        evaluator=_make_tool_count_eval(5),
    ),
    AchievementSpec(
        id="tool_journeyman",
        title="Tool-Journeyman",
        description="15 verschiedene Tools erfolgreich benutzt.",
        tier="mastery",
        evaluator=_make_tool_count_eval(15),
    ),
    AchievementSpec(
        id="tool_master",
        title="Tool-Master",
        description="30 verschiedene Tools erfolgreich benutzt.",
        tier="mastery",
        evaluator=_make_tool_count_eval(30),
    ),
    AchievementSpec(
        id="triple_combo",
        title="Triple Combo",
        description="Drei verschiedene Tools in derselben Session chained.",
        tier="mastery",
        evaluator=_eval_triple_combo,
    ),
    AchievementSpec(
        id="ten_x_engineer",
        title="10x Engineer",
        description="Ueber 10 Jarvis-Agent-Stunden in den letzten 7 Tagen.",
        tier="mastery",
        evaluator=_eval_ten_x_engineer,
    ),
    AchievementSpec(
        id="centennial",
        title="Centennial",
        description="100 erfolgreiche Tasks.",
        tier="reflection",
        evaluator=_make_task_count_eval(100),
    ),
    AchievementSpec(
        id="kilo_club",
        title="Kilo Club",
        description="1000 erfolgreiche Tasks.",
        tier="reflection",
        evaluator=_make_task_count_eval(1000),
    ),
    AchievementSpec(
        id="one_year_with_jarvis",
        title="Ein Jahr mit Jarvis",
        description="365 Tage seit der ersten Aktivitaet.",
        tier="reflection",
        evaluator=_eval_one_year,
    ),
]

# ID lookup for the evaluator.
ACHIEVEMENTS_BY_ID: dict[str, AchievementSpec] = {a.id: a for a in ACHIEVEMENTS}


# ----------------------------------------------------------------------
# Trigger set — events the evaluator must subscribe to.
# ----------------------------------------------------------------------

# Which event classes can trigger an achievement at all?
# Dynamic wiring would be overkill — the set is stable and small.
TRIGGERING_EVENT_NAMES: frozenset[str] = frozenset({
    "ActionExecuted",
    "HarnessCompleted",
    "JarvisAgentTaskCompleted",
    "TaskCompleted",
})
