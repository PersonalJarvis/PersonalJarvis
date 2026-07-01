"""Unit tests for optimistic/tools.py — SA2 (MCP & Tooling).

TDD-first. All tests are sync functions that drive async code via asyncio.run().
No pytest-asyncio, no third-party deps.
"""
from __future__ import annotations

import asyncio

import pytest
from optimistic.events import CorrectionReason

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    """Run a coroutine synchronously (cloud-first: no extra dep)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# DumbTool tests
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
        # Dumb tools must be sub-100ms — they do no I/O.
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
# SmartTool tests
# ---------------------------------------------------------------------------

class TestSmartTool:
    def test_execute_returns_string(self):
        """SmartTool.execute returns a non-empty string for a normal command."""
        from optimistic.tools import SmartTool
        tool = SmartTool("calendar", work_seconds=0.01)
        result = run(tool.execute("Termin morgen um 10", {}))
        assert isinstance(result, str)
        assert result

    def test_execute_simulates_latency(self):
        """SmartTool.execute must await (at least some) latency."""
        import time

        from optimistic.tools import SmartTool
        # Use a tiny but measurable delay.
        tool = SmartTool("drive", work_seconds=0.05)
        start = time.monotonic()
        run(tool.execute("hochladen", {}))
        elapsed = time.monotonic() - start
        # Should take at least ~80% of work_seconds.
        assert elapsed >= 0.04, f"Expected latency simulation, got {elapsed:.3f}s"

    def test_execute_normal_gmail_with_contact(self):
        """Gmail tool succeeds when the recipient IS in contacts."""
        from optimistic.tools import SmartTool
        tool = SmartTool("gmail", work_seconds=0.01)
        context = {"contacts": {"Max": "max@example.com"}}
        result = run(tool.execute("Schreib Max eine Mail", context))
        assert isinstance(result, str)
        assert result

    def test_execute_generic_command(self):
        """SmartTool works for any arbitrary tool name."""
        from optimistic.tools import SmartTool
        tool = SmartTool("some_mcp_tool", work_seconds=0.01)
        result = run(tool.execute("do something", {}))
        assert isinstance(result, str)
        assert result


# ---------------------------------------------------------------------------
# Gmail / MissingInfoError tests (canonical "Max" scenario)
# ---------------------------------------------------------------------------

class TestGmailMissingInfo:
    def test_gmail_raises_missing_info_when_recipient_not_in_contacts(self):
        """Canonical: 'Schreib Max eine Mail' with empty contacts raises
        MissingInfoError(MISSING_INFO)."""
        from optimistic.tools import MissingInfoError, SmartTool
        tool = SmartTool("gmail", work_seconds=0.01)
        with pytest.raises(MissingInfoError) as exc_info:
            run(tool.execute("Schreib Max eine Mail", {}))
        err = exc_info.value
        assert err.reason == CorrectionReason.MISSING_INFO
        assert "Max" in err.detail

    def test_gmail_detail_contains_recipient_name(self):
        """The MissingInfoError detail must contain the capitalised recipient name."""
        from optimistic.tools import MissingInfoError, SmartTool
        tool = SmartTool("gmail", work_seconds=0.01)
        with pytest.raises(MissingInfoError) as exc_info:
            run(tool.execute("Schreib Anna eine kurze Nachricht", {}))  # i18n-allow: test content — user voice utterance DE
        err = exc_info.value
        assert "Anna" in err.detail

    def test_gmail_no_error_when_contact_present(self):
        """Gmail succeeds (no exception) when recipient is in the contacts dict."""
        from optimistic.tools import SmartTool
        tool = SmartTool("gmail", work_seconds=0.01)
        context = {"contacts": {"Max": "max@x.de"}}
        # Must NOT raise
        result = run(tool.execute("Schreib Max eine Mail", context))
        assert isinstance(result, str)
        assert result

    def test_gmail_contact_lookup_is_case_insensitive(self):
        """Contacts dict lookup is case-insensitive per spec."""
        from optimistic.tools import SmartTool
        tool = SmartTool("gmail", work_seconds=0.01)
        # Contacts has "max" in lowercase; command has "Max" capitalised.
        context = {"contacts": {"max": "max@x.de"}}
        result = run(tool.execute("Schreib Max eine Mail", context))
        assert result  # should succeed, not raise

    def test_gmail_no_recipient_no_error(self):
        """When no capitalised recipient word is found, no MissingInfoError is raised."""
        from optimistic.tools import SmartTool
        tool = SmartTool("gmail", work_seconds=0.01)
        # Command has no capitalised word after the first word.
        result = run(tool.execute("mail senden", {}))
        assert isinstance(result, str)

    def test_missing_info_error_stores_reason_and_detail(self):
        """MissingInfoError stores .reason and .detail attributes."""
        from optimistic.tools import MissingInfoError
        err = MissingInfoError(CorrectionReason.MISSING_INFO, "no address for Lukas")
        assert err.reason == CorrectionReason.MISSING_INFO
        assert err.detail == "no address for Lukas"


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

class TestFactoryFunctions:
    def test_get_dumb_tool_returns_dumb_tool(self):
        from optimistic.tools import DumbTool, get_dumb_tool
        tool = get_dumb_tool("play_music")
        assert isinstance(tool, DumbTool)
        assert tool.name == "play_music"

    def test_get_smart_tool_returns_smart_tool(self):
        from optimistic.tools import SmartTool, get_smart_tool
        tool = get_smart_tool("gmail")
        assert isinstance(tool, SmartTool)
        assert tool.name == "gmail"

    def test_get_smart_tool_none_returns_generic(self):
        """get_smart_tool(None) returns a usable SmartTool."""
        from optimistic.tools import SmartTool, get_smart_tool
        tool = get_smart_tool(None)
        assert isinstance(tool, SmartTool)
        result = run(tool.execute("do something", {}))
        assert result
