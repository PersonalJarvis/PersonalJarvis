"""The ``weekly_review`` automation template validates, renders and builds a spec."""
from __future__ import annotations

from datetime import datetime

from jarvis.tasks.templates import (
    LOCALES,
    AutomationTemplate,
    all_templates,
    build_spec,
    missing_requirements,
    render_prompt,
    schedule_label,
)
from jarvis.tasks.templates.weekly_review import TEMPLATE


def test_template_is_discovered_and_well_formed() -> None:
    assert isinstance(TEMPLATE, AutomationTemplate)
    assert all_templates(refresh=True)["weekly_review"] is TEMPLATE
    assert TEMPLATE.category == "productivity"
    assert TEMPLATE.icon == "clipboard-list"
    assert TEMPLATE.schedule.kind == "weekly"
    assert TEMPLATE.schedule.weekday == 4  # Friday
    assert TEMPLATE.schedule.time == "16:00"
    assert schedule_label(TEMPLATE.schedule, "en") == "Fridays at 16:00"


def test_all_locales_present() -> None:
    for locale in LOCALES:
        assert TEMPLATE.name.for_locale(locale)
        assert TEMPLATE.description.for_locale(locale)
    assert TEMPLATE.inputs == ()


def test_grants_are_read_only_and_nothing_is_required() -> None:
    granted = {g.plugin_id for g in TEMPLATE.plugin_grants}
    assert granted == {"google_calendar", "gmail", "wiki-recall"}
    assert all(g.scope == "read" for g in TEMPLATE.plugin_grants)
    # Degrades to memory-only: no hard requirement, so the card is ready on
    # an install without a calendar or a mailbox.
    assert TEMPLATE.requires == ()
    assert set(TEMPLATE.requires) <= granted
    assert missing_requirements(TEMPLATE.requires, ["wiki-recall"]) == []
    assert missing_requirements(TEMPLATE.requires, []) == []


def test_prompt_renders_without_inputs_and_names_every_tool() -> None:
    rendered = render_prompt(TEMPLATE, None)
    assert rendered == TEMPLATE.prompt
    assert "{" not in rendered  # no placeholders: the template has no inputs
    for tool in ("google_calendar", "gmail", "wiki-recall"):
        assert f"`{tool}`" in rendered
    for heading in ("Done", "Open", "Next week (3 priorities)"):
        assert f'"{heading}"' in rendered
    assert "15 lines" in rendered
    assert "no emojis" in rendered
    assert "no preamble" in rendered.lower()
    assert "configured output language" in rendered
    # Degradation contract: use what is available, say which sources were used.
    assert "carry on without it" in rendered
    assert "sources actually used" in rendered


def test_build_spec_builds_weekly_friday_agent_task() -> None:
    now = datetime(2026, 8, 19, 12, 0)  # a Wednesday
    spec = build_spec(TEMPLATE, locale="de", now=now)
    assert spec.created_by == "template"
    assert "template:weekly_review" in spec.tags
    assert spec.title == "Wochenrückblick"  # i18n-allow
    assert spec.trigger.type == "every"
    assert spec.trigger.interval_seconds == 7 * 86_400
    assert spec.trigger.start_at == "2026-08-21T16:00:00"
    assert spec.action.kind == "agent"
    assert spec.action.prompt == TEMPLATE.prompt
    assert spec.action.plugin_grants == TEMPLATE.plugin_grants
    assert spec.action.model_tier == "auto"


def test_build_spec_after_friday_cutoff_rolls_to_next_week() -> None:
    now = datetime(2026, 8, 21, 16, 30)  # Friday, past 16:00
    spec = build_spec(TEMPLATE, now=now)
    assert spec.trigger.start_at == "2026-08-28T16:00:00"
