"""The passive provider-health record behind the status dots (2026-09-30).

The dots on the API-Keys tabs, sidebar, dock and chat composer no longer probe
a provider; they read this record, which real calls and the explicit Test
button feed. These tests pin what it keeps, what it refuses to keep, and how
an outcome ages.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from jarvis.brain import provider_health_ledger as ledger
from jarvis.brain import provider_test as pt


class _Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _CountingLedger(ledger.ProviderHealthLedger):
    """Counts disk writes so the 'no write per call' rule is checkable."""

    def __init__(self, path: Path, clock: _Clock, **kwargs) -> None:  # noqa: ANN003
        super().__init__(path, clock=clock, **kwargs)
        self.writes = 0

    def _write_file(self, payload):  # noqa: ANN001, ANN202
        self.writes += 1
        super()._write_file(payload)


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def record(tmp_path: Path, clock: _Clock) -> _CountingLedger:
    return _CountingLedger(tmp_path / "provider_health.json", clock)


# ── what is evidence ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status",
    [pt.OK, pt.BAD_KEY, pt.NO_CREDITS, pt.RATE_LIMITED, pt.MODEL_UNAVAILABLE, pt.UNREACHABLE],
)
def test_a_decisive_outcome_of_a_real_call_is_kept(record, status: str) -> None:
    assert record.record("openai", ledger.MODALITY_BRAIN, status) is True
    outcome = record.get("openai", ledger.MODALITY_BRAIN)
    assert outcome is not None and outcome.status == status


@pytest.mark.parametrize("status", [pt.ERROR, pt.NOT_CONFIGURED, "whatever"])
def test_an_unrecognised_failure_of_normal_use_is_not_evidence(record, status: str) -> None:
    """A tool bug, a cancelled turn or a missing key says nothing about the
    provider's account — it must not paint its dot red."""
    assert record.record("openai", ledger.MODALITY_BRAIN, status) is False
    assert record.get("openai", ledger.MODALITY_BRAIN) is None


def test_an_explicit_test_verdict_counts_even_when_it_is_error(record) -> None:
    assert record.record(
        "openai", ledger.MODALITY_BRAIN, pt.ERROR, source=ledger.SOURCE_TEST
    )
    assert record.get("openai", ledger.MODALITY_BRAIN).status == pt.ERROR


def test_an_unknown_modality_is_refused(record) -> None:
    assert record.record("openai", "vision", pt.OK) is False


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("Error code: 401 - invalid x-api-key", pt.BAD_KEY),
        ("Error code: 402 - Payment Required", pt.NO_CREDITS),
        ("429 insufficient_quota: check your plan and billing details", pt.NO_CREDITS),
        ("HTTP 429", pt.RATE_LIMITED),
        (TimeoutError(), pt.UNREACHABLE),
    ],
)
def test_a_failure_is_classified_like_the_test_button(record, error, expected) -> None:
    record.record_failure("grok", ledger.MODALITY_BRAIN, error)
    assert record.get("grok", ledger.MODALITY_BRAIN).status == expected


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("tool gmail failed: the credit card field is empty"),
        ValueError("budget spreadsheet not found on the network drive"),
        "payment reminder could not be parsed",
    ],
)
def test_marker_words_from_a_tool_or_app_error_are_not_evidence(record, error) -> None:
    """Only the provider's own answer counts: a false no_credits would never
    expire, and these words turn up in tool and app errors all the time."""
    assert record.record_failure("openai", ledger.MODALITY_BRAIN, error) is False
    assert record.get("openai", ledger.MODALITY_BRAIN) is None


def test_a_provider_sdk_error_without_a_status_code_still_counts(record) -> None:
    class APIConnectionError(Exception):  # the name every OpenAI-compatible SDK uses
        pass

    record.record_failure("openai", ledger.MODALITY_BRAIN, APIConnectionError("Connection error."))
    assert record.get("openai", ledger.MODALITY_BRAIN).status == pt.UNREACHABLE


def test_the_test_button_keeps_the_lenient_classifier(record) -> None:
    """An explicit test is a provider call by construction."""
    record.record_failure(
        "openai", ledger.MODALITY_BRAIN, "credit balance too low", source=ledger.SOURCE_TEST
    )
    assert record.get("openai", ledger.MODALITY_BRAIN).status == pt.NO_CREDITS


def test_no_error_body_or_secret_reaches_the_file(record, tmp_path: Path) -> None:
    """AP-34: only the classification survives, never the provider's words."""
    body = 'Error code: 401 - {"error": "Incorrect API key provided: sk-proj-SECRET123"}'
    record.record_failure("openai", ledger.MODALITY_BRAIN, body)

    text = (tmp_path / "provider_health.json").read_text(encoding="utf-8")
    assert "SECRET123" not in text
    assert "Incorrect" not in text
    assert json.loads(text)["outcomes"][0]["status"] == pt.BAD_KEY


# ── how outcomes combine and age ─────────────────────────────────────────────


def test_a_newer_credential_failure_on_a_sibling_modality_wins(record, clock) -> None:
    """Brain and tool model of one provider share the key."""
    record.record("gemini", ledger.MODALITY_TOOL, pt.OK)
    clock.now += 10
    record.record("gemini", ledger.MODALITY_BRAIN, pt.NO_CREDITS)

    assert record.effective("gemini", ledger.MODALITY_TOOL).status == pt.NO_CREDITS


def test_a_model_failure_stays_on_its_own_modality(record, clock) -> None:
    record.record("gemini", ledger.MODALITY_TOOL, pt.MODEL_UNAVAILABLE)
    clock.now += 10
    record.record("gemini", ledger.MODALITY_BRAIN, pt.OK)

    assert record.effective("gemini", ledger.MODALITY_TOOL).status == pt.MODEL_UNAVAILABLE
    assert record.effective("gemini", ledger.MODALITY_BRAIN).status == pt.OK


def test_a_later_success_with_the_same_key_voids_an_old_credential_failure(
    record, clock
) -> None:
    record.record("gemini", ledger.MODALITY_TOOL, pt.BAD_KEY)
    clock.now += 10
    record.record("gemini", ledger.MODALITY_BRAIN, pt.OK)

    assert record.effective("gemini", ledger.MODALITY_TOOL) is None


def test_a_transient_failure_ages_out_but_a_key_failure_does_not(record, clock) -> None:
    record.record("grok", ledger.MODALITY_BRAIN, pt.RATE_LIMITED)
    record.record("openai", ledger.MODALITY_BRAIN, pt.BAD_KEY)
    clock.now += ledger.TRANSIENT_TTL_S + 1

    assert record.effective("grok", ledger.MODALITY_BRAIN) is None
    assert record.effective("openai", ledger.MODALITY_BRAIN).status == pt.BAD_KEY


def test_the_openrouter_speech_plugin_is_filed_under_its_card(record) -> None:
    """The plugin calls itself ``openrouter`` — the BRAIN card's id."""
    record.record("openrouter", ledger.MODALITY_TTS, pt.OK)

    assert record.get("openrouter-tts", ledger.MODALITY_TTS) is not None
    assert record.get("openrouter", ledger.MODALITY_BRAIN) is None


# ── forgetting ───────────────────────────────────────────────────────────────


def test_forget_drops_every_modality_of_a_provider(record) -> None:
    record.record("openai", ledger.MODALITY_BRAIN, pt.BAD_KEY)
    record.record("openai", ledger.MODALITY_TOOL, pt.OK)
    record.record("grok", ledger.MODALITY_BRAIN, pt.OK)

    assert record.forget(["openai"]) == 2
    assert record.get("openai", ledger.MODALITY_BRAIN) is None
    assert record.get("grok", ledger.MODALITY_BRAIN) is not None


def test_a_model_change_keeps_a_credential_failure(record) -> None:
    record.record("openai", ledger.MODALITY_BRAIN, pt.BAD_KEY)
    record.record("gemini", ledger.MODALITY_BRAIN, pt.MODEL_UNAVAILABLE)

    record.forget(["openai", "gemini"], modality=ledger.MODALITY_BRAIN,
                  keep_credential_failures=True)

    assert record.get("openai", ledger.MODALITY_BRAIN).status == pt.BAD_KEY
    assert record.get("gemini", ledger.MODALITY_BRAIN) is None


# ── persistence ──────────────────────────────────────────────────────────────


def test_outcomes_survive_a_restart(record, tmp_path: Path, clock) -> None:
    record.record("elevenlabs", ledger.MODALITY_TTS, pt.NO_CREDITS)

    reborn = ledger.ProviderHealthLedger(tmp_path / "provider_health.json", clock=clock)

    assert reborn.get("elevenlabs", ledger.MODALITY_TTS).status == pt.NO_CREDITS
    assert reborn.version > 0


def test_the_file_is_written_on_change_not_on_every_call(record, clock) -> None:
    """The voice path records every sentence; it must pay a dict update, not I/O."""
    for _ in range(50):
        record.record("elevenlabs", ledger.MODALITY_TTS, pt.OK)
        clock.now += 1
    assert record.writes == 1

    record.record("elevenlabs", ledger.MODALITY_TTS, pt.NO_CREDITS)
    assert record.writes == 2


def test_the_version_moves_only_when_a_status_changes(record) -> None:
    start = record.version
    record.record("grok", ledger.MODALITY_BRAIN, pt.OK)
    after_first = record.version
    record.record("grok", ledger.MODALITY_BRAIN, pt.OK)

    assert after_first > start
    assert record.version == after_first


def test_a_damaged_file_starts_empty_instead_of_failing(tmp_path: Path) -> None:
    path = tmp_path / "provider_health.json"
    path.write_text("{not json", encoding="utf-8")

    damaged = ledger.ProviderHealthLedger(path)

    assert damaged.get("openai", ledger.MODALITY_BRAIN) is None
    assert damaged.record("openai", ledger.MODALITY_BRAIN, pt.OK) is True


def test_module_helpers_never_raise_into_the_call_path(monkeypatch) -> None:
    """A health record must never cost the call it describes."""

    class _Broken(ledger.ProviderHealthLedger):
        def record(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            raise OSError("disk gone")

    ledger.set_ledger(_Broken(None))
    ledger.record_success("openai", ledger.MODALITY_BRAIN)
    ledger.record_failure("openai", ledger.MODALITY_BRAIN, RuntimeError("x"))


# ── a key replaced elsewhere ─────────────────────────────────────────────────


def test_a_credential_failure_is_voided_by_a_key_changed_elsewhere(record) -> None:
    """The terminal setup wizard runs in another process: no listener fires
    here, but the next read with the new key's fingerprint voids the verdict."""
    record.record("openai", ledger.MODALITY_BRAIN, pt.BAD_KEY)
    old = ledger.credential_fingerprint("sk-old")
    new = ledger.credential_fingerprint("sk-new")

    # First read stamps the failure with the key it was judged against.
    assert record.effective("openai", ledger.MODALITY_BRAIN, credential=old).status == pt.BAD_KEY
    assert record.get("openai", ledger.MODALITY_BRAIN).credential == old
    # Same key again: still red.
    assert record.effective("openai", ledger.MODALITY_BRAIN, credential=old).status == pt.BAD_KEY
    # A different key: the stale verdict is gone for good.
    assert record.effective("openai", ledger.MODALITY_BRAIN, credential=new) is None
    assert record.get("openai", ledger.MODALITY_BRAIN) is None


def test_the_fingerprint_is_not_the_key() -> None:
    fingerprint = ledger.credential_fingerprint("sk-proj-SECRET123")
    assert "SECRET123" not in fingerprint
    assert len(fingerprint) == 16
    assert ledger.credential_fingerprint("") == ""


# ── listeners ────────────────────────────────────────────────────────────────


def test_listeners_hear_status_changes_only(record) -> None:
    heard: list[tuple[str, str, str]] = []
    record.add_listener(lambda p, m, s: heard.append((p, m, s)))

    record.record("grok", ledger.MODALITY_BRAIN, pt.OK)
    record.record("grok", ledger.MODALITY_BRAIN, pt.OK)  # unchanged: silent
    record.record("grok", ledger.MODALITY_BRAIN, pt.NO_CREDITS)
    record.forget(["grok"])

    assert heard == [
        ("grok", "brain", pt.OK),
        ("grok", "brain", pt.NO_CREDITS),
        ("", "", ""),  # bulk change
    ]


def test_a_failing_listener_never_costs_the_record(record) -> None:
    def _boom(*_args: str) -> None:
        raise RuntimeError("listener down")

    record.add_listener(_boom)
    assert record.record("grok", ledger.MODALITY_BRAIN, pt.OK) is True


def test_changes_reach_the_bus_from_any_thread(tmp_path: Path) -> None:
    """The web server bridges the record onto the bus; the /ws fan-out then
    carries it to every window. A record made on a voice worker thread must
    arrive too."""
    from jarvis.core.bus import EventBus
    from jarvis.core.events import ProviderHealthChanged

    async def scenario() -> list[ProviderHealthChanged]:
        bus = EventBus()
        seen: list[ProviderHealthChanged] = []

        async def _on(event: ProviderHealthChanged) -> None:
            seen.append(event)

        bus.subscribe(ProviderHealthChanged, _on)
        ledger.set_ledger(ledger.ProviderHealthLedger(tmp_path / "h.json"))
        ledger.publish_changes_to(bus, asyncio.get_running_loop())
        worker = threading.Thread(
            target=ledger.record_failure,
            args=("elevenlabs", ledger.MODALITY_TTS, "HTTP 402 Payment Required"),
        )
        worker.start()
        worker.join()
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.01)
        return seen

    seen = asyncio.run(scenario())

    assert [(e.provider, e.modality, e.status) for e in seen] == [
        ("elevenlabs", "tts", pt.NO_CREDITS)
    ]


# ── background disk I/O ──────────────────────────────────────────────────────


def test_background_mode_never_writes_on_the_callers_thread(tmp_path: Path, clock) -> None:
    """AP-9: the voice path records every sentence; the file is written on
    the record's own writer thread, in order, never on the caller's."""
    path = tmp_path / "provider_health.json"
    record = _CountingLedger(path, clock, background_io=True)
    record.start_loading()
    caller = threading.current_thread()
    writers: list[threading.Thread] = []
    original = record._write_file

    def _spy(payload):  # noqa: ANN001, ANN202
        writers.append(threading.current_thread())
        original(payload)

    record._write_file = _spy  # type: ignore[method-assign]
    record.record("grok", ledger.MODALITY_BRAIN, pt.BAD_KEY)
    record.record("openai", ledger.MODALITY_BRAIN, pt.OK)
    record.flush()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert {o["provider"] for o in saved["outcomes"]} == {"grok", "openai"}
    assert writers and all(thread is not caller for thread in writers)


def test_background_load_merges_the_file_without_losing_newer_outcomes(
    tmp_path: Path, clock
) -> None:
    path = tmp_path / "provider_health.json"
    first = ledger.ProviderHealthLedger(path, clock=clock)
    first.record("grok", ledger.MODALITY_BRAIN, pt.NO_CREDITS)
    first.record("elevenlabs", ledger.MODALITY_TTS, pt.OK)

    reborn = ledger.ProviderHealthLedger(path, clock=clock, background_io=True)
    # A real call lands before the file has been read: it must win.
    reborn.record("grok", ledger.MODALITY_BRAIN, pt.OK)
    reborn.start_loading()
    reborn.flush()

    assert reborn.get("grok", ledger.MODALITY_BRAIN).status == pt.OK
    assert reborn.get("elevenlabs", ledger.MODALITY_TTS).status == pt.OK
    saved = json.loads(path.read_text(encoding="utf-8"))
    by_provider = {o["provider"]: o["status"] for o in saved["outcomes"]}
    assert by_provider == {"grok": pt.OK, "elevenlabs": pt.OK}
