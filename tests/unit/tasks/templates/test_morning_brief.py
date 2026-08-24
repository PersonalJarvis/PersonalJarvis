"""The ``morning_brief`` automation template validates, renders and builds a spec."""
from __future__ import annotations

from datetime import datetime

from jarvis.tasks.templates import (
    LOCALES,
    AutomationTemplate,
    all_templates,
    build_spec,
    render_prompt,
)
from jarvis.tasks.templates.morning_brief import TEMPLATE


def test_template_is_discovered_and_well_formed() -> None:
    assert isinstance(TEMPLATE, AutomationTemplate)
    assert all_templates(refresh=True)["morning_brief"] is TEMPLATE
    assert TEMPLATE.category == "news"
    assert TEMPLATE.icon == "sun"
    assert TEMPLATE.schedule.kind == "daily"
    assert TEMPLATE.schedule.time == "07:30"


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
    assert granted == {"search_web"}
    assert all(g.scope == "read" for g in TEMPLATE.plugin_grants)


def test_inputs_are_optional_with_sane_defaults() -> None:
    by_key = {i.key: i for i in TEMPLATE.inputs}
    assert set(by_key) == {"city", "interests"}
    assert not by_key["city"].required and by_key["city"].default == ""
    assert not by_key["interests"].required
    assert by_key["interests"].default == "world news, technology, business"


def test_prompt_renders_inputs_and_keeps_the_weather_conditional() -> None:
    rendered = render_prompt(TEMPLATE, {"city": "Hamburg", "interests": "AI, football"})
    assert 'City for the weather: "Hamburg"' in rendered
    assert "morning brief for AI, football" in rendered
    assert "{city}" not in rendered and "{interests}" not in rendered
    # The prompt tells the agent how to call the tool and how to stay honest.
    assert "search_web" in rendered
    assert "never pick or guess a city" in rendered
    assert "output language" in rendered


def test_prompt_without_city_leaves_the_field_empty_not_invented() -> None:
    rendered = render_prompt(TEMPLATE, {})
    assert 'City for the weather: ""' in rendered
    assert "world news, technology, business" in rendered


def test_build_spec_produces_a_daily_agent_task() -> None:
    now = datetime(2026, 8, 24, 9, 0, 0)
    spec = build_spec(TEMPLATE, inputs={"city": "Berlin"}, locale="de", now=now)
    assert spec.title == "Morgen-Briefing"  # i18n-allow
    assert spec.created_by == "template"
    assert "template:morning_brief" in spec.tags
    assert spec.trigger.type == "every"
    assert spec.trigger.interval_seconds == 86_400
    assert spec.trigger.start_at == "2026-08-25T07:30:00"
    assert spec.action.kind == "agent"
    assert 'City for the weather: "Berlin"' in spec.action.prompt
    assert [g.plugin_id for g in spec.action.plugin_grants] == ["search_web"]
