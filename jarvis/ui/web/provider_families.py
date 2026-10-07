"""Providers grouped by the company behind them — the API Keys page's model.

The provider catalog (``provider_spec.PROVIDERS``) is organised by FEATURE:
one card per tier (live voice, speech output, speech input, dictation polish,
brain). People think in COMPANIES: "I have a Claude subscription and an OpenAI
key". This module folds the catalog into one entry per company so the settings
page can show each company once, with one way to sign in, one key that serves
every feature, and the features it powers.

Nothing here is a second catalog. Every family and every member is derived from
the specs and the credential chains in ``jarvis.core.config``:

* a subscription-login spec belongs to the company that issues the login
  (``SUBSCRIPTION_FAMILY`` — the one hand-written fact, keyed by auth mode);
* an on-device spec belongs to the ``local`` family;
* a keyed spec belongs to the family its key slot falls back to
  (``secret_slot_scope``), so a new scoped slot joins the right company the
  moment its chain is declared;
* a keyed spec outside every chain (ElevenLabs, Cartesia…) is its own family.

A new provider therefore appears on the page without touching this file. The
display names and the order are presentation only (AP-21): they never gate a
code path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.core import config as cfg_mod

from .provider_spec import PROVIDERS, ProviderSpec, get_spec, provider_billing

# Which company issues each subscription login. Keyed by auth mode, never by a
# provider id, so a second spec on the same login lands in the same family.
SUBSCRIPTION_FAMILY: dict[str, str] = {
    "claude_cli": "claude-api",
    "codex": "openai",
    "antigravity": "gemini",
    "grok_build": "grok",
}

LOCAL_FAMILY = "local"

# Company names for the families whose spec label names a product instead
# ("Claude (API-Key)"). Anything not listed falls back to its first spec label.
_FAMILY_LABELS: dict[str, str] = {
    "claude-api": "Anthropic",
    "openai": "OpenAI",
    "gemini": "Google Gemini",
    "vertex": "Google Vertex AI",
    "grok": "xAI",
    "openrouter": "OpenRouter",
    "nvidia": "NVIDIA NIM",
    "groq": "Groq",
    "cartesia": "Cartesia",
    "inworld": "Inworld",
    LOCAL_FAMILY: "On this computer",
}

# Display order: the companies most installs connect first, then the rest in
# catalog order, then the machine itself.
_FAMILY_ORDER: tuple[str, ...] = (
    "claude-api",
    "openai",
    "gemini",
    "grok",
    "openrouter",
    "vertex",
    "nvidia",
    "groq",
)

# The Agents section keys its rows by the worker id, which differs from the
# spec id for exactly one provider.
_AGENT_ID_ALIASES: dict[str, str] = {"codex": "openai-codex"}


@dataclass(slots=True)
class _Family:
    id: str
    label: str
    specs: list[ProviderSpec] = field(default_factory=list)
    # Withdrawn cards still running an existing selection: no row on the page,
    # but health and "in use" reports name them, so they map to the company.
    hidden: list[str] = field(default_factory=list)


def family_of(spec: ProviderSpec) -> str:
    """The company a provider card belongs to (see the module docstring)."""
    issuer = SUBSCRIPTION_FAMILY.get(spec.auth_mode)
    if issuer is not None:
        return issuer
    if provider_billing(spec) == "local":
        return LOCAL_FAMILY
    for slot in spec.secret_keys:
        scope = cfg_mod.secret_slot_scope(slot)
        if scope is not None:
            return scope.family
    return spec.id


def _group() -> list[_Family]:
    families: dict[str, _Family] = {}
    hidden: list[ProviderSpec] = []
    for spec in PROVIDERS:
        if spec.hidden:
            hidden.append(spec)
            continue
        fid = family_of(spec)
        family = families.get(fid)
        if family is None:
            family = families[fid] = _Family(id=fid, label=_FAMILY_LABELS.get(fid, spec.label))
        family.specs.append(spec)
    for spec in hidden:
        owner = families.get(family_of(spec))
        if owner is not None:
            owner.hidden.append(spec.id)

    def rank(family: _Family) -> tuple[int, int]:
        if family.id == LOCAL_FAMILY:
            return (2, 0)
        if family.id in _FAMILY_ORDER:
            return (0, _FAMILY_ORDER.index(family.id))
        return (1, 0)

    # sorted() is stable: families outside the fixed order keep catalog order.
    return sorted(families.values(), key=rank)


def _primary_slot(family: _Family) -> str | None:
    # The machine itself has no account. A local server's optional key stays on
    # that server's own settings, never as "the key for this computer".
    if family.id == LOCAL_FAMILY:
        return None
    slot = cfg_mod.secret_family_primary_slot(family.id)
    if slot is not None:
        return slot
    # A standalone family (one vendor, one slot) stores its key on its card.
    for spec in family.specs:
        if spec.secret_keys:
            return spec.secret_keys[0]
    return None


def _subscription(family: _Family) -> dict[str, Any] | None:
    for spec in family.specs:
        if SUBSCRIPTION_FAMILY.get(spec.auth_mode) == family.id:
            return {"kind": spec.auth_mode, "provider_id": spec.id, "label": spec.label}
    return None


def _first(family: _Family, attr: str, prefer: str | None) -> str | None:
    """``attr`` from the spec that owns the family key, else the first that has it."""
    owner = get_spec(family.id)
    if owner is not None and (prefer is None or prefer in owner.secret_keys):
        value = getattr(owner, attr, None)
        if value:
            return value
    for spec in family.specs:
        if prefer is not None and prefer not in spec.secret_keys:
            continue
        value = getattr(spec, attr, None)
        if value:
            return value
    for spec in family.specs:
        value = getattr(spec, attr, None)
        if value:
            return value
    return None


def _slot_label(slot: str) -> str:
    """The surface a scoped slot belongs to, in the page's own words."""
    if slot.startswith("jarvis_agent_"):
        return "agents"
    if slot.startswith("realtime_"):
        return "live_voice"
    if slot.startswith("codex_"):
        return "codex"
    return "other"


def managed_agent_ids() -> frozenset[str]:
    """Every agent catalog id a company on the Agents tab switches on and off.

    A seat outside this set has no switch on the page, so the agents' pickers
    never offer it: they offer only what the person turned on there. No
    keyring read.
    """
    ids: set[str] = set()
    for family in _group():
        ids.update(_AGENT_ID_ALIASES.get(spec.id, spec.id) for spec in family.specs)
    return frozenset(ids)


def build_families(
    *,
    secret_present: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    """The page payload: one entry per company, catalog-derived.

    ``secret_present`` is a seam for tests; production reads every slot through
    ``cfg_mod.get_secret`` (keyring → ENV → .env → file), returning presence
    only — a value never leaves this function.
    """
    present = secret_present or (lambda slot: bool(cfg_mod.get_secret(slot)))
    payload: list[dict[str, Any]] = []
    for family in _group():
        primary = _primary_slot(family)
        separate: list[dict[str, Any]] = []
        if primary is not None and cfg_mod.secret_family_primary_slot(family.id) == primary:
            for slot in cfg_mod.secret_family_scoped_slots(family.id):
                if present(slot):
                    separate.append({"slot": slot, "surface": _slot_label(slot)})
        provider_ids = [spec.id for spec in family.specs]
        agent_ids = [_AGENT_ID_ALIASES.get(pid, pid) for pid in provider_ids]
        payload.append(
            {
                "id": family.id,
                "label": family.label,
                # The card whose brand mark stands for the company.
                "logo_id": family.specs[0].id if family.id == LOCAL_FAMILY else (
                    get_spec(family.id).id if get_spec(family.id) else family.specs[0].id
                ),
                "local": family.id == LOCAL_FAMILY,
                "key_slot": primary,
                "key_present": bool(primary) and present(primary),
                "separate_keys": separate,
                "dashboard_url": _first(family, "dashboard_url", primary),
                "signup_url": _first(family, "signup_url", None),
                "subscription": _subscription(family),
                "provider_ids": provider_ids,
                "hidden_ids": list(family.hidden),
                "agent_ids": list(dict.fromkeys(agent_ids)),
            }
        )
    return payload
