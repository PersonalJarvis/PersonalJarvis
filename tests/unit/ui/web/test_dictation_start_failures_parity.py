"""AP-4 parity for the dictation refusal reasons the web UI reacts to.

The WebSocket hook (``useWebSocket``) resets the optimistic recording pill when the
backend refuses a dictation start, and ONLY for the reasons in
``DICTATION_START_FAILURES`` (``lib/dictationRefusal.ts``). A token spelled differently
on the two sides would leave the pill stuck with a waveform and no error anywhere, so the
TypeScript set is regex-read here and every member must be a refusal reason Python can send
(``DICTATION_REFUSAL_REASONS``).
"""

from __future__ import annotations

import re
from pathlib import Path

from jarvis.core.events import DICTATION_REFUSAL_REASONS

_TS = (
    Path(__file__).resolve().parents[4]
    / "jarvis"
    / "ui"
    / "web"
    / "frontend"
    / "src"
    / "lib"
    / "dictationRefusal.ts"
)

# The reasons that mean "the start did not happen". The others (``already_running`` and the
# post-recording ``nothing_to_paste`` / ``paste_unavailable`` / ``history_disabled``) come from
# another trigger or from a recording that is over and must NOT drop a live pill.
_NOT_START_FAILURES = {
    "already_running",
    "nothing_to_paste",
    "paste_unavailable",
    "history_disabled",
}


def _ts_tokens() -> set[str]:
    assert _TS.exists(), f"frontend twin missing: {_TS}"
    source = _TS.read_text(encoding="utf-8")
    match = re.search(
        r"export const DICTATION_START_FAILURES: ReadonlySet<string> = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert match, "DICTATION_START_FAILURES missing from lib/dictationRefusal.ts"
    tokens = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert tokens, "parsed no tokens"
    return tokens


def test_the_dictation_start_failures_are_refusal_reasons_python_can_send() -> None:
    """The WS hook resets the recording pill only for these tokens: they must exist (AP-4)."""
    assert _ts_tokens() <= set(DICTATION_REFUSAL_REASONS)


def test_a_start_failure_is_never_one_of_the_post_recording_reasons() -> None:
    assert not _ts_tokens() & _NOT_START_FAILURES


def test_every_reason_python_can_send_is_classified_one_way_or_the_other() -> None:
    """A new refusal reason must be decided on: does it reset the pill, or not?"""
    assert set(DICTATION_REFUSAL_REASONS) <= _ts_tokens() | _NOT_START_FAILURES
