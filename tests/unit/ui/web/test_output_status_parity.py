"""Parity guard for the Outputs status vocabulary across Python and TypeScript."""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from jarvis.ui.web.outputs_routes import OUTPUT_STATUSES, OutputStatus


def test_output_status_python_and_typescript_vocabularies_match() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    hook_source = (
        repo_root / "jarvis/ui/web/frontend/src/hooks/useOutputs.ts"
    ).read_text(encoding="utf-8")
    match = re.search(
        r"export const OUTPUT_STATUSES = \[(?P<body>.*?)\] as const;",
        hook_source,
        flags=re.DOTALL,
    )
    assert match is not None, "TypeScript OUTPUT_STATUSES constant is missing"
    ts_statuses = tuple(re.findall(r'"([a-z_]+)"', match.group("body")))

    assert set(get_args(OutputStatus)) == set(OUTPUT_STATUSES)
    assert ts_statuses == OUTPUT_STATUSES
