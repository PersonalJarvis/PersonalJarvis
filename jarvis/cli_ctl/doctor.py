"""Diagnose WHY a Jarvis server is unreachable and say something useful.

One static "start the app" line is both boring and frequently WRONG: the app
may be running and merely still booting, it may have crashed leaving a stale
session file behind (starting it again IS right there), or the target may be
a remote ``--url`` that no local start would ever fix. This module inspects
the actual local state at error time and composes a cause-specific message,
with a small pool of phrasings per cause so repeated failures don't read
like a broken record.

Prose only — scripts and agents must key on exit codes / ``--json``, never on
this text.
"""
from __future__ import annotations

import json
import os
import random

from jarvis.cli_ctl import discovery, paths
from jarvis.cli_ctl.config import DEFAULT_BASE_URL


def _explicit_target() -> str | None:
    """A base URL the user pinned via env or ``auth login`` config, if any."""
    env = os.environ.get("JARVISCTL_BASE_URL")
    if env:
        return env
    p = paths.config_file()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        url = data.get("base_url")
        if isinstance(url, str) and url:
            return url
    return None


def _pick(variants: tuple[str, ...]) -> str:
    # Prose variety only — nothing security-relevant rides on this choice.
    return random.choice(variants)  # noqa: S311


def unreachable_message(base_url: str | None) -> str:
    """Cause-specific, non-repetitive explanation for an unreachable server."""
    base_url = base_url or DEFAULT_BASE_URL
    session = discovery.discover(check_pid=False)
    explicit = _explicit_target()

    # 1) The user pinned a custom/remote target — a local app start is
    #    irrelevant; the host/tunnel/port is what needs checking.
    if explicit and explicit.rstrip("/") == base_url.rstrip("/") and (
        session is None or explicit.rstrip("/") != session.base_url.rstrip("/")
    ):
        return _pick((
            f"No answer from {base_url}. That target comes from --url/env/"
            "config, so check that machine, tunnel, or port — starting the "
            "local desktop app won't change anything here. `jarvis auth "
            "login` re-points the CLI if the address moved.",
            f"The configured target {base_url} stayed silent. It's an "
            "explicit remote/custom address: verify the host is up and the "
            "port is reachable; a local app start won't help this one.",
        ))

    if session is not None and base_url.rstrip("/") == session.base_url.rstrip("/"):
        pid = session.pid
        port = session.base_url.rsplit(":", 1)[-1]
        if pid is not None and discovery._pid_alive(pid):
            # 2) The app process EXISTS but the port isn't answering —
            #    usually a boot still in progress, sometimes a wedged server.
            #    "Start the app" would be plain wrong here.
            return _pick((
                f"Jarvis is actually running (pid {pid}) — port {port} just "
                "isn't answering yet. A boot takes a few seconds; retry "
                "shortly. If it stays silent, restart it from the tray or "
                "via POST /api/settings/restart-app.",
                f"Found a live Jarvis process (pid {pid}) that isn't serving "
                f"on port {port} yet. Most likely it's still starting up — "
                "give it a moment and try again; a tray restart unsticks a "
                "wedged boot.",
                f"The app process is alive (pid {pid}) but not reachable on "
                f"port {port}. Wait a few seconds and retry before anything "
                "else; if it never answers, trigger a restart "
                "(tray icon or POST /api/settings/restart-app).",
            ))
        # 3) Stale session file: the app died without cleaning up.
        return _pick((
            f"A previous Jarvis exited uncleanly — its session file is still "
            f"there but the process (pid {pid}) is gone. Launch it again "
            "(run.bat) and retry.",
            f"Looks like Jarvis crashed earlier: stale session file, dead "
            f"pid {pid}. Start it fresh with run.bat, then rerun this "
            "command.",
        ))

    # 4) Nothing on this machine at all.
    return _pick((
        f"Nothing is listening at {base_url} and no local Jarvis session "
        "was found. Launch the desktop app (run.bat) — or run.bat --headless "
        "on a server — then retry; for a remote instance pass --url/--key.",
        f"No running Jarvis found on this machine ({base_url} is silent). "
        "Start it with run.bat (or run.bat --headless), or point the CLI at "
        "a remote instance via --url/--key.",
    ))
