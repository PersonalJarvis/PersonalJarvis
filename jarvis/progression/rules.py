"""The Jarvis Verse level system's rulebook: pure tables, no I/O.

Three kinds of subject level up:

* ``person`` — the person at the keyboard (one subject, id ``person``);
* ``agent`` — every Jarvis agent on the society roster (``agent:<agent_id>``);
* ``pet`` — the person's pet from My Pets, which IS Jarvis in the Verse
  (``pet:<pet_id>``; each pet keeps its own level, the active one earns).

XP only ever comes from a rule in :data:`RULES`. A rule names its source, the
subject kind it pays, a fixed amount, an optional cooldown and an optional
daily cap. Server rules are read off real bus events (a finished turn, a
finished task); world rules are small actions inside the Verse that the client
reports and the server still meters. Nothing here calls a model — the
system works with any single key and costs no tokens (AP-21).

The level curve is front-loaded the way long-running progression usually is:
level 2 arrives within the first few actions, level 10 after about a week of
ordinary use, level 50 after several months. Every level pays a cosmetic,
a frame or a new title band at a fixed milestone (:data:`REWARDS`,
:data:`TITLES`). ``docs/agent-society/level-system.md`` is the readable
version of this file; the parity test pins the frontend's copy of the ids.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

SubjectKind = Literal["person", "agent", "pet"]
SUBJECT_KINDS: Final[tuple[SubjectKind, ...]] = ("person", "agent", "pet")

#: The id of the one person subject.
PERSON_ID: Final[str] = "person"

MAX_LEVEL: Final[int] = 50

#: Society roster id of the lead. The lead is drawn as the person's pet, so its
#: work pays the pet, never an ``agent:jarvis`` subject.
LEAD_AGENT_ID: Final[str] = "jarvis"


def xp_to_next(level: int) -> int:
    """XP needed to climb from ``level`` to ``level + 1`` (0 at the cap).

    40 XP for the first step, then a gently super-linear rise, rounded to 5
    so the numbers read cleanly in the HUD.
    """
    if level >= MAX_LEVEL:
        return 0
    if level < 1:
        level = 1
    raw = 40 + 25 * (level - 1) ** 1.1
    return int(round(raw / 5.0) * 5)


def total_xp_for_level(level: int) -> int:
    """Total XP at which ``level`` is reached (level 1 = 0 XP)."""
    level = max(1, min(MAX_LEVEL, level))
    return sum(xp_to_next(step) for step in range(1, level))


def level_for_xp(total_xp: int) -> int:
    level = 1
    remaining = max(0, int(total_xp))
    while level < MAX_LEVEL:
        need = xp_to_next(level)
        if remaining < need:
            break
        remaining -= need
        level += 1
    return level


@dataclass(frozen=True, slots=True)
class LevelProgress:
    level: int
    xp: int
    #: XP gathered since the current level started.
    into_level: int
    #: XP the current level needs in total (0 at the cap).
    for_next: int


def progress_for_xp(total_xp: int) -> LevelProgress:
    total = max(0, int(total_xp))
    level = level_for_xp(total)
    into = total - total_xp_for_level(level)
    return LevelProgress(level=level, xp=total, into_level=into, for_next=xp_to_next(level))


# ------------------------------------------------------------------ XP rules

Trigger = Literal["server", "world"]


@dataclass(frozen=True, slots=True)
class XpRule:
    source: str
    kind: SubjectKind
    xp: int
    trigger: Trigger
    #: Minimum seconds between two awards of this rule to one subject.
    cooldown_s: int = 0
    #: Most XP one subject can earn from this rule per local day (0 = no cap).
    daily_cap: int = 0
    #: True: at most one award per ``ref`` ever (a floor is discovered once).
    once_per_ref: bool = False


RULES: Final[tuple[XpRule, ...]] = (
    # The person: talking to Jarvis, giving the team work, growing the team.
    XpRule("chat_turn", "person", 5, "server", daily_cap=100),
    XpRule("voice_turn", "person", 5, "server", daily_cap=100),
    XpRule("quest_posted", "person", 15, "server", daily_cap=75),
    XpRule("quest_completed", "person", 20, "server"),
    XpRule("agent_hired", "person", 50, "server"),
    XpRule("mission_completed", "person", 30, "server", daily_cap=150),
    # The person, inside the Verse.
    XpRule("daily_visit", "person", 25, "world", once_per_ref=True),
    XpRule("floor_discovered", "person", 20, "world", once_per_ref=True),
    XpRule("dog_petted", "person", 3, "world", cooldown_s=60, daily_cap=30),
    XpRule("dog_treat", "person", 10, "world", cooldown_s=300, daily_cap=30),
    XpRule("arcade_round", "person", 8, "world", cooldown_s=45, daily_cap=80),
    XpRule("arcade_record", "person", 15, "world", cooldown_s=45, daily_cap=60),
    XpRule("team_meeting", "person", 10, "world", cooldown_s=600, daily_cap=40),
    # Agents: finished work is what counts.
    XpRule("task_done", "agent", 40, "server"),
    XpRule("task_blocked", "agent", 8, "server", daily_cap=40),
    XpRule("quest_done", "agent", 60, "server"),
    XpRule("answered_teammate", "agent", 5, "server", daily_cap=50),
    # The pet is Jarvis: every answer and every hand-off it makes, and the
    # walks it takes beside the person.
    XpRule("jarvis_answered", "pet", 4, "server", daily_cap=120),
    XpRule("delegated", "pet", 10, "server", daily_cap=100),
    XpRule("walk_together", "pet", 3, "world", cooldown_s=20, daily_cap=60),
)

RULES_BY_SOURCE: Final[dict[str, XpRule]] = {rule.source: rule for rule in RULES}

#: Sources the client may report through ``POST /api/progression/actions``.
WORLD_ACTIONS: Final[frozenset[str]] = frozenset(r.source for r in RULES if r.trigger == "world")


# ------------------------------------------------------------------ rewards

Slot = Literal["frame", "trail", "aura", "gadget"]
SLOTS: Final[tuple[Slot, ...]] = ("frame", "trail", "aura", "gadget")


@dataclass(frozen=True, slots=True)
class Reward:
    reward_id: str
    slot: Slot
    #: The level each subject kind unlocks it at; ``None`` = not for that kind.
    person: int | None
    agent: int | None
    pet: int | None

    def level_for(self, kind: SubjectKind) -> int | None:
        return {"person": self.person, "agent": self.agent, "pet": self.pet}[kind]


REWARDS: Final[tuple[Reward, ...]] = (
    Reward("frame_bronze", "frame", person=3, agent=3, pet=3),
    Reward("frame_silver", "frame", person=10, agent=10, pet=10),
    Reward("frame_gold", "frame", person=20, agent=20, pet=20),
    Reward("frame_diamond", "frame", person=50, agent=40, pet=40),
    Reward("trail_footprints", "trail", person=2, agent=None, pet=None),
    Reward("trail_sparkle", "trail", person=5, agent=5, pet=2),
    Reward("trail_comet", "trail", person=12, agent=15, pet=12),
    Reward("trail_neon", "trail", person=20, agent=25, pet=None),
    Reward("trail_rainbow", "trail", person=30, agent=35, pet=25),
    Reward("trail_stardust", "trail", person=45, agent=None, pet=35),
    Reward("aura_glow", "aura", person=7, agent=8, pet=5),
    Reward("aura_runes", "aura", person=15, agent=18, pet=15),
    Reward("aura_storm", "aura", person=35, agent=30, pet=None),
    Reward("aura_legend", "aura", person=50, agent=50, pet=50),
    Reward("gadget_drone", "gadget", person=10, agent=12, pet=None),
    Reward("gadget_halo", "gadget", person=18, agent=20, pet=8),
    Reward("gadget_crown", "gadget", person=25, agent=None, pet=20),
    Reward("gadget_wings", "gadget", person=40, agent=45, pet=None),
)


def unlocked_rewards(kind: SubjectKind, level: int) -> list[Reward]:
    return [r for r in REWARDS if (at := r.level_for(kind)) is not None and level >= at]


def rewards_between(kind: SubjectKind, before: int, after: int) -> list[Reward]:
    """Rewards newly unlocked when a subject climbs from ``before`` to ``after``."""
    return [
        r for r in REWARDS
        if (at := r.level_for(kind)) is not None and before < at <= after
    ]


# ------------------------------------------------------------------ titles

#: Title bands per kind: (first level of the band, title id). The frontend
#: translates ``society.level.title.<id>``.
TITLES: Final[dict[SubjectKind, tuple[tuple[int, str], ...]]] = {
    "person": (
        (1, "newcomer"), (5, "apprentice"), (10, "operator"), (15, "specialist"),
        (20, "strategist"), (25, "architect"), (30, "commander"), (40, "visionary"),
        (50, "legend"),
    ),
    "agent": (
        (1, "rookie"), (5, "trainee"), (10, "associate"), (15, "professional"),
        (20, "expert"), (30, "veteran"), (40, "elite"), (50, "grandmaster"),
    ),
    "pet": (
        (1, "hatchling"), (5, "buddy"), (10, "sidekick"), (20, "partner"),
        (30, "guardian"), (40, "champion"), (50, "mythic"),
    ),
}


def title_for(kind: SubjectKind, level: int) -> str:
    title = TITLES[kind][0][1]
    for start, name in TITLES[kind]:
        if level >= start:
            title = name
    return title


def subject_kind(subject_id: str) -> SubjectKind | None:
    if subject_id == PERSON_ID:
        return "person"
    prefix, sep, rest = subject_id.partition(":")
    if not sep or not rest:
        return None
    if prefix == "agent":
        return "agent"
    if prefix == "pet":
        return "pet"
    return None


def agent_subject(agent_id: str) -> str:
    return f"agent:{agent_id}"


def pet_subject(pet_id: str) -> str:
    return f"pet:{pet_id}"
