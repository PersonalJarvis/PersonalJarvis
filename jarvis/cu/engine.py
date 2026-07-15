"""Computer-Use v2 engine — the perceive->act->verify mission loop.

Drop-in replacement for the legacy ``run_cu_loop`` (same signature, same
HarnessResult stream, same exit-code + readback contract), selected via
``[computer_use].engine = "v2"``. The structural differences to the legacy
monolith:

* **One frame, one mapper.** Every coordinate resolves through the
  CoordinateMapper of the frame the model actually saw; the coordinate
  convention is resolved per provider (``conventions.py``).
* **UI-idle capture.** A frame is only handed to the model once the screen
  stopped changing (bounded), replacing fixed settle sleeps.
* **Closed verification loop.** Every state-changing action is effect-checked
  (pre/post monitor grab: local crop + global diff; type read-back via the
  accessibility tree). A failed check fails the action, truncates the rest
  of the batch and forces re-perception — never silent continuation.
* **Idempotency ledger.** An action that already executed against a visually
  identical frame is refused deterministically (the double-type/double-click
  killer), replacing the legacy guard zoo.
* **One pointer action per batch.** Coordinates are only trusted for the
  first pointer action after a perception; later pointer actions in the same
  batch would act on a stale frame and are refused.

Exit codes (kept stable for the voice/UI layer): 0 done, 1 observe failure,
2 parse/confused, 3 no vision provider, 4 budget, 5 gave up / no progress,
8 tool failure, 124 timeout (outer harness), 130 cancelled.

Simplifications vs. legacy (documented, revisit when needed): no zoom-refine
LLM round (downscaled frames + effect-check retry replace it), no elevation-
prompt wait, no interactive human-handoff wait (a handoff screen fails fast
with a speakable reason instead).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.core.protocols import CancelToken, HarnessResult, HarnessTask, ImageBlock
from jarvis.cu import conventions as conv_mod
from jarvis.cu.brain_call import (
    CUNoVisionProviderError,
    call_vision_brain,
)
from jarvis.cu.capture import (
    Frame,
    capture_stable_frame,
    grab_region,
    select_capture_target,
    thumbs_similar,
)
from jarvis.cu.geometry import (
    CoordinateConvention,
    CoordinateMapper,
    MonitorInfo,
    list_monitors,
    monitor_topology_signature,
)
from jarvis.cu.ledger import ActionLedger
from jarvis.cu.target_guard import (
    foreground_matches,
    read_foreground_target,
    window_signature,
)
from jarvis.cu.verify import (
    crop_raw,
    foreground_ui_snapshot,
    regions_equal,
    snap_point_to_element,
    verify_click_focus_point,
    verify_typed_text,
)

log = logging.getLogger(__name__)

# Exit codes — mirror the legacy engine exactly (voice/UI layers branch on them).
_EXIT_OK = 0
_EXIT_OBSERVE = 1
_EXIT_PARSE = 2
_EXIT_NO_PROVIDER = 3
_EXIT_BUDGET = 4
_EXIT_FAIL = 5
_EXIT_TOOL = 8
_EXIT_CANCEL = 130

_OUTPUT_LANGUAGE_ENV_KEY = "JARVIS_OUTPUT_LANGUAGE"

_ACT_TIMEOUT_S = 15.0
_OBSERVE_TIMEOUT_S = 12.0
_DECIDE_MAX_TOKENS = 320
_JUDGE_MAX_TOKENS = 200
_HISTORY_TAIL = 12
_EFFECT_SETTLE_S = 0.35
_EFFECT_CROP_RADIUS = 110

_MAX_CONSECUTIVE_FAILURES = 4
_MAX_LLM_FAILURES = 3
_MAX_DONE_REJECTS = 3
_MAX_GUARD_HITS = 5
_MAX_OBSERVE_FAILURES = 2
_STUCK_FRAMES = 3

_SYSTEM_BASE = (
    "You are the computer-use executor: you operate the user's REAL desktop "
    "by looking at a screenshot and issuing mouse/keyboard actions.\n"
    "Core discipline — perceive, act, verify:\n"
    "* FIRST CHECK, before choosing ANY action: is the GOAL's proof already "
    "visible in this screenshot? Then reply with the done action IMMEDIATELY "
    "— every extra action after success is a failure (it wastes seconds and "
    "can undo the result). Scrolling around, waiting, or re-checking 'to be "
    "sure' AFTER the proof is visible is exactly that failure — the user is "
    "waiting for your confirmation.\n"
    "* The screenshot is the ONLY ground truth. Never assume an effect "
    "happened; check the fresh screenshot.\n"
    "* FIRST verify the effect of your previous action (see PREVIOUS STEPS). "
    "If it visibly failed or did nothing, correct course — do NOT repeat it "
    "unchanged.\n"
    "* Prefer click_element with an EXACT label from CLICKABLE ELEMENTS when "
    "one matches your target — it is more reliable than pixel coordinates.\n"
    "* Never two pointer actions (click/drag) in one reply — after the first "
    "one the screen may change, so a second guess would be blind. A tight "
    "click -> type -> key sequence IS allowed.\n"
    "* Before typing, the target field must have keyboard focus (click it "
    "first). Set clear_first true to REPLACE existing text (address bars, "
    "search boxes) instead of appending.\n"
    "* Use open_app to launch or focus an application. Use switch_window to "
    "focus an open window.\n"
    "* done: ONLY when the goal's observable proof is visible in the CURRENT "
    "screenshot; quote that proof in reason.\n"
    "* fail: ONLY when the goal is genuinely impossible from here; explain "
    "why in reason.\n\n"
)

_JUDGE_SYSTEM = (
    "You are a STRICT completion judge for desktop automation. Decide from "
    "the attached screenshot whether the GOAL is fully achieved. Output "
    "exactly ONE JSON object, no prose, no code fences: "
    '{"done": true|false, "proof": "<the exact on-screen evidence>"}.\n'
    "* Trust only what is visible in the screenshot. If the required proof "
    "is absent or ambiguous, answer done:false.\n"
    "* The proof is spoken back to the user verbatim — write one short, "
    "human sentence quoting the concrete on-screen evidence (window, title, "
    "field value, page content).\n"
    "* When done:false, state briefly in proof what is still missing."
)

_LANGUAGE_NAMES = {"de": "German", "en": "English", "es": "Spanish"}


def _proof_language_directive(output_language: str | None) -> str:
    """Judge directive: write ``proof`` in the user's turn language."""
    lang = (output_language or "").strip().lower()
    name = _LANGUAGE_NAMES.get(lang)
    if not name:
        return ""
    return (
        f"\n\nIMPORTANT: write the 'proof' value in {name}, the user's "
        "language — it is spoken back verbatim."
    )


def _parse_verdict(raw: str) -> tuple[bool, str]:
    """Tolerant ``{"done": bool, "proof": str}`` parse; (False, "") on junk."""
    import json
    import re

    cleaned = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", cleaned, re.DOTALL)
    if fence is not None:
        cleaned = fence.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return (False, "")
    try:
        obj = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return (False, "")
    if not isinstance(obj, dict):
        return (False, "")
    done = obj.get("done") is True
    proof = str(obj.get("proof", "") or "").strip()[:220]
    return (done, proof)


def _select_all_keys() -> list[str]:
    """Platform-correct select-all combo (the legacy engine hardcoded ctrl+a,
    which is wrong on macOS)."""
    return ["cmd", "a"] if sys.platform == "darwin" else ["ctrl", "a"]


def _wayland_refusal() -> str | None:
    try:
        from jarvis.platform.probes import is_wayland  # noqa: PLC0415

        if is_wayland():
            return (
                "cannot run on a Wayland session: Wayland blocks global "
                "screen capture and synthetic input by design. Log into an "
                "X11 session instead (running single apps under XWayland "
                "is not enough — the desktop session itself must be X11)."
            )
    except Exception:  # noqa: BLE001
        return None
    return None


def _is_cancelled(token: CancelToken | None) -> bool:
    if token is None:
        return False
    try:
        return bool(token.is_cancelled())
    except Exception:  # noqa: BLE001
        return False


@dataclass(frozen=True)
class _ImageCfg:
    """Frame-encoding knobs shared by perception and the done-judge."""

    max_dimension: int
    blob_dir: Path


class _Profiler:
    """Per-mission phase wall-time accumulator + CUStepProfiled emitter."""

    def __init__(self, bus: Any) -> None:
        self.phase_ms: dict[str, float] = {}
        self._bus = bus
        # Strong refs so fire-and-forget publishes are never GC'd mid-flight.
        self._tasks: set[Any] = set()

    def add(self, phase: str, t0: float, step_idx: int) -> None:
        """Accumulate one phase span and publish it as a heartbeat.

        Called from the loop's async context; the publish rides a fire-and-
        forget task so profiling never blocks the mission (the event doubles
        as the speech-pipeline liveness signal, mirroring the legacy engine).
        """
        ms = (time.monotonic() - t0) * 1000.0
        self.phase_ms[phase] = self.phase_ms.get(phase, 0.0) + ms
        if self._bus is None:
            return
        try:
            from jarvis.core.events import CUStepProfiled  # noqa: PLC0415

            event = CUStepProfiled(
                phase=phase,  # type: ignore[arg-type]
                duration_ms=max(0, int(ms)),
                step_idx=step_idx,
                engine="v2",
            )
            task = asyncio.get_running_loop().create_task(
                self._bus.publish(event),
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except Exception:  # noqa: BLE001
            log.debug("CUStepProfiled publish failed", exc_info=True)

    def summary(self, steps: int, t_start_ns: int) -> str:
        total_s = (time.time_ns() - t_start_ns) / 1e9
        parts = " ".join(
            f"{k}={v / 1000.0:.1f}s" for k, v in sorted(self.phase_ms.items())
        )
        return (
            f"[cu] mission profile: steps={steps} total={total_s:.1f}s {parts}"
        ).rstrip() + "\n"


async def _publish_observation(bus: Any, frame: Frame, window_title: str) -> None:
    if bus is None:
        return
    try:
        from jarvis.core.events import ObservationCaptured  # noqa: PLC0415

        await bus.publish(ObservationCaptured(
            source="screenshot_only",
            window_title=window_title,
            node_count=0,
            screenshot_hash=frame.sha256,
            screenshot_path=frame.blob_path,
        ))
    except Exception:  # noqa: BLE001
        log.debug("ObservationCaptured publish failed", exc_info=True)


def _foreground_title() -> str:
    try:
        from jarvis.platform import window_state  # noqa: PLC0415

        return str(window_state.get_foreground_title() or "")
    except Exception:  # noqa: BLE001
        return ""


def _window_state_signature(
    window: Any,
    rect: tuple[int, int, int, int] | None,
) -> tuple[Any, ...]:
    """Stable-enough foreground identity for capture-to-action race checks."""
    return window_signature(window, rect)


async def _live_window_state_signature() -> tuple[Any, ...]:
    """Read foreground identity and geometry as one fail-closed check."""
    return (await asyncio.to_thread(read_foreground_target)).signature


async def _dispatch_tool(
    ctx: Any, tool_name: str, args: dict[str, Any], trace_id: Any,
) -> tuple[bool, str]:
    """Run one action through the ToolExecutor (AP-3 choke point)."""
    # A macOS Screen Recording grant can be revoked after perception but
    # before actuation. Re-probe at the final dispatcher choke point so no CU
    # action can run against a screen Jarvis is no longer allowed to observe.
    try:
        from jarvis.cu.capture import (  # noqa: PLC0415
            _require_macos_screen_recording_permission,
        )

        _require_macos_screen_recording_permission()
    except RuntimeError as exc:
        return False, str(exc)

    tools = ctx.tools or {}
    tool = tools.get(tool_name)
    if tool is None:
        return False, f"{tool_name} tool not wired"
    executor = ctx.tool_executor
    if executor is None:
        return False, "tool_executor not wired"
    try:
        res = await asyncio.wait_for(
            executor.execute(
                tool, args, user_utterance="computer-use", trace_id=trace_id,
            ),
            timeout=_ACT_TIMEOUT_S,
        )
    except TimeoutError:
        return False, f"{tool_name} timed out after {_ACT_TIMEOUT_S:.0f}s"
    except Exception as exc:  # noqa: BLE001
        return False, f"{tool_name} crash: {type(exc).__name__}: {exc}"
    return (
        bool(getattr(res, "success", False)),
        str(getattr(res, "output", "") or getattr(res, "error", "") or ""),
    )


def _summarize_action(action: dict[str, Any]) -> str:
    kind = action.get("action", "?")
    if kind == "click":
        t = action.get("target") or ""
        return f"click({int(action.get('x', 0))},{int(action.get('y', 0))}{', ' + t if t else ''})"
    if kind == "click_element":
        return f"click_element({action.get('name')!r})"
    if kind == "type":
        return f"type({str(action.get('text', ''))[:40]!r})"
    if kind == "key":
        return f"key({'+'.join(action.get('keys', []))})"
    if kind == "scroll":
        return f"scroll({action.get('direction')} x{action.get('amount')})"
    if kind == "drag":
        return "drag"
    if kind in ("open_app", "switch_window"):
        return f"{kind}({action.get('name')!r})"
    if kind == "wait":
        return f"wait({action.get('ms')}ms)"
    return str(kind)


#: Zoom-refine crop half-side in input units. Near-square context crops
#: preserve surroundings better than tight boxes (MEGA-GUI / UI-Zoomer).
_REFINE_RADIUS = 220

def _crop_norm_to_screen(
    bbox: dict[str, int], image_size: tuple[int, int], nx: float, ny: float,
) -> tuple[int, int]:
    """Resolve a 0-1000 point WITHIN a zoom crop to absolute screen units.

    Builds a :class:`CoordinateMapper` for the crop rect — the SAME central
    translation every other coordinate resolves through — so the refine path
    can never disagree with the main mapping. The image size is the crop's
    native pixel size (2x the rect on Retina); the result is clamped inside
    the crop rect like every other mapped coordinate.
    """
    mapper = CoordinateMapper(
        capture_left=int(bbox["left"]),
        capture_top=int(bbox["top"]),
        capture_width=int(bbox["width"]),
        capture_height=int(bbox["height"]),
        image_width=int(image_size[0]),
        image_height=int(image_size[1]),
    )
    return mapper.normalized_to_screen(nx, ny)


_REFINE_SYSTEM = (
    "You are a precision click-refinement assistant. The attached image is a "
    "ZOOMED-IN crop of the live screen, centered on a click that produced NO "
    "visible effect. Locate the TARGET element inside the crop. Output "
    "exactly ONE JSON object, no prose, no fences:\n"
    '  {"found": true, "x": <0-1000>, "y": <0-1000>}  — x/y on a 0-1000 grid '
    "WITHIN THIS CROP (0,0 = crop top-left), aimed at the CENTER of the "
    "target.\n"
    '  {"found": false}  — the target is not visible anywhere in the crop.\n'
    "Never guess a position for an element you cannot actually see."
)


async def _zoom_refine_point(
    ctx: Any,
    frame: Frame,
    x: int,
    y: int,
    *,
    goal: str,
    target: str,
    expected_window_signature: tuple[Any, ...],
) -> tuple[int, int] | None:
    """One coarse-to-fine grounding round after a VERIFIED miss.

    Grabs a native-resolution crop around the missed point (no downscale —
    this is where zoom methods earn their ScreenSpot-Pro gains) and asks the
    FAST vision chain to re-locate the target inside it. Returns the refined
    absolute point, or ``None`` (keep/abandon). Runs ONLY on the miss path,
    so the ordinary click costs zero extra model calls.
    """
    import io  # noqa: PLC0415
    import json as _json  # noqa: PLC0415
    import re as _re  # noqa: PLC0415

    if await _live_window_state_signature() != expected_window_signature:
        return None
    bbox = frame.mapper.region_around(int(x), int(y), _REFINE_RADIUS)
    raw = await asyncio.to_thread(grab_region, bbox)
    if (
        raw is None
        or await _live_window_state_signature() != expected_window_signature
    ):
        return None
    try:
        from PIL import Image  # noqa: PLC0415

        img = Image.frombytes("RGB", raw[0], raw[1])
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        crop_jpeg = buf.getvalue()
    except Exception:  # noqa: BLE001
        return None
    user = (
        f"TARGET: {target or 'the element the GOAL needs clicked next'}\n"
        f"GOAL: {goal}\n"
        f"The crop is {bbox['width']}x{bbox['height']} screen units, centered "
        "on the missed click. Reply with the JSON object only."
    )
    try:
        reply = await call_vision_brain(
            ctx.brain_manager,
            build_prompt=lambda provider, brain: (_REFINE_SYSTEM, user),
            images=[ImageBlock(
                mime="image/jpeg",
                data_b64=base64.b64encode(crop_jpeg).decode("ascii"),
            )],
            max_tokens=64,
            early_stop_json=True,
        )
    except Exception:  # noqa: BLE001 — refine is strictly best-effort
        log.debug("[cu] zoom refine call failed", exc_info=True)
        return None
    if await _live_window_state_signature() != expected_window_signature:
        return None
    cleaned = (reply.text or "").strip()
    fence = _re.search(r"```(?:json)?\s*(.+?)\s*```", cleaned, _re.DOTALL)
    if fence is not None:
        cleaned = fence.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = _json.loads(cleaned[start:end + 1])
    except _json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or obj.get("found") is not True:
        return None
    try:
        nx = min(max(float(obj.get("x", 0)), 0.0), 1000.0)
        ny = min(max(float(obj.get("y", 0)), 0.0), 1000.0)
    except (TypeError, ValueError):
        return None
    return _crop_norm_to_screen(bbox, raw[0], nx, ny)


async def _judge_done(
    ctx: Any,
    goal: str,
    monitor: MonitorInfo,
    image_cfg: _ImageCfg,
    output_language: str | None,
    *,
    frame: Frame | None = None,
) -> tuple[bool, str]:
    """Strict completion judge.

    Verifies against ``frame`` when the caller passes one — the perception
    frame of the CURRENT step, valid when no action executed since it was
    captured (the model claims "the proof is visible in THIS screenshot",
    so judging that exact evidence is both faster AND the more faithful
    check). Without a frame (actions ran in this batch), a FRESH stable
    capture is taken as before. The recapture cost the completion readback
    ~0.5–1.5 s per done (live complaint 2026-07-02: "he was done but said
    nothing for too long").
    """
    if frame is None:
        try:
            frame = await asyncio.wait_for(
                asyncio.to_thread(
                    capture_stable_frame,
                    monitor,
                    max_dimension=image_cfg.max_dimension,
                    blob_dir=image_cfg.blob_dir,
                ),
                timeout=_OBSERVE_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 — judge is best-effort; reject on no frame
            return (False, "")
    system = _JUDGE_SYSTEM + _proof_language_directive(output_language)
    user = (
        f"GOAL: {goal}\n\nThe attached screenshot shows the CURRENT screen. "
        "Is the goal fully achieved? Reply with the JSON object only."
    )
    image = ImageBlock(
        mime="image/jpeg",
        data_b64=base64.b64encode(frame.jpeg).decode("ascii"),
        source_hash=frame.sha256,
    )
    try:
        reply = await call_vision_brain(
            ctx.brain_manager,
            build_prompt=lambda provider, brain: (system, user),
            images=[image],
            max_tokens=_JUDGE_MAX_TOKENS,
            early_stop_json=True,
        )
    except Exception:  # noqa: BLE001 — an unreachable judge must not crash a
        # finished mission; the caller treats it as "not confirmed".
        log.debug("[cu] done-judge call failed", exc_info=True)
        return (False, "")
    return _parse_verdict(reply.text)


async def run_cu_loop(
    task: HarnessTask,
    ctx: Any,
    *,
    cancel_token: CancelToken | None = None,
) -> AsyncIterator[HarnessResult]:
    """CU v2 mission loop. Same contract as the legacy ``run_cu_loop``."""
    t_start = time.time_ns()
    goal = task.prompt
    output_language = (getattr(task, "env", None) or {}).get(
        _OUTPUT_LANGUAGE_ENV_KEY,
    ) or None
    from uuid import uuid4  # noqa: PLC0415

    trace_id = uuid4()  # correlates every tool dispatch of this mission
    bus = getattr(ctx, "bus", None)
    profiler = _Profiler(bus)
    ledger = ActionLedger()
    history: list[str] = []
    step_idx = 0

    def _final(stdout: str = "", stderr: str = "", exit_code: int = 0) -> HarnessResult:
        profile = profiler.summary(step_idx, t_start)
        log.info(profile.rstrip())
        return HarnessResult(
            stdout=stdout,
            stderr=stderr + profile,
            exit_code=exit_code,
            duration_ms=(time.time_ns() - t_start) // 1_000_000,
            is_final=True,
        )

    def _progress(msg: str) -> HarnessResult:
        return HarnessResult(stdout=msg + "\n", is_final=False)

    yield _progress(f"[cu] Start (v2): {goal[:80]}")

    wl = _wayland_refusal()
    if wl is not None:
        yield _final(stderr=f"[cu] {wl}\n", exit_code=_EXIT_OBSERVE)
        return

    # Windows: declare the process PER_MONITOR_AWARE_V2 before any geometry
    # is read (idempotent no-op elsewhere / on later calls). The thread pin
    # in input_space() remains the per-call enforcement; the declaration
    # keeps window rects and monitor metrics un-virtualized process-wide.
    try:
        from jarvis.core.win32_dpi import ensure_dpi_awareness  # noqa: PLC0415

        ensure_dpi_awareness()
    except Exception:  # noqa: BLE001 — declaration is best-effort
        log.debug("[cu] DPI awareness declaration failed", exc_info=True)

    max_steps = max(25, int(getattr(ctx, "step_budget", 100)))
    monitor_policy = str(getattr(ctx, "monitor", "primary") or "primary")
    main_monitor = str(getattr(ctx, "main_monitor", "primary") or "primary")
    capture_scope = str(getattr(ctx, "capture_scope", "window") or "window")
    # DEFAULT OFF: the restore/maximize animation visibly "zooms" open windows
    # and rearranges the user's layout uninvited (maintainer complaint
    # 2026-07-02); window-scoped capture covers the grounding need untouched.
    normalize_window = bool(getattr(ctx, "normalize_window", False))
    coordinate_space = str(getattr(ctx, "coordinate_space", "auto") or "auto")
    # NOTE: no `or 1.0` here — a configured 0 means "no settle waits" (tests,
    # speed runs) and must be honored, not silently coerced back to 1.0.
    raw_settle = getattr(ctx, "settle_scale", None)
    settle_scale = 1.0 if raw_settle is None else float(raw_settle)
    strict_verify = bool(getattr(ctx, "strict_verify", True))
    image_cfg = _ImageCfg(
        max_dimension=int(getattr(ctx, "image_max_dimension", 1366) or 1366),
        blob_dir=Path("data") / "flight_recorder" / "blobs",
    )

    consecutive_failures = 0
    llm_failures = 0
    done_rejects = 0
    guard_hits = 0
    observe_failures = 0
    prev_thumb: bytes | None = None
    fruitless_steps = 0  # steps with zero successful actions on an unchanged screen
    last_step_had_success = False
    # Normalize the work surface before the first frame and again after every
    # focus change (open_app / switch_window): professional computer-use
    # harnesses never pixel-ground on a small window floating in a big desktop
    # (see jarvis.platform.window_state.normalize_foreground_window).
    need_normalize = normalize_window

    while step_idx < max_steps:
        step_idx += 1
        if _is_cancelled(cancel_token):
            yield _final(stderr="[cu] cancelled\n", exit_code=_EXIT_CANCEL)
            return

        # ---- perceive -----------------------------------------------------
        t0 = time.monotonic()
        if need_normalize:
            need_normalize = False
            try:
                from jarvis.platform import window_state  # noqa: PLC0415

                normalized, norm_msg = await asyncio.to_thread(
                    window_state.normalize_foreground_window,
                )
                if normalized:
                    log.info("[cu] normalized target window: %s", norm_msg)
            except Exception:  # noqa: BLE001 — normalize is best-effort
                log.debug("[cu] window normalize failed", exc_info=True)
        try:
            captured_displays = await asyncio.to_thread(list_monitors)
            if not captured_displays:
                raise RuntimeError(
                    "no physical displays are available for Computer-Use",
                )
            captured_topology = monitor_topology_signature(captured_displays)
            pre_capture_target = await asyncio.to_thread(read_foreground_target)
            pre_capture_window_signature = pre_capture_target.signature

            def capture_identity_guard(
                expected: tuple[Any, ...] = pre_capture_window_signature,
            ) -> bool:
                return foreground_matches(expected)

            monitor = await asyncio.to_thread(
                select_capture_target,
                monitor_policy,
                main_monitor=main_monitor,
                scope=capture_scope,
            )
            frame_coro = asyncio.to_thread(
                capture_stable_frame,
                monitor,
                max_dimension=image_cfg.max_dimension,
                blob_dir=image_cfg.blob_dir,
                capture_guard=capture_identity_guard,
            )
            snapshot_coro = foreground_ui_snapshot(
                observation_guard=capture_identity_guard,
            )
            frame, (labels, field_hint, handoff, clickables) = await asyncio.wait_for(
                asyncio.gather(frame_coro, snapshot_coro),
                timeout=_OBSERVE_TIMEOUT_S,
            )
            if captured_topology:
                live_topology = monitor_topology_signature(
                    await asyncio.to_thread(list_monitors),
                )
                if live_topology != captured_topology:
                    raise RuntimeError(
                        "display topology changed during capture; retrying with fresh geometry"
                    )
            captured_target = await asyncio.to_thread(read_foreground_target)
            captured_window = captured_target.window
            captured_window_rect = captured_target.rect
            captured_window_signature = captured_target.signature
            if captured_window_signature[0] == "none":
                raise RuntimeError(
                    "foreground window identity is unavailable; refusing "
                    "unbound Computer-Use input"
                )
            if captured_window_signature != pre_capture_window_signature:
                raise RuntimeError(
                    "foreground window changed during capture; retrying with a fresh frame"
                )
            if monitor.window_handle is not None and (
                captured_window is None
                or captured_window.handle != monitor.window_handle
                or captured_window_rect != (
                    monitor.left, monitor.top, monitor.width, monitor.height,
                )
            ):
                raise RuntimeError(
                    "foreground window changed during capture; retrying with a fresh frame"
                )
        except Exception as exc:  # noqa: BLE001 — capture is inherently flaky
            observe_failures += 1
            log.warning("[cu] observe failed (step %d): %s", step_idx, exc)
            if observe_failures > _MAX_OBSERVE_FAILURES:
                yield _final(
                    stderr=f"[cu] cannot see the screen: {exc}\n",
                    exit_code=_EXIT_OBSERVE,
                )
                return
            continue
        observe_failures = 0
        window_title = _foreground_title()
        await _publish_observation(bus, frame, window_title)
        profiler.add("observe", t0, step_idx)

        # Human-handoff screens (login / 2FA / CAPTCHA): the agent must never
        # type a secret it does not hold — stop with a speakable reason.
        if handoff is not None:
            yield _final(
                stderr=(
                    f"[cu] fail at step-{step_idx}: the screen is asking for "
                    f"{handoff} — please complete that yourself, then ask me "
                    "again.\n"
                ),
                exit_code=_EXIT_FAIL,
            )
            return

        # No-progress guard: several consecutive steps that produced no
        # successful action while the screen never changed means we are
        # circling — judge once (the goal may in fact be met), then stop.
        if (
            prev_thumb is not None
            and thumbs_similar(prev_thumb, frame.thumb)
            and not last_step_had_success
        ):
            fruitless_steps += 1
        else:
            fruitless_steps = 0
        prev_thumb = frame.thumb
        last_step_had_success = False
        if fruitless_steps >= _STUCK_FRAMES:
            done, proof = await _judge_done(
                ctx, goal, monitor, image_cfg, output_language,
                frame=frame,  # captured this step; the screen is unchanged
            )
            if done:
                yield _final(
                    stdout=f"[cu] done (verified: {proof})\n", exit_code=_EXIT_OK,
                )
            else:
                yield _final(
                    stderr=(
                        f"[cu] fail at step-{step_idx}: no progress — the "
                        "screen has not changed despite my actions.\n"
                    ),
                    exit_code=_EXIT_FAIL,
                )
            return

        # ---- decide ---------------------------------------------------------
        t0 = time.monotonic()
        conventions_used: dict[str, CoordinateConvention] = {}

        def _build_prompt(
            provider: str,
            brain: Any,
            *,
            # Bind THIS step's perception explicitly (B023): the builder runs
            # inside the same iteration, but explicit defaults make that a
            # structural guarantee instead of a timing accident.
            _frame: Frame = frame,
            _title: str = window_title,
            _labels: list[str] = labels,
            _field_hint: str = field_hint,
            _conv_used: dict[str, CoordinateConvention] = conventions_used,
        ) -> tuple[str, str]:
            convention = conv_mod.resolve_convention(
                provider, brain, config_override=coordinate_space,
            )
            _conv_used[provider] = convention
            system = (
                _SYSTEM_BASE
                + conv_mod.coordinate_prompt_block(
                    convention, _frame.image_width, _frame.image_height,
                )
                + "\n\n"
                + conv_mod.action_grammar_block()
            )
            lines = [f"GOAL: {goal}"]
            if _title:
                lines.append(f"FOREGROUND WINDOW: {_title}")
            if _labels:
                lines.append("CLICKABLE ELEMENTS: " + ", ".join(_labels))
            if _field_hint:
                lines.append(_field_hint.strip())
            tail = history[-_HISTORY_TAIL:]
            lines.append(
                "PREVIOUS STEPS:\n" + ("\n".join(tail) if tail else "(none)"),
            )
            if not _frame.stable:
                lines.append(
                    "NOTE: the screen was still changing when this screenshot "
                    "was captured — verify carefully before acting.",
                )
            lines.append("Reply with the JSON action(s) only.")
            return system, "\n\n".join(lines)

        image = ImageBlock(
            mime="image/jpeg",
            data_b64=base64.b64encode(frame.jpeg).decode("ascii"),
            source_hash=frame.sha256,
        )
        try:
            reply = await call_vision_brain(
                ctx.brain_manager,
                build_prompt=_build_prompt,
                images=[image],
                max_tokens=_DECIDE_MAX_TOKENS,
                early_stop_json=True,
            )
        except CUNoVisionProviderError as exc:
            yield _final(
                stderr=f"[cu] no screen-capable model reachable: {exc}\n",
                exit_code=_EXIT_NO_PROVIDER,
            )
            return
        except Exception as exc:  # noqa: BLE001 — transient brain failure
            llm_failures += 1
            log.warning("[cu] brain call failed (step %d): %s", step_idx, exc)
            if llm_failures >= _MAX_LLM_FAILURES:
                yield _final(
                    stderr=f"[cu] the screen-control model kept failing: {exc}\n",
                    exit_code=_EXIT_PARSE,
                )
                return
            history.append(f"step {step_idx}: (model call failed — retrying)")
            continue
        profiler.add("think", t0, step_idx)

        try:
            actions = conv_mod.parse_actions(reply.text)
        except conv_mod.ActionParseError as exc:
            llm_failures += 1
            log.info("[cu] unparseable model reply (step %d): %s", step_idx, exc)
            if llm_failures >= _MAX_LLM_FAILURES:
                yield _final(
                    stderr=(
                        "[cu] could not get a valid screen-control response "
                        f"from the model: {exc}\n"
                    ),
                    exit_code=_EXIT_PARSE,
                )
                return
            history.append(
                f"step {step_idx}: (reply was not a valid action — reply with "
                "the JSON action object only)",
            )
            continue
        llm_failures = 0
        convention = conventions_used.get(
            reply.provider,
            conv_mod.resolve_convention(
                reply.provider, None, config_override=coordinate_space,
            ),
        )

        # ---- act + verify ---------------------------------------------------
        pointer_used = False
        batch_acted = False  # any executed action invalidates the step frame
        for action in actions:
            if _is_cancelled(cancel_token):
                yield _final(stderr="[cu] cancelled\n", exit_code=_EXIT_CANCEL)
                return
            kind = action["action"]

            if kind == "done":
                done, proof = await _judge_done(
                    ctx, goal, monitor, image_cfg, output_language,
                    # No action ran since perception -> the screen IS the
                    # frame the model called done on; judge that evidence
                    # directly instead of paying a second stability capture.
                    frame=None if batch_acted else frame,
                )
                if done:
                    yield _final(
                        stdout=f"[cu] done (verified: {proof})\n",
                        exit_code=_EXIT_OK,
                    )
                    return
                done_rejects += 1
                if done_rejects >= _MAX_DONE_REJECTS:
                    yield _final(
                        stderr=(
                            f"[cu] fail at step-{step_idx}: the goal could not "
                            "be verified as achieved on screen"
                            + (f" ({proof})" if proof else "")
                            + ".\n"
                        ),
                        exit_code=_EXIT_FAIL,
                    )
                    return
                history.append(
                    f"step {step_idx}: done REJECTED by the verifier"
                    + (f" — {proof}" if proof else "")
                    + " — keep working toward visible proof",
                )
                break  # re-perceive

            if kind == "fail":
                reason = action.get("reason") or "the model gave up"
                yield _final(
                    stderr=f"[cu] fail at step-{step_idx}: {reason}\n",
                    exit_code=_EXIT_FAIL,
                )
                return

            # -- pointer staleness rule -----------------------------------
            # Scrolling always targets the pointer's current surface. Treat it
            # as a pointer action even when the model omitted coordinates; the
            # omission is resolved to this frame's capture center below.
            is_pointer = kind in ("click", "click_element", "drag", "scroll")
            if is_pointer and pointer_used:
                history.append(
                    f"step {step_idx}: {_summarize_action(action)} SKIPPED — "
                    "only one pointer action per screenshot; the screen may "
                    "have changed",
                )
                break  # re-perceive before the next pointer action

            # -- resolve coordinates through THIS frame's mapper -----------
            resolved_xy: tuple[int, int] | None = None
            if kind == "click":
                resolved_xy = frame.mapper.model_to_screen(
                    action["x"], action["y"], convention,
                )
                # Element anchoring: a point INSIDE a clickable element snaps
                # to that element's center (smallest containing rect, capped
                # against container traps) — the model only has to point
                # anywhere inside the right control, its residual 1-3%
                # pointing error stops mattering. Zero added latency: the
                # rects ride the snapshot already captured in parallel.
                snapped = snap_point_to_element(
                    resolved_xy[0], resolved_xy[1], clickables,
                    capture_area=monitor.width * monitor.height,
                    capture_rect=(
                        monitor.left, monitor.top,
                        monitor.width, monitor.height,
                    ),
                )
                if snapped is not None:
                    sx2, sy2, snap_label = snapped
                    if (sx2, sy2) != resolved_xy:
                        log.info(
                            "[cu] click (%d,%d) anchored to element center "
                            "(%d,%d)%s", resolved_xy[0], resolved_xy[1],
                            sx2, sy2,
                            f" '{snap_label[:40]}'" if snap_label else "",
                        )
                    resolved_xy = (sx2, sy2)
            elif kind == "drag":
                resolved_xy = frame.mapper.model_to_screen(
                    action["x"], action["y"], convention,
                )
            elif kind == "scroll":
                if "x" in action:
                    sx, sy = frame.mapper.model_to_screen(
                        action["x"], action["y"], convention,
                    )
                else:
                    # A stale cursor can sit on a different monitor. Ground a
                    # coordinate-less scroll in the center of the window or
                    # monitor that THIS screenshot represents.
                    sx = monitor.left + max(0, monitor.width) // 2
                    sy = monitor.top + max(0, monitor.height) // 2
                action = {**action, "x": sx, "y": sy}

            # -- idempotency ledger -----------------------------------------
            if kind in {"click", "click_element", "drag", "scroll"} and captured_topology:
                live_topology = monitor_topology_signature(
                    await asyncio.to_thread(list_monitors),
                )
                if live_topology != captured_topology:
                    history.append(
                        f"step {step_idx}: {_summarize_action(action)} REFUSED — "
                        "the display layout changed after the screenshot; "
                        "capturing fresh geometry before any pointer action."
                    )
                    break

            if ledger.is_duplicate(action, frame.thumb, resolved_xy=resolved_xy):
                guard_hits += 1
                history.append(
                    f"step {step_idx}: {_summarize_action(action)} REFUSED — "
                    "this exact action already ran on this unchanged screen; "
                    "the screen did not react. Choose a DIFFERENT action.",
                )
                if guard_hits >= _MAX_GUARD_HITS:
                    yield _final(
                        stderr=(
                            f"[cu] fail at step-{step_idx}: I kept trying the "
                            "same action without any effect on screen.\n"
                        ),
                        exit_code=_EXIT_FAIL,
                    )
                    return
                break  # re-perceive

            if kind in {"click", "click_element", "drag", "scroll", "type", "key"}:
                if await _live_window_state_signature() != captured_window_signature:
                    history.append(
                        f"step {step_idx}: {_summarize_action(action)} REFUSED — "
                        "the foreground window changed after the screenshot; "
                        "capturing a fresh frame before acting."
                    )
                    break

            # -- execute ------------------------------------------------------
            t0 = time.monotonic()
            ok = False
            detail = ""
            if kind == "click":
                assert resolved_xy is not None
                pointer_used = True
                pre = await asyncio.to_thread(grab_region, monitor.bbox)
                ok, detail = await _dispatch_tool(
                    ctx, "click",
                    {
                        "x": resolved_xy[0], "y": resolved_xy[1],
                        "button": action["button"], "double": action["double"],
                        "_expected_window_signature": captured_window_signature,
                    },
                    trace_id,
                )
                profiler.add("act", t0, step_idx)
                t0 = time.monotonic()
                if ok:
                    ledger.record(action, frame.thumb, resolved_xy=resolved_xy)
                    await asyncio.sleep(_EFFECT_SETTLE_S * settle_scale)
                    post = await asyncio.to_thread(grab_region, monitor.bbox)
                    rect = monitor.bbox
                    rect_t = (rect["left"], rect["top"], rect["width"], rect["height"])
                    local_pre = crop_raw(
                        pre, rect_t, *resolved_xy, _EFFECT_CROP_RADIUS,
                    ) if pre else None
                    local_post = crop_raw(
                        post, rect_t, *resolved_xy, _EFFECT_CROP_RADIUS,
                    ) if post else None
                    local_same = regions_equal(local_pre, local_post)
                    if local_same is True:
                        from jarvis.cu.capture import frames_differ  # noqa: PLC0415

                        if pre is not None and post is not None and frames_differ(pre, post):
                            detail = (detail + " — screen reacted elsewhere").strip()
                        elif await verify_click_focus_point(
                            *resolved_xy,
                            capture_area=monitor.width * monitor.height,
                        ) is True:
                            # Zero pixel change because the target was
                            # ALREADY in the desired state: the click point
                            # sits inside the control that holds keyboard
                            # focus (a default-focused address bar changes
                            # nothing when clicked). Failing this click
                            # beheads the batched type behind it and stalls
                            # the mission AT its goal (live incident
                            # 2026-07-02 19:06).
                            detail = (
                                detail
                                + " — no visible change, but the click "
                                "landed in the focused control (already in "
                                "the desired state)"
                            ).strip(" —")
                        else:
                            ok = False
                            detail = (
                                "the click produced NO visible change — it "
                                "likely missed the target. Re-locate the "
                                "element on the fresh screenshot (or use "
                                "click_element with its exact label)."
                            )
                            # Coarse-to-fine rescue (miss path only): re-ground
                            # the target inside a native-resolution zoom crop
                            # and retry ONCE at the refined point. This is the
                            # step behind the ScreenSpot-Pro zoom gains; the
                            # happy path never pays for it.
                            refined = await _zoom_refine_point(
                                ctx, frame, *resolved_xy,
                                goal=goal,
                                target=str(action.get("target", "")),
                                expected_window_signature=captured_window_signature,
                            )
                            if refined is not None and (
                                abs(refined[0] - resolved_xy[0]) > 12
                                or abs(refined[1] - resolved_xy[1]) > 12
                            ):
                                log.info(
                                    "[cu] zoom refine: retrying click at "
                                    "(%d,%d) after miss at (%d,%d)",
                                    refined[0], refined[1], *resolved_xy,
                                )
                                pre2 = await asyncio.to_thread(
                                    grab_region, monitor.bbox,
                                )
                                if (
                                    await _live_window_state_signature()
                                    != captured_window_signature
                                    or monitor_topology_signature(
                                        await asyncio.to_thread(list_monitors),
                                    )
                                    != captured_topology
                                ):
                                    history.append(
                                        f"step {step_idx}: zoom-refined click REFUSED — "
                                        "the foreground window or display layout changed; "
                                        "capturing a fresh frame before retrying."
                                    )
                                    break
                                ok2, detail2 = await _dispatch_tool(
                                    ctx, "click",
                                    {
                                        "x": refined[0], "y": refined[1],
                                        "button": action["button"],
                                        "double": action["double"],
                                        "_expected_window_signature": (
                                            captured_window_signature
                                        ),
                                    },
                                    trace_id,
                                )
                                if ok2:
                                    ledger.record(
                                        action, frame.thumb, resolved_xy=refined,
                                    )
                                    await asyncio.sleep(
                                        _EFFECT_SETTLE_S * settle_scale,
                                    )
                                    post2 = await asyncio.to_thread(
                                        grab_region, monitor.bbox,
                                    )
                                    lp2 = crop_raw(
                                        pre2, rect_t, *refined,
                                        _EFFECT_CROP_RADIUS,
                                    ) if pre2 else None
                                    lq2 = crop_raw(
                                        post2, rect_t, *refined,
                                        _EFFECT_CROP_RADIUS,
                                    ) if post2 else None
                                    if regions_equal(lp2, lq2) is not True:
                                        ok = True
                                        detail = (
                                            f"{detail2} (zoom-refined retry "
                                            "after a verified miss)"
                                        )
                profiler.add("verify", t0, step_idx)

            elif kind == "type":
                if action.get("clear_first"):
                    clear_ok, clear_detail = await _dispatch_tool(
                        ctx,
                        "hotkey",
                        {
                            "keys": _select_all_keys(),
                            "_expected_window_signature": captured_window_signature,
                        },
                        trace_id,
                    )
                    if not clear_ok:
                        ok = False
                        detail = (
                            "could not select existing text before replacement: "
                            f"{clear_detail}; refusing to append new text"
                        )
                    else:
                        ok, detail = await _dispatch_tool(
                            ctx,
                            "type_text",
                            {
                                "text": action["text"],
                                "_expected_window_signature": (
                                    captured_window_signature
                                ),
                            },
                            trace_id,
                        )
                else:
                    ok, detail = await _dispatch_tool(
                        ctx,
                        "type_text",
                        {
                            "text": action["text"],
                            "_expected_window_signature": captured_window_signature,
                        },
                        trace_id,
                    )
                profiler.add("act", t0, step_idx)
                t0 = time.monotonic()
                if ok:
                    ledger.record(action, frame.thumb)
                    if strict_verify:
                        landed = await verify_typed_text(action["text"])
                        if landed is False:
                            # One settle + re-check before failing: async UI
                            # surfaces (UWP flyouts, start menu) commit the
                            # typed value AFTER the injection returns, so an
                            # immediate read-back sees stale state (live
                            # incident 2026-07-02 18:00).
                            await asyncio.sleep(0.3 * settle_scale)
                            landed = await verify_typed_text(action["text"])
                        if landed is False:
                            ok = False
                            detail = (
                                f"typed {action['text'][:40]!r} but it did NOT "
                                "land in any editable field — the input is not "
                                "focused. Click the target field first, then "
                                "type again."
                            )
                        elif landed is True:
                            detail = (
                                f"typed {action['text'][:40]!r} — confirmed in "
                                "the field"
                            )
                profiler.add("verify", t0, step_idx)

            elif kind == "key":
                ok, detail = await _dispatch_tool(
                    ctx,
                    "hotkey",
                    {
                        "keys": action["keys"],
                        "_expected_window_signature": captured_window_signature,
                    },
                    trace_id,
                )
                if ok:
                    ledger.record(action, frame.thumb)
                profiler.add("act", t0, step_idx)

            elif kind == "scroll":
                args: dict[str, Any] = {
                    "direction": action["direction"], "amount": action["amount"],
                    "x": int(action["x"]), "y": int(action["y"]),
                    "_expected_window_signature": captured_window_signature,
                }
                pointer_used = True
                ok, detail = await _dispatch_tool(ctx, "scroll", args, trace_id)
                if ok:
                    ledger.record(action, frame.thumb)
                profiler.add("act", t0, step_idx)

            elif kind == "drag":
                assert resolved_xy is not None
                pointer_used = True
                end_xy = frame.mapper.model_to_screen(
                    action["x2"], action["y2"], convention,
                )
                ok, detail = await _dispatch_tool(
                    ctx, "drag",
                    {
                        "x1": resolved_xy[0], "y1": resolved_xy[1],
                        "x2": end_xy[0], "y2": end_xy[1],
                        "duration_ms": action["duration_ms"],
                        "_expected_window_signature": captured_window_signature,
                    },
                    trace_id,
                )
                if ok:
                    ledger.record(action, frame.thumb)
                profiler.add("act", t0, step_idx)

            elif kind == "open_app":
                ok, detail = await _dispatch_tool(
                    ctx, "open_app", {"app_name": action["name"]}, trace_id,
                )
                if ok:
                    ledger.record(action, frame.thumb)
                    # The freshly focused window gets normalized before the
                    # next perception (maximize on its own monitor).
                    need_normalize = normalize_window
                    # Let the app paint its first window; the next perception's
                    # stability probe covers the rest.
                    await asyncio.sleep(1.0 * settle_scale)
                profiler.add("act", t0, step_idx)

            elif kind == "switch_window":
                ok, detail = await _dispatch_tool(
                    ctx, "switch_window", {"title_contains": action["name"]},
                    trace_id,
                )
                if ok:
                    ledger.record(action, frame.thumb)
                    need_normalize = normalize_window
                profiler.add("act", t0, step_idx)

            elif kind == "click_element":
                pointer_used = True
                ok, detail = await _dispatch_tool(
                    ctx,
                    "click_element",
                    {
                        "name": action["name"],
                        "_expected_window_signature": captured_window_signature,
                    },
                    trace_id,
                )
                if ok:
                    ledger.record(action, frame.thumb)
                profiler.add("act", t0, step_idx)

            elif kind == "wait":
                await asyncio.sleep(action["ms"] / 1000.0)
                ok, detail = True, f"waited {action['ms']} ms"
                profiler.add("settle", t0, step_idx)

            # -- bookkeeping ---------------------------------------------------
            batch_acted = True
            summary = _summarize_action(action)
            if ok:
                consecutive_failures = 0
                last_step_had_success = True
                history.append(
                    f"step {step_idx}: {summary} -> OK"
                    + (f" ({detail[:120]})" if detail else ""),
                )
                # Also INFO-log each step: the progress chunks live only in
                # the harness stdout, which made live per-step forensics
                # impossible (2026-07-02 x.com run: 15 opaque steps).
                log.info("[cu] step %d: %s -> OK %s", step_idx, summary,
                         detail[:120] if detail else "")
                yield _progress(f"[cu] step {step_idx}: {summary} ok")
            else:
                consecutive_failures += 1
                history.append(
                    f"step {step_idx}: {summary} -> FAILED: {detail[:180]}",
                )
                log.info("[cu] step %d: %s -> FAILED: %s", step_idx, summary,
                         detail[:180])
                yield _progress(f"[cu] step {step_idx}: {summary} FAILED")
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    reason = detail or "the last actions kept failing"
                    yield _final(
                        stderr=f"[cu] fail at step-{step_idx}: {reason}\n",
                        exit_code=_EXIT_TOOL,
                    )
                    return
                break  # a failed action truncates the batch -> re-perceive

    yield _final(
        stderr=(
            f"[cu] step budget exhausted after {step_idx} steps without "
            "verified completion\n"
        ),
        exit_code=_EXIT_BUDGET,
    )
