"""A connected Windows computer: every feature reaches it the way it can be reached.

Windows OpenSSH runs commands in ``cmd.exe``, which carries neither line
breaks nor non-ASCII text on its command line. These tests play such a server
(real asyncssh, a scripted "cmd") and pin what goes over the wire: detection,
PowerShell scripts on stdin, Git Bash launchers uploaded over SFTP, and the
answers each feature builds from them.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.agent_chat import remote_cli
from jarvis.computers import identity, remote_os, service, toolbox
from jarvis.computers.probe import WINDOWS_PROBE_SCRIPT, parse_probe
from jarvis.computers.remote_terminal import SshPtyPool, windows_pane_argv
from jarvis.computers.service import ComputerService
from jarvis.computers.ssh import authorize_key_script_windows
from jarvis.computers.store import ComputerStore
from jarvis.society import remote
from tests.fakes.fake_ssh_server import TEST_PASSWORD, FakeSshServer

GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
HOME = "C:/Users/ada"

WINDOWS_PROBE_OUTPUT = (
    "@@hostname\r\nDESK-01\r\n@@uname\r\nWindows 10.0.26200 AMD64\r\n"
    "@@os\r\nID=windows\r\nPRETTY_NAME=Windows 11 Pro\r\n@@nproc\r\n4\r\n"
    "@@meminfo\r\nMemTotal: 8388608 kB\r\nMemAvailable: 4194304 kB\r\n"
    "@@df\r\nC: 104857600 52428800 52428800 - C:\\\r\n@@uptime\r\n3600\r\n"
    "@@loadavg\r\n@@end\r\n"
)

INSPECT_OUTPUT = (
    "tool git ok git version 2.51.0.windows.1\r\ntool node ok v22.1.0\r\n"
    "tool npm ok 10.0.0\r\ntool claude ok 2.1.284 (Claude Code)\r\ntool codex missing\r\n"
    "login claude ok\r\nlogin codex missing\r\npkg winget\r\nuid 0\r\nsudo no\r\nos Windows\r\n"
)


class WindowsBox:
    """Scripted answers of a Windows OpenSSH server whose default shell is cmd."""

    def __init__(self, *, bash: bool = True, shell: str = "cmd") -> None:
        self.bash = bash
        self.shell = shell
        self.scripts: list[tuple[str, str]] = []
        self.launched: list[str] = []

    async def __call__(self, command: str, process: Any) -> bool:
        if command == remote_os.DETECT_COMMAND:
            if self.shell == "powershell":
                process.stdout.write("%OS%\r\nWindows_NT\r\n")
            else:
                process.stdout.write("Windows_NT $env:OS\r\n")
            process.exit(0)
            return True
        if command.endswith(remote_os.POWERSHELL_STDIN):
            script = await process.stdin.read()
            self.scripts.append(("powershell", script))
            process.stdout.write(self._powershell(script))
            process.exit(0)
            return True
        if "-l -s" in command and GIT_BASH in command:
            script = await process.stdin.read()
            self.scripts.append(("bash", script))
            if "pwd -W" in script:
                process.stdout.write(f"{HOME}/jarvis-agents/scout\nfound\n")
            else:
                process.stdout.write("bash-ran\n")
            process.exit(0)
            return True
        if "--noprofile --norc" in command:
            self.launched.append(command)
            if process.term_type:
                process.stdout.write("pane-ready\r\n")
                while True:
                    data = await process.stdin.read(1024)
                    if not data:
                        break
                    if "quit" in data:
                        process.exit(5)
                        return True
                    process.stdout.write("echo:" + data)
                process.exit(0)
                return True
            line = await process.stdin.readline()
            process.stdout.write(json.dumps({"type": "echo", "got": line.strip()}) + "\n")
            process.exit(0)
            return True
        return False

    def _powershell(self, script: str) -> str:
        if "Test-Path -LiteralPath $path" in script:
            return f"home {HOME}\r\n" + (f"bash {GIT_BASH}\r\n" if self.bash else "")
        if script == WINDOWS_PROBE_SCRIPT:
            return WINDOWS_PROBE_OUTPUT
        if "tool $t" in script:
            return INSPECT_OUTPUT
        return "ps-ran\r\n"


@pytest.fixture
def computers(tmp_path: Path, secret_box, monkeypatch: pytest.MonkeyPatch) -> ComputerService:  # noqa: ANN001
    path = tmp_path / "computers.json"
    monkeypatch.setattr("jarvis.computers.store.default_path", lambda: path)
    svc = ComputerService(ComputerStore(path))
    monkeypatch.setattr(service, "_SERVICE", svc)
    return svc


@pytest.fixture
def box() -> WindowsBox:
    return WindowsBox()


@pytest.fixture
async def ssh_server(tmp_path: Path, box: WindowsBox):  # noqa: ANN201
    home = tmp_path / "remote-home"
    home.mkdir()
    server = FakeSshServer(sftp_root=home)
    server.state.handler = box
    server.state.authorized.add(identity.public_key_line())
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def desk(computers: ComputerService, ssh_server: FakeSshServer):  # noqa: ANN201
    return await computers.add_server(name="Desk", host="127.0.0.1", port=ssh_server.port)


# -- pure pieces ------------------------------------------------------------------


def test_detection_tells_the_three_shells_apart() -> None:
    assert remote_os.parse_detect("Windows_NT $env:OS\r\n") == ("windows", "cmd")
    assert remote_os.parse_detect("%OS%\r\nWindows_NT\r\n") == ("windows", "powershell")
    assert remote_os.parse_detect("%OS% :OS\n") == ("posix", "sh")
    assert remote_os.parse_windows_facts(f"home {HOME}\r\nbash {GIT_BASH}\r\n") == (HOME, GIT_BASH)
    assert remote_os.parse_windows_facts(f"home {HOME}\r\n") == (HOME, None)


def test_command_lines_quote_the_program_once() -> None:
    cmd = remote_os.RemoteHost(os="windows", default_shell="cmd", home=HOME, bash=GIT_BASH)
    assert cmd.launcher_command("jarvis-agents/.launch/x.sh") == (
        f'"{GIT_BASH}" --noprofile --norc {HOME}/jarvis-agents/.launch/x.sh'
    )
    powershell = remote_os.RemoteHost(
        os="windows", default_shell="powershell", home=HOME, bash=GIT_BASH
    )
    assert powershell.bash_script_command() == f'& "{GIT_BASH}" -l -s'
    # A home with a space stays out of the line: the relative path resolves there.
    spaced = remote_os.RemoteHost(os="windows", home="C:/Users/Ada Lovelace", bash=GIT_BASH)
    assert spaced.launcher_command("x.sh").endswith("--norc x.sh")
    assert cmd.sftp_path("C:/Users/ada/f") == "/C:/Users/ada/f"
    assert remote_os.RemoteHost().sftp_path("C:/x") == "C:/x"


def test_launcher_keeps_every_argument_intact() -> None:
    argv = ["claude", "--print", 'a "b" & c', "café Ω ✓", "multi\nline", "/help", "100%PATH%"]
    script = remote_os.launcher_script(f"{HOME}/jarvis-agents/scout", argv, {"CI": "1"})
    lines = script.splitlines()
    assert lines[0] == f"cd -- {HOME}/jarvis-agents/scout || exit 97"
    assert "export CI=1" in lines
    assert "MSYS_NO_PATHCONV=1" in script
    exec_line = script.split("exec ", 1)[1]
    assert shlex.split(exec_line) == argv
    assert remote_os.launcher_name("society:scout/x") == "society-scout-x.sh"


def test_windows_probe_output_reads_like_linux() -> None:
    reading = parse_probe(WINDOWS_PROBE_OUTPUT)
    assert reading.facts.os_id == "windows"
    assert reading.facts.os_name == "Windows 11 Pro"
    assert reading.facts.hostname == "DESK-01"
    assert reading.facts.kernel == "Windows 10.0.26200" and reading.facts.arch == "AMD64"
    assert reading.facts.cpu_count == 4 and reading.facts.mem_total_mb == 8192
    assert reading.mem_used_pct == 50.0 and reading.disk_used_pct == 50.0
    assert reading.facts.disk_total_gb == 100.0 and reading.uptime_s == 3600
    assert reading.load_1m is None


def test_windows_readiness_needs_git_not_tmux() -> None:
    readiness = toolbox.parse_inspection(INSPECT_OUTPUT)
    assert [t.id for t in readiness.tools] == ["git", "node", "claude", "codex"]
    assert readiness.ready and readiness.root and readiness.os == "Windows"
    assert readiness.package_manager == "winget"
    script = toolbox.install_script(readiness, ["codex"])
    assert "npm install -g --silent @openai/codex" in script
    assert "winget" not in script
    no_node = toolbox.parse_inspection(
        INSPECT_OUTPUT.replace("tool node ok v22.1.0", "tool node missing")
    )
    script = toolbox.install_script(no_node, ["codex"])
    assert "winget install --id OpenJS.NodeJS.LTS" in script
    no_winget = toolbox.parse_inspection(
        INSPECT_OUTPUT.replace("pkg winget\r\n", "").replace(
            "tool git ok git version 2.51.0.windows.1", "tool git missing"
        )
    )
    with pytest.raises(service.ComputerError):
        toolbox.install_script(no_winget, ["git"])


def test_key_script_names_groups_by_sid_and_escapes_quotes() -> None:
    script = authorize_key_script_windows("ssh-ed25519 AAAA ada's-pc")
    assert "$key = 'ssh-ed25519 AAAA ada''s-pc'" in script
    assert "administrators_authorized_keys" in script
    assert "*S-1-5-32-544:F" in script and "*S-1-5-18:F" in script


def test_powershell_workspace_quotes_the_folder() -> None:
    script = remote.powershell_in_workspace("scout", "Get-ChildItem", relative="it's/sub")
    assert "Join-Path $HOME 'jarvis-agents\\scout\\it''s\\sub'" in script
    assert "\nGet-ChildItem\n" in script
    assert windows_pane_argv(("bash", "-l")) == ("powershell.exe", "-NoLogo")
    assert windows_pane_argv(("claude", "--resume", "x")) == ("claude", "--resume", "x")


# -- over SSH -------------------------------------------------------------------------


async def test_check_reads_windows_facts_through_powershell(desk, box: WindowsBox) -> None:  # noqa: ANN001
    assert desk.health.status == "online", desk.health.message
    assert desk.facts is not None and desk.facts.os_id == "windows"
    assert desk.facts.os_name == "Windows 11 Pro" and desk.facts.cpu_count == 4
    assert ("powershell", WINDOWS_PROBE_SCRIPT) in box.scripts


async def test_password_login_plants_the_key_on_windows(
    computers: ComputerService, ssh_server: FakeSshServer, box: WindowsBox
) -> None:
    ssh_server.state.authorized.clear()
    original = box._powershell

    def planting(script: str) -> str:
        if "administrators_authorized_keys" in script:
            match = re.search(r"\$key = '([^']+)'", script)
            assert match is not None
            ssh_server.state.authorized.add(match.group(1))
            return "ok\r\n"
        return original(script)

    box._powershell = planting  # type: ignore[method-assign]
    computer = await computers.add_server(
        name="Desk",
        host="127.0.0.1",
        port=ssh_server.port,
        auth="password",
        password=TEST_PASSWORD,
    )
    assert computer.auth == "key"
    assert computer.health.status == "online", computer.health.message
    assert not any("authorized_keys" in c and "umask" in c for c in ssh_server.state.commands)


async def test_cli_turn_starts_through_an_uploaded_launcher(
    desk,
    box: WindowsBox,
    tmp_path: Path,
    ssh_server: FakeSshServer,  # noqa: ANN001
) -> None:
    proc = await remote_cli.spawn(
        desk.id,
        agent_id="scout",
        runner="claude-cli",
        binary="claude",
        argv=[
            r"C:\bin\claude.exe",
            "--print",
            "--append-system-prompt-file",
            r"C:\local\identity.md",
            "--mcp-config",
            "{}",
        ],
        local_cwd=r"C:\ws",
        env={"PATH": r"C:\secret-path", "NO_COLOR": "1"},
        system_prompt_files={r"C:\local\identity.md": "You are Scout. café Ω ✓!"},
    )
    proc.stdin.write("naïve ✓\n".encode())
    await proc.stdin.drain()
    first = json.loads(await proc.stdout.readline())
    proc.stdin.close()
    assert await proc.wait() == 0
    assert first == {"type": "echo", "got": "naïve ✓"}

    home = tmp_path / "remote-home"
    launcher = (home / "jarvis-agents/.launch/scout.sh").read_text(encoding="utf-8")
    exec_line = launcher.split("exec ", 1)[1]
    assert shlex.split(exec_line) == [
        "claude",
        "--print",
        "--append-system-prompt-file",
        f"{HOME}/jarvis-agents/.launch/scout-prompt-0.md",
    ]
    assert f"cd -- {HOME}/jarvis-agents/scout" in launcher
    assert "secret-path" not in launcher
    prompt = (home / "jarvis-agents/.launch/scout-prompt-0.md").read_text(encoding="utf-8")
    assert prompt.startswith("You are Scout. café Ω ✓!")
    assert '"Desk"' in prompt and "Windows 11 Pro" in prompt
    launcher_path = f"{HOME}/jarvis-agents/.launch/scout.sh"
    assert box.launched == [f'"{GIT_BASH}" --noprofile --norc {launcher_path}']


async def test_cli_turn_without_git_bash_says_what_to_install(
    computers: ComputerService, ssh_server: FakeSshServer, box: WindowsBox
) -> None:
    box.bash = False
    desk = await computers.add_server(name="Desk", host="127.0.0.1", port=ssh_server.port)
    with pytest.raises(remote_cli.RemoteCliUnavailable) as caught:
        await remote_cli.spawn(
            desk.id,
            agent_id="scout",
            runner="claude-cli",
            binary="claude",
            argv=["claude", "--print"],
            local_cwd="/ws",
            env={},
        )
    assert "Git for Windows" in str(caught.value)


async def test_agent_shell_runs_in_git_bash_or_powershell(
    desk,
    box: WindowsBox,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    agent = SimpleNamespace(agent_id="scout", computer_id=desk.id)
    backend = remote.backend_for(agent, tmp_path)

    result = await backend.run("ls -la", cwd=tmp_path, timeout_s=30)
    assert result.ok and "bash-ran" in result.output
    kind, script = box.scripts[-1]
    assert kind == "bash" and script.endswith("&& ls -la")
    assert backend.where == "Desk (Windows, Git Bash)"

    box.bash = False
    remote_os.forget(desk.id)
    result = await backend.run("Get-ChildItem", cwd=tmp_path, timeout_s=30)
    assert result.ok and "ps-ran" in result.output
    kind, script = box.scripts[-1]
    assert kind == "powershell" and "Set-Location -LiteralPath $ws" in script
    assert backend.where == "Desk (Windows, PowerShell)"


async def test_readiness_is_read_in_powershell(desk, box: WindowsBox) -> None:  # noqa: ANN001
    readiness = await toolbox.inspect(desk.id)
    assert readiness.os == "Windows" and readiness.ready
    assert "tmux" not in [t.id for t in readiness.tools]


class _Screen:
    def __init__(self) -> None:
        self.text = ""
        self.closed: list[int] = []

    async def output(self, _tid: str, chunk: str) -> None:
        self.text += chunk

    async def exit(self, _tid: str, code: int) -> None:
        self.closed.append(code)

    async def wait_for(self, check: Any) -> None:
        for _ in range(250):
            if check():
                return
            await asyncio.sleep(0.02)
        raise AssertionError(f"never happened; screen: {self.text!r}")


async def test_ide_pane_runs_without_tmux_and_ends_with_its_channel(
    computers: ComputerService,
    desk,
    box: WindowsBox,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    pool = SshPtyPool(desk.id, connect=lambda: computers.connect(desk.id))
    try:
        assert await pool.home() == HOME
        assert await pool.sftp_path(f"{HOME}/x") == f"/{HOME}/x"
        code, out, _err = await pool.run("command -v git")
        assert code == 0 and "bash-ran" in out

        screen = _Screen()
        spawned = await pool.spawn(
            shell_argv=("bash", "-l"),
            shell_id="agentic-ide:t1",
            cwd=f"{HOME}/jarvis-workspaces/app",
            cols=100,
            rows=30,
            on_output=screen.output,
            on_closed=screen.exit,
            meta={"history_id": "pane-1"},
        )
        await screen.wait_for(lambda: "pane-ready" in screen.text)
        # The fake server edits lines (a real ConPTY would pass keys through).
        assert pool.write(spawned.terminal_id, "hi\r")
        await screen.wait_for(lambda: "echo:hi" in screen.text)
        launcher = (tmp_path / "remote-home/jarvis-agents/.launch/jv-pane-1.sh").read_text()
        assert "exec powershell.exe -NoLogo" in launcher
        assert not any("tmux" in c for c in box.launched)

        assert pool.write(spawned.terminal_id, "quit\r")
        await screen.wait_for(lambda: screen.closed)
        assert screen.closed == [5], "the agent's own exit, no re-attach attempt"
    finally:
        pool.close_all()


async def test_keep_working_never_moves_panes_to_windows(
    desk,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without tmux its agents would stop with the app's connection at quit."""
    from jarvis.agentic_ide import offload_on_quit

    monkeypatch.setattr(offload_on_quit, "target", lambda: desk.id)
    placed: list[str] = []

    async def place_workspace(workspace_id: str, *, computer_id: str) -> None:
        placed.append(workspace_id)

    running = SimpleNamespace(pty_id="p1", computer_id="")
    registry = SimpleNamespace(
        sessions=[SimpleNamespace(id="ws1", name="app", terminals=[running])],
        place_workspace=place_workspace,
    )

    assert await offload_on_quit.offload_before_quit(registry) == []
    assert placed == []


async def test_cancelling_a_turn_ends_the_remote_process_tree(
    desk,  # noqa: ANN001
    box: WindowsBox,
    ssh_server: FakeSshServer,
    tmp_path: Path,
) -> None:
    """Hanging up ends only cmd and bash on Windows; the CLI below them lived on."""
    stopped = asyncio.Event()

    async def handler(command: str, process: Any) -> bool:
        if "--noprofile --norc" in command:
            await stopped.wait()  # the CLI runs until its tree is ended
            process.exit(1)
            return True
        if "-l -s" in command and GIT_BASH in command:
            script = await process.stdin.read()
            box.scripts.append(("bash", script))
            if "taskkill" in script:
                stopped.set()
            elif "pwd -W" in script:
                process.stdout.write(f"{HOME}/jarvis-agents/scout\nfound\n")
            process.exit(0)
            return True
        return await box(command, process)

    ssh_server.state.handler = handler
    proc = await remote_cli.spawn(
        desk.id,
        agent_id="scout",
        runner="claude-cli",
        binary="claude",
        argv=["claude", "--print"],
        local_cwd="/ws",
        env={},
    )
    launcher = (tmp_path / "remote-home/jarvis-agents/.launch/scout.sh").read_text(encoding="utf-8")
    assert 'cat /proc/$$/winpid > "$HOME"/jarvis-agents/.launch/scout.pid' in launcher

    proc.kill()
    assert await asyncio.wait_for(proc.wait(), timeout=30) == 1
    stop = next(script for _kind, script in box.scripts if "taskkill" in script)
    assert "jarvis-agents/.launch/scout.pid" in stop and "/T /F" in stop
