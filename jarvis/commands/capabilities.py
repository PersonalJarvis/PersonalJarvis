"""Runtime capability gate for Command-Registry entries.

Why this exists
---------------
Most registry commands run whenever the app runs. A few are bound to a mode
the user can switch off, and their endpoint then refuses the call outright —
``/api/ultrawiki/*`` answers 409 while UltraWiki mode is off. Handing such a
command to a language model as a callable tool is a promise the app cannot
keep, and the model has no way to see that from the schema.

Measured live 2026-08-24 10:13 (``data/jarvis_desktop.log``): asked what
Jarvis version 2 can do, the realtime model picked ``ultrawiki_ask``, got
``409 Conflict`` in 0.0 s, and stopped — while ``wiki-recall`` sat in the same
tool set and answers that question from the Obsidian vault in 13 ms. The
knowledge WAS there; the tool set pointed at the one door that was locked.

So this module answers one question — "can this capability answer right now?"
— against the LIVE config, which is what the mode switch mutates in place
(``ultrawiki_routes._apply_live``). Reading the live object rather than the
TOML is what makes a mode toggled during a session take effect without a
rebuild of the tool set.

Fail-open on purpose
--------------------
An unreadable config returns ``True``. Losing a working tool because the
probe itself failed is strictly worse than offering one that might refuse:
the refusal path is covered by :func:`steer_for`, which turns the 409 into an
instruction the model can act on in the same turn. Absence of evidence that a
capability is off is not evidence that it is off.

This is the AP-21 rule applied to tools: gate on the capability, never on a
name someone typed twice.
"""

from __future__ import annotations

import logging
from typing import Any, Final

log = logging.getLogger(__name__)

#: Capability name -> the ``[section] enabled`` flag on the live config that
#: decides it. One entry per mode a user can switch off from the UI.
_CONFIG_FLAGS: Final[dict[str, str]] = {"ultrawiki": "ultrawiki"}

#: What the model should do INSTEAD, said in the imperative so it can act on
#: it within the same turn rather than reporting a failure to the user. The
#: classic wiki tools are always loaded and answer the same questions from the
#: Obsidian vault, so an UltraWiki refusal is a redirect, never a dead end.
_STEERS: Final[dict[str, str]] = {
    "ultrawiki": (
        "UltraWiki mode is switched off, so the semantic knowledge base "
        "cannot answer. The normal wiki holds the same knowledge and is "
        "available right now: call wiki-recall to search it, wiki-list to see "
        "what it contains, and wiki-page-read to read one page in full. Do "
        "that instead of reporting a failure, and only say the knowledge base "
        "is unavailable if those tools also come back empty."
    ),
}


def _live_config() -> Any | None:
    """The config object the running WebServer holds, or ``None``.

    Lazy import and attribute-walked: this runs inside a tool call, long after
    boot, and must never be the reason a tool cannot be built (AP-26).
    """
    try:
        from jarvis.core import runtime_refs  # noqa: PLC0415 — lazy, boot-safe

        app = runtime_refs.get_web_app()
        return getattr(getattr(app, "state", None), "config", None)
    except Exception as exc:  # noqa: BLE001 — fail open, see module docstring
        log.debug("capability probe could not reach the live config: %s", exc)
        return None


def is_available(capability: str, *, config: Any | None = None) -> bool:
    """Can ``capability`` answer a call right now?

    An empty or unknown capability name is available — the gate only ever
    subtracts for modes it actually knows how to check.
    """
    name = (capability or "").strip()
    if not name:
        return True
    section = _CONFIG_FLAGS.get(name)
    if section is None:
        log.debug("unknown capability %r treated as available", name)
        return True
    cfg = config if config is not None else _live_config()
    if cfg is None:
        return True  # fail open — steer_for still covers the refusal
    return bool(getattr(getattr(cfg, section, None), "enabled", False))


def steer_for(capability: str) -> str:
    """The instruction to hand a model whose call was refused by the gate.

    Falls back to a generic sentence so a capability added without a steer
    still produces something the model can reason about.
    """
    name = (capability or "").strip()
    return _STEERS.get(
        name,
        f"The {name} mode is switched off, so this command cannot run. Tell "
        "the user it is off rather than retrying.",
    )


def unavailable_capability(command: Any, *, config: Any | None = None) -> str:
    """The capability blocking ``command``, or ``""`` when it can run.

    Takes the command rather than the name so callers stay a one-liner and
    a command with no ``requires`` needs no special-casing at the call site.
    """
    needed = str(getattr(command, "requires", "") or "").strip()
    if not needed or is_available(needed, config=config):
        return ""
    return needed
