"""The model slots Ollama fills, read and written from ONE place.

Today the local brain is spread over five config keys the user never sees
side by side: the chat model, the voice model (the managed voice server's
brain, baked into its launch command), the tool/screen model, the deep model
and the wiki's embedding model — plus two read-only consumers (the quick ack,
dictation polish) that pin their own tag.
This module names every slot as a :class:`RoleSpec`, reads its current pick
from a loaded config, judges which INSTALLED downloads qualify for it (by the
capabilities ``/api/show`` declares) and points at the shortlist's pick for
this machine, so the "Local models" section can show the four rows with one
button each instead of four different pickers in four different views.

Writing goes through the existing config writers only
(``config_writer.set_brain_provider_model`` / ``set_ultrawiki_slot``), so a
role change lands in ``jarvis.toml`` AND the drift baseline exactly like a
pick made on the provider card — the drift guard sees no difference (AP-7,
BUG-010 class). The tier defaults stay ``""`` (= plugin-side discovery);
nothing here pins a model by default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from jarvis.brain import ollama_inventory as inventory
from jarvis.brain import ollama_pull
from jarvis.brain.ollama_inventory import OllamaModelInfo, OllamaServerError, same_model

log = logging.getLogger(__name__)

__all__ = [
    "ROLES",
    "WRITABLE_ROLE_IDS",
    "RoleSpec",
    "RoleState",
    "current_pick",
    "list_roles",
    "qualifying_models",
    "role_spec",
    "roles_using",
    "set_role",
]


@dataclass(frozen=True, slots=True)
class RoleSpec:
    """One job a local model does, and where the brain reads its pick."""

    id: str
    #: i18n key of the row label (``local_models.role_<id>``).
    label_key: str
    #: Dotted config path, for the "where does this live" footnote.
    config_key: str
    #: Capabilities (``/api/show`` vocabulary) a model MUST declare.
    required: tuple[str, ...]
    #: Capabilities that make a model a better fit but are not a gate.
    recommended: tuple[str, ...]
    #: The shortlist role whose pick serves this slot
    #: (:data:`jarvis.brain.ollama_pull.ROLE_ORDER` vocabulary).
    pull_role: str
    #: Whether :func:`set_role` may write it. Read-only roles are shown under
    #: "More roles" so the user sees which tag they follow, and where.
    writable: bool = True
    #: Hidden in the Simple view.
    advanced: bool = False


ROLES: tuple[RoleSpec, ...] = (
    RoleSpec(
        id="chat",
        label_key="local_models.role_chat",
        config_key="brain.providers.ollama.model",
        required=("completion",),
        recommended=("tools",),
        pull_role="chat",
    ),
    RoleSpec(
        id="voice",
        label_key="local_models.role_voice",
        config_key="brain.providers.local-realtime.launch_command",
        required=("completion",),
        recommended=("tools",),
        pull_role="chat",
    ),
    RoleSpec(
        id="tools_screen",
        label_key="local_models.role_tools_screen",
        config_key="brain.providers.ollama.tool_model",
        required=("tools", "vision"),
        recommended=(),
        pull_role="vision",
    ),
    RoleSpec(
        id="deep",
        label_key="local_models.role_deep",
        config_key="brain.providers.ollama.deep_model",
        required=("tools",),
        recommended=("thinking",),
        pull_role="coder",
    ),
    RoleSpec(
        id="embedding",
        label_key="local_models.role_embedding",
        config_key="ultrawiki.embedding_model",
        required=("embedding",),
        recommended=(),
        pull_role="embedding",
    ),
    # Read-only consumers: the ack and polish models have their own pickers
    # on their own cards, so the rows only say which tag they follow.
    RoleSpec(
        id="ack",
        label_key="local_models.role_ack",
        config_key="ack_brain.providers.ollama.model",
        required=("completion",),
        recommended=(),
        pull_role="chat",
        writable=False,
        advanced=True,
    ),
    RoleSpec(
        id="polish",
        label_key="local_models.role_polish",
        config_key="dictation.polish_model",
        required=("completion",),
        recommended=(),
        pull_role="chat",
        writable=False,
        advanced=True,
    ),
)

WRITABLE_ROLE_IDS: tuple[str, ...] = tuple(r.id for r in ROLES if r.writable)


@dataclass(frozen=True, slots=True)
class RoleState:
    """A role as the section renders it."""

    spec: RoleSpec
    #: The configured tag, or ``""`` = the plugin discovers one.
    current: str
    #: Whether ``current`` is on the server right now.
    installed: bool
    #: Installed tags that declare every required capability.
    qualifying: tuple[str, ...]
    #: The shortlist's pick for this machine (``""`` when it has none).
    recommended: str
    #: One sentence when the slot is not Ollama-backed right now.
    note: str = ""
    #: The voice brain's effective ``num_ctx`` on this machine (voice role only).
    context_tokens: int | None = None
    #: ``"automatic"`` (sized from memory) | ``"manual"`` (set in Tune) | ``""``.
    context_source: str = ""


# ── Reading ──────────────────────────────────────────────────────────────


def role_spec(role_id: str) -> RoleSpec:
    for spec in ROLES:
        if spec.id == role_id:
            return spec
    raise ValueError(f"Unknown role '{role_id}' (known: {', '.join(r.id for r in ROLES)}).")


def _ollama_provider(cfg: Any) -> Any:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    return providers.get("ollama") if isinstance(providers, dict) else None


def _str(value: object) -> str:
    return str(value or "").strip()


def current_pick(cfg: Any, role_id: str) -> tuple[str, str]:
    """``(tag, note)`` the config holds for ``role_id``.

    ``tag`` is ``""`` for discovery; ``note`` is one sentence when the slot
    is served by something other than Ollama (the wiki embedding with a
    cloud provider, dictation polish on another provider), so the row can
    say so instead of showing an unrelated tag as if Ollama ran it.
    """
    if cfg is None:
        return "", ""
    provider = _ollama_provider(cfg)
    if role_id == "chat":
        return _str(getattr(provider, "model", "")), ""
    if role_id == "voice":
        return _voice_pick(cfg)
    if role_id == "tools_screen":
        return (
            _str(getattr(provider, "tool_model", "")) or _str(getattr(provider, "cu_model", "")),
            "",
        )
    if role_id == "deep":
        return _str(getattr(provider, "deep_model", "")), ""
    if role_id == "embedding":
        wiki = getattr(cfg, "ultrawiki", None)
        backend = _str(getattr(wiki, "embedding_provider", ""))
        model = _str(getattr(wiki, "embedding_model", ""))
        if backend and backend != "ollama":
            return "", f"The wiki embeds with {backend}, not with Ollama."
        return model, ""
    if role_id == "ack":
        ack = getattr(cfg, "ack_brain", None)
        providers = getattr(ack, "providers", None)
        return _str(getattr(getattr(providers, "ollama", None), "model", "")), ""
    if role_id == "polish":
        dictation = getattr(cfg, "dictation", None)
        backend = _str(getattr(dictation, "polish_provider", ""))
        model = _str(getattr(dictation, "polish_model", ""))
        if backend and backend not in ("auto", "ollama"):
            return "", f"Dictation polish runs on {backend}, not on Ollama."
        return model, ""
    raise ValueError(f"Unknown role '{role_id}'.")


def _voice_provider(cfg: Any) -> Any:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    return providers.get("local-realtime") if isinstance(providers, dict) else None


def _voice_pick(cfg: Any) -> tuple[str, str]:
    """The managed voice server's brain: the ``--model_name`` of its command.

    A voice call and a typed chat are different jobs (a call wants a small,
    fast model; a chat can afford a stronger one), so the voice brain has its
    own slot. It is not a config field of its own — the server bakes it into
    the launch command the installer wrote — hence the parse here, with the
    ``-voice-8k`` context alias folded back to the base tag the user picked.
    """
    command = _str(getattr(_voice_provider(cfg), "launch_command", ""))
    if not command:
        return "", "The managed voice server is not installed; a call answers with the Chat model."
    from jarvis.realtime.local_server.supervisor import (  # lazy: off the read path
        _brain_endpoint,
        _voice_context_models,
    )

    model, _base = _brain_endpoint(command)
    if not model:
        return "", "The voice server's launch command names no brain model."
    return _voice_context_models(model)[0], ""


def qualifying_models(spec: RoleSpec, models: list[OllamaModelInfo]) -> tuple[str, ...]:
    """Installed tags that declare every capability ``spec`` requires.

    A row whose ``/api/show`` failed (``probed=False``) is left out: "unknown"
    must not read as "qualifies", or a screen reader without vision gets
    assigned and fails on the first screenshot.
    """
    out: list[str] = []
    for info in models:
        if not info.probed:
            continue
        if all(cap in info.capabilities for cap in spec.required):
            out.append(info.name)
    return tuple(out)


def _recommended_for(spec: RoleSpec, rows: list[dict[str, Any]]) -> str:
    """The shortlist's pick for ``spec`` on this machine.

    :func:`ollama_pull._pick_recommended` marks nothing for a role that has
    a curated model installed already — the user has chosen. Then the
    largest INSTALLED curated model of that role is named instead, so the
    "Use recommended" button always has something honest to assign.
    """
    for row in rows:
        if spec.pull_role in (row.get("recommended_for") or []):
            return str(row["id"])
    installed = [
        row
        for row in rows
        if row.get("installed")
        and (row.get("role") == spec.pull_role or ollama_pull._serves_vision(spec.pull_role, row))
    ]
    if installed:
        return str(max(installed, key=lambda r: float(r.get("size_gb") or 0))["id"])
    return ""


async def list_roles(
    root: str, cfg: Any, *, transport: Any = None
) -> tuple[list[RoleState], str | None]:
    """Every role with its pick, what qualifies, and the recommendation.

    Returns ``(states, error)``; ``error`` is the server's English sentence
    when ``/api/tags`` did not answer — the states are still complete (with
    empty ``qualifying`` and ``installed=False``) so the rows render.
    """
    error: str | None = None
    try:
        models = await inventory.list_models(root, transport=transport)
    except OllamaServerError as exc:
        log.info("ollama-roles: inventory unavailable at %s: %s", root, exc)
        models = []
        error = str(exc)
    try:
        shortlist = (await ollama_pull.recommendations()).get("models") or []
    except Exception as exc:  # noqa: BLE001 — the shortlist is advisory, the rows are not
        log.warning("ollama-roles: shortlist unavailable: %s", exc)
        shortlist = []

    states: list[RoleState] = []
    for spec in ROLES:
        current, note = current_pick(cfg, spec.id)
        qualifying = qualifying_models(spec, models)
        context_tokens: int | None = None
        context_source = ""
        if spec.id == "voice" and current and error is None:
            context_tokens, context_source = await voice_context(cfg, current)
        states.append(
            RoleState(
                spec=spec,
                current=current,
                installed=bool(current) and any(same_model(m.name, current) for m in models),
                qualifying=qualifying,
                recommended=_recommended_for(spec, shortlist),
                note=note,
                context_tokens=context_tokens,
                context_source=context_source,
            )
        )
    return states, error


async def voice_context(cfg: Any, model: str) -> tuple[int | None, str]:
    """``(num_ctx, source)`` the managed voice brain would run ``model`` with.

    Off the event loop: the sizing reads the machine's memory and asks Ollama
    for the model's facts. Any failure is ``(None, "")`` — the row then simply
    shows no context line rather than a guess.
    """
    import asyncio

    from jarvis.realtime.local_server import supervisor  # lazy: off the read path

    command = _str(getattr(_voice_provider(cfg), "launch_command", ""))
    _model, base = supervisor._brain_endpoint(command)
    root = base[: -len("/v1")] if base.endswith("/v1") else base
    if not root.startswith(("http://", "https://")):
        return None, ""
    override = supervisor._voice_context_override(model)
    try:
        tokens, _why = await asyncio.to_thread(
            supervisor.voice_brain_context_tokens, root, model, timeout=2.0, override=override
        )
    except Exception:  # noqa: BLE001 — a sizing failure must not break the roles list
        log.debug("ollama-roles: voice context sizing failed for %s", model, exc_info=True)
        return None, ""
    return tokens, "manual" if override else "automatic"


# ── Writing ──────────────────────────────────────────────────────────────


def set_role(role_id: str, model: str, *, cfg: Any = None) -> dict[str, str]:
    """Persist ``model`` as the pick of ``role_id``; ``""`` = discovery.

    Dispatches to the writer the provider card already uses for that key, so
    the TOML and the drift baseline agree; ``cfg`` (the live config object) is
    updated in place when given, so the next read answers the new value
    without a restart. Returns ``{"role", "model", "config_key"}``.

    Raises ``ValueError`` for an unknown or read-only role, and for an empty
    embedding model (the wiki needs a name; there is no discovery there).
    """
    spec = role_spec(role_id)
    if not spec.writable:
        raise ValueError(
            f"'{role_id}' follows {spec.config_key} and is changed on its own card, not here."
        )
    tag = _str(model)
    from jarvis.core import config_writer  # lazy: tomlkit + file locks off the read path

    provider = _ollama_provider(cfg)
    if spec.id == "chat":
        config_writer.set_brain_provider_model("ollama", model=tag)
        if provider is not None:
            provider.model = tag
    elif spec.id == "voice":
        if not tag:
            raise ValueError(
                "The voice role needs a model name; the voice server cannot discover one."
            )
        voice = _voice_provider(cfg)
        command = _str(getattr(voice, "launch_command", ""))
        if not command or "--model_name" not in command:
            raise ValueError(
                "Install the managed voice server first; until then a call "
                "answers with the Chat model."
            )
        # The same writer the voice card uses: rewrites ONLY the model flag of
        # the persisted command, never a bring-your-own command.
        config_writer.update_local_realtime_launch_model(tag)
        if voice is not None:
            from jarvis.realtime.local_server.supervisor import _replace_brain_model

            voice.launch_command = _replace_brain_model(command, tag)
    elif spec.id == "tools_screen":
        # Same pair the cu-model setter writes: canonical + legacy key.
        config_writer.set_brain_provider_model("ollama", tool_model=tag, cu_model=tag)
        if provider is not None:
            provider.tool_model = tag
            provider.cu_model = tag
    elif spec.id == "deep":
        config_writer.set_brain_provider_model("ollama", deep_model=tag)
        if provider is not None:
            provider.deep_model = tag
    elif spec.id == "embedding":
        if not tag:
            raise ValueError("The embedding role needs a model name; the wiki cannot discover one.")
        wiki = getattr(cfg, "ultrawiki", None)
        if _str(getattr(wiki, "embedding_provider", "ollama")) != "ollama":
            config_writer.set_ultrawiki_slot("embedding_provider", "ollama")
            if wiki is not None:
                wiki.embedding_provider = "ollama"
        config_writer.set_ultrawiki_slot("embedding_model", tag)
        if wiki is not None:
            wiki.embedding_model = tag
    return {"role": spec.id, "model": tag, "config_key": spec.config_key}


def roles_using(cfg: Any, name: str) -> list[str]:
    """Writable role ids whose configured pick is ``name`` (``:latest`` tolerant)."""
    out: list[str] = []
    for spec in ROLES:
        if not spec.writable:
            continue
        current, _note = current_pick(cfg, spec.id)
        if current and same_model(current, name):
            out.append(spec.id)
    return out
