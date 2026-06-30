"""Section-level health rollup for the API-Keys view's tab indicators.

The API-Keys screen groups providers into segmented tabs — Brain, Voice Output
(TTS), Voice Input (STT), Subagents and the de-emphasized Advanced tab. A single
glanceable dot on each tab answers the only question that matters at that
altitude: *is the part of Jarvis this tab controls actually working right now?*

That is deliberately NOT "does any provider in this tab lack a key" — most
providers are intentionally left empty (you only configure the one you use), so a
"missing key anywhere" signal would paint every tab permanently red. Instead a tab
reflects the health of the ONE thing it drives: the active provider of its tier
(or, for Subagents, the selected worker; for Advanced, the optional integrations a
user has actually set up).

This module is the pure, dependency-free core: the status vocabulary (single
source of truth, mirrored by the Pydantic ``Literal`` in ``provider_routes`` and
the TS union in ``useProviders.ts`` — five-layer anti-drift, BUG-008 class) plus
the two pure functions the endpoint composes. All I/O (resolving the active
provider, running the real connectivity test) lives in the route; everything here
is trivially unit-testable.
"""
from __future__ import annotations

from collections.abc import Iterable

# ── Status vocabulary — SINGLE SOURCE OF TRUTH ────────────────────────────────
# Mirrored by ``SectionHealthStatusLiteral`` (provider_routes) and the TS
# ``SectionHealthStatus`` union (useProviders.ts). A parity test asserts the
# Python ↔ Pydantic side stays in lock-step.
OK = "ok"                       # set up and a live check confirmed it answers
NEEDS_SETUP = "needs_setup"     # the thing this tab drives has no usable credential yet
ERROR = "error"                 # set up, but failing its live check (bad key / no credits / down)
UNKNOWN = "unknown"             # not checked yet / nothing applicable to report — render NO dot

SECTION_HEALTH_STATUSES: tuple[str, ...] = (OK, NEEDS_SETUP, ERROR, UNKNOWN)

# Display intent (the route never re-derives this): only NEEDS_SETUP (amber) and
# ERROR (red) draw a dot; OK and UNKNOWN are silent. Severity orders the rollup so
# the most urgent contributing signal wins when a tab aggregates several checks.
_SEVERITY: dict[str, int] = {UNKNOWN: 0, OK: 1, NEEDS_SETUP: 2, ERROR: 3}

# Per-provider connectivity-test statuses (``jarvis.brain.provider_test``) that
# mean "reached, but broken for this account/key/model" — i.e. the integration is
# sound yet the tier is NOT functional right now. They all roll up to ERROR. The
# only non-error outcomes are ``ok`` (→ OK) and ``not_configured`` (→ NEEDS_SETUP).
_TEST_OK = "ok"
_TEST_NOT_CONFIGURED = "not_configured"


def section_status_for_test(test_status: str | None, *, configured: bool) -> str:
    """Map one provider's state into a section bucket.

    ``configured`` is the cheap credential-PRESENCE signal (a key string / login
    is stored); ``test_status`` is the honest live-call outcome from
    ``provider_test`` (``None`` when no test was run — e.g. the credential is
    missing so there is nothing to call).

    Rules:
      * no credential                → ``needs_setup`` (the user hasn't set this up)
      * credential, test ``ok``      → ``ok``
      * credential, test ``not_configured`` → ``needs_setup`` (the call itself
        found no key — treat as not set up, never as a hard error)
      * credential, any other status → ``error`` (bad key / no credits /
        rate-limited / model unavailable / unreachable / integration bug)
      * credential, no test run yet  → ``unknown`` (don't claim health we lack)
    """
    if not configured:
        return NEEDS_SETUP
    if test_status is None:
        return UNKNOWN
    if test_status == _TEST_OK:
        return OK
    if test_status == _TEST_NOT_CONFIGURED:
        return NEEDS_SETUP
    return ERROR


def aggregate(statuses: Iterable[str]) -> str:
    """Roll several section statuses into one — the most urgent signal wins.

    Used for the Advanced tab (several optional integrations) and anywhere a tab
    has more than one contributing check. Severity order: ``error`` >
    ``needs_setup`` > ``ok`` > ``unknown``. An empty input is ``unknown`` (the tab
    has nothing to say, so it stays silent).
    """
    items = list(statuses)
    if not items:
        return UNKNOWN
    return max(items, key=lambda s: _SEVERITY.get(s, 0))


__all__ = [
    "SECTION_HEALTH_STATUSES",
    "OK",
    "NEEDS_SETUP",
    "ERROR",
    "UNKNOWN",
    "section_status_for_test",
    "aggregate",
]
