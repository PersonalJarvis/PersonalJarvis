"""Per-model Ollama profiles: a derived model carries the knobs ``/v1`` cannot.

Ollama's OpenAI-compatible ``/v1`` endpoint ignores request ``options``,
``keep_alive`` and ``think`` (the main brain talks to it through
``jarvis/plugins/brain/ollama.py``). Rather than rewriting the streamer against
the native ``/api/chat``, the per-model knobs travel three ways:

* **Bakeable** knobs (:data:`BAKEABLE_KEYS`) are baked into a derived model
  via ``POST /api/create {"model": alias, "from": base, "parameters": ...}`` —
  the same trick the local-realtime supervisor uses for its ``-voice-8k``
  alias. The alias shares the weights (metadata only, no download), and its
  name carries a hash of the baked set so a changed knob yields a new alias
  and the stale one is deleted. Aliases end in ``-jarvis-<8 hex>`` so the
  inventory can hide them from the user.
* ``keep_alive`` rides a warm ping (``POST /api/generate`` with an empty
  prompt) once per process per (model, keep_alive).
* ``temperature``, ``num_predict`` and ``think`` ride the ``/v1`` request
  itself (:func:`to_v1_kwargs`); ``think`` maps to ``reasoning_effort``, which
  Ollama 0.32.15 honours (checked live 2026-08-24: ``"none"`` answered in 3
  completion tokens with no reasoning field, ``"high"`` streamed reasoning).

Pure HTTP against the server root — identical on every OS and on a remote
host (docs/os-parity.md P-32). Nothing here runs at import time (AP-26).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

import httpx

from jarvis.core.config import OllamaModelOptions

log = logging.getLogger(__name__)

#: Option keys that are baked into the derived model's ``parameters``.
#: ``num_predict``, ``temperature``, ``keep_alive`` and ``think`` are NOT
#: here on purpose: they have a per-request channel, and baking them would
#: force a new alias (and a stale-alias delete) for a knob that costs nothing
#: to send.
BAKEABLE_KEYS: tuple[str, ...] = (
    "num_ctx",
    "num_gpu",
    "num_thread",
    "top_p",
    "top_k",
    "min_p",
    "repeat_penalty",
    "seed",
    "stop",
)

#: Marker every profile alias carries; the inventory filters on it.
PROFILE_MARKER = "-jarvis-"
_PROFILE_ALIAS_RE = re.compile(r"^(?P<prefix>.+)-jarvis-(?P<hash>[0-9a-f]{8})$")

#: Context ladder :func:`suggest_options` picks from (tokens).
_CONTEXT_LADDER: tuple[int, ...] = (4096, 8192, 16384, 32768, 65536, 131072)
#: Rough KV-cache cost in GB per 1k tokens, scaled by the model's on-disk size
#: (a 4 GB model ~0.12 GB/1k, an 18 GB model ~0.54 GB/1k). An estimate: the
#: real figure depends on the architecture and the KV cache type, which is why
#: every suggestion stays advisory.
_KV_GB_PER_1K_PER_MODEL_GB = 0.03
#: Fixed runtime overhead beside the weights (compute buffers, the runner).
_OVERHEAD_GB = 1.0
#: Share of system RAM a model may plan with when no accelerator is known —
#: the same rule ``ollama_pull.fit_verdict`` applies.
_RAM_SHARE = 0.6

# Per-process memo so a turn never repeats an HTTP round-trip it has already
# won: (root, alias) once ensured, (root, model, keep_alive) once warmed.
_ensured: set[tuple[str, str]] = set()
_warmed: set[tuple[str, str, str]] = set()

# Creating a derived model or loading weights for the warm ping can take a
# while on a CPU box; connecting to a dead server must still fail fast.
_TIMEOUT = httpx.Timeout(connect=2.0, read=180.0, write=30.0, pool=30.0)


def _fold(base: str) -> str:
    """``qwen3.5:9b`` -> ``qwen3.5-9b``; ``hf.co/u/r:Q4`` -> ``hf.co-u-r-Q4``."""
    return base.strip().replace(":", "-").replace("/", "-")


def baked_parameters(opts: OllamaModelOptions) -> dict[str, Any]:
    """The subset of ``opts`` that is baked into the alias, canonical order."""
    params: dict[str, Any] = {}
    for key in BAKEABLE_KEYS:
        value = getattr(opts, key, None)
        if value is not None:
            params[key] = value
    return params


def has_bakeable(opts: OllamaModelOptions | None) -> bool:
    """Whether ``opts`` needs a derived model at all."""
    return bool(opts is not None and baked_parameters(opts))


def profile_name(base: str, opts: OllamaModelOptions) -> str:
    """``<folded base>-jarvis-<8-char sha256 of the canonical baked dict>``.

    Stable for equal option sets regardless of key order or the presence of
    non-bakeable knobs, so the same profile is never created twice.
    """
    canonical = json.dumps(baked_parameters(opts), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
    return f"{_fold(base)}{PROFILE_MARKER}{digest}"


def is_profile_alias(name: str) -> bool:
    """Whether ``name`` is one of our derived models (hidden from inventories)."""
    return bool(_PROFILE_ALIAS_RE.match((name or "").strip().removesuffix(":latest")))


def _stale_aliases(names: list[str], base: str, keep: str) -> list[str]:
    """Earlier ``<base>-jarvis-*`` aliases that are not ``keep``."""
    prefix = _fold(base)
    stale: list[str] = []
    for raw in names:
        name = raw.strip().removesuffix(":latest")
        match = _PROFILE_ALIAS_RE.match(name)
        if match and match.group("prefix") == prefix and name != keep:
            stale.append(name)
    return stale


async def _tag_names(client: httpx.AsyncClient, root: str) -> list[str]:
    resp = await client.get(f"{root}/api/tags")
    resp.raise_for_status()
    return [str(m.get("name") or "") for m in resp.json().get("models") or []]


async def ensure_profile(
    root: str,
    base: str,
    opts: OllamaModelOptions,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Return the alias that carries ``opts`` for ``base``, creating it once.

    Idempotent against ``/api/tags``: an alias that already exists is reused
    without a create call; a base with NO bakeable knob returns ``base``
    itself. When the hash changed, every earlier ``<base>-jarvis-*`` alias is
    deleted (a failed delete is logged and does not fail the turn — the alias
    is a ghost in the list, not a wrong answer). Raises ``RuntimeError`` with
    an English sentence when the create itself fails; the caller then runs the
    base model and says so.

    ``transport`` exists for tests (``httpx.MockTransport``); production
    passes ``None``.
    """
    if not has_bakeable(opts):
        return base
    alias = profile_name(base, opts)
    key = (root, alias)
    if key in _ensured:
        return alias
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        try:
            names = await _tag_names(client, root)
        except Exception as exc:
            raise RuntimeError(
                f"Ollama at {root} did not answer /api/tags while preparing the "
                f"profile for {base}: {type(exc).__name__}"
            ) from exc
        present = {n.removesuffix(":latest") for n in names}
        if alias not in present:
            payload = {
                "model": alias,
                "from": base,
                "parameters": baked_parameters(opts),
                "stream": False,
            }
            try:
                resp = await client.post(f"{root}/api/create", json=payload)
                resp.raise_for_status()
            except Exception as exc:
                raise RuntimeError(
                    f"could not create the Ollama profile {alias} from {base}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            log.info(
                "ollama profile: created %s from %s with %s",
                alias,
                base,
                json.dumps(baked_parameters(opts), sort_keys=True),
            )
        for stale in _stale_aliases(names, base, alias):
            try:
                resp = await client.request("DELETE", f"{root}/api/delete", json={"model": stale})
                resp.raise_for_status()
                log.info("ollama profile: deleted stale alias %s", stale)
            except Exception as exc:  # noqa: BLE001 — a leftover alias is cosmetic
                log.warning(
                    "ollama profile: could not delete stale alias %s (%s: %s)",
                    stale,
                    type(exc).__name__,
                    exc,
                )
    _ensured.add(key)
    return alias


async def warm(
    root: str,
    model: str,
    keep_alive: str | int,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """Load ``model`` with ``keep_alive`` via an empty ``/api/generate`` ping.

    Once per process per (root, model, keep_alive); ``keep_alive`` is what the
    user pinned (Go duration, seconds, ``-1`` forever, ``0`` unload now).
    Returns ``False`` after logging when the server did not take it — the
    turn still runs, only the residency promise is not kept. Never raises.
    """
    key = (root, model, str(keep_alive))
    if key in _warmed:
        return True
    payload = {"model": model, "keep_alive": keep_alive, "stream": False}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
            resp = await client.post(f"{root}/api/generate", json=payload)
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — the warm ping is best-effort
        log.warning(
            "ollama profile: warm ping for %s (keep_alive=%s) failed: %s: %s",
            model,
            keep_alive,
            type(exc).__name__,
            exc,
        )
        return False
    _warmed.add(key)
    log.info("ollama profile: %s warmed (keep_alive=%s)", model, keep_alive)
    return True


def reset_process_memo() -> None:
    """Forget what was ensured/warmed (tests, or after a server restart)."""
    _ensured.clear()
    _warmed.clear()


def to_v1_kwargs(opts: OllamaModelOptions | None) -> dict[str, Any]:
    """The knobs the OpenAI-compatible request honours directly.

    ``temperature`` as is; ``num_predict`` > 0 as ``max_tokens`` (``-1`` /
    ``-2`` are Ollama's "unlimited" / "fill the context" sentinels, which
    ``/v1`` has no spelling for, so they are left to the server default);
    ``think`` as ``reasoning_effort`` — ``False`` -> ``"none"``, a level ->
    that level, ``True`` -> nothing (the model's own default is to think).
    """
    if opts is None:
        return {}
    kwargs: dict[str, Any] = {}
    if opts.temperature is not None:
        kwargs["temperature"] = opts.temperature
    if opts.num_predict is not None and opts.num_predict > 0:
        kwargs["max_tokens"] = opts.num_predict
    if opts.think is False:
        kwargs["reasoning_effort"] = "none"
    elif isinstance(opts.think, str):
        kwargs["reasoning_effort"] = opts.think
    return kwargs


def largest_context_for(
    *, size_gb: float, native_context: int | None, budget_gb: float | None
) -> int:
    """The largest ladder rung whose weights + KV cache fit ``budget_gb``.

    ``native_context`` caps the ladder at the model's own window; ``None``
    budget (memory unreadable) returns the smallest rung. Shared by the
    per-model profile suggester and the managed voice brain so one machine
    never gets two different answers to "how much context fits here?".
    """
    kv_per_1k = max(size_gb, 0.5) * _KV_GB_PER_1K_PER_MODEL_GB
    ladder = [
        rung for rung in _CONTEXT_LADDER if native_context is None or rung <= native_context
    ] or [_CONTEXT_LADDER[0]]
    chosen = ladder[0]
    if budget_gb is not None:
        for rung in ladder:
            needed = size_gb + _OVERHEAD_GB + kv_per_1k * (rung / 1000)
            if needed <= budget_gb:
                chosen = rung
    return chosen


def suggest_options(
    *,
    size_gb: float,
    native_context: int | None,
    accelerator_gb: float,
    source: str,
    ram_gb: float | None,
) -> tuple[OllamaModelOptions, list[str]]:
    """Advisory ``(options, reasons)`` for a model of ``size_gb`` on this box.

    ``accelerator_gb``/``source`` come from ``ollama_pull.accelerator_gb()``
    (``(0.0, "none")`` = no accelerator this probe can vouch for) and
    ``ram_gb`` from ``ollama_pull.total_memory_gb()`` (``None`` = unreadable).
    One plain sentence per knob explains the pick. The estimate never
    promises a fit: the KV-cache rule is a rough per-size average, so the UI
    labels it "suggested" and Reset is one click.
    """
    reasons: list[str] = []
    opts = OllamaModelOptions()

    if accelerator_gb > 0:
        budget = accelerator_gb
        where = (
            "unified memory shared with the graphics cores"
            if source == "apple-unified"
            else "graphics memory"
        )
        budget_sentence = f"Planned against the {accelerator_gb:.0f} GB of {where} on this machine."
    elif ram_gb:
        budget = ram_gb * _RAM_SHARE
        budget_sentence = (
            "No accelerator this probe can vouch for, so the RAM rule applies: "
            f"about {_RAM_SHARE:.0%} of the {ram_gb:g} GB installed."
        )
    else:
        budget = None
        budget_sentence = (
            "This machine's memory could not be read, so only the conservative "
            "defaults are suggested."
        )
    reasons.append(budget_sentence)

    # Context: the largest rung whose weights + KV cache fit the budget.
    kv_per_1k = max(size_gb, 0.5) * _KV_GB_PER_1K_PER_MODEL_GB
    chosen = largest_context_for(size_gb=size_gb, native_context=native_context, budget_gb=budget)
    if budget is not None:
        estimate = size_gb + _OVERHEAD_GB + kv_per_1k * (chosen / 1000)
        reasons.append(
            f"num_ctx {chosen:,}: weights plus a {chosen // 1024}k context need "
            f"roughly {estimate:.1f} GB, the largest step that stays inside the budget"
            + (f" (the model's native window is {native_context:,})." if native_context else ".")
        )
    else:
        reasons.append(
            f"num_ctx {chosen:,}: the smallest step, because the memory budget is unknown."
        )
    opts.num_ctx = chosen

    # Placement.
    if accelerator_gb > 0:
        if size_gb + _OVERHEAD_GB <= accelerator_gb:
            opts.num_gpu = -1
            reasons.append(
                "num_gpu -1: every layer fits on the accelerator, so it runs at full speed."
            )
        else:
            reasons.append(
                "num_gpu left to Ollama: the weights are bigger than the accelerator, "
                "so it splits layers between the card and the CPU on its own."
            )
    else:
        reasons.append(
            "num_gpu left to Ollama: with no accelerator this probe can vouch for, "
            "Ollama's own detection decides the placement."
        )

    # Residency.
    if accelerator_gb > 0 and opts.num_gpu == -1:
        opts.keep_alive = "30m"
        reasons.append(
            "keep_alive 30m: the model stays loaded on the card between turns, so "
            "the next answer starts without a reload."
        )
    else:
        opts.keep_alive = "10m"
        reasons.append(
            "keep_alive 10m: long enough for a conversation, short enough to give "
            "the memory back when you stop talking."
        )
    return opts, reasons
