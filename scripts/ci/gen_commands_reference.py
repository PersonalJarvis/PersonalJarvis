"""Generate docs/commands-reference.md from the Command Registry.

Walks ``jarvis/commands/registry.py`` (the one machine-readable catalog of
user-facing app commands) and emits a markdown reference: what each command
does, its arguments, whether it requires confirmation, where it lives in the
desktop UI, an English voice example, and the backing REST endpoint. Personal
data cannot appear by construction — the registry contains only curated
product strings.

The reference documents the ENGLISH voice example only; the full multilingual
alias set (de/en/es) is machine-readable at ``GET /api/commands`` and in the
registry source.

Modes (mirrors ``gen_cli_reference.py``):
* (no flag) — regenerate ``docs/commands-reference.md`` in place.
* ``--check`` — build in memory and exit non-zero on drift (pre-push + CI).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from jarvis.commands.registry import get_registry  # noqa: E402

_OUT = _REPO / "docs" / "commands-reference.md"


def _args_line(params: dict) -> str:
    props = params.get("properties", {}) if params else {}
    if not props:
        return "none"
    required = set(params.get("required", []))
    parts = []
    for name, spec in props.items():
        desc = spec.get("type", "any")
        if spec.get("enum"):
            desc = "one of: " + ", ".join(str(v) for v in spec["enum"])
        marker = "required" if name in required else "optional"
        parts.append(f"`{name}` ({desc}; {marker})")
    return "; ".join(parts)


def _build_reference() -> str:
    lines = [
        "# App Commands — Reference",
        "",
        "_Generated from the Command Registry by "
        "`scripts/ci/gen_commands_reference.py` — do not edit by hand._",
        "",
        "Every command below is available on four surfaces backed by the SAME "
        "endpoint and validation chain:",
        "",
        "- **Voice/chat** — Jarvis's `app-command` tool (say it naturally).",
        "- **Desktop UI** — the sidebar section named per command.",
        "- **CLI** — `jarvis commands list` / `jarvis commands show <id>` to "
        "browse; execute via the curated command or `jarvis api <tag> <op>`.",
        "- **REST** — the endpoint listed per command "
        "(machine-readable catalog: `GET /api/commands`).",
        "",
        "Commands marked **requires confirmation** never run on a bare voice "
        "request — Jarvis asks first (two-turn confirm); the CLI needs "
        "`--yes`.",
        "",
    ]
    for cmd in get_registry():
        lines.append(f"## `{cmd.id}` — {cmd.title}")
        lines.append("")
        lines.append(cmd.description)
        lines.append("")
        en_alias = (cmd.voice_aliases.get("en") or ("",))[0]
        rows = [
            ("Endpoint", f"`{cmd.method} {cmd.path}`"),
            ("Arguments", _args_line(cmd.params)),
            ("Requires confirmation", "yes" if cmd.dangerous else "no"),
            ("Desktop UI section", f"`{cmd.ui_section}`"),
        ]
        if en_alias:
            rows.append(("Voice example (EN)", f'"{en_alias}"'))
        for label, value in rows:
            lines.append(f"- **{label}:** {value}")
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
                f"DRIFT: {rel} is out of date with the Command Registry.\n"
                "Run `python scripts/ci/gen_commands_reference.py` and commit "
                "the result."
            )
            return 1
        print(f"check OK — {rel} is current.")
        return 0
    _OUT.write_text(generated, encoding="utf-8")
    print(f"wrote {_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
