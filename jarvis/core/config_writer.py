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

# Canonical User-scope ENV var that overrides ``[brain.worker] provider``
# at boot. ``_apply_env_overrides`` splits on ``__`` and lower-cases, so
# ``JARVIS__BRAIN__WORKER__PROVIDER`` -> ``brain.worker.provider``
# (renamed from JARVIS__BRAIN__SUB_JARVIS__PROVIDER in the 2026-06-29
# Jarvis-Agents rename; the old var is accepted via AliasChoices + migration shim).
_WORKER_PROVIDER_ENV = "JARVIS__BRAIN__WORKER__PROVIDER"
_WORKER_MODEL_ENV = "JARVIS__BRAIN__WORKER__MODEL"
# Back-compat aliases for the old ENV var names — kept so any code that
# references these constants by name still resolves without an ImportError.
_SUB_JARVIS_PROVIDER_ENV = _WORKER_PROVIDER_ENV  # back-compat alias (pre-rename)
_SUB_JARVIS_MODEL_ENV = _WORKER_MODEL_ENV        # back-compat alias (pre-rename)

# Canonical User-scope ENV vars that override ``[tts] provider`` / ``[stt]
# provider`` at boot. Both section + key are single words, so
# ``_apply_env_overrides`` maps them cleanly to ``tts.provider`` / ``stt.provider``.
_TTS_PROVIDER_ENV = "JARVIS__TTS__PROVIDER"
_STT_PROVIDER_ENV = "JARVIS__STT__PROVIDER"
# ``[stt] model`` / ``[stt] language`` have the same single-word section + key,
# so a stale User-scope ENV var (e.g. one the wizard once wrote) OVERRIDES the
# TOML at boot and silently masks any later UI/TOML edit — the "model is
# hardcoded, I can't change it" trap (forensic 2026-06-28). The model/language
# setters therefore clear/sync this layer too, not just TOML + config-soll.  # i18n-allow
_STT_MODEL_ENV = "JARVIS__STT__MODEL"
_STT_LANGUAGE_ENV = "JARVIS__STT__LANGUAGE"


def set_brain_primary(name: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set ``[brain] primary`` to the given provider name across all layers.

    This is the AUTHORITATIVE writer for the user's Brain-provider choice.
    There are three persistence layers and a UI switch that only wrote one of
    them did not survive a restart:

      1. ``jarvis.toml`` ``[brain] primary``            (universal, always runs)
      2. ``scripts/config-soll.json`` ``brain.primary``  (drift-guard soll value)  # i18n-allow
      3. ``JARVIS__BRAIN__PRIMARY`` User-scope ENV var   (boot override)

    Raises ``FileNotFoundError`` if the TOML config file does not exist (a
    broken setup we do not silently mask). Layers 2 and 3 are best-effort
    cloud-first enhancements: they degrade to a graceful no-op on a headless
    Linux VPS (no config-soll.json, no Windows registry) and never raise out  # i18n-allow
    of this function nor break the TOML write.
    """
    # Layer 1 — universal, runs on every platform. May raise FileNotFoundError.
    _patch_table(path, "brain", "primary", name)
    # Layers 2 + 3 — best-effort, never raise.
    _sync_brain_primary_drift_soll(name)  # i18n-allow


def set_worker_provider(name: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set ``[brain.worker] provider`` (the Heavy-Task Jarvis-Agent provider)
    across all persistence layers.

    This is the AUTHORITATIVE writer for the user's subagent-provider choice
    and the write-side counterpart to the read-side resolution in
    ``jarvis.missions.worker_runtime.provider_map.canonical_worker_provider`` /
    ``jarvis.missions.init._worker_factory``. The worker provider is pinned
    in ``config-soll.json`` (``brain.worker.provider``), so a switch that  # i18n-allow
    wrote only the TOML would be reverted by the drift-guard within minutes —
    the same failure mode that hit ``brain.primary`` before it went 3-layer.

      1. ``jarvis.toml`` ``[brain.worker] provider``               (TOML)
      2. ``scripts/config-soll.json`` ``brain.worker.provider``    (drift-soll)  # i18n-allow
      3. ``JARVIS__BRAIN__WORKER__PROVIDER`` User-scope ENV var     (boot override)

    Raises ``FileNotFoundError`` if the TOML config file does not exist. Layers
    2 + 3 are best-effort cloud-first enhancements: graceful no-op on a headless
    Linux VPS and never raise out of this function nor break the TOML write.

    NB: this writes only ``provider``. The fallback chain
    (``fallback_provider`` etc.) is left untouched, mirroring how the brain
    switch leaves ``[brain]`` siblings alone.

    Renamed from ``set_sub_jarvis_provider`` in the 2026-06-29 Jarvis-Agents
    rename. The old name is preserved as a back-compat alias below.
    """
    # Layer 1 — universal, runs on every platform. May raise FileNotFoundError.
    _patch_worker_provider_toml(path, name)
    # Layers 2 + 3 — best-effort, never raise.
    _sync_worker_provider_drift_soll(name)  # i18n-allow


# Back-compat alias — callers that imported set_sub_jarvis_provider still work.
set_sub_jarvis_provider = set_worker_provider


def set_worker_model(model: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set ``[brain.worker] model`` (the dedicated Jarvis-Agent LLM override)
    across all persistence layers.

    The write-side counterpart to the read-side resolution in
    ``jarvis.missions.workers.provider_chain._resolve_provider_chain`` (the
    worker's primary model) and the ``/api/jarvis-agent/status`` ``model_resolved``
    display. Empty string is the documented sentinel: the active worker
    provider's ``deep_model`` (frontier) wins.

    ``brain.worker.model`` is pinned in ``config-soll.json`` like the  # i18n-allow
    provider, so a TOML-only write would be reverted by the drift-guard within
    minutes (BUG-010 class). Three layers, same shape as
    :func:`set_worker_provider`:

      1. ``jarvis.toml`` ``[brain.worker] model``                (TOML)
      2. ``scripts/config-soll.json`` ``brain.worker.model``     (drift-soll)  # i18n-allow
      3. ``JARVIS__BRAIN__WORKER__MODEL`` User-scope ENV var     (boot override)

    Layers 2 + 3 are best-effort cloud-first enhancements: graceful no-op on a
    headless Linux VPS and never raise out of this function nor break the TOML
    write. Takes effect for the NEXT mission (the worker resolves its chain per
    spawn).

    Renamed from ``set_sub_jarvis_model`` in the 2026-06-29 Jarvis-Agents rename.
    The old name is preserved as a back-compat alias below.
    """
    # Layer 1 — universal, runs on every platform. May raise FileNotFoundError.
    _patch_worker_key_toml(path, "model", model)
    # Layers 2 + 3 — best-effort, never raise.
    _sync_worker_model_drift_soll(model)  # i18n-allow


# Back-compat alias — callers that imported set_sub_jarvis_model still work.
set_sub_jarvis_model = set_worker_model


def set_tts_provider(name: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set ``[tts] provider`` and reconcile the provider-dependent defaults,
    across all THREE persistence layers.

    Beyond ``provider`` this also writes the provider-specific voice / language /
    model so the file never holds an invalid mix (e.g. switching Gemini ->
    Grok-Voice must not leave ``voice_de = "Charon"`` behind, a voice Grok cannot
    use). An existing value that is already valid for the new provider is kept —
    user overrides win.

    Three-layer persist (like ``set_brain_primary``): ``tts.provider`` (and the
    voice keys) are pinned in ``config-soll.json``, so a TOML-only write would be  # i18n-allow
    reverted by the drift-guard within 5 minutes — the same bug class that hit
    ``brain.primary``. We therefore sync config-soll.json + ENV too. Crucially,  # i18n-allow
    config-soll receives EXACTLY the keys the TOML write touched (provider + the  # i18n-allow
    voice/language/model it set or preserved) so the guard sees zero drift across
    the whole block.

    Layers 2 + 3 are best-effort (cloud-first): a graceful no-op on a headless
    Linux VPS, they never raise out of this function nor break the TOML write.
    """
    # Layer 1 — universal, runs on every platform. May raise FileNotFoundError.
    defaults = _TTS_DEFAULTS.get(name.lower(), {})
    applied = _patch_tts_block(path, name, defaults)
    # Layers 2 + 3 — best-effort, never raise.
    _sync_tts_provider_drift_soll(applied)  # i18n-allow


def set_stt_provider(name: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set ``[stt] provider`` across all THREE persistence layers.

    Takes effect on the next SpeechPipeline bootstrap (a voice restart): the STT
    provider is instantiated once at pipeline start.

    ``stt.provider`` is pinned in ``config-soll.json``, so — like Brain/TTS — the  # i18n-allow
    switch needs the 3-layer persist (TOML + config-soll + ENV), otherwise the  # i18n-allow
    drift-guard reverts it within 5 minutes. Layers 2 + 3 are best-effort
    (cloud-first) and never break the TOML write.
    """
    # Layer 1 — universal, runs on every platform. May raise FileNotFoundError.
    _patch_table(path, "stt", "provider", name)
    # Layers 2 + 3 — best-effort, never raise.
    _sync_stt_provider_drift_soll(name)  # i18n-allow


def set_tts_voice(voice: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set the global TTS voice (``[tts] voice_de`` + ``voice_en``).

    The TTS config is a single ``[tts]`` block, so this is the voice of the
    ACTIVE TTS provider. Most generative voices are multilingual (Gemini Charon,
    Grok leo …), so both language slots get the same value. ``voice_de`` /
    ``voice_en`` are pinned in ``config-soll.json`` (like ``tts.provider``), so a  # i18n-allow
    TOML-only write would be reverted by the drift-guard — we sync config-soll too.  # i18n-allow
    """
    _patch_table(path, "tts", "voice_de", voice)
    _patch_table(path, "tts", "voice_en", voice)
    try:
        _update_config_soll_section("tts", {"voice_de": voice, "voice_en": voice})  # i18n-allow
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync tts voice to config-soll.json: %s", exc)  # i18n-allow


def set_tts_model(model: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set the global TTS model (``[tts] model``) — e.g. Cartesia ``sonic-3.5``.

    Synced to config-soll (drift-guard pinned, same class as the voice keys).  # i18n-allow
    """
    _patch_table(path, "tts", "model", model)
    try:
        _update_config_soll_section("tts", {"model": model})  # i18n-allow
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync tts.model to config-soll.json: %s", exc)  # i18n-allow


def set_tts_volume(volume: float, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set the master TTS output volume (``[tts] volume``), a 0.0–1.0 gain.

    Clamped to the same bounds the config field enforces (``ge=0.0, le=1.0``) so
    a stray value can never over-drive (>1.0 clips) or invert (<0) playback. The
    ``[tts]`` block is drift-guard pinned (its reference snapshot already tracks
    other ``[tts]`` keys), so a TOML-only write would be reverted — we sync that
    snapshot too, exactly like :func:`set_tts_voice`. The Settings route applies
    the change live to the running player; this persists the boot default.
    """
    v = max(0.0, min(1.0, float(volume)))
    _patch_table(path, "tts", "volume", v)
    try:
        _update_config_soll_section("tts", {"volume": v})  # i18n-allow
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync tts.volume to config-soll.json: %s", exc)  # i18n-allow


def set_stt_model(model: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set the global STT model (``[stt] model``) across all THREE layers.

    Takes effect on the next SpeechPipeline bootstrap (a voice restart): the STT
    provider is instantiated once at pipeline start.

    ``stt.model`` is pinned in ``config-soll.json`` AND the single-word  # i18n-allow
    ``JARVIS__STT__MODEL`` ENV var overrides the TOML at boot, so — exactly like
    ``stt.provider`` — the switch needs the 3-layer persist (TOML + config-soll +  # i18n-allow
    ENV); otherwise a stale ENV var (the "model is hardcoded" trap, forensic
    2026-06-28) or the drift-guard silently reverts it. Layers 2 + 3 are
    best-effort (cloud-first) and never break the TOML write.
    """
    _patch_table(path, "stt", "model", model)
    try:
        _update_config_soll_section("stt", {"model": model})  # i18n-allow
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync stt.model to config-soll.json: %s", exc)  # i18n-allow
    try:
        _set_user_env_var(_STT_MODEL_ENV, model)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync %s to the User environment: %s", _STT_MODEL_ENV, exc)


def set_stt_language(language: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set the STT recognition language (``[stt] language``).

    One of ``auto`` | ``de`` | ``en`` | ``es`` (validated by the caller). ``auto``
    lets Whisper detect the spoken language per utterance (the bilingual default);
    a concrete code forces that language. Takes effect on the next SpeechPipeline
    bootstrap (a voice restart): the STT provider is instantiated once at pipeline
    start. Persisted across all THREE layers (TOML + config-soll + ENV): the stt  # i18n-allow
    block is drift-guard pinned, and the single-word ``JARVIS__STT__LANGUAGE`` ENV
    var would otherwise override the TOML at boot, so a 2-layer write could be
    silently masked (same trap as stt.model — forensic 2026-06-28).
    """
    _patch_table(path, "stt", "language", language)
    try:
        _update_config_soll_section("stt", {"language": language})  # i18n-allow
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync stt.language to config-soll.json: %s", exc)  # i18n-allow
    try:
        _set_user_env_var(_STT_LANGUAGE_ENV, language)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync %s to the User environment: %s", _STT_LANGUAGE_ENV, exc)


def set_tts_cartesia_model(model: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set Cartesia's model in its OWN sub-table ``[tts.cartesia] model_id``.

    Cartesia reads its model from this sub-table (default ``sonic-3.5``), NOT the
    global ``[tts] model`` that Gemini/OpenAI use. ``[tts.cartesia]`` is not pinned
    in config-soll, so a plain atomic TOML write suffices (no drift-guard revert).  # i18n-allow
    """
    path = _ensure_writable_config_path(path)
    with _WRITE_LOCK:
        raw = path.read_text(encoding="utf-8")
        had_bom = raw.startswith(_BOM)
        if had_bom:
            raw = raw[len(_BOM) :]
        doc: TOMLDocument = tomlkit.parse(raw)
        tts = doc.get("tts")
        if tts is None:
            tts = tomlkit.table()
            doc["tts"] = tts
        cart = tts.get("cartesia")
        if cart is None:
            cart = tomlkit.table()
            tts["cartesia"] = cart
        cart["model_id"] = model
        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)


def set_codex_binary_path(binary_path: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Set ``[codex] binary_path`` to work around Windows PATH issues."""
    _patch_table(path, "codex", "binary_path", binary_path)



# Voice-keybind action vocabulary. Shared with the keybinds API
# (jarvis/ui/web/settings_routes.py) and the TS type KeybindAction in the
# frontend (jarvis/ui/web/frontend/src/hooks/useHotkey.ts). Keep the three in
# sync. The mapped value is BOTH the jarvis.toml key under [trigger] AND the
# TriggerConfig field name (they are intentionally identical).
KEYBIND_ACTIONS = ("call", "hangup", "ptt")
KEYBIND_TOML_KEY = {
    "call": "hotkey_call",
    "hangup": "hotkey_hangup",
    "ptt": "hotkey",
}


def set_keybind(action: str, hotkey: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist a voice keybind (call / hangup / ptt) to ``[trigger]`` in jarvis.toml.

    Toml-only by design (same rationale as the other [trigger] writers — these
    keys are NOT tracked in the drift-guard's reference snapshot, so it never reverts
    them; a plain atomic write suffices and the BUG-010 3-layer rule does not
    apply). Takes effect on the next SpeechPipeline bootstrap (a Jarvis restart):
    bindings are armed once at pipeline start via ``TriggerConfig.resolve_hotkeys``
    plus the ``hotkey_hangup`` read at the call sites.
    """
    try:
        key = KEYBIND_TOML_KEY[action]
    except KeyError:
        raise ValueError(f"unknown keybind action: {action!r}") from None
    _patch_table(path, "trigger", key, hotkey)


def set_ptt_hotkey(hotkey: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Backward-compatible alias for ``set_keybind("ptt", ...)``."""
    set_keybind("ptt", hotkey, path=path)


def set_reply_language(name: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the user-facing reply-language pin in ``[brain] reply_language``.

    ``name`` is one of ``auto`` | ``de`` | ``en`` | ``es`` (validated by the
    caller). Takes effect as a boot default on the next ``load_config`` call — the
    live switch happens via ``BrainManager.set_reply_language``.
    """
    _patch_table(path, "brain", "reply_language", name)


def set_ui_language(name: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the interface (display) language in ``[ui] language``.

    ``name`` is one of ``en`` | ``de`` | ``es`` (validated by the caller). This
    is the backend home for what used to be a frontend-only localStorage value,
    so a voice command / the Control API can change the visible app language and
    the open UI switches live (the change is broadcast over /ws).
    """
    _patch_table(path, "ui", "language", name)


def set_preferred_opener(opener: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the remembered "open with" choice in ``[ui] preferred_opener``.

    ``opener`` is an opener id (``default`` | ``browser`` | an editor key like
    ``code``) validated by the caller. Used by the Outputs view so a file opens
    straight in the chosen app without re-prompting. Desktop-only setting.
    """
    _patch_table(path, "ui", "preferred_opener", opener)


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

      * The drift-guard only reverts keys it tracks in ``config-soll.json``, and  # i18n-allow
        ``trigger.wake_word`` is intentionally NOT tracked there — so a plain
        atomic toml write is never reverted. The three-layer rule exists to stop
        the guard from rolling a UI switch back (BUG-010); with no soll entry  # i18n-allow
        there is nothing to roll back.
      * Adding a nested ``wake_word`` object to ``config-soll.json`` would make  # i18n-allow
        the guard's scalar-only loops synthesise a garbage
        ``JARVIS__TRIGGER__WAKE_WORD`` user env var from a stringified dict
        (BUG-018 class). And a stale ``JARVIS__*`` override would silently win
        over a hand-edit of jarvis.toml — directly contradicting the
        "edit `phrase` here" guidance in the file. So neither the soll nor the  # i18n-allow
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
    try:
        _strip_persona_name(path)
    except Exception as exc:  # noqa: BLE001 — cleanup is best-effort, never breaks the save
        log.debug("persona-name strip skipped: %s", exc)


def set_autostart(enabled: bool, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the login-autostart toggle to ``[autostart] enabled`` in jarvis.toml.

    Toml-only by design: ``autostart.enabled`` is NOT tracked in
    ``config-soll.json`` (verified absent), so the drift-guard never reverts it —  # i18n-allow
    a plain atomic write suffices and the 3-layer rule (which exists only to stop
    the guard from rolling a UI switch back, BUG-010) does not apply.

    This persists the *intent*. The actual OS entry (install/remove) is applied
    by the caller via ``jarvis.autostart`` (live by the Settings route, or on the
    next boot by ``reconcile_autostart``).
    """
    _patch_table(path, "autostart", "enabled", bool(enabled))


def set_overlay_style(style: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the on-screen overlay style to ``[ui] orb_style`` in jarvis.toml.

    ``style`` is one of ``"jarvis_bar"`` / ``"mascot"`` / ``"none"``. TOML-only
    by design: ``ui.orb_style`` is NOT in the drift-guard's reference snapshot, so the
    drift-guard never reverts it (same rationale as :func:`set_autostart`). The
    Settings route applies the change live; this persists the boot default.
    """
    _patch_table(path, "ui", "orb_style", style)


def set_computer_use_engine(engine: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the active Computer-Use engine to ``[computer_use] engine``.

    ``engine`` is ``"current"`` (the maintained engine) or ``"june13"`` (the
    frozen 2026-06-10 / 352a784f snapshot kept as a reversible fallback).
    TOML-only by design: ``computer_use.engine`` is NOT in the drift-guard's
    reference snapshot, so it is never reverted (same rationale as
    :func:`set_overlay_style`). The harness reads the value per mission, so the
    switch applies on the next mission / restart without a code change.
    """
    _patch_table(path, "computer_use", "engine", engine)


def set_silence_window_ms(ms: int, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the voice silence window to ``[speech] vad_silence_ms`` in jarvis.toml.

    Clamps to the same 500–5000 ms bounds the config field enforces, so a stray
    value can never wedge endpointing. TOML-only by design (not in the
    drift-guard's reference snapshot, like :func:`set_overlay_style`); the
    Settings route applies the change live, this persists the boot default.
    """
    clamped = max(500, min(5000, int(ms)))
    _patch_table(path, "speech", "vad_silence_ms", clamped)


def set_session_idle_timeout_s(
    seconds: float, *, path: Path = DEFAULT_CONFIG_FILE
) -> None:
    """Persist ``[trigger] session_idle_timeout_s`` — the conversation-mode idle
    auto-hangup window.

    A value <= 0 DISABLES the auto-hangup entirely: the voice session then stays
    active until a manual hangup ("auflegen" / the hangup hotkey). Negative input
    is normalised to 0. Stored as a plain number; applies on the next voice
    (re)start. TOML-only (not drift-guarded), like :func:`set_silence_window_ms`.
    """
    value = max(0.0, float(seconds))
    _patch_table(path, "trigger", "session_idle_timeout_s", value)


def set_bar_persistent(enabled: bool, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist ``[ui] bar_persistent`` (the 'show bar at all times' toggle).

    TOML-only (not drift-guarded); the Taskbar route applies it live.
    """
    _patch_table(path, "ui", "bar_persistent", bool(enabled))


def set_mute_music(enabled: bool, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist ``[ducking] enabled`` (the 'mute music while dictating' toggle).

    TOML-only (not drift-guarded); the Taskbar route applies it live.
    """
    _patch_table(path, "ducking", "enabled", bool(enabled))


def set_sound_effects(enabled: bool, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist ``[ui] sound_effects`` (the global earcon master switch).

    TOML-only (not drift-guarded); the Settings route applies it live by
    mutating the shared in-memory config the speech pipeline reads.
    """
    _patch_table(path, "ui", "sound_effects", bool(enabled))


def set_team_proxy(
    enabled: bool,
    url: str,
    local_providers: list[str],
    *,
    path: Path = DEFAULT_CONFIG_FILE,
) -> None:
    """Persist client-side team-proxy mode to ``[team_proxy]`` in jarvis.toml.

    Writes ``enabled`` / ``url`` / ``local_providers`` (2026-06-20 team-proxy
    spec §4). TOML-only (not drift-guarded), like :func:`set_autostart`; the
    Settings route applies it live, this persists the boot default. The per-user
    token is a SECRET and is NEVER written here — it lives in the Credential
    Manager (slot ``team_proxy_token``).
    """
    _patch_table(path, "team_proxy", "enabled", bool(enabled))
    _patch_table(path, "team_proxy", "url", (url or "").strip())
    _patch_table(
        path,
        "team_proxy",
        "local_providers",
        [str(p).strip() for p in local_providers if str(p).strip()],
    )


def _ensure_writable_config_path(path: Path) -> Path:
    """Resolve a writable config path and create it if missing (M1, headless VPS).

    The in-app config writers (channel toggles, provider switches, wiki curator)
    default to ``DEFAULT_CONFIG_FILE`` (``/app/jarvis.toml``), which a headless
    ``python:3.11-slim`` container does not ship and ``/app`` is read-only. When the
    caller passed that bundled default, honour ``JARVIS_CONFIG`` via
    ``resolve_config_path()``; then create the file (+ parent) if absent so an
    in-app save/connect persists on EVERY OS instead of raising FileNotFoundError.
    """
    from jarvis.core.config import DEFAULT_CONFIG_FILE as _DEFAULT, resolve_config_path

    if path == _DEFAULT:
        path = resolve_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def set_telegram_enabled(enabled: bool, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the Telegram channel toggle to ``[integrations.telegram] enabled``.

    Written when the user connects/disconnects Telegram in the Plugin
    Marketplace: the bot token itself lives in the Credential Manager
    (``telegram_bot_token``); this flag tells the channel bootstrap to start it.

    ``[integrations.telegram]`` is a NESTED table, so ``_patch_table`` (single
    level) does not fit — we walk/create the two levels here. Toml-only by
    design: ``integrations.telegram.enabled`` is not tracked in
    ``config-soll.json``, so the drift-guard never reverts it.  # i18n-allow
    """
    path = _ensure_writable_config_path(path)

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
        telegram = integrations.get("telegram")
        if telegram is None:
            telegram = tomlkit.table()
            integrations["telegram"] = telegram
        telegram["enabled"] = bool(enabled)
        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)


def add_telegram_allowed_user_id(user_id: int, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist a Telegram user id under ``[integrations.telegram]``.

    Used by first-private-message pairing. The token remains a secret; the
    numeric Telegram user id is not secret and belongs in the operational config
    so the channel keeps working after restart. Idempotent and comment-preserving
    like :func:`set_telegram_enabled`.
    """
    path = _ensure_writable_config_path(path)

    uid = int(user_id)
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
        telegram = integrations.get("telegram")
        if telegram is None:
            telegram = tomlkit.table()
            integrations["telegram"] = telegram

        current = telegram.get("allowed_user_ids")
        values = [int(v) for v in current] if current is not None else []
        if uid not in values:
            values.append(uid)
            telegram["allowed_user_ids"] = values

        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)


def add_discord_allowed_user_id(user_id: int, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist a Discord user id under ``[integrations.discord]``.

    Used by first-direct-message pairing. The token remains a secret; the
    numeric Discord user id is not secret and belongs in the operational config
    so the channel keeps working after restart. Idempotent and comment-preserving
    like :func:`add_telegram_allowed_user_id`.
    """
    path = _ensure_writable_config_path(path)

    uid = int(user_id)
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
        discord = integrations.get("discord")
        if discord is None:
            discord = tomlkit.table()
            integrations["discord"] = discord

        current = discord.get("allowed_user_ids")
        values = [int(v) for v in current] if current is not None else []
        if uid not in values:
            values.append(uid)
            discord["allowed_user_ids"] = values

        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)


def _set_integration_value(
    platform: str, key: str, value: object, *, path: Path = DEFAULT_CONFIG_FILE
) -> None:
    """Set ``[integrations.<platform>] <key> = value`` in jarvis.toml.

    Walks/creates the two-level nested table (``_patch_table`` only handles a
    single level). Comment- and BOM-preserving, lock-guarded, atomic — same
    contract as :func:`set_telegram_enabled`. Toml-only by design: these
    operational integration flags are not tracked in ``config-soll.json``, so  # i18n-allow
    the drift-guard never reverts them.
    """
    path = _ensure_writable_config_path(path)

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
        table = integrations.get(platform)
        if table is None:
            table = tomlkit.table()
            integrations[platform] = table
        table[key] = value
        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)


def set_discord_enabled(enabled: bool, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the Discord channel toggle to ``[integrations.discord] enabled``.

    Mirror of :func:`set_telegram_enabled`: written when the user
    connects/disconnects Discord in the Plugin Marketplace. The bot token lives
    in the Credential Manager (``discord_bot_token``); this flag tells the
    channel bootstrap whether to start the bot.
    """
    _set_integration_value("discord", "enabled", bool(enabled), path=path)


def set_telegram_pairing(on: bool, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Toggle ``[integrations.telegram] pair_on_first_private_message``.

    Turned off when the owner connects with an explicit user id, so the bot
    never claims the allowlist for whoever messages first (owner-lock contract).
    """
    _set_integration_value("telegram", "pair_on_first_private_message", bool(on), path=path)


def set_discord_pairing(on: bool, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Toggle ``[integrations.discord] pair_on_first_dm``.

    Turned off when the owner connects with an explicit user id, so the bot
    never claims the allowlist for whoever DMs first (owner-lock contract).
    """
    _set_integration_value("discord", "pair_on_first_dm", bool(on), path=path)


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

    Background: providers added after the setup wizard via the UI (e.g. openrouter)
    often lack a ``[brain.providers.<name>]`` block. During a switch-persist we
    ensure here that after an app restart the tier-default fallback logic in
    BrainManager is not needed again — the block is then cleanly persisted.
    """
    path = _ensure_writable_config_path(path)

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
        # them to "Charon" keeps them consistent with config-soll.json's tts block  # i18n-allow
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
    # ElevenLabs uses voice IDs (cryptic hashes) — no whitelist.
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
    config-soll drift-sync mirrors exactly these keys so the guard sees zero  # i18n-allow
    drift across the whole block.
    """
    path = _ensure_writable_config_path(path)

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
                    # value, but still record it so the config-soll drift-sync  # i18n-allow
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
    cu_model: str | None = None,
    path: Path = DEFAULT_CONFIG_FILE,
) -> None:
    """Patch ``[brain.providers.<provider>]`` ``model`` / ``deep_model`` in
    the TOML file.

    Used by the per-provider model picker (``PUT /api/providers/{id}/model``)
    and the frontier auto-switch (Phase F.3) so a model change is persisted in
    jarvis.toml — otherwise the switch is lost on the next ``cfg.load_config()``.

    Three-layer persist (like ``set_brain_primary`` / ``set_sub_jarvis_provider``):
    ``brain.providers.<p>.model`` / ``deep_model`` are pinned in
    ``config-soll.json``, so a TOML-only write would be reverted by the  # i18n-allow
    drift-guard within 5 minutes (BUG-010 class) — exactly the "I picked a model
    and it flipped back" symptom. We therefore sync config-soll.json too. No ENV  # i18n-allow
    layer is written: per-provider model keys have no effective ``JARVIS__*``
    override (the boot override only nests on ``__`` and the drift-guard's dotted
    ``JARVIS__BRAIN.PROVIDERS.*`` vars are inert), so adding one would only create
    a new stale-override trap. Layer 2 is best-effort (cloud-first): a graceful
    no-op on a headless Linux VPS, it never raises out of this function nor
    breaks the TOML write.

    Idempotent: if the block is absent it is created; ``None`` values change
    nothing.
    """
    path = _ensure_writable_config_path(path)
    if model is None and deep_model is None and cu_model is None:
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
        if cu_model is not None:
            # "" is a meaningful value (UI "use my main model") distinct from
            # None ("leave unchanged"), so write whatever non-None was given.
            block["cu_model"] = cu_model

        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)

    # Layer 2 — best-effort drift-soll sync (never raises, never blocks the  # i18n-allow
    # TOML write). Only the keys actually written are synced so the guard sees
    # zero drift across the block.
    _sync_brain_provider_model_drift_soll(  # i18n-allow
        provider, model=model, deep_model=deep_model, cu_model=cu_model
    )


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
    path = _ensure_writable_config_path(path)
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


def _patch_table(path: Path, table: str, key: str, value: str | bool | list[str]) -> None:
    """Set ``[table] key = value`` in the TOML file.

    Creates the table if it is absent. Preserves comments and formatting via
    tomlkit, including the optional BOM (see module docstring). ``value`` may be
    a ``str``, a ``bool`` (serialised as ``true``/``false`` — used by the
    autostart toggle), or a ``list[str]`` (serialised as a TOML array — used by
    ``[team_proxy] local_providers``).
    """
    path = _ensure_writable_config_path(path)

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


def _patch_worker_provider_toml(path: Path, name: str) -> None:
    """Set ``[brain.worker] provider = name`` in the TOML.

    Unlike :func:`_patch_table`, this walks the NESTED ``brain`` -> ``worker``
    path instead of treating ``"brain.worker"`` as a flat top-level key
    (``doc.get("brain.worker")`` would create a literal dotted key, not the
    ``[brain.worker]`` section). Creates either level if missing. Preserves
    comments, sibling keys, and the optional BOM.

    Renamed from ``_patch_sub_jarvis_provider_toml`` in the 2026-06-29
    Jarvis-Agents rename. Writes to ``[brain.worker]`` so new config files
    use the new section name; old ``[brain.sub_jarvis]`` blocks are still
    read via BrainConfig.worker's AliasChoices back-compat alias.
    """
    path = _ensure_writable_config_path(path)

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
        sub = brain.get("worker")
        if sub is None:
            sub = tomlkit.table()
            brain["worker"] = sub
        sub["provider"] = name

        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)


def _patch_worker_key_toml(path: Path, key: str, value: object) -> None:
    """Set one key under the nested ``[brain.worker]`` table.

    Generalised sibling of :func:`_patch_worker_provider_toml` (kept
    untouched for parallel-session safety): walks ``brain`` -> ``worker``
    (creating either level if missing), preserves comments, sibling keys, and
    the optional BOM.

    Renamed from ``_patch_sub_jarvis_key_toml`` in the 2026-06-29
    Jarvis-Agents rename.
    """
    path = _ensure_writable_config_path(path)

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
        sub = brain.get("worker")
        if sub is None:
            sub = tomlkit.table()
            brain["worker"] = sub
        sub[key] = value

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
    path = _ensure_writable_config_path(path)

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


def _strip_persona_name(path: Path) -> None:
    """Remove a stale ``[persona] name`` entry (the legacy assistant-name override).

    The wake word is now the single name source, so a leftover ``[persona] name``
    from before the 2026-06-20 coupling must not linger. Best-effort: a missing
    file/table/key is a no-op. Preserves comments and the optional BOM, exactly
    like :func:`_patch_table`.
    """
    if not path.exists():
        return

    with _WRITE_LOCK:
        raw = path.read_text(encoding="utf-8")
        had_bom = raw.startswith(_BOM)
        if had_bom:
            raw = raw[len(_BOM):]
        doc: TOMLDocument = tomlkit.parse(raw)
        persona = doc.get("persona")
        if persona is None or "name" not in persona:
            return
        del persona["name"]
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
# Layer 2 + 3 — config-soll.json + ENV sync (best-effort, cloud-first safe)  # i18n-allow
# ----------------------------------------------------------------------


def _config_soll_path() -> Path:  # i18n-allow
    """Locate ``scripts/config-soll.json`` relative to the repo root.  # i18n-allow

    Derived from the same ``PROJECT_ROOT`` resolution that anchors
    ``DEFAULT_CONFIG_FILE`` so the two paths stay consistent. On a headless
    Linux VPS this file usually does not exist — callers must treat a missing
    file as a graceful no-op.
    """
    return PROJECT_ROOT / "scripts" / "config-soll.json"  # i18n-allow


def _sync_brain_primary_drift_soll(name: str) -> None:  # i18n-allow
    """Best-effort sync of ``brain.primary`` into the drift-soll + ENV layers.  # i18n-allow

    NEVER raises and NEVER breaks the (already-completed) TOML write. Two
    independent best-effort steps:

      (a) Update ``scripts/config-soll.json`` ``brain.primary`` so the  # i18n-allow
          drift-guard daemon (5-min cron) does not revert the switch. Graceful
          no-op when the file is absent (cloud-first / headless VPS).
      (b) Set the User-scope ``JARVIS__BRAIN__PRIMARY`` ENV var (Windows
          registry) so a fresh boot's ``JARVIS__*`` override matches the new
          choice instead of reverting it; also update ``os.environ`` so the
          live process and any child it spawns are immediately consistent.
          The registry write is gated behind ``sys.platform == "win32"``.
    """
    # (a) config-soll.json — graceful no-op if the file does not exist.  # i18n-allow
    try:
        _update_config_soll_brain_primary(name)  # i18n-allow
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync brain.primary to config-soll.json: %s", exc)  # i18n-allow

    # (b) ENV var — winreg gated to win32, os.environ updated cross-platform.
    try:
        _set_user_env_var(_BRAIN_PRIMARY_ENV, name)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync %s to the User environment: %s", _BRAIN_PRIMARY_ENV, exc)


def _sync_worker_provider_drift_soll(name: str) -> None:  # i18n-allow
    """Best-effort sync of ``brain.worker.provider`` into config-soll + ENV.  # i18n-allow

    NEVER raises and NEVER breaks the (already-completed) TOML write. Same
    two-step shape as :func:`_sync_brain_primary_drift_soll`.  # i18n-allow

    Renamed from ``_sync_sub_jarvis_provider_drift_soll`` in the 2026-06-29  # i18n-allow
    Jarvis-Agents rename.
    """
    try:
        _update_config_soll_worker_provider(name)  # i18n-allow
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync worker provider to config-soll.json: %s", exc)  # i18n-allow

    try:
        _set_user_env_var(_WORKER_PROVIDER_ENV, name)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning(
            "Could not sync %s to the User environment: %s",
            _WORKER_PROVIDER_ENV,
            exc,
        )


def _sync_worker_model_drift_soll(model: str) -> None:  # i18n-allow
    """Best-effort sync of ``brain.worker.model`` into config-soll + ENV.  # i18n-allow

    NEVER raises and NEVER breaks the (already-completed) TOML write. Same
    two-step shape as :func:`_sync_worker_provider_drift_soll`.  # i18n-allow

    Renamed from ``_sync_sub_jarvis_model_drift_soll`` in the 2026-06-29  # i18n-allow
    Jarvis-Agents rename.
    """
    try:
        _update_config_soll_worker_key("model", model)  # i18n-allow
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync worker model to config-soll.json: %s", exc)  # i18n-allow

    try:
        _set_user_env_var(_WORKER_MODEL_ENV, model)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning(
            "Could not sync %s to the User environment: %s",
            _WORKER_MODEL_ENV,
            exc,
        )


def _sync_tts_provider_drift_soll(applied: dict[str, str]) -> None:  # i18n-allow
    """Best-effort sync of the TTS block into the drift-soll + ENV layers.  # i18n-allow

    NEVER raises and NEVER breaks the (already-completed) TOML write. ``applied``
    is the exact set of ``[tts]`` keys the TOML write touched (provider + any
    provider-dependent voice/language/model), so config-soll ends up byte-for-byte  # i18n-allow
    in agreement and the drift-guard reverts nothing. The ENV layer only pins the
    provider (the single value a stale boot override could revert).
    """
    try:
        _update_config_soll_section("tts", applied)  # i18n-allow
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync tts.* to config-soll.json: %s", exc)  # i18n-allow

    provider_name = applied["provider"]  # always present — set in _patch_tts_block
    try:
        _set_user_env_var(_TTS_PROVIDER_ENV, provider_name)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync %s to the User environment: %s", _TTS_PROVIDER_ENV, exc)


def _sync_stt_provider_drift_soll(name: str) -> None:  # i18n-allow
    """Best-effort sync of ``stt.provider`` into the drift-soll + ENV layers.  # i18n-allow

    NEVER raises and NEVER breaks the (already-completed) TOML write. Same
    two-step shape as :func:`_sync_brain_primary_drift_soll`.  # i18n-allow
    """
    try:
        _update_config_soll_section("stt", {"provider": name})  # i18n-allow
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync stt.provider to config-soll.json: %s", exc)  # i18n-allow

    try:
        _set_user_env_var(_STT_PROVIDER_ENV, name)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning("Could not sync %s to the User environment: %s", _STT_PROVIDER_ENV, exc)


def _sync_brain_provider_model_drift_soll(  # i18n-allow
    provider: str, *, model: str | None, deep_model: str | None, cu_model: str | None = None
) -> None:
    """Best-effort sync of ``brain.providers.<p>`` model keys into the drift-soll.  # i18n-allow

    NEVER raises and NEVER breaks the (already-completed) TOML write. Only the
    keys actually written (non-``None``) are synced, so config-soll ends up in  # i18n-allow
    agreement with the TOML and the drift-guard reverts nothing. No ENV layer:
    per-provider model keys have no effective ``JARVIS__*`` boot override (see
    the docstring of :func:`set_brain_provider_model`). The flat dotted top-level
    key ``brain.providers.<p>`` is exactly how the soll file stores it.  # i18n-allow
    """
    values: dict[str, object] = {}
    if model is not None:
        values["model"] = model
    if deep_model is not None:
        values["deep_model"] = deep_model
    if cu_model is not None:
        values["cu_model"] = cu_model
    if not values:
        return
    try:
        _update_config_soll_section(f"brain.providers.{provider}", values)  # i18n-allow
    except Exception as exc:  # noqa: BLE001 — best-effort, must not propagate
        log.warning(
            "Could not sync brain.providers.%s model to config-soll.json: %s",  # i18n-allow
            provider,
            exc,
        )


def _update_config_soll_section(top: str, values: dict[str, object]) -> None:  # i18n-allow
    """Atomically merge ``values`` into ``data[top]`` in config-soll.json.  # i18n-allow

    Preserves every other key (``_comment``, ``_updated``, other keys in the
    same section, other top-level tables). Atomic tempfile + ``os.replace``,
    UTF-8, ``indent=2``. Graceful no-op when the file is absent (cloud-first)
    or when the section already matches every value (avoid a needless rewrite).

    MUST NOT be called while ``_WRITE_LOCK`` is held — it acquires that lock
    itself and ``_WRITE_LOCK`` is a non-reentrant ``threading.Lock`` (it would
    deadlock). Today's callers acquire it only sequentially, never nested.
    """
    soll_path = _config_soll_path()  # i18n-allow
    if not soll_path.exists():  # i18n-allow
        log.debug("config-soll.json absent (%s) — skipping drift-soll sync", soll_path)  # i18n-allow
        return

    with _WRITE_LOCK:
        raw = soll_path.read_text(encoding="utf-8")  # i18n-allow
        data = json.loads(raw)
        section = data.get(top)
        if not isinstance(section, dict):
            section = {}
            data[top] = section
        if all(section.get(k) == v for k, v in values.items()):
            return  # already in sync — avoid a needless rewrite
        section.update(values)

        out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        _atomic_write_text(soll_path, out)  # i18n-allow


def _update_config_soll_brain_primary(name: str) -> None:  # i18n-allow
    """Atomically set ``data["brain"]["primary"] = name`` in config-soll.json.  # i18n-allow

    Preserves all other keys (``_comment``, ``_updated``, other ``brain.*``
    keys, other top-level tables). Atomic tempfile + ``os.replace``, UTF-8,
    ``indent=2``. Graceful no-op when the file is absent.
    """
    soll_path = _config_soll_path()  # i18n-allow
    if not soll_path.exists():  # i18n-allow
        log.debug("config-soll.json absent (%s) — skipping drift-soll sync", soll_path)  # i18n-allow
        return

    with _WRITE_LOCK:
        raw = soll_path.read_text(encoding="utf-8")  # i18n-allow
        data = json.loads(raw)
        brain = data.get("brain")
        if not isinstance(brain, dict):
            brain = {}
            data["brain"] = brain
        if brain.get("primary") == name:
            return  # already in sync — avoid a needless rewrite
        brain["primary"] = name

        out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        _atomic_write_text(soll_path, out)  # i18n-allow


def _update_config_soll_worker_provider(name: str) -> None:  # i18n-allow
    """Atomically set ``data["brain.worker"]["provider"] = name`` in
    config-soll.json.  # i18n-allow

    Note the FLAT dotted key ``"brain.worker"`` — that is how the drift-guard
    soll file stores the sub-table (see scripts/config-soll.json), NOT a nested  # i18n-allow
    ``data["brain"]["worker"]``. Preserves all other keys (``_comment``, the
    fallback chain, other tables). Graceful no-op when the file is absent.

    Renamed from ``_update_config_soll_sub_jarvis_provider`` in the 2026-06-29  # i18n-allow
    Jarvis-Agents rename; now writes to the ``"brain.worker"`` flat key.
    """
    soll_path = _config_soll_path()  # i18n-allow
    if not soll_path.exists():  # i18n-allow
        log.debug("config-soll.json absent (%s) — skipping drift-soll sync", soll_path)  # i18n-allow
        return

    with _WRITE_LOCK:
        raw = soll_path.read_text(encoding="utf-8")  # i18n-allow
        data = json.loads(raw)
        block = data.get("brain.worker")
        if not isinstance(block, dict):
            block = {}
            data["brain.worker"] = block
        if block.get("provider") == name:
            return  # already in sync — avoid a needless rewrite
        block["provider"] = name

        out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        _atomic_write_text(soll_path, out)  # i18n-allow


def _update_config_soll_worker_key(key: str, value: str) -> None:  # i18n-allow
    """Atomically set ``data["brain.worker"][key] = value`` in config-soll.json.  # i18n-allow

    Generalised sibling of :func:`_update_config_soll_worker_provider`  # i18n-allow
    (same FLAT dotted-key layout, same preservation guarantees, same graceful
    no-op when the file is absent).

    Renamed from ``_update_config_soll_sub_jarvis_key`` in the 2026-06-29  # i18n-allow
    Jarvis-Agents rename.
    """
    soll_path = _config_soll_path()  # i18n-allow
    if not soll_path.exists():  # i18n-allow
        log.debug("config-soll.json absent (%s) — skipping drift-soll sync", soll_path)  # i18n-allow
        return

    with _WRITE_LOCK:
        raw = soll_path.read_text(encoding="utf-8")  # i18n-allow
        data = json.loads(raw)
        block = data.get("brain.worker")
        if not isinstance(block, dict):
            block = {}
            data["brain.worker"] = block
        if block.get(key) == value:
            return  # already in sync — avoid a needless rewrite
        block[key] = value

        out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        _atomic_write_text(soll_path, out)  # i18n-allow


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomic tempfile + replace for a plain UTF-8 text file (no read-only flag).

    Used for config-soll.json, which — unlike jarvis.toml — does not carry the  # i18n-allow
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

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
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


def set_wiki_curator_provider(
    name: str,
    *,
    model: str = "",
    path: Path = DEFAULT_CONFIG_FILE,
) -> None:
    """Persist the Wiki-curator model picker in ``[memory.wiki.curator]``.

    Writes ``provider`` and ``model`` together. Empty strings are persisted
    verbatim — they are the documented fallback sentinels resolved at runtime
    by ``jarvis.memory.wiki.curator_llm._resolve_provider_and_model``
    (``provider=""`` -> ``brain.primary``; ``model=""`` -> the provider's
    cheap/fast router model). Takes effect as a boot default on the next
    ``load_config``; the live switch happens in the settings route by resetting
    the running ``WikiCuratorLLM``'s cached brain.
    """
    _patch_wiki_curator_toml(path, {"provider": name, "model": model})


def _patch_wiki_curator_toml(path: Path, values: dict[str, object]) -> None:
    """Set keys under the nested ``[memory.wiki.curator]`` table.

    Walks ``memory`` -> ``wiki`` -> ``curator`` (creating any missing level),
    sets each key in ``values``, and preserves comments, sibling keys, and the
    optional BOM (same contract as :func:`_patch_sub_jarvis_provider_toml`).
    """
    path = _ensure_writable_config_path(path)

    with _WRITE_LOCK:
        raw = path.read_text(encoding="utf-8")
        had_bom = raw.startswith(_BOM)
        if had_bom:
            raw = raw[len(_BOM) :]
        doc: TOMLDocument = tomlkit.parse(raw)

        memory = doc.get("memory")
        if memory is None:
            memory = tomlkit.table()
            doc["memory"] = memory
        wiki = memory.get("wiki")
        if wiki is None:
            wiki = tomlkit.table()
            memory["wiki"] = wiki
        curator = wiki.get("curator")
        if curator is None:
            curator = tomlkit.table()
            wiki["curator"] = curator
        for key, value in values.items():
            curator[key] = value

        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)
