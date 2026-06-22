"""Tests fuer ``jarvis.brain.output_filter.scrub_for_voice``.

Persona-Mandat Phase 1: Output-Filter am Brain->TTS-Pfad scrubbt Tool-JSON,
Stacktraces, Engineering-Jargon, Self-Reference, Echo-Paraphrase und
Filler-Opener — bevor der Text an die TTS-Synthese geht.

Mandat-Anforderungen an die Tests:
- 5 Positiv-Cases (sauberer Text bleibt unveraendert)
- 5 Schwarzliste-Treffer (jedes Pattern mind. 1x)
- 3 Mixed-Cases (Stacktrace + Markdown + sauberer Text)
- 2 Whitelist-Schutz-Cases (User-Konzept-Worte wie "Datei" duerfen NIE
  als Engineering-Jargon scrubbt werden)
- Failure-Mode-6-Test: Echo-Paraphrase NUR Opener-Position (erste 60
  Zeichen) — mid-sentence Echo darf NICHT abgeschnitten werden.
"""
from __future__ import annotations

import pytest

from jarvis.brain.output_filter import ScrubResult, scrub_for_voice


# ---------------------------------------------------------------------------
# 5 Positiv-Cases — sauberer Text muss UNVERAENDERT durchgehen.
# ---------------------------------------------------------------------------

POSITIVE_CASES = [
    pytest.param("Halb drei.", id="terse-fact"),
    pytest.param(
        "Die Datei deklariert vier Brain-Provider und enthält den Voice-Stack-Block.",
        id="file-summary",
    ),
    pytest.param("Soll ich den Termin verschieben?", id="shall-i-question"),
    pytest.param("Es ist warm draussen.", id="weather-fact"),
    pytest.param("Goodbye, Alex.", id="hangup-contract"),
]


@pytest.mark.parametrize("text", POSITIVE_CASES)
def test_clean_text_passes_through_unchanged(text: str) -> None:
    """Sauberer Text wird nicht modifiziert, kein fallback_used."""
    result = scrub_for_voice(text)
    assert isinstance(result, ScrubResult)
    assert result.cleaned == text
    assert result.actions == []
    assert result.fallback_used is False


# ---------------------------------------------------------------------------
# 5 Schwarzliste-Treffer — jedes Pattern mind. einmal.
# ---------------------------------------------------------------------------


def test_tool_json_is_removed() -> None:
    """Tool-Call-JSON in der Mitte des Texts wird komplett rausgeschnitten."""
    text = 'Sub-Jarvis startet: spawn_worker({"utterance": "test"}) — bitte warten.'
    result = scrub_for_voice(text)
    assert '{"utterance"' not in result.cleaned
    assert "spawn_worker(" not in result.cleaned
    assert "removed_tool_json" in result.actions or "removed_tool_call" in result.actions
    # Resttext sollte erhalten sein
    assert "bitte warten" in result.cleaned.lower()


def test_tool_call_python_keyword_args_is_removed() -> None:
    """Python-style keyword-args (``tool(key='val', ...)``) — Probe-Drift 03."""
    text = (
        "spawn_worker(utterance='Wie kann ich das beschleunigen?', "
        "context_hints=['x', 'y'], action='analyze', target='')"
    )
    result = scrub_for_voice(text)
    assert "spawn_worker" not in result.cleaned
    assert "utterance=" not in result.cleaned
    assert "removed_tool_json" in result.actions


def test_tool_xml_tags_are_removed() -> None:
    """XML-Tool-Tags inkl. Inner-Content — Probe-Drift 12."""
    text = (
        "Ich delegiere das. <spawn_worker> <utterance>Lies die Datei</utterance>"
        " <action>x</action> </spawn_worker> Fertig."
    )
    result = scrub_for_voice(text)
    assert "<spawn_worker" not in result.cleaned
    assert "<utterance>" not in result.cleaned
    assert "removed_tool_json" in result.actions
    # Resttext bleibt
    assert "Fertig" in result.cleaned


def test_tool_kw_pattern_does_not_break_harmless_python() -> None:
    """Harmlose Python-Beispiele (``print(x=1)``) duerfen NICHT zerschossen werden.

    TOOL_CALL_KW_RE ist tool-name-spezifisch — nur bekannte Tool-Namen
    matchen, nicht jeder ``\\w+(...)``-Aufruf.
    """
    text = "Use print(x=1) to debug."
    result = scrub_for_voice(text)
    # 'print' ist nicht in TOOL_NAMES — bleibt erhalten
    assert "print(x=1)" in result.cleaned


def test_stacktrace_is_replaced_with_fallback_phrase() -> None:
    """Stacktrace -> 'Es trat ein Fehler auf.' + fallback_used=True."""
    text = (
        "Hier der Fehler:\n"
        'Traceback (most recent call last):\n'
        '  File "/x/y.py", line 42, in run\n'
        "    raise ValueError('boom')\n"
        "ValueError: boom"
    )
    result = scrub_for_voice(text)
    assert "Traceback" not in result.cleaned
    assert 'File "' not in result.cleaned
    assert "ValueError" not in result.cleaned
    assert "Es trat ein Fehler auf" in result.cleaned
    assert result.fallback_used is True
    assert "replaced_stacktrace" in result.actions


def test_markdown_is_stripped() -> None:
    """Markdown-Reste (``**``, ``##``, code-fences, list-bullets) werden entfernt."""
    text = "## Bericht\n\n**Wichtig:** Die Datei ist da.\n\n- Punkt eins\n- Punkt zwei"
    result = scrub_for_voice(text)
    assert "**" not in result.cleaned
    assert "##" not in result.cleaned
    assert "- Punkt" not in result.cleaned
    # Inhalt bleibt — nur das Markup ist weg
    assert "Wichtig" in result.cleaned
    assert "Datei" in result.cleaned
    assert "stripped_markdown" in result.actions


def test_self_reference_is_removed() -> None:
    """``Als KI`` / ``Ich bin nur ein Sprachmodell`` werden gescrubbt."""
    text = "Als KI kann ich das natuerlich pruefen. Halb drei."
    result = scrub_for_voice(text)
    low = result.cleaned.lower()
    assert "als ki" not in low
    assert "als sprachmodell" not in low
    assert "ich bin nur" not in low
    # Kerninhalt bleibt
    assert "halb drei" in low
    assert "removed_self_reference" in result.actions


def test_echo_paraphrase_in_opener_is_cut() -> None:
    """Echo-Paraphrase am Satzanfang wird abgeschnitten — Antwort ist die Substanz."""
    text = "Du möchtest also wissen, wie spät es ist. Halb drei."
    result = scrub_for_voice(text)
    low = result.cleaned.lower()
    assert "du möchtest also" not in low
    assert "halb drei" in low
    assert "rephrased_echo" in result.actions


def test_filler_opener_is_removed() -> None:
    """``Großartige Frage`` / ``Tolle Frage`` als Opener werden entfernt."""
    text = "Großartige Frage! Es ist halb drei."
    result = scrub_for_voice(text)
    low = result.cleaned.lower()
    assert "großartige frage" not in low
    assert "grossartige frage" not in low
    assert "halb drei" in low
    assert "removed_filler_opener" in result.actions


def test_engineering_jargon_is_removed() -> None:
    """``MCP``/``Subprocess``/``Provider`` als Standalone-Worte werden gescrubbt."""
    text = "Der Subprocess wurde via MCP-Provider gestartet."
    result = scrub_for_voice(text)
    low = result.cleaned.lower()
    # Substanz-Worte verschwinden ODER werden umschrieben — wichtig ist:
    # keines der drei Jargon-Worte als Standalone-Wort verbleibt.
    assert "mcp" not in low.split()  # nicht als Wort
    assert "subprocess" not in low
    assert "removed_engineering_jargon" in result.actions


# ---------------------------------------------------------------------------
# 3 Mixed-Cases — mehrere Patterns gleichzeitig.
# ---------------------------------------------------------------------------


def test_mixed_stacktrace_with_markdown() -> None:
    """Stacktrace + Markdown -> Stacktrace gewinnt (fallback_used=True)."""
    text = (
        "## Fehler\n\n"
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
    """Tool-JSON mid-sentence + sauberer Text -> Tool-JSON raus, Text bleibt."""
    text = 'Bitte: dispatch_to_admin({"op": "shutdown"}) ist nicht erlaubt.'
    result = scrub_for_voice(text)
    assert "dispatch_to_admin" not in result.cleaned
    assert "{\"op\"" not in result.cleaned
    assert "ist nicht erlaubt" in result.cleaned.lower()


def test_mixed_filler_with_clean_rest() -> None:
    """Filler-Opener + saubere Antwort -> Filler raus, Rest bleibt unveraendert.

    Bewusst keine Jargon-Worte (Provider/Subprocess/MCP/Harness) im Resttext,
    damit der Test nur die Filler-Entfernung isoliert prueft. Compounds wie
    "Brain-Modelle" sind durch das Bindestrich-Lookbehind in JARGON_RE
    ohnehin geschuetzt.
    """
    text = "Tolle Frage! Die Datei jarvis.toml hat vier Brain-Modelle."
    result = scrub_for_voice(text)
    assert "Tolle Frage" not in result.cleaned
    # User-Konzept-Worte (Datei) duerfen NICHT verschwinden
    assert "Datei" in result.cleaned
    assert "jarvis.toml" in result.cleaned
    assert "vier Brain-Modelle" in result.cleaned


# ---------------------------------------------------------------------------
# 2 Whitelist-Schutz-Cases — User-Konzept-Worte sind heilig.
# ---------------------------------------------------------------------------


def test_whitelist_datei_is_protected() -> None:
    """``Datei`` wird NIE gescrubbt, auch nicht als Jargon-Lookalike."""
    text = "Die Datei wurde gespeichert."
    result = scrub_for_voice(text)
    assert result.cleaned == text
    assert result.actions == []


def test_whitelist_user_concepts_protected() -> None:
    """``Browser``/``Email``/``Terminal``/``Notiz``/``Termin``/``Kalender`` bleiben."""
    text = "Browser geöffnet, Email versendet, Termin im Kalender eingetragen, Notiz im Terminal gespeichert."
    result = scrub_for_voice(text)
    for concept in ("Browser", "Email", "Termin", "Kalender", "Notiz", "Terminal"):
        assert concept in result.cleaned, f"Whitelist-Wort {concept!r} wurde gescrubbt"


# ---------------------------------------------------------------------------
# Failure-Mode-6-Test: Echo-Paraphrase NUR Opener-Position.
# ---------------------------------------------------------------------------


def test_echo_paraphrase_mid_sentence_is_kept() -> None:
    """Mid-sentence Echo-Pattern (nach 60 Zeichen) DARF NICHT gescrubbt werden.

    Mandat-Failure-Mode 6: Manchmal will der User wirklich eine Bestaetigung
    im Echo-Stil ('Du moechtest also den Termin verschieben? Ja oder nein?').
    Filter NUR auf Opener-Position (erste 60 Zeichen) anwenden.
    """
    # Echo-Pattern in der Mitte (nach >60 Zeichen sauberen Texts)
    text = (
        "Ich habe den Termin am Freitag im Kalender und einen Konflikt erkannt. "
        "Du möchtest also den Termin verschieben? Ja oder nein?"
    )
    # Sicherheitscheck: Echo-Pattern liegt nach Position 60
    echo_pos = text.lower().find("du möchtest also")
    assert echo_pos > 60, f"Test-Setup-Bug: Echo-Pattern bei Position {echo_pos}"

    result = scrub_for_voice(text)
    # Mid-sentence Echo bleibt erhalten
    assert "möchtest also" in result.cleaned.lower()
    assert "rephrased_echo" not in result.actions


def test_fallback_used_true_only_for_stacktrace() -> None:
    """``fallback_used`` nur ``True`` wenn der gesamte Text durch Standard-
    Phrase ersetzt wurde (Stacktrace), nicht bei Teil-Scrub."""
    # Ein Filler-Opener entfernt nur einen Teil — kein Fallback
    result = scrub_for_voice("Großartige Frage! Halb drei.")
    assert result.fallback_used is False


def test_empty_input_returns_empty() -> None:
    """Leerer Input -> leeres Output, keine Exception."""
    result = scrub_for_voice("")
    assert result.cleaned == ""
    assert result.actions == []
    assert result.fallback_used is False


# ---------------------------------------------------------------------------
# Phase-1-Erweiterungen aus Research-Report 2026-04-28 Sektion 1.3.
#
# Vier neue Drift-Klassen, die die ursprueeglich Mandat-Phase-1-Schwarzliste
# nicht abdeckte aber genau in den Filter-Scope passen:
#
#  1. A1 "Sir"-Anrede (Szenarien 03/07 der Probe vom 2026-04-28).
#  2. "Sub-Agent"/"Supervisor-Agent" als Engineering-Reveal.
#  3. Tool-Args-Body-Leak in YAML-/key:value-Form.
#  4. Post-Scrub-Muell (z.B. einzelnes `}`) -> fallback_used=True.
# ---------------------------------------------------------------------------


def test_sir_anrede_is_removed() -> None:
    """A1: 'Sir' als Anrede wird gescrubbt — Mandat A1 (Alex statt Sir)."""
    text = "Sir, ich starte die Analyse."
    result = scrub_for_voice(text)
    low = result.cleaned.lower()
    assert "sir" not in low.split(",")[0].split()  # nicht als Wort im Opener
    assert "sir" not in low
    assert "ich starte die analyse" in low
    assert "removed_anrede_drift" in result.actions


def test_sir_anrede_mid_sentence_is_removed() -> None:
    """A1: 'Sir' auch mitten im Satz (nach Komma) wird entfernt."""
    text = "Erledigt, Sir. Die Datei ist gespeichert."
    result = scrub_for_voice(text)
    assert "Sir" not in result.cleaned
    assert "Datei" in result.cleaned
    assert "removed_anrede_drift" in result.actions


def test_legitimate_sir_in_quote_is_kept() -> None:
    """Innerhalb von Anfuehrungszeichen bleibt 'Sir' erhalten (Zitat-Schutz).

    Wenn der User Jarvis bittet, einen Songtext oder ein Zitat vorzulesen, in
    dem 'Sir' vorkommt, soll der Filter das nicht zerschiessen. Heuristik:
    'Sir' direkt in Quotes wird nicht gescrubbt.
    """
    text = 'Im Lied steht: "Yes, Sir, I can boogie." — kennst du das?'
    result = scrub_for_voice(text)
    assert "Sir" in result.cleaned, "Sir in Quote wurde faelschlich gescrubbt"


def test_sub_agent_jargon_is_removed() -> None:
    """'Sub-Agent' als Engineering-Reveal wird entfernt — Probe-Drift 03/07."""
    text = "Ich starte einen Sub-Agent, der die Datei analysiert."
    result = scrub_for_voice(text)
    assert "Sub-Agent" not in result.cleaned
    assert "sub-agent" not in result.cleaned.lower()
    # Kerninhalt bleibt
    assert "analysiert" in result.cleaned.lower()
    assert "removed_engineering_jargon" in result.actions


def test_supervisor_agent_jargon_is_removed() -> None:
    """'Supervisor-Agent' als Engineering-Reveal wird entfernt — Probe-Drift 13."""
    text = "Dein persoenlicher Supervisor-Agent erledigt das."
    result = scrub_for_voice(text)
    assert "Supervisor-Agent" not in result.cleaned
    assert "supervisor-agent" not in result.cleaned.lower()
    assert "erledigt" in result.cleaned.lower()
    assert "removed_engineering_jargon" in result.actions


def test_tool_args_yaml_block_is_removed() -> None:
    """Tool-Args-Body-Leak als YAML/key:value-Block — Probe-Drift 03 Body.

    Die Probe vom 2026-04-28 hat in Szenario 03 den vollstaendigen
    Sub-Jarvis-Tool-Aufruf-Body durchgeleakt:
        utterance: "Wie kann ich das beschleunigen?"
        context_hints:
          - Unklar ...
          - Benoetigt Kontext ...
        action: "die Beschleunigung ..."
        target: ""
    Diese YAML-aehnliche Form ist von TOOL_JSON_RE nicht gefangen.
    """
    text = (
        "Ich starte die Analyse.\n"
        '"Wie kann ich das beschleunigen?"\n'
        "context_hints:\n"
        "Unklar, was beschleunigt werden soll.\n"
        "Benoetigt Kontext zur aktuellen Aufgabe oder zum System.\n"
        'action: "die Beschleunigung einer unklaren Aufgabe analysiert"\n'
        'target: ""'
    )
    result = scrub_for_voice(text)
    assert "context_hints" not in result.cleaned
    assert "action:" not in result.cleaned
    assert "target:" not in result.cleaned
    assert "removed_tool_json" in result.actions
    # Der Resttext ('Ich starte die Analyse.') bleibt erhalten
    assert "Analyse" in result.cleaned


def test_post_scrub_residue_triggers_fallback() -> None:
    """Wenn nach allen Scrubs nur Muell uebrig bleibt -> fallback_used=True.

    Probe-Drift 12: Tool-Filter zerschoss alles bis auf '}', das geht
    unkommentiert an TTS und der User hoert quasi nichts. Wenn der
    gefilterte Text weniger als 3 alphanumerische Chars enthaelt,
    soll der Filter auf die Standard-Fehler-Phrase zurueckfallen.
    """
    # Nur ein '}' uebrig — mehr nicht
    text = '<spawn_worker>{"utterance": "test"}</spawn_worker>}'
    result = scrub_for_voice(text)
    # Entweder fallback_used=True ODER cleaned ist leer (beides akzeptabel)
    assert (result.fallback_used and result.cleaned), (
        f"Post-Scrub-Muell {result.cleaned!r} wurde nicht durch Fallback ersetzt"
    )
    assert "Es trat ein Fehler auf" in result.cleaned or result.cleaned.strip() == ""


def test_post_scrub_meaningful_text_no_fallback() -> None:
    """Defense gegen den Fallback-Trigger: substanzieller Resttext loest KEIN
    fallback_used aus."""
    text = "Erledigt. Die Datei ist da."
    result = scrub_for_voice(text)
    assert result.fallback_used is False
    assert "Datei" in result.cleaned


# ---------------------------------------------------------------------------
# Phase-1-Erweiterung 2 (2026-04-28 spaeter):
# Anthropic-Internal-Tag-Drifts aus der Re-Probe — Brain leakt:
#   1. ``<function_calls>...<invoke name="...">...</invoke></function_calls>``
#   2. Generische ``<tool_call>...</tool_call>`` / ``<tool_response>...</tool_response>``
#   3. Base64-Image-Strings als Body-Leak
# ---------------------------------------------------------------------------


def test_anthropic_function_calls_block_is_removed() -> None:
    """Anthropic-internes ``<function_calls><invoke name='...'>``-Format —
    Re-Probe-Drift Szenario 12 vom 2026-04-28."""
    text = (
        'Hier: <function_calls>\n'
        '<invoke name="spawn_worker">\n'
        '<parameter name="utterance">Lies die Datei</parameter>\n'
        '<parameter name="action">x</parameter>\n'
        '</invoke>\n'
        '</function_calls>\n'
        'Fertig.'
    )
    result = scrub_for_voice(text)
    assert "<function_calls>" not in result.cleaned
    assert "<invoke" not in result.cleaned
    assert "<parameter" not in result.cleaned
    assert "spawn_worker" not in result.cleaned
    assert "removed_tool_json" in result.actions
    assert "Hier:" in result.cleaned
    assert "Fertig" in result.cleaned


def test_generic_tool_call_tags_are_removed() -> None:
    """``<tool_call>{}</tool_call>`` mit irgendwelchem Content — Re-Probe
    01/06/11."""
    text = (
        'Lass mich schauen. <tool_call>\n'
        '}\n'
        '</tool_call>\n'
        '<tool_response>\n'
        '{"status": "ok"}\n'
        '</tool_response>\n'
        '09:17 Uhr.'
    )
    result = scrub_for_voice(text)
    assert "<tool_call>" not in result.cleaned
    assert "</tool_call>" not in result.cleaned
    assert "<tool_response>" not in result.cleaned
    assert "</tool_response>" not in result.cleaned
    assert "removed_tool_json" in result.actions
    # Kerninhalt bleibt
    assert "09:17" in result.cleaned


def test_base64_image_block_is_removed() -> None:
    """Base64-Image-String (sehr lange ASCII-Sequenz) — Re-Probe 08."""
    # Realistischer Trigger: data-URI mit langer Base64-Sequenz
    text = (
        'Hier das Bild: data:image/webp;base64,'
        + 'A' * 800
        + '. Fertig.'
    )
    result = scrub_for_voice(text)
    # Base64-Block ist weg
    assert ("A" * 200) not in result.cleaned, "Lange Base64-Sequenz nicht entfernt"
    assert "data:image" not in result.cleaned
    assert "removed_tool_json" in result.actions
    assert "Fertig" in result.cleaned


def test_short_clean_alphanumeric_is_kept() -> None:
    """Defense gegen Base64-False-Positive: kurze alphanumerische Strings
    (z.B. Datei-Namen, Hashes, Tokens in normaler Kommunikation) bleiben."""
    text = "Die Datei abc123def456 wurde gespeichert."
    result = scrub_for_voice(text)
    assert "abc123def456" in result.cleaned
    assert result.actions == []


# ---------------------------------------------------------------------------
# Phase-2-Anti-Pattern-Filter — die in voice_e2e_probe.py:ANTI_PATTERNS
# gelisteten Strings sollen vom Filter VOR der Probe-Heuristik gescrubbt
# werden, nicht nur als Probe-Detection-Pattern dienen. Sonst zaehlt jeder
# Brain-Output mit Anti-Pattern als DRIFT, obwohl der User es nie hoert.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("opener,rest", [
    ("Lass mich kurz", "schauen. Ja, gespeichert."),
    ("Lass mich kurz", "checken. Halb drei."),
    ("Let me think,", "the answer is 42."),
    ("Let me think.", "It's halb drei."),
])
def test_filler_selbstreferenz_opener_is_removed(opener: str, rest: str) -> None:
    """Phase-2-Anti-Pattern: 'Lass mich kurz' / 'Let me think' als Opener.

    Diese Phrasen sind in voice_e2e_probe.py:ANTI_PATTERNS unter
    'Filler-Selbstreferenz' gelistet. Der Filter muss sie wegschneiden,
    sonst loest die Probe-Heuristik einen DRIFT-Treffer aus, obwohl der
    User die Phrase ohnehin nicht hoeren soll.
    """
    text = f"{opener} {rest}"
    result = scrub_for_voice(text)
    low = result.cleaned.lower()
    assert "lass mich kurz" not in low
    assert "let me think" not in low
    # Kerninhalt (rest minus Opener-Komma) bleibt erhalten
    rest_substantive = rest.split(".", 1)[-1].strip().lower()
    if rest_substantive:
        assert rest_substantive in low
    assert "removed_filler_opener" in result.actions


def test_filler_selbstreferenz_mid_sentence_is_kept() -> None:
    """Failure-Mode-6-analog: mid-sentence 'lass mich kurz' bleibt erhalten.

    Wenn der User wirklich darum bittet ('Schau dir das an, lass mich kurz
    mal nachdenken, dann sage ich es dir'), darf der Filter das nicht
    zerschiessen. Wie bei Echo-Paraphrase nur am Opener (<= 60 Zeichen).
    """
    # Mind. 61 Zeichen vor 'lass mich kurz' damit Failure-Mode-6 greift.
    text = (
        "Ich gehe das systematisch durch, "
        "und im naechsten Schritt soll ich lass mich kurz nachdenken, "
        "und dann antworte ich."
    )
    pos = text.lower().find("lass mich kurz")
    assert pos > 60, f"Test-Setup-Bug: pos={pos}"

    result = scrub_for_voice(text)
    assert "lass mich kurz" in result.cleaned.lower()
    assert "removed_filler_opener" not in result.actions


# ---------------------------------------------------------------------------
# Audit F-AUDIT-4 (2026-04-29): prosaisch geschriebene Tool-Args.
# Brain leakte in Probe-Run vom 2026-04-29 Szenario 07 wortlich:
#   "spawn_worker with utterance is Analysiere... context_hints is [...]
#   action is ... target is ..."
# Der Filter musste um dieses natuerlichsprachige Format erweitert werden.
# ---------------------------------------------------------------------------


def test_tool_call_prose_with_keys_is_removed() -> None:
    """spawn_worker with utterance is X context_hints is Y → gescrubbt.

    Realistischer Probe-Output 2026-04-29 Szenario 07. Filter muss den
    ganzen Tool-Call-Block bis Satzende rausschneiden. Resttext bleibt.
    """
    text = (
        "Okay. spawn_worker with utterance is Analysiere das gesamte "
        "Projektverzeichnis context_hints is Vollstaendige Projektstruktur "
        "erfassen action is das Projekt analysiert target is Arbeitsordner. "
        "Fertig."
    )
    result = scrub_for_voice(text)
    assert "spawn_worker" not in result.cleaned
    assert "utterance is" not in result.cleaned
    assert "context_hints" not in result.cleaned
    assert "action is" not in result.cleaned
    assert "removed_tool_json" in result.actions
    # Resttext bleibt
    assert "Okay" in result.cleaned
    assert "Fertig" in result.cleaned


def test_tool_args_prose_without_tool_name_is_removed() -> None:
    """'utterance is X' allein (ohne Tool-Name-Prefix) wird auch gescrubbt."""
    text = "Ich versuche es. utterance is lies die Datei. Fertig."
    result = scrub_for_voice(text)
    assert "utterance is" not in result.cleaned
    assert "removed_tool_json" in result.actions
    assert "Ich versuche es" in result.cleaned
    assert "Fertig" in result.cleaned


def test_legitimate_is_sentence_is_kept() -> None:
    """Defense gegen False-Positive: legitime 'X is Y'-Saetze bleiben.

    Wenn der User sagt 'Die Hauptstadt ist Paris', oder Brain antwortet
    'Es ist halb drei' — KEIN Match auf TOOL_ARGS_PROSE_RE, weil 'Die
    Hauptstadt' und 'Es' keine Tool-Arg-Schluessel sind.
    """
    text = "Es ist halb drei. Die Datei ist gespeichert."
    result = scrub_for_voice(text)
    assert "ist halb drei" in result.cleaned
    assert "Datei ist gespeichert" in result.cleaned
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
        "Mach ich, ich lasse dafür einen OpenClaw-Subagent "
        "ein Hello-World-Programm schreiben."
    )
    result = scrub_for_voice(text)
    assert "OpenClaw" not in result.cleaned
    assert "Subagent" not in result.cleaned
    assert "removed_engineering_jargon" in result.actions


def test_bare_subagent_still_scrubbed_without_brand_prefix() -> None:
    """Bare 'Subagent' / 'Sub-Agent' ohne Brand-Praefix bleibt gescrubbt."""
    text = "Ich aktiviere einen Subagent für die Aufgabe."
    result = scrub_for_voice(text)
    assert "Subagent" not in result.cleaned
    assert "removed_engineering_jargon" in result.actions


def test_openclaw_compound_prefix_stripped_but_noun_kept() -> None:
    """'OpenClaw-Mission' -> 'Mission' (brand prefix removed, noun stays)."""
    text = "Die OpenClaw-Mission ist fertig."
    result = scrub_for_voice(text)
    assert "OpenClaw" not in result.cleaned
    assert "Mission" in result.cleaned


def test_openclaw_subagenten_plural_also_scrubbed() -> None:
    """Plural 'OpenClaw-Subagenten' muss ebenfalls gescrubbt werden."""
    text = "Mehrere OpenClaw-Subagenten arbeiten parallel."
    result = scrub_for_voice(text)
    assert "OpenClaw" not in result.cleaned
    assert "Subagenten" not in result.cleaned


def test_scrub_strips_end_call_sentinel() -> None:
    from jarvis.speech.hangup import END_CALL_SIGNAL

    result = scrub_for_voice(f"Bis später, Alex. {END_CALL_SIGNAL}", language="de")
    assert END_CALL_SIGNAL not in result.cleaned
    assert result.cleaned.strip() == "Bis später, Alex."
    assert "stripped_end_signal" in result.actions


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


def test_scrub_keeps_humanized_readback_with_quotes() -> None:
    """No false positive: a clean readback that merely quotes a UI label (and
    contains an apostrophe + colon-free) must pass through untouched."""
    text = "Erledigt — die Einstellungen sind auf 'Bluetooth und Geräte' offen."
    result = scrub_for_voice(text, language="de")
    assert result.fallback_used is False
    assert "Bluetooth und Geräte" in result.cleaned


def test_scrub_keeps_sentence_with_inline_brace_not_a_dump() -> None:
    """A sentence that happens to mention a brace mid-text is NOT a dump (it
    does not OPEN with a container) — must pass."""
    text = "Ich habe die Funktion test() geprüft, alles in Ordnung."
    result = scrub_for_voice(text, language="de")
    assert result.fallback_used is False
    assert "test()" in result.cleaned or "test" in result.cleaned
