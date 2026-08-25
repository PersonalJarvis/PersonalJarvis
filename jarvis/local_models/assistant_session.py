"""Session policy of the setup assistant: one chat per install, on the Agents tier.

The assistant is a ``local-models`` agent-chat session. It runs on the
Jarvis-Agents tier (``[brain.worker]`` provider / model, the fallback pair
when the primary is unknown to the chat) — never on the voice brain, so a
person whose voice runs on Ollama still gets a capable cloud model to set
Ollama up. There is ONE session per install: the routes reuse the newest
``local-models`` session and re-create it only when the worker tier moved.

No usable Agents-tier credential → an honest refusal (:data:`NOT_READY`),
never a silent fallback onto another tier.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

log = logging.getLogger(__name__)

__all__ = [
    "NOT_READY",
    "SURFACE",
    "AgentsTier",
    "agents_tier",
    "ensure_session",
    "session_state",
]

SURFACE: Final[str] = "local-models"
NOT_READY: Final[str] = "Connect the Jarvis Agents tier first — the setup assistant runs on it."
#: The tier is connected but every provider in its chain drives a vendor CLI
#: on a flat prompt (Antigravity, for one) and cannot call the assistant's
#: tools; a fallback with an API key fixes it.
NO_TOOLS: Final[str] = (
    "The Jarvis Agents tier cannot call tools here (a CLI-only provider such as "
    "Antigravity). Set a tool-capable fallback with an API key — Gemini, OpenAI "
    "or OpenRouter — on the Agents tier and try again."
)


@dataclass(frozen=True, slots=True)
class AgentsTier:
    provider: str
    model: str
    ready: bool
    reason: str


def _known(provider: str) -> bool:
    from jarvis.agent_chat.catalog import provider_row
    from jarvis.agent_chat.runner_api import supports_api_runner

    return provider_row(provider) is not None or supports_api_runner(provider)


def _tool_capable(provider: str) -> bool:
    """Whether ``provider``'s brain plugin can call tools inside the brain loop.

    Read from the plugin itself (``can_call_tools`` / ``supports_tools``) via
    the ``jarvis.brain`` entry point, never from the name (AP-21). A plugin
    that cannot be loaded here answers True: the turn then fails honestly
    instead of a healthy provider being skipped by a probe glitch.
    """
    from importlib.metadata import entry_points

    try:
        eps = [ep for ep in entry_points(group="jarvis.brain") if ep.name == provider]
        if not eps:
            return True
        cls = eps[0].load()
        try:
            brain = cls()
        except TypeError:
            brain = cls(model=None)
        probe = getattr(brain, "can_call_tools", None)
        if callable(probe):
            return bool(probe())
        return bool(getattr(brain, "supports_tools", True))
    except Exception:  # noqa: BLE001 — a probe glitch must not hide a working provider
        log.debug("agents tier: tool-capability probe for %s failed", provider, exc_info=True)
        return True


def _default_usable(provider: str) -> bool:
    from jarvis.core.config import get_jarvis_agent_secret

    try:
        return bool(get_jarvis_agent_secret(provider))
    except Exception:  # noqa: BLE001 — no credential readable counts as none
        log.debug("agents tier: credential lookup for %s failed", provider, exc_info=True)
        return False


def agents_tier(
    cfg: Any,
    *,
    usable: Callable[[str], bool] | None = None,
    tool_capable: Callable[[str], bool] | None = None,
) -> AgentsTier:
    """The pair the assistant runs on and whether it can run right now.

    Walks the worker chain (primary, fallback, fallback 2) and takes the first
    provider that is known, can call tools and has a credential. ``usable`` is
    the credential / login probe (the routes pass the provider-agnostic worker
    check the API-keys page uses; the default reads the Agents-tier secret);
    ``tool_capable`` defaults to asking the brain plugin.
    """
    brain = getattr(cfg, "brain", None)
    worker = getattr(brain, "worker", None)
    if worker is None:
        return AgentsTier("", "", False, NOT_READY)
    chain = [
        (
            str(getattr(worker, "provider", "") or "").strip(),
            str(getattr(worker, "model", "") or "").strip(),
        ),
        (
            str(getattr(worker, "fallback_provider", "") or "").strip(),
            str(getattr(worker, "fallback_model", "") or "").strip(),
        ),
        (
            str(getattr(worker, "fallback_provider_2", "") or "").strip(),
            str(getattr(worker, "fallback_model_2", "") or "").strip(),
        ),
    ]
    known = [(p, m) for p, m in chain if p and _known(p)]
    if not known:
        first = next((p for p, _m in chain if p), "")
        return AgentsTier(first, "", False, NOT_READY)
    can_tools = tool_capable or _tool_capable
    handy = [(p, m) for p, m in known if can_tools(p)]
    if not handy:
        return AgentsTier(known[0][0], known[0][1], False, NO_TOOLS)
    probe = usable or _default_usable
    for provider, model in handy:
        try:
            ok = bool(probe(provider))
        except Exception:  # noqa: BLE001 — a failing probe is "not ready", with the reason logged
            log.info("agents tier: usability probe for %s failed", provider, exc_info=True)
            ok = False
        if ok:
            return AgentsTier(provider, model, True, "")
    return AgentsTier(handy[0][0], handy[0][1], False, NOT_READY)


def _current(svc: Any) -> Any | None:
    sessions = svc.store.list_sessions(limit=1, surface=SURFACE)
    return sessions[0] if sessions else None


def session_state(svc: Any, cfg: Any, *, usable: Callable[[str], bool] | None = None) -> dict:
    """``GET /session``: the session id (or null), the pair, readiness, reason."""
    tier = agents_tier(cfg, usable=usable)
    current = _current(svc)
    matches = (
        current is not None
        and current.provider == tier.provider
        and (not tier.model or current.model == tier.model)
    )
    return {
        "session_id": current.session_id if matches and current is not None else None,
        "surface": SURFACE,
        "provider": tier.provider,
        "model": tier.model,
        "ready": tier.ready,
        "reason": tier.reason,
    }


def ensure_session(svc: Any, cfg: Any, *, usable: Callable[[str], bool] | None = None) -> Any:
    """The one ``local-models`` session, created or re-created for the current pair.

    Raises ``PermissionError(NOT_READY)`` when the Agents tier cannot run.
    """
    tier = agents_tier(cfg, usable=usable)
    if not tier.ready:
        raise PermissionError(tier.reason or NOT_READY)
    current = _current(svc)
    if (
        current is not None
        and current.provider == tier.provider
        and (not tier.model or current.model == tier.model)
    ):
        return current
    return svc.create_session(
        provider=tier.provider,
        model=tier.model,
        title="Local models setup",
        surface=SURFACE,
    )
