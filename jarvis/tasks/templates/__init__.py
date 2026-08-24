"""Automation templates — the pre-built automations catalogue.

An *automation* is a recurring scheduled task (``jarvis/tasks``) whose action
is an agentic brain turn with a plugin allowlist. A *template* is the
ready-made blueprint for one: a name, a category, a default schedule, the
prompt, the plugin grants it needs, and optional user inputs (a watchlist, a
city, a repository path) that are substituted into the prompt when the user
adds it.

Layout — ONE MODULE PER TEMPLATE. Every ``jarvis/tasks/templates/<key>.py``
exports a module-level ``TEMPLATE: AutomationTemplate``; :func:`all_templates`
discovers them with ``pkgutil`` so adding a template never touches a shared
file (ten authors can land ten templates in parallel without a merge
conflict). Modules starting with ``_`` are helpers and are skipped.

Rules for a template:

- ``prompt`` is English. It asks for the reply in the configured output
  language — the one resolver (``jarvis/core/turn_language.py``) decides.
- ``plugin_grants`` name LIVE TOOL NAMES (``gmail``, ``google_calendar``,
  ``search_web``, ``wiki-recall``, ``github`` — the last one is a prefix
  grant that covers every ``github/<tool>``). ``requires`` lists the subset
  the automation cannot work without; the API reports ``ready`` per
  template by checking those against the live tool registry so the card can
  say "needs Gmail" instead of failing at 09:00.
- ``{placeholders}`` in ``prompt`` are template inputs (``inputs``), filled
  from the user's values (or the input's ``default``) when the template is
  instantiated. No other brace syntax — ``build_spec`` escapes the rest.
- ``name``/``description`` carry ``en``/``de``/``es`` — the UI picks the
  active locale and falls back to ``en``.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from jarvis.tasks.schema import (
    AgentAction,
    PluginGrant,
    TaskSpec,
    TriggerEvery,
)

Locale = Literal["en", "de", "es"]
LOCALES: tuple[Locale, ...] = ("en", "de", "es")

TemplateCategory = Literal["news", "productivity", "finance", "research", "developer"]
#: Display order of the catalogue sections (the UI groups cards by this).
CATEGORIES: tuple[TemplateCategory, ...] = (
    "news", "productivity", "finance", "research", "developer",
)

#: Tag that marks a task as "created from template <key>" — the UI uses it to
#: show the template as installed and to pair a task with its card.
TEMPLATE_TAG_PREFIX = "template:"


class LocalizedText(BaseModel):
    """A short UI string in the three product locales (``en`` is mandatory)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    en: str = Field(min_length=1, max_length=512)
    de: str = Field(default="", max_length=512)
    es: str = Field(default="", max_length=512)

    def for_locale(self, locale: str) -> str:
        return getattr(self, locale, "") or self.en


class TemplateInput(BaseModel):
    """A value the user may fill in when adding the template (a watchlist, a
    city, a repo path). ``key`` is the ``{placeholder}`` used in the prompt."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    label: LocalizedText
    placeholder: LocalizedText | None = None
    default: str = Field(default="", max_length=2048)
    required: bool = False


class TemplateSchedule(BaseModel):
    """Default schedule, expressed the way the UI shows it ("Daily at 07:30",
    "Mondays at 09:00"), not as a cron expression. Mapped onto the
    ``every`` trigger by :func:`schedule_to_trigger`.

    ``weekday`` follows ``datetime.weekday()``: 0 = Monday … 6 = Sunday.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["hourly", "daily", "weekly"] = "daily"
    time: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekday: int = Field(default=0, ge=0, le=6)


class AutomationTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_]*$")
    category: TemplateCategory
    #: A lucide icon name the UI resolves (``sun``, ``mail``, ``dollar-sign``).
    icon: str = Field(default="sparkles", max_length=48)
    name: LocalizedText
    description: LocalizedText
    schedule: TemplateSchedule = Field(default_factory=TemplateSchedule)
    prompt: str = Field(min_length=1, max_length=16_384)
    plugin_grants: tuple[PluginGrant, ...] = Field(default_factory=tuple)
    #: Tool names (or ``prefix`` grants like ``github``) the automation cannot
    #: run without. Empty = works on any install (brain only).
    requires: tuple[str, ...] = Field(default_factory=tuple)
    inputs: tuple[TemplateInput, ...] = Field(default_factory=tuple)
    model_tier: Literal["fast", "deep", "auto"] = "auto"
    tags: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def tag(self) -> str:
        return f"{TEMPLATE_TAG_PREFIX}{self.key}"

    def to_api(self, locale: str, *, live_tools: Iterable[str] | None = None) -> dict[str, Any]:
        """Flat dict for ``GET /api/tasks/templates`` — localized, with the
        readiness verdict so the card can say what is missing."""
        missing = missing_requirements(self.requires, live_tools) if live_tools is not None else []
        return {
            "key": self.key,
            "category": self.category,
            "icon": self.icon,
            "name": self.name.for_locale(locale),
            "description": self.description.for_locale(locale),
            "schedule": self.schedule.model_dump(),
            "schedule_label": schedule_label(self.schedule, locale),
            "plugin_grants": [g.model_dump() for g in self.plugin_grants],
            "requires": list(self.requires),
            "missing": missing,
            "ready": not missing,
            "inputs": [
                {
                    "key": i.key,
                    "label": i.label.for_locale(locale),
                    "placeholder": i.placeholder.for_locale(locale) if i.placeholder else "",
                    "default": i.default,
                    "required": i.required,
                }
                for i in self.inputs
            ],
            "model_tier": self.model_tier,
            "tags": list(self.tags),
            "prompt": self.prompt,
        }


# ---------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------

_cache: dict[str, AutomationTemplate] | None = None


def all_templates(*, refresh: bool = False) -> dict[str, AutomationTemplate]:
    """Every template module in this package, keyed by ``template.key``,
    ordered by category (catalogue order) and then by key."""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    found: dict[str, AutomationTemplate] = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        template = getattr(module, "TEMPLATE", None)
        if not isinstance(template, AutomationTemplate):
            continue
        if template.key in found:
            raise RuntimeError(
                f"duplicate automation template key {template.key!r} "
                f"({info.name}.py)"
            )
        found[template.key] = template
    order = {c: i for i, c in enumerate(CATEGORIES)}
    _cache = dict(
        sorted(found.items(), key=lambda kv: (order[kv[1].category], kv[0]))
    )
    return _cache


def get_template(key: str) -> AutomationTemplate | None:
    return all_templates().get(key)


# ---------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------

def grant_matches(grant: str, tool_name: str) -> bool:
    """A grant names a tool exactly, or is a prefix grant for a bridged
    plugin whose tools are namespaced ``<plugin>/<tool>`` (``github`` covers
    ``github/list_issues``)."""
    return tool_name == grant or tool_name.startswith(grant + "/")


def missing_requirements(
    requires: Iterable[str], live_tools: Iterable[str] | None,
) -> list[str]:
    live = list(live_tools or ())
    return [
        req for req in requires
        if not any(grant_matches(req, name) for name in live)
    ]


# ---------------------------------------------------------------------
# Schedule → trigger
# ---------------------------------------------------------------------

def _parse_hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.split(":")
    return int(hh), int(mm)


def next_occurrence(schedule: TemplateSchedule, now: datetime) -> datetime:
    """The first wall-clock moment strictly after ``now`` that matches the
    schedule (local time, naive — the scheduler treats ISO without a zone as
    the system zone)."""
    if schedule.kind == "hourly":
        return (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    hh, mm = _parse_hhmm(schedule.time)
    candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if schedule.kind == "daily":
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    # weekly
    days_ahead = (schedule.weekday - now.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def schedule_to_trigger(schedule: TemplateSchedule, now: datetime | None = None) -> TriggerEvery:
    now = now or datetime.now()
    interval = {"hourly": 3600, "daily": 86_400, "weekly": 7 * 86_400}[schedule.kind]
    first = next_occurrence(schedule, now)
    return TriggerEvery(
        interval_seconds=float(interval),
        start_at=first.isoformat(timespec="seconds"),
    )


_WEEKDAY_NAMES: dict[str, tuple[str, ...]] = {
    "en": ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"),
    "de": (  # i18n-allow
        "Montags", "Dienstags", "Mittwochs", "Donnerstags", "Freitags", "Samstags", "Sonntags",
    ),
    "es": ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábados", "Domingos"),
}
_SCHEDULE_WORDS: dict[str, dict[str, str]] = {
    "en": {"hourly": "Every hour", "daily": "Daily at {time}", "weekly": "{day} at {time}"},
    "de": {  # i18n-allow
        "hourly": "Stündlich", "daily": "Täglich um {time}", "weekly": "{day} um {time}",
    },
    "es": {"hourly": "Cada hora", "daily": "Diario a las {time}", "weekly": "{day} a las {time}"},
}


def schedule_label(schedule: TemplateSchedule, locale: str = "en") -> str:
    """"Daily at 07:30" / "Täglich um 07:30" / "Mondays at 09:00"."""
    loc = locale if locale in _SCHEDULE_WORDS else "en"
    words = _SCHEDULE_WORDS[loc]
    if schedule.kind == "hourly":
        return words["hourly"]
    if schedule.kind == "daily":
        return words["daily"].format(time=schedule.time)
    day = _WEEKDAY_NAMES[loc][schedule.weekday]
    return words["weekly"].format(day=day, time=schedule.time)


# ---------------------------------------------------------------------
# Instantiate
# ---------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def render_prompt(template: AutomationTemplate, inputs: dict[str, str] | None) -> str:
    """Substitute ``{input_key}`` placeholders; unknown braces stay literal."""
    values = {
        i.key: ((inputs or {}).get(i.key) or "").strip() or i.default
        for i in template.inputs
    }

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        return values.get(key, match.group(0))

    return _PLACEHOLDER.sub(_sub, template.prompt)


def build_spec(
    template: AutomationTemplate,
    *,
    inputs: dict[str, str] | None = None,
    schedule: TemplateSchedule | None = None,
    title: str | None = None,
    locale: str = "en",
    now: datetime | None = None,
) -> TaskSpec:
    """The TaskSpec a template turns into when the user adds it."""
    missing_inputs = [
        i.key for i in template.inputs
        if i.required and not (((inputs or {}).get(i.key) or "").strip() or i.default)
    ]
    if missing_inputs:
        raise ValueError(f"missing required inputs: {', '.join(missing_inputs)}")
    sched = schedule or template.schedule
    return TaskSpec(
        title=(title or "").strip() or template.name.for_locale(locale),
        trigger=schedule_to_trigger(sched, now),
        action=AgentAction(
            prompt=render_prompt(template, inputs),
            plugin_grants=template.plugin_grants,
            model_tier=template.model_tier,
        ),
        created_by="template",
        tags=(template.tag, *template.tags),
    )


__all__ = [
    "CATEGORIES",
    "LOCALES",
    "TEMPLATE_TAG_PREFIX",
    "AutomationTemplate",
    "LocalizedText",
    "TemplateInput",
    "TemplateSchedule",
    "all_templates",
    "build_spec",
    "get_template",
    "grant_matches",
    "missing_requirements",
    "next_occurrence",
    "render_prompt",
    "schedule_label",
    "schedule_to_trigger",
]
