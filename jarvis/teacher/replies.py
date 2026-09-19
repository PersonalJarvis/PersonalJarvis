"""Fixed teacher-mode replies (Japanese / English). Japanese is escaped ASCII."""

from __future__ import annotations

REPLIES: dict[str, dict[str, str]] = {
    "plan_done": {
        "ja": (
            "\u6307\u5c0e\u6848\u3092\u4f5c\u6210\u3057\u307e\u3057\u305f\uff08{min"
            "utes}\u5206\uff09\u3002\u4fdd\u5b58\u5148: {path}"
        ),
        "en": "The lesson plan is ready ({minutes} min). Saved to {path}",
    },
    "started": {
        "ja": (
            "\u6388\u696d\u3092\u958b\u59cb\u3057\u307e\u3057\u305f\uff08{minutes}"
            "\u5206\uff09\u3002\u767a\u8a00\u3092\u6587\u5b57\u3067\u8a18\u9332"
            "\u3057\u307e\u3059\u3002\u300c\u30b8\u30e3\u30fc\u30d3\u30b9\u3001"
            "\u307e\u3068\u3081\u3066\u300d\u3067\u8981\u7d04\u3057\u307e\u3059"
            "\u3002"
        ),
        "en": (
            "The lesson has started ({minutes} min). I am recording what is said as"
            " text. Say \"Jarvis, summarize\" for a summary."
        ),
    },
    "remaining": {
        "ja": "\u6b8b\u308a\u6642\u9593: \u7d04{minutes}\u5206",
        "en": "Time left: about {minutes} min",
    },
    "ended": {
        "ja": (
            "\u6388\u696d\u3092\u7d42\u4e86\u3057\u307e\u3057\u305f\u3002\u6388"
            "\u696d\u8a18\u9332\u3092\u4fdd\u5b58\u3057\u307e\u3057\u305f: {path}"
        ),
        "en": "The lesson has ended. The record is saved to {path}",
    },
    "failed": {
        "ja": (
            "\u3059\u307f\u307e\u305b\u3093\u3001\u6587\u7ae0\u3092\u4f5c\u6210"
            "\u3067\u304d\u307e\u305b\u3093\u3067\u3057\u305f: {error}"
        ),
        "en": "Sorry, I could not write that: {error}",
    },
}


def reply(key: str, language: str, **values: object) -> str:
    table = REPLIES[key]
    return table.get(language, table["en"]).format(**values)


__all__ = ["REPLIES", "reply"]
