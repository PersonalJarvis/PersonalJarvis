"""The ``inbox_brief`` automation template validates, renders and builds a spec."""
from __future__ import annotations

from datetime import datetime

from jarvis.tasks.templates import (
    LOCALES,
    AutomationTemplate,
    all_templates,
    build_spec,
    render_prompt,
)
from jarvis.tasks.templates.inbox_brief import TEMPLATE


def test_template_is_discovered_and_well_formed() -> None:
    assert isinstance(TEMPLATE, AutomationTemplate)
    assert all_templates(refresh=True)["inbox_brief"] is TEMPLATE
    assert TEMPLATE.category == "productivity"
    assert TEMPLATE.icon == "mail"
    assert TEMPLATE.schedule.kind == "daily"
    assert TEMPLATE.schedule.time == "09:00"


def test_all_locales_present() -> None:
    for locale in LOCALES:
        assert TEMPLATE.name.for_locale(locale)
        assert TEMPLATE.description.for_locale(locale)
        for inp in TEMPLATE.inputs:
            assert inp.label.for_locale(locale)
            assert inp.placeholder is not None and inp.placeholder.for_locale(locale)


def test_requires_is_subset_of_read_only_gmail_grant() -> None:
    granted = {g.plugin_id for g in TEMPLATE.plugin_grants}
    assert set(TEMPLATE.requires) <= granted
    assert granted == {"gmail"}
    # The brief never sends — a read grant is all it may ever hold.
    assert all(g.scope == "read" for g in TEMPLATE.plugin_grants)


def test_prompt_renders_lookback_and_keeps_output_rules() -> None:
    default = render_prompt(TEMPLATE, None)
    assert "newer_than:24h" in default
    custom = render_prompt(TEMPLATE, {"lookback_hours": "48"})
    assert "newer_than:48h" in custom
    assert "{lookback_hours}" not in custom
    assert "list_messages" in custom
    assert "get_message" in custom
    # list_messages returns ids only (gmail_rest.py), so the prompt must
    # force a get_message per id and pre-filter noise in the query itself.
    assert "EVERY id" in custom
    assert "-category:promotions" in custom
    assert "send" in custom and "never send" in custom
    assert "Maximum 8 lines" in custom
    assert "No emojis" in custom
    assert "inbox is clear" in custom


def test_build_spec_builds_daily_agent_task_without_inputs() -> None:
    now = datetime(2026, 8, 24, 12, 0)
    spec = build_spec(TEMPLATE, locale="de", now=now)
    assert spec.created_by == "template"
    assert "template:inbox_brief" in spec.tags
    assert spec.title == "Posteingang-Briefing"  # i18n-allow
    assert spec.trigger.type == "every"
    assert spec.trigger.interval_seconds == 86_400
    assert spec.trigger.start_at == "2026-08-25T09:00:00"
    assert spec.action.kind == "agent"
    assert "newer_than:24h" in spec.action.prompt
    assert spec.action.plugin_grants == TEMPLATE.plugin_grants
