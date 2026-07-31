"""Request budget for the dictation LIVE PREVIEW.

Measured on the maintainer's box (2026-07-29, from ``jarvis_desktop.log``): a
dictation spends roughly **40 provider requests per minute of speech**. Only
about 7.5 of those — one per closed segment — produce any of the final text.
The rest are live-preview calls: they re-transcribe the open tail every
``partial_interval_s`` so words appear while you are still speaking, and then
their result is thrown away and rebuilt on the next tick.

That is 85 % of the traffic buying a cosmetic feature, and it is what pushed a
137 s dictation past the provider's per-minute limit: five refused calls, and
367 characters returned for over two minutes of speech. The user's actual words
lost to a preview nobody keeps.

The budget below fixes the priority the old loop had backwards:

* **Segment closes and the final pass are never budgeted.** They ARE the
  transcript. Rationing them to protect a preview would be absurd.
* **The preview spends what is left, and skips when there is nothing.** A
  preview that updates every few seconds instead of every 1.2 s is a
  cosmetic difference; a transcript with a hole in it is not.

It is deliberately PROCESS-WIDE, not per session. Provider limits are rolling
windows, so three back-to-back 20-second dictations hit the same limit one long
one does — a per-session budget would reset each time and rebuild the exact
problem it is meant to prevent.

The backoff in the dictation loop stays: this keeps the limit from being
reached, the backoff recovers when something else reaches it anyway (a shared
key, another app, a tighter tier than we assumed).
"""

from __future__ import annotations

import threading
import time

#: Preview calls allowed per rolling minute, across every dictation in this
#: process.
#:
#: The budget is sized against a MEASURED limit, not a guess. Groq publishes 20
#: RPM / 2000 RPD for both whisper-large-v3 and whisper-large-v3-turbo, and —
#: this is the part worth knowing — the paid Developer plan carries the SAME 20
#: RPM as the free plan, so no amount of money buys a way out of this. The 2000
#: RPD matches the ``x-ratelimit-limit-requests`` header the maintainer's own
#: account returns, which is what confirms the figures apply here.
#:
#: 20 RPM minus the segment traffic that must always fit (~7.5/min at the
#: default 8 s segments) leaves ~12. Ten keeps a margin for the final pass and
#: its retries. Guessing high rebuilds the very failure this prevents; guessing
#: low only makes the preview lag a little, so the asymmetry decides.
PREVIEW_CALLS_PER_MINUTE = 10

_WINDOW_S = 60.0


class PreviewBudget:
    """Token budget over a rolling window. Thread-safe, allocation-light."""

    def __init__(self, calls_per_minute: int = PREVIEW_CALLS_PER_MINUTE) -> None:
        self._limit = max(0, int(calls_per_minute))
        self._lock = threading.Lock()
        self._stamps: list[float] = []

    def _prune(self, now: float) -> None:
        cutoff = now - _WINDOW_S
        if self._stamps and self._stamps[0] < cutoff:
            self._stamps = [t for t in self._stamps if t >= cutoff]

    def try_spend(self) -> bool:
        """Claim one preview call. ``False`` means: skip the preview this tick."""
        if self._limit <= 0:
            return False
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if len(self._stamps) >= self._limit:
                return False
            self._stamps.append(now)
            return True

    def remaining(self) -> int:
        """How many preview calls the current window still allows."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            return max(0, self._limit - len(self._stamps))

    def reset(self) -> None:
        """Drop the window — test-isolation hook."""
        with self._lock:
            self._stamps = []


#: The one budget every dictation shares. Module-level for the reason in the
#: docstring: consecutive dictations meet the same rolling provider window.
_BUDGET = PreviewBudget()


def preview_budget() -> PreviewBudget:
    """The shared preview budget."""
    return _BUDGET


__all__ = [
    "PREVIEW_CALLS_PER_MINUTE",
    "PreviewBudget",
    "preview_budget",
]
