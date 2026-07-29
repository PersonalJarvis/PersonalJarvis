"""Cross-layer parity guard for the dictation outcome vocabulary (AP-4).

``jarvis.dictation.outcomes.DICTATION_OUTCOMES`` is the single source of truth
for "how did this dictation end". The value is written by the speech pipeline,
carried on ``DictationCompleted``, stored in the history sidecar, serialised by
the REST layer and finally turned into a label by the UI — the exact five-layer
shape that has drifted four times in this repo (BUG-008).

The two layers pinned here are the ones nothing else can catch:

* the TypeScript mirror in ``useDictation.ts``. A value missing there is not a
  type error — the UI simply falls through to rendering the raw identifier
  ("clipboard_only") at the user;
* the ``dictation.outcome.<name>`` key in every locale. A missing key renders
  the key itself on screen, in the one place whose whole job is to explain to
  a human what happened to their words.

Every parsed set is asserted NON-EMPTY before comparison, so a regex that stops
matching fails loudly instead of going trivially green against an empty set.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jarvis.dictation.outcomes import (
    DICTATION_OUTCOMES,
    RECOVERABLE_OUTCOMES,
    is_recoverable,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FRONTEND = _REPO_ROOT / "jarvis" / "ui" / "web" / "frontend" / "src"
_DICTATION_TS = _FRONTEND / "hooks" / "useDictation.ts"
_HISTORY_GROUP_TSX = _FRONTEND / "views" / "voice" / "DictationHistoryGroup.tsx"
_LOCALES = _FRONTEND / "i18n" / "locales"

SUPPORTED_LOCALES = ("de", "en", "es")


def _ts_outcomes() -> list[str]:
    """Members of ``export const DICTATION_OUTCOMES = [...] as const``."""
    assert _DICTATION_TS.exists(), f"frontend hook missing: {_DICTATION_TS}"
    source = _DICTATION_TS.read_text(encoding="utf-8")
    match = re.search(
        r"export const DICTATION_OUTCOMES\s*=\s*\[(.*?)\]\s*as const",
        source,
        re.DOTALL,
    )
    assert match is not None, f"DICTATION_OUTCOMES array not found in {_DICTATION_TS.name}"
    return re.findall(r'"([a-z_]+)"', match.group(1))


def _locale(name: str) -> dict:
    path = _LOCALES / f"{name}.json"
    assert path.exists(), f"locale file missing: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_python_vocabulary_is_a_usable_set() -> None:
    assert DICTATION_OUTCOMES, "DICTATION_OUTCOMES is empty"
    assert len(set(DICTATION_OUTCOMES)) == len(DICTATION_OUTCOMES), DICTATION_OUTCOMES


def test_ts_outcome_array_mirrors_the_python_vocabulary() -> None:
    members = _ts_outcomes()
    # Guard against a trivially-green empty/partial parse.
    assert members, f"parsed no outcomes from {_DICTATION_TS.name}"
    assert len(members) == len(set(members)), members
    assert len(members) == len(DICTATION_OUTCOMES), members
    assert set(members) == set(DICTATION_OUTCOMES)


def test_every_outcome_has_a_label_in_every_locale() -> None:
    assert DICTATION_OUTCOMES, "DICTATION_OUTCOMES is empty"
    for name in SUPPORTED_LOCALES:
        table = _locale(name).get("dictation", {}).get("outcome", {})
        assert isinstance(table, dict) and table, f"{name}.json: dictation.outcome missing"
        for outcome in DICTATION_OUTCOMES:
            value = table.get(outcome)
            assert isinstance(value, str) and value.strip(), (
                f"{name}.json: dictation.outcome.{outcome}"
            )


def test_no_locale_carries_an_outcome_key_the_backend_never_emits() -> None:
    """A stale key is drift in the other direction — dead copy nobody maintains."""
    for name in SUPPORTED_LOCALES:
        table = _locale(name).get("dictation", {}).get("outcome", {})
        assert set(table) == set(DICTATION_OUTCOMES), f"{name}.json: {sorted(table)}"


def test_the_renderer_derives_its_known_set_from_the_shared_array() -> None:
    """The UI must not keep a second, hand-written copy of the vocabulary.

    ``DictationHistoryGroup`` decides whether to translate an outcome or to
    print it verbatim. If that decision ran off a locally declared list, a new
    outcome would render as a raw identifier even after the shared array was
    updated — a second SSOT is exactly what this whole file exists to prevent.
    """
    assert _HISTORY_GROUP_TSX.exists(), f"missing: {_HISTORY_GROUP_TSX}"
    source = _HISTORY_GROUP_TSX.read_text(encoding="utf-8")
    assert "DICTATION_OUTCOMES" in source, _HISTORY_GROUP_TSX.name
    assert "new Set(DICTATION_OUTCOMES)" in source, (
        f"{_HISTORY_GROUP_TSX.name} no longer derives its known-outcome set "
        "from the shared array"
    )
    # No second literal array of outcomes anywhere in the renderer.
    assert not re.search(r"\[\s*\"inserted\"", source), _HISTORY_GROUP_TSX.name


def test_partial_reached_every_layer() -> None:
    """The newest value, named explicitly so a gap says which file to edit.

    The generic tests above already iterate the whole vocabulary, but they fail
    with "dictation.outcome.partial" and leave the reader to work out what
    ``partial`` is and why it appeared. This one states it: a dictation that
    delivered some words and permanently lost others reports ``partial``, and
    that value has to exist in FIVE places — the Python tuple, the TypeScript
    mirror, and each of the three locales — before the UI can render anything
    but a raw identifier at the user.
    """
    assert "partial" in DICTATION_OUTCOMES, "jarvis/dictation/outcomes.py"
    assert "partial" in _ts_outcomes(), (
        "jarvis/ui/web/frontend/src/hooks/useDictation.ts: add \"partial\" to "
        "DICTATION_OUTCOMES"
    )
    for name in SUPPORTED_LOCALES:
        table = _locale(name).get("dictation", {}).get("outcome", {})
        assert str(table.get("partial", "")).strip(), (
            f"jarvis/ui/web/frontend/src/i18n/locales/{name}.json: add "
            "dictation.outcome.partial"
        )


def test_partial_keeps_its_audio_so_restore_has_something_to_offer() -> None:
    """The half of ``partial`` that is not about labels.

    ``RECOVERABLE_OUTCOMES`` is what decides whether the audio sidecar is
    written, and the sidecar is what the history's Restore button runs again.
    A ``partial`` that is not in this set is the shipped bug wearing a new
    name: an honest label on a dictation nobody can recover.
    """
    assert "partial" in RECOVERABLE_OUTCOMES
    assert is_recoverable("partial") is True
    # A delivery that lost nothing keeps today's rule: no audio is kept.
    for outcome in ("inserted", "paste_sent", "clipboard_only", "chat"):
        assert is_recoverable(outcome) is False, outcome


def test_recoverable_outcomes_are_a_real_subset_and_discarded_is_not_one() -> None:
    """``discarded`` is a boolean field, never an outcome (AD-6).

    Folding the two into one string would make "inserted, then discarded by the
    user" unrepresentable, which is a state the history genuinely has.
    """
    assert RECOVERABLE_OUTCOMES, "RECOVERABLE_OUTCOMES is empty"
    assert RECOVERABLE_OUTCOMES < set(DICTATION_OUTCOMES)
    assert "discarded" not in DICTATION_OUTCOMES
    for outcome in DICTATION_OUTCOMES:
        assert is_recoverable(outcome) is (outcome in RECOVERABLE_OUTCOMES), outcome
    # Tolerant on unknown input: a newer backend value must not raise.
    assert is_recoverable("something_new_from_a_newer_install") is False
    assert is_recoverable(None) is False
