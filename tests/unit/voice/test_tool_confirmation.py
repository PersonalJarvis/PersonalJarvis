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


class TestImpactQuestions:
    """Explain layer (2026-08-08): when the deferring tool classified its
    command, the question says WHAT would happen — in plain language."""

    def test_destructive_de_names_the_danger_and_the_command(self) -> None:
        q = format_tool_confirmation(
            "run_shell", language="de",
            impact_level="destructive", impact_commands="rm",
        )
        assert "löschen" in q.lower()  # i18n-allow — quotes the German product surface
        assert "rm" in q
        assert "ja" in q.lower()  # the confirm cue

    def test_modify_and_read_have_calmer_wording(self) -> None:
        modify = format_tool_confirmation(
            "run_shell", language="de",
            impact_level="modify", impact_commands="mkdir",
        )
        read = format_tool_confirmation(
            "run_shell", language="de",
            impact_level="read", impact_commands="ls",
        )
        assert "verändern" in modify.lower() and "mkdir" in modify  # i18n-allow
        assert "liest" in read.lower() and "ls" in read
        assert "löschen" not in modify.lower()  # i18n-allow — German surface quote
        assert "löschen" not in read.lower()  # i18n-allow — German surface quote

    @pytest.mark.parametrize("level", ["destructive", "modify", "read"])
    @pytest.mark.parametrize("lang", ["de", "en", "es"])
    def test_every_impact_level_covers_all_three_languages(
        self, level: str, lang: str
    ) -> None:
        q = format_tool_confirmation(
            "run_shell", language=lang,
            impact_level=level, impact_commands="rm",
        )
        assert q.strip() != ""
        assert "?" in q
        assert "{commands}" not in q  # placeholder must always resolve

    def test_missing_commands_leaves_no_placeholder_residue(self) -> None:
        q = format_tool_confirmation(
            "run_shell", language="de", impact_level="destructive",
        )
        assert "{commands}" not in q
        assert "()" not in q
        assert q.strip() != ""

    def test_unknown_impact_level_falls_back_to_tool_or_generic(self) -> None:
        q = format_tool_confirmation(
            "gmail", language="de",
            impact_level="bogus", impact_commands="rm",
        )
        # Unknown level is ignored — the gmail-specific question wins.
        assert "E-Mail" in q

    def test_command_list_is_collapsed_and_bounded(self) -> None:
        q = format_tool_confirmation(
            "run_shell", language="en",
            impact_level="destructive",
            impact_commands="  rm \n del  " + "x" * 500,
        )
        assert "\n" not in q
        assert len(q) < 220


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
