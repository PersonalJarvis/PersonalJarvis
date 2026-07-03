"""Per-call STT -> Brain -> TTS turn loop for a Twilio Media Streams call.

One :class:`TelephonyCallSession` owns one phone call. It is fed inbound Twilio
WS events (``connected`` / ``start`` / ``media`` / ``mark`` / ``stop``) and
emits outbound Twilio events (``media`` / ``mark`` / ``clear``) through an
async ``send`` callback. The transport (the FastAPI WebSocket) is injected, so
this class is fully testable with a fake send sink and synthetic frames — no
real socket, no model download, no API key.

Pipeline per turn (mirrors the mic path's seams, AD-T2):

    inbound mu-law 8 kHz frames
      -> ulaw_to_pcm16 -> resample 8->16 kHz (stateful)
      -> EnergyEndpointer (VAD): accumulate until end-of-turn
      -> hangup regex guard (end call BEFORE hitting the brain)
      -> STT.transcribe_pcm(pcm, 16000) -> text
      -> classify_completeness(text)         (spec §5, stdlib-only, AP-9/11)
         COMPLETE     -> combine pending buffer; dispatch combined text to brain
         INCOMPLETE   -> append to pending buffer; speak "Mhm?"; stay listening
         ABRUPT_ABORT -> clear pending buffer; speak "Okay."; stay listening
      -> per-call brain.generate_stream(text) -> response text
      -> scrub_for_voice(response)            (AP-11, regex only)
      -> TTS.synthesize(scrubbed) -> 24 kHz int16 PCM
      -> resample 24->8 kHz (stateful) -> lin2ulaw -> 20 ms frames
      -> paced outbound media frames + a trailing mark

Barge-in (AD-T6): if the caller starts speaking while we are mid-playback, we
send Twilio ``{"event":"clear"}`` (flush its outbound buffer), cancel the TTS
task, and reset the outbound resampler so the next answer starts clean.

The brain instance is created PER CALL (AD-T4) — ``BrainManager._history`` is
per-instance, so each call has isolated memory and phone + desktop chats never
interleave.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from jarvis.brain.output_filter import scrub_for_voice
from jarvis.speech.completeness import Completeness, classify_completeness
from jarvis.speech.hangup import HANGUP_RE, contains_end_signal, is_legacy_farewell

from .audio import (
    STT_SAMPLE_RATE,
    TTS_SAMPLE_RATE,
    TWILIO_FRAME_MS,
    TWILIO_SAMPLE_RATE,
    EnergyEndpointer,
    Resampler,
    frame_ulaw,
    pcm16_to_ulaw,
    ulaw_to_pcm16,
)
from .constants import (
    CALL_COMPLETED,
    CALL_FAILED,
    CALL_IN_PROGRESS,
    CALL_NO_AUDIO,
)

log = logging.getLogger("jarvis.telephony.session")

# Hang-up regex + the END_CALL sentinel are shared with the mic path via
# jarvis/speech/hangup.py (stdlib-only — no sounddevice import on this path).
# HANGUP_RE is re-exported below (see __all__) so importers keep working.

# Default greeting when TwilioConfig.greeting is empty AND no assistant name has
# been resolved yet — a neutral, name-free welcome. When a name IS set the
# assistant announces itself by THAT name instead (see _default_greeting); the
# product imposes no fixed name here (jarvis/brain/assistant_name.py).
DEFAULT_GREETING_DE = "Guten Tag, wie kann ich helfen?"  # i18n-allow: German TTS greeting spoken to phone callers
DEFAULT_GREETING_EN = "Hello, how can I help?"

# Send callback signature: an awaitable that ships one JSON-serialisable dict
# to Twilio over the WS.
SendFn = Callable[[dict[str, Any]], Awaitable[None]]


class TelephonyCallSession:
    """Drives one phone call's conversation loop.

    Args:
        call_sid: Twilio CallSid.
        stream_sid: Twilio streamSid (needed on every outbound frame).
        send: async callback shipping one Twilio WS message dict.
        stt: an object exposing ``transcribe_pcm(pcm_bytes, sample_rate=...)``.
        brain: an object exposing ``generate_stream(text)`` (per-call instance).
        tts: an object exposing ``synthesize(text, language_code=...)``.
        from_number / to_number: caller / called E.164 numbers.
        language_code: TTS/STT language hint.
        greeting: optional spoken welcome (empty -> name-based default).
        assistant_name: the assistant's resolved name (from the wake phrase);
            used to build the default greeting. Empty/neutral -> name-free.
        max_call_seconds: hard cap; the call is ended when exceeded.
        bus: optional EventBus for telephony events (publish is best-effort).
    """

    def __init__(
        self,
        *,
        call_sid: str,
        stream_sid: str,
        send: SendFn,
        stt: Any,
        brain: Any,
        tts: Any,
        from_number: str = "",
        to_number: str = "",
        language_code: str = "de-DE",
        greeting: str = "",
        assistant_name: str = "",
        direction: str = "inbound",
        opening: str = "",
        max_call_seconds: int = 600,
        bus: Any = None,
        config: Any = None,
    ) -> None:
        self.call_sid = call_sid
        self.stream_sid = stream_sid
        self._send = send
        self._stt = stt
        self._brain = brain
        self._tts = tts
        self.from_number = from_number
        self.to_number = to_number
        self.language_code = language_code or "de-DE"
        self.greeting = greeting
        self.assistant_name = assistant_name
        # Chunk C: an outbound call ("outbound") speaks ``opening`` first instead
        # of the inbound greeting. Anything other than "outbound" is inbound and
        # behaves exactly as before.
        self.direction = direction or "inbound"
        self.opening = opening
        self.max_call_seconds = max_call_seconds
        self._bus = bus
        self._config = config

        self._in_resampler = Resampler(TWILIO_SAMPLE_RATE, STT_SAMPLE_RATE)
        self._out_resampler = Resampler(TTS_SAMPLE_RATE, TWILIO_SAMPLE_RATE)
        self._endpointer = EnergyEndpointer(sample_rate=STT_SAMPLE_RATE)

        self._started_at = time.time()
        self._turns = 0
        self._outbound_frames = 0
        self._any_audio = False
        self._speaking = False
        self._tts_task: asyncio.Task[None] | None = None
        self._processing = False
        self._ended = False
        self.status = CALL_IN_PROGRESS
        self._end_reason = ""

        # Completeness-gating state (per-call; spec §5)
        # List of INCOMPLETE fragment strings buffered until a completing utterance
        # or the discard timer fires.  DISCARD-ONLY — never auto-flushed to brain.
        self._pending_completeness_fragments: list[str] = []
        # Handle for the running discard timer task (if any).
        self._pending_discard_task: asyncio.Task[None] | None = None

    # -- public properties -------------------------------------------------

    @property
    def turns(self) -> int:
        return self._turns

    @property
    def outbound_frames(self) -> int:
        return self._outbound_frames

    @property
    def duration_s(self) -> float:
        return time.time() - self._started_at

    @property
    def ended(self) -> bool:
        return self._ended

    @property
    def end_reason(self) -> str:
        return self._end_reason

    # -- inbound media -----------------------------------------------------

    async def handle_media(self, payload_b64: str) -> None:
        """Process one inbound Twilio ``media`` frame (base64 mu-law 8 kHz)."""
        if self._ended:
            return
        try:
            ulaw = base64.b64decode(payload_b64)
        except Exception:  # noqa: BLE001 - malformed frame, drop it
            return
        if not ulaw:
            return
        self._any_audio = True

        pcm8 = ulaw_to_pcm16(ulaw)
        pcm16 = self._in_resampler.process(pcm8)

        # Barge-in: caller speaks while we play back -> flush Twilio + cancel TTS.
        if self._speaking and self._frame_is_speech(pcm16):
            await self._barge_in()

        utterance = self._endpointer.push(pcm16)
        if utterance is not None and not self._processing:
            # Run the turn off the inbound-frame path so we keep draining media
            # (and can detect barge-in) while STT/brain/TTS work.
            asyncio.create_task(self._run_turn(utterance))

    def _frame_is_speech(self, pcm16: bytes) -> bool:
        from .audio import audioop  # local import keeps module import light

        try:
            return audioop.rms(pcm16, 2) >= self._endpointer.rms_threshold
        except Exception:  # noqa: BLE001
            return False

    # -- turn loop ---------------------------------------------------------

    async def _run_turn(self, utterance_pcm16: bytes) -> None:
        if self._ended:
            return
        self._processing = True
        try:
            transcript = await self._transcribe(utterance_pcm16)
            text = (transcript or "").strip()
            if not text:
                return
            log.info("telephony[%s] caller: %s", self.call_sid, text)

            # Hangup guard runs BEFORE the brain (mirrors the mic path).
            if HANGUP_RE.search(text):
                await self.end(reason="hangup_phrase", status=CALL_COMPLETED)
                return

            # Completeness gating (spec §5) — route on classifier verdict.
            # Fail-open: any exception → treat as COMPLETE and dispatch normally
            # (AD-OE6 zero-silent-drop; AP-9/11 no LLM on this path).
            dispatch_text = await self._route_by_completeness(text)
            if dispatch_text is None:
                # INCOMPLETE or ABRUPT_ABORT — cue already spoken; stay listening.
                return

            response = await self._think(dispatch_text)
            # Brain-signal hangup: the brain appends [[END_CALL]] on a clear
            # intent to end (mirrors the mic path). Read it from the RAW
            # response BEFORE scrub_for_voice strips the sentinel below.
            end_requested = contains_end_signal(response) or is_legacy_farewell(
                response.strip().rstrip("!.").strip().lower()
            )
            spoken = scrub_for_voice(response, language=self._lang_short()).cleaned
            if not spoken.strip():
                # Always-speak invariant (AD-OE6): never leave the caller in
                # silence. Use a minimal acknowledgement.
                spoken = self._fallback_phrase()

            frames = await self._speak(spoken)
            self._turns += 1
            self._publish_turn(dispatch_text, response, frames)
            if end_requested:
                await self.end(reason="hangup_phrase", status=CALL_COMPLETED)
                return
            log.info(
                "telephony[%s] jarvis: %s (%d frames)",
                self.call_sid,
                spoken,
                frames,
            )
        except asyncio.CancelledError:  # barge-in cancelled the turn
            raise
        except Exception as exc:  # noqa: BLE001 - keep the call alive
            log.warning("telephony[%s] turn failed: %s", self.call_sid, exc)
            try:
                await self._speak(self._fallback_phrase())
            except Exception:  # noqa: BLE001, S110 - best-effort apology
                pass
        finally:
            self._processing = False

    # -- completeness gating (spec §5, telephony surface) ------------------

    def _completeness_config(self) -> tuple[bool, float, int]:
        """Read completeness config defensively; return (enabled, discard_s, max_frags).

        Uses chained getattr with defaults so missing config keys never raise
        (the config block may not have landed from a parallel agent yet — spec §6).
        """
        cfg = getattr(self, "_config", None)
        speech = getattr(cfg, "speech", None)
        comp = getattr(speech, "completeness", None)
        enabled: bool = getattr(comp, "enabled", True)
        discard_s: float = getattr(comp, "pending_discard_s", 8.0)
        max_frags: int = getattr(comp, "max_pending_fragments", 2)
        return enabled, discard_s, max_frags

    async def _route_by_completeness(self, text: str) -> str | None:
        """Classify ``text`` and handle routing for the telephony surface.

        Returns the text (possibly combined with pending buffer) to dispatch to
        the brain, or ``None`` when the turn should NOT reach the brain
        (INCOMPLETE / ABRUPT_ABORT — cue has been spoken inside this method).

        Fail-open: any exception from the classifier is caught and the text is
        returned as-is (treat as COMPLETE, AD-OE6 zero-silent-drop).
        """
        enabled, discard_s, max_frags = self._completeness_config()
        if not enabled:
            # Kill-switch: bypass gating entirely.
            return text

        try:
            verdict = classify_completeness(text, lang=self._lang_short())
        except Exception:  # noqa: BLE001 — fail-open
            log.warning("telephony[%s] completeness classifier raised; failing open", self.call_sid)
            return text

        label = verdict.label
        log.debug(
            "telephony[%s] completeness=%s reason=%s text=%r",
            self.call_sid,
            label,
            verdict.reason,
            text,
        )

        if label == Completeness.ABRUPT_ABORT:
            # Clear pending buffer; speak acknowledgement; stay listening.
            self._pending_completeness_fragments.clear()
            self._cancel_discard_timer()
            cue = "Okay."
            try:
                await self._speak(cue)
            except Exception:  # noqa: BLE001 — signal failure must never mute (AD-OE6)
                log.warning("telephony[%s] failed to speak abort cue", self.call_sid)
            return None

        if label == Completeness.INCOMPLETE:
            # Append fragment; enforce max_frags bound (discard oldest on overflow).
            if len(self._pending_completeness_fragments) >= max_frags:
                self._pending_completeness_fragments.pop(0)
            self._pending_completeness_fragments.append(text)
            # (Re-)arm discard-only timer.
            self._rearm_discard_timer(discard_s)
            # Speak a brief acknowledgement cue so the caller knows they were heard.
            cue = "Mhm?"
            try:
                await self._speak(cue)
            except Exception:  # noqa: BLE001 — signal failure must never mute (AD-OE6)
                log.warning("telephony[%s] failed to speak incomplete cue", self.call_sid)
            return None

        # COMPLETE — combine with any pending buffer and re-classify.
        assert label == Completeness.COMPLETE  # noqa: S101 — exhaustive match
        if self._pending_completeness_fragments:
            combined = " ".join(self._pending_completeness_fragments + [text])
            self._pending_completeness_fragments.clear()
            self._cancel_discard_timer()
            try:
                combined_verdict = classify_completeness(combined, lang=self._lang_short())
            except Exception:  # noqa: BLE001 — fail-open
                log.warning(
                    "telephony[%s] combined re-classify raised; dispatching combined",
                    self.call_sid,
                )
                return combined
            if combined_verdict.label == Completeness.INCOMPLETE:
                # Combined is still incomplete — re-enter INCOMPLETE branch.
                if len(self._pending_completeness_fragments) >= max_frags:
                    self._pending_completeness_fragments.pop(0)
                self._pending_completeness_fragments.append(combined)
                self._rearm_discard_timer(discard_s)
                cue = "Mhm?"
                try:
                    await self._speak(cue)
                except Exception:  # noqa: BLE001
                    log.warning(
                        "telephony[%s] failed to speak combined-incomplete cue", self.call_sid
                    )
                return None
            # Combined is COMPLETE (or ABRUPT_ABORT — treat as complete to avoid
            # discarding what was a legitimate multi-part sentence).
            return combined

        # No pending fragments; dispatch text directly.
        return text

    def _rearm_discard_timer(self, delay_s: float) -> None:
        """Cancel any running discard timer and start a fresh one.

        The timer is DISCARD-ONLY: it clears ``_pending_completeness_fragments``
        when it fires and does NOT dispatch anything to the brain.  This is the
        regression guard for the original "half-command auto-flush" bug.
        """
        self._cancel_discard_timer()
        self._pending_discard_task = asyncio.create_task(
            self._discard_pending_after(delay_s),
            name=f"completeness-discard-{self.call_sid}",
        )

    def _cancel_discard_timer(self) -> None:
        """Cancel the pending discard timer if one is running."""
        if self._pending_discard_task is not None and not self._pending_discard_task.done():
            self._pending_discard_task.cancel()
        self._pending_discard_task = None

    async def _discard_pending_after(self, delay_s: float) -> None:
        """Coroutine backing the discard timer.  DISCARD-ONLY — never dispatches."""
        try:
            await asyncio.sleep(delay_s)
            if self._pending_completeness_fragments:
                log.info(
                    "telephony[%s] discard timer fired; clearing %d pending fragment(s)",
                    self.call_sid,
                    len(self._pending_completeness_fragments),
                )
                self._pending_completeness_fragments.clear()
        except asyncio.CancelledError:
            pass  # normal cancellation — nothing to do

    async def _transcribe(self, pcm16: bytes) -> str:
        result = await self._stt.transcribe_pcm(pcm16, sample_rate=STT_SAMPLE_RATE)
        return getattr(result, "text", "") or ""

    async def _think(self, text: str) -> str:
        chunks: list[str] = []
        async for chunk in self._brain.generate_stream(text):
            if chunk:
                chunks.append(chunk)
        return "".join(chunks)

    async def _speak(self, text: str) -> int:
        """Synthesize ``text``, transcode to mu-law, and pace it to Twilio.

        Returns the number of 20 ms outbound media frames sent. Cancellable:
        a barge-in cancels the underlying task mid-stream.
        """
        # Master output volume (shared with the local speaker + browser sinks) so
        # the setting works on a call too, not just at the desk. Read once per
        # turn so a live change lands on the next utterance. numpy import is lazy
        # to keep the telephony module's cold-import light.
        from jarvis.audio.gain import apply_output_gain_pcm16

        vol = getattr(getattr(self._config, "tts", None), "volume", 1.0)

        self._speaking = True
        sent = 0
        try:
            async for chunk in self._tts.synthesize(text, language_code=self.language_code):
                pcm = getattr(chunk, "pcm", b"")
                rate = getattr(chunk, "sample_rate", TTS_SAMPLE_RATE)
                if not pcm:
                    continue
                if rate != TTS_SAMPLE_RATE and rate != TWILIO_SAMPLE_RATE:
                    # Resampler is pinned to 24k->8k; for an off-rate chunk fall
                    # back to a stateless resample to the Twilio rate.
                    from .audio import resample_pcm16

                    pcm8 = resample_pcm16(pcm, rate, TWILIO_SAMPLE_RATE)
                else:
                    pcm8 = self._out_resampler.process(pcm)
                pcm8 = apply_output_gain_pcm16(pcm8, vol)
                ulaw = pcm16_to_ulaw(pcm8)
                for frame in frame_ulaw(ulaw):
                    await self._send_media(frame)
                    sent += 1
                    # Pace at ~real time so Twilio's jitter buffer stays sane.
                    await asyncio.sleep(TWILIO_FRAME_MS / 1000.0)
            # Trailing mark lets us correlate playback completion if needed.
            await self._send_mark("turn-end")
        finally:
            self._speaking = False
        self._outbound_frames += sent
        return sent

    # -- greeting ----------------------------------------------------------

    async def speak_greeting(self) -> int:
        """Speak the configured (or default) greeting at call start."""
        text = self.greeting.strip() or self._default_greeting()
        return await self._speak(scrub_for_voice(text, language=self._lang_short()).cleaned)

    async def speak_opening(self) -> int:
        """Speak the outbound ``opening`` line (Chunk C) at call start.

        Mirrors :meth:`speak_greeting` (scrub -> TTS -> paced mu-law frames) but
        speaks the caller-supplied opening. Falls back to the greeting when no
        opening was provided so an outbound call is never silent (AD-OE6).
        """
        text = self.opening.strip()
        if not text:
            return await self.speak_greeting()
        return await self._speak(scrub_for_voice(text, language=self._lang_short()).cleaned)

    async def speak_intro(self) -> int:
        """Speak the right call-opening line for this call's direction.

        Outbound (``direction == "outbound"``) speaks the ``opening``; everything
        else is inbound and speaks the greeting — byte-for-byte the previous
        behaviour, so the inbound path is unchanged.
        """
        if self.direction == "outbound":
            return await self.speak_opening()
        return await self.speak_greeting()

    # -- barge-in ----------------------------------------------------------

    async def _barge_in(self) -> None:
        """Flush Twilio's outbound buffer and cancel in-flight playback."""
        log.info("telephony[%s] barge-in", self.call_sid)
        try:
            await self._send({"event": "clear", "streamSid": self.stream_sid})
        except Exception:  # noqa: BLE001, S110 - clear is best-effort
            pass
        if self._tts_task is not None and not self._tts_task.done():
            self._tts_task.cancel()
        self._out_resampler.reset()
        self._speaking = False

    # -- outbound primitives ----------------------------------------------

    async def _send_media(self, ulaw_frame: bytes) -> None:
        await self._send(
            {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {"payload": base64.b64encode(ulaw_frame).decode("ascii")},
            }
        )

    async def _send_mark(self, name: str) -> None:
        await self._send(
            {
                "event": "mark",
                "streamSid": self.stream_sid,
                "mark": {"name": name},
            }
        )

    # -- lifecycle ---------------------------------------------------------

    async def end(self, *, reason: str = "", status: str = CALL_COMPLETED) -> None:
        """End the call: cancel playback, mark status, publish the end event."""
        if self._ended:
            return
        self._ended = True
        self._end_reason = reason
        if not self._any_audio and status == CALL_COMPLETED:
            status = CALL_NO_AUDIO
        self.status = status
        if self._tts_task is not None and not self._tts_task.done():
            self._tts_task.cancel()
        # Cancel any pending completeness discard timer so no dangling tasks survive
        # after the call ends.
        self._cancel_discard_timer()
        self._publish_end()
        log.info(
            "telephony[%s] ended: status=%s reason=%s turns=%d dur=%.1fs",
            self.call_sid,
            status,
            reason,
            self._turns,
            self.duration_s,
        )

    def fail(self, reason: str) -> None:
        """Synchronous failure marker (used before the loop starts)."""
        self._ended = True
        self._end_reason = reason
        self.status = CALL_FAILED

    def check_time_cap(self) -> bool:
        """Return ``True`` if the call exceeded ``max_call_seconds``."""
        return self.duration_s >= self.max_call_seconds

    # -- helpers -----------------------------------------------------------

    def _lang_short(self) -> str:
        return "en" if self.language_code.lower().startswith("en") else "de"

    def _default_greeting(self) -> str:
        """Greeting spoken when no custom ``greeting`` was configured.

        The assistant announces itself by its OWN resolved name (derived from the
        user's wake phrase). When no name is set yet — the neutral shipped
        fallback — it greets without any name rather than imposing "Jarvis".
        """
        lang = self._lang_short()
        name = (self.assistant_name or "").strip()
        from jarvis.brain.assistant_name import DEFAULT_ASSISTANT_NAME

        if name and name != DEFAULT_ASSISTANT_NAME:
            if lang == "en":
                return f"{name} here. How can I help?"
            return f"Hier ist {name}. Wie kann ich helfen?"  # i18n-allow: German TTS greeting
        return DEFAULT_GREETING_EN if lang == "en" else DEFAULT_GREETING_DE

    def _fallback_phrase(self) -> str:
        if self._lang_short() == "en":
            return "Sorry, I did not catch that. Could you say it again?"
        return "Entschuldigung, das habe ich nicht verstanden. Bitte wiederhole es."  # i18n-allow: German TTS fallback phrase spoken to phone callers

    # -- bus events --------------------------------------------------------

    def _publish_turn(self, transcript: str, response: str, frames: int) -> None:
        if self._bus is None:
            return
        try:
            from .events import TelephonyCallTurn

            self._bus.publish(
                TelephonyCallTurn(
                    call_sid=self.call_sid,
                    transcript=transcript,
                    response_text=response,
                    outbound_frames=frames,
                )
            )
        except Exception:  # noqa: BLE001, S110 - bus errors never break a call
            pass

    def _publish_end(self) -> None:
        if self._bus is None:
            return
        try:
            from .events import TelephonyCallEnded

            self._bus.publish(
                TelephonyCallEnded(
                    call_sid=self.call_sid,
                    status=self.status,
                    duration_s=self.duration_s,
                    turns=self._turns,
                    reason=self._end_reason,
                )
            )
        except Exception:  # noqa: BLE001, S110 - bus errors never break a call
            pass


__all__ = [
    "DEFAULT_GREETING_DE",
    "DEFAULT_GREETING_EN",
    "HANGUP_RE",
    "TelephonyCallSession",
]
