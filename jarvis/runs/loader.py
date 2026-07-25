"""Assemble a Run from the existing stores. Read-only; never on the hot path.

The voice critical path never calls this — it runs only when a REST request or
the live WS forward asks for a run (AP-9). Each datum is fetched defensively: a
missing missions lookup or an empty usage log degrades to an empty slice, never
an exception."""
from __future__ import annotations

import logging
from collections.abc import Callable

from jarvis.clis.usage_log import UsageLog
from jarvis.runs import analyzer
from jarvis.runs.model import MissionRef, Run, RunListItem, RunTurn
from jarvis.sessions.models import VoiceEventRow, VoiceTurnRow
from jarvis.sessions.store import SessionStore

log = logging.getLogger(__name__)

# Optional callable: session_id -> list[MissionRef]. None disables the slice.
MissionsLookup = Callable[[str], list[MissionRef]]

# The ONLY event kinds the run LIST derives from — error count, worst SLO,
# outcome, feature tags. Keeping this narrow is what makes the list query cheap;
# the full stream is loaded only when a single run is opened. Adding a kind here
# is fine, adding "all kinds" is the regression this replaced.
_LIST_EVENT_KINDS: list[str] = [
    "ErrorOccurred",
    "ActionDenied",
    "LatencySpan",
    "ResponseGenerated",
    "SpeechSpoken",
    "ActionProposed",
    "ActionExecuted",
    "JarvisAgentTaskStarted",
]


class RunLoader:
    def __init__(
        self,
        *,
        session_store: SessionStore,
        usage_log: UsageLog | None,
        missions_lookup: MissionsLookup | None = None,
    ) -> None:
        self._sessions = session_store
        self._usage = usage_log
        self._missions = missions_lookup

    def list_runs(self, *, limit: int = 100) -> list[RunListItem]:
        sessions = self._sessions.list_sessions(limit=limit)
        # ONE narrow query for all sessions instead of a full event fetch per
        # session. The list only needs the kinds below; pulling everything cost
        # 100 store-lock acquisitions, which stalled for tens of seconds
        # whenever a live voice session held that lock through the recorder.
        events_by_session = self._sessions.get_events_for_sessions(
            [s.id for s in sessions], kinds=_LIST_EVENT_KINDS
        )
        items: list[RunListItem] = []
        for s in sessions:
            events = events_by_session.get(s.id, [])
            error_count = sum(
                1 for e in events if e.kind in ("ErrorOccurred", "ActionDenied")
            )
            worst = analyzer.SLO_OK
            for e in events:
                if e.kind == "LatencySpan":
                    st = analyzer.classify_latency(
                        str(e.payload.get("phase", "")),
                        float(e.payload.get("duration_ms", 0.0) or 0.0),
                    )
                    if analyzer._SLO_RANK.get(st, 0) > analyzer._SLO_RANK.get(worst, 0):
                        worst = st
            items.append(RunListItem(
                session_id=s.id,
                started_ms=s.started_ms,
                ended_ms=s.ended_ms,
                duration_s=s.duration_s,
                hangup_reason=s.hangup_reason,
                wake_source=_wake_source(s.wake_keyword),
                turn_count=s.turn_count,
                total_cost_usd=s.total_cost_usd,
                error_count=error_count,
                outcome=analyzer.outcome_from_events(events),
                slo_status=worst,
                feature_tags=analyzer.feature_tags_from_events(events),
                preview=s.preview,
            ))
        return items

    def load_run(self, session_id: str) -> Run | None:
        session = self._sessions.get_session(session_id)
        if session is None:
            return None
        turn_rows = self._sessions.get_turns(session_id)
        events = self._sessions.get_events(session_id)
        events_by_turn: dict[str | None, list[VoiceEventRow]] = {}
        for e in events:
            events_by_turn.setdefault(e.turn_id, []).append(e)

        run_turns = [
            self._build_turn(tr, events_by_turn.get(tr.id, []))
            for tr in turn_rows
        ]
        missions = self._safe_missions(session_id)
        analytics = analyzer.build_analytics(
            run_turns, started_ms=session.started_ms, ended_ms=session.ended_ms
        )
        # Events the recorder stored WITHOUT a turn id are the session frame
        # (wake, session start/end, provider switches between turns). They used
        # to be dropped entirely because the turn join never matched them.
        session_events, _ = analyzer.build_raw_events(
            events_by_turn.get(None, []), turn_started_ms=session.started_ms
        )
        return Run(
            session=session,
            outcome=analyzer.build_outcome(run_turns),
            turns=run_turns,
            missions=missions,
            activity=analyzer.build_activity(run_turns),
            analytics=analytics,
            environment=analyzer.build_environment(session, events, run_turns),
            session_events=session_events,
            event_counts=analyzer.build_event_counts(events),
        )

    def _build_turn(self, tr: VoiceTurnRow, events: list[VoiceEventRow]) -> RunTurn:
        cli_tools = []
        if self._usage is not None:
            # trace_id is not a column on voice_turns; the turn's CLI calls are
            # tagged with the per-turn trace_id only in cli_usage.db. We use the
            # turn id as the correlation key the recorder writes; fall back to an
            # empty list when nothing matches.
            try:
                rows = self._usage.list_for_trace(tr.id)
                cli_tools = analyzer.tools_from_usage(rows)
            except Exception as exc:  # noqa: BLE001 — usage log is best-effort
                log.debug("usage join failed for turn %s: %s", tr.id, exc)
        tools = analyzer.merge_action_tools(events, cli_tools)
        # Surface the already-captured command + result onto each tool row.
        analyzer.attach_tool_io(events, tools)
        raw_events, truncated = analyzer.build_raw_events(
            events, turn_started_ms=tr.started_ms
        )
        turn = RunTurn(
            idx=tr.idx,
            trace_id=tr.id,
            user_text=tr.user_text,
            jarvis_text=tr.jarvis_text,
            tier=tr.tier,
            provider=tr.provider,
            model=tr.model,
            tokens_in=tr.tokens_in,
            tokens_out=tr.tokens_out,
            cost_usd=tr.cost_usd,
            think_ms=tr.think_ms,
            speak_ms=tr.speak_ms,
            transcript=analyzer.build_transcript(events, turn_started_ms=tr.started_ms),
            latency=analyzer.build_latency(events),
            decision_path=analyzer.build_decision_path(events),
            tools=tools,
            errors=analyzer.build_errors(events),
            extras=analyzer.build_extras(events, tokens_in=tr.tokens_in),
            events=raw_events,
            event_counts=analyzer.build_event_counts(events),
            events_truncated=truncated,
            # "Not measured" is a different fact from "measured as zero" — a
            # realtime turn billed at session level records neither tokens nor
            # cost, and printing a bare 0 would misreport that as free.
            usage_recorded=bool(
                tr.tokens_in or tr.tokens_out or tr.cost_usd
                or any(e.kind == "BrainTurnCompleted" for e in events)
            ),
        )
        turn.outcome = analyzer.turn_outcome(turn)
        turn.activity = analyzer.build_activity([turn])
        analyzer.ensure_brain_step(turn.decision_path, turn)
        return turn

    def _safe_missions(self, session_id: str) -> list[MissionRef]:
        if self._missions is None:
            return []
        try:
            return self._missions(session_id)
        except Exception as exc:  # noqa: BLE001 — missions are an optional slice
            log.debug("missions lookup failed for %s: %s", session_id, exc)
            return []


# One implementation, shared with build_environment — the list card and the
# run header must never disagree about how a run was started.
_wake_source = analyzer.wake_source


__all__ = ["RunLoader", "MissionsLookup"]
