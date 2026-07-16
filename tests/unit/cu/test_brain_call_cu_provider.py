"""Dedicated Computer-Use provider dispatch (jarvis/cu/brain_call.py).

Covers the CU-own-provider hoist: ``manager._cu_provider()``, when it returns
a configured provider id, must be hoisted to the HEAD of the
``_build_fallback_chain("fast")`` candidate list before anything else in
``call_vision_brain`` applies (the cu_model pin loop, the speed tune, and —
critically — the vision/health gate in ``ComputerUsePlannerSelector``, so a
blind/dead CU pick degrades through the rest of the chain instead of
bricking Computer-Use, AP-21/22). When ``_cu_provider()`` returns ``""``
(unset) or is absent altogether, dispatch must be BYTE-FOR-BYTE unchanged —
CU keeps leading with whatever ``_build_fallback_chain`` already produces
(``brain.primary`` first), the default-preserving contract from the plan.

Fakes only (no real network, no real BrainManager) — mirrors the
``_FallbackChainManager``/``_StreamingBrain`` shape used in
``tests/unit/harness/test_cu_loop_robustness.py``.
"""
from __future__ import annotations

from typing import Any

from jarvis.core.protocols import BrainDelta, ImageBlock
from jarvis.cu.brain_call import call_vision_brain


class _FakeBrain:
    """Minimal provider-shaped brain: records dispatch, yields scripted text."""

    supports_tools = False

    def __init__(self, *, text: str = '{"action": "done"}', supports_vision: bool = True) -> None:
        self.text = text
        self.supports_vision = supports_vision
        self.calls = 0

    async def complete(self, req: Any):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.text:
            yield BrainDelta(content=self.text)
        yield BrainDelta(finish_reason="stop")

    def estimate_cost(self, req: Any) -> float:
        return 0.0


class _FakeManager:
    """BrainManager-shaped fake exposing only the CU dispatch surface.

    Deliberately has NO ``complete_text`` attribute — ``call_vision_brain``
    checks ``getattr(manager, "complete_text", None)`` first and, if present,
    takes the FakeBrain test-shim early-return path, which would bypass the
    fallback-chain / hoist logic entirely and defeat these tests.
    """

    active_provider = "brain-primary"

    def __init__(self, *, cu_provider: str = "") -> None:
        self._cu_provider_value = cu_provider
        self.brains: dict[str, _FakeBrain] = {
            "brain-primary": _FakeBrain(),
            "brain-fallback": _FakeBrain(),
            "claude-api": _FakeBrain(),
        }
        self.requested: list[tuple[str, str | None]] = []

    def _build_fallback_chain(self, level: str) -> list[tuple[str, str | None]]:
        assert level == "fast"
        return [("brain-primary", None), ("brain-fallback", None)]

    def _get_brain(self, name: str, model: str | None = None) -> _FakeBrain:
        self.requested.append((name, model))
        return self.brains[name]

    def _cu_model(self, name: str) -> str | None:
        return None

    def _cu_provider(self) -> str:
        return self._cu_provider_value


class _FakeManagerNoResolver(_FakeManager):
    """Same as ``_FakeManager`` but without ``_cu_provider`` at all — the
    shape of an older/pre-feature manager. Must dispatch exactly as before,
    never raise ``AttributeError``."""

    _cu_provider = None  # type: ignore[assignment]  # not callable -> not used

    def __init__(self) -> None:
        super().__init__(cu_provider="")


def _build_prompt(provider: str, brain: Any) -> tuple[str, str]:
    return "system", "user"


async def test_cu_provider_hoists_configured_provider_to_chain_head() -> None:
    """A dedicated CU provider takes priority over brain.primary / the normal
    fallback chain — the chain is REORDERED (hoisted), not replaced, so the
    downstream vision/health gate still runs over it."""
    manager = _FakeManager(cu_provider="claude-api")

    reply = await call_vision_brain(manager, build_prompt=_build_prompt, images=[])

    assert reply.provider == "claude-api"
    assert manager.requested[0] == ("claude-api", None)
    assert manager.brains["claude-api"].calls == 1
    assert manager.brains["brain-primary"].calls == 0


async def test_no_cu_provider_leaves_chain_unchanged() -> None:
    """An unset CU provider ('') must not touch dispatch — CU still leads
    with brain.primary / the existing fallback-chain head (default-preserving,
    current behavior)."""
    manager = _FakeManager(cu_provider="")

    reply = await call_vision_brain(manager, build_prompt=_build_prompt, images=[])

    assert reply.provider == "brain-primary"
    assert manager.requested[0] == ("brain-primary", None)


async def test_cu_provider_already_in_chain_is_deduplicated() -> None:
    """When the configured CU provider is already present in the fallback
    chain, it is hoisted, never duplicated (no repeated dispatch attempt)."""
    manager = _FakeManager(cu_provider="brain-fallback")

    reply = await call_vision_brain(manager, build_prompt=_build_prompt, images=[])

    assert reply.provider == "brain-fallback"
    assert manager.requested == [("brain-fallback", None)]


async def test_cu_provider_resolver_missing_is_safe() -> None:
    """A manager without a callable ``_cu_provider`` (older manager shape)
    must dispatch exactly as before the feature — no AttributeError, no
    change in behavior."""
    manager = _FakeManagerNoResolver()

    reply = await call_vision_brain(manager, build_prompt=_build_prompt, images=[])

    assert reply.provider == "brain-primary"
    assert manager.requested[0] == ("brain-primary", None)


async def test_blind_cu_provider_falls_back_through_vision_gate() -> None:
    """A configured CU provider that cannot see the screen must never brick
    Computer-Use: the vision/health gate downstream (ComputerUsePlannerSelector)
    skips it and falls through to the next vision-capable candidate in the
    (hoisted) chain — never a hard failure just because the CU pick is blind
    (AP-21/22)."""
    manager = _FakeManager(cu_provider="claude-api")
    manager.brains["claude-api"] = _FakeBrain(
        text='{"action": "click"}', supports_vision=False,
    )
    img = ImageBlock(mime="image/jpeg", data_b64="QQ==", source_hash="x")

    reply = await call_vision_brain(
        manager, build_prompt=_build_prompt, images=[img],
    )

    assert reply.provider == "brain-primary"
    assert manager.brains["claude-api"].calls == 0


class _ProviderCfg:
    """Shape of ``BrainProviderConfig`` for the pin-resolution surface."""

    def __init__(
        self,
        *,
        tool_model: str | None = None,
        cu_model: str | None = None,
        model: str | None = None,
    ) -> None:
        self.tool_model = tool_model
        self.cu_model = cu_model
        self.model = model


class _PinAwareManager(_FakeManager):
    """FakeManager with a realistic per-provider config + Tool Model resolver.

    ``_cu_model`` mirrors the real ``BrainManager._tool_model_model``:
    ``tool_model`` → ``cu_model`` → main ``model`` → None. ``_provider_cfg``
    exposes the RAW config the pin detector reads.
    """

    def __init__(self, pins: dict[str, _ProviderCfg]) -> None:
        super().__init__(cu_provider="")
        self._pins = pins

    def _provider_cfg(self, name: str) -> _ProviderCfg | None:
        return self._pins.get(name)

    def _cu_model(self, name: str) -> str | None:
        cfg = self._pins.get(name)
        if cfg is None:
            return None
        return cfg.tool_model or cfg.cu_model or cfg.model


def _patch_speed_tune_catalog(monkeypatch) -> None:
    """Make the speed tune deterministic: every provider has a known fast
    vision sibling, and no configured model counts as fast-class."""
    import jarvis.brain.model_catalog as catalog

    monkeypatch.setattr(catalog, "model_capabilities", lambda p, m: {})
    monkeypatch.setattr(catalog, "is_fast_class_model", lambda m: False)
    monkeypatch.setattr(
        catalog, "pick_fast_vision_model", lambda p: "fast-vision-sibling"
    )
    monkeypatch.setattr(catalog, "provider_has_modality_data", lambda p: True)


async def test_canonical_tool_model_selection_is_a_pin_cu_must_not_swap(
    monkeypatch,
) -> None:
    """THE 2026-07-16 defect: the Tool Model tab persists only the canonical
    ``tool_model`` key, so after a restart the user's explicit selection
    (e.g. Claude Fable 5) no longer counted as a pin and the speed tune
    silently swapped it for the fast vision sibling — Computer-Use then ran
    on a model the user never chose, while realtime delegation honoured the
    selection. The canonical key must pin exactly like the legacy one."""
    _patch_speed_tune_catalog(monkeypatch)
    manager = _PinAwareManager(
        {"brain-primary": _ProviderCfg(tool_model="claude-fable-5")}
    )
    img = ImageBlock(mime="image/jpeg", data_b64="QQ==", source_hash="x")

    await call_vision_brain(manager, build_prompt=_build_prompt, images=[img])

    assert manager.requested[0] == ("brain-primary", "claude-fable-5")


async def test_legacy_cu_model_pin_still_wins(monkeypatch) -> None:
    """Old installations pin via ``cu_model`` only — that word still counts."""
    _patch_speed_tune_catalog(monkeypatch)
    manager = _PinAwareManager(
        {"brain-primary": _ProviderCfg(cu_model="my-legacy-cu-model")}
    )
    img = ImageBlock(mime="image/jpeg", data_b64="QQ==", source_hash="x")

    await call_vision_brain(manager, build_prompt=_build_prompt, images=[img])

    assert manager.requested[0] == ("brain-primary", "my-legacy-cu-model")


async def test_unpinned_provider_still_gets_speed_tuned(monkeypatch) -> None:
    """No explicit Tool Model selection → the speed tune keeps swapping the
    slow main model for the fast vision sibling (existing behavior)."""
    _patch_speed_tune_catalog(monkeypatch)
    manager = _PinAwareManager(
        {"brain-primary": _ProviderCfg(model="slow-flagship-chat-model")}
    )
    img = ImageBlock(mime="image/jpeg", data_b64="QQ==", source_hash="x")

    await call_vision_brain(manager, build_prompt=_build_prompt, images=[img])

    assert manager.requested[0] == ("brain-primary", "fast-vision-sibling")


async def test_serving_brain_logged_once_per_identity(caplog) -> None:
    """The provider that ACTUALLY serves is logged — once per identity change.

    Live forensic 2026-07-15: the per-candidate speed-tune line ("nvidia:
    stepping with the fast vision model …") fired for a chain member that
    never served a single call and was read as the stepping model, producing
    a false "text-only model drove Computer-Use" diagnosis. The serving log
    is the ground truth; repeating it on every step would only recreate the
    noise, so it fires only when the serving identity changes.
    """
    import logging

    import jarvis.cu.brain_call as brain_call

    brain_call._serving_logged = None
    manager = _FakeManager(cu_provider="")

    with caplog.at_level(logging.INFO, logger="jarvis.cu.brain_call"):
        await call_vision_brain(manager, build_prompt=_build_prompt, images=[])
        await call_vision_brain(manager, build_prompt=_build_prompt, images=[])

    serving = [r for r in caplog.records if "served by" in r.getMessage()]
    assert len(serving) == 1, [r.getMessage() for r in serving]
    assert "brain-primary" in serving[0].getMessage()

    caplog.clear()
    switched = _FakeManager(cu_provider="claude-api")
    with caplog.at_level(logging.INFO, logger="jarvis.cu.brain_call"):
        await call_vision_brain(switched, build_prompt=_build_prompt, images=[])

    serving = [r for r in caplog.records if "served by" in r.getMessage()]
    assert len(serving) == 1
    assert "claude-api" in serving[0].getMessage()
