"""Choosing a writer and surviving one are different questions.

``resolve_writer`` answers the first: a pin that cannot be honoured degrades
openly rather than billing a provider the user did not choose. It says nothing
about the case that actually broke the feature — the chosen writer ACCEPTS the
job and dies inside it. Measured on the dev box 2026-08-02: the resolved writer
was a key whose prepayment credits were gone, so every composition for days
ended on the raw spoken sentence while a signed-in subscription sat unused.
That is the single-provider brick AP-22 forbids.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import writer


def _cfg(choice: str = "auto") -> SimpleNamespace:
    return SimpleNamespace(agentic_ide=SimpleNamespace(prompt_writer=choice))


def test_a_dead_api_tier_crosses_to_the_tool_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact live failure: the API rung answered 429, so the brief must be
    written by the model the user picked rather than not written at all."""
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("api"))
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: "tool-brain")
    monkeypatch.setattr(writer, "_quality", lambda cfg: "api-brain")

    brain, source = writer.resolve_rescue_writer(exclude=("api",))

    assert brain == "tool-brain"
    assert source.startswith("tool_model")


def test_the_rung_that_died_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-probing the same tier spends the same dead credential twice."""
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg())
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: "tool-brain")
    monkeypatch.setattr(writer, "_subscription", lambda cfg, timeout: "sub-brain")
    monkeypatch.setattr(writer, "_quality", lambda cfg: "api-brain")

    brain, source = writer.resolve_rescue_writer(exclude=("tool_model:grok",))

    assert brain == "sub-brain"
    assert source.startswith("subscription")


def test_a_named_source_is_compared_by_its_rung(monkeypatch: pytest.MonkeyPatch) -> None:
    """``subscription:antigravity`` and ``subscription:codex`` are one rung: the
    resolver hands back whichever CLI is connected, so trying again lands on the
    same one."""
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg())
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: None)
    monkeypatch.setattr(
        writer, "_subscription", lambda cfg, timeout: pytest.fail("re-probed a dead rung")
    )
    monkeypatch.setattr(writer, "_quality", lambda cfg: "api-brain")

    brain, source = writer.resolve_rescue_writer(exclude=("subscription:antigravity",))

    assert brain == "api-brain"
    assert source == "api"


def test_nothing_left_degrades_instead_of_looping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg())
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: None)
    monkeypatch.setattr(writer, "_subscription", lambda cfg, timeout: None)
    monkeypatch.setattr(writer, "_quality", lambda cfg: None)

    assert writer.resolve_rescue_writer() == (None, "")


def test_a_raising_rung_does_not_stop_the_rescue(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken CLI probe must not cost the working rung behind it."""

    def _boom(_cfg: object, _timeout: object = None) -> object:
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(writer, "_load_config", lambda: _cfg())
    monkeypatch.setattr(writer, "_tool_model", _boom)
    monkeypatch.setattr(writer, "_subscription", _boom)
    monkeypatch.setattr(writer, "_quality", lambda cfg: "api-brain")

    brain, source = writer.resolve_rescue_writer()

    assert brain == "api-brain"
    assert source == "api"


def test_rescue_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> object:
        raise RuntimeError("config exploded")

    monkeypatch.setattr(writer, "_load_config", _boom)

    assert writer.resolve_rescue_writer() == (None, "")


def test_the_cli_timeout_reaches_the_subscription_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CLI brain's own voice-tier cap must not kill a call the composer is
    still willing to wait for — same contract as the first resolution."""
    seen: list[float | None] = []
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg())
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: None)
    monkeypatch.setattr(
        writer, "_subscription", lambda cfg, timeout: seen.append(timeout) or "sub-brain"
    )

    writer.resolve_rescue_writer(cli_timeout_s=90.0)

    assert seen == [90.0]


def test_both_call_sites_can_rescue() -> None:
    """The drift guard: a composer that crosses over while the splitter dies on
    the same dead key leaves the fleet on the crude by-directory plan."""
    import inspect

    from jarvis.agentic_ide import prompt_composer, work_split

    for module, function in (
        (prompt_composer, "_rescue_writer"),
        (work_split, "_rescue_splitter"),
    ):
        source = inspect.getsource(getattr(module, function))
        assert "resolve_rescue_writer" in source, f"{module.__name__} cannot cross over"
