"""An agent's own seat as the brain for its background memory work.

Maintainer decision (2026-09-30): an agent's memory updates run on exactly the
provider, model and auth mode that agent's chat runs on. An agent on the
Claude API key with a chosen model reviews on that key and that model; one on
the Claude subscription reviews on that subscription; one on the ChatGPT
subscription on that one. Nothing else is ever asked. Before, reviews walked
a chain of other providers and landed on keys nobody chose for that work: one
agent's own key answered 401 and its reviews fell through to an unrelated
per-token key for a month.

A seat that cannot be resolved, or that fails, raises here and the caller's
own bounded retry applies (the Society review queue, the learning loop's
backoff); after that the work is dropped. There is no fallback to another
provider, another model, a subscription the agent does not sit on, or a key
the agent does not use.

Three callers share this module: the Society turn review
(``society/review.py``), Society skill learning (``society/learning.py``)
and Jarvis' own learning loop (``memory/learning``), whose seat is the Jarvis
lead's: the front-page chat's pick.

"The seat" is read from the same places the chat reads it, so the two cannot
disagree:

* the provider/model pair: ``chat_binding.pair_for`` for an agent;
  ``model_selection.worker_selection`` + ``task_agent.subscription_seat`` for
  Jarvis, exactly as ``AgentChatService.send`` reseats the front-page chat;
* the runner: ``agent_chat.service.resolve_runner``. A vendor CLI seat answers
  through that CLI's brain in structured mode; an API seat through the
  provider's own brain plugin behind the Agents-tab key
  (``get_jarvis_agent_secret``, as ``runner_brain`` does for the chat);
* the instance: ``brain.resolver.resolve_browser_brain``, the resolver of an
  agent's existing access with a structured-output contract.

Everything that may block (a login probe, the keyring, plugin construction)
runs in a worker thread, off the desktop event loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Final

log = logging.getLogger(__name__)

__all__ = [
    "Seat",
    "SeatBrain",
    "SeatCallFailed",
    "SeatUnavailable",
    "agent_brain",
    "agent_seat",
    "jarvis_seat",
    "seat_brain",
]

#: Runners that are a provider's own API behind a key, not a vendor CLI.
_KEYED_RUNNERS: Final[frozenset[str]] = frozenset({"brain", "api"})


class SeatUnavailable(RuntimeError):
    """The seat cannot be built right now; no other provider is asked instead.

    The message is app-authored (never a provider's error body, AP-34).
    """


class SeatCallFailed(RuntimeError):
    """A call on the seat failed. Carries the exception type, never its text:
    a provider error body must not reach a log line (AP-34)."""


@dataclass(frozen=True, slots=True)
class Seat:
    """What a chat runs on: provider, model and the runner behind them."""

    provider: str
    #: ``""`` = the seat's own default (the CLI's, or the provider's
    #: router-tier model, exactly as the chat picks it).
    model: str = ""
    #: ``brain``/``api`` = the provider API behind a key; else a CLI runner id.
    runner: str = "brain"
    #: Whether the Agents-tab key slot applies. ``False`` for an explicit
    #: ``[memory.learning]`` provider override, which uses the provider's
    #: ordinary credential chain.
    agent_key: bool = True

    @property
    def keyed(self) -> bool:
        return self.runner in _KEYED_RUNNERS

    def describe(self) -> str:
        return f"{self.provider}/{self.model or 'default model'}"


def agent_seat(config: Any, agent: Any) -> Seat:
    """The seat of a Society agent's own chat. Blocking: call it in a thread."""
    from jarvis.agent_chat.service import resolve_runner

    from .chat_binding import pair_for

    try:
        provider, model, _effort = pair_for(config, agent)
    except PermissionError as exc:
        raise SeatUnavailable(str(exc) or "no provider can run this agent's chat") from None
    if not provider:
        raise SeatUnavailable("this agent's chat names no provider")
    return Seat(provider, model or "", resolve_runner(provider, surface="society"))


def jarvis_seat(config: Any) -> Seat:
    """The Jarvis lead's seat: what the front-page chat answers on right now.

    The Agents selection (``[brain.worker]``) when one is set, mapped onto its
    subscription seat the way ``AgentChatService.send`` maps it; otherwise the
    Agents tier, which is what a chat with no provider of its own runs on.
    Blocking (the Claude login probe, the credential probes): call it in a
    thread.
    """
    from jarvis.core.model_selection import worker_selection
    from jarvis.core.task_agent import subscription_seat

    selection = worker_selection(config)
    if selection is not None:
        provider, runner = subscription_seat(selection.provider) or (selection.provider, "brain")
        return Seat(provider, selection.model or "", runner)
    from jarvis.agent_chat.service import resolve_runner
    from jarvis.local_models.assistant_session import agents_tier

    tier = agents_tier(config)
    if not tier.ready or not tier.provider:
        raise SeatUnavailable(tier.reason or "no provider can run the Jarvis chat")
    return Seat(tier.provider, tier.model or "", resolve_runner(tier.provider, surface="jarvis"))


def _chat_default_model(config: Any, provider: str) -> str:
    """The model an API seat's chat runs when it names none.

    ``runner_brain`` asks ``BrainManager._fast_model``: the provider's
    configured model, else its router-tier default. Read from the config here
    so a review drained before the brain manager exists never falls to a
    plugin's hardcoded default (for a gateway that can be a paid frontier
    model the person never picked).
    """
    from jarvis.brain.manager import get_tier_default_model

    providers = getattr(getattr(config, "brain", None), "providers", None) or {}
    configured = str(getattr(providers.get(provider), "model", "") or "")
    return configured or get_tier_default_model("router", provider) or ""


def _cli_adapter(seat: Seat, model: str) -> Any:
    """A CLI seat with no brain plugin of its own, adapted like the browser does."""
    from jarvis.agent_chat.catalog import provider_row

    row = provider_row(seat.provider)
    if row is None or row.runner != "grok-cli":
        raise SeatUnavailable(f"the {seat.provider} seat has no background model in this app")
    from jarvis.agent_chat.browser_model import GrokBrowserModel
    from jarvis.brain.usage_meter import meter_brain

    return meter_brain(GrokBrowserModel(model), seat.provider)


def _build(config: Any, seat: Seat, caller: str) -> SeatBrain:
    """Instantiate exactly ``seat``. Blocking: call it in a thread."""
    from jarvis.brain.resolver import resolve_browser_brain

    model = seat.model
    if seat.keyed and not model:
        model = _chat_default_model(config, seat.provider)
    try:
        inner = resolve_browser_brain(config, seat.provider, model, runner=seat.runner)
    except LookupError:  # no brain plugin: a CLI adapter, or SeatUnavailable
        inner = _cli_adapter(seat, model)
    except Exception as exc:  # noqa: BLE001 - reported by type; the text may be a provider's
        raise SeatUnavailable(
            f"the {seat.provider} seat could not be built ({type(exc).__name__})"
        ) from None
    secrets: dict[str, str] = {}
    if seat.keyed and seat.agent_key:
        from jarvis.core.config import get_jarvis_agent_secret

        try:
            secret = get_jarvis_agent_secret(seat.provider)
        except Exception as exc:  # noqa: BLE001 - no dedicated key: the family key answers
            # Same as the chat (``runner_brain._agent_secret``): without an
            # Agents-tab key the provider resolves its ordinary credential.
            log.debug(
                "seat brain: no Agents-tab key for %s (%s)", seat.provider, type(exc).__name__
            )
            secret = None
        if secret:
            secrets[seat.provider] = secret
    return SeatBrain(inner, seat, secrets=secrets, caller=caller)


async def seat_brain(config: Any, seat: Seat, *, caller: str) -> SeatBrain:
    """``seat`` as a Brain, built off the event loop. Raises ``SeatUnavailable``."""
    return await asyncio.to_thread(_build, config, seat, caller)


async def agent_brain(config: Any, agent: Any, *, caller: str) -> SeatBrain:
    """A Society agent's own seat as a Brain. Raises ``SeatUnavailable``."""

    def resolve() -> SeatBrain:
        return _build(config, agent_seat(config, agent), caller)

    return await asyncio.to_thread(resolve)


class SeatBrain:
    """One seat's brain: every call runs under that seat's credential and is
    written to the usage ledger under ``caller``.

    The credential override is entered around each step of the stream, never
    across a ``yield``, so it is always reset in the context that set it,
    however the consumer ends the stream. Attributes other than ``complete``
    forward to the seat's own brain (``name``, ``context_window``, ...).
    """

    __slots__ = ("_caller", "_inner", "_secrets", "seat")

    def __init__(
        self, inner: Any, seat: Seat, *, secrets: Mapping[str, str], caller: str
    ) -> None:
        self._inner = inner
        self.seat = seat
        self._secrets = dict(secrets)
        self._caller = caller

    def __getattr__(self, name: str) -> Any:
        if name in SeatBrain.__slots__ or name.startswith("__"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    def __repr__(self) -> str:  # pragma: no cover - logging nicety
        return f"SeatBrain({self.seat.describe()}, runner={self.seat.runner})"

    @contextmanager
    def _scope(self) -> Iterator[None]:
        from jarvis.core.config import override_provider_secrets
        from jarvis.costs.ledger import usage_context

        # Exactly this seat's credentials: an override inherited from whatever
        # task scheduled this work never reaches the call.
        with usage_context(self._caller), override_provider_secrets(self._secrets):
            yield

    def complete(self, request: Any) -> AsyncIterator[Any]:
        return self._stream(request)

    async def _stream(self, request: Any) -> AsyncIterator[Any]:
        iterator: Any = None
        try:
            with self._scope():
                iterator = self._inner.complete(request).__aiter__()
            while True:
                with self._scope():
                    try:
                        delta = await iterator.__anext__()
                    except StopAsyncIteration:  # the seat's stream ended normally
                        return
                yield delta
        except Exception as exc:  # noqa: BLE001 - re-raised without the provider's text
            raise SeatCallFailed(
                f"the {self.seat.describe()} seat failed ({type(exc).__name__})"
            ) from None
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                await close()
