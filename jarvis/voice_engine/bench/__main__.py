"""Command line for the P0 bench: ``python -m jarvis.voice_engine.bench <suite>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jarvis.voice_engine.bench import corpus
from jarvis.voice_engine.bench.env import describe, load_snapshot
from jarvis.voice_engine.llm import DEFAULT_BASE_URL
from jarvis.voice_engine.paths import results_dir


def _languages(value: str) -> list[str]:
    languages = [v.strip() for v in value.split(",") if v.strip()]
    unknown = [v for v in languages if v not in corpus.LANGUAGES]
    if unknown:
        raise argparse.ArgumentTypeError(f"no corpus for {unknown}; have {corpus.LANGUAGES}")
    return languages


def _list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _options(pairs: list[str]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for pair in pairs:
        key, _, raw = pair.partition("=")
        try:
            options[key] = json.loads(raw)
        except json.JSONDecodeError:
            options[key] = raw
    return options


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m jarvis.voice_engine.bench")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("env", help="describe this machine")
    fetch = sub.add_parser("fetch", help="download the registry models")
    fetch.add_argument("--only", type=_list, default=None)

    def common(p: argparse.ArgumentParser, *, llm: bool = False) -> None:
        p.add_argument("--languages", type=_languages, default=list(corpus.LANGUAGES))
        if llm:
            p.add_argument("--model", required=True)
            p.add_argument("--base-url", default=DEFAULT_BASE_URL)
            p.add_argument("--num-gpu", type=int, default=None,
                           help="0 forces the model onto the CPU")

    common(stt := sub.add_parser("stt", help="transcription speed and accuracy"))
    stt.add_argument("--voices", type=_list, default=["piper", "pocket"])
    common(tts := sub.add_parser("tts", help="speech synthesis speed and accuracy"))
    tts.add_argument("--engine", required=True, choices=["piper", "pocket", "qwen3"])
    tts.add_argument("--opt", action="append", default=[], help="backend option key=value")
    regrade = sub.add_parser("regrade", help="add ASR error rates to a TTS report")
    regrade.add_argument("report", type=Path)
    common(turn := sub.add_parser("turn", help="end-of-turn decisions on hesitations"))
    turn.add_argument("--voices", type=_list, default=["piper", "pocket"])
    common(llm := sub.add_parser("llm", help="LLM latency and prefix cache"), llm=True)
    llm.add_argument("--tools", action="store_true", help="declare the bench tool set")
    common(tools := sub.add_parser("tools", help="tool-call accuracy"), llm=True)
    tools.add_argument("--limit", type=int, default=None, help="cases per language")
    tools.add_argument("--set", dest="case_set", default="main", choices=["main", "hard"],
                       help="hard = utterances that must not end the call or grab the screen")
    regrade_tools = sub.add_parser("regrade-tools", help="re-score tools reports")
    regrade_tools.add_argument("reports", type=Path, nargs="+")
    common(e2e := sub.add_parser("e2e", help="speech end to first audio"), llm=True)
    e2e.add_argument("--tts", default="pocket", choices=["piper", "pocket", "qwen3"])
    e2e.add_argument("--voice", default="piper", choices=["piper", "pocket"])
    e2e.add_argument("--opt", action="append", default=[], help="TTS option key=value")
    common(memory := sub.add_parser("memory", help="resident memory per component"))
    memory.add_argument("--tts", type=_list, default=["piper", "pocket"])
    common(worker := sub.add_parser("worker", help="P1: real-time talk through the worker"),
           llm=True)
    worker.add_argument("--tts", default="pocket", choices=["piper", "pocket", "qwen3"])
    worker.add_argument("--voice", default="piper", choices=["piper", "pocket"])
    worker.add_argument("--opt", action="append", default=[], help="TTS option key=value")
    worker.add_argument("--barge-trials", type=int, default=3)
    drill = sub.add_parser("drill", help="P1: kill the worker's parent, measure the orphan")
    drill.add_argument("--trials", type=int, default=3)
    sub.add_parser("summary", help="print the latest result of every suite")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "env":
        print(json.dumps(describe(), indent=2))
        return 0
    if args.command == "fetch":
        from jarvis.voice_engine import models  # noqa: PLC0415

        for name in args.only or list(models.REGISTRY):
            path, digest = models.fetch(name, progress=_progress)
            print(f"{name}: {path} ({digest[:12]})")
        return 0
    if args.command == "summary":
        from jarvis.voice_engine.bench.report import print_summary  # noqa: PLC0415

        print_summary(results_dir())
        return 0
    from jarvis.voice_engine.bench import suites  # noqa: PLC0415

    before = load_snapshot()
    if args.command == "regrade":
        print(suites.regrade(args.report))
        return 0
    if args.command == "regrade-tools":
        for report in args.reports:
            print(suites.regrade_tools(report))
        return 0
    if args.command == "stt":
        payload = suites.run_stt(args.languages, args.voices)
    elif args.command == "tts":
        payload = suites.run_tts(args.engine, args.languages, _options(args.opt))
    elif args.command == "turn":
        payload = suites.run_turn(args.languages, args.voices)
    elif args.command == "llm":
        payload = suites.run_llm(args.model, args.languages, with_tools=args.tools,
                                 num_gpu=args.num_gpu, base_url=args.base_url)
    elif args.command == "tools":
        payload = suites.run_tools(args.model, args.languages, limit=args.limit,
                                   num_gpu=args.num_gpu, base_url=args.base_url,
                                   case_set=args.case_set)
    elif args.command == "e2e":
        payload = suites.run_e2e(args.model, args.tts, args.languages, voice_kind=args.voice,
                                 num_gpu=args.num_gpu, base_url=args.base_url,
                                 tts_options=_options(args.opt))
    elif args.command == "memory":
        payload = suites.run_memory(args.languages, args.tts)
    elif args.command == "worker":
        from jarvis.voice_engine.bench.worker_suite import run_worker  # noqa: PLC0415

        payload = run_worker(args.model, args.tts, args.languages, voice_kind=args.voice,
                             tts_options=_options(args.opt), barge_trials=args.barge_trials)
    elif args.command == "drill":
        from jarvis.voice_engine.bench.worker_suite import run_drill  # noqa: PLC0415

        payload = run_drill(args.trials)
    else:  # pragma: no cover - argparse enforces the choices
        raise SystemExit(f"unknown command {args.command}")
    path = suites.write_report(args.command, payload, before)
    headline = {k: v for k, v in payload.items() if k in ("summary", "groups", "load_s")}
    print(json.dumps(headline, indent=2, ensure_ascii=False))
    print(f"report: {path}")
    return 0


def _progress(name: str, done: int, total: int) -> None:
    percent = 100 * done / total if total else 0
    print(f"\r{name}: {done / 1e6:.0f} / {total / 1e6:.0f} MB ({percent:.0f} %)", end="",
          file=sys.stderr)
    if done >= total:
        print(file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
