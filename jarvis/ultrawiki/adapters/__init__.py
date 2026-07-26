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

#: Every adapter shipped with Jarvis: (integration id, module, factory attr).
#: One row per catalog entry with a reader — the parity test asserts this
#: table covers every roster id whose reader exists, so a new adapter that
#: forgets its row here fails CI instead of silently staying "pending".
_BUILTIN_ADAPTERS: tuple[tuple[str, str, str], ...] = (
    ("plugin:github", "jarvis.ultrawiki.adapters.github", "github_pull_adapter"),
    ("plugin:slack", "jarvis.ultrawiki.adapters.slack", "slack_pull_adapter"),
    ("plugin:gmail", "jarvis.ultrawiki.adapters.google_mail", "gmail_pull_adapter"),
    (
        "plugin:google_calendar",
        "jarvis.ultrawiki.adapters.google_calendar",
        "google_calendar_pull_adapter",
    ),
    (
        "plugin:google_drive",
        "jarvis.ultrawiki.adapters.google_drive",
        "google_drive_pull_adapter",
    ),
    ("plugin:notion", "jarvis.ultrawiki.adapters.notion", "notion_pull_adapter"),
    ("plugin:asana", "jarvis.ultrawiki.adapters.asana", "asana_pull_adapter"),
    ("plugin:linear", "jarvis.ultrawiki.adapters.linear", "linear_pull_adapter"),
    ("plugin:todoist", "jarvis.ultrawiki.adapters.todoist", "todoist_pull_adapter"),
    ("plugin:clickup", "jarvis.ultrawiki.adapters.clickup", "clickup_pull_adapter"),
    ("plugin:dropbox", "jarvis.ultrawiki.adapters.dropbox", "dropbox_pull_adapter"),
    (
        "plugin:airtable",
        "jarvis.ultrawiki.adapters.airtable",
        "airtable_pull_adapter",
    ),
    ("plugin:discord", "jarvis.ultrawiki.adapters.discord", "discord_pull_adapter"),
    (
        "plugin:telegram",
        "jarvis.ultrawiki.adapters.telegram",
        "telegram_pull_adapter",
    ),
)


def register_builtin_adapters() -> list[str]:
    """Register every adapter shipped with Jarvis. Idempotent, never raises.

    A broken or absent adapter must shorten the list, never stop the others
    from registering — one product's API client failing to import is not a
    reason for the whole bridge to lose its readers.
    """
    global _REGISTERED
    import importlib  # noqa: PLC0415 — lazy

    from jarvis.ultrawiki.connectors import plugin_bridge  # noqa: PLC0415 — lazy

    if _REGISTERED:
        return registered_adapter_ids()

    registered: list[str] = []
    for integration_id, module_path, factory_name in _BUILTIN_ADAPTERS:
        try:
            module = importlib.import_module(module_path)
            factory = getattr(module, factory_name)
            declared = getattr(module, "INTEGRATION_ID", integration_id)
            if declared != integration_id:
                raise RuntimeError(
                    f"module declares {declared!r}, table says {integration_id!r}"
                )
            plugin_bridge.register_pull_adapter(integration_id, factory)
            registered.append(integration_id)
        except Exception as exc:  # noqa: BLE001 — one bad adapter must not sink the rest
            log.warning(
                "UltraWiki: pull adapter %s could not register: %s",
                integration_id,
                exc,
            )

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
