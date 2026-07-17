"""Frontier model resolver — queries provider /v1/models endpoints and returns
the latest model for each (provider, tier) pair.

Goal: Hauptjarvis defaults should migrate automatically to frontier models
without having to maintain jarvis.toml manually. Wave-4 migration:
``[brain.sub_jarvis]`` is legacy — the sticky pin remains effective as long as
the block exists.

Not covered (user mandate):
- OpenRouter (not authoritative — direct provider /models endpoints win)
- Mistral/Deepseek (require a separate trigger)

Cache: ``data/frontier_cache.json``, TTL 24 h. On error: fall back to the last
cached value; if that is also missing → ``None`` (caller keeps its TOML default).

Tier mapping per provider:
- claude-api : fast=haiku, deep=opus
- gemini     : fast=flash, deep=pro
- openai     : fast=gpt-{N} (without -pro/-mini/-nano), deep=gpt-{N}-pro
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import httpx

from jarvis.core import config as cfg

log = logging.getLogger(__name__)

Tier = Literal["fast", "deep"]

DEFAULT_TTL_HOURS = 24
SUPPORTED_PROVIDERS: tuple[str, ...] = ("claude-api", "gemini", "openai")

# Stale list — models we explicitly NEVER pick as frontier, even if the
# provider API lists them (account tier limits, legacy snapshots).
# 2026-04-29 user mandate: "frontier everywhere, no budget models". If the
# pick lands on a stale entry, the picker returns None and the auto-switch
# keeps the TOML default (frontier).
#
# Maintenance: only production end-of-life IDs here. Active-but-older snapshots
# (e.g. claude-haiku-4-5) stay out, otherwise listings that have no 4.6+ entry
# are killed.
STALE_MODELS: frozenset[str] = frozenset({
    # xAI — Grok-3-Generation komplett raus, 4.x ist Frontier
    "grok-3", "grok-3-mini", "grok-3-fast", "grok-3-mini-fast",
    "grok-2", "grok-2-mini", "grok-2-vision",
    "grok-beta", "grok-vision-beta",
    # OpenAI — alles vor GPT-5
    "gpt-4o", "gpt-4o-mini", "gpt-4o-2024-05-13", "gpt-4o-2024-08-06",
    "gpt-4-turbo", "gpt-4-turbo-preview", "gpt-4", "gpt-4-32k",
    "gpt-3.5-turbo", "gpt-3.5-turbo-16k",
    # Google — alles vor Gemini 3
    "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite",
    "gemini-2.0-flash", "gemini-2.0-pro", "gemini-2.0-flash-lite",
    "gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.5-flash-8b",
    "gemini-pro", "gemini-pro-vision",
    # Anthropic — alles vor 4.x
    "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
    "claude-3-5-sonnet", "claude-3-5-haiku",
    "claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20240620",
    "claude-3-opus-20240229", "claude-3-haiku-20240307",
})


@dataclass(frozen=True, slots=True)
class FrontierModel:
    """Cache entry for (provider, tier) → model_id."""

    provider: str
    tier: str
    model_id: str
    fetched_at: float


class FrontierResolver:
    """Deliver frontier models per (provider, tier) with a 24 h cache.

    Boot path: ``await resolver.resolve_latest("gemini", "fast")``. On a
    cache hit (TTL 24 h) no API call is made. Otherwise: /v1/models, filter,
    persist, return. On API error: last known value or ``None``.
    """

    def __init__(
        self,
        cache_path: Path | None = None,
        ttl_hours: int = DEFAULT_TTL_HOURS,
        http_client_factory: object | None = None,
    ) -> None:
        self._cache_path = cache_path or Path("data/frontier_cache.json")
        self._ttl_seconds = ttl_hours * 3600
        self._cache: dict[str, dict[str, FrontierModel]] = {}
        self._lock = asyncio.Lock()
        # Test hook: tests can inject an httpx.AsyncClient factory.
        self._client_factory = http_client_factory
        self._load_cache()

    # ------------------------------------------------------------------
    # Cache I/O
    # ------------------------------------------------------------------

    def _load_cache(self) -> None:
        if not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            for prov, tiers in data.items():
                self._cache[prov] = {
                    t: FrontierModel(**v) for t, v in tiers.items()
                }
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            log.warning("frontier_cache.json corrupt — discarded: %s", exc)
            self._cache.clear()

    def _save_cache(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            prov: {t: asdict(fm) for t, fm in tiers.items()}
            for prov, tiers in self._cache.items()
        }
        self._cache_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8",
        )

    def _is_fresh(self, fm: FrontierModel) -> bool:
        return (time.time() - fm.fetched_at) < self._ttl_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def resolve_latest(self, provider: str, tier: Tier) -> str | None:
        """Return the latest model for (provider, tier).

        Returns ``None`` when the provider is unknown or both the API call
        and the cache are unavailable.
        """
        if provider not in SUPPORTED_PROVIDERS:
            return None

        async with self._lock:
            cached = self._cache.get(provider, {}).get(tier)
            if cached and self._is_fresh(cached):
                return cached.model_id

            try:
                models = await self._fetch_models(provider)
            except Exception as exc:  # noqa: BLE001 — resolver failure must not stop the boot.
                log.warning(
                    "Frontier fetch for %s failed (cache fallback): %s",
                    provider, exc,
                )
                return cached.model_id if cached else None

            chosen = self._pick_latest(models, provider, tier)
            if chosen is None:
                log.info(
                    "Frontier resolver: no match for %s/%s in %d models",
                    provider, tier, len(models),
                )
                return cached.model_id if cached else None

            self._cache.setdefault(provider, {})[tier] = FrontierModel(
                provider=provider, tier=tier, model_id=chosen,
                fetched_at=time.time(),
            )
            self._save_cache()
            return chosen

    # ------------------------------------------------------------------
    # Provider /v1/models calls
    # ------------------------------------------------------------------

    async def _client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            # Test injection: factory returns an AsyncClient (or mock).
            return self._client_factory()  # type: ignore[operator]
        return httpx.AsyncClient(timeout=10.0)

    async def _fetch_models(self, provider: str) -> list[str]:
        """Call the /v1/models endpoint for the given provider."""
        client = await self._client()
        async with client:
            if provider == "claude-api":
                api_key = cfg.get_provider_secret("claude-api")
                if not api_key:
                    raise RuntimeError(
                        "No Anthropic API key for the frontier resolver.",
                    )
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
                resp.raise_for_status()
                return [m["id"] for m in resp.json().get("data", [])]

            if provider == "openai":
                api_key = cfg.get_provider_secret("openai")
                if not api_key:
                    raise RuntimeError(
                        "No OpenAI API key for the frontier resolver.",
                    )
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                return [m["id"] for m in resp.json().get("data", [])]

            if provider == "gemini":
                api_key = cfg.get_provider_secret("gemini")
                if not api_key:
                    raise RuntimeError(
                        "No Gemini API key for the frontier resolver.",
                    )
                resp = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models"
                    f"?key={api_key}",
                )
                resp.raise_for_status()
                # Gemini-Format: models[].name = "models/gemini-3-flash".
                # Capability gate: only ids the API declares as generateContent-
                # capable may win the auto-pick. Google has listed ids that 404
                # on every chat call (the 2026-04 "gemini-3-flash" incident:
                # listed as a clean stable name, so the GA-over-preview ranking
                # picked it — and every fresh install ran a dead model).
                from jarvis.brain.model_catalog import (
                    gemini_entry_serves_generate_content,
                )

                return [
                    m["name"].removeprefix("models/")
                    for m in resp.json().get("models", [])
                    if gemini_entry_serves_generate_content(m)
                ]

            raise ValueError(f"Unsupported provider: {provider}")

    # ------------------------------------------------------------------
    # Provider-Heuristik
    # ------------------------------------------------------------------

    def _pick_latest(
        self, models: list[str], provider: str, tier: Tier,
    ) -> str | None:
        """Provider-specific heuristic for selecting the latest model."""
        if provider == "claude-api":
            return _pick_anthropic(models, tier)
        if provider == "gemini":
            return _pick_gemini(models, tier)
        if provider == "openai":
            return _pick_openai(models, tier)
        return None


# ----------------------------------------------------------------------
# Provider picker (module-level for easier testing)
# ----------------------------------------------------------------------

def _pick_anthropic(models: list[str], tier: Tier) -> str | None:
    """Anthropic: claude-opus-4-7 / claude-haiku-4-5-{date}.

    Fast=Haiku, Deep=Opus. Prefers CLEAN stable aliases without a date suffix
    (claude-opus-4-7 > claude-opus-4-20250514) — otherwise 8-digit date
    snapshots beat semantic versions numerically. Dated IDs are used only when
    no clean variant exists.
    """
    family = "haiku" if tier == "fast" else "opus"
    candidates = [m for m in models if family in m and m not in STALE_MODELS]
    if not candidates:
        return None

    def is_dated(m: str) -> bool:
        # claude-opus-4-7-20251022 or claude-opus-4-20250514 — date suffix
        # with 6+ trailing digits. Numeric Major.Minor.Patch are <= 4 digits.
        return bool(re.search(r"-\d{6,}$", m))

    def key(m: str) -> tuple[int, ...]:
        # Priority: clean (non-dated) > dated
        # Secondary: Major.Minor (numeric tuple of the first 2-3 fields)
        prio = 1 if not is_dated(m) else 0
        # First 2 numeric tokens (Major, Minor) — ignore dates
        nums = re.findall(r"\d+", m)
        # Filter out obvious dates (>= 6 digits)
        nums = [int(n) for n in nums if len(n) < 6]
        return (prio, *nums)

    candidates.sort(key=key, reverse=True)
    return candidates[0]


def _pick_gemini(models: list[str], tier: Tier) -> str | None:
    """Gemini: gemini-{major}-{family} or gemini-{major}.{minor}-{family}-{stage}.

    Fast=flash, Deep=pro. Lite is excluded for deep. Preview-stage may win
    when no stable model exists (frontier models are often preview-only).
    """
    family = "flash" if tier == "fast" else "pro"
    candidates = [
        m for m in models
        if family in m and m.startswith("gemini-") and m not in STALE_MODELS
    ]
    # Remove specialised variants: the user wants general-purpose frontier, not
    # Lite/Image/Vision/TTS/Live/Audio/Native models. Bug 2026-04-29: the resolver
    # returned "gemini-3.1-flash-image-preview" as fast (image-generation variant,
    # not general-purpose).
    SPECIALIZED = ("lite", "image", "vision", "tts", "audio", "live",
                   "native", "thinking", "tuning", "embedding")
    candidates = [
        c for c in candidates
        if not any(spec in c for spec in SPECIALIZED)
    ]
    if tier == "deep":
        candidates = [c for c in candidates if "pro" in c]
    elif tier == "fast":
        candidates = [c for c in candidates if "pro" not in c]
    if not candidates:
        return None

    def key(m: str) -> tuple[int, int, int, int]:
        match = re.search(r"gemini-(\d+)(?:\.(\d+))?", m)
        major = int(match.group(1)) if match else 0
        minor = int(match.group(2)) if match and match.group(2) else 0
        # GA wins over Preview when both exist.
        stage = 0 if ("preview" in m or "exp" in m or "experimental" in m) else 1
        # TTS/Vision specialised variants are demoted (not general-purpose frontier).
        is_specialized = 1 if any(
            tag in m for tag in ("-tts-", "-vision-", "-thinking-", "-tuning")
        ) else 0
        return (major, minor, -is_specialized, stage)

    candidates.sort(key=key, reverse=True)
    return candidates[0]


def _pick_openai(models: list[str], tier: Tier) -> str | None:
    """OpenAI: gpt-{major}{.minor}{-pro|-mini|-nano}?.

    Fast=gpt-{N} (no -pro/-mini/-nano), Deep=gpt-{N}-pro.
    """
    candidates = [m for m in models if m.startswith("gpt-") and m not in STALE_MODELS]
    if not candidates:
        return None

    if tier == "deep":
        filtered = [c for c in candidates if "-pro" in c]
    else:
        # Fast: no Pro/Mini/Nano. -mini is cost-effective but not always the
        # fastest/most capable — the user wants frontier, i.e. full gpt-{N}.
        filtered = [
            c for c in candidates
            if "-pro" not in c
            and "-mini" not in c
            and "-nano" not in c
            and "preview" not in c
        ]
    if not filtered:
        return None

    def key(m: str) -> tuple[float, int]:
        match = re.search(r"gpt-(\d+(?:\.\d+)?)", m)
        ver = float(match.group(1)) if match else 0.0
        stage = 0 if "preview" in m else 1
        return (ver, stage)

    filtered.sort(key=key, reverse=True)
    return filtered[0]
