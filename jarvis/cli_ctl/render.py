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


def is_human() -> bool:
    """True when the caller is a person at a terminal, not a pipe or an agent.

    A command that wants to narrate ("Downloading… done", "installed but not
    connected yet") asks this first: prose belongs to the interactive terminal
    only, while every non-TTY consumer keeps receiving the machine JSON
    ``emit`` produces. Callers must still honor the global ``--json`` flag.
    """
    return _stdout_isatty()


def line(text: str = "") -> None:
    """Print one prose line to stdout (Rich markup allowed, no highlighting)."""
    _out.print(text)


def field(label: str, value: str, *, label_width: int = 9) -> None:
    """Print one ``label   value`` row of a status block, wrapped in-column.

    The value is wrapped here rather than by Rich because Rich restarts a
    continuation line at column 0, which destroys the two-column read exactly
    when it matters most — a narrow terminal. Long unbroken tokens (a Windows
    path, a URL) are left intact and allowed to overhang; a path chopped
    mid-word is worse than one that runs on. The value is escaped, so a
    file path or an error message containing ``[`` cannot be eaten as markup.
    """
    import textwrap

    from rich.markup import escape

    indent = " " * (2 + label_width + 1)
    width = max(40, min(_out.width, 100))
    parts = textwrap.wrap(
        value,
        width=max(20, width - len(indent)),
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]
    _out.print(f"  [bold]{label:<{label_width}}[/bold] {escape(parts[0])}", soft_wrap=True)
    for extra in parts[1:]:
        _out.print(f"{indent}{escape(extra)}", soft_wrap=True)


def error(message: str) -> None:
    _err.print(f"[red]error:[/red] {message}")
