"""UltraWiki distillation — turn one raw item into a normalized memory document.

The "distill before you embed" principle (design doc 01): what gets embedded is
not the raw fragmentary text but an LLM-normalized document — a searchable
question, a summary, the resolution, mentioned entities, and references. The
prompt uses XML-tagged sections (per the Anthropic prompting guidance cited in
the Cerebras write-up) and demands strict minified JSON output.

Provider resolution is key-aware and cross-family (AP-22), reusing the wiki
provider chain directly: when ``cfg.ultrawiki.distill_provider`` is set, that
provider (with ``distill_model`` or its cheap router-tier default) leads the
chain; every other credential-ready provider follows as fallback. On total
chain failure :class:`DistillError` is raised — the staged pipeline owns
retries and dead-lettering.

Caching contract (design doc 02, determinism economics): results are cached on
``(content_hash, PROMPT_VERSION, model)``. Bump :data:`PROMPT_VERSION` on ANY
prompt change so improved prompts re-enrich incrementally instead of serving
stale cached distillations.

**Version 2 — episodic events.** The document now also carries an ``events``
array (design doc 01, ``uw_events``). It rides the SAME call: extracting
events costs no extra round trip, no extra model, and nothing on the read
path. Two rules make that array safe to consume:

- ``when`` is either ISO-8601 (whenever the source states a date outright) or
  ONE token from a closed English relative vocabulary
  (:data:`jarvis.ultrawiki.events.RELATIVE_VOCABULARY`). The model therefore
  does the LANGUAGE normalization — a German or Spanish source arrives already
  translated — while ``jarvis/ultrawiki/events.py`` does the arithmetic that
  turns the token into an absolute instant. No per-language phrase table, so
  the feature works for every locale rather than the two somebody tested.
- The model never computes a date. Asking an LLM to add five days to a
  timestamp is asking for a wrong answer that looks right; the resolver
  anchors every relative expression against the item's own timestamp.

**What the bump does and does not do.** It changes the cache key, so every
distillation from here on is a v2 one and no v1 cache row can be served for
it. It does NOT re-distill an existing corpus: the pipeline's distillation
stage claims only items that are not distilled yet, and re-running a whole
corpus through a model is a cost no version bump gets to decide on the
owner's behalf.

That is affordable because events do not actually need a v2 distillation. An
already-distilled corpus gets them from the text it already stores, wherever
that states an absolute date (``events.derive_events``'s legacy path), driven
by the pipeline's deterministic backfill lane
(``pipeline.PipelineWorker._events_backfill_pass``) — no model call, no
network, no re-embedding. A v2 distillation is simply richer when it arrives:
the model states the kind, the place, the participants and a relative date
this module could never recover from prose.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from jarvis.brain.streaming import aggregate
from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.memory.wiki.provider_chain import (
    build_wiki_provider_chain,
    complete_with_fallback,
    credential_ready_wiki_providers,
)
from jarvis.ultrawiki.types import content_hash_for

log = logging.getLogger(__name__)

__all__ = [
    "PROMPT_VERSION",
    "DistillError",
    "DistillResult",
    "build_distill_prompt",
    "distill_cache_key",
    "distill_text",
    "extract_json_object",
]

#: Part of the distillation cache key — bump on EVERY prompt change.
#: 1 = question/summary/resolution/entities/refs.
#: 2 = + the ``events`` array (episodic facts, same call).
PROMPT_VERSION = 2

#: Raw bodies are truncated before prompting; a distillation summarizes, it
#: does not need six-figure transcripts, and the marker keeps the cut honest.
_BODY_TRUNCATE_CHARS = 8000
_TRUNCATION_MARKER = "\n[... input truncated for distillation ...]"

_DEFAULT_TIMEOUT_S = 90.0

_SYSTEM_PROMPT = (
    "You are a precise archival distiller inside a personal memory system. "
    "You turn one raw captured item into a normalized, searchable memory "
    "document. You answer with STRICT minified JSON only - no prose, no "
    "markdown fences, no keys beyond the requested ones. You never invent "
    "facts that are not in the source item."
)


class DistillError(RuntimeError):
    """The whole provider chain failed to produce parseable distillation JSON.

    Retryable: the pipeline keeps the item's last good state and retries later.
    """


@dataclass(slots=True)
class DistillResult:
    """Salvaged distillation output. Keys the model omitted arrive empty —
    a partially-filled document is still worth embedding."""

    question: str = ""
    summary: str = ""
    resolution: str = ""
    entities: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    #: Episodic facts (prompt version 2). Raw payload dicts, deliberately NOT
    #: parsed here: turning ``when`` into an absolute instant needs the item's
    #: own timestamp, which the distiller does not have and must not guess.
    #: ``jarvis.ultrawiki.events.derive_events`` owns that step.
    events: list[dict[str, Any]] = field(default_factory=list)
    raw_json: str = ""  # the JSON exactly as the provider produced it


def _truncate_body(body: str) -> str:
    if len(body) <= _BODY_TRUNCATE_CHARS:
        return body
    return body[:_BODY_TRUNCATE_CHARS] + _TRUNCATION_MARKER


#: The closed relative vocabulary the prompt is allowed to emit, spelled out
#: for the model. It MUST stay in sync with
#: ``jarvis.ultrawiki.events.RELATIVE_VOCABULARY`` — a token invented here
#: resolves to nothing and the event silently falls back to the item's own
#: timestamp. ``tests/unit/ultrawiki/test_events.py`` pins the pair together.
_RELATIVE_TOKENS = (
    "today|yesterday|tomorrow|this <weekday>|last <weekday>|next <weekday>|"
    "last week|next week|last month|next month|last year|next year|"
    "<n> days ago|<n> weeks ago|<n> months ago|<n> years ago|"
    "in <n> days|in <n> weeks|in <n> months|in <n> years"
)


def build_distill_prompt(*, title: str, body: str, source_kind: str) -> str:
    """The normalized user prompt: XML-tagged sections, strict-JSON contract."""
    return (
        "<task>\n"
        "Distill the source item below into one normalized memory document\n"
        "for semantic search over a personal knowledge base.\n"
        "</task>\n"
        f'<source kind="{source_kind}">\n'
        f"<title>{title}</title>\n"
        f"<body>\n{_truncate_body(body)}\n</body>\n"
        "</source>\n"
        "<output_format>\n"
        "Return STRICT minified JSON on a single line with exactly these keys:\n"
        '{"question":"the question this item answers, phrased as a user would '
        'ask it","summary":"a 2-4 sentence factual summary","resolution":"the '
        'outcome, decision, or answer; empty string if none","entities":'
        '["mentioned people, places, organizations, projects, systems"],'
        '"refs":["explicit references to other documents, URLs, or ids"],'
        '"events":[{"kind":"meal|travel|meeting|purchase|milestone|other",'
        '"title":"short name of what happened","when":"see the time rules",'
        '"when_end":"same format, empty unless the item states an end",'
        '"where":"place name, empty if none","participants":["people who were '
        'there"],"confidence":0.0}]}\n'
        "Rules: no markdown fences; no additional keys; entities, refs and\n"
        "participants are arrays of short strings; use empty values where the\n"
        "item provides nothing; never invent facts.\n"
        "</output_format>\n"
        "<event_rules>\n"
        "An event is something that HAPPENED or is scheduled to happen at a\n"
        "point in time: a meal, a trip, a meeting, a purchase, a milestone.\n"
        'Return "events":[] when the item records none — most items record\n'
        "none, and an invented event poisons the memory permanently.\n"
        "Time format for when / when_end, in this order of preference:\n"
        "1. The item states an absolute date or time: return ISO-8601\n"
        '   ("2026-03-14", "2026-03-14T19:30", "2026-03" for a whole month,\n'
        '   "2026" for a whole year). Never shift or reformat the date.\n'
        "2. The item uses a relative expression, in ANY language: translate it\n"
        "   to EXACTLY ONE of these English tokens, optionally followed by\n"
        f'   " at HH:MM" or a daypart word:\n   {_RELATIVE_TOKENS}\n'
        "3. Neither applies: return an empty string.\n"
        "NEVER compute a date yourself and never guess a year: the system\n"
        "resolves relative tokens against the item's own timestamp.\n"
        "confidence is 0.0-1.0: how sure you are the event really happened as\n"
        "described.\n"
        "</event_rules>"
    )


def distill_cache_key(*, title: str, body: str, model: str) -> tuple[str, int, str]:
    """``(content_hash, prompt_version, model)`` — the ``uw_distill_cache`` key.

    The hash covers the TRUNCATED body (what is actually distilled), so two
    inputs differing only beyond the truncation point share one cache row.
    """
    return content_hash_for(title, _truncate_body(body)), PROMPT_VERSION, model


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json_object(text: str) -> tuple[str, dict[str, Any]] | None:
    """Best-effort extraction of the first JSON object from model output.

    Returns ``(raw_json_text, parsed_dict)`` or ``None``. Tolerates markdown
    fences and surrounding prose — providers wrap JSON despite instructions.
    Shared with the LLM reranker, which faces the identical strict-JSON
    contract against the identical provider chain.
    """
    candidates = [text.strip()]
    for match in _JSON_FENCE_RE.finditer(text):
        candidates.append(match.group(1).strip())
    for candidate in list(candidates):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidates.append(candidate[start : end + 1])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return candidate, parsed
    return None


def _string_list(value: Any) -> list[str]:
    """Salvage a JSON value into ``list[str]`` — scalars wrap, junk drops."""
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for entry in value:
            text = entry if isinstance(entry, str) else str(entry)
            text = text.strip()
            if text:
                out.append(text)
        return out
    return []


def _event_list(value: Any) -> list[dict[str, Any]]:
    """Salvage the ``events`` array into ``list[dict]``; junk entries drop.

    Deliberately structure-only: no field is validated or resolved here.
    ``events.derive_events`` is the single place that decides what a payload
    means, so a provider quirk cannot produce two different interpretations
    depending on which layer looked at it first.
    """
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(entry) for entry in value if isinstance(entry, dict)]


def _result_from(raw_json: str, parsed: dict[str, Any]) -> DistillResult:
    """Salvage whatever keys arrived; missing keys become empty values."""
    return DistillResult(
        question=str(parsed.get("question") or "").strip(),
        summary=str(parsed.get("summary") or "").strip(),
        resolution=str(parsed.get("resolution") or "").strip(),
        entities=_string_list(parsed.get("entities")),
        refs=_string_list(parsed.get("refs")),
        events=_event_list(parsed.get("events")),
        raw_json=raw_json,
    )


def _validate_distill_response(agg: Any) -> str | None:
    """Chain validator: a transport success without parseable JSON is unusable
    output — the chain then tries the next provider family."""
    text = getattr(agg, "text", "") or ""
    if extract_json_object(text) is None:
        return "distillation output holds no parseable JSON object"
    return None


async def distill_text(
    cfg: Any,
    *,
    title: str,
    body: str,
    source_kind: str,
    registry: Any = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> DistillResult:
    """Distill one raw item through the key-aware cross-family provider chain.

    ``registry`` (a ``BrainProviderRegistry``) is injectable for tests; the
    default builds the entry-point registry lazily.

    Raises :class:`DistillError` when the whole chain fails; the caller (the
    staged pipeline) schedules the retry.
    """
    if registry is None:
        from jarvis.brain.provider_registry import BrainProviderRegistry

        registry = BrainProviderRegistry()

    ultrawiki = getattr(cfg, "ultrawiki", None)
    configured_provider = str(getattr(ultrawiki, "distill_provider", "") or "").strip()
    configured_model = str(getattr(ultrawiki, "distill_model", "") or "").strip()

    # The Codex subscription and OpenAI API are intentionally separate cards.
    # The shared Codex brain normally prefers a stored API key, so the
    # subscription auth capability must ride into this explicitly selected
    # instance or the UI would claim one billing path while using another.
    provider_options: dict[str, dict[str, Any]] = {}
    if configured_provider:
        from jarvis.ultrawiki.provider_catalog import get_provider_spec

        selected_spec = get_provider_spec("distill", configured_provider)
        if selected_spec is not None and selected_spec.auth_mode == "codex":
            provider_options[configured_provider] = {"prefer_subscription": True}

    available = set(registry.available())
    credential_ready = credential_ready_wiki_providers(available=available, config=cfg)

    # An explicit distill provider leads the chain with its explicit model (or
    # its cheap router-tier default); otherwise the brain primary leads. Every
    # other credential-ready provider family follows either way (AP-22).
    primary = configured_provider or str(getattr(cfg.brain, "primary", "") or "")
    chain = build_wiki_provider_chain(
        primary=primary,
        model_override=configured_model if configured_provider else "",
        available=available,
        credential_ready=credential_ready,
    )

    request = BrainRequest(
        messages=(
            BrainMessage(
                role="user",
                content=build_distill_prompt(
                    title=title, body=body, source_kind=source_kind
                ),
            ),
        ),
        system=_SYSTEM_PROMPT,
        temperature=0.1,  # normalization, not creativity
        # Raised with prompt version 2: the events array shares this budget,
        # and a truncated JSON object is an unparseable one — the whole
        # distillation would be retried for the sake of a few tokens.
        max_tokens=2000,
        stream=True,
    )

    result = await complete_with_fallback(
        registry=registry,
        chain=chain,
        request=request,
        timeout_s=timeout_s,
        label="UltraWikiDistiller",
        aggregate=aggregate,
        validate=_validate_distill_response,
        provider_options=provider_options,
    )
    if result is None:
        raise DistillError(
            "distillation failed: no configured provider returned parseable "
            "JSON - the item keeps its state and will be retried later"
        )

    agg, provider = result
    extracted = extract_json_object(getattr(agg, "text", "") or "")
    if extracted is None:  # defensive: validate() already guaranteed this
        raise DistillError(
            f"distillation failed: provider {provider} passed validation but "
            "no JSON object could be extracted"
        )
    raw_json, parsed = extracted
    log.debug("distilled item via provider %s (%d chars of JSON)", provider, len(raw_json))
    return _result_from(raw_json, parsed)
