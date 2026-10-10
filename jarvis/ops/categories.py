"""Work, private and own business — which part of life a briefing item belongs to.

Every appointment and work item gets ONE category: ``work``, ``private``,
``business`` (the person's own projects), ``trading``, ``video`` (e.g. channel
statistics) or ``unassigned``. Trading and video are their own categories so
a day profile can show them without the rest of the own projects. Classification is
deterministic and explainable — no model call:

0. in strict mode (``only_listed``) an appointment from a calendar that is
   not listed is ``excluded`` — never shown, never counted, and the reader
   does not even fetch it (e.g. colleagues' calendars in a shared account);
1. an explicit assignment of that very item (source + id) wins;
2. then the calendar the appointment lives in (calendar id or name);
3. then keywords in the title (case-insensitive);
4. otherwise ``unassigned`` — never silently ``private``.

The rules are the person's own local settings (``ops.sqlite``): which
calendars and keywords mean their employer, their private life, their own
projects. Nothing about a person or company is part of the code.

A day profile says which categories each weekday shows (default: all, every
day). On a day that does not show every category, ``unassigned`` items are
left out — they might belong to an excluded category — and only their count
is reported, so nothing is lost and nothing leaks in.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final

import aiosqlite

log = logging.getLogger(__name__)

CATEGORIES: Final[tuple[str, ...]] = ("work", "private", "business", "trading", "video")
UNASSIGNED: Final = "unassigned"
EXCLUDED: Final = "excluded"
ALL: Final = frozenset(CATEGORIES)
KEYWORD_MAX: Final = 200
RULES_MAX: Final = 500


class CategoryError(ValueError):
    """A rule the store refuses (unknown category, empty or overlong text)."""


@dataclass(frozen=True, slots=True)
class CategoryRules:
    #: calendar id or calendar name (casefolded) -> category
    calendars: Mapping[str, str] = field(default_factory=dict)
    #: (keyword casefolded, category), checked in order
    keywords: tuple[tuple[str, str], ...] = ()
    #: "source:id" -> category, for single items
    items: Mapping[str, str] = field(default_factory=dict)
    #: strict mode: only the listed calendars count; every other one is excluded
    only_listed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "calendars": dict(self.calendars),
            "keywords": [{"keyword": k, "category": c} for k, c in self.keywords],
            "items": dict(self.items),
            "only_listed": self.only_listed,
        }


@dataclass(frozen=True, slots=True)
class DayProfile:
    """Categories shown per weekday (0 = Monday … 6 = Sunday)."""

    days: Mapping[int, frozenset[str]] = field(default_factory=lambda: {d: ALL for d in range(7)})

    def allowed(self, day: date) -> frozenset[str]:
        return self.days.get(day.weekday(), ALL)

    def full(self, day: date) -> bool:
        return self.allowed(day) >= ALL

    def to_dict(self) -> dict[str, Any]:
        return {str(d): sorted(c) for d, c in sorted(self.days.items())}


def _check(category: str) -> str:
    if category not in CATEGORIES:
        raise CategoryError(f"unknown category {category!r}")
    return category


def _key(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def validate_rules(rules: CategoryRules) -> CategoryRules:
    total = len(rules.calendars) + len(rules.keywords) + len(rules.items)
    if total > RULES_MAX:
        raise CategoryError(f"more than {RULES_MAX} rules")
    for name, category in rules.calendars.items():
        if not name or len(name) > KEYWORD_MAX:
            raise CategoryError("calendar names must be 1-200 characters")
        _check(category)
    for keyword, category in rules.keywords:
        if not keyword or len(keyword) > KEYWORD_MAX:
            raise CategoryError("keywords must be 1-200 characters")
        _check(category)
    for item, category in rules.items.items():
        if ":" not in item or len(item) > KEYWORD_MAX:
            raise CategoryError("item keys look like 'source:id'")
        _check(category)
    if rules.only_listed and not rules.calendars:
        raise CategoryError("only_listed needs at least one listed calendar")
    return rules


def make_rules(
    *,
    calendars: Mapping[str, str] | None = None,
    keywords: Iterable[tuple[str, str]] = (),
    items: Mapping[str, str] | None = None,
    only_listed: bool = False,
) -> CategoryRules:
    return validate_rules(
        CategoryRules(
            calendars={_key(k): v for k, v in (calendars or {}).items()},
            keywords=tuple((_key(k), v) for k, v in keywords),
            items=dict(items or {}),
            only_listed=bool(only_listed),
        )
    )


def listed_calendar_ids(rules: CategoryRules) -> tuple[str, ...]:
    """In strict mode, the calendar ids (``…@…``) to read — and only those.
    Empty means "read every calendar" (no strict mode, or only names listed)."""
    if not rules.only_listed:
        return ()
    return tuple(sorted(k for k in rules.calendars if "@" in k))


def make_profile(days: Mapping[int, Iterable[str]]) -> DayProfile:
    out: dict[int, frozenset[str]] = {d: ALL for d in range(7)}
    for day, cats in days.items():
        if not 0 <= int(day) <= 6:
            raise CategoryError("weekday must be 0 (Monday) to 6 (Sunday)")
        out[int(day)] = frozenset(_check(c) for c in cats)
    return DayProfile(out)


def _by_keyword(title: str, rules: CategoryRules) -> str | None:
    text = _key(title)
    for keyword, category in rules.keywords:
        if keyword in text:
            return category
    return None


def classify_event(event: Mapping[str, Any], rules: CategoryRules) -> tuple[str, str]:
    """``(category, why)`` for one calendar entry."""
    if rules.only_listed and not any(
        _key(str(event.get(f) or "")) in rules.calendars for f in ("calendar_id", "calendar")
    ):
        return EXCLUDED, "not_listed"
    explicit = rules.items.get(f"calendar:{event.get('id')}")
    if explicit:
        return explicit, "item"
    for field_name in ("calendar_id", "calendar"):
        category = rules.calendars.get(_key(str(event.get(field_name) or "")))
        if category:
            return category, "calendar"
    category = _by_keyword(str(event.get("title") or ""), rules)
    if category:
        return category, "keyword"
    return UNASSIGNED, "none"


def classify_item(source: str, item_id: str, title: str, rules: CategoryRules) -> tuple[str, str]:
    """``(category, why)`` for one work item (mission, task, quest, workflow)."""
    explicit = rules.items.get(f"{source}:{item_id}")
    if explicit:
        return explicit, "item"
    category = _by_keyword(title, rules)
    if category:
        return category, "keyword"
    return UNASSIGNED, "none"


def keep(category: str, day: date, profile: DayProfile) -> bool:
    """Whether an item of *category* is shown on *day*."""
    if category == EXCLUDED:
        return False
    if category == UNASSIGNED:
        return profile.full(day)  # might be an excluded category: never leak it
    return category in profile.allowed(day)


# ---------------------------------------------------------------- store

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_categories (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    rules       TEXT NOT NULL,
    profile     TEXT NOT NULL,
    updated_ms  INTEGER NOT NULL
)
"""


class CategoryStore:
    """The person's classification rules and day profile, in ``ops.sqlite``.

    Remembers the last rules it read: if the database is briefly unreadable,
    those still apply instead of nothing (which would let work through on a
    work-free day)."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._last: tuple[CategoryRules, DayProfile] | None = None

    async def _connect(self) -> aiosqlite.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA busy_timeout = 5000")
        await conn.execute(_SCHEMA)
        return conn

    async def load(self) -> tuple[CategoryRules, DayProfile]:
        try:
            loaded = await self._load()
        except Exception:
            if self._last is None:
                raise
            log.warning("ops categories: unreadable, using the last rules read", exc_info=True)
            return self._last
        self._last = loaded
        return loaded

    async def _load(self) -> tuple[CategoryRules, DayProfile]:
        conn = await self._connect()
        try:
            cur = await conn.execute("SELECT rules, profile FROM ops_categories WHERE id = 1")
            row = await cur.fetchone()
        finally:
            await conn.close()
        if row is None:
            return CategoryRules(), DayProfile()
        raw_rules = json.loads(row["rules"])
        raw_profile = json.loads(row["profile"])
        rules = make_rules(
            calendars=raw_rules.get("calendars") or {},
            keywords=[(k["keyword"], k["category"]) for k in raw_rules.get("keywords") or []],
            items=raw_rules.get("items") or {},
            only_listed=bool(raw_rules.get("only_listed")),
        )
        profile = make_profile({int(d): c for d, c in raw_profile.items()})
        return rules, profile

    async def save(self, rules: CategoryRules, profile: DayProfile) -> None:
        validate_rules(rules)
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT INTO ops_categories (id, rules, profile, updated_ms) VALUES (1, ?, ?, ?)"
                " ON CONFLICT (id) DO UPDATE SET rules = excluded.rules,"
                " profile = excluded.profile, updated_ms = excluded.updated_ms",
                (
                    json.dumps(rules.to_dict(), ensure_ascii=False),
                    json.dumps(profile.to_dict()),
                    int(time.time() * 1000),
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
        self._last = (rules, profile)


__all__ = [
    "ALL",
    "CATEGORIES",
    "EXCLUDED",
    "UNASSIGNED",
    "CategoryError",
    "CategoryRules",
    "CategoryStore",
    "DayProfile",
    "classify_event",
    "classify_item",
    "keep",
    "listed_calendar_ids",
    "make_profile",
    "make_rules",
    "validate_rules",
]
