"""Parity test for the SpeechSpoken ``spoken_kind`` vocabulary.

Same anti-drift contract as ``test_hangup_reason_parity.py``: the set of spoken
phrase-kinds (timeout / announcement / clarify / …) must agree across every
layer, so the Transcription view's per-kind label covers every value the
pipeline can emit. A drift fails this test instead of shipping a missing label.

Layers under test:

1. ``jarvis/sessions/constants.py``       — ``SPOKEN_KINDS`` tuple (source of truth)
2. ``jarvis/sessions/models.py``          — ``KNOWN_SPOKEN_KINDS`` frozenset (mirror)
3. ``jarvis/ui/web/frontend/.../sessions/types.ts``
                                          — ``KNOWN_SPOKEN_KINDS`` const tuple
4. ``jarvis/ui/web/frontend/.../sessions/TurnCard.tsx``
                                          — the ``SPOKEN_KIND_LABEL`` map
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.sessions.constants import SPOKEN_KINDS
from jarvis.sessions.models import KNOWN_SPOKEN_KINDS

REPO_ROOT = Path(__file__).resolve().parents[3]
TYPES_TS = REPO_ROOT / "jarvis/ui/web/frontend/src/components/sessions/types.ts"
TURN_CARD_TSX = (
    REPO_ROOT / "jarvis/ui/web/frontend/src/components/sessions/TurnCard.tsx"
)


def _expected() -> set[str]:
    return set(SPOKEN_KINDS)


def test_models_known_set_matches_constants_tuple() -> None:
    assert set(KNOWN_SPOKEN_KINDS) == _expected()


def test_types_ts_known_set_matches_constants_tuple() -> None:
    text = TYPES_TS.read_text(encoding="utf-8")
    block = re.search(
        r"export\s+const\s+KNOWN_SPOKEN_KINDS\s*=\s*\[([\s\S]+?)\]\s*as\s+const",
        text,
        re.MULTILINE,
    )
    assert block is not None, "could not find KNOWN_SPOKEN_KINDS const in types.ts"
    found = set(re.findall(r'"([^"]*)"', block.group(1)))
    assert found == _expected(), (
        f"types.ts drift: extra={found - _expected()}, "
        f"missing={_expected() - found}"
    )


def test_turn_card_label_map_covers_every_kind() -> None:
    """``SPOKEN_KIND_LABEL`` in TurnCard.tsx must have a label for every kind,
    so no recorded spoken phrase renders without a human-readable tag."""
    text = TURN_CARD_TSX.read_text(encoding="utf-8")
    block = re.search(
        r"SPOKEN_KIND_LABEL[^=]*=\s*\{([\s\S]+?)\}",
        text,
    )
    assert block is not None, "could not find SPOKEN_KIND_LABEL map in TurnCard.tsx"
    keys = set(re.findall(r'(?:"([^"]+)"|(\w+))\s*:', block.group(1)))
    # findall with two groups yields tuples — flatten to the non-empty member.
    flat = {a or b for (a, b) in keys}
    assert flat == _expected(), (
        f"TurnCard.tsx SPOKEN_KIND_LABEL drift: extra={flat - _expected()}, "
        f"missing={_expected() - flat}"
    )
