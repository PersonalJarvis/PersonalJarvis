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
from collections.abc import Awaitable, Callable
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
NOT_READY: Final[str] = (
    "Pick a Tool Model with an API key first (API Keys → Tool Model) — the setup "
    "assistant runs on it, billed through that key."
)
#: The tier is connected but every provider in its chain drives a vendor CLI
#: on a flat prompt (Antigravity, for one) and cannot call the assistant's
#: tools; a fallback with an API key fixes it.
NO_TOOLS: Final[str] = (
    "The picked Tool Model cannot call tools here (a CLI-only provider such as "
    "Antigravity). Pick an API provider — Gemini, OpenAI, Vertex or OpenRouter — "
    "under API Keys → Tool Model and try again."
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
    """A credential exists: the provider's own key (the Tool Model bills
    through it) or the Agents-tier secret for the fallback chain."""
    from jarvis.core.config import get_jarvis_agent_secret

    try:
        if get_jarvis_agent_secret(provider):
            return True
    except Exception:  # noqa: BLE001 — no credential readable counts as none
        log.debug("agents tier: credential lookup for %s failed", provider, exc_info=True)
    try:
        from jarvis.brain.app_control import is_credential_present
        from jarvis.ui.web.provider_spec import get_spec

        spec = get_spec(provider)
        return spec is not None and bool(is_credential_present(spec))
    except Exception:  # noqa: BLE001 — same: unreadable is "none"
        log.debug("agents tier: main credential lookup for %s failed", provider, exc_info=True)
        return False


def _tool_model_pair(cfg: Any) -> tuple[str, str] | None:
    """The user's Tool Model pick, ``None`` when it is ``auto``.

    The same reader the Tool Model tab and the IDE's prompt writer use
    (``resolver._tool_model_selection``), so the assistant runs on exactly the
    model the user pointed that tab at — and is billed through that key.
    """
    from jarvis.brain.resolver import _tool_model_selection

    try:
        provider, model = _tool_model_selection(cfg)
    except Exception:  # noqa: BLE001 — a malformed section reads as unset, logged
        log.debug("agents tier: tool-model selection unreadable", exc_info=True)
        return None
    if not provider or provider == "auto":
        return None
    return provider, str(model or "").strip()


def _chain(cfg: Any) -> list[tuple[str, str]]:
    """The Tool Model first; the Agents-tier chain only as a fallback."""
    out: list[tuple[str, str]] = []
    pinned = _tool_model_pair(cfg)
    if pinned is not None:
        out.append(pinned)
    brain = getattr(cfg, "brain", None)
    worker = getattr(brain, "worker", None)
    if worker is None:
        return out
    pairs = [
        ("provider", "model"),
        ("fallback_provider", "fallback_model"),
        ("fallback_provider_2", "fallback_model_2"),
    ]
    for p_key, m_key in pairs:
        provider = str(getattr(worker, p_key, "") or "").strip()
        model = str(getattr(worker, m_key, "") or "").strip()
        if provider and (provider, model) not in out:
            out.append((provider, model))
    return out


def chain_candidates(
    cfg: Any,
    *,
    usable: Callable[[str], bool] | None = None,
    tool_capable: Callable[[str], bool] | None = None,
) -> list[tuple[str, str]]:
    """The worker pairs worth a live probe: known, tool-capable, credentialed."""
    can_tools = tool_capable or _tool_capable
    probe = usable or _default_usable
    out: list[tuple[str, str]] = []
    for provider, model in _chain(cfg):
        if not _known(provider) or not can_tools(provider):
            continue
        try:
            if probe(provider):
                out.append((provider, model))
        except Exception:  # noqa: BLE001 — a failing probe is "not ready", with the reason logged
            log.info("agents tier: usability probe for %s failed", provider, exc_info=True)
    return out


#: (provider, model) -> (ok, reason, monotonic) — one real call per pair per
#: ten minutes; a run and the session read share it.
_LIVE_CACHE: dict[tuple[str, str], tuple[bool, str, float]] = {}
_LIVE_TTL_S: Final[float] = 600.0


def _reset_for_tests() -> None:
    _LIVE_CACHE.clear()


async def probe_live(
    cfg: Any,
    pairs: list[tuple[str, str]],
    *,
    timeout_s: float = 20.0,
    tester: Callable[..., Awaitable[Any]] | None = None,
) -> dict[tuple[str, str], tuple[bool, str]]:
    """One real one-token generation per pair, classified like the API-keys
    page's "Test" (``provider_test``): a key that authenticates but is refused
    for quota or billing is NOT usable here, and the sentence says so."""
    import time

    from jarvis.brain import provider_test
    from jarvis.ui.web.provider_spec import get_spec

    out: dict[tuple[str, str], tuple[bool, str]] = {}
    now = time.monotonic()
    for provider, model in pairs:
        hit = _LIVE_CACHE.get((provider, model))
        if hit is not None and now - hit[2] < _LIVE_TTL_S:
            out[(provider, model)] = (hit[0], hit[1])
            continue
        spec = get_spec(provider)
        if spec is None:
            out[(provider, model)] = (False, f"{provider} is not a provider this build knows.")
            continue
        run = tester or provider_test.run_provider_test
        try:
            result = await run(spec, cfg, model=model or None, timeout_s=timeout_s)
            ok = result.status == provider_test.OK
            reason = "" if ok else _plain_reason(provider, model, result)
        except Exception as exc:  # noqa: BLE001 — a probe crash is "not usable", logged
            log.warning("agents tier: live probe of %s/%s crashed: %s", provider, model, exc)
            ok, reason = False, f"{provider} ({model}): probe failed — {exc}"
        _LIVE_CACHE[(provider, model)] = (ok, reason, time.monotonic())
        out[(provider, model)] = (ok, reason)
    return out


_PLAIN_BY_STATUS: Final[dict[str, str]] = {
    "no_credits": "the account has no credit or quota left for this model",
    "rate_limited": "the provider is rate-limiting this key right now",
    "bad_key": "the key was refused",
    "model_unavailable": "the provider no longer offers this model",
    "unreachable": "the provider could not be reached",
    "not_configured": "no key is stored for it",
}


def _plain_reason(provider: str, model: str, result: Any) -> str:
    """One readable sentence, never a dumped error body.

    The status vocabulary is the API-keys page's; the provider's own text is
    kept only when it is short and free of JSON braces (a 404 body is not
    something a person should have to read in a status line).
    """
    status = str(getattr(result, "status", "") or "error")
    head = f"{provider} ({model or 'default model'}): "
    plain = _PLAIN_BY_STATUS.get(status)
    detail = str(getattr(result, "detail", "") or "").strip()
    if plain:
        return head + plain + "."
    if detail and "{" not in detail and len(detail) <= 140:
        return head + detail
    return head + status.replace("_", " ") + "."


def agents_tier(
    cfg: Any,
    *,
    usable: Callable[[str], bool] | None = None,
    tool_capable: Callable[[str], bool] | None = None,
    live: dict[tuple[str, str], tuple[bool, str]] | None = None,
) -> AgentsTier:
    """The pair the assistant runs on and whether it can run right now.

    Walks the worker chain (primary, fallback, fallback 2) and takes the first
    provider that is known, can call tools, has a credential and — when a
    ``live`` map from :func:`probe_live` is given — answered a real call.
    ``usable`` is the credential / login probe (the routes pass the
    provider-agnostic worker check the API-keys page uses; the default reads
    the Agents-tier secret); ``tool_capable`` defaults to asking the plugin.
    """
    chain = _chain(cfg)
    if not chain:
        return AgentsTier("", "", False, NOT_READY)
    known = [(p, m) for p, m in chain if _known(p)]
    if not known:
        return AgentsTier(chain[0][0], "", False, NOT_READY)
    can_tools = tool_capable or _tool_capable
    handy = [(p, m) for p, m in known if can_tools(p)]
    if not handy:
        return AgentsTier(known[0][0], known[0][1], False, NO_TOOLS)
    probe = usable or _default_usable
    last_reason = NOT_READY
    for provider, model in handy:
        try:
            ok = bool(probe(provider))
        except Exception:  # noqa: BLE001 — a failing probe is "not ready", with the reason logged
            log.info("agents tier: usability probe for %s failed", provider, exc_info=True)
            ok = False
        if not ok:
            continue
        if live is not None and (provider, model) in live:
            alive, reason = live[(provider, model)]
            if not alive:
                last_reason = reason or NOT_READY
                continue
        return AgentsTier(provider, model, True, "")
    return AgentsTier(handy[0][0], handy[0][1], False, last_reason)


def _current(svc: Any) -> Any | None:
    sessions = svc.store.list_sessions(limit=1, surface=SURFACE)
    return sessions[0] if sessions else None


def session_state(
    svc: Any,
    cfg: Any,
    *,
    usable: Callable[[str], bool] | None = None,
    live: dict[tuple[str, str], tuple[bool, str]] | None = None,
) -> dict:
    """``GET /session``: the session id (or null), the pair, readiness, reason."""
    tier = agents_tier(cfg, usable=usable, live=live)
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


def ensure_session(
    svc: Any,
    cfg: Any,
    *,
    usable: Callable[[str], bool] | None = None,
    live: dict[tuple[str, str], tuple[bool, str]] | None = None,
) -> Any:
    """The one ``local-models`` session, created or re-created for the current pair.

    Raises ``PermissionError(reason)`` when the Agents tier cannot run.
    """
    tier = agents_tier(cfg, usable=usable, live=live)
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
