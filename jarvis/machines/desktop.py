"""Explicit remote leases over the existing screen providers."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from typing import Any
from uuid import uuid4

from .models import MachineCommand


class ConnectorDesktops:
    def __init__(self, mode: str = "none", *, managers: dict[str, Any] | None = None) -> None:
        self.mode = mode
        self.managers = managers or {}
        self.leases: dict[str, tuple[str, Any, float]] = {}
        self.observations: dict[str, tuple[str, str, float]] = {}
        self.lock = asyncio.Lock()
        self.execution_guard: Any = lambda: None
        self.paused = False

    def manager(self, mode: str) -> Any:
        if self.mode not in {mode, "both"}:
            raise PermissionError("Enable this desktop mode locally in the connector first")
        if mode not in self.managers:
            from jarvis.agent_screen.manager import AgentScreenManager, ScreenSettings

            self.managers[mode] = AgentScreenManager(
                settings=ScreenSettings(
                    provider="attached" if mode == "attached" else "auto",
                    max_screens=1,
                )
            )
        return self.managers[mode]

    def probe(self) -> tuple[bool, bool, str]:
        available: dict[str, bool] = {}
        reasons = []
        for mode in ("own", "attached"):
            if self.mode not in {mode, "both"}:
                available[mode] = False
                continue
            provider, reason = self.manager(mode).select_provider()
            available[mode] = provider is not None
            if reason:
                reasons.append(reason)
        return any(available.values()), available.get("own", False), "; ".join(reasons)

    async def execute(self, command: MachineCommand) -> dict[str, Any]:
        mode = command.grant.desktop
        if mode == "none":
            raise PermissionError("Desktop access is not granted")
        manager = self.manager(mode)
        async with self.lock:
            if self.paused:
                raise PermissionError(
                    "Desktop access was stopped; explicitly enable its profile again"
                )
            await self._expire_locked()
            held = self.leases.get(mode)
            if held and held[0] != command.agent_id:
                raise PermissionError(f"Desktop is busy with agent {held[0]}")
            if held:
                lease = held[1]
            else:
                start = asyncio.create_task(
                    manager.acquire(
                        command.agent_id,
                        purpose="Remote agent desktop",
                        require_isolated=mode == "own",
                    )
                )
                try:
                    lease = await asyncio.shield(start)
                except asyncio.CancelledError:
                    lease = await start
                    await manager.release(lease)
                    raise
            try:
                self.execution_guard()
            except PermissionError:
                if not held:
                    await manager.release(lease)
                raise
            self.leases[mode] = (command.agent_id, lease, time.monotonic() + 30)
            verb = str(command.args.get("verb", "observe"))
            if verb == "release":
                await manager.release(lease)
                self.leases.pop(mode, None)
                self.observations.pop(mode, None)
                return {"released": True}
            session = lease.session
            if verb == "observe":
                return await asyncio.to_thread(self._observe, mode, session)
            if verb not in {
                "click",
                "type_text",
                "hotkey",
                "scroll",
                "drag",
                "open_app",
                "switch_window",
            }:
                raise ValueError("Unsupported desktop action")
            if verb not in {"open_app", "switch_window"}:
                observation = self.observations.get(mode)
                if (
                    not observation
                    or observation[0] != command.args.get("observation_id")
                    or time.monotonic() - observation[2] > 10
                ):
                    raise PermissionError("Capture a fresh desktop observation before acting")
                current = await asyncio.to_thread(session.foreground)
                if not current.available or json.dumps(current.signature()) != observation[1]:
                    raise PermissionError("Foreground changed since capture; observe again")
            outcome = await asyncio.to_thread(session.act, verb, command.args.get("params", {}))
            self.observations.pop(mode, None)
            if not outcome.ok:
                raise RuntimeError(outcome.detail)
            return {"detail": outcome.detail, "screen_id": session.screen_id}

    def _observe(self, mode: str, session: Any) -> dict[str, Any]:
        from PIL import Image

        before = session.foreground()
        left, top, width, height = session.geometry()
        frame = session.grab({"left": left, "top": top, "width": width, "height": height}, rgb=True)
        after = session.foreground()
        if not before.available or before.signature() != after.signature():
            raise PermissionError("Desktop focus is unavailable or changed during capture")
        picture = Image.frombytes("RGB", frame.size, frame.data)
        picture.thumbnail((1280, 800))
        buffer = io.BytesIO()
        picture.save(buffer, format="PNG")
        observation_id = uuid4().hex
        self.observations[mode] = (observation_id, json.dumps(after.signature()), time.monotonic())
        return {
            "screen_id": session.screen_id,
            "observation_id": observation_id,
            "screen": {"left": left, "top": top, "width": width, "height": height},
            "image_width": picture.width,
            "image_height": picture.height,
            "foreground": after.title,
            "image_png": base64.b64encode(buffer.getvalue()).decode(),
        }

    async def _expire_locked(self) -> None:
        for mode, (_owner, lease, deadline) in list(self.leases.items()):
            if time.monotonic() >= deadline:
                await self.managers[mode].release(lease)
                self.leases.pop(mode, None)
                self.observations.pop(mode, None)

    async def expire(self) -> None:
        async with self.lock:
            await self._expire_locked()

    async def close(self, *, pause: bool = False) -> None:
        async with self.lock:
            if pause:
                self.paused = True
            for manager in self.managers.values():
                await manager.shutdown()
            self.leases.clear()
            self.observations.clear()
