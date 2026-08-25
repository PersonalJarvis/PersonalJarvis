"""Tests for the Spend & Tokens read model (``jarvis/costs``).

The section makes four claims that are easy to get quietly wrong, so each has
a test of its own:

1. **No double counting.** A voice turn whose model calls are also in the
   event stream must be counted from the events OR from the turn row, never
   from both — the turn row is the fallback for pre-event history.
2. **The role split is real.** Inside one realtime turn, audio usage and the
   delegated tool model land on different roles; outside one, a brain call is
   the pipeline.
3. **Zero is not automatically free.** A recorded 0.0 with tokens on it is a
   pricing GAP for a metered provider, a genuine 0.0 for a local one, and a
   re-derived price when the rate tables know the model.
4. **Sources are optional.** A fresh install has none of these databases and
   the section must still answer.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jarvis.costs import CostSources, build_report, collect_entries
from jarvis.costs.aggregate import filter_entries
from jarvis.costs.model import price_entry

# ---------------------------------------------------------------------------
# Fixtures — minimal replicas of the real schemas
# ---------------------------------------------------------------------------

_SESSIONS_DDL = """
CREATE TABLE voice_turns (
    id TEXT PRIMARY KEY, session_id TEXT, idx INTEGER, started_ms INTEGER,
    ended_ms INTEGER, user_text TEXT, user_lang TEXT, jarvis_text TEXT,
    jarvis_lang TEXT, tier TEXT, provider TEXT, model TEXT,
    tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL,
    latency_total_ms INTEGER
);
CREATE TABLE voice_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, turn_id TEXT,
    ts_ms INTEGER, kind TEXT, payload_json TEXT
);
"""

_MISSIONS_DDL = """
CREATE TABLE missions (
    id TEXT PRIMARY KEY, prompt TEXT, state TEXT, created_ms INTEGER,
    cost_usd REAL
);
CREATE TABLE mission_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT, worker_id TEXT,
    event_type TEXT, ts_ms INTEGER, payload_json TEXT
);
"""

_AGENT_CHAT_DDL = """
CREATE TABLE agent_chat_sessions (
    session_id TEXT PRIMARY KEY, title TEXT, provider TEXT, model TEXT,
    created_ms INTEGER, updated_ms INTEGER
);
CREATE TABLE agent_chat_events (
    session_id TEXT, seq INTEGER, ts_ms INTEGER, kind TEXT, payload TEXT
);
"""

T0 = 1_780_000_000_000


def _sessions_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_SESSIONS_DDL)
    # A realtime turn that delegated one tool call: two model calls, one turn.
    conn.execute(
        "INSERT INTO voice_turns (id, session_id, started_ms, tier, provider, model, "
        "tokens_in, tokens_out, cost_usd, user_text) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("turn-1", "sess-1", T0, "realtime", "gemini-live", "gemini-3.1-flash-live-preview",
         50_000, 300, 0.4, "what is on my calendar"),
    )
    # A classic pipeline turn with no event rows — the fallback path.
    conn.execute(
        "INSERT INTO voice_turns (id, session_id, started_ms, tier, provider, model, "
        "tokens_in, tokens_out, cost_usd, user_text) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("turn-2", "sess-1", T0 + 60_000, "deep", "anthropic", "claude-opus-4-7-20251022",
         1_000, 200, 0.05, "summarise the meeting"),
    )
    for turn_id, payload in (
        (
            "turn-1",
            {
                "provider": "gemini-live",
                "model": "gemini-3.1-flash-live-preview",
                "tokens_in": 50_000,
                "tokens_out": 300,
                "cost_usd": 0.4,
                "finish_reason": "realtime_usage",
            },
        ),
        (
            "turn-1",
            {
                "provider": "grok",
                "model": "grok-4.3",
                "tokens_in": 8_000,
                "tokens_out": 120,
                "cost_usd": 0.0,
                "finish_reason": "stop",
            },
        ),
    ):
        conn.execute(
            "INSERT INTO voice_events (session_id, turn_id, ts_ms, kind, payload_json) "
            "VALUES (?,?,?,?,?)",
            ("sess-1", turn_id, T0 + 1_000, "BrainTurnCompleted", json.dumps(payload)),
        )
    conn.commit()
    conn.close()


def _missions_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_MISSIONS_DDL)
    conn.execute(
        "INSERT INTO missions (id, prompt, state, created_ms, cost_usd) VALUES (?,?,?,?,?)",
        ("m-1", "refactor the parser", "done", T0, 0.0),
    )
    # A worker that reported its own price...
    conn.execute(
        "INSERT INTO mission_events (mission_id, worker_id, event_type, ts_ms, payload_json) "
        "VALUES (?,?,?,?,?)",
        ("m-1", "w-1", "WorkerDraftReady", T0 + 5_000,
         json.dumps({"tokens_used": 26_000, "cost_usd": 1.25})),
    )
    # ...and one that reported tokens but no price and no model, which is what
    # a CLI-driven worker actually emits. Those tokens are a real accounting
    # gap and must be visible as such.
    conn.execute(
        "INSERT INTO mission_events (mission_id, worker_id, event_type, ts_ms, payload_json) "
        "VALUES (?,?,?,?,?)",
        ("m-1", "w-2", "WorkerDraftReady", T0 + 6_000,
         json.dumps({"tokens_used": 33_000, "cost_usd": 0.0})),
    )
    conn.commit()
    conn.close()


def _agent_chat_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_AGENT_CHAT_DDL)
    conn.execute(
        "INSERT INTO agent_chat_sessions (session_id, title, provider, model, created_ms, "
        "updated_ms) VALUES (?,?,?,?,?,?)",
        ("chat-1", "Fix the build", "claude", "claude-opus-4-7-20251022", T0, T0),
    )
    conn.execute(
        "INSERT INTO agent_chat_events (session_id, seq, ts_ms, kind, payload) VALUES (?,?,?,?,?)",
        ("chat-1", 1, T0 + 10_000, "turn_finished", json.dumps({
            "status": "done",
            "cost_usd": 0.31,
            "usage": {
                "input_tokens": 4_000,
                "output_tokens": 900,
                "cache_read_input_tokens": 12_000,
            },
        })),
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def sources(tmp_path: Path) -> CostSources:
    sessions = tmp_path / "sessions.db"
    missions = tmp_path / "missions.db"
    agent_chat = tmp_path / "agent_chat.db"
    _sessions_db(sessions)
    _missions_db(missions)
    _agent_chat_db(agent_chat)
    return CostSources(sessions_db=sessions, missions_db=missions, agent_chat_db=agent_chat)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def test_collects_every_source(sources: CostSources) -> None:
    entries = collect_entries(sources)
    surfaces = {e.surface for e in entries}
    assert surfaces == {"voice", "agent-chat", "mission"}


def test_event_backed_turn_is_not_counted_twice(sources: CostSources) -> None:
    """turn-1 has two events; its own row must not add a third entry."""
    voice = [e for e in collect_entries(sources) if e.surface == "voice"]
    # 2 events for turn-1 + 1 fallback row for the event-less turn-2.
    assert len(voice) == 3
    realtime_cost = sum(e.cost_usd for e in voice if e.role == "realtime")
    assert realtime_cost == pytest.approx(0.4)


def test_role_split_inside_one_realtime_turn(sources: CostSources) -> None:
    voice = [e for e in collect_entries(sources) if e.surface == "voice"]
    roles = {e.role: e for e in voice}
    assert roles["realtime"].provider == "gemini-live"
    # The delegated text model inside the realtime turn is the TOOL model...
    assert roles["tool"].model == "grok-4.3"
    # ...while a brain call in a non-realtime tier is the pipeline.
    assert roles["pipeline"].model == "claude-opus-4-7-20251022"


def test_agent_chat_usage_is_bucketed_by_direction(sources: CostSources) -> None:
    entry = next(e for e in collect_entries(sources) if e.surface == "agent-chat")
    assert entry.role == "agent"
    assert entry.tokens_in == 4_000
    assert entry.tokens_out == 900
    # Cache reads are their own bucket — billed at a fraction of input.
    assert entry.tokens_cached == 12_000
    assert entry.cost_usd == pytest.approx(0.31)


# ---------------------------------------------------------------------------
# Speech — its own database, so the counts every other test asserts stay put
# ---------------------------------------------------------------------------


def _speech_sources(tmp_path: Path) -> CostSources:
    path = tmp_path / "speech.db"
    conn = sqlite3.connect(path)
    conn.executescript(_SESSIONS_DDL)
    for payload in (
        {
            "stage": "stt",
            "provider": "deepgram",
            "voice": "nova-3",
            "chars": 0,
            "audio_ms": 4200.0,
            "cost_usd": 0.0003,
            "price_source": "derived",
        },
        {
            "stage": "tts",
            "provider": "cartesia",
            "voice": "ben",
            "chars": 412,
            "audio_ms": 8200.0,
            "cost_usd": 0.0124,
            "price_source": "derived",
        },
        # A stage nobody knows must not become a cost row.
        {"stage": "hum", "provider": "x", "chars": 9, "cost_usd": 1.0},
        # Nothing consumed — not a row either.
        {"stage": "tts", "provider": "cartesia", "chars": 0, "audio_ms": 0.0},
    ):
        conn.execute(
            "INSERT INTO voice_events (session_id, turn_id, ts_ms, kind, payload_json) "
            "VALUES (?,?,?,?,?)",
            ("sess-1", "turn-1", T0 + 2_000, "SpeechUsageRecorded", json.dumps(payload)),
        )
    conn.commit()
    conn.close()
    return CostSources(sessions_db=path)


def test_speech_is_its_own_surface_with_no_tokens(tmp_path: Path) -> None:
    """Hearing and speaking cost money and consume no tokens.

    Folding characters or audio seconds into the token columns would make
    them sum with the brain's tokens — a different unit and a wrong total.
    """
    rows = list(collect_entries(_speech_sources(tmp_path)))
    assert {e.surface for e in rows} == {"jarvis-voice"}
    assert {e.role for e in rows} == {"stt", "tts"}
    assert all(e.tokens_in == e.tokens_out == e.tokens_cached == 0 for e in rows)
    assert sum(e.cost_usd for e in rows) == pytest.approx(0.0127)


def test_speech_label_names_the_billed_quantity(tmp_path: Path) -> None:
    """The unit that was actually bought, since the token columns cannot say."""
    rows = {e.role: e for e in collect_entries(_speech_sources(tmp_path))}
    assert "412" in rows["tts"].label and "character" in rows["tts"].label
    assert "4.2" in rows["stt"].label


def test_speech_rows_with_nothing_consumed_are_dropped(tmp_path: Path) -> None:
    """A zero-quantity row is not spend, and an unknown stage is not ours."""
    assert len(list(collect_entries(_speech_sources(tmp_path)))) == 2


def test_speech_rows_carry_their_quantities_and_the_buckets_sum_them(tmp_path: Path) -> None:
    """The speech-models table needs the billed quantity as a NUMBER, per
    model: characters spoken and audio heard ride on the entry and add up in
    every bucket, while the token columns stay at zero."""
    rows = {e.role: e for e in collect_entries(_speech_sources(tmp_path))}
    assert rows["tts"].chars == 412
    assert rows["stt"].audio_ms == 4_200 and rows["stt"].chars == 0

    report = build_report(list(rows.values()), since_ms=0, until_ms=2**62)
    by_role = {b["key"]: b for b in report.by_role}
    assert by_role["tts"]["chars"] == 412
    assert by_role["stt"]["audio_ms"] == 4_200
    assert all(b["tokens_total"] == 0 for b in report.by_model)


def test_speech_keeps_the_price_the_meter_settled(tmp_path: Path) -> None:
    """The meter priced it in the vendor's unit; re-deriving it here from a
    token rate would be nonsense, so its verdict must survive the read."""
    rows = list(collect_entries(_speech_sources(tmp_path)))
    assert {e.price_source for e in rows} == {"derived"}

def test_missing_sources_are_skipped(tmp_path: Path) -> None:
    empty = CostSources(sessions_db=tmp_path / "nope.db")
    assert collect_entries(empty) == []
    report = build_report([], since_ms=0, until_ms=1)
    assert report.totals["cost_usd"] == 0.0
    assert report.by_provider == []


def test_range_filter_excludes_older_rows(sources: CostSources) -> None:
    assert collect_entries(sources, since_ms=T0 + 100_000) == []


# ---------------------------------------------------------------------------
# Pricing honesty
# ---------------------------------------------------------------------------


def test_recorded_price_wins() -> None:
    cost, source = price_entry(
        provider="gemini-live", model="x", tokens_in=1, tokens_out=1, recorded_usd=0.5
    )
    assert (cost, source) == (0.5, "recorded")


def test_local_provider_is_free_not_a_gap() -> None:
    cost, source = price_entry(
        provider="local-realtime", model="", tokens_in=9_000, tokens_out=10, recorded_usd=0.0
    )
    assert (cost, source) == (0.0, "free")


def test_free_model_suffix_is_free() -> None:
    _, source = price_entry(
        provider="openrouter",
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
        tokens_in=1_000,
        tokens_out=10,
        recorded_usd=0.0,
    )
    assert source == "free"


def test_unknown_model_is_a_gap_not_a_zero_bill() -> None:
    cost, source = price_entry(
        provider="acme",
        model="totally-unknown-model-9000",
        tokens_in=500_000,
        tokens_out=1_000,
        recorded_usd=0.0,
    )
    assert (cost, source) == (0.0, "unknown")


def test_known_model_is_re_derived() -> None:
    cost, source = price_entry(
        provider="anthropic",
        model="claude-opus-4-7-20251022",
        tokens_in=1_000_000,
        tokens_out=0,
        recorded_usd=0.0,
    )
    assert source == "derived"
    assert cost > 0


def test_cached_tokens_bill_at_the_cache_read_rate() -> None:
    """A coding session is nine tenths cache hits — neither free nor full price."""
    fresh, _ = price_entry(
        provider="anthropic",
        model="claude-opus-4-7-20251022",
        tokens_in=1_000_000,
        tokens_out=0,
        recorded_usd=0.0,
    )
    cached, source = price_entry(
        provider="anthropic",
        model="claude-opus-4-7-20251022",
        tokens_in=0,
        tokens_out=0,
        recorded_usd=0.0,
        tokens_cached=1_000_000,
    )
    assert source == "derived"
    assert 0 < cached < fresh
    assert cached == pytest.approx(fresh * 0.10)


def test_a_seat_with_only_cached_tokens_is_still_priced() -> None:
    cost, source = price_entry(
        provider="claude-cli",
        model="claude-opus-4-7-20251022",
        tokens_in=0,
        tokens_out=0,
        recorded_usd=0.0,
        subscription=True,
        tokens_cached=1_000_000,
    )
    assert source == "subscription"
    assert cost > 0


def test_no_tokens_no_gap() -> None:
    """A 0.0 with nothing consumed is not a pricing hole."""
    assert price_entry(
        provider="acme", model="unknown", tokens_in=0, tokens_out=0, recorded_usd=0.0
    ) == (0.0, "recorded")


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_report_totals_and_breakdowns(sources: CostSources) -> None:
    entries = collect_entries(sources)
    report = build_report(entries, since_ms=T0 - 1, until_ms=T0 + 100_000)

    totals = report.totals
    assert totals["entries"] == len(entries)
    assert totals["cost_usd"] == pytest.approx(sum(e.cost_usd for e in entries))
    assert totals["tokens_total"] == sum(e.tokens_total for e in entries)

    # Every dimension sums back to the same money.
    for dimension in (report.by_provider, report.by_model, report.by_role, report.by_surface):
        assert sum(row["cost_usd"] for row in dimension) == pytest.approx(
            totals["cost_usd"], abs=1e-6
        )

    # Ranked by cost, so the top row is the biggest spender.
    costs = [row["cost_usd"] for row in report.by_provider]
    assert costs == sorted(costs, reverse=True)


def test_gap_tokens_are_reported_not_hidden(sources: CostSources) -> None:
    entries = collect_entries(sources)
    report = build_report(entries, since_ms=0, until_ms=T0 + 100_000)
    # grok-4.3 IS in the price table; the gap is the model-less worker draft
    # that reported tokens without a price.
    gap = report.totals["gap_tokens"]
    assert gap == sum(e.tokens_total for e in entries if e.is_gap)
    assert gap == 33_000
    assert report.totals["gap_entries"] == 1
    # The gap is NOT quietly folded into the money as if it were free.
    assert report.totals["cost_usd"] == pytest.approx(sum(e.cost_usd for e in entries))


def test_series_buckets_by_day_over_a_long_range(sources: CostSources) -> None:
    report = build_report(
        collect_entries(sources), since_ms=T0 - 30 * 86_400_000, until_ms=T0 + 86_400_000
    )
    assert report.bucket == "day"
    assert len(report.series) >= 1
    assert all(len(row["key"]) == len("2026-08-23") for row in report.series)


def test_series_fills_quiet_days(sources: CostSources) -> None:
    """A day with no spend still gets a bucket, or the axis lies about time."""
    entries = collect_entries(sources)
    # Two entries a week apart: the days between them must appear as zeroes.
    spread = [
        *entries,
    ]
    far = entries[0]
    spread.append(
        type(far)(
            ts_ms=far.ts_ms + 7 * 86_400_000,
            surface=far.surface,
            role=far.role,
            provider=far.provider,
            model=far.model,
            tokens_in=10,
            tokens_out=1,
            tokens_cached=0,
            cost_usd=0.01,
            price_source="recorded",
            ref_id=far.ref_id,
            label=far.label,
        )
    )
    report = build_report(spread, since_ms=0, until_ms=far.ts_ms + 8 * 86_400_000)
    assert report.bucket == "day"
    assert len(report.series) == 8
    assert sum(1 for row in report.series if row["entries"] == 0) == 6


def test_series_buckets_by_hour_over_a_short_range(sources: CostSources) -> None:
    report = build_report(collect_entries(sources), since_ms=T0, until_ms=T0 + 3_600_000)
    assert report.bucket == "hour"


def test_breakdown_carries_the_second_dimension(sources: CostSources) -> None:
    report = build_report(collect_entries(sources), since_ms=0, until_ms=T0 + 100_000)
    day = report.series[0]
    assert set(day["breakdown"]).issubset({"realtime", "tool", "pipeline", "agent", "worker"})


def test_filters_narrow_the_entries(sources: CostSources) -> None:
    entries = collect_entries(sources)
    assert all(e.role == "tool" for e in filter_entries(entries, roles={"tool"}))
    assert all(e.provider == "grok" for e in filter_entries(entries, providers={"grok"}))
    assert filter_entries(entries, search="nothing matches this") == []
    # Search reaches the label, not only the ids.
    assert filter_entries(entries, search="calendar")


def test_empty_filters_keep_everything(sources: CostSources) -> None:
    entries = collect_entries(sources)
    assert filter_entries(entries) == entries


# ---------------------------------------------------------------------------
# Missions: who did the work lives on WorkerSpawned, not on the draft
# ---------------------------------------------------------------------------


def _missions_db_with_spawn(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_MISSIONS_DDL)
    conn.execute(
        "INSERT INTO missions (id, prompt, state, created_ms, cost_usd) VALUES (?,?,?,?,?)",
        ("m-2", "write the tests", "done", T0, 0.0),
    )
    spawn = {
        "event_type": "WorkerSpawned",
        "worker_id": "w-9",
        "step": {"worker_cli": "claude", "model": "sonnet"},
        "cli": "claude",
        "model": "sonnet",
    }
    rows = [
        ("m-2", "w-9", "WorkerSpawned", T0 + 1_000, json.dumps(spawn)),
        # A Claude Code seat quoting its own API-equivalent, and reporting its
        # TURN count in the token slot (real shape, 2026-08-25).
        ("m-2", "w-9", "WorkerDraftReady", T0 + 5_000,
         json.dumps({"tokens_used": 4, "cost_usd": 3.15})),
        # The same worker on a draft that reported tokens but no price.
        ("m-2", "w-9", "WorkerDraftReady", T0 + 6_000,
         json.dumps({"tokens_used": 33_000, "cost_usd": 0.0})),
    ]
    conn.executemany(
        "INSERT INTO mission_events (mission_id, worker_id, event_type, ts_ms, payload_json) "
        "VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_mission_draft_takes_cli_and_model_from_its_spawn(tmp_path: Path) -> None:
    path = tmp_path / "missions.db"
    _missions_db_with_spawn(path)
    rows = collect_entries(CostSources(missions_db=path))
    assert len(rows) == 2
    assert {e.provider for e in rows} == {"claude-cli"}
    assert {e.model for e in rows} == {"sonnet"}


def test_mission_seat_quote_is_subscription_not_billed(tmp_path: Path) -> None:
    path = tmp_path / "missions.db"
    _missions_db_with_spawn(path)
    quoted = next(e for e in collect_entries(CostSources(missions_db=path)) if e.cost_usd == 3.15)
    assert quoted.price_source == "subscription"
    # 4 "tokens" for $3.15 is a turn count; it must not land in a token column.
    assert quoted.tokens_in == 0


def test_mission_short_model_name_is_priced_not_a_gap(tmp_path: Path) -> None:
    path = tmp_path / "missions.db"
    _missions_db_with_spawn(path)
    tokens_only = next(
        e for e in collect_entries(CostSources(missions_db=path)) if e.tokens_in == 33_000
    )
    assert tokens_only.price_source == "subscription"
    assert tokens_only.cost_usd > 0


# ---------------------------------------------------------------------------
# Agent chat: the OpenAI usage convention counts cache hits inside the input
# ---------------------------------------------------------------------------


def _agent_chat_db_two_runners(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_AGENT_CHAT_DDL)
    conn.execute(
        "INSERT INTO agent_chat_sessions (session_id, title, provider, model, created_ms, "
        "updated_ms) VALUES (?,?,?,?,?,?)",
        ("chat-2", "Two runners", "claude", "claude-opus-4-7-20251022", T0, T0),
    )
    events = [
        ("chat-2", 1, T0 + 1_000, "turn_started", json.dumps(
            {"turn_id": "t-codex", "runner": "codex-cli", "provider": "codex",
             "model": "gpt-5.5"})),
        ("chat-2", 2, T0 + 2_000, "turn_finished", json.dumps(
            {"turn_id": "t-codex", "status": "done", "cost_usd": 0.0,
             "usage": {"input_tokens": 1_000, "cached_input_tokens": 900,
                       "output_tokens": 10}})),
        ("chat-2", 3, T0 + 3_000, "turn_started", json.dumps(
            {"turn_id": "t-claude", "runner": "claude-cli", "provider": "claude",
             "model": "claude-opus-4-7-20251022"})),
        ("chat-2", 4, T0 + 4_000, "turn_finished", json.dumps(
            {"turn_id": "t-claude", "status": "done", "cost_usd": 0.0,
             "usage": {"input_tokens": 1_000, "cache_read_input_tokens": 900,
                       "output_tokens": 10}})),
    ]
    conn.executemany(
        "INSERT INTO agent_chat_events (session_id, seq, ts_ms, kind, payload) VALUES (?,?,?,?,?)",
        events,
    )
    conn.commit()
    conn.close()


def test_codex_cache_hits_are_subtracted_from_input_but_claude_ones_are_not(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agent_chat.db"
    _agent_chat_db_two_runners(path)
    rows = {e.model: e for e in collect_entries(CostSources(agent_chat_db=path))}
    assert rows["gpt-5.5"].tokens_in == 100
    assert rows["gpt-5.5"].tokens_cached == 900
    assert rows["claude-opus-4-7-20251022"].tokens_in == 1_000
    assert rows["claude-opus-4-7-20251022"].tokens_cached == 900


def test_a_subscription_provider_is_priced_not_free() -> None:
    """A paid seat is worth its API-equivalent; "$0.00 free" is for local engines."""
    cost, source = price_entry(
        provider="codex-subscription-realtime",
        model="gpt-5.5",
        tokens_in=1_000_000,
        tokens_out=0,
        recorded_usd=0.0,
    )
    assert source == "subscription"
    assert cost > 0
