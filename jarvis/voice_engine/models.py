"""The engine's model store: pinned sources, checksums, resumable downloads.

Every model the engine loads comes from this registry. Each entry pins an
immutable source (a release asset or a Hugging Face revision) and, once
verified, its SHA-256. Downloads resume after an interruption, are verified
before they become visible, and archives are unpacked with tarfile's data
filter. At runtime the engine only reads the store; it never touches the
network (``docs/local-live-voice-rebuild.md`` section 4.9).

Models that their own runtime package downloads (Pocket TTS, Qwen3-TTS) are not
listed here; the bench reports where those runtimes cache them.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jarvis.voice_engine.paths import models_dir

_CHUNK = 1 << 20
_USER_AGENT = "jarvis-voice-engine/0.1 (+https://github.com/PersonalJarvis/PersonalJarvis)"

ProgressFn = Callable[[str, int, int], None]


@dataclass(frozen=True, slots=True)
class ModelSource:
    name: str
    url: str
    size: int
    sha256: str
    # For archives: the directory the archive unpacks to (relative to the store).
    unpacks_to: str = ""
    # For single files: the file name inside the model's own directory.
    filename: str = ""
    licence: str = ""

    @property
    def is_archive(self) -> bool:
        return self.url.endswith((".tar.bz2", ".tar.gz", ".tgz"))


_GH_SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download"

REGISTRY: dict[str, ModelSource] = {
    source.name: source
    for source in (
        ModelSource(
            name="silero-vad-v6",
            url=(
                "https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.3/"
                "src/silero_vad/data/silero_vad.onnx"
            ),
            size=2_327_524,
            sha256="1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
            filename="silero_vad.onnx",
            licence="MIT",
        ),
        ModelSource(
            name="smart-turn-v3.2",
            url=(
                "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/"
                "f766f81d3cfdf7737ac64aad813d91bbfd56bf93/smart-turn-v3.2-cpu.onnx"
            ),
            size=8_679_182,
            sha256="2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f",
            filename="smart-turn-v3.2-cpu.onnx",
            licence="BSD-2-Clause",
        ),
        ModelSource(
            name="parakeet-tdt-0.6b-v3-int8",
            url=f"{_GH_SHERPA}/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2",
            size=487_170_055,
            sha256="5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf",
            unpacks_to="sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
            licence="CC-BY-4.0",
        ),
        ModelSource(
            name="piper-de-thorsten-medium",
            url=f"{_GH_SHERPA}/tts-models/vits-piper-de_DE-thorsten-medium.tar.bz2",
            size=67_214_254,
            sha256="50487d9c95fdf2191f31d2588569381063ba1591dcd4c7d4bdd30f12b2191714",
            unpacks_to="vits-piper-de_DE-thorsten-medium",
            licence="CC0-1.0 voice; Apache-2.0 runtime",
        ),
        ModelSource(
            name="piper-en-ryan-medium",
            url=f"{_GH_SHERPA}/tts-models/vits-piper-en_US-ryan-medium.tar.bz2",
            size=67_213_100,
            sha256="c546af78b6395b4e7c4ce1ed899438b64426a362f5d4ec5fecd090ded9ad7505",
            unpacks_to="vits-piper-en_US-ryan-medium",
            licence="CC-BY-4.0 voice; Apache-2.0 runtime",
        ),
    )
}


def model_path(name: str, root: Path | None = None) -> Path:
    """Where a registered model lives once fetched (directory or single file)."""
    source = REGISTRY[name]
    base = (root or models_dir())
    if source.is_archive:
        return base / source.unpacks_to
    return base / name / source.filename


def is_present(name: str, root: Path | None = None) -> bool:
    path = model_path(name, root)
    return path.is_dir() and any(path.iterdir()) if REGISTRY[name].is_archive else path.is_file()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, target: Path, expected_size: int, progress: ProgressFn | None) -> None:
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https model source: {url}")
    partial = target.with_name(target.name + ".part")
    done = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310 - https checked above
    if done:
        request.add_header("Range", f"bytes={done}-")
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - https checked above
        if done and response.status != 206:
            # The server ignored the range request; start over rather than
            # appending a second copy to the partial file.
            done = 0
            partial.unlink(missing_ok=True)
        total = int(response.headers.get("Content-Length") or 0) + done
        total = total or expected_size
        with partial.open("ab" if done else "wb") as handle:
            last = 0.0
            while True:
                block = response.read(_CHUNK)
                if not block:
                    break
                handle.write(block)
                done += len(block)
                now = time.monotonic()
                if progress is not None and now - last > 0.5:
                    progress(target.name, done, total)
                    last = now
    if progress is not None:
        progress(target.name, done, done)
    partial.replace(target)


def fetch(
    name: str,
    root: Path | None = None,
    *,
    progress: ProgressFn | None = None,
    allow_unpinned: bool = False,
) -> tuple[Path, str]:
    """Download, verify and unpack one model. Returns (path, sha256).

    An entry without a pinned checksum is refused unless ``allow_unpinned`` is
    set (bench bootstrap only); the computed checksum is returned so it can be
    pinned in :data:`REGISTRY`.
    """
    source = REGISTRY[name]
    base = root or models_dir()
    final = model_path(name, base)
    if is_present(name, base):
        return final, source.sha256 or "present"
    if not source.sha256 and not allow_unpinned:
        raise RuntimeError(f"{name} has no pinned checksum; refusing to download it")
    staging = base / ".download"
    staging.mkdir(parents=True, exist_ok=True)
    blob = staging / source.url.rsplit("/", 1)[-1]
    if not blob.exists():
        _download(source.url, blob, source.size, progress)
    digest = sha256_of(blob)
    if source.sha256 and digest != source.sha256:
        blob.unlink(missing_ok=True)
        raise RuntimeError(f"{name}: checksum mismatch (got {digest}, pinned {source.sha256})")
    if source.is_archive:
        unpack_dir = staging / f"{name}.unpack"
        shutil.rmtree(unpack_dir, ignore_errors=True)
        unpack_dir.mkdir(parents=True)
        with tarfile.open(blob) as archive:
            archive.extractall(unpack_dir, filter="data")
        produced = unpack_dir / source.unpacks_to
        if not produced.is_dir():
            raise RuntimeError(f"{name}: archive did not contain {source.unpacks_to}/")
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(produced, final)
        shutil.rmtree(unpack_dir, ignore_errors=True)
    else:
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(blob, final)
    blob.unlink(missing_ok=True)
    return final, digest
