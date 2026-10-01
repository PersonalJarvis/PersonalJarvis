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
    def __init__(self):
        self.sessions = {}
        self.calls = []

    async def ensure(self, agent, **kwargs):
        self.calls.append(("ensure", agent.agent_id, kwargs))
        return self.sessions.setdefault(
            agent.agent_id, SimpleNamespace(closed=False, control_owner=None)
        )

    async def control(self, session, owner, op, args):
        self.calls.append((op, args))
        if op == "takeover":
            session.control_owner = owner if args["enabled"] else None

    def release_when_idle(self, session):
        self.calls.append(("idle", session.control_owner))


class ProfileSession:
    def __init__(self, agent_id):
        self.agent_id = agent_id
        self.closed = False
        self.run_lock = asyncio.Lock()
        self.control_lock = asyncio.Lock()
        self.control_owner = None
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
