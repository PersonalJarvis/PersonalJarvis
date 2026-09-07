"""Vocabulary for agent screens — a screen the AGENT owns, not the user's.

The load-bearing insight of this whole subsystem, stated once so no later
change can quietly violate it:

    One login session has ONE visible desktop, ONE pointer and ONE input
    stream. Synthetic input (``SendInput`` / XTest / Quartz) is injected into
    the input stream of ITS OWN session — it cannot be aimed at a background
    window of the SAME session. Therefore "the agent gets its own screen" can
    only mean "the agent gets its own SESSION": a Windows Sandbox, a virtual
    X display, a VM. It can never mean "a hidden window on this desktop".

Everything here follows from that. A :class:`ScreenSession` is a session with
a framebuffer and an input stream that Jarvis may drive without touching the
user's desktop; a :class:`ScreenProvider` knows how to create one on this host
and — just as importantly — how to say honestly that it cannot.

Cross-platform contract (repo baseline rule): every provider is selected by a
runtime capability probe, never by an OS allowlist in a caller. A host that
cannot provide an invisible screen must degrade to a NAMED reason plus an
actionable remedy, never to a silent fallback onto the user's real screen —
silently borrowing the physical pointer is exactly the failure this subsystem
exists to prevent.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Literal

#: Wire-level pixel layouts a screen may hand back. ``BGRX`` is the native
#: layout of both mss (Windows/Linux) and GDI ``LockBits``, so it travels
#: without a conversion pass; ``RGB`` is the packed 3-byte form the public
#: :class:`jarvis.cu.capture.Grabber` contract uses.
PixelFormat = Literal["RGB", "BGRX"]

#: What kind of session a screen lives in. ``real`` is the user's physical
#: desktop; every other value is an isolated session the user never sees.
ScreenKind = Literal["real", "windows-sandbox", "xvfb", "attached"]


class AgentScreenUnavailable(RuntimeError):
    """No isolated screen can be created on this host.

    The message is user-actionable and English (artifact rule); spoken
    readbacks localize separately. Raised instead of falling back to the
    user's real screen: a subagent that cannot get its own screen must stop
    honestly, not take over the desktop the user is working on.
    """


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Whether one provider can run here, and what to do when it cannot.

    ``remedy`` is deliberately a concrete instruction (a command to run, a
    feature to enable), because "not available" without a next step is the
    honesty failure this repo keeps fixing elsewhere.
    """

    kind: ScreenKind
    available: bool
    reason: str = ""
    remedy: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "available": self.available,
            "reason": self.reason,
            "remedy": self.remedy,
        }


@dataclass(frozen=True, slots=True)
class ScreenSpec:
    """What a caller wants from a screen.

    ``owner`` identifies the errand/mission/worker the screen belongs to, so
    a second request from the SAME owner reuses its screen instead of booting
    a second session (an errand that runs ten desktop steps must not pay ten
    sandbox boots).
    """

    owner: str
    purpose: str = ""
    width: int = 1280
    height: int = 800


@dataclass(frozen=True, slots=True)
class ForegroundInfo:
    """The foreground window INSIDE a screen, in that screen's input units.

    ``available=False`` means the screen cannot currently name its foreground
    window. The Computer-Use engine treats that as "refuse unbound input",
    which is the correct fail-closed behaviour on every screen kind: without a
    window identity there is no way to prove the click lands where the model
    looked.
    """

    available: bool
    title: str = ""
    app: str = ""
    handle: int | None = None
    rect: tuple[int, int, int, int] | None = None

    def signature(self) -> tuple[Any, ...]:
        """Identity tuple in the shape :mod:`jarvis.cu.target_guard` produces.

        Keeping the SHAPE identical (``("handle", id, rect)`` /
        ``("none",)``) means the engine's capture-to-action race checks work
        unchanged on an agent screen — same comparison, same same-app
        tolerance helper, no parallel implementation to drift.
        """
        if not self.available:
            return ("none",)
        if self.handle:
            return ("handle", int(self.handle), self.rect)
        return ("title", str(self.title or "").casefold(), self.rect)


@dataclass(frozen=True, slots=True)
class ActOutcome:
    """Result of one input primitive dispatched into a screen."""

    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RawFrame:
    """Raw pixels straight out of a screen, before any downscale/encode.

    Deliberately NOT a Pillow image: the Computer-Use capture pipeline
    (stability loop, thumbnail identity, one-mapper-per-frame downscale) is
    the valuable part and stays shared. A screen only supplies pixels.
    """

    width: int
    height: int
    pixel_format: PixelFormat
    data: bytes

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)


class ScreenSession(abc.ABC):
    """One live isolated screen: a framebuffer plus an input stream.

    Implementations must be safe to call from worker threads (the CU engine
    reaches them through ``asyncio.to_thread``) and must never raise on a
    merely-degraded capability — an absent accessibility tree returns empty,
    an unreadable foreground returns ``ForegroundInfo(available=False)``.
    Only a genuinely dead screen raises :class:`AgentScreenUnavailable`.
    """

    #: Stable id, unique per process. Used by the REST surface, the events,
    #: and the per-screen actuation lock.
    screen_id: str
    kind: ScreenKind
    #: The owner that acquired it (errand id, mission id, "user", ...).
    owner: str = ""
    purpose: str = ""
    #: Whether the screen draws NOTHING on the user's display. Isolation and
    #: invisibility are two different promises: a sandbox started through the
    #: classic launcher is fully isolated (its input never reaches the user's
    #: session) yet still shows a viewer window. Callers that promised the
    #: user "you won't see it" must check this, not :attr:`isolated`.
    hidden: bool = True

    @property
    def is_real(self) -> bool:
        """Whether driving this screen moves the USER's physical pointer."""
        return self.kind == "real"

    @property
    def isolated(self) -> bool:
        """Whether the user can neither see nor feel work done here.

        The gate a subagent must pass. ``attached`` is deliberately excluded
        even though it speaks the same wire protocol: it runs a runner in the
        user's OWN session, which makes it a faithful end-to-end test of the
        transport and an honest local fallback, but never an invisible one.
        """
        return self.kind not in ("real", "attached")

    @abc.abstractmethod
    def geometry(self) -> tuple[int, int, int, int]:
        """Screen rect ``(left, top, width, height)`` in its input units."""

    @abc.abstractmethod
    def grab(self, bbox: dict[str, int], *, rgb: bool = False) -> RawFrame:
        """Grab a rect. ``rgb=True`` forces the packed 3-byte layout.

        Callers that only compare bytes (the pre/post visual probes) ask for
        ``rgb`` so the comparison never sees a backend's padding byte;
        callers that feed the downscale pipeline accept the native layout and
        save a full-desktop conversion per stability re-grab.
        """

    @abc.abstractmethod
    def foreground(self) -> ForegroundInfo:
        """The screen's current foreground window."""

    @abc.abstractmethod
    def act(self, action: str, params: dict[str, Any]) -> ActOutcome:
        """Dispatch one input primitive into this screen.

        The action vocabulary mirrors the Computer-Use tool names exactly
        (``click``, ``type_text``, ``hotkey``, ``scroll``, ``drag``,
        ``open_app``, ``switch_window``) so a screen-bound tool is a thin
        adapter and no second grammar exists.
        """

    def typed_text_landed(self, typed: str) -> bool | None:
        """Did ``typed`` land in a focused field? ``None`` = cannot tell.

        Default ``None``: a screen without an accessibility channel must NOT
        pretend a read-back succeeded (that would turn strict verification
        into a rubber stamp) and must not report failure either (that would
        fail healthy typing on a canvas app).
        """
        return None

    def click_landed_in_focus(
        self,
        x: int,
        y: int,
        *,
        capture_area: int | None = None,
    ) -> bool | None:
        """Is ``(x, y)`` inside the focused control? ``None`` = cannot tell."""
        return None

    def ui_snapshot(self) -> tuple[list[str], str, str | None, list[Any]]:
        """``(clickable labels, field hint, handoff reason, clickable rects)``.

        The empty tuple is a first-class answer: the engine self-gates back to
        pure pixel grounding when a screen exposes no tree, which is exactly
        what a fresh sandbox without an accessibility bridge should do.
        """
        return ([], "", None, [])

    def alive(self) -> bool:
        """Cheap liveness check; ``False`` once the session died."""
        return True

    @abc.abstractmethod
    def close(self) -> None:
        """Tear the screen down. Must be idempotent and never raise."""


class ScreenProvider(abc.ABC):
    """Knows how to create one kind of isolated screen on this host."""

    kind: ScreenKind

    @abc.abstractmethod
    def probe(self) -> ProbeResult:
        """Can this provider run here right now? Never raises."""

    @abc.abstractmethod
    def start(self, spec: ScreenSpec) -> ScreenSession:
        """Boot a screen. Raises :class:`AgentScreenUnavailable` on failure."""


@dataclass(slots=True)
class ScreenLease:
    """A borrowed screen plus the release handle.

    Leases are refcounted per owner by :class:`~jarvis.agent_screen.manager`
    so overlapping desktop steps of one errand share one session and the
    screen dies exactly once, when the last lease is released.
    """

    session: ScreenSession
    owner: str
    _released: bool = field(default=False, repr=False)

    @property
    def screen_id(self) -> str:
        return self.session.screen_id


__all__ = [
    "ActOutcome",
    "AgentScreenUnavailable",
    "ForegroundInfo",
    "PixelFormat",
    "ProbeResult",
    "RawFrame",
    "ScreenKind",
    "ScreenLease",
    "ScreenProvider",
    "ScreenSession",
    "ScreenSpec",
]
