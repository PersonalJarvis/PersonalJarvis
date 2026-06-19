# jarvis/cli_ctl/render.py
"""Render API payloads: machine JSON (--json) or human-friendly rich output."""
from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

# highlight=False disables Rich's automatic token highlighting so emitted key
# names/values never gain ANSI escape codes — keeps machine-grep + test
# assertions on the captured output robust across platforms.
_out = Console(highlight=False)
_err = Console(stderr=True, highlight=False)


def emit(payload: Any, *, as_json: bool) -> None:
    if as_json:
        # ensure_ascii=False keeps umlauts/emoji intact across platforms.
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return
    if isinstance(payload, list) and payload and all(
        isinstance(r, dict) for r in payload
    ):
        cols: list[str] = []
        for row in payload:
            for k in row:
                if k not in cols:
                    cols.append(k)
        table = Table(show_header=True, header_style="bold")
        for c in cols:
            table.add_column(str(c))
        for row in payload:
            table.add_row(*(str(row.get(c, "")) for c in cols))
        _out.print(table)
    elif isinstance(payload, (dict, list)):
        _out.print_json(json.dumps(payload, ensure_ascii=False))
    elif payload is not None:
        _out.print(str(payload))


def error(message: str) -> None:
    _err.print(f"[red]error:[/red] {message}")
