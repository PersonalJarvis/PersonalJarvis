"""Computer-Use uses an explicit agent-screen port without touching the host desktop."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("PIL", reason="pillow required for frame handling")

import jarvis.cu.engine as engine_mod
from jarvis.cu.capture import capture_stable_frame
from jarvis.cu.geometry import MonitorInfo
from jarvis.cu.target_guard import ForegroundTarget

MONITOR = MonitorInfo(left=0, top=0, width=160, height=90)


def _solid() -> tuple[tuple[int, int], bytes]:
    return ((160, 90), bytes((30, 30, 30)) * (160 * 90))


class FakeBrain:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)

    async def complete_text(self, *, system: str, user: str) -> str:
        del system, user
        return self.replies.pop(0)


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, tool, args, *, user_utterance, trace_id):
        del args, user_utterance, trace_id
        self.calls.append(tool["name"])
        return SimpleNamespace(success=True, output="ok", error=None)


class FakeRemotePort:
    is_real = False
    kind = "xvfb"

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.capture_calls = 0
        self.foreground_calls = 0

    def wayland_refusal(self) -> None:
        return None

    def select_all_keys(self) -> list[str]:
        return ["ctrl", "a"]

    def list_monitors(self) -> list[MonitorInfo]:
        return [MONITOR]

    def select_capture_target(
        self,
        policy: str,
        *,
        main_monitor: str,
        scope: str,
    ) -> MonitorInfo:
        del policy, main_monitor, scope
        return MONITOR

    def capture_stable_frame(
        self,
        monitor: MonitorInfo,
        *,
        max_dimension: int,
        blob_dir: Path | None,
        capture_guard: Any = None,
    ):
        del blob_dir
        self.capture_calls += 1
        return capture_stable_frame(
            monitor,
            grab=lambda _bbox: _solid(),
            sleep=lambda _seconds: None,
            max_dimension=max_dimension,
            blob_dir=self.tmp_path,
            capture_guard=capture_guard,
        )

    def read_foreground_target(self) -> ForegroundTarget:
        self.foreground_calls += 1
        return ForegroundTarget(
            window=None,
            rect=(0, 0, 160, 90),
            signature=("handle", 7, (0, 0, 160, 90)),
        )

    def foreground_matches_or_same_app(self, expected: tuple[Any, ...]) -> bool:
        return expected == ("handle", 7, (0, 0, 160, 90))

    def foreground_title(self) -> str:
        return "Agent Screen"

    def normalize_foreground_window(self) -> tuple[bool, str]:
        return (False, "isolated screen")

    def grab_visual_probe(self, *args, **kwargs):
        raise AssertionError("no action should need an effect probe in this test")

    def grab_region(self, *args, **kwargs):
        raise AssertionError("zoom-refine is not expected in this test")

    async def ui_snapshot(self, *, observation_guard=None):
        if observation_guard is not None:
            assert observation_guard()
        return [], "", None, []

    async def verify_typed_text(self, text: str) -> bool | None:
        del text
        return None

    async def verify_click_focus_point(
        self,
        x: int,
        y: int,
        *,
        capture_area: int | None = None,
    ) -> bool | None:
        del x, y, capture_area
        return None


def _tool_map(prefix: str = "") -> dict[str, Any]:
    names = (
        "click",
        "type_text",
        "hotkey",
        "scroll",
        "drag",
        "click_element",
        "open_app",
        "switch_window",
    )
    return {name: {"name": f"{prefix}{name}"} for name in names}


def _ctx(tmp_path: Path, *, screen_tools: dict[str, Any] | None = None):
    return SimpleNamespace(
        brain_manager=FakeBrain(['{"action":"fail","reason":"finished port check"}']),
        tool_executor=RecordingExecutor(),
        tools=_tool_map("desktop-"),
        screen_tools=_tool_map("screen-") if screen_tools is None else screen_tools,
        screen_port=FakeRemotePort(tmp_path),
        bus=None,
        step_budget=30,
        monitor="primary",
        main_monitor="primary",
        settle_scale=0.0,
        strict_verify=True,
        image_max_dimension=160,
        coordinate_space="auto",
    )


async def _run(ctx) -> list[Any]:
    task = SimpleNamespace(prompt="inspect isolated screen", env={}, timeout_s=60)
    return [chunk async for chunk in engine_mod.run_cu_loop(task, ctx, cancel_token=None)]


async def test_remote_port_perception_never_reads_host_screen(monkeypatch, tmp_path):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("host-screen primitive was touched")

    for name in (
        "list_monitors",
        "select_capture_target",
        "capture_stable_frame",
        "read_foreground_target",
        "foreground_matches_or_same_app",
        "grab_visual_probe",
    ):
        monkeypatch.setattr(engine_mod, name, forbidden)

    async def forbidden_async(*_args, **_kwargs):
        raise AssertionError("host-screen verification was touched")

    monkeypatch.setattr(engine_mod, "foreground_ui_snapshot", forbidden_async)
    monkeypatch.setattr(engine_mod, "verify_typed_text", forbidden_async)
    monkeypatch.setattr(engine_mod, "verify_click_focus_point", forbidden_async)

    ctx = _ctx(tmp_path)
    chunks = await _run(ctx)

    final = next(chunk for chunk in chunks if chunk.is_final)
    assert final.exit_code == 5
    assert ctx.screen_port.capture_calls == 1
    assert ctx.screen_port.foreground_calls >= 2


async def test_remote_port_without_bound_tools_fails_before_capture(tmp_path):
    ctx = _ctx(tmp_path, screen_tools={})

    chunks = await _run(ctx)

    final = next(chunk for chunk in chunks if chunk.is_final)
    assert final.exit_code == 8
    assert "isolated screen tools are not wired" in final.stderr
    assert ctx.screen_port.capture_calls == 0


async def test_remote_dispatch_uses_screen_tools_and_skips_host_tcc(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        "jarvis.cu.capture._require_macos_screen_recording_permission",
        lambda: (_ for _ in ()).throw(RuntimeError("host TCC must not run")),
    )

    ok, detail = await engine_mod._dispatch_tool(
        ctx,
        "click",
        {"x": 12, "y": 9},
        trace_id=None,
    )

    assert ok is True
    assert detail == "ok"
    assert ctx.tool_executor.calls == ["screen-click"]
