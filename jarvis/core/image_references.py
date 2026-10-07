"""Scoped, short-lived image capabilities for an explicitly selected work order.

Pixels remain in RAM until an authorized handoff copies the selected images.
There is deliberately no latest-image lookup and no arbitrary path/URL loader.
"""

from __future__ import annotations

import base64
import hashlib
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4

MAX_IMAGES = 20
MAX_BYTES = 25 * 1024 * 1024
TTL_SECONDS = 120.0
#: A picture handed to a live call stays in that call's model context until the
#: call ends, so its reference must live as long or the model sees an image it
#: cannot forward. The call's close clears the scope; this bound only expires a
#: scope whose call never closed cleanly.
LIVE_CALL_TTL_SECONDS = 3600.0
active_scope: ContextVar[str] = ContextVar("visual_reference_scope", default="")
_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class ImageReferenceError(ValueError):
    """Nothing may be sent when a selected image is unavailable."""


@dataclass(frozen=True, slots=True)
class ReferencedImage:
    id: str
    data: bytes
    mime: str
    source: str
    sha256: str

    @property
    def name(self) -> str:
        return self.id + _EXTENSIONS[self.mime]


def scope_for(config: dict | None = None, trace_id: object = "") -> str:
    """Trusted conversation identity, never a model-supplied argument."""
    from jarvis.core.protocols import current_chat_turn

    turn = current_chat_turn.get()
    if turn is not None:
        return "chat:" + turn.session_id
    config = config or {}
    if config.get("live_session_id"):
        return "live:" + str(config["live_session_id"])
    if config.get("approval_ref", "").startswith("agent-chat:"):
        return "chat:" + config["approval_ref"].removeprefix("agent-chat:")
    return str(
        config.get("image_reference_scope") or active_scope.get() or ("turn:" + str(trace_id))
    )


def ttl_for(scope: str, config: object = None) -> float:
    """Reference lifetime: the capture budget, or the whole call for a live scope."""
    ttl = float(getattr(getattr(config, "screen_context", None), "ttl_s", TTL_SECONDS))
    return max(ttl, LIVE_CALL_TTL_SECONDS) if scope.startswith("live:") else ttl


def appshot_context(session_id: str, image: bytes, mime: str, config: object) -> str:
    scope = "live:" + session_id
    ref = get_store().add(scope, image, mime, source="appshot", ttl_s=ttl_for(scope, config))
    return instruction([ref])


def instruction(refs: list[str]) -> str:
    if not refs:
        return ""
    return (
        "Visual reference IDs (same order as the attached images): " + ", ".join(refs) + ". "
        "When handing a task based on these images to workspace-orchestrate create, "
        "open_workspace or send, include the relevant IDs in image_refs. Select only "
        "images used by that specific task; never substitute a description or unrelated "
        "older screenshots. References expire; report unavailability honestly."
    )


class ImageReferences:
    def __init__(self, *, clock=time.monotonic, schedule_expiry: bool = True) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[str, float, ReferencedImage]] = {}
        self._schedule_expiry = schedule_expiry
        self._timer: threading.Timer | None = None
        self._expiry_at = 0.0

    def _expire(self) -> None:
        now = self._clock()
        for key in [k for k, (_, until, _) in self._entries.items() if until <= now]:
            del self._entries[key]

    def _arm_expiry(self) -> None:
        """One daemon timer per store, so idle conversations do not retain pixels."""
        if not self._schedule_expiry or not self._entries:
            return
        deadline = min(row[1] for row in self._entries.values())
        if self._timer is not None and self._expiry_at <= deadline:
            return
        if self._timer is not None:
            self._timer.cancel()
        self._expiry_at = deadline
        self._timer = threading.Timer(max(0.001, deadline - self._clock()), self._on_expiry)
        self._timer.daemon = True
        self._timer.start()

    def _on_expiry(self) -> None:
        with self._lock:
            if self._timer is not threading.current_thread():
                return  # A replacement or clear cancelled this timer while it waited.
            self._timer = None
            self._expire()
            self._arm_expiry()

    def add(
        self, scope: str, data: bytes, mime: str, *, source: str, ttl_s: float = TTL_SECONDS
    ) -> str:
        if not scope or mime not in _EXTENSIONS or not data or len(data) > MAX_BYTES:
            raise ImageReferenceError(
                "The image cannot be handed off (unsupported or empty image)."
            )
        with self._lock:
            self._expire()
            # A bounded RAM cache. Evicted IDs fail closed, never refer to another image.
            while self._entries and (
                len(self._entries) >= 100
                or sum(len(row[2].data) for row in self._entries.values()) + len(data)
                > 100 * 1024 * 1024
            ):
                del self._entries[next(iter(self._entries))]
            key = "img_" + uuid4().hex
            image = ReferencedImage(key, data, mime, source, hashlib.sha256(data).hexdigest())
            self._entries[key] = (scope, self._clock() + max(0, ttl_s), image)
            self._arm_expiry()
            return key

    def add_blocks(self, scope: str, images: tuple, *, source: str) -> list[str]:
        refs = []
        for image in images:
            try:
                data = base64.b64decode(image.data_b64, validate=True)
            except ValueError as exc:
                raise ImageReferenceError("The attached image could not be decoded.") from exc
            refs.append(self.add(scope, data, image.mime, source=source))
        return refs

    def resolve(self, scope: str, refs: object) -> tuple[ReferencedImage, ...]:
        if (
            not isinstance(refs, list)
            or len(refs) > MAX_IMAGES
            or any(not isinstance(r, str) for r in refs)
        ):
            raise ImageReferenceError("image_refs must be a list of at most 20 image IDs.")
        with self._lock:
            self._expire()
            images = []
            for key in dict.fromkeys(refs):
                row = self._entries.get(key)
                if row is None or row[0] != scope:
                    raise ImageReferenceError(
                        "A selected image is expired, missing or belongs to another conversation. "
                        "Nothing was sent. Ask for the image again "
                        "or take a new authorized appshot."
                    )
                images.append(row[2])
            return tuple(images)

    def clear_scope(self, scope: str) -> None:
        with self._lock:
            for key in [k for k, row in self._entries.items() if row[0] == scope]:
                del self._entries[key]
            if not self._entries and self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def available(self, scope: str) -> list[dict[str, str]]:
        with self._lock:
            self._expire()
            return [
                {"id": image.id, "source": image.source}
                for owner, _, image in self._entries.values()
                if owner == scope
            ]


_STORE = ImageReferences()


def get_store() -> ImageReferences:
    return _STORE
