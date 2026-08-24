"""The ``repo_pulse`` automation template validates, renders and builds a spec."""
from __future__ import annotations

from datetime import datetime

import pytest

from jarvis.tasks.templates import (
    LOCALES,
    AutomationTemplate,
    all_templates,
    build_spec,
    grant_matches,
    missing_requirements,
    render_prompt,
)
from jarvis.tasks.templates.repo_pulse import TEMPLATE


def test_template_is_discovered_and_well_formed() -> None:
    assert isinstance(TEMPLATE, AutomationTemplate)
    assert all_templates(refresh=True)["repo_pulse"] is TEMPLATE
    assert TEMPLATE.category == "developer"
    assert TEMPLATE.icon == "git-branch"
    assert TEMPLATE.schedule.kind == "daily"
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
    assert granted == {"github"}
    assert all(g.scope == "read" for g in TEMPLATE.plugin_grants)


def test_prefix_grant_covers_bridged_github_tools() -> None:
    live = ["gmail", "github/list_commits", "github/list_pull_requests", "github/list_issues"]
    assert grant_matches("github", "github/list_commits")
    assert not grant_matches("github", "githubish/list_commits")
    assert missing_requirements(TEMPLATE.requires, live) == []
    assert missing_requirements(TEMPLATE.requires, ["gmail", "search_web"]) == ["github"]


def test_prompt_renders_repo() -> None:
    rendered = render_prompt(TEMPLATE, {"repo": "octo/hello"})
    assert "`octo/hello`" in rendered
    assert "{repo}" not in rendered
    for tool in ("github/list_commits", "github/list_pull_requests", "github/list_issues"):
        assert tool in rendered
    assert "24 hours" in rendered
    assert "12 lines" in rendered


def test_build_spec_requires_repo() -> None:
    with pytest.raises(ValueError, match="repo"):
        build_spec(TEMPLATE, inputs={})
    with pytest.raises(ValueError, match="repo"):
        build_spec(TEMPLATE, inputs={"repo": ""})
    with pytest.raises(ValueError, match="repo"):
        build_spec(TEMPLATE)


def test_build_spec_builds_daily_agent_task() -> None:
    now = datetime(2026, 8, 24, 12, 0)
    spec = build_spec(TEMPLATE, inputs={"repo": "octo/hello"}, locale="de", now=now)
    assert spec.created_by == "template"
    assert "template:repo_pulse" in spec.tags
    assert spec.title == "Repo-Puls"  # i18n-allow
    assert spec.trigger.type == "every"
    assert spec.trigger.interval_seconds == 86_400
    assert spec.trigger.start_at == "2026-08-25T09:00:00"
    assert spec.action.kind == "agent"
    assert "octo/hello" in spec.action.prompt
    assert spec.action.plugin_grants == TEMPLATE.plugin_grants
