"""Shared-slot consumer map for the API-Keys UI (field report 2026-07-21).

``secret_slot_consumers`` powers the delete warning: deleting
``openai_api_key`` from the STT card used to silently disable the Brain and
Tool Model too, because the UI had no idea the slot was shared.
"""
from __future__ import annotations

from jarvis.ui.web.provider_spec import get_spec, secret_slot_consumers


def test_openai_key_is_shared_across_multiple_surfaces() -> None:
    consumers = secret_slot_consumers("openai_api_key")
    assert len(consumers) > 1, (
        "one OpenAI key backs several surfaces (brain, STT, TTS, codex "
        "fallback, realtime fallback) — the delete warning depends on this"
    )
    openai_label = get_spec("openai").label
    assert openai_label in consumers


def test_realtime_openai_slot_names_its_fallback_family() -> None:
    # The runtime chain for the openai family cross-reads the realtime slot,
    # so deleting it must at least name the realtime + openai surfaces.
    consumers = secret_slot_consumers("realtime_openai_api_key")
    assert get_spec("openai-realtime").label in consumers
    assert get_spec("openai").label in consumers


def test_unshared_slot_reports_only_its_own_surface() -> None:
    consumers = secret_slot_consumers("nvidia_api_key")
    assert consumers == [get_spec("nvidia").label]


def test_unknown_slot_reports_no_consumers() -> None:
    assert secret_slot_consumers("no_such_slot_ever") == []


# ── every card's key field must actually be writable ─────────────────────────


def test_every_card_secret_slot_is_accepted_by_the_save_route() -> None:
    """A key field the Control API refuses is a dead end with a Save button.

    The save route validates against ``ALLOWED_SECRET_KEYS``, which is derived
    from the setup wizard's SECRETS table — a DIFFERENT list from the provider
    cards' ``secret_keys``. Nothing tied the two together, so a provider could
    ship a card whose field looked ordinary and answered "Unknown secret key"
    on save. That is what the four Vertex cards did (field report 2026-08-17):
    the slots existed everywhere the runtime looks and nowhere the writer does.

    BUG-008 shape — two lists of the same fact — so it gets a guard rather than
    a fix.
    """
    from jarvis.ui.web.provider_routes import ALLOWED_SECRET_KEYS
    from jarvis.ui.web.provider_spec import PROVIDERS

    declared = {key for spec in PROVIDERS for key in spec.secret_keys}
    unwritable = sorted(declared - ALLOWED_SECRET_KEYS)
    assert not unwritable, (
        "these provider cards render a key field the save route rejects: "
        f"{unwritable}. Add a SecretSpec row in jarvis/setup/wizard.py "
        "(prompt=False when setup should not ask for it)."
    )
