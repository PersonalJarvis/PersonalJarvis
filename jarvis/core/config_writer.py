"""Atomic edits to the jarvis.toml configuration at runtime.

Used by the provider-switch endpoint so that a user's change of Brain provider
updates not only the BrainManager's in-memory state but also the persistent default
selection in jarvis.toml. tomlkit preserves comments and formatting — the user has
many explanatory comments in jarvis.toml that must not be lost.

Writes atomically via a tempfile + os.replace (atomic on NTFS).

BOM handling: On Windows it is common for editors (Notepad, VS Code with
``files.encoding=utf8bom``) to prepend a UTF-8 BOM. ``tomlkit.parse``
does not tolerate this and raises EmptyKeyError. We strip the BOM on read and
write it back on save — so the file stays byte-identical for tools that expect
the BOM, while the patch is still applied in-place.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
import threading
from pathlib import Path

import tomlkit
from tomlkit import TOMLDocument

from .config import DEFAULT_CONFIG_FILE, PROJECT_ROOT

log = logging.getLogger(__name__)

_WRITE_LOCK = threading.Lock()
_BOM = "﻿"

# Canonical User-scope ENV var that overrides ``[brain] primary`` at boot
# (see jarvis/core/config.py: ``JARVIS__*`` overrides are applied LAST and win
# over the TOML). The UI provider switch must keep this in sync, otherwise a
# stale env value silently reverts the switch on the next start.
_BRAIN_PRIMARY_ENV = "JARVIS__BRAIN__PRIMARY"

# Canonical User-scope ENV var that overrides ``[brain.sub_jarvis] provider``
# at boot. ``_apply_env_overrides`` splits on ``__`` and lower-cases, so
# ``JARVIS__BRAIN__SUB_JARVIS__PROVIDER`` -> ``brain.sub_jarvis.provider``
# (``sub_jarvis`` survives because it carries only a single underscore).
_SUB_JARVIS_PROVIDER_ENV = "JARVIS__BRAIN__SUB_JARVIS__PROVIDER"

# Canonical User-scope ENV vars that override ``[tts] provider`` / ``[stt]
# provider`` at boot. Both section + key are single words, so
# ``_apply_env_overrides`` maps them cleanly to ``tts.provider`` / ``stt.provider``.
_TTS_PROVIDER_ENV = "JARVIS__TTS__PROVIDER"
_STT_PROVIDER_ENV = "JARVIS__STT__PROVIDER"


def set_brain_primary(name: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set ``[brain] primary`` to the given provider name across all layers.

    This is the AUTHORITATIVE writer for the user's Brain-provider choice.
    There are three persistence layers and a UI switch that only wrote one of
    them did not survive a restart:

      1. ``jarvis.toml`` ``[brain] primary``            (universal, always runs)
      2. ``scripts/config-soll.json`` ``brain.primary``  (drift-guard soll value)
      3. ``JARVIS__BRAIN__PRIMARY`` User-scope ENV var   (boot override)

    Raises ``FileNotFoundError`` if the TOML config file does not exist (a
    broken setup we do not silently mask). Layers 2 and 3 are best-effort
    cloud-first enhancements: they degrade to a graceful no-op on a headless
    Linux VPS (no config-soll.json, no Windows registry) and never raise out
    of this function nor break the TOML write.
    """
    # Layer 1 — universal, runs on every platform. May raise FileNotFoundError.
    _patch_table(path, "brain", "primary", name)
    # Layers 2 + 3 — best-effort, never raise.
    _sync_brain_primary_drift_soll(name)


def set_sub_jarvis_provider(name: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set ``[brain.sub_jarvis] provider`` (the Heavy-Task SUBAGENT provider)
    across all persistence layers.

    This is the AUTHORITATIVE writer for the user's subagent-provider choice
    and the write-side counterpart to the read-side resolution in
    ``jarvis.missions.openclaw.provider_map.canonical_subagent_provider`` /
    ``jarvis.missions.init._worker_factory``. The subagent provider is pinned
    in ``config-soll.json`` (``brain.sub_jarvis.provider``), so a switch that
    wrote only the TOML would be reverted by the drift-guard within minutes —
    the same failure mode that hit ``brain.primary`` before it went 3-layer.

      1. ``jarvis.toml`` ``[brain.sub_jarvis] provider``               (TOML)
      2. ``scripts/config-soll.json`` ``brain.sub_jarvis.provider``   (drift-soll)
      3. ``JARVIS__BRAIN__SUB_JARVIS__PROVIDER`` User-scope ENV var    (boot override)

    Raises ``FileNotFoundError`` if the TOML config file does not exist. Layers
    2 + 3 are best-effort cloud-first enhancements: graceful no-op on a headless
    Linux VPS and never raise out of this function nor break the TOML write.

    NB: this writes only ``provider``. The fallback chain
    (``fallback_provider`` etc.) is left untouched, mirroring how the brain
    switch leaves ``[brain]`` siblings alone.
    """
    # Layer 1 — universal, runs on every platform. May raise FileNotFoundError.
    _patch_sub_jarvis_provider_toml(path, name)
    # Layers 2 + 3 — best-effort, never raise.
    _sync_sub_jarvis_provider_drift_soll(name)


def set_tts_provider(name: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set ``[tts] provider`` and reconcile the provider-dependent defaults,
    across all THREE persistence layers.

    Beyond ``provider`` this also writes the provider-specific voice / language /
    model so the file never holds an invalid mix (e.g. switching Gemini ->
    Grok-Voice must not leave ``voice_de = "Charon"`` behind, a voice Grok cannot
    use). An existing value that is already valid for the new provider is kept —
    user overrides win.

    Three-layer persist (like ``set_brain_primary``): ``tts.provider`` (and the
    voice keys) are pinned in ``config-soll.json``, so a TOML-only write would be
    reverted by the drift-guard within 5 minutes — the same bug class that hit
    ``brain.primary``. We therefore sync config-soll.json + ENV too. Crucially,
    config-soll receives EXACTLY the keys the TOML write touched (provider + the
    voice/language/model it set or preserved) so the guard sees zero drift across
    the whole block.

    Layers 2 + 3 are best-effort (cloud-first): a graceful no-op on a headless
    Linux VPS, they never raise out of this function nor break the TOML write.
    """
    # Layer 1 — universal, runs on every platform. May raise FileNotFoundError.
    defaults = _TTS_DEFAULTS.get(name.lower(), {})
    applied = _patch_tts_block(path, name, defaults)
    # Layers 2 + 3 — best-effort, never raise.
    _sync_tts_provider_drift_soll(applied)


def set_stt_provider(name: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set ``[stt] provider`` across all THREE persistence layers.

    Takes effect on the next SpeechPipeline bootstrap (a voice restart): the STT
    provider is instantiated once at pipeline start.

    ``stt.provider`` is pinned in ``config-soll.json``, so — like Brain/TTS — the
    switch needs the 3-layer persist (TOML + config-soll + ENV), otherwise the
    drift-guard reverts it within 5 minutes. Layers 2 + 3 are best-effort
    (cloud-first) and never break the TOML write.
    """
    # Layer 1 — universal, runs on every platform. May raise FileNotFoundError.
    _patch_table(path, "stt", "provider", name)
    # Layers 2 + 3 — best-effort, never raise.
    _sync_stt_provider_drift_soll(name)


def set_codex_binary_path(binary_path: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set ``[codex] binary_path`` to work around Windows PATH issues."""
    _patch_table(path, "codex", "binary_path", binary_path)


def set_assistant_name(name: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the assistant's display name to ``[persona] name`` in jarvis.toml.

    Toml-only by design: ``persona.name`` is NOT tracked in ``config-soll.json``,
    so the drift-guard never reverts it (a plain atomic write suffices). Empty
    string means "derive the name from the wake phrase" (see
    ``jarvis.brain.assistant_name.resolve_assistant_name``). Takes effect on the
    next BrainManager build (a Jarvis restart): the system prompt is assembled
    once per manager.
    """
    _patch_table(path, "persona", "name", name)


def set_ptt_hotkey(hotkey: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the push-to-talk hotkey to ``[trigger] hotkey`` in jarvis.toml.

    Toml-only by design: ``trigger.hotkey`` is NOT tracked in
    ``config-soll.json`` (only ``trigger.single_turn_mode`` is), so the
    drift-guard never reverts it — a plain atomic write suffices and the
    3-layer rule (which exists only to stop the guard from rolling a UI switch
    back, BUG-010) does not apply. Takes effect on the next SpeechPipeline
    bootstrap (a Jarvis restart): the hotkey bindings are armed once at pipeline
    start via ``TriggerConfig.resolve_hotkeys``.
    """
    _patch_table(path, "trigger", "hotkey", hotkey)


def set_reply_language(name: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the user-facing reply-language pin in ``[brain] reply_language``.

    ``name`` is one of ``auto`` | ``de`` | ``en`` | ``es`` (validated by the
    caller). Takes effect as a boot default on the next ``load_config`` call — the
    live switch happens via ``BrainManager.set_reply_language``.
    """
    _patch_table(path, "brain", "reply_language", name)


def set_wake_word(
    phrase: str,
    *,
    engine: str | None = None,
    custom_model_path: str | None = None,
    sensitivity: float | None = None,
    fuzzy_match_ratio: float | None = None,
    path: Path = DEFAULT_CONFIG_FILE,
) -> None:
    """Persist the user's wake word to ``[trigger.wake_word]`` in jarvis.toml.

    Toml-only by design — and that is a deliberate decision, NOT an oversight of
    the "user-switchable settings are written to all three layers" rule:

      * The drift-guard only reverts keys it tracks in ``config-soll.json``, and
        ``trigger.wake_word`` is intentionally NOT tracked there — so a plain
        atomic toml write is never reverted. The three-layer rule exists to stop
        the guard from rolling a UI switch back (BUG-010); with no soll entry
        there is nothing to roll back.
      * Adding a nested ``wake_word`` object to ``config-soll.json`` would make
        the guard's scalar-only loops synthesise a garbage
        ``JARVIS__TRIGGER__WAKE_WORD`` user env var from a stringified dict
        (BUG-018 class). And a stale ``JARVIS__*`` override would silently win
        over a hand-edit of jarvis.toml — directly contradicting the
        "edit `phrase` here" guidance in the file. So neither the soll nor the
        ENV layer is written for the wake word.

    Takes effect on the next voice-pipeline bootstrap (a Jarvis restart): the
    OWW model + phrase matcher are resolved once at SpeechPipeline construction.
    """
    values: dict[str, object] = {"phrase": phrase}
    if engine is not None:
        values["engine"] = engine
    if custom_model_path is not None:
        values["custom_model_path"] = custom_model_path
    if sensitivity is not None:
        values["sensitivity"] = float(sensitivity)
    if fuzzy_match_ratio is not None:
        values["fuzzy_match_ratio"] = float(fuzzy_match_ratio)
    _patch_wake_word_toml(path, values)


def set_autostart(enabled: bool, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the login-autostart toggle to ``[autostart] enabled`` in jarvis.toml.

    Toml-only by design: ``autostart.enabled`` is NOT tracked in
    ``config-soll.json`` (verified absent), so the drift-guard never reverts it —
    a plain atomic write suffices and the 3-layer rule (which exists only to stop
    the guard from rolling a UI switch back, BUG-010) does not apply.

    This persists the *intent*. The actual OS entry (install/remove) is applied
    by the caller via ``jarvis.autostart`` (live by the Settings route, or on the
    next boot by ``reconcile_autostart``).
    """
    _patch_table(path, "autostart", "enabled", bool(enabled))


def set_brain_provider_defaults(
    name: str,
    *,
    model: str | None = None,
    deep_model: str | None = None,
    auth_mode: str = "api_key",
    path: Path = DEFAULT_CONFIG_FILE,
) -> None:
    """Ensure that the ``[brain.providers.<name>]`` block exists.

    Idempotent: an already-existing block is NOT overwritten —
    user overrides from jarvis.toml are preserved. If the block is absent it
    is created with the supplied defaults (typically the tier defaults from
    ``BrainManager.TIER_DEFAULTS_BY_PROVIDER``).

    Background: providers added after the setup wizard via the UI (e.g. grok)
    often lack a ``[brain.providers.<name>]`` block. During a switch-persist we
    ensure here that after an app restart the tier-default fallback logic in
    BrainManager is not needed again — the block is then cleanly persisted.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config-Datei fehlt: {path}")

    with _WRITE_LOCK:
        raw = path.read_text(encoding="utf-8")
        had_bom = raw.startswith(_BOM)
        if had_bom:
            raw = raw[len(_BOM) :]
        doc: TOMLDocument = tomlkit.parse(raw)

        brain = doc.get("brain")
        if brain is None:
            brain = tomlkit.table()
            doc["brain"] = brain
        providers = brain.get("providers")
        if providers is None:
            providers = tomlkit.table()
            providers.is_super_table = True  # type: ignore[attr-defined]
            brain["providers"] = providers

        if name in providers:
            # Existing block — do not overwrite (user override wins).
            return

        block = tomlkit.table()
        if model:
            block["model"] = model
        if deep_model:
            block["deep_model"] = deep_model
        block["auth_mode"] = auth_mode
        providers[name] = block

        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)


# Provider defaults for the TTS switch. Written to the TOML, but only when
# the respective value does not already match the new provider.
#
# Every provider declared with tier="tts" in jarvis/ui/web/provider_spec.py
# MUST have an entry here, enforced by tests/unit/test_tts_defaults_parity.py.
# Without an entry, set_tts_provider() would pass an empty defaults dict and
# leave a stale model/voice from the previous provider in jarvis.toml.
_TTS_DEFAULTS: dict[str, dict[str, str]] = {
    "gemini-flash-tts": {
        # model from jarvis/plugins/tts/gemini_flash_tts.py (factory line:
        #   tts_cfg.model or "gemini-3.1-flash-tts-preview")
        "model": "gemini-3.1-flash-tts-preview",
        "voice_de": "Charon",
        "voice_en": "Charon",
        "language_code": "de-DE",
    },
    "grok-voice": {
        # model is ignored by the Grok plugin (no model param in GrokVoiceTTS).
        # voice from jarvis/plugins/tts/grok_voice_tts.py: GROK_VOICE_LEO = "leo"
        "model": "",  # Grok ignores model — leave blank
        "voice_de": "leo",
        "voice_en": "leo",
        "language_code": "auto",
    },
    "elevenlabs": {
        # model + voice from jarvis/plugins/tts/elevenlabs_tts.py:
        #   model = tts_cfg.model or "eleven_flash_v2_5"
        #   default_voice = tts_cfg.voice_de or JARVIS_VOICE_DANIEL
        #   JARVIS_VOICE_DANIEL = "onwK4e9ZLuTAKqWW03F9"
        "model": "eleven_flash_v2_5",
        "voice_de": "onwK4e9ZLuTAKqWW03F9",  # Daniel
        "voice_en": "onwK4e9ZLuTAKqWW03F9",
        "language_code": "de-DE",
    },
    "cartesia": {
        # Cartesia reads voice UUIDs from [tts.cartesia].voice_id* (a subtable),
        # NOT from [tts].voice_de/voice_en (jarvis/plugins/tts/__init__.py lines
        # 103-116: ct = tts_cfg.model_extra["cartesia"]). The scalar [tts].voice_de
        # and voice_en keys are never consumed by the Cartesia factory path; setting
        # them to "Charon" keeps them consistent with config-soll.json's tts block
        # (which holds "Charon" as a carry-over from the Gemini era) so the
        # drift-guard sees zero drift.
        # model="" because [tts].model is not read by CartesiaTTS at all
        # (DEFAULT_MODEL_ID comes from [tts.cartesia].model_id, not [tts].model).
        # The empty-string gate in _patch_tts_block skips writing it, leaving any
        # prior value untouched — harmless since Cartesia ignores [tts].model.
        "model": "",  # Cartesia ignores [tts].model — leave blank
        "voice_de": "Charon",  # placeholder; Cartesia reads [tts.cartesia].voice_id_de
        "voice_en": "Charon",  # placeholder; Cartesia reads [tts.cartesia].voice_id_en
        "language_code": "auto",
    },
    "google-neural2": {
        # google-neural2 plugin does not yet exist (no jarvis/plugins/tts/
        # google_neural2_tts.py). The factory falls back to gemini-flash-tts.
        # These are minimal safe defaults: empty model/voice strings are
        # skipped by _patch_tts_block's falsy gate, so no values are written
        # until a real plugin provides meaningful defaults. language_code="auto"
        # avoids pinning a stale "de-DE" from a prior provider.
        "model": "",
        "voice_de": "",
        "voice_en": "",
        "language_code": "auto",
    },
    "openai-tts": {
        # openai-tts plugin does not yet exist (no jarvis/plugins/tts/
        # openai_tts.py). The factory falls back to gemini-flash-tts.
        # Same strategy as google-neural2: empty strings are skipped by the
        # falsy gate in _patch_tts_block. When a real plugin is added, update
        # these to the correct model (e.g. "tts-1-hd") and voice (e.g.
        # "onyx" for a masculine default — see OpenAI voice options).
        "model": "",
        "voice_de": "",
        "voice_en": "",
        "language_code": "auto",
    },
}

# Per-provider voice allowlist — when the existing voice does not match the
# new provider, we overwrite with the provider default. Kept in sync with
# `jarvis/plugins/tts/__init__.py`.
_VOICES_FOR_PROVIDER: dict[str, frozenset[str]] = {
    "gemini-flash-tts": frozenset(
        {
            "Charon",
            "Orus",
            "Iapetus",
            "Rasalgethi",
            "Algenib",
            "Algieba",
            "Kore",
            "Fenrir",
            "Aoede",
        }
    ),
    "grok-voice": frozenset({"leo", "rex", "sal", "ara", "eve"}),
    # ElevenLabs nutzt Voice-IDs (kryptische Hashes) — keine Whitelist.
}


def _patch_tts_block(path: Path, provider: str, defaults: dict[str, str]) -> dict[str, str]:
    """Write ``[tts] provider`` and ensure that dependent fields
    (voice_de, voice_en, language_code, model) are compatible with the provider.

    An existing voice value is only overwritten when it does *not* belong to the
    new provider's allowlist. This preserves meaningful user edits
    (e.g. ``voice_de = "Orus"`` for Gemini) while correcting nonsensical leftovers
    such as ``voice_de = "Charon"`` for Grok.

    Returns the dict of keys it ACTUALLY wrote to ``[tts]`` (always
    ``provider``; plus whichever of voice/language/model it set). The
    config-soll drift-sync mirrors exactly these keys so the guard sees zero
    drift across the whole block.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config-Datei fehlt: {path}")

    whitelist = _VOICES_FOR_PROVIDER.get(provider.lower())
    applied: dict[str, str] = {"provider": provider}

    with _WRITE_LOCK:
        raw = path.read_text(encoding="utf-8")
        had_bom = raw.startswith(_BOM)
        if had_bom:
            raw = raw[len(_BOM) :]
        doc: TOMLDocument = tomlkit.parse(raw)
        section = doc.get("tts")
        if section is None:
            section = tomlkit.table()
            doc["tts"] = section
        section["provider"] = provider
        for key, default_value in defaults.items():
            if key in ("voice_de", "voice_en") and whitelist is not None:
                current = section.get(key)
                if current is None or str(current) not in whitelist:
                    if default_value:
                        section[key] = default_value
                        applied[key] = default_value
                elif current is not None:
                    # Voice is already valid for this provider — keep the user's
                    # value, but still record it so the config-soll drift-sync
                    # agrees with the TOML (else the guard reverts it).
                    applied[key] = str(current)
                continue
            if key == "language_code":
                # Always set language_code to the provider default so
                # "auto" vs "de-DE" does not carry over between providers.
                if default_value:
                    section[key] = default_value
                    applied[key] = default_value
                continue
            if key == "model":
                # Only write model when non-empty — Grok has no model concept.
                if default_value:
                    section[key] = default_value
                    applied[key] = default_value
                continue
            # Generic fall-through (e.g. voice_de/voice_en for a provider with no
            # whitelist entry). Skip empty-string placeholders so a plugin-less
            # provider (google-neural2 / openai-tts: empty voice defaults) never
            # blanks the carried-over voice in the TOML.
            if default_value:
                section[key] = default_value
                applied[key] = default_value

        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)

    return applied


def set_brain_provider_model(
    provider: str,
    *,
    model: str | None = None,
    deep_model: str | None = None,
    path: Path = DEFAULT_CONFIG_FILE,
) -> None:
    """Patch ``[brain.providers.<provider>]`` ``model`` / ``deep_model`` in
    the TOML file.

    Used by the frontier auto-switch (Phase F.3, 2026-04-29) so that a detected
    frontier model change is persisted in jarvis.toml — otherwise the switch is
    lost on the next ``cfg.load_config()`` call in ``_phase2_full_brain``.

    Idempotent: if the block is absent it is created; ``None`` values change
    nothing.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config-Datei fehlt: {path}")
    if model is None and deep_model is None:
        return

    with _WRITE_LOCK:
        raw = path.read_text(encoding="utf-8")
        had_bom = raw.startswith(_BOM)
        if had_bom:
            raw = raw[len(_BOM) :]
        doc: TOMLDocument = tomlkit.parse(raw)

        brain = doc.get("brain")
        if brain is None:
            brain = tomlkit.table()
            doc["brain"] = brain
        providers = brain.get("providers")
        if providers is None:
            providers = tomlkit.table()
            providers.is_super_table = True  # type: ignore[attr-defined]
            brain["providers"] = providers
        block = providers.get(provider)
        if block is None:
            block = tomlkit.table()
            providers[provider] = block

        if model is not None:
            block["model"] = model
        if deep_model is not None:
            block["deep_model"] = deep_model

        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)


def set_telephony_config(values: dict[str, object], *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Patch ``[integrations.twilio]`` with the given non-secret fields.

    Only the keys present in ``values`` are written (partial update); the
    Twilio Auth Token is NEVER written here — it lives in the Credential
    Manager (AP-12). Used by ``POST /api/telephony/config`` and
    ``/api/telephony/credentials`` (the latter only ever passes
    ``account_sid``).

    Idempotent and comment-preserving via tomlkit, BOM-aware like the other
    writers in this module.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config-Datei fehlt: {path}")
    if not values:
        return

    with _WRITE_LOCK:
        raw = path.read_text(encoding="utf-8")
        had_bom = raw.startswith(_BOM)
        if had_bom:
            raw = raw[len(_BOM) :]
        doc: TOMLDocument = tomlkit.parse(raw)

        integrations = doc.get("integrations")
        if integrations is None:
            integrations = tomlkit.table()
            doc["integrations"] = integrations
        twilio = integrations.get("twilio")
        if twilio is None:
            twilio = tomlkit.table()
            integrations["twilio"] = twilio

        for key, value in values.items():
            twilio[key] = value

        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)


def _patch_table(path: Path, table: str, key: str, value: str | bool) -> None:
    """Set ``[table] key = value`` in the TOML file.

    Creates the table if it is absent. Preserves comments and formatting via
    tomlkit, including the optional BOM (see module docstring). ``value`` may be
    a ``str`` or a ``bool`` — tomlkit serialises ``bool`` as ``true``/``false``
    (used by the autostart toggle ``[autostart] enabled``).
    """
    if not path.exists():
        raise FileNotFoundError(f"Config-Datei fehlt: {path}")

    with _WRITE_LOCK:
        raw = path.read_text(encoding="utf-8")
        had_bom = raw.startswith(_BOM)
        if had_bom:
            raw = raw[len(_BOM) :]
        doc: TOMLDocument = tomlkit.parse(raw)
        section = doc.get(table)
        if section is None:
            section = tomlkit.table()
            doc[table] = section
        section[key] = value
        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)


def _patch_sub_jarvis_provider_toml(path: Path, name: str) -> None:
    """Set ``[brain.sub_jarvis] provider = name`` in the TOML.

    Unlike :func:`_patch_table`, this walks the NESTED ``brain`` -> ``sub_jarvis``
    path instead of treating ``"brain.sub_jarvis"`` as a flat top-level key
    (``doc.get("brain.sub_jarvis")`` would create a literal dotted key, not the
    ``[brain.sub_jarvis]`` section). Creates either level if missing. Preserves
    comments, sibling keys, and the optional BOM.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config-Datei fehlt: {path}")

    with _WRITE_LOCK:
        raw = path.read_text(encoding="utf-8")
        had_bom = raw.startswith(_BOM)
        if had_bom:
            raw = raw[len(_BOM) :]
        doc: TOMLDocument = tomlkit.parse(raw)

        brain = doc.get("brain")
        if brain is None:
            brain = tomlkit.table()
            doc["brain"] = brain
        sub = brain.get("sub_jarvis")
        if sub is None:
            sub = tomlkit.table()
            brain["sub_jarvis"] = sub
        sub["provider"] = name

        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)


def _patch_wake_word_toml(path: Path, values: dict[str, object]) -> None:
    """Set keys under the nested ``[trigger.wake_word]`` table.

    Walks ``trigger`` -> ``wake_word`` (creating either level if missing), sets
    each key in ``values``, and preserves comments, sibling keys, and the
    optional BOM (same contract as :func:`_patch_sub_jarvis_provider_toml`).
    """
    if not path.exists():
        raise FileNotFoundError(f"Config-Datei fehlt: {path}")

    with _WRITE_LOCK:
        raw = path.read_text(encoding="utf-8")
        had_bom = raw.startswith(_BOM)
        if had_bom:
            raw = raw[len(_BOM) :]
        doc: TOMLDocument = tomlkit.parse(raw)

        trigger = doc.get("trigger")
        if trigger is None:
            trigger = tomlkit.table()
            doc["trigger"] = trigger
        wake_word = trigger.get("wake_word")
        if wake_word is None:
            wake_word = tomlkit.table()
            trigger["wake_word"] = wake_word
        for key, value in values.items():
            wake_word[key] = value

        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)


def _atomic_write(path: Path, content: str) -> None:
    """Atomic tempfile + replace, read-only-aware.

    ``jarvis.toml`` carries a Windows read-only flag as the BUG-010 second
    defense layer (parallel sessions cannot blindly overwrite the provider
    config). The flag must be temporarily cleared for ``os.replace`` to
    succeed; otherwise the call fails with ``[WinError 5] Zugriff
    verweigert``. We restore the flag in ``finally`` so the defense holds
    even if the write itself raises.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")

    was_read_only = False
    if path.exists():
        mode = path.stat().st_mode
        was_read_only = not bool(mode & stat.S_IWRITE)
        if was_read_only:
            os.chmod(path, mode | stat.S_IWRITE)

    try:
        tmp.replace(path)
    finally:
        if was_read_only and path.exists():
            current_mode = path.stat().st_mode
            os.chmod(path, current_mode & ~stat.S_IWRITE)


# ----------------------------------------------------------------------
# Layer 2 + 3 — config-soll.json + ENV sync (best-effort, cloud-first safe)
# ----------------------------------------------------------------------


def _config_soll_path() -> Path:
    """Locate ``scripts/config-soll.json`` relative to the repo root.

    Derived from the same ``PROJECT_ROOT`` resolution that anchors
    ``DEFAULT_CONFIG_FILE`` so the two paths stay consistent. On a headless
    Linux VPS this file usually does not exist — callers must treat a missing
    file as a graceful no-op.
    """
    return PROJECT_ROOT / "scripts" / "config-soll.json"


def _sync_brain_primary_drift_soll(name: str) -> None:
    """Best-effort sync of ``brain.primary`` into the drift-soll + ENV layers.

    NEVER raises and NEVER breaks the (already-completed) TOML write. Two
    independent best-effort steps:

      (a) Update ``scripts/config-soll.json`` ``brain.primary`` so the
          drift-guard daemon (5-min cron) does not revert the switch. Graceful
          no-op when the file is absent (cloud-first / headless VPS).
      (b) Set the User-scope ``JARVIS__BRAIN__PRIMARY`` ENV var (Windows
          registry) so a fresh boot's ``JARVIS__*`` override matches the new
          choice instead of reverting it; also update ``os.environ`` so the
          live process and any child it spawns are immediately consistent.
          The registry write is gated behind ``sys.platform == "win32"``.
    """
    # (a) config-soll.json — graceful no-op if the file does not exist.
    try:
        _update_config_soll_brain_primary(name)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync brain.primary to config-soll.json: %s", exc)

    # (b) ENV var — winreg gated to win32, os.environ updated cross-platform.
    try:
        _set_user_env_var(_BRAIN_PRIMARY_ENV, name)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync %s to the User environment: %s", _BRAIN_PRIMARY_ENV, exc)


def _sync_sub_jarvis_provider_drift_soll(name: str) -> None:
    """Best-effort sync of ``brain.sub_jarvis.provider`` into config-soll + ENV.

    NEVER raises and NEVER breaks the (already-completed) TOML write. Same
    two-step shape as :func:`_sync_brain_primary_drift_soll`.
    """
    try:
        _update_config_soll_sub_jarvis_provider(name)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync sub_jarvis provider to config-soll.json: %s", exc)

    try:
        _set_user_env_var(_SUB_JARVIS_PROVIDER_ENV, name)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning(
            "Could not sync %s to the User environment: %s",
            _SUB_JARVIS_PROVIDER_ENV, exc,
        )


def _sync_tts_provider_drift_soll(applied: dict[str, str]) -> None:
    """Best-effort sync of the TTS block into the drift-soll + ENV layers.

    NEVER raises and NEVER breaks the (already-completed) TOML write. ``applied``
    is the exact set of ``[tts]`` keys the TOML write touched (provider + any
    provider-dependent voice/language/model), so config-soll ends up byte-for-byte
    in agreement and the drift-guard reverts nothing. The ENV layer only pins the
    provider (the single value a stale boot override could revert).
    """
    try:
        _update_config_soll_section("tts", applied)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync tts.* to config-soll.json: %s", exc)

    provider_name = applied["provider"]  # always present — set in _patch_tts_block
    try:
        _set_user_env_var(_TTS_PROVIDER_ENV, provider_name)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync %s to the User environment: %s", _TTS_PROVIDER_ENV, exc)


def _sync_stt_provider_drift_soll(name: str) -> None:
    """Best-effort sync of ``stt.provider`` into the drift-soll + ENV layers.

    NEVER raises and NEVER breaks the (already-completed) TOML write. Same
    two-step shape as :func:`_sync_brain_primary_drift_soll`.
    """
    try:
        _update_config_soll_section("stt", {"provider": name})
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync stt.provider to config-soll.json: %s", exc)

    try:
        _set_user_env_var(_STT_PROVIDER_ENV, name)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync %s to the User environment: %s", _STT_PROVIDER_ENV, exc)


def _update_config_soll_section(top: str, values: dict[str, object]) -> None:
    """Atomically merge ``values`` into ``data[top]`` in config-soll.json.

    Preserves every other key (``_comment``, ``_updated``, other keys in the
    same section, other top-level tables). Atomic tempfile + ``os.replace``,
    UTF-8, ``indent=2``. Graceful no-op when the file is absent (cloud-first)
    or when the section already matches every value (avoid a needless rewrite).

    MUST NOT be called while ``_WRITE_LOCK`` is held — it acquires that lock
    itself and ``_WRITE_LOCK`` is a non-reentrant ``threading.Lock`` (it would
    deadlock). Today's callers acquire it only sequentially, never nested.
    """
    soll_path = _config_soll_path()
    if not soll_path.exists():
        log.debug("config-soll.json absent (%s) — skipping drift-soll sync", soll_path)
        return

    with _WRITE_LOCK:
        raw = soll_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        section = data.get(top)
        if not isinstance(section, dict):
            section = {}
            data[top] = section
        if all(section.get(k) == v for k, v in values.items()):
            return  # already in sync — avoid a needless rewrite
        section.update(values)

        out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        _atomic_write_text(soll_path, out)


def _update_config_soll_brain_primary(name: str) -> None:
    """Atomically set ``data["brain"]["primary"] = name`` in config-soll.json.

    Preserves all other keys (``_comment``, ``_updated``, other ``brain.*``
    keys, other top-level tables). Atomic tempfile + ``os.replace``, UTF-8,
    ``indent=2``. Graceful no-op when the file is absent.
    """
    soll_path = _config_soll_path()
    if not soll_path.exists():
        log.debug("config-soll.json absent (%s) — skipping drift-soll sync", soll_path)
        return

    with _WRITE_LOCK:
        raw = soll_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        brain = data.get("brain")
        if not isinstance(brain, dict):
            brain = {}
            data["brain"] = brain
        if brain.get("primary") == name:
            return  # already in sync — avoid a needless rewrite
        brain["primary"] = name

        out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        _atomic_write_text(soll_path, out)


def _update_config_soll_sub_jarvis_provider(name: str) -> None:
    """Atomically set ``data["brain.sub_jarvis"]["provider"] = name`` in
    config-soll.json.

    Note the FLAT dotted key ``"brain.sub_jarvis"`` — that is how the
    drift-guard soll file stores the sub-table (see scripts/config-soll.json),
    NOT a nested ``data["brain"]["sub_jarvis"]``. Preserves all other keys
    (``_comment``, the fallback chain, other tables). Graceful no-op when the
    file is absent.
    """
    soll_path = _config_soll_path()
    if not soll_path.exists():
        log.debug("config-soll.json absent (%s) — skipping drift-soll sync", soll_path)
        return

    with _WRITE_LOCK:
        raw = soll_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        block = data.get("brain.sub_jarvis")
        if not isinstance(block, dict):
            block = {}
            data["brain.sub_jarvis"] = block
        if block.get("provider") == name:
            return  # already in sync — avoid a needless rewrite
        block["provider"] = name

        out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        _atomic_write_text(soll_path, out)


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomic tempfile + replace for a plain UTF-8 text file (no read-only flag).

    Used for config-soll.json, which — unlike jarvis.toml — does not carry the
    BUG-010 read-only defense flag.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _set_user_env_var(name: str, value: str) -> None:
    """Persist a User-scope ENV var and update the live ``os.environ``.

    The persistent (registry) write is Windows-only and gated behind
    ``sys.platform == "win32"``. ``os.environ`` is always updated so the live
    process and any child it spawns immediately observe the new value — this
    is the cross-platform part that also benefits a Linux VPS.
    """
    # Always update the live process (and inherited children).
    os.environ[name] = value

    if sys.platform != "win32":
        return

    _set_user_env_var_winreg(name, value)


def _set_user_env_var_winreg(name: str, value: str) -> None:
    """Write ``name=value`` to ``HKCU\\Environment`` (REG_SZ) and broadcast.

    Windows-only. Imported lazily so the module imports cleanly on Linux.
    Best-effort broadcast of ``WM_SETTINGCHANGE`` so new processes pick up the
    change without a logout; a broadcast failure is non-fatal.
    """
    import winreg  # local import: Windows-only module

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    # Best-effort: tell already-running shells/processes the env block changed.
    try:
        import ctypes

        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHANG = 0x0002
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHANG,
            1000,
            None,
        )
    except Exception as exc:  # noqa: BLE001 — broadcast is a nicety, not required
        log.debug("WM_SETTINGCHANGE broadcast failed (non-fatal): %s", exc)
