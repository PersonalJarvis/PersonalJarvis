"""The ``task_extractor`` automation template validates, renders and builds a spec."""
from __future__ import annotations

from datetime import datetime

from jarvis.tasks.templates import (
    LOCALES,
    AutomationTemplate,
    all_templates,
    build_spec,
    render_prompt,
)
from jarvis.tasks.templates.task_extractor import TEMPLATE


def test_template_is_discovered_and_well_formed() -> None:
    assert isinstance(TEMPLATE, AutomationTemplate)
    assert all_templates(refresh=True)["task_extractor"] is TEMPLATE
    assert TEMPLATE.category == "productivity"
    assert TEMPLATE.icon == "list-checks"
    assert TEMPLATE.schedule.kind == "daily"
    assert TEMPLATE.schedule.time == "17:00"


def test_all_locales_present() -> None:
    for locale in LOCALES:
        assert TEMPLATE.name.for_locale(locale)
        assert TEMPLATE.description.for_locale(locale)
        for inp in TEMPLATE.inputs:
            assert inp.label.for_locale(locale)
            assert inp.placeholder is not None and inp.placeholder.for_locale(locale)


def test_requires_is_subset_of_grants_and_storage_may_write() -> None:
    scopes = {g.plugin_id: g.scope for g in TEMPLATE.plugin_grants}
    assert set(TEMPLATE.requires) <= set(scopes)
    # Storage is a soft requirement: the card is "ready" with Gmail alone.
    assert TEMPLATE.requires == ("gmail",)
    assert scopes["gmail"] == "read"
    # The unattended run must be able to store the list without approval,
    # through a tool that is actually live for task turns (not `remember`,
    # which ADR-0011 keeps out of the router tool set).
    assert scopes["wiki-ingest"] == "write"
    assert "remember" not in scopes


def test_prompt_renders_lookback_default_and_override() -> None:
    rendered = render_prompt(TEMPLATE, None)
    assert "last 24 hours" in rendered
    assert "{lookback_hours}" not in rendered
    rendered = render_prompt(TEMPLATE, {"lookback_hours": "48"})
    assert "last 48 hours" in rendered
    for needle in ("list_messages", "get_message", "wiki-ingest",
                   "automation:task_extractor", "Action items",
                   "no action items today", "no emojis"):
        assert needle in rendered
    assert "`remember`" not in rendered


def test_build_spec_builds_daily_agent_task() -> None:
    now = datetime(2026, 8, 24, 18, 0)  # after 17:00 → tomorrow
    spec = build_spec(TEMPLATE, inputs={}, locale="de", now=now)
    assert spec.created_by == "template"
    assert "template:task_extractor" in spec.tags
    assert spec.title == "Aufgaben-Extraktor"  # i18n-allow
    assert spec.trigger.type == "every"
    assert spec.trigger.interval_seconds == 86_400
    assert spec.trigger.start_at == "2026-08-25T17:00:00"
    assert spec.action.kind == "agent"
    assert "last 24 hours" in spec.action.prompt
    assert spec.action.plugin_grants == TEMPLATE.plugin_grants
