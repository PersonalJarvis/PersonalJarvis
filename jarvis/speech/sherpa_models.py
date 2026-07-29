"""Download of the ONNX model bundles the local sherpa-onnx providers run.

Unlike Whisper — whose weights live in the HuggingFace cache that
``faster_whisper`` manages itself — a sherpa-onnx model is a handful of loose
files (encoder / decoder / joiner / tokens, or a voice + its tokens) that the
recognizer is handed by path. So this module owns their whole lifecycle: where
they land, how they get there, and what "complete" means.

Two properties matter more than speed here:

* **Atomic.** Files are written into a temporary directory next to the target
  and moved into place only once every one of them arrived. A half-finished
  download must never be visible under the model's real name, because the
  readiness probe would then see a directory full of files and call the
  provider ready — and the failure would surface as a native crash mid-sentence
  instead of an honest "download it again".
* **Resumable by simply re-running.** A failed attempt leaves nothing behind,
  so pressing the button again is always the correct fix. There is no partial
  state a user could get stuck in.

The files are fetched from the model repository over plain HTTPS rather than
through ``huggingface_hub``: the base install has no HF client of its own (that
package arrives with the optional Whisper engine), and a local TTS voice must
not drag a downloader dependency into an install that only wants to speak.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from jarvis.speech.local_models import sherpa_model_dir

log = logging.getLogger("jarvis.speech.sherpa_models")

#: Direct file endpoint of a HuggingFace repo. ``resolve/main`` serves the raw
#: bytes (and redirects to the CDN), which is exactly what a plain HTTP client
#: needs — no API token, no client library.
_HF_FILE_URL = "https://huggingface.co/{repo}/resolve/main/{path}"

#: Chunk size for the streamed write. A 650 MB encoder must never be held in
#: memory on a machine that might be a small VPS or an old laptop.
_CHUNK_BYTES = 1 << 20


def _download_file(url: str, target: Path, *, timeout_s: float = 60.0) -> None:
    """Stream one file to *target*, raising with a usable message on failure."""
    import httpx

    with httpx.stream(
        "GET", url, follow_redirects=True, timeout=timeout_s
    ) as response:
        response.raise_for_status()
        with target.open("wb") as fh:
            for chunk in response.iter_bytes(_CHUNK_BYTES):
                fh.write(chunk)


def _extract_archive(archive: Path, staging: Path) -> None:
    """Unpack a tar.bz2 into *staging*, stripping its single top-level directory.

    The upstream archives wrap everything in a folder named after the model; the
    rest of the app addresses bundles by their own directory, so the wrapper is
    removed here rather than encoded into every path elsewhere.
    """
    import tarfile

    unpacked = staging / "_unpacked"
    unpacked.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, mode="r:bz2") as tf:
        # 'data' rejects absolute paths, parent traversal, links pointing out of
        # the tree and device files — a downloaded archive is untrusted input,
        # and without the filter a crafted one could write anywhere on disk.
        try:
            tf.extractall(unpacked, filter="data")
        except TypeError:  # pragma: no cover — Python without the filter arg
            tf.extractall(unpacked)  # noqa: S202
    entries = [p for p in unpacked.iterdir() if not p.name.startswith(".")]
    root = entries[0] if len(entries) == 1 and entries[0].is_dir() else unpacked
    for item in list(root.iterdir()):
        item.replace(staging / item.name)
    shutil.rmtree(unpacked, ignore_errors=True)


def download_sherpa_model(
    model_id: str,
    *,
    data_dir: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Fetch the bundle for *model_id* and return its directory.

    Raises ``LookupError`` for an unknown bundle (a catalog/caller mismatch, not
    a network problem) and lets transport errors propagate — the caller turns
    them into the retryable state the user sees.
    """
    from jarvis.speech.local_models import SHERPA_BUNDLES

    bundle = SHERPA_BUNDLES.get(model_id)
    if bundle is None:
        raise LookupError(
            f"No downloadable model bundle is registered for '{model_id}'."
        )

    target_dir = sherpa_model_dir(model_id, data_dir=data_dir)
    if target_dir.is_dir() and all(
        (target_dir / name).exists() for name in bundle.required_files
    ):
        log.info("Local model %s is already complete at %s.", model_id, target_dir)
        return target_dir

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    # Staged in a sibling directory so the final move is a rename on the SAME
    # filesystem — an atomic operation, unlike a cross-device copy that could be
    # interrupted halfway.
    staging = Path(tempfile.mkdtemp(prefix=f".{model_id}-", dir=target_dir.parent))
    try:
        if bundle.archive_url:
            if progress is not None:
                progress(f"Downloading {bundle.label}…")
            archive = staging / "bundle.tar.bz2"
            _download_file(bundle.archive_url, archive)
            if progress is not None:
                progress(f"Unpacking {bundle.label}…")
            _extract_archive(archive, staging)
            archive.unlink(missing_ok=True)
        else:
            total = len(bundle.required_files)
            for index, name in enumerate(bundle.required_files, start=1):
                if progress is not None:
                    progress(f"Downloading {name} ({index}/{total})…")
                log.info("Fetching %s for local model %s", name, model_id)
                _download_file(
                    _HF_FILE_URL.format(repo=bundle.hf_repo, path=name),
                    staging / name,
                )
        missing = [n for n in bundle.required_files if not (staging / n).exists()]
        if missing:
            raise RuntimeError(
                f"The download for '{model_id}' is missing {', '.join(missing)}."
            )
        # Replace any incomplete previous attempt, then move the finished set in.
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        staging.replace(target_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    log.info("Local model %s ready at %s", model_id, target_dir)
    return target_dir


def model_paths(model_id: str, *, data_dir: str | None = None) -> dict[str, Path]:
    """Absolute paths of a bundle's files, keyed by their filename.

    Providers ask for this instead of composing paths themselves, so the
    directory layout stays a fact of this module alone.
    """
    from jarvis.speech.local_models import SHERPA_BUNDLES

    bundle = SHERPA_BUNDLES.get(model_id)
    directory = sherpa_model_dir(model_id, data_dir=data_dir)
    names = bundle.required_files if bundle is not None else ()
    return {name: directory / name for name in names}


__all__ = ["download_sherpa_model", "model_paths"]
