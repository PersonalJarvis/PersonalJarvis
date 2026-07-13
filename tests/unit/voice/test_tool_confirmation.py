"""Tests for ``jarvis.voice.tool_confirmation`` — the generic, channel-agnostic
voice/text confirmation phrasing for an ``ask``-tier tool that is being run
through the two-turn confirmation flow.

Root cause this module is part of (2026-06-18, session 2995997b): an ``ask``-tier
tool (gmail) invoked on the voice path blocks in ``ApprovalWorkflow.wait()`` for a
UI approval the voice user never gives; the 20 s no-first-frame ceiling then
beheads the turn and speaks the brain-timeout fallback. Instead of hanging,
Jarvis now SPEAKS a short confirmation question and the next "ja"/"nein" resolves
it.

Runtime Output Language doctrine: every spoken phrase carries de/en/es.
"""
from __future__ import annotations

import pytest

from jarvis.voice.tool_confirmation import (
    format_confirm_outcome,
    format_tool_confirmation,
)


class TestFormatToolConfirmation:
    def test_known_tool_de_is_a_german_question_about_the_action(self) -> None:
        q = format_tool_confirmation("gmail", language="de")
        assert q.endswith("?") or "?" in q
        # End-Focus: the action sits late in the sentence so an STT misshear is
        # obvious. The German send-email question mentions the email + sending.
        assert "E-Mail" in q
        assert "senden" in q.lower()

    def test_known_tool_en_is_an_english_question(self) -> None:
        q = format_tool_confirmation("gmail", language="en")
        assert "?" in q
        assert "email" in q.lower()
        assert "send" in q.lower()

    def test_known_tool_es_is_a_spanish_question(self) -> None:
        q = format_tool_confirmation("gmail", language="es")
        assert "?" in q
        # Spanish marker — inverted question mark or "correo"/"enviar".
        assert "¿" in q
        assert "correo" in q.lower() or "enviar" in q.lower()

    def test_unknown_tool_falls_back_to_a_generic_question(self) -> None:
        q = format_tool_confirmation("some_unmapped_tool", language="de")
        assert "?" in q
        # Generic German phrasing — no tool-specific noun, still a real question.
        assert q.strip() != ""
        assert "ja" in q.lower()  # the confirm cue "Sag ja ..."

    def test_generic_fallback_covers_all_three_languages(self) -> None:
        for lang in ("de", "en", "es"):
            q = format_tool_confirmation("unmapped", language=lang)
            assert "?" in q
            assert q.strip() != ""

    def test_unrecognised_language_falls_back_to_default_locale_not_empty(self) -> None:
        # A bogus tag must not yield an empty string (zero-silent-drop): it
        # resolves to the default locale's phrase.
        q = format_tool_confirmation("gmail", language="zz")
        assert q.strip() != ""
        assert "?" in q


class TestFormatConfirmOutcome:
    def test_done_de_is_a_short_confirmation(self) -> None:
        msg = format_confirm_outcome("done", "gmail", language="de")
        assert msg.strip() != ""
        # "Erledigt." is the canonical butler confirmation (output_filter).
        assert "erledigt" in msg.lower() or "gesendet" in msg.lower()

    def test_vetoed_de_acknowledges_the_cancel(self) -> None:
        msg = format_confirm_outcome("vetoed", "gmail", language="de")
        assert msg.strip() != ""
        assert "lass" in msg.lower() or "okay" in msg.lower()

    def test_timeout_de_is_honest_about_no_answer(self) -> None:
        msg = format_confirm_outcome("timeout", "gmail", language="de")
        assert msg.strip() != ""

    def test_failed_de_is_honest_about_the_failure(self) -> None:
        msg = format_confirm_outcome("failed", "gmail", language="de")
        assert msg.strip() != ""

    @pytest.mark.parametrize("kind", ["done", "vetoed", "timeout", "failed"])
    @pytest.mark.parametrize("lang", ["de", "en", "es"])
    def test_every_outcome_covers_all_three_languages(self, kind: str, lang: str) -> None:
        msg = format_confirm_outcome(kind, "gmail", language=lang)
        assert msg.strip() != ""

    def test_failed_appends_the_actionable_reason(self) -> None:
        msg = format_confirm_outcome(
            "failed",
            "manage-mcp-server",
            language="de",
            detail="no MCP server named 'github' — configured MCP servers: notebooklm",
        )
        assert "github" in msg
        assert "notebooklm" in msg

    def test_failed_detail_is_collapsed_and_bounded(self) -> None:
        msg = format_confirm_outcome(
            "failed", "gmail", language="en", detail="  a\n\n b  " + "x" * 500
        )
        assert "\n" not in msg
        assert len(msg) < 220

    def test_done_never_carries_a_detail(self) -> None:
        msg = format_confirm_outcome(
            "done", "gmail", language="en", detail="should not appear"
        )
        assert "should not appear" not in msg
