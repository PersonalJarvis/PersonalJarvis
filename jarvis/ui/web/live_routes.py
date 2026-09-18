"""Guided GPT-Live setup; mounted under the authenticated provider API."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from jarvis.core.config import BrainTierConfig
from jarvis.live.config import LiveConfig

router = APIRouter(prefix="/api/live", tags=["live"])


@router.get("/options", summary="List available thinking models and Live voices")
async def get_live_options() -> dict:
    from jarvis.agent_chat.effort import effort_levels
    from jarvis.brain.model_catalog import shared_catalog

    catalog = await shared_catalog().list_models("openai")
    return {
        "models": [{"id": m.id, "label": m.label} for m in catalog.models],
        "source": catalog.source,
        "efforts": ["", *effort_levels("openai")],
        "voices": [
            "quartz",
            "ripple",
            "vesper",
            "willow",
            "stone",
            "gleam",
            "meridian",
            "bossa",
            "tempo",
            "beacon",
            "delta",
            "cinder",
        ],
    }


def _config(request: Request):
    from jarvis.core.config import load_config

    return getattr(request.app.state, "config", None) or load_config()


@router.get("/profile", summary="Read GPT-Live setup and migration state")
async def get_live_profile(request: Request) -> dict:
    cfg = _config(request)
    from jarvis.core.config import get_secret_any
    from jarvis.plugins.realtime.openai_live import OpenAILiveProvider

    ready = await asyncio.to_thread(get_secret_any, OpenAILiveProvider.credential_candidates)
    return {
        "profile": cfg.live.model_dump(),
        "key_ready": bool(ready),
        "active": getattr(cfg.brain.realtime, "provider", "") == "openai-live",
        "agent_configured": cfg.brain.worker is not None,
    }


@router.put("/profile", summary="Save the selected GPT-Live voice and thinking model")
async def save_live_profile(body: LiveConfig, request: Request) -> dict:
    from jarvis.core.config_writer import set_live_profile

    if not body.backend_model.strip() or not body.configured:
        raise HTTPException(422, "Choose a thinking model before enabling GPT-Live.")
    cfg = _config(request)
    await asyncio.to_thread(set_live_profile, body.model_dump())
    cfg.live = body
    cfg.brain.realtime = BrainTierConfig(provider="openai-live", model=body.model)
    cfg.voice.mode = "realtime"
    return {"profile": body.model_dump(), "applies": "next_call"}


@router.post("/use-key-for-agent", summary="Use the selected OpenAI model for agents and text chat")
async def use_live_key_for_agent(request: Request) -> dict:
    from jarvis.core.config_writer import set_worker_model, set_worker_provider

    cfg = _config(request)
    if not cfg.live.configured or not cfg.live.backend_model:
        raise HTTPException(409, "Set up GPT-Live first.")
    await asyncio.to_thread(set_worker_provider, "openai")
    await asyncio.to_thread(set_worker_model, cfg.live.backend_model)
    cfg.brain.worker = BrainTierConfig(provider="openai", model=cfg.live.backend_model)
    return {"provider": "openai", "model": cfg.live.backend_model}
