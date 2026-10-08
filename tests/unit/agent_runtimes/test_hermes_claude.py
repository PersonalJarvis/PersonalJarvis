"""Claude subscriptions retain Hermes' tools and its isolated profile lifecycle."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from jarvis.agent_runtimes import claude_hermes as provider
from jarvis.agent_runtimes import hermes
from jarvis.agent_runtimes.base import RuntimeStatus, RuntimeTurn, RuntimeUnavailable, child_env
from jarvis.agent_runtimes.model_map import ModelRoute
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS


def _turn(tmp_path: Path) -> RuntimeTurn:
    return RuntimeTurn(
        agent_id="hermit", agent_name="Hermit", session_id="society:hermit",
        workspace=tmp_path, route=ModelRoute(
            "claude-api", "claude-sonnet-5", "", "claude_cli", None,
            context_window=200_000, claude_binary=str(tmp_path / "claude.exe"),
            claude_config_dir=str(tmp_path / "native-account"),
        ), resume="conversation", auto_approve=True, mcp_url="", control_key="",
    )


def _receipt(
    home: Path, *, prepared: bool = True, enabled: bool = True, **overrides: object,
) -> None:
    directory = home / "plugins" / provider.PROVIDER_NAME
    directory.mkdir(parents=True, exist_ok=True)
    for name in provider._FILES:
        (directory / name).write_text("fixture", encoding="utf-8")
    entry = {
        "pinned": True, "revision": provider.REVISION,
        "source": provider._SOURCE + ".git", **overrides,
    }
    (directory.parent / ".install-metadata.json").write_text(
        json.dumps({provider.PROVIDER_NAME: entry}), encoding="utf-8",
    )
    (home / "config.yaml").write_text(json.dumps({"plugins": {
        "enabled": [provider.PROVIDER_NAME] if enabled else [],
    }}), encoding="utf-8")
    if prepared:
        (home / provider._PREPARED_FILE).write_text(provider.REVISION, encoding="utf-8")


def test_native_config_keeps_hermes_tools_and_approvals_without_gateway(tmp_path):
    config = hermes.HermesRuntime().config_for(_turn(tmp_path))
    assert config["model"] == {
        "provider": provider.PROVIDER_NAME, "default": "claude-sonnet-5",
        "context_length": 200_000,
    }
    assert "jarvis" not in config["providers"]
    assert config["plugins"]["enabled"] == [provider.PROVIDER_NAME]
    assert config["approvals"] == {"mode": "manual"}
    assert config["fallback_model"] is None
    assert config["agent"]["api_max_retries"] == 1
    assert config["agent"]["auto_recovery_cycles"] == 0
    assert "terminal" not in config["agent"]["disabled_toolsets"]
    assert config["auxiliary"]["background_review"]["enabled"] is False


def test_native_account_environment_does_not_inherit_paid_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-secret")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-bearer")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://other.invalid")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "wrong-account")
    turn = _turn(tmp_path)
    env = child_env({**turn.route.env(), **provider.provider_env(turn.route)})
    assert env["CLAUDE_CONFIG_DIR"] == turn.route.claude_config_dir
    assert env["CLAUDE_SUBSCRIPTION_DIRECTSDK_CONFIG_DIR"] == turn.route.claude_config_dir
    assert env["CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND"] == turn.route.claude_binary
    assert not any(key.startswith("ANTHROPIC_") for key in env)


def test_missing_native_cli_is_an_actionable_failure(tmp_path):
    with pytest.raises(RuntimeUnavailable, match="Connect Claude in Settings"):
        provider.provider_env(replace(_turn(tmp_path).route, claude_binary=""))


@pytest.mark.parametrize("version", ["0.21.3", "", "unknown"])
def test_plugin_minimum_is_checked_explicitly(version):
    with pytest.raises(RuntimeUnavailable, match="0.21.4"):
        provider.require_version(version)


@pytest.mark.parametrize("version", ["0.21.4", "0.21.5", "0.21.6"])
def test_supported_hermes_versions(version):
    provider.require_version(version)


def test_idempotency_requires_official_revision_and_complete_install(tmp_path):
    assert not provider.installed(tmp_path)
    _receipt(tmp_path)
    assert provider.installed(tmp_path)
    (tmp_path / "plugins" / provider.PROVIDER_NAME / "directsdk.py").unlink()
    assert not provider.installed(tmp_path)
    _receipt(tmp_path, revision="a" * 40)
    assert not provider.installed(tmp_path)
    _receipt(tmp_path, source="https://other.invalid/repository")
    assert not provider.installed(tmp_path)


class FakeTree:
    def __init__(self) -> None:
        self.assigned: list[int] = []
        self.closed = False

    def assign(self, pid: int) -> None:
        self.assigned.append(pid)

    def close(self) -> None:
        self.closed = True


class FakeInstaller:
    pid = 1234

    def __init__(
        self, home: Path, *, code: int = 0, hang: bool = False, enabled: bool = True,
    ) -> None:
        self.home, self.code, self.hang = home, code, hang
        self.enabled = enabled
        self.returncode: int | None = None
        self.killed = False
        self.waiting = asyncio.Event()
        self.finished = asyncio.Event()

    async def wait(self) -> int:
        self.waiting.set()
        if self.hang and self.returncode is None:
            await self.finished.wait()
        if self.returncode is None:
            self.returncode = self.code
            if self.code == 0:
                _receipt(self.home, prepared=False, enabled=self.enabled)
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -1
        self.finished.set()


def _installer(
    monkeypatch, home: Path, *, code: int = 0, hang: bool = False, enabled: bool = True,
):
    from jarvis.core import process_tree

    process = FakeInstaller(home, code=code, hang=hang, enabled=enabled)
    tree = FakeTree()
    calls: list[tuple[tuple, dict]] = []

    async def spawn(*argv, **kwargs):
        calls.append((argv, kwargs))
        return process

    monkeypatch.setattr(provider.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(process_tree, "make_process_tree", lambda name: tree)
    return process, tree, calls


@pytest.mark.asyncio
async def test_preparation_is_pinned_profile_local_and_runs_once(tmp_path, monkeypatch):
    process, tree, calls = _installer(monkeypatch, tmp_path)
    env = child_env({"HERMES_HOME": str(tmp_path)})
    await provider.ensure_provider("hermes", tmp_path, env)
    await provider.ensure_provider("hermes", tmp_path, env)
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == (
        "hermes", "plugins", "install", provider.CATALOG_NAME, "--ref", provider.REVISION,
        "--yes-deps", "--enable",
    )
    assert kwargs["env"]["HERMES_HOME"] == str(tmp_path)
    assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert kwargs["creationflags"] == NO_WINDOW_CREATIONFLAGS
    assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
    assert tree.assigned == [process.pid] and tree.closed


@pytest.mark.asyncio
async def test_interrupted_install_resumes_dependency_admission(tmp_path, monkeypatch):
    _receipt(tmp_path, prepared=False, enabled=False)
    assert provider.source_installed(tmp_path) and not provider.installed(tmp_path)
    _, _, calls = _installer(monkeypatch, tmp_path)
    await provider.ensure_provider("hermes", tmp_path, child_env())
    assert [argv[1:] for argv, _ in calls] == [
        ("pm", "install", "venv"),
        ("plugins", "enable", provider.PROVIDER_NAME, "--no-allow-tool-override"),
    ]
    assert provider.installed(tmp_path)


@pytest.mark.asyncio
async def test_success_exit_without_enable_is_not_preparation_success(tmp_path, monkeypatch):
    _installer(monkeypatch, tmp_path, enabled=False)
    with pytest.raises(RuntimeUnavailable, match="did not finish enabling"):
        await provider.ensure_provider("hermes", tmp_path, child_env())
    assert provider.source_installed(tmp_path) and not provider.installed(tmp_path)


@pytest.mark.asyncio
async def test_failed_install_does_not_claim_readiness(tmp_path, monkeypatch):
    _, tree, _ = _installer(monkeypatch, tmp_path, code=7)
    with pytest.raises(RuntimeUnavailable, match="exit 7"):
        await provider.ensure_provider("hermes", tmp_path, child_env())
    assert tree.closed and not provider.installed(tmp_path)


@pytest.mark.asyncio
async def test_cancelled_preparation_reaps_installer_before_return(tmp_path, monkeypatch):
    process, tree, _ = _installer(monkeypatch, tmp_path, hang=True)
    task = asyncio.create_task(provider.ensure_provider("hermes", tmp_path, child_env()))
    await process.waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tree.closed and process.killed and process.returncode == -1
    assert not provider.installed(tmp_path)


@pytest.mark.asyncio
async def test_timed_out_preparation_reaps_installer(tmp_path, monkeypatch):
    process, tree, _ = _installer(monkeypatch, tmp_path, hang=True)
    monkeypatch.setattr(provider, "_PREPARE_TIMEOUT_S", 0.01)
    with pytest.raises(RuntimeUnavailable, match="timed out"):
        await provider.ensure_provider("hermes", tmp_path, child_env())
    assert tree.closed and process.killed


@pytest.mark.asyncio
async def test_launch_prepares_before_final_config_and_releases_slot(tmp_path, monkeypatch):
    from jarvis.agent_runtimes import base

    monkeypatch.setattr(base, "runtimes_root", lambda: tmp_path)
    monkeypatch.setattr(hermes, "profile_home", lambda key: tmp_path)
    monkeypatch.setattr(hermes, "_binary", lambda: "hermes")
    runtime = hermes.HermesRuntime()
    monkeypatch.setattr(runtime, "detect", lambda: RuntimeStatus(
        "hermes", "Hermes", installed=True, ready=True, version="0.21.5",
    ))

    async def prepare(binary, home, env):
        assert runtime.busy()
        assert env["CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND"].endswith("claude.exe")
        (home / "config.yaml").write_text('{"model": "installer-choice"}', encoding="utf-8")

    monkeypatch.setattr(provider, "ensure_provider", prepare)
    launch = await runtime.launch(_turn(tmp_path))
    config = json.loads((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["model"]["provider"] == provider.PROVIDER_NAME
    assert launch.acp_resume == "conversation"
    assert launch.acp_model == f"{provider.PROVIDER_NAME}:claude-sonnet-5"
    assert "OPENAI_API_KEY" not in launch.env
    assert launch.release is not None
    launch.release()
    assert not runtime.busy()


@pytest.mark.asyncio
async def test_failed_preparation_releases_the_profile_lock(tmp_path, monkeypatch):
    from jarvis.agent_runtimes import base

    monkeypatch.setattr(base, "runtimes_root", lambda: tmp_path)
    monkeypatch.setattr(hermes, "profile_home", lambda key: tmp_path)
    monkeypatch.setattr(hermes, "_binary", lambda: "hermes")
    runtime = hermes.HermesRuntime()
    monkeypatch.setattr(runtime, "detect", lambda: RuntimeStatus(
        "hermes", "Hermes", installed=True, ready=True, version="0.21.5",
    ))

    async def prepare(*args):
        raise RuntimeUnavailable("fixture install failure")

    monkeypatch.setattr(provider, "ensure_provider", prepare)
    with pytest.raises(RuntimeUnavailable, match="fixture install failure"):
        await runtime.launch(_turn(tmp_path))
    assert not runtime.busy()


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_native", [False, True])
async def test_switching_back_to_api_replaces_native_session_route(
    tmp_path, monkeypatch, previous_native,
):
    monkeypatch.setattr(hermes, "profile_home", lambda key: tmp_path)
    if previous_native:
        _receipt(tmp_path)
    turn = _turn(tmp_path)
    turn = replace(turn, route=replace(
        turn.route, transport="chat_completions", base_url="http://127.0.0.1:1234/v1",
        api_key="fixture-gateway-token",
    ))
    launch = await hermes.HermesRuntime()._launch("hermes", turn, lambda: None)
    assert launch.acp_model == ("custom:jarvis:claude-sonnet-5" if previous_native else "")
    assert "CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND" not in launch.env
    assert launch.env["OPENAI_API_KEY"] == "fixture-gateway-token"
    config = json.loads((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert (config.get("plugins") or {}).get("enabled", []) == (
        [provider.PROVIDER_NAME] if previous_native else []
    )


def test_native_system_context_names_authoritative_tool_workspace(tmp_path):
    turn = _turn(tmp_path)
    turn = replace(turn, workspace=tmp_path / "Project with spaces")
    hermes.HermesRuntime()._write_profile(tmp_path, turn)
    persona = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert json.dumps(str(turn.workspace), ensure_ascii=False) in persona
    assert "authoritative workspace" in persona
    assert "temporary working directory" in persona
    assert "not the tool workspace" in persona
    assert "Resolve relative file paths against that workspace" in persona


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport, expected_updates", [("claude_cli", 1), ("chat_completions", 0)],
)
async def test_only_native_route_upgrades_older_api_capable_runtime(
    tmp_path, monkeypatch, transport, expected_updates,
):
    from jarvis.agent_chat import runner_acp
    from jarvis.agent_runtimes import manager

    runtime = hermes.HermesRuntime()
    old = RuntimeStatus("hermes", "Hermes", installed=True, ready=True, version="0.21.3")
    monkeypatch.setattr(runtime, "detect", lambda: old)
    monkeypatch.setattr(manager, "job", lambda name: None)
    monkeypatch.setattr(manager, "needed", lambda target, status: None)
    updates = []
    events = []

    async def start(name, kind):
        updates.append((name, kind))

    async def wait_ready(name):
        return replace(old, version="0.21.5")

    class Handle:
        turn_id = "fixture"

        async def emit(self, event):
            events.append(event)

    monkeypatch.setattr(manager, "start", start)
    monkeypatch.setattr(manager, "wait_ready", wait_ready)
    route = replace(_turn(tmp_path).route, transport=transport)
    await runner_acp._ready(Handle(), "hermes", runtime, route=route)
    assert updates == [("hermes", "update")] * expected_updates
    assert len(events) == expected_updates


@pytest.mark.asyncio
async def test_failed_route_upgrade_does_not_launch_on_old_runtime(tmp_path, monkeypatch):
    from jarvis.agent_chat import runner_acp
    from jarvis.agent_chat.runner_cli import CliUnavailable
    from jarvis.agent_runtimes import manager

    runtime = hermes.HermesRuntime()
    old = RuntimeStatus("hermes", "Hermes", installed=True, ready=True, version="0.21.3")
    monkeypatch.setattr(runtime, "detect", lambda: old)
    monkeypatch.setattr(manager, "job", lambda name: None)
    monkeypatch.setattr(manager, "needed", lambda target, status: None)
    monkeypatch.setattr(manager, "failure_reason", lambda name: "fixture network failure")

    async def start(name, kind):
        return None

    async def wait_ready(name):
        return old

    class Handle:
        turn_id = "fixture"

        async def emit(self, event):
            return None

    monkeypatch.setattr(manager, "start", start)
    monkeypatch.setattr(manager, "wait_ready", wait_ready)
    with pytest.raises(CliUnavailable, match="fixture network failure"):
        await runner_acp._ready(Handle(), "hermes", runtime, route=_turn(tmp_path).route)
