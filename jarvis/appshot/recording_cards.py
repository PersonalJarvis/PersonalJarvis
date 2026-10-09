"""Hand finalized recordings to the existing screenshot corner-card sidecar."""

from __future__ import annotations

import asyncio
import base64
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from jarvis.appshot.recording import recording_file

log = logging.getLogger(__name__)
RECORDING_PREFIX = "recording:"


async def show_recording_preview(recording_id: str, metadata: dict[str, Any]) -> bool:
    from jarvis.appshot.recording_labels import LABELS
    from jarvis.core.config import load_config
    from jarvis.cu.indicator.controller import get_indicator_controller

    try:
        path = recording_file(recording_id)
        controller = get_indicator_controller()
        if path is None or controller is None:
            return False
        config = await asyncio.to_thread(load_config)
        if not config.appshot.effect:
            return False
        poster = await asyncio.to_thread(path.with_suffix(".jpg").read_bytes)
        labels = dict(LABELS.get(config.ui.language, LABELS["en"]))
        return await controller.show_recording(
            recording_id=recording_id,
            video_path=str(path),
            thumb_b64=base64.b64encode(poster).decode("ascii"),
            monitor=metadata.get("monitor", []),
            rect=metadata.get("rect", [0, 0, 1, 1]),
            screen_name=str(metadata.get("screen_name", "")),
            duration_s=float(metadata.get("duration_s", 0)),
            rest_ms=max(0, min(600, int(getattr(config.appshot, "card_seconds", 6)))) * 1000,
            labels=labels,
        )
    except Exception:
        # Presentation failure must never turn an already finalized video into an error.
        log.exception("appshot: recording preview could not be shown")
        return False


def open_recording(recording_id: str) -> bool:
    from jarvis.platform.open_path import open_file

    path = recording_file(recording_id)
    return path is not None and open_file(path)


def save_recording(recording_id: str, folder: Path | None = None) -> Path | None:
    path = recording_file(recording_id)
    if path is None:
        return None
    if folder is None:
        from jarvis.platform.user_dirs import downloads_dir

        folder = downloads_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"recording-{time.strftime('%Y%m%d-%H%M%S')}-{recording_id[:6]}"
    counter = 0
    while True:
        destination = folder / f"{stem}{f'-{counter}' if counter else ''}.mp4"
        created = False
        try:
            with path.open("rb") as source, destination.open("xb") as target:
                created = True
                shutil.copyfileobj(source, target, length=1024 * 1024)
            return destination
        except FileExistsError:  # Pick a fresh filename; never overwrite an existing export.
            counter += 1
        except OSError:
            if created:
                destination.unlink(missing_ok=True)
            raise


async def run_recording_card_action(action: str, recording_id: str) -> str:
    from jarvis.appshot.recording_labels import LABELS
    from jarvis.core.config import load_config

    config = await asyncio.to_thread(load_config)
    labels = LABELS.get(config.ui.language, LABELS["en"])
    try:
        if action == "open":
            from jarvis.appshot.editor_window import open_recording_window

            # The app's own video editor first; the system player where the
            # shell cannot open windows (browser-only or headless host).
            if await open_recording_window(recording_id):
                return ""
            opened = await asyncio.to_thread(open_recording, recording_id)
            return "" if opened else labels["failed"]
        saved = await asyncio.to_thread(save_recording, recording_id) if action == "save" else None
        return labels["saved"] if saved is not None else labels["failed"]
    except OSError:
        log.exception("appshot: recording could not be copied to Downloads")
        return labels["failed"]
