"""The marketplace install standard: one source for the three install surfaces.

Every installable community entry is offered the same three ways (the
CLI / npx / Prompt pattern the comparable stores converged on, adapted to a
Python app):

- ``cli``    — the curated one-liner for a machine that already has the CLI:
               ``jarvis marketplace install <name>``.
- ``runner`` — the zero-install equivalent of ``npx``: ``uvx`` resolves the
               published ``personal-jarvis`` package into a throwaway
               environment and runs the same curated command, so the line
               works on a machine that never ran ``pip install``.
- ``prompt`` — a plain-language sentence to paste at the assistant, which
               honors it through its CLI tool (the same curated command, so
               the confirmation gate is identical).

These strings are computed HERE and nowhere else. The community REST payload
embeds them per entry and the frontend renders them verbatim — a store that
derived its own command strings client-side could drift from what the CLI
actually accepts, and a shown-but-nonexistent command is the one bug a store
is judged on.
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict

# The Agent Plugins name rule (same as agent_plugins_loader.validate_spec_name
# and community_source._SKILL_NAME_RE). Names matching it consist of
# ``[a-z0-9.-]`` only, so they are safe to embed in a shell line unquoted —
# which is what makes a copy-paste one-liner possible at all.
_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")

EntryKind = Literal["plugin", "skill", "wallpaper"]


class InstallBlock(TypedDict):
    cli: str
    runner: str
    prompt: str


def install_block(name: str, kind: EntryKind) -> InstallBlock | None:
    """The three install strings for one community entry.

    Returns ``None`` when the name would not survive a shell line unquoted.
    Both index validators already enforce the same rule, so a miss here means
    the entry is invalid anyway — no block beats a quoted-and-therefore-scary
    command.
    """
    if not _NAME_RE.fullmatch(name) or ".." in name:
        return None
    cli = f"jarvis marketplace install {name}"
    return {
        "cli": cli,
        "runner": f"uvx --from personal-jarvis {cli}",
        "prompt": f'Install the "{name}" {kind} from the community marketplace.',
    }
