"""Who writes an Agentic IDE brief — one order, both call sites.

The prompt composer and the work splitter each used to carry their own copy of
this decision. They must never diverge: a user who moves briefs onto their
subscription and then finds the splitter still billing an API key has been told
one thing and charged for another.

The order, and why each rung is where it is:

1. **A pin is a pin.** ``api`` never touches a subscription — a user who chose
   the API tier must not have their plan quota spent behind their back — and a
   named provider or ``subscription`` never quietly falls back to the API key,
   which is the same surprise in the other direction. An unhonourable pin
   degrades to the caller's deterministic layer, which is at least honest.
2. **``auto`` prefers a connected subscription**, because it is work the user
   has already paid for, then crosses to the API tier if none answers.
3. **Nothing qualifies → ``(None, "")``**, and the caller says so.

Never raises. Every failure here costs a rougher prompt; none of them may cost
the user their instruction.
"""
from __future__ import annotations

from typing import Any

from loguru import logger


def _load_config() -> Any:
    from jarvis.core.config import load_config

    return load_config()


def _subscription(config: Any, cli_timeout_s: float | None) -> Any:
    from jarvis.brain.resolver import resolve_subscription_brain

    return resolve_subscription_brain(config, cli_timeout_s=cli_timeout_s)


def _quality(config: Any) -> Any:
    from jarvis.brain.resolver import resolve_quality_brain

    return resolve_quality_brain(config)


def _configured_choice(config: Any) -> str:
    raw = getattr(getattr(config, "agentic_ide", None), "prompt_writer", "auto")
    return str(raw or "auto").strip() or "auto"


def resolve_writer(*, cli_timeout_s: float | None = None) -> tuple[Any | None, str]:
    """The brain that writes the brief, and where it came from.

    ``cli_timeout_s`` is the caller's own budget, forwarded so a CLI brain's
    internal voice-tier cap cannot kill a call the caller is still waiting on.

    Returns ``(None, "")`` whenever nothing qualifies — the caller then uses its
    deterministic layer and reports that honestly.
    """
    try:
        config = _load_config()
        choice = _configured_choice(config)
    except Exception:  # noqa: BLE001 - resolution must never break a turn
        logger.info("Agentic IDE writer could not be resolved", exc_info=True)
        return None, ""

    if choice == "api":
        return _api_writer(config)

    subscription = _try_subscription(config, cli_timeout_s)
    if subscription is not None:
        name = getattr(subscription, "name", None) or choice
        return subscription, f"subscription:{name}"

    if choice != "auto":
        # An explicit pin that cannot be honoured degrades openly rather than
        # quietly billing a key the user did not choose.
        logger.info(
            "Agentic IDE writer pinned to '{}' but it is unreachable — using the "
            "deterministic prompt rather than silently billing another provider",
            choice,
        )
        return None, ""

    return _api_writer(config)


def _try_subscription(config: Any, cli_timeout_s: float | None) -> Any | None:
    """A connected subscription brain, or None. A failure here is not fatal."""
    try:
        return _subscription(config, cli_timeout_s)
    except Exception:  # noqa: BLE001 - a broken CLI probe must not cost the API path
        logger.info("Agentic IDE subscription writer unavailable", exc_info=True)
        return None


def _api_writer(config: Any) -> tuple[Any | None, str]:
    """The API-billed quality tier, or ``(None, "")``."""
    try:
        brain = _quality(config)
    except Exception:  # noqa: BLE001
        logger.info("Agentic IDE quality writer unavailable", exc_info=True)
        return None, ""
    return (brain, "api") if brain is not None else (None, "")


__all__ = ["resolve_writer"]
