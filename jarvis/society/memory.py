"""The society's one memory service — the Memory House's backend.

``docs/agent-society/memory-house.md`` §3. The Obsidian vault is the memory;
this service is the only code path an agent's memory goes through:

* ``head``            the agent's own memory in the briefing
* ``recall``          a ranked, bounded lookup in the agent's own notes only
* ``remember``        append a durable fact to the agent's ``memory.md`` (own, free)
* ``note``            a dated work note under ``society/<agent>/`` (own, free)
* ``propose_shared``  a note plus an approval item "promote to shared knowledge"
* ``promote``         the approved copy into ``society/shared/`` with ``reviewed: true``
* ``dismiss``         mark a staging row reviewed without promotion
* ``overview``        what the Memory House drawer shows

Every operation appends one ``DIGEST`` envelope ``{kind: "memory", …}`` to the
board and reports activity to the checkpoint engine, so the ledger and the
island both see "Scout remembered X". Agents never write outside their own
folder; ``shared/`` is written by ``promote`` only, which the approvals route
calls. Secrets are refused at the door (AP-2/AP-12). Rankings are
deterministic and dependency-free. Existing shared pages and their management
operations remain available to the user, but are not injected or retrieved by
ordinary agents. Deliberate user-wiki access uses the separately granted wiki tools.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from .events import MsgType, SocietyEnvelope, now_ms
from .roster import AgentRecord, slugify

log = logging.getLogger(__name__)

__all__ = [
    "MEMORY_SHARE_CAPABILITY",
    "MemoryRefused",
    "MemoryHit",
    "SocietyMemory",
    "atomic_write",
    "resolve_society_vault",
]

#: The capability id an approval item carries when an agent proposes shared knowledge.
MEMORY_SHARE_CAPABILITY: Final[str] = "core:memory:share"
#: The frontmatter ``type`` that makes an agent page schema-valid for the vault index.
PAGE_TYPE: Final[str] = "society"
#: Seconds a memory touch keeps a figure at the Memory House. The house stands at the
#: island's north end, ~50 tiles from the square: the walk alone takes most of a minute.
MEMORY_HOLD_S: Final[float] = 60.0

_MAX_TEXT: Final[int] = 40_000
_HEAD_CHARS: Final[int] = 1_200
_SNIPPET_CHARS: Final[int] = 200
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_KV_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$", re.MULTILINE)
_TOKEN_RE = re.compile(r"[\w][\w'-]{2,}", re.UNICODE)
_FRONTMATTER_SAFE = re.compile(r"[\"\r\n]+")
_SCOPE_BOOST: Final[dict[str, float]] = {"own": 0.30, "shared": 0.20, "user": 0.10, "other": 0.0}


class MemoryRefused(ValueError):
    """A refused memory operation (secret in the body, unknown row, bad path)."""


@dataclass(frozen=True, slots=True)
class MemoryHit:
    path: str
    title: str
    scope: str  # own | shared | user | other
    origin: str  # user | tool | web | agent
    reviewed: bool
    author: str
    score: float
    snippet: str
    updated_ms: int

    def label(self) -> str:
        """The trust marker every result line carries."""
        if self.scope == "own":
            return "own"
        if self.scope == "shared":
            return "shared"
        if self.scope == "user":
            return "user"
        who = self.author.removeprefix("agent:") or "agent"
        return f"unreviewed · {self.origin} · {who}" if not self.reviewed else f"reviewed · {who}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["label"] = self.label()
        return data


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".society-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def resolve_society_vault(cfg: Any) -> Path:
    """The vault the agents write into: the configured root wins, the last
    resolution the app made is the fallback for a config without one."""
    from jarvis.memory.wiki.vault_root import last_resolution, resolve_vault_root

    raw = None
    for holder in (getattr(cfg, "wiki", None), getattr(cfg, "memory", None)):
        raw = getattr(holder, "vault_root", None)
        if raw:
            break
    if raw:
        return resolve_vault_root(raw).path
    known = last_resolution()
    if known is not None:
        return known.path
    return resolve_vault_root(None).path


def _parse(raw: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    fm: dict[str, str] = {}
    for key, value in _KV_RE.findall(match.group(1)):
        fm[key.strip()] = value.strip().strip('"')
    return fm, raw[match.end() :]


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text)}


def _contains_secret(text: str) -> bool:
    try:
        from jarvis.memory.wiki.secret_guard import contains_secret

        return bool(contains_secret(text))
    except Exception:  # noqa: BLE001 — a missing guard never opens the door
        log.debug("society memory: secret guard unavailable, refusing nothing", exc_info=True)
        return False


@dataclass(frozen=True, slots=True)
class _Page:
    path: Path
    rel: str
    fm: dict[str, str]
    body: str
    updated_ms: int


class SocietyMemory:
    """See the module docstring. ``runtime`` gives the store, roster and approvals;
    ``vault_root`` resolves lazily because the config is."""

    def __init__(
        self,
        runtime: Any,
        *,
        vault_root: Callable[[], Path] | None = None,
        on_activity: Callable[[str], Any] | None = None,
    ) -> None:
        self._runtime = runtime
        self._vault_root = vault_root
        self._on_activity = on_activity

    # ---------------------------------------------------------------- paths

    def root(self, override: Path | None = None) -> Path:
        if override is not None:
            return Path(override)
        if self._vault_root is not None:
            return Path(self._vault_root())
        return resolve_society_vault(self._runtime._get_cfg())  # noqa: SLF001 — the runtime owns its config getter

    @staticmethod
    def namespace(root: Path, agent_id: str) -> Path:
        return root / "society" / agent_id

    @staticmethod
    def shared_dir(root: Path) -> Path:
        return root / "society" / "shared"

    def _contained(self, root: Path, rel: str) -> Path:
        base = (root / "society").resolve()
        target = (root / rel).resolve()
        if base != target and base not in target.parents:
            raise MemoryRefused("path outside the society folder")
        return target

    # ----------------------------------------------------------- frontmatter

    @staticmethod
    def _frontmatter(title: str, agent_id: str, origin: str, trace: str, **extra: str) -> str:
        safe_title = _FRONTMATTER_SAFE.sub(" ", title).strip()
        lines = [
            "---",
            f"type: {PAGE_TYPE}",
            f'title: "{safe_title}"',
            f"author: agent:{agent_id}",
            f"origin: {origin}",
            f"trace: {trace or 'none'}",
            "reviewed: false",
        ]
        lines.extend(f"{k}: {v}" for k, v in extra.items())
        lines.append("---")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _origin(origin: str) -> str:
        origin = str(origin or "agent").strip().lower()
        return origin if origin in ("tool", "web", "agent", "user") else "agent"

    # --------------------------------------------------------------- writes

    async def remember(
        self,
        agent: AgentRecord,
        text: str,
        *,
        origin: str = "agent",
        trace: str = "",
        root: Path | None = None,
    ) -> str:
        """Append one durable fact to the agent's memory page. Returns the vault-relative path."""
        text = str(text or "").strip()[:_MAX_TEXT]
        if not text:
            raise MemoryRefused("text is required")
        if _contains_secret(text):
            raise MemoryRefused("memory never holds a secret")
        vault = self.root(root)
        path = self.namespace(vault, agent.agent_id) / "memory.md"
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        if not existing:
            existing = (
                self._frontmatter(f"{agent.name} — memory", agent.agent_id, "agent", trace) + "\n"
            )
        today = _dt.datetime.now(tz=_dt.UTC).date().isoformat()
        atomic_write(path, existing.rstrip("\n") + f"\n\n## {today}\n\n{text}\n")
        rel = path.relative_to(vault).as_posix()
        await self._stage(agent, rel, self._origin(origin), trace, text[:280])
        await self._touch(agent, "remember", {"path": rel, "scope": "own"})
        return rel

    async def note(
        self,
        agent: AgentRecord,
        title: str,
        text: str,
        *,
        origin: str = "agent",
        trace: str = "",
        root: Path | None = None,
        proposed_shared: bool = False,
    ) -> tuple[str, int]:
        """A dated page under the agent's folder. Returns (vault-relative path, staging row id)."""
        text = str(text or "").strip()[:_MAX_TEXT]
        if not text:
            raise MemoryRefused("text is required")
        if _contains_secret(text):
            raise MemoryRefused("memory never holds a secret")
        vault = self.root(root)
        title = _FRONTMATTER_SAFE.sub(" ", str(title or text[:60])).strip() or "note"
        slug = slugify(title)[:60] or "note"
        today = _dt.datetime.now(tz=_dt.UTC).date().isoformat()
        folder = self.namespace(vault, agent.agent_id)
        path = folder / f"{today}-{slug}.md"
        n = 2
        while path.exists():
            path = folder / f"{today}-{slug}-{n}.md"
            n += 1
        extra = {"proposed": "shared"} if proposed_shared else {}
        page = (
            self._frontmatter(title, agent.agent_id, self._origin(origin), trace, **extra)
            + f"\n# {title}\n\n{text}\n"
        )
        atomic_write(path, page)
        rel = path.relative_to(vault).as_posix()
        row_id = await self._stage(
            agent, rel, self._origin(origin), trace, f"{title}: {text[:220]}"
        )
        await self._touch(agent, "note", {"path": rel, "scope": "own", "title": title})
        return rel, row_id

    async def propose_shared(
        self,
        agent: AgentRecord,
        title: str,
        text: str,
        *,
        origin: str = "agent",
        trace: str = "",
        root: Path | None = None,
    ) -> dict[str, Any]:
        """The note lands in the agent's folder; promotion waits for the person."""
        rel, row_id = await self.note(
            agent, title, text, origin=origin, trace=trace, root=root, proposed_shared=True
        )
        item = await self._runtime.approvals.enqueue(
            agent_id=agent.agent_id,
            trace_id=trace or f"memory:{agent.agent_id}",
            capability=MEMORY_SHARE_CAPABILITY,
            action={"knowledge_id": row_id, "path": rel, "title": title},
            summary=f"Promote to shared knowledge: {title}",
        )
        await self._touch(agent, "propose_shared", {"path": rel, "approval_id": item.id})
        return {"path": rel, "knowledge_id": row_id, "approval_id": item.id}

    async def promote(self, knowledge_id: int, *, root: Path | None = None) -> str:
        """Copy a staged page into ``society/shared/`` as reviewed knowledge."""
        row = await self._row(knowledge_id)
        vault = self.root(root)
        src = self._contained(vault, str(row["wiki_path"]))
        if not src.is_file():
            raise MemoryRefused(f"page missing: {row['wiki_path']}")
        fm, body = _parse(src.read_text(encoding="utf-8"))
        title = fm.get("title") or src.stem
        slug = slugify(title)[:60] or src.stem
        dest = self.shared_dir(vault) / f"{slug}.md"
        n = 2
        marker = f"promoted_from: {row['wiki_path']}"
        while dest.exists() and marker not in dest.read_text(encoding="utf-8"):
            dest = self.shared_dir(vault) / f"{slug}-{n}.md"
            n += 1
        head = (
            "---\n"
            f"type: {PAGE_TYPE}\n"
            f'title: "{_FRONTMATTER_SAFE.sub(" ", title).strip()}"\n'
            f"author: {fm.get('author', 'agent:' + str(row['agent_id']))}\n"
            f"origin: {fm.get('origin', row.get('origin', 'agent'))}\n"
            f"trace: {fm.get('trace', 'none')}\n"
            "reviewed: true\n"
            f"promoted_from: {row['wiki_path']}\n"
            f"promoted_ms: {now_ms()}\n"
            "---\n"
        )
        atomic_write(dest, head + body.lstrip("\n"))
        await self._runtime.store.mark_knowledge_reviewed(int(knowledge_id))
        rel = dest.relative_to(vault).as_posix()
        agent = await self._runtime.roster.get(str(row["agent_id"]))
        if agent is not None:
            await self._touch(agent, "promote", {"path": rel, "from": row["wiki_path"]}, by="user")
        return rel

    async def dismiss(self, knowledge_id: int) -> None:
        row = await self._row(knowledge_id)
        await self._runtime.store.mark_knowledge_reviewed(int(knowledge_id))
        agent = await self._runtime.roster.get(str(row["agent_id"]))
        if agent is not None:
            await self._touch(agent, "dismiss", {"path": row["wiki_path"]}, by="user")

    # ---------------------------------------------------------------- reads

    def head(self, agent: AgentRecord, *, root: Path | None = None) -> str:
        """The briefing's ``## Your memory`` section. Byte-stable between writes."""
        vault = self.root(root)
        page = self.namespace(vault, agent.agent_id) / "memory.md"
        lines = ["## Your memory"]
        if page.is_file():
            _, body = _parse(page.read_text(encoding="utf-8"))
            body = body.strip()
            if body:
                clipped = body[:_HEAD_CHARS]
                if len(body) > _HEAD_CHARS:
                    clipped = clipped.rsplit("\n", 1)[0] + "\n…"
                lines.append(clipped)
        if len(lines) == 1:
            lines.append("Nothing remembered yet.")
        lines.append(
            "Recall your own notes with society_memory_recall; keep personal durable facts with "
            "society_wiki_note (kind memory) and your findings as kind note. "
            "Other agents' notes and shared knowledge are not part of your memory."
        )
        return "\n".join(lines)

    async def recall(
        self, agent: AgentRecord, query: str, *, k: int = 5, root: Path | None = None
    ) -> list[MemoryHit]:
        query = str(query or "").strip()
        if not query:
            return []
        vault = self.root(root)
        qtokens = _tokens(query)
        hits: list[MemoryHit] = []
        for page in self._pages(vault, agent_id=agent.agent_id):
            scope = self._scope_of(page, agent.agent_id)
            title = page.fm.get("title") or page.path.stem
            ptokens = _tokens(title + "\n" + page.body)
            matched = len(qtokens & ptokens)
            if not qtokens or matched == 0:
                continue
            reviewed = page.fm.get("reviewed", "false").lower() == "true"
            score = matched / len(qtokens) + _SCOPE_BOOST[scope]
            if scope == "other" and not reviewed:
                score -= 0.15
            hits.append(
                MemoryHit(
                    path=page.rel,
                    title=title,
                    scope=scope,
                    origin=page.fm.get("origin", "agent"),
                    reviewed=reviewed or scope in ("own", "shared", "user"),
                    author=page.fm.get("author", ""),
                    score=round(score, 4),
                    snippet=self._snippet(page.body, qtokens),
                    updated_ms=page.updated_ms,
                )
            )
        hits.sort(key=lambda h: (-h.score, -h.updated_ms, h.path))
        picked = hits[: max(1, int(k))]
        await self._touch(
            agent,
            "recall",
            {"query": query[:200], "hits": len(picked), "paths": [h.path for h in picked]},
        )
        return picked

    async def overview(self, *, root: Path | None = None) -> dict[str, Any]:
        vault = self.root(root)
        shared = []
        for page in self._pages(vault):
            if page.rel.startswith("society/shared/"):
                shared.append(
                    {
                        "path": page.rel,
                        "title": page.fm.get("title") or page.path.stem,
                        "author": page.fm.get("author", ""),
                        "updated_ms": page.updated_ms,
                    }
                )
        shared.sort(key=lambda s: -s["updated_ms"])
        agents = []
        for agent in await self._runtime.roster.list():
            page = self.namespace(vault, agent.agent_id) / "memory.md"
            body = ""
            if page.is_file():
                _, body = _parse(page.read_text(encoding="utf-8"))
            notes = (
                sum(
                    1
                    for p in self.namespace(vault, agent.agent_id).glob("*.md")
                    if p.name != "memory.md"
                )
                if self.namespace(vault, agent.agent_id).is_dir()
                else 0
            )
            agents.append(
                {
                    "agent_id": agent.agent_id,
                    "name": agent.name,
                    "title": agent.title,
                    "memory_head": body.strip()[:600],
                    "notes": notes,
                    "checkpoint": str(agent.checkpoint),
                }
            )
        unreviewed = await self._runtime.store.list_knowledge_rows(reviewed=False, limit=100)
        return {
            "shared": shared,
            "agents": agents,
            "unreviewed": [
                {
                    "id": int(r["id"]),
                    "agent_id": r["agent_id"],
                    "path": r["wiki_path"],
                    "origin": r["origin"],
                    "summary": r["summary"],
                    "created_ms": int(r["created_ms"]),
                }
                for r in unreviewed
            ],
            "vault_root": str(vault),
        }

    # ------------------------------------------------------------- internals

    async def _row(self, knowledge_id: int) -> dict[str, Any]:
        rows = await self._runtime.store.list_knowledge_rows(limit=100_000)
        for row in rows:
            if int(row["id"]) == int(knowledge_id):
                return row
        raise MemoryRefused(f"knowledge row {knowledge_id} not found")

    async def _stage(
        self, agent: AgentRecord, rel: str, origin: str, trace: str, summary: str
    ) -> int:
        try:
            return int(
                await self._runtime.store.insert_knowledge(
                    {
                        "agent_id": agent.agent_id,
                        "wiki_path": rel,
                        "origin": origin,
                        "source_event": None,
                        "trace_id": trace or None,
                        "reviewed": 0,
                        "summary": summary,
                        "created_ms": now_ms(),
                    }
                )
            )
        except Exception:  # noqa: BLE001 — the page is written; the staging row is bookkeeping
            log.warning("society memory: staging row not recorded for %s", rel, exc_info=True)
            return 0

    async def _touch(
        self, agent: AgentRecord, op: str, payload: dict[str, Any], *, by: str | None = None
    ) -> None:
        try:
            await self._runtime.store.append_and_publish(
                SocietyEnvelope(
                    msg_type=MsgType.DIGEST,
                    from_agent=by or agent.agent_id,
                    to_agent=None,
                    trace_id=f"memory:{agent.agent_id}",
                    payload={"kind": "memory", "op": op, "agent_id": agent.agent_id, **payload},
                )
            )
        except Exception:  # noqa: BLE001 — the memory is written; the board line is a courtesy
            log.warning("society memory: board digest not written (%s)", op, exc_info=True)
        if self._on_activity is not None and by is None:
            try:
                maybe = self._on_activity(agent.agent_id)
                if hasattr(maybe, "__await__"):
                    await maybe
            except Exception:  # noqa: BLE001 — the world is a projection; it never breaks memory
                log.debug("society memory: activity hook failed", exc_info=True)

    def _pages(self, vault: Path, *, agent_id: str | None = None) -> list[_Page]:
        folder = vault / "society" if agent_id is None else self.namespace(vault, agent_id)
        if not folder.is_dir():
            return []
        pages: list[_Page] = []
        for path in sorted(folder.rglob("*.md")):
            if path.name.startswith(".") or path.name == "README.md":
                continue
            try:
                raw = path.read_text(encoding="utf-8")
                updated = int(path.stat().st_mtime * 1000)
            except OSError:
                continue
            fm, body = _parse(raw)
            pages.append(_Page(path, path.relative_to(vault).as_posix(), fm, body, updated))
        return pages

    @staticmethod
    def _scope_of(page: _Page, agent_id: str) -> str:
        parts = page.rel.split("/")
        if len(parts) < 3 or parts[0] != "society":
            return "user"
        if parts[1] == "shared":
            return "shared"
        return "own" if parts[1] == agent_id else "other"

    def _shared_titles(self, vault: Path) -> list[str]:
        folder = self.shared_dir(vault)
        if not folder.is_dir():
            return []
        titles: list[str] = []
        for path in sorted(folder.glob("*.md")):
            fm, _ = _parse(path.read_text(encoding="utf-8"))
            titles.append(fm.get("title") or path.stem)
        return titles

    @staticmethod
    def _snippet(body: str, qtokens: set[str]) -> str:
        text = " ".join(body.split())
        low = text.lower()
        at = min((low.find(t) for t in qtokens if low.find(t) >= 0), default=0)
        start = max(0, at - 60)
        return text[start : start + _SNIPPET_CHARS]

    def _user_hits(self, vault: Path, query: str, k: int) -> list[MemoryHit]:
        """The user's own vault pages, through the wiki's FTS index when it exists."""
        try:
            from jarvis.memory.wiki.db_path import resolve_wiki_db_path
            from jarvis.memory.wiki.search import VaultSearch

            cfg = self._runtime._get_cfg()  # noqa: SLF001 — the runtime owns its config getter
            data_dir = getattr(getattr(cfg, "memory", None), "data_dir", None)
            if not data_dir:
                return []  # no app config: no user index to consult
            db = resolve_wiki_db_path(data_dir)
            if not db.is_file():
                return []
            hits = VaultSearch(vault, db_path=db).search(query, k=k)
        except Exception:  # noqa: BLE001 — the user's index is optional for the agents
            log.debug("society memory: vault search unavailable", exc_info=True)
            return []
        out: list[MemoryHit] = []
        for hit in hits:
            try:
                rel = Path(hit.path).resolve().relative_to(vault.resolve()).as_posix()
            except ValueError:
                continue  # an index row from another vault is never this agent's memory
            if rel.startswith("society/"):
                continue  # already ranked from the folder scan
            out.append(
                MemoryHit(
                    path=rel,
                    title=hit.title,
                    scope="user",
                    origin="user",
                    reviewed=True,
                    author="user",
                    score=round(min(1.0, float(hit.score)) * 0.9 + _SCOPE_BOOST["user"], 4),
                    snippet=str(hit.snippet or "")[:_SNIPPET_CHARS],
                    updated_ms=0,
                )
            )
        return out
