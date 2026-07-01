"""Pydantic schemas + dataclasses for skills.

Frontmatter is YAML — pydantic validates it structurally. `Skill` itself is
a frozen dataclass (immutability for flight-recorder replay).

Skill lifecycle events inherit from `jarvis.core.events.Event` — but are defined
here (not in events.py) so we don't have to touch the core layer.
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
# Lifecycle state
# ----------------------------------------------------------------------

class SkillLifecycleState(str, Enum):
    """Phases in a skill's lifecycle."""
    DRAFT = "draft"            # not yet validated / broken
    VALIDATED = "validated"    # frontmatter OK, tools exist
    ACTIVE = "active"          # activated by the user, triggers
    DISABLED = "disabled"      # deactivated by the user


# ----------------------------------------------------------------------
# Frontmatter models
# ----------------------------------------------------------------------

TriggerType = Literal["voice", "hotkey", "schedule"]


class SkillTrigger(BaseModel):
    """A trigger — voice pattern, hotkey combo, or cron expression."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: TriggerType
    pattern: str | None = None   # voice: regex or template
    combo: str | None = None     # hotkey: e.g. "ctrl+right_alt+j"
    cron: str | None = None      # schedule: cron expression
    language: list[str] = Field(default_factory=lambda: ["de", "en"])

    @field_validator("pattern", "combo", "cron")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) else v

    def validate_payload(self) -> list[str]:
        """Semantic check: depending on `type`, the matching field must be set."""
        errors: list[str] = []
        if self.type == "voice" and not self.pattern:
            errors.append("voice trigger needs 'pattern'")
        if self.type == "hotkey" and not self.combo:
            errors.append("hotkey trigger needs 'combo'")
        if self.type == "schedule" and not self.cron:
            errors.append("schedule trigger needs 'cron'")
        return errors


class SkillRiskPolicy(BaseModel):
    """Risk-tier configuration for a skill."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    default_tier: RiskTier = "monitor"
    per_tool_overrides: dict[str, RiskTier] = Field(default_factory=dict)
    require_confirmation: list[str] = Field(default_factory=list)


class SkillFrontmatter(BaseModel):
    """YAML frontmatter of a SKILL.md."""
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
    # Phase 7.5: lifecycle state in the frontmatter — OpenClaw-authored skills
    # explicitly carry `state: draft`, enforced by the `draft_writer` (Plan-§AD-8).
    # Default `None` → the loader interprets it as "validated/active" (legacy).
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
    # Instruction-skill model (2026-06-09 rebuild, AD-S5/S7): optional trigger
    # guidance appended to the description in the AVAILABLE SKILLS listing
    # (Anthropic Agent Skills convention), and the execution mode split —
    # inline skills are followed by the brain in the current turn, mission
    # skills are dispatched as background worker briefs.
    when_to_use: str | None = None
    execution: Literal["inline", "mission"] = "inline"

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name must not be empty")
        return v.strip()

    @field_validator("homepage_url", "source_url", "docs_url", mode="before")
    @classmethod
    def _clean_url(cls, v: Any) -> str | None:
        """Trims whitespace, maps empty strings to None, enforces http(s)://.

        Rationale: pydantic's ``HttpUrl`` is too strict (e.g. trailing-slash
        normalization), and we want to store the original URL roundtrip-safely.
        A 2048-char max guards against abuse (homepage field used as a
        JSON payload drop).
        """
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("URL must be a string")
        cleaned = v.strip()
        if not cleaned:
            return None
        if len(cleaned) > 2048:
            raise ValueError("URL too long (max 2048 characters)")
        if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
            raise ValueError("URL must start with http:// or https://")
        return cleaned


# ----------------------------------------------------------------------
# Skill container
# ----------------------------------------------------------------------

RESOURCE_KINDS: tuple[str, ...] = ("references", "scripts", "assets", "agents")


@dataclass(frozen=True, slots=True)
class Skill:
    """A loaded SKILL.md — parsed frontmatter + body + metadata.

    ``resources`` holds the bundle sibling folders (analogous to Anthropic's
    Claude-Skills structure: ``references/``, ``scripts/``, ``assets/``,
    ``agents/``). The value per key is the list of relative file paths to the
    skill root. Empty if the respective folder doesn't exist.
    """
    path: Path
    frontmatter: SkillFrontmatter | None           # None if DRAFT/broken
    body: str
    state: SkillLifecycleState = SkillLifecycleState.DRAFT
    body_hash: str = ""
    error: str | None = None                       # parse/validation error
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
        """Directory containing the SKILL.md — base for resource lookups."""
        return self.path.parent


@dataclass(frozen=True, slots=True)
class SkillResult:
    """The overall result of a SkillRunner.run()."""
    skill_name: str
    success: bool
    steps: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    rendered_body: str = ""
    error: str | None = None
    duration_ms: int = 0


# ----------------------------------------------------------------------
# Skill events (inherit from jarvis.core.events.Event)
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
    """A user-authored skill was created via the desktop app.

    Separate from ``SkillStateChanged`` so the UI can react specifically to the
    authoring event (toast "Skill created", React-Query invalidation).
    """
    skill_name: str = ""
    author: str = ""


@dataclass(frozen=True, slots=True)
class SkillLinkCheckCompleted(Event):
    """The LinkHealthChecker has completed HEAD requests for a skill's URLs
    and updated the result in the cache.
    """
    skill_name: str = ""
    healthy: int = 0
    broken: int = 0


# ----------------------------------------------------------------------
# Activation events (skills-brain integration, Phase Skills-1)
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SkillInvoked(Event):
    """A skill's instructions were loaded for execution (instruction-skill model).

    Emitted on every invocation path — model-decided (``run-skill`` tool),
    direct trigger (voice/chat), hotkey, or cron. This is the single
    observability signal that answers "did a skill actually fire?"
    (2026-06-09 rebuild, AD-S6). ``source`` is one of:
    ``model | trigger | hotkey | cron | chat``.
    """
    skill_name: str = ""
    source: str = ""


@dataclass(frozen=True, slots=True)
class SkillDirectTriggered(Event):
    """Skill was directly activated via the TriggerMatcher (pre-brain hook).

    Complementary to SkillStarted: SkillDirectTriggered marks the
    *activation decision* (brain bypassed), SkillStarted marks the
    actual run start in the SkillRunner. Forward-compatible with the
    awareness layer (A0-A5) via ``trigger_type`` as the activation-path
    discriminator.
    """
    skill_name: str = ""
    trigger_type: str = ""   # "voice_direct" | "hotkey" | "cron"
