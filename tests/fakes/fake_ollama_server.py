"""An in-process Ollama server for tests: the native API over ``httpx.MockTransport``.

Models the surface :mod:`jarvis.brain.ollama_inventory` talks to — ``/api/tags``,
``/api/show``, ``/api/ps``, ``/api/generate`` (unload ping), ``/api/embed``, ``/api/delete`` —
with real state: deleting a model removes it from the next ``/api/tags``, an
unload ping removes it from ``/api/ps``, and every call is recorded so a test
can assert on what was sent. Nothing here touches the network.

Usage::

    fake = FakeOllamaServer()
    fake.add("qwen3.5:4b", size=3_400_000_000, capabilities=("completion", "tools"))
    models = await list_models("http://localhost:11434", transport=fake.transport())
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

__all__ = ["FakeOllamaModel", "FakeOllamaServer"]


@dataclass
class FakeOllamaModel:
    name: str
    size: int = 1_000_000_000
    digest: str = "sha256:deadbeef"
    modified_at: str = "2026-08-20T10:00:00Z"
    family: str = "qwen3"
    parameter_size: str = "4B"
    quantization_level: str = "Q4_K_M"
    context_length: int | None = 262_144
    capabilities: tuple[str, ...] = ("completion", "tools", "vision")
    license: str = "Apache-2.0"
    parameters: str = "temperature 0.7"
    template: str = "{{ .Prompt }}"
    #: When True the ``/api/show`` probe for this model answers 500.
    show_fails: bool = False
    #: Present in ``/api/ps`` when set.
    loaded: bool = False
    #: Vector length ``/api/embed`` answers with (0 = the server returns no vector).
    embed_dim: int = 768
    size_vram: int = 0
    expires_at: str = "2026-08-24T12:05:00Z"
    running_context: int = 8192


@dataclass
class FakeOllamaServer:
    models: dict[str, FakeOllamaModel] = field(default_factory=dict)
    #: ``(method, path, json body)`` of every request, in order.
    calls: list[tuple[str, str, Any]] = field(default_factory=list)
    #: When True every request raises ``httpx.ConnectError``.
    offline: bool = False
    #: What ``GET /api/version`` answers.
    version: str = "0.32.15"

    # -- building state ----------------------------------------------------

    def add(self, name: str, **facts: Any) -> FakeOllamaModel:
        model = FakeOllamaModel(name=name, **facts)
        self.models[name] = model
        return model

    def load(self, name: str, *, size_vram: int | None = None) -> None:
        model = self.models[name]
        model.loaded = True
        model.size_vram = model.size if size_vram is None else size_vram

    # -- transport ---------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _body(self, request: httpx.Request) -> Any:
        if not request.content:
            return None
        try:
            return json.loads(request.content.decode("utf-8"))
        except ValueError:
            return None

    def _find(self, name: str) -> FakeOllamaModel | None:
        name = (name or "").strip()
        if name in self.models:
            return self.models[name]
        if ":" not in name and f"{name}:latest" in self.models:
            return self.models[f"{name}:latest"]
        return None

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        body = self._body(request)
        self.calls.append((request.method, path, body))

        if request.method == "GET" and path == "/api/version":
            return httpx.Response(200, json={"version": self.version})
        if request.method == "GET" and path == "/api/tags":
            rows = [self._tags_row(m) for m in self.models.values()]
            return httpx.Response(200, json={"models": rows})
        if request.method == "POST" and path == "/api/show":
            model = self._find((body or {}).get("model") or (body or {}).get("name") or "")
            if model is None:
                return httpx.Response(404, json={"error": "model not found"})
            if model.show_fails:
                return httpx.Response(500, json={"error": "manifest unreadable"})
            return httpx.Response(200, json=self._show_payload(model))
        if request.method == "GET" and path == "/api/ps":
            return httpx.Response(
                200, json={"models": [self._ps_row(m) for m in self.models.values() if m.loaded]}
            )
        if request.method == "POST" and path == "/api/generate":
            model = self._find((body or {}).get("model") or "")
            if model is None:
                return httpx.Response(404, json={"error": "model not found"})
            if (body or {}).get("keep_alive") == 0:
                model.loaded = False
                return httpx.Response(
                    200, json={"model": model.name, "done": True, "done_reason": "unload"}
                )
            model.loaded = True
            return httpx.Response(200, json={"model": model.name, "done": True, "response": ""})
        if request.method == "POST" and path == "/api/embed":
            model = self._find((body or {}).get("model") or "")
            if model is None:
                return httpx.Response(404, json={"error": "model not found"})
            if "embedding" not in model.capabilities:
                return httpx.Response(
                    400, json={"error": f"{model.name} does not support embeddings"}
                )
            vectors = [[0.1] * model.embed_dim] if model.embed_dim > 0 else []
            return httpx.Response(200, json={"model": model.name, "embeddings": vectors})
        if request.method == "DELETE" and path == "/api/delete":
            model = self._find((body or {}).get("model") or "")
            if model is None:
                return httpx.Response(404, json={"error": "model not found"})
            del self.models[model.name]
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": f"no route for {request.method} {path}"})

    # -- payload shapes (mirror Ollama 0.32) --------------------------------

    @staticmethod
    def _details(m: FakeOllamaModel) -> dict[str, Any]:
        return {
            "format": "gguf",
            "family": m.family,
            "families": [m.family],
            "parameter_size": m.parameter_size,
            "quantization_level": m.quantization_level,
        }

    def _tags_row(self, m: FakeOllamaModel) -> dict[str, Any]:
        return {
            "name": m.name,
            "model": m.name,
            "modified_at": m.modified_at,
            "size": m.size,
            "digest": m.digest,
            "details": self._details(m),
        }

    def _show_payload(self, m: FakeOllamaModel) -> dict[str, Any]:
        model_info: dict[str, Any] = {"general.architecture": m.family}
        if m.context_length is not None:
            model_info[f"{m.family}.context_length"] = m.context_length
        return {
            "license": m.license,
            "modelfile": f"FROM {m.name}",
            "parameters": m.parameters,
            "template": m.template,
            "details": self._details(m),
            "model_info": model_info,
            "capabilities": list(m.capabilities),
        }

    def _ps_row(self, m: FakeOllamaModel) -> dict[str, Any]:
        return {
            "name": m.name,
            "model": m.name,
            "size": m.size,
            "digest": m.digest,
            "details": self._details(m),
            "expires_at": m.expires_at,
            "size_vram": m.size_vram,
            "context_length": m.running_context,
        }
