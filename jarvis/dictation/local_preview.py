"""Local engine for the dictation LIVE PREVIEW — the words while you speak.

The preview and the transcript want opposite things. The transcript wants the
best model available and can afford a round-trip; the preview wants to be on
screen NOW and is thrown away on the next tick. Sending both to the same cloud
provider meant the throwaway half was spending the quota the keeping half
needed — measured at ~40 requests per minute of speech, ~85 % of it preview,
against a 20 RPM ceiling that Groq applies to its **paid plan too**. That is
what made a 137 s dictation come back with 367 characters.

Measured on the maintainer's box (RTX 5070 Ti, faster-whisper ``base``,
int8_float16):

    1 s tail ->  34 ms      4 s tail ->  63 ms      8 s tail -> 137 ms

against 400-1500 ms plus one rationed request for a cloud round-trip. So the
preview is not merely cheaper locally, it is an order of magnitude faster —
and it costs no quota at all, which is what lets the transcript have the whole
budget. That is the difference between "runs out after 40 seconds" and "as long
as you care to talk".

Why ``base`` and not the model the transcript uses: a preview is read at a
glance and replaced a second later, so a rare wrong ending costs nothing, while
a model that takes 800 ms turns the preview into a lagging distraction. The
FINAL text is never produced here — it always comes from the configured
provider, so the words that land in the user's document are the good ones.

Degrades honestly (CLAUDE.md §3): on a host without ``faster_whisper`` — a base
or headless install — this reports unavailable and the caller falls back to the
budgeted cloud preview. No GPU is required either; the engine picks CPU and the
caller simply sees a slower preview.

In the desktop app the engine is hosted OUT OF PROCESS
(``jarvis/dictation/preview_worker.py``): building it in-process loaded the
CUDA DLLs (~600 MB of cublas) while holding the Windows loader lock, freezing
the whole window for 15-30 s on the first dictation after a cold boot. The
worker runs this same class; the parent talks to it through a faster-whisper-
shaped proxy (``_WorkerModel``), so every timeout/failure/recovery path below
applies to both hostings. ``JARVIS_DICTATION_PREVIEW_IN_PROCESS=1`` is the
debug escape back to the in-process build.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import threading
import time
import weakref
from pathlib import Path
from types import SimpleNamespace
from typing import Any

log = logging.getLogger(__name__)

#: How long the out-of-process engine may take to build + warm before the
#: spawn attempt is abandoned. Generous on purpose: the first CUDA decode on a
#: cold driver cache pays a one-off kernel compile measured at 19 s, and a
#: cold-boot disk multiplies everything — the parent loses nothing by waiting,
#: the preview simply stays on its cloud fallback meanwhile.
_WORKER_BOOT_TIMEOUT_S = 180.0

#: Model for the preview. Small enough to answer in tens of milliseconds, good
#: enough that the line on screen reads as what was said. Never used for the
#: text that is actually delivered.
PREVIEW_MODEL = "base"

#: How long one preview transcription may take before we stop waiting for it.
#: The preview is worthless once it is stale, and a wedged native engine must
#: never hold the dictation loop (AP-24: a timeout BOUNDS the wait, it does not
#: recover the engine — that is what ``_failures``/``reset`` below are for).
PREVIEW_TIMEOUT_S = 2.0

#: Consecutive failures after which the engine is dropped and rebuilt on next
#: use. A native engine that has wedged never recovers by being asked again.
_MAX_FAILURES = 3

#: The clip the freshly built engine decodes before it is advertised — four
#: seconds of near-silence, the shape of an ordinary preview tail. Its timing
#: is logged, never judged: the first CUDA decode in a process pays context
#: init and, on a cold driver cache, a one-off kernel compile measured at 19 s,
#: so a clock here would reject a perfectly good GPU on its first day.
#:
#: What IS judged is the live tick. Measured 2026-08-22 on a Blackwell card
#: (ctranslate2 4.8): ``int8_float16`` decoded 8 s of real speech in 10-11 s,
#: ``float16`` in 0.7 s — so the engine timed out on every tick, was dropped,
#: rebuilt with the SAME settings and dropped again, every 7 s for the whole
#: dictation. A drop therefore now advances to the next engine in the attempt
#: list (``_load_model``) instead of rebuilding the one that just failed, and
#: stays on the CPU floor once it gets there.
_WARM_CLIP_S = 4.0


def faster_whisper_available() -> bool:
    """Is a local engine importable here? Cheap spec probe, no heavy import."""
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


def _child_python() -> str:
    """The interpreter for the preview worker, beside the running executable.

    ``sys.executable`` in the desktop app is the venv's GUI entry point
    (``PersonalJarvis.exe``), which cannot take ``-m`` — the real
    ``python(.exe)`` lives in the same Scripts/bin directory.
    """
    exe = Path(sys.executable)
    if exe.stem.lower().startswith("python"):
        return str(exe)
    for name in ("python.exe", "python"):
        candidate = exe.parent / name
        if candidate.exists():
            return str(candidate)
    raise RuntimeError(f"no python interpreter beside {exe}")


def _close_worker_process(proc: subprocess.Popen[bytes]) -> None:
    """Kill, reap and close pipes. Runs only on a cleanup/caller worker thread."""
    try:
        proc.kill()
    except Exception as exc:  # noqa: BLE001 — an already-dead worker is the goal state
        log.debug("Preview worker kill skipped: %s", exc)
    try:
        wait = getattr(proc, "wait", None)
        if callable(wait):
            wait(timeout=3.0)
    except Exception as exc:  # noqa: BLE001 — never wait forever on teardown
        log.warning("Preview worker did not exit after kill: %s", exc)
        return
    for stream in (proc.stdin, proc.stdout):
        if stream is not None:
            try:
                stream.close()
            except Exception as exc:  # noqa: BLE001 — already-closed pipe is harmless
                log.debug("Preview worker pipe close skipped: %s", exc)


def _release_abandoned_worker(proc: subprocess.Popen[bytes]) -> None:
    """Do not let garbage collection block the voice/UI loop on pipe teardown."""
    try:
        threading.Thread(
            target=_close_worker_process,
            args=(proc,),
            name="dictation-worker-reap",
            daemon=True,
        ).start()
    except RuntimeError:
        log.exception("Could not schedule abandoned dictation worker cleanup")


class _WorkerModel:
    """faster-whisper-shaped proxy whose decodes run in the preview worker.

    Presents the same ``transcribe(...)`` surface ``_transcribe_sync`` uses,
    so every failure/timeout/recovery path of the parent class applies
    unchanged — with one upgrade: dropping this "model" (``close``) KILLS the
    worker process, which is the recovery an in-process wedged native engine
    never had (AP-24).
    """

    def __init__(self, proc: subprocess.Popen[bytes], device: str, compute: str) -> None:
        self._proc = proc
        self.device = device
        self.compute = compute
        # Provider switches discard the owner without necessarily calling close.
        # Popen can retain a live child in its global _active list, including
        # its open stdin pipe: neither GC nor the child's EOF loop then frees
        # the native model. Retain only the process here, never a bound method
        # of self. Active transcribe calls keep self alive until they finish.
        self._cleanup = weakref.finalize(self, _release_abandoned_worker, proc)
        # No new threads during interpreter shutdown. Parent pipe closure is
        # the worker's existing EOF/exit path at process shutdown.
        self._cleanup.atexit = False  # type: ignore[misc]  # Writable property despite finalize's slots.

    def transcribe(
        self,
        samples: Any,
        language: str | None = None,
        beam_size: int = 1,
        **_ignored: Any,
    ) -> tuple[list[Any], Any]:
        import numpy as np

        from jarvis.dictation.preview_worker import read_message, write_message

        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        stdin, stdout = self._proc.stdin, self._proc.stdout
        if stdin is None or stdout is None:  # pragma: no cover — Popen(PIPE) guarantees both
            raise RuntimeError("preview worker pipes are gone")
        request: dict[str, Any] = {"n": len(pcm), "language": language}
        if beam_size and int(beam_size) > 1:
            request["beam_size"] = int(beam_size)
        write_message(stdin, request)
        stdin.write(pcm)
        stdin.flush()
        response = read_message(stdout)
        if response is None:
            raise RuntimeError("preview worker exited")
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        timings = response.get("segments") or []
        segments = [
            SimpleNamespace(
                start=item.get("start"),
                end=item.get("end"),
                text=str(item.get("text", "") or ""),
            )
            for item in timings
            if isinstance(item, dict)
        ] or [SimpleNamespace(start=None, end=None, text=str(response.get("text", "")))]
        info = SimpleNamespace(
            language=str(response.get("language", "") or ""),
            language_probability=float(response.get("probability", 0.0) or 0.0),
        )
        return segments, info

    def close(self) -> None:
        """Kill and reap the worker. Blocking: callers must keep this off-loop."""
        if self._cleanup.detach() is not None:
            _close_worker_process(self._proc)


def _spawn_worker_model(model_name: str, *, compute: str | None = None) -> _WorkerModel | None:
    """Start the out-of-process engine; ``None`` when this host cannot.

    A refused spawn disables the decorative preview; final STT retains its
    authorized fallback policy. No native engine is loaded into the parent.
    ``compute`` pins the CUDA compute type (or the CPU floor) for the worker
    (the dictation final pass shares the card and asks for ``int8_float16``).
    """
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
    from jarvis.dictation.preview_worker import read_message

    argv = [_child_python(), "-X", "utf8", "-m", "jarvis.dictation.preview_worker", model_name]
    if compute:
        argv.append(compute)
    started = time.perf_counter()
    try:
        proc = subprocess.Popen(  # noqa: S603 — our own interpreter + module, no user input
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except Exception as exc:  # noqa: BLE001 — no interpreter / spawn refused: use the floor
        log.info(
            "Dictation preview worker could not start (%s: %s) — staying in-process.",
            type(exc).__name__,
            exc,
        )
        return None
    result: dict[str, Any] = {}
    done = threading.Event()

    def _handshake() -> None:
        try:
            result["msg"] = read_message(proc.stdout)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 — reported via the empty result
            result["exc"] = exc
        done.set()

    spawned_ms = (time.perf_counter() - started) * 1000.0
    reader = threading.Thread(target=_handshake, name="dictation-preview-handshake", daemon=True)
    reader.start()
    if not done.wait(_WORKER_BOOT_TIMEOUT_S):
        log.info(
            "Dictation preview worker did not become ready within %.0f s — killed.",
            _WORKER_BOOT_TIMEOUT_S,
        )
        _WorkerModel(proc, "", "").close()
        reader.join(timeout=3.0)
        return None
    message = result.get("msg")
    if not isinstance(message, dict) or not message.get("ready"):
        detail = (
            message.get("error")
            if isinstance(message, dict)
            else result.get("exc") or "worker exited during startup"
        )
        log.info("Dictation preview worker reported no engine (%s).", detail)
        _WorkerModel(proc, "", "").close()
        reader.join(timeout=3.0)
        return None
    reader.join(timeout=3.0)
    log.info(
        "Dictation worker startup: spawn=%.0f ms, ready=%.0f ms, stages=%s.",
        spawned_ms,
        (time.perf_counter() - started) * 1000.0,
        message.get("timings", {}),
    )
    return _WorkerModel(
        proc,
        str(message.get("device", "?") or "?"),
        str(message.get("compute", "?") or "?"),
    )


class LocalPreviewTranscriber:
    """Lazily-built local engine for preview text. Never raises to the caller.

    One instance owns one engine and serialises access to it with a
    NON-BLOCKING lock: a second caller arriving while a transcription is in
    flight is turned away rather than queued (AP-24 — ctranslate2 is not
    thread-safe, and a queued caller would wedge it permanently). Turning a
    preview away is free; the next tick asks again.
    """

    def __init__(
        self,
        model_name: str = PREVIEW_MODEL,
        *,
        prefer_worker: bool = False,
        compute_override: str | None = None,
    ) -> None:
        self._model_name = model_name
        #: CUDA compute type to try first instead of the probe's pick — the
        #: dictation final pass asks for ``int8_float16`` so a second model
        #: fits beside the wake engine and the voice stack. CPU is unaffected.
        self._compute_override = (compute_override or "").strip() or None
        #: True in the DESKTOP process (set by the ``local_preview`` factory):
        #: the engine is hosted out of process so the CUDA DLL load never
        #: holds this process's loader lock. False inside the worker itself
        #: and in tests, where the in-process ladder below runs unchanged.
        self._prefer_worker = prefer_worker
        self._model: Any = None
        self._lock = threading.Lock()
        self._busy = threading.Lock()
        # Which entry of the attempt list the next build starts from, and what
        # the current engine runs on (for the log line and the status card).
        self._attempt_index = 0
        self._engine_device = ""
        self._engine_compute = ""
        self._failures = 0
        self._unavailable = False
        self._loading = False
        self._load_failed = False
        self._load_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._load_timings: dict[str, float] = {}
        #: Language of the MOST RECENT preview, as ``(code, probability)``.
        #: The engine computes this on every call and it used to be discarded.
        #: It is the only reading of the spoken language taken from the AUDIO
        #: rather than from a transcript, which is what makes it worth keeping:
        #: a cloud provider handed a few seconds of speech may silently
        #: TRANSLATE it, and a translated sentence looks like the wrong
        #: language to any text-based detector (BUG: German dictation
        #: delivered in English, 2026-07-29). Empty until a preview has run.
        self.last_language = ""
        self.last_language_probability = 0.0

    @property
    def available(self) -> bool:
        """False once this host has proven it cannot serve a local preview."""
        return not self._unavailable

    @property
    def ready(self) -> bool:
        """Whether the engine can answer right now (model built)."""
        return self._model is not None

    def _load_model(self) -> None:
        """Build the engine. Runs OFF the transcribe path — see ``transcribe``."""
        try:
            if self._prefer_worker:
                # Recovery must advance the WORKER'S device ladder too. Resetting
                # the child to its default recreated the same wedged GPU forever.
                compute = (None, "int8_float16", "cpu")[min(self._attempt_index, 2)]
                worker = (
                    _spawn_worker_model(self._model_name, compute=compute)
                    if compute
                    else _spawn_worker_model(self._model_name)
                )
                if worker is not None:
                    with self._lock:
                        self._model = worker
                        self._engine_device = worker.device
                        self._engine_compute = worker.compute
                    log.info(
                        "Dictation preview engine ready: %s on %s (%s), out of process.",
                        self._model_name,
                        worker.device,
                        worker.compute,
                    )
                    return
                # A decorative preview must never defeat process isolation
                # because its worker failed. Final STT retains the recording.
                self._load_failed = True
                self._unavailable = True
                log.info("Local preview unavailable: isolated worker could not start.")
                return
            else:
                probe_started = time.perf_counter()
                preferred = (
                    ("cpu", "int8") if self._compute_override == "cpu" else self._pick_device()
                )
                self._load_timings["probe_ms"] = (time.perf_counter() - probe_started) * 1000.0
                if self._compute_override and preferred[0] == "cuda":
                    preferred = ("cuda", self._compute_override)
            from jarvis.plugins.stt.fwhisper import _new_whisper_model

            attempts = [preferred]
            if preferred == ("cuda", "float16"):
                # The quantized pair is the old GPU default and still the
                # right answer on a GPU where it is fast; tried second so a
                # host that cannot do float16 keeps its GPU preview.
                attempts.append(("cuda", "int8_float16"))
            if preferred[0] != "cpu":
                attempts.append(("cpu", "int8"))
            # A rebuild after repeated live failures starts one entry further
            # down: the engine that just kept timing out is not asked again.
            skip = min(self._attempt_index, len(attempts) - 1)
            attempts = attempts[skip:]
            last_error: Exception | None = None
            for device, compute in attempts:
                try:
                    build_started = time.perf_counter()
                    model = _new_whisper_model(self._model_name, device, compute)
                    self._load_timings["build_ms"] = (time.perf_counter() - build_started) * 1000.0
                except Exception as exc:  # noqa: BLE001 — try the portable floor
                    last_error = exc
                    log.info(
                        "Dictation preview engine could not use %s (%s: %s).",
                        device,
                        type(exc).__name__,
                        exc,
                    )
                    continue
                try:
                    # Constructing the model does not pay CUDA's first-decode
                    # setup cost. Prime one throwaway greedy decode here, while
                    # the preview is still advertised as unavailable, so the
                    # first visible preview keeps the steady-state latency —
                    # and TIME it: a GPU engine that cannot decode a preview-
                    # sized clip well inside the tick ceiling is not a preview
                    # engine, whatever the constructor said.
                    import numpy as np

                    rng = np.random.default_rng(0)
                    warm_audio = (
                        rng.standard_normal(int(16_000 * _WARM_CLIP_S)).astype(np.float32) * 0.001
                    )
                    started = time.perf_counter()
                    segments, _info = model.transcribe(
                        warm_audio,
                        beam_size=1,
                        temperature=0.0,
                        condition_on_previous_text=False,
                    )
                    list(segments)
                    warm_s = time.perf_counter() - started
                    self._load_timings["first_decode_ms"] = warm_s * 1000.0
                except Exception as exc:  # noqa: BLE001 — try the portable floor
                    last_error = exc
                    log.info(
                        "Dictation preview rejected %s after its first decode failed (%s: %s).",
                        device,
                        type(exc).__name__,
                        exc,
                    )
                    continue
                with self._lock:
                    self._model = model
                    self._engine_device = device
                    self._engine_compute = compute
                log.info(
                    "Dictation preview engine ready: %s on %s (%s), %.0fs clip in %.2fs.",
                    self._model_name,
                    device,
                    compute,
                    _WARM_CLIP_S,
                    warm_s,
                )
                break
            else:
                self._load_failed = True
                self._unavailable = True
                log.info(
                    "Local dictation preview unavailable (%s: %s) — the transcript "
                    "is unaffected; the live line falls back to the provider.",
                    type(last_error).__name__ if last_error is not None else "Error",
                    last_error or "no usable local inference device",
                )
        finally:
            self._loading = False

    @staticmethod
    def _pick_device() -> tuple[str, str]:
        """``(device, compute_type)`` — CUDA when it is genuinely usable.

        Ask the inference runtime that will actually execute the model. Importing
        torch here was both indirect and racy: the faster-whisper import shield
        can temporarily hide torch from another loader thread, which made a
        CUDA-capable desktop silently choose the CPU for the rest of the process.
        Anything uncertain picks CPU, and ``_load_model`` still proves the choice
        with a real model build before accepting it.
        """
        try:
            from jarvis.plugins.stt.fwhisper import (
                ensure_cuda_libraries_findable,
                inference_only_import_shield,
            )

            ensure_cuda_libraries_findable()
            with inference_only_import_shield():
                import ctranslate2  # type: ignore[import-untyped]

            supported = set(ctranslate2.get_supported_compute_types("cuda"))
            # Plain half precision first: on the one GPU measured so far the
            # quantized int8 pairs were an order of magnitude SLOWER than
            # float16 (10-11 s against 0.7 s for 8 s of speech, ctranslate2
            # 4.8 on a Blackwell card) — the preview model is tiny, so the
            # memory int8 saves buys nothing here. The warm-decode gate in
            # ``_load_model`` is the backstop for the next surprise.
            if "float16" in supported:
                return "cuda", "float16"
            if "int8_float16" in supported:
                return "cuda", "int8_float16"
        except Exception as exc:  # noqa: BLE001 — a probe must never decide by raising
            log.debug("Preview CUDA probe failed (%s); using CPU.", exc)
        return "cpu", "int8"

    def _transcribe_sync(
        self, pcm: bytes, language: str | None, *, model: Any = None
    ) -> tuple[str, str, float]:
        """``(text, language_code, language_probability)``.

        The language is reported back rather than dropped: it costs nothing
        (the decoder already produced it) and it is an AUDIO-derived reading,
        which no downstream text inspection can reconstruct once a provider
        has translated the words.
        """
        text, detected, probability, _timings = self._transcribe_sync_detailed(
            pcm, language, model=model
        )
        return text, detected, probability

    def _transcribe_sync_detailed(
        self, pcm: bytes, language: str | None, *, beam_size: int = 1, model: Any = None
    ) -> tuple[str, str, float, list[dict[str, Any]]]:
        """:meth:`_transcribe_sync` plus the decoder's own segment timings.

        ``beam_size`` defaults to greedy — the preview trades a little accuracy
        for latency — and the dictation final pass, read once after release,
        asks for a real beam. The segments carry ``start``/``end`` on the
        recognizer's clock so a caller can tell a dropped tail from a slow
        speaker, which the joined text alone cannot.
        """
        import numpy as np

        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return "", "", 0.0, []
        model = self._model if model is None else model
        if model is None:  # pragma: no cover — transcribe() gates on ready
            return "", "", 0.0, []
        segments, info = model.transcribe(
            samples,
            language=language,
            beam_size=max(1, int(beam_size)),
            # A tuple/default enables Whisper's temperature fallback ladder and
            # may decode the same stale preview repeatedly. One fixed pass
            # keeps the measured preview path in the tens-of-milliseconds range.
            temperature=0.0,
            condition_on_previous_text=False,
        )
        decoded = list(segments)
        text = " ".join(seg.text for seg in decoded).strip()
        # A language the CALLER pinned is not a detection — reporting it back as
        # one would let a pin confirm itself forever.
        detected = "" if language else str(getattr(info, "language", "") or "")
        try:
            probability = float(getattr(info, "language_probability", 0.0) or 0.0)
        except (TypeError, ValueError):
            # Deliberately quiet: zero is the honest reading of "this engine
            # build reports no confidence", and it makes the caller's gate
            # reject the detection — the same outcome a log line would only
            # narrate, once per preview tick.
            probability = 0.0
        timings = [
            {
                "start": getattr(seg, "start", None),
                "end": getattr(seg, "end", None),
                "text": str(getattr(seg, "text", "") or ""),
            }
            for seg in decoded
        ]
        return text, detected, probability, timings

    async def transcribe(self, pcm: bytes, language: str | None = None) -> str | None:
        """Preview text, or ``None`` when this tick has none.

        ``None`` is not an error — it is "no preview right now" (engine busy,
        too slow, or unavailable). The caller shows the previous line and asks
        again on the next tick.
        """
        if self._unavailable or not pcm:
            return None
        if self._model is None:
            # Building the engine takes seconds — far longer than the timeout a
            # PREVIEW may hold. Loading it inside the timed call meant the first
            # ticks of the first dictation all "timed out", and each one counted
            # as an engine failure, so the local preview reliably disabled
            # itself before it had ever worked once. Load OFF this path and
            # answer "nothing yet" meanwhile: a missing preview line for the
            # first couple of seconds is not a failure of anything.
            self._start_loading()
            return None
        guard = self._busy
        if not guard.acquire(blocking=False):
            # A previous preview is still running. Skipping is correct: a queued
            # call on a native engine is how it wedges (AP-24). This can only
            # persist after a timed-out/cancelled waiter, so count it toward
            # recovery; otherwise one truly wedged worker owns the guard forever.
            self._note_failure("still busy after its waiter ended")
            return None
        work: asyncio.Task[tuple[str, str, float]] | None = None
        release_here = True
        try:
            work = asyncio.create_task(
                asyncio.to_thread(self._transcribe_sync, pcm, language, model=self._model),
                name="dictation-local-preview",
            )
            text, detected, probability = await asyncio.wait_for(
                asyncio.shield(work),
                timeout=PREVIEW_TIMEOUT_S,
            )
            if detected:
                self.last_language = detected
                self.last_language_probability = probability
            self._failures = 0
            return text
        except TimeoutError:
            # Not silent: _note_failure logs the attempt and drops the engine once
            # the failures persist. Returning None is the whole handling — a
            # preview that missed its slot has nothing left to say, and raising
            # here would break the dictation the preview only decorates.
            self._note_failure("timed out")
            return None
        except Exception as exc:  # noqa: BLE001 — a preview must never break dictation
            self._note_failure(f"{type(exc).__name__}: {exc}")
            return None
        finally:
            if work is not None and not work.done():
                # Cancelling or timing out the asyncio waiter does not stop the
                # native thread. Keep the non-blocking guard held until that
                # thread really exits, or the next preview would enter the same
                # ctranslate2 session concurrently (AP-24).
                release_here = False

                def _release_finished_worker(done: asyncio.Task) -> None:
                    guard.release()
                    try:
                        done.result()
                    except asyncio.CancelledError:  # Cancellation is the contained worker teardown.
                        pass
                    except Exception as exc:  # noqa: BLE001 — detached worker is contained
                        log.debug("Detached dictation preview failed: %s", exc)
                    else:
                        # It came back late, but it came back: a slow engine is
                        # not a wedged one, and the streak that would have
                        # dropped it is over.
                        if self._busy is guard:
                            self._failures = 0

                work.add_done_callback(_release_finished_worker)
            if release_here:
                guard.release()

    def _start_loading(self) -> None:
        """Kick off a one-shot background load. Cheap and idempotent."""
        with self._lock:
            if self._loading or self._model is not None or self._load_failed:
                return
            self._loading = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            threading.Thread(
                target=self._load_model, name="dictation-preview-load", daemon=True
            ).start()
            return

        async def load() -> None:
            try:
                if self._cleanup_task is not None:
                    await asyncio.shield(self._cleanup_task)
                await asyncio.to_thread(self._load_model)
            except Exception:
                self._loading = False
                self._load_failed = True
                self._unavailable = True
                log.exception("Dictation preview background load failed")

        self._load_task = loop.create_task(load(), name="dictation-preview-load")

    def _note_failure(self, why: str) -> None:
        """Count a failure and drop the engine once they persist.

        Rebuilding a FRESH engine is the only way back from a wedged native
        session — re-asking the same one never recovers it (AP-24). Dropping the
        model is therefore the whole repair here; the NEXT tick sees no model
        and starts a background rebuild.

        Turning the local path off for good is deliberately NOT decided here. A
        transcription failure says "this engine is unwell", not "this host
        cannot run one" — only a failed BUILD proves that, and ``_load_model``
        is where it is recorded. Deciding it in both places is how a machine
        that had one bad segment loses its fast preview permanently.
        """
        self._failures += 1
        log.debug("Dictation preview failed (%s), attempt %d.", why, self._failures)
        if self._failures < _MAX_FAILURES:
            return
        with self._lock:
            # The old native worker keeps its captured model and guard. Rotate
            # both references atomically so the next tick builds a genuinely
            # fresh session rather than re-polling a wedged engine (AP-24).
            dropped = self._model
            self._model = None
            self._busy = threading.Lock()
            # Not the same engine again: the next build takes the next entry
            # of the attempt list (float16 -> int8_float16 -> cpu) and stays
            # on the last one. An engine that keeps failing live is slow or
            # wedged on THIS device/compute pair; the floor below it is not.
            self._attempt_index += 1
        self._failures = 0
        # An out-of-process engine can be recovered for REAL: killing the
        # worker unwedges whatever its native session was stuck in, and the
        # detached transcribe thread's blocking read ends with the pipe. An
        # in-process model has no close() and is left to the GC as before.
        close = getattr(dropped, "close", None)
        if callable(close):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                close()
            else:

                async def cleanup() -> None:
                    try:
                        await asyncio.to_thread(close)
                    except Exception:
                        log.exception("Dictation preview cleanup failed")

                self._cleanup_task = loop.create_task(cleanup(), name="dictation-preview-cleanup")
        log.info(
            "Dictation preview engine dropped (%s on %s/%s); rebuilding on the "
            "next tick with the next engine.",
            why,
            self._engine_device or "?",
            self._engine_compute or "?",
        )


_INSTANCE: LocalPreviewTranscriber | None = None
_INSTANCE_LOCK = threading.Lock()


def local_preview() -> LocalPreviewTranscriber | None:
    """The shared preview engine, or ``None`` where none can run.

    Shared for the same reason the preview budget is: consecutive dictations
    should not each pay the model load. Returns ``None`` (rather than an inert
    object) so the caller's fallback is an explicit branch, not a silent no-op.
    """
    global _INSTANCE
    if not faster_whisper_available():
        return None
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            import os

            # Out of process by default (the CUDA DLL load must never hold
            # this process's loader lock); the env switch is the debug escape
            # back to the old in-process build.
            in_process = os.environ.get("JARVIS_DICTATION_PREVIEW_IN_PROCESS") == "1"
            _INSTANCE = LocalPreviewTranscriber(prefer_worker=not in_process)
        return _INSTANCE if _INSTANCE.available else None


def reset_local_preview_for_tests() -> None:
    """Drop the shared engine — test-isolation hook."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = None


__all__ = [
    "PREVIEW_MODEL",
    "PREVIEW_TIMEOUT_S",
    "LocalPreviewTranscriber",
    "faster_whisper_available",
    "local_preview",
    "reset_local_preview_for_tests",
]
