"""Credentials an agent asks for: a secure field, a vault, and a value the agent never sees."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from jarvis.agent_chat.credential_requests import (
    MAX_REQUESTS_PER_TURN,
    CredentialSpec,
    TooManyCredentialRequests,
)
from jarvis.agent_chat.service import AgentChatService, _Running
from jarvis.agent_chat.store import AgentChatStore
from jarvis.society import credentials
from jarvis.society.credential_tool import CREDENTIAL_TOOL_NAME, RequestCredentialTool
from jarvis.society.credentials import CredentialError, CredentialVault, validate_env_name
from jarvis.society.shell import ShellResult
from tests.fakes.fake_secret_store import FakeSecretStore

SECRET = "ghp_" + "s3cr3t" * 6
SID = "society:ada"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CredentialVault:
    store = CredentialVault(tmp_path / "credentials", secrets=FakeSecretStore())
    monkeypatch.setattr(credentials, "current_vault", lambda: store)
    return store


# ------------------------------------------------------------------ vault


def test_env_names_are_conventional_and_never_system_variables() -> None:
    assert validate_env_name(" GITHUB_TOKEN ") == "GITHUB_TOKEN"
    for bad in ("github_token", "1TOKEN", "A", "PATH", "SYSTEMROOT", "JARVIS_CONTROL_API_KEY", ""):
        with pytest.raises(CredentialError):
            validate_env_name(bad)


def test_the_index_holds_names_and_the_store_holds_values(vault: CredentialVault) -> None:
    info = vault.store("ada", "GITHUB_TOKEN", f"  {SECRET}  ", label="GitHub token")
    assert info.env == "GITHUB_TOKEN" and info.label == "GitHub token"
    assert vault.has("ada", "GITHUB_TOKEN") and not vault.has("bob", "GITHUB_TOKEN")
    assert [row.env for row in vault.list("ada")] == ["GITHUB_TOKEN"]
    assert vault.values("ada") == {"GITHUB_TOKEN": SECRET}
    index = next((vault._root).glob("*.json")).read_text(encoding="utf-8")
    assert SECRET not in index and "GitHub token" in index
    assert vault.delete("ada", "GITHUB_TOKEN")
    assert not vault.delete("ada", "GITHUB_TOKEN")
    assert vault.values("ada") == {} and vault.list("ada") == []


def test_bad_values_are_refused(tmp_path: Path) -> None:
    vault = CredentialVault(tmp_path, secrets=FakeSecretStore())
    for bad in ("", "   ", "two\nlines", "x" * 20_000):
        with pytest.raises(CredentialError):
            vault.store("ada", "API_KEY", bad)
    refusing = CredentialVault(tmp_path, secrets=FakeSecretStore(refuse=True))
    with pytest.raises(CredentialError):
        refusing.store("ada", "API_KEY", SECRET)
    assert refusing.list("ada") == []


def test_encoded_forms_and_nested_secrets_are_masked(vault: CredentialVault) -> None:
    import base64
    from urllib.parse import quote

    vault.store("ada", "GITHUB_TOKEN", SECRET)
    vault.store("ada", "GITHUB_PREFIX", SECRET[:12])  # a shorter secret inside the longer one
    printed = " ".join([
        SECRET,
        base64.b64encode(SECRET.encode()).decode(),
        base64.b64encode((SECRET + "\n").encode()).decode(),
        quote(SECRET + "/x", safe=""),
    ])
    masked = vault.redact("ada", printed)
    assert SECRET[:12] not in masked and "s3cr3t" not in masked
    assert masked.startswith("[credential GITHUB_TOKEN] ")


def test_a_vanished_store_entry_is_not_available(tmp_path: Path) -> None:
    secrets = FakeSecretStore()
    vault = CredentialVault(tmp_path, secrets=secrets)
    vault.store("ada", "GITHUB_TOKEN", SECRET)
    secrets.values.clear()  # the OS credential store lost it
    fresh = CredentialVault(tmp_path, secrets=secrets)
    assert [row.env for row in fresh.list("ada")] == ["GITHUB_TOKEN"]
    assert not fresh.has("ada", "GITHUB_TOKEN")


def test_a_save_during_a_load_is_never_lost(tmp_path: Path) -> None:
    class SlowStore(FakeSecretStore):
        armed = False

        def get(self, slot: str) -> str | None:
            if self.armed:
                self.armed = False
                # Another caller saves while this load reads the store.
                vault.store("ada", "DISCORD_BOT_TOKEN", "d" * 30)
            return super().get(slot)

    secrets = SlowStore()
    CredentialVault(tmp_path, secrets=secrets).store("ada", "GITHUB_TOKEN", SECRET)
    vault = CredentialVault(tmp_path, secrets=secrets)
    secrets.armed = True
    assert vault.values("ada") == {"GITHUB_TOKEN": SECRET, "DISCORD_BOT_TOKEN": "d" * 30}


def test_values_are_masked_everywhere_in_a_payload(vault: CredentialVault) -> None:
    vault.store("ada", "GITHUB_TOKEN", SECRET)
    vault.store("ada", "PIN_CODE", "1234")  # too short to mask without eating numbers
    payload = {"output": f"token={SECRET}", "list": [SECRET, 1234], "keep": "1234"}
    redacted = vault.redact_payload("ada", payload)
    assert redacted == {
        "output": "token=[credential GITHUB_TOKEN]",
        "list": ["[credential GITHUB_TOKEN]", 1234],
        "keep": "1234",
    }
    clean = {"output": "nothing secret"}
    assert vault.redact_payload("ada", clean) is clean
    assert vault.redact_payload("bob", payload) is payload


def test_values_load_from_the_store_once(tmp_path: Path) -> None:
    secrets = FakeSecretStore()
    CredentialVault(tmp_path, secrets=secrets).store("ada", "GITHUB_TOKEN", SECRET)
    secrets.reads = 0
    fresh = CredentialVault(tmp_path, secrets=secrets)  # a new process
    assert not fresh.is_loaded("ada")
    assert fresh.values("ada") == {"GITHUB_TOKEN": SECRET}
    fresh.values("ada")
    assert secrets.reads == 1 and fresh.is_loaded("ada")


# ------------------------------------------------------------------ the card


async def _service_with_turn(tmp_path: Path, session_id: str = SID):
    svc = AgentChatService(AgentChatStore(":memory:"), assistant_name=lambda: "Testo")
    session = svc.store.create_session(
        provider="fakeprov",
        model="m",
        effort="high",
        cwd=str(tmp_path),
        session_id=session_id,
        surface="society",
    )
    run = _Running("turn-1", asyncio.Event())
    run.task = asyncio.create_task(asyncio.sleep(3600))
    svc._running[session.session_id] = run
    return svc, session.session_id, run.task


async def _next(q: asyncio.Queue, kind: str) -> dict[str, Any]:
    while True:
        ev = await asyncio.wait_for(q.get(), timeout=5)
        if ev["kind"] == kind:
            return ev["payload"]


def _logged(svc: AgentChatService, sid: str) -> str:
    return json.dumps(svc.store.list_events(sid), ensure_ascii=False)


_SPEC = CredentialSpec.build(
    "GITHUB_TOKEN", "GitHub token", "To open the pull request.", "ghp_…"
)


async def test_the_value_goes_to_save_and_never_into_the_chat(tmp_path: Path, vault) -> None:
    svc, sid, task = await _service_with_turn(tmp_path)
    q = svc.subscribe(sid)
    saved: list[str] = []
    rid = await svc.open_credential_request(sid, _SPEC, saved.append, asker="Ada")
    card = await _next(q, "credential_required")
    assert card["request_id"] == rid and card["env"] == "GITHUB_TOKEN"
    assert card["asker"] == "Ada" and card["placeholder"] == "ghp_…"
    assert svc.pending_credential_requests(sid) == [rid]
    assert not await svc.submit_credential("other", rid, SECRET)
    assert await svc.submit_credential(sid, rid, SECRET)
    assert saved == [SECRET]
    resolved = await _next(q, "credential_resolved")
    assert resolved == {
        "turn_id": "turn-1", "request_id": rid, "env": "GITHUB_TOKEN", "status": "saved"
    }
    assert await svc.wait_credential_request(sid, rid, 1) == "saved"
    assert await svc.submit_credential(sid, rid, SECRET)  # a lost success receipt is retryable
    assert saved == [SECRET]
    assert SECRET not in _logged(svc, sid)
    task.cancel()


async def test_a_refused_value_keeps_the_field_open(tmp_path: Path) -> None:
    svc, sid, task = await _service_with_turn(tmp_path)

    def save(value: str) -> None:
        raise CredentialError("the credential is empty")

    rid = await svc.open_credential_request(sid, _SPEC, save)
    with pytest.raises(ValueError):
        await svc.submit_credential(sid, rid, " ")
    assert svc.pending_credential_requests(sid) == [rid]
    task.cancel()


async def test_only_explicit_decline_closes_an_unsaved_field(tmp_path: Path) -> None:
    svc, sid, task = await _service_with_turn(tmp_path)
    q = svc.subscribe(sid)
    declined = await svc.open_credential_request(sid, _SPEC, lambda _v: None)
    assert await svc.decline_credential(sid, declined)
    assert await svc.wait_credential_request(sid, declined, 1) == "declined"
    late = await svc.open_credential_request(sid, _SPEC, lambda _v: None, timeout_s=0.05)
    assert await svc.wait_credential_request(sid, late, 0.1) is None
    assert (await _next(q, "credential_resolved"))["status"] == "declined"
    assert svc.pending_credential_requests(sid) == [late]
    task.cancel()
    svc2, sid2, task2 = await _service_with_turn(tmp_path, "society:bob")
    open_rid = await svc2.open_credential_request(sid2, _SPEC, lambda _v: None)
    svc2._cancel_questions(sid2)  # what a finished or stopped turn runs
    assert svc2.pending_credential_requests(sid2) == [open_rid]
    assert await svc2.wait_credential_request(sid2, open_rid, 0) is None
    assert await svc2.submit_credential(sid2, open_rid, SECRET)
    task2.cancel()


async def test_a_turn_runs_out_of_fields_and_needs_a_turn(tmp_path: Path) -> None:
    svc, sid, task = await _service_with_turn(tmp_path)
    for index in range(MAX_REQUESTS_PER_TURN):
        await svc.open_credential_request(
            sid, CredentialSpec.build(f"TOKEN_{index}", "Token"), lambda _v: None,
        )
    with pytest.raises(TooManyCredentialRequests):
        await svc.open_credential_request(sid, _SPEC, lambda _v: None)
    task.cancel()
    idle = AgentChatService(AgentChatStore(":memory:"))
    with pytest.raises(RuntimeError):
        await idle.open_credential_request("society:ada", _SPEC, lambda _v: None)


async def test_a_printed_value_is_masked_in_the_chat_log(tmp_path: Path, vault) -> None:
    vault.store("ada", "GITHUB_TOKEN", SECRET)
    svc, sid, task = await _service_with_turn(tmp_path)
    from jarvis.agent_chat.events import make_event

    await svc._emit(
        sid,
        make_event(
            "tool_result",
            {"turn_id": "turn-1", "call_id": "c1", "output": f"echo -> {SECRET}"},
        ),
    )
    logged = _logged(svc, sid)
    assert SECRET not in logged and "[credential GITHUB_TOKEN]" in logged
    task.cancel()


# ------------------------------------------------------------------ the tool


def _tool(session_id: str, service: Any) -> RequestCredentialTool:
    runtime = SimpleNamespace(
        chat_service=lambda: service, cached_agent=lambda _id: SimpleNamespace(name="Ada")
    )
    return RequestCredentialTool(runtime, "ada", session_id=session_id)


_ARGS = {
    "env": "GITHUB_TOKEN",
    "label": "GitHub token",
    "description": "To push the fix. Create it under Settings > Developer settings.",
}


async def test_the_tool_asks_and_the_agent_only_learns_the_name(tmp_path: Path, vault) -> None:
    svc, sid, task = await _service_with_turn(tmp_path)
    q = svc.subscribe(sid)
    first = await asyncio.wait_for(_tool(sid, svc).execute(dict(_ARGS), None), timeout=1)
    assert first.success and first.output["status"] == "waiting"
    card = await _next(q, "credential_required")
    assert await svc.submit_credential(sid, card["request_id"], SECRET)
    result = await _tool(sid, svc).execute({"wait_for": card["request_id"]}, None)
    assert result.success and result.output["status"] == "saved"
    assert "$GITHUB_TOKEN" in result.output["note"] and "society_shell" in result.output["note"]
    assert SECRET not in json.dumps(result.output) + (result.error or "")
    assert vault.values("ada") == {"GITHUB_TOKEN": SECRET}
    assert [row.label for row in vault.list("ada")] == ["GitHub token"]
    again = await _tool(sid, svc).execute(dict(_ARGS), None)
    assert again.success and again.output["status"] == "available"  # no second card
    task.cancel()


async def test_checking_a_pending_field_returns_without_waiting(tmp_path: Path, vault) -> None:
    svc, sid, task = await _service_with_turn(tmp_path)
    tool = _tool(sid, svc)
    first = await asyncio.wait_for(tool.execute(dict(_ARGS), None), timeout=1)
    assert first.success and first.output["status"] == "waiting"
    rid = first.output["request_id"]
    pending = await asyncio.wait_for(tool.execute({"wait_for": rid}, None), timeout=1)
    assert pending.output["status"] == "waiting"
    assert svc.pending_credential_requests(sid) == [rid]
    assert await svc.decline_credential(sid, rid)
    second = await tool.execute({"wait_for": rid}, None)
    assert not second.success and second.output["status"] == "declined"
    assert "not set" in (second.error or "")
    gone = await tool.execute({"wait_for": "nope"}, None)
    assert not gone.success
    task.cancel()


async def test_followup_finishes_before_the_token_is_saved(
    tmp_path: Path, vault, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.agent_chat import service as service_module
    from jarvis.agent_chat.events import make_event
    from jarvis.agent_chat.send_queue import send_or_queue

    svc = AgentChatService(AgentChatStore(":memory:"), assistant_name=lambda: "Testo")
    session = svc.store.create_session(
        provider="fakeprov", model="m", effort="", cwd=str(tmp_path), session_id=SID,
        surface="society",
    )
    results = []
    replies = []

    async def bind(_sid, **_kwargs):
        return session

    async def run(handle, prompt):
        if not results:
            results.append(await _tool(SID, svc).execute(dict(_ARGS), None))
        else:
            replies.append(prompt)
            assert svc.pending_credential_requests(SID) == [results[0].output["request_id"]]
            await handle.emit(make_event("assistant_text", {
                "turn_id": handle.turn_id, "text": "It allows me to open the pull request.",
            }))
        await handle.emit(make_event("turn_finished", {
            "turn_id": handle.turn_id, "status": "done",
        }))

    monkeypatch.setattr(svc, "bind_society_session", bind)
    monkeypatch.setattr(service_module, "resolve_runner", lambda *a, **k: "api")
    monkeypatch.setattr(service_module, "supports_api_runner", lambda _provider: True)
    monkeypatch.setattr(service_module, "run_api_turn", run)
    try:
        await svc.send(SID, "Open the pull request")
        await asyncio.wait_for(svc.wait_turn(SID), timeout=2)
        assert results[0].output["status"] == "waiting"
        rid = results[0].output["request_id"]
        turn_id, queue_id = await send_or_queue(svc, SID, "What do you need it for?")
        assert turn_id and not queue_id
        await asyncio.wait_for(svc.wait_turn(SID), timeout=2)
        assert replies == ["What do you need it for?"]
        assert not vault.has("ada", "GITHUB_TOKEN")
        assert await svc.submit_credential(SID, rid, SECRET)
        assert vault.values("ada") == {"GITHUB_TOKEN": SECRET}
        assert (await _tool(SID, svc).execute({"wait_for": rid}, None)).output["status"] == "saved"
        assert SECRET not in _logged(svc, SID)
    finally:
        await svc.cancel(SID)


async def test_routines_and_bad_requests_never_open_a_field(vault) -> None:
    opened: list[Any] = []
    service = SimpleNamespace(open_credential_request=lambda *a, **k: opened.append(a))
    routine = await _tool("society:ada:routine:t1:run1", service).execute(dict(_ARGS), None)
    assert not routine.success and routine.output["status"] == "unattended"
    bad = await _tool(SID, service).execute({**_ARGS, "env": "path"}, None)
    assert not bad.success and "invalid request" in (bad.error or "")
    nameless = await _tool(SID, service).execute({**_ARGS, "label": " "}, None)
    assert not nameless.success
    unexplained = await _tool(SID, service).execute({**_ARGS, "description": " "}, None)
    assert not unexplained.success and "explain why" in unexplained.error
    assert opened == []


def test_the_tool_is_offered_outside_routines(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.society import surface

    browser = SimpleNamespace(is_installed=lambda: False, live=SimpleNamespace(model_resolver=None))
    rt = SimpleNamespace(browser=browser, chat_service=lambda: None)
    monkeypatch.setattr(surface, "current_runtime", lambda: rt)
    canonical = SimpleNamespace(session_id="society:ada", cwd="", permission_mode="")
    routine = SimpleNamespace(
        session_id="society:ada:routine:t1:run1", cwd="", permission_mode="bypass"
    )
    assert CREDENTIAL_TOOL_NAME in surface.society_tools(None, None, canonical)
    assert CREDENTIAL_TOOL_NAME not in surface.society_tools(None, None, routine)


# ------------------------------------------------------------------ where the value is used


class _EnvBackend:
    name = "local"
    accepts_env = True

    def __init__(self) -> None:
        self.extra_env: dict[str, str] | None = None

    async def run(self, command: str, *, cwd: Path, timeout_s: float, extra_env=None):
        self.extra_env = dict(extra_env or {})
        return ShellResult(output=f"Bearer {SECRET}", exit_code=0, seconds=0.01)


async def test_society_shell_sets_the_variable_and_masks_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.society.agent_tools import ShellTool
    from jarvis.society.runtime import SocietyRuntime

    rt = SocietyRuntime(tmp_path / "society", seed_starter_team=False)
    await rt.ensure_started()
    try:
        await rt.roster.create(name="Scout")
        store = CredentialVault(tmp_path / "creds", secrets=FakeSecretStore())
        store.store("scout", "GITHUB_TOKEN", SECRET)
        monkeypatch.setattr(credentials, "current_vault", lambda: store)
        backend = _EnvBackend()
        tool = ShellTool(rt, "scout", workspace=tmp_path / "ws", backend=backend)
        ctx = SimpleNamespace(trace_id=uuid4(), user_utterance="", config={}, memory_read=None)
        command = 'curl -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/user'
        result = await tool.execute({"command": command}, ctx)
        assert result.success, result.error
        assert backend.extra_env == {"GITHUB_TOKEN": SECRET}
        assert result.output["output"] == "Bearer [credential GITHUB_TOKEN]"
    finally:
        await rt.close()


async def test_the_briefing_names_stored_credentials_and_says_when_there_are_none(
    tmp_path: Path, vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.society.surface import _credential_line

    monkeypatch.setattr(credentials, "vault_for", lambda _data_dir: vault)
    rt = SimpleNamespace(data_dir=tmp_path)
    # A resumed CLI conversation remembers a token it once saved; the briefing
    # must say it is gone, or the agent reports a deleted credential as stored.
    assert "Stored credentials: none" in await _credential_line(rt, "ada")
    vault.store("ada", "DISCORD_BOT_TOKEN", SECRET, label="Discord bot token")
    line = await _credential_line(rt, "ada")
    assert "DISCORD_BOT_TOKEN (Discord bot token)" in line and SECRET not in line


async def test_a_question_card_never_asks_for_the_secret_itself() -> None:
    from jarvis.society.ask_tool import AskUserTool

    rt = SimpleNamespace(chat_service=lambda: None)
    tool = AskUserTool(rt, "ada", session_id=SID)
    asking = {"question": "Please paste your Discord bot token.", "options": ["Done", "Later"]}
    result = await tool.execute({"questions": [asking]}, None)
    assert not result.success and "society_request_credential" in (result.error or "")
    # Choosing HOW to connect is a real decision, not a secret.
    choice = {"question": "Connect the bot with a token or a browser login?", "options": ["Token", "Browser"]}  # noqa: E501
    result = await tool.execute({"questions": [choice]}, None)
    assert "society_request_credential" not in (result.error or "")
