#!/usr/bin/env python3
"""Generate the README-film voiceover and a deterministic timeline.

Same pipeline as gen_vo_promo.py, with one addition: per-line / per-kind VOICE
and RATE overrides, so the narrator (en-US-AndrewNeural) and Jarvis's spoken
replies (a distinct British "butler" voice) can differ inside one script.

Resolution order for a line's voice/rate:
    line["voice"]  ->  script["voices"][line["kind"]]  ->  script["voice"]
    line["rate"]   ->  script["rates"][line["kind"]]   ->  script["rate"]

Run from the `video/` directory:  python scripts/gen_vo_film.py
Re-run any time the script text changes; it is idempotent (edge-tts is
deterministic, so identical text+voice+rate gives byte-identical audio).
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # the video/ directory
SCRIPT_PATH = ROOT / "vo-script-film.json"
VO_DIR = ROOT / "public" / "vo-film"
OUT_PATH = ROOT / "src" / "intro" / "film" / "generated" / "timeline.json"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")


def synth(text: str, voice: str, rate: str, dest: Path) -> None:
    """Render one line to MP3 via edge-tts."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "edge_tts",
        "--voice", voice,
        "--rate", rate,
        "--text", text,
        "--write-media", str(dest),
    ]
    res = run(cmd)
    if res.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"edge-tts failed for {dest.name}:\n{res.stderr}\n{res.stdout}")


def probe_seconds(path: Path) -> float:
    res = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path),
    ])
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path.name}: {res.stderr}")
    return float(res.stdout.strip())


def main() -> None:
    script = json.loads(SCRIPT_PATH.read_text(encoding="utf-8"))
    default_voice = script["voice"]
    default_rate = script["rate"]
    voices_by_kind = script.get("voices", {})
    rates_by_kind = script.get("rates", {})
    t = script["timing"]
    fps = t["fps"]
    lead_in = t["leadIn"]
    gap = t["gap"]
    tail = t["tail"]
    overlap = t["overlap"]

    scenes_out = []
    audio_out = []

    abs_from = 0  # absolute start frame of the current scene (overlap-accumulated)

    for si, scene in enumerate(script["scenes"]):
        extra = scene.get("extra", 0)
        scene_lead = scene.get("leadIn", lead_in)
        lines_out = []
        cursor = scene_lead  # scene-local frame where the next line's audio starts

        for li, line in enumerate(scene["lines"]):
            kind = line["kind"]
            voice = line.get("voice") or voices_by_kind.get(kind) or default_voice
            rate = line.get("rate") or rates_by_kind.get(kind) or default_rate

            dest = VO_DIR / f"{line['id']}.mp3"
            tts_text = line.get("tts", line["text"])
            synth(tts_text, voice, rate, dest)
            secs = probe_seconds(dest)
            dur = max(1, math.ceil(secs * fps))

            local_start = cursor
            lines_out.append({
                "id": line["id"],
                "kind": kind,
                "text": line["text"],
                "file": f"vo-film/{line['id']}.mp3",
                "localStart": local_start,
                "dur": dur,
            })
            audio_out.append({
                "file": f"vo-film/{line['id']}.mp3",
                "from": abs_from + local_start,
                "dur": dur,
            })
            cursor += dur + gap
            print(f"  {line['id']:<12} {voice:<20} {secs:5.2f}s -> {dur:4d}f  @local {local_start}")

        # scene-local span: last line ends at (cursor - gap); add tail + extra hold
        audio_span_end = cursor - gap
        scene_dur = audio_span_end + tail + extra

        scenes_out.append({
            "id": scene["id"],
            "dur": scene_dur,
            "lines": lines_out,
        })
        # advance: overlap with the next scene for a true crossfade
        step = scene_dur - (overlap if si < len(script["scenes"]) - 1 else 0)
        abs_from += step

    total_frames = abs_from  # last scene added its full dur (no trailing overlap)

    timeline = {
        "fps": fps,
        "overlap": overlap,
        "totalFrames": total_frames,
        "scenes": scenes_out,
        "audio": audio_out,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(timeline, indent=2, ensure_ascii=False), encoding="utf-8")

    secs_total = total_frames / fps
    print(f"\nTimeline: {total_frames} frames @ {fps}fps = {secs_total:.1f}s "
          f"({int(secs_total // 60)}:{int(secs_total % 60):02d})")
    print(f"Wrote {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
