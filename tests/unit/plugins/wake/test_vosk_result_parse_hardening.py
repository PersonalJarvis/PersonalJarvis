"""Malformed native recognizer JSON must be a no-hit, never a wake-loop kill.

Field incident (macOS, 2026-07-17): libvosk returned one malformed
``Result()`` payload and the whole parallel wake stack died in a crash-loop
("Wake loop failed: Expecting property name…" every ~20 s) — wake was
effectively deaf. ``_parse_recognizer_json`` converts that into a logged
"heard nothing" (AD-6) and preserves the raw payload for diagnosis.
"""

from __future__ import annotations

import logging

import jarvis.plugins.wake.vosk_kws_provider as vk


def _reset_latch() -> None:
    vk._MALFORMED_RESULT_LOGGED = False


def test_valid_json_passes_through() -> None:
    _reset_latch()
    assert vk._parse_recognizer_json('{"text": "hey leah"}', where="t") == {
        "text": "hey leah"
    }


def test_malformed_json_degrades_to_empty_dict_and_logs_payload(caplog) -> None:
    _reset_latch()
    raw = '{\n  "result" : [{\n      "conf" : nan_garbage'
    with caplog.at_level(logging.WARNING, logger="jarvis.wake.vosk"):
        assert vk._parse_recognizer_json(raw, where="grammar_hit.Result") == {}
    joined = " ".join(r.message for r in caplog.records)
    assert "malformed JSON" in joined
    assert "nan_garbage" in joined  # the raw payload stays diagnosable


def test_malformed_json_warns_only_once(caplog) -> None:
    _reset_latch()
    with caplog.at_level(logging.WARNING, logger="jarvis.wake.vosk"):
        vk._parse_recognizer_json("{broken", where="a")
        vk._parse_recognizer_json("{broken", where="b")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_non_dict_json_degrades_to_empty_dict() -> None:
    _reset_latch()
    assert vk._parse_recognizer_json("[1, 2]", where="t") == {}
