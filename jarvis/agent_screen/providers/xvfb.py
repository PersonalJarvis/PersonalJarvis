"""A virtual X display as an agent screen (Linux).

The cheapest real isolation of the three supported systems: ``Xvfb`` is an X
server that renders into memory instead of a monitor, so a process started
against it has its own display, its own focus and — crucially — its own XTest
input stream. Synthetic input there never reaches the user's session, and the
user never sees a window.

A window manager matters more here than it looks. Without one, X11 has no
concept of a focused top-level window, so "click the button in that dialog"
degrades badly and ``switch_window`` cannot work at all. The provider starts
one when the host has it and says so honestly when it does not, rather than
pretending the screen is fully featured.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

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
    screen_dir,
    terminate_quietly,
    write_token_file,
)
from jarvis.agent_screen.session import RemoteScreenSession
from jarvis.agent_screen.wire import HttpTransport

logger = logging.getLogger(__name__)

#: Window managers tried in order. All are tiny, none needs a session bus.
_WINDOW_MANAGERS = ("openbox", "fluxbox", "i3", "matchbox-window-manager", "twm")

_DISPLAY_RANGE = range(90, 130)
_BOOT_TIMEOUT_S = 45.0
_BOOT_POLL_S = 0.25

#: X11's own well-known locations, fixed by the protocol — not scratch files
#: of ours, so the usual "put temporary files somewhere private" rule does not
#: apply. ``Xvfb :N`` refuses to start while the lock file exists, which is
#: exactly what makes them the right liveness markers.
_X_LOCK_TEMPLATE = "/tmp/.X{n}-lock"  # noqa: S108 — X11 protocol location
_X_SOCKET_TEMPLATE = "/tmp/.X11-unix/X{n}"  # noqa: S108 — X11 protocol location


def _free_display() -> int:
    """Pick a display number no X server is currently using."""
    for number in _DISPLAY_RANGE:
        lock = Path(_X_LOCK_TEMPLATE.format(n=number))
        socket = Path(_X_SOCKET_TEMPLATE.format(n=number))
        if not lock.exists() and not socket.exists():
            return number
    raise AgentScreenUnavailable(
        "no free X display number is available for an agent screen",
    )


class XvfbProvider(ScreenProvider):
    """Runs a virtual X display plus an in-session runner."""

    kind = "xvfb"

    def probe(self) -> ProbeResult:
        if not sys.platform.startswith("linux"):
            return ProbeResult(
                kind=self.kind,
                available=False,
                reason="virtual X displays are a Linux capability",
            )
        if shutil.which("Xvfb") is None:
            return ProbeResult(
                kind=self.kind,
                available=False,
                reason="Xvfb is not installed",
                remedy=(
                    "Install it: sudo apt install xvfb  (Debian/Ubuntu) or "
                    "sudo dnf install xorg-x11-server-Xvfb (Fedora). For window "
                    "focus and switch_window also install a small window "
                    "manager, e.g. sudo apt install openbox."
                ),
            )
        return ProbeResult(kind=self.kind, available=True)

    @property
    def max_screens(self) -> int:
        """Virtual displays are cheap; the manager's own cap is the real limit."""
        return len(_DISPLAY_RANGE)

    def start(self, spec: ScreenSpec) -> ScreenSession:
        probe = self.probe()
        if not probe.available:
            raise AgentScreenUnavailable(f"{probe.reason}. {probe.remedy}".strip())

        screen_id = f"xvfb-{uuid.uuid4().hex[:8]}"
        root = screen_dir(screen_id)
        display_number = _free_display()
        display = f":{display_number}"

        xvfb = popen_detached(
            [
                "Xvfb",
                display,
                "-screen",
                "0",
                f"{max(320, spec.width)}x{max(240, spec.height)}x24",
                "-nolisten",
                "tcp",
            ],
        )
        helpers: list[subprocess.Popen[bytes]] = []
        runner: subprocess.Popen[bytes] | None = None

        def teardown() -> None:
            terminate_quietly(runner)
            for helper in helpers:
                terminate_quietly(helper)
            terminate_quietly(xvfb)

        try:
            self._await_display(display_number, xvfb)
            window_manager = next(
                (name for name in _WINDOW_MANAGERS if shutil.which(name)),
                None,
            )
            if window_manager is not None:
                helpers.append(popen_detached([window_manager], env={"DISPLAY": display}))
            else:
                logger.warning(
                    "[agent-screen] %s has no window manager — window focus and "
                    "switch_window will be limited. Install openbox to fix.",
                    screen_id,
                )

            token = mint_token()
            token_file = root / "token"
            port_file = root / "port"
            port_file.unlink(missing_ok=True)
            write_token_file(token_file, token)
            runner = popen_detached(
                [
                    sys.executable,
                    "-m",
                    "jarvis.agent_screen.runner",
                    "--transport",
                    "http",
                    "--kind",
                    "xvfb",
                    "--token-file",
                    str(token_file),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--port-file",
                    str(port_file),
                ],
                env={"DISPLAY": display},
            )
            port = self._await_port(port_file, runner)
            session = RemoteScreenSession(
                screen_id=screen_id,
                kind="xvfb",
                transport=HttpTransport("127.0.0.1", port, token),
                owner=spec.owner,
                purpose=spec.purpose,
                hidden=True,
                on_close=teardown,
            )
            session.handshake(timeout_s=_BOOT_TIMEOUT_S)
        except Exception:
            teardown()
            raise
        logger.info(
            "[agent-screen] %s: virtual display %s up on port %s",
            screen_id,
            display,
            port,
        )
        return session

    @staticmethod
    def _await_display(number: int, xvfb: subprocess.Popen[bytes]) -> None:
        socket_path = Path(_X_SOCKET_TEMPLATE.format(n=number))
        deadline = time.monotonic() + _BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            if xvfb.poll() is not None:
                raise AgentScreenUnavailable(
                    f"Xvfb exited with code {xvfb.returncode} before the display "
                    "was ready",
                )
            if socket_path.exists():
                return
            time.sleep(_BOOT_POLL_S)
        raise AgentScreenUnavailable(
            f"the virtual display :{number} did not come up within "
            f"{_BOOT_TIMEOUT_S:.0f}s",
        )

    @staticmethod
    def _await_port(port_file: Path, runner: subprocess.Popen[bytes]) -> int:
        """Wait for the runner to publish the port it actually bound.

        The runner binds port 0 and writes back what the OS gave it. Picking a
        port on the host instead would race any other process on the machine —
        a class of flake worth designing out rather than retrying.
        """
        deadline = time.monotonic() + _BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            if runner.poll() is not None:
                raise AgentScreenUnavailable(
                    f"the screen runner exited with code {runner.returncode} "
                    "before it was listening",
                )
            if port_file.exists():
                try:
                    return int(port_file.read_text(encoding="utf-8").strip())
                except (OSError, ValueError):
                    # The runner writes atomically, so this is a read racing
                    # the rename; the next poll sees the settled file.
                    logger.debug("port file not readable yet", exc_info=True)
            time.sleep(_BOOT_POLL_S)
        raise AgentScreenUnavailable(
            "the screen runner never reported a listening port",
        )


__all__ = ["XvfbProvider"]
