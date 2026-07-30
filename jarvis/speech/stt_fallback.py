"""Runtime STT fallback — cross to another provider family when one fails.

``build_stt_from_config`` already picks a different provider when the configured
one has no usable credential. That is a BUILD-time decision, and it was the only
one there was: once a provider was chosen, a failure at CALL time ended the
transcription. So a Groq rate limit in the middle of a dictation deleted the
user's words even though three other keyed providers were sitting there unused
(live bug 2026-07-29: a 137 s dictation returned 367 characters, the log full of
``429 Too Many Requests``).

That is exactly what AP-22 forbids — "a missing / depleted / 429 / 402 /
unreachable one degrades or crosses to a **different family** with an honest
message, never bricking a core path". This module supplies the missing half.

What it does NOT do, on purpose:

* **It never retries a rejection the audio itself caused.** A 400 means this
  provider understood the request and refused it; sending the same bytes to
  another provider would just burn a second quota for the same answer. Only
  failures another provider could plausibly survive cross over — the quota,
  availability and transport ones enumerated in :func:`_is_crossable`.
* **It never builds an alternate on the boot path** (AP-26). The chain is a
  list of provider NAMES; an instance is constructed the first time it is
  actually needed and then kept.
* **It never reorders the user's choice.** The configured provider is always
  tried first, except while it is in the cooldown a rate limit put it in —
  which is the whole point: hammering a 429 keeps it locked.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

log = logging.getLogger(__name__)

#: How long a provider stays skipped after it reported a quota failure. Long
#: enough that a per-minute limit has actually rolled over, short enough that a
#: user who fixed their billing is not stuck on the fallback all session.
RATE_LIMIT_COOLDOWN_S = 60.0

#: Cooldown for the failures that are not about quota (5xx, transport). Shorter:
#: these clear on their own and the user's configured provider should come back
#: as soon as it can.
TRANSIENT_COOLDOWN_S = 15.0

#: Reasons whose cooldown is the LONG one. A quota window and a rejected key
#: both need real time to change; a 5xx or a dropped connection does not.
_LONG_COOLDOWN_REASONS = frozenset({"rate_limited", "no_credit", "bad_key"})


def _classify(exc: BaseException) -> str:
    """The failure reason when another provider deserves a shot, else ``""``.

    Delegates to :func:`jarvis.speech.stt_failure.classify_stt_failure` rather
    than keeping a second marker table. The same judgement is what the dictation
    history stores and what the UI translates, and two tables that must agree
    are two tables that will not (AP-4).

    A ``rejected`` (400-class) answer deliberately returns ``""``: the provider
    understood the request and refused it, so another one would refuse the same
    bytes and only burn a second quota to say so.
    """
    from jarvis.speech.stt_failure import classify_stt_failure, is_crossable_failure

    reason = classify_stt_failure(exc)
    return reason if is_crossable_failure(reason) else ""


def _is_crossable(exc: BaseException) -> bool:
    """Whether another provider deserves a shot at this same audio."""
    return bool(_classify(exc))


class FallbackSTT:
    """One STT handle that crosses to another provider on a runtime failure.

    Transparent decorator, same shape as ``DictionaryCorrectingSTT``: every
    attribute that is not a transcribe method delegates to whichever provider is
    currently in front, so duck-typed callers (``recover()``, ``is_warm``, model
    fields) keep working.

    ``alternates`` are provider NAMES, not instances — see the module docstring
    on why nothing here is built until it is needed.
    """

    def __init__(
        self,
        primary: Any,
        alternates: Sequence[str],
        build: Any,
        *,
        primary_name: str = "",
    ) -> None:
        self._primary = primary
        self._primary_name = primary_name or type(primary).__name__
        self._alternate_names = list(alternates)
        self._build = build
        # name -> instance, built on first use and kept for the process.
        self._instances: dict[str, Any] = {}
        # name -> monotonic deadline before which we do not try it again.
        self._cooldown: dict[str, float] = {}
        # The alternate that last worked, tried first among the alternates so a
        # long dictation does not re-walk the whole chain on every segment.
        self._preferred: str | None = None

    # ------------------------------------------------------------------
    # Delegation
    # ------------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        return getattr(self._primary, name)

    def __repr__(self) -> str:  # pragma: no cover — logging nicety
        return f"FallbackSTT({self._primary_name} -> {self._alternate_names})"

    @property
    def provider_label(self) -> str:
        return self._primary_name

    @property
    def last_used_provider(self) -> str:
        """Which provider produced the most recent transcript."""
        return self._last_used

    @property
    def last_used_model(self) -> str:
        """Model exposed by the provider that produced the latest transcript.

        Alternates are constructed lazily, so an unused one has no model to
        report. The common attribute spellings cover the cloud plugins and the
        local faster-whisper provider without making a provider id load-bearing.
        """
        name = self._last_used
        if not name:
            return ""
        provider = (
            self._primary
            if name == self._primary_name
            else self._instances.get(name)
        )
        if provider is None:
            return ""
        for attribute in ("last_used_model", "_model", "_model_name"):
            value = getattr(provider, attribute, "")
            if value:
                return str(value)
        return ""

    _last_used = ""

    # ------------------------------------------------------------------
    # Cooldown bookkeeping
    # ------------------------------------------------------------------
    def _available(self, name: str) -> bool:
        until = self._cooldown.get(name, 0.0)
        return time.monotonic() >= until

    def _penalize(self, name: str, kind: str) -> None:
        seconds = (
            RATE_LIMIT_COOLDOWN_S
            if kind in _LONG_COOLDOWN_REASONS
            else TRANSIENT_COOLDOWN_S
        )
        self._cooldown[name] = time.monotonic() + seconds

    def _instance(self, name: str) -> Any:
        inst = self._instances.get(name)
        if inst is None:
            inst = self._build(name)
            self._instances[name] = inst
        return inst

    def _order(self) -> list[tuple[str, Any]]:
        """(name, provider) pairs to try, best first.

        The configured provider leads unless it is cooling down, in which case
        it moves to the BACK rather than being dropped: if every alternate is
        also exhausted it still gets the last word, which beats returning
        nothing at all.
        """
        alternates = [n for n in self._alternate_names]
        if self._preferred in alternates:
            alternates.remove(self._preferred)
            alternates.insert(0, self._preferred)
        head: list[tuple[str, Any]] = []
        tail: list[tuple[str, Any]] = []
        entry = (self._primary_name, self._primary)
        (head if self._available(self._primary_name) else tail).append(entry)
        for name in alternates:
            (head if self._available(name) else tail).append((name, None))
        return head + tail

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------
    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        first_error: BaseException | None = None
        for name, preloaded in self._order():
            try:
                provider = preloaded if preloaded is not None else self._instance(name)
            except Exception as exc:  # noqa: BLE001 — an unbuildable alternate is skipped
                log.debug("STT fallback: %s could not be built (%s)", name, exc)
                self._penalize(name, "transient")
                continue
            fn = getattr(provider, method, None)
            if fn is None:
                continue
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — classified below
                kind = _classify(exc)
                if not kind:
                    # The provider understood us and refused. Another one would
                    # refuse the same bytes; surface it instead of burning quota.
                    raise
                if first_error is None:
                    first_error = exc
                self._penalize(name, kind)
                log.warning(
                    "STT provider %s failed (%s: %s) — crossing to the next "
                    "provider so the transcription is not lost.",
                    name,
                    kind,
                    exc,
                )
                continue
            if name != self._primary_name:
                self._preferred = name
                log.info("STT transcribed by fallback provider %s.", name)
            self._last_used = name
            return result
        if first_error is not None:
            raise first_error
        raise RuntimeError("No speech-to-text provider was able to transcribe.")

    async def transcribe_pcm(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("transcribe_pcm", *args, **kwargs)

    async def transcribe(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("transcribe", *args, **kwargs)

    async def stream_transcribe(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]:
        # Streaming is NOT re-routed mid-stream: a consumer has already received
        # part of the transcript, and restarting on another provider would
        # duplicate it. The chain protects the one-shot calls, which is where
        # dictation and the final utterance transcription live.
        async for item in self._primary.stream_transcribe(*args, **kwargs):
            yield item


def alternate_provider_names(configured: str, available: Sequence[str]) -> list[str]:
    """The provider names to fall back to, in order, minus ``configured``.

    Ordered by what a failing provider most likely needs: another CLOUD family
    first (fast, and the user demonstrably has a key for it), then the key-free
    local engine as the floor. Local goes last because it is absent on a base /
    headless install and slow on a machine without a GPU — but when it IS there
    it is the one option no quota can take away.
    """
    ordered = [n for n in available if n != configured and n != "faster-whisper"]
    if "faster-whisper" in available and configured != "faster-whisper":
        ordered.append("faster-whisper")
    return ordered


def configured_fallback_names(
    stt_cfg: Any,
    configured: str,
    automatic: Sequence[str],
) -> tuple[str, ...]:
    """Apply the user's ``[stt].fallback`` setting to an automatic chain.

    An empty setting disables crossover, a concrete provider pins exactly that
    provider, and ``auto`` keeps the key-aware chain supplied by the caller.
    The selected provider is never repeated as its own fallback.
    """
    setting = str(getattr(stt_cfg, "fallback", "auto") or "").strip()
    if not setting:
        return ()
    if setting.lower() != "auto":
        return () if setting == configured else (setting,)
    return tuple(name for name in automatic if name != configured)


def wrap_stt_with_fallback(provider: Any, stt_cfg: Any) -> Any:
    """Wrap ``provider`` in a :class:`FallbackSTT` over the user's other keys.

    Returns ``provider`` unchanged when there is nothing to fall back to (no
    other keyed provider) or it is already wrapped — so a single-key install
    pays nothing for this.
    """
    if provider is None or isinstance(provider, FallbackSTT):
        return provider
    try:
        from jarvis.plugins.stt import (
            available_stt_provider_names,
            build_named_stt_provider,
        )

        configured = (getattr(stt_cfg, "provider", "") or "").strip()
        alternates = configured_fallback_names(
            stt_cfg,
            configured,
            alternate_provider_names(configured, available_stt_provider_names()),
        )
        if not alternates:
            return provider

        def _build(name: str) -> Any:
            return build_named_stt_provider(name, stt_cfg)

        log.info(
            "STT fallback chain armed: %s -> %s",
            configured or type(provider).__name__,
            ", ".join(alternates),
        )
        return FallbackSTT(
            provider, alternates, _build, primary_name=configured
        )
    except Exception as exc:  # noqa: BLE001 — a chain that cannot be built is not fatal
        log.warning("STT fallback chain unavailable (%s); using one provider.", exc)
        return provider


__all__ = [
    "FallbackSTT",
    "RATE_LIMIT_COOLDOWN_S",
    "TRANSIENT_COOLDOWN_S",
    "alternate_provider_names",
    "configured_fallback_names",
    "wrap_stt_with_fallback",
]
