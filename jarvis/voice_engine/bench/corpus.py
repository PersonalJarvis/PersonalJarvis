"""The bench's German and English test material and the tool-call checks."""

from __future__ import annotations

import itertools
import json
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


def _fill(value: Any, slots: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        return slots.get(value[1:-1], value)
    return value


def tool_cases(language: str) -> list[ToolCase]:
    corpus = load(language)
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


def _matches(rule: dict[str, Any], actual: Any) -> bool:
    if "equals" in rule:
        expected = rule["equals"]
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return str(actual).strip().casefold() == str(expected).strip().casefold()
    if "contains" in rule:
        options = rule["contains"]
        options = options if isinstance(options, list) else [options]
        text = str(actual).casefold()
        return any(str(option).casefold() in text for option in options)
    return True


def grade(case: ToolCase, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Score the model's first tool call against the case.

    Verdicts: ``correct`` (right tool, arguments pass), ``wrong_args``,
    ``wrong_tool``, ``missed`` (no call where one was needed) and
    ``unneeded`` (a call where a direct answer was expected).
    """
    if case.tool is None:
        return {"verdict": "correct" if not calls else "unneeded",
                "called": [c.get("name") for c in calls]}
    if not calls:
        return {"verdict": "missed", "called": []}
    first = calls[0]
    name = first.get("name")
    if name != case.tool and name not in case.accept:
        return {"verdict": "wrong_tool", "called": [c.get("name") for c in calls]}
    if name != case.tool:
        return {"verdict": "correct", "called": [name], "note": "accepted alternative"}
    args = first.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return {"verdict": "wrong_args", "called": [name], "detail": "arguments not JSON"}
    failed = [arg for arg, rule in case.checks.items() if not _matches(rule, args.get(arg))]
    if failed:
        return {"verdict": "wrong_args", "called": [name], "failed": failed, "args": args}
    return {"verdict": "correct", "called": [name]}
