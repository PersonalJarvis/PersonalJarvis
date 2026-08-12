"""The writer cache answers repeats; it must never answer a changed question.

Resolving a writer costs a config load plus a sign-in probe per candidate CLI,
and a fleet fan-out asks the identical question once per pane within the same
second. The cache exists for that repeat. Everything else here is about the
cases where remembering would be wrong: failures, spent budgets that differ,
a writer that just died in someone's hands, and a user who changed the setting.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import writer


def _cfg(choice: str) -> SimpleNamespace:
    return SimpleNamespace(agentic_ide=SimpleNamespace(prompt_writer=choice))


def test_a_success_is_reused_within_the_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[int] = []
    monkeypatch.setattr(
        writer, "_load_config", lambda: loads.append(1) or _cfg("auto")
    )
    monkeypatch.setattr(writer, "_subscription", lambda cfg, timeout: "sub-brain")

    first = writer.resolve_writer(cli_timeout_s=90.0)
    second = writer.resolve_writer(cli_timeout_s=90.0)

    assert first == second == ("sub-brain", "subscription:auto")
    assert len(loads) == 1


def test_a_failure_is_never_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user who just fixed their key must not wait out a stale 'nothing'."""
    loads: list[int] = []
    monkeypatch.setattr(
        writer, "_load_config", lambda: loads.append(1) or _cfg("auto")
    )
    monkeypatch.setattr(writer, "_subscription", lambda cfg, timeout: None)
    monkeypatch.setattr(writer, "_quality", lambda cfg: None)

    assert writer.resolve_writer() == (None, "")
    assert writer.resolve_writer() == (None, "")
    assert len(loads) == 2


def test_a_different_budget_misses_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """The budget is baked into a CLI brain at instantiation; handing a 30 s
    caller a brain built for 90 s hands it the wrong internal deadline."""
    budgets: list[float | None] = []
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("auto"))
    monkeypatch.setattr(
        writer, "_subscription", lambda cfg, timeout: budgets.append(timeout) or "sub"
    )

    writer.resolve_writer(cli_timeout_s=90.0)
    writer.resolve_writer(cli_timeout_s=30.0)

    assert budgets == [90.0, 30.0]


def test_a_rescue_resolution_invalidates_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rescue means the cached choice just failed in someone's hands; the
    next composition must re-ask instead of getting the dead writer back."""
    loads: list[int] = []
    monkeypatch.setattr(
        writer, "_load_config", lambda: loads.append(1) or _cfg("auto")
    )
    monkeypatch.setattr(writer, "_subscription", lambda cfg, timeout: "sub-brain")
    monkeypatch.setattr(writer, "_quality", lambda cfg: "api-brain")
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: None)

    writer.resolve_writer()
    writer.resolve_rescue_writer(exclude=("subscription:auto",))
    writer.resolve_writer()

    # Three real resolutions: the second resolve_writer re-asked.
    assert len(loads) == 3


def test_the_cache_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(
        writer, "time", SimpleNamespace(monotonic=lambda: now[0])
    )
    loads: list[int] = []
    monkeypatch.setattr(
        writer, "_load_config", lambda: loads.append(1) or _cfg("auto")
    )
    monkeypatch.setattr(writer, "_subscription", lambda cfg, timeout: "sub-brain")

    writer.resolve_writer()
    now[0] += writer._CACHE_TTL_S + 1.0  # noqa: SLF001
    writer.resolve_writer()

    assert len(loads) == 2
