"""Generate docs/jarvis-cli-reference.md from the curated CLI command tree.

Walks the Typer app (the curated commands only — NOT the dynamic `api` group,
which needs a live server's OpenAPI) and emits a grouped markdown reference. Run
after adding/removing commands so the reference never drifts; the
``generate-cli-command`` skill calls this.

Modes:
* (no flag) — regenerate ``docs/jarvis-cli-reference.md`` in place.
* ``--check`` — build the reference in memory and exit non-zero if it differs
  from the committed file (the pre-push hook + CI drift gate use this, so the
  reference can never silently fall behind the command tree).
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


def _build_reference() -> str:
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
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    generated = _build_reference()
    rel = _OUT.relative_to(_REPO)
    if "--check" in args:
        current = _OUT.read_text(encoding="utf-8") if _OUT.exists() else ""
        if current != generated:
            print(
                f"DRIFT: {rel} is out of date with the curated command tree.\n"
                "Run `python scripts/ci/gen_cli_reference.py` and commit the result."
            )
            return 1
        print(f"check OK — {rel} is current.")
        return 0
    _OUT.write_text(generated, encoding="utf-8")
    print(f"wrote {_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
