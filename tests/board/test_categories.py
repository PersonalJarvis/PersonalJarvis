"""Tests for the Board usage-category mapping (``jarvis/board/categories.py``).

The six categories answer "what did you use Jarvis FOR" and are derived from
the tools that actually ran. These tests pin the mapping for the real tool
names observed in ``data/sessions.db`` so a future rename of a tool surfaces
here instead of silently mis-bucketing usage.
"""
from __future__ import annotations

import pytest

from jarvis.board.categories import (
    BOARD_CATEGORY_KEYS,
    categorize_tool,
)


def test_six_stable_keys_in_order() -> None:
    assert BOARD_CATEGORY_KEYS == (
        "agents",
        "browser",
        "mail",
        "community",
        "knowledge",
        "system",
    )


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        # Agents — dispatch + worker execution (the dominant real bucket).
        ("spawn_openclaw", "agents"),
        ("spawn_sub_jarvis", "agents"),
        ("spawn_worker", "agents"),
        ("multi_spawn", "agents"),
        ("dispatch_to_harness", "agents"),
        ("dispatch_with_review", "agents"),
        ("run-skill", "agents"),
        ("run_shell", "agents"),
        ("cli_gcloud", "agents"),
        ("cli_gh", "agents"),
        # Browser / on-screen automation.
        ("click", "browser"),
        ("open_app", "browser"),
        ("screenshot", "browser"),
        ("type_text", "browser"),
        ("hotkey", "browser"),
        ("computer_use", "browser"),
        ("switch_window", "browser"),
        ("inspect-pointer", "browser"),
        ("wait_for_ui_state", "browser"),
        ("read_visible_ui_state", "browser"),
        ("click_element", "browser"),
        # Knowledge / memory — MUST win over "community" for the "recall"
        # substring (awareness-recall contains "call").
        ("awareness-recall", "knowledge"),
        ("awareness-snapshot", "knowledge"),
        ("wiki-recall", "knowledge"),
        ("wiki-ingest", "knowledge"),
        ("remember", "knowledge"),
        # Community / people / channels.
        ("contact-lookup", "community"),
        ("contact-upsert", "community"),
        ("call-contact", "community"),
        ("discord-send", "community"),
        ("telegram-send", "community"),
        # Mail.
        ("gmail-send", "mail"),
        ("send_email", "mail"),
        # System / config / admin.
        ("list_mutable_settings", "system"),
        ("get_config_value", "system"),
        ("set_config_value", "system"),
        ("describe-app-settings", "system"),
        ("reveal-key-preview", "system"),
    ],
)
def test_categorize_known_tools(tool: str, expected: str) -> None:
    assert categorize_tool(tool) == expected


def test_unknown_tool_falls_back_to_system() -> None:
    assert categorize_tool("some_brand_new_tool_xyz") == "system"


def test_blank_and_none_safe() -> None:
    assert categorize_tool("") == "system"
    assert categorize_tool("   ") == "system"
    assert categorize_tool(None) == "system"  # type: ignore[arg-type]


def test_every_category_is_a_valid_key() -> None:
    for tool in ("spawn_openclaw", "click", "gmail-send", "call-contact",
                 "wiki-recall", "list_mutable_settings", "???"):
        assert categorize_tool(tool) in BOARD_CATEGORY_KEYS
