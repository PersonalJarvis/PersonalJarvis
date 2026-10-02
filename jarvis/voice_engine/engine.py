"""One live conversation inside the engine worker.

Listens to 16 kHz audio, decides when the user finished (Silero VAD + Smart
Turn, with the transcription started speculatively at the first pause), hands
the final transcript to the app, answers with a streaming LLM when the app
requests a response, speaks clause by clause, routes tool calls to the app
and stops the moment the user talks over it
(``docs/local-live-voice-rebuild.md`` section 4.3).

All session state lives on the worker's asyncio loop. Model inference runs in
threads, one caller per model at a time (AP-24): the turn model, the
recogniser and each voice are guarded by their own lock.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from jarvis.voice_engine import protocol as p
from jarvis.voice_engine.audio import resample, to_float32, to_pcm16
from jarvis.voice_engine.chunker import ClauseChunker
from jarvis.voice_engine.vad import FRAME_MS, FRAME_SAMPLES, Endpointer

log = logging.getLogger("jarvis.voice_engine.engine")

INPUT_RATE = p.INPUT_RATE
OUTPUT_RATE = p.OUTPUT_RATE
LANGUAGE_NAMES = {"de": "German", "en": "English", "es": "Spanish", "fr": "French",
                  "it": "Italian", "pt": "Portuguese", "nl": "Dutch"}


class VadModel(Protocol):
    def __call__(self, frame: np.ndarray) -> float: ...
    def reset(self) -> None: ...


class TurnModel(Protocol):
    def probability(self, audio16k: np.ndarray) -> float: ...


class SttModel(Protocol):
    def transcribe(self, audio: np.ndarray, rate: int = INPUT_RATE) -> str: ...


class TtsModel(Protocol):
    name: str
    sample_rate: int

    def stream(self, text: str, *, stop: threading.Event | None = None) -> Iterator[np.ndarray]:
        ...


class ChatModel(Protocol):
    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any: ...


class Emit(Protocol):
    def json(self, message: dict[str, Any]) -> None: ...
    def audio(self, slot: int, seq: int, pcm16: bytes) -> None: ...


@dataclass
class EngineModels:
    """Models shared by every session of one worker."""

    vad_factory: Callable[[], VadModel]
    turn: TurnModel
    stt: SttModel
    tts_for: Callable[[str], TtsModel]
    llm: ChatModel
    _locks: dict[str, threading.Lock] = field(default_factory=dict)
    _guard: threading.Lock = field(default_factory=threading.Lock)

    def lock(self, name: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(name, threading.Lock())


@dataclass
class EngineConfig:
    silence_ms: int = 200
    min_speech_ms: int = 160
    barge_in_ms: int = 250
    preroll_ms: int = 300
    turn_threshold: float = 0.5
    incomplete_wait_ms: int = 1000
    # Re-score the turn at this spacing while waiting on an "incomplete"
    # verdict: more trailing silence often turns it into "complete".
    reevaluate_ms: int = 300
    # Start transcribing after this much quiet, ahead of the turn decision.
    early_stt_ms: int = 96
    # Amplitude below which synthesized audio counts as silence (about -42 dBFS).
    tts_silence: float = 0.008
    max_utterance_s: float = 30.0
    output_frame_ms: int = 40
    max_history_messages: int = 24
    max_tool_rounds: int = 4
    tool_timeout_s: float = 30.0


@dataclass
class _Clause:
    text: str
    start_ms: float = 0.0
    end_ms: float = 0.0


@dataclass
class _Response:
    cancel: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task[None] | None = None
    clauses: list[_Clause] = field(default_factory=list)
    audio_ms: float = 0.0
    first_audio_at: float | None = None
    requested_at: float = 0.0
    tool_rounds: int = 0
    self_initiated_cancel: bool = False


class ConversationSession:
    def __init__(
        self,
        *,
        slot: int,
        session_id: str,
        models: EngineModels,
        emit: Emit,
        loop: asyncio.AbstractEventLoop,
        config: EngineConfig | None = None,
        instructions: str = "",
        language: str = "en",
        tools: list[dict[str, Any]] | None = None,
        history: list[dict[str, str]] | None = None,
        turn_pause_ms: int | None = None,
    ) -> None:
        self.slot = slot
        self.session_id = session_id
        self._models = models
        self._emit = emit
        self._loop = loop
        self._config = config or EngineConfig()
        self._instructions = instructions
        self._language = language
        self._tools = _ollama_tools(tools or [])
        self._turn_pause_ms = turn_pause_ms or None
        self._vad = models.vad_factory()
        silence = self._turn_pause_ms or self._config.silence_ms
        self._endpointer = Endpointer(min_speech_ms=self._config.min_speech_ms, silence_ms=silence,
                                      early_ms=self._config.early_stt_ms)
        self._barge = Endpointer(min_speech_ms=self._config.barge_in_ms, silence_ms=silence)
        preroll_frames = max(1, self._config.preroll_ms // FRAME_MS)
        self._preroll: deque[np.ndarray] = deque(maxlen=preroll_frames)
        self._tail = np.zeros(0, dtype=np.float32)
        self._utterance: list[np.ndarray] = []
        self._last_voiced_at = 0.0
        self._decision: asyncio.Task[None] | None = None
        self._speculative: asyncio.Task[tuple[str, float]] | None = None
        self._decision_epoch = 0
        self._awaiting_request = False
        self._pending_user_text = ""
        self._merge_prefix = ""
        self._response: _Response | None = None
        self._tool_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._seq = 0
        self._turn_index = 0
        self._turn_metrics: dict[str, Any] = {}
        self._closed = False
        self._messages: list[dict[str, Any]] = [{"role": "system", "content": self._system_text()}]
        for item in history or []:
            role = item.get("role")
            if role in ("user", "assistant") and item.get("text"):
                self._messages.append({"role": role, "content": str(item["text"])})
        self._prime()

    # ── inputs from the adapter ─────────────────────────────────────────────

    def on_audio(self, pcm16: bytes) -> None:
        if self._closed:
            return
        samples = to_float32(pcm16)
        self._tail = np.concatenate([self._tail, samples]) if self._tail.size else samples
        while self._tail.size >= FRAME_SAMPLES:
            frame, self._tail = self._tail[:FRAME_SAMPLES], self._tail[FRAME_SAMPLES:]
            self._on_frame(frame)

    def on_response_request(self, language: str | None = None) -> None:
        if language:
            self._set_language(language)
        if not self._awaiting_request:
            # A request for a turn the user has since continued; the merged
            # transcript brings its own request.
            return
        self._awaiting_request = False
        self._start_response(self._pending_user_text)

    def on_text(self, text: str) -> None:
        """Typed input or an announcement prompt: answer it right away."""
        text = text.strip()
        if not text or self._closed:
            return
        self._cancel_response(self_initiated=True)
        self._start_response(text)

    def on_tool_result(self, call_id: str, result: dict[str, Any]) -> None:
        waiter = self._tool_waiters.pop(call_id, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(result)

    def on_interrupt(self) -> None:
        self._cancel_response(self_initiated=True)

    def on_truncate(self, audio_end_ms: float) -> None:
        """Keep only what the listener actually heard of the last answer."""
        for index in range(len(self._messages) - 1, 0, -1):
            message = self._messages[index]
            if message.get("role") == "assistant" and message.get("_clauses"):
                heard = [c for c in message["_clauses"] if c.start_ms < audio_end_ms]
                message["content"] = " ".join(c.text for c in heard)
                message["_clauses"] = heard
                return

    def on_update(
        self,
        *,
        instructions: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        language: str | None = None,
    ) -> None:
        if instructions is not None:
            self._instructions = instructions
        if tools is not None:
            self._tools = _ollama_tools(tools)
        if language:
            self._set_language(language)
        self._messages[0] = {"role": "system", "content": self._system_text()}
        self._prime()

    def close(self) -> None:
        self._closed = True
        self._cancel_response(self_initiated=True)
        if self._decision is not None:
            self._decision.cancel()
        for waiter in self._tool_waiters.values():
            if not waiter.done():
                waiter.cancel()
        self._tool_waiters.clear()

    # ── listening ──────────────────────────────────────────────────────────

    def _on_frame(self, frame: np.ndarray) -> None:
        probability = self._vad(frame)
        now = time.monotonic()
        response = self._response
        if response is not None and not response.cancel.is_set():
            event = self._barge.feed(probability)
            self._preroll.append(frame)
            if event is not None and event.kind == "speech_start":
                self._barge_in(response)
                self._begin_utterance(now, carry_preroll=True)
            return
        self._barge.reset()
        if probability >= self._endpointer.on_threshold:
            self._last_voiced_at = now
            # Speech resumed inside a short pause: an early transcript is stale.
            self._speculative = None
        event = self._endpointer.feed(probability)
        if event is not None and event.kind == "speech_start":
            # The frames that proved speech are in the pre-roll; the utterance
            # starts with them, then this frame.
            self._begin_utterance(now, carry_preroll=True)
        if self._endpointer.in_speech:
            self._utterance.append(frame)
        else:
            self._preroll.append(frame)
        if event is None:
            too_long = self._utterance_seconds() > self._config.max_utterance_s
            if self._endpointer.in_speech and too_long:
                self._start_decision(force=True)
            return
        if event.kind == "silence_early":
            self._speculative = self._loop.create_task(self._transcribe(self._utterance_audio()))
        elif event.kind == "silence_start":
            self._start_decision(force=False)
        elif event.kind == "silence_end":
            # The user resumed before the turn was taken: the pending decision
            # describes a sentence that is still growing.
            self._decision_epoch += 1
            self._speculative = None

    def _begin_utterance(self, now: float, *, carry_preroll: bool) -> None:
        if self._awaiting_request:
            # The user continued after the transcript left but before the app
            # asked for an answer: one turn, not two.
            self._awaiting_request = False
            self._merge_prefix = self._pending_user_text
        if carry_preroll:
            self._utterance = list(self._preroll)
            self._preroll.clear()
        self._endpointer.start_speech()
        self._turn_metrics = {"speech_start": now}
        self._emit.json({"type": p.SPEECH_STARTED, "session": self.session_id})

    def _utterance_seconds(self) -> float:
        return sum(f.size for f in self._utterance) / INPUT_RATE

    def _utterance_audio(self) -> np.ndarray:
        if not self._utterance:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._utterance)

    async def _transcribe(self, audio: np.ndarray) -> tuple[str, float]:
        return await asyncio.to_thread(
            self._locked, "stt", self._models.stt.transcribe, _with_noise_tail(audio)
        )

    def _start_decision(self, *, force: bool) -> None:
        self._decision_epoch += 1
        epoch = self._decision_epoch
        audio = self._utterance_audio()
        stt = self._speculative or self._loop.create_task(self._transcribe(audio))
        self._speculative = None
        speech_end = self._last_voiced_at
        if self._decision is not None and not self._decision.done():
            self._decision.cancel()
        self._decision = self._loop.create_task(
            self._decide(epoch, audio, speech_end, force, stt)
        )

    async def _decide(self, epoch: int, audio: np.ndarray, speech_end: float, force: bool,
                      stt: asyncio.Task[tuple[str, float]]) -> None:
        started = time.monotonic()
        turn_job = (
            asyncio.to_thread(self._locked, "turn", self._models.turn.probability, audio)
            if not force
            else _ready(1.0)
        )
        try:
            (text, stt_ms), (probability, turn_ms) = await asyncio.gather(stt, turn_job)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("transcription or turn decision failed")
            self._emit.json({"type": p.ERROR, "session": self.session_id, "code": "stt_failed",
                             "message": "Speech recognition failed for this turn.",
                             "recoverable": True})
            return
        if epoch != self._decision_epoch:
            return
        self._turn_metrics.update({
            "speech_end": speech_end, "stt_ms": round(stt_ms, 1), "turn_ms": round(turn_ms, 1),
            "p_complete": round(float(probability), 3),
            "decision_ms": round((time.monotonic() - started) * 1000.0, 1),
        })
        waited = 0
        while (not force and probability < self._config.turn_threshold
               and waited < self._config.incomplete_wait_ms):
            step = min(self._config.reevaluate_ms, self._config.incomplete_wait_ms - waited)
            await asyncio.sleep(step / 1000.0)
            waited += step
            if epoch != self._decision_epoch:
                return
            probability, _ms = await asyncio.to_thread(
                self._locked, "turn", self._models.turn.probability, self._utterance_audio()
            )
            if epoch != self._decision_epoch:
                return
        if waited:
            self._turn_metrics["waited_incomplete_ms"] = waited
            self._turn_metrics["p_complete_final"] = round(float(probability), 3)
        self._finalize(text)

    def _finalize(self, text: str) -> None:
        text = " ".join(part for part in (self._merge_prefix, text.strip()) if part).strip()
        self._merge_prefix = ""
        self._utterance = []
        self._endpointer.reset()
        self._vad.reset()
        if not text:
            return
        self._turn_index += 1
        voiced_ms = int(1000 * (self._turn_metrics.get("speech_end", 0) -
                                self._turn_metrics.get("speech_start", 0)))
        self._turn_metrics["final_at"] = time.monotonic()
        self._pending_user_text = text
        self._awaiting_request = True
        self._emit.json({"type": p.TRANSCRIPT_INPUT, "session": self.session_id, "text": text,
                         "final": True, "voiced_ms": max(0, voiced_ms)})

    # ── answering ──────────────────────────────────────────────────────────

    def _start_response(self, user_text: str) -> None:
        self._cancel_response(self_initiated=True)
        response = _Response(requested_at=time.monotonic())
        self._response = response
        self._barge.reset()
        response.task = self._loop.create_task(self._respond(response, user_text))

    async def _respond(self, response: _Response, user_text: str) -> None:
        messages = self._bounded_messages() + [{"role": "user", "content": user_text}]
        self._messages.append({"role": "user", "content": user_text})
        speech: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = self._loop.create_task(self._speak(response, speech))
        status = "completed"
        try:
            for _round in range(self._config.max_tool_rounds + 1):
                result = await asyncio.to_thread(self._chat_round, response, messages, speech)
                if response.cancel.is_set():
                    status = "cancelled"
                    break
                if result.error:
                    raise RuntimeError(result.error)
                if not result.tool_calls:
                    break
                response.tool_rounds += 1
                calls = [
                    {"id": f"call_{uuid.uuid4().hex[:12]}", **call} for call in result.tool_calls
                ]
                messages.append({"role": "assistant", "content": result.text,
                                 "tool_calls": [{"function": {"name": c["name"],
                                                              "arguments": c["arguments"]}}
                                                for c in calls]})
                outcomes = await asyncio.gather(*(self._run_tool(c) for c in calls))
                for call, outcome in zip(calls, outcomes, strict=True):
                    messages.append({"role": "tool", "tool_name": call["name"],
                                     "content": json.dumps(outcome, ensure_ascii=False)})
                if response.cancel.is_set():
                    status = "cancelled"
                    break
            await speech.put(None)
            await speaker
        except asyncio.CancelledError:
            status = "cancelled"
            speaker.cancel()
            raise
        except Exception as exc:
            log.exception("response failed")
            status = "failed"
            speaker.cancel()
            self._emit.json({"type": p.ERROR, "session": self.session_id, "code": "llm_failed",
                             "message": f"The local language model failed: {exc}",
                             "recoverable": True})
        finally:
            spoken = [c for c in response.clauses if c.start_ms <= response.audio_ms]
            if spoken:
                message = {"role": "assistant", "content": " ".join(c.text for c in spoken)}
                message["_clauses"] = spoken
                self._messages.append(message)
            if self._response is response:
                self._response = None
            final_status = "cancelled" if response.cancel.is_set() else status
            self._emit_metrics(response, final_status)
            self._emit.json({"type": p.RESPONSE_DONE, "session": self.session_id,
                             "status": final_status})

    def _chat_round(self, response: _Response, messages: list[dict[str, Any]],
                    speech: asyncio.Queue[str | None]) -> Any:
        def on_clause(clause: str, _at: float) -> None:
            self._loop.call_soon_threadsafe(speech.put_nowait, clause)

        result = self._models.llm.chat(
            messages, tools=self._tools or None, chunker=ClauseChunker(), on_clause=on_clause,
            stop=response.cancel,
        )
        first = getattr(result, "t_first_clause", None)
        if first is not None and "llm_first_clause_ms" not in self._turn_metrics:
            self._turn_metrics["llm_first_clause_ms"] = round(first * 1000.0, 1)
        return result

    async def _run_tool(self, call: dict[str, Any]) -> dict[str, Any]:
        waiter: asyncio.Future[dict[str, Any]] = self._loop.create_future()
        self._tool_waiters[call["id"]] = waiter
        self._emit.json({"type": p.TOOL_CALL, "session": self.session_id, "call_id": call["id"],
                         "name": call["name"], "arguments": call["arguments"]})
        try:
            return await asyncio.wait_for(waiter, self._config.tool_timeout_s)
        except TimeoutError:  # the timeout is returned to the model as the tool result
            self._tool_waiters.pop(call["id"], None)
            return {"success": False, "error": "The tool did not answer in time."}

    async def _speak(self, response: _Response, speech: asyncio.Queue[str | None]) -> None:
        tts = self._models.tts_for(self._language)
        previous = ""
        while True:
            clause = await speech.get()
            if clause is None or response.cancel.is_set():
                return
            clause = speakable(clause)
            if not clause:
                continue
            entry = _Clause(text=clause, start_ms=response.audio_ms)
            response.clauses.append(entry)
            self._emit.json({"type": p.TRANSCRIPT_OUTPUT, "session": self.session_id,
                             "delta": clause + " "})
            pause_ms = _pause_after(previous) if previous else 0
            await asyncio.to_thread(self._synthesize, tts, clause, response, pause_ms)
            entry.end_ms = response.audio_ms
            previous = clause

    def _synthesize(self, tts: TtsModel, text: str, response: _Response, pause_ms: int) -> None:
        """Stream one clause, dropping the voice's own silence at both ends.

        Voices pad clauses with up to a second of near-silence at the start
        and a few hundred milliseconds at the end; played as is, the first
        word arrives late and clauses drift apart. The lead-in is dropped, a
        quiet stretch is held back until it proves to be a pause inside the
        clause (then it is played) or the clause ends (then it is dropped), and
        the gap between clauses is a deliberate ``pause_ms``.
        """
        frame = OUTPUT_RATE * self._config.output_frame_ms // 1000
        keep = OUTPUT_RATE // 50  # 20 ms of onset and decay around the voice
        threshold = self._config.tts_silence
        out = np.zeros(0, dtype=np.float32)
        quiet = np.zeros(0, dtype=np.float32)
        started = False
        with self._models.lock(f"tts:{getattr(tts, 'name', 'tts')}:{self._language}"):
            for chunk in tts.stream(text, stop=response.cancel):
                if response.cancel.is_set():
                    return
                audio = resample(np.asarray(chunk, dtype=np.float32), tts.sample_rate, OUTPUT_RATE)
                loud = np.flatnonzero(np.abs(audio) > threshold)
                if not started:
                    if loud.size == 0:
                        continue
                    audio = audio[max(0, int(loud[0]) - keep):]
                    loud = loud - loud[0] + min(int(loud[0]), keep)
                    started = True
                    if pause_ms:
                        out = np.zeros(OUTPUT_RATE * pause_ms // 1000, dtype=np.float32)
                if loud.size == 0:
                    quiet = np.concatenate([quiet, audio])
                    continue
                cut = min(audio.size, int(loud[-1]) + 1 + keep)
                out = np.concatenate([out, quiet, audio[:cut]])
                quiet = audio[cut:]
                while out.size >= frame:
                    piece, out = out[:frame], out[frame:]
                    self._loop.call_soon_threadsafe(self._send_audio, response, piece)
            if out.size and not response.cancel.is_set():
                self._loop.call_soon_threadsafe(self._send_audio, response, out)

    def _send_audio(self, response: _Response, samples: np.ndarray) -> None:
        if response.cancel.is_set() or self._closed:
            return
        if response.first_audio_at is None:
            response.first_audio_at = time.monotonic()
        response.audio_ms += 1000.0 * samples.size / OUTPUT_RATE
        self._seq += 1
        self._emit.audio(self.slot, self._seq, to_pcm16(samples))

    # ── interruption ───────────────────────────────────────────────────────

    def _barge_in(self, response: _Response) -> None:
        before_audio = response.first_audio_at is None
        if before_audio and self._pending_user_text:
            # Nothing was heard yet: the user is still finishing the request.
            self._merge_prefix = self._pending_user_text
            if self._messages and self._messages[-1].get("role") == "user":
                self._messages.pop()
        self._cancel_response(self_initiated=False)

    def _cancel_response(self, *, self_initiated: bool) -> None:
        response = self._response
        if response is None or response.cancel.is_set():
            return
        response.cancel.set()
        response.self_initiated_cancel = self_initiated
        for waiter in self._tool_waiters.values():
            if not waiter.done():
                waiter.set_result({"success": False, "error": "cancelled"})
        self._tool_waiters.clear()
        self._emit.json({"type": p.INTERRUPTED, "session": self.session_id,
                         "self_initiated": self_initiated, "audio_ms": round(response.audio_ms)})

    # ── helpers ────────────────────────────────────────────────────────────

    def _prime(self) -> None:
        """Pre-evaluate system prompt and tools so the first turn hits the cache."""
        prime = getattr(self._models.llm, "prime", None)
        if not callable(prime):
            return
        prefix = [self._bounded_messages()[0]]

        def run() -> None:
            try:
                prime(prefix, self._tools or None)
            except Exception:
                # Priming only saves latency; a failure leaves the cold path.
                log.warning("priming the LLM prompt cache failed", exc_info=True)

        self._loop.run_in_executor(None, run)

    def _locked(self, name: str, fn: Callable[..., Any], *args: Any) -> tuple[Any, float]:
        with self._models.lock(name):
            started = time.perf_counter()
            value = fn(*args)
            return value, (time.perf_counter() - started) * 1000.0

    def _set_language(self, language: str) -> None:
        language = language.split("-")[0].lower()
        if language and language != self._language:
            self._language = language
            self._messages[0] = {"role": "system", "content": self._system_text()}

    def _system_text(self) -> str:
        name = LANGUAGE_NAMES.get(self._language, self._language)
        rule = f"Reply in {name}. Keep spoken answers short: one to three sentences."
        return f"{self._instructions.strip()}\n\n{rule}".strip()

    def _bounded_messages(self) -> list[dict[str, Any]]:
        history = [
            {k: v for k, v in m.items() if not k.startswith("_")} for m in self._messages[1:]
        ][-self._config.max_history_messages :]
        return [self._messages[0], *history]

    def _emit_metrics(self, response: _Response, status: str) -> None:
        metrics = dict(self._turn_metrics)
        speech_end = metrics.pop("speech_end", None)
        metrics.pop("speech_start", None)
        final_at = metrics.pop("final_at", None)
        if response.first_audio_at is not None and speech_end:
            metrics["speech_end_to_first_audio_ms"] = round(
                (response.first_audio_at - speech_end) * 1000.0, 1
            )
        if final_at is not None:
            metrics["final_to_request_ms"] = round((response.requested_at - final_at) * 1000.0, 1)
        if response.first_audio_at is not None:
            metrics["request_to_first_audio_ms"] = round(
                (response.first_audio_at - response.requested_at) * 1000.0, 1
            )
        metrics.update({"turn": self._turn_index, "status": status,
                        "tool_rounds": response.tool_rounds,
                        "audio_ms": round(response.audio_ms)})
        self._turn_metrics = {}
        self._emit.json({"type": p.METRICS, "session": self.session_id, "turn": metrics})


async def _ready(value: float) -> tuple[float, float]:
    return value, 0.0


_NOISE = np.random.default_rng(7)


_MARKDOWN = re.compile(r"(\*\*|__|\*|`+|^#+\s*|^\s*[-*•]\s+|\[([^\]]*)\]\([^)]*\))", re.M)


def speakable(text: str) -> str:
    """Drop Markdown a model may emit; a voice must not read asterisks aloud."""
    cleaned = _MARKDOWN.sub(lambda m: m.group(2) or "", text)
    return " ".join(cleaned.split())


def _pause_after(clause: str) -> int:
    """Gap before the next clause, from how the previous one ended."""
    end = clause.rstrip()[-1:]
    if end in ".!?…":
        return 250
    if end in ",;:–—":
        return 150
    return 100


def _with_noise_tail(audio: np.ndarray, seconds: float = 0.15) -> np.ndarray:
    """Close the segment with a little room noise (about -60 dBFS).

    The recogniser clips the last word of a segment that ends abruptly; a short
    noise floor behaves like the microphone it stands in for, where digital
    zeros made it invent words in the bench.
    """
    tail = (_NOISE.standard_normal(int(seconds * INPUT_RATE)) * 0.001).astype(np.float32)
    return np.concatenate([np.asarray(audio, dtype=np.float32), tail])


def _ollama_tools(declarations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Realtime-style declarations ({name, description, parameters}) to Ollama's shape."""
    tools = []
    for item in declarations:
        if item.get("type") == "function" and "function" in item:
            tools.append(item)
            continue
        name = item.get("name")
        if not name:
            continue
        tools.append({"type": "function", "function": {
            "name": name, "description": item.get("description", ""),
            "parameters": item.get("parameters") or {"type": "object", "properties": {}},
        }})
    return tools
