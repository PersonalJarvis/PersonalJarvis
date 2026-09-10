"""Concurrent, read-only subscription probes for the existing agent status API."""

from __future__ import annotations

import asyncio
import importlib
import logging
from typing import Any

log = logging.getLogger(__name__)


def _status(module: str, service: str, args: tuple[Any, ...]) -> Any:
    # Imports and constructors may themselves read credentials or resolve a CLI.
    factory = getattr(importlib.import_module(module), service)
    return factory(*args).status()


async def subscription_statuses(codex_binary: str | None) -> dict[str, Any]:
    """Probe independent logins together; one broken CLI hides only its own row."""
    probes = (
        ("claude", "jarvis.claude_auth", "ClaudeAuthService", ()),
        ("codex", "jarvis.codex_auth", "CodexAuthService", (codex_binary,)),
        ("google", "jarvis.google_cli.auth_service", "GoogleCliAuthService", ()),
        ("grok", "jarvis.grok_build_auth", "GrokBuildAuthService", ()),
    )

    async def probe(key: str, module: str, service: str, args: tuple[Any, ...]) -> tuple[str, Any]:
        try:
            return key, await asyncio.to_thread(_status, module, service, args)
        except Exception:
            log.debug("Agent status: %s login probe unavailable", key, exc_info=True)
            return key, None

    return dict(await asyncio.gather(*(probe(*item) for item in probes)))


def observe_status_result(task: asyncio.Task[dict[str, Any]]) -> None:
    """Consume a failed shared read even if its last HTTP waiter disconnected."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log.warning(
            "Shared agent status read failed", exc_info=(type(error), error, error.__traceback__)
        )
