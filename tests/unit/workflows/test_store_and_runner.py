"""Smoke-Tests fuers Workflow-System.

Scope:
- Store CRUD (insert, list, upsert, run-create, run-step-update).
- Seed-Idempotenz.
- Runner mit Fake-Brain: brain_prompt-Step produziert erwarteten Output.
- Runner mit Template-Expansion (``{{prev.output}}`` + ``{{input.X}}``).
- Runner Error-Pfad: speak ohne kaputtes Dep geht durch, tool_call ohne Registry faillt sauber.

Keine echten Brain-Provider, keine echte SQLite-Datei auf Platte — alles
tempdir + Fakes. Pattern spiegelt ``tests/unit/clis/test_smoke.py`` etc.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core.bus import EventBus
from jarvis.workflows import (
    WorkflowRunner,
    WorkflowStore,
    ensure_seed_workflows,
)
from jarvis.workflows.schema import (
    BrainPromptStep,
    CronTrigger,
    ManualTrigger,
    SpeakStep,
    WorkflowDef,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
async def store(tmp_path: Path) -> WorkflowStore:
    s = WorkflowStore(tmp_path / "wf.sqlite")
    await s.init()
    yield s
    await s.close()


class FakeBrain:
    """Async-callable das den Prompt echoed — ausreichend fuer Runner-Tests."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        return f"ECHO: {prompt[:80]}"


# ----------------------------------------------------------------------
# Store
# ----------------------------------------------------------------------

async def test_store_upsert_and_list(store: WorkflowStore) -> None:
    wf = WorkflowDef(
        name="Test",
        description="smoke",
        trigger=ManualTrigger(),
        steps=(SpeakStep(text="hi"),),
    )
    wid = await store.upsert_workflow(wf)
    rows = await store.list_workflows()
    assert len(rows) == 1
    assert rows[0]["id"] == wid
    assert rows[0]["name"] == "Test"
    assert rows[0]["trigger_type"] == "manual"


async def test_store_cron_trigger_persists_expression(store: WorkflowStore) -> None:
    wf = WorkflowDef(
        name="Cron",
        trigger=CronTrigger(expression="30 7 * * *"),
        steps=(SpeakStep(text="hi"),),
    )
    await store.upsert_workflow(wf)
    rows = await store.list_workflows()
    assert rows[0]["trigger_type"] == "cron"
    assert rows[0]["cron_expression"] == "30 7 * * *"


async def test_store_upsert_is_idempotent(store: WorkflowStore) -> None:
    wf = WorkflowDef(
        name="X",
        trigger=ManualTrigger(),
        steps=(SpeakStep(text="a"),),
    )
    wid1 = await store.upsert_workflow(wf)
    wid2 = await store.upsert_workflow(wf)
    assert wid1 == wid2
    rows = await store.list_workflows()
    assert len(rows) == 1


async def test_seed_is_idempotent(store: WorkflowStore) -> None:
    first = await ensure_seed_workflows(store)
    # Aktuelle Seed-Count ergibt sich aus SEED_WORKFLOWS — robust gegen Zufuegungen.
    from jarvis.workflows.seed import SEED_WORKFLOWS
    assert first == len(SEED_WORKFLOWS)
    second = await ensure_seed_workflows(store)
    assert second == 0


async def test_runs_crud(store: WorkflowStore) -> None:
    wf = WorkflowDef(
        name="X",
        trigger=ManualTrigger(),
        steps=(SpeakStep(text="a"),),
    )
    wid = await store.upsert_workflow(wf)
    run_id = await store.create_run(wid, trigger="manual")
    await store.update_run_state(run_id, "running")
    await store.start_step(run_id, 1, "speak", "say hi")
    await store.finish_step(run_id, 1, success=True, output="hi")
    await store.update_run_state(run_id, "completed")

    run = await store.get_run(run_id)
    assert run is not None
    assert run["state"] == "completed"
    assert len(run["steps"]) == 1
    assert run["steps"][0]["success"] == 1


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------

async def test_runner_brain_prompt_executes_and_publishes_events(
    store: WorkflowStore,
) -> None:
    bus = EventBus()
    captured: list[str] = []
    bus.subscribe_all(lambda e: captured.append(type(e).__name__))

    brain = FakeBrain()
    runner = WorkflowRunner(store=store, bus=bus, brain=brain)

    wf = WorkflowDef(
        name="Echo",
        trigger=ManualTrigger(),
        steps=(
            BrainPromptStep(prompt="sag hallo", max_output_chars=500),
            SpeakStep(text="{{prev.output}}"),
        ),
    )
    wid = await store.upsert_workflow(wf)
    run_id = await runner.trigger(wid)

    # Einen Micro-Turn warten — Runner laeuft als asyncio.create_task.
    import asyncio
    for _ in range(50):
        await asyncio.sleep(0.01)
        run = await store.get_run(run_id)
        if run and run["state"] == "completed":
            break
    else:
        pytest.fail("Run wurde nicht in 500ms fertig")

    assert brain.calls == ["sag hallo"]
    run = await store.get_run(run_id)
    assert run["state"] == "completed"
    assert len(run["steps"]) == 2
    # Step 1: brain_prompt → ECHO-Text
    assert "ECHO:" in run["steps"][0]["output"]
    # Step 2: speak hat prev.output via Template expanded bekommen
    assert "ECHO:" in run["steps"][1]["output"]
    # Event-Sequenz: WorkflowStarted, WorkflowStepStarted x2, WorkflowStepCompleted x2,
    # WorkflowCompleted, plus AnnouncementRequested vom Speak-Step.
    assert "WorkflowStarted" in captured
    assert "WorkflowCompleted" in captured
    assert captured.count("WorkflowStepStarted") == 2
    assert captured.count("WorkflowStepCompleted") == 2
    assert "AnnouncementRequested" in captured


async def test_runner_input_variable_expansion(store: WorkflowStore) -> None:
    bus = EventBus()
    brain = FakeBrain()
    runner = WorkflowRunner(store=store, bus=bus, brain=brain)

    wf = WorkflowDef(
        name="URL",
        trigger=ManualTrigger(),
        steps=(
            BrainPromptStep(prompt="Fasse {{input.url}} zusammen"),
        ),
    )
    wid = await store.upsert_workflow(wf)
    run_id = await runner.trigger(wid, input_data={"url": "https://x.test"})

    import asyncio
    for _ in range(50):
        await asyncio.sleep(0.01)
        run = await store.get_run(run_id)
        if run and run["state"] == "completed":
            break
    else:
        pytest.fail("Run nicht fertig")

    assert brain.calls == ["Fasse https://x.test zusammen"]


async def test_runner_without_brain_fails_cleanly(store: WorkflowStore) -> None:
    """brain_prompt ohne BrainManager → sauberer Error-State, kein Crash."""
    bus = EventBus()
    runner = WorkflowRunner(store=store, bus=bus, brain=None)

    wf = WorkflowDef(
        name="No-Brain",
        trigger=ManualTrigger(),
        steps=(BrainPromptStep(prompt="hi"),),
    )
    wid = await store.upsert_workflow(wf)
    run_id = await runner.trigger(wid)

    import asyncio
    for _ in range(50):
        await asyncio.sleep(0.01)
        run = await store.get_run(run_id)
        if run and run["state"] in ("completed", "failed"):
            break

    run = await store.get_run(run_id)
    assert run["state"] == "failed"
    assert "Brain" in (run["error"] or "")


# ----------------------------------------------------------------------
# External-Integration-Steps (shell_cmd + telegram_send)
# ----------------------------------------------------------------------

async def test_runner_shell_cmd_captures_stdout(
    store: WorkflowStore, tmp_path: Path,
) -> None:
    """shell_cmd mit einem einfachen Python-Script — stdout landet in output.

    Ein File-basiertes Script vermeidet die Escape-Hoelle von ``-c "print(...)"``
    auf Windows und prueft den realistischen Case (gws/git-style-CLI).
    """
    import sys

    from jarvis.workflows.schema import ShellCmdStep

    script = tmp_path / "echo.py"
    script.write_text("print('hallo_workflow')", encoding="utf-8")

    bus = EventBus()
    runner = WorkflowRunner(store=store, bus=bus)

    cmd = f'"{sys.executable}" "{script}"'
    wf = WorkflowDef(
        name="Shell-Echo",
        trigger=ManualTrigger(),
        steps=(ShellCmdStep(command=cmd, timeout_s=10.0),),
    )
    wid = await store.upsert_workflow(wf)
    run_id = await runner.trigger(wid)

    import asyncio
    for _ in range(200):
        await asyncio.sleep(0.02)
        run = await store.get_run(run_id)
        if run and run["state"] in ("completed", "failed"):
            break

    run = await store.get_run(run_id)
    assert run["state"] == "completed", f"Runner failed: {run['error']}"
    assert "hallo_workflow" in run["steps"][0]["output"]


async def test_runner_shell_cmd_nonzero_exit_fails(
    store: WorkflowStore, tmp_path: Path,
) -> None:
    """Exit-Code != 0 → Step failed + Error-Text."""
    import sys

    from jarvis.workflows.schema import ShellCmdStep

    script = tmp_path / "fail.py"
    script.write_text("import sys; sys.exit(7)", encoding="utf-8")

    bus = EventBus()
    runner = WorkflowRunner(store=store, bus=bus)

    cmd = f'"{sys.executable}" "{script}"'
    wf = WorkflowDef(
        name="Shell-Fail",
        trigger=ManualTrigger(),
        steps=(ShellCmdStep(command=cmd, timeout_s=5.0),),
    )
    wid = await store.upsert_workflow(wf)
    run_id = await runner.trigger(wid)

    import asyncio
    for _ in range(200):
        await asyncio.sleep(0.02)
        run = await store.get_run(run_id)
        if run and run["state"] in ("completed", "failed"):
            break

    run = await store.get_run(run_id)
    assert run["state"] == "failed"
    err = (run["error"] or "").lower()
    assert "exit_code" in err or "7" in err


async def test_runner_telegram_without_token_fails_cleanly(
    store: WorkflowStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """telegram_send ohne Token → sauberer Fehler mit Setup-Hinweis."""
    from jarvis.workflows.schema import TelegramSendStep

    # Token-Lookup garantiert leer. monkeypatch stellt sicher, dass kein
    # eingeloggter keyring-Key aus der echten Dev-Maschine reingelesen wird.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(
        "jarvis.core.config.get_secret",
        lambda key, env_fallback=None: None,
    )

    bus = EventBus()
    runner = WorkflowRunner(store=store, bus=bus)

    wf = WorkflowDef(
        name="TG-No-Token",
        trigger=ManualTrigger(),
        steps=(TelegramSendStep(text="hi"),),
    )
    wid = await store.upsert_workflow(wf)
    run_id = await runner.trigger(wid)

    import asyncio
    for _ in range(50):
        await asyncio.sleep(0.01)
        run = await store.get_run(run_id)
        if run and run["state"] in ("completed", "failed"):
            break

    run = await store.get_run(run_id)
    assert run["state"] == "failed"
    err = run["error"] or ""
    assert "telegram" in err.lower()
    assert "token" in err.lower()


async def test_runner_telegram_posts_to_api(
    store: WorkflowStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """telegram_send mit Token + Chat-ID → POST an Bot-API, Payload korrekt.

    Wir mocken ``httpx.AsyncClient.post`` um einen 200-OK zurueckzugeben und
    pruefen, dass Body chat_id/text wie erwartet enthaelt.
    """
    from jarvis.workflows.schema import TelegramSendStep

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(
        "jarvis.core.config.get_secret",
        lambda key, env_fallback=None: "TESTTOKEN" if key == "telegram_bot_token" else None,
    )

    captured_payloads: list[dict] = []

    class _FakeResp:
        status_code = 200

        def json(self) -> dict:
            return {"ok": True}

    class _FakeClient:
        def __init__(self, *_, **__): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def post(self, url: str, json: dict):  # noqa: A002
            captured_payloads.append({"url": url, "body": json})
            return _FakeResp()

    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)

    bus = EventBus()
    runner = WorkflowRunner(store=store, bus=bus)

    wf = WorkflowDef(
        name="TG-Send",
        trigger=ManualTrigger(),
        steps=(TelegramSendStep(text="hallo von jarvis", chat_id="987654"),),
    )
    wid = await store.upsert_workflow(wf)
    run_id = await runner.trigger(wid)

    import asyncio
    for _ in range(50):
        await asyncio.sleep(0.01)
        run = await store.get_run(run_id)
        if run and run["state"] in ("completed", "failed"):
            break

    run = await store.get_run(run_id)
    assert run["state"] == "completed", f"Fehler: {run['error']}"
    assert len(captured_payloads) == 1
    body = captured_payloads[0]["body"]
    assert body["chat_id"] == "987654"
    assert body["text"] == "hallo von jarvis"
    assert "TESTTOKEN" in captured_payloads[0]["url"]
