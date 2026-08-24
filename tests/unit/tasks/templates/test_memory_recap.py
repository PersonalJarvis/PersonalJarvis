"""The ``memory_recap`` automation template validates, renders and builds a spec."""
from __future__ import annotations

from datetime import datetime

from jarvis.tasks.templates import (
    LOCALES,
    AutomationTemplate,
    all_templates,
    build_spec,
    missing_requirements,
    render_prompt,
)
from jarvis.tasks.templates.memory_recap import TEMPLATE

_LOCAL_MEMORY_TOOLS = ("wiki-recall", "wiki-list", "wiki-page-read", "ultrawiki-search")


def test_template_is_discovered_and_well_formed() -> None:
    assert isinstance(TEMPLATE, AutomationTemplate)
    assert all_templates(refresh=True)["memory_recap"] is TEMPLATE
    assert TEMPLATE.category == "research"
    assert TEMPLATE.icon == "brain"
    assert TEMPLATE.schedule.kind == "weekly"
    assert TEMPLATE.schedule.weekday == 6  # Sunday
    assert TEMPLATE.schedule.time == "18:00"
    assert TEMPLATE.inputs == ()


def test_all_locales_present() -> None:
    for locale in LOCALES:
        assert TEMPLATE.name.for_locale(locale)
        assert TEMPLATE.description.for_locale(locale)


def test_description_says_it_works_offline_without_a_key() -> None:
    en = TEMPLATE.description.en.lower()
    assert "offline" in en
    assert "key" in en
    assert "offline" in TEMPLATE.description.de.lower()
    assert "sin conexión" in TEMPLATE.description.es.lower()


def test_requires_is_subset_of_grants_and_all_read_only() -> None:
    granted = {g.plugin_id for g in TEMPLATE.plugin_grants}
    assert set(TEMPLATE.requires) <= granted
    assert granted == set(_LOCAL_MEMORY_TOOLS)
    assert TEMPLATE.requires == ("wiki-recall",)
    assert all(g.scope == "read" for g in TEMPLATE.plugin_grants)


def test_only_local_tools_no_external_dependency() -> None:
    # Ready on a box that has nothing but the local memory tools.
    assert missing_requirements(TEMPLATE.requires, ["wiki-recall"]) == []
    # Not ready when the memory tool is absent.
    assert missing_requirements(TEMPLATE.requires, ["search_web"]) == ["wiki-recall"]
    for external in ("search_web", "gmail", "google_calendar", "github"):
        assert external not in {g.plugin_id for g in TEMPLATE.plugin_grants}


def test_prompt_renders_without_placeholders() -> None:
    rendered = render_prompt(TEMPLATE, None)
    assert rendered == TEMPLATE.prompt
    assert "{" not in rendered and "}" not in rendered
    for tool in _LOCAL_MEMORY_TOOLS:
        assert tool in rendered
    assert "7 days" in rendered
    assert "3 to 5 themes" in rendered
    assert "12 lines" in rendered
    assert "configured output language" in rendered
    assert "no emojis" in rendered
    assert "memory is empty" in rendered


def test_build_spec_builds_weekly_sunday_agent_task() -> None:
    now = datetime(2026, 8, 19, 12, 0)  # a Wednesday
    spec = build_spec(TEMPLATE, locale="de", now=now)
    assert spec.created_by == "template"
    assert "template:memory_recap" in spec.tags
    assert spec.title == "Gedächtnis-Rückblick"  # i18n-allow
    assert spec.trigger.type == "every"
    assert spec.trigger.interval_seconds == 7 * 86_400
    assert spec.trigger.start_at == "2026-08-23T18:00:00"
    assert spec.action.kind == "agent"
    assert spec.action.prompt == TEMPLATE.prompt
    assert spec.action.plugin_grants == TEMPLATE.plugin_grants
