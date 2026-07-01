"""Voice style eval — proves Jarvis speaks in full, natural sentences.

Regression context (2026-07-01): before commit 5bbf7ad2 (2026-06-29) the voice
persona in ``JARVIS_PERSONA.md`` told the brain to "Speak in one or two short,
complete sentences. Never paragraphs." plus "Smalltalk or simple factual
requests: one or two sentences, done." Those clipped-length rules made spoken
replies choppy ("telegram style"). The consolidation commit replaced them with
"Speak in complete, grammatical sentences." + "Two to four flowing sentences is
the sweet spot" + "finishing the sentence always wins over saving a word".

This script is the acceptance harness for that fix. It drives the SAME model
call path the running app uses for the spoken answer — the router-tier brain
(``build_default_brain(tier="router")``), which per ``jarvis.toml`` is the main
voice brain on ``gemini-3.5-flash`` — with a set of realistic conversational
utterances, then runs deterministic style checks on the produced answer.

Checks (all must pass for the done-gate):
  1. no em/en dash or spaced hyphen used as punctuation (–, —, " - ", " -- ")
  2. no markdown (*, _, #, `, bullet lines)
  3. no digits (numbers / times / units must be spelled out)
  4. not built from more than two fragments under four words (anti-telegram)
  5. does not end on a counter-question unless the scenario genuinely lacks info

Both the RAW brain output (what the persona actually produced) and the
``scrub_for_voice`` result (what TTS speaks) are checked. RAW is the honest
style signal: the scrubber removes dashes/markdown but cannot turn fragments
into sentences or spell out a number, so a clean RAW output is the real target.

Usage:
    python scripts/voice_style_eval.py
    python scripts/voice_style_eval.py --raw     # show full raw responses

Exit code is the number of failing checks (0 = green done-gate).
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Windows defaults stdout to cp1252; a redirect then mangles umlauts and dashes
# into unreadable bytes (BUG-class "UTF-8 stdout"). Force UTF-8 so the printed
# output — and any dash/umlaut in it — is faithful.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


@dataclass(frozen=True)
class Scenario:
    """One conversational probe. ``needs_clarification`` allows a trailing '?'."""

    id: str
    tag: str
    user: str
    needs_clarification: bool = False


# Realistic, conversational turns that SHOULD get a full-sentence spoken answer
# and do NOT depend on a tool (so the style, not a tool round-trip, is measured).
SCENARIOS: tuple[Scenario, ...] = (
    Scenario("01", "greeting", "Guten Morgen, wie geht es dir?"),
    Scenario("02", "smalltalk", "Ich bin heute echt ein bisschen gestresst."),
    Scenario("03", "opinion-80s", "Was hältst du eigentlich von Musik aus den Achtzigern?"),
    Scenario("04", "explain", "Erklär mir mal, warum der Himmel blau ist."),
    Scenario("05", "advice", "Hast du einen Tipp, wie ich morgens besser in die Gänge komme?"),
    Scenario("06", "knowledge", "Erzähl mir was Interessantes über den Mond."),
    Scenario("07", "compare", "Was ist der Unterschied zwischen Nebel und Wolken?"),
    Scenario("08", "chitchat", "Puh, war ein langer Tag. Womit kennst du dich eigentlich gut aus?"),
)


# --- deterministic style checks --------------------------------------------

DASH_RE = re.compile(r"[–—]|(?<=\s)-{1,2}(?=\s)")
MARKDOWN_RE = re.compile(r"(\*\*|\*|__|_|^#{1,6}\s|`|^\s*[-*]\s+)", re.MULTILINE)
DIGIT_RE = re.compile(r"\d")
SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _short_fragments(text: str) -> list[str]:
    """Sentence-like fragments that carry fewer than four words."""
    pieces = [p.strip() for p in SENTENCE_SPLIT_RE.split(text) if p.strip()]
    return [p for p in pieces if len(WORD_RE.findall(p)) < 4]


def _ends_on_question(text: str) -> bool:
    stripped = text.rstrip().rstrip('"').rstrip()
    return stripped.endswith("?")


@dataclass
class CheckResult:
    dash: bool = True
    markdown: bool = True
    digits: bool = True
    fragments: bool = True
    counter_question: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(
            (self.dash, self.markdown, self.digits, self.fragments, self.counter_question)
        )


def check(text: str, scenario: Scenario) -> CheckResult:
    r = CheckResult()
    if DASH_RE.search(text):
        r.dash = False
        r.notes.append(f"dash: {DASH_RE.findall(text)!r}")
    if MARKDOWN_RE.search(text):
        r.markdown = False
        r.notes.append("markdown token present")
    if DIGIT_RE.search(text):
        r.digits = False
        r.notes.append(f"digits: {DIGIT_RE.findall(text)!r}")
    frags = _short_fragments(text)
    if len(frags) > 2:
        r.fragments = False
        r.notes.append(f"{len(frags)} short fragments: {frags!r}")
    if _ends_on_question(text) and not scenario.needs_clarification:
        r.counter_question = False
        r.notes.append("ends on counter-question")
    return r


async def run() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", action="store_true", help="print full raw responses")
    ap.add_argument(
        "--persona-file",
        default=None,
        help="override the persona with this file's text (for before/after baselines)",
    )
    args = ap.parse_args()

    from jarvis.brain import manager as _manager
    from jarvis.brain.factory import build_default_brain
    from jarvis.brain.output_filter import scrub_for_voice
    from jarvis.brain.persona_loader import invalidate_cache, load_effective_persona_prompt
    from jarvis.core.bus import EventBus
    from jarvis.core.config import load_config

    cfg = load_config(REPO / "jarvis.toml")
    invalidate_cache()

    if args.persona_file:
        from jarvis.brain.persona_loader import _extract_fence_after_marker

        file_text = Path(args.persona_file).read_text(encoding="utf-8")  # noqa: ASYNC240 — one-shot script setup read
        # A full JARVIS_PERSONA.md carries the prompt inside the fence after the
        # "## System-Prompt" marker; a bare prompt file is used verbatim.
        override_text = _extract_fence_after_marker(file_text) or file_text
        _manager.load_effective_persona_prompt = lambda: override_text  # type: ignore[assignment]
        persona = override_text
        print(f"persona OVERRIDE from {args.persona_file}: {len(persona)} chars")
    else:
        persona = load_effective_persona_prompt()
        print(f"persona block: {len(persona)} chars")
    print(f"primary brain: {cfg.brain.primary} | routing: "
          f"{getattr(cfg.brain, 'routing_provider', '?')}/"
          f"{getattr(cfg.brain, 'routing_model', '?')}")

    bus = EventBus()
    bm = build_default_brain(tier="router", bus=bus)
    print("brain build: OK\n")
    print("=" * 70)

    check_names = ("dash", "markdown", "digits", "fragments", "counter_question")
    # Gate = the DELIVERED (scrubbed) text — what TTS actually speaks and the
    # user actually hears; scrub_for_voice is part of the real app path.
    fail_counts = dict.fromkeys(check_names, 0)
    raw_drift = dict.fromkeys(check_names, 0)  # secondary: model-level drift

    for s in SCENARIOS:
        try:
            if hasattr(bm, "_history"):
                bm._history.clear()  # fresh session each scenario
            raw = await asyncio.wait_for(bm(s.user), timeout=90)
        except Exception as exc:  # noqa: BLE001
            raw = f"<ERROR: {type(exc).__name__}: {exc}>"
        scrubbed = scrub_for_voice(raw, language="de").cleaned

        r_deliver = check(scrubbed, s)   # gate
        r_raw = check(raw, s)            # model drift signal
        for name in check_names:
            if not getattr(r_deliver, name):
                fail_counts[name] += 1
            if not getattr(r_raw, name):
                raw_drift[name] += 1

        verdict = "GREEN" if r_deliver.all_ok else "RED"
        print(f"--- {s.id} [{s.tag}] {verdict} ---")
        print(f"User:   {s.user}")
        print(f"Jarvis: {scrubbed if not args.raw else raw}")
        if not r_deliver.all_ok:
            print(f"  DELIVERED issues: {r_deliver.notes}")
        raw_only = [n for n in r_raw.notes if n not in r_deliver.notes]
        if raw_only:
            print(f"  (raw-only drift, scrubbed away): {raw_only}")
        print()

    print("=" * 70)
    print("STYLE-CHECK SUMMARY (failures per criterion, over DELIVERED output)")
    total_fail = 0
    for name in check_names:
        n = fail_counts[name]
        total_fail += n
        status = "OK" if n == 0 else "FAIL"
        drift = f"  (raw model drift: {raw_drift[name]})" if raw_drift[name] else ""
        print(f"  {name:16s}: {n} failing scenario(s)  [{status}]{drift}")
    green = total_fail == 0
    print(f"\nDONE-GATE: {'GREEN — all checks pass' if green else f'RED — {total_fail} failures'}")
    return total_fail


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
