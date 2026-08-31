"""Image turns must lead with a live vision FAST model, not the Tool Model.

Live 2026-08-31 17:06: prefer_tool_model hoisted Vertex gemini-3.7-flash.
Five providers failed (schema / 402 / 401 / 429 retries) and grok-4.6
answered after 20 s. A look turn leads with the router-tier vision model.
"""

from __future__ import annotations

from types import SimpleNamespace

from jarvis.brain.manager import BrainManager


class _Vision:
    supports_vision = True


class _Blind:
    supports_vision = False


def _mgr(*, active: str = "grok") -> BrainManager:
    m = BrainManager.__new__(BrainManager)
    m._active_name = active
    m._dead_providers = set()
    m._brain_cache = {}
    m._config = SimpleNamespace(
        brain=SimpleNamespace(
            router=SimpleNamespace(
                provider="grok",
                model="grok-4.20-0309-non-reasoning",
            )
        )
    )
    brains = {
        "grok": _Vision(),
        "vertex": _Vision(),
        "openrouter": _Vision(),
        "claude-api": _Vision(),
        "codex": _Blind(),
    }

    def _get_brain(provider: str, model: str | None = None, **kwargs: object) -> object:
        return brains[provider]

    def _fast_model(provider: str) -> str:
        return {"grok": "grok-4.3", "vertex": "gemini-3.5-flash"}.get(provider, "fast")

    m._get_brain = _get_brain  # type: ignore[method-assign]
    m._fast_model = _fast_model  # type: ignore[method-assign]
    return m


def test_image_turn_leads_with_the_router_vision_model() -> None:
    m = _mgr()
    chain = [
        ("vertex", "gemini-3.7-flash"),
        ("openrouter", "google/gemini-3.5-flash"),
        ("claude-api", "claude-opus-4-8"),
        ("grok", "grok-4.6"),
    ]
    led = m._lead_vision_chain(chain)
    assert led[0] == ("grok", "grok-4.20-0309-non-reasoning")


def test_blind_providers_stay_in_the_tail() -> None:
    m = _mgr(active="codex")
    led = m._lead_vision_chain([("codex", "gpt-5.5"), ("grok", "grok-4.6")])
    assert led[0][0] == "grok"
    assert led[-1][0] == "codex"
