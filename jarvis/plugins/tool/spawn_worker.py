"""SpawnWorkerTool — Delegation an die OpenClaw-Bridge via Mission-Manager.

Welle-4-Migration: vorher ``SpawnSubJarvisTool`` mit eigenem
``SubJarvisManager``. Sub-Jarvis-Tier wurde durch OpenClaw-Bridge ersetzt
(siehe docs/openclaw-bridge.md §11). Das Tool dispatched jetzt eine Mission
an den ``MissionManager`` aus ``jarvis.missions.manager``; der Kontrollierer
+ Worker uebernehmen Worktree-Setup, Subprocess-Spawn (OpenClaw-Bridge in
``jarvis/missions/worker_runtime/``) und Voice-Readback.

Das Router-Brain (Haiku/Flash) ruft dieses Tool, wenn der User eine komplexe
Aufgabe beschreibt (Code-Build, App-Entwicklung, mehrstufige Recherche).

Ablauf (Fire-and-Forget):
    1. ``OpenClawAnnouncement`` auf den Bus publishen fuer UI/Telemetry.
    2. ``MissionManager.dispatch(prompt=<utterance>, ...)`` Background-
       Task anstossen — Mission-Manager erzeugt PENDING-Mission, der
       Kontrollierer pickt sie auf und scheduled einen Worker.
    3. Tool-Call kehrt SOFORT mit leerem Output zurueck. Der Router-Brain
       ist sofort wieder frei fuer neue User-Utterances.
    4. Wenn die Mission im Hintergrund fertig ist, publisht der Voice-
       Listener (``jarvis/missions/voice/listener.py``) eine
       ``OpenClawBackgroundCompleted``-Nachricht auf den Bus, die die
       Speech-Pipeline in eine Voice-Ansage umwandelt.

Wichtig: Die User-Utterance wird **verbatim** weitergegeben (AC2). Die
``context_hints`` aus dem Router-Brainstorm bleiben in den Bus-Events fuer
UI/Telemetrie erhalten — der Mission-Decomposer nutzt sie nicht direkt
(er bekommt nur ``prompt``).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Any, Final

from jarvis.brain.ack_brain.spawn_announcement import SpawnAnnouncementComposer
from jarvis.core.bus import EventBus
from jarvis.core.events import OpenClawAnnouncement, OpenClawBackgroundCompleted
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.missions.manager import MissionManager
from jarvis.missions.stream_evidence import strip_spawn_meta

log = logging.getLogger(__name__)


# Resolved at execute-time, not at __init__-time. Required because the
# Mission stack is bootstrapped AFTER build_default_brain() in the
# DesktopApp startup sequence, but the BrainManager is built once and
# never re-evaluates its tool dict. See AD-OC1 Lazy-Resolver in
# docs/openclaw-bridge.md and the regression in
# tests/integration/test_openclaw_lazy_bootstrap.py.
MissionManagerResolver = Callable[[], "MissionManager | None"]
# Same lazy-resolver pattern for the Kontrollierer (mission orchestrator).
# The Kontrollierer is what actually executes a mission after dispatch.
# Without it the voice path persists a PENDING mission but never triggers
# run_mission, so the user hears nothing (BUG-016).
KontrollierersResolver = Callable[[], "Any | None"]


# Per-tool spawn cooldown. After a dispatched spawn, subsequent ``execute``
# calls within this window return a voice-friendly ACK and do NOT dispatch
# a second mission. Live regression 2026-05-27 (mission_019e6983-{82e7,
# a83b,b0be}): one long voice utterance triggered THREE missions because the
# VAD's max-utterance cut produced multiple turns, the brain saw each
# fragment as a separate request and re-issued spawn_worker with the
# prior utterance from history. 30 seconds covers the typical worker-spawn
# window (a real second user-request typically follows much later).
_COOLDOWN_SECONDS: Final[float] = 30.0

# The spoken acknowledgement is composed dynamically — see
# ``jarvis.brain.ack_brain.spawn_announcement.SpawnAnnouncementComposer``.
# History: a fixed scaffold ("Mach ich, ich kümmere mich im Hintergrund  # i18n-allow
# darum, ...") plus a 6-phrase rotation lived here until 2026-06-10. The
# user flagged the stock phrasing twice (2026-05-26 and 2026-06-10) as
# robotic, so any finite hardcoded phrase list in this module is a
# regression. The composer prefers a brain-supplied ``spoken_ack``, then
# a flash-LLM phrasing, and only then its own no-repeat fallback pool.


# Generic ACK filler used when no contextual action verb is available (the
# force-spawn / leak-recovery paths). It is NOT a real interpretation of the
# request and must never become the worker's task instruction.
_GENERIC_ACTION_FALLBACK: Final[str] = "einer komplexen Aufgabe nachgeht"


# Force-spawn task floor. After stripping the spawn/routing meta-clause from the
# verbatim utterance, a body this short means the utterance was ONLY the routing
# wrapper ("spawn a sub-agent") with no real task — fall back to the raw words so
# the worker never receives an empty task.
_MIN_FORCE_SPAWN_TASK_CHARS: Final[int] = 8

# Punctuation/whitespace the meta-strip can expose at the head of the task.
_FORCE_SPAWN_HEAD_PUNCT: Final[str] = " ,;:.–—-•\t\n"

# A single orphaned leading connector / relative pronoun the strip can leave
# behind ("spawn a sub-agent which will help me …" -> "which will help me …").
# Dropping it makes the residual read as an imperative. Fixed token list ONLY —
# never a general rewrite (that would be a new bug class).
_LEADING_CONNECTOR_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:and|und|which|that|who|whom|der|die|das|dass)\b",
    re.IGNORECASE,
)


# Standing quality directive prepended to EVERY dispatched mission prompt.
# Live incident 2026-05-31 (mission 019e7e04): the user's voice request was
# VAD-truncated ("... wie soll"), the Gemini-Flash router tier compressed it
# into a minimal brief ("Erstelle ein sinnvolles HTML-Grundgerüst oder frage
# den User nach Details"), and the frontier Opus worker faithfully shipped a
# 12-line `Hallo Welt / Inhalt folgt` stub that passed the (structural-only)
# critic. The worker is not lazy — it executes the brief it is handed. This
# directive is the worker-side floor that overrides a lazy/minimal brief from
# the cheap router tier, independent of which backend (Claude / Gemini /
# Codex) runs the task: a stub is never an acceptable answer.
_QUALITY_DIRECTIVE: Final[str] = (
    "Deliver a complete, polished, production-quality result that fully "
    'satisfies the request. A skeleton, stub, placeholder, or "content '
    'follows" / "Inhalt folgt" shell is a FAILURE — never ship one. If a '
    "detail is unspecified, pick a rich, sensible default and build the "
    "finished artefact; never downgrade the task to a minimal version, and "
    'treat any hint (even one that says "Grundgerüst" / "skeleton") as a '
    "floor, not a ceiling. This raises the quality of exactly what was asked "
    "— it does not add unrequested features. "
    # Honest-impossibility escape (latency 2026-06-14): without this, the "never
    # downgrade / build the finished artefact" floor pushes the worker to spiral
    # on a task it cannot actually do (e.g. "book a trip" with no booking tool —
    # live mission 019ec708 ran 535s producing nothing). The floor applies to
    # the QUALITY of a doable task, never as a mandate to fake an undoable one.
    "If the task genuinely cannot be completed with the tools available to you "
    "(for example it requires a real-world action like booking, purchasing, "
    "sending, or signing into an external account for which you have no tool), "
    "do NOT simulate, partially attempt, or keep retrying it — state plainly and "
    "briefly that you cannot do it and why, then stop."
)


def _build_mission_prompt(
    utterance: str,
    action: str,
    target: str = "",
    context_hints: list[str] | None = None,
) -> str:
    """Construct the worker's task instruction from the brain's tool-call args.

    The tool schema keeps ``utterance`` verbatim (no detail loss), so a
    VAD-cut turn lands as a fragment (live 2026-05-29 mission 019e70a9: the
    worker received only ``"die Detailwürfelspiele.html"`` while the brain had
    already interpreted ``action="eine HTML-Seite namens Würfelspiel.html
    baut"``). The worker reads ONLY the mission prompt, so a bare fragment
    leaves it building from garbage.

    Lead with the interpreted ``action`` (+ target + brainstorm hints) when it
    is a genuine interpretation, and keep the verbatim utterance as the user's
    exact words. Fall back to the raw utterance for the force-spawn path
    (``action=""``) where no interpretation exists.
    """
    utterance = (utterance or "").strip()
    action = (action or "").strip()
    target = (target or "").strip()
    if not action or action == _GENERIC_ACTION_FALLBACK:
        # Force-spawn / no interpretation — the verbatim utterance is all we
        # have. Strip any spawn/routing meta-clause ("spawn a sub-agent that …",
        # "create and spawn a sub-agent …") so the worker receives the REAL task,
        # not the routing instruction it cannot act on (no spawn tool, AP-5).
        # Live regression 2026-06-16: an explicit voice trigger ("spawn a
        # sub-agent which will help me find out X") made the worker try to spawn
        # instead of doing X -> empty/off-target diff -> critic_loop_exhausted.
        # ``strip_spawn_meta`` is the SAME function the critic classifier uses
        # (jarvis.missions.stream_evidence) so the worker prompt and the
        # informational classification can never drift apart.
        stripped = re.sub(r"\s{2,}", " ", strip_spawn_meta(utterance)).strip()
        stripped = stripped.lstrip(_FORCE_SPAWN_HEAD_PUNCT)
        stripped = _LEADING_CONNECTOR_RE.sub("", stripped, count=1).lstrip(_FORCE_SPAWN_HEAD_PUNCT)
        # Floor: if stripping left nothing real (the utterance was ONLY the
        # routing wrapper), fall back to the raw utterance — never an empty task.
        body = stripped if len(stripped) >= _MIN_FORCE_SPAWN_TASK_CHARS else utterance
    else:
        parts = [f"Aufgabe: {action}."]
        if target:
            parts.append(f"Zielort/Kontext: {target}.")
        hints = [
            h.strip()
            for h in (context_hints or [])
            if isinstance(h, str) and h.strip()
        ]
        if hints:
            parts.append("Hinweise: " + "; ".join(hints) + ".")
        if utterance:
            parts.append(f'Wortlaut des Nutzers: "{utterance}".')
        body = "\n".join(parts)
    if not body:
        # Nothing to dispatch — keep the empty-in/empty-out contract so the
        # caller's empty-utterance guard still short-circuits cleanly.
        return ""
    # Prepend the standing quality directive so a lazy/minimal brief from the
    # router tier cannot downgrade the deliverable to a stub (2026-05-31 fix).
    return f"{_QUALITY_DIRECTIVE}\n\n{body}"


class SpawnWorkerTool:
    """Delegiert komplexe Aufgaben an die OpenClaw-Bridge via Mission-Manager.

    Tier-Filter: Dieses Tool ist NUR im Router-Tier verfuegbar
    (``ROUTER_TOOLS`` in ``jarvis/brain/factory.py``). Worker selbst haben
    es nicht — damit wird Rekursion (OpenClaw spawnt Sub-OpenClaw) im
    Tool-Layer blockiert.
    """

    name: str = "spawn_worker"
    # LLMs pick tools primarily by description (ADR-0011, computer-use
    # amendment). The old wording sold this tool for generic "Recherche",
    # which pulled plain news/knowledge questions into multi-minute worker
    # missions (live complaint 2026-06-10). Keep the heavy-only contract and
    # the explicit negative boundary in sync with router.py SPAWN-CRITERIA
    # and tests/unit/plugins/tool/test_spawn_worker_description.py.
    description: str = (
        "Delegates a GENUINELY HEAVY task to a background frontier worker "
        "(mission-manager orchestrated; runs for several minutes). Use it "
        "ONLY when the task builds a real work product (code, app, script, "
        "refactor, file, document, HTML report) or clearly needs many steps "
        "of multi-minute focused work (deep multi-source research WITH a "
        "deliverable report). NEVER use it for simple or medium requests: "
        "questions, news, quick lookups, single web searches, or anything "
        "you can answer inline with your own tools in this turn. Pass the "
        "user utterance verbatim plus optional 3-5 short brainstorm notes "
        "as ``context_hints``."
    )
    risk_tier: str = "monitor"
    suppress_response: bool = True
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "utterance": {
                "type": "string",
                "description": (
                    "Die EXAKTE User-Utterance, verbatim und unveraendert. "
                    "Nicht umformulieren, nicht zerlegen, nicht zusammenfassen."
                ),
            },
            "context_hints": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "3-5 kurze Brainstorm-Gedanken vom Router zur Aufgabe. "
                    "Die User-Utterance bleibt verbatim; context_hints sind "
                    "NUR zusaetzliche Notizen (Requirements, Stolperfallen, "
                    "Erfolgskriterien)."
                ),
                "default": [],
            },
            "action": {
                "type": "string",
                "description": (
                    "Kurzer Infinitiv-Satz was du delegierst, "
                    "z.B. 'eine Flask-App baut', 'die Datei x analysiert'."
                ),
            },
            "target": {
                "type": "string",
                "description": (
                    "Ort/Ziel falls bekannt, z.B. 'auf Port 8000' oder leer."
                ),
                "default": "",
            },
            "spoken_ack": {
                "type": "string",
                "description": (
                    "A SHORT spoken confirmation (1-2 sentences, 20 words "
                    "max) in the user's language, phrased freshly for THIS "
                    "exact request: name the concrete topic and naturally "
                    "convey that it runs on the side and may take a moment. "
                    "NEVER a generic stock phrase, never claim the task is "
                    "already done, no internal component names."
                ),
                "default": "",
            },
            "language": {
                "type": "string",
                "description": (
                    "Language of the current user turn: 'de' or 'en'."
                ),
                "default": "",
            },
        },
        "required": ["utterance", "action"],
    }

    def __init__(
        self,
        bus: EventBus,
        manager: MissionManager | None = None,
        manager_resolver: MissionManagerResolver | None = None,
        kontrollierer: Any | None = None,
        kontrollierer_resolver: KontrollierersResolver | None = None,
        announcer: Any | None = None,
    ) -> None:
        """Wires the tool to a MissionManager directly or via lazy resolver.

        Either ``manager`` (eager binding) or ``manager_resolver`` (deferred
        binding) must be provided. The resolver path is the production wiring
        and exists because the Mission stack bootstraps after the BrainManager
        is built. ``manager`` is kept for tests that inject a fake directly.

        ``kontrollierer`` / ``kontrollierer_resolver`` are optional. When
        present, the background dispatch will call ``run_mission`` on the
        kontrollierer after persisting the mission. Without them the
        mission stays in PENDING — the legacy behaviour kept for tests
        that don't care about execution.

        ``announcer`` composes the spoken spawn acknowledgement (see
        ``jarvis.brain.factory.build_spawn_announcer``). When omitted, a
        fallback-only :class:`SpawnAnnouncementComposer` is used so the
        spoken confirmation is guaranteed even without the flash-LLM.
        """
        if manager is None and manager_resolver is None:
            raise ValueError(
                "SpawnWorkerTool requires either 'manager' or 'manager_resolver'"
            )
        self._bus = bus
        self._manager = manager
        self._manager_resolver = manager_resolver
        self._kontrollierer = kontrollierer
        self._kontrollierer_resolver = kontrollierer_resolver
        self._announcer = (
            announcer if announcer is not None else SpawnAnnouncementComposer()
        )
        # Cooldown LIVENESS gate (2026-05-27 hardening audit). Two pieces of
        # state, both reset on tool instantiation so a brain rebuild starts
        # clean:
        #   _last_spawn_at      — monotonic timestamp the gate was last armed.
        #   _active_dispatches  — number of background dispatches currently in
        #                         flight. A duplicate spawn is suppressed ONLY
        #                         while a dispatch is running AND within the
        #                         window — so a fast mission failure (counter
        #                         back to 0) immediately re-opens the gate for a
        #                         legitimate retry (#3), and the counter is
        #                         incremented synchronously before the first
        #                         await so two concurrent execute() coroutines
        #                         cannot both pass (#4).
        self._last_spawn_at: float = 0.0
        self._active_dispatches: int = 0

    def _resolve_manager(self) -> MissionManager | None:
        """Returns the active MissionManager or None if not yet bootstrapped.

        Eager-bound manager always wins. Falls back to the resolver closure
        which queries the module-level singleton each call — that's how a
        post-hoc ``set_mission_manager`` becomes visible without a Brain
        rebuild.
        """
        if self._manager is not None:
            return self._manager
        if self._manager_resolver is not None:
            return self._manager_resolver()
        return None

    def _resolve_kontrollierer(self) -> Any | None:
        """Returns the active Kontrollierer or None if not yet bootstrapped."""
        if self._kontrollierer is not None:
            return self._kontrollierer
        if self._kontrollierer_resolver is not None:
            return self._kontrollierer_resolver()
        return None

    async def execute(
        self, args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:
        utterance = (args.get("utterance") or "").strip()
        if not utterance:
            return ToolResult(success=False, error="empty utterance")

        # Turn output language — ONE authoritative source. The tool-use loop
        # stamps ``ctx.config["output_language"]`` via ``resolve_output_language``
        # (honors the reply_language pin + conversation stickiness + text
        # detection), so it wins over the brain's ``language`` tool-call guess,
        # which can echo a wrong STT tag (forensic 2026-06-20 "Mask it up":
        # English speech mis-tagged ``German`` resolved to ``en``, yet the ACK
        # came from the German pool and the mission readback was German — German
        # text then spoken by the Cartesia English voice = "British accent").
        # This one value drives BOTH the spoken ACK and the mission dispatch
        # language so neither diverges from the turn (Runtime Output Language).
        turn_language = (
            str(ctx.config.get("output_language") or "").strip().lower()
            or (args.get("language") or "").strip().lower()
            or None
        )
        ack_language = turn_language

        # Spawn cooldown — suppress duplicate spawns while a dispatch is in
        # flight AND within _COOLDOWN_SECONDS of the last arm. Live regression
        # 2026-05-27: a single user voice request fragmented across VAD turns
        # produced THREE mission cards (mission_019e6983-{82e7,a83b,b0be}).
        # Guard here is a central choke-point for force-spawn, brain
        # function-call AND leak-recovery paths so no source can sneak around
        # it. The ``_active_dispatches > 0`` term makes this a liveness gate,
        # not a fixed timer: a fast mission failure releases the counter and a
        # legitimate retry dispatches immediately instead of hearing a false
        # "already running" ACK (#3 spawn-cooldown-no-failure-reset).
        now = time.monotonic()
        if (
            self._active_dispatches > 0
            and self._last_spawn_at > 0
            and (now - self._last_spawn_at) < _COOLDOWN_SECONDS
        ):
            # "already_running" is the composer's deterministic fast path —
            # no LLM round-trip on a turn that is pure duplicate rejection.
            ack = await self._announcer.compose(
                utterance=utterance,
                language=ack_language,
                kind="already_running",
            )
            log.info(
                "spawn_worker cooldown active (%.1fs since last spawn < %.0fs) "
                "— suppressing duplicate spawn, returning ACK %r",
                now - self._last_spawn_at,
                _COOLDOWN_SECONDS,
                ack,
            )
            return ToolResult(
                success=True,
                output=ack,
                artifacts=({"cooldown_suppressed": True, "utterance": utterance},),
            )

        # Short-circuit on permanent bootstrap failure (server.py's
        # `_init_mission_stack` raised). The transient "noch nicht bereit"
        # branch below assumes a still-booting process; this one is final
        # for the lifetime of the current Jarvis run, so the user gets an
        # honest "konnte nicht initialisiert werden — siehe Log" instead
        # of being told to wait for something that will never happen.
        try:
            from jarvis.brain.factory import is_openclaw_bootstrap_failed

            if is_openclaw_bootstrap_failed():
                log.warning(
                    "spawn_worker invoked but bootstrap was marked failed — "
                    "returning permanent-failure ack"
                )
                return ToolResult(
                    success=False,
                    output="",
                    error=(
                        "Der Hintergrund-Worker konnte nicht initialisiert "
                        "werden — siehe Log."
                    ),
                )
        except ImportError:
            # Older factory without the sentinel — fall through to the
            # legacy transient-failure path below.
            pass

        manager = self._resolve_manager()
        if manager is None:
            # Honest failure when bootstrap is incomplete. The Force-Spawn
            # caller (BrainManager._force_spawn_worker) propagates the
            # error string into the voice path so the user gets feedback
            # instead of silence.
            log.warning(
                "spawn_worker invoked but MissionManager not yet available — "
                "backend bootstrap incomplete"
            )
            return ToolResult(
                success=False,
                output="",
                error=(
                    "Der Hintergrund-Worker ist noch nicht bereit — er "
                    "wird gerade initialisiert. Bitte einen Moment warten und "
                    "erneut versuchen."
                ),
            )

        raw_action = (args.get("action") or "").strip()
        action = raw_action or _GENERIC_ACTION_FALLBACK
        target = (args.get("target") or "").strip()
        context_hints = args.get("context_hints") or []
        # The worker reads ONLY the mission prompt. Enrich the verbatim
        # utterance with the brain's interpreted action so a VAD-cut fragment
        # doesn't leave the worker building from garbage (2026-05-29 fix ①).
        mission_prompt = _build_mission_prompt(
            utterance, raw_action, target, context_hints
        )
        kontrollierer = self._resolve_kontrollierer()

        # Arm the liveness gate BEFORE the first await (the announce publish).
        # `bus.publish` suspends when subscribers are active (live UI/telemetry
        # handlers are), so a second concurrent execute() could otherwise run
        # its suppress-check during that suspension while the gate was still
        # open and double-spawn (#4). Incrementing synchronously here closes
        # the gate for the whole dispatch decision. The matching decrement is
        # owned by _background_dispatch's finally; if we never reach
        # create_task we roll the increment back so the gate can't wedge.
        self._last_spawn_at = now
        self._active_dispatches += 1
        launched = False
        try:
            # 1. UI/Telemetry-Announce — kein Voice-ACK (Pipeline filtert Empty).
            await self._bus.publish(
                OpenClawAnnouncement(
                    trace_id=ctx.trace_id,
                    action=action,
                    target=target,
                )
            )

            # 2. Fire-and-Forget: Mission anstossen und sofort zurueckkehren.
            #    Der Router-Brain ist dadurch sofort wieder frei fuer neue
            #    User-Utterances. Das Completion-Event publisht der Voice-
            #    Listener als OpenClawBackgroundCompleted.
            asyncio.create_task(
                self._background_dispatch(
                    mission_prompt, utterance, manager, kontrollierer,
                    mission_language=(turn_language or "de"),
                ),
                name=f"openclaw-{ctx.trace_id.hex[:8]}",
            )
            launched = True
        finally:
            if not launched:
                # Announce or task-launch failed before the bg task could own
                # the decrement — release the gate so it never wedges shut.
                self._active_dispatches -= 1

        # Compose the CONTEXT-aware spoken acknowledgement AFTER the dispatch
        # is armed (AD-OE1: the promise follows the handover, never delays
        # it). Preference order inside the composer: brain-supplied
        # ``spoken_ack`` (zero latency) → flash-LLM with the delegation
        # persona (bounded by [ack_brain].timeout_ms + breaker) → bilingual
        # no-repeat fallback pool. Guaranteed non-empty, never raises.
        ack = await self._announcer.compose(
            utterance=utterance,
            language=ack_language,
            candidate=(args.get("spoken_ack") or "").strip() or None,
            action=raw_action,
            target=target,
        )
        return ToolResult(
            success=True,
            output=ack,
            artifacts=({"background_task": True, "utterance": utterance},),
        )

    async def _background_dispatch(
        self,
        prompt: str,
        utterance: str,
        manager: MissionManager,
        kontrollierer: Any | None,
        *,
        mission_language: str = "de",
    ) -> None:
        """Laeuft im Background. Dispatched + executes eine Mission.

        ``prompt`` is the enriched worker task instruction (interpreted action
        + verbatim utterance, built by :func:`_build_mission_prompt`);
        ``utterance`` is the raw user words, kept only for the completion
        event's echo field.

        The manager and kontrollierer are captured at execute-time and
        passed in to avoid a TOCTOU window where the resolver could yield
        a different (or None) value between execute() and the background
        task running.

        Two-step contract (mirrors the REST path in
        ``jarvis/ui/web/missions_routes.py:249-252``):
        1. ``manager.dispatch()`` persists the mission as PENDING and
           publishes ``MissionDispatched``.
        2. ``kontrollierer.run_mission()`` plans + executes it and
           publishes ``MissionApproved`` / ``MissionFailed``, which the
           Voice-Listener turns into an ``OpenClawBackgroundCompleted``
           event for TTS readback.

        Without step 2 the mission stays PENDING forever and the user
        gets no voice feedback (BUG-016). When no Kontrollierer is
        available the dispatch still happens — the next app start's
        recovery sweep will mark it as crash-recovered, which is at
        least visible in the UI instead of silently lost.

        Exceptions werden geloggt und als ``success=False`` publisht — nichts
        propagiert nach aussen, weil der Task fire-and-forget ist und ein
        unbehandelter Exception sonst im Event-Loop als unhandled-exception
        enden wuerde.
        """
        try:
            mission_id = await manager.dispatch(
                prompt=prompt,
                language=mission_language,
                source_actor="hauptjarvis",
            )
            if kontrollierer is None:
                log.warning(
                    "spawn_worker: mission %s dispatched but no Kontrollierer "
                    "available — mission stays PENDING until next app start "
                    "(recovery will mark it crash_recovered)",
                    mission_id,
                )
                return
            # Run the mission. This blocks until APPROVED / FAILED;
            # the Voice-Listener will then publish OpenClawBackgroundCompleted.
            await kontrollierer.run_mission(mission_id)
        except asyncio.CancelledError:
            log.info("OpenClaw background dispatch cancelled (app shutdown)")
            raise  # Propagieren, damit Loop sauber aufraeumt.
        except BaseException as exc:  # noqa: BLE001
            log.exception("Background openclaw dispatch crashed")
            try:
                await self._bus.publish(
                    OpenClawBackgroundCompleted(
                        success=False,
                        utterance=utterance,
                        summary="",
                        error=f"{type(exc).__name__}: {exc}",
                        duration_s=0.0,
                    )
                )
            except Exception:  # noqa: BLE001
                # If even the fail-event publish crashed (dead bus,
                # shutdown race), at least leave a trace — otherwise the
                # whole failure disappears with no record in either the
                # log or the voice path.
                log.exception(
                    "OpenClawBackgroundCompleted bus-publish crashed"
                )
        finally:
            # Release the liveness gate on EVERY terminal state — success,
            # kontrollierer-None early return, failure, or shutdown cancel.
            # A fast failure must re-open the gate so the user's next request
            # is not falsely suppressed as "already running" (#3). Floor at 0
            # defends against any unpaired decrement.
            self._active_dispatches = max(0, self._active_dispatches - 1)
