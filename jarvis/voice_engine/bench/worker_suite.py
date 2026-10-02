"""P1 gate measurements through the real worker process.

``run_worker`` starts the engine worker the way the app will (child process,
stdin/stdout frames), configures it, runs its self-test, then talks to it in
real time: synthetic user speech is streamed in 20 ms chunks at wall-clock
pace, the bench plays the app's part (requests the answer after the final
transcript, answers tool calls) and measures speech end -> first audio frame
on the receiving side, plus barge-in latency. ``run_drill`` kills the worker's
parent and measures how long the worker survives it.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from jarvis.voice_engine import protocol as p
from jarvis.voice_engine.audio import STT_RATE, silence, to_pcm16
from jarvis.voice_engine.bench import corpus
from jarvis.voice_engine.bench.speech import Components, UserVoice
from jarvis.voice_engine.bench.stats import summary
from jarvis.voice_engine.bench.suites import _FAKE_RESULTS
from jarvis.voice_engine.client import EngineClient, worker_env
from jarvis.voice_engine.paths import results_dir

CHUNK_SAMPLES = 320  # 20 ms, the browser's usual capture quantum
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_BARGE = {"de": "Stopp, warte mal kurz.", "en": "Stop, wait a moment."}  # i18n-allow: fixture
_LONG = {"de": "Erklär mir ausführlich, wie ein Schwarzes Loch entsteht.",  # i18n-allow: fixture
         "en": "Explain in detail how a black hole forms."}


def package_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _declarations() -> list[dict[str, Any]]:
    return [{"name": t["function"]["name"], "description": t["function"]["description"],
             "parameters": t["function"]["parameters"]} for t in corpus.tools()]


class _Conversation:
    """Plays the app's side of one session and timestamps what comes back."""

    def __init__(self, client: EngineClient, session: str, slot: int, language: str) -> None:
        self.client, self.session, self.slot, self.language = client, session, slot, language
        self.seq = 0
        self.events: list[tuple[float, dict[str, Any]]] = []
        self.first_audio_at: float | None = None
        self.last_audio_at: float | None = None
        self.audio_frames = 0
        self.done = asyncio.Event()
        self.interrupted_at: float | None = None
        self.metrics: dict[str, Any] = {}

    def reset(self) -> None:
        self.events.clear()
        self.first_audio_at = None
        self.last_audio_at = None
        self.audio_frames = 0
        self.done = asyncio.Event()
        self.interrupted_at = None
        self.metrics = {}

    async def pump(self) -> None:
        async def messages() -> None:
            while True:
                message = await self.client.messages.get()
                if message.get("type") == "_exited":
                    self.done.set()
                    return
                now = time.monotonic()
                self.events.append((now, message))
                kind = message.get("type")
                if kind == p.TRANSCRIPT_INPUT and message.get("final"):
                    await self.client.send({"type": p.RESPONSE_REQUEST, "session": self.session,
                                            "language": self.language})
                elif kind == p.TOOL_CALL:
                    result = _FAKE_RESULTS.get(message.get("name"), {"success": True})
                    await self.client.send({"type": p.TOOL_RESULT, "session": self.session,
                                            "call_id": message["call_id"], "result": result})
                elif kind == p.INTERRUPTED and self.interrupted_at is None:
                    self.interrupted_at = now
                elif kind == p.METRICS:
                    self.metrics = message.get("turn", {})
                elif kind == p.RESPONSE_DONE:
                    self.done.set()

        async def audio() -> None:
            while True:
                await self.client.audio_frames.get()
                now = time.monotonic()
                self.audio_frames += 1
                if self.first_audio_at is None:
                    self.first_audio_at = now
                self.last_audio_at = now

        await asyncio.gather(messages(), audio())

    async def stream(self, samples: np.ndarray, *, until_done_s: float = 0.0) -> float:
        """Send audio at wall-clock pace; returns the monotonic start time."""
        start = time.monotonic()
        for index in range(0, samples.size, CHUNK_SAMPLES):
            chunk = samples[index : index + CHUNK_SAMPLES]
            self.seq += 1
            await self.client.send_audio(self.slot, self.seq, to_pcm16(chunk))
            target = start + (index + CHUNK_SAMPLES) / STT_RATE
            await asyncio.sleep(max(0.0, target - time.monotonic()))
        if until_done_s > 0:
            quiet = silence(CHUNK_SAMPLES / STT_RATE, STT_RATE)
            tail_start = time.monotonic()
            while not self.done.is_set() and time.monotonic() - tail_start < until_done_s:
                self.seq += 1
                await self.client.send_audio(self.slot, self.seq, to_pcm16(quiet))
                await asyncio.sleep(CHUNK_SAMPLES / STT_RATE)
        return start


def _speech_end(samples: np.ndarray, threshold: float = 0.01) -> float:
    voiced = np.flatnonzero(np.abs(samples) > threshold)
    return (voiced[-1] + 1) / STT_RATE if voiced.size else 0.0


async def _run(model: str, tts: str, languages: list[str], voice_kind: str,
               tts_options: dict[str, Any], barge_trials: int) -> dict[str, Any]:
    client = EngineClient(sys.executable, env=worker_env(package_root=package_root()),
                          stderr_path=results_dir() / "worker-stderr.log")
    started = time.monotonic()
    hello = await client.start()
    await client.send({"type": p.CONFIGURE, "languages": languages, "tts": tts,
                       "tts_options": tts_options, "llm": {"model": model}})
    state = await client.wait_for(
        lambda m: m["type"] == p.STATE and m.get("phase") in ("ready", "failed"), 600
    )
    ready_s = round(time.monotonic() - started, 2)
    report: dict[str, Any] = {"model": model, "tts": tts, "tts_options": tts_options,
                              "voice": voice_kind, "hello": hello, "ready_s": ready_s,
                              "state": state}
    if state.get("phase") != "ready":
        await client.close()
        return report
    await client.send({"type": p.SELFTEST})
    report["selftest"] = await client.wait_for(lambda m: m["type"] == p.SELFTEST_RESULT, 300)

    voice = UserVoice(Components(), voice_kind)
    items: list[dict[str, Any]] = []
    barges: list[dict[str, Any]] = []
    for slot, language in enumerate(languages, start=1):
        session = f"bench-{language}"
        await client.send({"type": p.SESSION_OPEN, "session": session, "slot": slot,
                           "language": language, "tools": _declarations(),
                           "instructions": corpus.load(language)["system_prompt"]})
        await client.wait_for(lambda m, s=session: m["type"] == p.SESSION_READY
                              and m.get("session") == s, 30)
        convo = _Conversation(client, session, slot, language)
        pump = asyncio.get_running_loop().create_task(convo.pump())
        texts = [(t, "dialog") for t in corpus.load(language)["dialog"]]
        seen: set[str] = set()
        for case in corpus.tool_cases(language):
            if case.tool and case.tool not in seen and case.tool != "end_call":
                seen.add(case.tool)
                texts.append((case.utterance, "tool"))
        for text, kind in texts:
            convo.reset()
            samples = voice.render(text, language)
            start = await convo.stream(samples, until_done_s=20.0)
            speech_end = start + _speech_end(samples)
            first = convo.first_audio_at
            items.append({
                "language": language, "kind": kind, "text": text,
                "e2e_ms": round((first - speech_end) * 1000.0, 1) if first else None,
                "done": convo.done.is_set(), "audio_frames": convo.audio_frames,
                "worker": convo.metrics,
                "transcript": next((m.get("text") for _, m in convo.events
                                    if m.get("type") == p.TRANSCRIPT_INPUT), None),
                "tools": [m.get("name") for _, m in convo.events if m.get("type") == p.TOOL_CALL],
            })
            await convo.stream(silence(0.5, STT_RATE))
        for _ in range(barge_trials):
            convo.reset()
            await convo.stream(voice.render(_LONG[language], language), until_done_s=0.0)
            waited = time.monotonic()
            while convo.first_audio_at is None and time.monotonic() - waited < 15:
                await convo.stream(silence(0.1, STT_RATE))
            if convo.first_audio_at is None:
                barges.append({"language": language, "error": "no answer audio"})
                continue
            await convo.stream(silence(0.4, STT_RATE))
            barge = voice.render(_BARGE[language], language, pad_s=0.0)
            barge_start = time.monotonic()
            await convo.stream(np.concatenate([barge, silence(0.6, STT_RATE)]))
            interrupted = convo.interrupted_at
            leak = 0.0
            if interrupted is not None and convo.last_audio_at is not None:
                leak = max(0.0, convo.last_audio_at - interrupted)
            barges.append({
                "language": language,
                "interrupt_ms": round((interrupted - barge_start) * 1000.0, 1)
                if interrupted else None,
                "audio_after_interrupt_ms": round(leak * 1000.0, 1),
            })
            convo.done = asyncio.Event()
            await convo.stream(silence(0.3, STT_RATE), until_done_s=10.0)
        await client.send({"type": p.SESSION_CLOSE, "session": session})
        pump.cancel()
    exit_code = await client.close()
    answered = [i for i in items if i["e2e_ms"] is not None]
    report.update({
        "exit_code": exit_code,
        "summary": {
            "e2e_ms_dialog": summary(i["e2e_ms"] for i in answered if i["kind"] == "dialog"),
            "e2e_ms_tool": summary(i["e2e_ms"] for i in answered if i["kind"] == "tool"),
            "worker_speech_end_to_first_audio_ms": summary(
                i["worker"].get("speech_end_to_first_audio_ms") for i in answered),
            "stt_ms": summary(i["worker"].get("stt_ms") for i in answered),
            "turn_ms": summary(i["worker"].get("turn_ms") for i in answered),
            "llm_first_clause_ms": summary(i["worker"].get("llm_first_clause_ms")
                                           for i in answered),
            "unanswered": len(items) - len(answered),
            "barge_interrupt_ms": summary(b.get("interrupt_ms") for b in barges),
            "barge_audio_after_interrupt_ms": summary(
                b.get("audio_after_interrupt_ms") for b in barges),
        },
        "items": items, "barges": barges,
    })
    return report


def run_worker(model: str, tts: str, languages: list[str], *, voice_kind: str,
               tts_options: dict[str, Any], barge_trials: int = 3) -> dict[str, Any]:
    return asyncio.run(_run(model, tts, languages, voice_kind, tts_options, barge_trials))


_PARENT = r"""
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-m", "jarvis.voice_engine.worker"],
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=CREATIONFLAGS)
print(child.pid, flush=True)
time.sleep(600)
"""


def run_drill(trials: int = 3) -> dict[str, Any]:
    """Kill the worker's parent the hard way; the worker must exit on its own."""
    import psutil  # noqa: PLC0415 - bench-only dependency

    env = worker_env(package_root=package_root())
    results = []
    for _ in range(trials):
        script = _PARENT.replace("CREATIONFLAGS", str(_NO_WINDOW))
        parent = subprocess.Popen(  # noqa: S603 - fixed argv
            [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True, env=env,
            creationflags=_NO_WINDOW,
        )
        assert parent.stdout is not None
        worker_pid = int(parent.stdout.readline().strip())
        time.sleep(3.0)  # let the worker reach its read loop
        parent.kill()
        parent.wait()
        killed_at = time.monotonic()
        gone_after = None
        while time.monotonic() - killed_at < 10:
            if not psutil.pid_exists(worker_pid):
                gone_after = round(time.monotonic() - killed_at, 2)
                break
            try:
                if psutil.Process(worker_pid).status() == psutil.STATUS_ZOMBIE:
                    gone_after = round(time.monotonic() - killed_at, 2)
                    break
            except psutil.NoSuchProcess:  # the process being gone is exactly what is measured
                gone_after = round(time.monotonic() - killed_at, 2)
                break
            time.sleep(0.05)
        if gone_after is None:
            psutil.Process(worker_pid).kill()
        results.append({"worker_pid": worker_pid, "exited_after_s": gone_after})
    return {"trials": results,
            "orphans": sum(r["exited_after_s"] is None for r in results)}


def dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)
