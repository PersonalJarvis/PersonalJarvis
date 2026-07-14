"""Curator LLM — provider-agnostic intelligence layer (Phase B1, Instance D).

Wraps one Brain provider in the ``CuratorLLM`` Protocol from
``protocols.py``. The provider is resolved at construction time from
``[memory.wiki.curator]`` with two fallbacks:

1. ``provider`` empty → use ``brain.primary``.
2. ``model`` empty → use the provider's CHEAP/FAST router-tier model
   (``get_tier_default_model("router", provider)``), NOT the provider's
   full frontier chat model. An explicit ``model`` override always wins.

The brain call is wrapped in ``asyncio.wait_for(timeout=cfg.timeout_s)``
so a hung provider never blocks an ingest forever. Any failure — JSON
parse error, schema mismatch, timeout, brain exception — produces an
empty ``list[PageUpdate]`` and a logged warning. The orchestrator is
required to keep running across LLM faults; raising is not an option.

The salience filter (smalltalk → ``[]``) lives in the prompt, not here.
We trust the LLM to follow the contract; we only validate its JSON
shape and translate it into ``PageUpdate`` objects.

Reference pattern: ``jarvis/awareness/verdichter.py`` (same brain-via-
config, timeout-via-wait_for, error-tolerant shape).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis.brain.provider_registry import BrainProviderRegistry
from jarvis.brain.streaming import aggregate, is_length_truncated
from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.memory.wiki.prompt import (
    build_system_prompt,
    build_user_prompt,
    compute_vault_summary,
    select_top_slugs,
)
from jarvis.memory.wiki.protocols import PageUpdate
from jarvis.memory.wiki.telemetry import telemetry

if TYPE_CHECKING:
    from jarvis.core.config import JarvisConfig, WikiCuratorConfig
    from jarvis.core.protocols import Brain
    from jarvis.memory.wiki.protocols import PageRepository, VaultIndex

logger = logging.getLogger(__name__)

# Hard cap on the proposed updates the writer will receive. Keeps a
# misbehaving LLM (e.g. one that emits hundreds of speculative edits)
# from overwhelming Instance C's backup + validate pipeline.
_MAX_UPDATES_PER_INGEST = 30

# Allowed operation strings — anything else is dropped at parse time.
_VALID_OPERATIONS: frozenset[str] = frozenset({"create", "update", "rename", "archive"})

# Minimum non-whitespace characters in a source before the LLM is even
# called. Avoids burning tokens on empty events.
_MIN_SOURCE_CHARS = 3

# Cheap/fast model per provider for the curator's default path. This is
# the long-term-memory tier: background ingest must NOT bill the user's
# frontier chat model. We mirror the router-tier ("fast") defaults from
# jarvis.brain.manager.TIER_DEFAULTS_BY_PROVIDER["router"]; the live
# values are read from there at resolve time, this map is only the
# last-resort fallback when that import is unavailable.
_CHEAP_MODEL_FALLBACK: dict[str, str] = {
    "claude-api": "claude-haiku-4-5-20251001",
    "gemini": "gemini-3-flash-preview",
    "openai": "gpt-5.5",
    # codex removed: not in TIER_DEFAULTS_BY_PROVIDER["router"]; the fallback
    # map must stay in lock-step with the live router defaults (drift guard in
    # test_cheap_model_fallback_map_matches_live_router_defaults).
    "deepseek": "deepseek-chat",
    # Gateway: background curation must not bill a paid Anthropic model on a
    # free OpenRouter key (§3/AP-22) — use a free general-purpose model.
    "openrouter": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "mistral": "mistral-small-3.1",
}


def _cheap_model_for(provider: str) -> str | None:
    """Cheap/fast model for the curator's default path.

    Prefers the live router-tier default from ``jarvis.brain.manager``
    (single source of truth); falls back to the local ``_CHEAP_MODEL_FALLBACK``
    map if that module cannot be imported (minimal VPS / partial install).
    Returns ``None`` for an unknown provider so the registry picks its own
    default.
    """

    try:
        from jarvis.brain.manager import get_tier_default_model

        live = get_tier_default_model("router", provider)
        if live:
            return live
    except ImportError:
        # Minimal VPS / partial install — expected, fallback map is the plan.
        logger.debug(
            "_cheap_model_for(%r): jarvis.brain.manager unimportable, "
            "using _CHEAP_MODEL_FALLBACK",
            provider,
        )
    except Exception as exc:  # noqa: BLE001
        # Anything else is unexpected — make it diagnosable.
        logger.warning(
            "_cheap_model_for(%r): get_tier_default_model failed (%s) — "
            "using _CHEAP_MODEL_FALLBACK",
            provider, exc,
        )
    return _CHEAP_MODEL_FALLBACK.get(provider)


def _resolve_provider_and_model(
    cfg: WikiCuratorConfig, root: JarvisConfig,
) -> tuple[str, str | None]:
    """Resolve (provider, model) for the curator LLM.

    Provider: ``cfg.provider`` if set, else ``brain.primary``.

    Model precedence (cheap-by-default — long-term memory must not bill
    the user's frontier chat model):

    1. An explicit ``cfg.model`` always wins.
    2. Otherwise the resolved provider's CHEAP/FAST router-tier model
       (``jarvis.brain.manager.get_tier_default_model("router", provider)``,
       mirrored by ``_CHEAP_MODEL_FALLBACK``).
    3. Otherwise ``None`` — the registry instantiates its own default
       (matches ``BrainProviderRegistry.instantiate(name, model=None)``).

    Note: ``brain.providers[provider].model`` (the user's full frontier
    chat model) is intentionally NOT used here — that is the expensive
    path this resolver exists to avoid.
    """

    provider = cfg.provider.strip() or root.brain.primary
    model = cfg.model.strip()

    if not model:
        model = _cheap_model_for(provider) or ""

    return provider, (model or None)


def instantiate_curator_brain(
    registry: Any, provider: str, model: str | None,
) -> Any:
    """Instantiate a curator-tier brain with extended thinking DISABLED.

    Background curation (extractor, judge, legacy curator) is deterministic
    JSON work; Gemini 3 thinking models otherwise burn the
    ``max_output_tokens`` budget on internal reasoning tokens and the
    visible output hits MAX_TOKENS after a few sentences — every batch then
    dies in the truncation guard (live finding 2026-06-10). Mirrors the
    ``BrainManager`` fast-path rules: ``thinking_budget=0`` only for
    Gemini NON-pro models (pro rejects 0 with a 400); other providers
    never see the kwarg; a ``TypeError`` from an older provider signature
    falls back to plain instantiation.
    """
    kwargs: dict[str, Any] = {"model": model}
    if provider == "gemini" and "pro" not in (model or "").lower():
        kwargs["thinking_budget"] = 0
    try:
        return registry.instantiate(provider, **kwargs)
    except TypeError:
        return registry.instantiate(provider)


def _extract_json_array(text: str) -> Any:
    """Best-effort JSON-array extraction from a possibly-noisy response.

    LLMs sometimes wrap structured output in code fences or add a leading
    sentence even when told not to. We strip an optional ``` fence and
    then look for the first ``[`` and the matching last ``]``. Anything
    that does not decode to a list is rejected.

    Raises ``ValueError`` when no usable array is found.
    """

    candidate = text.strip()

    # Strip a code fence if present.
    if candidate.startswith("```"):
        # Drop the opening fence (with or without language hint).
        candidate = candidate.split("\n", 1)[1] if "\n" in candidate else ""
        if candidate.endswith("```"):
            candidate = candidate[:-3]
        candidate = candidate.strip()

    # Locate the outermost array brackets.
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON array found in response")

    array_text = candidate[start : end + 1]
    parsed = json.loads(array_text)
    if not isinstance(parsed, list):
        raise ValueError(f"expected JSON array, got {type(parsed).__name__}")
    return parsed


def _coerce_to_update(item: Any) -> PageUpdate | None:
    """Translate one JSON object into a ``PageUpdate``.

    Returns ``None`` when the object is malformed (missing fields, bad
    operation, empty body, …). The caller logs the count of dropped
    items but does not surface individual errors.
    """

    if not isinstance(item, dict):
        return None

    target = item.get("target")
    operation = item.get("operation")
    new_body = item.get("new_body")
    rename_from = item.get("rename_from")
    reason = item.get("reason", "")

    if not isinstance(target, str) or not target.strip():
        return None
    if not isinstance(operation, str) or operation not in _VALID_OPERATIONS:
        return None
    if not isinstance(new_body, str) or not new_body.strip():
        # archive updates may carry an empty body in theory, but B1 has
        # no archive-without-body use case; treat empty bodies as bugs.
        return None

    target_path = Path(target)
    # Defensive: a leading "./" or absolute path would let an LLM escape
    # the vault. Strip the leading slash; the writer joins against the
    # vault root and re-validates.
    if target_path.is_absolute():
        try:
            target_path = Path(*target_path.parts[1:])
        except IndexError:
            return None

    rename_path: Path | None = None
    if operation == "rename":
        if not isinstance(rename_from, str) or not rename_from.strip():
            return None
        rename_path = Path(rename_from)
        if rename_path.is_absolute():
            try:
                rename_path = Path(*rename_path.parts[1:])
            except IndexError:
                return None

    return PageUpdate(
        target_path=target_path,
        operation=operation,
        new_body=new_body,
        rename_from=rename_path,
        reason=str(reason)[:500] if reason else "",
    )


def _parse_updates(raw_text: str) -> list[PageUpdate]:
    """Turn the raw LLM text into a list of ``PageUpdate``.

    Returns an empty list when no usable updates survive. Logs a warning
    when items are dropped so we can spot bad prompts in the field.
    """

    parsed = _extract_json_array(raw_text)
    if not parsed:
        return []

    updates: list[PageUpdate] = []
    dropped = 0
    for item in parsed:
        update = _coerce_to_update(item)
        if update is None:
            dropped += 1
            continue
        updates.append(update)
        if len(updates) >= _MAX_UPDATES_PER_INGEST:
            break

    if dropped:
        logger.warning(
            "WikiCuratorLLM dropped %d malformed update(s) from response",
            dropped,
        )
    return updates


class WikiCuratorLLM:
    """Default ``CuratorLLM`` implementation.

    Constructed once per process and reused across ingests. The Brain
    instance is created lazily on the first call so a misconfigured
    provider never crashes the orchestrator at startup.

    Parameters
    ----------
    config:
        Full root config — we read ``memory.wiki.curator`` and
        ``brain.primary`` / ``brain.providers``.
    schema_path:
        Path to the binding ``schema.md`` (verbatim in the system
        prompt). Defaults to ``wiki/obsidian-vault/schema.md`` next to the
        vault.
    log_path:
        Path to the wiki ``log.md`` — used only to enrich the system
        prompt with recent activity. Missing file degrades silently.
    registry:
        Optional injected ``BrainProviderRegistry``. Tests inject a fake
        registry; production code uses a fresh one.
    """

    def __init__(
        self,
        *,
        config: JarvisConfig,
        schema_path: Path,
        log_path: Path | None = None,
        registry: BrainProviderRegistry | None = None,
    ) -> None:
        self._config = config
        self._cfg = config.memory.wiki.curator
        self._schema_path = schema_path
        self._log_path = log_path
        self._registry = registry or BrainProviderRegistry()
        self._credential_filter = registry is None
        self._brain: Brain | None = None
        self._resolved_provider: str | None = None
        self._resolved_model: str | None = None
        self._lock = asyncio.Lock()

    @property
    def provider_name(self) -> str | None:
        """The provider that will be (or has been) instantiated."""

        return self._resolved_provider

    async def propose_updates(
        self,
        source_content: str,
        source_label: str,
        *,
        repo: PageRepository,
        vault: VaultIndex,
    ) -> list[PageUpdate]:
        """Ask the configured Brain for a list of wiki updates.

        Always returns a list. Never raises. The empty list covers:

        * source content shorter than ``_MIN_SOURCE_CHARS`` chars,
        * schema file unreadable,
        * brain unavailable / not in the registry,
        * brain timeout (``asyncio.wait_for`` above ``cfg.timeout_s``),
        * brain raises any exception,
        * response JSON is malformed,
        * every update in the response failed validation.
        """

        # ``repo`` is unused today; the Protocol exposes it so a future
        # post-validation pass can re-parse pages without changing the
        # signature. Referencing it keeps mypy / pyright quiet.
        del repo

        if not source_content or len(source_content.strip()) < _MIN_SOURCE_CHARS:
            logger.debug(
                "WikiCuratorLLM: source too short (%d chars), skipping LLM call",
                len(source_content or ""),
            )
            return []

        schema_md = await self._load_schema()
        if schema_md is None:
            logger.warning(
                "WikiCuratorLLM: schema file unreadable at %s — returning empty list",
                self._schema_path,
            )
            return []

        vault_summary = await asyncio.to_thread(
            compute_vault_summary, vault, log_path=self._log_path,
        )
        candidate_slugs = self._collect_candidate_slugs(vault, vault_summary)
        top_slugs = select_top_slugs(source_content, candidate_slugs)

        system_prompt = build_system_prompt(schema_md, vault_summary)
        user_prompt = build_user_prompt(source_label, source_content, top_slugs)

        request = BrainRequest(
            messages=(BrainMessage(role="user", content=user_prompt),),
            system=system_prompt,
            max_tokens=self._cfg.max_output_tokens,
            temperature=0.4,                    # factual editor, not creative
            stream=True,
        )

        start_ns = time.time_ns()
        from jarvis.memory.wiki.provider_chain import (
            build_wiki_provider_chain,
            complete_with_fallback,
            credential_ready_wiki_providers,
        )

        # Key-aware fallback (AP-22/23): cross to a reachable family instead of
        # dying on one dead / throttled provider (live 2026-06-30 silent brick).
        available = set(self._registry.available())
        chain = build_wiki_provider_chain(
            primary=(self._cfg.provider.strip() or self._config.brain.primary),
            model_override=self._cfg.model,
            available=available,
            credential_ready=(
                credential_ready_wiki_providers(
                    available=available,
                    config=self._config,
                )
                if self._credential_filter
                else available
            ),
        )
        rejection_reasons: list[str] = []

        def _validate_response(agg: Any) -> str | None:
            if is_length_truncated(agg.finish_reason, agg.text):
                reason = (
                    f"truncated structured output ({len(agg.text or '')} chars, "
                    f"finish_reason={agg.finish_reason!r})"
                )
                rejection_reasons.append(reason)
                return reason
            try:
                _parse_updates(agg.text)
            except (ValueError, json.JSONDecodeError) as exc:
                reason = f"malformed update JSON: {exc}"
                rejection_reasons.append(reason)
                return reason
            return None

        result = await complete_with_fallback(
            registry=self._registry,
            chain=chain,
            request=request,
            timeout_s=self._cfg.timeout_s,
            label="WikiCuratorLLM",
            aggregate=aggregate,
            validate=_validate_response,
        )
        if result is None:
            if any(reason.startswith("truncated") for reason in rejection_reasons):
                logger.warning(
                    "WikiCuratorLLM: every provider hit the output-token cap "
                    "or failed after a truncated response; no updates were persisted"
                )
                telemetry.inc("wiki_writes_blocked_truncated")
            return []
        agg, self._resolved_provider = result

        if is_length_truncated(agg.finish_reason, agg.text):
            duration_ms = (time.time_ns() - start_ns) // 1_000_000
            logger.warning(
                "WikiCuratorLLM: response hit the output-token cap "
                "(finish_reason=%r, %d chars, %dms, provider=%s) — discarding "
                "truncated updates rather than persisting a half-written page",
                agg.finish_reason, len(agg.text), duration_ms, self._resolved_provider,
            )
            telemetry.inc("wiki_writes_blocked_truncated")
            return []

        try:
            updates = _parse_updates(agg.text)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                "WikiCuratorLLM: malformed JSON from %s (%s) — returning empty list",
                self._resolved_provider, exc,
            )
            return []

        duration_ms = (time.time_ns() - start_ns) // 1_000_000
        logger.info(
            "WikiCuratorLLM produced %d update(s) in %dms (provider=%s, source=%r)",
            len(updates), duration_ms, self._resolved_provider, source_label[:60],
        )
        return updates

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _ensure_brain(self) -> Brain | None:
        """Instantiate the configured Brain once, cache the result.

        Returns ``None`` when the provider can't be created — the caller
        treats that as "skip ingest, log warning".
        """

        async with self._lock:
            if self._brain is not None:
                return self._brain

            try:
                provider, model = _resolve_provider_and_model(self._cfg, self._config)
            except Exception as exc:                              # noqa: BLE001
                logger.warning(
                    "WikiCuratorLLM: provider resolution failed: %s", exc,
                )
                return None

            self._resolved_provider = provider
            self._resolved_model = model

            try:
                # Thinking disabled for the curator tier (Gemini non-pro):
                # background JSON work must not burn the token budget on
                # internal reasoning (see instantiate_curator_brain).
                brain = await asyncio.to_thread(
                    instantiate_curator_brain, self._registry, provider, model,
                )
            except Exception as exc:                              # noqa: BLE001
                logger.warning(
                    "WikiCuratorLLM: cannot instantiate provider %r (model=%r): %s",
                    provider, model, exc,
                )
                return None

            self._brain = brain
            logger.info(
                "WikiCuratorLLM active (provider=%s model=%s timeout=%.1fs)",
                provider, model or "<provider-default>", self._cfg.timeout_s,
            )
            return brain

    async def _load_schema(self) -> str | None:
        """Read ``schema.md`` from disk on a worker thread."""

        path = self._schema_path

        def _read() -> str | None:
            try:
                return path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return None

        return await asyncio.to_thread(_read)

    @staticmethod
    def _collect_candidate_slugs(
        vault: VaultIndex, vault_summary: dict[str, Any],
    ) -> list[str]:
        """All known slugs across the four page types.

        Falls back to the ``latest`` preview inside ``vault_summary``
        when the vault implementation does not expose ``pages_by_type``
        (e.g. minimalist test fakes).
        """

        slugs: list[str] = []
        for ptype in ("entity", "concept", "project", "session"):
            try:
                pages = vault.pages_by_type(ptype) or []
            except Exception:                                     # noqa: BLE001
                pages = []
            for page in pages:
                slug = getattr(page, "slug", "")
                if slug:
                    slugs.append(slug)

        if slugs:
            return slugs

        # Fallback: read from the summary preview block.
        latest: dict[str, list[str]] = vault_summary.get("latest", {})
        for ptype_slugs in latest.values():
            slugs.extend(ptype_slugs)
        return slugs


__all__ = [
    "WikiCuratorLLM",
]
