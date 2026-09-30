"""The passive provider-health record behind the status dots (2026-09-30).

The dots on the API-Keys tabs, sidebar, dock and chat composer no longer probe
a provider; they read this record, which real calls and the explicit Test
button feed. These tests pin what it keeps, what it refuses to keep, and how
an outcome ages.
"""
from __future__ import annotations

import json
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

    def __init__(self, path: Path, clock: _Clock) -> None:
        super().__init__(path, clock=clock)
        self.writes = 0

    def _persist(self, payload):  # noqa: ANN001, ANN202
        self.writes += 1
        super()._persist(payload)


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
