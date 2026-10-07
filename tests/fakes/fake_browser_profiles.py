"""Browser profile fixtures that never start a browser or model."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


class ProfileRoster:
    def __init__(self):
        self.rows = [
            SimpleNamespace(
                agent_id=aid, name=aid.title(), browser_mode="own", browser_allowed_domains=[]
            )
            for aid in ("lead", "scout")
        ]

    async def list(self):
        return self.rows

    async def resolve(self, aid):
        return next((row for row in self.rows if row.agent_id == aid), None)


async def already_started():
    return None


class LoginLive:
    def __init__(self, *, login_available=False, login_mode=False):
        self.sessions = {}
        self.calls = []
        self.login_available = login_available
        self.login_mode = login_mode
        self.fail_navigation = False
        self.fail_return = False
        self.manual_cycles = 0

    async def ensure(self, agent, **kwargs):
        self.calls.append(("ensure", agent.agent_id, kwargs))
        return self.sessions.setdefault(
            agent.agent_id,
            SimpleNamespace(
                closed=False,
                control_owner=None,
                manual_epoch="",
                generation="old-surface",
                state={
                    "login_available": self.login_available,
                    "login_mode": self.login_mode,
                    "manual": self.login_mode,
                    "generation": "old-surface",
                },
            ),
        )

    async def control(self, session, owner, op, args, *, expected_manual=None):
        self.calls.append((op, args))
        if op == "takeover":
            if expected_manual is not None and (
                (session.manual_epoch, session.generation) != expected_manual
                or not (session.state.get("manual") or session.state.get("login_mode"))
            ):
                raise ValueError("This login session has already ended")
            if session.control_owner not in {None, owner}:
                raise ValueError("Browser is controlled by another viewer")
            if self.fail_return and not args["enabled"]:
                raise RuntimeError("Chrome is still closing")
            session.control_owner = owner if args["enabled"] else None
            session.state["manual"] = args["enabled"]
            if "login" in args:
                session.state["login_mode"] = args["login"]
            if session.state["manual"] or session.state["login_mode"]:
                if not session.manual_epoch:
                    self.manual_cycles += 1
                    session.manual_epoch = f"manual-{self.manual_cycles}"
            else:
                session.manual_epoch = ""
            session.generation = "login-surface"
            return {
                "generation": "login-surface",
                "manual": session.state["manual"],
                "login_mode": session.state["login_mode"],
            }
        if op == "navigate":
            if self.fail_navigation:
                raise RuntimeError("Navigation unavailable")
            if self.login_available and args.get("generation") != "login-surface":
                raise ValueError("The browser changed; wait for its new image")
        return {}

    def release_when_idle(self, session):
        self.calls.append(("idle", session.control_owner))


class ProfileSession:
    def __init__(self, agent_id):
        self.agent_id = agent_id
        self.closed = False
        self.run_lock = asyncio.Lock()
        self.control_lock = asyncio.Lock()
        self.control_owner = None
        self.manual_epoch = ""
        self.generation = "surface-0"
        self.subscribers = set()
        self.readers = []
        self.tasks = set()
        self.attention = {}
        self.active_trace = ""
        self.profile_binding = self.profile_lease = None
        self.window_upgrade_pending = False
        self.state = {"full_window": True}
        self.commands = []

    async def close(self):
        self.closed = True
        if self.profile_lease is not None:
            self.profile_lease.release()
            self.profile_lease = None

    async def command(self, op, args=None, **kwargs):
        self.commands.append((op, args))
        return {}

    def publish(self, event):
        self.commands.append(("event", event))


class LoginControlSession(ProfileSession):
    """A browser worker response with real parent-side control bookkeeping."""

    def __init__(self, agent_id, *, capable):
        super().__init__(agent_id)
        self.profile_binding = SimpleNamespace(kind="managed" if capable else "chrome")
        self.state.update(manual=False, login_mode=False, login_available=capable)
        self.worker_login_mode = False
        self.surface_number = 0
        self.fail_return = False

    async def command(self, op, args=None, **kwargs):
        self.commands.append((op, args))
        if op != "takeover":
            return {}
        enabled = bool(args["enabled"])
        if self.fail_return and not enabled:
            raise RuntimeError("Chrome is still closing")
        if self.state["login_available"] and args.get("login") is enabled:
            if enabled != self.worker_login_mode:
                self.surface_number += 1
            self.worker_login_mode = enabled
        return {
            "manual": enabled,
            "login_mode": self.worker_login_mode,
            "generation": f"surface-{self.surface_number}",
        }

    async def ensure(self, agent, **kwargs):
        return self
