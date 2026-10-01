"""Bench suites for the measurements in section 8 of the rebuild plan.

Each suite returns a JSON-serialisable payload; ``write_report`` stores it
together with the machine description and the load before and after the run.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from jarvis.voice_engine.audio import STT_RATE, resample, silence
from jarvis.voice_engine.bench import corpus
from jarvis.voice_engine.bench.env import describe, gpus, load_snapshot
from jarvis.voice_engine.bench.speech import Components, UserVoice, save_sample
from jarvis.voice_engine.bench.stats import error_rates, summary
from jarvis.voice_engine.llm import OllamaChat
from jarvis.voice_engine.paths import results_dir
from jarvis.voice_engine.vad import FRAME_SAMPLES, Endpointer

SILENCE_MS = 200
INCOMPLETE_WAIT_MS = 1000
COMPLETE_THRESHOLD = 0.5


def write_report(suite: str, payload: dict[str, Any], load_before: dict[str, Any]) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = results_dir() / f"{suite}-{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "suite": suite,
        "created_utc": stamp,
        "machine": describe(),
        "load_before": load_before,
        **payload,
    }
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _timed(fn: Callable[..., Any], *args: Any) -> tuple[Any, float]:
    started = time.perf_counter()
    value = fn(*args)
    return value, (time.perf_counter() - started) * 1000.0


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000.0, 1)


# ── STT ──────────────────────────────────────────────────────────────────────


def run_stt(languages: list[str], voices: list[str], repeats: int = 2) -> dict[str, Any]:
    components = Components()
    stt = components.stt()
    items: list[dict[str, Any]] = []
    for kind in voices:
        voice = UserVoice(components, kind)
        for language in languages:
            texts = corpus.load(language)["roundtrip"]
            stt.transcribe(voice.render(texts[0], language))  # warm-up, not recorded
            for text in texts:
                audio = voice.render(text, language)
                times = []
                hypothesis = ""
                for _ in range(repeats):
                    hypothesis, elapsed = _timed(stt.transcribe, audio)
                    times.append(elapsed)
                cer, wer = error_rates(text, hypothesis)
                items.append({
                    "voice": kind, "language": language, "text": text,
                    "hypothesis": hypothesis, "audio_s": round(audio.size / STT_RATE, 2),
                    "stt_ms": round(min(times), 1), "cer": round(cer, 4), "wer": round(wer, 4),
                })
    groups: dict[str, Any] = {}
    for kind in voices:
        for language in languages:
            rows = [i for i in items if i["voice"] == kind and i["language"] == language]
            groups[f"{kind}/{language}"] = {
                "stt_ms": summary(r["stt_ms"] for r in rows),
                "rtfx_p50": round(
                    float(np.median([r["audio_s"] * 1000 / r["stt_ms"] for r in rows])), 1
                ),
                "cer_mean": round(float(np.mean([r["cer"] for r in rows])), 4),
                "wer_mean": round(float(np.mean([r["wer"] for r in rows])), 4),
            }
    return {"config": {"voices": voices, "repeats": repeats}, "load_s": components.load_s,
            "groups": groups, "items": items}


# ── TTS ──────────────────────────────────────────────────────────────────────


def run_tts(engine: str, languages: list[str], options: dict[str, Any]) -> dict[str, Any]:
    components = Components()
    items: list[dict[str, Any]] = []
    gpu_before = gpus()
    gpu_after_load: dict[str, Any] = {}
    for language in languages:
        tts = components.tts(engine, language, **options)
        gpu_after_load[language] = gpus()
        warm = getattr(tts, "warmup", None)
        if callable(warm):
            warm()
        list(tts.stream("Hallo." if language == "de" else "Hello."))  # warm-up, not recorded
        for index, text in enumerate(corpus.load(language)["roundtrip"]):
            started = time.perf_counter()
            first: float | None = None
            chunks = []
            for chunk in tts.stream(text):
                if first is None:
                    first = time.perf_counter() - started
                chunks.append(chunk)
            total = time.perf_counter() - started
            audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
            duration = audio.size / tts.sample_rate
            sample = save_sample(f"{tts.name}/{language}/{index:02d}", audio, tts.sample_rate)
            items.append({
                "language": language, "text": text, "sample": str(sample),
                "ttfa_ms": _ms(first), "synth_s": round(total, 3),
                "audio_s": round(duration, 2),
                "speed_x_realtime": round(duration / total, 2) if total > 0 else None,
            })
    try:
        _regrade_items(items, components)
    except ImportError:
        # This environment has no transcription runtime (e.g. a CUDA-only TTS
        # venv); ``bench regrade`` fills the error rates in from the CPU venv.
        pass
    return {"engine": engine, "options": options, "load_s": components.load_s,
            "gpu_before": gpu_before, "gpu_after_load": gpu_after_load,
            "torch_cuda_peak_mb": _torch_cuda_peak_mb(),
            "groups": _tts_groups(items, languages), "items": items}


def _torch_cuda_peak_mb() -> float | None:
    import sys  # noqa: PLC0415

    torch = sys.modules.get("torch")
    if torch is None or not torch.cuda.is_available():
        return None
    return round(torch.cuda.max_memory_reserved() / 1e6, 1)


def _regrade_items(items: list[dict[str, Any]], components: Components) -> None:
    from jarvis.voice_engine.audio import read_wav  # noqa: PLC0415

    stt = components.stt()
    pad = silence(0.2, STT_RATE)
    for item in items:
        audio, rate = read_wav(Path(item["sample"]))
        # The live engine hands the recogniser a VAD segment with pre-roll and
        # trailing silence; raw TTS output starts on the first phoneme, which
        # makes Parakeet drop or clip the first word.
        hypothesis = stt.transcribe(np.concatenate([pad, resample(audio, rate, STT_RATE), pad]))
        cer, wer = error_rates(item["text"], hypothesis)
        item.update({"asr": hypothesis, "cer": round(cer, 4), "wer": round(wer, 4)})


def _tts_groups(items: list[dict[str, Any]], languages: list[str]) -> dict[str, Any]:
    groups = {}
    for language in languages:
        rows = [i for i in items if i["language"] == language]
        graded = [r["cer"] for r in rows if r.get("cer") is not None]
        groups[language] = {
            "ttfa_ms": summary(r["ttfa_ms"] for r in rows),
            "speed_x_realtime_p50": float(np.median([r["speed_x_realtime"] for r in rows])),
            "cer_mean": round(float(np.mean(graded)), 4) if graded else None,
        }
    return groups


def regrade(report_path: Path) -> Path:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    components = Components()
    _regrade_items(report["items"], components)
    languages = sorted({i["language"] for i in report["items"]})
    report["groups"] = _tts_groups(report["items"], languages)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report_path


# ── Turn-taking ──────────────────────────────────────────────────────────────


def _silence_events(vad: Any, audio: np.ndarray) -> list[tuple[float, float]]:
    """(silence_start time, last voiced time) for every pause the endpointer reports."""
    vad.reset()
    endpointer = Endpointer(silence_ms=SILENCE_MS)
    last_voiced = 0.0
    events = []
    for start in range(0, audio.size - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        probability = vad(audio[start : start + FRAME_SAMPLES])
        now = (start + FRAME_SAMPLES) / STT_RATE
        if probability >= endpointer.on_threshold:
            last_voiced = now
        event = endpointer.feed(probability)
        if event is not None and event.kind == "silence_start":
            events.append((now, last_voiced))
    return events


def run_turn(languages: list[str], voices: list[str]) -> dict[str, Any]:
    components = Components()
    vad = components.vad()
    turn = components.turn()
    items: list[dict[str, Any]] = []
    for kind in voices:
        voice = UserVoice(components, kind)
        for language in languages:
            for case in corpus.load(language)["hesitations"]:
                audio, ends = voice.render_with_pauses(
                    case["segments"], case["pauses_ms"], language
                )
                for at, last_voiced in _silence_events(vad, audio):
                    probability, elapsed = _timed(turn.probability, audio[: int(at * STT_RATE)])
                    items.append({
                        "voice": kind, "language": language,
                        "utterance": " … ".join(case["segments"]),
                        "at_s": round(at, 3), "final": last_voiced >= ends[-1] - 0.05,
                        "p_complete": round(probability, 3), "turn_ms": round(elapsed, 1),
                    })
    groups = {}
    for kind in voices:
        for language in languages:
            rows = [i for i in items if i["voice"] == kind and i["language"] == language]
            inner = [r for r in rows if not r["final"]]
            final = [r for r in rows if r["final"]]
            groups[f"{kind}/{language}"] = {
                "pause_events": len(inner),
                "cut_rate": round(
                    sum(r["p_complete"] >= COMPLETE_THRESHOLD for r in inner) / len(inner), 3
                ) if inner else None,
                "end_events": len(final),
                "end_complete_rate": round(
                    sum(r["p_complete"] >= COMPLETE_THRESHOLD for r in final) / len(final), 3
                ) if final else None,
                "turn_ms": summary(r["turn_ms"] for r in rows),
            }
    return {"config": {"silence_ms": SILENCE_MS, "threshold": COMPLETE_THRESHOLD},
            "load_s": components.load_s, "groups": groups, "items": items}


# ── LLM ──────────────────────────────────────────────────────────────────────

_FOLLOW_UP = {
    "de": "Und das bitte noch kürzer.",  # i18n-allow: German speech-input fixture
    "en": "Say that even shorter, please.",
}


def run_llm(model: str, languages: list[str], *, with_tools: bool, num_gpu: int | None,
            base_url: str) -> dict[str, Any]:
    llm = OllamaChat(model, base_url=base_url, num_gpu=num_gpu)
    llm.unload()
    cold_load_s = llm.warm()
    tools = corpus.tools() if with_tools else None
    items: list[dict[str, Any]] = []
    for language in languages:
        system = {"role": "system", "content": corpus.load(language)["system_prompt"]}
        for question in corpus.load(language)["dialog"]:
            first = llm.chat([system, {"role": "user", "content": question}], tools=tools)
            history = [system, {"role": "user", "content": question},
                       {"role": "assistant", "content": first.text},
                       {"role": "user", "content": _FOLLOW_UP[language]}]
            second = llm.chat(history, tools=tools)
            items.append({
                "language": language, "question": question, "answer": first.text,
                "error": first.error or second.error,
                "ttft_ms": _ms(first.t_first_token), "first_clause_ms": _ms(first.t_first_clause),
                "done_ms": _ms(first.t_done), "prompt_tokens": first.prompt_tokens,
                "prefill_tps": round(first.prefill_tps), "gen_tps": round(first.generation_tps),
                "turn2_ttft_ms": _ms(second.t_first_token),
                "turn2_first_clause_ms": _ms(second.t_first_clause),
                "turn2_prompt_tokens": second.prompt_tokens,
                "turn2_cached_tokens": second.prompt_cached_tokens,
                "tool_calls": first.tool_calls,
            })
    return {
        "model": model, "with_tools": with_tools, "num_gpu": num_gpu,
        "cold_load_s": round(cold_load_s, 2),
        "ollama_ps": llm.loaded_models(),
        "summary": {
            "ttft_ms": summary(i["ttft_ms"] for i in items),
            "first_clause_ms": summary(i["first_clause_ms"] for i in items),
            "turn2_first_clause_ms": summary(i["turn2_first_clause_ms"] for i in items),
            "gen_tps_p50": float(np.median([i["gen_tps"] for i in items])),
            "prefill_tps_p50": float(np.median([i["prefill_tps"] for i in items])),
        },
        "items": items,
    }


# ── Tool calls ───────────────────────────────────────────────────────────────


def run_tools(model: str, languages: list[str], *, limit: int | None, num_gpu: int | None,
              base_url: str, case_set: str = "main") -> dict[str, Any]:
    llm = OllamaChat(model, base_url=base_url, num_gpu=num_gpu, temperature=0.0)
    llm.warm()
    tools = corpus.tools()
    items: list[dict[str, Any]] = []
    for language in languages:
        system = {"role": "system", "content": corpus.load(language)["system_prompt"]}
        llm.chat([system, {"role": "user", "content": "Hi."}], tools=tools)  # warm-up
        cases = corpus.tool_cases(language, case_set)
        if limit:
            step = max(1, len(cases) // limit)
            cases = cases[::step][:limit]
        for case in cases:
            result = llm.chat([system, {"role": "user", "content": case.utterance}], tools=tools)
            verdict = corpus.grade(case, result.tool_calls, result.text)
            items.append({
                "language": language, "utterance": case.utterance, "expected": case.tool,
                "calls": result.tool_calls, "text": result.text[:400], "error": result.error,
                "thinking_chars": result.thinking_chars,
                "decision_ms": _ms(result.t_done), **verdict,
            })
    return {"model": model, "num_gpu": num_gpu, "case_set": case_set, "temperature": 0.0,
            "groups": tool_groups(items, languages), "items": items}


_VERDICTS = ("correct", "wrong_args", "wrong_tool", "missed", "malformed", "invented",
             "clarify", "unneeded", "forbidden")


def tool_groups(items: list[dict[str, Any]], languages: list[str]) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for language in languages:
        rows = [i for i in items if i["language"] == language]
        if not rows:
            continue
        needed = [r for r in rows if r["expected"] is not None]
        per_tool: dict[str, list[bool]] = {}
        for row in needed:
            per_tool.setdefault(row["expected"], []).append(row["verdict"] == "correct")
        groups[language] = {
            "cases": len(rows),
            "accuracy": round(sum(r["verdict"] == "correct" for r in rows) / len(rows), 3),
            "tool_accuracy": round(
                sum(r["verdict"] == "correct" for r in needed) / len(needed), 3
            ) if needed else None,
            "tool_accuracy_macro": round(
                float(np.mean([np.mean(v) for v in per_tool.values()])), 3
            ) if per_tool else None,
            "per_tool": {k: round(float(np.mean(v)), 3) for k, v in sorted(per_tool.items())},
            **{v: sum(r["verdict"] == v for r in rows) for v in _VERDICTS if v != "correct"},
            "alternatives": sum(r.get("note") == "accepted alternative" for r in rows),
            "thinking_items": sum(1 for r in rows if r.get("thinking_chars")),
            "decision_ms": summary(r["decision_ms"] for r in rows),
        }
    return groups


def regrade_tools(report_path: Path) -> Path:
    """Re-score a tools report with the current corpus and grader (stored calls)."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    case_set = report.get("case_set", "main")
    lookup = {
        (case.language, case.utterance): case
        for language in corpus.LANGUAGES
        for case in corpus.tool_cases(language, case_set)
    }
    for item in report["items"]:
        case = lookup.get((item["language"], item["utterance"]))
        if case is None:
            item["verdict"] = "unknown_case"
            continue
        for key in ("note", "failed", "args", "detail"):
            item.pop(key, None)
        item.update(corpus.grade(case, item.get("calls") or [], item.get("text") or ""))
    languages = sorted({i["language"] for i in report["items"]})
    report["groups"] = tool_groups(report["items"], languages)
    report["regraded_with"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report_path


# ── End to end ───────────────────────────────────────────────────────────────

_FAKE_RESULTS = {
    "set_timer": {"success": True, "output": "Timer started."},
    "set_reminder": {"success": True, "output": "Reminder saved."},
    "get_weather": {"success": True, "output": {"condition": "light rain", "temp_c": 14}},
    "calendar_list_events": {"success": True, "output": [{"title": "Team sync", "start": "10:00"}]},
    "calendar_create_event": {"success": True, "output": "Event created."},
    "open_app": {"success": True, "output": "Opened."},
    "play_music": {"success": True, "output": "Playing."},
    "set_volume": {"success": True, "output": "Volume set."},
    "search_web": {"success": True, "output": "ITER is scheduled to start operation in the 2030s."},
    "send_email": {"success": True, "output": "Sent."},
    "home_control": {"success": True, "output": "Done."},
    "take_appshot": {"success": True, "output": "A code editor showing a Python file."},
    "end_call": {"success": True, "output": "Ending."},
}


def _first_audio(tts: Any, text: str) -> float:
    stop = threading.Event()
    started = time.perf_counter()
    stream = tts.stream(text, stop=stop)
    try:
        next(stream)
    except StopIteration:
        # The engine produced no audio for this clause; time to "nothing" is
        # still the honest latency of the attempt.
        pass
    elapsed = (time.perf_counter() - started) * 1000.0
    stop.set()
    for _ in stream:
        pass
    return elapsed


def run_e2e(model: str, tts_kind: str, languages: list[str], *, voice_kind: str,
            num_gpu: int | None, base_url: str, tts_options: dict[str, Any]) -> dict[str, Any]:
    components = Components()
    vad, turn, stt = components.vad(), components.turn(), components.stt()
    llm = OllamaChat(model, base_url=base_url, num_gpu=num_gpu)
    llm.warm()
    tools = corpus.tools()
    voice = UserVoice(components, voice_kind)
    pool = cf.ThreadPoolExecutor(max_workers=2)
    items: list[dict[str, Any]] = []
    for language in languages:
        tts = components.tts(tts_kind, language, **tts_options)
        list(tts.stream("Hallo." if language == "de" else "Hello."))  # warm-up
        system = {"role": "system", "content": corpus.load(language)["system_prompt"]}
        texts = [(t, "dialog") for t in corpus.load(language)["dialog"]]
        seen: set[str] = set()
        for case in corpus.tool_cases(language):
            if case.tool and case.tool not in seen:
                seen.add(case.tool)
                texts.append((case.utterance, "tool"))
        for text, kind in texts:
            audio = np.concatenate([voice.render(text, language), silence(1.0, STT_RATE)])
            events = _silence_events(vad, audio)
            if not events:
                items.append({"language": language, "text": text, "error": "no end of speech"})
                continue
            at, last_voiced = events[-1]
            turn_future = pool.submit(_timed, turn.probability, audio[: int(at * STT_RATE)])
            stt_future = pool.submit(
                _timed, stt.transcribe, audio[: int((last_voiced + 0.1) * STT_RATE)]
            )
            probability, turn_ms = turn_future.result()
            transcript, stt_ms = stt_future.result()
            wait_ms = (at - last_voiced) * 1000.0
            decision_ms = wait_ms + max(turn_ms, stt_ms)
            if probability < COMPLETE_THRESHOLD:
                decision_ms += INCOMPLETE_WAIT_MS
            messages = [system, {"role": "user", "content": transcript}]
            first = llm.chat(messages, tools=tools)
            tool_round_ms = 0.0
            reply = first
            if first.tool_calls:
                call = first.tool_calls[0]
                tool_round_ms = first.t_done * 1000.0
                messages += [
                    {"role": "assistant", "content": first.text,
                     "tool_calls": [{"function": {"name": call["name"],
                                                  "arguments": call["arguments"]}}]},
                    {"role": "tool", "tool_name": call["name"],
                     "content": json.dumps(_FAKE_RESULTS.get(call["name"], {"success": True}))},
                ]
                reply = llm.chat(messages, tools=tools)
            clause = reply.clauses[0] if reply.clauses else reply.text.strip()
            reply_ms = tool_round_ms + 1000.0 * (reply.t_first_clause or reply.t_done)
            tts_ms = _first_audio(tts, clause) if clause else 0.0
            items.append({
                "language": language, "kind": kind, "text": text, "transcript": transcript,
                "tool_calls": [c["name"] for c in first.tool_calls], "first_clause": clause,
                "wait_ms": round(wait_ms, 1), "turn_ms": round(turn_ms, 1),
                "stt_ms": round(stt_ms, 1), "p_complete": round(probability, 3),
                "decision_ms": round(decision_ms, 1), "tool_round_ms": round(tool_round_ms, 1),
                "reply_first_clause_ms": round(reply_ms, 1), "tts_first_audio_ms": round(tts_ms, 1),
                "e2e_ms": round(decision_ms + reply_ms + tts_ms, 1),
            })
    pool.shutdown()
    done = [i for i in items if "e2e_ms" in i]
    return {
        "model": model, "tts": tts_kind, "tts_options": tts_options, "voice": voice_kind,
        "num_gpu": num_gpu, "load_s": components.load_s,
        "summary": {
            "e2e_ms_all": summary(i["e2e_ms"] for i in done),
            "e2e_ms_dialog": summary(i["e2e_ms"] for i in done if i["kind"] == "dialog"),
            "e2e_ms_tool": summary(i["e2e_ms"] for i in done if i["kind"] == "tool"),
            "decision_ms": summary(i["decision_ms"] for i in done),
            "stt_ms": summary(i["stt_ms"] for i in done),
            "turn_ms": summary(i["turn_ms"] for i in done),
            "reply_first_clause_ms": summary(i["reply_first_clause_ms"] for i in done),
            "tts_first_audio_ms": summary(i["tts_first_audio_ms"] for i in done),
            "incomplete_verdicts": sum(i["p_complete"] < COMPLETE_THRESHOLD for i in done),
        },
        "items": items,
    }


# ── Memory ───────────────────────────────────────────────────────────────────


def run_memory(languages: list[str], tts_kinds: list[str]) -> dict[str, Any]:
    import psutil  # noqa: PLC0415 - bench-only dependency

    process = psutil.Process()
    components = Components()
    steps = []
    loaders: list[tuple[str, Callable[[], Any]]] = [
        ("vad", components.vad), ("turn", components.turn), ("stt", components.stt),
    ]
    for kind in tts_kinds:
        for language in languages:
            loaders.append((f"tts:{kind}:{language}",
                            lambda k=kind, lang=language: components.tts(k, lang)))
    baseline = process.memory_info().rss
    for name, loader in loaders:
        before = process.memory_info().rss
        started = time.perf_counter()
        loader()
        steps.append({
            "component": name, "load_s": round(time.perf_counter() - started, 2),
            "rss_delta_mb": round((process.memory_info().rss - before) / 1e6, 1),
        })
    total_mb = round((process.memory_info().rss - baseline) / 1e6, 1)
    return {"languages": languages, "tts": tts_kinds, "steps": steps,
            "resident_total_mb": total_mb, "gpus": describe()["gpus"],
            "load_snapshot": load_snapshot()}
