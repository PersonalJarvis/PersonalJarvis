"""Committed speech fixtures for the codex live probe: shape and size pins.

The WAVs are committed so running the probe needs no TTS key; these pins keep
the directory from quietly growing into a repo-weight problem and keep every
file in the exact shape the adapter consumes (24 kHz mono s16).
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

FIXTURE_DIR = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "audio" / "realtime"
)
MAX_FILE_BYTES = 500_000
MAX_DIR_BYTES = 4_000_000
EXPECTED_RATE = 24_000


def _manifest() -> list[dict]:
    return json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_and_wavs_agree() -> None:
    entries = _manifest()
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "duplicate fixture ids"
    assert "room_noise" in ids, "the probe's room tone is part of the contract"
    for entry in entries:
        path = FIXTURE_DIR / entry["path"]
        assert path.exists(), f"missing fixture file {entry['path']}"
        with wave.open(str(path), "rb") as handle:
            assert handle.getframerate() == EXPECTED_RATE, entry["id"]
            assert handle.getnchannels() == 1, entry["id"]
            assert handle.getsampwidth() == 2, entry["id"]
            duration_ms = int(
                handle.getnframes() / handle.getframerate() * 1000
            )
        assert abs(duration_ms - int(entry["duration_ms"])) <= 5, entry["id"]


def test_fixture_sizes_stay_bounded() -> None:
    total = 0
    for path in FIXTURE_DIR.glob("*.wav"):
        size = path.stat().st_size
        total += size
        assert size <= MAX_FILE_BYTES, f"{path.name} is {size} bytes"
    assert total <= MAX_DIR_BYTES, f"fixture dir grew to {total} bytes"


def test_speech_fixtures_carry_their_nonce() -> None:
    """The role-play check matches the literal question; every speech fixture
    must therefore name the nonce that makes it unmistakable."""
    for entry in _manifest():
        if entry["id"] == "room_noise":
            continue
        assert entry["nonce"], entry["id"]
        assert entry["nonce"].lower().split()[0] in entry["text"].lower(), entry["id"]


def test_room_noise_is_quiet_but_never_digital_zero() -> None:
    with wave.open(str(FIXTURE_DIR / "room_noise.wav"), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
    samples = [
        int.from_bytes(frames[i : i + 2], "little", signed=True)
        for i in range(0, min(len(frames), 96_000), 2)
    ]
    peak = max(abs(s) for s in samples)
    assert 0 < peak <= 120, f"room tone peak {peak} is not ~-55 dBFS"
