"""Local audio sidecars for dictations that produced nothing usable.

Why keep audio at all
---------------------
A dictation that fails — a provider 401, a wedged engine, a transcript that
came back empty — currently costs the user everything they just said. Keeping
the raw audio for those cases (and only those cases) is what makes a later
"Restore" more than a button that shakes its head.

Why this is written carefully
-----------------------------
Raw microphone audio is the most sensitive thing this application would ever
store. It is a voiceprint, it contains whatever was said in the room before
the key was released, and it cannot be redacted after the fact. So:

* **Only recoverable outcomes.** The success path never writes a file. That
  decision lives in the caller, but nothing here makes the success path easy.
* **Local only.** Files land under ``<user data>/data/dictation_audio/`` and
  are never uploaded, synced, or referenced by an absolute path in any API
  response.
* **Capped hard.** ``prune_audio`` keeps at most a handful of recent files and
  ages the rest out, so "keep failed audio" can never grow into an archive.
* **Purgeable in one call.** ``purge_dictation_audio`` is what the "delete my
  dictation history" action calls, so the user's mental model ("I deleted it")
  matches reality.
* **Opt-out is a real switch.** ``[dictation].keep_failed_audio = false``
  means nothing here is ever called.

Format: plain uncompressed WAV via the stdlib :mod:`wave` module — 16 kHz,
mono, signed 16-bit, which is exactly the PCM the capture path already holds.
No codec, no third-party dependency, and readable by every STT provider we
hand it back to. At the 300 s capture ceiling one file is ~9.6 MB.

The :mod:`wave` import is deliberately made inside the functions: this module
sits on no hot path and must not cost anything at import time (AP-26).
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

#: The capture format. These are not tunables — they describe the PCM the
#: dictation buffer already contains.
SAMPLE_RATE: int = 16_000
CHANNELS: int = 1
SAMPLE_WIDTH: int = 2  # bytes per sample (signed 16-bit)

#: Longest audio written to one file. Mirrors the 300 s capture ceiling; a
#: longer buffer is stored truncated rather than refused, because a partial
#: recovery still beats none.
MAX_AUDIO_SECONDS: int = 300
MAX_AUDIO_BYTES: int = MAX_AUDIO_SECONDS * SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH

#: Entry ids are generated as ``uuid4().hex``, but a file name is built from
#: them and untrusted input must never be able to escape the directory.
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")
_MAX_ID_LEN = 64


def default_audio_dir() -> Path:
    """``<user data>/data/dictation_audio``. Does not create the directory."""
    from jarvis.core.paths import user_data_dir

    return Path(user_data_dir()) / "data" / "dictation_audio"


def _safe_stem(entry_id: str) -> str:
    """A file-name-safe stem, or ``""`` when nothing usable is left."""
    cleaned = _SAFE_ID_RE.sub("", str(entry_id or ""))
    return cleaned[:_MAX_ID_LEN]


def audio_path_for(entry_id: str, *, directory: Path | str | None = None) -> Path | None:
    """Where the sidecar for ``entry_id`` lives. ``None`` for an unusable id.

    Deterministic, so a caller can find the file again without having stored
    the path — but the path IS stored on the history entry as well, because
    that is what lets a prune know which files are still spoken for.
    """
    stem = _safe_stem(entry_id)
    if not stem:
        return None
    base = Path(directory) if directory is not None else default_audio_dir()
    return base / f"{stem}.wav"


def save_dictation_audio(
    entry_id: str,
    pcm: bytes,
    *,
    directory: Path | str | None = None,
    sample_rate: int = SAMPLE_RATE,
) -> Path | None:
    """Write ``pcm`` as a WAV sidecar. ``None`` when nothing was written.

    Never raises: a failed sidecar is a lost recovery option, which must never
    turn into a lost dictation. Blocking — call it from a worker thread.
    """
    import wave  # lazy: nothing at import time (AP-26)

    path = audio_path_for(entry_id, directory=directory)
    if path is None:
        log.debug("dictation audio not saved: unusable entry id")
        return None
    data = bytes(pcm or b"")
    if not data:
        return None
    if len(data) > MAX_AUDIO_BYTES:
        # Truncate rather than refuse — the first five minutes are still the
        # part the user is most likely to want back.
        data = data[:MAX_AUDIO_BYTES]
    # Whole samples only; a trailing half-sample makes an unreadable frame.
    frame_bytes = CHANNELS * SAMPLE_WIDTH
    if len(data) % frame_bytes:
        data = data[: len(data) - (len(data) % frame_bytes)]
    if not data:
        return None

    tmp_name = ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic tempfile + os.replace, same discipline as the history sidecar:
        # a crash mid-write never leaves a torn WAV that a later read chokes on.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".dictation_audio_", suffix=".tmp"
        )
        os.close(fd)
        with wave.open(str(tmp_name), "wb") as handle:
            handle.setnchannels(CHANNELS)
            handle.setsampwidth(SAMPLE_WIDTH)
            handle.setframerate(int(sample_rate or SAMPLE_RATE))
            handle.writeframes(data)
        os.replace(tmp_name, path)
    except Exception:  # noqa: BLE001 — a sidecar is never worth a failure
        log.warning("could not store the dictation audio sidecar", exc_info=True)
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        return None
    return path


def load_dictation_audio(path: Path | str | None) -> bytes:
    """Read a sidecar back as raw PCM frames. ``b""`` when unreadable.

    Returns the frames, not the WAV container, because that is what an STT
    provider's ``transcribe_pcm`` expects. Blocking — call it from a worker
    thread.
    """
    import wave  # lazy (AP-26)

    if not path:
        return b""
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.readframes(handle.getnframes())
    except Exception:  # noqa: BLE001
        log.debug("dictation audio sidecar unreadable: %s", path, exc_info=True)
        return b""


def audio_exists(path: Path | str | None) -> bool:
    """``True`` when the sidecar is still on disk. Never raises."""
    if not path:
        return False
    try:
        return Path(path).is_file()
    except OSError:
        return False


def delete_dictation_audio(path: Path | str | None) -> bool:
    """Remove one sidecar. ``False`` when there was nothing to remove."""
    if not path:
        return False
    try:
        Path(path).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("could not delete a dictation audio sidecar: %s", exc)
        return False


def purge_dictation_audio(*, directory: Path | str | None = None) -> int:
    """Delete every sidecar. Returns how many files went away.

    This is what "delete my dictation history" calls. It must leave nothing
    behind, so the user's mental model matches the disk.
    """
    base = Path(directory) if directory is not None else default_audio_dir()
    removed = 0
    try:
        candidates = sorted(base.glob("*.wav"))
    except OSError:
        return 0
    for item in candidates:
        if delete_dictation_audio(item):
            removed += 1
    return removed


def prune_audio(
    *,
    max_files: int = 20,
    retention_days: int = 7,
    directory: Path | str | None = None,
) -> int:
    """Enforce the retention caps. Returns how many files were deleted.

    Two independent caps, both applied: anything older than ``retention_days``
    goes, and only the ``max_files`` newest survive. ``0`` for either value
    disables that cap; ``max_files = 0`` therefore means "age-based only", not
    "delete everything" — use :func:`purge_dictation_audio` for that.
    """
    import time

    base = Path(directory) if directory is not None else default_audio_dir()
    try:
        files = [p for p in base.glob("*.wav") if p.is_file()]
    except OSError:
        return 0
    if not files:
        return 0

    stamped: list[tuple[float, Path]] = []
    for item in files:
        try:
            stamped.append((item.stat().st_mtime, item))
        except OSError:
            continue
    # Newest first — the survivors of a count cap are the recent ones.
    stamped.sort(key=lambda pair: pair[0], reverse=True)

    doomed: list[Path] = []
    if retention_days and retention_days > 0:
        cutoff = time.time() - (float(retention_days) * 86_400.0)
        doomed.extend(item for mtime, item in stamped if mtime < cutoff)
    if max_files and max_files > 0:
        doomed.extend(item for _, item in stamped[int(max_files) :])

    removed = 0
    for item in dict.fromkeys(doomed):  # de-duplicate, keep order
        if delete_dictation_audio(item):
            removed += 1
    return removed


__all__ = [
    "CHANNELS",
    "MAX_AUDIO_BYTES",
    "MAX_AUDIO_SECONDS",
    "SAMPLE_RATE",
    "SAMPLE_WIDTH",
    "audio_exists",
    "audio_path_for",
    "default_audio_dir",
    "delete_dictation_audio",
    "load_dictation_audio",
    "prune_audio",
    "purge_dictation_audio",
    "save_dictation_audio",
]
