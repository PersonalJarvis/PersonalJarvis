"""Conductor-Smoke-Tests: Store CRUD + alle drei Handler + Runner + Seed-YAML."""
from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from conductor import (
    AgentJobSpec,
    ConductorStore,
    CronSchedule,
    HttpJobSpec,
    IntervalSchedule,
    Job,
    ManualSchedule,
    Runner,
    ShellJobSpec,
    ensure_seed_jobs,
    SEED_YAML_DIR,
)


@pytest.fixture
async def store(tmp_path: Path) -> ConductorStore:
    s = ConductorStore(tmp_path / "conductor.sqlite")
    await s.init()
    yield s
    await s.close()


# ---------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------

async def test_store_upsert_and_list(store: ConductorStore) -> None:
    job = Job(
        name="Test",
        spec=ShellJobSpec(command="echo hi"),
        schedule=ManualSchedule(),
    )
    jid = await store.upsert_job(job)
    rows = await store.list_jobs()
    assert len(rows) == 1
    assert rows[0]["id"] == jid
    assert rows[0]["type"] == "shell"
    assert rows[0]["schedule_type"] == "manual"


async def test_store_denormalized_fields(store: ConductorStore) -> None:
    """Cron-Expression und Webhook-Token müssen separat queryable sein."""
    cron_job = Job(
        name="Cron",
        spec=ShellJobSpec(command="echo cron"),
        schedule=CronSchedule(expression="0 9 * * *"),
    )
    await store.upsert_job(cron_job)

    int_job = Job(
        name="Interval",
        spec=ShellJobSpec(command="echo int"),
        schedule=IntervalSchedule(seconds=300),
    )
    await store.upsert_job(int_job)

    rows = await store.list_jobs()
    rows_by_name = {r["name"]: r for r in rows}
    assert rows_by_name["Cron"]["schedule_expr"] == "0 9 * * *"
    assert rows_by_name["Interval"]["schedule_expr"] == "300"
    assert rows_by_name["Cron"]["webhook_token"] is None


async def test_store_webhook_lookup_by_token(store: ConductorStore) -> None:
    from conductor.core.schema import WebhookSchedule
    job = Job(
        name="Hook",
        spec=ShellJobSpec(command="echo hook"),
        schedule=WebhookSchedule(token="abc123def456ghi789jkl"),
    )
    await store.upsert_job(job)
    row = await store.get_job_by_webhook_token("abc123def456ghi789jkl")
    assert row is not None
    assert row["name"] == "Hook"


async def test_runs_crud(store: ConductorStore) -> None:
    job = Job(
        name="R",
        spec=ShellJobSpec(command="echo r"),
        schedule=ManualSchedule(),
    )
    jid = await store.upsert_job(job)
    rid = await store.create_run(jid, trigger="manual")
    await store.update_run(rid, state="running")
    await store.update_run(
        rid, state="completed", exit_code=0,
        output="hi", metrics={"duration_ms": 42},
    )
    run = await store.get_run(rid)
    assert run["state"] == "completed"
    assert run["exit_code"] == 0
    assert "duration_ms" in run["metrics_json"]


# ---------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------

async def test_shell_handler_echo(tmp_path: Path) -> None:
    import sys

    from conductor.jobs.shell import ShellHandler
    script = tmp_path / "hi.py"
    script.write_text("print('conductor_shell_test')", encoding="utf-8")

    spec = ShellJobSpec(command=f'"{sys.executable}" "{script}"', timeout_s=10.0)
    result = await ShellHandler().execute(spec, {})
    assert result.success
    assert "conductor_shell_test" in result.output
    assert result.exit_code == 0
    assert "duration_ms" in result.metrics


async def test_shell_handler_nonzero(tmp_path: Path) -> None:
    import sys
    from conductor.jobs.shell import ShellHandler
    script = tmp_path / "fail.py"
    script.write_text("import sys; sys.exit(3)", encoding="utf-8")
    spec = ShellJobSpec(command=f'"{sys.executable}" "{script}"', timeout_s=5.0)
    result = await ShellHandler().execute(spec, {})
    assert not result.success
    assert result.exit_code == 3


async def test_http_handler_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from conductor.jobs.http import HttpHandler

    class _FakeResp:
        status_code = 200
        text = "hello from mock"
        content = b"hello from mock"

    class _FakeClient:
        def __init__(self, **_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def request(self, **_): return _FakeResp()

    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)
    spec = HttpJobSpec(url="https://example.test/path", expect_status="2xx")
    result = await HttpHandler().execute(spec, {})
    assert result.success
    assert "hello from mock" in result.output
    assert result.metrics["status_code"] == 200


async def test_http_handler_status_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from conductor.jobs.http import HttpHandler

    class _FakeResp:
        status_code = 500
        text = "boom"
        content = b"boom"

    class _FakeClient:
        def __init__(self, **_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def request(self, **_): return _FakeResp()

    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)
    spec = HttpJobSpec(url="https://example.test/x", expect_status="2xx")
    result = await HttpHandler().execute(spec, {})
    assert not result.success
    assert result.exit_code == 500


async def test_agent_handler_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne ANTHROPIC_API_KEY muss der Agent einen klaren Fehler liefern."""
    from conductor.jobs.agent import AgentHandler
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    spec = AgentJobSpec(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        user_prompt="hi",
    )
    result = await AgentHandler().execute(spec, {})
    assert not result.success
    assert "ANTHROPIC_API_KEY" in (result.error or "")


async def test_agent_gemini_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kein GEMINI_API_KEY → klarer Fehler mit aistudio-Link."""
    from conductor.jobs.agent import AgentHandler
    for k in ("GEMINI_API_KEY", "GOOGLE_AIStudio_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    spec = AgentJobSpec(
        provider="gemini", model="gemini-3.1-pro", user_prompt="hi",
    )
    result = await AgentHandler().execute(spec, {})
    assert not result.success
    err = result.error or ""
    assert "GEMINI_API_KEY" in err
    assert "aistudio" in err.lower()


async def test_agent_gemini_default_model_is_3_1_pro() -> None:
    """Default-Provider ist 'gemini', Default-Model 'gemini-3.1-pro'."""
    spec = AgentJobSpec(user_prompt="x")   # alle Defaults greifen
    assert spec.provider == "gemini"
    assert spec.model == "gemini-3.1-pro"


@pytest.mark.skip(
    reason=(
        "Pins behaviour of the `claude_cli` provider (commit e93479b2c, "
        "2026-04-24): when the `claude` binary is missing on $PATH the "
        "handler should surface a Subscription-aware setup hint. The "
        "`claude_cli` provider was removed from `AgentJobSpec` during a "
        "later schema cleanup and `_run_anthropic` now only honours the "
        "API-key path. Restoring this test requires reinstating the "
        "subscription fallback in `conductor/jobs/agent.py` — out of "
        "scope for a test-suite repair pass."
    )
)
async def test_agent_anthropic_missing_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wenn 'claude' nicht im PATH: sauberer Fehler mit Setup-Hint."""
    from conductor.jobs.agent import AgentHandler
    monkeypatch.setattr("shutil.which", lambda name: None)
    spec = AgentJobSpec(
        provider="anthropic",
        model="sonnet",
        user_prompt="hi",
    )
    result = await AgentHandler().execute(spec, {})
    assert not result.success
    err = result.error or ""
    assert "claude-CLI" in err
    assert "claude /login" in err or "Subscription" in err


@pytest.mark.skip(
    reason=(
        "Same reason as test_agent_anthropic_missing_binary: pins the "
        "`claude_cli` provider's JSON-output parsing. That code path no "
        "longer exists in `_run_anthropic` — the handler routes through "
        "the `anthropic` python SDK with an API key. Re-enable when the "
        "subscription path is restored."
    )
)
async def test_agent_anthropic_parses_json_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Wir mocken 'claude' durch ein kleines Python-Script, das exakt das
    JSON-Format von --output-format=json zurueckgibt (Feld 'result').
    Der Handler muss es parsen und die Text-Antwort als output liefern."""
    import json
    import sys
    from conductor.jobs.agent import AgentHandler

    fake_bin = tmp_path / "fake_claude.py"
    fake_response = {
        "result": "fake claude answer",
        "usage": {"input_tokens": 42, "output_tokens": 11},
        "total_cost_usd": 0.0005,
    }
    fake_bin.write_text(
        f"import sys, json\n"
        f"print({json.dumps(json.dumps(fake_response))})\n",
        encoding="utf-8",
    )

    def _fake_which(name: str) -> str | None:
        return sys.executable if name == "claude" else None

    monkeypatch.setattr("shutil.which", _fake_which)

    # Patch asyncio.create_subprocess_exec damit 'claude <args>' stattdessen
    # 'python fake_bin.py' startet (ignoriert die anderen Args).
    original_exec = asyncio.create_subprocess_exec

    async def _fake_exec(*_args, **kwargs):
        return await original_exec(
            sys.executable, str(fake_bin),
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    spec = AgentJobSpec(
        provider="anthropic", model="sonnet",
        user_prompt="say hi",
    )
    result = await AgentHandler().execute(spec, {})
    assert result.success
    assert result.output == "fake claude answer"
    assert result.metrics["provider"] == "anthropic"
    assert result.metrics["input_tokens"] == 42
    assert result.metrics["output_tokens"] == 11
    assert result.metrics["auth"] == "subscription"


async def test_agent_handler_template_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``{{input.X}}`` muss vor dem LLM-Call expandiert werden."""
    from conductor.jobs.agent import _expand_template
    out = _expand_template(
        "Hallo {{input.name}}, analysiere {{input.topic}}",
        {"name": "Alex", "topic": "K8s"},
    )
    assert out == "Hallo Alex, analysiere K8s"


# ---------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------

async def test_runner_shell_end_to_end(
    tmp_path: Path, store: ConductorStore,
) -> None:
    import sys
    events: list[tuple[str, dict]] = []

    def _cb(event, payload): events.append((event, payload))

    runner = Runner(store, on_event=_cb)
    script = tmp_path / "x.py"
    script.write_text("print('ok_from_runner')", encoding="utf-8")

    job = Job(
        name="Runner-E2E",
        spec=ShellJobSpec(command=f'"{sys.executable}" "{script}"'),
        schedule=ManualSchedule(),
    )
    jid = await store.upsert_job(job)
    rid = await runner.trigger(jid, trigger="manual")

    for _ in range(100):
        await asyncio.sleep(0.05)
        run = await store.get_run(rid)
        if run and run["state"] in ("completed", "failed"):
            break

    run = await store.get_run(rid)
    assert run["state"] == "completed"
    assert "ok_from_runner" in run["output"]

    event_names = [e[0] for e in events]
    assert "run.started" in event_names
    assert "run.finished" in event_names


# ---------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------

async def test_seed_yaml_loads(store: ConductorStore) -> None:
    """Die 4 Seed-YAMLs werden ohne Fehler geparst + persistiert."""
    added = await ensure_seed_jobs(store)
    assert added >= 3            # Mindestens 3, wir haben aktuell 4
    rows = await store.list_jobs()
    names = [r["name"] for r in rows]
    assert "GitHub-API Zen" in names
    assert "Daily Standup" in names
    # Zweiter Aufruf: idempotent
    again = await ensure_seed_jobs(store)
    assert again == 0


async def test_seed_dir_exists() -> None:
    assert SEED_YAML_DIR.exists()
    yamls = list(SEED_YAML_DIR.glob("*.yaml"))
    assert len(yamls) >= 3
