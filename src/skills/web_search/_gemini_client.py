"""Gemini-backed client for the web-search skill.

The skill never imports a vendor SDK at module level — the client is defined
as a ``Protocol`` so unit tests can inject a fake without touching the
network. ``DefaultGeminiClient`` is the production implementation; it lazily
imports ``google.generativeai`` so the test suite stays import-clean even
when the SDK isn't installed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SearchHit:
    """A single search result row."""

    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class SearchResponse:
    """Aggregated response returned to the skill layer."""

    query: str
    summary: str
    hits: tuple[SearchHit, ...] = field(default_factory=tuple)
    latency_ms: float = 0.0
    model: str = "unknown"


@runtime_checkable
class GeminiClient(Protocol):
    """Minimal client surface the skill depends on.

    Anything that satisfies this protocol — including the in-test fake — is
    swappable. Keeping the surface tiny (one method) makes the latency
    contract easy to reason about: the skill measures wall-clock time
    around exactly this call.
    """

    def search(self, query: str, *, max_results: int) -> SearchResponse: ...


class DefaultGeminiClient:
    """Production client wrapping ``google.generativeai``.

    The vendor import is deferred to :meth:`search` so an ``ImportError`` is
    only raised when the client is actually invoked, never at module load.

    The default model tracks the Personal Jarvis main-brain default
    (``gemini-3.5-flash`` since 2026-05-20, see ``jarvis.toml:187``). Phase 2
    of ADR-021 replaces this hardcode with a read from
    ``cfg.brain.providers.gemini.flash_model`` — the CLAUDE.md rule
    "NIEMALS hardcoded Flash" applies once the skill is wired into Jarvis.
    """

    def __init__(
        self,
        *,
        model: str = "gemini-3.5-flash",
        api_key: str | None = None,
    ) -> None:
        self._model_name = model
        self._api_key = api_key
        self._configured = False

    def _configure(self) -> None:
        if self._configured:
            return
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise RuntimeError(
                "google-generativeai is not installed; "
                "install it or inject a fake GeminiClient in tests"
            ) from exc

        if self._api_key:
            genai.configure(api_key=self._api_key)
        self._genai = genai
        self._configured = True

    def search(self, query: str, *, max_results: int) -> SearchResponse:
        self._configure()
        start = time.perf_counter()
        model = self._genai.GenerativeModel(self._model_name)
        prompt = (
            "Search the web for the user query and return the top "
            f"{max_results} factual results as a brief markdown summary "
            "followed by a JSON array of {title,url,snippet} objects.\n\n"
            f"Query: {query}"
        )
        raw = model.generate_content(prompt)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        text = getattr(raw, "text", "")
        return SearchResponse(
            query=query,
            summary=text,
            hits=(),
            latency_ms=elapsed_ms,
            model=self._model_name,
        )


class FakeGeminiClient:
    """Deterministic in-memory client for tests.

    * ``latency_ms`` lets a latency test bound the round-trip time without
      sleeping for real.
    * ``hits`` is the fixed dataset returned for every call.
    """

    def __init__(
        self,
        *,
        summary: str = "fake summary",
        hits: tuple[SearchHit, ...] = (),
        latency_ms: float = 5.0,
        model: str = "fake-model",
    ) -> None:
        self.summary = summary
        self.hits = hits
        self.latency_ms = latency_ms
        self.model = model
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, max_results: int) -> SearchResponse:
        self.calls.append((query, max_results))
        if self.latency_ms > 0:
            time.sleep(self.latency_ms / 1000.0)
        truncated = self.hits[:max_results]
        return SearchResponse(
            query=query,
            summary=self.summary,
            hits=truncated,
            latency_ms=self.latency_ms,
            model=self.model,
        )
