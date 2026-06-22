"""Generate docs/jarvis-cli-reference.md from the curated CLI command tree.

Walks the Typer app (the curated commands only — NOT the dynamic `api` group,
which needs a live server's OpenAPI) and emits a grouped markdown reference. Run
after adding/removing commands so the reference never drifts; the
``generate-cli-command`` skill calls this.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

import click  # noqa: E402
import typer  # noqa: E402

from jarvis.cli_ctl.__main__ import app  # noqa: E402

_OUT = _REPO / "docs" / "jarvis-cli-reference.md"


def _usage(cmd: click.Command) -> str:
    parts: list[str] = []
    for p in cmd.params:
        if isinstance(p, click.Argument):
            parts.append(f"<{p.name}>")
        elif isinstance(p, click.Option) and p.opts:
            parts.append(p.opts[0])
    return " ".join(parts)


def _walk(cmd: click.Command, prefix: str, lines: list[str]) -> None:
    if isinstance(cmd, click.Group):
        for name in sorted(cmd.commands):
            _walk(cmd.commands[name], f"{prefix} {name}".strip(), lines)
    else:
        summary = (cmd.help or cmd.short_help or "").strip().splitlines()
        summary_line = summary[0] if summary else ""
        usage = _usage(cmd)
        sig = f"jarvis {prefix}" + (f" {usage}" if usage else "")
        lines.append(f"- `{sig}` — {summary_line}")


def main() -> int:
    root = typer.main.get_command(app)
    assert isinstance(root, click.Group)
    lines = [
        "# Jarvis CLI — Command Reference",
        "",
        "_Generated from the curated command tree by "
        "`scripts/ci/gen_cli_reference.py` — do not edit by hand. Every mounted "
        "REST endpoint is additionally reachable via `jarvis api <tag> <op>`._",
        "",
    ]
    for name in sorted(root.commands):
        if name == "api":
            continue  # dynamic, server-dependent
        lines.append(f"## {name}")
        lines.append("")
        _walk(root.commands[name], name, lines)
        lines.append("")
    _OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
