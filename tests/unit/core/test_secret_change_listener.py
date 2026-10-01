"""Every credential write or delete is observable in ONE place.

The API-Keys routes, the Control API, CLI connect flows and the setup wizard
all save through ``set_secret`` / ``delete_secret``. Whatever must follow a
key change (the provider-health record forgetting what the old key did, caches
keyed on "which keys exist") hangs off this one hook instead of each route.
"""
from __future__ import annotations

from jarvis.core import config as cfg_mod


def test_a_changed_slot_reaches_every_listener_and_bumps_the_generation() -> None:
    heard: list[str] = []

    def _listener(slot: str) -> None:
        heard.append(slot)

    cfg_mod.add_secret_change_listener(_listener)
    cfg_mod.add_secret_change_listener(_listener)  # idempotent
    before = cfg_mod.secret_generation()
    try:
        cfg_mod._mark_secret_changed("groq_api_key")
    finally:
        cfg_mod._SECRET_CHANGE_LISTENERS.remove(_listener)

    assert heard == ["groq_api_key"]
    assert cfg_mod.secret_generation() == before + 1
    assert cfg_mod.secret_revision("groq_api_key") >= 1


def test_a_failing_listener_never_fails_the_save() -> None:
    def _boom(slot: str) -> None:
        raise RuntimeError("follower down")

    cfg_mod.add_secret_change_listener(_boom)
    try:
        cfg_mod._mark_secret_changed("groq_api_key")  # must not raise
    finally:
        cfg_mod._SECRET_CHANGE_LISTENERS.remove(_boom)


def test_the_health_record_forgets_what_the_old_key_did() -> None:
    """The web layer's listener maps the slot to every card that reads it."""
    from jarvis.brain import provider_health_ledger as ledger
    from jarvis.ui.web import provider_routes  # noqa: F401 — registers the listener

    record = ledger.get_ledger()
    record.record("groq-api", ledger.MODALITY_STT, "bad_key")
    record.record("elevenlabs", ledger.MODALITY_TTS, "no_credits")

    cfg_mod._mark_secret_changed("groq_api_key")

    assert record.get("groq-api", ledger.MODALITY_STT) is None
    assert record.get("elevenlabs", ledger.MODALITY_TTS) is not None
