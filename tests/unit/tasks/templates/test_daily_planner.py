"""The ``daily_planner`` automation template validates, renders and builds a spec."""
from __future__ import annotations

from datetime import datetime

from jarvis.tasks.templates import (
    LOCALES,
    AutomationTemplate,
    all_templates,
    build_spec,
    render_prompt,
)
from jarvis.tasks.templates.daily_planner import TEMPLATE


def test_template_is_discovered_and_well_formed() -> None:
    assert isinstance(TEMPLATE, AutomationTemplate)
    assert all_templates(refresh=True)["daily_planner"] is TEMPLATE
    assert TEMPLATE.category == "productivity"
    assert TEMPLATE.icon == "calendar-check"
    assert TEMPLATE.schedule.kind == "daily"
    assert TEMPLATE.schedule.time == "08:30"


def test_all_locales_present() -> None:
    for locale in LOCALES:
        assert TEMPLATE.name.for_locale(locale)
        assert TEMPLATE.description.for_locale(locale)
        for inp in TEMPLATE.inputs:
            assert inp.label.for_locale(locale)
            assert inp.placeholder is not None and inp.placeholder.for_locale(locale)


def test_requires_is_subset_of_grants_and_read_only() -> None:
    granted = {g.plugin_id for g in TEMPLATE.plugin_grants}
    assert set(TEMPLATE.requires) <= granted
    assert TEMPLATE.requires == ("google_calendar",)
    assert granted == {"google_calendar", "gmail"}
    assert all(g.scope == "read" for g in TEMPLATE.plugin_grants)


def test_prompt_renders_default_work_hours() -> None:
    rendered = render_prompt(TEMPLATE, None)
    assert "09:00-18:00" in rendered
    assert "{work_hours}" not in rendered
    assert "list_events" in rendered
    assert "list_messages" in rendered
    assert "calendar is empty" in rendered


def test_prompt_renders_custom_work_hours() -> None:
    rendered = render_prompt(TEMPLATE, {"work_hours": "08:00-16:00"})
    assert "08:00-16:00" in rendered
    assert "09:00-18:00" not in rendered


def test_build_spec_builds_daily_agent_task_without_inputs() -> None:
    now = datetime(2026, 8, 24, 9, 0)
    spec = build_spec(TEMPLATE, inputs={}, locale="de", now=now)
    assert spec.created_by == "template"
    assert "template:daily_planner" in spec.tags
    assert spec.title == "Tagesplaner"  # i18n-allow
    assert spec.trigger.type == "every"
    assert spec.trigger.interval_seconds == 86_400
    assert spec.trigger.start_at == "2026-08-25T08:30:00"
    assert spec.action.kind == "agent"
    assert "09:00-18:00" in spec.action.prompt
    assert spec.action.plugin_grants == TEMPLATE.plugin_grants
