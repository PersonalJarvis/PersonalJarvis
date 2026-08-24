"""The ``ai_news_digest`` automation template — validates, builds a spec,
renders its prompt, and is discoverable through the catalogue."""
from __future__ import annotations

from datetime import datetime

from jarvis.tasks.schema import TriggerEvery
from jarvis.tasks.templates import (
    LOCALES,
    all_templates,
    build_spec,
    missing_requirements,
    render_prompt,
)
from jarvis.tasks.templates.ai_news_digest import TEMPLATE

_DEFAULT_FOCUS = "model releases, open-source models, AI research, AI funding"


def test_identity_and_catalogue_placement() -> None:
    assert TEMPLATE.key == "ai_news_digest"
    assert TEMPLATE.category == "news"
    assert TEMPLATE.icon == "bot"
    assert TEMPLATE.schedule.kind == "daily"
    assert TEMPLATE.schedule.time == "08:00"


def test_discovered_by_catalogue() -> None:
    assert all_templates(refresh=True)["ai_news_digest"] is TEMPLATE


def test_copy_present_in_every_locale() -> None:
    for locale in LOCALES:
        assert TEMPLATE.name.for_locale(locale).strip()
        assert TEMPLATE.description.for_locale(locale).strip()
        for inp in TEMPLATE.inputs:
            assert inp.label.for_locale(locale).strip()
    assert TEMPLATE.name.for_locale("en") == "AI News Digest"


def test_requires_is_subset_of_grants_and_read_only() -> None:
    granted = {g.plugin_id for g in TEMPLATE.plugin_grants}
    assert set(TEMPLATE.requires) <= granted
    assert granted == {"search_web"}
    assert all(g.scope == "read" for g in TEMPLATE.plugin_grants)


def test_readiness_against_live_tools() -> None:
    assert missing_requirements(TEMPLATE.requires, ["search_web", "gmail"]) == []
    assert missing_requirements(TEMPLATE.requires, ["gmail"]) == ["search_web"]


def test_prompt_renders_default_focus_and_leaves_no_placeholder() -> None:
    text = render_prompt(TEMPLATE, None)
    assert _DEFAULT_FOCUS in text
    assert "{focus}" not in text
    custom = render_prompt(TEMPLATE, {"focus": "robotics"})
    assert "robotics" in custom
    assert _DEFAULT_FOCUS not in custom


def test_prompt_states_the_contract() -> None:
    text = TEMPLATE.prompt
    assert "search_web" in text
    assert "at most 6" in text
    assert "48" in text
    assert "no emojis" in text
    assert "configured output language" in text
    assert "no preamble" in text


def test_build_spec_without_inputs() -> None:
    now = datetime(2026, 8, 24, 9, 30)
    spec = build_spec(TEMPLATE, locale="de", now=now)
    assert spec.title == "KI-News-Digest"
    assert spec.created_by == "template"
    assert "template:ai_news_digest" in spec.tags
    assert spec.action.kind == "agent"
    assert spec.action.plugin_grants == TEMPLATE.plugin_grants
    assert _DEFAULT_FOCUS in spec.action.prompt
    assert isinstance(spec.trigger, TriggerEvery)
    assert spec.trigger.interval_seconds == 86_400
    # 09:30 is past 08:00, so the first run is tomorrow at 08:00.
    assert spec.trigger.start_at == "2026-08-25T08:00:00"
    assert spec.model_dump(mode="json")["action"]["prompt"] == spec.action.prompt
