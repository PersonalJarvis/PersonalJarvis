"""Decide which finished run a human actually wants to hear about.

A scheduler that reports every run is noise. The URL healthcheck in
``conductor/seed/url_healthcheck.yaml`` runs every 300 seconds — announcing
its 300th consecutive success carries exactly zero information, and after two
days of that the user has learned to ignore Conductor entirely.

What carries information is a **change of state**:

* a job that was fine and has just started failing — that is news;
* a job that was failing and has just recovered — that is news;
* anything else (a repeat pass, a repeat failure) — silence.

The first run of a job has no predecessor. A first failure is still news (the
job never worked); a first success is not (nobody needs "your new job worked",
least of all right after boot, when every seed job runs for the first time).

This module deliberately produces **structured facts, never a sentence**.
Conductor is a standalone tool with no Jarvis import and no notion of the
user's language; the embedding host renders the wording and picks the locale.
See ``Runner._emit`` for how the facts leave the package.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["NEWS_EVENT", "RunNews", "classify_run"]

#: The ``on_event`` name a piece of news is emitted under. Distinct from the
#: ``run.*`` lifecycle events so a dashboard subscriber (which wants every run)
#: and a notification subscriber (which wants only the news) never fight over
#: the same stream.
NEWS_EVENT = "job.news"

#: ``failing`` — the job just broke. ``recovered`` — the job works again.
NewsKind = Literal["failing", "recovered"]

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


def classify_run(
    *,
    job_id: str,
    job_name: str,
    run_id: str,
    trigger: str,
    previous_state: str | None,
    new_state: str,
    error: str | None = None,
) -> RunNews | None:
    """Return the news in this run, or ``None`` when the run is not news.

    ``previous_state`` is the job's last terminal state before this run
    (``None`` when the job has never finished one). ``new_state`` is this
    run's terminal state (``completed`` / ``failed`` / ``cancelled``).

    A cancelled run is never news: somebody cancelled it on purpose, so they
    already know.
    """
    prev = (previous_state or "").strip().lower() or None
    new = (new_state or "").strip().lower()

    if new == _STATE_FAILED and prev != _STATE_FAILED:
        kind: NewsKind = "failing"
    elif new == _STATE_COMPLETED and prev == _STATE_FAILED:
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
