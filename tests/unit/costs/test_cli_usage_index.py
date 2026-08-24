"""Tests for the coding-CLI usage index (``jarvis/costs/cli_usage_index.py``).

Everything here runs against transcripts built inside ``tmp_path``. The real
``~/.claude`` and ``~/.codex`` are never read: every call passes the ``home``
seam, so a test can neither depend on this machine's history nor be slowed
down by nine gigabytes of it.

The index makes five claims that are easy to get quietly wrong, and each has
its own test:

1. **A response is counted once.** Claude Code writes one line per content
   block of the SAME API response, each with its own ``uuid`` and the same
   usage. Counting lines instead of responses roughly doubles the bill.
2. **Rescanning is free and idempotent.** An unchanged file is skipped; a file
   read again anyway still adds nothing; an appended file yields only its new
   lines; a shrunk file is re-read from zero with its stale rows dropped.
3. **Cumulative counters are not summed.** Codex reports both a per-turn and a
   running total on the same record; only the per-turn one may be read.
4. **Reasoning is a breakdown of output, not an addition to it.**
5. **Nothing raises.** A corrupt line, a missing root, an index that does not
   exist yet — each is an empty answer, never an exception.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from jarvis.costs.cli_usage_index import (
    AGENT_AGY,
    AGENT_CLAUDE,
    AGENT_CODEX,
    entries,
    index_db_path,
    index_state,
    refresh,
    rollups,
)

_FAR_FUTURE = 2**62


# ---------------------------------------------------------------------------
# Builders — the record shapes as the live CLIs write them
# ---------------------------------------------------------------------------


def _write(path: Path, lines: list[str]) -> None:
    """Write JSONL as bytes: offsets are byte offsets, so no newline rewriting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def _append(path: Path, lines: list[str]) -> None:
    with path.open("ab") as fh:
        fh.write(("\n".join(lines) + "\n").encode("utf-8"))


def _claude_line(
    *,
    uuid: str,
    msg_id: str,
    ts: str = "2026-08-23T17:16:09.424Z",
    session: str = "sess-1",
    cwd: str = "/work/personal-jarvis",
    model: str = "claude-opus-5",
    input_tokens: int = 10,
    cache_creation: int = 5,
    cache_read: int = 7,
    output: int = 20,
    thinking: int = 8,
) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "uuid": uuid,
            "requestId": f"req_{msg_id}",
            "sessionId": session,
            "timestamp": ts,
            "cwd": cwd,
            "gitBranch": "main",
            "message": {
                "id": msg_id,
                "model": model,
                "usage": {
                    "input_tokens": input_tokens,
                    "cache_creation_input_tokens": cache_creation,
                    "cache_read_input_tokens": cache_read,
                    "output_tokens": output,
                    "output_tokens_details": {"thinking_tokens": thinking},
                },
            },
        }
    )


def _claude_path(home: Path, name: str = "sess-1") -> Path:
    return home / ".claude" / "projects" / "-work-personal-jarvis" / f"{name}.jsonl"


def _codex_token_line(
    *,
    ts: str = "2026-08-13T15:05:55.557Z",
    last: dict[str, int] | None = None,
    total: dict[str, int] | None = None,
    turn: int = 1,
) -> str:
    """One ``token_count`` record.

    ``turn`` moves the running total the way a real session does — it only
    ever grows — because the total is what identifies a call across files.
    Two records with the same total ARE the same call.
    """
    return json.dumps(
        {
            "timestamp": ts,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": total
                    or {
                        "input_tokens": 900_000 + 100 * turn,
                        "cached_input_tokens": 400_000 + 40 * turn,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 90_000 + 30 * turn,
                        "reasoning_output_tokens": 40_000,
                        "total_tokens": 990_000 + 130 * turn,
                    },
                    "last_token_usage": last
                    or {
                        "input_tokens": 100,
                        "cached_input_tokens": 40,
                        "cache_write_input_tokens": 5,
                        "output_tokens": 30,
                        "reasoning_output_tokens": 12,
                        "total_tokens": 130,
                    },
                    "model_context_window": 258_400,
                },
            },
        }
    )


def _codex_path(home: Path, session: str = "019ffba8-3748-7652-bf9d-f3b54697b10a") -> Path:
    return (
        home
        / ".codex"
        / "sessions"
        / "2026"
        / "08"
        / "13"
        / f"rollout-2026-08-13T17-05-33-{session}.jsonl"
    )


def _codex_prelude(session: str, cwd: str = "/work/downloads") -> list[str]:
    return [
        json.dumps(
            {
                "timestamp": "2026-08-13T15:05:44.589Z",
                "type": "session_meta",
                "payload": {"session_id": session, "cwd": cwd},
            }
        ),
        json.dumps(
            {
                "timestamp": "2026-08-13T15:05:53.434Z",
                "type": "turn_context",
                "payload": {"model": "gpt-5.6-terra", "cwd": cwd},
            }
        ),
    ]


def _agy_line(
    *,
    ts: float = 1_770_293_705.85,
    input_other: int = 1384,
    output: int = 184,
    cache_read: int = 4864,
    cache_creation: int = 0,
) -> str:
    return json.dumps(
        {
            "timestamp": ts,
            "message": {
                "type": "StatusUpdate",
                "payload": {
                    "context_usage": 0.0238,
                    "token_usage": {
                        "input_other": input_other,
                        "output": output,
                        "input_cache_read": cache_read,
                        "input_cache_creation": cache_creation,
                    },
                    "message_id": "chatcmpl-TQB83Qe9ZRALondCq3HgwhL8",
                },
            },
        }
    )


def _all(data_dir: Path) -> list:
    return list(entries(data_dir=data_dir, since_ms=0, until_ms=_FAR_FUTURE))


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------


def test_claude_transcript_is_indexed(tmp_path: Path) -> None:
    """One assistant record becomes one turn, split by the shared convention."""
    data = tmp_path / "data"
    _write(_claude_path(tmp_path), [_claude_line(uuid="u1", msg_id="msg_a")])

    result = refresh(data_dir=data, home=tmp_path)

    assert result.complete is True
    assert result.files_scanned == 1
    assert result.turns_added == 1
    assert result.bytes_read > 0

    (turn,) = _all(data)
    assert turn.agent == AGENT_CLAUDE
    assert turn.session_id == "sess-1"
    assert turn.model == "claude-opus-5"
    # input = input_tokens + cache CREATION; cache reads are their own bucket.
    assert turn.tokens_in == 15
    assert turn.tokens_cached == 7
    # Thinking is inside the output count, never added on top of it.
    assert turn.tokens_out == 20
    assert turn.cwd == "/work/personal-jarvis"
    assert turn.label == "personal-jarvis"
    assert turn.ts_ms == 1787505369424


def test_one_response_written_as_several_lines_counts_once(tmp_path: Path) -> None:
    """The uuid is per content block; the billed unit is ``message.id``.

    A live transcript sampled on this machine held 64 assistant lines for 31
    API responses, every line repeating its response's full usage. Keying on
    ``uuid`` would have reported roughly twice the tokens that were spent.
    """
    data = tmp_path / "data"
    _write(
        _claude_path(tmp_path),
        [
            _claude_line(uuid="u1", msg_id="msg_a", ts="2026-08-23T17:16:09.424Z"),
            _claude_line(uuid="u2", msg_id="msg_a", ts="2026-08-23T17:16:10.135Z"),
            _claude_line(uuid="u3", msg_id="msg_a", ts="2026-08-23T17:16:11.728Z"),
            _claude_line(uuid="u4", msg_id="msg_b", ts="2026-08-23T17:16:13.603Z"),
        ],
    )

    result = refresh(data_dir=data, home=tmp_path)

    assert result.turns_added == 2
    turns = _all(data)
    assert len(turns) == 2
    assert sum(t.tokens_out for t in turns) == 40


def test_reasoning_only_usage_falls_back_to_the_breakdown(tmp_path: Path) -> None:
    """Reasoning stands in for output ONLY when there is no output count."""
    data = tmp_path / "data"
    _write(
        _claude_path(tmp_path),
        [_claude_line(uuid="u1", msg_id="msg_a", output=0, thinking=12)],
    )

    refresh(data_dir=data, home=tmp_path)

    (turn,) = _all(data)
    assert turn.tokens_out == 12


def test_rerunning_refresh_adds_nothing(tmp_path: Path) -> None:
    """An unchanged file is skipped, and would still dedupe if it were read."""
    data = tmp_path / "data"
    _write(
        _claude_path(tmp_path),
        [_claude_line(uuid="u1", msg_id="msg_a"), _claude_line(uuid="u2", msg_id="msg_b")],
    )

    first = refresh(data_dir=data, home=tmp_path)
    second = refresh(data_dir=data, home=tmp_path)

    assert first.turns_added == 2
    assert second.turns_added == 0
    assert second.files_scanned == 0
    assert second.complete is True
    assert len(_all(data)) == 2


def test_appending_picks_up_only_the_new_lines(tmp_path: Path) -> None:
    """The second run resumes at the stored byte offset."""
    data = tmp_path / "data"
    path = _claude_path(tmp_path)
    _write(path, [_claude_line(uuid="u1", msg_id="msg_a")])
    first = refresh(data_dir=data, home=tmp_path)

    _append(path, [_claude_line(uuid="u2", msg_id="msg_b")])
    second = refresh(data_dir=data, home=tmp_path)

    assert second.turns_added == 1
    assert second.files_scanned == 1
    # Exactly the appended bytes were read the second time round — the first
    # line was never touched again.
    assert first.bytes_read + second.bytes_read == path.stat().st_size
    assert len(_all(data)) == 2


def test_a_half_written_trailing_line_is_left_for_the_next_run(tmp_path: Path) -> None:
    """A line without its newline is the CLI mid-write, never a parsed turn."""
    data = tmp_path / "data"
    path = _claude_path(tmp_path)
    _write(path, [_claude_line(uuid="u1", msg_id="msg_a")])
    with path.open("ab") as fh:
        fh.write(_claude_line(uuid="u2", msg_id="msg_b")[:40].encode("utf-8"))

    refresh(data_dir=data, home=tmp_path)
    assert len(_all(data)) == 1

    # Completing the line makes it readable, and it is read exactly once.
    with path.open("ab") as fh:
        fh.write(_claude_line(uuid="u2", msg_id="msg_b")[40:].encode("utf-8"))
        fh.write(b"\n")
    result = refresh(data_dir=data, home=tmp_path)

    assert result.turns_added == 1
    assert len(_all(data)) == 2


def test_a_shrunk_file_is_reread_from_scratch(tmp_path: Path) -> None:
    """Rotated or rewritten: the old rows describe bytes that no longer exist."""
    data = tmp_path / "data"
    path = _claude_path(tmp_path)
    _write(
        path,
        [
            _claude_line(uuid="u1", msg_id="msg_a"),
            _claude_line(uuid="u2", msg_id="msg_b"),
            _claude_line(uuid="u3", msg_id="msg_c"),
        ],
    )
    refresh(data_dir=data, home=tmp_path)
    assert len(_all(data)) == 3

    _write(path, [_claude_line(uuid="u9", msg_id="msg_z")])
    result = refresh(data_dir=data, home=tmp_path)

    assert result.turns_added == 1
    turns = _all(data)
    assert len(turns) == 1
    assert turns[0].tokens_in == 15


def test_a_corrupt_line_does_not_cost_the_file(tmp_path: Path) -> None:
    """One unparsable record is one skipped record, not a skipped transcript."""
    data = tmp_path / "data"
    _write(
        _claude_path(tmp_path),
        [
            _claude_line(uuid="u1", msg_id="msg_a"),
            '{"type": "assistant", "usage": {broken',
            "not json at all, but it says usage",
            _claude_line(uuid="u2", msg_id="msg_b"),
        ],
    )

    result = refresh(data_dir=data, home=tmp_path)

    assert result.turns_added == 2
    assert result.errors == 0
    assert len(_all(data)) == 2


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------


def test_codex_uses_last_token_usage_and_ignores_the_running_total(tmp_path: Path) -> None:
    """``total_token_usage`` is cumulative; summing it multiplies the bill."""
    data = tmp_path / "data"
    session = "019ffba8-3748-7652-bf9d-f3b54697b10a"
    _write(
        _codex_path(tmp_path, session),
        [*_codex_prelude(session), _codex_token_line(turn=1), _codex_token_line(turn=2)],
    )

    refresh(data_dir=data, home=tmp_path)

    turns = _all(data)
    assert len(turns) == 2
    assert {t.agent for t in turns} == {AGENT_CODEX}
    # last_token_usage only. OpenAI reports the 40 cache hits INSIDE the 100
    # input tokens, so uncached input is 100 - 40 + 5 cache write = 65.
    assert [t.tokens_in for t in turns] == [65, 65]
    assert [t.tokens_out for t in turns] == [30, 30]
    assert [t.tokens_cached for t in turns] == [40, 40]
    # The model lives on turn_context and is carried forward to the usage rows.
    assert {t.model for t in turns} == {"gpt-5.6-terra"}
    assert {t.session_id for t in turns} == {session}
    assert {t.cwd for t in turns} == {"/work/downloads"}


def test_codex_records_without_usage_contribute_nothing(tmp_path: Path) -> None:
    """``info: null`` is what a session writes before its first model call."""
    data = tmp_path / "data"
    session = "019ffba8-3748-7652-bf9d-f3b54697b10a"
    _write(
        _codex_path(tmp_path, session),
        [
            *_codex_prelude(session),
            json.dumps(
                {
                    "timestamp": "2026-08-13T15:05:55.557Z",
                    "type": "event_msg",
                    "payload": {"type": "token_count", "info": None},
                }
            ),
        ],
    )

    result = refresh(data_dir=data, home=tmp_path)

    assert result.files_scanned == 1
    assert result.turns_added == 0
    assert _all(data) == []


def test_codex_turns_are_not_recounted_after_an_append(tmp_path: Path) -> None:
    """Codex records carry no id, so identity is the line's place in the file."""
    data = tmp_path / "data"
    session = "019ffba8-3748-7652-bf9d-f3b54697b10a"
    path = _codex_path(tmp_path, session)
    _write(path, [*_codex_prelude(session), _codex_token_line(turn=1)])
    refresh(data_dir=data, home=tmp_path)

    _append(path, [_codex_token_line(ts="2026-08-13T15:09:00.000Z", turn=2)])
    result = refresh(data_dir=data, home=tmp_path)

    assert result.turns_added == 1
    assert len(_all(data)) == 2


def test_codex_fork_replays_its_parent_but_is_counted_once(tmp_path: Path) -> None:
    """A fork writes the whole parent history into a new file (2026-08-24).

    Real shape: 1 787 ``token_count`` records inside 100 ms, all stamped
    with the moment of the fork, before any ``turn_context`` — so no model.
    The running total identifies each call, so the replay collapses onto the
    parent's rows, and the parent's model fills the rows the replay lacked.
    """
    data = tmp_path / "data"
    parent = "019fb38d-0077-7823-a306-c7c256d64efe"
    fork = "019fb88c-ebde-7d11-b851-5ae72a0885cb"
    fork_meta = json.dumps(
        {
            "timestamp": "2026-07-31T14:21:11.072Z",
            "type": "session_meta",
            "payload": {
                "session_id": parent,
                "id": fork,
                "forked_from_id": parent,
                "cwd": "/work/downloads",
            },
        }
    )
    replay = [
        _codex_token_line(ts="2026-07-31T14:21:11.389Z", turn=1),
        _codex_token_line(ts="2026-07-31T14:21:11.389Z", turn=2),
    ]
    # The fork is the NEWER file, so it is scanned first — with no model.
    fork_path = tmp_path / ".codex" / "sessions" / "2026" / "07" / "31" / (
        f"rollout-2026-07-31T16-21-11-{fork}.jsonl"
    )
    _write(fork_path, [fork_meta, *replay, _codex_token_line(turn=3)])
    parent_path = tmp_path / ".codex" / "sessions" / "2026" / "07" / "30" / (
        f"rollout-2026-07-30T17-03-10-{parent}.jsonl"
    )
    _write(
        parent_path,
        [*_codex_prelude(parent), _codex_token_line(turn=1), _codex_token_line(turn=2)],
    )
    import os

    os.utime(parent_path, ns=(1, 1))

    refresh(data_dir=data, home=tmp_path)

    turns = _all(data)
    # Two real calls in the parent, one new call in the fork. Not five.
    assert len(turns) == 3
    assert {t.session_id for t in turns} == {parent}
    # The replayed rows had no model; the parent's copy supplied it.
    assert sum(1 for t in turns if t.model == "gpt-5.6-terra") == 2
    assert sum(1 for t in turns if t.model == "") == 1


def test_an_index_built_under_an_older_rule_is_rebuilt(tmp_path: Path) -> None:
    """A schema bump throws the old rows away rather than mixing two rules."""
    import sqlite3

    from jarvis.costs.cli_usage_index import index_db_path

    data = tmp_path / "data"
    session = "019ffba8-3748-7652-bf9d-f3b54697b10a"
    _write(_codex_path(tmp_path, session), [*_codex_prelude(session), _codex_token_line()])
    refresh(data_dir=data, home=tmp_path)
    assert len(_all(data)) == 1

    db = index_db_path(data)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE cli_turns SET tokens_in = 999999"
        )  # a number the old rule produced
        conn.execute("PRAGMA user_version=1")

    refresh(data_dir=data, home=tmp_path)

    turns = _all(data)
    assert len(turns) == 1
    assert turns[0].tokens_in == 65


# ---------------------------------------------------------------------------
# agy
# ---------------------------------------------------------------------------


def test_agy_legacy_wire_log_is_indexed(tmp_path: Path) -> None:
    """The shape taken from real legacy transcripts, translated to the convention."""
    data = tmp_path / "data"
    wire = (
        tmp_path
        / ".kimi"
        / "sessions"
        / "7b011e17c1582b98e430e02b1d15d8d4"
        / "0efac775-c21e-4ab5-8439-b8cecd92c7f8"
        / "wire.jsonl"
    )
    _write(wire, [_agy_line(), _agy_line(input_other=220, output=87, cache_read=13568)])

    refresh(data_dir=data, home=tmp_path)

    turns = _all(data)
    assert len(turns) == 2
    assert {t.agent for t in turns} == {AGENT_AGY}
    assert turns[0].tokens_in == 1384
    assert turns[0].tokens_out == 184
    assert turns[0].tokens_cached == 4864
    # agy writes no model id anywhere in its transcript.
    assert turns[0].model == ""
    assert turns[0].session_id == "0efac775-c21e-4ab5-8439-b8cecd92c7f8"
    assert turns[0].ts_ms == 1770293705850


def test_agy_current_layout_takes_its_folder_from_state_json(tmp_path: Path) -> None:
    session = tmp_path / ".kimi-code" / "sessions" / "wd_jarvis_1fce" / "session_abc"
    session.mkdir(parents=True)
    (session / "state.json").write_text(
        json.dumps({"title": "New Session", "workDir": "/work/personal-jarvis"}),
        encoding="utf-8",
    )
    _write(session / "agents" / "main" / "wire.jsonl", [_agy_line()])
    data = tmp_path / "data"

    refresh(data_dir=data, home=tmp_path)

    (turn,) = _all(data)
    assert turn.session_id == "session_abc"
    assert turn.cwd == "/work/personal-jarvis"
    assert turn.label == "personal-jarvis"


def test_agy_setup_only_transcript_yields_nothing(tmp_path: Path) -> None:
    """What every current-layout transcript on the reference machine looks like."""
    data = tmp_path / "data"
    wire = (
        tmp_path / ".kimi-code" / "sessions" / "wd_x_1" / "session_abc" / "agents" / "main"
        / "wire.jsonl"
    )
    _write(
        wire,
        [
            json.dumps({"type": "metadata", "protocol_version": "1.4"}),
            json.dumps({"type": "config.update", "profileName": "agent"}),
            json.dumps({"type": "tools.set_active_tools", "names": ["Read"]}),
        ],
    )

    result = refresh(data_dir=data, home=tmp_path)

    assert result.files_scanned == 1
    assert result.turns_added == 0
    assert _all(data) == []


# ---------------------------------------------------------------------------
# Budget, empty machines, state
# ---------------------------------------------------------------------------


def test_a_spent_deadline_stops_the_run_and_says_so(tmp_path: Path) -> None:
    """Whatever was read is kept; the caller is told the picture is partial."""
    data = tmp_path / "data"
    _write(_claude_path(tmp_path, "a"), [_claude_line(uuid="u1", msg_id="msg_a")])
    _write(_claude_path(tmp_path, "b"), [_claude_line(uuid="u2", msg_id="msg_b")])

    cut = refresh(data_dir=data, home=tmp_path, deadline_s=0.0)

    assert cut.complete is False
    assert cut.files_seen == 2
    assert cut.files_scanned == 0
    assert cut.turns_added == 0

    # The next run has a budget and finishes the job.
    full = refresh(data_dir=data, home=tmp_path)
    assert full.complete is True
    assert full.turns_added == 2


def test_since_ms_skips_older_files_without_consuming_them(tmp_path: Path) -> None:
    """A window narrower than a file's mtime leaves its resume state untouched."""
    data = tmp_path / "data"
    _write(_claude_path(tmp_path), [_claude_line(uuid="u1", msg_id="msg_a")])
    future_ms = int((time.time() + 3600) * 1000)

    skipped = refresh(data_dir=data, home=tmp_path, since_ms=future_ms)

    assert skipped.files_seen == 1
    assert skipped.files_scanned == 0
    assert skipped.turns_added == 0

    later = refresh(data_dir=data, home=tmp_path, since_ms=0)
    assert later.turns_added == 1


def test_a_machine_without_any_cli_returns_empty_results(tmp_path: Path) -> None:
    """A fresh install has none of these directories and must not fail."""
    data = tmp_path / "data"
    empty_home = tmp_path / "nobody"

    result = refresh(data_dir=data, home=empty_home)

    assert result.complete is True
    assert result.files_seen == 0
    assert result.turns_added == 0
    assert result.errors == 0
    assert _all(data) == []

    state = index_state(data_dir=data, home=empty_home)
    assert state.files_known == 0
    assert state.turns == 0
    assert state.complete is True


def test_reading_before_the_index_exists_is_empty_not_an_error(tmp_path: Path) -> None:
    data = tmp_path / "never-refreshed"

    assert _all(data) == []
    state = index_state(data_dir=data, home=tmp_path / "nobody")
    assert state.turns == 0
    assert state.db_path == index_db_path(data)


def test_index_state_reports_what_is_still_unread(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _write(_claude_path(tmp_path, "a"), [_claude_line(uuid="u1", msg_id="msg_a")])
    _write(_claude_path(tmp_path, "b"), [_claude_line(uuid="u2", msg_id="msg_b")])

    pending = index_state(data_dir=data, home=tmp_path)
    assert pending.files_known == 2
    assert pending.files_pending == 2
    assert pending.bytes_pending > 0
    assert pending.complete is False

    refresh(data_dir=data, home=tmp_path)

    done = index_state(data_dir=data, home=tmp_path)
    assert done.files_indexed == 2
    assert done.files_pending == 0
    assert done.bytes_pending == 0
    assert done.turns == 2
    assert done.complete is True


def test_entries_are_windowed_and_ordered(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _write(
        _claude_path(tmp_path),
        [
            _claude_line(uuid="u2", msg_id="msg_b", ts="2026-08-23T12:00:00.000Z"),
            _claude_line(uuid="u1", msg_id="msg_a", ts="2026-08-21T12:00:00.000Z"),
            _claude_line(uuid="u3", msg_id="msg_c", ts="2026-08-25T12:00:00.000Z"),
        ],
    )
    refresh(data_dir=data, home=tmp_path)

    stamps = [t.ts_ms for t in _all(data)]
    assert stamps == sorted(stamps)

    window = list(
        entries(data_dir=data, since_ms=stamps[0] + 1, until_ms=stamps[2] - 1)
    )
    assert len(window) == 1
    assert window[0].ts_ms == stamps[1]


def test_all_three_agents_share_one_index(tmp_path: Path) -> None:
    data = tmp_path / "data"
    session = "019ffba8-3748-7652-bf9d-f3b54697b10a"
    _write(_claude_path(tmp_path), [_claude_line(uuid="u1", msg_id="msg_a")])
    _write(_codex_path(tmp_path, session), [*_codex_prelude(session), _codex_token_line()])
    _write(
        tmp_path / ".kimi" / "sessions" / "bucket" / "sess" / "wire.jsonl",
        [_agy_line()],
    )

    result = refresh(data_dir=data, home=tmp_path)

    assert result.files_seen == 3
    assert result.turns_added == 3
    assert {t.agent for t in _all(data)} == {AGENT_CLAUDE, AGENT_CODEX, AGENT_AGY}


# ---------------------------------------------------------------------------
# Rollups — what the cost report actually asks for
# ---------------------------------------------------------------------------


def _rolled(data_dir: Path, bucket_ms: int = 86_400_000) -> list:
    return sorted(
        rollups(data_dir=data_dir, since_ms=0, until_ms=_FAR_FUTURE, bucket_ms=bucket_ms),
        key=lambda r: (r.session_id, r.ts_ms),
    )


def test_rollup_sums_turns_inside_one_bucket(tmp_path: Path) -> None:
    """Three calls hours apart are one row on a daily grain."""
    data = tmp_path / "data"
    _write(
        _claude_path(tmp_path),
        [
            _claude_line(uuid="u0", msg_id="msg_0", ts="2026-08-23T01:00:00.000Z"),
            _claude_line(uuid="u1", msg_id="msg_1", ts="2026-08-23T09:00:00.000Z"),
            _claude_line(uuid="u2", msg_id="msg_2", ts="2026-08-23T17:00:00.000Z"),
        ],
    )
    refresh(data_dir=data, home=tmp_path)

    (row,) = _rolled(data)
    assert row.turns == 3
    assert row.tokens_in == 15 * 3
    assert row.tokens_out == 20 * 3
    assert row.tokens_cached == 7 * 3
    assert row.model == "claude-opus-5"
    # The bucket is stamped with its earliest call, never with "now".
    assert row.ts_ms == min(t.ts_ms for t in _all(data))


def test_rollup_splits_on_the_bucket_boundary(tmp_path: Path) -> None:
    """The same three calls are three rows once the grain is an hour."""
    data = tmp_path / "data"
    _write(
        _claude_path(tmp_path),
        [
            _claude_line(uuid="u0", msg_id="msg_0", ts="2026-08-23T01:00:00.000Z"),
            _claude_line(uuid="u1", msg_id="msg_1", ts="2026-08-23T09:00:00.000Z"),
            _claude_line(uuid="u2", msg_id="msg_2", ts="2026-08-23T17:00:00.000Z"),
        ],
    )
    refresh(data_dir=data, home=tmp_path)

    assert len(_rolled(data, bucket_ms=3_600_000)) == 3


def test_rollup_never_merges_two_sessions(tmp_path: Path) -> None:
    """Session is part of the key — "where it went" would lie otherwise."""
    data = tmp_path / "data"
    _write(
        _claude_path(tmp_path, "sess-1"),
        [_claude_line(uuid="a", msg_id="msg_a", session="sess-1")],
    )
    _write(
        _claude_path(tmp_path, "sess-2"),
        [_claude_line(uuid="b", msg_id="msg_b", session="sess-2")],
    )
    refresh(data_dir=data, home=tmp_path)

    rows = _rolled(data)
    assert len(rows) == 2
    assert {r.session_id for r in rows} == {"sess-1", "sess-2"}
    assert {r.turns for r in rows} == {1}


def test_rollup_totals_match_the_raw_turns(tmp_path: Path) -> None:
    """Whatever the grain, no token may appear or vanish in the grouping."""
    data = tmp_path / "data"
    _write(
        _claude_path(tmp_path),
        [
            _claude_line(
                uuid=f"u{i}", msg_id=f"msg_{i}", ts=f"2026-08-2{i}T04:00:00.000Z"
            )
            for i in range(5)
        ],
    )
    refresh(data_dir=data, home=tmp_path)

    raw = _all(data)
    assert len(raw) == 5
    for bucket in (3_600_000, 86_400_000):
        rolled = _rolled(data, bucket_ms=bucket)
        assert sum(r.turns for r in rolled) == len(raw)
        for field in ("tokens_in", "tokens_out", "tokens_cached"):
            assert sum(getattr(r, field) for r in rolled) == sum(
                getattr(t, field) for t in raw
            )


def test_rollup_of_an_absent_index_is_empty(tmp_path: Path) -> None:
    """A machine that has never indexed returns nothing, not an error."""
    assert _rolled(tmp_path / "nothing") == []
