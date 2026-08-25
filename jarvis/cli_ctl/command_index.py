"""The `jarvisctl` command tree as plain data — what the brain's CLI tool shows.

Why this exists (voice session 2026-08-18 17:51): the brain wanted to create a
skill, knew `cli_jarvisctl` existed and nothing about its commands, and spent
three tool rounds — `jarvisctl --help`, `jarvisctl skills --help`,
`jarvisctl skills draft --help` — reading help text until the 45 s loop
deadline ended the turn. The CLI catalog gives the tool six example commands;
everything else the model had to discover at the user's expense, one slow
provider round per `--help`. That is not a skills problem: it is the same for
workflows, tasks, wiki, board, contacts, missions — every group the CLI
has and the model cannot see.

So the tool description carries the whole tree: one line per group, the
command names, and for the commands a spoken request most often lands on, the
argument shape in angle brackets. Roughly 1.6 k characters — the price of one
tool schema, paid once per prompt build, against several `--help` rounds paid
per turn.

Static on purpose. Importing ``jarvis.cli_ctl.__main__`` costs ~5 s on a cold
process (Typer plus every command module), which has no place on the brain
build path (AP-26). Drift is impossible to miss instead of impossible to make:
``tests/unit/cli_ctl/test_command_index_parity.py`` walks the live Typer app
and fails the moment a group or command name here disagrees with it.
"""

from __future__ import annotations

#: ``group -> (command, ...)``. A command may carry an argument hint after a
#: space (``'create "<what it should do>" [--name --trigger --schedule]'``);
#: the parity test compares only the first token. Order: the groups a spoken
#: request most often needs first, then the rest alphabetically.
COMMAND_INDEX: dict[str, tuple[str, ...]] = {
    "skills": (
        "list",
        "show <name>",
        'create "<what it should do>" [--name --trigger --schedule --language]',
        'draft "<what it should do>"',
        "commit --draft <json>",
        "enable <name>",
        "disable <name>",
        "reload",
        "import <folder-or-url>",
        'catalog-search "<query>"',
        "catalog-install <name> --source-url --title",
    ),
    "brain": (
        "status",
        "list",
        "switch <provider>",
        "subagent-switch <provider>",
        "test <provider>",
        "deep-model",
    ),
    "config": ("get <key>", "list", "set <key> <value>", "language get", "language set <de|en|es>"),
    "wiki": (
        'recall "<query>"',
        "page <path>",
        "tree",
        "vaults",
        "health",
        'ingest "<text>"',
        "reindex",
        "backfill",
    ),
    "ultrawiki": (
        'ask "<question>"',
        "topics",
        "topic <name>",
        "moments",
        "graph",
        "vault",
        "export",
        "register",
    ),
    "missions": (
        "list",
        "show <id>",
        "result <id>",
        "tool-approvals",
        "approve-tool",
        "deny-tool",
        "dispatch",
        "cancel <id>",
        "rerun <id>",
        "kill <id>",
    ),
    "tasks": ("list", "get <id>", "create", "cancel <id>", "delete <id>"),
    "workflows": ("list", "show <id>", "create", "run <id>", "delete <id>", "run-history"),
    "sessions": (
        "list",
        "latest-turn",
        "show <id>",
        "delete <id>",
        "resume <id>",
        'speak "<text>"',
    ),
    "outputs": (
        "list",
        "plan",
        "files <slug>",
        "graph",
        "openers",
        "open-with",
        "preferred-opener",
    ),
    "contacts": ("list", "show <name>", "add", "edit <name>", "delete <name>", "import", "export"),
    "board": ("summary", "heatmap", "records", "achievements", "bio", "bio-regenerate", "profile"),
    "docs": ("list", "tree", 'search "<query>"', "show <path>"),
    "system": ("restart", "audio-devices", "status"),
    "auth": ("login", "status", "logout"),
    "clis": (
        "list",
        "show <name>",
        "check <name>",
        "install <name>",
        "connect <name>",
        "disconnect <name>",
        "usage",
        "usage-stats",
    ),
    "commands": ("list", "show <id>"),
    "computer-use": ("start", "list", "show <id>", "cancel <id>", "cancel-all"),
    "conductor": ("list", "show <id>", "add", "run <id>", "toggle <id>", "delete <id>"),
    "friends": (
        "list",
        "show <name>",
        "add",
        "edit <name>",
        "delete <name>",
        "messages <name>",
        'message <name> "<text>"',
    ),
    "frontier": ("pending", "ack"),
    "ide": ("rename-terminal", "close-terminals"),
    "local-models": (
        "roles list",
        "roles set <chat|tools_screen|deep|embedding> <model>",
        "models list",
        "models show <model>",
        "models unload <model> --yes",
        "models delete <model> [--reassign <model>] --yes",
        "options get <model>",
        "options set <model> <key=value ...>",
        "options clear <model>",
        "options suggest <model>",
        'catalog search "<query>" [--capability --sort]',
        "catalog tags <model>",
        "catalog recommended",
        'hf search "<query>"',
        "hf files <user> <repo>",
        "hf pull <user> <repo> [--quant]",
        "hf enable [on|off]",
        "server status",
        "server stop --yes",
        "server test <url>",
        "server log [--lines]",
        "server env-guide [--os]",
        "assistant setup",
        "assistant diagnose",
        "assistant test [--role]",
        "assistant benchmarks [--refresh]",
        "assistant health",
        "assistant session",
    ),
    "marketplace": (
        "install <id>",
        "browse",
        "list",
        "connect-pat",
        "connect-start",
        "connect-poll",
        "disconnect <id>",
    ),
    "mcps": (
        "list",
        "enable <name>",
        "disable <name>",
        "check <name>",
        "import-claude-desktop",
        "delete <name>",
    ),
    "permissions": ("status", "request", "open-settings"),
    "socials": ("list", "add", "edit", "delete"),
    "telephony": ("status", "config", "outbound"),
}

#: Top-level commands that are not in a group.
TOP_LEVEL_COMMANDS: tuple[str, ...] = ("version", "refresh")


def command_names(group: str) -> tuple[str, ...]:
    """The bare command names of ``group`` (hints stripped)."""
    return tuple(entry.split(" ", 1)[0] for entry in COMMAND_INDEX.get(group, ()))


def render_command_index() -> str:
    """The tree as the tool description shows it — one line per group."""
    lines = [
        "Command index (`jarvisctl <group> <command> [args]`; call the command "
        "directly — do not spend rounds on --help):",
    ]
    for group, commands in COMMAND_INDEX.items():
        lines.append(f"  {group}: " + ", ".join(commands))
    lines.append("  top-level: " + ", ".join(TOP_LEVEL_COMMANDS))
    return "\n".join(lines)


__all__ = ["COMMAND_INDEX", "TOP_LEVEL_COMMANDS", "command_names", "render_command_index"]
