"""The model slots Ollama fills, read and written from ONE place.

Today the local brain is spread over five config keys the user never sees
side by side: the chat model, the voice model (the managed voice server's
brain, baked into its launch command), the tool/screen model, the deep model
and the wiki's embedding model — plus two read-only consumers (the quick ack,
dictation polish) that pin their own tag.
This module names every slot as a :class:`RoleSpec`, reads its current pick
from a loaded config, judges which INSTALLED downloads qualify for it (by the
capabilities ``/api/show`` declares) and recommends one, so the "Local
models" section can show the four rows with one button each instead of four
different pickers in four different views.

The recommendation is INSTALLED FIRST: the best qualifying download already
on the server — the largest one that fits this machine's memory, with the
role's preferred capabilities — and only when nothing installed qualifies
does the curated shortlist's pick (a download) stand in. A user with eleven
models on disk is told which of them to use, not sent to the catalogue.

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
    "FITS",
    "ROLES",
    "WRITABLE_ROLE_IDS",
    "Machine",
    "RoleSpec",
    "RoleState",
    "choices_for",
    "current_pick",
    "download_picks",
    "fit_for",
    "list_roles",
    "machine_from",
    "pick_installed",
    "qualifying_models",
    "role_spec",
    "roles_using",
    "set_role",
]

_GIB = 1024**3


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
    #: Installed picks at or under this size (GiB) are preferred — the roles
    #: where speed beats depth (a call must answer within a breath). ``None``
    #: = the largest model that fits wins.
    max_size_gb: float | None = None
    #: The smallest native context window that can do the job; a model whose
    #: manifest declares less is unfit, not merely slow. ``None`` = no floor.
    min_context_tokens: int | None = None
    #: Where the section shows the role: ``card`` (one of the peers the user
    #: talks to), ``row`` (set here, not part of the conversation) or
    #: ``footnote`` (follows another pick; read-only). The frontend iterates
    #: what the backend sends, so a new role lands somewhere by construction.
    layout: str = "card"


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
        # The live model calls every Jarvis tool itself (ADR-0035), so a
        # download without tool calls is a conversation without hands — a
        # gate, not a preference. Ollama declares ``tools`` per manifest.
        required=("completion", "tools"),
        recommended=("thinking",),
        pull_role="chat",
        # A call is judged by its first word, not its best one: the pick stays
        # in the class that answers within a breath, the chat slot takes the
        # depth.
        max_size_gb=6.0,
        # The voice brain runs a machine-sized window of 8k or more; a 4k
        # model would truncate the call's own history.
        min_context_tokens=8192,
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
        layout="row",
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
        layout="footnote",
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
        layout="footnote",
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
    #: The pick for this machine: the best qualifying INSTALLED download, or
    #: the shortlist's download when nothing installed qualifies (``""`` when
    #: neither has one).
    recommended: str
    #: One sentence saying why ``recommended`` is the pick (``""`` when none).
    recommended_reason: str = ""
    #: One sentence when the slot is not Ollama-backed right now.
    note: str = ""
    #: The voice brain's effective ``num_ctx`` on this machine (voice role only).
    context_tokens: int | None = None
    #: ``"automatic"`` (sized from memory) | ``"manual"`` (set in Tune) | ``""``.
    context_source: str = ""
    #: Every installed download judged for this job, inventory order:
    #: ``(tag, fit, reason)`` with ``fit`` from :data:`FITS`.
    choices: tuple[tuple[str, str, str], ...] = ()
    #: The verdict on ``current`` itself: a fit from :data:`FITS`, ``absent``
    #: when it is configured but not on the server, ``""`` when nothing is set.
    current_fit: str = ""
    current_reason: str = ""
    #: Downloads from the shortlist that would do this job on this machine
    #: and are not installed yet: ``(tag, label, size_gb, fit, note)``.
    downloads: tuple[tuple[str, str, float, str, str], ...] = ()


#: The verdicts :func:`fit_for` hands out, strongest first.
#:
#: ``fits`` — declares every required capability, sits inside the job's size
#: class and context floor, and fits this machine; ``slow`` — can do the job
#: but costs something the job feels (over the size class, or spilling past
#: the graphics memory); ``unfit`` — lacks a required capability, declares a
#: window under the floor, or cannot be held by this machine at all;
#: ``unknown`` — ``/api/show`` did not answer, so nothing can be said.
FITS: tuple[str, ...] = ("fits", "slow", "unfit", "unknown")

_CAPABILITY_WORDS: dict[str, str] = {
    "tools": "no tool calls",
    "vision": "no vision",
    "thinking": "no thinking",
    "embedding": "not an embedding model",
    "completion": "no text completion",
    "audio": "no audio",
}


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


@dataclass(frozen=True, slots=True)
class Machine:
    """The memory an installed pick is judged against.

    ``memory_gb`` is ``None`` when the host would not say; ``accelerator_gb``
    is ``0.0`` for "no graphics memory I can vouch for" — the same two
    answers :func:`ollama_pull.fit_verdict` already reads.
    """

    memory_gb: float | None = None
    accelerator_gb: float = 0.0


def _size_gb(info: OllamaModelInfo) -> float:
    return info.size_bytes / _GIB


def _context_label(tokens: int) -> str:
    return f"{tokens // 1024}k" if tokens >= 1024 else str(tokens)


def fit_for(
    spec: RoleSpec, info: OllamaModelInfo, machine: Machine | None = None
) -> tuple[str, str]:
    """``(fit, reason)`` — the ONE verdict on ``info`` doing ``spec``'s job.

    The card, the picker, the catalogue badge and the setup automation all
    read this; nothing else re-derives it (BUG-188's over-correction came
    from a second definition of "fits" in the client). The reason is one
    short English clause the picker shows beside the name — "" when it fits.
    """
    machine = machine or Machine()
    if not info.probed:
        return "unknown", "Jarvis could not read what this model can do."
    missing = [cap for cap in spec.required if cap not in info.capabilities]
    if missing:
        return "unfit", ", ".join(_CAPABILITY_WORDS.get(cap, f"no {cap}") for cap in missing)
    floor = spec.min_context_tokens
    if floor and info.context_length and info.context_length < floor:
        return (
            "unfit",
            f"{_context_label(info.context_length)} context — this job needs "
            f"{_context_label(floor)} or more",
        )
    size = _size_gb(info)
    verdict, _note = ollama_pull.fit_verdict(size, machine.memory_gb, machine.accelerator_gb)
    over_card = (
        machine.accelerator_gb > 0 and size + ollama_pull._OVERHEAD_GB > machine.accelerator_gb
    )
    if verdict == "tight" and spec.max_size_gb is not None:
        # A job that must answer within a breath cannot wait for the CPU.
        if over_card:
            return (
                "unfit",
                f"{size:.1f} GB — over the {machine.accelerator_gb:.0f} GB of graphics memory",
            )
        return "unfit", f"{size:.1f} GB — too tight on this machine's memory for a call"
    if spec.max_size_gb is not None and size > spec.max_size_gb:
        return "slow", f"{size:.1f} GB — over {spec.max_size_gb:g} GB, slower to answer"
    if verdict == "tight":
        if over_card:
            return "slow", (
                f"{size:.1f} GB — bigger than the {machine.accelerator_gb:.0f} GB of "
                "graphics memory, runs partly on the processor"
            )
        return "slow", f"{size:.1f} GB — tight on this machine's memory"
    return "fits", ""


def choices_for(
    spec: RoleSpec, models: list[OllamaModelInfo], machine: Machine | None = None
) -> tuple[tuple[str, str, str], ...]:
    """Every installed download with its verdict for ``spec``, inventory order."""
    return tuple((info.name, *fit_for(spec, info, machine)) for info in models)


def qualifying_models(
    spec: RoleSpec, models: list[OllamaModelInfo], machine: Machine | None = None
) -> tuple[str, ...]:
    """Installed tags that can do ``spec``'s job (``fits`` or ``slow``).

    A row whose ``/api/show`` failed (``probed=False``) is left out: "unknown"
    must not read as "qualifies", or a screen reader without vision gets
    assigned and fails on the first screenshot.
    """
    return tuple(
        name for name, fit, _reason in choices_for(spec, models, machine) if fit in ("fits", "slow")
    )


def _has_preferred(spec: RoleSpec, info: OllamaModelInfo) -> bool:
    return all(cap in info.capabilities for cap in spec.recommended)


def _preferred_clause(spec: RoleSpec) -> str:
    return f" with {' and '.join(spec.recommended)}" if spec.recommended else ""


def pick_installed(
    spec: RoleSpec, models: list[OllamaModelInfo], machine: Machine
) -> tuple[str, str]:
    """``(tag, reason)`` — the best INSTALLED download for ``spec``, or ``("", "")``.

    Bigger is better right up to the point where it stops fitting, so the
    pick is the largest qualifying model this machine runs comfortably; a
    model that also declares the role's preferred capabilities (tools for a
    chat, thinking for deep work) beats a larger one without them, because
    a chat model that cannot call tools is the wrong model however smart.
    When nothing fits comfortably the SMALLEST one is named (a starting
    point rather than four rows flagged "tight"); when the memory could not
    be read at all the largest is — the user installed it, the box presumably
    runs it. A role with ``max_size_gb`` prefers the class under that size.
    The reason is one English sentence the row shows beside the button.
    """
    candidates = [info for info in models if fit_for(spec, info, machine)[0] in ("fits", "slow")]
    if not candidates:
        return "", ""
    preferred = _preferred_clause(spec)

    if spec.max_size_gb is not None:
        fast = [m for m in candidates if _size_gb(m) <= spec.max_size_gb]
        if fast:
            best = max(fast, key=lambda m: (_has_preferred(spec, m), _size_gb(m)))
            return best.name, (
                f"Largest installed model{preferred} under {spec.max_size_gb:g} GB — "
                "this job needs fast answers more than depth."
            )
        best = min(candidates, key=lambda m: (not _has_preferred(spec, m), _size_gb(m)))
        return best.name, (
            f"Smallest installed model{preferred}; nothing under {spec.max_size_gb:g} GB "
            "is installed, and this job needs fast answers."
        )

    verdicts = {
        m.name: ollama_pull.fit_verdict(_size_gb(m), machine.memory_gb, machine.accelerator_gb)[0]
        for m in candidates
    }
    comfortable = [m for m in candidates if verdicts[m.name] == "comfortable"]
    if comfortable:
        best = max(comfortable, key=lambda m: (_has_preferred(spec, m), _size_gb(m)))
        where = (
            f"the {machine.accelerator_gb:.0f} GB of graphics memory"
            if machine.accelerator_gb > 0 and _size_gb(best) + 2.0 <= machine.accelerator_gb
            else f"{machine.memory_gb:g} GB of memory"
        )
        return best.name, f"Largest installed model{preferred} that fits in {where}."
    if all(v == "unknown" for v in verdicts.values()):
        best = max(candidates, key=lambda m: (_has_preferred(spec, m), _size_gb(m)))
        return best.name, (
            f"Largest installed model{preferred}; this machine's memory could not be read."
        )
    best = min(candidates, key=lambda m: (not _has_preferred(spec, m), _size_gb(m)))
    return best.name, (
        f"Smallest installed model{preferred} — every option is tight on this machine."
    )


def _row_serves(spec: RoleSpec, row: dict[str, Any]) -> bool:
    """Whether a shortlist row could do ``spec``'s job at all.

    The shortlist knows two capabilities per entry (``tools``, ``vision``)
    and its role; the size class is the same gate the installed picks get,
    so the voice slot is never sent a 25 GB download for a 6 GB job.
    """
    role_ok = row.get("role") == spec.pull_role or ollama_pull._serves_vision(spec.pull_role, row)
    if not role_ok:
        return False
    # Curated entries declare ``tools`` (default True in the shortlist) and
    # ``vision``; a row without the key is a chat-class model that has them.
    if "tools" in spec.required and not row.get("tools", True):
        return False
    if "vision" in spec.required and not row.get("vision", False):
        return False
    if spec.max_size_gb is not None:
        size = float(row.get("size_gb") or 0)
        if size > spec.max_size_gb:
            return False
    return row.get("fit") != "tight" or spec.max_size_gb is None


def _recommended_for(spec: RoleSpec, rows: list[dict[str, Any]]) -> str:
    """The shortlist's pick for ``spec`` on this machine — the fallback when
    :func:`pick_installed` found nothing installed that qualifies.

    :func:`ollama_pull._pick_recommended` marks nothing for a role that has
    a curated model installed already — the user has chosen. Then the
    largest INSTALLED curated model of that role is named instead, so the
    "Use recommended" button always has something honest to assign. Either
    way the entry has to pass :func:`_row_serves` — the job's own gates.
    """
    for row in rows:
        if spec.pull_role in (row.get("recommended_for") or []) and _row_serves(spec, row):
            return str(row["id"])
    serving = [row for row in rows if _row_serves(spec, row)]
    installed = [row for row in serving if row.get("installed")]
    if installed:
        return str(max(installed, key=lambda r: float(r.get("size_gb") or 0))["id"])
    comfortable = [row for row in serving if row.get("fit") == "comfortable"]
    if comfortable:
        return str(max(comfortable, key=lambda r: float(r.get("size_gb") or 0))["id"])
    return ""


def download_picks(
    spec: RoleSpec, rows: list[dict[str, Any]], *, limit: int = 3
) -> tuple[tuple[str, str, float, str, str], ...]:
    """Shortlist entries that would do ``spec``'s job here and are not on disk.

    What the picker offers under "download a pick": the curated, reviewed
    entries (``ollama_pull.RECOMMENDED_MODELS``, dated by
    ``CURATED_REVIEWED_ON``) that pass the job's gates, largest comfortable
    first, then the tight ones — never more than ``limit``.
    """
    serving = [row for row in rows if _row_serves(spec, row) and not row.get("installed")]
    order = {"comfortable": 0, "unknown": 1, "tight": 2}
    serving.sort(key=lambda r: (order.get(str(r.get("fit")), 3), -float(r.get("size_gb") or 0)))
    return tuple(
        (
            str(row["id"]),
            str(row.get("label") or row["id"]),
            float(row.get("size_gb") or 0),
            str(row.get("fit") or "unknown"),
            str(row.get("fit_note") or ""),
        )
        for row in serving[:limit]
    )


def machine_from(recommended: dict[str, Any] | None) -> Machine:
    """The :class:`Machine` a shortlist payload was judged against."""
    if not isinstance(recommended, dict):
        return Machine()
    memory = recommended.get("memory_gb")
    accel = recommended.get("accelerator_gb")
    return Machine(
        memory_gb=float(memory) if isinstance(memory, (int, float)) else None,
        accelerator_gb=float(accel) if isinstance(accel, (int, float)) else 0.0,
    )


async def list_roles(
    root: str,
    cfg: Any,
    *,
    transport: Any = None,
    models: list[OllamaModelInfo] | None = None,
    shortlist: list[dict[str, Any]] | None = None,
    machine: Machine | None = None,
) -> tuple[list[RoleState], str | None]:
    """Every role with its pick, what qualifies, and the recommendation.

    Returns ``(states, error)``; ``error`` is the server's English sentence
    when ``/api/tags`` did not answer — the states are still complete (with
    empty ``qualifying`` and ``installed=False``) so the rows render.

    ``models``, ``shortlist`` and ``machine`` let a caller that already holds
    the shared inventory snapshot and the recommendations pass them in; any
    left ``None`` is fetched here (the snapshot is shared, so this costs no
    extra sweep within its window; the shortlist answers the machine too).
    """
    error: str | None = None
    if models is None:
        try:
            snapshot = await inventory.cached_snapshot(root, transport=transport)
            models = list(snapshot.models)
        except OllamaServerError as exc:
            log.info("ollama-roles: inventory unavailable at %s: %s", root, exc)
            models = []
            error = str(exc)
    if shortlist is None:
        try:
            recommended = await ollama_pull.recommendations()
            shortlist = recommended.get("models") or []
            if machine is None:
                machine = machine_from(recommended)
        except Exception as exc:  # noqa: BLE001 — the shortlist is advisory, the rows are not
            log.warning("ollama-roles: shortlist unavailable: %s", exc)
            shortlist = []
    if machine is None:
        machine = Machine()

    states: list[RoleState] = []
    for spec in ROLES:
        current, note = current_pick(cfg, spec.id)
        choices = choices_for(spec, models, machine)
        qualifying = tuple(name for name, fit, _r in choices if fit in ("fits", "slow"))
        context_tokens: int | None = None
        context_source = ""
        if spec.id == "voice" and current and error is None:
            context_tokens, context_source = await voice_context(cfg, current)
        recommended_tag, reason = pick_installed(spec, models, machine)
        if not recommended_tag:
            recommended_tag = _recommended_for(spec, shortlist)
            if recommended_tag:
                reason = "Nothing installed fits this job yet; this download is the pick."
        installed = bool(current) and any(same_model(m.name, current) for m in models)
        current_fit, current_reason = "", ""
        if current:
            verdict = next((c for c in choices if same_model(c[0], current)), None)
            if verdict is not None:
                current_fit, current_reason = verdict[1], verdict[2]
            elif error is not None:
                current_fit, current_reason = "unknown", "The server did not answer."
            else:
                current_fit, current_reason = "absent", "Not downloaded."
        states.append(
            RoleState(
                spec=spec,
                current=current,
                installed=installed,
                qualifying=qualifying,
                recommended=recommended_tag,
                recommended_reason=reason,
                note=note,
                context_tokens=context_tokens,
                context_source=context_source,
                choices=choices,
                current_fit=current_fit,
                current_reason=current_reason,
                downloads=download_picks(spec, shortlist) if spec.writable else (),
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


def set_role(role_id: str, model: str, *, cfg: Any = None) -> dict[str, Any]:
    """Persist ``model`` as the pick of ``role_id``; ``""`` = discovery.

    Dispatches to the writer the provider card already uses for that key, so
    the TOML and the drift baseline agree; ``cfg`` (the live config object) is
    updated in place when given, so the next read answers the new value
    without a restart. Returns ``{"role", "model", "config_key",
    "drift_guarded"}`` — ``drift_guarded`` is ``False`` when the writer could
    not confirm the drift baseline (config-soll.json) followed the pick, in  # i18n-allow
    which case the drift guard may revert it and the caller should say so.

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
    # A writer that returns no receipt (older fakes, other writers) counts as
    # guarded: only an explicit ``baseline_ok=False`` turns the flag off.
    receipt: Any = None
    if spec.id == "chat":
        receipt = config_writer.set_brain_provider_model("ollama", model=tag)
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
        # the persisted command, never a bring-your-own command. It answers
        # False when nothing reached jarvis.toml — a missing block, a command
        # without the flag, or the same value — and only the last of those
        # is a no-op the caller may call a success (BUG class: a pick that
        # reads back from memory while the file still holds the old one).
        current_tag, _note = _voice_pick(cfg)
        written = config_writer.update_local_realtime_launch_model(tag)
        if not written and not same_model(current_tag, tag):
            raise ValueError(
                "The voice server's launch command in jarvis.toml could not be "
                "updated — it names no --model_name, or the local-realtime block "
                "is missing. Reinstall the managed voice server from the Voice "
                "settings, then pick again."
            )
        if voice is not None:
            from jarvis.realtime.local_server.supervisor import _replace_brain_model

            voice.launch_command = _replace_brain_model(command, tag)
    elif spec.id == "tools_screen":
        # Same pair the cu-model setter writes: canonical + legacy key.
        receipt = config_writer.set_brain_provider_model("ollama", tool_model=tag, cu_model=tag)
        if provider is not None:
            provider.tool_model = tag
            provider.cu_model = tag
    elif spec.id == "deep":
        receipt = config_writer.set_brain_provider_model("ollama", deep_model=tag)
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
    return {
        "role": spec.id,
        "model": tag,
        "config_key": spec.config_key,
        "drift_guarded": bool(getattr(receipt, "baseline_ok", True)),
    }


def roles_using(cfg: Any, name: str, *, include_readonly: bool = False) -> list[str]:
    """Role ids whose configured pick is ``name`` (``:latest`` tolerant).

    Writable roles by default; ``include_readonly`` adds the consumers that
    pin their own tag elsewhere (the quick ack, dictation polish), so a
    delete can refuse to pull the model out from under them.
    """
    out: list[str] = []
    for spec in ROLES:
        if not spec.writable and not include_readonly:
            continue
        current, _note = current_pick(cfg, spec.id)
        if current and same_model(current, name):
            out.append(spec.id)
    return out
