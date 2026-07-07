"""Tests for the perceived-latency-reduction acknowledgment generator.

Coverage map (2026-07-06 interim-ack redesign — pool-based engine):

* per-tool-family pools produce a sensible, family-correct ack
* every pool covers de/en/es (runtime-output-language doctrine)
* AckPhrasePicker never repeats the same phrase twice in a row
* skip-list returns ``None`` (caller suppresses the announcement)
* unknown tool names fall back to the generic pool instead of raising
* hyphen vs underscore tool names normalize to the same handler
* cli_<name> prefix resolves a human service name (cli_gh -> GitHub)
* templates never raise even with absurd / missing args
* German phrases carry real umlauts (no "ue"/"ae" TTS mispronunciation)
* ``should_prepend_marker`` skips when brain self-confirms
"""
from __future__ import annotations

import pytest

from jarvis.brain.ack_generator import (
    ACK_SKIP_TOOLS,
    _CALENDAR_ACK,
    _CLI_SERVICE_ACK,
    _GENERIC_ACK,
    _GMAIL_READ_ACK,
    _HARNESS_ACK,
    _SEARCH_GENERIC_ACK,
    _SEARCH_TOPIC_ACK,
    _SHELL_ACK,
    AckPhrasePicker,
    describe_tool_action,
    final_summary_marker,
    generate_ack,
    is_voice_control_utterance,
    should_prepend_marker,
)

_ALL_POOLS = (
    _CALENDAR_ACK,
    _CLI_SERVICE_ACK,
    _GENERIC_ACK,
    _GMAIL_READ_ACK,
    _HARNESS_ACK,
    _SEARCH_GENERIC_ACK,
    _SEARCH_TOPIC_ACK,
    _SHELL_ACK,
)


def _fresh() -> AckPhrasePicker:
    """Isolated picker so tests never share no-repeat state."""
    return AckPhrasePicker()


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
        assert generate_ack(tool_name, {}, picker=_fresh()) is None

    def test_hyphenated_skip_tool_also_returns_none(self) -> None:
        # Awareness-snapshot is registered with hyphens in ROUTER_TOOLS;
        # normalization must catch both spellings.
        assert generate_ack("awareness-snapshot", {}, picker=_fresh()) is None
        assert generate_ack("screen-snapshot", {}, picker=_fresh()) is None

    def test_fast_wiki_reads_are_skipped(self) -> None:
        # A ~70 ms passive memory read must not get a spoken "one moment"
        # (2026-07-06 redesign, R6).
        assert generate_ack("wiki-recall", {}, picker=_fresh()) is None
        assert generate_ack("wiki_search", {"q": "x"}, picker=_fresh()) is None

    def test_empty_tool_name_returns_none(self) -> None:
        assert generate_ack("", {}, picker=_fresh()) is None
        assert generate_ack("   ", {}, picker=_fresh()) is None


class TestPerToolPools:
    def test_dispatch_to_harness_de(self) -> None:
        ack = generate_ack(
            "dispatch_to_harness", {"task": "build a Flask app"},
            language="de", picker=_fresh(),
        )
        assert ack in _HARNESS_ACK["de"]

    def test_dispatch_to_harness_en(self) -> None:
        ack = generate_ack(
            "dispatch_to_harness", {"task": "build a Flask app"},
            language="en", picker=_fresh(),
        )
        assert ack in _HARNESS_ACK["en"]

    def test_hyphen_form_dispatches_to_same_pool(self) -> None:
        a = generate_ack("dispatch-to-harness", {"task": "x"}, language="de", picker=_fresh())
        b = generate_ack("dispatch_to_harness", {"task": "x"}, language="de", picker=_fresh())
        assert a in _HARNESS_ACK["de"]
        assert b in _HARNESS_ACK["de"]

    def test_dispatch_with_review_uses_harness_pool(self) -> None:
        ack = generate_ack("dispatch_with_review", {"task": "audit"}, language="de", picker=_fresh())
        assert ack in _HARNESS_ACK["de"]

    def test_run_shell_never_echoes_the_command(self) -> None:
        # Shell commands are technical noise — never spoken back at the user.
        ack = generate_ack(
            "run_shell", {"command": "rm -rf /tmp/x"}, language="de", picker=_fresh()
        )
        assert ack in _SHELL_ACK["de"]
        assert "rm -rf" not in ack

    def test_search_web_echoes_short_query(self) -> None:
        ack = generate_ack("search_web", {"query": "Supabase"}, language="de", picker=_fresh())
        assert "Supabase" in ack
        assert ack in tuple(
            p.format(topic="Supabase") for p in _SEARCH_TOPIC_ACK["de"]
        )

    def test_search_web_echoes_short_query_en(self) -> None:
        ack = generate_ack("search_web", {"query": "kubernetes"}, language="en", picker=_fresh())
        assert "kubernetes" in ack

    def test_search_web_falls_back_for_long_query(self) -> None:
        long_query = (
            "what is the difference between react server components and "
            "client components in nextjs 16"
        )
        ack = generate_ack("search_web", {"query": long_query}, language="de", picker=_fresh())
        assert ack in _SEARCH_GENERIC_ACK["de"]

    def test_search_web_falls_back_for_sentence_query(self) -> None:
        # Short but sentence-shaped (many words) — reading it back sounds robotic.
        ack = generate_ack(
            "search_web", {"query": "who won the game today"},
            language="en", picker=_fresh(),
        )
        assert ack in _SEARCH_GENERIC_ACK["en"]

    def test_search_web_accepts_q_alias(self) -> None:
        ack = generate_ack("search_web", {"q": "Vercel"}, language="de", picker=_fresh())
        assert "Vercel" in ack

    def test_spawn_sub_jarvis_is_warm_generic(self) -> None:
        ack = generate_ack(
            "spawn_sub_jarvis",
            {"utterance": "build a Flask app", "action": "eine Flask-App baut"},
            language="de",
            picker=_fresh(),
        )
        assert ack in _HARNESS_ACK["de"]

    def test_multi_spawn_with_count(self) -> None:
        ack = generate_ack(
            "multi_spawn",
            {"tasks": [{"task": "a"}, {"task": "b"}, {"task": "c"}]},
            language="de",
            picker=_fresh(),
        )
        assert "3" in ack

    def test_multi_spawn_with_one_task_uses_generic(self) -> None:
        ack = generate_ack("multi_spawn", {"tasks": [{"task": "x"}]}, language="de", picker=_fresh())
        assert ack in _GENERIC_ACK["de"]

    def test_open_app_echoes_app_name(self) -> None:
        ack_de = generate_ack("open_app", {"app": "Notepad"}, language="de", picker=_fresh())
        assert "Notepad" in ack_de
        ack_en = generate_ack("open_app", {"app_name": "Chrome"}, language="en", picker=_fresh())
        assert "Chrome" in ack_en

    def test_run_skill_echoes_skill_name(self) -> None:
        ack = generate_ack("run_skill", {"skill": "weather"}, language="de", picker=_fresh())
        assert "weather" in ack

    def test_remember_is_promissory(self) -> None:
        ack = generate_ack("remember", {"text": "x"}, language="de", picker=_fresh())
        assert ack
        # Never a completion claim at selection time — the tool has not run.
        assert "erledigt" not in ack.lower()

    def test_gmail_read_names_the_mailbox(self) -> None:
        ack = generate_ack("gmail", {"action": "list_messages"}, language="de", picker=_fresh())
        assert ack in _GMAIL_READ_ACK["de"]

    def test_gmail_send_stays_generic(self) -> None:
        # A send still goes through echo-confirmation — it must not be
        # mis-announced as a mailbox read.
        ack = generate_ack("gmail", {"action": "send_message"}, language="de", picker=_fresh())
        assert ack in _GENERIC_ACK["de"]

    def test_calendar_names_the_calendar(self) -> None:
        ack = generate_ack("google_calendar", {}, language="de", picker=_fresh())
        assert ack in _CALENDAR_ACK["de"]


class TestCliServiceNames:
    def test_cli_gh_names_github(self) -> None:
        ack = generate_ack("cli_gh", {"command": "gh repo view"}, language="de", picker=_fresh())
        assert "GitHub" in ack

    @pytest.mark.parametrize(
        ("tool_name", "service"),
        [
            ("cli_supabase", "Supabase"),
            ("cli_vercel", "Vercel"),
            ("cli_aws", "AWS"),
            ("cli_docker", "Docker"),
            ("cli_gcloud", "Google Cloud"),
        ],
    )
    def test_known_cli_services_resolve(self, tool_name: str, service: str) -> None:
        ack = generate_ack(tool_name, {}, language="en", picker=_fresh())
        assert service in ack

    def test_unknown_cli_suffix_is_title_cased(self) -> None:
        ack = generate_ack("cli_foobar", {}, language="en", picker=_fresh())
        assert "Foobar" in ack

    def test_cli_never_echoes_the_command(self) -> None:
        ack = generate_ack(
            "cli_gh", {"command": "gh api graphql --paginate"},
            language="de", picker=_fresh(),
        )
        assert "graphql" not in ack

    def test_cli_tools_explicit_entry_uses_shell_pool(self) -> None:
        ack = generate_ack("cli_tools", {}, language="de", picker=_fresh())
        assert ack in _SHELL_ACK["de"]


class TestUnknownToolFallback:
    def test_unknown_tool_returns_generic(self) -> None:
        ack = generate_ack("definitely_not_a_real_tool", {}, language="de", picker=_fresh())
        assert ack in _GENERIC_ACK["de"]

    def test_unknown_tool_returns_generic_en(self) -> None:
        ack = generate_ack("nonsense_tool", {}, language="en", picker=_fresh())
        assert ack in _GENERIC_ACK["en"]

    def test_unknown_tool_does_not_raise_on_garbage_args(self) -> None:
        # Args with weird types must not break the dispatcher.
        ack = generate_ack("unknown", {"x": object(), "y": [1, 2]}, language="de", picker=_fresh())
        assert ack in _GENERIC_ACK["de"]

    def test_none_args_safe(self) -> None:
        ack = generate_ack("dispatch_to_harness", None, language="de", picker=_fresh())
        assert ack in _HARNESS_ACK["de"]


class TestVariety:
    def test_no_two_consecutive_picks_identical(self) -> None:
        # The 2026-07-05 forensic bug: the SAME ack spoken three times in a
        # row. With one shared picker, consecutive picks must always differ.
        picker = _fresh()
        previous = None
        for _ in range(20):
            ack = generate_ack("run_shell", {}, language="de", picker=picker)
            assert ack != previous
            previous = ack

    def test_consecutive_distinct_across_tool_families(self) -> None:
        # The memory is global: a shell ack followed by a cli ack followed by
        # another shell ack must not resurface the identical wording back to
        # back even across families.
        picker = _fresh()
        seen = []
        for tool in ("run_shell", "cli_gh", "run_shell", "cli_gh", "run_shell"):
            seen.append(generate_ack(tool, {}, language="de", picker=picker))
        for a, b in zip(seen, seen[1:]):
            assert a != b

    def test_picker_survives_pool_smaller_than_memory(self) -> None:
        picker = AckPhrasePicker(memory=4)
        pool = ("a", "b")
        previous = None
        for _ in range(10):
            choice = picker.pick(pool)
            assert choice in pool
            assert choice != previous
            previous = choice

    def test_single_item_pool_still_yields(self) -> None:
        picker = AckPhrasePicker()
        assert picker.pick(("only",)) == "only"
        assert picker.pick(("only",)) == "only"


class TestLanguageCoverage:
    @pytest.mark.parametrize("lang", ["de", "en", "es"])
    def test_all_pools_carry_all_supported_languages(self, lang: str) -> None:
        # Runtime-output-language doctrine: a phrase table missing a
        # supported language silently degrades that language's turns.
        for pool in _ALL_POOLS:
            assert pool.get(lang), f"pool missing language {lang!r}"

    @pytest.mark.parametrize(
        "tool",
        [
            "dispatch_to_harness", "run_shell", "search_web", "spawn_sub_jarvis",
            "multi_spawn", "open_app", "run_skill", "gmail", "google_calendar",
            "remember", "verify_via_curl", "start_preview_server",
            "set_config_value", "cli_gh", "unknown_tool",
        ],
    )
    def test_every_family_answers_in_spanish(self, tool: str) -> None:
        # Before the redesign most handlers raised KeyError for 'es' and
        # silently degraded to the generic pool — doctrine violation.
        ack = generate_ack(tool, {"query": "x", "tasks": [1, 2]}, language="es", picker=_fresh())
        assert ack
        for token in ("Okay, einen Moment", "One moment", "Ich "):  # i18n-allow: German leak-detection tokens
            assert token not in ack

    @pytest.mark.parametrize("hint", ["en", "EN", "en-US", "english", "en-GB"])
    def test_english_hints_resolve_to_en(self, hint: str) -> None:
        ack = generate_ack("run_shell", {}, language=hint, picker=_fresh())
        assert ack in _SHELL_ACK["en"]

    @pytest.mark.parametrize("hint", ["de", "DE", "de-DE", "german", "deutsch", "fr", ""])
    def test_non_english_hints_default_to_de(self, hint: str) -> None:
        # Project default is German; unrecognized hints fall to de.
        ack = generate_ack("run_shell", {}, language=hint, picker=_fresh())
        assert ack in _SHELL_ACK["de"]

    def test_spanish_hint_resolves_to_es(self) -> None:
        ack = generate_ack("run_shell", {}, language="es", picker=_fresh())
        assert ack in _SHELL_ACK["es"]

    def test_none_language_defaults_to_de(self) -> None:
        ack = generate_ack("run_shell", {}, language=None, picker=_fresh())  # type: ignore[arg-type]
        assert ack in _SHELL_ACK["de"]


class TestGermanDiacritics:
    def test_no_ascii_umlaut_substitutes_in_any_german_pool(self) -> None:
        # "kuemmere"/"pruefe"/"oeffne" mispronounce on TTS (R5). Real umlauts
        # only. The check is heuristic: the known-bad substituted tokens must
        # not appear in any German phrase.
        bad_tokens = ("kuemmere", "pruefe", "oeffne", "aendere", "ausfuehren", "dafuer")  # i18n-allow: retired ASCII-substituted German tokens under test
        for pool in _ALL_POOLS:
            for phrase in pool["de"]:
                low = phrase.lower()
                for bad in bad_tokens:
                    assert bad not in low, f"ASCII umlaut substitute in {phrase!r}"


class TestDescribeToolAction:
    """English action descriptions consumed as PROMPT INPUT by the contextual
    interim composer (v2 spec) — never spoken verbatim."""

    def test_search_names_the_query(self) -> None:
        desc = describe_tool_action("search_web", {"query": "PrimeRandat github"})
        assert "web search" in desc
        assert "PrimeRandat" in desc

    def test_cli_names_the_service(self) -> None:
        assert "GitHub" in describe_tool_action("cli_gh", {"command": "gh repo view"})
        assert "Google Cloud" in describe_tool_action("cli_gcloud", {})

    def test_cli_never_echoes_the_raw_command(self) -> None:
        desc = describe_tool_action("cli_gh", {"command": "gh api graphql --paginate"})
        assert "graphql" not in desc

    def test_shell_stays_generic(self) -> None:
        desc = describe_tool_action("run_shell", {"command": "rm -rf /tmp/x"})
        assert "rm -rf" not in desc
        assert desc

    def test_spawn_family_describes_the_handover(self) -> None:
        for tool in ("spawn_worker", "spawn_sub_jarvis", "dispatch_to_harness"):
            assert "background helper" in describe_tool_action(tool, {})

    def test_gmail_send_vs_read(self) -> None:
        assert "email" in describe_tool_action("gmail", {"action": "send_message"})
        assert "fetching" in describe_tool_action("gmail", {"action": "list_messages"})

    def test_open_app_names_the_app(self) -> None:
        assert "Notepad" in describe_tool_action("open_app", {"app": "Notepad"})

    def test_unknown_tool_is_neutral_and_total(self) -> None:
        assert describe_tool_action("mystery_tool", {"x": object()})
        assert describe_tool_action("", None)


class TestFinalSummaryMarker:
    def test_german_default(self) -> None:
        assert final_summary_marker("de") == "Erledigt."  # i18n-allow: German completion marker under test

    def test_english(self) -> None:
        assert final_summary_marker("en") == "Done."

    def test_unknown_language_defaults_to_de(self) -> None:
        assert final_summary_marker("fr") == "Erledigt."  # i18n-allow: German completion marker under test


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
