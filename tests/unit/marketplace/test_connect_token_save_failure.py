"""connect_start's background _drive() must not report "connected" before the
token actually landed in the TokenStore.

Previously `slot.result` (what `connect_poll` reads) was set to the
successful FlowResult BEFORE `TokenStore().save()` ran, so a save failure
(or a crash right after `await_completion`) still let the poll endpoint
report a successful connect for a token that was never persisted.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import BackgroundTasks

from jarvis.marketplace.auth.base import AuthSession, FlowResult
from jarvis.marketplace.token_store import Tokens
from jarvis.ui.web import marketplace_routes as mr


class _Catalog:
    def __init__(self, specs):
        self.plugins = specs

    def by_id(self, plugin_id: str):
        return next((s for s in self.plugins if s.id == plugin_id), None)


def _pkce_spec(client_id: str):
    for spec in mr.load_catalog().plugins:
        if spec.auth is not None and getattr(spec.auth, "mode", "") == "oauth_pkce_loopback":
            return spec.model_copy(
                update={"auth": spec.auth.model_copy(update={"client_id": client_id})}
            )
    pytest.skip("no oauth_pkce_loopback plugin in catalog")


class _StubPkceHandler:
    """Stands in for the real PkceLoopbackHandler: returns a session
    immediately and a completed FlowResult carrying tokens, without ever
    touching a socket or a real OAuth provider."""

    def __init__(self, config) -> None:
        self.config = config

    async def start(self, plugin_spec) -> AuthSession:
        return AuthSession(
            flow_id=f"test-flow-{plugin_spec.id}",
            plugin_id=plugin_spec.id,
            kind="browser_redirect",
        )

    async def await_completion(self, session: AuthSession) -> FlowResult:
        return FlowResult(tokens=Tokens(access="tok-123"), error=None)


@pytest.fixture
def _capture_background_tasks(monkeypatch):
    """connect_start fires `_drive()` via `asyncio.create_task` and returns
    immediately — capture the task so the test can await it before polling."""
    real_create_task = asyncio.create_task
    captured: list[asyncio.Task] = []

    def _capturing(coro, **kwargs):
        task = real_create_task(coro, **kwargs)
        captured.append(task)
        return task

    monkeypatch.setattr(mr.asyncio, "create_task", _capturing)
    return captured


async def _start_and_drain(monkeypatch, capture_background_tasks) -> tuple[str, str]:
    spec = _pkce_spec("real-client-id.apps.example")
    monkeypatch.setattr(mr, "load_catalog", lambda: _Catalog([spec]))
    monkeypatch.setattr(
        "jarvis.marketplace.connect_helpers.resolve_pkce_client",
        lambda pid, cid, csec: ("real-client-id.apps.example", None),
    )
    monkeypatch.setattr(mr, "PkceLoopbackHandler", _StubPkceHandler)

    session = await mr.connect_start(spec.id, BackgroundTasks())
    for task in capture_background_tasks:
        await task
    return session["plugin_id"], session["flow_id"]


@pytest.mark.asyncio
async def test_connect_poll_reports_error_when_token_save_fails(
    monkeypatch, _capture_background_tasks
) -> None:
    def _raising_save(self, plugin_id, tokens) -> None:
        raise RuntimeError("simulated keyring save failure")

    monkeypatch.setattr(mr.TokenStore, "save", _raising_save)

    plugin_id, flow_id = await _start_and_drain(monkeypatch, _capture_background_tasks)

    result = await mr.connect_poll(plugin_id, flow_id)
    assert result["state"] == "error"


@pytest.mark.asyncio
async def test_connect_poll_reports_connected_when_save_succeeds(
    monkeypatch, _capture_background_tasks
) -> None:
    saved: dict[str, Tokens] = {}

    def _recording_save(self, plugin_id, tokens) -> None:
        saved[plugin_id] = tokens

    monkeypatch.setattr(mr.TokenStore, "save", _recording_save)
    monkeypatch.setattr(mr, "_refresh_plugin_in_live_registry", lambda plugin_id: None)

    plugin_id, flow_id = await _start_and_drain(monkeypatch, _capture_background_tasks)

    result = await mr.connect_poll(plugin_id, flow_id)
    assert result["state"] == "connected"
    assert saved  # the token was actually persisted before the state flipped
