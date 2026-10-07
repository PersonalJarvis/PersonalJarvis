"""Local voice card: status, one-click setup, self-test and settings (ADR-0037).

The card talks only to these routes. Nothing here runs on its own: setup and
the self-test start when the user presses the card's buttons, and the status
read never starts the engine or downloads anything (AP-26; nothing the user
did not start may run or bill anything).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from jarvis.core import config as cfg_mod

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/providers/local-voice", tags=["providers"])


class LocalVoiceSettingsBody(BaseModel):
    """A partial update: an omitted field keeps its value."""

    voice: str | None = None
    #: An Ollama tag; an empty string returns to the automatic choice.
    llm_model: str | None = None


def _config(request: Request) -> Any:
    """The app's live configuration object, the one live calls also read."""
    cfg = getattr(request.app.state, "config", None) or getattr(request.app.state, "cfg", None)
    if cfg is not None:
        return cfg
    try:
        return cfg_mod.load_config()
    except Exception as exc:  # noqa: BLE001 - the card degrades to defaults
        log.warning("Local voice routes could not load the configuration (%s: %s); "
                    "the card shows defaults", type(exc).__name__, exc)
        return None


@router.get("/status", summary="Local voice engine status for the settings card")
async def local_voice_status(request: Request) -> dict[str, Any]:
    """Phase, setup progress, chosen model and voice, expected latency, self-test."""
    from jarvis.realtime.local_voice_setup import card_status  # noqa: PLC0415

    return await card_status(_config(request))


@router.post("/setup", summary="Start (or join) the local voice engine setup")
async def local_voice_setup(request: Request) -> dict[str, Any]:
    """Build the engine's environment, fetch its models and prove it works.

    Returns at once; the work runs in a background thread and the card polls
    ``/status``. A second call while setup runs joins it.
    """
    from jarvis.realtime.local_voice_setup import (  # noqa: PLC0415
        card_status,
        real_deps,
        start_setup,
    )

    cfg = _config(request)
    deps = real_deps(loop=asyncio.get_running_loop(), config_loader=lambda: _config(request))
    started, message = start_setup(deps)
    payload = await card_status(cfg)
    payload.update({"started": started, "message": message})
    return payload


@router.post("/selftest", summary="Run the local voice self-test")
async def local_voice_selftest(request: Request) -> dict[str, Any]:
    """Start the engine if needed and speak, hear and answer once per language."""
    from jarvis.plugins.realtime.local_voice import (  # noqa: PLC0415
        NOT_SET_UP_REASON,
        EngineSettings,
        LocalVoiceProvider,
        engine_installed,
    )
    from jarvis.realtime.local_voice_setup import (  # noqa: PLC0415
        card_status,
        engine_home,
        run_engine_selftest,
        selftests,
    )

    cfg = _config(request)
    settings = EngineSettings.from_config(cfg)
    if not await asyncio.to_thread(engine_installed, settings):
        raise HTTPException(status_code=409, detail=NOT_SET_UP_REASON)
    engine = LocalVoiceProvider.shared_engine(cfg)
    home = engine_home()
    started = selftests.start(lambda: run_engine_selftest(engine, home=home, settings=settings))
    payload = await card_status(cfg)
    payload["started"] = started
    return payload


@router.put("/settings", summary="Choose the local voice and language model")
async def local_voice_settings(request: Request, body: LocalVoiceSettingsBody) -> dict[str, Any]:
    """Persist the card's choices to ``[voice_engine]`` and apply them in memory.

    The next engine start uses them; a running engine is replaced on the next
    call or self-test, never mid-call.
    """
    from jarvis.core import config_writer  # noqa: PLC0415
    from jarvis.realtime.local_voice_setup import card_status  # noqa: PLC0415

    try:
        await asyncio.to_thread(
            config_writer.set_voice_engine_settings, tts=body.voice, llm_model=body.llm_model
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cfg = _config(request)
    section = getattr(cfg, "voice_engine", None)
    if section is not None:
        if body.voice is not None:
            section.tts = body.voice.strip().lower()
        if body.llm_model is not None:
            section.llm_model = body.llm_model.strip()
    return await card_status(cfg)
