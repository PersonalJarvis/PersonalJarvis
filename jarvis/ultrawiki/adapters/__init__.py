"""Pull adapters — the readers that turn a connected integration into items.

The plugin bridge is a gateway: it knows about consent, scheduling and
checkpoints, but nothing about any particular product's API. An adapter is the
missing half — ``(ctx, checkpoint) -> AsyncIterator[RawItem]`` for exactly one
integration id.

Until this package existed, EVERY connected app reported "pull adapter
pending": seven integrations that a user had genuinely connected, that the
Plugins store honestly called connected, and that could never contribute a
single item. That gap is what made a knowledge base look broken while nothing
was wrong.

Registration is explicit and late (:func:`register_builtin_adapters`), never a
side effect of importing this package — an adapter reaches for credentials and
network clients, and the plugin bridge must stay importable on a machine that
has neither. The runtime calls it once, lazily, off the boot path (AP-26).
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

__all__ = ["register_builtin_adapters", "registered_adapter_ids"]

#: Guard so a repeated call is a no-op rather than a re-registration storm.
_REGISTERED = False


def register_builtin_adapters() -> list[str]:
    """Register every adapter shipped with Jarvis. Idempotent, never raises.

    A broken or absent adapter must shorten the list, never stop the others
    from registering — one product's API client failing to import is not a
    reason for the whole bridge to lose its readers.
    """
    global _REGISTERED
    from jarvis.ultrawiki.connectors import plugin_bridge  # noqa: PLC0415 — lazy

    if _REGISTERED:
        return registered_adapter_ids()

    registered: list[str] = []
    try:
        from jarvis.ultrawiki.adapters.github import (  # noqa: PLC0415 — lazy
            INTEGRATION_ID,
            github_pull_adapter,
        )

        plugin_bridge.register_pull_adapter(INTEGRATION_ID, github_pull_adapter)
        registered.append(INTEGRATION_ID)
    except Exception as exc:  # noqa: BLE001 — one bad adapter must not sink the rest
        log.warning("UltraWiki: the GitHub pull adapter could not register: %s", exc)

    _REGISTERED = True
    if registered:
        log.info("UltraWiki pull adapters registered: %s", ", ".join(registered))
    return registered


def registered_adapter_ids() -> list[str]:
    """Integration ids that currently have a reader (sorted, for display)."""
    from jarvis.ultrawiki.connectors import plugin_bridge  # noqa: PLC0415 — lazy

    return sorted(plugin_bridge._PULL_ADAPTERS)  # noqa: SLF001 — documented seam


def reset_for_tests() -> None:
    """Drop the idempotence guard so a test can re-register deliberately."""
    global _REGISTERED
    _REGISTERED = False
