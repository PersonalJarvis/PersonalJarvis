"""Tests for the perceived-latency-reduction acknowledgment generator.

Coverage map:

* per-tool-family templates produce a sensible ack
* skip-list returns ``None`` (caller suppresses the announcement)
* unknown tool names fall back to the generic ack instead of raising
* hyphen vs underscore tool names normalize to the same handler
* cli_<name> prefix dispatch routes to the shell ack
* language switching de/en
* deterministic output: same args => same string
* templates never raise even with absurd / missing args
* ``should_prepend_marker`` skips when brain self-confirms
"""
from __future__ import annotations

import pytest

from jarvis.brain.ack_generator import (
    ACK_SKIP_TOOLS,
    final_summary_marker,
    generate_ack,
    is_voice_control_utterance,
    should_prepend_marker,
)


class TestVoiceControlDetection:
    @pytest.mark.parametrize(
        "utterance",
        [
            "lauter",
            "leiser",
            "mach lauter",
            "mach leiser",
            "Mach leiser",
            "Pause",
            "pausier",
            "pausiere",
            "pausier mal",
            "stop",
            "stop sprechen",
            "stop reden",
            "halt",
            "halt mal",
            "sei still",
            "sei still bitte",
            "schweig",
            "stumm",
            "stumm schalten",
            "louder",
            "quieter",
            "be quiet",
            "be quiet please",
            "shut up",
            "stop talking",
            "stop speaking",
            "mute",
            "mute yourself",
            "volume up",
            "volume down",
        ],
    )
    def test_voice_control_phrases_match(self, utterance: str) -> None:
        assert is_voice_control_utterance(utterance) is True

    @pytest.mark.parametrize(
        "utterance",
        [
            "",
            "  ",
            "hallo jarvis",
            "wie geht's dir",
            "oeffne notepad",
            "recherchier zu Supabase",
            "baue eine app",
            "wechsel auf gemini",
            "denk gruendlich",
            "what is the capital of france",
            "still im gespraech",  # 'still' inside other context — no leading match  # i18n-allow
            "lauter applaus war zu hoeren",  # noun, not command
        ],
    )
    def test_non_voice_control_does_not_match(self, utterance: str) -> None:
        assert is_voice_control_utterance(utterance) is False

    def test_none_input_safe(self) -> None:
        assert is_voice_control_utterance(None) is False


class TestSkipList:
    @pytest.mark.parametrize(
        "tool_name",
        sorted(ACK_SKIP_TOOLS),
    )
    def test_skip_list_returns_none(self, tool_name: str) -> None:
        assert generate_ack(tool_name, {}) is None

    def test_hyphenated_skip_tool_also_returns_none(self) -> None:
        # Awareness-snapshot is registered with hyphens in ROUTER_TOOLS;
        # normalization must catch both spellings.
        assert generate_ack("awareness-snapshot", {}) is None
        assert generate_ack("screen-snapshot", {}) is None

    def test_empty_tool_name_returns_none(self) -> None:
        assert generate_ack("", {}) is None
        assert generate_ack("   ", {}) is None


class TestPerToolTemplates:
    def test_dispatch_to_harness_de(self) -> None:
        ack = generate_ack("dispatch_to_harness", {"task": "build a Flask app"}, language="de")
        assert ack == "Verstanden, ich kuemmere mich darum."

    def test_dispatch_to_harness_en(self) -> None:
        ack = generate_ack("dispatch_to_harness", {"task": "build a Flask app"}, language="en")
        assert ack == "Got it, on it."

    def test_hyphen_form_dispatches_to_same_handler(self) -> None:
        a = generate_ack("dispatch-to-harness", {"task": "x"}, language="de")
        b = generate_ack("dispatch_to_harness", {"task": "x"}, language="de")
        assert a == b

    def test_dispatch_with_review_uses_harness_template(self) -> None:
        ack = generate_ack("dispatch_with_review", {"task": "audit"}, language="de")
        assert ack == "Verstanden, ich kuemmere mich darum."

    def test_run_shell_is_minimal(self) -> None:
        # Shell commands are technical noise — never spoken back at the user.
        assert generate_ack("run_shell", {"command": "rm -rf /"}, language="de") == "Moment."
        assert generate_ack("run_shell", {"command": "ls"}, language="en") == "One moment."

    def test_search_web_echoes_short_query(self) -> None:
        ack = generate_ack("search_web", {"query": "Supabase"}, language="de")
        assert ack == "Okay, ich schau mir Supabase an."

    def test_search_web_echoes_short_query_en(self) -> None:
        ack = generate_ack("search_web", {"query": "kubernetes"}, language="en")
        assert ack == "Okay, looking up kubernetes."

    def test_search_web_falls_back_for_long_query(self) -> None:
        long_query = "what is the difference between react server components and " \
            "client components in nextjs 16"
        ack = generate_ack("search_web", {"query": long_query}, language="de")
        assert ack == "Okay, ich recherchiere."

    def test_search_web_accepts_q_alias(self) -> None:
        ack = generate_ack("search_web", {"q": "Vercel"}, language="de")
        assert ack == "Okay, ich schau mir Vercel an."

    def test_spawn_sub_jarvis_is_warm_generic(self) -> None:
        ack = generate_ack(
            "spawn_sub_jarvis",
            {"utterance": "build a Flask app", "action": "eine Flask-App baut"},
            language="de",
        )
        assert ack == "Verstanden, ich kuemmere mich drum."

    def test_multi_spawn_with_count(self) -> None:
        ack = generate_ack(
            "multi_spawn",
            {"tasks": [{"task": "a"}, {"task": "b"}, {"task": "c"}]},
            language="de",
        )
        assert ack == "Okay, ich erledige 3 Sachen parallel."

    def test_multi_spawn_with_one_task_uses_generic(self) -> None:
        ack = generate_ack("multi_spawn", {"tasks": [{"task": "x"}]}, language="de")
        assert ack == "Okay, einen Moment."

    def test_open_app_echoes_app_name(self) -> None:
        assert generate_ack("open_app", {"app": "Notepad"}, language="de") == "Okay, ich oeffne Notepad."
        assert generate_ack("open_app", {"app_name": "Chrome"}, language="en") == "Okay, opening Chrome."

    def test_run_skill_echoes_skill_name(self) -> None:
        ack = generate_ack("run_skill", {"skill": "weather"}, language="de")
        assert ack == "Okay, ich starte weather."

    def test_remember_acks_storage(self) -> None:
        assert generate_ack("remember", {"text": "x"}, language="de") == "Okay, ich merk's mir."

    def test_verify_tools_share_template(self) -> None:
        a = generate_ack("verify_via_curl", {"url": "x"}, language="de")
        b = generate_ack("verify_localhost", {"port": 8000}, language="de")
        assert a == b == "Okay, ich pruefe das."

    def test_set_config_value_acks_change(self) -> None:
        ack = generate_ack(
            "set_config_value",
            {"key": "tts.provider", "value": "grok"},
            language="de",
        )
        assert ack == "Okay, ich aendere das."


class TestCliPrefixDispatch:
    @pytest.mark.parametrize(
        "tool_name",
        ["cli_supabase", "cli_gh", "cli_vercel", "cli_aws", "cli_docker"],
    )
    def test_cli_aliases_use_shell_ack(self, tool_name: str) -> None:
        ack = generate_ack(tool_name, {"command": "supabase projects list"}, language="de")
        assert ack == "Moment."

    def test_cli_tools_explicit_entry_works(self) -> None:
        ack = generate_ack("cli_tools", {}, language="de")
        assert ack == "Moment."


class TestUnknownToolFallback:
    def test_unknown_tool_returns_generic(self) -> None:
        ack = generate_ack("definitely_not_a_real_tool", {}, language="de")
        assert ack == "Okay, einen Moment."

    def test_unknown_tool_returns_generic_en(self) -> None:
        ack = generate_ack("nonsense_tool", {}, language="en")
        assert ack == "Okay, one moment."

    def test_unknown_tool_does_not_raise_on_garbage_args(self) -> None:
        # Args with weird types must not break the dispatcher.
        ack = generate_ack("unknown", {"x": object(), "y": [1, 2]}, language="de")
        assert ack == "Okay, einen Moment."

    def test_none_args_safe(self) -> None:
        ack = generate_ack("dispatch_to_harness", None, language="de")
        assert ack == "Verstanden, ich kuemmere mich darum."


class TestDeterminism:
    def test_same_input_same_output(self) -> None:
        # Generate three times — must be byte-identical (no random, no time).
        args = {"app": "Notepad"}
        results = {generate_ack("open_app", args, language="de") for _ in range(3)}
        assert len(results) == 1


class TestLanguageHandling:
    @pytest.mark.parametrize("hint", ["en", "EN", "en-US", "english", "en-GB"])
    def test_english_hints_resolve_to_en(self, hint: str) -> None:
        ack = generate_ack("run_shell", {}, language=hint)
        assert ack == "One moment."

    @pytest.mark.parametrize("hint", ["de", "DE", "de-DE", "german", "deutsch", "fr", "es", ""])
    def test_non_english_hints_default_to_de(self, hint: str) -> None:
        # Project default is German; unrecognized hints fall to de.
        ack = generate_ack("run_shell", {}, language=hint)
        assert ack == "Moment."

    def test_none_language_defaults_to_de(self) -> None:
        ack = generate_ack("run_shell", {}, language=None)  # type: ignore[arg-type]
        assert ack == "Moment."


class TestFinalSummaryMarker:
    def test_german_default(self) -> None:
        assert final_summary_marker("de") == "Erledigt."

    def test_english(self) -> None:
        assert final_summary_marker("en") == "Done."

    def test_unknown_language_defaults_to_de(self) -> None:
        assert final_summary_marker("fr") == "Erledigt."


class TestShouldPrependMarker:
    def test_empty_text_wants_marker(self) -> None:
        assert should_prepend_marker("") is True
        assert should_prepend_marker("   ") is True
        assert should_prepend_marker(None) is True

    def test_substantive_text_wants_marker(self) -> None:
        assert should_prepend_marker("Die App laeuft auf Port 8000.") is True  # i18n-allow
        assert should_prepend_marker("Ich habe drei Dateien gefunden.") is True

    @pytest.mark.parametrize(
        "opener",
        [
            "Erledigt — die App laeuft.",  # i18n-allow
            "Fertig.",  # i18n-allow
            "Okay, das hab ich gemacht.",
            "OK, sieht gut aus.",
            "Done. The server is up.",
            "Got it — three results.",
            "Verstanden, du wirst geliked.",
            "In Ordnung, das uebernehme ich.",
            "Sure, here you go.",
            "Alright, lemme check.",
        ],
    )
    def test_self_confirming_openers_skip_marker(self, opener: str) -> None:
        assert should_prepend_marker(opener) is False

    def test_word_boundary_required(self) -> None:
        # "Okayama is a city" — 'Okayama' is not 'okay'. Word boundary.
        assert should_prepend_marker("Okayama is a city in Japan.") is True
        # 'Doneness' is not 'done'.
        assert should_prepend_marker("Doneness varies by recipe.") is True
