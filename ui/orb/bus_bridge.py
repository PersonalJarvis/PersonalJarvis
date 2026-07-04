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
    ListeningStarted,
    JarvisAgentBackgroundCompleted,
    OrbResetRequested,
    ResponseGenerated,
    ShowWindowRequested,
    SystemStateChanged,
    TranscriptionUpdate,
    UserVisibleFeedback,
    WakeCandidateDetected,
    WakeWordDetected,
    VoiceBootStatus,
    VoiceMuteChanged,
    VoiceMuteToggleRequested,
    VoiceSessionEnded,
    VoiceSessionStarted,
)

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
        bus: "EventBus",
        orb: "OrbOverlay",
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
        self._idle_task: asyncio.Task | None = None
        self._idle_enabled = idle_animations_enabled
        self._hide_on_idle = hide_on_idle
        self._hangup_task: asyncio.Task | None = None
        self._completion_task: asyncio.Task | None = None
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
        # Boot z-order re-lift latch. The persistent bar is now visible from
        # boot (the overlay maps its window immediately — see
        # DesktopApp._build_overlay_surface), so this is NO LONGER a visibility
        # gate. Once voice is ready, ``reveal_bar_when_voice_ready`` re-asserts
        # the bar's topmost (after the main window + tray have finished mapping).
        # ``_boot_reveal_done`` makes the re-lift idempotent across the ready
        # signal and the fallback timeout. asyncio.Event() is loop-agnostic at
        # construction (Py3.10+ dropped the loop param; project minimum is
        # 3.11), so it is safe to build off the running loop.
        self._voice_ready_event = asyncio.Event()
        self._boot_reveal_done: bool = False
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
        # Live audio → equalizer bars. Wired FIRST and each in its OWN try, OUTSIDE
        # the bus-subscription block below. The mic/TTS level feed is what makes the
        # bars move, and it must NEVER be skipped just because some unrelated
        # bus.subscribe() or surface-setter call raised. Buried at the END of the one
        # big try (as it used to be), a single earlier failure jumped straight to the
        # outer except and the mic subscription never ran — so
        # ``mic_level.has_subscribers()`` stayed False, the VAD frame loop skipped its
        # ``mic_level.feed(rms)`` guard, and the equalizer was PERMANENTLY DEAD (bars
        # never reacted to your voice) even though the bar itself — constructed
        # separately in ``DesktopApp._build_overlay_surface`` — still showed and still
        # switched looks on state changes. Idempotent: guarded on the stored unsub so a
        # repeated attach() cannot double-register.
        if self._mic_level_unsub is None:
            try:
                from jarvis.audio import mic_level

                self._mic_level_unsub = mic_level.subscribe(self._on_mic_level)
            except Exception as exc:  # noqa: BLE001
                log.warning("OrbBridge mic_level subscribe failed: %s", exc)
        if self._tts_recency_unsub is None:
            try:
                from jarvis.audio import level_tap

                self._tts_recency_unsub = level_tap.subscribe(self._note_tts_level)
            except Exception as exc:  # noqa: BLE001
                log.warning("OrbBridge level_tap recency subscribe failed: %s", exc)
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
            # Boot z-order re-lift: once the speech pipeline signals voice is
            # ready, re-assert the (already-visible) persistent bar's topmost.
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
            # NOTE: the mic_level + level_tap (equalizer) subscriptions are wired
            # at the TOP of attach(), BEFORE this try — see the comment there. They
            # must not live here at the end of the bus-subscription block, or an
            # earlier failure in this block silently kills the live equalizer.
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
            log.warning(
                "OrbBridge: reset requested but orb has no _root / _on_reset"
            )
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
        coro = self._bus.publish(
            VoiceMuteToggleRequested(source="orb_dblclick_double")
        )
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
        cleanly. Until that fires the equalizer mic-feed stays gated off (<1 s).
        """
        if event.active:
            # Incoming speech candidate — cancel any pending idle hide and pop
            # the bar. _last_state untouched (see docstring).
            self._cancel_idle_scheduler()
            self._orb.show(mode="listen")
            return
        # Retract a rejected candidate. A real session owns the bar → leave it.
        if self._last_state in _ACTIVE_VOICE_STATES:
            return
        if self._hide_on_idle:
            self._orb.hide()
        else:
            self._orb.show(mode="idle")

    async def _on_wake_word_detected(self, event: WakeWordDetected) -> None:
        """Pop the orb on the earliest confirmed wake signal."""
        log.info("OrbBridge._on_wake_word_detected: keyword=%s", event.keyword)
        prev_state = self._last_state
        self._last_state_trace_id = str(event.trace_id)
        self._suppress_show_until_session = False
        self._last_state = "LISTENING"
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
        prev_state = self._last_state
        self._suppress_show_until_session = False
        self._last_state = "LISTENING"
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
        log.info(
            "OrbBridge._on_session_ended: session=%s reason=%s — orb stays hidden "
            "until next wake.",
            event.session_id,
            event.hangup_reason,
        )
        self._suppress_show_until_session = True

    async def _on_voice_boot_status(self, event: VoiceBootStatus) -> None:
        """Track the speech-pipeline boot readiness.

        ``ready=True`` (emitted once Phase A of warm-up is live — audio + VAD +
        wake + STT + TTS client) releases the latch so the persistent bar's
        topmost z-order is re-asserted (the bar is already visible from boot).
        ``ready=False`` (warm-up start) is ignored.
        """
        if event.ready:
            self._voice_ready_event.set()

    async def reveal_bar_when_voice_ready(self, *, timeout_s: float = 30.0) -> None:
        """Show the persistent bar once the voice stack is genuinely ready.

        Synchronized appearance (2026-06-29): the persistent bar is now started
        WITHDRAWN (``start_hidden=True`` — see DesktopApp._build_overlay_surface),
        so THIS is the visibility gate. Scheduled once on the event loop at boot,
        it waits for ``VoiceBootStatus(ready=True)`` (emitted after the deferred
        loaders bring up wake+VAD+TTS) — or a bounded ``timeout_s`` fallback so
        the bar can never be stuck hidden — and then shows the idle pill, which
        maps + lifts the bar exactly when the user can actually talk. A
        non-persistent bar / the mascot (``hide_on_idle``) is left untouched: it
        pops on a real session, not at boot.
        """
        reason = "timeout-fallback"
        try:
            await asyncio.wait_for(self._voice_ready_event.wait(), timeout_s)
            reason = "voice-ready"
        except TimeoutError:
            pass
        self._reveal_persistent_bar(reason)

    def _reveal_persistent_bar(self, reason: str) -> None:
        """Show the persistent bar's idle pill exactly once (the boot reveal).

        Idempotent (``_boot_reveal_done``). The bar starts withdrawn
        (start_hidden), so ``show("idle")`` here maps + lifts it — this is the
        moment the bar first becomes visible, synchronized with voice-ready.
        """
        if self._boot_reveal_done:
            return
        self._boot_reveal_done = True
        if self._hide_on_idle:
            # Non-persistent bar / mascot: stays hidden until a voice session.
            return
        try:
            self._orb.show("idle")
            log.info("Persistent overlay revealed after boot (%s).", reason)
        except Exception:  # noqa: BLE001
            log.debug("persistent bar boot reveal failed", exc_info=True)

    async def _on_state(self, event: SystemStateChanged) -> None:
        # Lazily pin the backend loop (idempotent) so Tk-thread gestures can
        # marshal onto it. _on_state fires on every transition, long before the
        # orb is clickable, so the loop is always captured in time.
        self._remember_loop()
        state = event.new_state
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
                "OrbBridge: stray %s outside live session suppressed — "
                "mascot stays hidden.",
                state,
            )
            return
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
        if self._listening_transcript_text and getattr(
            self, "_completion_continuation", False
        ):
            return
        self._listening_transcript_text = ""
        self._last_response_text = ""
        self._show_listening_transcript("")

    async def _on_transcription_update(self, event: TranscriptionUpdate) -> None:
        # Accept transcript events across the entire user-side lifecycle
        # (LISTENING, USER_SPEAKING, WAITING_FOR_FINAL_TRANSCRIPT,
        # WAITING_FOR_COMPLETION). Outside this window (THINKING/SPEAKING/
        # IDLE) the bubble must NOT be repainted with stale user text —
        # the brain has already taken over.
        if self._last_state not in _USER_SIDE_BUBBLE_STATES:
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
            self._show_listening_transcript(
                self._last_response_text or THINKING_BUBBLE_TEXT
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
            self._idle_loop(), name="orb-idle-animation-scheduler",
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

    def _note_tts_level(self, _level: float) -> None:
        """Recency tracker only: TTS just produced an output level, so it is
        making sound now. The surface's own ``level_tap`` subscription does the
        actual SPEAKING ``set_level``; we just remember the time so the mic does
        not clobber it."""
        self._last_tts_level_t = time.monotonic()

    def _on_mic_level(self, level: float) -> None:
        """Forward the live mic loudness to the active surface's bars.

        The level comes from ``jarvis.audio.mic_level`` (the VAD feeds it from
        the audio already captured for STT — no second stream). It is forwarded
        only when (a) NO TTS output is currently playing — Jarvis's voice owns
        the bars while it speaks, and the state label is unreliable because
        continue-listening flips to LISTENING mid-playback — and (b) the coarse
        state is LISTENING. Works for whichever surface is current."""
        if time.monotonic() - self._last_tts_level_t < self._TTS_OWNS_BARS_S:
            return  # TTS is making sound → it drives the bars, not the silent mic
        if self._last_state != "LISTENING":
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
        setter = getattr(surface, "set_on_mute_toggle", None)
        if callable(setter):
            setter(self._publish_mute_toggle)
        feedback_setter = getattr(surface, "set_feedback_publisher", None)
        if callable(feedback_setter):
            feedback_setter(self._publish_visible_feedback)
        show_window_setter = getattr(surface, "set_on_show_window", None)
        if callable(show_window_setter):
            show_window_setter(self._publish_show_window)
        log.info("OrbBridge surface swapped (last_state=%s)", self._last_state)
