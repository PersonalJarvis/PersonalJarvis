"""The seam the Computer-Use engine perceives and verifies through.

The engine's value is its loop — stable frames, one mapper per frame, the
idempotency ledger, effect verification, the zoom-refine rescue, the handoff
detector, the prompt-injection defence. None of that is screen-specific, and
none of it is duplicated here. What IS screen-specific is a short list of
primitives: where to capture, how to capture, who is in the foreground, and
what the accessibility layer says. Those are the port.

:class:`RealScreenPort` forwards every one of them to the functions the
engine called before this seam existed, so the physical-screen behaviour is
unchanged by construction rather than by careful re-implementation.
:class:`RemoteScreenPort` answers the same questions for an isolated screen.

The rule this file exists to enforce: on an agent screen, NOTHING may read or
write the user's session. Two verification paths make that easy to get wrong
— the typed-text read-back and the click-focus hit-test both walk the local
accessibility tree — so both are routed here explicitly. A port that forgot
them would verify the agent's typing against the user's screen and quietly
report nonsense.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Protocol

from jarvis.cu.capture import (
    Frame,
    Grabber,
    RawCapture,
    VisualProbe,
    capture_stable_frame,
    grab_region,
    grab_visual_probe,
    raw_pixels,
    select_capture_target,
)
from jarvis.cu.geometry import MonitorInfo
from jarvis.cu.target_guard import ForegroundTarget

logger = logging.getLogger(__name__)


class ScreenPort(Protocol):
    """What the Computer-Use engine needs from whatever screen it drives."""

    kind: str
    is_real: bool

    def wayland_refusal(self) -> str | None: ...

    def select_all_keys(self) -> list[str]: ...

    def list_monitors(self) -> list[MonitorInfo]: ...

    def select_capture_target(
        self,
        policy: str,
        *,
        main_monitor: str,
        scope: str,
    ) -> MonitorInfo: ...

    def capture_stable_frame(
        self,
        monitor: MonitorInfo,
        *,
        max_dimension: int,
        blob_dir: Path | None,
        capture_guard: Any = None,
    ) -> Frame: ...

    def read_foreground_target(self) -> ForegroundTarget: ...

    def foreground_matches_or_same_app(self, expected: tuple[Any, ...]) -> bool: ...

    def foreground_title(self) -> str: ...

    def normalize_foreground_window(self) -> tuple[bool, str]: ...

    def grab_visual_probe(
        self,
        bbox: dict[str, int],
        *,
        point: tuple[int, int] | None = None,
        radius: int = 110,
        local_only: bool = False,
    ) -> VisualProbe | None: ...

    def grab_region(self, bbox: dict[str, int]) -> RawCapture | None: ...

    async def ui_snapshot(
        self,
        *,
        observation_guard: Any = None,
    ) -> tuple[list[str], str, str | None, list[Any]]: ...

    async def verify_typed_text(self, text: str) -> bool | None: ...

    async def verify_click_focus_point(
        self,
        x: int,
        y: int,
        *,
        capture_area: int | None = None,
    ) -> bool | None: ...


class RealScreenPort:
    """The user's physical desktop — the pre-existing behaviour, verbatim."""

    kind = "real"
    is_real = True

    def wayland_refusal(self) -> str | None:
        try:
            from jarvis.platform.probes import is_wayland  # noqa: PLC0415

            if is_wayland():
                return (
                    "cannot run on a Wayland session: Wayland blocks global "
                    "screen capture and synthetic input by design. Log into an "
                    "X11 session instead (running single apps under XWayland "
                    "is not enough — the desktop session itself must be X11)."
                )
        except Exception:  # noqa: BLE001 — an unreadable probe is not a refusal
            logger.debug("[cu] wayland probe failed", exc_info=True)
            return None
        return None

    def select_all_keys(self) -> list[str]:
        return ["cmd", "a"] if sys.platform == "darwin" else ["ctrl", "a"]

    def list_monitors(self) -> list[MonitorInfo]:
        from jarvis.cu.geometry import list_monitors  # noqa: PLC0415

        return list_monitors()

    def select_capture_target(
        self,
        policy: str,
        *,
        main_monitor: str,
        scope: str,
    ) -> MonitorInfo:
        return select_capture_target(policy, main_monitor=main_monitor, scope=scope)

    def capture_stable_frame(
        self,
        monitor: MonitorInfo,
        *,
        max_dimension: int,
        blob_dir: Path | None,
        capture_guard: Any = None,
    ) -> Frame:
        return capture_stable_frame(
            monitor,
            max_dimension=max_dimension,
            blob_dir=blob_dir,
            capture_guard=capture_guard,
        )

    def read_foreground_target(self) -> ForegroundTarget:
        from jarvis.cu.target_guard import read_foreground_target  # noqa: PLC0415

        return read_foreground_target()

    def foreground_matches_or_same_app(self, expected: tuple[Any, ...]) -> bool:
        from jarvis.cu.target_guard import (  # noqa: PLC0415
            foreground_matches_or_same_app,
        )

        return foreground_matches_or_same_app(expected)

    def foreground_title(self) -> str:
        try:
            from jarvis.platform import window_state  # noqa: PLC0415

            return str(window_state.get_foreground_title() or "")
        except Exception:  # noqa: BLE001 — a title is decoration, never a gate
            logger.debug("[cu] foreground title read failed", exc_info=True)
            return ""

    def normalize_foreground_window(self) -> tuple[bool, str]:
        from jarvis.platform import window_state  # noqa: PLC0415

        return window_state.normalize_foreground_window()

    def grab_visual_probe(
        self,
        bbox: dict[str, int],
        *,
        point: tuple[int, int] | None = None,
        radius: int = 110,
        local_only: bool = False,
    ) -> VisualProbe | None:
        return grab_visual_probe(
            bbox,
            point=point,
            radius=radius,
            local_only=local_only,
        )

    def grab_region(self, bbox: dict[str, int]) -> RawCapture | None:
        return grab_region(bbox)

    async def ui_snapshot(
        self,
        *,
        observation_guard: Any = None,
    ) -> tuple[list[str], str, str | None, list[Any]]:
        from jarvis.cu.verify import foreground_ui_snapshot  # noqa: PLC0415

        return await foreground_ui_snapshot(observation_guard=observation_guard)

    async def verify_typed_text(self, text: str) -> bool | None:
        from jarvis.cu.verify import verify_typed_text  # noqa: PLC0415

        return await verify_typed_text(text)

    async def verify_click_focus_point(
        self,
        x: int,
        y: int,
        *,
        capture_area: int | None = None,
    ) -> bool | None:
        from jarvis.cu.verify import verify_click_focus_point  # noqa: PLC0415

        return await verify_click_focus_point(x, y, capture_area=capture_area)


class RemoteScreenPort:
    """An isolated agent screen, reached through its in-session runner."""

    is_real = False

    def __init__(self, session: Any) -> None:
        self._session = session
        self.kind = str(session.kind)

    # -- capability --------------------------------------------------------

    def wayland_refusal(self) -> str | None:
        """Never: the screen's own session decided this before it booted.

        The host may well be a Wayland desktop while the agent screen is an
        Xvfb display or a Windows sandbox. Refusing here would block exactly
        the configuration that solves the problem.
        """
        return None

    def select_all_keys(self) -> list[str]:
        """Select-all inside the SCREEN, not on the host.

        A Windows sandbox is Windows and a virtual X display is Linux, whoever
        the host happens to be — sending ``cmd+a`` from a Mac host into a
        sandbox would simply do nothing, and the mission would blame the app.
        """
        return ["ctrl", "a"]

    # -- geometry ----------------------------------------------------------

    def _screen_monitor(self) -> MonitorInfo:
        left, top, width, height = self._session.geometry()
        return MonitorInfo(
            left=left,
            top=top,
            width=width,
            height=height,
            is_primary=True,
            name=f"agent-screen:{self._session.screen_id}",
        )

    def list_monitors(self) -> list[MonitorInfo]:
        return [self._screen_monitor()]

    def select_capture_target(
        self,
        policy: str,
        *,
        main_monitor: str,
        scope: str,
    ) -> MonitorInfo:
        """Always the whole screen.

        Window-scoped framing exists because a small window floating on a big
        physical desktop shrinks to stamp size in the downscaled frame. An
        agent screen is sized for the job (1280x800 by default), so its whole
        framebuffer already IS the tight frame — and cropping to a window
        would need per-window geometry the guest reports only for the
        foreground, adding a failure mode for no grounding gain.
        """
        return self._screen_monitor()

    # -- perception --------------------------------------------------------

    def _grabber(self, *, rgb: bool = False) -> Grabber:
        session = self._session

        def grab(bbox: dict[str, int]) -> RawCapture:
            frame = session.grab(bbox, rgb=rgb)
            return raw_pixels(
                frame.size,
                frame.data,
                pixel_format=frame.pixel_format,
            )

        return grab

    def capture_stable_frame(
        self,
        monitor: MonitorInfo,
        *,
        max_dimension: int,
        blob_dir: Path | None,
        capture_guard: Any = None,
    ) -> Frame:
        return capture_stable_frame(
            monitor,
            max_dimension=max_dimension,
            blob_dir=blob_dir,
            capture_guard=capture_guard,
            grab=self._grabber(),
        )

    def read_foreground_target(self) -> ForegroundTarget:
        info = self._session.foreground()
        rect = info.rect
        return ForegroundTarget(
            window=None,
            rect=rect,
            signature=info.signature(),
        )

    def foreground_matches_or_same_app(self, expected: tuple[Any, ...]) -> bool:
        if not expected or expected[0] == "none":
            return False
        return self.read_foreground_target().signature == expected

    def foreground_title(self) -> str:
        return str(self._session.foreground().title or "")

    def normalize_foreground_window(self) -> tuple[bool, str]:
        """Nothing to normalise: the screen is exactly the size of the job."""
        return (False, "agent screens are not resized")

    def grab_visual_probe(
        self,
        bbox: dict[str, int],
        *,
        point: tuple[int, int] | None = None,
        radius: int = 110,
        local_only: bool = False,
    ) -> VisualProbe | None:
        # Packed RGB for the probe path: the local crop is compared BYTE for
        # byte, and a backend's unused fourth byte must never masquerade as a
        # visual effect.
        return grab_visual_probe(
            bbox,
            point=point,
            radius=radius,
            local_only=local_only,
            grab=self._grabber(rgb=True),
        )

    def grab_region(self, bbox: dict[str, int]) -> RawCapture | None:
        # The zoom-refine crop is JPEG-encoded from a packed RGB tuple by the
        # engine, so hand it that shape directly.
        return grab_region(bbox, grab=self._grabber(rgb=True))

    async def ui_snapshot(
        self,
        *,
        observation_guard: Any = None,
    ) -> tuple[list[str], str, str | None, list[Any]]:
        if observation_guard is not None and not observation_guard():
            raise RuntimeError("foreground window changed before UI-tree observation")
        snapshot = await asyncio.to_thread(self._session.ui_snapshot)
        if observation_guard is not None and not observation_guard():
            raise RuntimeError("foreground window changed during UI-tree observation")
        return snapshot

    async def verify_typed_text(self, text: str) -> bool | None:
        return await asyncio.to_thread(self._session.typed_text_landed, text)

    async def verify_click_focus_point(
        self,
        x: int,
        y: int,
        *,
        capture_area: int | None = None,
    ) -> bool | None:
        return await asyncio.to_thread(
            lambda: self._session.click_landed_in_focus(
                x,
                y,
                capture_area=capture_area,
            ),
        )


def resolve_port(ctx: Any) -> ScreenPort:
    """The port a Computer-Use context should drive.

    Defaults to the physical screen so every existing caller, test and rig
    keeps its exact behaviour without knowing this seam exists.
    """
    port = getattr(ctx, "screen_port", None)
    if port is not None:
        return port  # type: ignore[no-any-return]
    return RealScreenPort()


__all__ = [
    "RealScreenPort",
    "RemoteScreenPort",
    "ScreenPort",
    "resolve_port",
]
