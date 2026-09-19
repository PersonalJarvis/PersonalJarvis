"""Deterministic copilot commands (Japanese first, English as a peer).

Only an explicit request counts; "what is a slide deck?" reaches the brain.

* ``material``        - "make/create a slide deck ...", Japanese "shiryou wo tsukutte"
* ``workflow_start``  - "start observing my work", Japanese "sagyou no kansatsu wo kaishi"
* ``workflow_stop``   - "stop observing my work", Japanese "sagyou no kansatsu wo shuuryou"
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_JA_MATERIAL_RE = re.compile(
    "(\u8cc7\u6599|\u30b9\u30e9\u30a4\u30c9|\u30d7\u30ec\u30bc\u30f3|\u30d1\u30ef\u30dd|PPT|pptx)"
    ".{0,6}?"
    "(\u3092|\u306e)?(\u4f5c\u3063\u3066|\u4f5c\u6210\u3057\u3066|\u4f5c\u6210|\u3064\u304f\u3063\u3066|\u4f5c\u308c|\u7528\u610f\u3057\u3066)"
)
_EN_MATERIAL_RE = re.compile(
    r"\b(make|create|build|prepare)\b.{0,20}\b(slide\s*deck|slides|presentation|pptx)\b",
    re.I,
)
_JA_OBS = (
    "(\u4f5c\u696d|\u64cd\u4f5c|PC\u4f5c\u696d|\u30d1\u30bd\u30b3\u30f3\u4f5c\u696d)"
    "(\u306e|\u3092)?(\u89b3\u5bdf|\u8a18\u9332)"
)
_JA_WF_START_RE = re.compile(
    _JA_OBS + "(\u3092)?(\u958b\u59cb|\u59cb\u3081|\u30b9\u30bf\u30fc\u30c8|\u3057\u3066)"
)
_JA_WF_STOP_RE = re.compile(
    _JA_OBS
    + "(\u3092)?(\u7d42\u4e86|\u7d42\u308f|\u6b62\u3081|\u3084\u3081|\u30b9\u30c8\u30c3\u30d7)"
)
_EN_WF_START_RE = re.compile(r"\bstart\b.{0,12}\bobserv\w*\b.{0,12}\bwork\b", re.I)
_EN_WF_STOP_RE = re.compile(r"\b(stop|end)\b.{0,12}\bobserv\w*\b.{0,12}\bwork\b", re.I)


_JA_REVISE_RE = re.compile(
    r"(\d{1,2})\s*(\u30da\u30fc\u30b8\u76ee|\u679a\u76ee|\u30da\u30fc\u30b8|\u30b9\u30e9\u30a4\u30c9\u76ee)"
    ".{0,30}(\u76f4\u3057\u3066|\u4fee\u6b63|\u77ed\u304f|\u9577\u304f|\u5909\u3048\u3066|\u66f8\u304d\u76f4|\u8a73\u3057\u304f|\u7c21\u5358\u306b|\u308f\u304b\u308a\u3084\u3059\u304f)"
)
_EN_REVISE_RE = re.compile(
    r"\b(?:page|slide)\s*(\d{1,2})\b.{0,40}"
    r"\b(shorter|shorten|fix|change|rewrite|simplify|expand|longer)\b",
    re.I,
)
_JA_DRAFT_RE = re.compile(
    r"\u5019\u88dc\s*(\d)\s*(\u3092|\u306e)?.{0,6}"
    r"(\u81ea\u52d5\u5316|\u30b9\u30af\u30ea\u30d7\u30c8)"
)
_EN_DRAFT_RE = re.compile(r"\bautomate\b.{0,12}\bcandidate\s*(\d)\b", re.I)


@dataclass(frozen=True)
class CopilotCommand:
    # "material" | "material_revise" | "workflow_start" | "workflow_stop" | "workflow_draft"
    kind: str
    text: str = ""
    number: int = 0


def match_copilot_command(text: str) -> CopilotCommand | None:
    t = (text or "").strip()
    if not t:
        return None
    m = _JA_REVISE_RE.search(t) or _EN_REVISE_RE.search(t)
    if m:
        return CopilotCommand("material_revise", t, int(m.group(1)))
    m = _JA_DRAFT_RE.search(t) or _EN_DRAFT_RE.search(t)
    if m:
        return CopilotCommand("workflow_draft", t, int(m.group(1)))
    if _JA_WF_STOP_RE.search(t) or _EN_WF_STOP_RE.search(t):
        return CopilotCommand("workflow_stop")
    if _JA_WF_START_RE.search(t) or _EN_WF_START_RE.search(t):
        return CopilotCommand("workflow_start")
    if _JA_MATERIAL_RE.search(t) or _EN_MATERIAL_RE.search(t):
        return CopilotCommand("material", t)
    return None


__all__ = ["CopilotCommand", "match_copilot_command"]
