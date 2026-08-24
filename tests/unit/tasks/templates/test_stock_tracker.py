"""The ``stock_tracker`` automation template validates, renders and builds a spec."""
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
from jarvis.tasks.templates.stock_tracker import TEMPLATE


def test_template_is_discovered_and_well_formed() -> None:
    assert isinstance(TEMPLATE, AutomationTemplate)
    assert all_templates(refresh=True)["stock_tracker"] is TEMPLATE
    assert TEMPLATE.category == "finance"
    assert TEMPLATE.icon == "dollar-sign"
    assert TEMPLATE.schedule.kind == "daily"
    assert TEMPLATE.schedule.time == "14:00"


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


def test_prompt_renders_watchlist_and_guardrails() -> None:
    rendered = render_prompt(TEMPLATE, {"watchlist": "NVDA, AAPL"})
    assert "NVDA, AAPL" in rendered
    assert "{watchlist}" not in rendered
    assert "search_web" in rendered
    assert "price not found" in rendered
    assert "no emojis" in rendered
    assert "configured output language" in rendered
    assert "advice" in rendered


def test_build_spec_requires_watchlist() -> None:
    with pytest.raises(ValueError, match="watchlist"):
        build_spec(TEMPLATE, inputs={})
    with pytest.raises(ValueError, match="watchlist"):
        build_spec(TEMPLATE, inputs={"watchlist": ""})
    with pytest.raises(ValueError, match="watchlist"):
        build_spec(TEMPLATE)


def test_build_spec_builds_daily_agent_task() -> None:
    now = datetime(2026, 8, 24, 15, 0)  # after 14:00 -> next day
    spec = build_spec(TEMPLATE, inputs={"watchlist": "NVDA, AAPL"}, locale="de", now=now)
    assert spec.created_by == "template"
    assert "template:stock_tracker" in spec.tags
    assert spec.title == "Täglicher Aktien-Tracker"  # i18n-allow
    assert spec.trigger.type == "every"
    assert spec.trigger.interval_seconds == 86_400
    assert spec.trigger.start_at == "2026-08-25T14:00:00"
    assert spec.action.kind == "agent"
    assert "NVDA, AAPL" in spec.action.prompt
    assert spec.action.plugin_grants == TEMPLATE.plugin_grants
