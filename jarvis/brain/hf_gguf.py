"""Browse GGUF repositories on Hugging Face for an Ollama pull.

Ollama pulls ``hf.co/<user>/<repo>[:<quant>]`` through the same ``/api/pull``
as its own library. What it does NOT offer is a way to find those names. This
module is that finder — read-only, anonymous JSON, no scraping:

- search: ``GET /api/models?filter=gguf&search=…&sort=…&expand[]=gguf``
- files:  ``GET /api/models/{user}/{repo}/tree/main`` filtered to ``.gguf``

Contract, same as :mod:`jarvis.brain.ollama_library`: every failure degrades
to ``{"repos"/"files": [], "error": "<English sentence>"}`` and is logged;
the pull itself never depends on this module, the local server stays the
authority on whether a name exists.

Rate limits: anonymous callers get 500 API calls per 5-minute window per IP
(shared with everything else the box does). A 10-minute cache keeps a browsing
session far below that; a 429 names the window and the optional token
(``get_secret("huggingface")``, sent as a bearer when present, never required).

Lazy-importable on purpose: the routes import this module only when
``[brain.providers.ollama].hf_enabled`` is true, so an install that wants no
outbound Hugging Face traffic makes none.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Literal

import httpx

from jarvis.brain.ollama_pull import accelerator_gb, fit_verdict, total_memory_gb

log = logging.getLogger(__name__)

_HF_ROOT = "https://huggingface.co"

#: Short connect so a dead network fails the panel fast; the tree listing of a
#: repository with dozens of quantizations is still small JSON.
_FETCH_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=10.0)

#: Ten minutes, like the Ollama library browser: the catalogue moves slowly and
#: the panel re-queries on every keystroke burst.
_CACHE_TTL_SECONDS = 600.0

#: One page of results; a narrower query beats scrolling.
_SEARCH_LIMIT = 30
_SEARCH_LIMIT_MAX = 100

SortKey = Literal["downloads", "lastModified", "trendingScore"]
_SORT_KEYS: tuple[str, ...] = ("downloads", "lastModified", "trendingScore")

#: Hugging Face namespaces and repository names. Doubles as the URL-injection
#: guard: a value that fails this never reaches the fetch layer.
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")

#: A quantization label inside a GGUF filename, e.g. ``Q4_K_M``, ``IQ2_XS``,
#: ``Q8_0``, ``F16``, ``BF16``. Anchored on separators so ``iq2`` in an
#: unrelated word does not match.
_QUANT_RE = re.compile(
    r"(?<![A-Za-z0-9])((?:I?Q\d(?:_[A-Z0-9]+)*)|F16|F32|BF16)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

_RATE_LIMIT_SENTENCE = (
    "Hugging Face is rate-limiting this machine (anonymous callers get 500 "
    "requests per 5-minute window). Wait a few minutes, or add a Hugging Face "
    "token under API keys to raise the limit."
)
_OFFLINE_SENTENCE = (
    "huggingface.co did not answer, so repositories cannot be browsed right "
    "now. Pulling by exact hf.co/<user>/<repo> name still works."
)

# Caches hold the CATALOGUE only — fit verdicts are judged fresh per call.
_search_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_files_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


# ── validation & naming ───────────────────────────────────────────────────


def valid_segment(value: str) -> bool:
    """True when ``value`` is a well-formed Hugging Face user or repo name."""
    return bool(value) and ".." not in value and _SEGMENT_RE.match(value) is not None


def pull_name(user: str, repo: str, quant: str | None = None) -> str:
    """The name Ollama pulls: ``hf.co/{user}/{repo}`` plus ``:{quant}`` if given.

    Raises ``ValueError`` on a malformed segment so a caller can never build a
    name that smuggles a path or query into ``/api/pull``.
    """
    if not (valid_segment(user) and valid_segment(repo)):
        raise ValueError("Not a valid Hugging Face repository name.")
    name = f"hf.co/{user}/{repo}"
    quant = (quant or "").strip()
    if quant:
        if not _QUANT_RE.fullmatch(quant) and not _SEGMENT_RE.match(quant):
            raise ValueError("Not a valid quantization label.")
        name = f"{name}:{quant}"
    return name


def parse_quant(filename: str) -> str | None:
    """The quantization label a GGUF filename carries, or ``None``.

    Upper-cased so ``q4_k_m`` and ``Q4_K_M`` build the same pull name — Ollama
    matches the suffix case-insensitively anyway.
    """
    stem = filename.rsplit("/", 1)[-1]
    if stem.lower().endswith(".gguf"):
        stem = stem[: -len(".gguf")]
    match = _QUANT_RE.search(stem)
    return match.group(1).upper() if match else None


# ── fetch layer ───────────────────────────────────────────────────────────


def _bearer() -> str | None:
    """An optional Hugging Face token; absence is the normal state."""
    try:
        from jarvis.core import config as cfg_mod  # noqa: PLC0415 — lazy (AP-26)

        return cfg_mod.get_secret("huggingface", env_fallback="HF_TOKEN") or None
    except Exception:  # noqa: BLE001 — a broken secret store must not block browsing
        log.debug("hf-gguf: token lookup failed", exc_info=True)
        return None


async def _fetch_json(
    path: str, params: list[tuple[str, str]] | None = None
) -> tuple[Any | None, str | None]:
    """``(payload, error)`` for one Hugging Face API call — exactly one is non-None."""
    url = f"{_HF_ROOT}{path}"
    headers = {"User-Agent": "Jarvis-Agents local model browser"}
    if token := _bearer():
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT, follow_redirects=True, headers=headers
        ) as client:
            resp = await client.get(url, params=list(params or []))
    except Exception as exc:  # noqa: BLE001 — offline is a normal state here
        log.info("hf-gguf: %s unreachable (%s)", url, type(exc).__name__)
        return None, _OFFLINE_SENTENCE
    if resp.status_code == 429:
        log.info("hf-gguf: rate-limited by huggingface.co on %s", path)
        return None, _RATE_LIMIT_SENTENCE
    if resp.status_code == 404:
        return None, "Hugging Face has no repository with that name."
    if resp.status_code in (401, 403):
        return None, (
            "Hugging Face refused access to this repository. It is private or "
            "gated — a token under API keys is needed to browse it."
        )
    if resp.status_code != 200:
        return None, (
            f"huggingface.co answered {resp.status_code}, so repositories cannot "
            "be browsed right now."
        )
    try:
        return resp.json(), None
    except ValueError:
        log.warning("hf-gguf: %s answered non-JSON", url)
        return None, "huggingface.co answered something that is not JSON."


def _cache_get(cache: dict[str, tuple[float, Any]], key: str) -> Any | None:
    hit = cache.get(key)
    if hit and (time.monotonic() - hit[0]) < _CACHE_TTL_SECONDS:
        return hit[1]
    return None


# ── search ────────────────────────────────────────────────────────────────


def _shape_repo(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One search entry in the shape the panel reads; ``None`` if unreadable."""
    repo_id = raw.get("id") or raw.get("modelId")
    if not isinstance(repo_id, str) or "/" not in repo_id:
        return None
    author = raw.get("author") or repo_id.split("/", 1)[0]
    gguf: dict[str, Any] = raw["gguf"] if isinstance(raw.get("gguf"), dict) else {}
    total = gguf.get("total")
    context = gguf.get("context_length")
    return {
        "id": repo_id,
        "author": str(author),
        "downloads": int(raw.get("downloads") or 0),
        "likes": int(raw.get("likes") or 0),
        "last_modified": str(raw.get("lastModified") or ""),
        "architecture": str(gguf.get("architecture") or ""),
        "total_params": int(total) if isinstance(total, (int, float)) else None,
        "context_length": int(context) if isinstance(context, (int, float)) else None,
    }


def parse_search_payload(payload: Any) -> list[dict[str, Any]]:
    """Repositories from a ``/api/models`` answer, order kept, junk skipped."""
    if not isinstance(payload, list):
        return []
    repos: list[dict[str, Any]] = []
    for raw in payload:
        if isinstance(raw, dict) and (shaped := _shape_repo(raw)):
            repos.append(shaped)
    return repos


async def search(q: str, *, sort: str = "downloads", limit: int = _SEARCH_LIMIT) -> dict[str, Any]:
    """GGUF repositories matching ``q``: ``{"repos": [...], "error": str | None}``."""
    q = (q or "").strip()
    if sort not in _SORT_KEYS:
        sort = "downloads"
    limit = max(1, min(int(limit or _SEARCH_LIMIT), _SEARCH_LIMIT_MAX))
    key = f"{sort}|{limit}|{q.lower()}"
    repos = _cache_get(_search_cache, key)
    if repos is None:
        params: list[tuple[str, str]] = [
            ("filter", "gguf"),
            ("sort", sort),
            ("direction", "-1"),
            ("limit", str(limit)),
            ("expand[]", "gguf"),
        ]
        if q:
            params.insert(1, ("search", q))
        payload, error = await _fetch_json("/api/models", params)
        if error:
            return {"repos": [], "error": error}
        repos = parse_search_payload(payload)
        if not repos and payload:
            log.warning("hf-gguf: search answered %d entries, none readable", len(payload))
            return {
                "repos": [],
                "error": "Hugging Face answered in a shape this version cannot read.",
            }
        _search_cache[key] = (time.monotonic(), [dict(r) for r in repos])
    return {"repos": [dict(r) for r in repos], "error": None}


# ── files ─────────────────────────────────────────────────────────────────


def parse_tree_payload(payload: Any) -> list[dict[str, Any]]:
    """``.gguf`` entries from a ``/tree/main`` answer with quant and size."""
    if not isinstance(payload, list):
        return []
    files: list[dict[str, Any]] = []
    for raw in payload:
        if not isinstance(raw, dict) or raw.get("type") != "file":
            continue
        path = raw.get("path")
        if not isinstance(path, str) or not path.lower().endswith(".gguf"):
            continue
        # Only the LFS size is the weight file's size; the plain ``size`` of a
        # pointer file is a few hundred bytes and would read as "0 GB, fits".
        lfs: dict[str, Any] = raw["lfs"] if isinstance(raw.get("lfs"), dict) else {}
        size = lfs.get("size")
        size_gb = round(size / 1e9, 2) if isinstance(size, (int, float)) and size > 0 else None
        files.append({"filename": path, "quant": parse_quant(path), "size_gb": size_gb})
    files.sort(key=lambda f: (f["size_gb"] is None, f["size_gb"] or 0.0))
    return files


async def files(user: str, repo: str) -> dict[str, Any]:
    """GGUF files of one repository with a fit verdict each.

    ``{"files": [{filename, quant, size_gb, fit, fit_note}], "error": str | None}``.
    The catalogue half is cached; the verdicts are judged fresh against this
    machine on every call.
    """
    if not (valid_segment(user) and valid_segment(repo)):
        return {"files": [], "error": "Not a valid Hugging Face repository name."}
    key = f"{user}/{repo}"
    listed = _cache_get(_files_cache, key)
    if listed is None:
        payload, error = await _fetch_json(f"/api/models/{user}/{repo}/tree/main")
        if error:
            return {"files": [], "error": error}
        listed = parse_tree_payload(payload)
        if not listed:
            return {"files": [], "error": f"{key} holds no GGUF file Ollama could pull."}
        _files_cache[key] = (time.monotonic(), [dict(f) for f in listed])

    memory_gb = total_memory_gb()
    accel, _source = accelerator_gb()
    out: list[dict[str, Any]] = []
    for entry in listed:
        row = dict(entry)
        if row["size_gb"] is None:
            row["fit"], row["fit_note"] = "unknown", ""
        else:
            row["fit"], row["fit_note"] = fit_verdict(row["size_gb"], memory_gb, accel)
        out.append(row)
    return {"files": out, "error": None}


__all__ = [
    "SortKey",
    "files",
    "parse_quant",
    "parse_search_payload",
    "parse_tree_payload",
    "pull_name",
    "search",
    "valid_segment",
]
