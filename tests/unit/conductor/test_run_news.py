"""Conductor tells the user about state CHANGES, never about every run.

Audit AU-01: the Conductor scheduler runs from the moment the app boots, three
seed jobs are enabled out of the box, and every result went into its own SQLite
and nowhere else. From outside, Jarvis looked like it had no proactivity at all.

The fix has two halves, and this file pins the Conductor half:

* ``conductor.core.notify.classify_run`` decides what is worth saying — a job
  that just started failing, or one that just recovered. The 300th consecutive
  pass of the 5-minute healthcheck is not news and stays silent.
* ``Runner`` emits that verdict through its existing ``on_event`` seam, so the
  package still never imports ``jarvis``.

Real store, real shell subprocess, real runner — no mocks.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from conductor import (
    NEWS_EVENT,
    ConductorStore,
    IntervalSchedule,
    Job,
    ManualSchedule,
    Runner,
    ShellJobSpec,
)
from conductor.core.notify import (
    FAILURES_TO_ANNOUNCE_FREQUENT,
    classify_run,
    failures_to_announce,
)


@pytest.fixture
async def store(tmp_path: Path) -> ConductorStore:
    s = ConductorStore(tmp_path / "conductor.sqlite")
    await s.init()
    yield s
    await s.close()


class _EventSink:
    """Records every ``on_event`` call, exactly as a real observer sees it."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, dict(payload)))

    @property
    def news(self) -> list[dict[str, Any]]:
        return [p for name, p in self.events if name == NEWS_EVENT]


def _script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{path}"'


async def _drain_runs() -> None:
    """Wait until every in-flight Conductor run task has finished."""
    for _ in range(50):
        pending = [
            t
            for t in asyncio.all_tasks()
            if t.get_name().startswith("conductor-run-") and not t.done()
        ]
        if not pending:
            return
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True), timeout=30.0
        )
    raise AssertionError("Conductor runs never settled")


# ----------------------------------------------------------------------
# The rule itself
# ----------------------------------------------------------------------

def _classify(
    previous: str | None,
    new: str,
    error: str | None = None,
    *,
    streak: int = 1,
    required: int = 1,
    announced: bool = False,
):
    return classify_run(
        job_id="job-1",
        job_name="GitHub-API Zen",
        run_id="run-1",
        trigger="interval",
        previous_state=previous,
        new_state=new,
        error=error,
        failure_streak=streak,
        failures_required=required,
        failing_was_announced=announced,
    )


def test_a_job_that_starts_failing_is_news() -> None:
    news = _classify("completed", "failed", error="status 500 does not match '2xx'")
    assert news is not None
    assert news.kind == "failing"
    assert news.job_name == "GitHub-API Zen"


def test_a_first_ever_run_that_fails_is_news() -> None:
    """No predecessor and it broke — the user has never had a working job."""
    assert _classify(None, "failed") is not None


def test_a_job_that_keeps_failing_says_nothing_more() -> None:
    """One alert per breakage. The second failing run is the same breakage."""
    assert _classify("failed", "failed", streak=2) is None
    assert _classify("failed", "failed", streak=7, required=3) is None


def test_the_three_hundredth_passing_healthcheck_is_silent() -> None:
    assert _classify("completed", "completed") is None


def test_a_first_ever_run_that_succeeds_is_silent() -> None:
    """Every seed job runs for the first time right after boot. Announcing
    those would make the very first minute of the app pure noise."""
    assert _classify(None, "completed") is None


def test_recovery_is_news() -> None:
    news = _classify("failed", "completed", announced=True)
    assert news is not None
    assert news.kind == "recovered"
    # A recovery carries no error detail — there is nothing wrong to report.
    assert news.detail == ""


def test_a_recovery_nobody_heard_the_breakage_of_is_silent() -> None:
    """Live box 2026-08-23: one 504 on the 5-minute healthcheck woke the user
    twice — "started failing", six minutes later "working again". If the
    blip stayed under the threshold and was never spoken, "it works again"
    answers a question nobody heard."""
    assert _classify("failed", "completed", announced=False) is None


# ----------------------------------------------------------------------
# Confirmation window for frequent jobs
# ----------------------------------------------------------------------

def test_a_frequent_job_needs_three_strikes() -> None:
    assert failures_to_announce("interval", "300") == FAILURES_TO_ANNOUNCE_FREQUENT
    assert failures_to_announce("interval", "900") == FAILURES_TO_ANNOUNCE_FREQUENT


def test_a_rare_job_announces_on_the_first_failure() -> None:
    """A broken daily report must not stay silent for three days."""
    assert failures_to_announce("interval", "3600") == 1
    assert failures_to_announce("cron", "0 9 * * 1-5") == 1
    assert failures_to_announce("manual", None) == 1
    assert failures_to_announce("webhook", None) == 1
    assert failures_to_announce(None, None) == 1
    assert failures_to_announce("interval", "not-a-number") == 1


def test_a_single_blip_on_a_frequent_job_is_not_news() -> None:
    assert _classify("completed", "failed", "status 504", required=3, streak=1) is None
    assert _classify("failed", "failed", "status 504", required=3, streak=2) is None


def test_the_third_consecutive_failure_is_the_news() -> None:
    news = _classify("failed", "failed", "getaddrinfo failed", required=3, streak=3)
    assert news is not None
    assert news.kind == "failing"
    # ...and the fourth is the same breakage, already announced.
    assert _classify("failed", "failed", "still down", required=3, streak=4) is None


def test_a_cancelled_run_is_never_news() -> None:
    """Somebody cancelled it on purpose, so they already know."""
    assert _classify("completed", "cancelled") is None
    assert _classify("failed", "cancelled") is None


def test_the_technical_reason_is_condensed_to_one_short_line() -> None:
    stack = "Traceback (most recent call last):\n" + "  frame\n" * 200
    news = _classify("completed", "failed", error=stack)
    assert news is not None
    assert "\n" not in news.detail
    assert len(news.detail) <= 160


def test_payload_is_json_safe_and_names_the_job() -> None:
    news = _classify("completed", "failed", error="boom")
    assert news is not None
    payload = news.as_payload()
    assert payload["kind"] == "failing"
    assert payload["job_name"] == "GitHub-API Zen"
    assert all(isinstance(v, str) for v in payload.values())


# ----------------------------------------------------------------------
# The Runner emits it
# ----------------------------------------------------------------------

async def _run_once(runner: Runner, job_id: str) -> None:
    await runner.trigger(job_id, trigger="interval")
    await _drain_runs()


async def test_runner_announces_a_job_that_starts_failing(
    store: ConductorStore, tmp_path: Path
) -> None:
    sink = _EventSink()
    runner = Runner(store, on_event=sink)
    job = Job(
        name="GitHub-API Zen",
        spec=ShellJobSpec(
            command=_script(tmp_path, "ok.py", "print('fine')"), timeout_s=30.0
        ),
        schedule=ManualSchedule(),
    )
    jid = await store.upsert_job(job)

    # A first, healthy run: the dashboard hears about it, the user does not.
    await _run_once(runner, jid)
    assert sink.news == []
    assert any(name == "run.finished" for name, _ in sink.events)

    # Now the job breaks.
    broken = job.model_copy(
        update={
            "spec": ShellJobSpec(
                command=_script(tmp_path, "bad.py", "import sys; sys.exit(3)"),
                timeout_s=30.0,
            )
        }
    )
    await store.upsert_job(broken)
    await _run_once(runner, jid)

    assert len(sink.news) == 1
    assert sink.news[0]["kind"] == "failing"
    assert sink.news[0]["job_name"] == "GitHub-API Zen"
    assert sink.news[0]["job_id"] == jid


async def test_runner_stays_silent_when_the_same_job_fails_again(
    store: ConductorStore, tmp_path: Path
) -> None:
    sink = _EventSink()
    runner = Runner(store, on_event=sink)
    jid = await store.upsert_job(
        Job(
            name="Broken",
            spec=ShellJobSpec(
                command=_script(tmp_path, "bad.py", "import sys; sys.exit(1)"),
                timeout_s=30.0,
            ),
            schedule=ManualSchedule(),
        )
    )

    await _run_once(runner, jid)
    await _run_once(runner, jid)
    await _run_once(runner, jid)

    assert len(sink.news) == 1, "one breakage, one announcement"


async def test_runner_stays_silent_on_a_boring_repeat_success(
    store: ConductorStore, tmp_path: Path
) -> None:
    sink = _EventSink()
    runner = Runner(store, on_event=sink)
    jid = await store.upsert_job(
        Job(
            name="Healthy",
            spec=ShellJobSpec(
                command=_script(tmp_path, "ok.py", "print('ok')"), timeout_s=30.0
            ),
            schedule=ManualSchedule(),
        )
    )

    for _ in range(3):
        await _run_once(runner, jid)

    assert sink.news == []


async def _force_failure_streak(store: ConductorStore, job_id: str, n: int) -> None:
    """Write ``n`` failed terminal runs straight into the store — the same
    rows a real streak leaves behind, without waiting for real runs."""
    for _ in range(n):
        rid = await store.create_run(job_id, trigger="interval")
        await store.update_run(rid, state="failed", error="status 504")
    await store.set_last_run(job_id, 1, "failed")


async def test_a_frequent_job_blip_never_reaches_the_user(
    store: ConductorStore, tmp_path: Path
) -> None:
    """The 5-minute healthcheck hits one 504 and is fine again: silence."""
    sink = _EventSink()
    runner = Runner(store, on_event=sink)
    bad = ShellJobSpec(
        command=_script(tmp_path, "bad.py", "import sys; sys.exit(1)"), timeout_s=30.0
    )
    good = ShellJobSpec(
        command=_script(tmp_path, "ok.py", "print('ok')"), timeout_s=30.0
    )
    job = Job(name="GitHub-API Zen", spec=good, schedule=IntervalSchedule(seconds=300))
    jid = await store.upsert_job(job)

    await _run_once(runner, jid)  # healthy
    await store.upsert_job(job.model_copy(update={"spec": bad}))
    await _run_once(runner, jid)  # one blip
    await store.upsert_job(job.model_copy(update={"spec": good}))
    await _run_once(runner, jid)  # fine again

    assert sink.news == [], "a single blip must not wake the user twice"


async def test_a_frequent_job_announces_once_the_failure_is_confirmed(
    store: ConductorStore, tmp_path: Path
) -> None:
    sink = _EventSink()
    runner = Runner(store, on_event=sink)
    bad = ShellJobSpec(
        command=_script(tmp_path, "bad.py", "import sys; sys.exit(1)"), timeout_s=30.0
    )
    good = ShellJobSpec(
        command=_script(tmp_path, "ok.py", "print('ok')"), timeout_s=30.0
    )
    job = Job(name="GitHub-API Zen", spec=bad, schedule=IntervalSchedule(seconds=300))
    jid = await store.upsert_job(job)

    for _ in range(FAILURES_TO_ANNOUNCE_FREQUENT - 1):
        await _run_once(runner, jid)
    assert sink.news == [], "not yet confirmed"

    await _run_once(runner, jid)
    assert [n["kind"] for n in sink.news] == ["failing"]

    await _run_once(runner, jid)
    assert len(sink.news) == 1, "the same outage is announced once"

    await store.upsert_job(job.model_copy(update={"spec": good}))
    await _run_once(runner, jid)
    assert [n["kind"] for n in sink.news] == ["failing", "recovered"]


async def test_a_runner_restart_does_not_replay_a_stale_recovery(
    store: ConductorStore, tmp_path: Path
) -> None:
    """The outage was announced by a previous process (or never at all): a
    fresh runner seeing the first success says nothing about it."""
    job = Job(
        name="GitHub-API Zen",
        spec=ShellJobSpec(
            command=_script(tmp_path, "ok.py", "print('ok')"), timeout_s=30.0
        ),
        schedule=IntervalSchedule(seconds=300),
    )
    jid = await store.upsert_job(job)
    await _force_failure_streak(store, jid, 10)

    sink = _EventSink()
    runner = Runner(store, on_event=sink)  # a fresh process
    await _run_once(runner, jid)

    assert sink.news == []


async def test_a_runner_restart_does_not_reannounce_an_old_outage(
    store: ConductorStore, tmp_path: Path
) -> None:
    """Ten failures already on disk, still failing after boot: the streak is
    way past the threshold, so this is the same breakage, not new news."""
    job = Job(
        name="GitHub-API Zen",
        spec=ShellJobSpec(
            command=_script(tmp_path, "bad.py", "import sys; sys.exit(1)"),
            timeout_s=30.0,
        ),
        schedule=IntervalSchedule(seconds=300),
    )
    jid = await store.upsert_job(job)
    await _force_failure_streak(store, jid, 10)

    sink = _EventSink()
    runner = Runner(store, on_event=sink)
    await _run_once(runner, jid)

    assert sink.news == []


async def test_runner_announces_the_recovery(
    store: ConductorStore, tmp_path: Path
) -> None:
    sink = _EventSink()
    runner = Runner(store, on_event=sink)
    bad = ShellJobSpec(
        command=_script(tmp_path, "bad.py", "import sys; sys.exit(1)"), timeout_s=30.0
    )
    good = ShellJobSpec(
        command=_script(tmp_path, "ok.py", "print('back')"), timeout_s=30.0
    )
    job = Job(name="Flaky", spec=bad, schedule=ManualSchedule())
    jid = await store.upsert_job(job)

    await _run_once(runner, jid)
    await store.upsert_job(job.model_copy(update={"spec": good}))
    await _run_once(runner, jid)

    kinds = [n["kind"] for n in sink.news]
    assert kinds == ["failing", "recovered"]


async def test_a_job_with_an_unknown_type_announces_once_not_every_run(
    store: ConductorStore,
) -> None:
    """A job whose spec no longer loads still 'ran' and still failed.

    Recording that terminal state is what keeps the second broken run quiet.
    """
    sink = _EventSink()
    runner = Runner(store, on_event=sink)
    jid = await store.upsert_job(
        Job(
            name="Rotten",
            spec=ShellJobSpec(command="echo hi"),
            schedule=ManualSchedule(),
        )
    )
    conn = store._require_conn()
    await conn.execute(
        "UPDATE jobs SET spec_json = ? WHERE id = ?",
        ('{"type": "not_a_real_type"}', jid),
    )

    await _run_once(runner, jid)
    await _run_once(runner, jid)

    assert len(sink.news) == 1
    assert sink.news[0]["kind"] == "failing"


async def test_a_crashing_callback_never_kills_the_run(
    store: ConductorStore, tmp_path: Path
) -> None:
    """The Conductor loop must not depend on its observer behaving."""

    def _explode(event: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("observer is broken")

    runner = Runner(store, on_event=_explode)
    jid = await store.upsert_job(
        Job(
            name="Resilient",
            spec=ShellJobSpec(
                command=_script(tmp_path, "ok.py", "print('still ran')"),
                timeout_s=30.0,
            ),
            schedule=ManualSchedule(),
        )
    )
    run_id = await runner.trigger(jid, trigger="manual")
    await _drain_runs()

    run = await store.get_run(run_id)
    assert run is not None
    assert run["state"] == "completed"
