"""Readable names for Ollama downloads, from the tag and the manifest.

A tag is an address, not a name: ``deepseek-r1:latest`` hides that it is
the 8B, ``qwen3.8-16gb:latest`` is a 27B at IQ2_S whatever its author called
it, and ``gemma4:12b-it-qat`` carries two variant markers a person scanning
a list does not need. The "Local models" section showed every one of them
verbatim, so a user had to know the tags by heart to tell the cards apart.

:func:`describe` turns a tag plus the ``/api/show`` facts into the parts a
card shows separately: the family name humanised from the repository part
of the tag ("Qwen 3.5", "DeepSeek R1", "BGE M3"), the parameter count as
a short label ("4B", "27B", "567M" — the tag's own size token when it has
one, the manifest's otherwise), the quantisation, and the source namespace
for Hugging Face imports. The tag itself stays on the card, small, so the
address is never lost.

:func:`alias_kind` / :func:`base_of` fold Jarvis's own derived models — the
Tune profiles ``<base>-jarvis-<hash>`` and the voice brain's
``<base>-voice-<N>k`` — back onto the download they share their weights
with, so a loaded alias counts for its base instead of for nobody.

Pure functions, no I/O; the inventory and overview builders call them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "ModelName",
    "alias_kind",
    "base_of",
    "describe",
    "params_label",
]

_PROFILE_ALIAS_RE = re.compile(r"^(?P<prefix>.+)-jarvis-[0-9a-f]{8}$")
_VOICE_ALIAS_RE = re.compile(r"^(?P<prefix>.+)-voice-\d+k$")

#: Family words whose casing a plain ``capitalize()`` gets wrong.
_WORDS: dict[str, str] = {
    "arctic": "Arctic",
    "bge": "BGE",
    "cascade": "Cascade",
    "codellama": "CodeLlama",
    "coder": "Coder",
    "deepseek": "DeepSeek",
    "dolphin": "Dolphin",
    "e5": "E5",
    "embed": "Embed",
    "embedding": "Embedding",
    "embeddinggemma": "EmbeddingGemma",
    "gemma": "Gemma",
    "glimmer": "Glimmer",
    "glm": "GLM",
    "gpt": "GPT",
    "granite": "Granite",
    "hermes": "Hermes",
    "kimi": "Kimi",
    "lfm": "LFM",
    "lightning": "Lightning",
    "llama": "Llama",
    "llm": "LLM",
    "minicpm": "MiniCPM",
    "minilm": "MiniLM",
    "mistral": "Mistral",
    "mixtral": "Mixtral",
    "moe": "MoE",
    "muse": "Muse",
    "mxbai": "MxBAI",
    "nemotron": "Nemotron",
    "nomic": "Nomic",
    "olmo": "OLMo",
    "ornith": "Ornith",
    "oss": "OSS",
    "phi": "Phi",
    "qwen": "Qwen",
    "qwq": "QwQ",
    "smollm": "SmolLM",
    "snowflake": "Snowflake",
    "starcoder": "StarCoder",
    "tinyllama": "TinyLlama",
    "vl": "VL",
    "yi": "Yi",
}

#: Variant markers that describe a build, not a model line.
_DROP = frozenset({"it", "instruct", "chat", "qat", "latest", "gguf"})
_SIZE_TOKEN = re.compile(r"^(\d+(?:\.\d+)?)(b|m|k|gb)$")
_ACTIVE_TOKEN = re.compile(r"^a\d+(?:\.\d+)?b$")
_QUANT_TOKEN = re.compile(r"^(i?q\d[a-z0-9_]*|fp\d+|f\d+|bf\d+|int\d+)$")
_WORD_NUM = re.compile(r"^([a-z]+)(\d+(?:\.\d+)?)$")
_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_PARAMS = re.compile(r"^(\d+(?:\.\d+)?)\s*([bmk])$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ModelName:
    """The parts of a readable model name, for a card to lay out."""

    #: The model line, humanised: "Qwen 3.5", "DeepSeek R1", "BGE M3".
    name: str
    #: Parameter count as a short label: "4B", "27B", "567M"; "" unknown.
    params: str
    #: Quantisation as the manifest spells it: "Q4_K_M", "IQ2_S"; "" unknown.
    quant: str
    #: ``name`` and ``params`` in one line: "Qwen 3.5 4B".
    label: str
    #: Where the tag comes from when not the Ollama library: "hf.co/unsloth".
    source: str
    #: Variant markers the tag carries beyond the size: "it-qat"; "" none.
    variant: str


def _strip_latest(tag: str) -> str:
    return tag[: -len(":latest")] if tag.endswith(":latest") else tag


def _tokens(text: str) -> list[str]:
    """Split on ``-``, then on ``_`` — except inside a quantisation token
    (``q4_k_m``), whose underscores are part of the name."""
    out: list[str] = []
    for piece in text.lower().split("-"):
        if not piece:
            continue
        if _QUANT_TOKEN.match(piece):
            out.append(piece)
            continue
        out.extend(p for p in piece.split("_") if p)
    return out


def _humanise(part: str) -> str:
    if part in _WORDS:
        return _WORDS[part]
    if _NUMBER.match(part):
        return part
    match = _WORD_NUM.match(part)
    if match:
        word, number = match.groups()
        if len(word) == 1:
            return f"{word.upper()}{number}"
        return f"{_WORDS.get(word, word.capitalize())} {number}"
    return part.capitalize()


def params_label(parameter_size: str, tag_token: str = "") -> str:
    """``"9.0B"`` → ``"9B"``, ``"26.9B"`` → ``"27B"``, ``"566.70M"`` → ``"567M"``.

    A size token in the tag (``:4b``, ``:14b``) wins over the manifest: it is
    the size the model is known by, and ``4.7B`` next to a card everyone
    calls "the 4B" reads like a different model.
    """
    token = _SIZE_TOKEN.match(tag_token.lower()) if tag_token else None
    if token and token.group(2) in ("b", "m"):
        number, unit = token.groups()
        return f"{_short_number(float(number))}{unit.upper()}"
    match = _PARAMS.match((parameter_size or "").strip())
    if not match:
        return ""
    number, unit = match.groups()
    return f"{_short_number(float(number))}{unit.upper()}"


def _short_number(value: float) -> str:
    if value >= 10:
        return str(int(round(value)))
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def describe(tag: str, parameter_size: str = "", quantization_level: str = "") -> ModelName:
    """The readable parts of ``tag`` (any Ollama tag, ``hf.co/`` imports too)."""
    bare = _strip_latest((tag or "").strip())
    if not bare:
        return ModelName(name="", params="", quant="", label="", source="", variant="")
    repo, _, variant = bare.partition(":")
    source = ""
    if repo.lower().startswith("hf.co/") or repo.lower().startswith("huggingface.co/"):
        pieces = repo.split("/")
        source = "/".join(pieces[:2])
        repo = pieces[-1]
    elif "/" in repo:
        namespace, _, repo = repo.rpartition("/")
        source = namespace

    words: list[str] = []
    size_token = ""
    for part in _tokens(repo):
        if not part or part in _DROP:
            continue
        if _SIZE_TOKEN.match(part):
            if part.endswith("b") or part.endswith("m"):
                size_token = size_token or part
            continue
        if _ACTIVE_TOKEN.match(part) or _QUANT_TOKEN.match(part):
            continue
        words.append(_humanise(part))

    variant_words: list[str] = []
    for part in _tokens(variant):
        if not part or part == "latest":
            continue
        if _SIZE_TOKEN.match(part):
            size_token = size_token or part
            continue
        if _QUANT_TOKEN.match(part) and not quantization_level:
            quantization_level = part.upper()
            continue
        variant_words.append(part)

    name = " ".join(words) or repo
    params = params_label(parameter_size, size_token)
    quant = (quantization_level or "").strip()
    label = f"{name} {params}".strip()
    return ModelName(
        name=name,
        params=params,
        quant=quant,
        label=label,
        source=source,
        variant="-".join(variant_words),
    )


def alias_kind(tag: str) -> str:
    """``"tune_profile"`` | ``"voice_profile"`` | ``""`` for a user's download."""
    bare = _strip_latest((tag or "").strip())
    if _PROFILE_ALIAS_RE.match(bare):
        return "tune_profile"
    if _VOICE_ALIAS_RE.match(bare):
        return "voice_profile"
    return ""


def _fold(tag: str) -> str:
    return _strip_latest(tag.strip()).replace(":", "-").replace("/", "-")


def base_of(tag: str, candidates: Iterable[str] = ()) -> str:
    """The download an alias shares its weights with; ``tag`` itself otherwise.

    A voice alias keeps the base's ``:`` (``ornith:9b-voice-32k``), so the
    suffix simply comes off. A Tune profile folded ``:`` and ``/`` into ``-``
    when it was named (``ornith-9b-jarvis-1a2b3c4d``), so the base is the
    candidate whose folded name equals the prefix; with no candidate to
    match, the folded prefix is the best answer there is.
    """
    bare = _strip_latest((tag or "").strip())
    voice = _VOICE_ALIAS_RE.match(bare)
    if voice:
        return voice.group("prefix")
    profile = _PROFILE_ALIAS_RE.match(bare)
    if not profile:
        return bare
    prefix = profile.group("prefix")
    for candidate in candidates:
        if _fold(candidate) == prefix:
            return _strip_latest(candidate.strip())
    return prefix
