"""A runner attached to the USER's own display — the honesty provider.

This one is not isolated and never pretends to be: it starts the same runner
in the user's session and drives the same wire protocol against the real
screen. Two jobs justify it:

1. **Proof.** It exercises the entire chain — transport, framing, capture,
   coordinate handling, verified input — on any machine, including one where
   no isolated provider can run. A green self-test here means the only thing
   an isolated provider still has to get right is booting its session.
2. **Attended fallback.** Where a host genuinely cannot offer isolation
   (macOS today, a Windows Home edition, a Linux box without Xvfb), a
   maintainer may still want desktop automation. Making that an explicitly
   named, explicitly non-hidden screen is more honest than silently routing a
   subagent onto the physical pointer.

``auto`` never selects it, and :attr:`ScreenSession.isolated` is ``False``, so
a subagent cannot reach it by accident.
"""

from __future__ import annotations

import logging
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

_BOOT_TIMEOUT_S = 30.0
_BOOT_POLL_S = 0.2


class AttachedProvider(ScreenProvider):
    """Runs the in-session runner against the display this process already has."""

    kind = "attached"

    def probe(self) -> ProbeResult:
        try:
            from jarvis.platform.probes import display_present, is_wayland  # noqa: PLC0415

            if sys.platform not in ("win32", "darwin"):
                if is_wayland():
                    return ProbeResult(
                        kind=self.kind,
                        available=False,
                        reason=(
                            "Wayland blocks global capture and synthetic input "
                            "by design"
                        ),
                        remedy="Log into an X11 session instead.",
                    )
                if not display_present():
                    return ProbeResult(
                        kind=self.kind,
                        available=False,
                        reason="this host has no display session",
                    )
        except Exception as exc:  # noqa: BLE001 — probe failure is a "no"
            return ProbeResult(
                kind=self.kind,
                available=False,
                reason=f"display capability probe failed: {exc}",
            )
        return ProbeResult(kind=self.kind, available=True)

    @property
    def max_screens(self) -> int:
        """One: there is only one physical pointer to attach to."""
        return 1

    def start(self, spec: ScreenSpec) -> ScreenSession:
        probe = self.probe()
        if not probe.available:
            raise AgentScreenUnavailable(f"{probe.reason}. {probe.remedy}".strip())

        screen_id = f"attached-{uuid.uuid4().hex[:8]}"
        root = screen_dir(screen_id)
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
                "attached",
                "--token-file",
                str(token_file),
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--port-file",
                str(port_file),
            ],
        )
        try:
            port = _await_port(port_file, runner)
            session = RemoteScreenSession(
                screen_id=screen_id,
                kind="attached",
                transport=HttpTransport("127.0.0.1", port, token),
                owner=spec.owner,
                purpose=spec.purpose,
                # The user's own screen: never claim it is hidden.
                hidden=False,
                on_close=lambda: terminate_quietly(runner),
            )
            session.handshake(timeout_s=_BOOT_TIMEOUT_S)
        except Exception:
            terminate_quietly(runner)
            raise
        logger.info("[agent-screen] %s: attached runner on port %s", screen_id, port)
        return session


def _await_port(port_file: Path, runner: subprocess.Popen[bytes]) -> int:
    deadline = time.monotonic() + _BOOT_TIMEOUT_S
    while time.monotonic() < deadline:
        if runner.poll() is not None:
            raise AgentScreenUnavailable(
                f"the screen runner exited with code {runner.returncode} before "
                "it was listening",
            )
        if port_file.exists():
            try:
                return int(port_file.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                logger.debug("port file not readable yet", exc_info=True)
        time.sleep(_BOOT_POLL_S)
    raise AgentScreenUnavailable("the screen runner never reported a listening port")


__all__ = ["AttachedProvider"]
