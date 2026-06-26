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


def _stdout_isatty() -> bool:
    """True only for a real interactive terminal. Any failure (an exotic stdio
    wrapper without ``isatty``, or one that raises) is treated as
    non-interactive, so non-TTY consumers — the brain's piped subprocess, a
    shell pipe, a script — receive machine-readable JSON."""
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # noqa: BLE001 - non-interactive is the safe default
        return False


def emit(payload: Any, *, as_json: bool) -> None:
    # Machine-readable JSON when explicitly requested (--json) OR whenever stdout
    # is not an interactive terminal. The cli_jarvisctl tool runs `jarvisctl`
    # with a piped stdout, so the brain (and any pipe/script) gets parsable JSON
    # instead of a Rich table it would have to parse character-by-character.
    if as_json or not _stdout_isatty():
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
