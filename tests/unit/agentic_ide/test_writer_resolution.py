"""One resolution order, shared by the prompt composer and the work splitter.

They used to carry a copy of this decision each. That is a drift the user pays
for: moving briefs onto a subscription and still being billed per token for the
split is exactly the surprise this feature exists to remove.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import writer


def _cfg(choice: str) -> SimpleNamespace:
    return SimpleNamespace(agentic_ide=SimpleNamespace(prompt_writer=choice))


def test_auto_prefers_the_pinned_tool_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The live 2026-08-18 16:10 delay: ``auto`` handed a spoken instruction to
    the subscription CLI, which spent 18.9 s — nearly all of it its own process
    start — while the user's Tool Model would have written the same brief in
    1-3 s. Under ``auto`` the writer that answers soonest goes first, and a
    slower rung is not even probed while a faster one qualifies."""
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("auto"))
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: SimpleNamespace(name="vertex"))
    monkeypatch.setattr(writer, "_quality", lambda cfg: "api-brain")
    monkeypatch.setattr(
        writer,
        "_subscription",
        lambda cfg, timeout: pytest.fail("probed a coding CLI while a faster writer qualified"),
    )

    brain, source = writer.resolve_writer()

    assert getattr(brain, "name", "") == "vertex"
    assert source == "tool_model:vertex"


def test_auto_prefers_the_api_tier_over_a_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Tool Model pinned: a direct API call has no cold start to pay, so it
    writes before any coding CLI is asked."""
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("auto"))
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: None)
    monkeypatch.setattr(writer, "_quality", lambda cfg: "api-brain")
    monkeypatch.setattr(
        writer,
        "_subscription",
        lambda cfg, timeout: pytest.fail("probed a coding CLI while a faster writer qualified"),
    )

    brain, source = writer.resolve_writer()

    assert brain == "api-brain"
    assert source == "api"


def test_auto_reaches_the_subscription_when_nothing_api_side_qualifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The downloader whose ONLY credential is a coding CLI still gets a
    written brief — the subscription is last, not gone."""
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("auto"))
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: None)
    monkeypatch.setattr(writer, "_quality", lambda cfg: None)
    monkeypatch.setattr(
        writer, "_subscription", lambda cfg, timeout: SimpleNamespace(name="claude-cli")
    )

    brain, source = writer.resolve_writer()

    assert getattr(brain, "name", "") == "claude-cli"
    assert source == "subscription:claude-cli"


def test_an_unset_tool_model_is_the_ordinary_auto_case(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under ``auto`` a missing Tool Model is not a broken pin: the next rung
    answers, and the log stays free of a "pinned to the Tool Model" complaint
    that would send the user to a setting they never touched."""
    seen: list[str] = []
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("auto"))
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: None)
    monkeypatch.setattr(writer, "_quality", lambda cfg: "api-brain")
    monkeypatch.setattr(writer, "_subscription", lambda cfg, timeout: None)
    monkeypatch.setattr(writer.logger, "info", lambda msg, *a, **k: seen.append(str(msg)))

    brain, _source = writer.resolve_writer()

    assert brain == "api-brain"
    assert not [line for line in seen if "pinned to the Tool Model" in line]


def test_api_pin_never_reaches_a_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user who pinned 'api' must not have their plan quota spent behind their
    back — the mirror image of the billing surprise this feature removes."""
    called: list[int] = []
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("api"))
    monkeypatch.setattr(
        writer, "_subscription", lambda cfg, timeout: called.append(1) or "sub-brain"
    )
    monkeypatch.setattr(writer, "_quality", lambda cfg: "api-brain")

    brain, source = writer.resolve_writer()

    assert brain == "api-brain"
    assert source == "api"
    assert not called


def test_subscription_pin_does_not_silently_bill_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinning 'subscription' and getting an API bill is the exact surprise to
    avoid, so an unhonourable pin degrades to the deterministic prompt."""
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("subscription"))
    monkeypatch.setattr(writer, "_subscription", lambda cfg, timeout: None)
    monkeypatch.setattr(writer, "_quality", lambda cfg: "api-brain")

    brain, source = writer.resolve_writer()

    assert brain is None
    assert source == ""


def test_a_named_provider_pin_is_honoured_by_the_subscription_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("claude-cli"))
    monkeypatch.setattr(
        writer, "_subscription", lambda cfg, timeout: SimpleNamespace(name="claude-cli")
    )
    monkeypatch.setattr(writer, "_quality", lambda cfg: "api-brain")

    brain, source = writer.resolve_writer()

    assert brain is not None
    assert source == "subscription:claude-cli"


def test_the_timeout_reaches_the_subscription_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CLI brain's own voice-tier cap must not kill a call the composer is
    still willing to wait for."""
    seen: list[float | None] = []
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("auto"))
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: None)
    monkeypatch.setattr(
        writer, "_subscription", lambda cfg, timeout: seen.append(timeout) or "sub"
    )
    monkeypatch.setattr(writer, "_quality", lambda cfg: None)

    writer.resolve_writer(cli_timeout_s=90.0)

    assert seen == [90.0]


def test_resolution_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any failure means the caller uses its deterministic layer, not a crash."""

    def _boom() -> object:
        raise RuntimeError("config exploded")

    monkeypatch.setattr(writer, "_load_config", _boom)

    assert writer.resolve_writer() == (None, "")


def test_a_raising_subscription_probe_degrades_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken CLI probe is the last rung's problem alone: it must neither
    reach the caller nor be mistaken for a writer."""

    def _boom(cfg: object, timeout: object) -> object:
        raise RuntimeError("cli probe exploded")

    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("auto"))
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: None)
    monkeypatch.setattr(writer, "_quality", lambda cfg: None)
    monkeypatch.setattr(writer, "_subscription", _boom)

    assert writer.resolve_writer() == (None, "")


def test_both_call_sites_use_this_module() -> None:
    """The drift guard: a second copy of this decision is the bug itself."""
    import inspect

    from jarvis.agentic_ide import prompt_composer, work_split

    for module, function in (
        (prompt_composer, "_resolve_writer"),
        (work_split, "_resolve_splitter"),
    ):
        source = inspect.getsource(getattr(module, function))
        assert "resolve_writer" in source, f"{module.__name__} bypasses the shared order"
        assert (
            "resolve_quality_brain" not in source
        ), f"{module.__name__} still resolves its own writer"


def test_tool_model_pin_uses_the_model_the_user_picked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of the setting: honour the Tool Model, nothing else."""
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("tool_model"))
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: "tool-brain")
    monkeypatch.setattr(writer, "_subscription", lambda cfg, timeout: "sub-brain")
    monkeypatch.setattr(writer, "_quality", lambda cfg: "api-brain")

    brain, source = writer.resolve_writer()

    assert brain == "tool-brain"
    assert source.startswith("tool_model")


def test_tool_model_pin_never_reaches_a_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user picks the Tool Model precisely to stop a coding CLI writing their
    briefs; probing one anyway would spend a plan they chose not to use."""
    probed: list[int] = []
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("tool_model"))
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: "tool-brain")
    monkeypatch.setattr(
        writer, "_subscription", lambda cfg, timeout: probed.append(1) or "sub-brain"
    )

    brain, _source = writer.resolve_writer()

    assert brain == "tool-brain"
    assert not probed


def test_an_unusable_tool_model_degrades_instead_of_substituting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Use the model I picked" and "use whatever is left" are different
    answers. Landing on a third model would make the setting meaningless in
    exactly the way it was added to fix."""
    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("tool_model"))
    monkeypatch.setattr(writer, "_tool_model", lambda cfg: None)
    monkeypatch.setattr(writer, "_quality", lambda cfg: "api-brain")
    monkeypatch.setattr(writer, "_subscription", lambda cfg, timeout: "sub-brain")

    brain, source = writer.resolve_writer()

    assert brain is None
    assert source == ""


def test_a_raising_tool_model_costs_the_brief_not_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every failure in this module degrades; none may reach the caller."""

    def _boom(_cfg: object) -> object:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(writer, "_load_config", lambda: _cfg("tool_model"))
    monkeypatch.setattr(writer, "_tool_model", _boom)

    brain, source = writer.resolve_writer()

    assert brain is None
    assert source == ""
