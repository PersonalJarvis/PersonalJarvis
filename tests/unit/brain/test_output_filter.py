"""Tests for ``jarvis.brain.output_filter.scrub_for_voice``.

Persona mandate phase 1: the output filter on the Brain->TTS path scrubs tool
JSON, stacktraces, engineering jargon, self-reference, echo paraphrase, and
filler openers — before the text goes to TTS synthesis.

Mandate requirements for the tests:
- 5 positive cases (clean text passes through unchanged)
- 5 blacklist hits (each pattern at least once)
- 3 mixed cases (stacktrace + markdown + clean text)
- 2 whitelist-protection cases (user concept words like "Datei" must NEVER
  be scrubbed as engineering jargon)
- Failure-mode-6 test: echo paraphrase ONLY at opener position (first 60
  characters) — mid-sentence echo must NOT be cut off.
"""
from __future__ import annotations

import pytest

from jarvis.brain.output_filter import ScrubResult, scrub_for_voice


# ---------------------------------------------------------------------------
# 5 positive cases — clean text must pass through UNCHANGED.
# ---------------------------------------------------------------------------

POSITIVE_CASES = [
    pytest.param("Halb drei.", id="terse-fact"),
    pytest.param(
        "Die Datei deklariert vier Brain-Provider und enthält den Voice-Stack-Block.",  # i18n-allow
        id="file-summary",
    ),
    pytest.param("Soll ich den Termin verschieben?", id="shall-i-question"),  # i18n-allow
    pytest.param("Es ist warm draussen.", id="weather-fact"),
    pytest.param("Goodbye, Ruben.", id="hangup-contract"),
]


@pytest.mark.parametrize("text", POSITIVE_CASES)
def test_clean_text_passes_through_unchanged(text: str) -> None:
    """Clean text is not modified, no fallback_used."""
    result = scrub_for_voice(text)
    assert isinstance(result, ScrubResult)
    assert result.cleaned == text
    assert result.actions == []
    assert result.fallback_used is False


# ---------------------------------------------------------------------------
# 5 blacklist hits — each pattern at least once.
# ---------------------------------------------------------------------------


def test_tool_json_is_removed() -> None:
    """Tool-call JSON in the middle of the text is cut out entirely."""
    text = 'Sub-Jarvis startet: spawn_worker({"utterance": "test"}) — bitte warten.'  # i18n-allow
    result = scrub_for_voice(text)
    assert '{"utterance"' not in result.cleaned
    assert "spawn_worker(" not in result.cleaned
    assert "removed_tool_json" in result.actions or "removed_tool_call" in result.actions
    # The rest of the text should be preserved
    assert "bitte warten" in result.cleaned.lower()  # i18n-allow


def test_tool_call_python_keyword_args_is_removed() -> None:
    """Python-style keyword args (``tool(key='val', ...)``) — probe drift 03."""
    text = (
        "spawn_worker(utterance='Wie kann ich das beschleunigen?', "  # i18n-allow
        "context_hints=['x', 'y'], action='analyze', target='')"
    )
    result = scrub_for_voice(text)
    assert "spawn_worker" not in result.cleaned
    assert "utterance=" not in result.cleaned
    assert "removed_tool_json" in result.actions


def test_tool_xml_tags_are_removed() -> None:
    """XML tool tags including inner content — probe drift 12."""
    text = (
        "Ich delegiere das. <spawn_worker> <utterance>Lies die Datei</utterance>"  # i18n-allow
        " <action>x</action> </spawn_worker> Fertig."  # i18n-allow
    )
    result = scrub_for_voice(text)
    assert "<spawn_worker" not in result.cleaned
    assert "<utterance>" not in result.cleaned
    assert "removed_tool_json" in result.actions
    # The rest of the text remains
    assert "Fertig" in result.cleaned  # i18n-allow


def test_tool_kw_pattern_does_not_break_harmless_python() -> None:
    """Harmless Python examples (``print(x=1)``) must NOT be destroyed.

    TOOL_CALL_KW_RE is tool-name-specific — only known tool names
    match, not every ``\\w+(...)`` call.
    """
    text = "Use print(x=1) to debug."
    result = scrub_for_voice(text)
    # 'print' is not in TOOL_NAMES — stays intact
    assert "print(x=1)" in result.cleaned


def test_stacktrace_is_replaced_with_fallback_phrase() -> None:
    """A stacktrace becomes the fallback phrase 'Es trat ein Fehler auf.' + fallback_used=True."""  # i18n-allow
    text = (
        "Hier der Fehler:\n"  # i18n-allow
        'Traceback (most recent call last):\n'
        '  File "/x/y.py", line 42, in run\n'
        "    raise ValueError('boom')\n"
        "ValueError: boom"
    )
    result = scrub_for_voice(text)
    assert "Traceback" not in result.cleaned
    assert 'File "' not in result.cleaned
    assert "ValueError" not in result.cleaned
    assert "Es trat ein Fehler auf" in result.cleaned  # i18n-allow
    assert result.fallback_used is True
    assert "replaced_stacktrace" in result.actions


def test_markdown_is_stripped() -> None:
    """Leftover markdown (``**``, ``##``, code fences, list bullets) is removed."""
    text = "## Bericht\n\n**Wichtig:** Die Datei ist da.\n\n- Punkt eins\n- Punkt zwei"  # i18n-allow
    result = scrub_for_voice(text)
    assert "**" not in result.cleaned
    assert "##" not in result.cleaned
    assert "- Punkt" not in result.cleaned
    # Content remains — only the markup is gone
    assert "Wichtig" in result.cleaned
    assert "Datei" in result.cleaned
    assert "stripped_markdown" in result.actions


def test_self_reference_is_removed() -> None:
    """``Als KI`` / ``Ich bin nur ein Sprachmodell`` are scrubbed."""  # i18n-allow
    text = "Als KI kann ich das natuerlich pruefen. Halb drei."  # i18n-allow
    result = scrub_for_voice(text)
    low = result.cleaned.lower()
    assert "als ki" not in low
    assert "als sprachmodell" not in low
    assert "ich bin nur" not in low
    # Core content remains
    assert "halb drei" in low
    assert "removed_self_reference" in result.actions


@pytest.mark.parametrize(
    ("text", "language", "gone", "kept"),
    [
        # The maintainer never wants Jarvis to SAY it is noting / reviewing the
        # last transcription — it happens silently in the background.
        ("Verstanden. Ich notiere mir das. Das Wetter ist sonnig.", "de",  # i18n-allow
         "notiere", "sonnig"),
        ("Ich schaue mir die letzte Transkription an. Die Hauptstadt ist Rom.",  # i18n-allow
         "de", "transkription", "rom"),
        ("Ich merke mir das. Hier ist die Antwort.", "de", "merke mir", "antwort"),  # i18n-allow
        ("Got it. I am noting that down. The capital is Rome.", "en",
         "noting", "rome"),
        ("Sure. Let me look at the last transcription. It is 25 degrees.", "en",
         "transcription", "25 degrees"),
        ("Entendido, tomo nota. La capital es Roma.", "es", "tomo nota", "roma"),
    ],
)
def test_background_action_narration_is_removed(
    text: str, language: str, gone: str, kept: str,
) -> None:
    """Noting / "review the last transcription" narration is stripped (DE/EN/ES)."""
    result = scrub_for_voice(text, language=language)
    low = result.cleaned.lower()
    assert gone not in low
    assert kept in low  # the real content survives
    assert "removed_background_action_narration" in result.actions


@pytest.mark.parametrize(
    ("text", "language"),
    [
        # Must NOT be stripped: content lead-ins + legit save/check confirmations.
        ("Looking at the data, the answer is 42.", "en"),
        ("Ich schaue gleich nach dem Wetter und melde mich.", "de"),  # i18n-allow
        ("I'm saving the file now.", "en"),
        ("Die Datei ist gespeichert.", "de"),  # i18n-allow
        ("I will check the weather for you.", "en"),
    ],
)
def test_background_action_narration_no_false_positive(
    text: str, language: str,
) -> None:
    """A content lead-in or a legit save/check action is left intact."""
    result = scrub_for_voice(text, language=language)
    assert "removed_background_action_narration" not in result.actions


def test_echo_paraphrase_in_opener_is_cut() -> None:
    """Echo paraphrase at the start of a sentence is cut off — the answer is the substance."""
    text = "Du möchtest also wissen, wie spät es ist. Halb drei."  # i18n-allow
    result = scrub_for_voice(text)
    low = result.cleaned.lower()
    assert "du möchtest also" not in low  # i18n-allow
    assert "halb drei" in low
    assert "rephrased_echo" in result.actions


def test_filler_opener_is_removed() -> None:
    """``Großartige Frage`` / ``Tolle Frage`` as an opener is removed."""  # i18n-allow
    text = "Großartige Frage! Es ist halb drei."  # i18n-allow
    result = scrub_for_voice(text)
    low = result.cleaned.lower()
    assert "großartige frage" not in low  # i18n-allow
    assert "grossartige frage" not in low
    assert "halb drei" in low
    assert "removed_filler_opener" in result.actions


def test_engineering_jargon_is_removed() -> None:
    """``MCP``/``Subprocess``/``Provider`` as standalone words are scrubbed."""
    text = "Der Subprocess wurde via MCP-Provider gestartet."  # i18n-allow
    result = scrub_for_voice(text)
    low = result.cleaned.lower()
    # The substance words disappear OR get paraphrased — what matters is:
    # none of the three jargon words remains as a standalone word.
    assert "mcp" not in low.split()  # not as a word
    assert "subprocess" not in low
    assert "removed_engineering_jargon" in result.actions


# ---------------------------------------------------------------------------
# 3 mixed cases — several patterns at once.
# ---------------------------------------------------------------------------


def test_mixed_stacktrace_with_markdown() -> None:
    """Stacktrace + markdown -> stacktrace wins (fallback_used=True)."""
    text = (
        "## Fehler\n\n"  # i18n-allow
        "**Detail:** Traceback (most recent call last):\n"
        '  File "/x/y.py", line 1, in <module>\n'
        "    raise RuntimeError('x')"
    )
    result = scrub_for_voice(text)
    assert "Traceback" not in result.cleaned
    assert "**" not in result.cleaned
    assert "##" not in result.cleaned
    assert result.fallback_used is True


def test_mixed_tool_json_with_clean_text() -> None:
    """Tool JSON mid-sentence + clean text -> tool JSON removed, text remains."""
    text = 'Bitte: dispatch_to_admin({"op": "shutdown"}) ist nicht erlaubt.'  # i18n-allow
    result = scrub_for_voice(text)
    assert "dispatch_to_admin" not in result.cleaned
    assert "{\"op\"" not in result.cleaned
    assert "ist nicht erlaubt" in result.cleaned.lower()  # i18n-allow


def test_mixed_filler_with_clean_rest() -> None:
    """Filler opener + clean answer -> filler removed, the rest stays unchanged.

    Deliberately no jargon words (Provider/Subprocess/MCP/Harness) in the
    remaining text, so this test isolates only the filler-removal check.
    Compounds like "Brain-Modelle" are protected anyway by the hyphen
    lookbehind in JARGON_RE.
    """
    text = "Tolle Frage! Die Datei jarvis.toml hat vier Brain-Modelle."  # i18n-allow
    result = scrub_for_voice(text)
    assert "Tolle Frage" not in result.cleaned
    # User concept words (Datei) must NOT disappear
    assert "Datei" in result.cleaned
    assert "jarvis.toml" in result.cleaned
    assert "vier Brain-Modelle" in result.cleaned


# ---------------------------------------------------------------------------
# 2 whitelist-protection cases — user concept words are sacred.
# ---------------------------------------------------------------------------


def test_whitelist_datei_is_protected() -> None:
    """``Datei`` is NEVER scrubbed, not even as a jargon lookalike."""
    text = "Die Datei wurde gespeichert."  # i18n-allow
    result = scrub_for_voice(text)
    assert result.cleaned == text
    assert result.actions == []


def test_whitelist_user_concepts_protected() -> None:
    """``Browser``/``Email``/``Terminal``/``Notiz``/``Termin``/``Kalender`` remain."""
    text = "Browser geöffnet, Email versendet, Termin im Kalender eingetragen, Notiz im Terminal gespeichert."  # i18n-allow
    result = scrub_for_voice(text)
    for concept in ("Browser", "Email", "Termin", "Kalender", "Notiz", "Terminal"):
        assert concept in result.cleaned, f"whitelist word {concept!r} was scrubbed"


# ---------------------------------------------------------------------------
# Failure-mode-6 test: echo paraphrase ONLY at opener position.
# ---------------------------------------------------------------------------


def test_echo_paraphrase_mid_sentence_is_kept() -> None:
    """A mid-sentence echo pattern (after 60 characters) must NOT be scrubbed.

    Mandate failure mode 6: sometimes the user genuinely wants an
    echo-style confirmation ('Du moechtest also den Termin verschieben?
    Ja oder nein?'). Apply the filter ONLY at the opener position (first
    60 characters).
    """
    # Echo pattern in the middle (after >60 characters of clean text)
    text = (
        "Ich habe den Termin am Freitag im Kalender und einen Konflikt erkannt. "  # i18n-allow
        "Du möchtest also den Termin verschieben? Ja oder nein?"  # i18n-allow
    )
    # Sanity check: the echo pattern sits after position 60
    echo_pos = text.lower().find("du möchtest also")  # i18n-allow
    assert echo_pos > 60, f"test-setup bug: echo pattern at position {echo_pos}"

    result = scrub_for_voice(text)
    # Mid-sentence echo is preserved
    assert "möchtest also" in result.cleaned.lower()  # i18n-allow
    assert "rephrased_echo" not in result.actions


# ---------------------------------------------------------------------------
# Em-dash / en-dash scrub (2026-06-29 "choppy voice" forensic): a parenthetical
# dash renders as a hard TTS pause. Collapse it to a comma; hyphen compounds
# (plain ASCII '-', no surrounding spaces) must survive.
# ---------------------------------------------------------------------------


def test_em_dash_becomes_comma() -> None:
    text = "Kurz nach drei — Viertel nach, genau genommen."
    result = scrub_for_voice(text)
    assert "—" not in result.cleaned
    assert "Kurz nach drei, Viertel nach" in result.cleaned


def test_en_dash_becomes_comma() -> None:
    text = "Alles bereit – wir können sofort loslegen."  # i18n-allow
    result = scrub_for_voice(text)
    assert "–" not in result.cleaned
    assert "Alles bereit, wir können" in result.cleaned  # i18n-allow


def test_hyphen_compound_survives_em_dash_filter() -> None:
    """A plain ASCII hyphen compound has no surrounding spaces and must stay."""
    text = "Dein T-Shirt liegt im Schrank."
    result = scrub_for_voice(text)
    assert "T-Shirt" in result.cleaned


def test_trailing_em_dash_leaves_no_dangling_comma() -> None:
    text = "Im Hintergrund laufen mehrere Programme —"
    result = scrub_for_voice(text)
    assert "—" not in result.cleaned
    assert not result.cleaned.endswith(",")


def test_ascii_double_hyphen_dash_aside_becomes_comma() -> None:
    """A ' -- ' dash-aside (ASCII double hyphen, space-surrounded) reads as the
    same hard TTS pause as an em dash — collapse it to a comma too. The
    Unicode-only scrub missed it, and several canned phrases / LLM outputs use
    ' -- ' (live forensic 2026-06-30)."""
    result = scrub_for_voice("Mach ich -- ich sage Bescheid, sobald es fertig ist.")  # i18n-allow
    assert "--" not in result.cleaned
    assert "Mach ich, ich sage Bescheid" in result.cleaned  # i18n-allow


def test_ascii_hyphen_compound_and_range_survive_double_hyphen_filter() -> None:
    """No false positive: a compound ('T-Shirt') or numeric range ('20-30') has
    no surrounding spaces and must survive the double-hyphen scrub."""
    result = scrub_for_voice("Dein T-Shirt kostet 20-30 Euro.")
    assert "T-Shirt" in result.cleaned
    assert "20-30" in result.cleaned


def test_fallback_used_true_only_for_stacktrace() -> None:
    """``fallback_used`` is only ``True`` when the whole text was replaced by
    the standard phrase (stacktrace), not on a partial scrub."""
    # A filler opener only removes a part — no fallback
    result = scrub_for_voice("Großartige Frage! Halb drei.")  # i18n-allow
    assert result.fallback_used is False


def test_empty_input_returns_empty() -> None:
    """Empty input -> empty output, no exception."""
    result = scrub_for_voice("")
    assert result.cleaned == ""
    assert result.actions == []
    assert result.fallback_used is False


# ---------------------------------------------------------------------------
# Phase-1 extensions from research report 2026-04-28 section 1.3.
#
# Four new drift classes that the original mandate phase-1 blacklist did not
# cover but that fit exactly into the filter's scope:
#
#  1. A1 "Sir" address (scenarios 03/07 of the 2026-04-28 probe).
#  2. "Sub-Agent"/"Supervisor-Agent" as an engineering reveal.
#  3. Tool-args body leak in YAML-/key:value form.
#  4. Post-scrub residue (e.g. a lone `}`) -> fallback_used=True.
# ---------------------------------------------------------------------------


def test_sir_anrede_is_removed() -> None:  # i18n-allow
    """A1: 'Sir' as a form of address is scrubbed — mandate A1 (Ruben instead of Sir)."""
    text = "Sir, ich starte die Analyse."  # i18n-allow
    result = scrub_for_voice(text)
    low = result.cleaned.lower()
    assert "sir" not in low.split(",")[0].split()  # not as a word in the opener
    assert "sir" not in low
    assert "ich starte die analyse" in low  # i18n-allow
    assert "removed_anrede_drift" in result.actions  # i18n-allow


def test_sir_anrede_mid_sentence_is_removed() -> None:  # i18n-allow
    """A1: 'Sir' is also removed mid-sentence (after a comma)."""
    text = "Erledigt, Sir. Die Datei ist gespeichert."  # i18n-allow
    result = scrub_for_voice(text)
    assert "Sir" not in result.cleaned
    assert "Datei" in result.cleaned
    assert "removed_anrede_drift" in result.actions  # i18n-allow


def test_legitimate_sir_in_quote_is_kept() -> None:
    """'Sir' is preserved inside quotation marks (quote protection).

    If the user asks Jarvis to read out a song lyric or a quote that
    contains 'Sir', the filter must not destroy it. Heuristic: 'Sir'
    directly inside quotes is not scrubbed.
    """
    text = 'Im Lied steht: "Yes, Sir, I can boogie." — kennst du das?'  # i18n-allow
    result = scrub_for_voice(text)
    assert "Sir" in result.cleaned, "Sir in a quote was wrongly scrubbed"


def test_sub_agent_jargon_is_removed() -> None:
    """'Sub-Agent' as an engineering reveal is removed — probe drift 03/07."""
    text = "Ich starte einen Sub-Agent, der die Datei analysiert."  # i18n-allow
    result = scrub_for_voice(text)
    assert "Sub-Agent" not in result.cleaned
    assert "sub-agent" not in result.cleaned.lower()
    # Core content remains
    assert "analysiert" in result.cleaned.lower()  # i18n-allow
    assert "removed_engineering_jargon" in result.actions


def test_supervisor_agent_jargon_is_removed() -> None:
    """'Supervisor-Agent' as an engineering reveal is removed — probe drift 13."""
    text = "Dein persoenlicher Supervisor-Agent erledigt das."  # i18n-allow
    result = scrub_for_voice(text)
    assert "Supervisor-Agent" not in result.cleaned
    assert "supervisor-agent" not in result.cleaned.lower()
    assert "erledigt" in result.cleaned.lower()  # i18n-allow
    assert "removed_engineering_jargon" in result.actions


def test_tool_args_yaml_block_is_removed() -> None:
    """Tool-args body leak as a YAML/key:value block — probe drift 03 body.

    The 2026-04-28 probe leaked the entire Sub-Jarvis tool-call body in
    scenario 03:
        utterance: "Wie kann ich das beschleunigen?"  # i18n-allow
        context_hints:
          - Unklar ...
          - Benoetigt Kontext ...
        action: "die Beschleunigung ..."
        target: ""
    This YAML-like form is not caught by TOOL_JSON_RE.
    """
    text = (
        "Ich starte die Analyse.\n"  # i18n-allow
        '"Wie kann ich das beschleunigen?"\n'  # i18n-allow
        "context_hints:\n"
        "Unklar, was beschleunigt werden soll.\n"  # i18n-allow
        "Benoetigt Kontext zur aktuellen Aufgabe oder zum System.\n"  # i18n-allow
        'action: "die Beschleunigung einer unklaren Aufgabe analysiert"\n'  # i18n-allow
        'target: ""'
    )
    result = scrub_for_voice(text)
    assert "context_hints" not in result.cleaned
    assert "action:" not in result.cleaned
    assert "target:" not in result.cleaned
    assert "removed_tool_json" in result.actions
    # The rest of the text ('Ich starte die Analyse.') remains
    assert "Analyse" in result.cleaned


def test_post_scrub_residue_triggers_fallback() -> None:
    """When only residue is left after all scrubs -> fallback_used=True.

    Probe drift 12: the tool filter tore everything apart except a '}',
    which went to TTS uncommented and the user heard practically nothing.
    When the filtered text contains fewer than 3 alphanumeric characters,
    the filter should fall back to the standard error phrase.
    """
    # Only a '}' remains — nothing more
    text = '<spawn_worker>{"utterance": "test"}</spawn_worker>}'
    result = scrub_for_voice(text)
    # Either fallback_used=True OR cleaned is empty (both acceptable)
    assert (result.fallback_used and result.cleaned), (
        f"post-scrub residue {result.cleaned!r} was not replaced by the fallback"
    )
    assert "Es trat ein Fehler auf" in result.cleaned or result.cleaned.strip() == ""  # i18n-allow


def test_post_scrub_meaningful_text_no_fallback() -> None:
    """Defense against the fallback trigger: substantial remaining text does
    NOT trigger fallback_used."""
    text = "Erledigt. Die Datei ist da."  # i18n-allow
    result = scrub_for_voice(text)
    assert result.fallback_used is False
    assert "Datei" in result.cleaned


# ---------------------------------------------------------------------------
# Phase-1 extension 2 (2026-04-28, later):
# Anthropic-internal tag drifts from the re-probe — the brain leaks:
#   1. ``<function_calls>...<invoke name="...">...</invoke></function_calls>``
#   2. Generic ``<tool_call>...</tool_call>`` / ``<tool_response>...</tool_response>``
#   3. Base64 image strings as a body leak
# ---------------------------------------------------------------------------


def test_anthropic_function_calls_block_is_removed() -> None:
    """Anthropic-internal ``<function_calls><invoke name='...'>`` format —
    re-probe drift scenario 12 from 2026-04-28."""
    text = (
        'Hier: <function_calls>\n'  # i18n-allow
        '<invoke name="spawn_worker">\n'
        '<parameter name="utterance">Lies die Datei</parameter>\n'  # i18n-allow
        '<parameter name="action">x</parameter>\n'
        '</invoke>\n'
        '</function_calls>\n'
        'Fertig.'  # i18n-allow
    )
    result = scrub_for_voice(text)
    assert "<function_calls>" not in result.cleaned
    assert "<invoke" not in result.cleaned
    assert "<parameter" not in result.cleaned
    assert "spawn_worker" not in result.cleaned
    assert "removed_tool_json" in result.actions
    assert "Hier:" in result.cleaned  # i18n-allow
    assert "Fertig" in result.cleaned  # i18n-allow


def test_generic_tool_call_tags_are_removed() -> None:
    """``<tool_call>{}</tool_call>`` with arbitrary content — re-probe
    01/06/11."""
    text = (
        'Lass mich schauen. <tool_call>\n'  # i18n-allow
        '}\n'
        '</tool_call>\n'
        '<tool_response>\n'
        '{"status": "ok"}\n'
        '</tool_response>\n'
        '09:17 Uhr.'  # i18n-allow
    )
    result = scrub_for_voice(text)
    assert "<tool_call>" not in result.cleaned
    assert "</tool_call>" not in result.cleaned
    assert "<tool_response>" not in result.cleaned
    assert "</tool_response>" not in result.cleaned
    assert "removed_tool_json" in result.actions
    # Core content remains
    assert "09:17" in result.cleaned


def test_base64_image_block_is_removed() -> None:
    """Base64 image string (very long ASCII sequence) — re-probe 08."""
    # Realistic trigger: data-URI with a long base64 sequence
    text = (
        'Hier das Bild: data:image/webp;base64,'  # i18n-allow
        + 'A' * 800
        + '. Fertig.'  # i18n-allow
    )
    result = scrub_for_voice(text)
    # The base64 block is gone
    assert ("A" * 200) not in result.cleaned, "long base64 sequence not removed"
    assert "data:image" not in result.cleaned
    assert "removed_tool_json" in result.actions
    assert "Fertig" in result.cleaned  # i18n-allow


def test_short_clean_alphanumeric_is_kept() -> None:
    """Defense against a base64 false positive: short alphanumeric strings
    (e.g. file names, hashes, tokens in normal communication) remain."""
    text = "Die Datei abc123def456 wurde gespeichert."  # i18n-allow
    result = scrub_for_voice(text)
    assert "abc123def456" in result.cleaned
    assert result.actions == []


# ---------------------------------------------------------------------------
# Phase-2 anti-pattern filter — the strings listed in
# voice_e2e_probe.py:ANTI_PATTERNS should be scrubbed by the filter BEFORE
# the probe heuristic runs, not merely serve as a probe-detection pattern.
# Otherwise every brain output containing an anti-pattern counts as DRIFT,
# even though the user never hears it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("opener,rest", [
    ("Lass mich kurz", "schauen. Ja, gespeichert."),  # i18n-allow
    ("Lass mich kurz", "checken. Halb drei."),
    ("Let me think,", "the answer is 42."),
    ("Let me think.", "It's halb drei."),
])
def test_filler_selbstreferenz_opener_is_removed(opener: str, rest: str) -> None:
    """Phase-2 anti-pattern: 'Lass mich kurz' / 'Let me think' as an opener.

    These phrases are listed in voice_e2e_probe.py:ANTI_PATTERNS under
    'Filler-Selbstreferenz'. The filter must cut them off, otherwise the
    probe heuristic triggers a DRIFT hit even though the user was never
    going to hear the phrase anyway.
    """
    text = f"{opener} {rest}"
    result = scrub_for_voice(text)
    low = result.cleaned.lower()
    assert "lass mich kurz" not in low
    assert "let me think" not in low
    # Core content (rest minus the opener comma) is preserved
    rest_substantive = rest.split(".", 1)[-1].strip().lower()
    if rest_substantive:
        assert rest_substantive in low
    assert "removed_filler_opener" in result.actions


def test_filler_selbstreferenz_mid_sentence_is_kept() -> None:
    """Failure-mode-6 analog: mid-sentence 'lass mich kurz' is preserved.

    If the user genuinely asks for it ('Schau dir das an, lass mich kurz
    mal nachdenken, dann sage ich es dir'), the filter must not destroy
    it. Same as echo paraphrase: only at the opener (<= 60 characters).
    """
    # At least 61 characters before 'lass mich kurz' so failure-mode-6 applies.
    text = (
        "Ich gehe das systematisch durch, "
        "und im naechsten Schritt soll ich lass mich kurz nachdenken, "  # i18n-allow
        "und dann antworte ich."
    )
    pos = text.lower().find("lass mich kurz")
    assert pos > 60, f"test-setup bug: pos={pos}"

    result = scrub_for_voice(text)
    assert "lass mich kurz" in result.cleaned.lower()
    assert "removed_filler_opener" not in result.actions


# ---------------------------------------------------------------------------
# Audit F-AUDIT-4 (2026-04-29): tool args written as prose.
# The brain leaked, verbatim, in the 2026-04-29 probe run scenario 07:
#   "spawn_worker with utterance is Analysiere... context_hints is [...]
#   action is ... target is ..."
# The filter had to be extended to cover this natural-language format.
# ---------------------------------------------------------------------------


def test_tool_call_prose_with_keys_is_removed() -> None:
    """spawn_worker with utterance is X context_hints is Y → scrubbed.

    Realistic probe output from 2026-04-29 scenario 07. The filter must
    cut the entire tool-call block up to the end of the sentence. The
    rest of the text remains.
    """
    text = (
        "Okay. spawn_worker with utterance is Analysiere das gesamte "  # i18n-allow
        "Projektverzeichnis context_hints is Vollstaendige Projektstruktur "
        "erfassen action is das Projekt analysiert target is Arbeitsordner. "  # i18n-allow
        "Fertig."  # i18n-allow
    )
    result = scrub_for_voice(text)
    assert "spawn_worker" not in result.cleaned
    assert "utterance is" not in result.cleaned
    assert "context_hints" not in result.cleaned
    assert "action is" not in result.cleaned
    assert "removed_tool_json" in result.actions
    # The rest of the text remains
    assert "Okay" in result.cleaned
    assert "Fertig" in result.cleaned  # i18n-allow


def test_tool_args_prose_without_tool_name_is_removed() -> None:
    """'utterance is X' alone (without a tool-name prefix) is also scrubbed."""
    text = "Ich versuche es. utterance is lies die Datei. Fertig."  # i18n-allow
    result = scrub_for_voice(text)
    assert "utterance is" not in result.cleaned
    assert "removed_tool_json" in result.actions
    assert "Ich versuche es" in result.cleaned  # i18n-allow
    assert "Fertig" in result.cleaned  # i18n-allow


def test_legitimate_is_sentence_is_kept() -> None:
    """Defense against a false positive: legitimate 'X is Y' sentences remain.

    If the user says 'Die Hauptstadt ist Paris', or the brain answers  # i18n-allow
    'Es ist halb drei' — NO match on TOOL_ARGS_PROSE_RE, because 'Die  # i18n-allow
    Hauptstadt' and 'Es' are not tool-arg keys.
    """
    text = "Es ist halb drei. Die Datei ist gespeichert."  # i18n-allow
    result = scrub_for_voice(text)
    assert "ist halb drei" in result.cleaned
    assert "Datei ist gespeichert" in result.cleaned  # i18n-allow
    assert "removed_tool_json" not in result.actions


# --- OpenClaw brand-name is now SCRUBBED (reversal 2026-05-24) ---
#
# The 2026-05-13 whitelist that let Jarvis say "OpenClaw-Subagent" is
# reversed: the OpenClaw subprocess was retired (the worker runs Opus 4.7
# directly), so naming it would announce a component that no longer exists.
# OPENCLAW_RE strips the brand token and JARGON_COMPOUND_RE removes the bare
# "Subagent" left behind.


def test_openclaw_brand_name_is_scrubbed() -> None:
    """OpenClaw must NOT survive voice output anymore (2026-05-24 reversal)."""
    text = (
        "Mach ich, ich lasse dafür einen OpenClaw-Subagent "  # i18n-allow
        "ein Hello-World-Programm schreiben."
    )
    result = scrub_for_voice(text)
    assert "OpenClaw" not in result.cleaned
    assert "Subagent" not in result.cleaned
    assert "removed_engineering_jargon" in result.actions


def test_bare_subagent_still_scrubbed_without_brand_prefix() -> None:
    """Bare 'Subagent' / 'Sub-Agent' without the brand prefix is still scrubbed."""
    text = "Ich aktiviere einen Subagent für die Aufgabe."  # i18n-allow
    result = scrub_for_voice(text)
    assert "Subagent" not in result.cleaned
    assert "removed_engineering_jargon" in result.actions


def test_openclaw_compound_prefix_stripped_but_noun_kept() -> None:
    """'OpenClaw-Mission' -> 'Mission' (brand prefix removed, noun stays)."""
    text = "Die OpenClaw-Mission ist fertig."  # i18n-allow
    result = scrub_for_voice(text)
    assert "OpenClaw" not in result.cleaned
    assert "Mission" in result.cleaned


def test_openclaw_subagenten_plural_also_scrubbed() -> None:
    """The plural 'OpenClaw-Subagenten' must be scrubbed too."""
    text = "Mehrere OpenClaw-Subagenten arbeiten parallel."  # i18n-allow
    result = scrub_for_voice(text)
    assert "OpenClaw" not in result.cleaned
    assert "Subagenten" not in result.cleaned


def test_scrub_strips_end_call_sentinel() -> None:
    from jarvis.speech.hangup import END_CALL_SIGNAL

    result = scrub_for_voice(f"Bis später, Ruben. {END_CALL_SIGNAL}", language="de")  # i18n-allow
    assert END_CALL_SIGNAL not in result.cleaned
    assert result.cleaned.strip() == "Bis später, Ruben."  # i18n-allow
    assert "stripped_end_signal" in result.actions


# ---------------------------------------------------------------------------
# Web-search / SERP source artefacts (live forensic 2026-06-28, voice Turn 4).
# The brain occasionally reads a raw search hit verbatim — title, snippet, URL,
# "Weitere Ergebnisse von <domain>" — instead of synthesizing an answer. The
# search_web tool result now instructs the brain to synthesize (primary fix);
# this is the fail-closed defense so a source URL / domain can never be spoken.
# ---------------------------------------------------------------------------


def test_http_url_is_stripped_from_voice() -> None:
    """A spoken answer must never read out an http(s) URL."""
    text = "Die Prüfung dauert 190 Minuten. Mehr unter https://www.km.bayern.de/schueler.html"  # i18n-allow
    result = scrub_for_voice(text)
    assert "http" not in result.cleaned.lower()
    assert "km.bayern.de" not in result.cleaned.lower()
    assert "190 minuten" in result.cleaned.lower()
    assert "removed_source_artifacts" in result.actions


def test_serp_more_results_footer_is_stripped() -> None:
    """The 'Weitere Ergebnisse von <domain>' SERP footer is never spoken."""
    text = "Note 2 gibt es ab 34,5 Punkten. Weitere Ergebnisse von www.gutefrage.net"
    result = scrub_for_voice(text)
    low = result.cleaned.lower()
    assert "gutefrage" not in low
    assert "weitere ergebnisse" not in low
    assert "note 2" in low
    assert "removed_source_artifacts" in result.actions


def test_bare_www_domain_is_stripped_from_voice() -> None:
    """A bare www-domain hit reference is stripped (English/Spanish SERP too)."""
    text = "Insgesamt 190 Minuten. Mehr dazu: www.realschule.bayern.de Viel Erfolg."
    result = scrub_for_voice(text)
    assert "www." not in result.cleaned.lower()
    assert "realschule.bayern.de" not in result.cleaned.lower()
    assert "190 minuten" in result.cleaned.lower()
    assert "viel erfolg" in result.cleaned.lower()


def test_clean_answer_without_sources_is_untouched() -> None:
    """Defense against false positives: a normal spoken answer with no URL /
    domain / SERP footer must pass through unchanged (no source-artifact pass)."""
    text = "Morgen schreiben alle Realschüler in Bayern ihre Mathe-Abschlussprüfung."  # i18n-allow
    result = scrub_for_voice(text)
    assert result.cleaned == text
    assert "removed_source_artifacts" not in result.actions


def test_scrub_sentinel_only_yields_empty() -> None:
    from jarvis.speech.hangup import END_CALL_SIGNAL

    result = scrub_for_voice(END_CALL_SIGNAL, language="de")
    assert result.cleaned == ""


# ---------------------------------------------------------------------------
# Raw data-structure dump guard (live bug 2026-06-22).
#
# A code path str()'d a whole tool-result DICT instead of humanizing it, and
# scrub_for_voice — which only knew SPECIFIC tool-leak shapes ({"tool":…},
# XML tags, YAML, prose) — passed it through, stripping only the jargon word
# "harness" (hence the leaked empty '' key). A raw Python/JSON data-structure
# dump must NEVER be spoken/shown, regardless of its keys or quote style. This
# is the fail-closed common-chokepoint defense that makes the bug class
# impossible no matter which path forgets to humanize its output.
# ---------------------------------------------------------------------------


def test_scrub_replaces_raw_harness_result_dict_with_fallback() -> None:
    """The exact leaked shape from the live bug -> fallback, never spoken."""
    from jarvis.brain.output_filter import FALLBACK_PHRASES

    leaked = (
        "{'harness': 'screenshot', 'exit_code': 0, 'stdout': \"[cu] done at "
        "step 6.1 (verified: Settings open)\", 'stderr': '[cu] mission profile: "
        "steps=6 total=15.4s', 'cost_usd': 0.0, 'duration_ms': 15442}"
    )
    result = scrub_for_voice(leaked, language="de")
    assert result.fallback_used is True
    assert result.cleaned == FALLBACK_PHRASES["de"]
    for leak in ("{", "}", "exit_code", "cost_usd", "duration_ms", "screenshot"):
        assert leak not in result.cleaned, leak


def test_scrub_replaces_already_scrubbed_harness_dict() -> None:
    """Even the post-jargon form ({'' : 'screenshot', …} — what actually
    reached the user) must be refused, not merely have 'harness' removed."""
    from jarvis.brain.output_filter import FALLBACK_PHRASES

    leaked = "{'': 'screenshot', 'exit_code': 0, 'cost_usd': 0.0, 'duration_ms': 15442}"
    result = scrub_for_voice(leaked, language="de")
    assert result.fallback_used is True
    assert result.cleaned == FALLBACK_PHRASES["de"]


def test_scrub_refuses_arbitrary_dict_dump_unknown_keys() -> None:
    """STRUCTURAL guard: a brand-new result shape with keys never seen before
    is refused too — no key-by-key whack-a-mole."""
    from jarvis.brain.output_filter import FALLBACK_PHRASES

    leaked = "{'foo': 1, 'bar': 'baz', 'nested': {'a': 2}, 'when': 'now'}"
    result = scrub_for_voice(leaked, language="en")
    assert result.fallback_used is True
    assert result.cleaned == FALLBACK_PHRASES["en"]


def test_scrub_refuses_json_list_of_objects_dump() -> None:
    """A JSON array of objects (double quotes) is a dump too."""
    from jarvis.brain.output_filter import FALLBACK_PHRASES

    leaked = '[{"title": "X", "snippet": "y"}, {"title": "Z", "snippet": "w"}]'
    result = scrub_for_voice(leaked, language="de")
    assert result.fallback_used is True
    assert result.cleaned == FALLBACK_PHRASES["de"]


def test_fallback_phrase_localized_to_spanish() -> None:
    """Runtime-output-language doctrine: every phrase table carries all
    supported locales (de/en/es). A Spanish-pinned user must hear the Spanish
    fallback, not German — true for BOTH the raw-dump guard and the stacktrace
    guard (same FALLBACK_PHRASES table)."""
    from jarvis.brain.output_filter import FALLBACK_PHRASES

    assert "es" in FALLBACK_PHRASES, "Spanish fallback phrase missing"
    # raw-dump guard in Spanish
    dump = scrub_for_voice("{'exit_code': 0, 'cost_usd': 0.0}", language="es")
    assert dump.fallback_used is True
    assert dump.cleaned == FALLBACK_PHRASES["es"]
    # stacktrace guard in Spanish (same table)
    trace = scrub_for_voice(
        "Traceback (most recent call last):\n  File x\nValueError: boom",
        language="es",
    )
    assert trace.fallback_used is True
    assert trace.cleaned == FALLBACK_PHRASES["es"]
    # the Spanish phrase is actually Spanish, not the German default
    assert dump.cleaned != FALLBACK_PHRASES["de"]


def test_scrub_keeps_humanized_readback_with_quotes() -> None:
    """No false positive: a clean readback that merely quotes a UI label (and
    contains an apostrophe + colon-free) must pass through untouched."""
    text = "Erledigt — die Einstellungen sind auf 'Bluetooth und Geräte' offen."  # i18n-allow
    result = scrub_for_voice(text, language="de")
    assert result.fallback_used is False
    assert "Bluetooth und Geräte" in result.cleaned  # i18n-allow


def test_scrub_keeps_sentence_with_inline_brace_not_a_dump() -> None:
    """A sentence that happens to mention a brace mid-text is NOT a dump (it
    does not OPEN with a container) — must pass."""
    text = "Ich habe die Funktion test() geprüft, alles in Ordnung."  # i18n-allow
    result = scrub_for_voice(text, language="de")
    assert result.fallback_used is False
    assert "test()" in result.cleaned or "test" in result.cleaned


# ---------------------------------------------------------------------------
# Raw shell / PowerShell command guard (live bug 2026-06-28): the fast tier
# emitted a SendKeys PowerShell command as its reply and TTS read it aloud
# "with special characters and everything". A raw command is code, never a
# spoken sentence -> fail-closed to the standard phrase, like a stacktrace.
# ---------------------------------------------------------------------------

SHELL_COMMAND_CASES = [
    pytest.param(
        "Add-Type -AssemblyName System.Windows.Forms; "
        "[System.Windows.Forms.SendKeys]::SendWait('^(g)')",
        id="powershell-sendkeys-the-live-bug",
    ),
    pytest.param(
        "[System.Windows.Forms.SendKeys]::SendWait('^g')",
        id="dotnet-static-call",
    ),
    pytest.param("$env:USERPROFILE", id="ps-env-var"),
    pytest.param("cmd /c start discord", id="cmd-invocation"),
]


@pytest.mark.parametrize("text", SHELL_COMMAND_CASES)
def test_raw_shell_command_is_never_spoken(text: str) -> None:
    result = scrub_for_voice(text, language="de")
    assert result.fallback_used is True
    for frag in ("SendKeys", "Add-Type", "::", "$env", "cmd /c"):
        assert frag not in result.cleaned


@pytest.mark.parametrize(
    "clean",
    [
        "Schau bitte im Browser-Verlauf nach.",  # i18n-allow
        "Das kostet 5-10 Euro.",
        "Die E-Mail ist raus.",  # i18n-allow
        "Ich habe die Datei gespeichert.",  # i18n-allow
        "Ruf Anna an und sag ihr Bescheid.",
    ],
)
def test_shell_guard_does_not_destroy_normal_prose(clean: str) -> None:
    # Hyphen compounds, ranges and ordinary speech must NOT trip the guard.
    result = scrub_for_voice(clean, language="de")
    assert result.fallback_used is False
