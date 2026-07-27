"""How fast the pipeline is actually moving, and what that means in days.

Why this module exists (2026-07-27 forensic). A maintainer watched the Wiki
section for five hours and reported it had converted "about 200 files". The
screen he was reading said, in order:

* ``Ready to answer — and still filling up.``
* ``235 915 of 236 131 are still being prepared for meaning search.``
* ``Everything else can be searched right now — you do not have to wait.``

Every one of those sentences was true. Together they were a lie of omission,
because the screen never answered the only question a 236 000-item backlog
raises: **how long?** Measured on that live store the embedding stage was
moving 0.65 items per second — a little over four days of uninterrupted work
for the queue on screen, and that is before a single summary is written. "You
do not have to wait" is not a defensible thing to print over four days.

The missing number was never unknowable. The worker has always counted its
own successful transitions; nothing sampled that counter over time and divided.
This module does exactly that and nothing else.

Design rules, all of them about not replacing one confident lie with another:

* **Measured, never assumed.** A rate comes only from observed work. There is
  no hardcoded "items per second" for any backend, and no estimate derived
  from a provider or model name (AP-21) — a local model on a fast machine and
  a cloud key on a throttled account are the same code path here.
* **Silence beats a guess.** Below :data:`MIN_SAMPLE_SPAN_S` of observation or
  :data:`MIN_SAMPLE_WORK` of completed work the answer is ``None``, and the UI
  says it is still measuring. A first-minute extrapolation is how a progress
  bar earns the reputation this one is trying to lose.
* **A stalled lane reports a stall, not eternity.** Zero work over a full
  window is ``rate == 0.0`` with ``eta_seconds is None`` — "not moving",
  which is actionable, rather than an infinity dressed up as a date.
* **The window forgets.** Only the last :data:`WINDOW_S` of samples count, so
  a rate that collapses when a big folder arrives shows up as a collapse
  instead of being averaged away by a fast first hour.
"""

from __future__ import annotations

from collections import deque
from typing import Any

__all__ = [
    "MIN_SAMPLE_SPAN_S",
    "MIN_SAMPLE_WORK",
    "WINDOW_S",
    "ThroughputTracker",
    "build_throughput",
    "format_duration",
]

#: How much recent history a rate is averaged over. Long enough that one slow
#: batch does not swing it, short enough that a genuine slowdown surfaces
#: within minutes rather than being diluted by a faster hour.
WINDOW_S = 900.0

#: Minimum observation span before any rate is published. A pipeline that just
#: started has no honest rate, and printing one anyway is the whole defect.
MIN_SAMPLE_SPAN_S = 90.0

#: Minimum completed items in the window before a rate is published. Guards the
#: other direction: 15 minutes in which exactly one item finished says nothing
#: about the next thousand.
MIN_SAMPLE_WORK = 5


class ThroughputTracker:
    """A sliding window over one cumulative counter.

    The caller owns the clock (``now`` is passed in, never read here) so tests
    are deterministic and the production caller can keep using the monotonic
    clock a wall-clock jump cannot corrupt.

    One tracker per lane: the embedding stage and the distillation stage move
    at completely different speeds, and averaging them produces a number that
    describes neither.
    """

    def __init__(self, *, window_s: float = WINDOW_S) -> None:
        self._window_s = max(1.0, float(window_s))
        #: (now, cumulative_done), oldest first.
        self._samples: deque[tuple[float, int]] = deque()

    def sample(self, now: float, done: int) -> None:
        """Record the cumulative counter at *now*, then drop stale samples.

        A counter that went BACKWARDS (the worker restarted and its per-stage
        counters began again at zero) resets the window instead of reporting
        the negative delta as a rate — a restart is missing history, not
        negative progress.
        """
        try:
            now = float(now)
            done = int(done)
        except (TypeError, ValueError):
            return
        if self._samples and done < self._samples[-1][1]:
            self._samples.clear()
        self._samples.append((now, done))
        horizon = now - self._window_s
        while len(self._samples) > 2 and self._samples[0][0] < horizon:
            self._samples.popleft()

    def rate_per_second(self) -> float | None:
        """Completed items per second, or ``None`` when not yet measurable.

        ``0.0`` is a real answer meaning "observed long enough, nothing moved";
        ``None`` means "not observed long enough to say".

        The work floor (:data:`MIN_SAMPLE_WORK`) applies only while the window
        is still filling. Once a FULL window has been observed, a small number
        is no longer a thin sample — it is the measurement. A lane creeping
        along at two items every quarter of an hour is exactly the case a
        backlog estimate is most needed for, and holding out for five would
        have kept that lane permanently unreportable: the slower it ran, the
        longer it stayed silent, which is precisely backwards.
        """
        if len(self._samples) < 2:
            return None
        first_at, first_done = self._samples[0]
        last_at, last_done = self._samples[-1]
        span = last_at - first_at
        if span < MIN_SAMPLE_SPAN_S:
            return None
        worked = last_done - first_done
        if worked <= 0:
            return 0.0
        if worked < MIN_SAMPLE_WORK and span < self._window_s:
            # Still filling the window, and too few items to divide into a
            # backlog of hundreds of thousands.
            return None
        return worked / span

    def snapshot(self) -> dict[str, Any]:
        """What was observed, for a surface that wants to show its working."""
        if len(self._samples) < 2:
            return {"rate_per_second": None, "measured_s": 0.0, "measured_items": 0}
        first_at, first_done = self._samples[0]
        last_at, last_done = self._samples[-1]
        return {
            "rate_per_second": self.rate_per_second(),
            "measured_s": round(max(0.0, last_at - first_at), 1),
            "measured_items": max(0, last_done - first_done),
        }


def build_throughput(
    *,
    embed: dict[str, Any],
    distill: dict[str, Any],
    embed_backlog: int,
    distill_backlog: int,
    distill_paused_reason: str = "",
) -> dict[str, Any]:
    """Assemble the honest "how long is left" payload for the status route.

    Args:
        embed: :meth:`ThroughputTracker.snapshot` of the embedding lane.
        distill: the same for the summarising lane.
        embed_backlog: items that still have to be embedded.
        distill_backlog: items that still have to be summarised.
        distill_paused_reason: why summaries are not running, if they are not.
            Carried through verbatim so the UI never has to infer a stall from
            a rate of zero — a paused lane and a dead lane look identical in
            the numbers and are completely different situations.

    Returns:
        One dict per lane with ``rate_per_hour``, ``backlog``, ``eta_seconds``
        (``None`` when unmeasurable or stalled) and ``paused_reason``, plus a
        top-level ``eta_seconds`` for the whole job: the two lanes summed,
        because an item is not finished until it has been summarised, and the
        stages run one after the other rather than side by side.
    """
    embed_lane = _lane(embed, embed_backlog)
    distill_lane = _lane(distill, distill_backlog, paused_reason=distill_paused_reason)

    # The total is only honest when BOTH halves are known. A paused summary
    # lane with an unmeasurable rate must not silently shorten the estimate to
    # just the embedding half — that is the same optimism this module exists
    # to remove. Report what is known per lane and leave the total open.
    total: float | None
    if embed_lane["eta_seconds"] is None or distill_lane["eta_seconds"] is None:
        total = None
    else:
        total = embed_lane["eta_seconds"] + distill_lane["eta_seconds"]

    return {
        "embed": embed_lane,
        "distill": distill_lane,
        "eta_seconds": total,
        # True when something is genuinely being observed to move. The UI uses
        # this to decide between "measuring…" and a number.
        "measured": embed_lane["rate_per_hour"] is not None
        or distill_lane["rate_per_hour"] is not None,
    }


def _lane(
    snapshot: dict[str, Any], backlog: int, *, paused_reason: str = ""
) -> dict[str, Any]:
    """One lane's measured rate, backlog and resulting ETA."""
    rate_s = snapshot.get("rate_per_second")
    backlog = max(0, int(backlog or 0))
    eta: float | None = None
    rate_per_hour: float | None = None
    if isinstance(rate_s, (int, float)):
        rate_per_hour = round(float(rate_s) * 3600.0, 1)
        if backlog == 0:
            eta = 0.0
        elif rate_s > 0:
            eta = backlog / float(rate_s)
        # rate == 0 with a backlog: standing still. eta stays None on purpose —
        # "never at this rate" is not a duration, and printing one implies the
        # queue is moving.
    return {
        "rate_per_hour": rate_per_hour,
        "backlog": backlog,
        "eta_seconds": eta,
        "measured_s": float(snapshot.get("measured_s") or 0.0),
        "measured_items": int(snapshot.get("measured_items") or 0),
        "stalled": rate_per_hour == 0.0 and backlog > 0,
        "paused_reason": str(paused_reason or ""),
    }


def format_duration(seconds: float | None) -> str:
    """A duration a person reads at a glance, or ``""`` when unknown.

    Deliberately coarse and deliberately English-source-only (the UI composes
    the localized sentence around this from its own strings): the difference
    between "4 days" and "4 days 7 hours" changes no decision anyone makes
    while looking at a backlog this size.
    """
    if seconds is None:
        return ""
    try:
        total = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return ""
    if total < 90:
        return "under a minute"
    minutes = total / 60.0
    if minutes < 90:
        return f"{round(minutes)} minutes"
    hours = minutes / 60.0
    if hours < 36:
        return f"{round(hours)} hours"
    days = hours / 24.0
    if days < 14:
        return f"{days:.1f} days".replace(".0 ", " ")
    weeks = days / 7.0
    if weeks < 9:
        return f"{round(weeks)} weeks"
    return f"{round(days / 30.0)} months"
