"""Parity guard: the TS ``ProviderTestStatus`` union must mirror the Python SSOT.

The Python <-> Pydantic side is already guarded (``test_provider_test_endpoint.py``
+ the import-time assert in ``provider_routes.py``). This closes the remaining gap:
the frontend union in ``useProviders.ts`` had NO automated parity test, so a status
added in Python (e.g. a future ``budget_exceeded``) could silently desync the UI —
the multi-layer enum-drift class (BUG-008). Now all three layers are guarded.
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.brain.provider_test import PROVIDER_TEST_STATUSES

_REPO_ROOT = Path(__file__).resolve().parents[4]
_TS_FILE = _REPO_ROOT / "jarvis" / "ui" / "web" / "frontend" / "src" / "hooks" / "useProviders.ts"


def _parse_string_union(type_name: str, text: str) -> set[str]:
    """Extract the string-literal members of an ``export type X = "a" | "b";`` union."""
    match = re.search(rf"export type {type_name}\s*=\s*(.+?);", text, re.DOTALL)
    assert match, f"{type_name} union not found in {_TS_FILE.name}"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_ts_provider_test_status_mirrors_python_ssot() -> None:
    assert _TS_FILE.exists(), f"frontend union file missing: {_TS_FILE}"
    members = _parse_string_union("ProviderTestStatus", _TS_FILE.read_text(encoding="utf-8"))
    # Guard against a trivially-green empty/partial parse.
    assert len(members) == len(PROVIDER_TEST_STATUSES), members
    assert members == set(PROVIDER_TEST_STATUSES)
