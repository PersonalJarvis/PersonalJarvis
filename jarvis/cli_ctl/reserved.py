"""Reserved control-command names for the unified ``jarvis`` entry point.

``jarvis/__main__.py`` forwards an invocation to the control CLI (the Typer app
in ``jarvis.cli_ctl.__main__``) only when the first argument is one of these
names — or one of the control-global options. Everything else (bare ``jarvis``,
``jarvis serve``, every launcher flag such as ``--wizard`` / ``--check``) keeps
its existing launcher behavior untouched.

The set is frozen up front to include the command groups that later waves add,
so dispatch is stable as the curated surface grows. A parity test
(``tests/unit/cli_ctl/test_dispatch.py``) asserts none of these collide with a
launcher flag or command.
"""
from __future__ import annotations

# Control command groups + meta commands routed to the control CLI.
RESERVED_CONTROL_NAMES: frozenset[str] = frozenset(
    {
        # meta
        "version",
        "refresh",
        # the dynamic, OpenAPI-derived full-coverage surface
        "api",
        # curated groups (present + reserved for later waves)
        "auth",
        "system",
        "tasks",
        "missions",
        "brain",
        "commands",
        "config",
        "wiki",
        "sessions",
        "skills",
        "outputs",
        "permissions",
        "board",
        "workflows",
        "conductor",
        "contacts",
        "friends",
        "socials",
        "telephony",
        "clis",
        "mcps",
        "marketplace",
        "docs",
        "frontier",
        "ide",
    }
)

# Control-global options accepted before a subcommand (root callback options).
# When one of these leads the invocation we look PAST it for a reserved command
# name before routing — see is_control_invocation.
CONTROL_GLOBAL_OPTIONS: frozenset[str] = frozenset({"--json", "--url", "--key"})

# Control-global options that consume the following token as their value, so
# scanning for the command name must skip that token too.
_VALUE_OPTIONS: frozenset[str] = frozenset({"--url", "--key"})


def is_control_invocation(argv: list[str]) -> bool:
    """True if ``argv`` should be handled by the control CLI rather than the
    launcher argument parser.

    A leading bare word decides on its own. A leading control-global option is
    only a hint: ``--json`` is now ALSO a launcher flag (``jarvis --check
    --json`` emits the preflight as JSON Lines), so routing on the option alone
    would send ``jarvis --json --check`` to the control CLI, which has no such
    command. Instead, look past the leading options for the first bare word and
    route only if that word is a reserved control command.
    """
    if not argv:
        return False
    first = argv[0]
    if first in RESERVED_CONTROL_NAMES:
        return True
    if first not in CONTROL_GLOBAL_OPTIONS:
        return False

    i = 0
    while i < len(argv) and argv[i] in CONTROL_GLOBAL_OPTIONS:
        i += 2 if argv[i] in _VALUE_OPTIONS else 1
    if i >= len(argv):
        # Nothing but control-global options: `jarvis --json` belongs to the
        # control CLI, which answers with its own usage rather than launching
        # the app.
        return True
    return argv[i] in RESERVED_CONTROL_NAMES
