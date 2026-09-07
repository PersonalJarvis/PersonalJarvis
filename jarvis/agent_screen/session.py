"""A screen driven through a runner living inside the isolated session."""

from __future__ import annotations

import logging
from typing import Any

from jarvis.agent_screen.protocol import (
    ActOutcome,
    AgentScreenUnavailable,
    ForegroundInfo,
    RawFrame,
    ScreenKind,
    ScreenSession,
)
from jarvis.agent_screen.wire import PROTOCOL_VERSION, Transport, TransportError

logger = logging.getLogger(__name__)

#: Per-verb ceilings. A grab of a full desktop over the mailbox transport is
#: the slowest verb, and an ``act`` may legitimately wait on an app; both stay
#: below the engine's own ``_ACT_TIMEOUT_S`` so a stuck screen surfaces as a
#: named screen failure rather than an opaque tool timeout.
_TIMEOUT_FAST_S = 8.0
_TIMEOUT_GRAB_S = 15.0
_TIMEOUT_ACT_S = 12.0


class RemoteScreenSession(ScreenSession):
    """Talks to an in-session runner over one :class:`Transport`.

    Every degraded capability answers honestly rather than raising: a screen
    whose foreground is unreadable returns ``available=False`` (the engine
    then refuses unbound input, which is correct), and an absent
    accessibility channel returns the empty snapshot (the engine falls back to
    pixel grounding). Only a dead transport raises — that is a real outage
    and must not be mistaken for "the app has no buttons".
    """

    def __init__(
        self,
        *,
        screen_id: str,
        kind: ScreenKind,
        transport: Transport,
        owner: str = "",
        purpose: str = "",
        hidden: bool = True,
        on_close: Any = None,
    ) -> None:
        self.screen_id = screen_id
        self.kind = kind
        self.owner = owner
        self.purpose = purpose
        self.hidden = hidden
        self._transport = transport
        self._on_close = on_close
        self._closed = False
        self._geometry: tuple[int, int, int, int] | None = None

    # -- lifecycle ---------------------------------------------------------

    def handshake(self, *, timeout_s: float = 30.0) -> None:
        """Verify the runner is up and speaks our protocol version.

        Called once by the provider after boot. A version mismatch fails
        loudly here instead of producing subtly wrong pixels later — the
        Windows Sandbox runner is a separate implementation in another
        language, so drift between the two is a real risk, not a theoretical
        one.
        """
        envelope, _ = self._transport.call("health", timeout_s=timeout_s)
        version = int(envelope.get("v", 0) or 0)
        if version != PROTOCOL_VERSION:
            raise AgentScreenUnavailable(
                f"the screen runner speaks protocol v{version}, this build "
                f"needs v{PROTOCOL_VERSION}. Restart Personal Jarvis so the "
                "runner is re-deployed into the screen.",
            )
        if envelope.get("ok") is not True:
            raise AgentScreenUnavailable(
                "the screen runner reported itself unhealthy: "
                + str(envelope.get("error", "no reason given")),
            )

    def alive(self) -> bool:
        if self._closed:
            return False
        try:
            envelope, _ = self._transport.call("health", timeout_s=_TIMEOUT_FAST_S)
        except TransportError:
            return False
        return envelope.get("ok") is True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._transport.call("quit", timeout_s=3.0)
        except TransportError:
            logger.debug(
                "[agent-screen] %s: quit verb unanswered (already gone)",
                self.screen_id,
            )
        except Exception:  # noqa: BLE001 — teardown must never raise
            logger.debug("[agent-screen] %s: quit failed", self.screen_id, exc_info=True)
        try:
            self._transport.close()
        except Exception:  # noqa: BLE001
            logger.debug("[agent-screen] transport close failed", exc_info=True)
        if callable(self._on_close):
            try:
                self._on_close()
            except Exception:  # noqa: BLE001 — provider teardown is its own concern
                logger.warning(
                    "[agent-screen] %s: provider teardown failed",
                    self.screen_id,
                    exc_info=True,
                )

    # -- perception --------------------------------------------------------

    def geometry(self) -> tuple[int, int, int, int]:
        """Screen rect, cached: an isolated screen cannot be re-plugged.

        The user's physical desktop can gain or lose a monitor mid-mission,
        which is why the engine re-reads its topology; an agent screen has
        exactly one fixed framebuffer for its lifetime, so re-asking every
        step would only add round-trips.
        """
        if self._geometry is None:
            envelope, _ = self._transport.call("geometry", timeout_s=_TIMEOUT_FAST_S)
            self._geometry = (
                int(envelope.get("left", 0) or 0),
                int(envelope.get("top", 0) or 0),
                int(envelope.get("width", 0) or 0),
                int(envelope.get("height", 0) or 0),
            )
            if self._geometry[2] <= 0 or self._geometry[3] <= 0:
                raise AgentScreenUnavailable(
                    f"screen {self.screen_id} reported an empty framebuffer "
                    f"({self._geometry[2]}x{self._geometry[3]})",
                )
        return self._geometry

    def grab(self, bbox: dict[str, int], *, rgb: bool = False) -> RawFrame:
        envelope, blob = self._transport.call(
            "grab",
            {
                "left": int(bbox["left"]),
                "top": int(bbox["top"]),
                "width": int(bbox["width"]),
                "height": int(bbox["height"]),
                "rgb": bool(rgb),
            },
            timeout_s=_TIMEOUT_GRAB_S,
        )
        width = int(envelope.get("width", 0) or 0)
        height = int(envelope.get("height", 0) or 0)
        pixel_format = str(envelope.get("format", "RGB") or "RGB").upper()
        if pixel_format not in ("RGB", "BGRX"):
            raise AgentScreenUnavailable(
                f"screen {self.screen_id} sent an unknown pixel format "
                f"{pixel_format!r}",
            )
        expected = width * height * (3 if pixel_format == "RGB" else 4)
        if width <= 0 or height <= 0 or len(blob) != expected:
            raise AgentScreenUnavailable(
                f"screen {self.screen_id} sent a truncated frame: "
                f"{len(blob)} bytes for {width}x{height} {pixel_format} "
                f"(expected {expected})",
            )
        return RawFrame(
            width=width,
            height=height,
            pixel_format=pixel_format,  # type: ignore[arg-type]
            data=blob,
        )

    def foreground(self) -> ForegroundInfo:
        try:
            envelope, _ = self._transport.call(
                "foreground",
                timeout_s=_TIMEOUT_FAST_S,
            )
        except TransportError:
            # A dead transport is a dead screen; the caller's next verb will
            # raise. Reporting "no foreground" keeps the fail-closed contract
            # (the engine refuses unbound input) without masking the outage.
            logger.debug(
                "[agent-screen] %s: foreground read failed",
                self.screen_id,
                exc_info=True,
            )
            return ForegroundInfo(available=False)
        if envelope.get("available") is not True:
            return ForegroundInfo(available=False)
        raw_rect = envelope.get("rect")
        rect: tuple[int, int, int, int] | None = None
        if isinstance(raw_rect, (list, tuple)) and len(raw_rect) == 4:
            rect = (
                int(raw_rect[0]),
                int(raw_rect[1]),
                int(raw_rect[2]),
                int(raw_rect[3]),
            )
        handle_raw = envelope.get("handle")
        return ForegroundInfo(
            available=True,
            title=str(envelope.get("title", "") or ""),
            app=str(envelope.get("app", "") or ""),
            handle=int(handle_raw) if handle_raw else None,
            rect=rect,
        )

    def ui_snapshot(self) -> tuple[list[str], str, str | None, list[Any]]:
        """Accessibility snapshot from inside the screen, empty when absent.

        The runner answers ``supported: false`` on a session without an
        accessibility bridge. That empty answer is a feature: the engine then
        grounds purely on pixels, which is exactly right for a fresh sandbox,
        and no code has to special-case the screen kind.
        """
        try:
            envelope, _ = self._transport.call("ui_snapshot", timeout_s=_TIMEOUT_FAST_S)
        except TransportError:
            return ([], "", None, [])
        if envelope.get("supported") is not True:
            return ([], "", None, [])
        labels = [str(x) for x in (envelope.get("labels") or []) if str(x)]
        hint = str(envelope.get("field_hint", "") or "")
        handoff_raw = envelope.get("handoff")
        handoff = str(handoff_raw) if handoff_raw else None
        rects: list[Any] = []
        for entry in envelope.get("clickables") or []:
            if not isinstance(entry, (list, tuple)) or len(entry) != 3:
                continue
            bounds = entry[2]
            if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
                continue
            rects.append(
                (
                    str(entry[0]),
                    str(entry[1]),
                    (
                        int(bounds[0]),
                        int(bounds[1]),
                        int(bounds[2]),
                        int(bounds[3]),
                    ),
                ),
            )
        return (labels, hint, handoff, rects)

    def typed_text_landed(self, typed: str) -> bool | None:
        try:
            envelope, _ = self._transport.call(
                "typed_text_landed",
                {"text": str(typed)},
                timeout_s=_TIMEOUT_FAST_S,
            )
        except TransportError:
            return None
        verdict = envelope.get("landed")
        return verdict if isinstance(verdict, bool) else None

    def click_landed_in_focus(
        self,
        x: int,
        y: int,
        *,
        capture_area: int | None = None,
    ) -> bool | None:
        try:
            envelope, _ = self._transport.call(
                "click_landed_in_focus",
                {
                    "x": int(x),
                    "y": int(y),
                    "capture_area": int(capture_area or 0),
                },
                timeout_s=_TIMEOUT_FAST_S,
            )
        except TransportError:
            return None
        verdict = envelope.get("focused")
        return verdict if isinstance(verdict, bool) else None

    # -- actuation ---------------------------------------------------------

    def act(self, action: str, params: dict[str, Any]) -> ActOutcome:
        try:
            envelope, _ = self._transport.call(
                "act",
                {"action": str(action), "params": dict(params)},
                timeout_s=_TIMEOUT_ACT_S,
            )
        except TransportError as exc:
            return ActOutcome(ok=False, detail=str(exc))
        return ActOutcome(
            ok=envelope.get("ok") is True,
            detail=str(envelope.get("detail", "") or envelope.get("error", "") or ""),
        )


__all__ = ["RemoteScreenSession"]
