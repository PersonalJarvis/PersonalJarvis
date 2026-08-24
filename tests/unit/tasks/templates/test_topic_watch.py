"""The ``topic_watch`` automation template validates, renders and builds a spec."""
from __future__ import annotations

from datetime import datetime

import pytest

from jarvis.tasks.templates import (
    LOCALES,
    AutomationTemplate,
    all_templates,
    build_spec,
    render_prompt,
)
from jarvis.tasks.templates.topic_watch import TEMPLATE


def test_template_is_discovered_and_well_formed() -> None:
    assert isinstance(TEMPLATE, AutomationTemplate)
    assert all_templates(refresh=True)["topic_watch"] is TEMPLATE
    assert TEMPLATE.category == "news"
    assert TEMPLATE.icon == "radar"
    assert TEMPLATE.schedule.kind == "weekly"
    assert TEMPLATE.schedule.weekday == 0
    assert TEMPLATE.schedule.time == "09:00"


def test_all_locales_present() -> None:
    for locale in LOCALES:
        assert TEMPLATE.name.for_locale(locale)
        assert TEMPLATE.description.for_locale(locale)
        for inp in TEMPLATE.inputs:
            assert inp.label.for_locale(locale)
            assert inp.placeholder is not None and inp.placeholder.for_locale(locale)


def test_requires_is_subset_of_grants() -> None:
    granted = {g.plugin_id for g in TEMPLATE.plugin_grants}
    assert set(TEMPLATE.requires) <= granted
    assert "search_web" in granted
    assert all(g.scope == "read" for g in TEMPLATE.plugin_grants)


def test_prompt_renders_topics() -> None:
    rendered = render_prompt(TEMPLATE, {"topics": "OpenAI, xAI"})
    assert "OpenAI, xAI" in rendered
    assert "{topics}" not in rendered
    assert "search_web" in rendered
    assert "nothing notable" in rendered


def test_build_spec_requires_topics() -> None:
    with pytest.raises(ValueError, match="topics"):
        build_spec(TEMPLATE, inputs={})
    with pytest.raises(ValueError, match="topics"):
        build_spec(TEMPLATE, inputs={"topics": ""})
    with pytest.raises(ValueError, match="topics"):
        build_spec(TEMPLATE)


def test_build_spec_builds_weekly_agent_task() -> None:
    now = datetime(2026, 8, 19, 12, 0)  # a Wednesday
    spec = build_spec(TEMPLATE, inputs={"topics": "OpenAI, xAI"}, locale="de", now=now)
    assert spec.created_by == "template"
    assert "template:topic_watch" in spec.tags
    assert spec.title == "Themen-Radar"  # i18n-allow
    assert spec.trigger.type == "every"
    assert spec.trigger.interval_seconds == 7 * 86_400
    assert spec.trigger.start_at == "2026-08-24T09:00:00"
    assert spec.action.kind == "agent"
    assert "OpenAI, xAI" in spec.action.prompt
    assert spec.action.plugin_grants == TEMPLATE.plugin_grants
