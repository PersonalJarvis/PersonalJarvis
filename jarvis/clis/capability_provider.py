"""Bridge connected CLIs into the CapabilityRegistry.

Design: docs/superpowers/specs/2026-06-10-cli-first-class-capabilities-design.md
(AD-CLI1..AD-CLI3). One Capability per CLI (``cli.<name>``), registered only
while the CLI is usable (installed + authenticated). All functions are
defensive: an infrastructure failure degrades to a no-op/empty result and
must never propagate into the caller (registry lifecycle or voice path).
"""

from __future__ import annotations

import logging
from typing import Any

from jarvis.clis.spec import CliSpec
from jarvis.clis.tool import TOOL_NAME_PREFIX
from jarvis.core.capabilities import Capability

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
