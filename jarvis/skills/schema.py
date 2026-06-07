"""Pydantic-Schemas + DataClasses für Skills.

Frontmatter ist YAML — pydantic validiert strukturell. `Skill` selbst ist
ein frozen DataClass (Immutability für Flight-Recorder-Replay).

Skill-Lifecycle-Events erben von `jarvis.core.events.Event` — sind aber hier
definiert (nicht in events.py), damit wir die core-Layer nicht anfassen müssen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jarvis.core.events import Event
from jarvis.core.protocols import RiskTier

# ----------------------------------------------------------------------
# Lifecycle-State
# ----------------------------------------------------------------------

class SkillLifecycleState(str, Enum):
    """Phasen im Skill-Leben."""
    DRAFT = "draft"            # noch nicht validiert / fehlerhaft
    VALIDATED = "validated"    # Frontmatter OK, Tools existieren
    ACTIVE = "active"          # vom User aktiviert, triggert
    DISABLED = "disabled"      # vom User deaktiviert


# ----------------------------------------------------------------------
# Frontmatter-Models
# ----------------------------------------------------------------------

TriggerType = Literal["voice", "hotkey", "schedule"]


class SkillTrigger(BaseModel):
    """Ein Trigger — Voice-Pattern, Hotkey-Combo oder Cron-Expression."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: TriggerType
    pattern: str | None = None   # Voice: Regex oder Template
    combo: str | None = None     # Hotkey: z.B. "ctrl+right_alt+j"
    cron: str | None = None      # Schedule: Cron-Expression
    language: list[str] = Field(default_factory=lambda: ["de", "en"])

    @field_validator("pattern", "combo", "cron")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) else v

    def validate_payload(self) -> list[str]:
        """Semantischer Check: je nach `type` muss das passende Feld gesetzt sein."""
        errors: list[str] = []
        if self.type == "voice" and not self.pattern:
            errors.append("voice-trigger braucht 'pattern'")
        if self.type == "hotkey" and not self.combo:
            errors.append("hotkey-trigger braucht 'combo'")
        if self.type == "schedule" and not self.cron:
            errors.append("schedule-trigger braucht 'cron'")
        return errors


class SkillRiskPolicy(BaseModel):
    """Risk-Tier-Konfiguration für einen Skill."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    default_tier: RiskTier = "monitor"
    per_tool_overrides: dict[str, RiskTier] = Field(default_factory=dict)
    require_confirmation: list[str] = Field(default_factory=list)


class SkillFrontmatter(BaseModel):
    """YAML-Frontmatter einer SKILL.md."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    name: str
    version: str = "0.1.0"
    description: str = ""
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    author: str = ""
    license: str = "MIT"
    homepage_url: str | None = None
    source_url: str | None = None
    docs_url: str | None = None
    triggers: list[SkillTrigger] = Field(default_factory=list)
    requires_tools: list[str] = Field(default_factory=list)
    risk_policy: SkillRiskPolicy = Field(default_factory=SkillRiskPolicy)
    config: dict[str, Any] = Field(default_factory=dict)
    token_budget_estimate: int = Field(default=2000, ge=1, le=100_000)
    # Phase 7.5: Lifecycle-State im Frontmatter — OpenClaw-authored Skills
    # tragen explizit `state: draft`, vom `draft_writer` erzwungen (Plan-§AD-8).
    # Default `None` → Loader interpretiert als "validated/active" (Legacy).
    state: SkillLifecycleState | None = None
    # Plugin<->Skill pairing (2026-06-07). When set, this skill is the canonical
    # source for a marketplace plugin's intent vocabulary; the deterministic
    # generator in jarvis/skills/plugin_coupling.py (created in a follow-up task)
    # turns intent_verbs + intent_objects into a CapabilityRegistry entry so the
    # connected plugin is reachable (resolve_intent != None silences the
    # UNSUPPORTED refusal AND the force-spawn gate). plugin_id=None marks a
    # standalone "skill without plugin" that still carries an intent capability.
    # HARD CONSTRAINT (Task 5.5 corrected): a paired-skill cap only matches when
    # BOTH a verb AND a domain object hit (resolve_intent in capabilities.py), so
    # the hard-negative guard lives in the VERB list: intent_verbs MUST EXCLUDE
    # coding verbs (implement/build/write/refactor/debug) so a coding task that
    # merely names the domain ("implement an Email-Validation") never gets a verb
    # hit. intent_objects SHOULD include the real domain nouns a user says,
    # INCLUDING "mail"/"email" (needed so "send an Email" matches) plus a UNIQUE
    # keyword (e.g. "gmail") so the cap never steals another domain's request.
    plugin_id: str | None = None
    intent_verbs: list[str] = Field(default_factory=list)
    intent_objects: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name darf nicht leer sein")
        return v.strip()

    @field_validator("homepage_url", "source_url", "docs_url", mode="before")
    @classmethod
    def _clean_url(cls, v: Any) -> str | None:
        """Trimmt Whitespace, mappt leere Strings auf None, erzwingt http(s)://.

        Begruendung: Pydantic's ``HttpUrl`` ist zu strikt (z.B. trailing-slash
        Normalisierung), und wir wollen die Original-URL roundtrip-sicher
        speichern. Max 2048 Zeichen schuetzt vor Missbrauch (Homepage-Feld als
        JSON-Payload-Drop).
        """
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("URL muss string sein")
        cleaned = v.strip()
        if not cleaned:
            return None
        if len(cleaned) > 2048:
            raise ValueError("URL zu lang (max 2048 Zeichen)")
        if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
            raise ValueError("URL muss mit http:// oder https:// beginnen")
        return cleaned


# ----------------------------------------------------------------------
# Skill-Container
# ----------------------------------------------------------------------

RESOURCE_KINDS: tuple[str, ...] = ("references", "scripts", "assets", "agents")


@dataclass(frozen=True, slots=True)
class Skill:
    """Eine geladene SKILL.md — parsed Frontmatter + Body + Metadaten.

    ``resources`` haelt die Bundle-Sibling-Ordner (analog zu Anthropic's
    Claude-Skills-Struktur: ``references/``, ``scripts/``, ``assets/``,
    ``agents/``). Wert pro Key ist die Liste der relativen File-Pfade zum
    Skill-Root. Leer wenn der jeweilige Ordner nicht existiert.
    """
    path: Path
    frontmatter: SkillFrontmatter | None           # None wenn DRAFT/broken
    body: str
    state: SkillLifecycleState = SkillLifecycleState.DRAFT
    body_hash: str = ""
    error: str | None = None                       # Parse-/Validation-Fehler
    resources: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {k: () for k in RESOURCE_KINDS}
    )

    @property
    def name(self) -> str:
        if self.frontmatter is not None:
            return self.frontmatter.name
        return self.path.stem

    @property
    def root(self) -> Path:
        """Verzeichnis in dem die SKILL.md liegt — Basis fuer Resource-Lookups."""
        return self.path.parent


@dataclass(frozen=True, slots=True)
class SkillResult:
    """Gesamtes Ergebnis eines SkillRunner.run()."""
    skill_name: str
    success: bool
    steps: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    rendered_body: str = ""
    error: str | None = None
    duration_ms: int = 0


# ----------------------------------------------------------------------
# Skill-Events (erben von jarvis.core.events.Event)
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SkillStarted(Event):
    skill_name: str = ""
    trigger_type: str = ""


@dataclass(frozen=True, slots=True)
class SkillStepExecuted(Event):
    skill_name: str = ""
    step_index: int = 0
    tool_name: str = ""
    success: bool = False
    duration_ms: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SkillCompleted(Event):
    skill_name: str = ""
    duration_ms: int = 0
    steps_count: int = 0


@dataclass(frozen=True, slots=True)
class SkillFailed(Event):
    skill_name: str = ""
    error: str = ""
    at_step: int | None = None


@dataclass(frozen=True, slots=True)
class SkillStateChanged(Event):
    skill_name: str = ""
    previous: str = ""
    new_state: str = ""


@dataclass(frozen=True, slots=True)
class SkillRegistryReloaded(Event):
    total: int = 0
    active: int = 0
    draft: int = 0


@dataclass(frozen=True, slots=True)
class SkillCreated(Event):
    """Ein User-authored Skill wurde ueber die Desktop-App angelegt.

    Getrennt von ``SkillStateChanged``, damit die UI spezifisch auf das Authoring-
    Event reagieren kann (Toast "Skill erstellt", React-Query-Invalidation).
    """
    skill_name: str = ""
    author: str = ""


@dataclass(frozen=True, slots=True)
class SkillLinkCheckCompleted(Event):
    """Der LinkHealthChecker hat HEAD-Requests fuer die URLs eines Skills
    abgeschlossen und das Ergebnis im Cache aktualisiert.
    """
    skill_name: str = ""
    healthy: int = 0
    broken: int = 0


# ----------------------------------------------------------------------
# Activation-Events (Skills-Brain-Integration, Phase Skills-1)
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SkillDirectTriggered(Event):
    """Skill wurde via TriggerMatcher direkt aktiviert (Pre-Brain-Hook).

    Komplementaer zu SkillStarted: SkillDirectTriggered markiert die
    *Aktivierungs-Entscheidung* (Brain bypassed), SkillStarted markiert
    den eigentlichen Run-Beginn im SkillRunner. Forward-compatible mit
    Awareness-Layer (A0-A5) via ``trigger_type`` als activation_path-
    Discriminator.
    """
    skill_name: str = ""
    trigger_type: str = ""   # "voice_direct" | "hotkey" | "cron"
