"""Model discovery must follow the actual CLI, account, and current settings."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agent_chat import cli_catalog as catalog
from jarvis.agent_chat import runner_cli as rc


def test_catalog_identity_changes_on_update_login_config_and_workspace(tmp_path):
    cli = tmp_path / "codex"
    cli.write_text("old", encoding="utf-8")
    env = {"CODEX_HOME": str(tmp_path), "PRIVATE_TOKEN": "never-log-this"}
    old = catalog.catalog_key([str(cli)], env, tmp_path)
    cli.write_text("updated-cli", encoding="utf-8")
    updated = catalog.catalog_key([str(cli)], env, tmp_path)
    assert old != updated
    for filename in ("auth.json", "config.toml"):
        (tmp_path / filename).write_text("changed", encoding="utf-8")
        new = catalog.catalog_key([str(cli)], env, tmp_path)
        assert new != updated
        updated = new
    assert updated != catalog.catalog_key([str(cli)], {**env, "CODEX_HOME": "other"}, tmp_path)
    assert updated != catalog.catalog_key([str(cli)], env, tmp_path / "other-project")
    assert "never-log-this" not in updated


def test_cache_separates_accounts_refreshes_unknown_models_and_bounds_failures(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(catalog.time, "monotonic", lambda: now[0])
    cache = catalog.CatalogCache()
    rows = [{"id": "old", "efforts": ["high"]}]
    calls = []

    def load():
        calls.append(True)
        return rows

    first = cache.read("account-a", load)
    first[0]["efforts"].append("bogus")
    assert cache.read("account-a", load)[0]["efforts"] == ["high"]
    cache.read("account-b", load)
    assert len(calls) == 2
    rows = [{"id": "new", "efforts": []}]
    now[0] += 11
    assert cache.read("account-a", load, required_model="new") == rows
    assert len(calls) == 3

    # Repeated rejected selections do not start a probe for every click.
    cache.read("account-a", load, required_model="missing")
    assert len(calls) == 3
    assert cache.read("failed", lambda: None) is None
    assert cache.read("failed", load) is None
    now[0] += 11
    assert cache.read("failed", load) == rows


async def test_catalog_route_uses_session_seat_and_keeps_empty_results(monkeypatch, tmp_path):
    from jarvis import agent_accounts
    from jarvis.ui.web import agent_chat_routes as routes

    session = SimpleNamespace(surface="agent", account_id="seat-two", cwd=str(tmp_path))
    service = SimpleNamespace(
        store=SimpleNamespace(get_session=lambda sid: session),
        default_cwd=lambda surface: str(tmp_path),
    )
    monkeypatch.setattr(agent_accounts, "resolve", lambda account: SimpleNamespace(id=account))
    monkeypatch.setattr(routes, "_service", lambda request: service)
    monkeypatch.setattr(routes, "_cli_installed", lambda runner: True)
    seen = []

    async def live():
        seen.append((rc.ACCOUNT_OVERRIDE.get(), rc._catalog_cwd()))
        return {"codex-cli": []}

    monkeypatch.setattr(routes, "_live_cli_models", live)
    before = rc.ACCOUNT_OVERRIDE.get()
    result = await routes.get_catalog(None, session_id="chat", account_id="wrong-seat")
    assert seen == [("seat-two", tmp_path)]
    assert rc.ACCOUNT_OVERRIDE.get() == before
    codex = next(row for row in result["providers"] if row["id"] == "openai-codex")
    assert codex["curated_models"] == []


def test_empty_live_catalog_never_reintroduces_bundled_models():
    from jarvis.workspace.launch_picks import offered_models

    assert offered_models("codex", {"codex-cli": []}) == []


async def test_future_codex_effort_is_kept(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(
        rc, "read_codex_models", lambda **kwargs: [{"id": "future", "efforts": ["future-effort"]}]
    )

    def planner(**kwargs):
        seen.append(kwargs["effort"])
        raise rc.CliUnavailable("stop before process launch")

    monkeypatch.setitem(rc._PLANNERS, "codex-cli", planner)
    handle = SimpleNamespace(
        session=SimpleNamespace(
            session_id="test",
            cwd=str(tmp_path),
            provider="openai-codex",
            model="future",
            effort="future-effort",
            permission_mode="plan",
        )
    )
    await rc._run_cli_once(handle, "hello", "codex-cli", None)
    assert seen == ["future-effort"]


def test_removed_pinned_account_never_falls_back_to_another_subscription(monkeypatch):
    from jarvis import agent_accounts

    monkeypatch.setattr(agent_accounts, "resolve", lambda account: None)
    with rc.cli_catalog_scope(account_id="removed"):
        with pytest.raises(rc.CliUnavailable, match="no longer exists"):
            rc._account_env("codex")


def test_account_environment_is_frozen_for_discovery_and_spawn(monkeypatch):
    from jarvis import agent_accounts

    selected = ["one"]
    monkeypatch.setattr(agent_accounts, "resolve", lambda account_id: None)
    monkeypatch.setattr(
        agent_accounts, "active_account", lambda platform: SimpleNamespace(id=selected[0])
    )
    monkeypatch.setattr(
        agent_accounts, "spawn_env", lambda platform, account_id, base: {"CODEX_HOME": account_id}
    )
    with rc.cli_catalog_scope():
        assert rc._account_env("codex")["CODEX_HOME"] == "one"
        selected[0] = "two"
        assert rc._account_env("codex")["CODEX_HOME"] == "one"
    assert rc._account_env("codex")["CODEX_HOME"] == "two"


class FakeCodex:
    def __init__(self, *, hanging=False, malformed=False):
        self.stdout = asyncio.StreamReader()
        self.stdin = self
        self.returncode = None
        self.requests = []
        self.killed = False
        self.hanging = hanging
        self.malformed = malformed

    def write(self, data):
        request = json.loads(data)
        self.requests.append(request)
        if self.hanging or "id" not in request:
            return
        result = {}
        if request["method"] == "model/list":
            cursor = request["params"]["cursor"]
            result = {
                "data": [
                    {
                        "model": "future-model" if cursor is None else "second-model",
                        "supportedReasoningEfforts": [{"reasoningEffort": "future-effort"}],
                    }
                ],
                "nextCursor": "page-2" if cursor is None else None,
            }
        reply = {"id": request["id"], "result": result}
        if self.malformed:
            reply = {"id": request["id"], "error": {"message": "PRIVATE_PROVIDER_BODY"}}
        self.stdout.feed_data((json.dumps(reply) + "\n").encode())

    async def drain(self):
        pass

    def kill(self):
        self.killed = True
        self.returncode = -1

    async def wait(self):
        return self.returncode


async def test_codex_discovery_uses_same_environment_paginates_and_reaps(monkeypatch, tmp_path):
    proc = FakeCodex()
    spawned = []

    async def spawn(*argv, **kwargs):
        spawned.append((argv, kwargs))
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    env = {"CODEX_HOME": "pinned-seat"}
    rows = await catalog._codex_models(["actual-codex"], env, tmp_path)
    assert [row["id"] for row in rows] == ["future-model", "second-model"]
    assert rows[0]["efforts"] == ["future-effort"]
    assert spawned[0][0] == ("actual-codex", "app-server")
    assert spawned[0][1]["env"] == env
    assert spawned[0][1]["cwd"] == str(tmp_path)
    assert proc.killed
    assert not any(request["method"].startswith(("thread/", "turn/")) for request in proc.requests)


@pytest.mark.parametrize("hanging", [False, True])
async def test_codex_discovery_reaps_on_error_or_timeout(monkeypatch, tmp_path, hanging):
    proc = FakeCodex(hanging=hanging, malformed=not hanging)

    async def spawn(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises((ValueError, TimeoutError)) as failure:
        await asyncio.wait_for(catalog._codex_models(["codex"], {}, tmp_path), 0.02)
    assert "PRIVATE_PROVIDER_BODY" not in str(failure.value)
    assert proc.killed


def test_retired_hidden_and_no_effort_models(monkeypatch):
    monkeypatch.setattr(catalog.time, "time", lambda: 100)
    rows = catalog.parse_codex_models(
        [
            {"id": "retired", "supportedReasoningEfforts": [], "upgradeInfo": {"retirementAt": 99}},
            {"id": "hidden", "hidden": True},
            {"id": "new", "supportedReasoningEfforts": []},
        ]
    )
    assert rows == [{"id": "new", "label": "new", "efforts": [], "note": ""}]


async def test_unavailable_codex_model_is_stopped_before_inference(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "read_codex_models", lambda **kwargs: [{"id": "supported"}])

    def planner(**kwargs):
        pytest.fail("An unavailable model must not start a turn")

    monkeypatch.setitem(rc._PLANNERS, "codex-cli", planner)
    handle = SimpleNamespace(
        session=SimpleNamespace(
            session_id="test",
            cwd=str(tmp_path),
            provider="openai-codex",
            model="unsupported",
            effort="high",
            permission_mode="plan",
        )
    )
    result = await rc._run_cli_once(handle, "hello", "codex-cli", "existing-conversation")
    assert result.status == "error"
    assert "update Codex" in result.error


@pytest.mark.parametrize("family", ["agy", "opencode"])
def test_cli_catalog_reloads_on_account_and_config_change(monkeypatch, tmp_path, family):
    import subprocess

    seat = [str(tmp_path / "one")]
    calls = []
    monkeypatch.setattr(rc, "_account_env", lambda platform: {"AGENT_CONFIG_DIR": seat[0]})
    monkeypatch.setattr(rc, "_registry_env", lambda agent, env: env)
    monkeypatch.setattr(rc, f"{family}_argv_prefix", lambda: [family])
    monkeypatch.setattr(
        rc, "_AGY_CATALOG" if family == "agy" else "_OPENCODE_CATALOG", {"at": 0.0, "rows": None}
    )

    def run(*args, **kwargs):
        calls.append(kwargs["env"]["AGENT_CONFIG_DIR"])
        return SimpleNamespace(
            returncode=0,
            stdout=(
                b'{"command":{"data":{"models":[{"id":"new-high"}]}}}'
                if family == "agy"
                else b"vendor/new\n"
            ),
        )

    monkeypatch.setattr(subprocess, "run", run)
    read = rc.read_agy_models if family == "agy" else rc.read_opencode_models
    read()
    read()
    assert len(calls) == 1
    seat[0] = str(tmp_path / "two")
    read()
    assert len(calls) == 2
    Path(seat[0]).mkdir()
    (Path(seat[0]) / "auth.json").write_text("changed", encoding="utf-8")
    read()
    assert len(calls) == 3
