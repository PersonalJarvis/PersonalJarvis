"""A conversation follows its agent from one subscription seat to another.

What these pin down is the one failure a seat switch must never have: the
agent restarting on the new seat with ``--resume <id>`` and finding nothing,
because each seat files its conversations inside its own directory.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from jarvis.agentic_ide import seat_handoff

SID = "0f4d7c1e-1111-4222-8333-944455556666"


def _claude_seat(root: Path, name: str, lines: list[dict], *, mtime: float | None = None) -> Path:
    home = root / name
    folder = home / "projects" / "C--work-repo"
    folder.mkdir(parents=True)
    transcript = folder / f"{SID}.jsonl"
    transcript.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    if mtime is not None:
        os.utime(transcript, (mtime, mtime))
    return home


def test_claude_conversation_is_carried_with_its_side_files(tmp_path: Path) -> None:
    source = _claude_seat(tmp_path, "a", [{"type": "user", "message": {"content": "hi"}}])
    (source / "projects" / "C--work-repo" / SID / "subagents").mkdir(parents=True)
    (source / "projects" / "C--work-repo" / SID / "subagents" / "x.jsonl").write_text("{}\n")
    (source / "file-history" / SID).mkdir(parents=True)
    (source / "file-history" / SID / "snap").write_text("v1")
    target = tmp_path / "b"

    assert seat_handoff.carry_conversation("claude_session", SID, target, [source, target])

    carried = target / "projects" / "C--work-repo" / f"{SID}.jsonl"
    assert carried.read_text(encoding="utf-8").startswith('{"type": "user"')
    assert (target / "projects" / "C--work-repo" / SID / "subagents" / "x.jsonl").is_file()
    assert (target / "file-history" / SID / "snap").read_text() == "v1"


def test_the_newest_copy_wins_and_an_older_one_never_overwrites_it(tmp_path: Path) -> None:
    old = time.time() - 600
    stale = _claude_seat(tmp_path, "a", [{"n": 1}], mtime=old)
    fresh = _claude_seat(tmp_path, "b", [{"n": 1}, {"n": 2}], mtime=old + 300)

    # Switching back to the seat holding the older prefix brings the newest.
    assert seat_handoff.carry_conversation("claude_session", SID, stale, [stale, fresh])
    path = stale / "projects" / "C--work-repo" / f"{SID}.jsonl"
    assert path.read_text(encoding="utf-8").count("\n") == 2

    # The newer seat is left exactly as it is.
    assert seat_handoff.carry_conversation("claude_session", SID, fresh, [stale, fresh])
    assert (fresh / "projects" / "C--work-repo" / f"{SID}.jsonl").read_text().count("\n") == 2


def test_nothing_to_carry_answers_honestly(tmp_path: Path) -> None:
    empty_a, empty_b = tmp_path / "a", tmp_path / "b"
    assert not seat_handoff.carry_conversation("claude_session", SID, empty_b, [empty_a, empty_b])
    assert not seat_handoff.carry_conversation("kimi_session", SID, empty_b, [empty_a])
    assert not seat_handoff.carry_conversation("claude_session", "../evil", empty_b, [empty_a])


def test_codex_rollout_is_carried_to_the_same_day_folder(tmp_path: Path) -> None:
    source = tmp_path / "a"
    day = source / "sessions" / "2026" / "10" / "08"
    day.mkdir(parents=True)
    rollout = day / f"rollout-2026-10-08T10-00-00-{SID}.jsonl"
    rollout.write_text('{"type":"session_meta"}\n', encoding="utf-8")
    target = tmp_path / "b"

    assert seat_handoff.carry_conversation("codex_rollout", SID, target, [source, target])
    assert (target / "sessions" / "2026" / "10" / "08" / rollout.name).is_file()


def test_grok_session_folder_is_carried(tmp_path: Path) -> None:
    source = tmp_path / "a"
    folder = source / "sessions" / "work%20repo" / SID
    folder.mkdir(parents=True)
    (folder / "updates.jsonl").write_text('{"sessionUpdate":"user_message_chunk"}\n')
    target = tmp_path / "b"

    assert seat_handoff.carry_conversation("grok_session", SID, target, [source, target])
    assert (target / "sessions" / "work%20repo" / SID / "updates.jsonl").is_file()


# ------------------------------------------------------------- limit stops


def _iso(at: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(at))


def _claude_limit(at: float) -> dict:
    return {
        "type": "assistant",
        "timestamp": _iso(at),
        "isApiErrorMessage": True,
        "error": "rate_limit",
        "apiErrorStatus": 429,
        "message": {"role": "assistant", "content": [{"type": "text", "text": "limit"}]},
    }


def _claude_user(at: float, text: str = "go on") -> dict:
    return {"type": "user", "timestamp": _iso(at), "message": {"content": text}}


def test_a_claude_turn_cut_off_by_the_limit_is_read_from_the_transcript(tmp_path: Path) -> None:
    now = time.time()
    home = _claude_seat(tmp_path, "a", [_claude_user(now - 60), _claude_limit(now - 30)])
    stop = seat_handoff.limit_stop("claude_session", SID, home, now=now)
    assert stop is not None and abs(stop - (now - 30)) < 2


def test_a_limit_the_user_already_typed_past_is_history(tmp_path: Path) -> None:
    now = time.time()
    home = _claude_seat(
        tmp_path, "a", [_claude_limit(now - 90), _claude_user(now - 60, "continue")]
    )
    assert seat_handoff.limit_stop("claude_session", SID, home, now=now) is None


def test_a_handled_stop_does_not_fire_again_on_the_new_seat(tmp_path: Path) -> None:
    now = time.time()
    home = _claude_seat(tmp_path, "a", [_claude_user(now - 60), _claude_limit(now - 30)])
    stop = seat_handoff.limit_stop("claude_session", SID, home, now=now)
    assert stop is not None
    assert seat_handoff.limit_stop("claude_session", SID, home, after=stop, now=now) is None


def test_an_old_limit_stop_is_stale(tmp_path: Path) -> None:
    now = time.time()
    old = now - seat_handoff.LIMIT_STALE_S - 60
    home = _claude_seat(tmp_path, "a", [_claude_user(old - 5), _claude_limit(old)])
    assert seat_handoff.limit_stop("claude_session", SID, home, now=now) is None


def test_an_ordinary_api_error_is_not_a_limit(tmp_path: Path) -> None:
    now = time.time()
    row = {**_claude_limit(now - 10), "error": "server_error", "apiErrorStatus": 529}
    home = _claude_seat(tmp_path, "a", [_claude_user(now - 60), row])
    assert seat_handoff.limit_stop("claude_session", SID, home, now=now) is None


def test_a_codex_usage_limit_error_is_read(tmp_path: Path) -> None:
    now = time.time()
    home = tmp_path / "codex"
    day = home / "sessions" / "2026" / "10" / "08"
    day.mkdir(parents=True)
    rows = [
        {"timestamp": _iso(now - 60), "type": "event_msg", "payload": {"type": "user_message"}},
        {
            "timestamp": _iso(now - 20),
            "type": "event_msg",
            "payload": {"type": "error", "message": "You've hit your usage limit. Try again."},
        },
    ]
    (day / f"rollout-x-{SID}.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    assert seat_handoff.limit_stop("codex_rollout", SID, home, now=now) is not None
