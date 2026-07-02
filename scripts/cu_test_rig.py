"""CU-RIG — deterministic Computer-Use accuracy / duplicate-action / latency rig.

A cross-platform (tkinter, stdlib) target window with KNOWN geometry records
every real mouse click and keystroke it receives. The rig then drives the
actual input pipeline against those targets and measures, deterministically:

* **hit accuracy** — pixel distance between each received click and the
  intended target center,
* **duplicate-action rate** — clicks/typed text received more than once for
  a single intended action (the double-URL bug class),
* **misdirected typing** — text typed while the intended field never had
  focus (the "click missed, typed anyway" bug class),
* **latency** — wall-clock per perceive->act->verify step.

Modes (all runnable on Windows, macOS and Linux/X11):

  python scripts/cu_test_rig.py --mode raw
      Drive CoordinateMapper + platform actuator directly (no LLM, no
      engine): proves the v2 coordinate pipeline end-to-end on THIS machine.

  python scripts/cu_test_rig.py --mode engine --engine v2
  python scripts/cu_test_rig.py --mode engine --engine stable|current
      Run the REAL engine (v2 or a legacy one) with a SCRIPTED brain that
      emits the targets' coordinates — model variance is excluded, so two
      runs compare the ENGINES, not the model. Includes the duplicate-type
      and misaimed-click provocations.

This script controls the real mouse/keyboard while it runs — keep hands off.
Results are printed as a table and written as JSON next to the script
(cu_rig_results_<mode>_<engine>.json) for before/after comparison.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The rig window must report PHYSICAL pixels on Windows so its geometry lives
# in the same space as capture + input (per-monitor DPI). Must run before Tk.
from jarvis.core.win32_dpi import ensure_dpi_awareness  # noqa: E402

ensure_dpi_awareness()

WINDOW_W, WINDOW_H = 820, 520
WINDOW_X, WINDOW_Y = 120, 120
TARGETS: dict[str, tuple[int, int, int, int]] = {
    # name -> (x1, y1, x2, y2) rects on the canvas, spread across the window.
    "T1": (40, 60, 190, 120),
    "T2": (620, 40, 770, 100),
    "T3": (70, 380, 220, 440),
    "T4": (600, 360, 750, 420),
}
ENTRY_RECT = (260, 220, 560, 250)  # the text field's canvas-space rect
TYPE_TEXT = "example.com"
HIT_TOLERANCE_PX = 5


@dataclass
class RigLog:
    """Everything the target window actually received, with timestamps."""

    clicks: list[dict[str, Any]] = field(default_factory=list)
    keys: list[dict[str, Any]] = field(default_factory=list)
    entry_focus_events: int = 0

    def clicks_on(self, name: str) -> list[dict[str, Any]]:
        return [c for c in self.clicks if c["target"] == name]


class RigWindow:
    """tkinter canvas with known targets; runs its mainloop on a thread."""

    def __init__(self) -> None:
        self.log = RigLog()
        self.ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._root: Any = None
        self._entry: Any = None
        self._canvas: Any = None

    def start(self) -> None:
        self._thread.start()
        if not self.ready.wait(timeout=10):
            raise RuntimeError("rig window did not appear within 10s")
        time.sleep(0.5)  # let the WM settle the final position

    def _run(self) -> None:
        import tkinter as tk

        root = tk.Tk()
        self._root = root
        root.title("CU-RIG target window")
        root.geometry(f"{WINDOW_W}x{WINDOW_H}+{WINDOW_X}+{WINDOW_Y}")
        root.resizable(False, False)
        canvas = tk.Canvas(
            root, width=WINDOW_W, height=WINDOW_H,
            bg="#101418", highlightthickness=0,
        )
        canvas.pack(fill="both", expand=True)
        self._canvas = canvas
        self._items: dict[str, int] = {}
        for name, (x1, y1, x2, y2) in TARGETS.items():
            rect = canvas.create_rectangle(
                x1, y1, x2, y2, fill="#2b6cb0", outline="#e7c46e", width=2,
            )
            canvas.create_text(
                (x1 + x2) // 2, (y1 + y2) // 2, text=name,
                fill="white", font=("TkDefaultFont", 14, "bold"),
            )
            self._items[name] = rect
        ex1, ey1, ex2, ey2 = ENTRY_RECT
        entry = tk.Entry(root, font=("TkDefaultFont", 12))
        entry.place(x=ex1, y=ey1, width=ex2 - ex1, height=ey2 - ey1)
        self._entry = entry

        def on_click(event: Any) -> None:
            target = self._target_at(event.x, event.y)
            self.log.clicks.append({
                "t": time.monotonic(),
                "x": event.x, "y": event.y,
                "target": target,
            })
            if target:
                # Visible reaction: flip the target's fill so the click has a
                # guaranteed local pixel effect (feeds the engines' verify).
                current = canvas.itemcget(self._items[target], "fill")
                canvas.itemconfig(
                    self._items[target],
                    fill="#38a169" if current == "#2b6cb0" else "#2b6cb0",
                )

        def on_key(event: Any) -> None:
            self.log.keys.append({
                "t": time.monotonic(),
                "char": event.char,
                "entry_text": entry.get(),
                "entry_focused": root.focus_get() is entry,
            })

        def on_entry_focus(_event: Any) -> None:
            self.log.entry_focus_events += 1

        canvas.bind("<Button-1>", on_click)
        root.bind("<KeyRelease>", on_key)
        entry.bind("<FocusIn>", on_entry_focus)
        root.attributes("-topmost", True)
        root.update()
        self._tk_id = root.winfo_id()
        self.ready.set()
        root.mainloop()

    def activate(self) -> None:
        """Bring the rig to the foreground from ITS OWN process (window API,
        not injected input). Load-bearing on Windows: if an ELEVATED window
        (e.g. an admin-run app) holds the foreground, UIPI silently discards
        every input this non-elevated process injects — clicks "succeed" but
        never arrive. Self-activation sidesteps that."""
        def _do() -> None:
            try:
                self._root.attributes("-topmost", True)
                self._root.lift()
                self._root.focus_force()
            except Exception:  # noqa: BLE001
                pass

        try:
            self._root.after(0, _do)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)

    def is_foreground(self) -> bool:
        """Is the rig window the FOREGROUND window? (Windows: real check;
        elsewhere: trust the activation click.) Guards the typing scenarios —
        the rig must NEVER type into a foreign window."""
        if sys.platform != "win32":
            return True
        try:
            import ctypes  # noqa: PLC0415

            user32 = ctypes.windll.user32
            GA_ROOT = 2
            ours = user32.GetAncestor(int(self._tk_id), GA_ROOT)
            return bool(ours) and user32.GetForegroundWindow() == ours
        except Exception:  # noqa: BLE001
            return True

    def _target_at(self, cx: int, cy: int) -> str | None:
        for name, (x1, y1, x2, y2) in TARGETS.items():
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                return name
        return None

    # -- geometry ---------------------------------------------------------

    def canvas_origin_on_screen(self) -> tuple[int, int]:
        return (self._canvas.winfo_rootx(), self._canvas.winfo_rooty())

    def target_center_screen(self, name: str) -> tuple[int, int]:
        x1, y1, x2, y2 = TARGETS[name]
        ox, oy = self.canvas_origin_on_screen()
        return (ox + (x1 + x2) // 2, oy + (y1 + y2) // 2)

    def entry_center_screen(self) -> tuple[int, int]:
        x1, y1, x2, y2 = ENTRY_RECT
        ox, oy = self.canvas_origin_on_screen()
        return (ox + (x1 + x2) // 2, oy + (y1 + y2) // 2)

    def entry_text(self) -> str:
        return self._entry.get()

    def clear_entry(self) -> None:
        self._entry.delete(0, "end")

    def close(self) -> None:
        try:
            self._root.after(0, self._root.destroy)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Shared measurement helpers
# ---------------------------------------------------------------------------

def _distance(a: tuple[int, int], b: tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _capture_monitor(rig: RigWindow):
    """The monitor CONTAINING the rig window (by containment, not focus —
    the console that launched the rig may hold focus on another screen)."""
    from jarvis.cu.geometry import list_monitors

    ox, oy = rig.canvas_origin_on_screen()
    cx, cy = ox + WINDOW_W // 2, oy + WINDOW_H // 2
    monitors = list_monitors()
    for m in monitors:
        if m.contains(cx, cy):
            return m
    if monitors:
        return next((m for m in monitors if m.is_primary), monitors[0])
    raise RuntimeError("no display detected")


def _virtual_rect():
    """The whole virtual desktop as a rect-like object. The ENGINE scenarios
    run with [computer_use].monitor='all' so a human focusing another window
    mid-measurement cannot flip the captured monitor under the mission
    (observed live: foreground-follow made runs land on the wrong screen)."""
    from jarvis.cu.geometry import list_monitors, virtual_screen_bounds

    left, top, width, height = virtual_screen_bounds(list_monitors())
    if width <= 0:
        raise RuntimeError("no display detected")
    return SimpleNamespace(left=left, top=top, width=width, height=height)


def _activate_rig(rig: RigWindow) -> None:
    """Bring the rig window to the FOREGROUND: first via its own window API
    (immune to UIPI — an elevated foreground window silently discards
    injected input from a non-elevated process), then a confirmation click
    on dead canvas space that also PROVES injected input arrives."""
    from jarvis.cu.actuate import get_actuator

    rig.activate()
    ox, oy = rig.canvas_origin_on_screen()
    before = len(rig.log.clicks)
    get_actuator().click(ox + 410, oy + 480)  # dead strip below the targets
    time.sleep(0.4)
    if len(rig.log.clicks) == before:
        print(
            "  !! injected input did not arrive — an elevated window may hold "
            "the foreground (UIPI). Retrying activation once.",
        )
        rig.activate()
        get_actuator().click(ox + 410, oy + 480)
        time.sleep(0.4)
        if len(rig.log.clicks) == before:
            raise RuntimeError(
                "environment blocks injected input (UIPI/elevated foreground) "
                "— run the rig from an elevated shell or close the elevated "
                "foreground app",
            )
    rig.log.clicks.clear()
    rig.log.keys.clear()


def _norm_for(point: tuple[int, int], monitor) -> tuple[int, int]:
    nx = round((point[0] - monitor.left) / monitor.width * 1000)
    ny = round((point[1] - monitor.top) / monitor.height * 1000)
    return (nx, ny)


def _summarize(
    label: str,
    intended: list[tuple[str, tuple[int, int]]],
    rig: RigWindow,
    *,
    step_times: list[float],
    typed_expected: str | None = None,
    provoked_duplicates: int = 0,
) -> dict[str, Any]:
    """Compare the rig's received events against the intended actions."""
    distances: list[float] = []
    hits = 0
    duplicates = 0
    for name, center in intended:
        # Received clicks are canvas-relative; convert intended to canvas space.
        ox, oy = rig.canvas_origin_on_screen()
        expected = (center[0] - ox, center[1] - oy)
        received = rig.log.clicks_on(name)
        if received:
            first = received[0]
            distances.append(_distance((first["x"], first["y"]), expected))
            hits += 1
            duplicates += max(0, len(received) - 1)
    result: dict[str, Any] = {
        "scenario": label,
        "intended_clicks": len(intended),
        "received_first_hits": hits,
        "hit_rate": round(hits / len(intended), 3) if intended else None,
        "mean_distance_px": round(statistics.mean(distances), 2) if distances else None,
        "max_distance_px": round(max(distances), 2) if distances else None,
        "duplicate_clicks": duplicates,
        "step_latency_s_p50": round(statistics.median(step_times), 3) if step_times else None,
        "step_latency_s_max": round(max(step_times), 3) if step_times else None,
        "provoked_duplicates_executed": provoked_duplicates,
    }
    if typed_expected is not None:
        got = rig.entry_text()
        result["typed_expected"] = typed_expected
        result["typed_received"] = got
        result["typed_exactly_once"] = got == typed_expected
        result["typed_duplicated"] = typed_expected * 2 in got or (
            got.count(typed_expected) > 1
        )
    return result


# ---------------------------------------------------------------------------
# Mode: raw (mapper + actuator, no engine, no brain)
# ---------------------------------------------------------------------------

def run_raw(rig: RigWindow) -> list[dict[str, Any]]:
    """Drive the v2 coordinate pipeline directly: capture geometry -> mapper
    -> normalized coords -> actuator click. Proves mapping + actuation."""
    from jarvis.cu.actuate import get_actuator, verified_move
    from jarvis.cu.geometry import CoordinateMapper

    actuator = get_actuator()
    _activate_rig(rig)
    monitor = _capture_monitor(rig)
    # A mapper for a synthetic model image (downscaled 1366px longest side),
    # exactly like a v2 frame would carry.
    scale = 1366 / max(monitor.width, monitor.height)
    mapper = CoordinateMapper(
        capture_left=monitor.left, capture_top=monitor.top,
        capture_width=monitor.width, capture_height=monitor.height,
        image_width=max(1, round(monitor.width * scale)),
        image_height=max(1, round(monitor.height * scale)),
    )
    intended: list[tuple[str, tuple[int, int]]] = []
    step_times: list[float] = []
    interference_retries = 0
    for name in TARGETS:
        center = rig.target_center_screen(name)
        nx, ny = _norm_for(center, monitor)
        t0 = time.monotonic()
        sx, sy = mapper.normalized_to_screen(nx, ny)
        for attempt in (1, 2):
            move = verified_move(actuator, sx, sy)
            if not move.ok:
                print(f"  !! landed-verification for {name}: {move.detail}")
            before = len(rig.log.clicks_on(name))
            actuator.click(sx, sy)
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                if len(rig.log.clicks_on(name)) > before:
                    break
                time.sleep(0.05)
            if len(rig.log.clicks_on(name)) > before:
                break
            # Nothing arrived: external interference (a human hand on the
            # mouse / a foreign foreground window). Re-activate and retry ONCE.
            interference_retries += 1
            print(f"  !! no click event arrived for {name} — retrying once")
            _activate_rig_click_only(rig, actuator)
        step_times.append(time.monotonic() - t0)
        intended.append((name, center))
        time.sleep(0.2)

    # Typing: click the entry, type, verify content. HARD GUARD: never type
    # while the rig is not the foreground window (keystrokes would land in a
    # foreign app).
    typed_expected: str | None = TYPE_TEXT
    ex, ey = rig.entry_center_screen()
    actuator.click(ex, ey)
    time.sleep(0.3)
    if not rig.is_foreground():
        _activate_rig_click_only(rig, actuator)
        actuator.click(ex, ey)
        time.sleep(0.3)
    if rig.is_foreground():
        actuator.type_text(TYPE_TEXT, delay_s=0.01)
        time.sleep(0.4)
    else:
        print("  !! rig lost foreground — typing scenario SKIPPED for safety")
        typed_expected = None

    summary = _summarize(
        "raw-pipeline", intended, rig,
        step_times=step_times, typed_expected=typed_expected,
    )
    summary["actuator"] = actuator.name
    summary["monitor"] = f"{monitor.width}x{monitor.height}@{monitor.left},{monitor.top}"
    summary["interference_retries"] = interference_retries
    return [summary]


def _activate_rig_click_only(rig: RigWindow, actuator: Any) -> None:
    ox, oy = rig.canvas_origin_on_screen()
    actuator.click(ox + 410, oy + 480)
    time.sleep(0.3)


# ---------------------------------------------------------------------------
# Mode: engine (real engine, scripted brain)
# ---------------------------------------------------------------------------

class ScriptedBrain:
    """Rule-based ``complete_text`` shim: pops the next scripted ACTION for
    executor calls, and answers verifier/refine prompts deterministically —
    so a rig run measures the ENGINE, not a live model."""

    def __init__(self, actions: list[str]) -> None:
        self.actions = list(actions)
        self.calls = 0

    async def complete_text(self, *, system: str, user: str) -> str:
        self.calls += 1
        if '"done": true|false' in system or '"done":true|false' in system:
            # Completion/fail judge: confirm once the script is exhausted.
            if not self.actions:
                return '{"done": true, "proof": "all rig targets were activated"}'
            return '{"done": false, "proof": "targets still pending"}'
        if '"found": true' in system:
            # Legacy zoom-refine: never move the point (keep coarse estimate).
            return '{"found": false}'
        if self.actions:
            return self.actions.pop(0)
        return '{"action": "done", "reason": "script exhausted"}'


class PassthroughExecutor:
    """Direct tool dispatch for the rig (no risk-tier gate — measurement only)."""

    def __init__(self) -> None:
        from uuid import uuid4

        self._ctx = SimpleNamespace(
            trace_id=uuid4(), user_utterance="cu-rig", config={},
            memory_read=None, approved_by="auto",
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, tool: Any, args: dict[str, Any], **_kw: Any) -> Any:
        self.calls.append((tool.name, dict(args)))
        return await tool.execute(args, self._ctx)


def _real_tools() -> dict[str, Any]:
    from jarvis.plugins.tool.click import ClickTool
    from jarvis.plugins.tool.hotkey import HotkeyTool
    from jarvis.plugins.tool.scroll import ScrollTool
    from jarvis.plugins.tool.type_text import TypeTextTool

    tools: dict[str, Any] = {
        "click": ClickTool(),
        "type_text": TypeTextTool(),
        "hotkey": HotkeyTool(),
        "scroll": ScrollTool(),
    }
    try:
        from jarvis.plugins.tool.drag import DragTool

        tools["drag"] = DragTool()
    except Exception:  # noqa: BLE001
        pass
    return tools


class LegacyVisionShim:
    """Minimal ``vision_engine`` for the legacy engines: one mss screenshot
    per observe(), saved to disk, with the capture monitor's geometry."""

    async def observe(self, **_kw: Any) -> Any:
        import hashlib
        import time as _time
        from uuid import uuid4

        from PIL import Image

        from jarvis.cu.capture import mss_grab, select_monitor

        monitor = await asyncio.to_thread(select_monitor, "all")
        (w, h), rgb = await asyncio.to_thread(mss_grab, monitor.bbox)
        img = Image.frombytes("RGB", (w, h), rgb)
        blob_dir = REPO_ROOT / "data" / "flight_recorder" / "blobs"
        blob_dir.mkdir(parents=True, exist_ok=True)
        import io

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        data = buf.getvalue()
        sha = hashlib.sha256(data).hexdigest()
        path = blob_dir / f"{sha}.jpg"
        if not path.exists():
            path.write_bytes(data)
        from jarvis.core.protocols import Observation

        return Observation(
            trace_id=uuid4(),
            timestamp_ns=_time.time_ns(),
            screenshot_path=str(path),
            screenshot_hash=sha,
            nodes=(),
            window_title="CU-RIG target window",
            active_pid=0,
            source="screenshot_only",
            pruning_stats={"nodes_before": 0, "nodes_after": 0, "depth_used": 0},
            monitor_geom=(monitor.left, monitor.top, monitor.width, monitor.height),
        )


def _ensure_measurable(rig: RigWindow) -> None:
    """Re-check the environment before EVERY mission: the rig must be the
    foreground window (an elevated foreground app makes UIPI discard our
    injected input mid-suite — observed live). Raises instead of measuring
    garbage."""
    if rig.is_foreground():
        return
    rig.activate()
    if not rig.is_foreground():
        raise RuntimeError(
            "rig lost the foreground (elevated window / user interaction) — "
            "measurement environment is not clean",
        )


async def _run_engine_mission(
    engine: str, brain: ScriptedBrain, goal: str,
) -> tuple[list[str], float]:
    """Run one mission through the selected engine; returns (chunks, seconds)."""
    tools = _real_tools()
    executor = PassthroughExecutor()
    if engine == "v2":
        ctx: Any = SimpleNamespace(
            brain_manager=brain, tool_executor=executor, tools=tools,
            bus=None, step_budget=30, monitor="all",
            main_monitor="primary", settle_scale=1.0, strict_verify=True,
            image_max_dimension=1366, coordinate_space="auto",
        )
        from jarvis.cu.engine import run_cu_loop
    else:
        from jarvis.harness.computer_use_context import ComputerUseContext

        ctx = ComputerUseContext(
            vision_engine=LegacyVisionShim(),
            brain_manager=brain,
            tool_executor=executor,
            tools=tools,
            bus=None,
            monitor="all",
        )
        if engine == "stable":
            from jarvis.harness.screenshot_only_loop_stable import run_cu_loop
        else:
            from jarvis.harness.screenshot_only_loop import run_cu_loop

    task = SimpleNamespace(prompt=goal, env={}, timeout_s=180, cwd=".")
    chunks: list[str] = []
    t0 = time.monotonic()
    async for chunk in run_cu_loop(task, ctx, cancel_token=None):
        text = (chunk.stdout or "") + (chunk.stderr or "")
        if text.strip():
            chunks.append(text.strip())
        if chunk.is_final:
            break
    return chunks, time.monotonic() - t0


def run_engine_mode(rig: RigWindow, engine: str) -> list[dict[str, Any]]:
    _activate_rig(rig)
    monitor = _virtual_rect()
    results: list[dict[str, Any]] = []

    # -- scenario 1: click all four targets (accuracy + latency) ----------
    intended = [(name, rig.target_center_screen(name)) for name in TARGETS]
    actions = []
    for name, center in intended:
        nx, ny = _norm_for(center, monitor)
        actions.append(
            json.dumps({"action": "click", "x": nx, "y": ny, "target": f"the {name} button"}),
        )
    brain = ScriptedBrain(actions)
    _ensure_measurable(rig)
    t0 = time.monotonic()
    chunks, total_s = asyncio.run(_run_engine_mission(
        engine, brain, "click each rig target button once",
    ))
    per_step = total_s / max(1, len(intended))
    summary = _summarize(
        "engine-click-targets", intended, rig,
        step_times=[per_step] * len(intended),
    )
    summary["engine"] = engine
    summary["mission_wall_s"] = round(total_s, 2)
    summary["final_chunk"] = chunks[-1][:160] if chunks else ""
    results.append(summary)

    # -- scenario 2: type into the field (click -> type -> verify) --------
    rig.clear_entry()
    rig.log.clicks.clear()
    ex, ey = rig.entry_center_screen()
    nx, ny = _norm_for((ex, ey), monitor)
    brain = ScriptedBrain([
        json.dumps({"action": "click", "x": nx, "y": ny, "target": "the text field"}),
        json.dumps({"action": "type", "text": TYPE_TEXT}),
    ])
    _ensure_measurable(rig)
    chunks, total_s = asyncio.run(_run_engine_mission(
        engine, brain, f"type {TYPE_TEXT} into the rig text field",
    ))
    results.append({
        "scenario": "engine-type-once",
        "engine": engine,
        "typed_expected": TYPE_TEXT,
        "typed_received": rig.entry_text(),
        "typed_exactly_once": rig.entry_text() == TYPE_TEXT,
        "mission_wall_s": round(total_s, 2),
    })

    # -- scenario 3: duplicate-type provocation ----------------------------
    # The script asks to type the SAME text twice with nothing in between —
    # the double-URL bug class. A correct engine executes it at most once.
    rig.clear_entry()
    brain = ScriptedBrain([
        json.dumps({"action": "click", "x": nx, "y": ny, "target": "the text field"}),
        json.dumps({"action": "type", "text": TYPE_TEXT}),
        json.dumps({"action": "type", "text": TYPE_TEXT}),
        json.dumps({"action": "type", "text": TYPE_TEXT}),
    ])
    _ensure_measurable(rig)
    chunks, total_s = asyncio.run(_run_engine_mission(
        engine, brain, f"type {TYPE_TEXT} into the rig text field",
    ))
    got = rig.entry_text()
    results.append({
        "scenario": "engine-duplicate-type-provocation",
        "engine": engine,
        "typed_expected": TYPE_TEXT,
        "typed_received": got,
        "duplicates_executed": max(0, got.count(TYPE_TEXT) - 1),
        "typed_exactly_once": got == TYPE_TEXT,
        "mission_wall_s": round(total_s, 2),
    })

    # -- scenario 4: misaimed-click provocation ----------------------------
    # Both scripted clicks aim at DEAD SPACE (no target, no visible change).
    # A correct engine reports the miss instead of silently succeeding; the
    # rig counts how many blind repeats actually landed.
    rig.log.clicks.clear()
    ox, oy = rig.canvas_origin_on_screen()
    dead = (ox + 410, oy + 330)  # empty canvas area
    ndx, ndy = _norm_for(dead, monitor)
    brain = ScriptedBrain([
        json.dumps({"action": "click", "x": ndx, "y": ndy, "target": "the OK button"}),
        json.dumps({"action": "click", "x": ndx, "y": ndy, "target": "the OK button"}),
        json.dumps({"action": "click", "x": ndx, "y": ndy, "target": "the OK button"}),
    ])
    _ensure_measurable(rig)
    chunks, total_s = asyncio.run(_run_engine_mission(
        engine, brain, "click the OK button",
    ))
    dead_clicks = [c for c in rig.log.clicks if c["target"] is None]
    results.append({
        "scenario": "engine-misaim-provocation",
        "engine": engine,
        "dead_space_clicks_executed": len(dead_clicks),
        "blind_repeats_after_first": max(0, len(dead_clicks) - 1),
        "mission_wall_s": round(total_s, 2),
        "final_chunk": chunks[-1][:160] if chunks else "",
    })
    return results


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["raw", "engine"], default="raw")
    parser.add_argument(
        "--engine", choices=["v2", "stable", "current"], default="v2",
        help="engine to drive in --mode engine",
    )
    args = parser.parse_args()

    try:
        import tkinter  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP: no display / tkinter unavailable ({exc})")
        return 2

    rig = RigWindow()
    rig.start()
    print(f"[rig] window up at {rig.canvas_origin_on_screen()} — measuring...")
    try:
        if args.mode == "raw":
            results = run_raw(rig)
            out_name = "cu_rig_results_raw.json"
        else:
            results = run_engine_mode(rig, args.engine)
            out_name = f"cu_rig_results_engine_{args.engine}.json"
    finally:
        time.sleep(0.3)
        rig.close()

    print(json.dumps(results, indent=2))
    out_path = REPO_ROOT / "scripts" / out_name
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"[rig] results written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
