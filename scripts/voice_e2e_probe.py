"""Text E2E acceptance test for the persona refactor.

Calls the BrainManager in the router tier with the probe scenarios, collects
the responses, and checks simple heuristics against the expected
speech patterns. No TTS, no mic — pure text path.

Setup:
- Persona mandate phase 1: the output filter (``scrub_for_voice``) sits on
  the TTS path — not active here because the script never goes through the
  TTS path. Filter tests live separately in
  ``tests/unit/brain/test_output_filter.py``.
- Persona mandate phase 2: ANTI_PATTERNS extended with echo/hedging/filler
  strings; scenarios 11-13 cover the echo trap, tool-output leak, and
  self-reference trap.
- Persona mandate phase 3: ``build_default_brain(tier="router")`` activates
  the pure dispatcher including the ROUTER DISCIPLINE prompt and the
  deterministic force-spawn heuristic.

Run:
    python scripts/voice_e2e_probe.py

ENV:
- ``JARVIS_PROBE_LANG=de|en|both``  Default: both. Controls whether both
  language variants are tested (where defined).
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Add repo root to sys.path in case the script runs outside ``cd <repo>``.
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# The persona loader is missing on the ``router-permanent-vision`` branch —
# import it defensively so the script still runs. It would be present on ``main``.
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
    """A probe scenario, optionally bilingual.

    ``user_en`` is optional — if ``None``, only ``user_de`` is run.
    Otherwise both languages, if ``JARVIS_PROBE_LANG`` allows it.
    """
    id: str
    tag: str
    user_de: str
    pattern: str
    user_en: str | None = None


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("01", "routine-status",
             user_de="Ist die Datei gespeichert?",  # i18n-allow
             pattern="Fact first, no name (Pattern 1, 2)"),
    Scenario("02", "formal-greeting",
             user_de="Guten Morgen.",  # i18n-allow
             pattern="Formal status update with name (Pattern 2)"),
    Scenario("03", "open-question",
             user_de="Wie kann ich das beschleunigen?",  # i18n-allow
             pattern="Shall-I question form instead of an open follow-up question (Pattern 3)"),
    Scenario("04", "risky-command",
             user_de="Loesche alle Logs von gestern.",  # i18n-allow
             pattern="One warning with fact, then execution (Pattern 4)"),
    Scenario("05", "reckless-action",
             user_de="Ich starte jetzt den Deploy auf Prod ohne Tests.",  # i18n-allow
             pattern="Dry comment with an anchor (Pattern 5)"),
    Scenario("06", "proactive-context",
             user_de="Wie warm ist es draussen?",  # i18n-allow
             pattern="Fact + ONE additional sentence (Pattern 6)"),
    Scenario("07", "long-task-start",
             user_de="Analysiere das gesamte Projektverzeichnis.",  # i18n-allow
             pattern="Initiative announcement in 3 parts (Pattern 7)"),
    Scenario("08", "bad-news",
             user_de="Hat der Build funktioniert?",  # i18n-allow
             pattern="Bad news without padding (Pattern 8)"),
    Scenario("09", "high-pressure",
             user_de="Schnell, die Praesentation beginnt gleich!",  # i18n-allow
             pattern="Shorter under pressure, register doesn't break (Pattern 9)"),
    Scenario("10", "hangup",
             user_de="Das war's, danke.",  # i18n-allow
             pattern="Exact hangup contract"),
    # Persona mandate phase 2 — three new scenarios for the echo trap,
    # tool-output leak, and self-reference trap, each bilingual.
    Scenario("11", "echo-trap",
             user_de="Ich möchte wissen, wie spät es ist.",  # i18n-allow
             user_en="I want to know what time it is.",
             pattern="Direct time answer, NO 'So you'd like to know...'"),
    Scenario("12", "tool-spawn-output-leak",
             user_de="Lies die Datei jarvis.toml und sag mir was drin steht.",  # i18n-allow
             user_en="Read the file jarvis.toml and tell me what's inside.",
             pattern="Content summarized, no tool args, no dispatch_to_harness JSON"),
    Scenario("13", "self-reference-trap",
             user_de="Was bist du eigentlich?",  # i18n-allow
             user_en="What are you actually?",
             pattern="Butler identity, NO 'I am a language model'"),
)


# Anti-patterns — matched case-insensitively against every Brain response.
# Each occurrence counts as DRIFT. Extended in persona mandate phase 2 with
# echo paraphrase, hedging, filler self-reference, padding.
ANTI_PATTERNS = [
    # Classic (phase 0)
    "grossartige frage", "tolle frage",  # i18n-allow
    "als ki", "als sprachmodell",  # i18n-allow
    "ich hoffe, das hilft",  # i18n-allow
    # Echo paraphrase (phase 2)
    "du möchtest also", "ich verstehe, dass",  # i18n-allow
    "if i understand correctly", "you'd like me to",
    # Hedging (phase 2)
    "ich glaube", "vermutlich", "möglicherweise",  # i18n-allow
    "i think", "perhaps", "i believe",
    # Filler self-reference (phase 2)
    "lass mich kurz", "let me think",  # i18n-allow
    # Padding (phase 2)
    "es tut mir leid, aber", "i'm so sorry to say",  # i18n-allow
]


async def probe() -> int:
    from jarvis.brain.manager import BrainManager
    from jarvis.brain.output_filter import scrub_for_voice
    from jarvis.core.bus import EventBus
    from jarvis.core.config import load_config

    print(f"Persona loader present: {HAS_PERSONA_LOADER}")
    invalidate_cache()
    persona = load_persona_prompt()
    print(f"Persona block loaded: {len(persona)} chars")

    cfg = load_config(Path("jarvis.toml"))
    primary = cfg.brain.primary
    primary_provider = cfg.brain.providers.get(primary)
    primary_model = primary_provider.model if primary_provider else "?"
    print(f"Primary brain: {primary} / {primary_model}")

    bus = EventBus()
    # Preferred: build_default_brain(tier='router') — full voice path with
    # tools + force-spawn heuristic. Fallback: direct BrainManager with the
    # router prompt — used when tool loading fails (e.g. because
    # ``jarvis.clis.risk_integration`` is missing on the current branch).
    bm: BrainManager | None = None
    try:
        from jarvis.brain.factory import build_default_brain
        bm = build_default_brain(tier="router", bus=bus)
        print("Brain setup: build_default_brain(tier='router') ✓")
    except Exception as exc:  # noqa: BLE001
        print(f"Brain setup fallback: factory failed ({type(exc).__name__}: {exc})")
        bm = BrainManager(config=cfg, bus=bus, tools={}, tool_executor=None)
        try:
            from jarvis.brain.router import SYSTEM_PROMPT as ROUTER_SYSTEM_PROMPT
            bm._system_prompt_extra = ROUTER_SYSTEM_PROMPT  # type: ignore[attr-defined]
            print("Brain setup: direct BrainManager + ROUTER SYSTEM_PROMPT manually ✓")
        except Exception as exc2:  # noqa: BLE001
            print(f"Router prompt injection failed: {exc2}")

    prompt = ""
    try:
        prompt = bm._build_system_prompt()  # type: ignore[attr-defined]
    except AttributeError:
        prompt = getattr(bm, "_system_prompt_extra", "")
    print(f"System prompt: {len(prompt)} chars")
    print(f"  Contains 'ROUTER DISCIPLINE': {'ROUTER DISCIPLINE' in prompt}")
    print(f"  Contains 'ECHO-PARAPHRASE': {'ECHO-PARAPHRASE' in prompt}")
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
            # The phase-1 filter in the probe mirrors what the user
            # actually hears: Brain output -> scrub_for_voice -> TTS.
            scrubbed = scrub_for_voice(raw_response, language=lang)
            response = scrubbed.cleaned
            if scrubbed.actions:
                print(f"Filter:   {scrubbed.actions} (fallback={scrubbed.fallback_used})")
            print(f"Jarvis:   {response}")
            print(f"Expected: {s.pattern}")
            print()
            results.append((s.id, lang, s.tag, user_text, response, s.pattern))

    print("=" * 60)
    print("HEURISTIC CHECKS")
    print("=" * 60)

    total = len(results)
    if total == 0:
        print("No scenarios were run.")
        return 0

    with_name = sum(1 for r in results if "Alex" in r[4])
    name_ratio = with_name / total
    print(f"Name frequency: {with_name}/{total} ({name_ratio:.0%}) — target <= 33%.")
    print(f"  Result: {'OK' if name_ratio <= 0.34 else 'DRIFT'}")

    too_long = [r[0] for r in results if len(r[4]) > 220]
    print(f"Responses > 220 chars: {len(too_long)} {too_long}")

    anti_hits = [
        (r[0], r[1], pat) for r in results
        for pat in ANTI_PATTERNS if pat in r[4].lower()
    ]
    print(f"Anti-pattern hits: {len(anti_hits)} {anti_hits}")

    sie_hits = [
        (r[0], r[1]) for r in results
        if any(tok in r[4] for tok in (" Sie ", " Ihnen ", " Ihre ", "Sie möchten"))  # i18n-allow
    ]
    print(f"Formal 'Sie' occurrences: {len(sie_hits)} {sie_hits}")

    filler_as_opener = [
        (r[0], r[1]) for r in results
        if r[4].strip().startswith((
            "Einen Moment, Alex", "Einen Augenblick", "Ich schaue gleich nach",  # i18n-allow
        )) and r[0] not in ("06",)
    ]
    print(f"Filler-as-opener (non-tool scenarios): {len(filler_as_opener)} {filler_as_opener}")

    # Hangup contract: scenario 10 must return exactly one of the two phrases.
    hangup_de_runs = [r for r in results if r[0] == "10" and r[1] == "de"]
    if hangup_de_runs:
        hangup = hangup_de_runs[0][4]
        hangup_ok = "auf wiedersehen, alex" in hangup.lower()  # i18n-allow
        print(f"Hangup contract DE (scenario 10): {'OK' if hangup_ok else 'MISS'}")
    hangup_en_runs = [r for r in results if r[0] == "10" and r[1] == "en"]
    if hangup_en_runs:
        hangup_en = hangup_en_runs[0][4]
        hangup_en_ok = "goodbye, alex" in hangup_en.lower()
        print(f"Hangup contract EN (scenario 10): {'OK' if hangup_en_ok else 'MISS'}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(probe()))
