"""Capacity policy for mission workers: subscriptions first, paid use only by consent.

When a mission's worker has no capacity left — the subscription's usage window
is spent, its login expired, or it cannot run at all — the mission first moves
to ANOTHER connected subscription (codex over the ChatGPT login, the ``claude``
CLI login): that costs the user nothing extra and needs no approval, for the
worker and the critic alike. When no subscription can run, there are exactly
two ways onward:

- the user's persisted setting ``[missions] paid_api_fallback`` (default
  ``false``): ON lets the mission continue on the user's own API key, within a
  hard per-mission cap (:data:`PAID_MISSION_CAP_USD`, cumulative) and a rolling
  24 h cap across all automatic use (:data:`PAID_DAILY_CAP_USD`);
- an explicit per-mission approval of the offer shown in the app (each approval
  grants up to :data:`PAID_MISSION_CAP_USD` more, worker and critic calls).

Anything else parks the mission in ``MissionState.WAITING_CAPACITY`` with a
checkpoint of the work done so far. It is NOT failed. Consent is re-checked in
front of EVERY paid model call (:class:`PaidCallGate`), so switching the
setting off stops automatic paid use at the next call.

Who is "pinned" (subject to all of the above): an install with a real
subscription — the sticky signal :mod:`jarvis.brain.background_policy` uses for
background work, or a positive login probe right now. An install that never
connected a subscription keeps its cross-family fallback chain, its API keys
are its primary, and it never parks for capacity (AGENTS.md "any single key
must work", AP-22). The paid-fallback setting has no effect there.

Every decision — dispatch, resume, retry, critic — goes through
:func:`decide_route`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, get_args

log = logging.getLogger(__name__)

CapacityWaitReason = Literal[
    "provider_quota",
    "provider_auth",
    "provider_unavailable",
    "paid_cap_reached",
    "paid_daily_cap_reached",
    "paid_consent_revoked",
]
CapacityDecision = Literal["wait", "approve_paid", "cancel"]

#: The reason vocabulary as a set (mirrors events.CAPACITY_WAIT_REASONS).
CAPACITY_WAIT_REASON_VALUES: frozenset[str] = frozenset(get_args(CapacityWaitReason))

#: Written into ``<mission_dir>/`` when a mission is parked. Its presence also
#: keeps the mission directory out of the age-based cleanup sweep; it is
#: deleted once the mission reaches a terminal state.
CHECKPOINT_NAME = "checkpoint.json"

#: Bumped when the checkpoint layout changes incompatibly.
CHECKPOINT_VERSION = 1

#: How often parked missions are re-checked for returned capacity. The check
#: itself is offline (quota cooldowns, login state), so it costs nothing; a
#: subscription window resets on a scale of minutes to hours.
RESUME_INTERVAL_S = 300.0

#: Random extra delay per tick so instances and windows never fire in lockstep
#: (AP-33).
RESUME_JITTER_S = 60.0

#: A mission that parks again without finishing anything new waits longer
#: before the next try: 5 min after the first such park, doubling up to an
#: hour, jittered by +/- :data:`RESUME_BACKOFF_JITTER`.
RESUME_BACKOFF_BASE_S = 300.0
RESUME_BACKOFF_MAX_S = 3600.0
RESUME_BACKOFF_JITTER = 0.2

#: Hard spend ceiling of paid API use for ONE mission (user decision
#: 2026-10-04). Automatic (setting-based) use is cumulative over the whole
#: mission and never resets; each manual approval grants up to this much more.
PAID_MISSION_CAP_USD = 2.0

#: Rolling 24 h ceiling across all AUTOMATIC paid mission use on this install.
PAID_DAILY_CAP_USD = 10.0

#: The rolling window of the daily cap.
DAILY_WINDOW_S = 24 * 3600.0

#: Token volume assumed per open step for the up-front estimate. Deliberately
#: generous: a worker re-reads its workspace every turn, and one trivial
#: mission once used 1.3M input tokens exploring a repository.
ESTIMATE_INPUT_TOKENS_PER_STEP = 400_000
ESTIMATE_OUTPUT_TOKENS_PER_STEP = 30_000

#: Reservation sizing for one paid call. A byte-level tokenizer never makes
#: more tokens than the UTF-8 bytes it reads, so bytes are an upper bound on
#: input tokens; a few tokens per message cover the framing. Every input
#: token is priced as a prompt-cache WRITE (the most expensive input kind).
RESERVE_OVERHEAD_TOKENS = 512
CACHE_WRITE_PREMIUM = 1.25

#: Provider families billed by a flat subscription (no per-token cost). Every
#: other family — claude-api, openai, gemini, openrouter, grok, nvidia, any
#: other api_agent provider — is metered.
SUBSCRIPTION_FAMILIES: frozenset[str] = frozenset(
    {"claude", "codex", "antigravity", "grok-build"}
)


def is_subscription_family(family: str | None) -> bool:
    """True when ``family`` bills a subscription, not per token."""
    return (family or "").strip().lower() in SUBSCRIPTION_FAMILIES


@dataclass(frozen=True)
class PaidOffer:
    """What the user is asked to approve before any paid API use: which
    provider and model, what it will roughly cost, the hard cap, and why."""

    provider: str
    model: str
    estimated_cost_usd: float
    cost_cap_usd: float
    reason: str
    open_steps: int


class PaidOption(Protocol):
    """Builds the paid alternative for a parked mission (wired in
    ``jarvis.missions.init``). ``offer`` never calls a provider."""

    def offer(self, *, pinned_family: str | None, open_steps: int, reason: str) -> PaidOffer | None:
        ...

    def worker(self, offer: PaidOffer, *, gate: PaidCallGate, task_text: str) -> Any:
        ...


def estimate_paid_cost_usd(model: str, open_steps: int) -> float | None:
    """Rough USD for ``open_steps`` steps on ``model``, or None when the model
    has no known price — an unpriced model is never offered for paid use,
    because neither the estimate nor the cap could be trusted."""
    from jarvis.brain.cost import resolve_rates

    rates = resolve_rates(model)
    if rates is None or rates == (0.0, 0.0):
        return None
    rate_in, rate_out = rates
    steps = max(open_steps, 1)
    return round(
        steps
        * (
            ESTIMATE_INPUT_TOKENS_PER_STEP * rate_in
            + ESTIMATE_OUTPUT_TOKENS_PER_STEP * rate_out
        )
        / 1_000_000,
        4,
    )


def max_call_cost_usd(model: str, *, input_bytes: int, max_output_tokens: int) -> float | None:
    """Upper bound in USD for ONE model call, reserved before it is made.

    Input tokens are bounded by the request's UTF-8 bytes (plus
    :data:`RESERVE_OVERHEAD_TOKENS`) and billed as if every one were a cache
    write; output at the full ``max_output_tokens``. None for an unpriced model
    (never called on a cap).
    """
    from jarvis.brain.cost import resolve_rates

    rates = resolve_rates(model)
    if rates is None or rates == (0.0, 0.0):
        return None
    rate_in, rate_out = rates
    input_tokens = max(input_bytes, 0) + RESERVE_OVERHEAD_TOKENS
    return (
        input_tokens * rate_in * CACHE_WRITE_PREMIUM
        + max(max_output_tokens, 0) * rate_out
    ) / 1_000_000


_INPUT_KEYS = ("input_tokens", "prompt_tokens")
_OUTPUT_KEYS = ("output_tokens", "completion_tokens")
_CACHE_READ_KEYS = ("cache_hit_tokens", "cache_read_input_tokens", "cached_input_tokens")
_CACHE_WRITE_KEYS = ("cache_write_tokens", "cache_creation_input_tokens")


def usage_cost_usd(model: str, usage: dict[str, Any]) -> float:
    """USD for one usage block: uncached input, output, cache reads at the
    vendor's read rate and cache writes at :data:`CACHE_WRITE_PREMIUM`."""
    from jarvis.brain.cost import calculate_cost_usd, resolve_rates

    def first(keys: tuple[str, ...]) -> int:
        for key in keys:
            value = usage.get(key)
            if value is not None:
                try:
                    return max(0, int(value))
                except (TypeError, ValueError):
                    # A non-numeric count says nothing; the reservation's fail
                    # closed rule covers a call that reported nothing usable.
                    return 0
        return 0

    cost = calculate_cost_usd(
        model, first(_INPUT_KEYS), first(_OUTPUT_KEYS), first(_CACHE_READ_KEYS)
    )
    written = first(_CACHE_WRITE_KEYS)
    if written:
        rates = resolve_rates(model)
        if rates is not None:
            cost += written * rates[0] * CACHE_WRITE_PREMIUM / 1_000_000
    return cost


class WorkerCapacityUnavailable(RuntimeError):
    """Raised by the worker factory instead of picking a worker that would bill
    a per-token API key nobody consented to."""

    def __init__(
        self,
        reason: CapacityWaitReason,
        provider: str,
        detail: str = "",
    ) -> None:
        super().__init__(f"{provider}: {reason}" + (f" ({detail})" if detail else ""))
        self.reason: CapacityWaitReason = reason
        self.provider = provider
        self.detail = detail


class CriticCapacityUnavailable(RuntimeError):
    """No critic can grade right now: neither the configured subscription nor
    another connected one, and no consented paid critic call."""

    def __init__(self, reason: CapacityWaitReason, provider: str, detail: str = "") -> None:
        super().__init__(f"critic {provider}: {reason}" + (f" ({detail})" if detail else ""))
        self.reason: CapacityWaitReason = reason
        self.provider = provider
        self.detail = detail


#: How often a parked "review only" step may hit a critic failure that is NOT
#: about capacity before the mission fails honestly (critic_unavailable)
#: instead of waiting forever on a critic that is broken for another reason.
MAX_REVIEW_RETRIES = 3


class CapacityDecisionRejected(RuntimeError):
    """A capacity decision that cannot apply: the mission is no longer
    parked, there is no paid option, or the option changed since it was
    shown. Nothing was decided or billed."""


def pinned_to_subscription(*, login_probe: Callable[[], bool] | None = None) -> bool:
    """Whether this install is a subscription install (missions pinned).

    True when a subscription was seen connected recently (the sticky
    background-policy signal) or ``login_probe`` reports a usable subscription
    login right now. A CLI that is merely installed is NOT a subscription: a
    key-only user with Claude Code installed but never logged in keeps the
    cross-family chain (AP-22).

    Fails closed on an unreadable sticky signal: the mission waits rather than
    risk billing a key nobody approved. A failing login probe only means "no
    positive answer".
    """
    try:
        from jarvis.brain.background_policy import subscription_mode

        if subscription_mode():
            return True
    except Exception:  # noqa: BLE001 - unreadable signal: waiting is the safe answer
        log.warning(
            "capacity: subscription signal unreadable — pinning the mission "
            "to its subscription",
            exc_info=True,
        )
        return True
    if login_probe is None:
        return False
    try:
        return bool(login_probe())
    except Exception:  # noqa: BLE001 - a probe error is "no positive login", logged
        log.warning("capacity: subscription login probe failed", exc_info=True)
        return False


def paid_api_fallback_enabled() -> bool:
    """The persisted ``[missions] paid_api_fallback`` switch, read fresh.

    No cache: a toggle applies to the very next decision. Fails closed — an
    unreadable config never enables paid use.
    """
    try:
        from jarvis.core.config import load_config

        missions = getattr(load_config(), "missions", None)
        return bool(getattr(missions, "paid_api_fallback", False) is True)
    except Exception:  # noqa: BLE001 - unreadable config: paid use stays off, logged
        log.warning("capacity: [missions] config unreadable — paid fallback OFF", exc_info=True)
        return False


# --- Decision -----------------------------------------------------------------


@dataclass(frozen=True)
class RouteDecision:
    """Where the next model call of a pinned mission goes.

    ``subscription``: a subscription (configured or another connected one).
    ``paid``: the user's API key — ``automatic`` True for the setting-based
    path, False for a manual per-mission approval. ``park``: wait, ``reason``.
    """

    route: Literal["subscription", "paid", "park"]
    reason: CapacityWaitReason | None = None
    automatic: bool = False


def decide_route(
    *,
    subscription_ok: bool,
    unavailable_reason: CapacityWaitReason,
    manual_approval: bool,
    paid_fallback_enabled: bool,
    paid_option_available: bool,
    mission_remaining_usd: float,
    daily_remaining_usd: float,
) -> RouteDecision:
    """The one routing rule for dispatch, resume, retry and critic calls.

    1. Any subscription with capacity wins (free).
    2. An active manual approval for this mission run: paid, within what that
       approval left of its cap.
    3. The paid-fallback setting ON: paid, within the per-mission cap and the
       rolling daily cap.
    4. Otherwise: park with the subscription's reason.
    """
    if subscription_ok:
        return RouteDecision("subscription")
    if manual_approval:
        if mission_remaining_usd <= 0:
            return RouteDecision("park", "paid_cap_reached")
        return RouteDecision("paid", automatic=False)
    if paid_fallback_enabled and paid_option_available:
        if mission_remaining_usd <= 0:
            return RouteDecision("park", "paid_cap_reached")
        if daily_remaining_usd <= 0:
            return RouteDecision("park", "paid_daily_cap_reached")
        return RouteDecision("paid", automatic=True)
    return RouteDecision("park", unavailable_reason)


# --- Ledgers ------------------------------------------------------------------


class MissionPaidLedger:
    """Cumulative paid spend of ONE mission, shared by all its parallel steps,
    retries, resumes, worker and critic calls.

    Every paid call reserves its maximum possible cost first; a reservation
    that would cross the cap is refused, so parallel callers cannot overshoot.
    The automatic limit is :data:`PAID_MISSION_CAP_USD` over the whole
    mission; a manual approval sets a limit of what was spent at approval time
    plus :data:`PAID_MISSION_CAP_USD`.
    """

    def __init__(self, spent_usd: float = 0.0) -> None:
        self._lock = threading.Lock()
        self._spent = max(float(spent_usd), 0.0)
        self._reserved = 0.0
        self._manual_limit: float | None = None

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return self._spent

    @property
    def manual_active(self) -> bool:
        with self._lock:
            return self._manual_limit is not None

    def grant_manual(self) -> None:
        """A manual approval: up to :data:`PAID_MISSION_CAP_USD` more."""
        with self._lock:
            self._manual_limit = self._spent + PAID_MISSION_CAP_USD

    def revoke_manual(self) -> None:
        with self._lock:
            self._manual_limit = None

    def _limit(self, automatic: bool) -> float:
        if automatic:
            return PAID_MISSION_CAP_USD
        return self._manual_limit if self._manual_limit is not None else 0.0

    def remaining(self, *, automatic: bool) -> float:
        with self._lock:
            return max(self._limit(automatic) - self._spent - self._reserved, 0.0)

    def try_reserve(self, amount_usd: float, *, automatic: bool) -> bool:
        amount = max(float(amount_usd), 0.0)
        with self._lock:
            if self._spent + self._reserved + amount > self._limit(automatic) + 1e-9:
                return False
            self._reserved += amount
            return True

    def release(self, amount_usd: float) -> None:
        with self._lock:
            self._reserved = max(self._reserved - max(float(amount_usd), 0.0), 0.0)

    def settle(self, reserved_usd: float, actual_usd: float) -> None:
        """Replace a reservation by the actual cost of the call."""
        with self._lock:
            self._reserved = max(self._reserved - max(float(reserved_usd), 0.0), 0.0)
            self._spent += max(float(actual_usd), 0.0)

    def charge(self, amount_usd: float) -> None:
        """Count spend that was never reserved (a worker that reported its
        cost only at the end). Fail closed: always counted."""
        with self._lock:
            self._spent += max(float(amount_usd), 0.0)


def _default_daily_ledger_path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "data" / "mission_paid_ledger.json"


class DailyPaidLedger:
    """Rolling 24 h record of paid mission use, persisted as small JSON.

    Automatic (setting-based) calls reserve against :data:`PAID_DAILY_CAP_USD`;
    manual approvals are recorded for reporting and never blocked by it. The
    file is rewritten atomically under a lock and pruned to the window; an
    unreadable file fails closed (no automatic budget left) until it is
    rewritten.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        cap_usd: float = PAID_DAILY_CAP_USD,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path_override = path
        self._cap = cap_usd
        self._clock = clock
        self._lock = threading.Lock()
        self._reserved = 0.0

    @property
    def cap_usd(self) -> float:
        return self._cap

    def _path(self) -> Path:
        return self._path_override or _default_daily_ledger_path()

    def _entries(self) -> list[dict[str, Any]] | None:
        """Entries inside the window, or None when the file is unreadable."""
        try:
            raw = json.loads(self._path().read_text(encoding="utf-8"))
        except FileNotFoundError:  # nothing recorded yet
            return []
        except (OSError, ValueError):
            log.warning("capacity: daily paid ledger unreadable — automatic paid use blocked",
                        exc_info=True)
            return None
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            return None
        cutoff = self._clock() - DAILY_WINDOW_S
        return [
            e for e in entries
            if isinstance(e, dict)
            and isinstance(e.get("ts"), (int, float))
            and isinstance(e.get("usd"), (int, float))
            and e["ts"] >= cutoff
        ]

    def _write(self, entries: list[dict[str, Any]]) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mission_paid_ledger.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "entries": entries}, handle)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    @staticmethod
    def _sum(entries: list[dict[str, Any]], *, automatic_only: bool) -> float:
        return sum(
            float(e["usd"]) for e in entries
            if not automatic_only or e.get("automatic") is True
        )

    def spent_last_24h(self, *, automatic_only: bool = True) -> float:
        """USD spent in the last 24 h (automatic use only by default — what
        the daily cap counts)."""
        with self._lock:
            entries = self._entries()
        if entries is None:
            return self._cap
        return round(self._sum(entries, automatic_only=automatic_only), 6)

    def remaining(self) -> float:
        with self._lock:
            entries = self._entries()
            if entries is None:
                return 0.0
            return max(self._cap - self._sum(entries, automatic_only=True) - self._reserved, 0.0)

    def try_reserve(self, amount_usd: float) -> bool:
        amount = max(float(amount_usd), 0.0)
        with self._lock:
            entries = self._entries()
            if entries is None:
                return False
            spent = self._sum(entries, automatic_only=True)
            if spent + self._reserved + amount > self._cap + 1e-9:
                return False
            self._reserved += amount
            return True

    def release(self, amount_usd: float) -> None:
        with self._lock:
            self._reserved = max(self._reserved - max(float(amount_usd), 0.0), 0.0)

    def record(
        self,
        actual_usd: float,
        *,
        automatic: bool,
        mission_id: str,
        reserved_usd: float = 0.0,
    ) -> None:
        """Book a call's actual cost (and drop its reservation, if any)."""
        with self._lock:
            self._reserved = max(self._reserved - max(float(reserved_usd), 0.0), 0.0)
            cost = max(float(actual_usd), 0.0)
            if cost <= 0:
                return
            entries = self._entries() or []
            entries.append({
                "ts": self._clock(),
                "usd": round(cost, 6),
                "automatic": automatic,
                "mission_id": mission_id,
            })
            try:
                self._write(entries)
            except OSError:
                # The in-memory mission ledger still caps this mission; the
                # daily view just misses the entry. Logged, never silent.
                log.exception("capacity: daily paid ledger write failed")


_DEFAULT_DAILY: DailyPaidLedger | None = None
_DEFAULT_DAILY_LOCK = threading.Lock()


def default_daily_ledger() -> DailyPaidLedger:
    """The process-wide daily ledger (shared by the runner and the routes)."""
    global _DEFAULT_DAILY
    with _DEFAULT_DAILY_LOCK:
        if _DEFAULT_DAILY is None:
            _DEFAULT_DAILY = DailyPaidLedger()
        return _DEFAULT_DAILY


class CapacityPolicy:
    """The live answers capacity decisions need, read fresh on every call.

    ``pinned`` / ``paid_fallback`` are injectable for tests (plain callables,
    no mocks); production uses the install's subscription signal and the
    persisted setting.
    """

    def __init__(
        self,
        *,
        pinned: Callable[[], bool] | None = None,
        paid_fallback: Callable[[], bool] | None = None,
        daily: DailyPaidLedger | None = None,
    ) -> None:
        self._pinned = pinned
        self._paid_fallback = paid_fallback or paid_api_fallback_enabled
        self.daily = daily or default_daily_ledger()

    def pinned(self) -> bool:
        if self._pinned is not None:
            return bool(self._pinned())
        from jarvis.missions.init import install_is_pinned

        return install_is_pinned()

    def paid_fallback_enabled(self) -> bool:
        return bool(self._paid_fallback())


# --- The per-call gate --------------------------------------------------------


class PaidCallRefused(RuntimeError):
    """A paid call was refused before it was made: consent gone or a cap hit."""

    def __init__(self, reason: CapacityWaitReason, provider: str) -> None:
        super().__init__(f"paid {provider} call refused: {reason}")
        self.reason: CapacityWaitReason = reason
        self.provider = provider


@dataclass(frozen=True)
class PaidReservation:
    amount_usd: float
    automatic: bool


class PaidCallGate:
    """Consent + caps in front of every paid model call of one mission.

    ``reserve`` re-runs the routing decision (``authorize``) — the setting is
    read fresh, a manual approval must still be active — then reserves the
    call's maximum cost on the mission ledger and, for automatic use, on the
    daily ledger. ``commit`` books the actual cost; a call that reported no
    usage counts its full reservation (fail closed).
    """

    def __init__(
        self,
        *,
        mission_id: str,
        offer: PaidOffer,
        ledger: MissionPaidLedger,
        daily: DailyPaidLedger,
        authorize: Callable[[], RouteDecision],
        on_charge: Callable[[PaidOffer, bool, float], None] | None = None,
    ) -> None:
        self.mission_id = mission_id
        self.offer = offer
        self._ledger = ledger
        self._daily = daily
        self._authorize = authorize
        self._on_charge = on_charge
        self._lock = threading.Lock()
        self.charged_usd = 0.0
        self.calls = 0
        self.refusal: CapacityWaitReason | None = None
        self.last_automatic: bool | None = None

    @property
    def provider(self) -> str:
        return self.offer.provider

    @property
    def model(self) -> str:
        return self.offer.model

    def remaining_usd(self) -> float:
        """What this mission may still spend on its current consent."""
        decision = self._authorize()
        if decision.route != "paid":
            return 0.0
        remaining = self._ledger.remaining(automatic=decision.automatic)
        if decision.automatic:
            remaining = min(remaining, self._daily.remaining())
        return remaining

    def _refuse(self, reason: CapacityWaitReason) -> PaidCallRefused:
        with self._lock:
            self.refusal = reason
        log.warning(
            "capacity: paid %s call for mission %s refused (%s)",
            self.offer.provider, self.mission_id, reason,
        )
        return PaidCallRefused(reason, self.offer.provider)

    def reserve(self, max_cost_usd: float | None) -> PaidReservation:
        if max_cost_usd is None:
            # Unpriced: neither the reservation nor the cap could be trusted.
            raise self._refuse("provider_unavailable")
        decision = self._authorize()
        if decision.route != "paid":
            reason = decision.reason
            if reason not in ("paid_cap_reached", "paid_daily_cap_reached"):
                reason = "paid_consent_revoked"
            raise self._refuse(reason)
        amount = max(float(max_cost_usd), 0.0)
        if not self._ledger.try_reserve(amount, automatic=decision.automatic):
            raise self._refuse("paid_cap_reached")
        if decision.automatic and not self._daily.try_reserve(amount):
            self._ledger.release(amount)
            raise self._refuse("paid_daily_cap_reached")
        with self._lock:
            self.calls += 1
            self.last_automatic = decision.automatic
        return PaidReservation(amount_usd=amount, automatic=decision.automatic)

    def commit(self, reservation: PaidReservation, actual_usd: float | None) -> float:
        """Book the call. ``actual_usd`` None (no usage reported) counts the
        whole reservation. Returns the booked amount."""
        cost = reservation.amount_usd if actual_usd is None else max(float(actual_usd), 0.0)
        if actual_usd is not None and cost > reservation.amount_usd + 1e-9:
            log.warning(
                "capacity: paid call for mission %s cost $%.4f, above its $%.4f "
                "reservation", self.mission_id, cost, reservation.amount_usd,
            )
        self._ledger.settle(reservation.amount_usd, cost)
        self._daily.record(
            cost,
            automatic=reservation.automatic,
            mission_id=self.mission_id,
            reserved_usd=reservation.amount_usd if reservation.automatic else 0.0,
        )
        self._book(reservation.automatic, cost)
        return cost

    def settle_reported(self, reported_usd: float | None, *, automatic: bool) -> float:
        """Charge what a worker reported beyond what its gated calls booked —
        a worker that never used the gate is charged its whole report.
        Returns the extra amount charged."""
        with self._lock:
            extra = max(float(reported_usd or 0.0) - self.charged_usd, 0.0)
        if extra <= 0:
            return 0.0
        self._ledger.charge(extra)
        self._daily.record(extra, automatic=automatic, mission_id=self.mission_id)
        self._book(automatic, extra)
        return extra

    def _book(self, automatic: bool, cost: float) -> None:
        with self._lock:
            self.charged_usd += cost
        if self._on_charge is not None:
            self._on_charge(self.offer, automatic, cost)


# --- Checkpoint ---------------------------------------------------------------


def write_checkpoint(mission_dir: Path, data: dict[str, Any]) -> Path:
    """Atomically write the checkpoint and return its path."""
    mission_dir.mkdir(parents=True, exist_ok=True)
    path = mission_dir / CHECKPOINT_NAME
    payload = {"version": CHECKPOINT_VERSION, **data}
    fd, tmp = tempfile.mkstemp(dir=mission_dir, prefix=".checkpoint.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def read_checkpoint(mission_dir: Path) -> dict[str, Any] | None:
    """The stored checkpoint, or None when it is missing or unreadable."""
    path = mission_dir / CHECKPOINT_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:  # no checkpoint is a normal answer, not a failure
        return None
    except (OSError, ValueError):
        log.warning("capacity: unreadable checkpoint %s", path, exc_info=True)
        return None
    return data if isinstance(data, dict) else None


def delete_checkpoint(mission_dir: Path) -> None:
    """Drop the checkpoint of a mission that reached a terminal state, so the
    age-based cleanup may sweep its directory again."""
    try:
        (mission_dir / CHECKPOINT_NAME).unlink(missing_ok=True)
    except OSError:
        log.warning("capacity: could not delete checkpoint in %s", mission_dir, exc_info=True)


def resume_backoff_s(
    idle_parks: int,
    *,
    jitter: Callable[[float, float], float] = random.uniform,
) -> float:
    """Extra wait before the next automatic resume of a mission that parked
    ``idle_parks`` times in a row without finishing anything new."""
    if idle_parks <= 0:
        return 0.0
    base = min(RESUME_BACKOFF_BASE_S * (2 ** (idle_parks - 1)), RESUME_BACKOFF_MAX_S)
    return base * jitter(1.0 - RESUME_BACKOFF_JITTER, 1.0 + RESUME_BACKOFF_JITTER)


def worker_family(worker: object) -> str:
    """The provider family a worker bills — the identity a parked mission is
    pinned to. Every built-in worker declares ``family``; anything else falls
    back to its ``provider``/``cli`` attribute."""
    for attr in ("family", "provider", "cli"):
        value = getattr(worker, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return type(worker).__name__


async def capacity_resume_loop(
    resume_pass: Callable[[], Awaitable[list[str]]],
    *,
    interval_s: float = RESUME_INTERVAL_S,
    jitter_s: float = RESUME_JITTER_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[float, float], float] = random.uniform,
) -> None:
    """Run ``resume_pass`` on a jittered timer until cancelled.

    The first pass waits one interval: nothing here belongs on the boot
    critical path (AP-26). A failing pass is logged and the loop carries on —
    one bad tick must not stop every later resume.
    """
    while True:
        await sleep(interval_s + jitter(0.0, jitter_s))
        try:
            resumed = await resume_pass()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - logged; the next tick retries
            log.exception("capacity: resume pass failed")
            continue
        if resumed:
            log.info("capacity: resumed %d parked mission(s): %s", len(resumed), resumed)


__all__ = [
    "CACHE_WRITE_PREMIUM",
    "CAPACITY_WAIT_REASON_VALUES",
    "CHECKPOINT_NAME",
    "CHECKPOINT_VERSION",
    "DAILY_WINDOW_S",
    "ESTIMATE_INPUT_TOKENS_PER_STEP",
    "ESTIMATE_OUTPUT_TOKENS_PER_STEP",
    "MAX_REVIEW_RETRIES",
    "PAID_DAILY_CAP_USD",
    "PAID_MISSION_CAP_USD",
    "RESUME_BACKOFF_BASE_S",
    "RESUME_BACKOFF_MAX_S",
    "RESUME_INTERVAL_S",
    "RESUME_JITTER_S",
    "SUBSCRIPTION_FAMILIES",
    "CapacityDecision",
    "CapacityDecisionRejected",
    "CapacityPolicy",
    "CapacityWaitReason",
    "CriticCapacityUnavailable",
    "DailyPaidLedger",
    "MissionPaidLedger",
    "PaidCallGate",
    "PaidCallRefused",
    "PaidOffer",
    "PaidOption",
    "PaidReservation",
    "RouteDecision",
    "WorkerCapacityUnavailable",
    "capacity_resume_loop",
    "decide_route",
    "default_daily_ledger",
    "delete_checkpoint",
    "estimate_paid_cost_usd",
    "is_subscription_family",
    "max_call_cost_usd",
    "paid_api_fallback_enabled",
    "pinned_to_subscription",
    "read_checkpoint",
    "resume_backoff_s",
    "usage_cost_usd",
    "worker_family",
    "write_checkpoint",
]
