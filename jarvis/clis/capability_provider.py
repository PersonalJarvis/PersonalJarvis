"""Bridge connected CLIs into the CapabilityRegistry.

Design: docs/superpowers/specs/2026-06-10-cli-first-class-capabilities-design.md
(AD-CLI1..AD-CLI3). One Capability per CLI (``cli.<name>``), registered only
while the CLI is usable (installed + authenticated). All functions are
defensive: an infrastructure failure degrades to a no-op/empty result and
must never propagate into the caller (registry lifecycle or voice path).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from jarvis.clis.spec import CliSpec
from jarvis.clis.tool import TOOL_NAME_PREFIX
from jarvis.core.capabilities import Capability, _normalize

log = logging.getLogger(__name__)

# Documented domain vocabulary for CliCapabilityDecl.domains. The evidence
# gate only consumes the configured subset (calendar/email/tasks/repos/
# deployments); the rest exists so catalog curation stays typo-guarded
# (parity test in tests/unit/clis/test_seed_catalog_capabilities.py).
DOMAIN_VOCAB: frozenset[str] = frozenset(
    {
        "calendar",
        "email",
        "tasks",
        "repos",
        "deployments",
        "cloud",
        "containers",
        "kubernetes",
        "database",
        "payments",
        "messaging",
        "storage",
        "workspace",
    }
)

CAP_ID_PREFIX = "cli."


def capability_for_spec(spec: CliSpec) -> Capability | None:
    """Merge a spec's capability declarations into one Capability, or None."""
    if not spec.capabilities:
        return None
    verbs: list[str] = []
    objects: list[str] = []
    descriptions: list[str] = []
    for decl in spec.capabilities:
        verbs.extend(v for v in decl.verbs if v not in verbs)
        objects.extend(o for o in decl.objects if o not in objects)
        if decl.description not in descriptions:
            descriptions.append(decl.description)
    return Capability(
        id=f"{CAP_ID_PREFIX}{spec.name}",
        source="cli",
        verbs=tuple(verbs),
        objects=tuple(objects),
        description=" ".join(descriptions),
        risk_tier=spec.risk.default_tier,
        requires_evidence=True,
    )


def sync_registry(cli_registry: Any, capability_registry: Any) -> None:
    """Mirror the usable-CLI set into the CapabilityRegistry. Idempotent.

    Derives everything from the catalog + active tool set — no module state,
    so repeated calls (bootstrap, every refresh_status) converge.
    """
    try:
        active = {t.name for t in cli_registry.active_tools()}
        for spec in cli_registry.catalog().all().values():
            cap = capability_for_spec(spec)
            if cap is None:
                continue
            if f"{TOOL_NAME_PREFIX}{spec.name}" in active:
                capability_registry.register(cap)
            else:
                capability_registry.deregister(cap.id)
    except Exception:  # noqa: BLE001 — sync must never break the lifecycle
        log.debug("cli capability sync failed", exc_info=True)


def connected_domain_tool_map(cli_registry: Any) -> dict[str, str]:
    """Map evidence domain -> cli tool name for usable CLIs only.

    First registered CLI per domain wins (catalog order is deterministic).
    """
    out: dict[str, str] = {}
    try:
        active = {t.name for t in cli_registry.active_tools()}
        for spec in cli_registry.catalog().all().values():
            tool_name = f"{TOOL_NAME_PREFIX}{spec.name}"
            if tool_name not in active:
                continue
            for decl in spec.capabilities:
                for domain in decl.domains:
                    out.setdefault(domain, tool_name)
    except Exception:  # noqa: BLE001
        log.debug("cli domain map failed", exc_info=True)
    return out


# Ambiguous bare nouns that appear in CLI capability objects but would hijack
# unrelated questions ("was kostet ein Tesla?") if used as forcing keywords.
# Applied ONLY to derived objects — curated config keywords are never filtered.
_KEYWORD_DENYLIST: frozenset[str] = frozenset(
    {"kosten", "cost", "costs", "preis", "preise", "price", "geld", "money"}
)


def connected_domain_keyword_map(cli_registry: Any) -> dict[str, list[str]]:
    """Map evidence domain -> trigger keywords, derived from usable CLIs' objects.

    Unions each usable CLI capability's ``objects`` per declared ``domain`` so a
    connected CLI becomes implicitly triggerable from its own catalog vocabulary
    with no hand-maintained config. Ambiguous bare cost/price nouns are dropped
    (``_KEYWORD_DENYLIST``). Defensive: any fault returns ``{}`` (the gate then
    runs on the config keyword list exactly as before).
    """
    out: dict[str, list[str]] = {}
    try:
        active = {t.name for t in cli_registry.active_tools()}
        for spec in cli_registry.catalog().all().values():
            if f"{TOOL_NAME_PREFIX}{spec.name}" not in active:
                continue
            for decl in spec.capabilities:
                for domain in decl.domains:
                    bucket = out.setdefault(domain, [])
                    for obj in decl.objects:
                        kw = _normalize(obj)
                        if kw and kw not in _KEYWORD_DENYLIST and kw not in bucket:
                            bucket.append(kw)
    except Exception:  # noqa: BLE001 — derivation must never break the gate
        log.debug("cli domain keyword map failed", exc_info=True)
        return {}
    return out


def merged_evidence_domains(
    cli_registry: Any, config_domains: Mapping[str, Sequence[str]]
) -> dict[str, list[str]]:
    """Domain -> keywords, deriving from connected CLIs and overlaying config.

    Config keywords are always included (curated override); derived CLI-object
    keywords augment them. Config-only domains (no backing CLI) are preserved.
    Defensive: on any fault returns ``dict(config_domains)`` unchanged.
    """
    try:
        derived = connected_domain_keyword_map(cli_registry)
        out: dict[str, list[str]] = {d: list(kws) for d, kws in derived.items()}
        for domain, kws in config_domains.items():
            bucket = out.setdefault(domain, [])
            for kw in kws:
                if kw not in bucket:
                    bucket.append(kw)
        return out
    except Exception:  # noqa: BLE001
        log.debug("merged evidence domains failed", exc_info=True)
        return {d: list(kws) for d, kws in config_domains.items()}


# A marketplace plugin (or native REST tool) and a CLI for the SAME service.
# When the CLI is connected, its plugin counterpart is hidden so the CLI is the
# only choice (req 4: CLI > plugin; plugin is a fallback only). Key = plugin id
# / native tool name; value = CLI name. Guarded against drift by a parity test
# (tests/unit/clis/test_plugin_cli_overlap_parity.py).
PLUGIN_CLI_OVERLAP: dict[str, str] = {
    "github": "gh",
    "vercel": "vercel",
    "supabase": "supabase",
    "stripe": "stripe",
    "gmail": "gam",
}


def suppress_plugin_tools_covered_by_cli(tools: dict[str, Any]) -> dict[str, Any]:
    """Drop plugin/native tools whose CLI counterpart is connected this turn.

    For each overlap entry whose ``cli_<name>`` is present in ``tools``, removes
    the namespaced ``<plugin_id>/*`` tools and the exact native ``<plugin_id>``
    tool. Defensive: returns ``tools`` unchanged on any fault.
    """
    try:
        present_clis = {n for n in tools if n.startswith(TOOL_NAME_PREFIX)}
        drop: set[str] = set()
        for plugin_id, cli_name in PLUGIN_CLI_OVERLAP.items():
            if f"{TOOL_NAME_PREFIX}{cli_name}" not in present_clis:
                continue
            prefix = f"{plugin_id}/"
            for name in tools:
                if name == plugin_id or name.startswith(prefix):
                    drop.add(name)
        if not drop:
            return tools
        return {n: t for n, t in tools.items() if n not in drop}
    except Exception:  # noqa: BLE001 — suppression must never blind the brain
        log.debug("plugin suppression failed; using full tool set", exc_info=True)
        return tools


def refusal_hint(domain: str, cli_registry: Any, lang: str) -> str:
    """One TTS-safe sentence pointing at the closest catalog CLI for *domain*.

    Used by the evidence gate's honest refusal (AD-CLI7): "installed but not
    connected" beats "available in the catalog". Returns "" when the catalog
    has no CLI for the domain.
    """
    try:
        status_map = cli_registry.all_status()
        for spec in cli_registry.catalog().all().values():
            if not any(domain in decl.domains for decl in spec.capabilities):
                continue
            st = status_map.get(spec.name)
            if st is not None and st.installed:
                if lang == "de":
                    # Spoken German voice reply (TTS-safe).
                    return (
                        f" Die {spec.display_name} ist installiert, aber"  # i18n-allow
                        " noch nicht verbunden — sag Bescheid, dann"  # i18n-allow
                        " richten wir das ein."  # i18n-allow
                    )
                return (
                    f" The {spec.display_name} is installed but not connected yet"
                    " — say the word and we'll set it up."
                )
            if lang == "de":
                # Spoken German voice reply (TTS-safe).
                return (
                    f" Im CLI-Katalog gibt es dafür die {spec.display_name}"  # i18n-allow
                    " — ich kann sie mit dir einrichten."  # i18n-allow
                )
            return f" The CLI catalog has {spec.display_name} for that — I can set it up with you."
    except Exception:  # noqa: BLE001
        log.debug("refusal hint failed", exc_info=True)
    return ""


__all__ = [
    "DOMAIN_VOCAB",
    "CAP_ID_PREFIX",
    "capability_for_spec",
    "sync_registry",
    "connected_domain_tool_map",
    "refusal_hint",
]
