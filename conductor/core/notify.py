"""Decide which finished run a human actually wants to hear about.

A scheduler that reports every run is noise. The URL healthcheck in
``conductor/seed/url_healthcheck.yaml`` runs every 300 seconds — announcing
its 300th consecutive success carries exactly zero information, and after two
days of that the user has learned to ignore Conductor entirely.

What carries information is a **change of state**:

* a job that was fine and has just started failing — that is news;
* a job that was failing and has just recovered — that is news;
* anything else (a repeat pass, a repeat failure) — silence.

Two refinements, both learned from the live box (2026-08-23):

**A single blip is not a breakage.** The 5-minute GitHub healthcheck hit one
504, one 403 (GitHub's unauthenticated rate limit) and a handful of DNS
drops, and every one of them woke the user twice — "has started failing",
then six minutes later "is working again". Every monitoring product that
people keep installed (Pingdom, UptimeRobot, healthchecks.io) confirms a
failure across several consecutive checks before it alerts. So a job that
runs frequently announces "failing" only once it has failed
:data:`FAILURES_TO_ANNOUNCE_FREQUENT` times in a row. A job that runs rarely
(daily cron, manual, webhook, long interval) cannot afford to wait three
runs — three days of silence about a broken daily report is worse than one
false alarm — so it still announces on the first failure. The threshold is
derived from the schedule by :func:`failures_to_announce`.

**A recovery is only news when the breakage was.** A failure that stayed
under the threshold was never spoken, so "it works again" would answer a
question nobody heard. The host tracks which jobs it announced as failing
(the Runner does, in memory) and passes that in as ``failing_was_announced``;
the rule itself stays a pure function.

The first run of a job has no predecessor. A first failure is still news for
a rarely-running job (the job never worked); a first success is not (nobody
needs "your new job worked", least of all right after boot, when every seed
job runs for the first time).

This module deliberately produces **structured facts, never a sentence**.
Conductor is a standalone tool with no Jarvis import and no notion of the
user's language; the embedding host renders the wording and picks the locale.
See ``Runner._emit`` for how the facts leave the package.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "FAILURES_TO_ANNOUNCE_FREQUENT",
    "FREQUENT_INTERVAL_MAX_S",
    "NEWS_EVENT",
    "RunNews",
    "classify_run",
    "failures_to_announce",
]

#: The ``on_event`` name a piece of news is emitted under. Distinct from the
#: ``run.*`` lifecycle events so a dashboard subscriber (which wants every run)
#: and a notification subscriber (which wants only the news) never fight over
#: the same stream.
NEWS_EVENT = "job.news"

#: ``failing`` — the job just broke. ``recovered`` — the job works again.
NewsKind = Literal["failing", "recovered"]

#: An interval job that runs at least this often is "frequent": its next run
#: is minutes away, so waiting for confirmation costs little and saves a
#: false alarm. 15 minutes × 3 strikes = at most 45 minutes of an outage
#: before the user hears about it — and a 5-minute job confirms in 15.
FREQUENT_INTERVAL_MAX_S = 15 * 60

#: How many consecutive failures a frequent job needs before "failing" is
#: announced. Three is the industry habit (one blip, one retry, one confirm).
FAILURES_TO_ANNOUNCE_FREQUENT = 3

_STATE_FAILED = "failed"
_STATE_COMPLETED = "completed"

#: Technical error text is a diagnostic, not a sentence for the user. It rides
#: along trimmed so a host can show it in a log/transcript track, and it is
#: capped so a stack trace can never become the payload.
_DETAIL_MAX_CHARS = 160
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class RunNews:
    """One announcement-worthy fact about a job, in structured form."""

    job_id: str
    job_name: str
    kind: NewsKind
    state: str
    run_id: str
    trigger: str
    #: Trimmed technical reason (empty when there is none). Never a sentence
    #: meant to be read aloud.
    detail: str = ""

    def as_payload(self) -> dict[str, Any]:
        """Flat dict for the ``on_event`` callback — JSON-safe throughout."""
        return {
            "job_id": self.job_id,
            "job_name": self.job_name,
            "kind": self.kind,
            "state": self.state,
            "run_id": self.run_id,
            "trigger": self.trigger,
            "detail": self.detail,
        }


def _condense(text: str | None) -> str:
    """Collapse a multi-line error into one short, single-line diagnostic."""
    if not text:
        return ""
    flat = _WHITESPACE_RE.sub(" ", str(text)).strip()
    if len(flat) <= _DETAIL_MAX_CHARS:
        return flat
    return flat[: _DETAIL_MAX_CHARS - 1].rstrip() + "…"


def failures_to_announce(schedule_type: str | None, schedule_expr: str | None) -> int:
    """How many consecutive failures make a job's breakage news.

    ``schedule_type`` / ``schedule_expr`` are the denormalized columns of the
    ``jobs`` row (``"interval"`` + seconds as a string, ``"cron"`` + the
    expression, ``"manual"``, ``"webhook"``). Only a frequent interval job
    gets the confirmation window; everything else announces on the first
    failure because its next run is too far away to wait for.
    """
    if (schedule_type or "").strip().lower() != "interval":
        return 1
    try:
        seconds = int(float(str(schedule_expr or "").strip()))
    except ValueError:
        return 1
    if seconds <= 0:
        return 1
    return FAILURES_TO_ANNOUNCE_FREQUENT if seconds <= FREQUENT_INTERVAL_MAX_S else 1


def classify_run(
    *,
    job_id: str,
    job_name: str,
    run_id: str,
    trigger: str,
    previous_state: str | None,
    new_state: str,
    error: str | None = None,
    failure_streak: int = 1,
    failures_required: int = 1,
    failing_was_announced: bool = False,
) -> RunNews | None:
    """Return the news in this run, or ``None`` when the run is not news.

    ``previous_state`` is the job's last terminal state before this run
    (``None`` when the job has never finished one). ``new_state`` is this
    run's terminal state (``completed`` / ``failed`` / ``cancelled``).

    ``failure_streak`` counts consecutive failed runs INCLUDING this one when
    it failed; ``failures_required`` is the threshold from
    :func:`failures_to_announce`. "Failing" is news exactly when the streak
    reaches the threshold — not before (unconfirmed blip), not after (same
    breakage, already announced). ``failing_was_announced`` says whether the
    host actually spoke a "failing" for this breakage; only then is the
    recovery news.

    A cancelled run is never news: somebody cancelled it on purpose, so they
    already know.
    """
    prev = (previous_state or "").strip().lower() or None
    new = (new_state or "").strip().lower()
    required = max(1, int(failures_required))
    streak = max(1, int(failure_streak))

    if new == _STATE_FAILED:
        if streak != required:
            return None
        kind: NewsKind = "failing"
    elif new == _STATE_COMPLETED and prev == _STATE_FAILED:
        if not failing_was_announced:
            return None
        kind = "recovered"
    else:
        return None

    return RunNews(
        job_id=job_id,
        job_name=job_name,
        kind=kind,
        state=new,
        run_id=run_id,
        trigger=trigger,
        detail=_condense(error) if kind == "failing" else "",
    )
