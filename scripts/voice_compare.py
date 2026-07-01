"""Voice persona acceptance test: 10 standard sentences for the subjective listening test.

After the persona refactor (2026-04-23) there are no automated tests for
the actual *voice quality* — that stays an ear-based judgment call by the
user. This script bundles ten representative scenarios, one each in
German and English, so they can be run through the pipeline one after
another.

Invocation pattern (manual):

    python -m scripts.voice_compare           # Lists all scenarios.
    python -m scripts.voice_compare --id 03   # A single scenario.

The output is plain text/YAML — no TTS call. The user plays the lines
through their voice pipeline (or reads them to the LLM) and rates them
against the patterns from `JARVIS_PERSONA.md` §RESPONSE ARCHITECTURE.
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
    pattern: str  # Which speech pattern should the response show?


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "01",
        "routine-status",
        "Ist die Datei gespeichert?",  # i18n-allow
        "Is the file saved?",
        "Fact first, no name (Pattern 1, 2)",
    ),
    Scenario(
        "02",
        "formal-greeting",
        "Guten Morgen.",  # i18n-allow
        "Good morning.",
        "Formal status update with name (Pattern 2)",
    ),
    Scenario(
        "03",
        "open-question",
        "Wie kann ich das beschleunigen?",  # i18n-allow
        "How can I speed this up?",
        "Shall-I question form instead of an open follow-up question (Pattern 3)",
    ),
    Scenario(
        "04",
        "risky-command",
        "Lösche alle Logs von gestern.",  # i18n-allow
        "Delete all logs from yesterday.",
        "One warning with fact, then execution (Pattern 4)",
    ),
    Scenario(
        "05",
        "reckless-action",
        "Ich starte jetzt den Deploy auf Prod ohne Tests.",  # i18n-allow
        "I'm deploying to prod without tests.",
        "Dry comment with an anchor (Pattern 5)",
    ),
    Scenario(
        "06",
        "proactive-context",
        "Wie warm ist es draußen?",  # i18n-allow
        "What's the temperature outside?",
        "Fact + ONE additional sentence (Pattern 6)",
    ),
    Scenario(
        "07",
        "long-task-start",
        "Analysiere das gesamte Projektverzeichnis.",  # i18n-allow
        "Analyse the entire project directory.",
        "Initiative announcement in 3 parts (Pattern 7)",
    ),
    Scenario(
        "08",
        "bad-news",
        "Hat der Build funktioniert?",  # i18n-allow
        "Did the build succeed?",
        "Bad news without padding (Pattern 8)",
    ),
    Scenario(
        "09",
        "high-pressure",
        "Schnell, die Präsentation beginnt gleich!",  # i18n-allow
        "Quick, the presentation starts now!",
        "Shorter under pressure, register doesn't break (Pattern 9)",
    ),
    Scenario(
        "10",
        "hangup",
        "Das war's, danke.",  # i18n-allow
        "That's all, thanks.",
        "Exact hangup contract: „Auf Wiedersehen, Ruben.\"",  # i18n-allow
    ),
)


def _render_all() -> str:
    lines = [
        "# Voice compare — 10 scenarios for subjective acceptance testing",
        "# After the persona refactor on 2026-04-23.",
        "# Expectation per scenario, see `pattern`. Source: `jarvis/brain/JARVIS_PERSONA.md`.",
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
    raise SystemExit(f"Unknown scenario ID: {scenario_id!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Voice persona acceptance test (10 standard sentences).",
    )
    parser.add_argument(
        "--id",
        help="Print a single scenario (e.g. '03'). Default: all.",
    )
    args = parser.parse_args(argv)
    if args.id:
        sys.stdout.write(_render_one(args.id))
    else:
        sys.stdout.write(_render_all())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
