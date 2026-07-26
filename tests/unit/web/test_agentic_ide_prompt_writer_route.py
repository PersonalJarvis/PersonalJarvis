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

    state = await routes.prompt_writer_state()

    assert state.prompt_writer == "auto"
    ids = [option.id for option in state.options]
    assert ids[:3] == ["auto", "subscription", "api"]
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
