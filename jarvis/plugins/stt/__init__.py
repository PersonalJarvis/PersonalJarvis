"""STT provider plugins (faster-whisper, Groq, OpenRouter, OpenAI, Gemini).

This package exposes a single ``build_stt_from_config`` factory that turns an
``STTConfig`` into the configured ``STTProvider``-conformant instance via the
``jarvis.stt`` entry-point group. Local Whisper stays the fallback so the wake-
detector path keeps working when no cloud provider is registered.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from functools import lru_cache
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from loguru import logger

ENTRY_POINT_GROUP = "jarvis.stt"

# Credential candidates per CLOUD STT provider — the (keyring_key, env_var) pairs
# that hold a usable key. A fresh downloader's single key is rarely Groq, so the
# factory must consult this before constructing a cloud STT and cross over to the
# key-free local faster-whisper when the configured cloud provider has no key
# (open-source single-provider resilience, AP-22). Providers NOT listed here are
# left untouched (unknown / third-party entry-points cannot be probed).
_STT_SECRET_CANDIDATES: dict[str, tuple[tuple[str, str], ...]] = {
    "groq-api": (("groq_api_key", "GROQ_API_KEY"),),
    # OpenRouter STT reuses the SAME key slot as the OpenRouter brain, so a user
    # who configured OpenRouter for chat gets cloud STT for free. The id is
    # ``openrouter-stt`` (distinct from the ``openrouter`` brain id) to avoid a
    # collision in the shared model-catalog / provider-spec namespaces.
    "openrouter-stt": (("openrouter_api_key", "OPENROUTER_API_KEY"),),
    # OpenAI Whisper STT reuses the SAME openai_api_key slot as the OpenAI brain.
    "openai-api": (("openai_api_key", "OPENAI_API_KEY"),),
    # Gemini STT reuses the SAME AI-Studio key slots as the Gemini brain/TTS, so
    # the Gemini-only downloader (the recommended-default persona) gets cloud
    # voice input with no second credential.
    "gemini-api": (
        ("gemini_api_key", "GEMINI_API_KEY"),
        ("google_aistudio_api_key", "GOOGLE_AIStudio_API_KEY"),
        ("google_api_key", "GOOGLE_API_KEY"),
    ),
}

# Cross-family probe order when the configured cloud STT has no usable key.
# OpenRouter leads because its shared Brain key commonly needs no second setup;
# OpenAI and Gemini follow, while Groq is deliberately the LAST cloud option.
# Live German dictation showed materially worse transcription quality on Groq,
# so availability must not make it the preferred takeover. Only a family that
# BOTH has a key AND is registered as a `jarvis.stt` entry-point is ever chosen
# (a keyed-but-unregistered name is skipped, so we never promise an STT we cannot
# build). The key-free local engine remains the final floor below every cloud
# family when it is installed.
_STT_CROSS_FAMILY_ORDER: tuple[str, ...] = (
    "openrouter-stt", "openai-api", "gemini-api", "groq-api",
)

# Constructor kwargs the factory offers but no provider is required to accept.
# A plugin that predates one of them raises TypeError, and the build retries
# with every one of them dropped rather than falling all the way through to the
# local engine a base install does not ship. Anything NOT listed here (the
# language, the team-proxy endpoint/key) is load-bearing and never dropped.
_OPTIONAL_PROVIDER_KWARGS: tuple[str, ...] = (
    "prompt", "timeout_s", "model", "temperature",
)


def resolve_stt_model(stt_cfg: Any, provider_name: str) -> str:
    """The model id to hand ``provider_name``; ``""`` for "your own default".

    ``[stt].model`` is a faster-whisper CHECKPOINT name (``large-v3-turbo``),
    and a checkpoint name means nothing to a hosted API. One global value
    forwarded to whichever provider is selected would therefore post
    ``large-v3-turbo`` to Groq on a fresh install — so the cloud pick lives in
    its own per-provider slot, ``[stt].models.<provider id>``, written by the
    model picker and read here.

    Precedence:

    1. **The per-provider pin**, when this install has one.
    2. **``""``** otherwise: the plugin's own default, i.e. exactly what every
       install did before per-provider pins existed.

    ``[stt].model`` is deliberately NOT a fallback here. It reaches the local
    engine through :func:`_build_local_fallback`, which is the one consumer a
    checkpoint name is the right kind of string for; letting it leak into this
    answer would hand ``large-v3-turbo`` to a hosted API or to the on-device
    Nemotron recognizer, neither of which has ever heard of it.

    Never raises: a config object that carries none of this (a test double, a
    stale install) answers ``""``, which is a working value everywhere.
    """
    name = (provider_name or "").strip()
    if not name:
        return ""
    try:
        pins = getattr(stt_cfg, "models", None) or {}
        pinned = str(pins.get(name, "") or "").strip() if hasattr(pins, "get") else ""
    except Exception as exc:  # noqa: BLE001 — a malformed pin is not worth a build
        logger.debug("STT per-provider model pin unreadable ({}); using defaults.", exc)
        pinned = ""
    return pinned


def _stt_has_credential(provider_name: str, kwargs: dict[str, Any]) -> bool:
    """Whether the configured cloud STT has a usable key (else: fall to local).

    True when a key is injected (team-proxy token / explicit ``api_key``) or a
    keyring/env credential resolves. Providers with no entry in
    ``_STT_SECRET_CANDIDATES`` (unknown / third-party) return True so their
    construction path is unchanged — only known cloud providers are gated.
    """
    if kwargs.get("api_key"):
        return True
    candidates = _STT_SECRET_CANDIDATES.get(provider_name)
    if candidates is None:
        return True
    from jarvis.core import config as _cfg

    return _cfg.get_secret_any(candidates) is not None


def _stt_family_has_key(provider_name: str) -> bool:
    """Whether the STT *family* ``provider_name`` has a usable credential on this host.

    Like :func:`_stt_has_credential` but without kwargs — used by the cross-family
    resolver to probe candidate families. Honours the team proxy for the proxy-
    capable ``groq-api`` (a proxied provider carries a per-user token even with no
    local key), and treats unknown / third-party providers (no entry in
    ``_STT_SECRET_CANDIDATES``) as keyed so their path is never gated.
    """
    from jarvis.core import config as _cfg

    if provider_name == "groq-api":
        try:
            ep = _cfg.resolve_provider_endpoint("groq-api")
            if getattr(ep, "via_proxy", False) and getattr(ep, "credential", None):
                return True
        except Exception as exc:  # noqa: BLE001 — a proxy probe must never break STT build
            logger.debug("STT groq proxy probe failed ({}); using key candidates.", exc)
    candidates = _STT_SECRET_CANDIDATES.get(provider_name)
    if candidates is None:
        return True
    return _cfg.get_secret_any(candidates) is not None


def _resolve_keyed_stt_provider(primary_name: str) -> str:
    """Pick a cloud STT the host can actually run (open-source AP-22).

    Keeps the configured provider when it has a usable key — so the maintainer
    path (whose configured key resolves) is untouched. Otherwise crosses to the
    first cloud STT family the user DOES have a key for AND that is registered as
    an entry-point. When NO cloud family has a usable key, returns the configured
    name unchanged; the caller then drops to the key-free local faster-whisper as
    the universal floor. Mirrors the TTS factory's ``_resolve_keyed_tts_provider``.
    """
    if _stt_family_has_key(primary_name):
        return primary_name
    for cand in _STT_CROSS_FAMILY_ORDER:
        if cand == primary_name:
            continue
        if _stt_family_has_key(cand) and _load_provider_class(cand) is not None:
            logger.warning(
                "STT provider {!r} has no usable API key; crossing to {!r} — the "
                "cloud STT family the user actually has a key for — so voice input "
                "still works for a single-key user (open-source AP-22). Set "
                "[stt].provider to silence this.",
                primary_name,
                cand,
            )
            return cand
    return primary_name


#: Class attribute a recognizer plugin sets to declare that it transcribes on
#: THIS machine. The capability the plugin answers for ITSELF (AP-21) — and the
#: seam a second on-device engine plugs into, because the dictation polish
#: pass's privacy floor keys on the answer and getting it wrong in the "cloud"
#: direction uploads text from somebody who chose a local recognizer to stop
#: exactly that.
_ON_DEVICE_ATTR = "runs_on_device"

#: The on-device engines this repo ships, for the case where the provider class
#: does not declare the attribute above. A NAME list is what AP-21 warns about,
#: so it is asked only for our OWN plugins (whose behaviour we know without
#: importing them) and never used to judge a stranger's.
_SHIPPED_ON_DEVICE_PROVIDERS: frozenset[str] = frozenset(
    {"faster-whisper", "nemotron-local"}
)


@lru_cache(maxsize=32)
def provider_runs_on_device(provider_name: str) -> bool:
    """Whether the recognizer ``provider_name`` transcribes on THIS machine.

    The privacy question — do the user's words leave this computer — asked in
    three steps, cheapest first:

    1. A provider with a CLOUD credential slot is remote by definition: a
       remote account is what that key pays for. No import, no guessing.
    2. Otherwise, one of the on-device engines this repo ships answers ``True``
       straight away. A name comparison, kept for our own plugins only, so the
       common case costs nothing.
    3. Anything else is asked of the PLUGIN: a provider class that sets
       ``runs_on_device = True`` is believed, whatever it is called. Declaring
       it is all a second on-device recognizer has to do — every consumer of
       this function, the polish privacy floor included, follows.

    A stranger's plugin that declares nothing answers ``False``. That is the
    honest answer rather than the safe one — we cannot prove somebody else's
    recognizer is local — and it is what the code already assumed before this
    function existed. A caller for whom the mistake is expensive fails closed
    on its own side (:func:`jarvis.dictation.polish_client.stt_runs_on_device`).

    Cached per name: the answer cannot change while the process runs, and step
    3 imports the plugin module to read the attribute.
    """
    name = (provider_name or "").strip()
    if not name:
        return False
    if name in _STT_SECRET_CANDIDATES:
        return False
    if name in _SHIPPED_ON_DEVICE_PROVIDERS:
        return True
    return bool(getattr(_load_provider_class(name), _ON_DEVICE_ATTR, False))


def stt_family_id(provider_name: str) -> str:
    """The CREDENTIAL family a provider id belongs to.

    "Family" is defined by the credential slot, never by the provider NAME
    (AP-21). What makes a fallback worthless is landing on a provider that draws
    on the SAME key as the one that just answered 429 / 402 / 401: a depleted or
    rejected credential is not rescued by a sibling id that reads the same
    keyring entry. Two ids that resolve the same primary secret are therefore
    ONE family, however differently they are spelled — which is exactly the
    trap ``openrouter`` (brain) vs ``openrouter-stt`` (STT) would set for a
    naive name comparison.

    An engine that transcribes on this machine is the ``local`` family — it has
    no credential to exhaust — and which engines those are is decided by
    :func:`provider_runs_on_device`, not by a name written down here.

    Unknown / third-party ids (absent from ``_STT_SECRET_CANDIDATES`` and
    declaring no on-device capability) are each their own family: we cannot
    prove they share a credential with anything, and excluding them would
    silently drop a provider that works.
    """
    name = (provider_name or "").strip()
    if not name:
        return ""
    candidates = _STT_SECRET_CANDIDATES.get(name)
    if candidates:
        return candidates[0][0]
    if provider_runs_on_device(name):
        return "local"
    return name


def resolve_keyed_stt_fallback(
    current_id: str,
    *,
    exclude_family: str | Iterable[str] = (),
) -> tuple[str, ...]:
    """Cloud STT providers to cross to when ``current_id`` fails at CALL time.

    The runtime half of AP-22. ``_resolve_keyed_stt_provider`` above decides at
    BUILD time and only when a key is entirely MISSING; a provider that has a
    key and then returns 429 / 402 / 401 mid-session was, until this existed,
    the end of the transcription — even with three other keyed families sitting
    unused. Mirrors the TTS tier's ``resolve_keyed_fallback``
    (``jarvis/plugins/tts/__init__.py``), which the TTS plugins have called from
    their real error handlers for months.

    Returns provider ids, in the shipped cross-family order, that are ALL of:

    * from a different family than ``current_id`` (and than every entry in
      ``exclude_family`` — pass the families a session already burned so a long
      dictation does not walk back into one that is still rate-limited);
    * one entry per family, never two ids drawing on the same credential;
    * backed by a credential this host actually holds, AND registered as a
      ``jarvis.stt`` entry-point — we never promise a provider we cannot build.

    ``exclude_family`` accepts a single value or an iterable, and each value may
    be a provider id or a family id from :func:`stt_family_id`.

    An empty tuple is the honest answer, not an error: this user has exactly one
    keyed STT family and the caller must degrade the way it always has (an
    honest message, or the key-free local floor via ``_build_local_fallback`` /
    ``jarvis.speech.stt_fallback.alternate_provider_names``, which is the one
    option no quota can take away). The local engine is deliberately NOT in this
    chain — it is a floor, not a family, and it is absent on a base install.

    Names only, nothing built: constructing an alternate here would put a model
    load on whatever path called us (AP-26). The caller builds the one it needs
    via ``build_named_stt_provider`` and keeps it. The caller also owns the
    user-facing message; this function stays quiet so a per-failure call cannot
    turn into log spam.
    """
    excluded = {stt_family_id(current_id)}
    if isinstance(exclude_family, str):
        exclude_family = (exclude_family,)
    for name in exclude_family:
        family = stt_family_id(str(name))
        if family:
            excluded.add(family)
    excluded.discard("")

    chain: list[str] = []
    seen: set[str] = set()
    for cand in _STT_CROSS_FAMILY_ORDER:
        family = stt_family_id(cand)
        if family in excluded or family in seen:
            continue
        if not _stt_family_has_key(cand) or _load_provider_class(cand) is None:
            continue
        seen.add(family)
        chain.append(cand)
    logger.debug(
        "STT runtime fallback for {!r} (excluding {}): {}",
        current_id,
        sorted(excluded),
        ", ".join(chain) or "<none — this host has one keyed STT family>",
    )
    return tuple(chain)


def _load_provider_class(name: str) -> type | None:
    """Resolve an STT provider class by its entry-point ``name`` (e.g. ``groq-api``).

    The catalogue read is cached process-wide (``jarvis.core.entry_points``)
    because this is a hot path on the event loop: the cross-family chain calls
    this once per candidate it probes, and an uncached read is a sweep over
    every installed distribution — the call at the bottom of a captured 16.5 s
    loop stall.
    """
    from jarvis.core.entry_points import entry_points_for

    selected = entry_points_for(ENTRY_POINT_GROUP)
    for ep in selected:
        if ep.name == name:
            try:
                return ep.load()
            except (ImportError, ModuleNotFoundError) as exc:
                logger.warning(
                    "STT entry-point {!r} failed to load: {}", name, exc
                )
                return None
    return None


def build_stt_from_config(stt_cfg: Any) -> Any:
    """Return an STTProvider instance for ``stt_cfg.provider``.

    Falls back to a local FasterWhisperProvider if the configured provider has
    no entry-point or raises on construction.
    """
    provider_name = (getattr(stt_cfg, "provider", "") or "").strip()
    # The mirror image of the cross-family rule below: a user who deliberately
    # SELECTED the on-device engine can still arrive on a host where it is not
    # installed — a fresh machine, a rebuilt venv, a base install that carried
    # the config over. Building it anyway yields a provider that raises on the
    # first utterance, which reads to the user as "voice input is broken" with
    # no cause. Cross to a cloud family this host holds a key for instead
    # (AP-22); with no key anywhere the local path stands and
    # ``_maybe_hint_no_working_stt`` states the dead-end honestly.
    if provider_name == "faster-whisper" and not _faster_whisper_installed():
        alternatives = [
            name for name in available_stt_provider_names() if name != "faster-whisper"
        ]
        if alternatives:
            logger.warning(
                "Local STT is selected but the faster-whisper engine is not "
                "installed on this host; using {!r} instead. Install the local "
                "engine from the API-Keys view to switch back.",
                alternatives[0],
            )
            provider_name = alternatives[0]
    # Open-source AP-22: when the configured cloud STT has no usable key, cross to
    # a cloud STT family the user DOES have a key for BEFORE dropping to local
    # faster-whisper (which is not installed on a base/headless host). The
    # maintainer (whose configured key resolves) is unaffected; local whisper stays
    # the last-resort floor when no cloud family has a usable key.
    if provider_name and provider_name != "faster-whisper":
        provider_name = _resolve_keyed_stt_provider(provider_name)
    language = getattr(stt_cfg, "language", "auto")
    language = language if language and language != "auto" else None
    bias_prompt = (getattr(stt_cfg, "bias_prompt", "") or "").strip()
    # Merge the user's STT-dictionary words into the decoder bias for
    # prompt-capable providers (capability-gated: providers that reject the
    # kwarg keep working via the TypeError retry below, and every provider
    # still gets the dictionary's post-STT corrections — AP-21/22). The local
    # utterance fwhisper deliberately receives NO initial_prompt (silence-
    # hallucination risk, see _build_local_fallback).
    try:
        from jarvis.speech.stt_dictionary import dictionary_bias_words

        vocab = dictionary_bias_words()
    except Exception as exc:  # noqa: BLE001 — the dictionary must never break STT build
        logger.debug("STT dictionary bias words unavailable: {}", exc)
        vocab = []
    if vocab:
        joined = ", ".join(vocab)
        bias_prompt = f"{bias_prompt}, {joined}" if bias_prompt else joined

    cls = _load_provider_class(provider_name) if provider_name else None
    if cls is not None and provider_name != "faster-whisper":
        kwargs: dict[str, Any] = {}
        if language:
            kwargs["language"] = language
        if bias_prompt:
            kwargs["prompt"] = bias_prompt
        # The model the USER picked. Until this line existed, the STT model
        # picker wrote a value nothing read: every cloud plugin transcribed on
        # its own hardcoded default, so choosing a genuinely multilingual model
        # in the app changed exactly nothing (AP-31).
        chosen_model = resolve_stt_model(stt_cfg, provider_name)
        if chosen_model:
            kwargs["model"] = chosen_model
        # Reproducibility. A transcription is a measurement, so the same audio
        # must come back the same way twice; the providers whose model rejects
        # the field drop it themselves (jarvis.plugins.stt.capabilities).
        temperature = getattr(stt_cfg, "temperature", None)
        try:
            if temperature is not None:
                kwargs["temperature"] = float(temperature)
        except (TypeError, ValueError):
            logger.debug(
                "STT temperature {!r} is not a number; using the provider default.",
                temperature,
            )
        # Per-request timeout. Forwarded only when the config actually carries
        # one, so every provider keeps its own documented default otherwise. It
        # matters most for Gemini: google-genai FORCES ``timeout=None`` on its
        # HTTP client unless it is told otherwise, and its call runs in an
        # uncancellable thread — so without a value arriving here there is no
        # layer left that could bound the request (the pipeline's ``wait_for``
        # stops waiting, it cannot stop the thread).
        timeout_s = getattr(stt_cfg, "timeout_s", None)
        try:
            if timeout_s is not None and float(timeout_s) > 0:
                kwargs["timeout_s"] = float(timeout_s)
        except (TypeError, ValueError):
            logger.debug(
                "STT timeout_s {!r} is not a number; using the provider default.",
                timeout_s,
            )
        # Team-proxy mode (2026-06-20 spec §4): route the cloud STT through the
        # key proxy with the per-user token instead of the real vendor key. Only
        # groq-api (the cloud STT exposing `endpoint` + `api_key` constructor
        # args) is proxy-capable today; other providers fall through unchanged.
        # Direct mode injects nothing, so the provider keeps its own endpoint +
        # key resolution (behaviour unchanged).
        if provider_name == "groq-api":
            from jarvis.core import config as _cfg

            ep = _cfg.resolve_provider_endpoint("groq-api")
            if ep.via_proxy and ep.base_url:
                kwargs["endpoint"] = ep.base_url.rstrip("/") + "/audio/transcriptions"
                if ep.credential:
                    kwargs["api_key"] = ep.credential
        # An on-device recognizer gets only the options that mean something to
        # it. ``prompt`` (decoder bias) and ``timeout_s`` (per-REQUEST ceiling)
        # are shaped for a cloud call; passing them to a local engine hits the
        # TypeError retry below, which works but logs a misleading "this plugin
        # is out of date" warning about a plugin that is perfectly current.
        # Capability-gated via the plugin's own declaration, not its name
        # (AP-21). The user's STT dictionary still applies to these providers
        # through its post-transcription corrections.
        if provider_runs_on_device(provider_name):
            # ``temperature`` joins the cloud-only set for the same reason:
            # the on-device recognizers this repo ships configure their own
            # decoding, and offering it only earns the misleading "this plugin
            # is out of date" warning from the TypeError retry below. A model
            # PIN is not stripped — that is an explicit per-provider choice.
            for cloud_only in ("prompt", "timeout_s", "temperature"):
                kwargs.pop(cloud_only, None)
        if not _stt_has_credential(provider_name, kwargs):
            logger.warning(
                "STT provider {!r} has no usable credential; falling back to the "
                "key-free local faster-whisper so voice input still works for a "
                "single-key user (AP-22).",
                provider_name,
            )
            return _build_local_fallback(stt_cfg, language)
        try:
            instance = cls(**kwargs) if kwargs else cls()
            logger.info(
                "STT provider resolved via entry-point: {} (class {}, model={}, "
                "temperature={}, bias_prompt={} chars)",
                provider_name,
                cls.__name__,
                kwargs.get("model") or "<provider default>",
                kwargs.get("temperature", "<provider default>"),
                len(bias_prompt),
            )
            return instance
        except TypeError as exc:
            # The provider class refused one of our OPTIONAL kwargs — most
            # likely a third-party plugin that predates ``prompt`` or
            # ``timeout_s``. Retry with all of them dropped (we cannot tell from
            # a TypeError WHICH one offended) so a stale plugin still loads;
            # only ``language`` and the proxy pair are load-bearing.
            # (faster-whisper local path is handled below as the explicit
            # fallback.)
            refused = [k for k in _OPTIONAL_PROVIDER_KWARGS if k in kwargs]
            if refused:
                for key in refused:
                    kwargs.pop(key, None)
                logger.warning(
                    "STT provider {!r} does not accept {} yet ({}); retrying "
                    "without those.",
                    provider_name,
                    " / ".join(refused),
                    exc,
                )
                try:
                    return cls(**kwargs) if kwargs else cls()
                except Exception as inner_exc:  # noqa: BLE001
                    logger.warning(
                        "STT provider {!r} init still failed ({}); falling back to faster-whisper",
                        provider_name,
                        inner_exc,
                    )
            else:
                logger.warning(
                    "STT provider {!r} init failed ({}); falling back to faster-whisper",
                    provider_name,
                    exc,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "STT provider {!r} init failed ({}); falling back to faster-whisper",
                provider_name,
                exc,
            )

    # Local fallback (also the explicit "faster-whisper" path).
    return _build_local_fallback(stt_cfg, language)


def available_stt_provider_names() -> list[str]:
    """Every provider this host could actually transcribe with, right now.

    "Could" is a CREDENTIAL + INSTALL question, never a config one: the answer
    is the menu the runtime fallback chain picks from when the configured
    provider starts failing (``jarvis.speech.stt_fallback``), so a name in here
    has to be one that would really work. Cloud families come in the
    cross-family order, the key-free local engine last and only when it is
    installed.
    """
    names: list[str] = []
    for name in _STT_CROSS_FAMILY_ORDER:
        if _stt_family_has_key(name) and _load_provider_class(name) is not None:
            names.append(name)
    if _faster_whisper_installed():
        names.append("faster-whisper")
    return names


def build_named_stt_provider(name: str, stt_cfg: Any) -> Any:
    """Build the provider called ``name`` using ``stt_cfg`` for everything else.

    Language, bias prompt, dictionary vocabulary and the team-proxy handling all
    have to match what the configured provider got — a fallback that quietly
    drops the user's vocabulary would transcribe their words differently the
    moment it took over. So this reuses :func:`build_stt_from_config` on a copy
    of the config rather than re-deriving any of it.
    """
    try:
        patched = stt_cfg.model_copy(update={"provider": name})
    except AttributeError:
        # Not a pydantic model (test doubles); a tiny view is enough.
        patched = _StttConfigView(stt_cfg, name)
    return build_stt_from_config(patched)


class _StttConfigView:
    """Read-only ``stt_cfg`` with ``provider`` swapped — for non-pydantic doubles."""

    def __init__(self, inner: Any, provider: str) -> None:
        self._inner = inner
        self.provider = provider

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


# One-time gate so the "no working STT" hint is logged at most once per process
# (the STT provider is rebuilt whenever a key changes; the hint must not repeat).
_NO_STT_HINT_EMITTED = False


def _faster_whisper_installed() -> bool:
    """True iff the local ``faster_whisper`` engine is importable on this host.

    A cheap spec probe (no heavy import): ``fwhisper`` imports ``faster_whisper``
    lazily, so a base/headless install builds a ``FasterWhisperProvider`` fine and
    only fails on the FIRST transcription. This lets the hint below detect the
    genuine dead-end (no cloud key AND no local engine) before that happens.
    """
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


def _any_cloud_stt_key() -> bool:
    """True iff the host has a usable key for ANY known cloud STT family."""
    return any(_stt_family_has_key(name) for name in _STT_CROSS_FAMILY_ORDER)


def _maybe_hint_no_working_stt() -> None:
    """Log a one-time, actionable English hint when NO STT can work here.

    Fires only when BOTH are true: no cloud STT key resolves (Groq / OpenRouter /
    OpenAI / Gemini) AND the local ``faster-whisper`` engine is not installed — the
    genuine dead-end where voice input silently cannot work. Log-only: it never
    changes the graceful local-fallback behaviour, and a host that has either a
    cloud key or the local engine (the maintainer, a keyed downloader) never sees
    it. The probe is wrapped defensively so a config hiccup can never break the
    STT build.
    """
    global _NO_STT_HINT_EMITTED
    if _NO_STT_HINT_EMITTED:
        return
    try:
        if _any_cloud_stt_key() or _faster_whisper_installed():
            return
    except Exception as exc:  # noqa: BLE001 — a hint probe must never break STT build
        logger.debug("STT self-recovery hint probe failed ({}); skipping hint.", exc)
        return
    _NO_STT_HINT_EMITTED = True
    logger.warning(
        "No speech-to-text is available on this host: no cloud STT key is "
        "configured (Groq / OpenRouter / OpenAI / Gemini) and the local "
        "faster-whisper engine is not installed, so voice input cannot work. "
        "To fix it in-app: add a Groq, OpenRouter, OpenAI, or Gemini key in the "
        "API-Keys view. Or install the local engine via the '[full]' or "
        "'[local-voice]' extra (e.g. pip install 'personal-jarvis[full]')."
    )


def _build_local_fallback(stt_cfg: Any, language: str | None) -> Any:
    """Construct the key-free local faster-whisper provider (the universal floor)."""
    from jarvis.core.device import resolve_device
    from jarvis.plugins.stt.fwhisper import FasterWhisperProvider

    # Honest self-recovery hint (log-only, does not change the fallback): when
    # this host has neither a cloud STT key nor the local engine, voice input
    # cannot work — tell the user exactly how to fix it in-app instead of only
    # the generic spoken apology later.
    _maybe_hint_no_working_stt()

    # Central CPU-first device resolution (ADR-0024). The utterance provider is
    # latency-tolerant, so its capability verdict is left unverified (None): an
    # explicit ``device = "cuda"`` in jarvis.toml is the user's opt-in and is
    # honored, while ``auto`` / empty / unknown resolve to the cloud-first CPU
    # floor — a stranger with no NVIDIA GPU is the baseline, not the maintainer's
    # card. fwhisper's construction-time self-heal remains the runtime safety net.
    device = resolve_device(
        getattr(stt_cfg, "device", "cpu"),
        cuda_usable=None,
        purpose="stt-utterance",
    ).device
    return FasterWhisperProvider(
        model=getattr(stt_cfg, "model", "distil-large-v3"),
        device=device,
        compute_type=getattr(stt_cfg, "compute_type", "int8"),
        language=language,
    )


def _wake_cuda_cache_path() -> Path:
    """Location of the persisted CUDA-availability probe result.

    CUDA presence is a stable hardware fact, so the probe result is cached ACROSS
    process restarts (not just in-process). Honours the same data-dir env seam the
    rest of the app uses (``JARVIS__MEMORY__DATA_DIR``); defaults to ``./data``
    relative to the project-root CWD.
    """
    base = os.environ.get("JARVIS__MEMORY__DATA_DIR") or "data"
    return Path(base) / "wake_cuda_probe.json"


@lru_cache(maxsize=1)
def _wake_cuda_available() -> bool:
    """True iff a CUDA device is usable by the CTranslate2 / faster-whisper backend.

    Cached in-process (``lru_cache``) AND persisted to disk. The FIRST CUDA call
    in a process (``ctranslate2.get_cuda_device_count``) initializes the CUDA
    context, which on a Blackwell (sm_120) GPU JIT-compiles kernels and costs
    ~30-60 s (measured). ``build_wake_whisper`` runs this SYNCHRONOUSLY on the
    desktop boot path to choose the wake model, so the probe used to freeze voice
    boot ("VOICE STARTING…") for up to a minute on EVERY launch.

    Persisting the boolean across restarts removes the probe from the boot path on
    every boot after the first; the (unavoidable, one-time-per-process) CUDA
    context init then happens later, during the already-backgrounded wake-model
    load — never on the wake-ready path. Delete ``data/wake_cuda_probe.json`` to
    force a re-probe after a GPU/driver change. Any import/probe error is treated
    as "no CUDA" so a host without the GPU stack degrades to the cloud-first CPU
    default. AP-21: gate on the capability, never a hardware name.
    """
    cache_path = _wake_cuda_cache_path()

    # 1) Persisted result — skip the expensive probe on every boot after the first.
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(cached, dict) and isinstance(cached.get("cuda"), bool):
            logger.info(
                "Wake-CUDA probe: cache HIT ({}) — probe skipped.",
                "available" if cached["cuda"] else "absent",
            )
            return cached["cuda"]
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 — a corrupt cache must never break boot
        logger.debug("Wake-CUDA probe cache unreadable ({}); re-probing.", exc)

    # 2) Cold path — pay the probe ONCE, log how long it took, then persist it.
    t0 = time.perf_counter()
    try:
        # Shield the ctranslate2 import from its transformers+torch converter
        # stack (inference/probe needs neither) — ~2.9 s warm / ~14 s cold saved.
        from jarvis.plugins.stt.fwhisper import inference_only_import_shield

        with inference_only_import_shield():
            import ctranslate2

        available = ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 - any failure means "treat as no GPU"
        available = False
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    logger.info(
        "Wake-CUDA probe: cache MISS — probed in {:.0f} ms -> CUDA {}.",
        elapsed_ms,
        "available" if available else "absent",
    )
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"cuda": available}), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — caching is best-effort
        logger.debug("Wake-CUDA probe cache write failed ({}).", exc)
    return available


def _wake_gpu_probe_cache_path() -> Path:
    """Location of the persisted GPU wake-inference probe result.

    Separate from ``wake_cuda_probe.json`` (mere CUDA *presence*): this file
    records whether a real ``large-v3-turbo`` CUDA *inference* completed on this
    host — presence and usability diverged in AP-25 (a Blackwell sm_120 box HAD
    CUDA but every CTranslate2 inference hung under the then-current runtime).
    Delete the file to force a re-probe after a GPU/driver change.
    """
    base = os.environ.get("JARVIS__MEMORY__DATA_DIR") or "data"
    return Path(base) / "wake_gpu_probe.json"


# Success marker the probe subprocess prints AFTER its real CUDA inferences.
# The probe verdict is this marker in stdout, NEVER the exit code: a CUDA
# process can die in native teardown (observed exit 127 on 2026-07-05) after
# doing all its work correctly.
_WAKE_GPU_PROBE_MARKER = "WAKE_GPU_PROBE_OK"

# Generous ceiling for ONE probe run: a cold Blackwell kernel-JIT model load
# was measured at ~71 s; a healthy warm load + two inferences is < 15 s. A
# probe that cannot finish inside this window is exactly the AP-25 hang the
# probe exists to catch.
_WAKE_GPU_PROBE_TIMEOUT_S = 180.0

# The probe exercises the SAME model the CUDA upgrade branches below swap in,
# in the SAME process constellation the live hot-swap runs in: torch loaded
# first (best effort). This matters twice over — (a) on this host
# cublas64_12.dll ships ONLY inside torch\lib and becomes loadable when
# torch's import registers its DLL directory (in the live app Silero VAD has
# done that long before the hot-swap; without it CTranslate2 raises
# "Library cublas64_12.dll is not found", measured 2026-07-05), and (b) the
# AP-25 hang class was specifically ctranslate2 *coexisting* with torch's
# OpenMP in one process, so probing without torch would test the wrong thing.
_WAKE_GPU_PROBE_SCRIPT = r"""
try:
    import torch  # noqa: F401 — mirror the live app: torch (Silero VAD) is loaded first
except Exception:
    pass
import numpy as np
from faster_whisper import WhisperModel
m = WhisperModel("large-v3-turbo", device="cuda", compute_type="int8_float16")
pcm = (np.random.default_rng(0).standard_normal(19200) * 0.05).astype("float32")
for _ in range(2):
    segments, _info = m.transcribe(pcm, language="de", beam_size=1)
    list(segments)  # consume the generator — this is what runs the inference
print("WAKE_GPU_PROBE_OK", flush=True)
"""


def _ctranslate2_version() -> str:
    """Installed ctranslate2 version (metadata only — no heavy import)."""
    try:
        return importlib_metadata.version("ctranslate2")
    except Exception:  # noqa: BLE001 — absent/broken install means "unknown"
        return "unknown"


def _run_wake_gpu_probe_subprocess() -> bool:
    """Run one real turbo/cuda inference in a KILLABLE child process.

    Out-of-process on purpose (BUG-036/AP-24): a hung native inference thread
    inside THIS process could never be cancelled and would poison the app; a
    child process is killed on timeout and leaves no residue. Blocking — only
    ever called from the background hot-swap thread, never the boot path.
    """
    import subprocess
    import sys

    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, our own interpreter
            [sys.executable, "-c", _WAKE_GPU_PROBE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=_WAKE_GPU_PROBE_TIMEOUT_S,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Wake-GPU probe: inference did not finish within {:.0f} s — the "
            "AP-25 hang signature. GPU wake upgrade stays OFF on this host.",
            _WAKE_GPU_PROBE_TIMEOUT_S,
        )
        return False
    except Exception as exc:  # noqa: BLE001 — a probe must never break the caller
        logger.warning("Wake-GPU probe: could not launch ({}).", exc)
        return False
    ok = _WAKE_GPU_PROBE_MARKER in (proc.stdout or "")
    if not ok:
        tail = ((proc.stderr or proc.stdout or "").strip())[-300:]
        logger.warning(
            "Wake-GPU probe: no success marker (exit={}). Tail: {}",
            proc.returncode,
            tail or "<empty>",
        )
    return ok


@lru_cache(maxsize=1)
def _wake_gpu_inference_verified() -> bool:
    """True iff a real turbo/cuda wake inference VERIFIABLY completes here.

    This is the capability gate (AP-21) that replaced the blanket AP-25
    "GPU turbo off by default": CUDA presence alone is not enough — on one
    Blackwell host every CTranslate2 inference hung under the then-current
    runtime while ``get_cuda_device_count()`` was happily > 0. The verdict is
    cached on disk keyed by the ctranslate2 version (a runtime upgrade that
    may fix — or break — the hang triggers exactly one re-probe).

    BLOCKING (up to ``_WAKE_GPU_PROBE_TIMEOUT_S`` on a cache miss): call it
    only from the background hot-swap (``fast_first=False`` builds), never on
    the boot / hear-ready path (AP-26).
    """
    ct2_version = _ctranslate2_version()
    cache_path = _wake_gpu_probe_cache_path()
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if (
            isinstance(cached, dict)
            and isinstance(cached.get("ok"), bool)
            and cached.get("ctranslate2") == ct2_version
        ):
            logger.info(
                "Wake-GPU probe: cache HIT ({}, ctranslate2 {}) — probe skipped.",
                "verified" if cached["ok"] else "unusable",
                ct2_version,
            )
            return cached["ok"]
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 — a corrupt cache must never break the swap
        logger.debug("Wake-GPU probe cache unreadable ({}); re-probing.", exc)

    t0 = time.perf_counter()
    ok = _run_wake_gpu_probe_subprocess()
    logger.info(
        "Wake-GPU probe: real turbo/cuda inference {} in {:.1f} s "
        "(ctranslate2 {}).",
        "VERIFIED" if ok else "FAILED",
        time.perf_counter() - t0,
        ct2_version,
    )
    _persist_wake_gpu_probe(ok, ct2_version)
    return ok


def wake_gpu_probe_cached() -> bool | None:
    """Return the PERSISTED GPU-inference verdict for this host, or ``None``.

    Non-blocking companion to :func:`_wake_gpu_inference_verified` (which BLOCKS
    on a cache miss to run one real turbo/cuda inference). Reads only the verdict
    already written to disk by a prior probe or the live backstop; it NEVER
    launches the probe subprocess. Returns:

    - ``True`` / ``False`` when a verdict for the CURRENTLY installed ctranslate2
      version is cached;
    - ``None`` when no verdict exists here, the cache is unreadable, or the cached
      verdict was written under a DIFFERENT ctranslate2 version (a runtime upgrade
      can fix — or re-introduce — the AP-25 hang, so a stale verdict is untrusted).

    Off-critical-path callers (the first-run hardware recommender) use this to gate
    a GPU recommendation on a REAL, verified inference (AP-21/AP-25) instead of mere
    CUDA presence, while paying nothing when the host has never probed. AP-26-safe:
    a pure file read, never the blocking probe.
    """
    ct2_version = _ctranslate2_version()
    try:
        cached = json.loads(_wake_gpu_probe_cache_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 — a corrupt cache must never break the caller
        logger.debug("Wake-GPU probe cache unreadable ({}); treating as unprobed.", exc)
        return None
    if (
        isinstance(cached, dict)
        and isinstance(cached.get("ok"), bool)
        and cached.get("ctranslate2") == ct2_version
    ):
        return cached["ok"]
    return None


def _persist_wake_gpu_probe(ok: bool, ct2_version: str | None = None) -> None:
    """Best-effort write of the probe verdict (shared by probe + bad-mark)."""
    cache_path = _wake_gpu_probe_cache_path()
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "ok": ok,
                    "ctranslate2": ct2_version or _ctranslate2_version(),
                    "model": "large-v3-turbo",
                }
            ),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001 — caching is best-effort
        logger.debug("Wake-GPU probe cache write failed ({}).", exc)


def mark_wake_gpu_bad() -> None:
    """Record that the LIVE GPU wake model wedged — future builds stay on CPU.

    Runtime backstop for a hang the one-off probe missed: the rolling wake's
    self-heal calls this right before swapping back to its base/cpu fallback,
    so the very next build (and every restart) skips the GPU upgrade until the
    ctranslate2 version changes (which re-keys the cache and re-probes).
    """
    _persist_wake_gpu_probe(False)
    _wake_gpu_inference_verified.cache_clear()
    logger.warning(
        "Wake-GPU probe: marked UNUSABLE after a live wedge — wake stays on "
        "the base/cpu model until the ctranslate2 runtime changes."
    )


def build_wake_whisper(
    stt_cfg: Any,
    *,
    language: str | None = None,
    wake_phrase: str | None = None,
    cuda_available: bool | None = None,
    fast_first: bool = False,
) -> Any:
    """Build the LOCAL wake-match / live-preview Whisper.

    Distinct from :func:`build_stt_from_config` (the post-wake *utterance* STT,
    often a cloud provider). This instance only powers wake-phrase transcript
    matching + the listening-bubble probe — both latency-tolerant — so it loads
    a small model on CPU by default (``stt_cfg.wake_model`` / ``wake_device`` /
    ``wake_compute_type``), NOT the heavy utterance model on the GPU.

    Why this matters for boot: on a Blackwell GPU (RTX 50xx) CTranslate2 JIT-
    compiles kernels at model load, costing ~71 s on CUDA vs ~0.45 s for ``base``
    on CPU (measured) — the dominant Phase-A warm-up cost. CPU is also the
    cloud-first floor. ``getattr`` fallbacks keep a pre-wake_*-field config (or a
    bare stub) building a safe small/cpu instance.

    ``wake_phrase`` (forensic 2026-06-22): when a user sets a CUSTOM wake word
    with no pretrained openWakeWord model ("Hey Ruben"), the wake routes to this
    small CPU model. ``base`` transcribed the proper noun as a common word
    ("Ruben" -> "job") so the wake never fired. Passing the spoken trigger here
    seeds Whisper's ``initial_prompt`` so it biases toward the actual name. This
    is deliberately scoped to the custom stt_match wake (the pipeline only
    forwards a phrase on that path) — the default "Hey Jarvis"/OWW paths pass
    nothing, so the hot-path prompt-hallucination caveat in
    ``FasterWhisperProvider.__init__`` does not apply to them.

    Bias is ON (forensic 2026-06-23). It was once disabled out of a hallucination
    concern, but that disabled the custom wake word entirely: empirically, on the
    user's real wake WAVs the unbiased base/cpu model heard "Hey Ruben" as
    "Space"/"Ego"/"Herum" -> 2-13% recall; seeding ``wake_phrase`` as the
    ``initial_prompt`` lifts that to 83%. The earlier false-wake risk is held off
    by the strict ["hey","ruben"] matcher (a stray "Ruben" in ordinary speech is
    not an adjacent "hey ruben") plus the ``no_speech_prob``/RMS gates, which kept
    the false-wake rate ~0% on 50 real talking-about-Ruben clips. The bias is
    scoped to this path: only the stt_match custom-phrase route forwards a
    ``wake_phrase``; the default "Hey Jarvis"/OWW paths pass nothing and stay
    unbiased, so the hot-path prompt-hallucination caveat in
    ``FasterWhisperProvider.__init__`` does not apply to them. A blank phrase is
    treated as no bias.
    """
    from jarvis.plugins.stt.fwhisper import FasterWhisperProvider, _bound_ct2_threads

    # Bound the ctranslate2/OpenMP CPU thread pool at the environment level
    # BEFORE the wake FasterWhisperProvider is constructed, so it is set
    # ahead of ctranslate2's first import on this path (AP-24/AP-25/BUG-036,
    # defensive only — see _bound_ct2_threads docstring). Never clobbers an
    # explicit user setting.
    _bound_ct2_threads(default=2)

    model = getattr(stt_cfg, "wake_model", "base")
    device = getattr(stt_cfg, "wake_device", "cpu")
    compute = getattr(stt_cfg, "wake_compute_type", "int8")
    bias = wake_phrase.strip() if wake_phrase and wake_phrase.strip() else None

    # Capability-gated GPU upgrade (forensic 2026-06-24, validated on the user's
    # real wake WAVs). On the cloud-first CPU defaults (base/cpu) AND a CUDA
    # device, transcribe the wake on the GPU with a fast MULTILINGUAL turbo model
    # AND DROP THE BIAS. The strong model hears the proper noun WITHOUT the
    # ``initial_prompt`` hint, so the wake stays fast (~150 ms vs ~1.4 s on
    # base/cpu) while it does NOT hallucinate the primed phrase onto quiet
    # silence — the bias on the strong model was the false-wake source ("Vielen
    # Dank." silence artifact -> "Hey Ruben"). Offline-validated: no-bias turbo is
    # 0 false-wakes across the user's silence / own-speech / other-wake clips,
    # while a clearly spoken wake still fires. The WEAK base/cpu model still NEEDS
    # the bias to hear the name, so the bias is kept ONLY there. Only the
    # stt_match custom-phrase path on a GPU box is affected; the bundled
    # "Hey Jarvis"/openWakeWord path and every CPU/VPS host are untouched. An
    # explicit wake_model/wake_device wins (only the base/cpu pair auto-upgrades).
    if cuda_available is None:
        cuda_available = _wake_cuda_available()
    # ``fast_first`` (progressive wake-model boot, 2026-06-27): when set, SKIP the
    # GPU turbo upgrade and return the light base/cpu model (with bias). It loads
    # in ~3 s with NO CUDA JIT (vs large-v3-turbo/cuda ~11 s warm in the boot
    # storm and ~71 s cold), so a CUSTOM wake phrase becomes hear-ready almost
    # immediately, even on a cold kernel cache. base/cpu+bias is a validated wake
    # model (83% recall / ~0% false on the user's real WAVs — see the bias note
    # above). The caller then hot-swaps in the turbo/cuda model in the background
    # for faster steady-state inference, so the 2026-06-24 accuracy upgrade is
    # preserved — only its load is moved off the hear-ready path.
    # Both CUDA branches additionally require the one-time out-of-process
    # INFERENCE probe (``_wake_gpu_inference_verified``): CUDA presence alone
    # proved insufficient in AP-25 (a Blackwell box had CUDA but every
    # CTranslate2 inference hung under the then-current runtime). The probe is
    # blocking on its first run, which is safe here: ``fast_first`` builds
    # (the boot path) never reach it, so it only ever runs inside the
    # background hot-swap. Short-circuit order keeps it last — a CPU-only or
    # opted-out host never pays for it.
    if (
        not fast_first
        and not bias
        and model == "base"
        and device == "cpu"
        and cuda_available
        and bool(getattr(stt_cfg, "wake_high_accuracy", False))
        and _wake_gpu_inference_verified()
    ):
        model, device, compute = "large-v3-turbo", "cuda", "int8_float16"
        bias = None  # strong model needs no bias; the bias is what hallucinates
        logger.info(
            "Wake-Whisper: CUDA inference verified -> GPU turbo "
            "(large-v3-turbo/cuda), bias OFF (fast + no silence hallucination)."
        )
    elif (
        not fast_first
        and bias
        and model == "base"
        and device == "cpu"
        and cuda_available
        and bool(getattr(stt_cfg, "wake_high_accuracy", False))
        and _wake_gpu_inference_verified()
    ):
        # CUSTOM WAKE PHRASE on a CUDA box: upgrade to the strong turbo model on
        # the GPU BUT KEEP the phrase bias. Mission 2026-06-30, live-log evidence
        # (data/jarvis_desktop.log): the base/cpu model WEDGED repeatedly ("5
        # consecutive transcribe failures -> rebuilding the wedged wake model" —
        # up to 40 s of total deafness) and mis-transcribed the wake name under
        # app CPU/GIL contention, so the wake needed 2-3 tries. The turbo model on
        # the GPU transcribes a ~1.8 s window in ~150 ms, so it NEVER blows the
        # transcribe timeout (this is what eliminates the wedge) and hears the
        # proper noun accurately. The bias is KEPT (turbo WITHOUT bias mangles a
        # short custom phrase — "Hey Nico" -> "cuf ich"); the earlier "bias
        # hallucinates the phrase onto SILENCE on the strong model" concern does
        # NOT apply here because the rolling wake path only transcribes windows
        # that already passed its rms/peak gates — it never feeds a silent window
        # to the model, and the no_speech_prob + confidence + strict-adjacency
        # matcher gates remain the false-wake guards. The utterance STT is a
        # separate provider (often cloud), so the wake owning the GPU does not
        # contend with it. Reversible: wake_high_accuracy=False forces base/cpu.
        model, device, compute = "large-v3-turbo", "cuda", "int8_float16"
        logger.info(
            "Wake-Whisper: custom phrase, CUDA inference verified -> GPU turbo "
            "(large-v3-turbo/cuda) WITH bias — fast (~150 ms, no wedge) + "
            "accurate. Set [stt].wake_high_accuracy=false to force base/cpu."
        )
    elif fast_first and model == "base" and device == "cpu" and cuda_available:
        logger.info(
            "Wake-Whisper: fast-first base/cpu (bias ON) — turbo/cuda upgrade "
            "deferred to a background hot-swap so wake is hear-ready in ~3 s."
        )

    return FasterWhisperProvider(
        model=model,
        device=device,
        compute_type=compute,
        language=language,
        initial_prompt=bias,
        # Greedy decoding for the wake: a short phrase on an always-on loop does
        # not need beam search, and beam_size=1 transcribes a window ~3-5x faster
        # on base/cpu — far less likely to blow the wedge timeout under app CPU
        # load, and snappier. The phrase bias + sound-folding matcher keep recall.
        beam_size=1,
        # A FIXED, small ctranslate2 thread count for the wake model (never
        # auto/all-cores — that deadlocks against PyTorch's OpenMP pool in the
        # shared process, the 2026-06-30 8 s ``model.transcribe`` hang, AP-24/25).
        # History: auto -> hang storm; 4 -> reduced (11 wedges / 16 min); 1 ->
        # no deadlock but ~2x slower per window, and one merely SLOW call under
        # load still blew the 8 s poll cap (p95 was measured at 7.5 s under
        # contention on 2026-07-02 — the wedge-cascade trigger). 2026-07-02
        # measurements: cpu_threads=2 transcribes a 1.8 s window 1.7-2.8x
        # faster than 1 (median 706 ms vs 2003 ms same-load matrix; 1718 ms vs
        # 2960 ms under a deliberate 3-thread torch-OpenMP burn) with ZERO
        # hangs in the 80-round torch-coexistence probe, identical recall, and
        # it halves the wake trigger latency. Should a rare hang still occur in
        # the wild, the poll loop now self-heals it in bounded time without the
        # old teardown cascade (busy-streak accounting + off-path re-warm,
        # commit 9a4da695) — the failure economics that made 1 the only safe
        # choice no longer apply. num_workers stays 1 (single inference
        # stream). NOTE: transcription wake still cannot reach KWS-instant
        # latency — the definitive fix remains a neural KWS model (AP-25).
        cpu_threads=2,
        # ONE greedy pass per wake window — drop Whisper's 6-step temperature
        # fallback ladder here. The ladder only re-decodes transcripts that
        # already look degenerate, which the phrase matcher and the
        # ``no_speech_prob``/RMS gates reject anyway, while a genuine wake is
        # decided on the first pass. Unbounded it made a single window cost up
        # to 7.7 s on a FAST box (measured) — past the 8 s abandon cap on weaker
        # hardware, where the timeout drops the model and forces a cold rebuild,
        # extending the deaf gap. Bounding the work changes no gate and no
        # transcript rule, so AP-27 is untouched. ``without_timestamps`` is
        # deliberately NOT set alongside it: collapsing the window to a single
        # segment would change what ``_reliable_wake_transcript``'s per-segment
        # ``no_speech_prob`` check sees, i.e. shift the ghost/recall coupling on
        # the very engine AP-27 was written for.
        temperature=0.0,
    )


def start_wake_model_prefetch(
    stt_cfg: Any,
    *,
    language: str | None = None,
    wake_phrase: str | None = None,
) -> Any:
    """Load the fast-first wake Whisper MODEL in a daemon thread, off the boot
    critical path (TTU iteration 10, docs/diagnostics/BOOT-TTU-NOTES.md).

    Drift-free by construction: the exact model parameters are resolved by
    building a throwaway provider through :func:`build_wake_whisper`
    (``fast_first=True`` — the same call the boot path makes; the ctor is
    cheap and fully lazy), then the weights are loaded into the hand-over
    cache that ``FasterWhisperProvider._ensure_model`` adopts. The phrase
    bias and language do not participate in the cache key (they are decode
    parameters, not load parameters), so a key can only miss if the real
    boot resolves a different model/device — in which case the consumer
    simply loads lazily as before.

    GIL note (2026-06-22 forensic): the load runs behind the
    ``inference_only_import_shield`` inside ``_new_whisper_model`` and touches
    no torch; the first real torch import (Silero VAD) happens seconds later
    in the deferred loaders, so the shield race window stays clear.

    No-op when voice is disabled (``JARVIS_VOICE``) or on any failure —
    a prefetch must never break boot. Returns the thread or ``None``.
    """
    import threading

    from jarvis.speech.warmup_prefetch import _voice_disabled

    if _voice_disabled():
        return None

    def _load() -> None:
        try:
            from jarvis.plugins.stt.fwhisper import prefetch_model

            probe = build_wake_whisper(
                stt_cfg,
                language=language,
                wake_phrase=wake_phrase,
                fast_first=True,
            )
            prefetch_model(
                probe._model_name,  # noqa: SLF001 — resolved params, same module family
                probe._device,  # noqa: SLF001
                probe._compute_type,  # noqa: SLF001
                probe._cpu_threads,  # noqa: SLF001
            )
        except Exception as exc:  # noqa: BLE001 — a prefetch must never break boot
            logger.debug("Wake-model prefetch skipped: {}", exc)

    thread = threading.Thread(target=_load, name="wake-model-prefetch", daemon=True)
    thread.start()
    return thread


__all__ = [
    "available_stt_provider_names",
    "build_named_stt_provider",
    "build_stt_from_config",
    "build_wake_whisper",
    "mark_wake_gpu_bad",
    "provider_runs_on_device",
    "resolve_keyed_stt_fallback",
    "resolve_stt_model",
    "start_wake_model_prefetch",
    "stt_family_id",
    "wake_gpu_probe_cached",
]
