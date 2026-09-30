"""Deterministic background-billing state for wiki tests.

Two pieces of process-wide state decide what a background wiki model call may
bill: the subscription policy (``jarvis.brain.background_policy`` — a login
probe plus a sticky "a subscription was seen" marker on disk) and the wiki's
runaway guard (``jarvis.memory.wiki.background_guard`` — a persisted daily
call counter and a backoff window). Both read the developer's real user-data
directory by default, so a suite run on a machine with a signed-in CLI would
silently switch every wiki test into subscription mode.

:class:`BackgroundBilling` pins both to a temporary directory and a scripted
login table. The default is a key-only install: no subscription signed in,
nothing remembered, zero calls spent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from jarvis.brain import background_policy
from jarvis.memory.wiki.background_guard import guard


@dataclass
class BackgroundBilling:
    """Scripted login states plus isolated marker/counter files."""

    root: Path
    logins: dict[str, bool | None] = field(default_factory=dict)

    def sign_in(self, provider: str) -> None:
        """Make *provider*'s subscription login answer ``True`` from now on."""
        self.logins[provider] = True
        background_policy._probe_cache.pop(provider, None)  # noqa: SLF001 - test seam

    def sign_out(self, provider: str) -> None:
        """The login now answers ``False``; the sticky marker is kept."""
        self.logins[provider] = False
        background_policy._probe_cache.pop(provider, None)  # noqa: SLF001 - test seam

    def remember_subscription(self, provider: str = "claude-cli") -> None:
        """Record that a subscription was seen connected (subscription mode)."""
        background_policy.note_connected(provider)

    @property
    def calls_today(self) -> int:
        return guard.calls_today()


def isolate_background_billing(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
) -> BackgroundBilling:
    """Point the policy and the guard at *root* and script the login probe."""
    root.mkdir(parents=True, exist_ok=True)
    billing = BackgroundBilling(root=root)
    marker = root / "subscription_seen.json"
    monkeypatch.setattr(background_policy, "_marker_path", lambda: marker)
    background_policy.reset_for_tests()
    background_policy._probe_override = billing.logins.get  # noqa: SLF001 - test seam
    guard.reset_for_tests(state_path=root / "wiki_background_calls.json")
    from jarvis.memory.wiki.health import health

    health.clear_background_wait()
    return billing


def reset_background_billing() -> None:
    from jarvis.memory.wiki.health import health

    background_policy.reset_for_tests()
    guard.reset_for_tests()
    health.clear_background_wait()


__all__ = [
    "BackgroundBilling",
    "isolate_background_billing",
    "reset_background_billing",
]
