"""Extractor — LLM call with strict JSON schema.

Uses a **fast** brain (Haiku-class) so that the curator pass after each turn
produces no noticeable latency. Currently ~300-700 ms per call.

Robustness:

- On LLM error (network, auth, rate-limit): `extract` returns `[]` without
  raising. The turn itself must not fail if the curator crashes.
- On JSON parse error: we first try `strip-code-fences`, then
  `ultra-robust-json-extract` (searches for the first `{...}` object in the
  output).
- On invalid schema (missing required field): the candidate is silently
  dropped — the validator handles the rest.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from jarvis.core.protocols import Brain, BrainMessage, BrainRequest

from .prompts import ALLOWED_FIELDS, EXTRACTOR_SYSTEM_PROMPT, build_extraction_prompt

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    """An extracted fact candidate before validation."""

    subject: str            # "user" | "person:<Name>"
    cluster: str            # identity|communication|work_style|values|relationship
    field: str
    value: Any
    operation: str          # set | append
    confidence: float       # 0.0 - 1.0
    evidence: str
    relationship: str | None = None

    @property
    def is_person(self) -> bool:
        return self.subject.startswith("person:")

    @property
    def person_name(self) -> str | None:
        if not self.is_person:
            return None
        return self.subject.split(":", 1)[1].strip()


class Extractor:
    """Wraps a brain instance and the extraction prompt."""

    def __init__(self, brain: Brain) -> None:
        self._brain = brain

    async def extract(
        self,
        user_text: str,
        assistant_text: str,
        *,
        user_name: str | None = None,
        known_people: list[str] | None = None,
    ) -> list[Candidate]:
        prompt = build_extraction_prompt(
            user_text=user_text,
            assistant_text=assistant_text,
            user_name=user_name,
            known_people=known_people,
        )
        req = BrainRequest(
            messages=(BrainMessage(role="user", content=prompt),),
            system=EXTRACTOR_SYSTEM_PROMPT,
            temperature=0.2,      # deterministic for schema extraction
            max_tokens=1024,
            stream=True,
        )

        try:
            chunks: list[str] = []
            async for delta in self._brain.complete(req):
                if delta.content:
                    chunks.append(delta.content)
            raw = "".join(chunks).strip()
        except Exception as exc:  # noqa: BLE001
            log.warning("Curator extraction failed (LLM): %s", exc)
            return []

        if not raw:
            return []

        data = _parse_json_robust(raw)
        if not data:
            log.debug("Curator output not parseable:\n%s", raw[:500])
            return []

        cands_raw = data.get("candidates", [])
        if not isinstance(cands_raw, list):
            return []

        out: list[Candidate] = []
        for item in cands_raw:
            if not isinstance(item, dict):
                continue
            try:
                cand = Candidate(
                    subject=str(item["subject"]),
                    cluster=str(item.get("cluster", "")),
                    field=str(item.get("field", "")),
                    value=item.get("value"),
                    operation=str(item.get("operation", "set")),
                    confidence=float(item.get("confidence", 0.0)),
                    evidence=str(item.get("evidence", ""))[:200],
                    relationship=item.get("relationship"),
                )
            except (KeyError, ValueError, TypeError) as exc:
                log.debug("Candidate invalid, skipping: %s (%s)", item, exc)
                continue

            # Quick check: cluster must be allowed, field too (except 'observation')
            if cand.field != "observation":
                if cand.cluster not in ALLOWED_FIELDS:
                    continue
                if cand.field not in ALLOWED_FIELDS.get(cand.cluster, []):
                    # Free field → treat as observation
                    cand.field = "observation"
            out.append(cand)
        return out


# ----------------------------------------------------------------------
# Robust JSON parsing (LLMs sometimes cheat with markdown fences)
# ----------------------------------------------------------------------

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.+?)\s*```$", re.DOTALL)


def _parse_json_robust(raw: str) -> dict[str, Any] | None:
    """Tries multiple strategies to extract JSON from LLM output."""
    s = raw.strip()

    # 1. Remove markdown fences
    m = _CODE_FENCE_RE.match(s)
    if m:
        s = m.group(1).strip()

    # 2. Direct JSON parse
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 3. Search for the first complete JSON object in the text (balanced braces)
    depth = 0
    start = -1
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                candidate = s[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    start = -1
                    continue
    return None
