"""Output filter for the voice path — ``scrub_for_voice``.

Persona mandate phase 1: brain output → TTS path scrubs tool JSON,
stack traces, engineering jargon, self-reference, echo paraphrase, and
filler openers. Regex-only, NO LLM calls (latency-fatal, mandate § "NICHT tun").

API:
    from jarvis.brain.output_filter import scrub_for_voice
    result = scrub_for_voice(text, language="de")
    result.cleaned        # scrubbed text, ready for TTS
    result.actions        # ["removed_tool_json", "rephrased_echo", ...]
    result.fallback_used  # True when the entire text was replaced by a standard phrase

Order of operations (stack trace is an early return):

    1. Stack trace → standard phrase, ``fallback_used=True`` (early return)
    2. Markdown strip (``**``, ``##``, ``` ``` ```, leading ``-``/``*``)
    3. Remove tool-call JSON (three forms: fn-call, inline, pure JSON)
    4. Remove self-reference ("Als KI", "Als Sprachmodell", "Ich bin nur")
    5. Echo paraphrase ONLY at opener position (``<= OPENER_BUDGET = 60`` chars)
    6. Remove filler openers ("Großartige Frage", "Tolle Frage", ...)
    7. Remove engineering jargon (with whitelist protection via hyphen
       lookbehind/lookahead — compounds like "Browser-Provider" are preserved)
    8. Normalise whitespace

Failure mode 6 (mandate): echo paraphrase ONLY at opener position. Sometimes
the user genuinely wants an echo-style confirmation ("Du moechtest also den
Termin verschieben? Ja oder nein?") — that must not be destroyed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from jarvis.speech.hangup import END_CALL_SIGNAL

# Mandate: user-concept words are sacred — NEVER scrubbed as jargon.
# Not referenced directly in a regex because they are not in ``JARGON_WORDS``
# anyway — the list serves as documentation and a fallback assertion
# (if someone later extends ``JARGON_WORDS``, the assert below catches it).
WHITELIST_WORDS: tuple[str, ...] = (
    "Datei", "Email", "Browser", "Terminal",
    "Notiz", "Termin", "Kalender",
)

# Mandate: engineering jargon — standalone words are scrubbed, but not
# inside hyphen-compounds ("Browser-Provider" is anchored to "Browser",
# a user-concept word, so the compound is preserved).
JARGON_WORDS: tuple[str, ...] = (
    "Harness", "MCP", "Subprocess", "Provider",
)

# Phase 1 extension 2026-04-28: engineering jargon compounds that, as a whole
# compound (with hyphen), reveal the implementation and have no user-concept
# anchor. Removed from output, including the surrounding clause when the
# compound is the subject. Probe-Drift 03/07/13 from 2026-04-28.
JARGON_COMPOUNDS: tuple[str, ...] = (
    "Sub-Agent", "Sub-Agenten",
    "Supervisor-Agent", "Supervisor-Agenten",
    "Subagent", "Subagenten",
)

# Defensive safety check: no whitelist word must appear in the jargon list.
# If that ever happens, the filter would kill one of the sacred user-concept
# words — a programming error.
assert not (set(WHITELIST_WORDS) & set(JARGON_WORDS)), (
    "Whitelist und Jargon-Liste ueberlappen — User-Konzept-Wort wuerde gescrubbt."
)

# Echo/filler patterns only in the first N characters. Mandate failure-mode 6.
OPENER_BUDGET = 60

FALLBACK_PHRASES: dict[str, str] = {
    "de": "Es trat ein Fehler auf.",
    "en": "An error occurred.",
    # Runtime-output-language doctrine: every spoken phrase table carries all
    # supported locales (de/en/es) so a Spanish-pinned user never falls back to
    # German. Used by the stacktrace, raw-dump, and post-scrub-residue guards.
    "es": "Se produjo un error.",
}


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

# Stack trace: Python-style. Greedy up to double newline or end.
STACKTRACE_RE = re.compile(
    r"Traceback \(most recent call last\):.*?(?=\n\s*\n|\Z)",
    re.DOTALL,
)

# Raw data-structure dump guard (live bug 2026-06-22). A code path may ``str()``
# a tool-result container (dict / list of dicts) instead of humanizing it — e.g.
# the whole ``dispatch_to_harness`` result ``{'harness': 'screenshot',
# 'exit_code': 0, 'stdout': …, 'cost_usd': …, 'duration_ms': …}`` reached a
# readback verbatim. The per-pattern tool-leak rules below only catch SPECIFIC
# named shapes ({"tool":…}, XML, YAML, prose) and SPECIFIC keys, so a new result
# shape or a single-quoted Python repr slips through. This is the STRUCTURAL,
# key-independent, quote-style-independent guard that makes the whole bug class
# impossible at the common chokepoint: if the text OPENS with a container ``{``/
# ``[`` and carries a mapping signature (a quoted ``key:`` or a ``key='…'``
# repr), it is a machine dump, never a spoken sentence — fail-closed to the
# standard phrase, exactly like a stack trace. Real prose never opens with a
# brace, so this does not touch a humanized readback (which reads "Erledigt — …").
RAW_REPR_OPENER_RE = re.compile(r"^\s*[\{\[]")
REPR_SIGNATURE_RE = re.compile(
    r"['\"][^'\"]{0,120}['\"]\s*:"   # 'key': / "key":  (JSON or Python dict repr)
    r"|\b\w+\s*=\s*['\"]"            # key='…'          (Python kwargs/obj repr)
)

# Raw shell / PowerShell / .NET command guard (live bug 2026-06-28). The fast
# tier, on a garbled "navigate Discord" turn, emitted a SendKeys PowerShell
# command as its REPLY TEXT and TTS read it aloud "mit Sonderzeichen und allem"
# (Add-Type … [System.Windows.Forms.SendKeys]::SendWait('^(g)')). The tool-leak
# rules above only catch tool NAMES / JSON / XML — a bare command string has
# none of those, so it slipped through to TTS. Each alternative below is a
# code-ONLY signature that never appears in spoken German/English, so matching is
# fail-closed to the standard phrase (like a stacktrace) without touching prose:
#   * a [Namespace.Type]::Method static call (pure .NET/PowerShell)
#   * Add-Type / -AssemblyName / SendKeys (PowerShell-isms / Win32 API names)
#   * a $env:VAR reference
#   * an explicit shell invocation (cmd /c …, powershell -…, bash -c …)
SHELL_COMMAND_RE = re.compile(
    r"\[[\w.]+\]::\w+"
    r"|\bAdd-Type\b"
    r"|\b-AssemblyName\b"
    r"|\bSendKeys\b"
    r"|\$env:\w+"
    r"|\b(?:cmd\s+/c\b|powershell(?:\.exe)?\s+-[A-Za-z]|bash\s+-c\b)",
    re.IGNORECASE,
)

# Tool-call patterns:
#   1) tool_name({"...": "..."})       — function-call form (OpenAI)
#   2) tool_name{"...": "..."}         — Anthropic tool-use inline
#   3) {"tool": "..."} / {"op": "..."} — pure JSON
#   4) tool_name(key='val', ...)       — Python keyword args (probe-drift 12)
#   5) <tool_name>...</tool_name>      — XML tool-use (Anthropic-style leak)
#
# Patterns 4 and 5 are tool-name-specific (conservative) so harmless
# Python doc snippets ("``print(x=1)``") are not destroyed.
TOOL_NAMES: tuple[str, ...] = (
    # Current spawn tool name is ``spawn_worker``. The legacy ``spawn_openclaw``
    # and ``spawn_sub_jarvis`` names stay in the scrub list for backwards-compat
    # (old logs / replays must never leak the tool name into the voice path).
    "spawn_worker", "spawn_openclaw", "spawn_sub_jarvis",
    "dispatch_to_harness", "dispatch_to_admin",
    "run_shell", "screen_snapshot", "multi_spawn",
    "search_web", "open_app", "type_text", "click", "hotkey",
    "remember", "whoami", "execute_multi_action",
    "verify_via_curl", "verify_localhost", "start_preview_server",
)

TOOL_CALL_FN_RE = re.compile(
    r"\b\w+\s*\(\s*\{[^{}]*\}\s*\)",
)
TOOL_CALL_INLINE_RE = re.compile(
    r"\b\w+\{\"[^\"]+\"\s*:[^}]*\}",
)
TOOL_JSON_RE = re.compile(
    r"\{[^{}]*\"(?:tool|action|op|command|name|args|parameters|utterance)\""
    r"\s*:\s*[^}]*\}",
    re.IGNORECASE,
)
# Tool-Name als Python-style keyword-call: ``spawn_openclaw(utterance='x', ...)``
TOOL_CALL_KW_RE = re.compile(
    r"\b(?:" + "|".join(TOOL_NAMES) + r")\s*\([^)]{0,2000}\)",
    re.DOTALL,
)
# XML-Tool-Tags inkl. Inner-Content: ``<spawn_openclaw>...</spawn_openclaw>``
TOOL_XML_RE = re.compile(
    r"<(?:" + "|".join(TOOL_NAMES) + r")\b[^>]*>"
    r".*?"
    r"</(?:" + "|".join(TOOL_NAMES) + r")>",
    re.DOTALL,
)

# Phase-1-Erweiterung 2 (2026-04-28 spaeter):
# Anthropic-internes ``<function_calls><invoke name="...">...</invoke></function_calls>``-
# Format. Brain leakt das gelegentlich wortlich in den Output. Pattern matcht
# den ganzen Block + greedy bis schliessendem Tag. Ausserdem ein Standalone-
# ``<invoke>`` falls der ``</function_calls>``-Wrapper fehlt.
ANTHROPIC_FUNCTION_CALLS_RE = re.compile(
    r"<function_calls>.*?</function_calls>",
    re.DOTALL | re.IGNORECASE,
)
ANTHROPIC_INVOKE_RE = re.compile(
    r"<invoke\b[^>]*>.*?</invoke>",
    re.DOTALL | re.IGNORECASE,
)

# Generische Tool-Wrapper-Tags wie ``<tool_call>...</tool_call>`` und
# ``<tool_response>...</tool_response>``. Konservativ auf bekannte
# Wrapper-Namen beschraenkt, damit harmlose XML/HTML im User-Content
# ("<tag>x</tag>" als Beispiel-Doku) nicht zerschossen wird.
GENERIC_TOOL_WRAPPER_RE = re.compile(
    r"<(?:tool_call|tool_response|tool_use|function_results)\b[^>]*>"
    r".*?"
    r"</(?:tool_call|tool_response|tool_use|function_results)>",
    re.DOTALL | re.IGNORECASE,
)

# Base64-Image-Drift: ``data:image/...;base64,<long-string>`` + lange
# Standalone-Base64-Sequenzen (>=200 Chars zusammenhaengende Base64-Chars).
# Re-Probe-Drift Szenario 08 vom 2026-04-28: Brain leakte einen kompletten
# WebP-Image als Body-String.
BASE64_DATA_URI_RE = re.compile(
    r"data:[a-zA-Z]+/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+",
)

# Audit F-AUDIT-4 (2026-04-29): Brain leakt Tool-Calls als prosaische
# Aufzaehlung ("spawn_openclaw with utterance is X context_hints is Y
# action is Z target is W"). Probe vom 2026-04-29 Szenario 07 zeigte
# das im Voice-Output. Das ist kein JSON, kein YAML, kein XML — der
# Filter musste vorher um dieses natuerlichsprachige Format erweitert
# werden.
#
# Pattern: tool-name + " with " + ein oder mehrere "<key> is <value>"-
# Phrasen, getrennt durch Leerzeichen oder ".". Greedy bis Doppel-Newline
# oder Satzgrenze (max 600 Chars als Sicherheits-Cap).
TOOL_CALL_PROSE_RE = re.compile(
    r"\b(?:" + "|".join(TOOL_NAMES) + r")\s+with\s+"
    r"[\w\-]+\s+is\s+.*?"
    r"(?=\n\s*\n|\Z|(?<=\.)(?=\s+[A-ZÄÖÜ]))",
    re.DOTALL | re.IGNORECASE,
)
# Fallback: einzelne "<key> is <value>"-Phrasen mit Tool-Arg-Schluesseln
# auch ohne Tool-Name-Prefix (Brain koennte Tool-Name weggelassen haben).
TOOL_ARGS_PROSE_KEYS: tuple[str, ...] = (
    "utterance", "context_hints", "context hints",
    "action", "target", "tool_hint", "tool hint",
    "step_id", "step id",
)
TOOL_ARGS_PROSE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in TOOL_ARGS_PROSE_KEYS) + r")"
    r"\s+is\s+[^.\n]{1,400}",
    re.IGNORECASE,
)
LONG_BASE64_RE = re.compile(
    r"[A-Za-z0-9+/=]{200,}",
)

# Web-search / SERP source artefacts (live forensic 2026-06-28, voice Turn 4).
# The brain occasionally reads a raw search hit verbatim — a title, a snippet,
# a URL, a bare domain, or the "Weitere Ergebnisse von <domain>" / "more results
# from <domain>" SERP footer — instead of synthesizing an answer (the whole
# DuckDuckGo result list incl. "26.06.2017 · …Weitere Ergebnisse von
# www.gutefrage.net" was spoken). The search_web tool result now instructs the
# brain to synthesize (primary fix); these patterns are the fail-closed defense
# so a source URL / domain / footer can never reach TTS. Real spoken prose has
# no http(s):// or bare www. token, so this does not touch a clean answer.
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
# The SERP "more results from <source>" footer (de/en/es). Cut it before the
# bare-www pass so "Weitere Ergebnisse von www.x.de" goes as one unit.
MORE_RESULTS_RE = re.compile(
    r"\b(?:weitere|mehr)\s+ergebnisse\s+(?:von|auf|bei)\s+\S+"  # de  # i18n-allow
    r"|more\s+results\s+(?:from|on|for)\s+\S+"                  # en
    r"|m[aá]s\s+resultados\s+(?:de|en|para)\s+\S+",             # es
    re.IGNORECASE,
)
# A bare www-prefixed domain reference ("www.gutefrage.net"). Anchored on the
# "www." token so a normal sentence is never touched (no false positive on a
# plain word that happens to contain a dot).
BARE_WWW_RE = re.compile(r"\bwww\.\S+", re.IGNORECASE)

# Markdown
MARKDOWN_BOLD_RE = re.compile(r"\*\*")
MARKDOWN_HEADER_RE = re.compile(r"^\s*#{1,6}\s+", re.MULTILINE)
CODE_FENCE_RE = re.compile(r"```[^`]*```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
LIST_BULLET_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)

# Self-Reference (DE+EN). Schneidet die ganze Klausel inkl. Satzpunkt weg.
SELF_REF_RE = re.compile(
    r"\b("
    r"Als KI|Als Sprachmodell|Ich bin nur|Ich bin lediglich|"
    r"As an AI|I'?m just a language model|I am a language model"
    r")\b[^.!?]*[.!?]?\s*",
    re.IGNORECASE,
)

# Background-action narration (DE+EN+ES). The maintainer finds it annoying when
# Jarvis ANNOUNCES internal bookkeeping it does silently in the background —
# "I'm noting that", "let me look at the last transcription", "ich notiere mir
# das", "ich schaue mir die letzte Transkription an", "tomo nota". The action
# still happens; it is just never spoken. Cuts the whole narration clause incl.
# its sentence punctuation (same shape as SELF_REF_RE). Applies in BOTH normal
# and ack mode. The "look at the previous transcript/answer" alternatives are
# gated on a leading intent verb ("let me / I'll / ich schaue") so a content
# lead-in like "Looking at the data, the answer is X" is NOT stripped.
BACKGROUND_ACTION_RE = re.compile(
    r"\b("
    # --- noting / saving down (DE) ---
    r"ich notiere(?:\s+mir)?|"
    r"ich merke\s+mir|"
    r"ich halte\s+(?:das|es|alles)\s+fest|"
    # --- reviewing the previous transcript / answer (DE) ---
    r"ich (?:schaue|sehe|gucke)(?:\s+mir)?\b[^.!?]*?\b"
    r"(?:transkription|transkript|aufzeichnung|aufnahme|"
    r"(?:letzte|vorherige|bisherige)[ns]?\s+antwort)|"
    # --- noting (EN) — kept narrow ("noting/jotting", not "saving/recording",
    #     so a legit "I'm saving the file" confirmation is NOT stripped) ---
    r"(?:I'?m|I am)\s+(?:noting|jotting)\b(?:[^.!?]*?\bdown)?|"
    r"(?:I'?ll|I will)\s+(?:note|jot)\b|"
    r"(?:let me|I'?ll)\s+make\s+a\s+note|"
    # --- reviewing the previous transcript / answer (EN) ---
    r"(?:let me|I'?ll|I'?m going to|I will)\s+"
    r"(?:look at|check|review|pull up|go through)\b[^.!?]*?\b"
    r"(?:transcript(?:ion)?|recording|(?:last|previous|earlier|prior)\s+"
    r"(?:answer|response|conversation))|"
    # --- noting / reviewing (ES) — "anoto/apunto", not "guardo" (= save) ---
    r"tomo nota|"
    r"(?:lo|eso|esto)\s+(?:anoto|apunto)|"
    r"(?:voy a|d[eé]jame)\s+(?:revisar|mirar|ver|consultar)\b[^.!?]*?\b"
    r"(?:transcripci[oó]n|grabaci[oó]n|(?:[uú]ltima|anterior)\s+respuesta)"
    r")\b[^.!?]*[.!?]?\s*",
    re.IGNORECASE,
)

# Echo-Paraphrase — nur am Opener (durch Position-Slicing in der Funktion,
# nicht im Regex selbst).
ECHO_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in [
        r"^\s*Du möchtest also\b[^.!?]*[.!?]\s*",
        r"^\s*Ich verstehe(?:,|\s+)?\s*dass\b[^.!?]*[.!?]\s*",
        r"^\s*If I understand correctly\b[^.!?]*[.!?]\s*",
        r"^\s*You'?d like me to\b[^.!?]*[.!?]\s*",
        r"^\s*Verstanden(?:,|\s+)?\s*du\b[^.!?]*[.!?]\s*",
    ]
)

# Filler-Opener — Phase-2-Anti-Pattern-Liste aus voice_e2e_probe.py
# erweitert um Filler-Selbstreferenz ('Lass mich kurz', 'Let me think').
# Pattern matcht NUR am Opener; mid-sentence Vorkommen bleibt erhalten
# (Failure-Mode-6-analog).
FILLER_OPENER_RE = re.compile(
    r"^\s*("
    # Klassisch-Phase-0
    r"Großartige Frage|Grossartige Frage|Tolle Frage|Geniale Frage|"
    r"Great question|Excellent question|Good question|"
    # Phase-2-Filler-Selbstreferenz (ANTI_PATTERNS-Liste in voice_e2e_probe.py)
    r"Lass mich kurz[^.!?]*?(?=[.!?,]|$)|"
    r"Let me think"
    r")[!.?,]*\s*",
    re.IGNORECASE,
)

# Engineering-Jargon — Standalone-Worte, kein Hyphen-Compound.
# WICHTIG: ``(?<!\w-)`` muss VOR der Alternative stehen, nicht dahinter —
# Lookbehind am Regex-Ende prueft die 2 Chars vor Match-END, nicht
# Match-START. Das hat in einem frueheren Entwurf "Brain-Provider" zerschossen.
# Siehe Test ``test_clean_text_passes_through_unchanged[file-summary]``.
JARGON_RE = re.compile(
    r"(?<!\w-)"     # kein "Browser-" / "Brain-"-Praefix vorne
    r"\b(?:" + "|".join(JARGON_WORDS) + r")\b"
    r"(?!-\w)",     # kein "-Server" / "-Provider"-Suffix folgen lassen
    re.IGNORECASE,
)

# Engineering-Jargon-Compounds (mit Bindestrich) — komplett raus, weil sie
# kein User-Konzept anhaengen ("Sub-Agent" hat keinen Whitelist-Anker wie
# "Browser" oder "Datei"). Pattern matcht Compound + folgender Artikel-/
# Nebenwort-Phrase wenn der Satz mit dem Compound startet, sonst nur der
# Compound selbst.
#
# 2026-05-24: the 2026-05-13 "OpenClaw is a brand name, let it through"
# exception is REVERSED. The OpenClaw subprocess was retired (the worker now
# runs Opus 4.7 directly), so Jarvis must never say "OpenClaw" or "OpenClaw-
# Subagent" — that would claim a component that no longer exists. The negative
# lookbehind is removed, and OPENCLAW_RE below strips the brand token itself.
JARGON_COMPOUND_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(c) for c in JARGON_COMPOUNDS) + r")\b",
    re.IGNORECASE,
)

# 2026-05-24: strip the retired "OpenClaw" brand from voice output. Removes the
# "OpenClaw-" compound prefix ("OpenClaw-Mission" -> "Mission"; "OpenClaw-
# Subagent" -> "Subagent", which JARGON_COMPOUND_RE then drops) and any
# standalone "OpenClaw"/"OpenClore" (common STT mis-spelling of the brand).
OPENCLAW_RE = re.compile(r"\bOpenCl(?:aw|ore)-?", re.IGNORECASE)

# A1-Drift (Mandat A1): "Sir"-Anrede aus dem Output entfernen.
# Pattern matcht ``Sir`` als Anrede in drei Formen:
#   1) Opener mit Komma:    "Sir, ich starte..." -> "ich starte..."
#   2) Tail nach Komma:     "Erledigt, Sir."    -> "Erledigt."
#   3) Standalone-Wort:     "Sir." (selten, aber moeglich nach Anrede-Drift)
# Innerhalb von Quotes (``"Yes, Sir, ..."``) wird NICHT gescrubbt — Zitat-
# Schutz fuer Songtexte, Zitate, Filme. Heuristik: wenn ``Sir`` zwischen zwei
# Anfuehrungszeichen liegt, kein Match.
SIR_OPENER_RE = re.compile(r"^\s*Sir\s*,\s*", re.IGNORECASE)
SIR_TAIL_RE = re.compile(r",\s*Sir\b", re.IGNORECASE)
QUOTE_PROTECT_RE = re.compile(r'"[^"]*\bSir\b[^"]*"', re.IGNORECASE)

# Tool-Args-YAML-Block — Probe-Drift 03 vom 2026-04-28. Erkennt YAML-aehnliche
# Bloecke mit Tool-Arg-Schluesseln wie ``context_hints:``, ``action:``,
# ``target:``, ``utterance:``. Greedy bis zum naechsten Doppel-Newline oder
# Ende — zerschneidet den ganzen YAML-Block.
TOOL_ARGS_YAML_KEYS: tuple[str, ...] = (
    "context_hints", "action", "target", "utterance",
    "tool_hint", "step_id", "args", "parameters",
)
TOOL_ARGS_YAML_RE = re.compile(
    r"(?:^|\n)"
    r"(?:" + "|".join(TOOL_ARGS_YAML_KEYS) + r")\s*:\s*"
    r"(?:.*?)"
    r"(?=\n\s*\n|\n[A-ZÄÖÜ][a-zäöüß]|\Z)",
    re.DOTALL | re.IGNORECASE,
)

# Post-Scrub-Muell-Threshold: nach allen Filtern muss der Output mindestens
# diese Anzahl alphanumerischer Zeichen enthalten, sonst wird er als
# Filter-Artefakt erkannt und durch die Standard-Phrase ersetzt.
# Probe-Drift 12 vom 2026-04-28: Output war einzelnes ``}``.
MIN_MEANINGFUL_CHARS = 3


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@dataclass
class ScrubResult:
    """Ergebnis von ``scrub_for_voice``.

    Attributes:
        cleaned: Der gescrubbte Text, ready fuer TTS.
        actions: Liste der durchgefuehrten Operationen, fuer Telemetrie/Debug.
            Beispiele: ``"replaced_stacktrace"``, ``"removed_tool_json"``,
            ``"stripped_markdown"``, ``"removed_self_reference"``,
            ``"rephrased_echo"``, ``"removed_filler_opener"``,
            ``"removed_engineering_jargon"``.
        fallback_used: ``True`` wenn der gesamte Text durch eine Standard-
            Phrase ersetzt wurde (aktuell nur bei Stacktrace-Treffer).
    """

    cleaned: str
    actions: list[str] = field(default_factory=list)
    fallback_used: bool = False


def scrub_for_voice(
    text: str, *, language: str = "de", ack_mode: bool = False
) -> ScrubResult:
    """Bereinigt Brain-Output fuer die TTS-Synthese.

    Args:
        text: Der zu scrubbende Text (Brain-Response, OpenClaw-Summary,
            Skill-Output, Announcement-Text, ...).
        language: ``"de"`` oder ``"en"`` — bestimmt die Fallback-Phrase
            bei Stacktrace-Treffer.
        ack_mode: ``True`` markiert den Aufruf als Pre-Thinking-Ack
            (Flash-Brain). Im ack_mode wird der ``FILLER_OPENER_RE``-Pass
            uebersprungen, weil Flash-Brain-Acks per Persona-Spec genau
            solche Opener verwenden duerfen ("Lass mich kurz nachschauen.",
            "Let me check on that."). Alle anderen Filter (Schwarzliste,
            Stacktrace, Markdown, Self-Reference) bleiben aktiv.

    Returns:
        ``ScrubResult`` mit cleaned/actions/fallback_used.
    """
    if not text or not text.strip():
        return ScrubResult(cleaned="", actions=[], fallback_used=False)

    actions: list[str] = []

    # 1. Stacktrace: Early-Return mit Standard-Phrase. Mandat: "komplett raus,
    #    durch 'Es trat ein Fehler auf.' ersetzt".
    if STACKTRACE_RE.search(text):
        fallback = FALLBACK_PHRASES.get(language, FALLBACK_PHRASES["de"])
        return ScrubResult(
            cleaned=fallback,
            actions=["replaced_stacktrace"],
            fallback_used=True,
        )

    # 1b. Raw data-structure dump: Early-Return mit Standard-Phrase. A text that
    #     OPENS with a container ({ / [) AND carries a mapping signature is a
    #     machine repr (a str()'d tool-result dict / JSON array), never a spoken
    #     sentence. Fail-closed at the common chokepoint so NO path — present or
    #     future — can ever speak/show a raw {'…': …} dump again (live bug
    #     2026-06-22: the whole dispatch_to_harness result reached a CU readback).
    if RAW_REPR_OPENER_RE.match(text) and REPR_SIGNATURE_RE.search(text):
        fallback = FALLBACK_PHRASES.get(language, FALLBACK_PHRASES["de"])
        return ScrubResult(
            cleaned=fallback,
            actions=["replaced_raw_repr"],
            fallback_used=True,
        )

    # 1c. Raw shell / PowerShell / .NET command: Early-Return mit Standard-Phrase.
    #     A code-only signature (a [Type]::Method call, Add-Type/-AssemblyName/
    #     SendKeys, $env:, an explicit shell invocation) is never a spoken
    #     sentence, so the whole text is replaced rather than partially stripped
    #     (a half-spoken command is worse than the generic phrase). Live bug
    #     2026-06-28: TTS read a SendKeys PowerShell command aloud verbatim.
    if SHELL_COMMAND_RE.search(text):
        fallback = FALLBACK_PHRASES.get(language, FALLBACK_PHRASES["de"])
        return ScrubResult(
            cleaned=fallback,
            actions=["replaced_shell_command"],
            fallback_used=True,
        )

    out = text

    # 0. Hang-up control sentinel: the brain appends END_CALL_SIGNAL to signal
    #    session end. The signal is read upstream on the RAW response; here we
    #    guarantee it can never reach TTS (defense-in-depth). If the text was
    #    nothing but the token, return empty so the caller stays silent.
    if END_CALL_SIGNAL in out:
        out = out.replace(END_CALL_SIGNAL, "")
        actions.append("stripped_end_signal")
        if not out.strip():
            return ScrubResult(cleaned="", actions=actions, fallback_used=False)

    # 2. Markdown — Code-Fences zuerst (sonst greift INLINE_CODE auf den
    #    Inhalt der Fence). Inline-Code behaelt den Inhalt, nur Backticks weg.
    new = CODE_FENCE_RE.sub(" ", out)
    new = INLINE_CODE_RE.sub(r"\1", new)
    new = MARKDOWN_BOLD_RE.sub("", new)
    new = MARKDOWN_HEADER_RE.sub("", new)
    new = LIST_BULLET_RE.sub("", new)
    if new != out:
        actions.append("stripped_markdown")
        out = new

    # 3. Tool-Call-JSON / -KW / -XML / YAML-Args / Anthropic-Tags / Base64 —
    #    alle Tool-Use-/Internal-Leaks rausschneiden.
    #    Reihenfolge: zuerst die groessten Wrapper-Bloecke (function_calls,
    #    generic_tool_wrappers, base64_data_uri), dann verbleibende kleinere
    #    Patterns. Sonst koennten innere Token-Patterns Teile des Wrapper-
    #    Inhalts matchen und Whitespace-Reste hinterlassen.
    new = ANTHROPIC_FUNCTION_CALLS_RE.sub("", out)
    new = ANTHROPIC_INVOKE_RE.sub("", new)
    new = GENERIC_TOOL_WRAPPER_RE.sub("", new)
    new = BASE64_DATA_URI_RE.sub("", new)
    new = LONG_BASE64_RE.sub("", new)
    new = TOOL_XML_RE.sub("", new)
    new = TOOL_CALL_FN_RE.sub("", new)
    new = TOOL_CALL_INLINE_RE.sub("", new)
    new = TOOL_JSON_RE.sub("", new)
    new = TOOL_CALL_KW_RE.sub("", new)
    new = TOOL_ARGS_YAML_RE.sub("", new)  # Phase-1-Erweiterung 2026-04-28
    # Audit F-AUDIT-4 (2026-04-29): prosaisch geschriebene Tool-Args
    # ("X with utterance is Y context_hints is Z action is ...") — nach
    # YAML-Pattern, weil Prose-Pattern strikter (greedy bis Satzende) ist
    # und sonst YAML-Block schon weg waere.
    new = TOOL_CALL_PROSE_RE.sub("", new)
    new = TOOL_ARGS_PROSE_RE.sub("", new)
    if new != out:
        actions.append("removed_tool_json")
        out = new

    # 3b. Web-search source artefacts — URLs, bare www-domains, and the
    #     "Weitere Ergebnisse von <domain>" / "more results from <domain>" SERP
    #     footer. The brain may read a raw search hit aloud instead of answering
    #     (live forensic 2026-06-28, Turn 4). The footer is cut BEFORE the bare
    #     www-domain so "von www.x.de" goes as one unit. Fail-closed defense; the
    #     real fix is the search_web answer_instruction that tells the brain to
    #     synthesize. Real spoken prose carries no http:// or www. token.
    new = URL_RE.sub("", out)
    new = MORE_RESULTS_RE.sub("", new)
    new = BARE_WWW_RE.sub("", new)
    if new != out:
        actions.append("removed_source_artifacts")
        out = new

    # 4. Self-Reference (ganze Klausel inkl. Satzpunkt entfernen)
    new = SELF_REF_RE.sub("", out)
    if new != out:
        actions.append("removed_self_reference")
        out = new

    # 4b. Background-action narration — the user never wants to HEAR that Jarvis
    # is noting/saving something or reviewing the last transcription/answer; it
    # happens silently in the background (maintainer mandate 2026-06-28). Cut the
    # whole narration clause (DE/EN/ES). Runs in both normal + ack mode.
    new = BACKGROUND_ACTION_RE.sub("", out)
    if new != out:
        actions.append("removed_background_action_narration")
        out = new

    # 5. Echo-Paraphrase NUR Opener (<=OPENER_BUDGET Zeichen).
    #    Mid-sentence Echo bleibt erhalten (Failure-Mode 6).
    head = out[:OPENER_BUDGET]
    tail = out[OPENER_BUDGET:]
    for pat in ECHO_PATTERNS:
        if pat.match(head):
            head = pat.sub("", head, count=1)
            actions.append("rephrased_echo")
            break
    out = head + tail

    # 6. Filler-Opener — skipped in ack_mode because Flash-Brain acks
    # are *meant* to look like contextual openers per persona spec
    # ("Lass mich kurz nachschauen.", "Let me check on that.").
    if not ack_mode:
        new = FILLER_OPENER_RE.sub("", out)
        if new != out:
            actions.append("removed_filler_opener")
            out = new

    # 7. Engineering-Jargon (Whitelist-Schutz via Bindestrich-Lookbehind)
    #    + Engineering-Compounds (Sub-Agent / Supervisor-Agent — Phase-1-
    #    Erweiterung 2026-04-28).
    new = OPENCLAW_RE.sub("", out)
    new = JARGON_RE.sub("", new)
    new = JARGON_COMPOUND_RE.sub("", new)
    if new != out:
        actions.append("removed_engineering_jargon")
        out = new

    # 7b. A1-Drift: "Sir"-Anrede entfernen, mit Quote-Schutz fuer Zitate.
    #     (Mandat A1 + Phase-1-Erweiterung 2026-04-28.)
    quote_spans: list[tuple[int, int]] = [
        m.span() for m in QUOTE_PROTECT_RE.finditer(out)
    ]

    def _outside_quotes(match: re.Match[str]) -> bool:
        ms, me = match.span()
        return not any(qs <= ms and me <= qe for qs, qe in quote_spans)

    sir_changed = False
    # Opener: "Sir, ..." -> "..."
    m = SIR_OPENER_RE.match(out)
    if m and _outside_quotes(m):
        out = out[m.end():]
        sir_changed = True
    # Tail/Mid: ", Sir" -> ""
    def _sub_sir_tail(m: re.Match[str]) -> str:
        return "" if _outside_quotes(m) else m.group(0)
    new = SIR_TAIL_RE.sub(_sub_sir_tail, out)
    if new != out:
        sir_changed = True
        out = new
    if sir_changed:
        actions.append("removed_anrede_drift")

    # 8. Whitespace normalisieren
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)

    # 9. Post-Scrub-Muell-Fallback: wenn nach allem Filtern weniger als
    #    MIN_MEANINGFUL_CHARS alphanumerische Zeichen uebrig sind UND der
    #    Filter ueberhaupt etwas gemacht hat (actions nicht leer), ist das
    #    ein Filter-Artefakt -> Standard-Phrase. Probe-Drift 12 vom 2026-04-28.
    if actions:
        meaningful = sum(1 for c in out if c.isalnum())
        if meaningful < MIN_MEANINGFUL_CHARS:
            fallback = FALLBACK_PHRASES.get(language, FALLBACK_PHRASES["de"])
            return ScrubResult(
                cleaned=fallback,
                actions=actions + ["replaced_with_fallback_residue"],
                fallback_used=True,
            )

    return ScrubResult(cleaned=out, actions=actions, fallback_used=False)


__all__ = [
    "ScrubResult",
    "scrub_for_voice",
    "WHITELIST_WORDS",
    "JARGON_WORDS",
    "OPENER_BUDGET",
    "FALLBACK_PHRASES",
]
