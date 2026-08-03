"""Choosing who writes the task briefs — and seeing why a subscription is idle.

The listing carries each option's live connection state on purpose. "My Claude
plan is connected but Jarvis still bills my API key" is otherwise unanswerable
from the UI, and the honest answer is usually "that CLI is not signed in on this
machine" — which the user can act on the moment they can see it.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from jarvis.ui.web import agentic_ide_routes as routes


async def test_listing_reports_the_current_choice_and_every_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "_writer_candidates", lambda: [("codex", True)])
    monkeypatch.setattr(routes, "_current_prompt_writer", lambda: "auto")

    state = await routes.prompt_writer_state()

    assert state.prompt_writer == "auto"
    ids = [option.id for option in state.options]
    # `tool_model` sits second, directly under `auto`: the two questions a user
    # opens this picker with are "stop guessing for me" and "use the model I
    # already chose", and the second must not be buried under the generic
    # billing modes.
    assert ids[:4] == ["auto", "tool_model", "subscription", "api"]
    assert "codex" in ids


async def test_a_disconnected_subscription_is_listed_but_marked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hiding it would leave the user with no way to learn WHY it is unused."""
    monkeypatch.setattr(routes, "_writer_candidates", lambda: [("codex", False)])

    state = await routes.prompt_writer_state()

    codex = next(option for option in state.options if option.id == "codex")
    assert codex.connected is False


async def test_choosing_a_writer_persists_it(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[str] = []
    monkeypatch.setattr(routes, "_writer_candidates", lambda: [("codex", True)])
    monkeypatch.setattr(
        routes, "_persist_prompt_writer", lambda value: written.append(value)
    )

    result = await routes.set_prompt_writer(
        routes.PromptWriterRequest(prompt_writer="subscription")
    )

    assert result.prompt_writer == "subscription"
    assert written == ["subscription"]


async def test_an_unknown_writer_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accepting it would silently fall back to today's billing while the UI
    claims the subscription was chosen."""
    monkeypatch.setattr(routes, "_writer_candidates", lambda: [("codex", True)])
    monkeypatch.setattr(routes, "_persist_prompt_writer", lambda value: None)

    with pytest.raises(HTTPException) as excinfo:
        await routes.set_prompt_writer(
            routes.PromptWriterRequest(prompt_writer="definitely-not-real")
        )

    assert excinfo.value.status_code == 422


async def test_a_disconnected_subscription_cannot_be_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinning one degrades to the deterministic prompt on every instruction;
    refusing at the point of choice is where the user can still fix it."""
    monkeypatch.setattr(routes, "_writer_candidates", lambda: [("codex", False)])
    monkeypatch.setattr(routes, "_persist_prompt_writer", lambda value: None)

    with pytest.raises(HTTPException) as excinfo:
        await routes.set_prompt_writer(
            routes.PromptWriterRequest(prompt_writer="codex")
        )

    assert excinfo.value.status_code == 409
    assert "not signed in" in str(excinfo.value.detail)


async def test_the_tool_model_option_names_the_model_it_would_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Tool Model" alone tells the user nothing about what would write their
    briefs. The label carries the actual selection so the choice is informed."""
    monkeypatch.setattr(routes, "_writer_candidates", lambda: [])
    monkeypatch.setattr(routes, "_tool_model_usable", lambda provider: True)
    monkeypatch.setattr(
        routes, "_provider_label", lambda provider: "Google Gemini"
    )
    monkeypatch.setattr(
        "jarvis.brain.resolver._tool_model_selection",
        lambda config: ("gemini", "gemini-3.6-flash"),
    )

    state = await routes.prompt_writer_state()

    option = next(o for o in state.options if o.id == "tool_model")
    assert "Google Gemini" in option.label
    assert "gemini-3.6-flash" in option.label
    assert option.connected is True


async def test_an_unpinned_tool_model_is_offered_but_not_selectable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hiding the row would leave "why can I not pick my Tool Model" unanswered;
    offering it as usable would persist a choice that writes nothing."""
    monkeypatch.setattr(routes, "_writer_candidates", lambda: [])
    monkeypatch.setattr(
        "jarvis.brain.resolver._tool_model_selection", lambda config: ("auto", None)
    )

    state = await routes.prompt_writer_state()

    option = next(o for o in state.options if o.id == "tool_model")
    assert option.connected is False


async def test_choosing_an_unusable_tool_model_names_the_real_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Not signed in" is true of a coding CLI and nonsense about a Tool Model.
    A user sent to fix the wrong thing gives up on the setting, not the error."""
    monkeypatch.setattr(routes, "_writer_candidates", lambda: [])
    monkeypatch.setattr(
        "jarvis.brain.resolver._tool_model_selection", lambda config: ("auto", None)
    )

    with pytest.raises(HTTPException) as excinfo:
        await routes.set_prompt_writer(
            routes.PromptWriterRequest(prompt_writer="tool_model")
        )

    assert excinfo.value.status_code == 409
    assert "Tool Model" in str(excinfo.value.detail)
    assert "signed in" not in str(excinfo.value.detail)


async def test_a_usable_tool_model_can_be_chosen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[str] = []
    monkeypatch.setattr(routes, "_writer_candidates", lambda: [])
    monkeypatch.setattr(routes, "_tool_model_usable", lambda provider: True)
    monkeypatch.setattr(
        "jarvis.brain.resolver._tool_model_selection",
        lambda config: ("gemini", "gemini-3.6-flash"),
    )
    monkeypatch.setattr(routes, "_persist_prompt_writer", saved.append)

    state = await routes.set_prompt_writer(
        routes.PromptWriterRequest(prompt_writer="tool_model")
    )

    assert saved == ["tool_model"]
    assert state.prompt_writer == "tool_model"


async def test_a_connected_cli_is_listed_under_its_own_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The picker must name the CLI this install actually connected — read off
    the provider card, never mapped from a list of vendors in this file."""
    monkeypatch.setattr(routes, "_writer_candidates", lambda: [("antigravity", True)])

    state = await routes.prompt_writer_state()

    option = next(o for o in state.options if o.id == "antigravity")
    assert option.label != "antigravity"
    assert option.label.strip()
