"""Who writes an Agentic IDE brief — one order, both call sites.

The prompt composer and the work splitter each used to carry their own copy of
this decision. They must never diverge: a user who moves briefs onto their
subscription and then finds the splitter still billing an API key has been told
one thing and charged for another.

The order, and why each rung is where it is:

1. **A pin is a pin.** ``api`` never touches a subscription — a user who chose
   the API tier must not have their plan quota spent behind their back — and a
   named provider, ``tool_model`` or ``subscription`` never quietly falls back
   to the API key, which is the same surprise in the other direction. An
   unhonourable pin degrades to the caller's deterministic layer, which is at
   least honest.
2. **``tool_model`` means the model the user already chose**, in the Tool Model
   tab, for work the assistant does on its own behalf. It resolves through
   ``resolve_tool_model_brain`` and nowhere else: a Tool Model pin that quietly
   landed on a coding CLI would make the setting meaningless.
3. **``auto`` prefers a connected subscription**, because it is work the user
   has already paid for, then crosses to the API tier if none answers.
4. **Nothing qualifies → ``(None, "")``**, and the caller says so.

The distinction users actually make here is "an API key I pay per token for"
versus "a coding CLI I already subscribe to" — and which CLI that is differs
per install. Nothing in this module names a vendor: the subscription rung asks
the provider cards which providers bill that way (AP-21/22), so whichever CLI
the user connected is the one that writes.

**Choosing a writer and surviving one are different questions**
(``resolve_rescue_writer``). The order above answers "who should write this",
and a pin that cannot be honoured there degrades openly — that half is
unchanged. What it does NOT answer is what happens when the chosen writer
accepts the job and dies inside it: a depleted key answering 429, a CLI
returning its own flag error on stdout, a provider timing out. Measured on the
dev box 2026-08-02: the writer resolved to a key whose prepayment credits were
gone, so EVERY composition ended on the raw spoken sentence while a signed-in
subscription sat unused. Treating a runtime failure as "the user gets no
brief" is the single-provider brick AP-22 exists to forbid, so a dead call
crosses to the next rung instead. That is not a silent substitution: the
composer names the writer that ended up writing, and the rung that died.

Never raises. Every failure here costs a rougher prompt; none of them may cost
the user their instruction.
"""

from __future__ import annotations

from collections.abc import Sequence
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


def _tool_model(config: Any) -> Any:
    from jarvis.brain.resolver import resolve_tool_model_brain

    return resolve_tool_model_brain(config)


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

    if choice == "tool_model":
        return _tool_model_writer(config)

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


# The rungs a dead call may cross to, best first. Deliberately the same three
# the pins name, so "who writes" and "who rescues" can never drift apart.
_RESCUE_ORDER = ("tool_model", "subscription", "api")


def resolve_rescue_writer(
    *, cli_timeout_s: float | None = None, exclude: Sequence[str] = ()
) -> tuple[Any | None, str]:
    """The next writer to try after one accepted the job and failed inside it.

    ``exclude`` is the sources already tried, in the ``rung`` or ``rung:name``
    shape ``resolve_writer`` returns; only the rung part is compared, because
    re-probing the same tier with a different model name would just spend the
    same dead credential twice.

    Returns ``(None, "")`` when nothing is left, and the caller then uses its
    deterministic layer. Only reached AFTER a real failure — an unhonourable
    pin still degrades at resolution time rather than quietly landing on a
    provider the user did not choose.
    """
    tried = {str(source).split(":", 1)[0] for source in exclude if source}
    try:
        config = _load_config()
    except Exception:  # noqa: BLE001 - a rescue never breaks a turn
        logger.info("Agentic IDE rescue writer could not be resolved", exc_info=True)
        return None, ""

    for rung in _RESCUE_ORDER:
        if rung in tried:
            continue
        if rung == "tool_model":
            brain, source = _tool_model_writer(config)
        elif rung == "subscription":
            brain = _try_subscription(config, cli_timeout_s)
            source = f"subscription:{getattr(brain, 'name', None) or 'subscription'}"
        else:
            brain, source = _api_writer(config)
        if brain is not None:
            logger.info("Agentic IDE brief falls across to {} after a failed writer", source)
            return brain, source
    return None, ""


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


def _tool_model_writer(config: Any) -> tuple[Any | None, str]:
    """The user's configured Tool Model, or ``(None, "")``.

    Does NOT fall through to the quality tier when the Tool Model is unset or
    unreachable. "Use the model I picked" and "use whatever is left" are
    different answers, and a user who chose this option to stop briefs being
    written by a coding CLI would be no better off if it silently landed on a
    third model instead. The caller degrades to its deterministic prompt and
    reports that honestly.
    """
    try:
        brain = _tool_model(config)
    except Exception:  # noqa: BLE001 - a broken selection costs the brief, not the turn
        logger.info("Agentic IDE tool-model writer unavailable", exc_info=True)
        return None, ""
    if brain is None:
        logger.info(
            "Agentic IDE writer pinned to the Tool Model, but no usable Tool "
            "Model is configured — using the deterministic prompt rather than "
            "silently writing with another model"
        )
        return None, ""
    name = getattr(brain, "name", None) or "tool model"
    return brain, f"tool_model:{name}"


__all__ = ["resolve_rescue_writer", "resolve_writer"]
