"""A tool call picks the pet's action state from the tool's own name."""

from __future__ import annotations

import pytest

from jarvis.ui.pets.actions import action_for_tool
from jarvis.ui.pets.states import ACTION_STATES


@pytest.mark.parametrize(
    "tool",
    [
        "search_web",
        "web_search",
        "read_visible_ui_state",
        "navigate",
        "screen_snapshot",
        "mcp__github__list_issues",
        "mcp__fetch__fetch",
        "wiki-query",
    ],
)
def test_lookups_search(tool: str) -> None:
    assert action_for_tool(tool) == "searching"


@pytest.mark.parametrize(
    "tool",
    ["run_shell", "type_text", "gmail", "create_artifact", "mcp__github__create_issue", "", "x"],
)
def test_everything_else_works(tool: str) -> None:
    assert action_for_tool(tool) == "working"


def test_the_server_name_of_an_mcp_tool_does_not_count() -> None:
    # "search" names the server here, not what the tool does.
    assert action_for_tool("mcp__search__delete_index") == "working"


def test_every_result_is_an_action_state() -> None:
    assert {action_for_tool("search_web"), action_for_tool("run_shell")} == set(ACTION_STATES)
