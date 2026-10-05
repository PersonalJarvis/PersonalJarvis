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
ordinary use, level 50 after several months. Levels climb a military rank
ladder (:data:`RANKS`): a promotion every two or three levels, from private
to the five-star general at the cap, and promotions unlock real uniform
pieces (:data:`REWARDS`). ``docs/agent-society/level-system.md`` is the readable
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


# ------------------------------------------------------------------ ranks

#: The rank ladder every subject climbs, lowest first: (first level, rank id,
#: pay grade). The person, every agent and the pet share it, so a rank means
#: the same thing on every name plate. Enlisted grades come first, then the
#: commissioned grades up to the five-star general at the cap. The frontend
#: draws each rank's insignia and translates ``society.level.title.<id>``.
RANKS: Final[tuple[tuple[int, str, str], ...]] = (
    (1, "private", "E-1"),
    (2, "private_second_class", "E-2"),
    (4, "private_first_class", "E-3"),
    (6, "specialist", "E-4"),
    (8, "corporal", "E-4"),
    (10, "sergeant", "E-5"),
    (12, "staff_sergeant", "E-6"),
    (14, "sergeant_first_class", "E-7"),
    (16, "master_sergeant", "E-8"),
    (18, "first_sergeant", "E-8"),
    (20, "sergeant_major", "E-9"),
    (22, "command_sergeant_major", "E-9"),
    (24, "sergeant_major_of_the_army", "E-9"),
    (26, "second_lieutenant", "O-1"),
    (28, "first_lieutenant", "O-2"),
    (30, "captain", "O-3"),
    (32, "major", "O-4"),
    (34, "lieutenant_colonel", "O-5"),
    (36, "colonel", "O-6"),
    (38, "brigadier_general", "O-7"),
    (41, "major_general", "O-8"),
    (44, "lieutenant_general", "O-9"),
    (47, "general", "O-10"),
    (50, "general_of_the_army", "O-11"),
)


# ------------------------------------------------------------------ rewards

Slot = Literal["uniform", "headwear", "decoration"]
SLOTS: Final[tuple[Slot, ...]] = ("uniform", "headwear", "decoration")


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


#: Real uniform pieces, each unlocking on a promotion. Agents keep the outfit
#: their owner dressed them in, so they earn decorations only; the pet has no
#: body to dress and shows its rank on its name plate.
REWARDS: Final[tuple[Reward, ...]] = (
    Reward("uniform_service_shirt", "uniform", person=4, agent=None, pet=None),
    Reward("uniform_field_jacket", "uniform", person=10, agent=None, pet=None),
    Reward("uniform_service_greens", "uniform", person=20, agent=None, pet=None),
    Reward("uniform_dress_blues", "uniform", person=26, agent=None, pet=None),
    Reward("uniform_mess_dress", "uniform", person=41, agent=None, pet=None),
    Reward("headwear_patrol_cap", "headwear", person=6, agent=None, pet=None),
    Reward("headwear_garrison_cap", "headwear", person=14, agent=None, pet=None),
    Reward("headwear_beret", "headwear", person=24, agent=None, pet=None),
    Reward("headwear_service_cap", "headwear", person=32, agent=None, pet=None),
    Reward("decoration_ribbon_bar", "decoration", person=8, agent=3, pet=None),
    Reward("decoration_ribbon_rack", "decoration", person=18, agent=16, pet=None),
    Reward("decoration_aiguillette", "decoration", person=36, agent=32, pet=None),
    Reward("decoration_medals", "decoration", person=47, agent=44, pet=None),
)


def unlocked_rewards(kind: SubjectKind, level: int) -> list[Reward]:
    return [r for r in REWARDS if (at := r.level_for(kind)) is not None and level >= at]


def rewards_between(kind: SubjectKind, before: int, after: int) -> list[Reward]:
    """Rewards newly unlocked when a subject climbs from ``before`` to ``after``."""
    return [
        r for r in REWARDS
        if (at := r.level_for(kind)) is not None and before < at <= after
    ]


# ------------------------------------------------------------------ agent looks

#: The agent level at which each wearable look unlocks
#: (``companion/accessories.json`` ids). Four are open from the start, so
#: every agent can be dressed at once; the rest arrive as level-up rewards.
#: A look an agent already wears is never taken away, whatever its level.
LOOK_UNLOCKS: Final[dict[str, int]] = {
    "sunglasses": 1,
    "cap": 1,
    "headphones": 1,
    "hoodie": 1,
    "lab_coat": 2,
    "cigar": 3,
    "suit": 4,
    "top_hat": 6,
    "tuxedo": 8,
    "crown": 10,
}


def looks_between(before: int, after: int) -> list[str]:
    """Agent looks newly unlocked when an agent climbs from ``before`` to ``after``."""
    return [look for look, at in LOOK_UNLOCKS.items() if before < at <= after]


# ------------------------------------------------------------------ titles

_RANK_BANDS: Final[tuple[tuple[int, str], ...]] = tuple((level, rank) for level, rank, _ in RANKS)

#: Title bands per kind: (first level of the band, title id). Every kind wears
#: the rank ladder, so the bands are the same; the per-kind shape stays so the
#: API can give a kind its own ladder later without a contract change.
TITLES: Final[dict[SubjectKind, tuple[tuple[int, str], ...]]] = {
    "person": _RANK_BANDS,
    "agent": _RANK_BANDS,
    "pet": _RANK_BANDS,
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
