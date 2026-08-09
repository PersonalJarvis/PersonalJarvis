"""Bus bridge: connects the Jarvis EventBus to the orb overlay.

The orb itself knows neither Jarvis core, EventBus, nor supervisor states.
This bridge subscribes to `SystemStateChanged` and translates the high-level
states (IDLE/LISTENING/THINKING/SPEAKING) into orb API calls.

The bridge additionally manages the mic-listener lifecycle:
    - LISTENING  → start the mic stream, pump live level to the orb
    - THINKING / SPEAKING / IDLE → stop the mic stream (privacy + CPU)

Animation mapping (phase 1c-add 2026-04-24):
    LISTENING                → 'wave' (greet on wake word)
    THINKING                 → 'think' (loop bubble), stopped on transition
    SPEAKING                 → a light 'nod' (subtle acknowledgement)
    SPEAKING → IDLE          → 'salute' (hangup gesture, then hide)
    Idle scheduler           → every 30-90s a random animation from the pool

Architecture rule: the UI layer (L7) subscribes, the business layer (L2
speech, L6 supervisor) publishes. The bridge lives in the UI layer.

Threading:
    subscribe() handlers are called from the asyncio event loop; they call
    the orb API (show/hide/set_mode), which internally queues the UI
    mutation onto the Tk main thread via `root.after(0, ...)`.
    The idle scheduler runs as an asyncio.Task on the same loop and
    only uses the thread-safe orb API.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import TYPE_CHECKING, Any

from jarvis.core.events import (
    AudioOutFirst,
    DictationCompleted,
    DictationRefused,
    DictationStarted,
    DictationTranscribing,
    DictationTranscript,
    JarvisAgentBackgroundCompleted,
    ListeningStarted,
    OrbResetRequested,
    ResponseGenerated,
    ShowWindowRequested,
    SystemStateChanged,
    TranscriptionUpdate,
    UserVisibleFeedback,
    VoiceBootStatus,
    VoiceMuteChanged,
    VoiceMuteToggleRequested,
    VoiceSessionEnded,
    VoiceSessionStarted,
    WakeCandidateDetected,
    WakeWordDetected,
)
from jarvis.dictation.outcomes import was_delivered
from ui.orb.animations import IDLE_ANIMATION_POOL

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus
    from ui.orb.overlay import OrbOverlay


log = logging.getLogger("jarvis.orb.bridge")


# Idle scheduler configuration: random wait time between animations.
# Range deliberately wide so the ghost doesn't feel "predictable".
IDLE_MIN_INTERVAL_S = 30.0
IDLE_MAX_INTERVAL_S = 90.0

# The hangup animation plays, then a delayed hide() call.
SALUTE_DURATION_S = 1.1
# Grace period when transitioning from a voice state (LISTENING/THINKING) to
# IDLE, without a SPEAKING state in between (e.g. an STT silence timeout):
# the user should still see the mascot briefly instead of it vanishing instantly.
GRACE_HIDE_DURATION_S = 1.5

# Hard ceiling on how long a dictation may keep the bar lit without a
# ``DictationCompleted``. The pipeline caps a recording at ``dictation.
# max_seconds`` (300 s default, and a configured 0 falls back to that same
# default, so the cap can never be switched off); this is that ceiling plus a
# generous transcription margin. It exists ONLY as a fail-safe: if the
# completion event is lost — a crashed session, a dropped subscriber, a
# surface swap mid-dictation — the bar must still come down on its own rather
# than stay lit with no visible cause and no way back. A deadline expires; a
# latch you forget to clear does not.
DICTATION_MAX_VISIBLE_S = 360.0

# How long a refused dictation stays on screen before the surface goes back to
# whatever it was doing. Long enough to be noticed and read on a surface that
# renders the sentence (the mascot's bubble), short enough that it never becomes
# a state the user has to dismiss — this is an answer to a keypress, not a
# notification. It must also comfortably outlast a repeated keypress so holding
# the shortcut down does not strobe the surface.
DICTATION_REFUSAL_DWELL_S = 3.0

# Shown when a refusal arrives with no sentence in it. A refusal event with an
# empty ``detail`` is a contract violation upstream, but the user still pressed
# a key and still deserves to see that the key was heard and declined, so the
# surface says the one thing that is true without the detail.
DICTATION_REFUSAL_FALLBACK_TEXT = "Dictation could not start."

# How long the bar stays up after a dictation that came back with NOTHING —
# silence, or a provider that refused. Short, and deliberately not zero: the
# recording look was on screen a moment ago, so vanishing without a beat is
# indistinguishable from "the shortcut did nothing", which is the exact
# failure shape the dictation lane exists to end.
DICTATION_NOTHING_BACK_DWELL_S = 1.5

# How long an outcome that needs ACTING on stays up (the OS blocked the paste
# and the text is on the clipboard; a custom paste chord was sent to an app
# that may not bind it). Long enough to read a full sentence.
DICTATION_OUTCOME_DWELL_S = 4.0

# Refusal reasons the bar must NOT answer with the failure look, keyed by the
# one thing that makes them different: nothing failed.
#
# ``already_running`` is the whole set today and the reason it exists. It is
# raised when a SECOND start lands on a dictation that is happily recording —
# on Windows the polling hotkey backend re-reports the chord, so a key edge
# arriving next to the release produces exactly that. Answering it with the
# red cross put a verdict on the turn the user was watching, and (because the
# handler also drops ``_dictation_active``) swallowed that turn's completion,
# so the cross stayed up over a dictation that pasted perfectly.
DICTATION_INERT_REFUSALS: frozenset[str] = frozenset({"already_running"})

# Long enough to cover an entire THINKING/SPEAKING phase. The transcript
# bubble is explicitly hidden when the state leaves voice mode (→ IDLE/ERROR).
#
# The orb bubble walks the user through the whole turn, mirroring the sidebar:
#   LISTENING → the live user transcript (what you said)
#   THINKING  → a thinking indicator while the brain has no reply text yet
#   SPEAKING  → Jarvis's actual reply (the sidebar assistant line)
# Random personality quips are still never popped here — an earlier bug let
# them overwrite the shared bubble widget; the opposite over-correction then
# froze the *user* transcript across the whole turn so the user never saw the
# thinking/speaking state. The bubble only ever renders meaningful turn
# content. Personality stays in the orb's animations (wave / think / nod /
# salute / idle pool), not in the bubble text.
VOICE_BUBBLE_DURATION_MS = 30_000

# Shown in the orb bubble while the brain is thinking and no reply text exists
# yet. User-facing German conversational UI on purpose: the same bubble renders
# the German live transcript and the German reply, and CLAUDE.md keeps
# user-facing conversational content German. Single source of truth so it is
# trivially translatable later.
THINKING_BUBBLE_TEXT = "Denke nach …"  # i18n-allow

# States during which the user is still composing their utterance and the
# bubble must KEEP showing the live transcript (and accept further
# TranscriptionUpdate events). Includes WAITING_FOR_COMPLETION so a paused
# incomplete fragment stays visible across the pause — without this the
# bubble appears to "submit" or vanish the moment the user takes a breath.
_USER_SIDE_BUBBLE_STATES = frozenset(
    {"LISTENING", "USER_SPEAKING", "WAITING_FOR_FINAL_TRANSCRIPT", "WAITING_FOR_COMPLETION"}
)

# Supervisor states during which the mascot is meant to be visible. After a
# voice session ENDS (hangup / idle-timeout), the pipeline can still emit a
# stray transition into one of these from an in-flight turn — e.g. a brain
# reply that was mid-flight when the user said "auflegen" finishes speaking a
# few seconds later. Those stray transitions must NOT resurrect the mascot;
# it stays hidden until a genuine new ``VoiceSessionStarted`` (the user calls
# "Hey Jarvis" again). See ``_on_session_ended`` / ``_on_session_started`` and
# the guard at the top of ``_on_state``.
_ACTIVE_VOICE_STATES = frozenset({"LISTENING", "THINKING", "SPEAKING"})

# German public-broadcaster subtitle-credit boilerplate that German-language
# STT sometimes hallucinates onto silence/noise (e.g. "Untertitelung des ZDF
# fuer funk, 2020" / "Vielen Dank"). Must stay the literal German tokens the  # i18n-allow
# STT engine actually emits — this is speech-recognition input vocabulary,
# not translatable prose.
_TRANSCRIPT_BOILERPLATE_RE = re.compile(
    r"\b("
    r"untertitelung\s+des\s+(zdf|wdr|ndr|swr|br|ard|arte)"  # i18n-allow
    r"(\s+(fuer|für|fur)\s+funk)?(\s*,?\s*\d{4})?|"  # i18n-allow
    r"untertitel\s+(von|der|im\s+auftrag)|"  # i18n-allow
    r"(eine\s+)?(sendung|produktion|redaktion|programm)\s+"  # i18n-allow
    r"(des|der|von)\s+(zdf|wdr|ndr|swr|br|ard|arte)"  # i18n-allow
    r"(\s*,?\s*\d{4})?|"
    r"(zdf|wdr|ndr|swr|br|ard|arte)\s+"
    r"(fernsehen|mediagroup|rundfunk)(\s*,?\s*\d{4})?|"  # i18n-allow
    r"(norddeutscher|westdeutscher|bayerischer)\s+rundfunk|"  # i18n-allow
    r"im\s+auftrag\s+des|"  # i18n-allow
    r"mediagroup|"
    r"thanks\s+for\s+watching|"
    r"vielen\s+dank"  # i18n-allow
    r")\b",
    re.IGNORECASE,
)


def _is_transcript_boilerplate(text: str) -> bool:
    return _TRANSCRIPT_BOILERPLATE_RE.search(text) is not None


# NOTE 2026-05-27 (bubble-pendulum Ep.3): the STT pipeline accumulates probe
# tails into a complete snapshot itself (jarvis/speech/pipeline.py:409
# ``_merge_partial_transcript`` over ``_probe_live_text``) and every
# TranscriptionUpdate carries that snapshot. The Desktop App's
# TranscriptionView wires the same event into the store 1:1 via
# ``setTranscription`` (frontend/src/hooks/useWebSocket.ts:138-140) and is
# correct. An earlier bridge-side re-merge here drifted from that source
# (downward-corrections kept the dirty older snapshot; missed overlaps
# duplicated words). The bridge now mirrors the snapshot 1:1, matching the
# TranscriptionView byte-for-byte.


class OrbBusBridge:
    """Couples the orb to the event bus + manages the mic-listener lifecycle."""

    def __init__(
        self,
        bus: EventBus,
        orb: OrbOverlay,
        idle_animations_enabled: bool = True,
        hide_on_idle: bool = True,
    ) -> None:
        self._bus = bus
        self._orb = orb
        self._mic_level_unsub = None  # mic_level subscription (registered in attach)
        self._tts_recency_unsub = None  # level_tap subscription (TTS-active tracker)
        # Monotonic time of the last TTS output level. The state label
        # (LISTENING/SPEAKING) flips to LISTENING while TTS audio is still
        # playing (continue-listening), so we gate mic routing on "is TTS
        # actually producing sound" instead — whoever makes sound drives bars.
        self._last_tts_level_t = 0.0
        self._last_state: str = "IDLE"
        self._voice_session_active = False
        self._wake_preview_origin_state = "IDLE"
        # A verified-enough wake candidate can reveal the listening bar before
        # the authoritative session state arrives. The wake microphone is
        # already capturing during that interval, so its live levels must be
        # allowed through without mutating the state-machine edge.
        self._wake_candidate_active = False
        self._idle_task: asyncio.Task | None = None
        self._idle_enabled = idle_animations_enabled
        self._hide_on_idle = hide_on_idle
        self._hangup_task: asyncio.Task | None = None
        self._completion_task: asyncio.Task | None = None
        # Dictation lane (separate from every voice state — see
        # _on_dictation_started): True while a dictation is painting the bar.
        self._dictation_active = False
        # True once recording stopped and the transcription is running, so a
        # late partial transcript cannot drag the bar back to the mic look.
        self._dictation_transcribing = False
        self._dictation_standdown_task: asyncio.Task | None = None
        # Expiring fail-safe, armed on DictationStarted and cancelled on
        # DictationCompleted. If the completion event never arrives (a crashed
        # session, a dropped subscriber) the bar would otherwise stay lit
        # forever with no way back — the "it never goes away and I can't tell
        # why" failure class. A deadline cannot stick the way a latch can.
        self._dictation_failsafe_task: asyncio.Task | None = None
        self._rng = random.Random()
        self._listening_transcript_text = ""
        # True while the pipeline is mid-completion-buffer (paused on an
        # incomplete fragment, waiting for the rest). Used so the next
        # LISTENING / ListeningStarted does NOT reset the bubble — same
        # bubble grows across pause + continuation. Set in the
        # WAITING_FOR_COMPLETION state branch; cleared on THINKING / SPEAKING
        # / IDLE-ish or on a fresh LISTENING (i.e. not from a continuation).
        self._completion_continuation: bool = False
        # Latest Jarvis reply text for the current turn (from ResponseGenerated).
        # Shown in the bubble during SPEAKING; reset at the start of each turn
        # so a stale reply never leaks into the next THINKING phase.
        self._last_response_text = ""
        # ADR-0016 visible-feedback contract: latest SystemStateChanged
        # trace_id, used as correlation_id when the orb publishes its
        # visibility snapshot. Empty string means "no prior state event"
        # (e.g. a sticky orb that was visible before any wake-word).
        self._last_state_trace_id: str = ""
        # Session-lifecycle latch (orb-resurrection bug 2026-05-29). Set True
        # when a voice session ENDS and cleared when the next session STARTS.
        # While True, stray active-state transitions (LISTENING/THINKING/
        # SPEAKING) emitted by an in-flight turn after the hangup are ignored
        # so the mascot does not pop back. Defaults False so any surface that
        # drives _on_state without publishing VoiceSession events (and the
        # very first session before any end-event) behaves exactly as before.
        self._suppress_show_until_session: bool = False
        # The boot-created Jarvis Bar is fully initialized but withdrawn. This
        # latch is released directly by the first genuine VoiceBootStatus ready
        # event; there is deliberately no timeout reveal because a visible bar is
        # the product's promise that the user can speak now.
        self._voice_usable: bool = False
        self._boot_visibility_released: bool = False
        # Backend asyncio loop the bridge's bus handlers run on. The Tk gesture
        # callbacks (_publish_mute_toggle / _publish_show_window /
        # _publish_visible_feedback) fire on the overlay's *Tk thread*, which has
        # no asyncio loop of its own. They must marshal bus.publish onto THIS
        # loop via run_coroutine_threadsafe — never asyncio.run(), which spins a
        # throwaway loop and then explodes when a subscriber (the per-WS-client
        # _forward) acquires an asyncio.Lock bound to the real loop
        # ("RuntimeError: bound to a different event loop"). 2026-06-28 forensic:
        # an orb double-click mute did exactly that → mute publish failed, mic
        # stayed muted, voice stuck in LISTENING, WS-forward log storm,
        # session reason=error. Captured lazily in attach()/_on_state (both run
        # on the backend loop, well before the orb is ever clickable).
        self._loop: asyncio.AbstractEventLoop | None = None

    def _remember_loop(self) -> None:
        """Capture the running backend loop (idempotent). Called from async bus
        handlers, which always run on that loop."""
        if self._loop is not None and self._loop.is_running():
            return
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    def _marshal_publish(self, coro, *, label: str) -> None:
        """Schedule a ``bus.publish`` coroutine on the captured backend loop from
        the Tk thread. Fire-and-forget; never blocks the Tk mainloop.

        Falls back to a one-shot ``asyncio.run`` ONLY when no backend loop was
        ever captured (the Tk-only test harness). In the live app a state event
        always fires before the orb is clickable, so the captured-loop path is
        the one that runs — and the throwaway-loop cross-event-loop crash that
        froze the mic (2026-06-28) cannot recur."""
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(coro, loop)
            except RuntimeError as exc:
                log.warning("%s publish dropped: %s", label, exc)
            return
        # No backend loop reachable — last resort so the gesture is not silently
        # swallowed in a Tk-only harness. Never the live-app path.
        try:
            asyncio.run(coro)
        except RuntimeError as exc:
            log.warning("%s publish dropped (no backend loop): %s", label, exc)

    def attach(self) -> None:
        """Subscribes the bridge to SystemStateChanged. Idempotent."""
        # If attach() runs on the backend loop (the live async-startup path),
        # capture it now so the very first gesture already marshals correctly.
        self._remember_loop()
        try:
            self._bus.subscribe(SystemStateChanged, self._on_state)
            # Earliest safe visual wake cue: WakeWordDetected is emitted only
            # after wake verification, before the later session/state events.
            self._bus.subscribe(WakeWordDetected, self._on_wake_word_detected)
            # Optimistic VISUAL-ONLY reveal: pops the bar on the OWW candidate,
            # before the slow STT prefix-verify gates WakeWordDetected (so the
            # bar feels instant on "Hey Jarvis"). Retracted on a rejected hit.
            self._bus.subscribe(WakeCandidateDetected, self._on_wake_candidate)
            # Voice-session lifecycle: the orb tracks SESSION boundaries, not
            # just raw turn-states, so a late in-flight turn after a hangup
            # cannot resurrect the mascot (orb-resurrection bug 2026-05-29).
            self._bus.subscribe(VoiceSessionStarted, self._on_session_started)
            self._bus.subscribe(VoiceSessionEnded, self._on_session_ended)
            self._bus.subscribe(ListeningStarted, self._on_listening_started)
            self._bus.subscribe(TranscriptionUpdate, self._on_transcription_update)
            self._bus.subscribe(ResponseGenerated, self._on_response_generated)
            self._bus.subscribe(JarvisAgentBackgroundCompleted, self._on_background_completed)
            self._bus.subscribe(AudioOutFirst, self._on_audio_out_first)
            # Dictation is a SEPARATE lane from the voice states: it raises no
            # SystemStateChanged, so the four voice modes stay exactly as they
            # are and dictation gets its own coarse modes instead of borrowing
            # one. The full lifecycle, in order:
            #   DictationStarted      → the bar RISES, listening look, live level
            #   DictationTranscript   → the live text in the bubble
            #   DictationTranscribing → recording stopped, working look
            #   DictationCompleted    → outcome, then stand down and close
            #   DictationRefused      → (instead of all of the above) the reason,
            #                           briefly, then hand the surface back
            # The reveal hangs off ``DictationStarted`` and nothing else: the
            # first transcript costs a partial interval plus an STT round-trip
            # and never arrives at all for a short press, so a transcript-driven
            # reveal meant the bar came up late or not at all.
            self._bus.subscribe(DictationStarted, self._on_dictation_started)
            self._bus.subscribe(DictationTranscript, self._on_dictation_transcript)
            self._bus.subscribe(DictationTranscribing, self._on_dictation_transcribing)
            self._bus.subscribe(DictationCompleted, self._on_dictation_completed)
            # ...and the fifth outcome: the dictation never started at all. A
            # refusal is the ONE dictation event with no visual of its own
            # before this subscription existed, which is precisely why pressing
            # the shortcut could do nothing at all — see _on_dictation_refused.
            self._bus.subscribe(DictationRefused, self._on_dictation_refused)
            # Boot visibility gate: a genuine voice-ready signal releases the
            # hidden Jarvis Bar. Degraded UI-only ready signals do not.
            self._bus.subscribe(VoiceBootStatus, self._on_voice_boot_status)
            # Authoritative mute mirror: the pipeline owns the global voice-mute
            # flag and broadcasts VoiceMuteChanged whenever it flips (from this
            # bar, the mascot, or a voice command). Forward it to the current
            # surface's ``set_muted`` so the slashed-mic icon stays in lock-step
            # with the real state — defensive getattr keeps surfaces without the
            # method (the mascot orb) working unchanged.
            self._bus.subscribe(VoiceMuteChanged, self._on_voice_mute_changed)
            # ADR-0016 L2 — voice-driven recovery from "orb lost on screen".
            # The local_action_gate publishes OrbResetRequested when the
            # user says "Orb zurück" / "wo bist du" / "reset orb".  # i18n-allow
            self._bus.subscribe(OrbResetRequested, self._on_reset_requested)
            # Wire the orb's double-double-click gesture to a bus publish.
            # The orb requires two ``<Double-Button-1>`` events inside
            # ``MUTE_GESTURE_WINDOW_MS`` (four clicks in <600 ms) before
            # firing this callback — accidental triggers from clicking the
            # popup orb were the 2026-05-18 wake-loop-mute regression.
            # The orb fires the callback from the Tk main-thread; we
            # marshal onto the asyncio loop because EventBus.publish is
            # an async coroutine. ``set_on_mute_toggle`` is a defensive
            # getattr so older orb stubs (e.g. test doubles) still work.
            setter = getattr(self._orb, "set_on_mute_toggle", None)
            if setter is not None:
                setter(self._publish_mute_toggle)
            # ADR-0016 visible-feedback contract: inject the publisher so
            # the orb stays bus-agnostic. Defensive getattr keeps older
            # orb test doubles working.
            feedback_setter = getattr(self._orb, "set_feedback_publisher", None)
            if feedback_setter is not None:
                feedback_setter(self._publish_visible_feedback)
            # Wire the overlay's right-click gesture (bar AND mascot) to a bus
            # publish. The surface fires the callback from the Tk main-thread;
            # we marshal onto the asyncio loop (EventBus.publish is a coroutine),
            # exactly like the mute-toggle path. Defensive getattr keeps older
            # surface test doubles without the setter working.
            show_window_setter = getattr(self._orb, "set_on_show_window", None)
            if show_window_setter is not None:
                show_window_setter(self._publish_show_window)
            # Live mic loudness → equalizer bars during LISTENING. The VAD frame
            # loop feeds jarvis.audio.mic_level from the audio already captured
            # for STT — no second mic stream. One subscription for the bridge's
            # whole life; it forwards to whichever surface is current.
            try:
                from jarvis.audio import mic_level

                self._mic_level_unsub = mic_level.subscribe(self._on_mic_level)
            except Exception as exc:  # noqa: BLE001
                log.warning("OrbBridge mic_level subscribe failed: %s", exc)
            # Track AND forward TTS output. In-process surfaces also subscribe
            # to level_tap themselves, so this is a harmless duplicate there;
            # the macOS bar/mascot live in a companion process and cannot see
            # this process-local signal at all. Forwarding here is therefore
            # load-bearing for cross-process speaking animation, while the
            # recency timestamp still prevents the mic from clobbering it.
            try:
                from jarvis.audio import level_tap

                self._tts_recency_unsub = level_tap.subscribe(self._note_tts_level)
            except Exception as exc:  # noqa: BLE001
                log.warning("OrbBridge level_tap recency subscribe failed: %s", exc)
            log.info(
                "OrbBridge subscribed to SystemStateChanged + VoiceSessionStarted "
                "+ VoiceSessionEnded + ListeningStarted + TranscriptionUpdate "
                "+ ResponseGenerated + AudioOutFirst + OrbResetRequested "
                "+ mute-toggle gesture + show-window gesture "
                "+ visible-feedback contract."
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("OrbBridge.attach() failed: %s", exc)

    async def _on_reset_requested(self, event: OrbResetRequested) -> None:
        """Voice-triggered reset: bring the orb back to the default
        anchor (ADR-0016 L2). Dispatched onto the Tk thread because
        ``_on_reset_double_click`` mutates Tk widgets."""
        log.info("OrbBridge._on_reset_requested source=%s", event.source)
        root = getattr(self._orb, "_root", None)
        reset_fn = getattr(self._orb, "_on_reset_double_click", None)
        if root is None or reset_fn is None:
            log.warning("OrbBridge: reset requested but orb has no _root / _on_reset")
            return
        try:
            root.after(0, lambda: reset_fn(None))
        except Exception:  # noqa: BLE001
            log.exception("OrbBridge.reset dispatch failed")

    async def _on_voice_mute_changed(self, event: VoiceMuteChanged) -> None:
        """Forward the pipeline's authoritative mute state to the current surface
        so its slashed-mic icon mirrors reality. Defensive getattr: a surface
        without ``set_muted`` (the mascot orb) is simply skipped — the call is a
        no-op, never an error. The write is a quick atomic flag set on the
        surface; no Tk marshal needed (the bar reads it on its own frame loop)."""
        setter = getattr(self._orb, "set_muted", None)
        if not callable(setter):
            return
        try:
            setter(bool(event.muted))
        except Exception:  # noqa: BLE001 — a mirror update must never break the bus
            log.debug("surface set_muted failed", exc_info=True)

    def _publish_visible_feedback(self, mode: str, observed: dict) -> None:
        """Called from the orb's Tk thread after a deiconify. Builds and
        publishes ``UserVisibleFeedback`` onto the asyncio bus.

        Same Tk→asyncio marshal pattern as ``_publish_mute_toggle``.
        """
        expected: dict[str, Any] = {"mode": mode, "viewable": True}
        coro = self._bus.publish(
            UserVisibleFeedback(
                surface="orb",
                expected=expected,
                observed=dict(observed),
                correlation_id=self._last_state_trace_id,
            )
        )
        self._marshal_publish(coro, label="UserVisibleFeedback")

    def _publish_mute_toggle(self) -> None:
        """Called from the Tk main-thread when the orb detects a double
        double-click. We hop onto the captured backend loop to publish, because
        EventBus.publish is a coroutine and Tk is sync.

        Marshals via the shared ``_marshal_publish`` helper: scheduling on the
        backend loop is mandatory — a throwaway ``asyncio.run`` loop dispatches
        the per-WS-client ``_forward`` subscriber whose ``send_lock`` is bound to
        the real loop, raising "bound to a different event loop" and leaving the
        mic muted with the voice session frozen in LISTENING (2026-06-28).
        """
        coro = self._bus.publish(VoiceMuteToggleRequested(source="orb_dblclick_double"))
        self._marshal_publish(coro, label="mute-toggle")

    def _publish_show_window(self) -> None:
        """Called from the surface's Tk main-thread on a right-click. Publishes
        ``ShowWindowRequested`` so the DesktopApp raises its window.

        Same Tk→asyncio marshal pattern as ``_publish_mute_toggle``: hop onto
        the running loop if there is one, else a one-shot ``asyncio.run`` so the
        gesture is never silently swallowed.
        """
        coro = self._bus.publish(ShowWindowRequested(source="overlay_rightclick"))
        self._marshal_publish(coro, label="show-window")

    async def _on_wake_candidate(self, event: WakeCandidateDetected) -> None:
        """Optimistic, visual-only bar reveal — pops the bar the instant OWW
        fires, BEFORE the slow STT prefix-verify gates the authoritative
        ``WakeWordDetected``. This is the latency fix for "the bar appears ~1 s
        after 'Hey Jarvis'" (the reveal used to wait for the STT round-trip).

        ``active=True``  → show the listening bar now.
        ``active=False`` → the prefix-verifier rejected the candidate (a false
        positive): retract. Hide a non-persistent bar; restore the idle pill for
        a persistent one. If a real session has meanwhile begun (``_last_state``
        is an active voice state) the retract is a no-op — the session owns it.

        Deliberately does NOT mutate ``_last_state`` on show: the authoritative
        ``_on_wake_word_detected`` that follows a confirmed wake must still see
        the IDLE→LISTENING edge so it plays the greet 'wave' and sets the state
        cleanly. A separate candidate latch enables the equalizer during this
        preview without forging an authoritative session state.
        """
        if event.active:
            if self._dictation_active:
                # A candidate is an UNVERIFIED, retractable preview; a running
                # dictation is a real thing the user started. Letting the
                # preview repaint over it makes the dictation bar flicker into
                # the wake look and back. The authoritative signals
                # (WakeWordDetected / VoiceSessionStarted) are deliberately NOT
                # gated, so a genuine session still takes the bar — and the
                # dictation lane cannot deafen the preview for good because it
                # carries its own expiring fail-safe.
                log.debug("OrbBridge wake preview skipped: a dictation owns the bar")
                return
            # Incoming speech candidate — cancel any pending idle hide and pop
            # the bar. _last_state untouched (see docstring).
            if not self._wake_candidate_active:
                self._wake_preview_origin_state = self._last_state
                # Only on the reveal edge — a repeated candidate while the user
                # is already speaking must not re-zero the live envelope.
                self._clear_input_level()
            self._wake_candidate_active = True
            # A fresh input affordance supersedes stale output recency from the
            # preceding turn. Startup no longer plays an ACK, so retaining the
            # old 500 ms TTS ownership window would keep truthful mic bars dark.
            self._last_tts_level_t = 0.0
            self._cancel_idle_scheduler()
            self._orb.show(mode="listen")
            return
        self._wake_candidate_active = False
        # Retract a rejected or gate-dropped preview. Only an authoritative
        # VoiceSessionStarted owns the bar; WakeWordDetected alone is still a
        # pre-session preview and can legitimately be rolled back.
        if self._voice_session_active:
            return
        self._last_state = self._wake_preview_origin_state
        if self._hide_on_idle:
            self._orb.hide()
        else:
            self._orb.show(mode="idle")

    async def _on_wake_word_detected(self, event: WakeWordDetected) -> None:
        """Pop the orb on the earliest confirmed wake signal."""
        log.info("OrbBridge._on_wake_word_detected: keyword=%s", event.keyword)
        prev_state = self._last_state
        if not self._wake_candidate_active:
            self._wake_preview_origin_state = prev_state
        self._last_state_trace_id = str(event.trace_id)
        self._suppress_show_until_session = False
        was_preview = self._wake_candidate_active
        self._wake_candidate_active = False
        self._last_tts_level_t = 0.0
        self._last_state = "LISTENING"
        if not was_preview and prev_state != "LISTENING":
            # Fresh reveal (no candidate preview preceded it): drop the wake
            # word's leftover envelope. After a preview the user may already be
            # mid-command — re-zeroing would dip the live bars.
            self._clear_input_level()
        self._orb.show(mode="listen")
        if prev_state in ("IDLE", "ERROR", "PAUSED"):
            self._orb.play_animation("wave")
        self._cancel_idle_scheduler()

    async def _on_session_started(self, event: VoiceSessionStarted) -> None:
        """A genuine new voice session began (wake-word / hotkey / call).

        Releases the post-hangup suppression latch AND drives the surface into
        its listening look immediately — from THIS authoritative signal, not
        from the ``SystemStateChanged(IDLE→LISTENING)`` the pipeline emits right
        after.

        Why not rely on that state event: it is *derived* and lossy. When the
        supervisor's high-level state was already ``LISTENING`` (a stale prior
        teardown left it there, or the turn-state cycles LISTENING↔USER_SPEAKING
        without ever re-entering IDLE), ``set_state("LISTENING")`` is a no-op and
        NO ``SystemStateChanged`` is published. The bridge then saw nothing until
        ``THINKING`` and the bar only "woke up" once Jarvis started thinking —
        never while the user was speaking into it (live forensic 2026-06-21,
        session 1a3df62a: ``_on_session_started`` → the next bridge state was
        ``IDLE → THINKING`` with no LISTENING in between).

        ``VoiceSessionStarted`` is the authoritative "the user is being listened
        to now" signal (the pipeline opens the mic + sets LISTENING immediately
        after publishing it), so the listening visual is driven from here.
        ``_last_state`` is set to ``LISTENING`` (not ``IDLE``) so the genuine
        ``SystemStateChanged(LISTENING)`` that normally follows is a clean
        same-state no-op rather than a second show, and so mic loudness is
        forwarded to the equalizer (gated on ``_last_state == "LISTENING"``) from
        the very first word.
        """
        log.info("OrbBridge._on_session_started: session=%s", event.session_id)
        self._voice_session_active = True
        # A real session outranks the dictation lane. Releasing it here means a
        # dictation flag that somehow survived its own completion cannot follow
        # the user into the next voice turn (stray mic-level routing, a
        # suppressed wake preview) — the session is the authoritative owner.
        self._dictation_active = False
        self._dictation_transcribing = False
        self._cancel_dictation_failsafe()
        self._cancel_dictation_standdown()
        prev_state = self._last_state
        self._suppress_show_until_session = False
        was_preview = self._wake_candidate_active
        self._wake_candidate_active = False
        self._last_tts_level_t = 0.0
        self._last_state = "LISTENING"
        if not was_preview and prev_state != "LISTENING":
            # Fresh reveal only (hotkey / call): after a wake preview or the
            # confirmed wake the envelope was already cleared and the user may
            # be mid-command — re-zeroing would dip the live bars.
            self._clear_input_level()
        # Enter the listening look now — robust to a deduplicated LISTENING state.
        self._orb.show(mode="listen")
        if prev_state in ("IDLE", "ERROR", "PAUSED"):
            self._orb.play_animation("wave")
        # Fresh turn: clear any transcript/reply left over from a prior session
        # and open an empty live-transcript bubble, mirroring the LISTENING
        # branch of ``_on_state`` (a session never resumes a paused completion).
        self._listening_transcript_text = ""
        self._last_response_text = ""
        self._show_listening_transcript("")
        self._completion_continuation = False
        self._cancel_idle_scheduler()

    async def _on_session_ended(self, event: VoiceSessionEnded) -> None:
        """A voice session ended (hangup / idle-timeout / shutdown / error).

        Arms the suppression latch so any stray active-state transition from
        an in-flight turn (a brain reply that was mid-flight when the user said
        "auflegen") cannot pop the mascot back. The actual hide is performed by
        the IDLE transition that the pipeline emits immediately after this
        event (preserving the existing salute/grace animation); the latch only
        prevents the resurrection that follows.
        """
        self._voice_session_active = False
        self._wake_candidate_active = False
        log.info(
            "OrbBridge._on_session_ended: session=%s reason=%s — late active "
            "states suppressed until next wake.",
            event.session_id,
            event.hangup_reason,
        )
        self._suppress_show_until_session = True
        # Defense in depth: a persistent (always-on) bar must drop to its idle
        # look the instant a session ends, not only when the follow-up
        # SystemStateChanged(IDLE) arrives. That state edge can be skipped or
        # delayed — the supervisor's turn-state may never have been a real
        # LISTENING→IDLE edge (so set_state("IDLE") is a no-op and nothing is
        # published), or a stalling realtime teardown delays it — which froze
        # the JarvisBar on its "listening" look after a bar-X hangup (live
        # 2026-07-23). show(mode="idle") is idempotent and NOT gated by the
        # suppression latch (that only blocks ACTIVE-state repaints), so the
        # genuine IDLE transition, if it still arrives, is a harmless same-mode
        # repaint. The idle-animation scheduler stays owned by that transition.
        if not self._hide_on_idle:
            try:
                self._orb.show(mode="idle")
            except Exception as exc:  # noqa: BLE001
                log.debug("session-ended idle repaint failed: %s", exc)

    async def _on_voice_boot_status(self, event: VoiceBootStatus) -> None:
        """Release the Jarvis Bar only when voice is genuinely usable.

        The normal ``ready=True`` event is emitted after wake + VAD + TTS are
        initialized. ``voice_unavailable`` and ``watchdog_timeout`` are degraded
        web-UI escape hatches, not permission to advertise a working microphone.
        ``ready=False`` (warm-up start) leaves the gate closed.
        """
        if not event.voice_usable:
            return
        self._voice_usable = True
        self._release_bar_startup_gate(event.detail or "voice-ready")

    def _release_bar_startup_gate(self, reason: str) -> None:
        """Release a boot-gated bar and repair legacy visible surfaces once."""
        if self._boot_visibility_released:
            return
        try:
            release = getattr(self._orb, "release_startup_gate", None)
            was_gated = bool(release()) if callable(release) else False

            # A non-persistent bar needs its gate released too, but remains
            # withdrawn while idle. Mascot/Null surfaces expose no gate and stay
            # untouched. An already-visible legacy bar gets the existing safe
            # z-order repair instead.
            if not self._hide_on_idle and not was_gated:
                reassert = getattr(self._orb, "reassert_z_order", None)
                if callable(reassert):
                    reassert()
                else:
                    mode = str(getattr(self._orb, "_mode", "idle") or "idle")
                    self._orb.show(mode)
            self._boot_visibility_released = True
            log.info(
                "Overlay startup visibility released after voice became usable (%s).",
                reason,
            )
        except Exception:  # noqa: BLE001
            # Leave the latch open to a later genuine readiness event instead of
            # converting a transient surface error into a permanent hidden bar.
            log.debug("overlay startup visibility release failed", exc_info=True)

    async def _on_state(self, event: SystemStateChanged) -> None:
        # Lazily pin the backend loop (idempotent) so Tk-thread gestures can
        # marshal onto it. _on_state fires on every transition, long before the
        # orb is clickable, so the loop is always captured in time.
        self._remember_loop()
        state = event.new_state
        if state != "IDLE":
            # Any authoritative non-idle state supersedes a visual-only wake
            # preview. Keeping the latch beyond this edge could let a late mic
            # sample overwrite THINKING/SPEAKING bars.
            self._wake_candidate_active = False
        # ADR-0016: remember the trace_id so the next visibility snapshot
        # can correlate back to the state-transition that triggered it.
        self._last_state_trace_id = str(event.trace_id)
        log.info("OrbBridge._on_state: %s → %s", self._last_state, state)
        # Session-lifecycle latch: after a session ended, ignore stray active
        # states emitted by a late in-flight turn — keep the mascot hidden
        # until the next VoiceSessionStarted. Checked BEFORE updating
        # ``_last_state`` so a real new session (IDLE → LISTENING) is still a
        # clean transition. (orb-resurrection bug 2026-05-29.)
        if self._suppress_show_until_session and state in _ACTIVE_VOICE_STATES:
            # Do NOT hide here: the IDLE transition the pipeline emits right
            # after VoiceSessionEnded already hides the orb (with its salute/
            # grace animation intact). Re-hiding on every stray would either be
            # a no-op or cut that animation short. Suppressing the *show* is the
            # whole job — the orb simply stays hidden.
            log.info(
                "OrbBridge: stray %s outside live session suppressed — mascot stays hidden.",
                state,
            )
            return
        # The live-session mirror is updated only for states that survived the
        # suppression latch above, and deliberately so. A stray LISTENING from
        # an in-flight turn after a hangup is, by this handler's own definition,
        # NOT a live session — but it used to set this flag anyway, and nothing
        # ever cleared it again on a configuration where the session machine
        # never returns to IDLE by itself (``session_idle_timeout_s = 0`` with no
        # hangup key). The flag then permanently outranked the dictation lane,
        # so every dictation reveal after that point was dropped with only a
        # ``log.debug`` behind it: the shortcut did nothing, visibly or
        # otherwise, until the app was restarted. Suppressed states must not
        # resurrect the mirror any more than they resurrect the mascot.
        if state == "LISTENING":
            # An authoritative supervisor edge can arrive after a late bridge
            # attach even if VoiceSessionStarted itself was missed.
            self._voice_session_active = True
        elif state in {"IDLE", "ERROR", "PAUSED"}:
            self._voice_session_active = False
        # No-op if this isn't a real transition (the supervisor should already
        # filter this, but defensive programming)
        if state == self._last_state:
            return
        prev_state = self._last_state
        self._last_state = state

        # On every state transition: kill any running 'think' bubble — it
        # doesn't match reality in any other state and would otherwise linger.
        self._orb.stop_animation("think")
        # Kill the hangup task if a new state comes in while the salute is playing
        if self._hangup_task and not self._hangup_task.done():
            self._hangup_task.cancel()
            self._hangup_task = None
        if self._completion_task and not self._completion_task.done():
            self._completion_task.cancel()
            self._completion_task = None

        # Stop the talking-mouth overlay whenever we leave SPEAKING. Mouth is
        # explicitly tied to "Jarvis is talking" (audio actually playing),
        # not to the bubble or to listening/thinking.
        if prev_state == "SPEAKING" and state != "SPEAKING":
            stop_mouth = getattr(self._orb, "stop_mouth_animation", None)
            if callable(stop_mouth):
                try:
                    stop_mouth()
                except Exception as exc:  # noqa: BLE001
                    log.debug("stop_mouth_animation failed: %s", exc)

        if state == "LISTENING":
            self._orb.show(mode="listen")
            if prev_state in ("IDLE", "ERROR", "PAUSED"):
                self._orb.play_animation("wave")
            # The pulsing listen-mode already signals "I'm hearing you"
            # visually. The bubble starts empty and fills with the live
            # transcript as TranscriptionUpdate events arrive. A fresh turn
            # also clears any reply text left over from the previous turn.
            # EXCEPTION: entering LISTENING from WAITING_FOR_COMPLETION
            # (paused-incomplete continuation) preserves the buffered text
            # so the bubble stays the same across the pause/continue cycle.
            if prev_state != "WAITING_FOR_COMPLETION":
                self._listening_transcript_text = ""
                self._last_response_text = ""
                self._show_listening_transcript("")
                self._completion_continuation = False
            self._cancel_idle_scheduler()
        elif state == "WAITING_FOR_COMPLETION":
            # User paused mid-sentence; the pipeline buffered an incomplete
            # fragment and is waiting for the rest. Keep the listen-mode
            # mascot pose and KEEP the bubble showing the buffered text —
            # the pipeline publishes a TranscriptionUpdate(is_final=True)
            # right after this transition with the merged buffer fragment,
            # so the bubble reflects the so-far-spoken sentence. Do NOT
            # transition to think-mode here; the brain has not been called.
            self._orb.show(mode="listen")
            self._completion_continuation = True
            self._cancel_idle_scheduler()
        elif state == "THINKING":
            # Brain has taken over the (possibly merged) prompt. End the
            # completion-continuation window so subsequent LISTENING entries
            # behave normally (fresh bubble for the next user utterance).
            self._completion_continuation = False
            self._orb.show(mode="think")
            self._orb.play_animation("think")
            # Show that Jarvis is thinking. The brain has no reply text yet,
            # so the bubble shows the thinking indicator instead of freezing
            # the user's own words (which left the user unsure anything was
            # happening). A reply arriving mid-THINKING swaps it in via
            # _on_response_generated.
            self._refresh_voice_bubble()
            self._cancel_idle_scheduler()
        elif state == "SPEAKING":
            # TTS synthesis is often still running here — the state flips to
            # SPEAKING before the first audio sample actually leaves the
            # speaker (0.5–2 s lead time). From the user's perspective that
            # silent lead time is still "processing", so the overlay stays on
            # the THINKING wave and only switches to the SPEAKING bars once
            # there is real sound — driven by the AudioOutFirst event (see
            # _on_audio_out_first). The mouth + the "nod" hang off the
            # AudioOutFirst event for the same reason. The bubble already shows
            # Jarvis' reply text now — the same source as the sidebar assistant
            # line.
            self._orb.show(mode="think")
            self._refresh_voice_bubble()
            self._cancel_idle_scheduler()
        elif state in ("IDLE", "ERROR", "PAUSED"):
            # Voice phase is over — drop the comment bubble immediately so it
            # does not outlive the mascot or stick around past the session.
            # Also clear any in-flight completion-continuation window.
            self._completion_continuation = False
            hide_comment = getattr(self._orb, "hide_comment", None)
            if callable(hide_comment):
                try:
                    hide_comment()
                except Exception as exc:  # noqa: BLE001
                    log.debug("hide_comment failed: %s", exc)

            if not self._hide_on_idle:
                # Persistent "show at all times" bar: a standalone always-on
                # element. EVERY non-active state (IDLE, and also ERROR / PAUSED)
                # shows the idle pill and NEVER withdraws the bar. Hiding it on
                # ERROR/PAUSED was a second "the always-on bar vanishes until the
                # next wake word" path (a transient STT/provider ERROR or a manual
                # pause took it off screen). The salute + idle-animation scheduler
                # belong only to a genuine return to IDLE.
                self._orb.show(mode="idle")
                if state == "IDLE":
                    if prev_state == "SPEAKING":
                        self._orb.play_animation("salute")
                    self._start_idle_scheduler()
                return
            # Three cases, three delayed hides — never an instant hide out of
            # an active voice state, or the user won't see the mascot at all
            # during short sessions (e.g. an STT silence timeout).
            if prev_state == "SPEAKING" and state == "IDLE":
                self._orb.play_animation("salute")
                self._hangup_task = asyncio.create_task(
                    self._delayed_hide(SALUTE_DURATION_S),
                    name="orb-hangup-salute",
                )
            elif prev_state in ("LISTENING", "THINKING") and state == "IDLE":
                self._hangup_task = asyncio.create_task(
                    self._delayed_hide(GRACE_HIDE_DURATION_S),
                    name="orb-grace-hide",
                )
            else:
                self._orb.hide()
            if state != "IDLE":
                self._cancel_idle_scheduler()

    async def _on_listening_started(self, _event: ListeningStarted) -> None:
        """Reset the listening transcript surface for a fresh utterance.

        Suppress the reset during a completion-buffer continuation: when the
        previous turn was paused mid-sentence (WAITING_FOR_COMPLETION), we
        want the bubble to keep the buffered text so the user sees the same
        single bubble grow across the pause + continuation.
        """
        if self._last_state != "LISTENING":
            return
        if self._listening_transcript_text and getattr(self, "_completion_continuation", False):
            return
        self._listening_transcript_text = ""
        self._last_response_text = ""
        self._show_listening_transcript("")

    async def _on_transcription_update(self, event: TranscriptionUpdate) -> None:
        # Accept transcript events across the entire user-side lifecycle
        # (LISTENING, USER_SPEAKING, WAITING_FOR_FINAL_TRANSCRIPT,
        # WAITING_FOR_COMPLETION). Outside this window (THINKING/SPEAKING/
        # IDLE) the bubble must NOT be repainted with stale user text —
        # the brain has already taken over. ONE exception: the FINAL
        # transcript of the turn being answered right now. Realtime providers
        # deliver (chunks of) the final user transcript concurrently with the
        # flip to THINKING; dropping those froze the bubble on the first
        # fragment ("Was" instead of the whole question). While no reply text
        # exists yet, the authoritative user text is never stale.
        if self._last_state not in _USER_SIDE_BUBBLE_STATES:
            late_final_ok = (
                bool(getattr(event, "is_final", False))
                and self._last_state == "THINKING"
                and not self._last_response_text
            )
            if not late_final_ok:
                return
        if _is_transcript_boilerplate(event.text):
            log.info(
                "OrbBridge suppressed STT boilerplate transcript: %r",
                event.text[:80],
            )
            self._listening_transcript_text = ""
            self._show_listening_transcript("")
            return
        # Both is_final=True and is_final=False are pipeline snapshots, not
        # deltas — see module-level note above. Mirror them 1:1, like the
        # Desktop App's TranscriptionView does, so the two surfaces never
        # diverge.
        self._listening_transcript_text = event.text.strip()
        self._show_listening_transcript(self._listening_transcript_text)

    async def _on_response_generated(self, event: ResponseGenerated) -> None:
        """Capture Jarvis's reply so the orb bubble can show it while speaking.

        Mirrors the sidebar assistant line. ResponseGenerated may arrive while
        the turn is still THINKING (reply ready, TTS not started) or already
        SPEAKING (TTS raced ahead of this event) — in both cases the bubble is
        repainted with the reply. Once the turn is over (IDLE/ERROR) the bubble
        is already hidden, so we leave it alone.
        """
        self._last_response_text = (event.text or "").strip()
        if self._last_state in ("THINKING", "SPEAKING"):
            self._refresh_voice_bubble()

    def _refresh_voice_bubble(self) -> None:
        """Render the right bubble text for the current voice state.

        LISTENING → the live user transcript.
        THINKING  → Jarvis's reply if it already arrived, else the thinking
                    indicator.
        SPEAKING  → Jarvis's reply; falls back to the thinking indicator (never
                    the user transcript) if the reply has not landed yet during
                    a brief reply/TTS race, so the bubble never regresses to
                    "only shows what you said".
        """
        state = self._last_state
        if state == "LISTENING":
            self._show_listening_transcript(self._listening_transcript_text)
        elif state in ("THINKING", "SPEAKING"):
            self._show_listening_transcript(self._last_response_text or THINKING_BUBBLE_TEXT)

    def _show_dictation_mode(self, mode: str) -> None:
        """Drive the current surface into a dictation coarse mode.

        Every surface this app ships renders both dictation modes natively: the
        JarvisBar as the equalizer and the orbital core, the mascot orb as a
        mic-driven halo and a steady work pulse (``overlay.mode_energy``). So on
        every display style a dictation is actually visible, which is the whole
        point of the feature.

        The fallback below is for a surface this bridge does not own — an older
        or third-party one that only knows the four voice modes. For such a
        surface the honest equivalent of "recording" is its listening look,
        which on the mascot carries no destructive affordance (it has no
        close-X — only drag and the four-click mute gesture), so falling back
        cannot arm one.

        It only works because a surface reports the rejection SYNCHRONOUSLY, on
        this thread. A surface that queues the call onto its own UI thread and
        validates there returns "successfully" and drops the mode later, where
        no exception can reach us — the failure that left the mascot showing
        nothing at all. ``OrbOverlay.show`` validates before it queues for
        exactly this reason.

        A surface that rejects both modes is left alone: a missing visual is
        cosmetic, and it must never break the bus.
        """
        try:
            self._orb.show(mode=mode)
            return
        except Exception as exc:  # noqa: BLE001 — a surface hiccup is cosmetic
            log.debug("OrbBridge dictation mode %r not supported: %s", mode, exc)
        try:
            self._orb.show(mode="listen")
        except Exception as exc:  # noqa: BLE001
            log.debug("OrbBridge dictation fallback show suppressed: %s", exc)

    async def _on_dictation_started(self, event: DictationStarted) -> None:
        """The dictation key went down — raise the bar NOW.

        This is the whole point of the event: the bar must rise at key-down
        speed, exactly like it does on a wake word, instead of waiting for the
        first partial transcript (a partial interval plus an STT round-trip —
        and for a short press no partial ever arrives, so the bar never came up
        at all).

        Mirrors ``_on_session_started`` minus every voice-state mutation:
        ``_last_state`` stays whatever it is, because dictation is not a voice
        turn and must not forge one. Deliberately NOT gated on
        ``_last_state == "IDLE"`` either — dictation never drives that label, so
        a stale value left by a missed IDLE edge would block every dictation bar
        with no visible cause (the BUG-037 failure shape). The authoritative
        ``_voice_session_active`` flag (set and cleared by the session lifecycle
        events) is the only thing that outranks a dictation, and the whole lane
        uses that same guard so its four handlers cannot disagree.

        The boot startup gate still applies: a surface that has not been
        released by ``VoiceBootStatus`` stores the mode and stays withdrawn,
        exactly as it does for a wake word (AP-26).
        """
        if self._voice_session_active:
            log.debug("OrbBridge dictation reveal skipped: a voice session owns the bar")
            return
        log.info("OrbBridge._on_dictation_started: target=%s", getattr(event, "target", ""))
        self._cancel_dictation_standdown()
        self._dictation_active = True
        self._dictation_transcribing = False
        # A fresh input affordance supersedes stale output recency from a
        # preceding turn, and the mic envelope must start from silence so the
        # bars only ever show what is said AFTER the bar is visible.
        self._last_tts_level_t = 0.0
        self._clear_input_level()
        self._cancel_idle_scheduler()
        self._show_dictation_mode("dictate")
        self._show_listening_transcript("")
        self._arm_dictation_failsafe()

    async def _on_dictation_transcript(self, event: DictationTranscript) -> None:
        """Show the live dictation text on the bar.

        Dictation runs in its own lane — it never raises SystemStateChanged, so
        none of the voice-state handling above sees it and none of the four
        voice modes change meaning. It gets its own coarse modes, which the
        renderer paints as the equalizer while recording (the mic level is being
        fed, so the bars actually move) and as the orbital core while the text
        is being produced.

        A live voice session ALWAYS wins: dictation cannot start while one is
        running, but a race at the boundary must not repaint a real turn.

        The reveal itself belongs to ``_on_dictation_started``; this handler
        only re-asserts the recording look for a dictation that is genuinely
        still recording. Once ``DictationTranscribing`` has arrived it must NOT
        drag the bar back to the mic look — a final partial can land after the
        key is released.

        Same guard as the rest of the lane (``_voice_session_active`` only, not
        ``_last_state``) so the four dictation handlers can never disagree about
        who owns the bar.
        """
        if self._voice_session_active:
            return
        if getattr(event, "is_final", False):
            # The completion handler owns the end of a dictation — it knows the
            # outcome and whether anything needs saying.
            return
        text = (event.text or "").strip()
        if not self._dictation_transcribing:
            self._show_dictation_mode("dictate")
            if not self._dictation_active:
                # A transcript with no preceding DictationStarted (an older
                # pipeline, a lost event): this reveal has to arm the fail-safe
                # itself, or a lane opened here would have nothing bounding it.
                self._dictation_active = True
                self._arm_dictation_failsafe()
        self._show_listening_transcript(text)

    async def _on_dictation_transcribing(self, _event: DictationTranscribing) -> None:
        """Recording stopped, the transcription is running — show the work.

        The mic feed has ended here, so leaving the equalizer up would claim the
        bar is still listening when it is not. The dedicated
        ``dictate_transcribing`` mode renders the orbital core instead, and
        keeps the click surface inert exactly like the recording mode does.
        """
        if not self._dictation_active or self._voice_session_active:
            return
        self._dictation_transcribing = True
        self._show_dictation_mode("dictate_transcribing")

    async def _on_dictation_completed(self, event: DictationCompleted) -> None:
        """Dictation finished — leave the working look AT ONCE, then stand down.

        The thinking core represents work in flight. By the time this event
        arrives there is none: the transcription is done and the text has
        already been pasted into the user's field. Leaving
        ``dictate_transcribing`` up for the dwell is what made the bar keep
        "thinking" for one and a half seconds AFTER the words had visibly
        landed — a claim about the app's state that the screen already
        contradicted (reported 2026-07-29). So the mode is resolved here, by
        the outcome, and never left over from the previous phase:

        * The words arrived (``was_delivered``) → the bar leaves the working
          look at once and stands down. It never raises the failure mark here,
          not even when the outcome brought a sentence with it: this surface
          carries no text (``show_listening_transcript`` is a no-op on both bar
          overlays), so the red cross is not a footnote next to an explanation
          — it IS the explanation, and "your dictation was lost" is the wrong
          one for a paste that landed. A sentence only buys the DWELL, which is
          what keeps it readable on the surfaces that do render it.
        * The words did not arrive (``clipboard_only`` — the OS blocked the
          paste — ``unavailable``, ``empty``, ``failed``, or an outcome from a
          newer install we do not know) → the notice look, which is what that
          mark has always meant. It stays up long enough to read a sentence
          when there is one, and briefly when there is not: a dictation that
          vanishes in silence is indistinguishable from a dead shortcut.
        """
        if not getattr(self, "_dictation_active", False):
            return
        self._dictation_active = False
        self._dictation_transcribing = False
        self._cancel_dictation_failsafe()
        detail = (event.detail or "").strip()
        outcome = (event.outcome or "").strip()
        arrived = was_delivered(outcome)
        quiet = arrived and not detail
        # A delivered dictation with nothing to add gets NO echo bubble: the
        # words are already in the field the user is looking at, and a bubble
        # raised for one frame before the stand-down clears it is a flicker,
        # not a receipt.
        self._show_listening_transcript("" if quiet else (detail or (event.text or "").strip()))
        # Whatever raised the bar must be able to lower it. This guard is
        # deliberately the SAME one ``_on_dictation_started`` uses — an
        # asymmetric pair (raise on any state, lower only from IDLE) would leave
        # the bar lit whenever ``_last_state`` was stale, which is a real
        # possibility because dictation never touches the voice state machine.
        if self._voice_session_active:
            return
        if arrived:
            # Nothing is in flight and nothing failed, so the bar rests NOW
            # rather than sitting in the working look for the dwell. When a
            # sentence came with it, the stand-down is merely deferred so the
            # surfaces that can render it keep it up long enough to read; the
            # bar itself is already back at rest either way.
            delay = 0.0 if quiet else DICTATION_OUTCOME_DWELL_S
            if not quiet:
                self._rest_surface()
        else:
            self._show_notice_mode()
            delay = (
                DICTATION_OUTCOME_DWELL_S if detail else DICTATION_NOTHING_BACK_DWELL_S
            )
        try:
            self._schedule_dictation_standdown(delay)
        except Exception as exc:  # noqa: BLE001
            log.debug("OrbBridge dictation stand-down suppressed: %s", exc)

    async def _on_dictation_refused(self, event: DictationRefused) -> None:
        """The dictation shortcut was pressed and DECLINED — say so on screen.

        This is the whole reported bug. Every refusal used to end as a
        ``log.info`` in a file the desktop app cannot open (CLAUDE.md §9: the
        window is a WebView with no dev tools), so on the maintainer's
        configuration — one wake word, ``session_idle_timeout_s = 0``, no hangup
        key — a voice conversation stayed open all day and from then on EVERY
        press of the dictation key was refused in complete silence. Nothing on
        the bar, no sound, no toast: indistinguishable from a dead shortcut.

        Deliberately NOT gated on ``_voice_session_active``, unlike the rest of
        the lane. The single most common refusal reason IS a live voice session
        (``voice_session_active``), so that guard would swallow exactly the
        message this event exists to deliver and reproduce the invisible refusal
        one layer up. It is also why the stand-down below RESTORES the session's
        look instead of standing the surface down to idle: a refusal is normally
        raised while a conversation legitimately owns the surface, and taking
        that conversation off screen to report a declined keypress would trade
        one invisible state for another.

        The one thing it does NOT answer is a refusal that reports success:
        ``DICTATION_INERT_REFUSALS``. Those are dropped before anything is
        painted or torn down — see the comment at the guard.
        """
        reason = (event.reason or "").strip() or "unspecified"
        detail = (event.detail or "").strip() or DICTATION_REFUSAL_FALLBACK_TEXT
        log.info("OrbBridge._on_dictation_refused: reason=%s — %s", reason, detail)
        if reason in DICTATION_INERT_REFUSALS:
            # "A dictation is already recording" is not a failure — it is the
            # statement that the turn the user is watching is alive and will
            # deliver. Painting the refusal look here marks that turn as lost,
            # and the teardown below would drop ``_dictation_active``, so the
            # completion that follows is swallowed by its own guard and the
            # mark never comes off. That is how a dictation which transcribed
            # cleanly and pasted successfully ended under a red cross on
            # Windows, where the polling hotkey backend re-reports a held chord
            # and one edge lands next to the release.
            log.debug(
                "OrbBridge: refusal %r left the running dictation untouched.",
                reason,
            )
            return
        # Nothing is recording: a refusal means the session never opened. Drop
        # the lane's latches and both of its timers so a stale one can neither
        # fight this notice nor close the surface out from under it.
        self._dictation_active = False
        self._dictation_transcribing = False
        self._cancel_dictation_failsafe()
        self._cancel_dictation_standdown()
        self._show_notice_mode()
        self._show_listening_transcript(detail)
        self._schedule_notice_standdown(DICTATION_REFUSAL_DWELL_S)

    def _rest_surface(self) -> None:
        """Drop the working look NOW, without closing the surface yet.

        For a delivered dictation that still has a sentence to show. The
        stand-down would do both — rest AND clear the transcript bubble — and
        the bubble is exactly what has to survive the dwell. Hiding here is
        wrong for the same reason: on the mascot it would strand the bubble
        next to nothing, and the stand-down a few seconds later hides anyway.
        Resting is the honest middle: nothing is in flight, and nothing failed.
        """
        try:
            self._orb.show(mode="idle")
        except Exception as exc:  # noqa: BLE001 — a missed repaint is cosmetic
            log.debug("OrbBridge dictation rest repaint suppressed: %s", exc)

    def _show_notice_mode(self) -> None:
        """Drive the current surface into its brief "that did not happen" look.

        Unlike ``_show_dictation_mode`` there is deliberately NO degradation to
        the listening look for a surface that rejects the mode. A listening look
        on a REFUSED dictation is not an approximation, it is the opposite of
        the truth: it would claim the microphone is recording at the exact
        moment nothing is. Such a surface is simply left as it was and still
        receives the sentence through the transcript line.

        Like the dictation modes, this relies on the surface validating
        SYNCHRONOUSLY (``OrbOverlay.show`` raises before it queues); a surface
        that validates on its own UI thread returns successfully and drops the
        mode where no exception can reach us.
        """
        try:
            self._orb.show(mode="notice")
        except Exception as exc:  # noqa: BLE001 — a missing look is cosmetic
            log.debug("OrbBridge notice mode unsupported by this surface: %s", exc)

    def _current_voice_mode(self) -> str:
        """The coarse mode the voice lane would be painting right now.

        Mirrors ``_on_state``'s mapping exactly, SPEAKING included — that
        handler also paints ``think`` there, because the surface decides for
        itself when real audio takes the equalizer over (``AudioOutFirst`` /
        ``renderer.visual_mode``), not the state label.
        """
        state = self._last_state
        if state in ("LISTENING", "WAITING_FOR_COMPLETION"):
            return "listen"
        if state in ("THINKING", "SPEAKING"):
            return "think"
        return "idle"

    def _schedule_notice_standdown(self, delay_s: float) -> None:
        """Clear a notice after its dwell and put the surface back where it was.

        Reuses the dictation stand-down slot on purpose, so the two can never
        both be pending: a refusal ends any dictation lane, and a real dictation
        starting afterwards cancels the notice through the same
        ``_cancel_dictation_standdown`` every other handler already calls.

        The restore is the part that differs from the dictation stand-down. That
        one always returns the surface to idle, which is right when a dictation
        it revealed has finished. A notice is usually raised OVER something —
        most often the live voice session that caused the refusal — so it must
        hand the surface back rather than close it.
        """
        self._cancel_dictation_standdown()

        async def _standdown() -> None:
            try:
                await asyncio.sleep(delay_s)
            except asyncio.CancelledError:
                return
            if self._dictation_active:
                # A real dictation started inside the dwell and owns the
                # surface now — its own lane will stand it down.
                return
            self._show_listening_transcript("")
            restore = self._current_voice_mode() if self._voice_session_active else "idle"
            try:
                if restore == "idle" and self._hide_on_idle:
                    self._orb.hide()
                else:
                    self._orb.show(mode=restore)
            except Exception as exc:  # noqa: BLE001
                log.debug("OrbBridge notice stand-down repaint suppressed: %s", exc)

        self._dictation_standdown_task = asyncio.create_task(
            _standdown(), name="orb-notice-standdown"
        )

    def _cancel_dictation_standdown(self) -> None:
        """Drop a pending stand-down so a new dictation is not closed by the
        previous one's timer (press → release → press again inside the dwell)."""
        task = getattr(self, "_dictation_standdown_task", None)
        if task is not None and not task.done():
            task.cancel()
        self._dictation_standdown_task = None

    def _cancel_dictation_failsafe(self) -> None:
        task = getattr(self, "_dictation_failsafe_task", None)
        if task is not None and not task.done():
            task.cancel()
        self._dictation_failsafe_task = None

    def _arm_dictation_failsafe(self) -> None:
        """Guarantee the dictation bar comes down even if nothing tells it to.

        ``DictationCompleted`` normally ends the lane. This is the backstop for
        when it does not arrive at all — the session crashed before publishing,
        a subscriber was dropped, the surface was swapped mid-dictation. Without
        it the bar would sit there lit forever with no visible cause and no way
        back, which is the single worst failure shape this codebase has (the bar
        is the only proof the app is alive). The deadline is far longer than any
        real dictation, so it can never cut a genuine one short.
        """
        self._cancel_dictation_failsafe()

        async def _expire() -> None:
            try:
                await asyncio.sleep(DICTATION_MAX_VISIBLE_S)
            except asyncio.CancelledError:
                return
            if not self._dictation_active:
                return
            log.warning(
                "OrbBridge dictation fail-safe fired after %.0fs — no completion "
                "event arrived; standing the bar down.",
                DICTATION_MAX_VISIBLE_S,
            )
            self._dictation_active = False
            self._dictation_transcribing = False
            if self._voice_session_active:
                # A session took over and owns the bar — nothing to clean up.
                return
            # Stand down directly rather than through _schedule_dictation_
            # standdown: that path also refuses when ``_last_state`` is not
            # IDLE, and a stale state left by a missed edge is exactly the
            # situation this fail-safe exists for.
            self._show_listening_transcript("")
            try:
                if self._hide_on_idle:
                    self._orb.hide()
                else:
                    self._orb.show(mode="idle")
            except Exception as exc:  # noqa: BLE001
                log.debug("OrbBridge dictation fail-safe repaint suppressed: %s", exc)

        self._dictation_failsafe_task = asyncio.create_task(
            _expire(), name="orb-dictation-failsafe"
        )

    def _schedule_dictation_standdown(self, delay_s: float) -> None:
        """Return the bar to its resting look after a dictation."""
        self._cancel_dictation_standdown()

        async def _standdown() -> None:
            try:
                await asyncio.sleep(delay_s)
            except asyncio.CancelledError:
                return
            # A NEW dictation or a real voice session has taken the bar in the
            # meantime — it owns the look now. Deliberately NOT gated on
            # ``_last_state``: dictation never touches the voice state machine,
            # so a stale label there would strand the bar in the dictation look
            # with nothing left to clear it (see _on_dictation_completed).
            if self._dictation_active or self._voice_session_active:
                return
            self._show_listening_transcript("")
            try:
                if self._hide_on_idle:
                    self._orb.hide()
                else:
                    self._orb.show(mode="idle")
            except Exception as exc:  # noqa: BLE001
                log.debug("OrbBridge dictation idle repaint suppressed: %s", exc)

        self._dictation_standdown_task = asyncio.create_task(
            _standdown(), name="orb-dictation-standdown"
        )

    def _show_listening_transcript(self, text: str) -> None:
        show_transcript = getattr(self._orb, "show_listening_transcript", None)
        if not callable(show_transcript):
            return
        try:
            show_transcript(text, VOICE_BUBBLE_DURATION_MS)
        except Exception as exc:  # noqa: BLE001
            log.debug("OrbBridge listening transcript bubble suppressed: %s", exc)

    async def _on_audio_out_first(self, _event: AudioOutFirst) -> None:
        """First TTS audio sample reached the speaker — NOW switch the overlay
        to the speaking equalizer (bars) and start the talking-mouth + nod.

        Synced to the actual audible start instead of the speculative SPEAKING
        state-transition that fires 0.5–2 s earlier, before TTS synthesis even
        produces sound. Until this event the overlay stays on the THINKING wave
        (set on the SPEAKING transition), so the silent synthesis lead-in reads
        as "still processing" rather than as speaking. The transcript bubble is
        left as-is (already showing Jarvis's reply); no personality quip is
        popped over it.
        """
        if self._last_state != "SPEAKING":
            return
        log.info("OrbBridge._on_audio_out_first → speaking overlay + mouth")
        self._orb.show(mode="speak")
        self._orb.play_animation("nod")
        start_mouth = getattr(self._orb, "start_mouth_animation", None)
        if callable(start_mouth):
            try:
                start_mouth(60_000)
            except Exception as exc:  # noqa: BLE001
                log.debug("start_mouth_animation on AudioOutFirst failed: %s", exc)

    async def _delayed_hide(self, delay_s: float) -> None:
        """Wait out the salute/grace animation, then take the surface down.

        Persistence gate: a persistent "show at all times" bar must NEVER be
        withdrawn — it returns to the idle pill instead. The salute/grace
        callers in ``_on_state`` only reach here for a hide-on-idle surface (the
        persistent branch returns earlier), but ``_on_background_completed``
        schedules this hide for BOTH regimes: a finished Jarvis-Agent task pops
        the bar to 'speak' and then this fires. Without the gate that
        unconditional ``hide()`` withdrew the always-on bar — the "bar vanishes
        after I talk to it, only the wake word brings it back" path, the same
        class as the consolidate restore-trap but via the one hide() the
        ``_on_state`` persistence gate never covered. ``hide()`` itself stays
        unconditional (swap_overlay / shutdown still need a real withdraw); the
        gate belongs here at the caller that knows the regime.
        """
        try:
            await asyncio.sleep(delay_s)
            if self._hide_on_idle:
                self._orb.hide()
            else:
                self._orb.show(mode="idle")
            # Only (re-)start the idle scheduler if we're still in the IDLE
            # state (no new wake sequence came in during the salute).
            if self._last_state == "IDLE":
                self._start_idle_scheduler()
        except asyncio.CancelledError:
            pass

    async def _on_background_completed(self, _event: JarvisAgentBackgroundCompleted) -> None:
        """Briefly surface the mascot when an async task finishes.

        This is UI-only. It does not start or end the speech session, so the
        conversation/task context remains untouched.
        """
        if self._last_state not in ("IDLE", "ERROR", "PAUSED"):
            return
        self._orb.show(mode="speak")
        self._orb.play_animation("nod")
        if self._completion_task and not self._completion_task.done():
            self._completion_task.cancel()
        self._completion_task = asyncio.create_task(
            self._delayed_hide(2.5),
            name="orb-background-completed-pop",
        )

    # --- Idle-Animation-Scheduler --------------------------------------

    def _start_idle_scheduler(self) -> None:
        """Starts a background task that randomly plays idle animations.

        Deliberately NOT in show() mode — the orb is hidden here and the
        window is not visible. Idle animations are only visible when the
        user has the orb stickily displayed (e.g. during vision live mode)
        or when an upcoming phase introduces an always-on mode. We schedule
        them anyway — it costs nothing and is immediately visible once
        switched to always-on.
        """
        if not self._idle_enabled:
            return
        if self._idle_task and not self._idle_task.done():
            return
        self._idle_task = asyncio.create_task(
            self._idle_loop(),
            name="orb-idle-animation-scheduler",
        )

    def _cancel_idle_scheduler(self) -> None:
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None

    async def _idle_loop(self) -> None:
        """Plays a random animation from the idle pool every 30-90s."""
        try:
            while True:
                wait = self._rng.uniform(IDLE_MIN_INTERVAL_S, IDLE_MAX_INTERVAL_S)
                await asyncio.sleep(wait)
                if self._last_state != "IDLE":
                    return
                name = self._rng.choice(IDLE_ANIMATION_POOL)
                log.debug("Idle scheduler: %s", name)
                self._orb.play_animation(name)
        except asyncio.CancelledError:
            pass

    # --- Live loudness → equalizer bars (mic + TTS precedence) ---------

    _TTS_OWNS_BARS_S = 0.5  # mic is muted this long after the last TTS level

    def _clear_input_level(self) -> None:
        """Zero the mic-level envelope AND the surface bars on a listening
        reveal. The wake word itself was loud enough to leave a decaying
        envelope in the normalizer; without this it renders as a phantom
        swing the instant the bar appears — the bar must only ever show what
        the user says AFTER it is visible."""
        try:
            from jarvis.audio import mic_level

            mic_level.clear()
        except Exception:  # noqa: BLE001 — visual hygiene must never break the bus
            log.debug("mic_level clear failed", exc_info=True)
        try:
            self._orb.set_level(0.0)
        except Exception:  # noqa: BLE001
            log.debug("surface level clear failed", exc_info=True)

    def _note_tts_level(self, level: float) -> None:
        """Forward live TTS loudness and remember that Jarvis owns the bars.

        ``level_tap`` is process-local. The Windows/Linux in-process overlays
        subscribe directly, but the macOS companion surface cannot, so relying
        on the surface's own subscription leaves its speaking bars flat. The
        bridge already spans that process boundary through ``set_level``; send
        the same normalized value through it and retain the recency guard that
        suppresses simultaneous silent-mic updates.
        """
        self._last_tts_level_t = time.monotonic()
        try:
            self._orb.set_level(level)
        except Exception:  # noqa: BLE001
            log.debug("TTS level forward to surface failed", exc_info=True)

    def _on_mic_level(self, level: float) -> None:
        """Forward the live mic loudness to the active surface's bars.

        The level comes from ``jarvis.audio.mic_level`` (the VAD feeds it from
        the audio already captured for STT — no second stream). It is forwarded
        only when (a) NO TTS output is currently playing — Jarvis's voice owns
        the bars while it speaks, and the state label is unreliable because
        continue-listening flips to LISTENING mid-playback — and (b) the coarse
        state is LISTENING, a wake candidate is previewing, or a dictation is
        recording. Works for whichever surface is current.

        The dictation clause is what makes the level indicator move while you
        dictate: the dictation session feeds the very same ``mic_level`` channel,
        but it never enters a voice state, so without it every sample was
        dropped and the equalizer stood still on a visible bar."""
        if time.monotonic() - self._last_tts_level_t < self._TTS_OWNS_BARS_S:
            return  # TTS is making sound → it drives the bars, not the silent mic
        candidate_listening = self._wake_candidate_active and self._last_state in {
            "IDLE",
            "ERROR",
            "PAUSED",
        }
        if (
            self._last_state != "LISTENING"
            and not candidate_listening
            and not self._dictation_active
        ):
            return
        try:
            self._orb.set_level(level)
        except Exception:  # noqa: BLE001
            log.debug("mic level forward to surface failed", exc_info=True)

    # --- Live surface swap (display-style toggle) ----------------------

    def set_surface(self, surface) -> None:
        """Repoint the bridge at a NEW overlay surface for a live style swap.

        Reuses the single bridge — no second subscription, no detach. Swaps the
        ``_orb`` reference and re-injects the mute-toggle + visible-feedback
        publishers. The mic-level subscription (registered once in ``attach``)
        forwards to whichever surface is current, so there is nothing to rebind.
        The caller tears the old surface down afterwards.
        """
        self._orb = surface
        # Visibility release is per surface, not merely per bridge. A user can
        # switch to "none" during warm-up and back to the cached boot bar after
        # ready; that original bar must then have its still-active gate released
        # without waiting for another VoiceBootStatus event.
        self._boot_visibility_released = False
        setter = getattr(surface, "set_on_mute_toggle", None)
        if callable(setter):
            setter(self._publish_mute_toggle)
        feedback_setter = getattr(surface, "set_feedback_publisher", None)
        if callable(feedback_setter):
            feedback_setter(self._publish_visible_feedback)
        show_window_setter = getattr(surface, "set_on_show_window", None)
        if callable(show_window_setter):
            show_window_setter(self._publish_show_window)
        if self._voice_usable:
            self._release_bar_startup_gate("voice-ready surface swap")
        log.info("OrbBridge surface swapped (last_state=%s)", self._last_state)
