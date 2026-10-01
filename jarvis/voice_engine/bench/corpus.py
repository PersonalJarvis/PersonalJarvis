"""The bench's German and English test material and the tool-call checks."""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

CORPUS_DIR = Path(__file__).with_name("corpus")
LANGUAGES = ("de", "en")


@cache
def load(language: str) -> dict[str, Any]:
    return json.loads((CORPUS_DIR / f"{language}.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def tools() -> list[dict[str, Any]]:
    return json.loads((CORPUS_DIR / "tools.json").read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class ToolCase:
    language: str
    utterance: str
    tool: str | None  # None: the model must answer without a tool
    accept: tuple[str, ...] = ()
    checks: dict[str, Any] = field(default_factory=dict)
    # Tools whose call is a serious failure here (e.g. end_call on "thanks").
    forbidden: tuple[str, ...] = ()
    hard: bool = False


def _fill(value: Any, slots: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        return slots.get(value[1:-1], value)
    return value


def tool_cases(language: str, case_set: str = "main") -> list[ToolCase]:
    """``main``: the templated tool cases plus direct-answer cases; ``hard``:
    utterances that must not trigger a destructive tool."""
    corpus = load(language)
    if case_set == "hard":
        return [
            ToolCase(language=language, utterance=item["text"], tool=item.get("tool"),
                     forbidden=tuple(item.get("forbidden", ())), hard=True)
            for item in corpus.get("hard_negatives", [])
        ]
    cases: list[ToolCase] = []
    for spec in corpus["tool_cases"]:
        slot_names = list(spec.get("slots", {}))
        slot_values = [spec["slots"][name] for name in slot_names]
        combos = list(itertools.product(*slot_values)) if slot_names else [()]
        for template in spec["templates"]:
            for combo in combos:
                spoken = {name: pair[0] for name, pair in zip(slot_names, combo, strict=True)}
                expected = {name: pair[1] for name, pair in zip(slot_names, combo, strict=True)}
                checks = {
                    arg: {kind: _fill(raw, expected) for kind, raw in rule.items()}
                    for arg, rule in spec.get("check", {}).items()
                }
                cases.append(
                    ToolCase(
                        language=language,
                        utterance=template.format(**spoken),
                        tool=spec["tool"],
                        accept=tuple(spec.get("accept", ())),
                        checks=checks,
                    )
                )
    cases.extend(
        ToolCase(language=language, utterance=text, tool=None) for text in corpus["no_tool"]
    )
    return cases


_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})  # i18n-allow: umlauts


def fold(text: Any) -> str:
    """Case- and umlaut-insensitive form, so a transliterated city name still matches."""
    return str(text).casefold().translate(_FOLD)


def _matches(rule: dict[str, Any], actual: Any) -> bool:
    if "equals" in rule:
        expected = rule["equals"]
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return fold(actual).strip() == fold(expected).strip()
    if "contains" in rule:
        options = rule["contains"]
        options = options if isinstance(options, list) else [options]
        text = fold(actual)
        return any(fold(option) in text for option in options)
    return True


_CALL_SHAPES = re.compile(r'"name"\s*:|<tool_call>|"arguments"\s*:|function_call|\[TOOL_CALLS\]')
_DONE_CLAIMS = {
    "de": re.compile(  # i18n-allow: German completion claims the grader detects
        r"\b(habe|hab|ist|wurde|wurden|sind)\b[^.?!]*\b(gestellt|gesetzt|erstellt|"  # i18n-allow
        r"eingetragen|geöffnet|gestartet|gesendet|geschickt|verschickt|abgespielt|"  # i18n-allow
        r"eingeschaltet|ausgeschaltet|gedimmt|erledigt|gespeichert|angelegt)\b",  # i18n-allow
        re.IGNORECASE,
    ),
    "en": re.compile(
        r"\b(i've|i have|has been|have been|is now|are now|i set|i opened|i sent|i started|"
        r"i turned|i added|i created|i saved)\b",
        re.IGNORECASE,
    ),
}


def _arguments(call: dict[str, Any]) -> dict[str, Any] | None:
    args = call.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    return args if isinstance(args, dict) else None


def grade(case: ToolCase, calls: list[dict[str, Any]], text: str = "") -> dict[str, Any]:
    """Score the calls of the model's first round against the case.

    Verdicts: ``correct``; ``wrong_args``; ``wrong_tool``; ``unneeded`` (a call
    where a direct answer was expected); ``forbidden`` (a destructive call such
    as end_call on "thanks"); and for answers without a call where one was
    needed: ``malformed`` (a call written as text), ``invented`` (claims the
    action happened), ``clarify`` (asks back) or ``missed``.
    """
    names = [c.get("name") for c in calls]
    if any(name in case.forbidden for name in names):
        return {"verdict": "forbidden", "called": names}
    if case.tool is None:
        return {"verdict": "correct" if not calls else "unneeded", "called": names}
    if not calls:
        if _CALL_SHAPES.search(text or ""):
            return {"verdict": "malformed", "called": []}
        claims = _DONE_CLAIMS.get(case.language)
        if claims is not None and claims.search(text or ""):
            return {"verdict": "invented", "called": []}
        if (text or "").rstrip().endswith("?"):
            return {"verdict": "clarify", "called": []}
        return {"verdict": "missed", "called": []}
    match = next((c for c in calls if c.get("name") == case.tool), None)
    if match is None:
        alternative = next((c for c in calls if c.get("name") in case.accept), None)
        if alternative is not None:
            return {"verdict": "correct", "called": names, "note": "accepted alternative"}
        return {"verdict": "wrong_tool", "called": names}
    args = _arguments(match)
    if args is None:
        return {"verdict": "wrong_args", "called": names, "detail": "arguments not JSON"}
    failed = [arg for arg, rule in case.checks.items() if not _matches(rule, args.get(arg))]
    if failed:
        return {"verdict": "wrong_args", "called": names, "failed": failed, "args": args}
    result: dict[str, Any] = {"verdict": "correct", "called": names}
    if len(calls) > 1 or calls[0] is not match:
        result["note"] = "extra calls"
    return result
