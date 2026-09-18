"""Deterministic local-model mode switch ("developer mode" / "normal mode").

Runs in ``generate()`` before the LLM, like the reply-language switch: a small
local model does not reliably pick a tool for "switch to developer mode", and
the mode is plain configuration. A mode NAME alone never switches — "what is
developer mode?" reaches the brain — only a name with an explicit switch cue.

Languages are peers (ja/en/de). Japanese is matched on the raw text without
word boundaries (it has none).
"""

from __future__ import annotations

import re

_JA_DEV = "(" + "|".join(
    (
        "\u958b\u767a\u30e2\u30fc\u30c9",
        "\u30c7\u30d9\u30ed\u30c3\u30d1\u30fc\u30e2\u30fc\u30c9",
        "\u958b\u767a\u8005\u30e2\u30fc\u30c9",
    )
) + ")"
_JA_NORMAL = "(" + "|".join(
    (
        "\u901a\u5e38\u30e2\u30fc\u30c9",
        "\u30ce\u30fc\u30de\u30eb\u30e2\u30fc\u30c9",
        "\u666e\u901a\u306e\u30e2\u30fc\u30c9",
    )
) + ")"
_JA_ON = "(" + "|".join(
    (
        "\u306b\u3057\u3066",
        "\u306b\u5207\u308a\u66ff\u3048",
        "\u306b\u5207\u308a\u304b\u3048",
        "\u3078\u5207\u308a\u66ff\u3048",
        "\u306b\u5909\u3048",
        "\u306b\u5165",
        "\u306b\u79fb",
        "\u3092\u30aa\u30f3",
        "\u30aa\u30f3\u306b\u3057\u3066",
        "\u3092\u958b\u59cb",
        "\u3092\u8d77\u52d5",
        "\u59cb\u3081\u3066",
    )
) + ")"
_JA_OFF = "(" + "|".join(
    (
        "\u3092\u7d42\u4e86",
        "\u7d42\u308f",
        "\u3092\u30aa\u30d5",
        "\u30aa\u30d5\u306b\u3057\u3066",
        "\u3092\u3084\u3081",
        "\u6b62\u3081\u3066",
        "\u304b\u3089\u623b",
    )
) + ")"

_JA_DEV_ON_RE = re.compile(_JA_DEV + _JA_ON)
_JA_DEV_OFF_RE = re.compile(_JA_DEV + _JA_OFF)
_JA_NORMAL_ON_RE = re.compile(_JA_NORMAL + "(\u306b|\u3078|\u3067)")

_LATIN_DEV_RE = re.compile(
    r"\b(switch|go|change|turn|set|enable|activate|start|enter)\b.{0,20}"
    r"\b(developer|dev|coding)\s+mode\b"
    r"|\b(developer|dev|coding)\s+mode\s+(on|please)\b"
    r"|\bentwicklermodus\b.{0,20}\b(an|ein|aktivier\w*|starten)\b"
    r"|\b(aktivier\w*|wechsl\w*|schalt\w*)\b.{0,25}\bentwicklermodus\b",
    re.IGNORECASE,
)
_LATIN_NORMAL_RE = re.compile(
    r"\b(switch|go|change|turn|set|back)\b.{0,20}\bnormal\s+mode\b"
    r"|\b(developer|dev|coding)\s+mode\s+off\b"
    r"|\b(exit|leave|stop|end)\b.{0,10}\b(developer|dev|coding)\s+mode\b"
    r"|\b(zurueck|wechsl\w*|schalt\w*)\b.{0,25}\bnormalmodus\b"  # i18n-allow
    r"|\bentwicklermodus\b.{0,20}\b(aus|beenden)\b",
    re.IGNORECASE,
)


def match_local_mode(text: str) -> str | None:
    """``"developer"``, ``"normal"`` or None for an explicit mode-switch request."""
    t = (text or "").strip()
    if not t:
        return None
    if _JA_DEV_OFF_RE.search(t) or _JA_NORMAL_ON_RE.search(t) or _LATIN_NORMAL_RE.search(t):
        return "normal"
    if _JA_DEV_ON_RE.search(t) or _LATIN_DEV_RE.search(t):
        return "developer"
    return None


__all__ = ["match_local_mode"]
