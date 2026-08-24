"""What the local Ollama server holds, loaded, and can let go of.

The provider card only ever asked ``/api/tags`` — a list of names. That is
enough to pick a model and not enough to manage them: nothing said how big a
download was, what it can do (vision? tools? embeddings?), how much context it
natively carries, whether it is sitting in graphics memory right now, or how
to free that memory without restarting the server. This module is the native
Ollama surface behind the "Local models" section:

* :func:`list_models` — ``/api/tags`` joined with ``/api/show`` per download
  (bounded concurrency, fail-open per model so one broken manifest never
  blanks the table).
* :func:`running_models` — ``/api/ps``: what is loaded, how much of it sits
  in graphics memory, when it expires.
* :func:`unload_model` — a ``keep_alive: 0`` ping, Ollama's own "unload now".
* :func:`delete_model` — ``DELETE /api/delete``.
* :func:`disk_usage` — the sum of the user-visible downloads.

Two families of download are Jarvis's own bookkeeping, not the user's
choice, and are hidden from every user-facing list by :func:`is_hidden_alias`:
the per-model option profiles (``<base>-jarvis-<8 hex>``, created via
``/api/create`` so ``num_ctx`` & co. can ride the OpenAI-compatible ``/v1``
path) and the voice brain's ``-voice-8k`` alias. Aliases share their weights
with the base model, so hiding them also keeps :func:`disk_usage` honest.

Every function takes the server ``root`` explicitly (see
:func:`jarvis.brain.ollama_pull.server_root` for the configured one) and an
optional ``transport`` so tests drive it through a fake server rather than
the network. HTTP only — identical on every OS, including a headless box.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from jarvis.plugins.brain.ollama import CLIENT_TIMEOUT, normalize_server_root

log = logging.getLogger(__name__)

__all__ = [
    "OllamaModelInfo",
    "OllamaRunningModel",
    "OllamaServerError",
    "OllamaModelNotFound",
    "PROFILE_ALIAS_SUFFIX_RE",
    "VOICE_ALIAS_SUFFIX",
    "is_hidden_alias",
    "native_context_length",
    "same_model",
    "list_models",
    "get_model",
    "running_models",
    "unload_model",
    "delete_model",
    "disk_usage",
]

#: Per-model option profiles are derived models named ``<base>-jarvis-<hash>``
#: (``jarvis.brain.ollama_profiles``); the hash is the first 8 hex characters
#: of the baked option dict.
PROFILE_ALIAS_SUFFIX_RE = re.compile(r"-jarvis-[0-9a-f]{8}$")

#: The managed voice brain's fixed-context alias (``supervisor.py``).
VOICE_ALIAS_SUFFIX = "-voice-8k"

#: ``/api/show`` is one round-trip per download; eight in flight keeps a
#: 40-model host under a second on localhost without hammering a LAN box.
_SHOW_CONCURRENCY = 8


class OllamaServerError(RuntimeError):
    """The server did not answer usefully. ``str(exc)`` is an English sentence
    the UI can show verbatim."""


class OllamaModelNotFound(OllamaServerError):
    """The named download does not exist on the server (HTTP 404)."""


@dataclass(frozen=True, slots=True)
class OllamaModelInfo:
    """One installed download with the facts ``/api/tags`` + ``/api/show`` give.

    ``context_length`` is the model's NATIVE window from ``model_info``
    (``<arch>.context_length``), not what the server currently runs it with;
    ``None`` when the manifest does not say. ``capabilities`` is the declared
    list (``completion``, ``vision``, ``tools``, ``thinking``, ``embedding``,
    ``audio`` ...), empty when the probe failed — callers treat empty as
    "unknown", never as "can do nothing".
    """

    name: str
    size_bytes: int
    digest: str
    modified_at: str
    family: str
    parameter_size: str
    quantization_level: str
    context_length: int | None
    capabilities: tuple[str, ...]
    license: str
    #: Free-text extras from ``/api/show`` for the details view.
    parameters: str = ""
    template: str = ""
    #: True when ``/api/show`` answered — False means only ``/api/tags`` facts.
    probed: bool = True


@dataclass(frozen=True, slots=True)
class OllamaRunningModel:
    """One entry of ``/api/ps``: a model currently held in memory."""

    name: str
    size_bytes: int
    size_vram_bytes: int
    expires_at: str
    context_length: int | None
    digest: str = ""


# ── Names ────────────────────────────────────────────────────────────────


def _strip_latest(name: str) -> str:
    return name[: -len(":latest")] if name.endswith(":latest") else name


def same_model(a: str, b: str) -> bool:
    """``qwen3.5`` and ``qwen3.5:latest`` are one download (Ollama's own rule)."""
    a, b = a.strip(), b.strip()
    if not a or not b:
        return False
    return _strip_latest(a) == _strip_latest(b)


def is_hidden_alias(name: str) -> bool:
    """True for Jarvis's own derived models, which no user list should show.

    Profile aliases fold ``:`` into ``-`` and therefore carry no tag of their
    own, so ``/api/tags`` reports them as ``…-jarvis-ab12cd34:latest``.
    """
    bare = _strip_latest((name or "").strip())
    if not bare:
        return False
    if PROFILE_ALIAS_SUFFIX_RE.search(bare):
        return True
    return bare.endswith(VOICE_ALIAS_SUFFIX)


# ── HTTP ─────────────────────────────────────────────────────────────────


def _make_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """One client per call; tests inject a fake transport here."""
    return httpx.AsyncClient(timeout=CLIENT_TIMEOUT, transport=transport)


def _unreachable(root: str, exc: Exception) -> OllamaServerError:
    log.info("ollama-inventory: %s did not answer (%s)", root, type(exc).__name__)
    return OllamaServerError(
        f"Ollama did not answer at {root}. Start the server (or install it "
        "from ollama.com/download) and try again."
    )


async def _get_json(client: httpx.AsyncClient, root: str, path: str) -> Any:
    try:
        resp = await client.get(f"{root}{path}")
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        raise OllamaServerError(
            f"Ollama answered {exc.response.status_code} for {path} at {root}."
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise _unreachable(root, exc) from exc


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def native_context_length(model_info: Any) -> int | None:
    """``model_info`` keys are ``<architecture>.context_length``; the
    architecture itself sits under ``general.architecture``."""
    if not isinstance(model_info, dict):
        return None
    arch = str(model_info.get("general.architecture") or "").strip()
    candidates = [f"{arch}.context_length"] if arch else []
    candidates += [k for k in model_info if k.endswith(".context_length")]
    for key in candidates:
        value = model_info.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return None


def _details_of(payload: dict[str, Any]) -> dict[str, Any]:
    details = payload.get("details")
    return details if isinstance(details, dict) else {}


def _from_tags_row(row: dict[str, Any]) -> OllamaModelInfo:
    details = _details_of(row)
    return OllamaModelInfo(
        name=str(row.get("name") or row.get("model") or "").strip(),
        size_bytes=_as_int(row.get("size")),
        digest=str(row.get("digest") or ""),
        modified_at=str(row.get("modified_at") or ""),
        family=str(details.get("family") or ""),
        parameter_size=str(details.get("parameter_size") or ""),
        quantization_level=str(details.get("quantization_level") or ""),
        context_length=None,
        capabilities=(),
        license="",
        probed=False,
    )


def _merge_show(info: OllamaModelInfo, show: dict[str, Any]) -> OllamaModelInfo:
    details = _details_of(show)
    caps_raw = show.get("capabilities")
    caps = tuple(str(c) for c in caps_raw) if isinstance(caps_raw, list) else ()
    license_text = show.get("license")
    if isinstance(license_text, list):
        license_text = "\n".join(str(x) for x in license_text)
    return OllamaModelInfo(
        name=info.name,
        size_bytes=info.size_bytes,
        digest=info.digest,
        modified_at=info.modified_at,
        family=str(details.get("family") or info.family),
        parameter_size=str(details.get("parameter_size") or info.parameter_size),
        quantization_level=str(details.get("quantization_level") or info.quantization_level),
        context_length=native_context_length(show.get("model_info")),
        capabilities=caps,
        license=str(license_text or "").strip(),
        parameters=str(show.get("parameters") or ""),
        template=str(show.get("template") or ""),
        probed=True,
    )


async def _show(
    client: httpx.AsyncClient, root: str, info: OllamaModelInfo, semaphore: asyncio.Semaphore
) -> OllamaModelInfo:
    async with semaphore:
        try:
            resp = await client.post(f"{root}/api/show", json={"model": info.name})
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 — fail-open per model, the row stays
            log.debug("ollama-inventory: /api/show failed for %s: %s", info.name, exc)
            return info
        if not isinstance(payload, dict):
            log.debug("ollama-inventory: /api/show for %s is not an object", info.name)
            return info
        return _merge_show(info, payload)


# ── Public API ───────────────────────────────────────────────────────────


async def list_models(
    root: str,
    *,
    include_hidden: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[OllamaModelInfo]:
    """Every download with its facts, newest first.

    ``:cloud`` references and remote entries are dropped for the same reason
    the brain drops them: they are ollama.com-proxied, not local weights.
    Jarvis's own aliases are hidden unless ``include_hidden`` is set (the
    profile code needs them to find stale ones). Raises
    :class:`OllamaServerError` when ``/api/tags`` does not answer; a failing
    ``/api/show`` only leaves that row without capabilities.
    """
    root = normalize_server_root(root)
    async with _make_client(transport) as client:
        payload = await _get_json(client, root, "/api/tags")
        rows = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise OllamaServerError(f"Ollama at {root} answered /api/tags in an unexpected shape.")
        base: list[OllamaModelInfo] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            info = _from_tags_row(row)
            if not info.name or info.name.endswith(":cloud") or row.get("remote"):
                continue
            if not include_hidden and is_hidden_alias(info.name):
                continue
            base.append(info)
        semaphore = asyncio.Semaphore(_SHOW_CONCURRENCY)
        probed = await asyncio.gather(*(_show(client, root, m, semaphore) for m in base))
    return sorted(probed, key=lambda m: m.modified_at, reverse=True)


async def get_model(
    root: str, name: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> OllamaModelInfo:
    """One download by name (``:latest`` tolerant). Raises
    :class:`OllamaModelNotFound` when the server does not list it."""
    for info in await list_models(root, include_hidden=True, transport=transport):
        if same_model(info.name, name):
            return info
    raise OllamaModelNotFound(f"No download named {name!r} on the Ollama server at {root}.")


async def running_models(
    root: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[OllamaRunningModel]:
    """What ``/api/ps`` says is loaded right now (aliases included — they DO
    occupy memory, and the caller maps them back to their base by name)."""
    root = normalize_server_root(root)
    async with _make_client(transport) as client:
        payload = await _get_json(client, root, "/api/ps")
    rows = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise OllamaServerError(f"Ollama at {root} answered /api/ps in an unexpected shape.")
    out: list[OllamaRunningModel] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("model") or "").strip()
        if not name:
            continue
        ctx = row.get("context_length")
        out.append(
            OllamaRunningModel(
                name=name,
                size_bytes=_as_int(row.get("size")),
                size_vram_bytes=_as_int(row.get("size_vram")),
                expires_at=str(row.get("expires_at") or ""),
                context_length=int(ctx) if isinstance(ctx, (int, float)) and ctx > 0 else None,
                digest=str(row.get("digest") or ""),
            )
        )
    return out


async def unload_model(
    root: str, name: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    """Free the memory a loaded model holds — Ollama's own ``keep_alive: 0``.

    An empty prompt with ``keep_alive: 0`` is the documented unload call; the
    server answers ``done_reason: "unload"``. Unloading a model that is not
    loaded is a no-op on the server and here.
    """
    root = normalize_server_root(root)
    name = name.strip()
    if not name:
        raise OllamaServerError("A model name is required to unload it.")
    async with _make_client(transport) as client:
        try:
            resp = await client.post(f"{root}/api/generate", json={"model": name, "keep_alive": 0})
        except httpx.HTTPError as exc:
            raise _unreachable(root, exc) from exc
    if resp.status_code == 404:
        raise OllamaModelNotFound(f"No download named {name!r} on the Ollama server at {root}.")
    if resp.status_code >= 400:
        raise OllamaServerError(
            f"Ollama refused to unload {name!r} ({resp.status_code}): {_error_text(resp)}"
        )


async def delete_model(
    root: str, name: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    """Remove a download from the server's store (``DELETE /api/delete``).

    Deleting an alias only drops the manifest; the shared weights stay until
    the last name pointing at them is gone. Unknown names raise
    :class:`OllamaModelNotFound`.
    """
    root = normalize_server_root(root)
    name = name.strip()
    if not name:
        raise OllamaServerError("A model name is required to delete it.")
    async with _make_client(transport) as client:
        try:
            resp = await client.request("DELETE", f"{root}/api/delete", json={"model": name})
        except httpx.HTTPError as exc:
            raise _unreachable(root, exc) from exc
    if resp.status_code == 404:
        raise OllamaModelNotFound(f"No download named {name!r} on the Ollama server at {root}.")
    if resp.status_code >= 400:
        raise OllamaServerError(
            f"Ollama refused to delete {name!r} ({resp.status_code}): {_error_text(resp)}"
        )


async def disk_usage(root: str, *, transport: httpx.AsyncBaseTransport | None = None) -> int:
    """Bytes held by the user-visible downloads.

    Aliases are excluded on purpose: they share their weights with the base
    model, and counting both would report a 4 GB model as 8 GB.
    """
    models = await list_models(root, include_hidden=False, transport=transport)
    return sum(m.size_bytes for m in models)


def _error_text(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return resp.text.strip()[:200]
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    return resp.text.strip()[:200]
