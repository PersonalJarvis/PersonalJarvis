"""Text-E2E-Abnahme des Persona-Refactors.

Ruft den BrainManager im Router-Tier mit den Probe-Szenarien auf, sammelt
die Responses und prueft einfache Heuristiken gegen die erwarteten
Sprechmuster. Kein TTS, kein Mic — reiner Text-Pfad.

Setup:
- Persona-Mandat Phase 1: Output-Filter (``scrub_for_voice``) am TTS-Pfad —
  hier nicht aktiv, weil das Skript den TTS-Pfad nicht durchlaeuft. Filter-
  Tests sind separat in ``tests/unit/brain/test_output_filter.py``.
- Persona-Mandat Phase 2: ANTI_PATTERNS um Echo-/Hedging-/Filler-Strings
  erweitert; Szenarien 11-13 fuer Echo-Trap, Tool-Output-Leak und
  Self-Reference-Trap.
- Persona-Mandat Phase 3: ``build_default_brain(tier="router")`` aktiviert
  den Pure-Dispatcher inkl. ROUTER DISCIPLINE-Prompt und der
  deterministischen Force-Spawn-Heuristik.

Aufruf:
    python scripts/voice_e2e_probe.py

ENV:
- ``JARVIS_PROBE_LANG=de|en|both``  Default: both. Steuert ob beide
  Sprach-Varianten getestet werden (sofern definiert).
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Repo-Root auf sys.path falls das Skript ausserhalb von ``cd <repo>`` laeuft.
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Persona-Loader fehlt auf Branch ``router-permanent-vision`` — defensiv
# importieren, damit das Skript trotzdem laeuft. Auf ``main`` waere er da.
try:
    from jarvis.brain.persona_loader import invalidate_cache, load_persona_prompt  # type: ignore
    HAS_PERSONA_LOADER = True
except ModuleNotFoundError:
    HAS_PERSONA_LOADER = False

    def invalidate_cache() -> None:  # type: ignore
        pass

    def load_persona_prompt() -> str:  # type: ignore
        return ""


@dataclass(frozen=True)
class Scenario:
    """Ein Probe-Szenario, optional bilingual.

    ``user_en`` ist optional — wenn ``None``, wird nur ``user_de`` gefahren.
    Sonst beide Sprachen, wenn ``JARVIS_PROBE_LANG`` das zulaesst.
    """
    id: str
    tag: str
    user_de: str
    pattern: str
    user_en: str | None = None


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("01", "routine-status",
             user_de="Ist die Datei gespeichert?",
             pattern="Fakt zuerst, kein Name (Pattern 1, 2)"),
    Scenario("02", "formal-greeting",
             user_de="Guten Morgen.",
             pattern="Formelle Zustandsmeldung mit Name (Pattern 2)"),
    Scenario("03", "open-question",
             user_de="Wie kann ich das beschleunigen?",
             pattern="Shall-I-Frageform statt offener Rueckfrage (Pattern 3)"),
    Scenario("04", "risky-command",
             user_de="Loesche alle Logs von gestern.",
             pattern="One-Warning mit Fakt, dann Ausfuehrung (Pattern 4)"),
    Scenario("05", "reckless-action",
             user_de="Ich starte jetzt den Deploy auf Prod ohne Tests.",
             pattern="Trockener Kommentar mit Anker (Pattern 5)"),
    Scenario("06", "proactive-context",
             user_de="Wie warm ist es draussen?",
             pattern="Fakt + EIN Zusatzsatz (Pattern 6)"),
    Scenario("07", "long-task-start",
             user_de="Analysiere das gesamte Projektverzeichnis.",
             pattern="Initiative-Ankuendigung in 3 Teilen (Pattern 7)"),
    Scenario("08", "bad-news",
             user_de="Hat der Build funktioniert?",
             pattern="Bad news ohne Polsterung (Pattern 8)"),
    Scenario("09", "high-pressure",
             user_de="Schnell, die Praesentation beginnt gleich!",
             pattern="Kuerzer unter Druck, Register bricht nicht (Pattern 9)"),
    Scenario("10", "hangup",
             user_de="Das war's, danke.",
             pattern="Exakt der Hangup-Contract"),
    # Persona-Mandat Phase 2 — drei neue Szenarien fuer Echo-Trap,
    # Tool-Output-Leak und Self-Reference-Trap, jeweils bilingual.
    Scenario("11", "echo-trap",
             user_de="Ich möchte wissen, wie spät es ist.",
             user_en="I want to know what time it is.",
             pattern="Direkte Zeit-Antwort, KEIN 'Du möchtest also wissen...'"),
    Scenario("12", "tool-spawn-output-leak",
             user_de="Lies die Datei jarvis.toml und sag mir was drin steht.",
             user_en="Read the file jarvis.toml and tell me what's inside.",
             pattern="Inhalt zusammengefasst, keine Tool-Args, kein dispatch_to_harness JSON"),
    Scenario("13", "self-reference-trap",
             user_de="Was bist du eigentlich?",
             user_en="What are you actually?",
             pattern="Butler-Identitaet, KEIN 'Ich bin ein Sprachmodell'"),
)


# Anti-Patterns — werden case-insensitiv gegen jede Brain-Response gematcht.
# Jedes Vorkommen zaehlt als DRIFT. Erweitert in Persona-Mandat Phase 2 um
# Echo-Paraphrase, Hedging, Filler-Selbstreferenz, Polster.
ANTI_PATTERNS = [
    # Klassisch (Phase 0)
    "grossartige frage", "tolle frage",
    "als ki", "als sprachmodell",
    "ich hoffe, das hilft",
    # Echo-Paraphrase (Phase 2)
    "du möchtest also", "ich verstehe, dass",
    "if i understand correctly", "you'd like me to",
    # Hedging (Phase 2)
    "ich glaube", "vermutlich", "möglicherweise",
    "i think", "perhaps", "i believe",
    # Filler-Selbstreferenz (Phase 2)
    "lass mich kurz", "let me think",
    # Polster (Phase 2)
    "es tut mir leid, aber", "i'm so sorry to say",
]


async def probe() -> int:
    from jarvis.brain.manager import BrainManager
    from jarvis.brain.output_filter import scrub_for_voice
    from jarvis.core.bus import EventBus
    from jarvis.core.config import load_config

    print(f"Persona-Loader vorhanden: {HAS_PERSONA_LOADER}")
    invalidate_cache()
    persona = load_persona_prompt()
    print(f"Persona-Block geladen: {len(persona)} chars")

    cfg = load_config(Path("jarvis.toml"))
    primary = cfg.brain.primary
    primary_provider = cfg.brain.providers.get(primary)
    primary_model = primary_provider.model if primary_provider else "?"
    print(f"Primary Brain: {primary} / {primary_model}")

    bus = EventBus()
    # Bevorzugt: build_default_brain(tier='router') — voller Voice-Pfad mit
    # Tools + Force-Spawn-Heuristik. Fallback: direkter BrainManager mit
    # Router-Prompt — wenn das Tool-Loading scheitert (z.B. wegen fehlendem
    # ``jarvis.clis.risk_integration`` auf der aktuellen Branch).
    bm: BrainManager | None = None
    try:
        from jarvis.brain.factory import build_default_brain
        bm = build_default_brain(tier="router", bus=bus)
        print("Brain-Setup: build_default_brain(tier='router') ✓")
    except Exception as exc:  # noqa: BLE001
        print(f"Brain-Setup-Fallback: factory failed ({type(exc).__name__}: {exc})")
        bm = BrainManager(config=cfg, bus=bus, tools={}, tool_executor=None)
        try:
            from jarvis.brain.router import SYSTEM_PROMPT as ROUTER_SYSTEM_PROMPT
            bm._system_prompt_extra = ROUTER_SYSTEM_PROMPT  # type: ignore[attr-defined]
            print("Brain-Setup: direkter BrainManager + ROUTER SYSTEM_PROMPT manuell ✓")
        except Exception as exc2:  # noqa: BLE001
            print(f"Router-Prompt-Inject fehlgeschlagen: {exc2}")

    prompt = ""
    try:
        prompt = bm._build_system_prompt()  # type: ignore[attr-defined]
    except AttributeError:
        prompt = getattr(bm, "_system_prompt_extra", "")
    print(f"System-Prompt: {len(prompt)} chars")
    print(f"  Enthaelt 'ROUTER DISCIPLINE': {'ROUTER DISCIPLINE' in prompt}")
    print(f"  Enthaelt 'ECHO-PARAPHRASE': {'ECHO-PARAPHRASE' in prompt}")
    print()

    lang_mode = (os.environ.get("JARVIS_PROBE_LANG") or "both").lower()
    if lang_mode not in ("de", "en", "both"):
        lang_mode = "both"

    # Tuple: (id, lang, tag, user_text, response_text, pattern)
    results: list[tuple[str, str, str, str, str, str]] = []

    for s in SCENARIOS:
        runs: list[tuple[str, str]] = []  # (lang, user_text)
        if lang_mode in ("de", "both"):
            runs.append(("de", s.user_de))
        if (lang_mode in ("en", "both")) and s.user_en:
            runs.append(("en", s.user_en))

        for lang, user_text in runs:
            print(f"--- {s.id} [{lang}] {s.tag} ---")
            print(f"User:     {user_text}")
            try:
                if hasattr(bm, "_history"):
                    bm._history.clear()  # type: ignore[attr-defined]
                raw_response = await bm(user_text)
            except Exception as exc:  # noqa: BLE001
                raw_response = f"<ERROR: {type(exc).__name__}: {exc}>"
            # Phase-1-Filter im Probe spiegelt das wider, was der User
            # tatsaechlich hoert: Brain-Output -> scrub_for_voice -> TTS.
            scrubbed = scrub_for_voice(raw_response, language=lang)
            response = scrubbed.cleaned
            if scrubbed.actions:
                print(f"Filter:   {scrubbed.actions} (fallback={scrubbed.fallback_used})")
            print(f"Jarvis:   {response}")
            print(f"Erwartet: {s.pattern}")
            print()
            results.append((s.id, lang, s.tag, user_text, response, s.pattern))

    print("=" * 60)
    print("HEURISTIK-CHECKS")
    print("=" * 60)

    total = len(results)
    if total == 0:
        print("Keine Szenarien ausgefuehrt.")
        return 0

    with_name = sum(1 for r in results if "Alex" in r[4])
    name_ratio = with_name / total
    print(f"Name-Frequenz: {with_name}/{total} ({name_ratio:.0%}) — Ziel <= 33 %.")
    print(f"  Ergebnis: {'OK' if name_ratio <= 0.34 else 'DRIFT'}")

    too_long = [r[0] for r in results if len(r[4]) > 220]
    print(f"Responses > 220 chars: {len(too_long)} {too_long}")

    anti_hits = [
        (r[0], r[1], pat) for r in results
        for pat in ANTI_PATTERNS if pat in r[4].lower()
    ]
    print(f"Anti-Pattern-Treffer: {len(anti_hits)} {anti_hits}")

    sie_hits = [
        (r[0], r[1]) for r in results
        if any(tok in r[4] for tok in (" Sie ", " Ihnen ", " Ihre ", "Sie möchten"))
    ]
    print(f"Siezen-Vorkommen: {len(sie_hits)} {sie_hits}")

    filler_as_opener = [
        (r[0], r[1]) for r in results
        if r[4].strip().startswith((
            "Einen Moment, Alex", "Einen Augenblick", "Ich schaue gleich nach",
        )) and r[0] not in ("06",)
    ]
    print(f"Filler-als-Opener (nicht-Tool-Scenarios): {len(filler_as_opener)} {filler_as_opener}")

    # Hangup-Contract: Szenario 10 muss exakt eine der beiden Phrasen liefern.
    hangup_de_runs = [r for r in results if r[0] == "10" and r[1] == "de"]
    if hangup_de_runs:
        hangup = hangup_de_runs[0][4]
        hangup_ok = "auf wiedersehen, alex" in hangup.lower()
        print(f"Hangup-Contract DE (Szenario 10): {'OK' if hangup_ok else 'MISS'}")
    hangup_en_runs = [r for r in results if r[0] == "10" and r[1] == "en"]
    if hangup_en_runs:
        hangup_en = hangup_en_runs[0][4]
        hangup_en_ok = "goodbye, alex" in hangup_en.lower()
        print(f"Hangup-Contract EN (Szenario 10): {'OK' if hangup_en_ok else 'MISS'}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(probe()))
