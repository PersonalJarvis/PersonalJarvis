"""Records every seam through which a health check could reach a provider.

The status dots (API-Keys tabs, sidebar, dock, chat composer) must never send
a billed request on their own (decision 2026-09-30). This fake replaces each
seam that talks to a provider — the shared connectivity test and its default
builders, the brain health checker, the model-picker probe, the dictation
wording probe and brain-plugin instantiation — with a recorder. A test then
drives the health routes and asserts :attr:`ProviderCallRecorder.calls` is
empty; a regression that reintroduces a probe shows up as a named call
instead of a real request to a provider.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class ProviderCallRecorder:
    """Install with :meth:`install`; inspect :attr:`calls` afterwards."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def _note(self, seam: str, subject: Any) -> None:
        name = getattr(subject, "id", None) or getattr(subject, "name", None) or subject
        self.calls.append((seam, str(name)))

    def install(self, monkeypatch: Any) -> ProviderCallRecorder:
        from jarvis.brain import healthcheck, provider_registry, provider_test
        from jarvis.dictation import polish_probe
        from jarvis.ui.web import provider_routes

        recorder = self

        async def run_provider_test(spec: Any, cfg: Any, **_kwargs: Any) -> Any:
            recorder._note("run_provider_test", spec)
            return SimpleNamespace(provider=getattr(spec, "id", ""), status="ok", detail="")

        async def realtime_probe(spec: Any, cfg: Any, **_kwargs: Any) -> float:
            recorder._note("realtime_probe", spec)
            return 0.0

        def make_tts(cfg: Any, provider: str) -> Any:
            recorder._note("make_tts", provider)
            raise AssertionError("a health check built a TTS provider")

        def make_stt(cfg: Any, provider: str) -> Any:
            recorder._note("make_stt", provider)
            raise AssertionError("a health check built an STT provider")

        async def checker_probe(_self: Any, provider: str, model: str, **_kwargs: Any) -> Any:
            recorder._note("brain_health_probe", provider)
            return SimpleNamespace(ok=True, error=None, duration_ms=0.0)

        async def probe_brain_model(provider: str, model: str, **_kwargs: Any) -> Any:
            recorder._note("model_probe", provider)
            return provider_test.ProviderTestResult(provider, provider_test.OK)

        async def probe_polish(family: Any, cfg: Any, **_kwargs: Any) -> Any:
            recorder._note("polish_probe", family)
            return SimpleNamespace(status="ok", detail="")

        def instantiate(_self: Any, name: str, **_kwargs: Any) -> Any:
            recorder._note("brain_instantiate", name)
            raise AssertionError("a health check built a brain provider")

        monkeypatch.setattr(provider_test, "run_provider_test", run_provider_test)
        monkeypatch.setattr(provider_test, "_default_realtime_probe", realtime_probe)
        monkeypatch.setattr(provider_test, "_default_make_tts", make_tts)
        monkeypatch.setattr(provider_test, "_default_make_stt", make_stt)
        monkeypatch.setattr(healthcheck.BrainHealthChecker, "probe", checker_probe)
        monkeypatch.setattr(provider_routes, "_probe_brain_model", probe_brain_model)
        monkeypatch.setattr(polish_probe, "probe_polish_family", probe_polish)
        monkeypatch.setattr(
            provider_registry.BrainProviderRegistry, "instantiate", instantiate
        )
        return self


__all__ = ["ProviderCallRecorder"]
