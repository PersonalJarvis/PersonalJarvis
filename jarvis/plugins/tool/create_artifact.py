"""``create_artifact`` — build (or revise) one self-contained page, when asked.

An artifact is the thing the user wants to LOOK AT: a dashboard, a report, an
infographic, a small interactive tool — one HTML file, rendered full-size in
the Artifacts section and openable in any browser. Writing such a page well
needs the strongest model the install has and more time than a voice turn can
hold, so this tool never writes the page itself. It composes the brief
(:mod:`jarvis.artifacts.brief`) and hands it to the mission stack — the same
sub-agent path ``spawn_worker`` uses — then returns within the voice budget
with a spoken promise. The worker writes the file into its worktree, the
Kontrollierer archives it as the run's deliverable, and the mission voice
listener reads the completion back; the Artifacts section shows the page the
moment it lands, because it reads the very archive the worker wrote into.

**Ask-only.** The tool is withheld from the model's tool set on any turn that
did not explicitly ask for an artifact; see :mod:`jarvis.brain.artifact_gate`
and ``BrainManager._hide_artifact_tool_without_request``. The gate is the real
enforcement — this description is the second line, for the turns where the
gate opens but the request was about something else.

Router-tier. It is a dispatch (risk ``monitor``, like ``spawn_worker``), so it
never enters a worker tool set (AP-5/AP-14): a worker building an artifact
cannot spawn another worker to build it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from jarvis.artifacts.brand_marks import find_brand_marks
from jarvis.artifacts.brief import ArtifactBrief, build_artifact_brief
from jarvis.artifacts.locate import locate_artifact
from jarvis.artifacts.source_data import (
    SourceNeed,
    fetch_source_data,
    plan_source_data,
    render_source_data,
    unavailable_source_data,
    wants_sample_data,
)
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    JarvisAgentAnnouncement,
    JarvisAgentBackgroundCompleted,
    NavigateSidebar,
)
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.voice.action_phrases import action_phrase, resolve_ambient_language

log = logging.getLogger(__name__)

#: The section that shows artifacts — the frontend ``SectionId`` the navigate
#: tool's parity test pins.
ARTIFACTS_SECTION: Final = "visualization"

#: Resolved at execute-time, not at __init__-time: the mission stack is
#: bootstrapped AFTER the brain is built (AD-OC1, the same lazy-resolver
#: pattern ``spawn_worker`` uses).
ManagerResolver = Callable[[], Any | None]
KontrolliererResolver = Callable[[], Any | None]
#: The router's built tool set by name — read at fetch time, so the dict the
#: factory is still filling when this tool is constructed is seen complete.
ToolsResolver = Callable[[], Mapping[str, Any]]

#: The brief carries the user's request verbatim; this caps a runaway model
#: summary, not a real request. Anything longer is still a request — clipped.
_MAX_REQUEST_CHARS: Final = 6_000
_MAX_TITLE_CHARS: Final = 120


def _turn_language(ctx: Any, args: dict[str, Any]) -> str:
    """de/en/es for the brief and the spoken acknowledgement.

    The turn's resolved output language wins (stamped by the tool-use loop via
    ``resolve_output_language``); the brain's own ``language`` guess is next;
    the ambient answer last. This layer never re-derives a language from the
    utterance (CLAUDE.md §2).
    """
    config = getattr(ctx, "config", None)
    stamped = ""
    if isinstance(config, dict):
        stamped = str(config.get("output_language") or "").strip().lower()
    lang = stamped or str(args.get("language") or "").strip().lower()
    return lang or resolve_ambient_language()


def _utterance(ctx: Any) -> str:
    """What the user said this turn, when the context carries it."""
    for attribute in ("user_utterance", "user_text", "utterance", "text"):
        value = getattr(ctx, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class CreateArtifactTool:
    """Dispatch one artifact build to the mission stack and promise it aloud."""

    name: str = "create_artifact"
    risk_tier: str = "monitor"
    description: str = (
        "Build an ARTIFACT: one self-contained HTML page the user can look at — "
        "a dashboard, report, infographic, diagram, comparison, timeline, chart, "
        "landing page or small interactive tool. A strong background agent "
        "writes the page; it appears in the Artifacts section when done (about "
        "a minute). Use this when the user explicitly asks to SEE something as "
        "a page, picture, diagram or artifact ('visualisier mir das', 'mach mir "
        "ein Dashboard draus', 'build me an artifact', 'show me that as a "
        "timeline'). Never call it to decorate an ordinary answer. To change "
        "an existing artifact ('make the bars red'), pass 'revise' with its "
        "title (or 'latest'). This does NOT open the Artifacts section — that "
        "is 'navigate'. Prefer this over spawn_worker whenever the deliverable "
        "is a page to look at."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": (
                    "The artifact's title, a few words, in the user's language. "
                    "Names the page and the file."
                ),
            },
            "request": {
                "type": "string",
                "description": (
                    "Everything the page must contain, self-contained: the "
                    "subject, the content points, any numbers or data the user "
                    "gave, and style wishes. The agent that builds it sees "
                    "nothing else of this conversation, so include what was "
                    "discussed. Write it in the user's language."
                ),
            },
            "revise": {
                "type": "string",
                "description": (
                    "Only when changing an EXISTING artifact: its title (or part "
                    "of it), or 'latest' for the most recent one. 'request' then "
                    "describes the change."
                ),
            },
            "language": {
                "type": "string",
                "enum": ["de", "en", "es"],
                "description": "Language the user is speaking (fallback only).",
            },
            "spoken_ack": {
                "type": "string",
                "description": (
                    "One short spoken sentence confirming the artifact is being "
                    "built in the background, in the user's language. Optional."
                ),
            },
        },
        "required": ["title", "request"],
    }

    def __init__(
        self,
        bus: EventBus,
        *,
        manager: Any | None = None,
        manager_resolver: ManagerResolver | None = None,
        kontrollierer: Any | None = None,
        kontrollierer_resolver: KontrolliererResolver | None = None,
        executor: Any | None = None,
        tools_resolver: ToolsResolver | None = None,
    ) -> None:
        """
        ``executor`` (the ``ToolExecutor``) and ``tools_resolver`` (the router's
        built tool set, read lazily because the set is still being filled when
        this tool is constructed) are what lets a page about the user's own
        mail or calendar be built from FACTS: the tool reads those plugins
        itself before the brief is composed (:mod:`jarvis.artifacts.source_data`).
        Without them the brief still names every such source as unavailable,
        so the worker says so on the page instead of inventing an inbox.
        """
        if manager is None and manager_resolver is None:
            raise ValueError("CreateArtifactTool requires 'manager' or 'manager_resolver'")
        self._bus = bus
        self._manager = manager
        self._manager_resolver = manager_resolver
        self._kontrollierer = kontrollierer
        self._kontrollierer_resolver = kontrollierer_resolver
        self._executor = executor
        self._tools_resolver = tools_resolver

    # -- resolvers ----------------------------------------------------------

    def _resolve_manager(self) -> Any | None:
        if self._manager is not None:
            return self._manager
        if self._manager_resolver is not None:
            return self._manager_resolver()
        return None

    def _resolve_kontrollierer(self) -> Any | None:
        if self._kontrollierer is not None:
            return self._kontrollierer
        if self._kontrollierer_resolver is not None:
            return self._kontrollierer_resolver()
        return None

    # -- execute ------------------------------------------------------------

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        args = args or {}
        title = " ".join(str(args.get("title") or "").split())[:_MAX_TITLE_CHARS]
        request = str(args.get("request") or "").strip()[:_MAX_REQUEST_CHARS]
        revise = " ".join(str(args.get("revise") or "").split())
        language = _turn_language(ctx, args)

        # A rejected call is the model's to fix: the error says exactly what
        # was missing, so the second attempt can be right.
        if not title:
            return ToolResult(success=False, output="", error="'title' must not be empty.")
        if not request:
            return ToolResult(
                success=False,
                output="",
                error="'request' must describe what the page should contain.",
            )

        previous = None
        if revise:
            previous = await asyncio.to_thread(locate_artifact, revise)
            if previous is None:
                return ToolResult(
                    success=False,
                    output="",
                    error=(
                        f"No recent artifact matches {revise!r}. Build it fresh "
                        "without 'revise', or name the artifact's title more exactly."
                    ),
                )

        manager = self._resolve_manager()
        if manager is None:
            # The mission stack is not up yet — the machine's problem, said
            # plainly so the model relays it instead of retrying into a wall.
            return ToolResult(
                success=False,
                output="",
                error=action_phrase("spawn_no_runner", language),
            )

        # The original marks for the brands the request names ride along in the
        # brief (the worker's workspace holds no repo); a request that names
        # none adds one rule: text, never a lettered tile in a logo's place.
        brand_marks = find_brand_marks(f"{title} {request}")

        def build(source_data: str | None) -> ArtifactBrief:
            return build_artifact_brief(
                request,
                title=previous.title if previous else title,
                language=language,
                previous_html=previous.html if previous else None,
                previous_filename=previous.file.name if previous else None,
                brand_marks=brand_marks,
                source_data=source_data,
            )

        # The brief without facts names the file and the title now; the one
        # the worker reads is composed in the background, after the facts.
        brief = build(None)
        kontrollierer = self._resolve_kontrollierer()
        utterance = _utterance(ctx) or request
        # A page about the user's own mail or calendar is built from what
        # Jarvis reads there — fetched in the background, past the voice
        # budget, before the mission is composed. A request for a sample /
        # mock-up wants made-up items and gets none fetched (the brief tells
        # the worker to label them).
        needs = (
            ()
            if wants_sample_data(f"{utterance}\n{request}")
            else plan_source_data(f"{utterance}\n{request}")
        )
        # The mission dispatch contract is de/en; an "es" turn keeps the German
        # mission readback (the same cap spawn_worker applies).
        mission_language = language if language in ("de", "en") else "de"

        # UI/telemetry announce — the agent strip shows "builds the artifact".
        # Best-effort like every other announce: the build happens either way.
        try:
            await self._bus.publish(
                JarvisAgentAnnouncement(
                    trace_id=ctx.trace_id,
                    action=("revises the artifact" if brief.revision else "builds the artifact"),
                    target=brief.title,
                )
            )
        except Exception as exc:  # noqa: BLE001 — see comment above
            log.warning("create_artifact announce failed error=%s", exc)

        # Fire-and-forget: the router is free again within the voice budget;
        # the completion reaches the user through the mission voice listener.
        # The task-name prefix is a live matching key — BrainManager
        # ``_cancel_all_background_tasks`` cancels by it.
        asyncio.create_task(
            self._background_dispatch(
                build,
                needs,
                utterance,
                manager,
                kontrollierer,
                trace_id=ctx.trace_id,
                mission_language=mission_language,
                readback_language=language,
            ),
            name=f"jarvis-agent-artifact-{ctx.trace_id.hex[:8]}",
        )

        # Move the UI to the Artifacts section so the "building…" row is seen.
        # Best-effort on purpose: the build exists either way.
        try:
            await self._bus.publish(
                NavigateSidebar(
                    section=ARTIFACTS_SECTION,
                    source_layer="brain.tool.create_artifact",
                )
            )
        except Exception as exc:  # noqa: BLE001 — see comment above
            log.warning("create_artifact navigate failed error=%s", exc)

        ack = (str(args.get("spoken_ack") or "").strip()) or action_phrase(
            "artifact_revising" if brief.revision else "artifact_building",
            language,
            title=brief.title,
        )
        # The protocol types ``artifacts`` as paths; the brain's tool loop reads
        # this background marker as a dict (the spawn_worker precedent).
        return ToolResult(
            success=True,
            output=ack,
            artifacts=(  # type: ignore[arg-type]
                {
                    "background_task": True,
                    "utterance": utterance,
                    "artifact_title": brief.title,
                    "artifact_file": brief.filename,
                    "revision": brief.revision,
                },
            ),
        )

    async def _source_data(
        self, needs: tuple[SourceNeed, ...], *, trace_id: UUID, utterance: str
    ) -> str | None:
        """The brief's ``## Source data`` section for *needs* — the facts read
        through the executor, or every source marked unavailable when the tool
        was built without one. None when the request names no such data."""
        if not needs:
            return None
        tools: Mapping[str, Any] = {}
        if self._tools_resolver is not None:
            try:
                tools = self._tools_resolver() or {}
            except Exception as exc:  # noqa: BLE001 — a broken resolver reads as "no tools"
                log.warning("create_artifact: tools resolver failed: %s", exc)
                tools = {}
        if self._executor is None or not tools:
            log.warning(
                "create_artifact: no executor/tools wired — %s stay unavailable in the brief",
                ", ".join(n.key for n in needs),
            )
            data = unavailable_source_data(needs)
        else:
            data = await fetch_source_data(
                needs,
                tools=tools,
                executor=self._executor,
                trace_id=trace_id,
                utterance=utterance,
                now=datetime.now().astimezone(),
            )
        log.info(
            "create_artifact: source data %s",
            ", ".join(f"{s.need.key}={s.status}({len(s.items)})" for s in data.sections),
        )
        return render_source_data(data)

    async def _background_dispatch(
        self,
        build: Callable[[str | None], ArtifactBrief],
        needs: tuple[SourceNeed, ...],
        utterance: str,
        manager: Any,
        kontrollierer: Any | None,
        *,
        trace_id: UUID,
        mission_language: str,
        readback_language: str,
    ) -> None:
        """Read the facts, compose the brief, persist the mission and run it —
        the two-step contract spawn_worker documents (dispatch → PENDING,
        run_mission → APPROVED/FAILED).

        Every dead end becomes a spoken failure through the same completion
        event a finished mission uses, never a log line alone (AU-11): the
        user already heard a promise.
        """
        try:
            source_data = await self._source_data(needs, trace_id=trace_id, utterance=utterance)
            prompt = build(source_data).prompt
            mission_id = await manager.dispatch(
                prompt=prompt,
                language=mission_language,
                source_actor="hauptjarvis",
            )
            if kontrollierer is None:
                log.warning(
                    "create_artifact: mission %s dispatched but no Kontrollierer "
                    "available — it stays PENDING until the next app start",
                    mission_id,
                )
                await self._publish_failure(
                    utterance, action_phrase("spawn_no_runner", readback_language)
                )
                return
            await kontrollierer.run_mission(mission_id)
        except asyncio.CancelledError:
            log.info("create_artifact background dispatch cancelled (app shutdown)")
            raise
        except BaseException as exc:  # noqa: BLE001 — fire-and-forget task, see docstring
            log.exception("create_artifact background dispatch crashed")
            await self._publish_failure(utterance, f"{type(exc).__name__}: {exc}")

    async def _publish_failure(self, utterance: str, error: str) -> None:
        try:
            await self._bus.publish(
                JarvisAgentBackgroundCompleted(
                    success=False,
                    utterance=utterance,
                    summary="",
                    error=error,
                    duration_s=0.0,
                )
            )
        except Exception:  # noqa: BLE001 — dead bus at shutdown; leave a trace
            log.exception("create_artifact failure publish crashed")


__all__ = ["ARTIFACTS_SECTION", "CreateArtifactTool"]
