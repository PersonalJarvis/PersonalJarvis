"""Brain endpoint resolution for the managed local realtime server (AD-7).

The speech-to-speech server does not think; it forwards each turn to an
OpenAI-Responses-compatible endpoint. Resolution order, capability-gated and
never provider-pinned (AP-21/22):

1. A local Ollama that is actually running and holds a usable model —
   the fully-local path.
2. A configured OpenAI key — honest partial locality ("audio stays on this
   machine, reasoning uses your OpenAI key"). Other cloud families are not
   offered here because the server speaks the Responses API; they join once
   the server (or a proxy) supports their protocols.
3. Neither → a blocker naming the exact fixing actions.

Every probe is short-timeout and read-only; nothing here downloads models.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: Models preferred as the server brain, best first. Only ever chosen from
#: what is ALREADY present in Ollama — the resolver never pulls.
_PREFERRED_MODELS: tuple[str, ...] = (
    "qwen3.5:4b",
    "qwen3.5:9b",
    "gemma4:12b-it-qat",
)

#: Tag substrings that mark models a voice brain must never use.
#:
#: "embed" alone missed the embedding families whose NAME carries no such word
#: — a live picker offered ``bge-m3`` as a voice brain (2026-08-09 screenshot),
#: which would answer every spoken turn with a vector. The named families are
#: the well-known embedding/sentence-transformer lines (BGE = BAAI General
#: Embedding, GTE = General Text Embeddings, E5, MiniLM, paraphrase-*).
#:
#: ``:cloud`` is excluded for a different reason, and it is not a quality
#: judgement: those tags execute on Ollama's servers behind the user's
#: account. This resolver's whole promise is "Fully local: reasoning runs on
#: your Ollama", so a cloud tag would make that sentence a lie and add a
#: sign-in the local path never asked for.
_UNUSABLE_MARKERS: tuple[str, ...] = (
    "embed",
    "whisper",
    "rerank",
    "bge-",
    "gte-",
    "e5-",
    "minilm",
    "paraphrase-",
    ":cloud",
)

_OLLAMA_DEFAULT = "http://127.0.0.1:11434"
_MANAGED_VOICE_SUFFIX = "-voice-8k"


@dataclass(frozen=True, slots=True)
class BrainResolution:
    kind: str  # "ollama" | "cloud-openai" | "blocked"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    #: One honest sentence for the card / preflight summary.
    note: str = ""
    #: For "blocked": the concrete actions that would fix it, best first.
    actions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.kind != "blocked"


def _ollama_base() -> str:
    host = (os.environ.get("OLLAMA_HOST") or "").strip()
    if not host:
        return _OLLAMA_DEFAULT
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return host.rstrip("/")


#: Memory the brain model must leave free on the accelerator, in GiB: the
#: managed stack co-hosts the Qwen3-TTS (bfloat16 + CUDA graphs) and the KV
#: cache on the SAME device, so a model whose weights alone approach the
#: card's capacity OOMs at the first turn. Applied only when both the model
#: size and the usable memory are known — unknown stays permitted (fail-open).
_BRAIN_HEADROOM_GB = 6.0


def _ollama_models(base: str, timeout: float) -> list[tuple[str, float]] | None:
    """(tag, size_gb) of a running Ollama's models; ``None`` when unreachable.

    ``size_gb`` is 0.0 when the tags payload carries no usable size — the
    caller then skips the fit check rather than guessing.
    """
    url = f"{base}/api/tags"
    if not url.startswith(("http://", "https://")):  # scheme is normalized above
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — http(s) only
            payload = json.load(resp)
    except (urllib.error.URLError, OSError, ValueError):
        # An unreachable Ollama is a normal state, not an error: the caller
        # renders it as "not reachable" and offers the install/start path.
        return None
    models = payload.get("models") or []
    out: list[tuple[str, float]] = []
    for entry in models:
        name = str(entry.get("name", "") or "")
        if not name:
            continue
        try:
            size_gb = float(entry.get("size", 0) or 0) / (1024**3)
        except (TypeError, ValueError):
            # An unreadable size means UNKNOWN, and unknown never vetoes a
            # model (the fit check is deliberately fail-open).
            size_gb = 0.0
        out.append((name, size_gb))
    return out


def _usable_tag(tag: str) -> bool:
    lowered = tag.lower()
    return not any(marker in lowered for marker in _UNUSABLE_MARKERS)


def _fits(size_gb: float, usable_gb: float) -> bool:
    if size_gb <= 0.0 or usable_gb <= 0.0:
        return True  # unknown figures never veto (fail-open)
    return size_gb + _BRAIN_HEADROOM_GB <= usable_gb


def _pick_model(
    models: list[tuple[str, float]],
    *,
    preferred_model: str = "",
    usable_gb: float = 0.0,
) -> tuple[str, str]:
    """(chosen tag, honest note) — the note explains a surprising choice.

    Order: the user's configured model when it is served AND fits; then the
    curated preference list; then the first usable tag that fits. A model
    whose weights would not leave the TTS and KV cache any room is skipped
    with the reason in the note instead of OOMing at the first turn.
    """
    by_name = {name: size for name, size in models}
    note = ""
    if preferred_model:
        size = by_name.get(preferred_model)
        if size is not None and _usable_tag(preferred_model):
            if _fits(size, usable_gb):
                return preferred_model, ""
            note = (
                f"Your configured model {preferred_model} (~{size:.0f} GB) "
                f"does not fit next to the voice stack on {usable_gb:.0f} GB "
                "of accelerator memory; picked a smaller one instead."
            )
    for preferred in _PREFERRED_MODELS:
        size = by_name.get(preferred)
        if size is not None and _fits(size, usable_gb):
            return preferred, note
    for name, size in models:
        if _usable_tag(name) and _fits(size, usable_gb):
            return name, note
    return "", note


def _openai_key() -> str:
    try:
        from jarvis.core.config import get_secret

        return (get_secret("openai_api_key", env_fallback="OPENAI_API_KEY") or "").strip()
    except Exception:  # pragma: no cover — config not importable in odd contexts
        log.debug("brain resolution: secret lookup failed", exc_info=True)
        return ""


def list_brain_choices(
    *, timeout: float = 2.5, usable_gb: float = 0.0, current_model: str = ""
) -> dict[str, object]:
    """Every brain candidate for the voice server, annotated for THIS machine.

    The data behind the card's model picker (maintainer ask 2026-08-08: the
    user could not CHOOSE the server brain, only watch the resolver pick).
    Installed chat-capable Ollama tags come first, then curated
    recommendations that are not installed yet; every entry carries the
    honest fit verdict against the accelerator — the same ``_fits`` rule the
    resolver applies, so the picker and the resolver can never disagree.
    """
    base = _ollama_base()
    if current_model.endswith(_MANAGED_VOICE_SUFFIX):
        current_model = current_model[: -len(_MANAGED_VOICE_SUFFIX)]
    served = _ollama_models(base, timeout)
    reachable = served is not None
    choices: list[dict[str, object]] = []
    seen: set[str] = set()
    for name, size in served or []:
        # The managed 8k alias is an implementation detail derived from its
        # base tag. Listing both made one selection look like two models and
        # encouraged users to select an alias that is recreated automatically.
        if name.endswith(_MANAGED_VOICE_SUFFIX) or not _usable_tag(name):
            continue
        seen.add(name)
        fits = _fits(size, usable_gb)
        choices.append(
            {
                "id": name,
                "label": name,
                "size_gb": round(size, 1),
                "installed": True,
                "fits": fits,
                "note": "" if fits else _no_fit_note(size, usable_gb),
                "recommended": name in _PREFERRED_MODELS,
                "current": bool(current_model) and name == current_model,
            }
        )
    from jarvis.brain.ollama_pull import RECOMMENDED_MODELS  # lazy (AP-26)

    for entry in RECOMMENDED_MODELS:
        if entry.role != "chat" or entry.id in seen:
            continue
        fits = _fits(entry.size_gb, usable_gb)
        choices.append(
            {
                "id": entry.id,
                "label": entry.label,
                "size_gb": round(entry.size_gb, 1),
                "installed": False,
                "fits": fits,
                "note": "" if fits else _no_fit_note(entry.size_gb, usable_gb),
                "recommended": entry.id in _PREFERRED_MODELS,
                "current": False,
            }
        )
    return {
        "reachable": reachable,
        "usable_gb": round(usable_gb, 1),
        "current": current_model,
        "models": choices,
    }


def _no_fit_note(size_gb: float, usable_gb: float) -> str:
    return (
        f"~{size_gb:.0f} GB does not fit next to the voice stack on "
        f"{usable_gb:.0f} GB of accelerator memory."
    )


def resolve_brain(
    *, timeout: float = 2.5, preferred_model: str = "", usable_gb: float = 0.0
) -> BrainResolution:
    """Resolve the best brain endpoint available on THIS machine right now.

    ``preferred_model`` is the user's configured Ollama model (wins when it
    is served and fits); ``usable_gb`` enables the VRAM fit check — a 24 GB
    tag on a 16 GB card must be skipped with the reason said out loud, not
    selected and OOMed at the first turn.
    """
    base = _ollama_base()
    models = _ollama_models(base, timeout)
    if models is not None:
        model, note = _pick_model(
            models, preferred_model=preferred_model, usable_gb=usable_gb
        )
        if model:
            sentence = f"Fully local: reasoning runs on your Ollama ({model})."
            if note:
                sentence = f"{sentence} {note}"
            return BrainResolution(
                kind="ollama",
                base_url=f"{base}/v1",
                api_key="ollama",
                model=model,
                note=sentence,
            )
        return BrainResolution(
            kind="blocked",
            note=(
                "Ollama is running but holds no usable chat model, so the "
                "local server would have no brain."
            ),
            actions=(
                f"Run: ollama pull {_PREFERRED_MODELS[0]}",
                "Or add an OpenAI API key in Settings for cloud reasoning.",
            ),
        )
    key = _openai_key()
    if key:
        return BrainResolution(
            kind="cloud-openai",
            # base_url stays empty on purpose: the server's OpenAI client
            # defaults to api.openai.com and reads OPENAI_API_KEY from its
            # spawn environment, so no secret ever enters a command line.
            api_key=key,
            model="gpt-5.4-mini",
            note=(
                "Audio stays on this machine; reasoning uses your OpenAI "
                "key (no local brain found)."
            ),
        )
    return BrainResolution(
        kind="blocked",
        note="No brain available: neither a running Ollama nor an OpenAI key.",
        actions=(
            f"Install Ollama (ollama.com) and run: ollama pull {_PREFERRED_MODELS[0]}",
            "Or add an OpenAI API key in Settings.",
        ),
    )
