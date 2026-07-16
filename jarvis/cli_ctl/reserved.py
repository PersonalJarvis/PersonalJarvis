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
    }
)

# Control-global options accepted before a subcommand (root callback options).
# When one of these is the first argv token we also route to the control CLI.
CONTROL_GLOBAL_OPTIONS: frozenset[str] = frozenset({"--json", "--url", "--key"})


def is_control_invocation(argv: list[str]) -> bool:
    """True if ``argv`` should be handled by the control CLI rather than the
    launcher argument parser."""
    if not argv:
        return False
    first = argv[0]
    return first in RESERVED_CONTROL_NAMES or first in CONTROL_GLOBAL_OPTIONS
