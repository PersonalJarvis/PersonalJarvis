"""Local-model mode switch gate: a mode name plus an explicit switch cue only."""

from __future__ import annotations

import pytest

from jarvis.brain.local_mode_gate import match_local_mode


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("開発モードに切り替えて", "developer"),  # i18n-allow: "switch to developer mode" (ja)
        ("開発モードを終了して", "normal"),  # i18n-allow: "end developer mode" (ja)
        ("通常モードに戻して", "normal"),  # i18n-allow: "back to normal mode" (ja)
        ("switch to developer mode", "developer"),
        ("developer mode off", "normal"),
        ("Aktiviere den Entwicklermodus", "developer"),  # i18n-allow
    ],
)
def test_explicit_switch_requests(text: str, mode: str) -> None:
    assert match_local_mode(text) == mode


@pytest.mark.parametrize(
    "text",
    [
        "開発モードって何？",  # i18n-allow: "what is developer mode?" (ja)
        "what is developer mode",
        "メモ帳を開いて",  # i18n-allow: "open Notepad" (ja)
        "",
    ],
)
def test_mentions_and_other_requests_do_not_switch(text: str) -> None:
    assert match_local_mode(text) is None
