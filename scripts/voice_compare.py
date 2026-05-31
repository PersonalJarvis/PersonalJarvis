"""Voice-Persona-Abnahme: 10 Standard-Sätze für den subjektiven Hör-Test.

Nach dem Persona-Refactor (2026-04-23) gibt es keine automatisierten Tests
für die eigentliche *Voice-Qualität* — das bleibt eine Ohr-Entscheidung des
Users. Dieses Skript bündelt zehn repräsentative Szenarien, je einmal in
Deutsch und Englisch, so dass man sie der Reihe nach durch die Pipeline
schicken kann.

Aufrufmuster (manuell):

    python -m scripts.voice_compare           # Listet alle Szenarien.
    python -m scripts.voice_compare --id 03   # Einzelnes Szenario.

Die Ausgabe ist reines Text/YAML — kein TTS-Call. Der User spielt die Zeilen
in seiner Voice-Pipeline (oder liest sie vor dem LLM) und bewertet nach
den Mustern aus `JARVIS_PERSONA.md` §RESPONSE ARCHITECTURE.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    id: str
    tag: str
    user_de: str
    user_en: str
    pattern: str  # Welches Sprechmuster sollte die Antwort zeigen?


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "01",
        "routine-status",
        "Ist die Datei gespeichert?",
        "Is the file saved?",
        "Fakt zuerst, kein Name (Pattern 1, 2)",
    ),
    Scenario(
        "02",
        "formal-greeting",
        "Guten Morgen.",
        "Good morning.",
        "Formelle Zustandsmeldung mit Name (Pattern 2)",
    ),
    Scenario(
        "03",
        "open-question",
        "Wie kann ich das beschleunigen?",
        "How can I speed this up?",
        "Shall-I-Frageform statt offener Rückfrage (Pattern 3)",
    ),
    Scenario(
        "04",
        "risky-command",
        "Lösche alle Logs von gestern.",
        "Delete all logs from yesterday.",
        "One-Warning mit Fakt, dann Ausführung (Pattern 4)",
    ),
    Scenario(
        "05",
        "reckless-action",
        "Ich starte jetzt den Deploy auf Prod ohne Tests.",
        "I'm deploying to prod without tests.",
        "Trockener Kommentar mit Anker (Pattern 5)",
    ),
    Scenario(
        "06",
        "proactive-context",
        "Wie warm ist es draußen?",
        "What's the temperature outside?",
        "Fakt + EIN Zusatzsatz (Pattern 6)",
    ),
    Scenario(
        "07",
        "long-task-start",
        "Analysiere das gesamte Projektverzeichnis.",
        "Analyse the entire project directory.",
        "Initiative-Ankündigung in 3 Teilen (Pattern 7)",
    ),
    Scenario(
        "08",
        "bad-news",
        "Hat der Build funktioniert?",
        "Did the build succeed?",
        "Bad news ohne Polsterung (Pattern 8)",
    ),
    Scenario(
        "09",
        "high-pressure",
        "Schnell, die Präsentation beginnt gleich!",
        "Quick, the presentation starts now!",
        "Kürzer unter Druck, Register bricht nicht (Pattern 9)",
    ),
    Scenario(
        "10",
        "hangup",
        "Das war's, danke.",
        "That's all, thanks.",
        "Exakt der Hangup-Contract: „Auf Wiedersehen, Alex.\"",
    ),
)


def _render_all() -> str:
    lines = [
        "# Voice-Compare — 10 Szenarien für subjektive Abnahme",
        "# Nach dem Persona-Refactor 2026-04-23.",
        "# Erwartung pro Szenario siehe `pattern`. Quelle: `jarvis/brain/JARVIS_PERSONA.md`.",
        "",
    ]
    for s in SCENARIOS:
        lines.extend(
            [
                f"- id: {s.id}",
                f"  tag: {s.tag}",
                f"  user_de: {s.user_de!r}",
                f"  user_en: {s.user_en!r}",
                f"  pattern: {s.pattern!r}",
                "",
            ]
        )
    return "\n".join(lines)


def _render_one(scenario_id: str) -> str:
    for s in SCENARIOS:
        if s.id == scenario_id:
            return (
                f"id: {s.id}\n"
                f"tag: {s.tag}\n"
                f"pattern: {s.pattern}\n"
                f"de: {s.user_de}\n"
                f"en: {s.user_en}\n"
            )
    raise SystemExit(f"Unbekannte Szenario-ID: {scenario_id!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Voice-Persona-Abnahme (10 Standard-Sätze).",
    )
    parser.add_argument(
        "--id",
        help="Einzelnes Szenario ausgeben (z.B. '03'). Default: alle.",
    )
    args = parser.parse_args(argv)
    if args.id:
        sys.stdout.write(_render_one(args.id))
    else:
        sys.stdout.write(_render_all())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
