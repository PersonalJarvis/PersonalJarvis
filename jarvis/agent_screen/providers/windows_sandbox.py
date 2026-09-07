"""Windows Sandbox as an agent screen.

Windows Sandbox is a throwaway Windows session with its own desktop, its own
input stream and its own filesystem, booted from the host image in seconds and
discarded on stop. That makes it the right first provider on Windows: the
agent gets a real Windows it can click, install into and browse from, while
the user's desktop is untouched — and nothing the agent does survives unless
we explicitly mapped a folder for it.

Two things are deliberate and worth stating, because a later change will be
tempted to undo both:

* **Networking is configurable and defaults to enabled**, but the transport is
  files in a mapped folder, so the sandbox still works with networking off.
  The host cannot dial into the guest anyway (it is NAT'd), which is why a
  loopback socket was never an option here.
* **Exactly one folder is mapped writable** — the RPC mailbox and the
  persistent browser profile. Mapping the repository or the user's home in
  would hand a disposable session the very data the isolation exists to
  protect.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from jarvis.agent_screen.protocol import (
    AgentScreenUnavailable,
    ProbeResult,
    ScreenProvider,
    ScreenSession,
    ScreenSpec,
)
from jarvis.agent_screen.providers.base import (
    mint_token,
    popen_detached,
    profile_root,
    screen_dir,
    stage_sandbox_runner,
    terminate_quietly,
)
from jarvis.agent_screen.session import RemoteScreenSession
from jarvis.agent_screen.wire import MailboxTransport

logger = logging.getLogger(__name__)

#: Guest-side mount points. Fixed because the LogonCommand has to name them
#: literally and the guest is always Windows.
_GUEST_RUNNER_DIR = r"C:\jarvis\runner"
_GUEST_RPC_DIR = r"C:\jarvis\rpc"
_GUEST_PROFILE_DIR = r"C:\jarvis\profile"

#: A cold sandbox boots the whole Windows session before our LogonCommand
#: runs. Measured elsewhere at 20-40 s on warm hosts; the ceiling is generous
#: because failing a real boot as "unavailable" would be the worse error.
_BOOT_TIMEOUT_S = 180.0
_BOOT_POLL_S = 1.0


def _sandbox_binaries() -> tuple[str | None, str | None]:
    """``(wsb.exe, WindowsSandbox.exe)`` — whichever exist on this host.

    ``wsb.exe`` is the newer CLI (Windows 11 24H2 era). Where it exists it is
    preferred: it can start a sandbox by config and stop it by name, which
    gives the provider a real lifecycle instead of "kill the window".
    """
    if sys.platform != "win32":
        return (None, None)
    system32 = Path(r"C:\Windows\System32")
    wsb = shutil.which("wsb") or shutil.which("wsb.exe")
    if wsb is None and (system32 / "wsb.exe").is_file():
        wsb = str(system32 / "wsb.exe")
    classic = shutil.which("WindowsSandbox") or shutil.which("WindowsSandbox.exe")
    if classic is None and (system32 / "WindowsSandbox.exe").is_file():
        classic = str(system32 / "WindowsSandbox.exe")
    return (wsb, classic)


class WindowsSandboxProvider(ScreenProvider):
    """Boots a Windows Sandbox and drives it through a mapped mailbox."""

    kind = "windows-sandbox"

    def __init__(
        self,
        *,
        memory_mb: int = 4096,
        networking: bool = True,
        vgpu: bool = False,
    ) -> None:
        self._memory_mb = max(1024, int(memory_mb))
        self._networking = bool(networking)
        self._vgpu = bool(vgpu)

    # -- probe -------------------------------------------------------------

    def probe(self) -> ProbeResult:
        if sys.platform != "win32":
            return ProbeResult(
                kind=self.kind,
                available=False,
                reason="Windows Sandbox exists only on Windows",
                remedy="",
            )
        wsb, classic = _sandbox_binaries()
        if wsb is None and classic is None:
            return ProbeResult(
                kind=self.kind,
                available=False,
                reason=(
                    "the Windows Sandbox feature is not enabled on this machine "
                    "(neither wsb.exe nor WindowsSandbox.exe is present)"
                ),
                remedy=(
                    "Enable it once, as administrator: "
                    "Enable-WindowsOptionalFeature -Online -FeatureName "
                    "Containers-DisposableClientVM -All  — then restart Windows. "
                    "It needs Windows Pro/Enterprise/Education and virtualization "
                    "enabled in the firmware."
                ),
            )
        return ProbeResult(kind=self.kind, available=True)

    @property
    def max_screens(self) -> int:
        """How many sandboxes may run at once on this host.

        The classic launcher supports a single sandbox; the newer ``wsb.exe``
        manages named instances. Reporting the truth keeps the manager from
        promising a second screen that the host will refuse to boot.
        """
        wsb, _classic = _sandbox_binaries()
        return 4 if wsb is not None else 1

    # -- start -------------------------------------------------------------

    def start(self, spec: ScreenSpec) -> ScreenSession:
        probe = self.probe()
        if not probe.available:
            raise AgentScreenUnavailable(f"{probe.reason}. {probe.remedy}".strip())

        screen_id = f"wsb-{uuid.uuid4().hex[:8]}"
        root = screen_dir(screen_id)
        runner_dir = root / "runner"
        rpc_dir = root / "rpc"
        rpc_dir.mkdir(parents=True, exist_ok=True)
        stage_sandbox_runner(runner_dir)

        token = mint_token()
        # The guest reads its config from the mapped mailbox. The token has to
        # be reachable from inside, so it rides the same folder — its secrecy
        # comes from the folder being mapped into exactly one sandbox.
        import json  # noqa: PLC0415

        (rpc_dir / "config.json").write_text(
            json.dumps({"token": token, "mailbox": _GUEST_RPC_DIR}, ensure_ascii=False),
            encoding="utf-8",
        )

        wsb_path = root / f"{screen_id}.wsb"
        wsb_path.write_text(
            self._render_config(runner_dir, rpc_dir, profile_root()),
            encoding="utf-8",
        )

        managed_by_cli, process = self._launch(screen_id, wsb_path)

        transport = MailboxTransport(rpc_dir, token)
        session = RemoteScreenSession(
            screen_id=screen_id,
            kind="windows-sandbox",
            transport=transport,
            owner=spec.owner,
            purpose=spec.purpose,
            # The classic launcher always paints its viewer window; only the
            # id-managed CLI path is genuinely invisible.
            hidden=managed_by_cli,
            on_close=lambda: self._stop(screen_id, managed_by_cli, process),
        )
        try:
            self._await_boot(session, screen_id)
        except Exception:
            self._stop(screen_id, managed_by_cli, process)
            raise
        self._sweep(rpc_dir)
        return session

    def _launch(
        self,
        screen_id: str,
        wsb_path: Path,
    ) -> tuple[bool, subprocess.Popen[bytes] | None]:
        """Start the sandbox, preferring a lifecycle we can address by id.

        Only ONE ``wsb.exe`` invocation shape is used, and only when it also
        accepts ``--id``: without an id there is no way to stop this specific
        sandbox later, and stopping "all" of them would kill sandboxes the
        user started for their own reasons. When that shape is unavailable the
        provider falls back to the classic launcher, where owning the process
        handle gives an equally precise teardown.

        Returns ``(managed_by_cli, process)``.
        """
        wsb, classic = _sandbox_binaries()
        if wsb is not None:
            argv = [wsb, "start", "--config", str(wsb_path), "--id", screen_id]
            try:
                completed = subprocess.run(  # noqa: S603 — fixed system binary
                    argv,
                    capture_output=True,
                    timeout=90,
                    check=False,
                )
                if completed.returncode == 0:
                    return (True, None)
                logger.info(
                    "[agent-screen] wsb start --id rejected (rc=%s); using the "
                    "classic launcher instead: %s",
                    completed.returncode,
                    completed.stderr[:200].decode("utf-8", "replace"),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.info("[agent-screen] wsb start unusable (%s)", exc)
        if classic is None:
            raise AgentScreenUnavailable(
                "Windows Sandbox is present but could not be started: the "
                "wsb CLI rejected the start and no WindowsSandbox.exe was "
                "found. Check that Windows Sandbox starts manually.",
            )
        # The classic launcher always shows its viewer window — the sandbox is
        # isolated but not invisible on this path. Reported honestly by the
        # manager rather than papered over.
        return (False, popen_detached([classic, str(wsb_path)]))

    @staticmethod
    def _sweep(rpc_dir: Path) -> None:
        """Drop answers to boot probes the host had already given up on."""
        for stale in rpc_dir.glob("*.res.*"):
            try:
                stale.unlink(missing_ok=True)
            except OSError:  # noqa: PERF203 — hygiene only
                logger.debug("could not clear a stale mailbox file", exc_info=True)

    def _render_config(self, runner_dir: Path, rpc_dir: Path, profile_dir: Path) -> str:
        """Render the .wsb XML. Host paths are escaped — they contain user text."""
        mapped = [
            (runner_dir, _GUEST_RUNNER_DIR, True),
            (rpc_dir, _GUEST_RPC_DIR, False),
            (profile_dir, _GUEST_PROFILE_DIR, False),
        ]
        folders = "\n".join(
            "    <MappedFolder>\n"
            f"      <HostFolder>{xml_escape(str(host))}</HostFolder>\n"
            f"      <SandboxFolder>{xml_escape(guest)}</SandboxFolder>\n"
            f"      <ReadOnly>{'true' if read_only else 'false'}</ReadOnly>\n"
            "    </MappedFolder>"
            for host, guest, read_only in mapped
        )
        command = (
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -File "
            rf"{_GUEST_RUNNER_DIR}\sandbox_runner.ps1 "
            rf"-ConfigPath {_GUEST_RPC_DIR}\config.json"
        )
        return (
            "<Configuration>\n"
            f"  <VGpu>{'Enable' if self._vgpu else 'Disable'}</VGpu>\n"
            f"  <Networking>{'Enable' if self._networking else 'Disable'}</Networking>\n"
            f"  <MemoryInMB>{self._memory_mb}</MemoryInMB>\n"
            "  <MappedFolders>\n"
            f"{folders}\n"
            "  </MappedFolders>\n"
            "  <LogonCommand>\n"
            f"    <Command>{xml_escape(command)}</Command>\n"
            "  </LogonCommand>\n"
            "</Configuration>\n"
        )

    def _await_boot(self, session: RemoteScreenSession, screen_id: str) -> None:
        """Poll until the guest runner answers, or fail with a named reason."""
        deadline = time.monotonic() + _BOOT_TIMEOUT_S
        last_error = ""
        while time.monotonic() < deadline:
            try:
                session.handshake(timeout_s=_BOOT_POLL_S)
            except AgentScreenUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 — the guest is still booting
                last_error = str(exc)
                time.sleep(_BOOT_POLL_S)
                continue
            logger.info("[agent-screen] %s: sandbox runner is up", screen_id)
            return
        raise AgentScreenUnavailable(
            f"the Windows Sandbox screen did not come up within "
            f"{_BOOT_TIMEOUT_S:.0f}s ({last_error or 'no answer from the runner'}). "
            "Check that Windows Sandbox starts manually on this machine.",
        )

    def _stop(
        self,
        screen_id: str,
        managed_by_cli: bool,
        process: subprocess.Popen[bytes] | None,
    ) -> None:
        """Tear down exactly THIS sandbox, never any other."""
        if managed_by_cli:
            wsb, _classic = _sandbox_binaries()
            if wsb is not None:
                try:
                    subprocess.run(  # noqa: S603 — fixed system binary
                        [wsb, "stop", "--id", screen_id],
                        capture_output=True,
                        timeout=60,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    logger.warning(
                        "[agent-screen] could not stop sandbox %s", screen_id,
                        exc_info=True,
                    )
        terminate_quietly(process)


__all__ = ["WindowsSandboxProvider"]
