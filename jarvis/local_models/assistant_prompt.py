"""The setup assistant's briefing: a per-turn system-prompt addendum.

Rendered fresh for every turn of the ``local-models`` chat surface and
handed to the brain through ``TurnOverride.system_extra`` — the machine's
facts, what is installed, which role runs what (and under which config
key), the curated shortlist with fit verdicts and proven / new labels, the
benchmark table from the cache, the hard rules, and the output contract the
panel parses (a fenced ``jarvis-proposal`` JSON block). Budget: about 2.5k
tokens, so lists are capped rather than complete.

The three canned openers are what the panel's buttons send as the user
turn (D6): ``SETUP_OPENER``, ``DIAGNOSE_OPENER``, ``TEST_OPENER``.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
from typing import Any, Final

log = logging.getLogger(__name__)

__all__ = [
    "DIAGNOSE_OPENER",
    "PROPOSAL_FENCE",
    "SETUP_OPENER",
    "TEST_OPENER",
    "build_system_extra",
]

PROPOSAL_FENCE: Final[str] = "jarvis-proposal"

#: Rough budget guard: characters, not tokens (≈ 4 chars per token).
_MAX_CHARS = 10_000
_MAX_INVENTORY_ROWS = 30
_MAX_BENCHMARK_ROWS = 40

SETUP_OPENER: Final[str] = (
    "Help me set up local models on this machine. Check the hardware and the server, then "
    "propose ONE complete setup for the five roles (chat, voice, tools_screen, deep, "
    "embedding) using the best proven models that fit, as a jarvis-proposal block. "
    "Do not execute anything until I confirm."
)

DIAGNOSE_OPENER: Final[str] = (
    "Something is not working with my local models. Find out what is broken (server, "
    "roles, downloads, voice server), name the cause in one or two sentences, and propose "
    "the smallest fix as a jarvis-proposal block. Do not execute anything until I confirm."
)

TEST_OPENER: Final[str] = (
    "Run the end-to-end test of my local setup (lm_test_plan) and report the result as a "
    "short table: one line per role with ok / error and the reason. Propose a fix as a "
    "jarvis-proposal block only for roles that failed."
)

_RULES: Final[str] = """\
HARD RULES
- Never delete a model; there is no tool for it and you must not suggest one.
- Never change the active brain provider yourself. When Ollama should answer the voice and \
the chat, put a `brain_switch` entry in the proposal — the person clicks it.
- Installs, downloads, role picks, option writes and voice-stack changes happen only after \
the person confirmed the proposal; in that run, call the tools in the proposal's order.
- Never touch BIOS, OS, driver or firewall settings; point to the OLLAMA_* guide instead.
- Prefer models labelled `proven` whose fit is `comfortable`; a `new_little_tested` model is \
never a default; a `stale` label means the shortlist needs a refresh — say so.
- When a step fails, say exactly what failed (the tool's own sentence) and what to do next.
- Answer in the person's language; keep prose short, the proposal block carries the plan.
- Written delivery: this is a chat panel, not a voice reply — tables and lists are fine."""

_CONTRACT: Final[str] = """\
OUTPUT CONTRACT
When you propose or revise a setup, end the message with exactly one fenced block:
```jarvis-proposal
{"version": 1,
 "steps": [
   {"id": "s1", "kind": "install_ollama", "label": "Install Ollama"},
   {"id": "s2", "kind": "pull", "model": "qwen3.5:4b", "size_gb": 3.4, "fit": "comfortable",
    "proven": true, "label": "Download Qwen 3.5 4B"},
   {"id": "s3", "kind": "set_role", "role": "chat", "model": "qwen3.5:4b", "label": "..."},
   {"id": "s4", "kind": "set_options", "model": "qwen3.5:4b", "options": {"num_ctx": 16384},
    "label": "..."},
   {"id": "s5", "kind": "apply_voice_stack", "model": "qwen3.5:4b", "options":
    {"voice_model": "..."}, "label": "..."},
   {"id": "s6", "kind": "test", "label": "Test every role"}
 ],
 "brain_switch": {"provider": "ollama", "why": "one sentence"},
 "notes": ["one sentence per caveat"]}
```
`kind` is one of install_ollama | pull | set_role | set_options | apply_voice_stack | test. \
`brain_switch` and `notes` are optional; `steps` may be empty when nothing needs doing."""


# ── data gathering ────────────────────────────────────────────────────────


def _provider(cfg: Any, name: str) -> Any:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    return providers.get(name) if isinstance(providers, dict) else None


async def _machine(cfg: Any) -> dict[str, Any]:
    import platform

    from jarvis.brain.ollama_pull import accelerator_gb, total_memory_gb

    (accel, source), ram = await asyncio.gather(
        asyncio.to_thread(accelerator_gb), asyncio.to_thread(total_memory_gb)
    )
    brain = getattr(cfg, "brain", None)
    worker = getattr(brain, "worker", None)
    return {
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "ram_gb": ram,
        "accelerator_gb": round(accel, 1),
        "accelerator_source": source,
        "active_brain": str(getattr(brain, "primary", "") or ""),
        "assistant_tier": (
            f"{getattr(worker, 'provider', '') or ''} / {getattr(worker, 'model', '') or ''}"
        ).strip(" /"),
    }


async def _inventory(root: str, transport: Any) -> tuple[list[Any], str | None]:
    from jarvis.brain import ollama_inventory as inventory

    try:
        return await inventory.list_models(root, transport=transport), None
    except inventory.OllamaServerError as exc:
        return [], str(exc)


def _roles(cfg: Any, installed_names: list[str]) -> list[dict[str, Any]]:
    from jarvis.brain.ollama_inventory import same_model
    from jarvis.brain.ollama_roles import ROLES, current_pick

    rows: list[dict[str, Any]] = []
    for spec in ROLES:
        if not spec.writable:
            continue
        current, note = current_pick(cfg, spec.id)
        rows.append(
            {
                "role": spec.id,
                "config_key": spec.config_key,
                "required": list(spec.required),
                "current": current,
                "installed": bool(current) and any(same_model(n, current) for n in installed_names),
                "note": note,
            }
        )
    return rows


def _curated(
    machine: dict[str, Any], installed_names: list[str], table: Any, today: _dt.date
) -> list[dict[str, Any]]:
    from jarvis.brain.ollama_inventory import same_model
    from jarvis.brain.ollama_pull import RECOMMENDED_MODELS, fit_verdict
    from jarvis.local_models.benchmarks import label_for

    rows: list[dict[str, Any]] = []
    for entry in RECOMMENDED_MODELS:
        verdict, _note = fit_verdict(
            entry.size_gb, machine.get("ram_gb"), float(machine.get("accelerator_gb") or 0.0)
        )
        rows.append(
            {
                "id": entry.id,
                "role": entry.role,
                "size_gb": entry.size_gb,
                "tools": entry.tools,
                "vision": entry.vision,
                "fit": verdict,
                "installed": any(same_model(n, entry.id) for n in installed_names),
                "label": label_for(entry, table, today),
                "purpose": entry.purpose,
            }
        )
    return rows


# ── rendering ─────────────────────────────────────────────────────────────


def _yn(value: bool) -> str:
    return "yes" if value else "no"


def _render_machine(machine: dict[str, Any], server: dict[str, Any], root: str) -> str:
    ram = machine.get("ram_gb")
    lines = [
        "MACHINE",
        f"- OS: {machine['os']}",
        f"- RAM: {ram:g} GB" if ram is not None else "- RAM: unknown",
        f"- Graphics memory usable for models: {machine['accelerator_gb']:g} GB "
        f"(source: {machine['accelerator_source']})",
        f"- Ollama server: {root} — "
        + (
            f"running, version {server.get('version')}"
            if server.get("ok")
            else f"not answering ({server.get('detail')})"
        ),
        f"- Active brain provider: {machine['active_brain'] or 'unset'} "
        "(only the person may change it)",
        f"- You run on the Agents tier: {machine['assistant_tier'] or 'unknown'}",
    ]
    return "\n".join(lines)


def _render_inventory(models: list[Any], error: str | None) -> str:
    if error:
        return f"INSTALLED MODELS\n- unavailable: {error}"
    if not models:
        return "INSTALLED MODELS\n- none"
    lines = ["INSTALLED MODELS (name · size · capabilities · context)"]
    for m in models[:_MAX_INVENTORY_ROWS]:
        caps = ",".join(m.capabilities) or "unknown"
        ctx = f"{m.context_length}" if m.context_length else "?"
        lines.append(f"- {m.name} · {m.size_bytes / (1024**3):.1f} GB · {caps} · ctx {ctx}")
    if len(models) > _MAX_INVENTORY_ROWS:
        lines.append(f"- … and {len(models) - _MAX_INVENTORY_ROWS} more (lm_inventory)")
    return "\n".join(lines)


def _render_roles(rows: list[dict[str, Any]], voice_command: str) -> str:
    lines = ["ROLES (role · needs · current pick · installed · config key)"]
    for r in rows:
        pick = r["current"] or "not set"
        extra = f" — {r['note']}" if r["note"] else ""
        lines.append(
            f"- {r['role']} · {'+'.join(r['required'])} · {pick} · "
            f"{_yn(r['installed']) if r['current'] else '-'} · {r['config_key']}{extra}"
        )
    managed = "--model_name" in voice_command
    lines.append(
        "- managed voice server: "
        + ("installed (apply_voice_stack may change its models)" if managed else "not installed")
    )
    return "\n".join(lines)


def _render_curated(rows: list[dict[str, Any]], reviewed_on: _dt.date) -> str:
    lines = [
        f"CURATED SHORTLIST (reviewed {reviewed_on.isoformat()}; id · role · GB · fit · "
        "label · installed)"
    ]
    for r in rows:
        caps = "tools" if r["tools"] else "-"
        if r["vision"]:
            caps += "+vision"
        lines.append(
            f"- {r['id']} · {r['role']} · {r['size_gb']:g} · {r['fit']} · {r['label']} · "
            f"{_yn(r['installed'])} · {caps} — {r['purpose']}"
        )
    return "\n".join(lines)


def _render_benchmarks(table: Any) -> str:
    if table is None:
        return "BENCHMARKS\n- no cached table (lm_benchmarks refresh=true inside a guided run)"
    lines = [f"BENCHMARKS (source: {table.source}; fetched {table.fetched_at or 'n/a'})"]
    if table.note:
        lines.append(f"- note: {table.note}")
    for row in table.rows[:_MAX_BENCHMARK_ROWS]:
        lines.append(f"- {row.family} · {row.source} · {row.metric} = {row.value:g}")
    for family, facts in list(table.library.items())[:_MAX_BENCHMARK_ROWS]:
        upd = f"{facts.updated_days} d ago" if facts.updated_days is not None else "?"
        lines.append(f"- {family} · library · pulls {facts.pulls:,} · updated {upd}")
    if len(lines) == 1:
        lines.append("- no rows")
    return "\n".join(lines)


async def build_system_extra(cfg: Any, *, root: str, transport: Any = None) -> str:
    """The addendum for one turn of the local-models assistant (≈ 2.5k tokens)."""
    from jarvis.brain.ollama_pull import CURATED_REVIEWED_ON
    from jarvis.brain.ollama_runtime import probe_host
    from jarvis.local_models.benchmarks import load_cached

    machine, server, (models, inv_error) = await asyncio.gather(
        _machine(cfg), probe_host(root, transport=transport), _inventory(root, transport)
    )
    installed_names = [m.name for m in models]
    table = load_cached()
    today = _dt.datetime.now(_dt.UTC).date()
    voice_command = str(getattr(_provider(cfg, "local-realtime"), "launch_command", "") or "")

    sections = [
        "LOCAL MODELS SETUP ASSISTANT\n"
        "You help this person set up, test and repair local models (Ollama) on THIS machine, "
        "inside the Local models section. Read with the lm_* tools before you propose; "
        "act only through them, and only after confirmation.",
        _render_machine(machine, server, root),
        _render_inventory(models, inv_error),
        _render_roles(_roles(cfg, installed_names), voice_command),
        _render_curated(_curated(machine, installed_names, table, today), CURATED_REVIEWED_ON),
        _render_benchmarks(table),
        _RULES,
        _CONTRACT,
    ]
    text = "\n\n".join(sections)
    if len(text) > _MAX_CHARS:
        log.info("local-models prompt: %d chars, trimming the benchmark section", len(text))
        sections[5] = "BENCHMARKS\n- table too long for the briefing; use lm_benchmarks"
        text = "\n\n".join(sections)
    return text
