"""Linux and macOS computers: the user's own PATH, any login shell, a Mac's login.

Over SSH the search path is short — ``~/.local/bin`` (Claude Code's own
installer) and ``/opt/homebrew/bin`` (Homebrew on Apple silicon) are missing —
and the login shell may be fish, which cannot parse a POSIX script on the
command line. These tests pin the answers: detection asks the login shell for
its PATH, every script travels on stdin to sh/bash behind that PATH, every
program starts from an uploaded launcher, and a Mac's agents log in with a
token because its Keychain is locked in SSH sessions.
"""

from __future__ import annotations

import re
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.agent_chat import remote_cli
from jarvis.computers import identity, remote_os, service, toolbox
from jarvis.computers.service import ComputerError, ComputerService
from jarvis.computers.store import ComputerStore
from jarvis.society import remote
from tests.fakes.fake_ssh_server import FakeSshServer

MAC_INSPECT = (
    "tool tmux ok tmux 3.5a\ntool git missing\ntool node ok v12.22.9\n"
    "tool claude ok 2.1.284 (Claude Code)\ntool codex missing\n"
    "login claude missing\nlogin codex missing\npkg brew\nuid 501\nsudo no\n"
    "os Darwin\narch arm64\nfetch curl\nclt no\n"
)

LINUX_INSPECT = (
    "tool tmux missing\ntool git ok git version 2.43.0\ntool node ok v12.22.9\n"
    "tool claude missing\ntool codex missing\n"
    "login claude missing\nlogin codex missing\npkg apt-get\nuid 1000\nsudo yes\n"
    "os Linux\narch x86_64\nfetch curl\nclt yes\n"
)

#: The only command lines a login shell is ever asked to parse.
_SAFE_LINE = re.compile(
    r"^(echo %OS% \$env:OS|/bin/(ba)?sh -s|/bin/sh jarvis-agents/\.launch/\S+)$"
)


@pytest.fixture
def computers(tmp_path: Path, secret_box, monkeypatch: pytest.MonkeyPatch) -> ComputerService:  # noqa: ANN001
    path = tmp_path / "computers.json"
    monkeypatch.setattr("jarvis.computers.store.default_path", lambda: path)
    svc = ComputerService(ComputerStore(path))
    monkeypatch.setattr(service, "_SERVICE", svc)
    return svc


@pytest.fixture
async def ssh_server(tmp_path: Path):  # noqa: ANN201
    home = tmp_path / "remote-home"
    home.mkdir()
    server = FakeSshServer(sftp_root=home)
    server.state.authorized.add(identity.public_key_line())
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def box(computers: ComputerService, ssh_server: FakeSshServer):  # noqa: ANN201
    return await computers.add_server(name="Box", host="127.0.0.1", port=ssh_server.port)


# -- pure pieces ------------------------------------------------------------------


def test_the_login_path_is_read_between_markers_and_checked() -> None:
    path = "/opt/homebrew/bin:/Users/a/.local/bin:/usr/bin"
    noisy = f"Welcome!\n__JARVIS_PATH__{path}__JARVIS_PATH__\n"
    assert remote_os.parse_login_path(noisy) == path
    assert remote_os.parse_login_path("no markers here") is None
    assert remote_os.parse_login_path("__JARVIS_PATH__relative:bin__JARVIS_PATH__") is None
    script = remote_os.login_path_script("-ilc")
    assert '"${SHELL:-/bin/sh}" -ilc' in script and "</dev/null" in script


def test_the_preamble_puts_the_users_path_and_the_install_folders_first() -> None:
    host = remote_os.RemoteHost(path="/opt/homebrew/bin:/usr/bin")
    preamble = remote_os.env_preamble(host)
    assert preamble.startswith('PATH=/opt/homebrew/bin:/usr/bin:"$PATH"\n')
    assert '"$HOME/.local/bin"' in preamble and "/opt/homebrew/bin" in preamble
    assert preamble.rstrip().endswith("export PATH")
    assert remote_os.env_preamble(remote_os.RemoteHost(os="windows")) == ""
    assert remote_os.RemoteHost(bash="/bin/bash").script_command() == "/bin/bash -s"
    assert remote_os.RemoteHost().script_command() == "/bin/sh -s"
    assert remote_os.RemoteHost().launcher_command("jarvis-agents/.launch/x.sh") == (
        "/bin/sh jarvis-agents/.launch/x.sh"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX /bin/sh")
def test_the_preamble_runs_in_a_real_sh(tmp_path: Path) -> None:
    import subprocess

    host = remote_os.RemoteHost(path="/custom/bin:/usr/bin:/bin")
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    script = remote_os.env_preamble(host) + 'printf %s "$PATH"\n'
    result = subprocess.run(  # noqa: S603 — fixed argv, script on stdin
        ["/bin/sh", "-s"],
        input=script.encode(),
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        check=True,
    )
    path = result.stdout.decode().split(":")
    # The user's own folder wins over the system's (a newer Node.js lives there).
    assert path[0] == f"{tmp_path}/.local/bin"
    assert path[1] == "/custom/bin"


def test_a_bare_cli_name_is_the_program_not_its_first_argument() -> None:
    """Planned for another computer, argv starts with the bare name ("claude")."""
    common = {"local_cwd": "/ws", "remote_cwd": "/home/u/jarvis-agents/a"}
    out = remote_cli.remote_argv(["claude", "--print"], binary="claude", **common)
    assert out == ["claude", "--print"]
    out = remote_cli.remote_argv(["codex.cmd", "exec", "-"], binary="codex", **common)
    assert out == ["codex", "exec", "-"]
    # A real first argument that happens to differ stays.
    out = remote_cli.remote_argv(["exec", "--json"], binary="codex", **common)
    assert out == ["codex", "exec", "--json"]


def test_a_macs_health_reading_has_memory_uptime_and_the_data_volume() -> None:
    from jarvis.computers.probe import parse_probe

    reading = parse_probe(
        "@@hostname\nmac-mini\n@@uname\nDarwin 24.0.0 arm64\n@@os\n"
        "@@darwin\nProductName:\t\tmacOS\nProductVersion:\t\t15.0\nBuildVersion:\t\t24A335\n"
        "@@nproc\n10\n@@meminfo\nMemTotal: 16777216 kB\nMemAvailable: 4194304 kB\n"
        "@@memsize\n17179869184\n"
        "@@df\n/dev/disk3s5 482797652 241398826 241398826 50% /System/Volumes/Data\n"
        "@@uptime\n3600\n@@loadavg\n{ 1.50 1.20 1.00 }\n@@end\n"
    )
    assert reading.facts.os_id == "macos" and reading.facts.os_name == "macOS 15.0"
    assert reading.facts.mem_total_mb == 16384 and reading.mem_used_pct == 75.0
    assert reading.disk_used_pct == 50.0 and reading.uptime_s == 3600
    assert reading.load_1m == 1.5


def test_a_mac_counts_the_git_placeholder_and_old_node_as_missing() -> None:
    readiness = toolbox.parse_inspection(MAC_INSPECT)
    by_id = {tool.id: tool for tool in readiness.tools}
    assert not by_id["git"].installed
    assert by_id["node"].outdated and not by_id["node"].installed
    assert by_id["node"].version == "v12.22.9"
    assert readiness.os == "Darwin" and readiness.package_manager == "brew"
    assert not readiness.ready


def test_a_mac_installs_through_homebrew_without_root() -> None:
    readiness = toolbox.parse_inspection(MAC_INSPECT)
    script = toolbox.install_script(readiness, ["git", "node", "codex"])
    assert "brew install git node" in script
    assert "sudo" not in script
    assert 'npm install -g --silent --prefix "$HOME/.local" @openai/codex' in script
    no_brew = toolbox.parse_inspection(MAC_INSPECT.replace("pkg brew\n", ""))
    with pytest.raises(ComputerError) as caught:
        toolbox.install_script(no_brew, ["git"])
    assert "Homebrew" in caught.value.message


def test_linux_installs_claude_natively_and_node_from_nodejs_org() -> None:
    readiness = toolbox.parse_inspection(LINUX_INSPECT)
    script = toolbox.install_script(readiness, ["tmux", "claude", "codex"])
    assert "sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tmux" in script
    assert "curl -fsSL https://claude.ai/install.sh | bash" in script
    assert "@anthropic-ai/claude-code" not in script, "Claude Code needs no Node.js"
    assert "https://nodejs.org/dist/latest-v24.x" in script
    assert "nodejs npm" not in script, "the distribution's Node.js is too old"
    assert 'ln -sf "$node_dir/bin/$b" "$HOME/.local/bin/$b"' in script
    no_sudo = toolbox.parse_inspection(LINUX_INSPECT.replace("sudo yes", "sudo no"))
    with pytest.raises(ComputerError):
        toolbox.install_script(no_sudo, ["tmux"])
    # Claude Code alone needs neither root nor Node.js.
    assert "curl -fsSL" in toolbox.install_script(no_sudo, ["claude"])


# -- over SSH -------------------------------------------------------------------------


async def test_detection_reads_the_login_shells_path(box, ssh_server: FakeSshServer) -> None:  # noqa: ANN001
    async with service.get_service().session(box.id) as opened:
        host = await remote_os.remote_host(box.id, opened)
    assert host.os == "posix" and host.system == "Linux"
    assert host.home == "/home/test" and host.bash == "/bin/bash"
    assert host.login_shell == "/bin/bash"
    assert host.path == "/usr/local/bin:/usr/bin:/bin"
    assert any("-ilc" in c and "__JARVIS_PATH__" in c for c in ssh_server.state.commands)


async def test_no_script_ever_reaches_the_login_shell(
    box,  # noqa: ANN001
    ssh_server: FakeSshServer,
    tmp_path: Path,
) -> None:
    """fish, tcsh or nu as login shell parse only these few fixed lines."""

    async def handler(command: str, process: Any) -> bool:
        if "command -v" in command and "pwd" in command:
            process.stdout.write("/home/test/jarvis-agents/scout\nfound\n")
            process.exit(0)
            return True
        if "exec claude" in command:
            process.exit(0)
            return True
        return False

    ssh_server.state.handler = handler
    await service.get_service().check(box.id)
    await toolbox.inspect(box.id)
    backend = remote.backend_for(SimpleNamespace(agent_id="scout", computer_id=box.id), tmp_path)
    await backend.run("echo hi; ls", cwd=tmp_path, timeout_s=30)
    proc = await remote_cli.spawn(
        box.id,
        agent_id="scout",
        runner="claude-cli",
        binary="claude",
        argv=["claude", "--print"],
        local_cwd="/ws",
        env={},
    )
    await proc.wait()
    unsafe = [line for line in ssh_server.state.lines if not _SAFE_LINE.match(line)]
    assert unsafe == []


async def test_a_claude_token_is_saved_owner_only_on_that_computer(
    box,  # noqa: ANN001
    ssh_server: FakeSshServer,
) -> None:
    token = "sk-ant-oat01-" + "a" * 40
    await toolbox.save_claude_token(box.id, token)
    saved = ssh_server.sftp_root / ".config" / "jarvis" / "agent.env"
    assert f"export CLAUDE_CODE_OAUTH_TOKEN={token}\n" in saved.read_text()
    if sys.platform != "win32":
        assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    with pytest.raises(ComputerError):
        await toolbox.save_claude_token(box.id, "not a token; rm -rf ~")


async def test_readiness_on_a_mac_reads_brew_and_the_git_placeholder(
    box,  # noqa: ANN001
    ssh_server: FakeSshServer,
) -> None:
    async def handler(command: str, process: Any) -> bool:
        if "tool $t" in command:
            assert "xcode-select -p" in command and "/usr/bin/git" in command
            process.stdout.write(MAC_INSPECT)
            process.exit(0)
            return True
        return False

    ssh_server.state.handler = handler
    readiness = await toolbox.inspect(box.id)
    assert readiness.package_manager == "brew"
    assert [t.id for t in readiness.tools if not t.installed] == ["git", "node", "codex"]
