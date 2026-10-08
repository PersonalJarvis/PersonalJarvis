"""Keep coding agents working when a subscription seat runs out.

A user holding several seats of one coding CLI (:mod:`jarvis.agent_accounts`)
holds them for one reason: when the first plan is spent, the work goes on on
the second. This module is what makes that happen without anybody watching.

**One switch moves everything.** :func:`switch_seat` makes a seat the active
one AND moves every pane of that CLI onto it. A pane that is idle moves at
once; a pane in the middle of a turn is marked and moves the moment the turn
ends, because ending its process would cut the step in flight off. Moving a
pane means ending its CLI and starting it again on the new seat with
``--resume`` — the conversation is carried along (:mod:`.seat_handoff`), so
the agent continues exactly where it was and only the plan it spends changes.
Whether a pane is idle is decided again under the pane's own lock, right
before its process ends, so a prompt that lands in between is never cut off.

**Two ways a seat counts as used up**, both read without spending anything:

* **The provider refused.** The CLI files a usage-limit stop in its own
  transcript (:func:`.seat_handoff.limit_stop`). The pane is moved to the next
  seat at once and told to continue, so the interrupted task carries on. This
  is the guarantee: whatever the usage numbers said, a refusal always moves
  the work. A stop that was answered is remembered on disk by conversation,
  so the carried transcript — which still ends in that stop — never triggers
  a second switch, not even after an app restart.
* **The plan is nearly spent.** The usage the Subscriptions panel shows
  (:mod:`jarvis.agent_usage`, a free status read) crosses the threshold — by
  default 97 %, three percent left. Idle panes move straight away, working
  ones when their turn ends, so most work never meets the refusal at all. A
  seat the user picked by hand is left to them until it actually refuses.

**The next seat** is the signed-in subscription of the same CLI with the most
budget left: never the same login under another name (two rows signed in as
one email share one plan), never an API-key login (nothing the user did not
start may bill a key), never one that ran out itself and has not reset, never
one already past the threshold. When there is none, nothing moves and the
status says so; once the refusing seat refills, its cut-off panes are told to
continue where they are.

Never on the boot path (AP-26): the watch starts with the first workspace and
costs a few small file reads per tick while panes run.
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import random
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from loguru import logger

if TYPE_CHECKING:
    from jarvis.agent_accounts import AgentAccount
    from jarvis.agent_usage import AccountUsage

    from .session import Registry, Session, Terminal

#: How often running panes are checked for a usage-limit stop. Local file
#: tails only (re-read only when they changed), so this can be short: the
#: sooner a refused pane moves, the less time the user's agents stand still.
WATCH_INTERVAL_S = 8.0

#: How often plan usage is read while panes run. The provider's numbers move
#: slowly and the panel shares the same cache (``agent_usage.USAGE_TTL_S``).
USAGE_INTERVAL_S = 90.0
USAGE_JITTER_S = 20.0

#: How long a seat that refused stays out of rotation when its reset time is
#: unknown. Past it, the seat is tried again — a fresh usage reading decides.
EXHAUSTED_FALLBACK_S = 3600.0

#: A pane somebody typed into this recently is left alone for now: restarting
#: its CLI would throw the half-written prompt away. The move waits like one
#: behind a running turn.
TYPING_GRACE_S = 60.0

#: Recent switches kept for the panel. A handful is all anyone reads.
MAX_EVENTS = 20

#: Answered limit stops remembered on disk, newest kept.
MAX_HANDLED = 500

#: What a pane that was cut off by a usage limit is told after the move. The
#: CLI has the whole conversation back; this only says why it stopped and that
#: it should carry on.
CONTINUE_PROMPT = (
    "Continue exactly where you stopped. The previous subscription reached its "
    "usage limit, so this session now runs on another subscription; nothing "
    "else changed."
)

#: The same, for a pane that waited on its own seat until that seat refilled.
RESUME_PROMPT = (
    "Continue exactly where you stopped. The usage limit that interrupted you has reset."
)


@dataclass(frozen=True, slots=True)
class SwitchEvent:
    """One switch, as the panel reports it."""

    at: float
    platform: str
    reason: str  # "manual" | "limit" | "threshold" | "no_seat" | "refilled"
    to_account: str | None
    to_label: str
    from_label: str
    moved: int = 0
    queued: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "platform": self.platform,
            "reason": self.reason,
            "to_account": self.to_account,
            "to_label": self.to_label,
            "from_label": self.from_label,
            "moved": self.moved,
            "queued": self.queued,
        }


@dataclass(slots=True)
class _State:
    #: account id -> epoch until which it is out of rotation. Written on the
    #: event loop only.
    exhausted: dict[str, float] = field(default_factory=dict)
    events: deque[SwitchEvent] = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    task: asyncio.Task[None] | None = None
    lock: asyncio.Lock | None = None
    next_usage_at: float = 0.0
    #: (platform, leaving ids) of the last "no seat left" report, so a seat
    #: that stays empty is reported once rather than every tick.
    reported_empty: set[tuple[str, frozenset[str]]] = field(default_factory=set)
    #: (conversation id, stop) pairs whose seat was already taken out of
    #: rotation — a stop that waits for a seat must not extend that every tick.
    seen_stops: set[tuple[str, float]] = field(default_factory=set)
    #: platform -> the seat the user picked by hand; the threshold leaves it be.
    manual: dict[str, str] = field(default_factory=dict)
    #: Continue prompts in flight, held so the loop cannot drop them.
    nudges: set[asyncio.Task[None]] = field(default_factory=set)


_STATE = _State()

# conversation id -> the limit stop (epoch) a switch already answered.
_HANDLED: dict[str, float] | None = None
_HANDLED_LOCK = threading.Lock()


def _lock() -> asyncio.Lock:
    if _STATE.lock is None:
        _STATE.lock = asyncio.Lock()
    return _STATE.lock


def reset() -> None:
    """Forget everything — for tests."""
    global _HANDLED
    stop()
    _STATE.exhausted.clear()
    _STATE.events.clear()
    _STATE.lock = None
    _STATE.next_usage_at = 0.0
    _STATE.reported_empty.clear()
    _STATE.seen_stops.clear()
    _STATE.manual.clear()
    _STATE.nudges.clear()
    with _HANDLED_LOCK:
        _HANDLED = None


# --------------------------------------------------------- answered stops


def _handled_path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agentic_ide" / "seat_switch_handled.json"


def _handled() -> dict[str, float]:
    """Answered stops by conversation id. Unreadable reads as none answered."""
    global _HANDLED
    with _HANDLED_LOCK:
        if _HANDLED is None:
            try:
                raw = json.loads(_handled_path().read_text(encoding="utf-8"))
            except FileNotFoundError:
                raw = {}
            except (OSError, ValueError) as exc:
                logger.warning(
                    "Seat switch: answered-stop record unreadable ({}) — starting empty", exc
                )
                raw = {}
            _HANDLED = {
                str(key): float(value)
                for key, value in (raw.items() if isinstance(raw, dict) else ())
                if isinstance(value, int | float)
            }
        return _HANDLED


def handled_stop(session_id: str) -> float:
    """The stop already answered for this conversation, or 0."""
    return _handled().get(session_id, 0.0)


def _remember_handled(session_id: str, stop: float) -> None:
    """Record an answered stop, atomically. Filesystem: off the loop."""
    handled = _handled()
    with _HANDLED_LOCK:
        handled[session_id] = max(stop, handled.get(session_id, 0.0))
        if len(handled) > MAX_HANDLED:
            for key, _value in sorted(handled.items(), key=lambda item: item[1])[
                : len(handled) - MAX_HANDLED
            ]:
                handled.pop(key, None)
        payload = json.dumps(handled, indent=1)
    path = _handled_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{uuid4().hex[:8]}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        # The in-memory record still guards this process; only a restart
        # inside the stale window could see the stop again.
        logger.warning("Seat switch: answered stop not saved: {}", exc)


# ---------------------------------------------------------------- readings


def _now() -> float:
    return time.time()


def _window_resets(window: Any) -> float | None:
    from datetime import datetime

    raw = getattr(window, "resets_at", None)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def spent_percent(
    usage: AccountUsage | None, *, now: float | None = None, scoped: bool = True
) -> float | None:
    """The tightest limit's spent share, or None when nothing was measured.

    A window whose reset time has already passed counts as empty: a cached
    reading from before the reset still says 100 %, and trusting it would
    keep a refilled seat out of rotation.

    ``scoped=False`` ignores budgets that bind only one model
    (``weekly_scoped``). Those must not move ALL work off a seat ahead of
    time — agents on other models still have the whole plan — while a turn
    that does run into one is refused and moved on by the limit-stop path.
    """
    if usage is None or usage.status != "ok" or not usage.windows:
        return None
    moment = _now() if now is None else now
    worst = 0.0
    for window in usage.windows:
        if not scoped and window.kind == "weekly_scoped":
            continue
        resets = _window_resets(window)
        if resets is not None and resets <= moment:
            continue
        worst = max(worst, float(window.percent))
    return worst


def _refill_at(
    usage: AccountUsage | None, at_percent: float, *, scoped: bool = True
) -> float | None:
    """When the windows at or past ``at_percent`` refill, the latest of them."""
    if usage is None or usage.status != "ok":
        return None
    moment = _now()
    latest: float | None = None
    for window in usage.windows:
        if not scoped and window.kind == "weekly_scoped":
            continue
        resets = _window_resets(window)
        if resets is None or resets <= moment or float(window.percent) < at_percent:
            continue
        latest = resets if latest is None else max(latest, resets)
    return latest


def is_exhausted(account_id: str | None, *, now: float | None = None) -> bool:
    """Whether a seat is out of rotation after refusing or crossing the threshold."""
    if not account_id:
        return False
    until = _STATE.exhausted.get(account_id)
    return until is not None and until > (_now() if now is None else now)


def _identity(snapshot: Any) -> str:
    email = (getattr(snapshot, "email", None) or "").strip().lower()
    return email or f"id:{snapshot.account.id}"


def _same_login_ids(platform: str, account_ids: set[str]) -> set[str]:
    """``account_ids`` plus every seat signed in as one of the same logins.

    Filesystem reads: callers run it off the event loop.
    """
    from jarvis import agent_accounts

    snapshots = agent_accounts.snapshots(platform)
    identities = {_identity(s) for s in snapshots if s.account.id in account_ids}
    return {s.account.id for s in snapshots if _identity(s) in identities} | set(account_ids)


def _set_exhausted(account_ids: set[str], until: float | None) -> None:
    """Take these seats out of rotation. On the event loop."""
    deadline = until if until and until > _now() else _now() + EXHAUSTED_FALLBACK_S
    for account_id in account_ids:
        _STATE.exhausted[account_id] = max(deadline, _STATE.exhausted.get(account_id, 0.0))


def choose_seat(
    platform: str,
    leaving: set[str],
    usage: dict[str, AccountUsage],
    at_percent: float,
    *,
    prefer: str | None = None,
    scoped: bool = True,
) -> AgentAccount | None:
    """The seat with the most budget left that is a different subscription.

    ``prefer`` (the active seat) wins whenever it is eligible at all: work
    leaving a pinned seat goes where everything else already runs, rather than
    pulling the whole CLI onto a third seat.

    Filesystem reads: callers run it off the event loop.
    """
    from jarvis import agent_accounts

    snapshots = agent_accounts.snapshots(platform)
    leaving_logins = {_identity(s) for s in snapshots if s.account.id in leaving}
    ranked: list[tuple[int, float, int, AgentAccount]] = []
    for order, snapshot in enumerate(snapshots):
        account = snapshot.account
        if account.id in leaving or _identity(snapshot) in leaving_logins:
            continue
        # A subscription only: an API-key login bills per token, and nothing
        # the user did not start may spend a key (AGENTS.md §2).
        if not snapshot.connected or snapshot.mode != "subscription":
            continue
        if is_exhausted(account.id):
            continue
        spent = spent_percent(usage.get(account.id), scoped=scoped)
        if spent is not None and spent >= at_percent:
            continue
        # Measured seats first, least spent first; an unmeasured seat is a
        # last resort, never preferred over one known to have budget.
        ranked.append((0 if spent is not None else 1, spent or 0.0, order, account))
    if not ranked:
        return None
    for item in ranked:
        if item[3].id == prefer:
            return item[3]
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    return ranked[0][3]


def preferred_seat(platform: str, account_id: str | None) -> str | None:
    """The seat a NEW turn of ``platform`` should run on instead of ``account_id``.

    For callers outside the panes (chat threads, agents): when the seat they
    would use is out of rotation and auto switch is on, the active seat —
    which the watch has already moved to the next subscription — is the
    answer, but only when it is a signed-in SUBSCRIPTION: a turn is never
    rerouted onto a login that bills per token. ``None`` means "keep yours".
    No network, no spawn; the file reads happen only for a spent seat.
    """
    from jarvis import agent_accounts

    if not account_id or not is_exhausted(account_id):
        return None
    if not agent_accounts.auto_switch().enabled:
        return None
    active = agent_accounts.active_account(platform)
    if active.id == account_id or is_exhausted(active.id):
        return None
    snapshot = agent_accounts.describe(active)
    if not snapshot.connected or snapshot.mode != "subscription":
        return None
    return active.id


# --------------------------------------------------------------- the moves


def _local_panes(registry: Registry, platform: str) -> list[tuple[Session, Terminal]]:
    from .session import has_accounts

    return [
        (session, term)
        for session in registry.sessions
        for term in session.terminals
        if term.agent == platform and not term.computer_id and has_accounts(term.agent)
    ]


def _record(event: SwitchEvent) -> None:
    _STATE.events.append(event)
    logger.info(
        "Seat switch ({}): {} {} -> {} — {} moved, {} after their turn",
        event.reason,
        event.platform,
        event.from_label or "-",
        event.to_label or "-",
        event.moved,
        event.queued,
    )


async def _busy(registry: Registry, term: Terminal) -> bool:
    """Is the pane working, asking, starting, or being typed into?"""
    if term.status != "live" or not term.pty_id:
        return False
    typed_at = getattr(term, "last_input_at", None)
    if typed_at and _now() - typed_at < TYPING_GRACE_S:
        return True
    try:
        state = await registry.turn_in_progress(term)
    except Exception as exc:  # noqa: BLE001 - unknown counts as busy: never cut work off
        logger.debug("Seat switch: could not read {}'s turn: {}", term.name, exc)
        return True
    return state in ("working", "starting", "asking")


def _idle_check(registry: Registry) -> Callable[[Terminal], Awaitable[bool]]:
    async def idle(term: Terminal) -> bool:
        return not await _busy(registry, term)

    return idle


def _nudge(registry: Registry, session: Session, term: Terminal, text: str) -> None:
    """Tell a pane that was cut off by a limit to carry on, once it is ready."""

    async def send() -> None:
        try:
            await registry.send_prompt(
                "pane:" + term.history_id, text, workspace_id=session.id, require_idle=True
            )
        except Exception as exc:  # noqa: BLE001 - the pane keeps its seat either way
            logger.info("Seat switch: {} was not told to continue: {}", term.name, exc)

    task = asyncio.ensure_future(send())
    _STATE.nudges.add(task)
    task.add_done_callback(_STATE.nudges.discard)


async def _move_panes(
    registry: Registry,
    platform: str,
    target: str,
    *,
    only_from: set[str] | None,
    skip: set[str],
    force: set[int],
) -> dict[int, tuple[Session, Terminal, str]]:
    """Move panes of ``platform`` onto ``target``; busy ones after their turn.

    ``only_from`` limits the move to panes on those seats; ``skip`` never
    moves panes on those seats (the target's own login). ``force`` holds panes
    (by ``id``) stopped by a limit — their turn is over even when the
    transcript has not recorded its end yet. Every other pane is moved only
    if it is still idle under its own lock; otherwise it waits
    (``pending_account``) and the watch moves it when its turn ends.

    Returns each pane's outcome: moved, repointed, deferred, unchanged, failed.
    """
    idle = _idle_check(registry)
    chosen: list[tuple[Session, Terminal]] = []
    for session, term in _local_panes(registry, platform):
        if term.account == target or term.account in skip:
            term.pending_account = None
            continue
        if only_from is not None and term.account not in only_from:
            continue
        chosen.append((session, term))

    async def one(term: Terminal) -> str:
        try:
            return await registry.move_to_seat(
                term, target, idle=None if id(term) in force else idle
            )
        except Exception as exc:  # noqa: BLE001 - one pane must not stop the others
            logger.warning("Seat switch: {} could not be moved: {}", term.name, exc)
            return "failed"

    # Together: each pane restarts under its own lock, and a grid of agents
    # standing still one after another is exactly what a switch must avoid.
    outcomes = await asyncio.gather(*(one(term) for _session, term in chosen))
    return {
        id(term): (session, term, outcome)
        for (session, term), outcome in zip(chosen, outcomes, strict=True)
    }


def _count(outcomes: dict[int, tuple[Session, Terminal, str]]) -> tuple[int, int]:
    moved = sum(1 for *_pair, outcome in outcomes.values() if outcome in ("moved", "repointed"))
    queued = sum(1 for *_pair, outcome in outcomes.values() if outcome == "deferred")
    return moved, queued


async def _switch(
    registry: Registry,
    platform: str,
    account_id: str,
    *,
    reason: str,
    only_from: set[str] | None = None,
    stopped: list[tuple[Session, Terminal]] | None = None,
) -> tuple[SwitchEvent, dict[int, tuple[Session, Terminal, str]]]:
    """:func:`switch_seat`, also returning every pane's outcome. Holds the lock."""
    from jarvis import agent_accounts

    account = await asyncio.to_thread(agent_accounts.resolve, account_id)
    if account is None or account.platform != platform:
        raise agent_accounts.AccountError("That account no longer exists.")
    previous = await asyncio.to_thread(agent_accounts.active_account, platform)
    await asyncio.to_thread(agent_accounts.set_active, platform, account.id)
    same_login = await asyncio.to_thread(_same_login_ids, platform, {account.id})
    if reason == "manual":
        # The user knows something the readings do not (a reset, an
        # upgrade): their pick is back in rotation, and the threshold leaves
        # it to them until the provider actually refuses it.
        for seat in same_login:
            _STATE.exhausted.pop(seat, None)
        _STATE.manual[platform] = account.id
    elif _STATE.manual.get(platform) != account.id:
        _STATE.manual.pop(platform, None)
    forced = {id(term) for _session, term in (stopped or [])}
    outcomes = await _move_panes(
        registry,
        platform,
        account.id,
        only_from=only_from,
        skip=same_login - {account.id},
        force=forced,
    )
    moved, queued = _count(outcomes)
    event = SwitchEvent(
        at=_now(),
        platform=platform,
        reason=reason,
        to_account=account.id,
        to_label=account.label,
        from_label=previous.label if previous.id != account.id else "",
        moved=moved,
        queued=queued,
    )
    _record(event)
    for session, term, outcome in outcomes.values():
        if id(term) in forced and outcome == "moved":
            _nudge(registry, session, term, CONTINUE_PROMPT)
    return event, outcomes


async def switch_seat(
    registry: Registry,
    platform: str,
    account_id: str,
    *,
    reason: str = "manual",
    only_from: set[str] | None = None,
) -> SwitchEvent:
    """Make ``account_id`` the active seat of ``platform`` and move the work.

    Every pane of that CLI follows — except panes already on the same login
    as the target, which would restart for nothing. ``only_from`` restricts
    the move to panes on the seats being left (an automatic switch leaves
    panes on other healthy seats alone).
    """
    async with _lock():
        event, _outcomes = await _switch(
            registry, platform, account_id, reason=reason, only_from=only_from
        )
    return event


def _report_empty(platform: str, leaving: set[str], labels: str, reason: str) -> None:
    key = (platform, frozenset(leaving))
    if key in _STATE.reported_empty:
        return
    _STATE.reported_empty.add(key)
    _record(
        SwitchEvent(
            at=_now(),
            platform=platform,
            reason="no_seat",
            to_account=None,
            to_label="",
            from_label=labels,
        )
    )
    logger.warning(
        "Seat switch ({}): {} has no other subscription with budget left — work stays put",
        reason,
        platform,
    )


# ------------------------------------------------------------------- watch


async def _apply_pending(registry: Registry) -> None:
    """Carry out moves that waited for a turn to end."""
    idle = _idle_check(registry)
    for session in list(registry.sessions):
        for term in list(session.terminals):
            if not term.pending_account:
                continue
            async with _lock():
                # Re-read under the lock: a switch may have moved the pane or
                # changed its target while this tick was waiting.
                target = term.pending_account
                if not target or term.account == target:
                    term.pending_account = None
                    continue
                if await _busy(registry, term):
                    continue
                try:
                    await registry.move_to_seat(term, target, idle=idle)
                except Exception as exc:  # noqa: BLE001 - one pane must not stop the watch
                    logger.warning("Seat switch: {} could not be moved: {}", term.name, exc)
                    term.pending_account = None


def _limit_stops(registry: Registry) -> list[tuple[Session, Terminal, float]]:
    """Panes whose last turn a usage limit cut off. Filesystem: off the loop."""
    from . import seat_handoff
    from .session import account_home, has_accounts

    found: list[tuple[Session, Terminal, float]] = []
    for session in list(registry.sessions):
        for term in list(session.terminals):
            handle = term.resume
            if (
                term.computer_id
                or term.status != "live"
                or handle is None
                or not has_accounts(term.agent)
                or not seat_handoff.can_detect_limit(handle.kind)
            ):
                continue
            home = account_home(term.agent, term.account)
            if home is None:
                continue
            stop = seat_handoff.limit_stop(
                handle.kind,
                handle.id,
                home,
                captured_at=handle.captured_at,
                after=max(term.limit_handled_at, handled_stop(handle.id)),
            )
            if stop is not None:
                found.append((session, term, stop))
    return found


async def _answered(term: Terminal, stop: float) -> None:
    term.limit_handled_at = max(term.limit_handled_at, stop)
    if term.resume is not None:
        await asyncio.to_thread(_remember_handled, term.resume.id, stop)


async def _handle_limit_stops(registry: Registry, at_percent: float) -> None:
    from jarvis import agent_accounts

    stops = await asyncio.to_thread(_limit_stops, registry)
    by_platform: dict[str, list[tuple[Session, Terminal, float]]] = {}
    for item in stops:
        by_platform.setdefault(item[1].agent, []).append(item)
    for platform, items in by_platform.items():
        leaving = {term.account for _s, term, _at in items if term.account}
        usage = await asyncio.to_thread(_cached_usage, platform)
        leaving_all = await asyncio.to_thread(_same_login_ids, platform, leaving)
        fresh = [
            (term, at)
            for _s, term, at in items
            if term.resume is not None and (term.resume.id, at) not in _STATE.seen_stops
        ]
        if fresh:
            # Out of rotation once per stop, until its refill: a stop that
            # waits for a seat must not push that refill back every tick.
            until = max((_refill_at(usage.get(a), 99.0) or 0.0 for a in leaving), default=0.0)
            _set_exhausted(leaving_all, until or None)
            _STATE.seen_stops.update((term.resume.id, at) for term, at in fresh if term.resume)
        active = await asyncio.to_thread(agent_accounts.active_account, platform)
        seat = await asyncio.to_thread(
            functools.partial(choose_seat, platform, leaving, usage, at_percent, prefer=active.id)
        )
        if seat is None:
            refilled = not any(is_exhausted(a) for a in leaving_all) and all(
                (percent := spent_percent(usage.get(a))) is not None and percent < at_percent
                for a in leaving
            )
            if refilled:
                # Nowhere else to go, but the seat itself measurably refilled:
                # carry on where it is. Never on a guess — a "continue" into a
                # seat that is still spent is refused all over again.
                for session, term, at in items:
                    _nudge(registry, session, term, RESUME_PROMPT)
                    await _answered(term, at)
                labels = await asyncio.to_thread(
                    lambda ids=frozenset(leaving): ", ".join(sorted(_label(a) for a in ids))
                )
                _record(
                    SwitchEvent(
                        at=_now(),
                        platform=platform,
                        reason="refilled",
                        to_account=None,
                        to_label=labels,
                        from_label=labels,
                        moved=len(items),
                    )
                )
                continue
            labels = await asyncio.to_thread(
                lambda ids=frozenset(leaving): ", ".join(sorted(_label(a) for a in ids))
            )
            _report_empty(platform, leaving, labels, "limit")
            continue
        _STATE.reported_empty.discard((platform, frozenset(leaving)))
        async with _lock():
            _event, outcomes = await _switch(
                registry,
                platform,
                seat.id,
                reason="limit",
                only_from=leaving_all,
                stopped=[(session, term) for session, term, _at in items],
            )
        for _session, term, at in items:
            outcome = outcomes.get(id(term), (None, None, "unchanged"))[2]
            # Answered only once the pane really is on a seat with budget; a
            # failed move is tried again on the next tick.
            if outcome in ("moved", "repointed") or term.account == seat.id:
                await _answered(term, at)


def _label(account_id: str | None) -> str:
    from .session import account_label

    return account_label(account_id) or (account_id or "")


def _cached_usage(platform: str) -> dict[str, AccountUsage]:
    """This platform's usage from the shared cache, reading only what is stale."""
    from jarvis import agent_accounts, agent_usage

    if not agent_usage.supports_usage(platform):
        return {}
    return agent_usage.collect(agent_accounts.list_accounts(platform))  # type: ignore[arg-type]


async def _check_thresholds(registry: Registry, at_percent: float) -> None:
    """Move work off seats whose plan is nearly spent."""
    from jarvis import agent_accounts

    platforms: dict[str, set[str]] = {}
    for platform in agent_accounts.platforms():
        for _session, term in _local_panes(registry, platform):
            if term.status == "live" and term.account:
                platforms.setdefault(platform, set()).add(term.account)
    for platform, in_use in platforms.items():
        usage = await asyncio.to_thread(_cached_usage, platform)
        if not usage:
            continue
        active = await asyncio.to_thread(agent_accounts.active_account, platform)
        manual = _STATE.manual.get(platform)
        watched = in_use | {active.id}
        spent = {
            account_id
            for account_id in watched
            if account_id != manual
            and (percent := spent_percent(usage.get(account_id), scoped=False)) is not None
            and percent >= at_percent
        }
        if not spent:
            continue
        leaving_all = await asyncio.to_thread(_same_login_ids, platform, spent)
        until = max(
            (_refill_at(usage.get(a), at_percent, scoped=False) or 0.0 for a in spent),
            default=0.0,
        )
        _set_exhausted(leaving_all, until or None)
        seat = await asyncio.to_thread(
            functools.partial(
                choose_seat, platform, spent, usage, at_percent, prefer=active.id, scoped=False
            )
        )
        if seat is None:
            labels = await asyncio.to_thread(
                lambda ids=frozenset(spent): ", ".join(sorted(_label(a) for a in ids))
            )
            _report_empty(platform, spent, labels, "threshold")
            continue
        _STATE.reported_empty.discard((platform, frozenset(spent)))
        async with _lock():
            await _switch(registry, platform, seat.id, reason="threshold", only_from=leaving_all)


async def _tick(registry: Registry) -> None:
    from jarvis import agent_accounts

    await _apply_pending(registry)
    choice = await asyncio.to_thread(agent_accounts.auto_switch)
    if not choice.enabled:
        return
    await _handle_limit_stops(registry, choice.at_percent)
    if _now() >= _STATE.next_usage_at:
        _STATE.next_usage_at = _now() + USAGE_INTERVAL_S + random.uniform(0, USAGE_JITTER_S)  # noqa: S311
        await _check_thresholds(registry, choice.at_percent)


async def _run(registry: Registry) -> None:
    """Watch until the last workspace closes; the next one starts it again."""
    try:
        while True:
            await asyncio.sleep(WATCH_INTERVAL_S)
            if not registry.sessions:
                return
            try:
                await _tick(registry)
            except Exception as exc:  # noqa: BLE001 - one bad tick must not end the watch
                logger.warning("Seat switch: watch tick failed: {}", exc)
    except asyncio.CancelledError:  # pragma: no cover - shutdown
        raise
    finally:
        _STATE.task = None


def start(registry: Registry) -> None:
    """Make sure the watch runs for ``registry``. Idempotent."""
    if _STATE.task is not None and not _STATE.task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _STATE.task = loop.create_task(_run(registry), name="agentic-ide-seat-switch")


def stop() -> None:
    task = _STATE.task
    _STATE.task = None
    if task is not None and not task.done():
        task.cancel()


def status() -> dict[str, Any]:
    """What the panel shows: the setting, seats out of rotation, recent switches."""
    from jarvis import agent_accounts

    moment = _now()
    choice = agent_accounts.auto_switch()
    return {
        **choice.to_dict(),
        "watching": _STATE.task is not None and not _STATE.task.done(),
        "exhausted": [
            {"account_id": account_id, "until": until}
            for account_id, until in sorted(_STATE.exhausted.items())
            if until > moment
        ],
        "events": [event.to_dict() for event in reversed(_STATE.events)],
    }


__all__ = [
    "CONTINUE_PROMPT",
    "SwitchEvent",
    "choose_seat",
    "handled_stop",
    "is_exhausted",
    "preferred_seat",
    "reset",
    "spent_percent",
    "start",
    "status",
    "stop",
    "switch_seat",
]
