"""Parity guard: every TTS provider in provider_spec.PROVIDERS must have an
entry in config_writer._TTS_DEFAULTS.

Without this guard a new TTS provider in the UI spec silently misses its
defaults entry, causing set_tts_provider() to leave a stale model/voice from
the previous provider in jarvis.toml after a switch.

This test is intentionally a compile-time / import-time check (no fixtures
needed) — it must FAIL when a provider is added to provider_spec without a
matching _TTS_DEFAULTS entry, and PASS once the entry is added.
"""
from __future__ import annotations

from jarvis.core import config_writer
from jarvis.ui.web.provider_spec import PROVIDERS


def test_all_tts_providers_have_defaults_entry() -> None:
    """Every provider with tier=='tts' must have a key in _TTS_DEFAULTS.

    Failure message names the missing providers so the developer knows exactly
    what entries to add.
    """
    tts_provider_ids = {spec.id for spec in PROVIDERS if spec.tier == "tts"}
    defaults_keys = set(config_writer._TTS_DEFAULTS.keys())

    missing = tts_provider_ids - defaults_keys
    assert not missing, (
        f"The following TTS providers are declared in provider_spec.PROVIDERS "
        f"but have no entry in config_writer._TTS_DEFAULTS: {sorted(missing)}. "
        f"Add a defaults dict for each missing provider so set_tts_provider() "
        f"can reconcile the [tts] block when the user switches to that provider."
    )

    # NB: we intentionally do NOT assert the reverse direction (every
    # _TTS_DEFAULTS key has a UI spec). A provider can legitimately have a
    # working plugin + defaults but no selectable UI card — e.g. ``elevenlabs``
    # has jarvis/plugins/tts/elevenlabs_tts.py and a factory branch but is not
    # in provider_spec.PROVIDERS. Such an entry is reachable via config/ENV and
    # must keep its defaults; flagging it as "orphaned" would be wrong.
