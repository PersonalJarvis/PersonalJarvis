"""The roster: agent records with validation on top of the store's raw rows.

Identity rules (agent-definition §2, §6):

* ``name`` is unique, case-insensitive; creating an agent with an existing
  name ADOPTS the row instead of minting a second one (adopt-before-mint) —
  the one invariant that keeps "one agent, one chat" true forever.
* ``agent_id`` is a slug derived from the name once and never changes; the
  canonical chat id is ``society:<agent_id>`` (a pure function, no column).
* Exactly one ``lead`` exists and it is Jarvis; creating another lead is
  refused with a typed reason.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Final

from .events import (
    AgentState,
    BrowserMode,
    Checkpoint,
    GrantMode,
    KnowledgeScope,
    PermissionCeiling,
    Tier,
    now_ms,
)
from .failure_reasons import FailureReason
from .store import SocietyStore

__all__ = [
    "AgentRecord",
    "LEAD_AGENT_ID",
    "Roster",
    "RosterError",
    "canonical_session_id",
    "slugify",
]

LEAD_AGENT_ID: Final[str] = "jarvis"

_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[^/\\:@#<>\"'`]{1,40}$")
_MAX_TITLE: Final[int] = 120
_MAX_DESCRIPTION: Final[int] = 20_000


class RosterError(ValueError):
    """A roster rule was violated; ``reason`` is the typed code."""

    def __init__(self, reason: FailureReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def slugify(name: str) -> str:
    """``"Mail Bot"`` → ``"mail-bot"``; diacritics folded, never empty."""
    # Fold the sharp s before NFKD drops it (it has no decomposition).
    folded = name.replace("ß", "ss")  # i18n-allow: unicode folding, not prose
    text = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "agent"


def canonical_session_id(agent_id: str) -> str:
    """The agent's one forever-chat on the agent_chat store."""
    return f"society:{agent_id}"


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class AgentRecord:
    agent_id: str
    name: str
    title: str
    description: str
    tier: Tier
    parent_agent_id: str | None
    state: AgentState
    avatar: dict[str, Any]
    checkpoint: Checkpoint
    provider: str
    model: str
    effort: str
    #: The subscription seat (``jarvis.agent_accounts`` id) a CLI-seated agent
    #: runs on; empty = the platform's active account.
    account_id: str
    grant_mode: GrantMode
    grants: list[str]
    focus: list[str]
    denies: list[str]
    skills: list[str] | None
    workspace_dir: str
    wiki_namespace: str
    knowledge_scope: KnowledgeScope
    permission_ceiling: PermissionCeiling
    approval_rules: dict[str, list[str]]
    daily_budget_usd: float
    max_concurrent_runs: int
    browser_mode: BrowserMode
    browser_allowed_domains: list[str]
    created_ms: int
    updated_ms: int
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        return canonical_session_id(self.agent_id)

    @property
    def may_assign(self) -> bool:
        return self.tier in (Tier.LEAD, Tier.ORCHESTRATOR)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "tier": str(self.tier),
            "parent_agent_id": self.parent_agent_id,
            "state": str(self.state),
            "avatar": self.avatar,
            "checkpoint": str(self.checkpoint),
            "provider": self.provider,
            "model": self.model,
            "effort": self.effort,
            "account_id": self.account_id,
            "grant_mode": str(self.grant_mode),
            "grants": list(self.grants),
            "focus": list(self.focus),
            "denies": list(self.denies),
            "skills": list(self.skills) if self.skills is not None else None,
            "workspace_dir": self.workspace_dir,
            "wiki_namespace": self.wiki_namespace,
            "knowledge_scope": str(self.knowledge_scope),
            "permission_ceiling": str(self.permission_ceiling),
            "approval_rules": {
                "require_approval": list(self.approval_rules.get("require_approval", [])),
                "always_allow": list(self.approval_rules.get("always_allow", [])),
            },
            "daily_budget_usd": self.daily_budget_usd,
            "max_concurrent_runs": self.max_concurrent_runs,
            "browser_mode": str(self.browser_mode),
            "browser_allowed_domains": list(self.browser_allowed_domains),
            "session_id": self.session_id,
            "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
            "stats": dict(self.stats),
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> AgentRecord:
        rules = _loads(row.get("approval_rules_json"), {})
        if not isinstance(rules, dict):
            rules = {}
        skills_raw = row.get("skills_json")
        skills = None if skills_raw is None else list(_loads(skills_raw, []))
        return cls(
            agent_id=str(row["agent_id"]),
            name=str(row["name"]),
            title=str(row.get("title") or ""),
            description=str(row.get("description") or ""),
            tier=Tier(str(row["tier"])),
            parent_agent_id=row.get("parent_agent_id"),
            state=AgentState(str(row.get("state") or "active")),
            avatar=dict(_loads(row.get("avatar_json"), {})),
            checkpoint=Checkpoint(str(row.get("checkpoint") or "idle")),
            provider=str(row.get("provider") or ""),
            model=str(row.get("model") or ""),
            effort=str(row.get("effort") or ""),
            account_id=str(row.get("account_id") or ""),
            grant_mode=GrantMode(str(row.get("grant_mode") or "all")),
            grants=list(_loads(row.get("grants_json"), [])),
            focus=list(_loads(row.get("focus_json"), [])),
            denies=list(_loads(row.get("denies_json"), [])),
            skills=skills,
            workspace_dir=str(row.get("workspace_dir") or ""),
            wiki_namespace=str(row.get("wiki_namespace") or ""),
            knowledge_scope=KnowledgeScope(str(row.get("knowledge_scope") or "shared")),
            permission_ceiling=PermissionCeiling(str(row.get("permission_ceiling") or "monitor")),
            approval_rules={
                "require_approval": [str(x) for x in rules.get("require_approval", [])],
                "always_allow": [str(x) for x in rules.get("always_allow", [])],
            },
            daily_budget_usd=float(row.get("daily_budget_usd") or 0.0),
            max_concurrent_runs=int(row.get("max_concurrent_runs") or 1),
            browser_mode=BrowserMode(str(row.get("browser_mode") or "own")),
            browser_allowed_domains=[
                str(x) for x in _loads(row.get("browser_allowed_domains_json"), [])
            ],
            created_ms=int(row.get("created_ms") or 0),
            updated_ms=int(row.get("updated_ms") or 0),
        )


_EDITABLE: Final[frozenset[str]] = frozenset(
    {
        "title",
        "description",
        "tier",
        "parent_agent_id",
        "state",
        "avatar",
        "checkpoint",
        "provider",
        "model",
        "effort",
        "account_id",
        "grant_mode",
        "grants",
        "focus",
        "denies",
        "skills",
        "workspace_dir",
        "wiki_namespace",
        "knowledge_scope",
        "permission_ceiling",
        "approval_rules",
        "daily_budget_usd",
        "max_concurrent_runs",
        "browser_mode",
        "browser_allowed_domains",
    }
)

_JSON_FIELDS: Final[dict[str, str]] = {
    "avatar": "avatar_json",
    "grants": "grants_json",
    "focus": "focus_json",
    "denies": "denies_json",
    "skills": "skills_json",
    "approval_rules": "approval_rules_json",
    "browser_allowed_domains": "browser_allowed_domains_json",
}


def _validate_name(name: str) -> str:
    cleaned = " ".join(str(name or "").split())
    if not _NAME_RE.match(cleaned):
        raise RosterError(
            FailureReason.BLOCKED_BY_POLICY,
            "agent name must be 1-40 characters without whitespace-only, slashes, @, #, quotes",
        )
    return cleaned


def _validate_rules(rules: Any) -> dict[str, list[str]]:
    if rules is None:
        return {"require_approval": [], "always_allow": []}
    if not isinstance(rules, dict):
        raise RosterError(FailureReason.BLOCKED_BY_POLICY, "approval_rules must be an object")
    out: dict[str, list[str]] = {"require_approval": [], "always_allow": []}
    for key in out:
        raw = rules.get(key, [])
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            raise RosterError(
                FailureReason.BLOCKED_BY_POLICY, f"approval_rules.{key} must be a list of strings"
            )
        out[key] = [x.strip() for x in raw if x.strip()]
    return out


def _enum(kind: Any, value: Any, field_name: str) -> str:
    try:
        return str(kind(str(value)))
    except ValueError as exc:
        allowed = ", ".join(str(m) for m in kind)
        raise RosterError(
            FailureReason.BLOCKED_BY_POLICY, f"{field_name} must be one of: {allowed}"
        ) from exc


def _coerce(field_name: str, value: Any) -> Any:
    """Validate one editable field and return its column value."""
    if field_name == "title":
        return str(value or "")[:_MAX_TITLE]
    if field_name == "description":
        return str(value or "")[:_MAX_DESCRIPTION]
    if field_name == "tier":
        return _enum(Tier, value, field_name)
    if field_name == "state":
        return _enum(AgentState, value, field_name)
    if field_name == "checkpoint":
        return _enum(Checkpoint, value, field_name)
    if field_name == "grant_mode":
        return _enum(GrantMode, value, field_name)
    if field_name == "knowledge_scope":
        return _enum(KnowledgeScope, value, field_name)
    if field_name == "permission_ceiling":
        return _enum(PermissionCeiling, value, field_name)
    if field_name == "browser_mode":
        return _enum(BrowserMode, value, field_name)
    if field_name == "browser_allowed_domains":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise RosterError(
                FailureReason.BLOCKED_BY_POLICY, "browser_allowed_domains must be a list"
            )
        return json.dumps(sorted({x.strip().lower() for x in value if x.strip()}))
    if field_name in ("grants", "focus", "denies"):
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise RosterError(
                FailureReason.BLOCKED_BY_POLICY, f"{field_name} must be a list of capability ids"
            )
        return json.dumps(sorted({x.strip() for x in value if x.strip()}))
    if field_name == "skills":
        if value is None:
            return None
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise RosterError(FailureReason.BLOCKED_BY_POLICY, "skills must be a list or null")
        return json.dumps(sorted({x.strip() for x in value if x.strip()}))
    if field_name == "avatar":
        if not isinstance(value, dict):
            raise RosterError(FailureReason.BLOCKED_BY_POLICY, "avatar must be an object")
        return json.dumps(value, ensure_ascii=False)
    if field_name == "approval_rules":
        return json.dumps(_validate_rules(value))
    if field_name == "daily_budget_usd":
        amount = float(value)
        if amount < 0:
            raise RosterError(FailureReason.BLOCKED_BY_POLICY, "daily_budget_usd must be >= 0")
        return amount
    if field_name == "max_concurrent_runs":
        runs = int(value)
        if runs < 1 or runs > 10:
            raise RosterError(FailureReason.BLOCKED_BY_POLICY, "max_concurrent_runs must be 1-10")
        return runs
    if field_name in (
        "provider",
        "model",
        "effort",
        "account_id",
        "workspace_dir",
        "wiki_namespace",
    ):
        return str(value or "")
    if field_name == "parent_agent_id":
        return str(value) if value else None
    raise RosterError(FailureReason.BLOCKED_BY_POLICY, f"unknown field {field_name}")


class Roster:
    def __init__(self, store: SocietyStore) -> None:
        self._store = store
        #: The live (non-archived) rows as of the last write or ``list()`` —
        #: what a synchronous reader on the brain's hot path may see without
        #: touching the database (the lead card, ``lead_card.py``). Every
        #: write through this class refreshes it, so a freshly created agent
        #: is on the very next turn's card.
        self._snapshot: tuple[AgentRecord, ...] = ()
        self._epoch: int = 0

    def snapshot(self) -> list[AgentRecord]:
        """The roster as last read — synchronous, no IO; ``[]`` before the first read."""
        return list(self._snapshot)

    @property
    def epoch(self) -> int:
        """Bumps on every roster write; a cheap "did the team change" probe."""
        return self._epoch

    async def refresh(self) -> list[AgentRecord]:
        """Re-read the live rows into the snapshot (``list()`` does the same)."""
        return await self.list()

    def _remember(self, agents: list[AgentRecord]) -> None:
        self._snapshot = tuple(agents)
        self._epoch += 1

    async def create(
        self,
        *,
        name: str,
        title: str = "",
        description: str = "",
        tier: Tier | str = Tier.SPECIALIST,
        **fields: Any,
    ) -> tuple[AgentRecord, bool]:
        """Create an agent; returns ``(record, created)``.

        An existing name adopts the row (``created=False``) and leaves it
        untouched — the caller decides whether to PATCH.
        """
        clean_name = _validate_name(name)
        existing = await self._store.get_agent_row_by_name(clean_name)
        if existing is not None:
            return await self._hydrate(existing), False
        tier_value = Tier(_enum(Tier, tier, "tier"))
        agent_id = slugify(clean_name)
        if tier_value is Tier.LEAD and agent_id != LEAD_AGENT_ID:
            raise RosterError(
                FailureReason.TIER_NOT_ALLOWED, "exactly one lead exists and it is Jarvis"
            )
        same_slug = await self._store.get_agent_row(agent_id)
        if same_slug is not None:
            # Same slug, different spelling ("Mail Bot" vs "mail-bot"): adopt.
            return await self._hydrate(same_slug), False
        parent = fields.get("parent_agent_id")
        if parent and await self._store.get_agent_row(str(parent)) is None:
            raise RosterError(FailureReason.TARGET_UNKNOWN, f"parent agent {parent!r} not found")
        now = now_ms()
        row: dict[str, Any] = {
            "agent_id": agent_id,
            "name": clean_name,
            "title": _coerce("title", title),
            "description": _coerce("description", description),
            "tier": str(tier_value),
            "created_ms": now,
            "updated_ms": now,
            "workspace_dir": f"society/{agent_id}/workspace",
            "wiki_namespace": f"society/{agent_id}/",
        }
        if tier_value is Tier.ORCHESTRATOR:
            row["max_concurrent_runs"] = 3
        for key, value in fields.items():
            if key not in _EDITABLE or key in ("title", "description", "tier"):
                raise RosterError(FailureReason.BLOCKED_BY_POLICY, f"unknown field {key}")
            row[_JSON_FIELDS.get(key, key)] = _coerce(key, value)
        await self._store.insert_agent(row)
        created = await self._store.get_agent_row(agent_id)
        assert created is not None
        record = await self._hydrate(created)
        await self.refresh()
        return record, True

    async def get(self, agent_id: str) -> AgentRecord | None:
        row = await self._store.get_agent_row(agent_id)
        return await self._hydrate(row) if row else None

    async def resolve(self, target: str) -> AgentRecord | None:
        """By id, then by name (case-insensitive); ``None`` when unknown."""
        row = await self._store.get_agent_row(target)
        if row is None:
            row = await self._store.get_agent_row_by_name(target)
        if row is None:
            row = await self._store.get_agent_row(slugify(target))
        return await self._hydrate(row) if row else None

    async def list(self, *, include_archived: bool = False) -> list[AgentRecord]:
        rows = await self._store.list_agent_rows(include_archived=include_archived)
        agents = [await self._hydrate(r) for r in rows]
        if not include_archived:
            self._remember(agents)
        return agents

    async def update(self, agent_id: str, fields: dict[str, Any]) -> AgentRecord:
        current = await self._store.get_agent_row(agent_id)
        if current is None:
            raise RosterError(FailureReason.TARGET_UNKNOWN, f"agent {agent_id!r} not found")
        columns: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in _EDITABLE:
                raise RosterError(FailureReason.BLOCKED_BY_POLICY, f"field {key!r} is not editable")
            if key == "tier" and agent_id == LEAD_AGENT_ID and str(value) != str(Tier.LEAD):
                raise RosterError(FailureReason.TIER_NOT_ALLOWED, "Jarvis stays the lead")
            if key == "tier" and str(value) == str(Tier.LEAD) and agent_id != LEAD_AGENT_ID:
                raise RosterError(FailureReason.TIER_NOT_ALLOWED, "only Jarvis is the lead")
            if key == "parent_agent_id" and value:
                if str(value) == agent_id:
                    raise RosterError(
                        FailureReason.BLOCKED_BY_POLICY, "an agent cannot parent itself"
                    )
                if await self._store.get_agent_row(str(value)) is None:
                    raise RosterError(FailureReason.TARGET_UNKNOWN, f"parent {value!r} not found")
            columns[_JSON_FIELDS.get(key, key)] = _coerce(key, value)
        await self._store.update_agent(agent_id, columns)
        row = await self._store.get_agent_row(agent_id)
        assert row is not None
        record = await self._hydrate(row)
        await self.refresh()
        return record

    async def archive(self, agent_id: str) -> AgentRecord:
        if agent_id == LEAD_AGENT_ID:
            raise RosterError(FailureReason.TIER_NOT_ALLOWED, "the lead cannot be archived")
        return await self.update(agent_id, {"state": AgentState.ARCHIVED})

    async def _hydrate(self, row: dict[str, Any]) -> AgentRecord:
        record = AgentRecord.from_row(row)
        record.stats = await self._store.agent_stats(record.agent_id)
        return record
