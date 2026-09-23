"""One warm local model and one conversation owner for the application.

Readiness here means tested AUDIO, not full Jarvis qualification. Tool/language
acceptance remains a separate gate before selecting this as the default voice.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .events import NativeAudioError
from .launch import NativeLaunchPlan
from .process import NativeAudioProcess

log = logging.getLogger(__name__)


class LocalVoiceRuntime:
    def __init__(
        self,
        directory: Path,
        *,
        process_factory: Callable[..., Any] = NativeAudioProcess,
    ) -> None:
        self._directory = directory
        self._factory = process_factory
        self._operation = threading.Lock()
        self._plan: NativeLaunchPlan | None = None
        self._worker: Any = None
        self._owner = ""
        self._phase = "stopped"
        self._error = ""

    def snapshot(self) -> dict[str, Any]:
        running = self._worker is not None and self._worker.usable
        phase = self._phase
        if phase in {"audio_ready", "in_use"} and not running:
            phase = "failed"
        return {
            "phase": phase,
            "model_id": self._plan.model_id if self._plan else "",
            "audio_ready": phase in {"audio_ready", "in_use"} and running,
            "conversation_active": bool(self._owner),
            "jarvis_qualified": False,
            "error": self._error
            or ("The native model stopped unexpectedly." if phase == "failed" else ""),
        }

    async def _load(self, plan: NativeLaunchPlan) -> Any:
        worker = self._factory(plan.command, self._directory, expected_revision=plan.revision)
        try:
            await worker.start()
            audio = False
            completed = False
            async for event in worker.generate(
                text="Ready.",
                instructions="Perform TTS. Use the US female voice.",
                output_mode="audio",
                max_tokens=128,
            ):
                audio = audio or (event.kind == "audio" and bool(event.pcm))
                completed = completed or event.kind == "done"
            if not audio or not completed:
                raise NativeAudioError("The model loaded but did not pass its speech test.")
            return worker
        except BaseException:
            await worker.close()
            raise

    async def _warm_locked(self, plan: NativeLaunchPlan) -> None:
        if self._owner:
            if (
                self._plan
                and self._worker is not None
                and plan.identity == self._plan.identity
                and self._worker.usable
            ):
                return
            raise NativeAudioError("End the current voice conversation before changing its model.")
        if (
            self._plan
            and self._worker is not None
            and plan.identity == self._plan.identity
            and self._worker.usable
        ):
            return
        previous = self._plan
        self._phase = "loading"
        self._error = ""
        try:
            if self._worker is not None:
                await self._worker.close()
            self._worker = None
            self._worker = await self._load(plan)
            self._plan = plan
            self._phase = "audio_ready"
        except BaseException:
            self._worker = None
            self._phase = "failed"
            self._error = "The selected model failed its speech test."
            if previous is not None and previous.identity != plan.identity:
                try:
                    self._worker = await self._load(previous)
                    self._plan = previous
                    self._phase = "audio_ready"
                    self._error = "The selected model failed; the previous model was restored."
                except Exception:
                    log.exception("Native model rollback did not become ready")
                    self._error = (
                        "The selected model failed and the previous model could not restart."
                    )
            raise

    async def warm(self, plan: NativeLaunchPlan) -> dict[str, Any]:
        if not self._operation.acquire(blocking=False):
            raise NativeAudioError("A native model operation is already in progress.")
        try:
            await self._warm_locked(plan)
            return self.snapshot()
        finally:
            self._operation.release()

    async def acquire_conversation(self, plan: NativeLaunchPlan) -> tuple[str, Any]:
        if not self._operation.acquire(blocking=False):
            raise NativeAudioError("A native model operation is already in progress.")
        try:
            if self._owner:
                raise NativeAudioError("The local model is already in a voice conversation.")
            await self._warm_locked(plan)
            self._owner = uuid.uuid4().hex
            self._phase = "in_use"
            # A new conversation never continues another user's native context.
            self._worker.discard_context()
            return self._owner, self._worker
        finally:
            self._operation.release()

    async def release_conversation(self, owner: str) -> None:
        if not self._operation.acquire(blocking=False):
            raise NativeAudioError("A native model operation is already in progress.")
        try:
            if not owner or owner != self._owner:
                raise NativeAudioError("This conversation does not own the native model.")
            if self._worker is not None and self._worker.busy:
                # A caller leaving with an unfinished generator loses the model
                # instance. Reusing its uncertain native state would leak turns.
                await self._worker.close()
            self._owner = ""
            self._phase = (
                "audio_ready" if self._worker is not None and self._worker.usable else "stopped"
            )
        finally:
            self._operation.release()

    async def stop(self) -> None:
        if not self._operation.acquire(blocking=False):
            raise NativeAudioError("A native model operation is already in progress.")
        try:
            if self._owner:
                raise NativeAudioError(
                    "End the current voice conversation before stopping its model."
                )
            if self._worker is not None:
                await self._worker.close()
            self._phase = "stopped"
            self._error = ""
        finally:
            self._operation.release()

    async def recover_conversation(self, owner: str) -> Any:
        """Keep the chat lease while replacing the engine; never replay its audio/tools.

        Native context is deliberately discarded. The caller must restore safe
        app-owned history and wait for fresh user input before continuing.
        """
        if not self._operation.acquire(blocking=False):
            raise NativeAudioError("A native model operation is already in progress.")
        try:
            if not owner or owner != self._owner or self._plan is None:
                raise NativeAudioError("This conversation does not own the native model.")
            self._phase = "loading"
            try:
                if self._worker is not None:
                    await self._worker.close()
                self._worker = None
                self._worker = await self._load(self._plan)
                self._worker.discard_context()
                self._phase = "in_use"
                self._error = ""
                return self._worker
            except BaseException:
                self._worker = None
                self._phase = "failed"
                self._error = "The native model could not recover."
                raise
        finally:
            self._operation.release()
