"""Construct isolated streaming providers for the Swarm composition root.

Selection follows the configured agent tier, then configured cross-family
fallbacks, then another credential-bearing tool-capable provider. No provider
settings are changed and subscription agents that execute their own tools are
excluded unless their transport explicitly guarantees scoped execution only.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from email.utils import parsedate_to_datetime
from typing import Any

from jarvis.core.config import PROVIDER_SECRET_CANDIDATES, JarvisConfig, get_jarvis_agent_secret
from jarvis.core.protocols import Brain
from jarvis.core.swarm_types import ProviderUnavailableError

from .provider_registry import BrainProviderRegistry

log = logging.getLogger(__name__)


def _retry_after(exc: BaseException, now: float) -> float | None:
    """Read HTTP and Google RPC retry hints without depending on an SDK class."""
    delays: list[float] = []

    def seconds(value: Any) -> None:
        try:
            delay = float(str(value).removesuffix("s"))
        except (ValueError, TypeError):
            return  # An absent/malformed hint leaves the jittered backoff in charge.
        if math.isfinite(delay) and delay >= 0:
            delays.append(delay)

    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is not None and callable(getattr(headers, "get", None)):
        hint = headers.get("retry-after")
        if hint is not None:
            seconds(hint)
            if not delays:
                try:
                    delay = parsedate_to_datetime(str(hint)).timestamp() - now
                    if math.isfinite(delay):
                        delays.append(max(0.0, delay))
                except (ValueError, TypeError, OverflowError):
                    log.debug("Swarm provider sent an invalid Retry-After date")

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 8:
            return  # Provider payloads are untrusted and cannot cause unbounded recursion.
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "retryDelay":
                    seconds(item)
                elif key in {"error", "details", "message"}:
                    visit(item, depth + 1)
        elif isinstance(value, list):
            for item in value[:32]:
                visit(item, depth + 1)
        elif isinstance(value, str):
            match = re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)s", value, re.IGNORECASE)
            if match:
                seconds(match.group(1))
            if value.lstrip().startswith("{"):
                try:
                    visit(json.loads(value), depth + 1)
                except (ValueError, RecursionError):
                    log.debug("Swarm provider retry message was not valid JSON")

    visit(getattr(exc, "response_json", None))
    visit(getattr(exc, "body", None))
    visit(getattr(exc, "message", None))
    return max(delays) if delays else None


@dataclass(frozen=True, slots=True)
class SwarmProvider:
    brain: Brain
    provider: str
    model: str
    microusd_per_token: Decimal | None
    _clients: list[Any] = field(default_factory=list, repr=False, compare=False)
    _closed: bool = field(default=False, init=False, repr=False, compare=False)
    offload: Callable[..., Awaitable[Any]] = field(
        default=asyncio.to_thread, repr=False, compare=False
    )

    def cost_bound(self, tokens: int) -> int | None:
        if self.microusd_per_token is None:
            return None
        return int((self.microusd_per_token * tokens).to_integral_value(rounding=ROUND_CEILING))

    def track_clients(self) -> None:
        """Retain clients replaced by a transport fallback until worker cleanup."""
        for attr in ("_client", "_async_client"):
            client = getattr(self.brain, attr, None)
            if client is not None and not any(client is old for old in self._clients):
                self._clients.append(client)

    def own_request_retries(self) -> None:
        """Disable hidden SDK retries; each Swarm retry needs its own reservation."""
        self.track_clients()
        for client in self._clients:
            if isinstance(getattr(client, "max_retries", None), int):
                client.max_retries = 0
            # google-genai builds independent sync/async Tenacity controllers.
            # Change their stop policy before the isolated client is ever used;
            # no SDK import or provider-name gate is needed.
            api = getattr(client, "_api_client", None)
            for attr in ("_retry", "_async_retry"):
                retry = getattr(api, attr, None)
                if callable(retry) and hasattr(retry, "stop"):
                    retry.stop = lambda state: True

    async def aclose(self) -> None:
        """Close this attempt's SDK clients once, including Gemini's two surfaces."""
        if self._closed:
            return
        object.__setattr__(self, "_closed", True)
        self.track_clients()
        seen: set[int] = set()
        for client in self._clients:
            # google-genai owns independent synchronous and asynchronous pools.
            # OpenAI/Anthropic expose a single async close method on the client.
            for surface in (getattr(client, "aio", None), client):
                if surface is None or id(surface) in seen:
                    continue
                seen.add(id(surface))
                close = getattr(surface, "aclose", None) or getattr(surface, "close", None)
                if not callable(close):
                    continue
                try:
                    if inspect.iscoroutinefunction(close):
                        await close()
                    else:
                        result = await self.offload(close)
                        if inspect.isawaitable(result):
                            await result
                except Exception:  # noqa: BLE001 - one pool must not prevent other cleanup
                    # SDK cleanup can echo provider responses. Keep diagnostics
                    # useful without emitting the error body or its chain (AP-34).
                    log.warning("Swarm provider %s client cleanup failed", self.provider)


class SwarmBrainFactory:
    def __init__(
        self,
        cfg: JarvisConfig,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        offload: Callable[..., Awaitable[Any]] = asyncio.to_thread,
    ) -> None:
        self.cfg = cfg
        self.offload = offload
        self.registry = BrainProviderRegistry()
        self._cooldowns: dict[str, float] = {}
        self._next_requests: dict[str, float] = {}
        self._gates: dict[str, asyncio.Lock] = {}
        self._failures: dict[str, int] = {}
        self._monotonic = monotonic
        self._sleep = sleep
        self._jitter = jitter

    def _candidates(self) -> list[tuple[str, str | None]]:
        tier = self.cfg.brain.worker
        preferred: list[tuple[str, str | None]] = []
        if tier:
            preferred.extend(
                [
                    (tier.provider, tier.model),
                    (tier.fallback_provider or "", tier.fallback_model),
                    (tier.fallback_provider_2 or "", tier.fallback_model_2),
                ]
            )
        preferred.append((self.cfg.brain.primary, None))
        preferred.extend(
            (name, pc.deep_model or pc.model) for name, pc in self.cfg.brain.providers.items()
        )
        selected = {name for name, _ in preferred}
        # API adapters validate portable credentials during preparation. A keyless
        # runtime is eligible only when configured/selected, never merely because
        # its package happens to be installed on a cloud-key-only machine.
        preferred.extend(
            (name, None)
            for name in self.registry.available()
            if name in PROVIDER_SECRET_CANDIDATES or name in selected
        )
        result: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        for name, model in preferred:
            if not name or name in seen:
                continue
            seen.add(name)
            conf = self.cfg.brain.providers.get(name)
            result.append((name, model or (conf.deep_model or conf.model if conf else None)))
        return result

    def _build(self, name: str, model: str | None) -> SwarmProvider:
        cls = self.registry.get_class(name)
        # Tool support is not containment: subscription adapters may run ambient
        # CLIs on tool-free calls or an auth failure. Reject before construction.
        if getattr(cls, "scoped_execution_only", False) is not True:
            raise RuntimeError("The configured transport cannot guarantee scoped execution")
        # A class is instantiated per worker attempt, never shared as a native
        # engine across callers. Construction does not hand credentials to tools.
        brain = cls(model=model) if model else cls()
        actual_model = str(model or getattr(brain, "_model", ""))
        from .cost import resolve_rates

        rates = resolve_rates(actual_model)
        bound = max(Decimal(str(rate)) for rate in rates) if rates else None
        # USD per million tokens equals micro-USD per token. Use the larger
        # input/output rate even for cache hits; never claim an unknown price is free.
        return SwarmProvider(brain, name, actual_model, bound, offload=self.offload)

    async def _candidate(self, name: str, model: str | None, *, prepare: bool) -> SwarmProvider:
        construction = asyncio.ensure_future(self.offload(self._build, name, model))
        try:
            instance = await asyncio.shield(construction)
        except asyncio.CancelledError:
            # A running setup thread cannot be canceled. Collect its client
            # before propagating cancellation so it cannot leak off-thread.
            result = await asyncio.gather(construction, return_exceptions=True)
            if isinstance(result[0], SwarmProvider):
                await result[0].aclose()
            raise

        def validate_and_prepare() -> None:
            brain = instance.brain
            probe = getattr(brain, "can_call_tools", None)
            supports = probe() if callable(probe) else getattr(brain, "supports_tools", False)
            if supports is not True:
                raise RuntimeError("The configured transport does not expose scoped tool calls")
            setup = getattr(brain, "_ensure_client", None)
            if prepare and callable(setup) and not inspect.signature(setup).parameters:
                # The adapter validates its credentials, including keyless/ADC.
                setup()

        setup_task = asyncio.ensure_future(self.offload(validate_and_prepare))
        try:
            await asyncio.shield(setup_task)
            instance.own_request_retries()
            return instance
        except BaseException:
            await asyncio.gather(setup_task, return_exceptions=True)
            await instance.aclose()
            raise

    async def create(self) -> SwarmProvider:
        candidates = await self.offload(self._candidates)
        for name, model in candidates:
            if self._cooldowns.get(name, 0) > self._monotonic():
                continue
            try:
                return await self._candidate(name, model, prepare=True)
            except Exception as exc:  # noqa: BLE001 - cross-family fallback
                log.info("Swarm provider %s unavailable: %s", name, type(exc).__name__)
        # An otherwise usable sole provider may still be cooling down from a
        # different team. Return its isolated client and let await_ready wait
        # inside the caller's existing runtime budget, before reserving a call.
        for name, model in candidates:
            if self._cooldowns.get(name, 0) <= self._monotonic():
                continue
            try:
                return await self._candidate(name, model, prepare=True)
            except Exception as exc:  # noqa: BLE001 - another prepared candidate may work
                log.info("Swarm cooling provider %s unavailable: %s", name, type(exc).__name__)
        raise ProviderUnavailableError(
            "No scoped tool-capable agent provider is ready; connect a provider in API Keys"
        )

    def failed(self, provider: str) -> None:
        self._cooldowns[provider] = max(
            self._cooldowns.get(provider, 0), self._monotonic() + 30 + self._jitter(0.1, 1.0)
        )

    async def await_ready(self, provider: str) -> None:
        """Serialize cooldown admission across all teams sharing this factory."""
        async with self._gates.setdefault(provider, asyncio.Lock()):
            while True:
                ready = max(self._cooldowns.get(provider, 0), self._next_requests.get(provider, 0))
                delay = ready - self._monotonic()
                if delay <= 0:
                    break
                await self._sleep(delay)
                # Another in-flight request can extend the cooldown while we wait.
            if self._failures.get(provider, 0):
                self._next_requests[provider] = self._monotonic() + self._jitter(0.5, 1.5)

    async def recover(self, provider: SwarmProvider, exc: BaseException) -> SwarmProvider | None:
        """Prefer a ready cross-family API, otherwise await the same provider."""
        name = provider.provider
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        failures = min(self._failures.get(name, 0) + 1, 6)
        self._failures[name] = failures
        hint = _retry_after(exc, time.time())
        delay = hint if hint is not None else min(60.0, 2.0**failures)
        self._cooldowns[name] = max(
            self._cooldowns.get(name, 0), self._monotonic() + delay + self._jitter(0.1, 1.0)
        )
        for other, model in await self.offload(self._candidates):
            if other == name or self._cooldowns.get(other, 0) > self._monotonic():
                continue
            try:
                return await self._candidate(other, model, prepare=True)
            except Exception as unavailable:  # noqa: BLE001 - no missing key consumes task retries
                log.debug(
                    "Swarm recovery provider %s unavailable: %s", other, type(unavailable).__name__
                )
        if status in {401, 402, 403, 404}:
            # Authentication, credit and model configuration need a user action;
            # they cannot recover through time alone. Show the in-app
            # connection action instead of spending the task's verification retries.
            return None
        return provider

    async def catalog(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for name, model in await self.offload(self._candidates):
            instance = None
            try:
                instance = await self._candidate(name, model, prepare=False)
                credential = await self.offload(get_jarvis_agent_secret, name)
                # This flag describes stored credentials, not a network health
                # claim; keyless adapters are explicitly represented as unknown.
                rows.append(
                    {
                        "id": name,
                        "model": instance.model,
                        "available": True,
                        "credential_present": bool(credential),
                        "reason": "Connectivity and credentials are checked when started",
                    }
                )
            except Exception as exc:  # noqa: BLE001 - one broken adapter hides no other provider
                log.debug("Swarm catalog probe %s failed: %s", name, type(exc).__name__)
                rows.append(
                    {
                        "id": name,
                        "model": model or "",
                        "available": False,
                        "reason": "Scoped tool calls are unavailable for this transport",
                    }
                )
            finally:
                if instance is not None:
                    await instance.aclose()
        return rows
