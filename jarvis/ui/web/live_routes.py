"""Guided GPT-Live setup; mounted under the authenticated provider API."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Request

from jarvis.core.config import BrainTierConfig
from jarvis.live.config import LiveConfig

router = APIRouter(prefix="/api/live", tags=["live"])


@router.get("/options", summary="List available thinking models and Live voices")
async def get_live_options(
    auth_mode: Literal["api_key", "chatgpt_subscription"] = "api_key",
    account_id: str = "",
) -> dict:
    from jarvis.agent_chat.effort import effort_levels
    from jarvis.brain.model_catalog import shared_catalog

    if auth_mode == "chatgpt_subscription":
        from jarvis.live.subscription_auth import SubscriptionAuth, SubscriptionAuthError
        from jarvis.live.subscription_reasoning import (
            SubscriptionReasoning,
            SubscriptionReasoningError,
        )
        from jarvis.plugins.realtime.openai_subscription_live import SUBSCRIPTION_VOICES

        auth = SubscriptionAuth(account_id)
        reasoning = SubscriptionReasoning(credentials=auth.credentials)
        try:
            models = await reasoning.list_models()
        except (SubscriptionAuthError, SubscriptionReasoningError):
            raise HTTPException(
                409, "Connect an eligible ChatGPT account to load its models."
            ) from None
        finally:
            await reasoning.aclose()
            await auth.aclose()
        return {
            "models": models, "source": "chatgpt_subscription",
            "efforts": ["", *effort_levels("openai")], "voices": list(SUBSCRIPTION_VOICES),
        }
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


def _profile_for_runtime(cfg) -> LiveConfig:
    """The selected Live provider wins after a CLI/provider-only switch."""
    from jarvis.ui.web.provider_spec import get_spec

    spec = get_spec(str(getattr(cfg.brain.realtime, "provider", "")))
    if spec is None or spec.configuration_surface != "live":
        return cfg.live
    mode = "chatgpt_subscription" if spec.auth_mode == "codex" else "api_key"
    return cfg.live.model_copy(update={"auth_mode": mode})


@router.get("/profile", summary="Read GPT-Live setup and migration state")
async def get_live_profile(request: Request) -> dict:
    cfg = _config(request)
    profile = _profile_for_runtime(cfg)
    from jarvis.core.config import get_secret_any
    from jarvis.plugins.realtime.openai_live import OpenAILiveProvider

    ready = await asyncio.to_thread(get_secret_any, OpenAILiveProvider.credential_candidates)
    from jarvis.live.subscription_auth import SubscriptionAuth

    auth = SubscriptionAuth.from_runtime_config(cfg)
    try:
        status = await auth.status()
    finally:
        await auth.aclose()
    return {
        "profile": profile.model_dump(),
        "key_ready": bool(ready),
        "active": getattr(cfg.brain.realtime, "provider", "") == profile.provider_id,
        "agent_configured": cfg.brain.worker is not None,
        "subscription": {
            "account_id": status["account_id"], "account_connected": status["connected"],
            "voice_status": "unverified" if status["connected"] else "unavailable",
            "reason": status["reason"],
        },
    }


@router.put("/profile", summary="Save the selected GPT-Live voice and thinking model")
async def save_live_profile(body: LiveConfig, request: Request) -> dict:
    from jarvis.core.config_writer import set_live_profile

    effective = body.for_session()
    if not effective.backend_model.strip() or not body.configured:
        raise HTTPException(422, "Choose a thinking model before enabling GPT-Live.")
    if body.auth_mode == "chatgpt_subscription":
        from jarvis.plugins.realtime.openai_subscription_live import SUBSCRIPTION_VOICES

        if body.subscription_voice not in SUBSCRIPTION_VOICES:
            raise HTTPException(422, "Choose a voice available for the ChatGPT subscription.")
    cfg = _config(request)
    await asyncio.to_thread(set_live_profile, body.model_dump())
    cfg.live = body
    cfg.brain.realtime = BrainTierConfig(provider=body.provider_id, model=effective.model)
    cfg.voice.mode = "realtime"
    return {"profile": body.model_dump(), "applies": "next_call"}


@router.post("/use-key-for-agent", summary="Use the selected OpenAI model for agents and text chat")
async def use_live_key_for_agent(request: Request) -> dict:
    from jarvis.core.config_writer import set_worker_model, set_worker_provider

    cfg = _config(request)
    if _profile_for_runtime(cfg).auth_mode != "api_key":
        raise HTTPException(409, "Switch to API-key mode before reusing an API key for agents.")
    if not cfg.live.configured or not cfg.live.backend_model:
        raise HTTPException(409, "Set up GPT-Live first.")
    await asyncio.to_thread(set_worker_provider, "openai")
    await asyncio.to_thread(set_worker_model, cfg.live.backend_model)
    cfg.brain.worker = BrainTierConfig(provider="openai", model=cfg.live.backend_model)
    return {"provider": "openai", "model": cfg.live.backend_model}
