"""The setup assistant's hands: Jarvis tools over the local-models functions.

Every tool here wraps a function the Local models section already calls
(``ollama_runtime``, ``ollama_inventory``, ``ollama_roles``, ``ollama_pull``,
``ollama_library``, the managed voice server) as a
:class:`jarvis.core.protocols.Tool`, the way :mod:`jarvis.agent_chat.folder_tools`
wraps the chat's folder tools — so every call goes through ``ToolExecutor``
with its risk tiers, blacklist, audit log and approval card (AP-3), on the
``local-models`` chat surface exactly as on the voice path.

Tiers: reads are ``safe``; everything that installs, starts, stops, downloads
or writes config is ``ask`` (the panel auto-answers the cards of a confirmed
proposal, the executor still asks); ``lm_unload`` is ``monitor``. There is
deliberately **no delete tool** and nothing that writes ``brain.primary`` —
the provider lock stays with the user's click.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

from jarvis.core.protocols import ExecutionContext, RiskTier, Tool, ToolResult

log = logging.getLogger(__name__)

__all__ = [
    "ACTION_TOOLS",
    "ALLOWED_TIERS",
    "LocalModelsTool",
    "READ_TOOLS",
    "TOOL_PREFIX",
    "build_tools",
    "tool_specs",
]

TOOL_PREFIX: Final[str] = "lm_"

#: Every tier a local-models tool may carry. ``block`` is not on the list on
#: purpose: a tool that must never run is a tool that must not exist here.
ALLOWED_TIERS: Final[tuple[RiskTier, ...]] = ("safe", "monitor", "ask")

Handler = Callable[[dict[str, Any], "_Env"], Awaitable[Any]]

#: How long ``lm_pull`` follows a download before handing back a "still
#: running" answer (the panel keeps polling ``lm_pull_status``).
PULL_FOLLOW_MAX_S = 1800.0
PULL_POLL_S = 1.0


@dataclass(frozen=True, slots=True)
class _Env:
    """What every handler needs: the live config, the server root, the seams."""

    cfg: Any
    root: str
    transport: Any = None
    search_fn: Callable[[str], Any] | None = None


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]
    risk_tier: RiskTier
    handler: Handler
    summary: Callable[[dict[str, Any]], str] | None = None


class LocalModelsTool:
    """One spec as a Jarvis ``Tool`` bound to a config + server root."""

    def __init__(self, spec: ToolSpec, env: _Env) -> None:
        self.name: str = spec.name
        self.description: str = spec.description
        self.schema: dict[str, Any] = dict(spec.schema)
        self.risk_tier: RiskTier = spec.risk_tier
        self._spec = spec
        self._env = env

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        try:
            output = await self._spec.handler(dict(args or {}), self._env)
        except Exception as exc:  # noqa: BLE001 — the model reads the failure, the log keeps it
            log.info("local-models tool %s failed: %s", self.name, exc, exc_info=True)
            return ToolResult(success=False, output=str(exc), error=f"{type(exc).__name__}: {exc}")
        return ToolResult(success=True, output=output)

    def describe_args(self, args: dict[str, Any]) -> dict[str, str]:
        """The one-line summary the approval card shows."""
        if self._spec.summary is None:
            return {}
        try:
            text = self._spec.summary(dict(args or {}))
        except Exception:  # noqa: BLE001 — a summary must never break the card
            log.debug("local-models tool %s: summary failed", self.name, exc_info=True)
            return {}
        return {"summary": text} if text else {}

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"LocalModelsTool({self.name!r}, tier={self.risk_tier!r})"


# ── schema helpers ────────────────────────────────────────────────────────


def _obj(props: dict[str, Any] | None = None, required: tuple[str, ...] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": dict(props or {})}
    if required:
        schema["required"] = list(required)
    return schema


_MODEL = {"type": "string", "description": "An Ollama model tag, e.g. qwen3.5:4b."}
_ROLE = {
    "type": "string",
    "description": "A writable role id: chat, voice, tools_screen, deep or embedding.",
}


def _str(args: dict[str, Any], key: str) -> str:
    return str(args.get(key) or "").strip()


# ── read handlers (safe) ──────────────────────────────────────────────────


async def _hardware(_args: dict[str, Any], _env: _Env) -> dict[str, Any]:
    from jarvis.brain.ollama_pull import accelerator_gb, total_memory_gb

    (accel, source), ram = await asyncio.gather(
        asyncio.to_thread(accelerator_gb), asyncio.to_thread(total_memory_gb)
    )
    import os

    return {
        "os": platform.system(),
        "os_version": platform.release(),
        "machine": platform.machine(),
        "cpu_threads": os.cpu_count() or 0,
        "ram_gb": ram,
        "accelerator_gb": round(accel, 1),
        "accelerator_source": source,
    }


async def _server_status(_args: dict[str, Any], env: _Env) -> dict[str, Any]:
    from jarvis.brain import ollama_runtime

    status, probe = await asyncio.gather(
        asyncio.to_thread(ollama_runtime.runtime_status),
        ollama_runtime.probe_host(env.root, transport=env.transport),
    )
    out: dict[str, Any] = {
        "runtime": status,
        "probe": probe,
        "install": ollama_runtime.install_snapshot(),
        "base_url": env.root,
    }
    command = _voice_command(env.cfg)
    if "--model_name" in command:
        from jarvis.realtime.local_server import install as voice_install
        from jarvis.realtime.local_server.preflight import report_payload, run_preflight

        voice_status = await asyncio.to_thread(voice_install.server_status)
        preflight = await asyncio.to_thread(run_preflight, voice_install.install_root())
        out["voice"] = {
            "launch_command": command,
            "status": voice_status,
            "preflight": report_payload(preflight),
        }
    else:
        out["voice"] = {"launch_command": command, "status": None, "preflight": None}
    return out


async def _inventory(_args: dict[str, Any], env: _Env) -> dict[str, Any]:
    from jarvis.brain import ollama_inventory as inventory

    models, running = await asyncio.gather(
        inventory.list_models(env.root, transport=env.transport),
        inventory.running_models(env.root, transport=env.transport),
    )
    return {
        "models": [
            {
                "name": m.name,
                "size_gb": round(m.size_bytes / (1024**3), 2),
                "family": m.family,
                "parameter_size": m.parameter_size,
                "quantization": m.quantization_level,
                "context_length": m.context_length,
                "capabilities": list(m.capabilities),
                "modified_at": m.modified_at,
            }
            for m in models
        ],
        "running": [
            {
                "name": r.name,
                "size_vram_gb": round(r.size_vram_bytes / (1024**3), 2),
                "context_length": r.context_length,
                "expires_at": r.expires_at,
            }
            for r in running
        ],
        "disk_gb": round(sum(m.size_bytes for m in models) / (1024**3), 1),
    }


def _role_row(state: Any) -> dict[str, Any]:
    spec = state.spec
    return {
        "role": spec.id,
        "config_key": spec.config_key,
        "writable": spec.writable,
        "required_capabilities": list(spec.required),
        "current": state.current,
        "installed": state.installed,
        "qualifying": list(state.qualifying),
        "recommended": state.recommended,
        "note": state.note,
        "context_tokens": state.context_tokens,
    }


async def _roles(_args: dict[str, Any], env: _Env) -> dict[str, Any]:
    from jarvis.brain.ollama_roles import list_roles

    states, error = await list_roles(env.root, env.cfg, transport=env.transport)
    return {"roles": [_role_row(s) for s in states], "error": error}


async def _recommendations(_args: dict[str, Any], _env: _Env) -> dict[str, Any]:
    import datetime as _dt

    from jarvis.brain.ollama_pull import recommendations
    from jarvis.local_models import benchmarks

    payload = await recommendations()
    table = benchmarks.load_cached()
    today = _dt.datetime.now(_dt.UTC).date()
    for row in payload.get("models") or []:
        row["label"] = benchmarks.label_for(str(row.get("id") or ""), table, today)
    payload["benchmark_source"] = table.source if table is not None else "curated"
    return payload


async def _catalog_search(args: dict[str, Any], _env: _Env) -> dict[str, Any]:
    from jarvis.brain.ollama_library import search_library

    return await search_library(
        _str(args, "query"),
        sort=_str(args, "sort") or "popular",
        capability=_str(args, "capability") or None,
        limit=int(args.get("limit") or 20),
    )


async def _catalog_tags(args: dict[str, Any], _env: _Env) -> dict[str, Any]:
    from jarvis.brain.ollama_library import library_tags

    return await library_tags(_str(args, "model"))


async def _hf_search(args: dict[str, Any], _env: _Env) -> dict[str, Any]:
    from jarvis.brain import hf_gguf  # lazy: only an install that switched HF on imports it

    return await hf_gguf.search(
        _str(args, "query"),
        sort=_str(args, "sort") or "downloads",
        limit=int(args.get("limit") or 20),
    )


async def _benchmarks(args: dict[str, Any], env: _Env) -> dict[str, Any]:
    from jarvis.local_models import benchmarks

    table: benchmarks.BenchmarkTable | None
    if bool(args.get("refresh")) and env.search_fn is not None:
        table = await benchmarks.refresh_benchmarks(env.search_fn)
    else:
        table = benchmarks.load_cached()
        if table is None:
            note = (
                "No benchmark cache yet; pass refresh=true to build one."
                if env.search_fn is not None
                else "No benchmark cache and no web search on this surface; curated list only."
            )
            table = benchmarks.curated_only(note)
    return table.to_payload()


async def _pull_status(args: dict[str, Any], _env: _Env) -> dict[str, Any]:
    from jarvis.brain.ollama_pull import pull_status

    return await pull_status(_str(args, "model"))


async def _server_log(args: dict[str, Any], _env: _Env) -> dict[str, Any]:
    from jarvis.brain.ollama_runtime import tail_log

    lines = max(1, min(int(args.get("lines") or 40), 200))
    return {"lines": await asyncio.to_thread(tail_log, lines)}


async def _env_guide(_args: dict[str, Any], _env: _Env) -> dict[str, Any]:
    from jarvis.brain.ollama_runtime import env_guide

    return {"entries": env_guide()}


async def _suggest(model: str, env: _Env) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    from jarvis.brain import ollama_inventory as inventory
    from jarvis.brain import ollama_profiles
    from jarvis.brain.ollama_pull import accelerator_gb, total_memory_gb

    info = await inventory.get_model(env.root, model, transport=env.transport)
    (accel, source), ram = await asyncio.gather(
        asyncio.to_thread(accelerator_gb), asyncio.to_thread(total_memory_gb)
    )
    size_gb = round(info.size_bytes / (1024**3), 2)
    opts, reasons = ollama_profiles.suggest_options(
        size_gb=size_gb,
        native_context=info.context_length,
        accelerator_gb=accel,
        source=source,
        ram_gb=ram,
    )
    facts = {
        "model": info.name,
        "size_gb": size_gb,
        "native_context": info.context_length,
        "accelerator_gb": round(accel, 1),
        "ram_gb": ram,
    }
    return opts.model_dump(exclude_none=True), reasons, facts


async def _suggested_options(args: dict[str, Any], env: _Env) -> dict[str, Any]:
    options, reasons, facts = await _suggest(_str(args, "model"), env)
    return {**facts, "options": options, "reasons": reasons}


async def _test_plan(args: dict[str, Any], env: _Env) -> dict[str, Any]:
    from jarvis.local_models.assistant_test import run_setup_test

    raw = args.get("roles")
    roles = tuple(str(r) for r in raw) if isinstance(raw, list) and raw else None
    report = await run_setup_test(env.cfg, roles, transport=env.transport)
    return report.to_payload()


# ── action handlers (ask / monitor) ───────────────────────────────────────


async def _install_ollama(_args: dict[str, Any], _env: _Env) -> dict[str, Any]:
    from jarvis.brain import ollama_runtime

    ok, detail = await asyncio.to_thread(ollama_runtime.start_install)
    return {"started": ok, "detail": detail, "install": ollama_runtime.install_snapshot()}


async def _start_server(_args: dict[str, Any], _env: _Env) -> dict[str, Any]:
    from jarvis.brain.ollama_runtime import start_server

    ok, detail = await asyncio.to_thread(start_server)
    return {"ok": ok, "detail": detail}


async def _stop_server(_args: dict[str, Any], _env: _Env) -> dict[str, Any]:
    from jarvis.brain.ollama_runtime import stop_server

    ok, detail = await asyncio.to_thread(stop_server)
    return {"ok": ok, "detail": detail}


async def _pull(args: dict[str, Any], _env: _Env) -> dict[str, Any]:
    """Start (or join) a download and follow it, reporting progress lines."""
    from jarvis.brain.ollama_pull import pull_status, start_pull

    model = _str(args, "model")
    state = await start_pull(model)
    lines: list[str] = [str(state.get("message") or "")]
    if state.get("state") != "running":
        return {**state, "progress": lines}
    max_wait = float(args.get("max_wait_s") or PULL_FOLLOW_MAX_S)
    started = time.monotonic()
    last_pct = -1
    while time.monotonic() - started < max_wait:
        await asyncio.sleep(PULL_POLL_S)
        state = await pull_status(model)
        total = int(state.get("total") or 0)
        done = int(state.get("completed") or 0)
        pct = int(done * 100 / total) if total else -1
        if pct >= 0 and pct // 10 > last_pct // 10:
            last_pct = pct
            lines.append(f"{model}: {pct}% ({done / 1e9:.1f} of {total / 1e9:.1f} GB)")
        if state.get("state") != "running":
            lines.append(str(state.get("message") or ""))
            return {**state, "progress": lines}
    lines.append(f"{model}: still downloading — check lm_pull_status for progress.")
    return {**state, "progress": lines}


async def _set_role(args: dict[str, Any], env: _Env) -> dict[str, Any]:
    from jarvis.brain.ollama_roles import set_role

    result = dict(set_role(_str(args, "role"), _str(args, "model"), cfg=env.cfg))
    if result.get("drift_guarded") is False:
        result["warning"] = (
            "The pick was written, but the drift baseline could not be updated — "
            "the drift guard may revert it within minutes. Check the config baseline."
        )
    return result


async def _set_model_options(args: dict[str, Any], env: _Env) -> dict[str, Any]:
    """Write ONLY keys the suggestion for this model returned — nothing else."""
    from jarvis.core import config_writer

    model = _str(args, "model")
    wanted = args.get("options")
    if not isinstance(wanted, dict) or not wanted:
        raise ValueError("options must be a non-empty object.")
    suggested, _reasons, _facts = await _suggest(model, env)
    allowed = set(suggested)
    rejected = sorted(k for k in wanted if k not in allowed)
    if rejected:
        raise ValueError(
            f"Only the suggested keys may be set for {model} ({', '.join(sorted(allowed))}); "
            f"refused: {', '.join(rejected)}."
        )
    written = await asyncio.to_thread(
        config_writer.set_ollama_model_options, model, {k: wanted[k] for k in wanted}
    )
    provider = _ollama_provider(env.cfg)
    models = getattr(provider, "models", None)
    if isinstance(models, dict):
        from jarvis.core.config import OllamaModelOptions

        try:
            models[model] = OllamaModelOptions.model_validate(written)
        except (TypeError, ValueError):
            log.info("local-models: in-memory options for %s not updated", model, exc_info=True)
    return {"model": model, "written": written}


async def _install_voice_server(args: dict[str, Any], _env: _Env) -> dict[str, Any]:
    from jarvis.realtime.local_server import install as voice_install

    ok, detail = await asyncio.to_thread(
        lambda: voice_install.start_install(
            confirmed_brain="ollama",
            brain_model=_str(args, "brain_model"),
            voice_model=_str(args, "voice_model"),
        )
    )
    return {"started": ok, "detail": detail, "install": voice_install.snapshot()}


async def _apply_voice_stack(args: dict[str, Any], env: _Env) -> dict[str, Any]:
    from jarvis.realtime.local_server.configure import ManagedSetupError, apply_and_test_stack

    command = _voice_command(env.cfg)
    if "--model_name" not in command:
        raise ValueError("Install the managed voice server first (lm_install_voice_server).")
    try:
        result = await apply_and_test_stack(
            base_url=_voice_base_url(env.cfg),
            current_command=command,
            brain_model=_str(args, "brain_model"),
            voice_model=_str(args, "voice_model"),
            language=_str(args, "language") or "en",
        )
    except ManagedSetupError as exc:
        # The function rolled back (or says it could not) — report, never hide.
        return {"ok": False, "rolled_back": "restored" in str(exc), "detail": str(exc)}
    voice = _voice_provider(env.cfg)
    if voice is not None and result.get("launch_command"):
        voice.launch_command = str(result["launch_command"])
    return dict(result)


async def _unload(args: dict[str, Any], env: _Env) -> dict[str, Any]:
    from jarvis.brain import ollama_inventory as inventory

    model = _str(args, "model")
    await inventory.unload_model(env.root, model, transport=env.transport)
    return {"model": model, "unloaded": True}


# ── config readers ────────────────────────────────────────────────────────


def _ollama_provider(cfg: Any) -> Any:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    return providers.get("ollama") if isinstance(providers, dict) else None


def _voice_provider(cfg: Any) -> Any:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    return providers.get("local-realtime") if isinstance(providers, dict) else None


def _voice_command(cfg: Any) -> str:
    return str(getattr(_voice_provider(cfg), "launch_command", "") or "")


def _voice_base_url(cfg: Any) -> str:
    return str(getattr(_voice_provider(cfg), "base_url", "") or "") or "http://127.0.0.1:8765"


# ── the specs ─────────────────────────────────────────────────────────────


def _s(
    name: str,
    description: str,
    schema: dict[str, Any],
    tier: RiskTier,
    handler: Handler,
    summary: Callable[[dict[str, Any]], str] | None = None,
) -> ToolSpec:
    return ToolSpec(name, description, schema, tier, handler, summary)


READ_TOOLS: Final[tuple[ToolSpec, ...]] = (
    _s(
        "lm_hardware",
        "This machine's memory: RAM, usable graphics memory and where that figure comes from.",
        _obj(),
        "safe",
        _hardware,
    ),
    _s(
        "lm_server_status",
        "Is Ollama installed and running, which version, and the state of the managed voice "
        "server (status + preflight) when one exists.",
        _obj(),
        "safe",
        _server_status,
    ),
    _s(
        "lm_inventory",
        "Every downloaded model with size, capabilities and context length, plus what is "
        "loaded in memory right now.",
        _obj(),
        "safe",
        _inventory,
    ),
    _s(
        "lm_roles",
        "The five roles (chat, voice, tools_screen, deep, embedding): current pick, its config "
        "key, whether it is installed, which downloads qualify and the recommendation.",
        _obj(),
        "safe",
        _roles,
    ),
    _s(
        "lm_recommendations",
        "The curated shortlist ranked for this machine with fit verdicts and the "
        "proven / new_little_tested / stale label per entry.",
        _obj(),
        "safe",
        _recommendations,
    ),
    _s(
        "lm_catalog_search",
        "Search the public Ollama library (ollama.com).",
        _obj(
            {
                "query": {"type": "string"},
                "sort": {"type": "string", "enum": ["popular", "newest"]},
                "capability": {
                    "type": "string",
                    "enum": ["tools", "vision", "embedding", "thinking"],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            }
        ),
        "safe",
        _catalog_search,
    ),
    _s(
        "lm_catalog_tags",
        "Every tag of one library model with its size and fit verdict for this machine.",
        _obj({"model": _MODEL}, ("model",)),
        "safe",
        _catalog_tags,
    ),
    _s(
        "lm_hf_search",
        "Search GGUF repositories on Hugging Face (only when the user enabled it).",
        _obj(
            {
                "query": {"type": "string"},
                "sort": {"type": "string", "enum": ["downloads", "lastModified", "trendingScore"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            }
        ),
        "safe",
        _hf_search,
    ),
    _s(
        "lm_benchmarks",
        "The benchmark table behind the proven / new labels (cached seven days). "
        "refresh=true rebuilds it from the web inside a guided run.",
        _obj({"refresh": {"type": "boolean"}}),
        "safe",
        _benchmarks,
    ),
    _s(
        "lm_pull_status",
        "Progress of one download.",
        _obj({"model": _MODEL}, ("model",)),
        "safe",
        _pull_status,
    ),
    _s(
        "lm_server_log",
        "The last lines of the Ollama server log Jarvis writes.",
        _obj({"lines": {"type": "integer", "minimum": 1, "maximum": 200}}),
        "safe",
        _server_log,
    ),
    _s(
        "lm_env_guide",
        "The OLLAMA_* environment variables that matter (models dir, host, keep-alive) and how "
        "to set each on this OS — advice only, nothing is applied.",
        _obj(),
        "safe",
        _env_guide,
    ),
    _s(
        "lm_suggested_options",
        "An advisory option profile (num_ctx, num_gpu, …) for one installed model on this "
        "machine, with one reason per knob. Only these keys may be set with "
        "lm_set_model_options.",
        _obj({"model": _MODEL}, ("model",)),
        "safe",
        _suggested_options,
    ),
    _s(
        "lm_test_plan",
        "Run the end-to-end test: server probe, one real generation per configured role, an "
        "embedding round trip, the voice probe when a managed voice server exists. Returns a "
        "per-role table with ok / error statuses.",
        _obj(
            {
                "roles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Subset of chat, voice, tools_screen, deep, embedding.",
                }
            }
        ),
        "safe",
        _test_plan,
    ),
)

ACTION_TOOLS: Final[tuple[ToolSpec, ...]] = (
    _s(
        "lm_install_ollama",
        "Install Ollama on this machine (winget / installer / install.sh). Starts in the "
        "background; follow it with lm_server_status.",
        _obj(),
        "ask",
        _install_ollama,
        lambda _a: "Install Ollama",
    ),
    _s(
        "lm_start_server",
        "Start the Ollama server.",
        _obj(),
        "ask",
        _start_server,
        lambda _a: "Start the Ollama server",
    ),
    _s(
        "lm_stop_server",
        "Stop the Ollama server Jarvis started (never one started elsewhere).",
        _obj(),
        "ask",
        _stop_server,
        lambda _a: "Stop the Ollama server",
    ),
    _s(
        "lm_pull",
        "Download a model and follow the download, returning progress lines. Long downloads "
        "hand back 'still downloading'; poll lm_pull_status then.",
        _obj({"model": _MODEL, "max_wait_s": {"type": "number"}}, ("model",)),
        "ask",
        _pull,
        lambda a: f"Download {a.get('model', '')}",
    ),
    _s(
        "lm_set_role",
        "Persist which installed model serves a role. Reports drift_guarded=false when the "
        "config baseline could not be updated (the pick may then be reverted).",
        _obj({"role": _ROLE, "model": _MODEL}, ("role", "model")),
        "ask",
        _set_role,
        lambda a: f"Use {a.get('model', '')} for {a.get('role', '')}",
    ),
    _s(
        "lm_set_model_options",
        "Persist per-model options — only keys lm_suggested_options returned for that model.",
        _obj(
            {
                "model": _MODEL,
                "options": {"type": "object", "description": "num_ctx, num_gpu, … values."},
            },
            ("model", "options"),
        ),
        "ask",
        _set_model_options,
        lambda a: f"Tune {a.get('model', '')}",
    ),
    _s(
        "lm_install_voice_server",
        "Install the Jarvis-managed local voice server (background; follow with lm_server_status).",
        _obj({"brain_model": _MODEL, "voice_model": {"type": "string"}}),
        "ask",
        _install_voice_server,
        lambda _a: "Install the local voice server",
    ),
    _s(
        "lm_apply_voice_stack",
        "Switch the managed voice server to a brain + voice model pair, run the voice test and "
        "persist only on success; reports a rollback otherwise.",
        _obj(
            {
                "brain_model": _MODEL,
                "voice_model": {"type": "string", "description": "A voice profile id."},
                "language": {"type": "string"},
            },
            ("brain_model", "voice_model"),
        ),
        "ask",
        _apply_voice_stack,
        lambda a: f"Voice stack: {a.get('brain_model', '')} + {a.get('voice_model', '')}",
    ),
    _s(
        "lm_unload",
        "Free the memory a loaded model holds (it reloads on the next request).",
        _obj({"model": _MODEL}, ("model",)),
        "monitor",
        _unload,
        lambda a: f"Unload {a.get('model', '')}",
    ),
)


def tool_specs(cfg: Any) -> tuple[ToolSpec, ...]:
    """The specs offered for ``cfg`` — HF search only when the user enabled it."""
    from jarvis.core.config import ollama_hf_enabled

    hf = ollama_hf_enabled(cfg) if cfg is not None else False
    reads = tuple(s for s in READ_TOOLS if hf or s.name != "lm_hf_search")
    return reads + ACTION_TOOLS


def build_tools(
    cfg: Any,
    *,
    root: str,
    transport: Any = None,
    search_fn: Callable[[str], Any] | None = None,
) -> dict[str, Tool]:
    """The assistant's tools for one turn, keyed by name.

    ``root`` is the Ollama server root the section talks to; ``transport``
    the httpx test seam; ``search_fn`` (the ``search_web`` call) lets
    ``lm_benchmarks`` refresh the table — without it the table is read-only.
    """
    env = _Env(cfg=cfg, root=root, transport=transport, search_fn=search_fn)
    return {spec.name: LocalModelsTool(spec, env) for spec in tool_specs(cfg)}
