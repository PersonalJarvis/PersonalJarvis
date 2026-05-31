"""Unit tests for optimistic/tools.py — Sub-Agent 1 (v2 rewrite).

SmartTool has been removed; tests for it are removed accordingly.
Tests now cover DumbTool, check_missing_info, and factory helpers.
All tests are sync using asyncio.run(). No pytest-asyncio.
"""
from __future__ import annotations

import asyncio

from optimistic.events import CorrectionReason

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# DumbTool tests — unchanged behaviour, still kept
# ---------------------------------------------------------------------------

class TestDumbTool:
    def test_fire_returns_non_empty_string(self):
        """DumbTool.fire must return a non-empty string."""
        from optimistic.tools import DumbTool
        tool = DumbTool("play_music")
        result = run(tool.fire("spiel Spotify"))
        assert isinstance(result, str)
        assert len(result) > 0

    def test_fire_returns_fast(self):
        """DumbTool.fire must complete almost instantly (no real sleep)."""
        import time

        from optimistic.tools import DumbTool
        tool = DumbTool("volume")
        start = time.monotonic()
        run(tool.fire("lauter"))
        elapsed = time.monotonic() - start
        assert elapsed < 0.1, f"DumbTool.fire took {elapsed:.3f}s — must be instant"

    def test_fire_includes_tool_name(self):
        """The result should reference the tool name for clarity."""
        from optimistic.tools import DumbTool
        tool = DumbTool("adjusties")
        result = run(tool.fire("adjusties machen"))
        assert "adjusties" in result.lower()

    def test_fire_different_commands(self):
        """DumbTool.fire works for arbitrary command strings."""
        from optimistic.tools import DumbTool
        tool = DumbTool("play_music")
        for cmd in ["spiel was", "play rock", "musik an"]:
            result = run(tool.fire(cmd))
            assert isinstance(result, str)
            assert result  # non-empty


# ---------------------------------------------------------------------------
# check_missing_info — new in v2
# ---------------------------------------------------------------------------

class TestCheckMissingInfo:
    def test_missing_info_for_max_with_empty_contacts(self):
        """'Schreib Max eine Mail' + empty contacts → MISSING_INFO tuple."""
        from optimistic.tools import check_missing_info
        result = check_missing_info("Schreib Max eine Mail", {})
        assert result is not None
        reason, detail = result
        assert reason == CorrectionReason.MISSING_INFO
        assert "Max" in detail

    def test_missing_info_for_anna_with_empty_contacts(self):
        """'Schreib Anna eine Mail' + empty contacts → MISSING_INFO."""
        from optimistic.tools import check_missing_info
        result = check_missing_info("Schreib Anna eine kurze Nachricht", {})
        assert result is not None
        reason, detail = result
        assert reason == CorrectionReason.MISSING_INFO
        assert "Anna" in detail

    def test_none_when_name_present_in_contacts(self):
        """Returns None when the recipient IS in contacts (case-insensitive)."""
        from optimistic.tools import check_missing_info
        result = check_missing_info(
            "Schreib Max eine Mail",
            {"contacts": {"Max": "max@example.com"}},
        )
        assert result is None

    def test_case_insensitive_lookup(self):
        """Contacts lookup is case-insensitive: 'max' matches 'Max'."""
        from optimistic.tools import check_missing_info
        result = check_missing_info(
            "Schreib Max eine Mail",
            {"contacts": {"max": "max@example.com"}},
        )
        assert result is None

    def test_none_when_no_capitalised_name_found(self):
        """'mail senden' has no capitalised name after the first word → None."""
        from optimistic.tools import check_missing_info
        result = check_missing_info("mail senden", {})
        assert result is None

    def test_none_for_single_word_command(self):
        """A single-word command can't have a capitalised word after position 0 → None."""
        from optimistic.tools import check_missing_info
        result = check_missing_info("mailen", {})
        assert result is None

    def test_detail_message_format(self):
        """Detail contains 'no email address on file for {name}'."""
        from optimistic.tools import check_missing_info
        result = check_missing_info("Schreib Max eine Mail", {})
        assert result is not None
        _, detail = result
        assert "no email address on file for" in detail
        assert "Max" in detail

    def test_returns_tuple_with_two_elements(self):
        """Return value is a 2-tuple (CorrectionReason, str)."""
        from optimistic.tools import check_missing_info
        result = check_missing_info("Schreib Max eine Mail", {})
        assert result is not None
        assert len(result) == 2
        assert isinstance(result[0], CorrectionReason)
        assert isinstance(result[1], str)

    def test_empty_contacts_dict_key(self):
        """context without 'contacts' key is the same as empty contacts."""
        from optimistic.tools import check_missing_info
        # context has no 'contacts' key at all
        result = check_missing_info("Schreib Max eine Mail", {"other": "value"})
        assert result is not None

    def test_contacts_as_dict_with_irrelevant_names(self):
        """Max not in contacts even when other names are present."""
        from optimistic.tools import check_missing_info
        result = check_missing_info(
            "Schreib Max eine Mail",
            {"contacts": {"Anna": "anna@x.de", "Bob": "bob@x.de"}},
        )
        assert result is not None
        _, detail = result
        assert "Max" in detail


# ---------------------------------------------------------------------------
# SmartTool is REMOVED — verify it is not importable
# ---------------------------------------------------------------------------

class TestSmartToolRemoved:
    def test_smart_tool_not_in_module(self):
        """SmartTool must have been removed from tools.py in v2."""
        import optimistic.tools as tools_mod
        assert not hasattr(tools_mod, "SmartTool"), (
            "SmartTool should be removed in v2 — worker uses llm.complete now"
        )

    def test_get_smart_tool_not_in_module(self):
        """get_smart_tool factory must also be removed."""
        import optimistic.tools as tools_mod
        assert not hasattr(tools_mod, "get_smart_tool"), (
            "get_smart_tool should be removed in v2"
        )


# ---------------------------------------------------------------------------
# Factory helpers — only DumbTool remains
# ---------------------------------------------------------------------------

class TestFactoryFunctions:
    def test_get_dumb_tool_returns_dumb_tool(self):
        from optimistic.tools import DumbTool, get_dumb_tool
        tool = get_dumb_tool("play_music")
        assert isinstance(tool, DumbTool)
        assert tool.name == "play_music"

    def test_get_dumb_tool_result_is_usable(self):
        """get_dumb_tool result can call .fire() successfully."""
        from optimistic.tools import get_dumb_tool
        tool = get_dumb_tool("volume")
        result = run(tool.fire("leiser"))
        assert result


# ---------------------------------------------------------------------------
# MissingInfoError — kept from v1
# ---------------------------------------------------------------------------

class TestMissingInfoError:
    def test_stores_reason_and_detail(self):
        from optimistic.tools import MissingInfoError
        err = MissingInfoError(CorrectionReason.MISSING_INFO, "no address for Lukas")
        assert err.reason == CorrectionReason.MISSING_INFO
        assert err.detail == "no address for Lukas"

    def test_is_exception_subclass(self):
        from optimistic.tools import MissingInfoError
        assert issubclass(MissingInfoError, Exception)
