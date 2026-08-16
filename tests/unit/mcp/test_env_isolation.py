"""What a community plugin's process is allowed to see.

The point of the isolation is a single sentence: a stranger's code running as
our child process must not be able to read the owner's API keys by looking at
its own environment.
"""

from __future__ import annotations

import sys

import pytest

from jarvis.mcp.env_isolation import isolated_environment

# The shapes a real machine actually leaks: exported provider keys, a token a
# shell profile set, and the credential helpers a developer accumulates.
SECRETS = {
    "OPENAI_API_KEY": "sk-not-a-real-key-000000000000",
    "ANTHROPIC_API_KEY": "sk-ant-not-real-000000000000",
    "GITHUB_TOKEN": "gho_notarealtoken0000000000",
    "AWS_SECRET_ACCESS_KEY": "not-real",
    "JARVIS_CONTROL_KEY": "not-real",
}


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    # The variables a process genuinely needs, so the test proves the cut is
    # selective rather than total.
    monkeypatch.setenv("PATH", "/usr/bin")
    if sys.platform == "win32":
        monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")


def test_no_credential_survives(machine: None) -> None:
    env = isolated_environment()
    for name in SECRETS:
        assert name not in env, f"{name} reached an untrusted plugin"


def test_the_process_can_still_start(machine: None) -> None:
    env = isolated_environment()
    assert env.get("PATH") == "/usr/bin"
    if sys.platform == "win32":
        # Without SYSTEMROOT the loader cannot find the C runtime and the
        # process dies before its first line.
        assert "SYSTEMROOT" in env


def test_the_plugin_still_gets_its_own_token(machine: None) -> None:
    env = isolated_environment({"TODOFOX_TOKEN": "tfx_this_plugins_own"})
    assert env["TODOFOX_TOKEN"] == "tfx_this_plugins_own"  # noqa: S105 - fixture
    assert "OPENAI_API_KEY" not in env


def test_a_proxy_survives_because_nothing_installs_without_it(
    machine: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:8080")
    assert isolated_environment()["HTTPS_PROXY"] == "http://proxy.internal:8080"


def test_the_client_actually_honours_the_flag(machine: None) -> None:
    """The end of the wire.

    `isolated_environment` can be perfect and the flag can be set correctly on
    every community plugin, and the credentials still leak if the one place
    that builds the child's environment ignores both.
    """
    from jarvis.mcp.client import MCPClient
    from jarvis.mcp.registry import MCPServerSpec

    def build(isolate: bool) -> dict[str, str]:
        spec = MCPServerSpec(
            name="todo-fox",
            display="TodoFox",
            description="Tasks",
            install_command=["npx", "-y", "@todofox/mcp@1.0.0"],
            transport="stdio",
            isolate_env=isolate,
        )
        client = MCPClient(spec, env_overrides={"TODOFOX_TOKEN": "tfx_own"})
        return client._resolve_install_command()[2]

    isolated = build(True)
    assert "OPENAI_API_KEY" not in isolated
    assert isolated["TODOFOX_TOKEN"] == "tfx_own"  # noqa: S105 - fixture

    # The default is unchanged: the user's own servers still see everything.
    assert build(False)["OPENAI_API_KEY"] == SECRETS["OPENAI_API_KEY"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows env names are case-insensitive")
def test_windows_matches_a_name_in_any_case(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Path` and `PATH` are the same variable on Windows, and os.environ can
    hand back either spelling depending on who set it."""
    monkeypatch.setenv("Path", "C:\\bin")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-real-0000000000000")
    env = isolated_environment()
    assert any(key.upper() == "PATH" for key in env)
    assert "OPENAI_API_KEY" not in env
