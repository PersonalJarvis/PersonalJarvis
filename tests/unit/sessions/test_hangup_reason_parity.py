"""Parity test for the four layers that share the HangupReason vocabulary.

Background
----------

BUG-008 occurred three times (2026-05-03, 2026-05-05, 2026-05-10). The
first two episodes had the same shape: the runtime path in
``jarvis/speech/pipeline.py`` learned to write a new hangup reason but
the Pydantic ``Literal`` did not, and the list endpoint blew up with
HTTP 500. After the third recurrence the Pydantic side was widened
from ``Literal`` to plain ``str`` (see ``models.py`` Z. 22-43), and
the TypeScript side mirrored that to plain ``string``.

What stayed is the *documenting* set of known reasons in three places —
they need to agree so the Transcription view's label switch covers
every value the pipeline can emit, and the schema.sql doc comment is
not lying. The coordination layers under test are:

1. ``jarvis/sessions/constants.py``           — the Python tuple (source of truth)
2. ``jarvis/sessions/models.py``              — ``KNOWN_HANGUP_REASONS`` frozenset (mirror)
3. ``jarvis/ui/web/frontend/src/components/sessions/types.ts``
                                              — ``KNOWN_HANGUP_REASONS`` const tuple
4. ``jarvis/ui/web/frontend/src/components/sessions/SessionList.tsx``
                                              — the user-facing label switch
5. ``jarvis/sessions/schema.sql``             — the doc comment

A drift introduces a single failing test instead of shipping a missing
label, an empty Transcription cell, or a stale schema comment.
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.sessions.constants import HANGUP_REASONS
from jarvis.sessions.models import KNOWN_HANGUP_REASONS

REPO_ROOT = Path(__file__).resolve().parents[3]
TYPES_TS = REPO_ROOT / "jarvis/ui/web/frontend/src/components/sessions/types.ts"
SESSION_LIST_TSX = (
    REPO_ROOT / "jarvis/ui/web/frontend/src/components/sessions/SessionList.tsx"
)
SCHEMA_SQL = REPO_ROOT / "jarvis/sessions/schema.sql"


def _expected_set() -> set[str]:
    """The canonical set under test. Treats the empty string the same
    as the other values; every layer must spell it."""
    return set(HANGUP_REASONS)


def test_models_known_set_matches_constants_tuple() -> None:
    """``KNOWN_HANGUP_REASONS`` in models.py must mirror constants.py.

    Replaces the older Literal-vs-tuple check — see ``models.py`` Z. 22-43
    for why Pydantic now uses plain ``str`` instead of ``Literal``."""
    assert set(KNOWN_HANGUP_REASONS) == _expected_set()


def test_types_ts_known_set_matches_constants_tuple() -> None:
    """The TypeScript ``KNOWN_HANGUP_REASONS`` const must list every value.

    Parsed by extracting the string literals from the
    ``export const KNOWN_HANGUP_REASONS = [...]`` block.
    """
    text = TYPES_TS.read_text(encoding="utf-8")
    block = re.search(
        r"export\s+const\s+KNOWN_HANGUP_REASONS\s*=\s*\[([\s\S]+?)\]\s*as\s+const",
        text,
        re.MULTILINE,
    )
    assert block is not None, (
        "could not find KNOWN_HANGUP_REASONS const in types.ts"
    )
    body = block.group(1)
    found = set(re.findall(r'"([^"]*)"', body))
    assert found == _expected_set(), (
        f"types.ts drift: extra={found - _expected_set()}, "
        f"missing={_expected_set() - found}"
    )


def test_session_list_tsx_switch_covers_all_reasons() -> None:
    """SessionList.tsx ``hangupLabel`` must have a case for every non-empty value.

    The empty string is the running-session marker and never reaches
    ``hangupLabel`` (the caller falls back to a "läuft" badge); we  # i18n-allow: quotes the actual German UI badge label under test
    therefore exclude it from the required cases.
    """
    text = SESSION_LIST_TSX.read_text(encoding="utf-8")
    fn = re.search(
        r"function\s+hangupLabel\s*\([^)]*\)\s*:\s*string\s*\{([\s\S]+?)\n\}",
        text,
    )
    assert fn is not None, "could not locate hangupLabel function in SessionList.tsx"
    body = fn.group(1)
    cases = set(re.findall(r'case\s+"([^"]+)"\s*:', body))
    required = _expected_set() - {""}
    assert cases == required, (
        f"SessionList.tsx switch drift: extra={cases - required}, "
        f"missing={required - cases}"
    )


def test_schema_sql_doc_comment_lists_every_reason() -> None:
    """The ``hangup_reason`` column comment in schema.sql is the
    on-disk contract everyone reads first. Keep it accurate."""
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    line = next(
        (
            ln
            for ln in text.splitlines()
            if "hangup_reason" in ln and "TEXT" in ln
        ),
        None,
    )
    assert line is not None, "schema.sql does not declare hangup_reason"
    comment = line.split("--", 1)[1] if "--" in line else ""
    declared = {tok.strip() for tok in comment.split("|") if tok.strip()}
    required = _expected_set() - {""}
    assert declared == required, (
        f"schema.sql comment drift: extra={declared - required}, "
        f"missing={required - declared}"
    )
