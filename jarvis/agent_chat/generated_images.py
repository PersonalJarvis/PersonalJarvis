"""Retain native CLI images as ordinary, safely served chat artifacts.

Some CLI JSON streams omit native image-generation extension items even though
the CLI saves their PNGs. Import only fresh files from that exact conversation's
generated-images directory. The chat event remains ordinary Markdown: no base64
in the event log, no private local paths in URLs, and no new serving endpoint.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from jarvis.missions.standalone_run import write_marker

MAX_IMAGE_BYTES = 16 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class GeneratedImage:
    message_id: str
    markdown: str
    path: Path


def collect_generated_images(
    *, codex_home: Path, thread_id: str, since: float, outputs_root: Path
) -> list[GeneratedImage]:
    """Archive this turn's completed PNG files, leaving source files untouched.

    UUID validation and resolved-path checks keep both thread and file symlinks
    from importing another conversation's data. Missing image support is a
    normal empty result; unreadable or invalid generated files raise so the
    runner can show a visible delivery error instead of claiming success.
    """
    thread = str(UUID(thread_id))
    home = codex_home.expanduser().resolve()
    images_root = home / "generated_images"
    source = images_root / thread
    if not source.exists():
        return []
    if images_root.is_symlink() or source.is_symlink() or source.resolve() != source:
        raise ValueError("Generated image directory must belong to the current conversation")

    candidates: list[tuple[float, Path]] = []
    for image in source.iterdir():
        if image.suffix.lower() != ".png":
            continue
        if image.is_symlink() or image.resolve().parent != source:
            raise ValueError("Generated image must stay inside its conversation directory")
        if image.is_file() and (modified := image.stat().st_mtime) >= since:
            candidates.append((modified, image))

    delivered: list[GeneratedImage] = []
    for modified, image in sorted(candidates):
        with image.open("rb") as handle:
            data = handle.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES or not data.startswith(PNG_SIGNATURE):
            raise ValueError("Generated image is not a supported PNG or exceeds 16 MiB")
        # Stable across recovery/replay, distinct even when two threads produce
        # identical pixels. Never use a provider-controlled filename as a URL.
        identity = hashlib.sha256(thread.encode() + image.name.encode() + data).hexdigest()[:20]
        stamp = datetime.fromtimestamp(modified, UTC).strftime("%Y%m%dT%H%M%S")
        slug = f"{stamp}__generated-image__{identity[:16]}"
        run_dir = outputs_root / slug
        files = run_dir / "tasks" / "chat" / "artifacts" / "files"
        if run_dir.resolve() != outputs_root.resolve() / slug or not files.resolve().is_relative_to(
            run_dir.resolve()
        ):
            raise ValueError("Generated image destination must stay inside its artifact directory")
        files.mkdir(parents=True, exist_ok=True)
        target = files / "generated-image.png"
        with tempfile.TemporaryDirectory(prefix=".image-", dir=run_dir) as scratch:
            staged = Path(scratch) / "image.png"
            staged.write_bytes(data)
            os.replace(staged, target)
        write_marker(run_dir, kind="chat_image", title="Generated image")
        url = f"/api/outputs/{slug}/files/tasks/chat/artifacts/files/generated-image.png/download"
        delivered.append(
            GeneratedImage(
                message_id=f"generated-image-{identity}",
                markdown=(
                    f"![generated-image.png]({url}?disposition=inline)\n\n"
                    f"[generated-image.png]({url})"
                ),
                path=target,
            )
        )
    return delivered
