"""Eager prefetch of the heavy wake-word import, off the boot critical path.

Why this exists
---------------
The wake-critical Phase-A warm-up (``SpeechPipeline._warmup_phase_a``) gates
``VoiceBootStatus(ready=True)`` — the moment the desktop UI flips its
"VOICE STARTING…" spinner to "listening" — on the OpenWakeWord model coming up.
Profiling on a real desktop showed the dominant cost is the
``openwakeword.model`` *import* (it pulls in the onnxruntime C-extension), at
~2.9 s, NOT the model parse (~0.1 s).

Worse, since the serve-first fast-boot bootstrap (the server now answers in
~200 ms and every subsystem — brain build, wiki FTS index, conductor, workflows,
awareness — boots concurrently as ``create_task``), that import no longer runs
alone: it serializes on the global Python import lock against all those other
heavy imports and starves to 7-24 s (measured ``wake-start=14187``). That is the
"VOICE STARTING… forever" the user sees.

Firing the import once, early, in a daemon thread — before the server/brain
build grabs the import lock — moves the cost off the wake path. By the time
Phase A calls ``_ensure_model``, ``openwakeword.model`` is already in
``sys.modules``, so the load collapses to a no-op import + the ~0.1 s parse, and
Phase A falls back to its audio-stabilize floor.

This is monotonically safe: if the prefetch has not finished when Phase A needs
the model, Phase A simply waits on the same import it would have triggered
itself — never slower than today, only faster. A missing openWakeWord (headless
VPS base install with no ``[desktop]`` extra) is a logged no-op, never an error,
so the cloud-first boot is unaffected.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable

from loguru import logger

__all__ = [
    "prefetch_wake_imports",
    "start_wake_import_prefetch",
    "prefetch_tts_imports",
    "start_tts_import_prefetch",
]

# Mirror the gate the speech stack itself uses (``_start_speech_and_orb``):
# JARVIS_VOICE in {0, off, false} means no voice, so nothing to prefetch.
_VOICE_OFF_TOKENS = ("0", "off", "false")


def _voice_disabled() -> bool:
    return os.environ.get("JARVIS_VOICE", "").strip().lower() in _VOICE_OFF_TOKENS


def prefetch_wake_imports(importer: Callable[[], None] | None = None) -> bool:
    """Eagerly import the heavy OpenWakeWord/onnxruntime C-extension.

    Idempotent (a second call hits the ``sys.modules`` cache) and fail-closed:
    any import failure (no openWakeWord on a headless host) is swallowed and
    logged at debug level. ``importer`` is injectable for tests.

    Returns:
        True iff the import succeeded; False if it was unavailable / failed.
    """

    def _default_import() -> None:
        import openwakeword.model  # noqa: F401, PLC0415 — eager warm import

    do_import = importer or _default_import
    try:
        do_import()
        return True
    except Exception as exc:  # noqa: BLE001 — a prefetch must never break boot
        logger.debug("Wake-import prefetch skipped (openWakeWord unavailable): {}", exc)
        return False


def start_wake_import_prefetch(
    *, importer: Callable[[], None] | None = None
) -> threading.Thread | None:
    """Run :func:`prefetch_wake_imports` in a daemon thread, off the boot path.

    No-op (returns ``None``) when voice is disabled via ``JARVIS_VOICE``. The
    thread is a daemon so it never holds up process shutdown. ``importer`` is
    threaded through for tests.
    """
    if _voice_disabled():
        return None
    thread = threading.Thread(
        target=prefetch_wake_imports,
        args=(importer,),
        name="wake-import-prefetch",
        daemon=True,
    )
    thread.start()
    return thread


def prefetch_tts_imports(importer: Callable[[], None] | None = None) -> bool:
    """Eagerly import the heavy default-TTS SDK (``google-genai``), off the
    boot critical path.

    Why: the deferred warm-up loader (``SpeechPipeline._warmup_deferred_loaders``)
    gates honest ``VoiceBootStatus(ready=True)`` on the TTS client being up, and
    the dominant cost of that ``_ensure_client`` is ``from google import genai``
    — a heavy import that, run inside the serve-first boot storm, serializes on
    the global import lock and starves to ~4-11 s (measured ``tts-init=10797``).
    Firing it once, early, in a daemon thread collapses the later import to a
    ``sys.modules`` cache hit. Unlike the wake (ctranslate2) load this touches
    NO torch/transformers, so it never races the ``inference_only_import_shield``.

    Idempotent and fail-closed: a missing/other TTS provider (Grok, SAPI5,
    headless VPS with no google-genai) is a logged no-op, never an error.
    ``importer`` is injectable for tests.
    """

    def _default_import() -> None:
        from google import genai  # noqa: F401, PLC0415 — eager warm import

    do_import = importer or _default_import
    try:
        do_import()
        return True
    except Exception as exc:  # noqa: BLE001 — a prefetch must never break boot
        logger.debug("TTS-import prefetch skipped (google-genai unavailable): {}", exc)
        return False


def start_tts_import_prefetch(
    *, importer: Callable[[], None] | None = None
) -> threading.Thread | None:
    """Run :func:`prefetch_tts_imports` in a daemon thread, off the boot path.

    No-op (returns ``None``) when voice is disabled via ``JARVIS_VOICE``. Safe to
    run concurrently with the wake-import prefetch — they touch disjoint modules.
    """
    if _voice_disabled():
        return None
    thread = threading.Thread(
        target=prefetch_tts_imports,
        args=(importer,),
        name="tts-import-prefetch",
        daemon=True,
    )
    thread.start()
    return thread
