"""The optional "dictation polish" provider tier.

Two things are pinned here, and the second one is the point of the tier:

1. **The cards are DERIVED, never re-declared.** ``POLISH_FAMILIES``
   (``jarvis.dictation.polish_client``) owns the family ids, labels and
   credential slots; ``provider_spec`` contributes only the card text. A second
   hand-typed copy of that list is the exact shape that produced BUG-008 four
   times, so the derivation is asserted rather than assumed.
2. **A missing key here must never raise an amber "needs setup" dot.** The
   polish pass degrades to the previous behaviour instead of breaking, so a
   permanent nag would be the feature's most visible effect on the majority of
   installs -- which is the opposite of what an optional convenience should do.
   The inverse is pinned too: a REQUIRED tier keeps nagging, and an optional
   tier with a key that is genuinely failing still reports the failure.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import jarvis.ui.web.provider_routes as pr
from jarvis.brain import section_health as _section_health
from jarvis.dictation.polish_client import POLISH_FAMILIES
from jarvis.setup.wizard import SECRETS as WIZARD_SECRETS
from jarvis.ui.web.provider_spec import (
    DICTATION_FAMILY_BY_SPEC_ID,
    DICTATION_SPEC_ID_BY_FAMILY,
    PROVIDERS,
    dictation_family_id,
    dictation_spec_id,
    provider_billing,
)

_DICTATION_SPECS = tuple(spec for spec in PROVIDERS if spec.tier == "dictation")
_FAMILY_IDS = frozenset(family.id for family in POLISH_FAMILIES)
_WIZARD_SLOTS = frozenset(secret.key for secret in WIZARD_SECRETS)


def _request(**config_fields):
    """A Request double carrying nothing but ``app.state.config``."""
    state = SimpleNamespace(config=SimpleNamespace(**config_fields))
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _dictation(**fields):
    """A ``[dictation]`` config double with the two keys this tier reads."""
    fields.setdefault("polish", True)
    fields.setdefault("polish_provider", "auto")
    return SimpleNamespace(**fields)


# ---------------------------------------------------------------------------
# The cards are derived from the polish family SSOT
# ---------------------------------------------------------------------------


def test_the_tier_is_not_empty() -> None:
    """Every comparison below is only meaningful against a non-empty set --
    the same discipline as ``test_outcome_parity.py``: a lookup that silently
    stops matching must fail loudly, not go trivially green."""
    assert _DICTATION_SPECS
    assert _FAMILY_IDS


def test_every_card_maps_back_to_a_declared_polish_family() -> None:
    for spec in _DICTATION_SPECS:
        family_id = dictation_family_id(spec.id)
        assert family_id in _FAMILY_IDS, (
            f"{spec.id} is a dictation card with no POLISH_FAMILIES row -- the "
            "card list must be derived from that tuple, never typed twice"
        )
        assert dictation_spec_id(family_id) == spec.id
        assert DICTATION_SPEC_ID_BY_FAMILY[family_id] == spec.id
        assert DICTATION_FAMILY_BY_SPEC_ID[spec.id] == family_id


def test_card_credentials_come_from_the_family_row() -> None:
    """The card renders the family's PRIMARY slot and nothing invented."""
    families = {family.id: family for family in POLISH_FAMILIES}
    for spec in _DICTATION_SPECS:
        family = families[dictation_family_id(spec.id)]
        assert spec.secret_keys == family.secret_candidates[:1]
        # Capability, never a provider name (AP-21): a keyless family becomes a
        # local card, which bills as "local" and can never report "no key set".
        assert spec.auth_mode == ("api_key" if family.needs_key else "none")
        if not family.needs_key:
            assert provider_billing(spec) == "local"


def test_a_family_we_can_store_a_key_for_has_a_card() -> None:
    """A family whose slot the wizard already declares MUST have a card.

    This is the drift gate for the one family deliberately left out today:
    Cerebras has no ``cerebras_api_key`` entry in ``wizard.SECRETS``, and
    ``ALLOWED_SECRET_KEYS`` is derived from that declaration -- so a Cerebras
    card would render a key field whose Save answers 404. The moment the wizard
    slot lands, this test goes red and the fix is one row of card text in
    ``provider_spec._DICTATION_CARD_TEXT``.
    """
    carded = set(DICTATION_SPEC_ID_BY_FAMILY)
    for family in POLISH_FAMILIES:
        storable = (not family.needs_key) or all(
            slot in _WIZARD_SLOTS for slot in family.secret_candidates[:1]
        )
        if storable:
            assert family.id in carded, (
                f"polish family {family.id!r} can accept a key "
                f"({family.secret_candidates[:1] or 'keyless'}) but has no "
                "dictation card -- add its entry to _DICTATION_CARD_TEXT"
            )


def test_every_card_secret_slot_is_storable() -> None:
    """The inverse: no card may offer a key field the API would refuse."""
    for spec in _DICTATION_SPECS:
        for slot in spec.secret_keys:
            assert slot in _WIZARD_SLOTS, (
                f"{spec.id} offers slot {slot!r}, which POST /api/secrets/"
                f"{slot} rejects -- declare it in wizard.SECRETS first"
            )


def test_exactly_one_recommendation_and_it_is_the_key_users_already_have() -> None:
    recommended = [spec.id for spec in _DICTATION_SPECS if spec.recommended]
    assert recommended == [dictation_spec_id("groq")]


def test_optional_is_set_on_this_tier_and_only_this_tier() -> None:
    """``optional`` suppresses the setup nag, so it must not leak into a tier
    the install genuinely depends on."""
    for spec in PROVIDERS:
        assert spec.optional == (spec.tier == "dictation"), spec.id


def test_cards_carry_plain_english_help_and_a_usable_id() -> None:
    for spec in _DICTATION_SPECS:
        assert spec.credential_help
        assert spec.id not in {
            other.id for other in PROVIDERS if other is not spec
        }, f"{spec.id} collides with another provider card"


# ---------------------------------------------------------------------------
# The headline behaviour: an optional tier never nags
# ---------------------------------------------------------------------------


async def test_no_key_anywhere_draws_no_dot(monkeypatch) -> None:
    """The AP-23 case: an install with no polish credential at all. Dictation
    still works, so the tab must stay silent."""
    monkeypatch.setattr(pr, "_is_credential_present", lambda spec, bp=None: False)

    health = await pr._dictation_section_health(None, None, enabled=True)

    assert health.status == _section_health.OK
    assert health.reason == "not_configured_optional"


async def test_a_carded_provider_without_its_key_draws_no_dot(monkeypatch) -> None:
    monkeypatch.setattr(pr, "_is_credential_present", lambda spec, bp=None: False)
    spec = pr.get_spec(dictation_spec_id("groq"))

    health = await pr._dictation_section_health(None, spec, enabled=True)

    assert health.status == _section_health.OK
    assert health.reason == "not_configured_optional"
    assert health.subject_id == spec.id


async def test_switching_the_pass_off_draws_no_dot() -> None:
    health = await pr._dictation_section_health(None, None, enabled=False)

    assert health.status == _section_health.OK
    assert health.reason == "disabled"


async def test_a_present_key_reports_ready_without_a_live_call(monkeypatch) -> None:
    """``probe=False``: the credential here is always a key another tier already
    tests, so page-open must not fire a second network round-trip."""
    monkeypatch.setattr(pr, "_is_credential_present", lambda spec, bp=None: True)

    async def _explode(*_args, **_kwargs):
        raise AssertionError("the dictation tier must not run a live provider test")

    monkeypatch.setattr(pr._provider_test, "run_provider_test", _explode)
    spec = pr.get_spec(dictation_spec_id("groq"))

    health = await pr._dictation_section_health(None, spec, enabled=True)

    assert health.status == _section_health.OK
    assert health.reason == "configured"


async def test_a_required_tier_still_nags(monkeypatch) -> None:
    """The suppression must not leak: speech-to-text without a key is a real
    problem and keeps its amber dot."""
    monkeypatch.setattr(pr, "_is_credential_present", lambda spec, bp=None: False)

    no_active = await pr._tier_section_health(None, None)
    unconfigured = await pr._tier_section_health(None, pr.get_spec("groq-api"))

    assert no_active.status == _section_health.NEEDS_SETUP
    assert unconfigured.status == _section_health.NEEDS_SETUP
    assert unconfigured.reason == "not_configured"


async def test_an_optional_tier_still_reports_a_failing_key(monkeypatch) -> None:
    """Nag suppression covers "not set up", never "set up and broken"."""
    monkeypatch.setattr(pr, "_is_credential_present", lambda spec, bp=None: True)

    async def _bad_key(spec, cfg, **_kwargs):
        return SimpleNamespace(status="bad_key", detail="rejected")

    # A wording card is judged by the dictation layer's own probe, never by the
    # STT branch of the shared provider test — the tier has no recognizer behind
    # it, and probing one reported the user's speech engine under this card's
    # name. Both seams are stubbed so the test pins the RULE (an optional tier
    # still goes red on a failing key) rather than which module answered.
    from jarvis.dictation import polish_probe

    monkeypatch.setattr(pr._provider_test, "run_provider_test", _bad_key)
    monkeypatch.setattr(polish_probe, "probe_polish_family", _bad_key)
    spec = pr.get_spec(dictation_spec_id("groq"))

    health = await pr._tier_section_health(None, spec, probe=True)

    assert health.status != _section_health.OK
    assert health.reason == "bad_key"


# ---------------------------------------------------------------------------
# Wiring: the section exists, resolves a family, and invalidates its cache
# ---------------------------------------------------------------------------


def test_the_rollup_carries_a_dictation_section() -> None:
    assert "dictation" in pr._SECTION_HEALTH_KEYS


def test_active_polish_translates_a_family_id_into_a_card_id(monkeypatch) -> None:
    """``[dictation].polish_provider`` stores "groq"; every subject id, card and
    health section speaks "groq-polish". The translation lives in one place."""
    from jarvis.dictation import polish_client

    monkeypatch.setattr(
        polish_client, "family_has_key", lambda family: family.id == "openai"
    )
    request = _request(dictation=_dictation())

    assert pr._active_polish(request) == dictation_spec_id("openai")


def test_active_polish_is_none_when_the_pass_is_off() -> None:
    assert pr._active_polish(_request(dictation=_dictation(polish=False))) is None


def test_active_polish_is_none_without_any_key(monkeypatch) -> None:
    from jarvis.dictation import polish_client

    monkeypatch.setattr(polish_client, "family_has_key", lambda family: False)

    assert pr._active_polish(_request(dictation=_dictation())) is None


def test_active_polish_survives_a_config_without_a_dictation_block() -> None:
    """A health panel must never 500, not even against a truncated config."""
    assert pr._active_polish(_request()) is None


def test_toggling_the_pass_invalidates_the_cached_health(monkeypatch) -> None:
    """Without this the switch appears to do nothing for _SECTION_HEALTH_TTL_S."""
    from jarvis.dictation import polish_client

    monkeypatch.setattr(polish_client, "family_has_key", lambda family: True)

    def _fingerprint(cfg):
        request = _request(dictation=cfg)
        subjects = pr._section_health_subjects(request, request.app.state.config)
        return pr._section_health_fingerprint(
            request, request.app.state.config, subjects
        )

    on = _fingerprint(_dictation(polish=True))
    off = _fingerprint(_dictation(polish=False))
    pinned = _fingerprint(_dictation(polish=True, polish_provider="openai"))

    assert on != off
    assert on != pinned


def test_the_card_payload_exposes_the_optional_flag(monkeypatch) -> None:
    monkeypatch.setattr(pr, "_is_credential_present", lambda spec, bp=None: False)
    monkeypatch.setattr(pr.cfg_mod, "get_secret", lambda *a, **kw: None)
    spec = pr.get_spec(dictation_spec_id("groq"))

    payload = pr._spec_to_payload(
        spec,
        active_brain=None,
        active_tts=None,
        active_stt=None,
        active_dictation=spec.id,
    )

    assert payload["tier"] == "dictation"
    assert payload["optional"] is True
    assert payload["active"] is True
    # The value a client must PUT into [dictation].polish_provider. The card id
    # differs from it on purpose ("groq" is already the brain card), and
    # resolve_polish_chain ignores an unknown family id -- so a client that
    # pinned `id` here would store a value that silently does nothing.
    assert payload["polish_family"] == "groq"


def test_only_dictation_cards_carry_a_polish_family(monkeypatch) -> None:
    monkeypatch.setattr(pr, "_is_credential_present", lambda spec, bp=None: False)
    monkeypatch.setattr(pr.cfg_mod, "get_secret", lambda *a, **kw: None)

    for spec in PROVIDERS:
        payload = pr._spec_to_payload(
            spec, active_brain=None, active_tts=None, active_stt=None
        )
        expected = dictation_family_id(spec.id) or None
        assert payload["polish_family"] == expected, spec.id
        if spec.tier != "dictation":
            assert payload["polish_family"] is None, spec.id


def test_a_dictation_card_is_never_active_because_of_the_stt_selection(
    monkeypatch,
) -> None:
    """Regression guard for the fall-through: before the explicit branch, any
    non-brain/tts/realtime card was compared against the ACTIVE STT provider."""
    monkeypatch.setattr(pr, "_is_credential_present", lambda spec, bp=None: False)
    monkeypatch.setattr(pr.cfg_mod, "get_secret", lambda *a, **kw: None)
    spec = pr.get_spec(dictation_spec_id("groq"))

    payload = pr._spec_to_payload(
        spec,
        active_brain=None,
        active_tts=None,
        active_stt=spec.id,
        active_dictation=None,
    )

    assert payload["active"] is False


def test_credential_presence_accepts_any_slot_the_family_reads(monkeypatch) -> None:
    """A Google user holding only ``google_api_key`` has a working polish pass;
    the single-slot check would call that card "no key set" and send them
    hunting for a key they do not need."""
    import jarvis.core.config as cfg_mod

    monkeypatch.setattr(
        cfg_mod, "get_secret", lambda slot, *a, **kw: "k" if slot == "google_api_key" else None
    )
    spec = pr.get_spec(dictation_spec_id("gemini"))

    assert "google_api_key" not in spec.secret_keys
    assert pr._is_credential_present(spec) is True


@pytest.mark.parametrize("spec_id", [s.id for s in _DICTATION_SPECS])
def test_a_dictation_card_can_never_be_switched_into_another_tier(spec_id) -> None:
    """``apply_provider_switch`` validates ``spec.tier`` against the requested
    tier, so a polish card can never become the brain/TTS/STT provider even
    though several of them share a credential slot with one."""
    from jarvis.brain.app_control import SWITCHABLE_TIERS
    from jarvis.ui.web.provider_spec import get_spec

    assert "dictation" not in SWITCHABLE_TIERS
    assert get_spec(spec_id).tier == "dictation"
