"""Workspace-aware target resolution and durable, addressed task delivery.

Names are references, never execution identities. Resolution returns the three
stable IDs needed by send; moving focus, renaming or reordering cannot retarget
an approval. This layer never activates a workspace or composes a second prompt.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from uuid import UUID, uuid4

from jarvis.core.protocols import CodingSessionGateway
from jarvis.live.state import LiveLedger

from .session import (
    MAX_TERMINALS,
    Registry,
    SessionError,
    accepts_prompts,
    terminals_added_event,
)
from .workspace_catalog import project_graph


def _matches(reference: str, *values: str) -> bool:
    return reference.strip().casefold() in {v.strip().casefold() for v in values if v}


# Words people wrap around a name when they speak it ("the Personal-Jarvis
# workspace", "mein Ordner Jarvis"). They never identify anything.
_FILLER = frozenset(
    {
        "the", "my", "a", "workspace", "workspaces", "project", "projects", "folder",
        "der", "die", "das", "mein", "meinem", "meinen", "projekt", "ordner",
        "arbeitsbereich",
    }
)  # fmt: skip
# A resolve is remembered this long so a voice model never has to copy its
# 32-character request_id verbatim (live 2026-10-01: one dropped "0" failed
# four sends in a row).
_RESOLVE_TTL_S = 15 * 60
_NEAR_MISS = 3
_TARGET_KEYS = ("project_id", "workspace_id", "terminal_id")


# An apostrophe inside a name joins, it does not split: the workspace "VM`s"
# is spoken "VMs" (live 2026-10-01), and "vm s" never matched "vms".
_JOINERS = re.compile("['`\u00b4\u2018\u2019]")
# Words around a spoken CLI name that never name the CLI ("Claude Code agent").
_CLI_FILLER = frozenset(
    {
        "agent", "agents", "agenten", "terminal", "terminals", "session", "sessions",
        "pane", "panes", "coding", "cli", "new",
    }
)  # fmt: skip


def _words(text: str) -> list[str]:
    joined = _JOINERS.sub("", text.casefold())
    return [w for w in re.sub(r"[\W_]+", " ", joined).split() if w not in _FILLER]


def _coding_cli(spoken: str) -> str | None:
    """The coding CLI a spoken or typed name means, or ``None``.

    Reads it the way the spoken spawn path does (``intent.canonical_agent``),
    then forgives what a transcript wraps around the name or garbles after it:
    "Claude Code agent" and "Claude Cotec" both mean a Claude Code pane.
    """
    from .intent import canonical_agent

    words = [w for w in re.split(r"[\s_-]+", spoken.casefold()) if w]
    kept = [w for w in words if w not in _CLI_FILLER] or words
    for attempt in (" ".join(words), " ".join(kept), *kept[:1]):
        found = canonical_agent(attempt) if attempt else None
        if found and accepts_prompts(found):
            return found
    return None


def _score(reference: str, ids: Iterable[str], names: Iterable[str]) -> int:
    """3 = exact id/name, 2 = same words, 1 = one name's words contain the other's."""
    names = [n for n in names if n]
    if _matches(reference, *ids, *names):
        return 3
    wanted = _words(reference)
    if not wanted:
        return 0
    best = 0
    for name in names:
        words = _words(name)
        if not words:
            continue
        if words == wanted:
            return 2
        if set(wanted) <= set(words) or set(words) <= set(wanted):
            best = 1
    return best


def _best(reference: str, items: list, key: Callable[[Any], tuple]) -> list:
    """The items matching ``reference`` at the strongest tier any item reaches."""
    scored = [(_score(reference, *key(item)), item) for item in items]
    top = max((score for score, _ in scored), default=0)
    return [item for score, item in scored if top and score == top]


def _distance(a: str, b: str) -> int:
    """Levenshtein distance, enough to recognise a mis-copied id."""
    if abs(len(a) - len(b)) > _NEAR_MISS:
        return _NEAR_MISS + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _is_request_id(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


class WorkspaceOrchestrator:
    """One graph for Live discovery, explicit resolution and scoped delivery."""

    def __init__(
        self,
        registry: Registry,
        sessions: CodingSessionGateway,
        ledger: LiveLedger,
    ) -> None:
        self.registry = registry
        self.sessions = sessions
        self.ledger = ledger
        # request_id -> (target IDs, issued at, prompt sent under it or "")
        self._issued: dict[str, tuple[dict[str, str], float, str]] = {}
        self._issued_lock = threading.Lock()
        # Announces new panes to the open UI; set by the runtime that owns a bus.
        self.publish: Callable[[Any], Awaitable[Any]] | None = None

    def graph(self) -> dict[str, Any]:
        from jarvis.workspace.agents import pty_available

        graph = project_graph(self.registry)
        for project in graph["projects"]:
            for workspace in project["workspaces"]:
                owner = self.registry.get(workspace["id"])
                workspace["agents"] = (
                    [
                        {
                            "id": "pane:" + term.history_id,
                            "name": term.name,
                            "agent": term.agent,
                            "status": term.status,
                            "activity": term.reading().activity,
                            "accepts_tasks": accepts_prompts(term.agent) and not term.archived,
                        }
                        for term in owner.terminals
                    ]
                    if owner
                    else []
                )
        return {
            **graph,
            "available": pty_available(),
            "note": (
                "Workspace selection is UI context, not permission. "
                "Closed workspaces must be restored before dispatch."
            ),
        }

    def _workspace(
        self, args: dict[str, Any], graph: dict[str, Any], agent_ref: str
    ) -> tuple[dict[str, Any], dict[str, Any]] | dict[str, Any]:
        """The one open (project, workspace) a request names, or the reply saying why not."""
        projects = graph["projects"]
        project_ref = str(args.get("project") or "")
        workspace_ref = str(args.get("workspace") or "")
        project_found = False
        if project_ref:
            matched = _best(project_ref, projects, lambda p: ((p["id"], p["path"]), (p["name"],)))
            if len(matched) > 1:
                return self._choice("project", matched, graph)
            if not matched and not (workspace_ref or agent_ref):
                return self._choice("project", [], graph, unmatched=project_ref)
            # A misheard project name ("Jarvis-Works") must not hide a
            # workspace or agent reference that does identify the target.
            if matched:
                projects, project_found = matched, True
        candidates = [(p, w) for p in projects for w in p["workspaces"]]
        if workspace_ref:
            matched = _best(
                workspace_ref, candidates, lambda pw: ((pw[1]["id"],), (pw[1]["name"],))
            )
            # People often call a project "the Personal Jarvis workspace".
            # Interpret that only when it identifies exactly one project.
            if not matched:
                owners = _best(workspace_ref, projects, lambda p: ((p["id"],), (p["name"],)))
                if len(owners) == 1:
                    matched = [(p, w) for p, w in candidates if p["id"] == owners[0]["id"]]
            if not matched:
                return self._choice("workspace", [], graph, unmatched=workspace_ref)
            candidates = matched
        elif not project_found:
            named_agents = (
                [
                    (p, w)
                    for p, w in candidates
                    if _best(agent_ref, w["agents"], lambda a: ((a["id"],), (a["name"],)))
                ]
                if agent_ref
                else []
            )
            candidates = named_agents or [
                (p, w) for p, w in candidates if w["id"] == graph["active_workspace_id"]
            ]
        if len(candidates) != 1:
            return self._choice(
                "workspace",
                [{"project_id": p["id"], "project": p["name"], **w} for p, w in candidates],
                graph,
            )
        project, workspace = candidates[0]
        if workspace["status"] != "open":
            return {
                "status": "unavailable",
                "reason": "Restore this closed workspace first.",
                "project_id": project["id"],
                "workspace_id": workspace["id"],
            }
        return project, workspace

    def resolve(self, args: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
        agent_ref = str(args.get("agent") or "")
        picked = self._workspace(args, graph, agent_ref)
        if isinstance(picked, dict):
            return picked
        project, workspace = picked
        agents = [a for a in workspace["agents"] if a["accepts_tasks"]]
        if agent_ref:
            named = _best(agent_ref, agents, lambda a: ((a["id"],), (a["name"],)))
            agents = named or [a for a in agents if _matches(agent_ref, a["agent"])]
            if len(agents) != 1:
                return self._choice("agent", agents, graph, unmatched=agent_ref)
        else:
            # A task need not require manually selecting a tile. Stable grid
            # order is the tie-breaker among idle sessions; never interrupt one.
            agents = [
                a
                for a in agents
                if a["status"] == "pending"
                or (
                    a["status"] == "live" and a["activity"] not in {"working", "asking", "starting"}
                )
            ]
            agents = agents[:1]
        if not agents:
            return {
                "status": "unavailable",
                # Without the way forward the model asked the user whether to
                # borrow another workspace's agent or wait (live 2026-10-01).
                "reason": (
                    "No idle coding agent is available in this workspace. To start a new one "
                    "here, call create with these IDs; never borrow another workspace's agent."
                ),
                "project_id": project["id"],
                "workspace_id": workspace["id"],
            }
        agent = agents[0]
        target = {
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "terminal_id": agent["id"],
        }
        request_id = self._issue(target)
        return {
            "status": "resolved",
            "request_id": request_id,
            "target": {
                **target,
                "project": project["name"],
                "workspace": workspace["name"],
                "agent": agent["name"],
            },
            "selection": "explicit_agent" if agent_ref else "first_idle_agent",
        }

    def _issue(self, target: dict[str, str]) -> str:
        """Mint a request_id for ``target`` and remember it for id repair."""
        request_id = uuid4().hex
        with self._issued_lock:
            now = time.monotonic()
            for stale in [k for k, (_, at, _) in self._issued.items() if now - at > _RESOLVE_TTL_S]:
                del self._issued[stale]
            self._issued[request_id] = (target, now, "")
        return request_id

    async def create(self, args: dict[str, Any], *, trace_id: str = "") -> dict[str, Any]:
        """Open new coding agents in one workspace, then optionally brief them.

        A request for a NEW agent must never land on an existing pane, nor on a
        background mission worker the workspace cannot show (live 2026-10-01:
        "spawn a new Claude Code agent in the VMs workspace" became an
        invisible worker in a separate checkout). The panes join the named or
        visible workspace, the open view is told so they appear at once, and a
        given prompt reaches each new pane through the ordinary receipted send,
        which starts the agent even when its workspace is not on screen.
        """
        graph = await asyncio.to_thread(self.graph)
        refs = {
            "project": str(args.get("project_id") or args.get("project") or ""),
            "workspace": str(args.get("workspace_id") or args.get("workspace") or ""),
        }
        picked = self._workspace(refs, graph, "")
        if isinstance(picked, dict):
            return picked
        project, workspace = picked
        spoken_cli = str(args.get("cli") or "").strip()
        cli = _coding_cli(spoken_cli) if spoken_cli else None
        if spoken_cli and cli is None:
            from jarvis.workspace import agents as workspace_agents

            return {
                "status": "needs_clarification",
                "kind": "cli",
                "reason": f"'{spoken_cli}' is not a coding CLI this app can open.",
                "candidates": [
                    a.name for a in workspace_agents.coding_agents() if accepts_prompts(a.name)
                ],
            }
        try:
            count = int(args.get("count") or 1)
        except (TypeError, ValueError):
            count = 1
        count = max(1, min(count, MAX_TERMINALS))
        name = str(args.get("name") or "").strip()
        try:
            if count == 1 and name:
                created = [
                    await self.registry.add_terminal(
                        workspace_id=workspace["id"], agent=cli, name=name
                    )
                ]
                capped = False
            else:
                created, capped = await self.registry.add_terminals(
                    count, agent=cli, workspace_id=workspace["id"]
                )
        except SessionError as exc:
            return {
                "status": "not_accepted",
                "reason": str(exc),
                "project_id": project["id"],
                "workspace_id": workspace["id"],
            }
        owner = self.registry.get(workspace["id"])
        if self.publish is not None and owner is not None and created:
            try:
                await self.publish(
                    terminals_added_event(owner, created, source_layer="agentic_ide.orchestration")
                )
            except Exception as exc:  # noqa: BLE001 - the panes exist either way
                from loguru import logger

                logger.warning("New coding agents were not announced to the UI: {}", exc)
        targets = [
            {
                "project_id": project["id"],
                "workspace_id": workspace["id"],
                "terminal_id": "pane:" + term.history_id,
            }
            for term in created
        ]
        result: dict[str, Any] = {
            "status": "created",
            "project": project["name"],
            "workspace": workspace["name"],
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "agents": [
                {"terminal_id": target["terminal_id"], "name": term.name, "cli": term.agent}
                for target, term in zip(targets, created, strict=True)
            ],
            "requested": count,
            "capped": capped,
        }
        prompt = str(args.get("prompt") or "").strip()
        if prompt:
            result["deliveries"] = list(
                await asyncio.gather(
                    *(
                        self.run(
                            {
                                "action": "send",
                                **target,
                                "request_id": self._issue(target),
                                "prompt": prompt,
                            },
                            trace_id=trace_id,
                        )
                        for target in targets
                    )
                )
            )
        return result

    @staticmethod
    def _choice(
        kind: str, choices: list[dict], graph: dict[str, Any], *, unmatched: str = ""
    ) -> dict[str, Any]:
        if choices:
            return {
                "status": "needs_clarification",
                "kind": kind,
                "candidates": choices,
                "reason": "Reference is ambiguous.",
            }
        # Never answer "nothing" without the options: the model then tells the
        # user a workspace does not exist while it is open on screen.
        wanted = set(_words(unmatched))
        open_workspaces = sorted(
            (
                {
                    "project_id": p["id"],
                    "project": p["name"],
                    "workspace_id": w["id"],
                    "workspace": w["name"],
                    "active": w["id"] == graph.get("active_workspace_id"),
                    "agents": [a["name"] for a in w["agents"] if a["accepts_tasks"]],
                }
                for p in graph["projects"]
                for w in p["workspaces"]
                if w["status"] == "open"
            ),
            key=lambda c: (
                -len(wanted & set(_words(f"{c['project']} {c['workspace']} {c['agents']}"))),
                not c["active"],
            ),
        )
        lead = f"Nothing open matches '{unmatched}'. " if unmatched else "No matching target. "
        return {
            "status": "needs_clarification",
            "kind": kind,
            "candidates": open_workspaces,
            "reason": lead
            + (
                "These are the open workspaces, closest first; resolve again with the one "
                "the user meant."
                if open_workspaces
                else "No workspace is open; connect or restore one first."
            ),
        }

    def _reconcile(self, args: dict[str, Any], action: str) -> tuple[dict[str, str], str]:
        """Target IDs and request_id, repaired against this app's own resolves.

        A voice model retypes long hex IDs and drops characters. The resolve
        that minted them is the authority: an exact or near-miss request_id,
        else a resolve for (nearly) the same terminal — the one this prompt
        already went out under (a retry), or the newest unused one — supplies
        the target. Only IDs nothing here issued are taken as given. An empty
        request_id comes back when the call needs a fresh key: a resolve
        already spent on a different prompt must not swallow a new task.
        """
        given = {key: str(args.get(key) or "").strip() for key in _TARGET_KEYS}
        request_id = str(args.get("request_id") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        with self._issued_lock:
            issued = dict(self._issued)
        match = request_id if request_id in issued else ""
        if not match and request_id:
            near = [rid for rid in issued if _distance(request_id, rid) <= _NEAR_MISS]
            match = near[0] if len(near) == 1 else ""
        if not match and given["terminal_id"] and not _is_request_id(request_id):
            same_pane = [
                (at, rid, sent)
                for rid, (target, at, sent) in issued.items()
                if _distance(given["terminal_id"], target["terminal_id"]) <= _NEAR_MISS
            ]
            retry = [(at, rid) for at, rid, sent in same_pane if sent and sent == prompt]
            fresh = [(at, rid) for at, rid, sent in same_pane if not sent or action != "send"]
            if retry or fresh:
                match = max(retry or fresh)[1]
        if not match:
            return given, request_id
        target, _, sent = issued[match]
        if action == "send" and sent and sent != prompt:
            return dict(target), ""
        return dict(target), match

    def _mark_sent(self, request_id: str, prompt: str) -> None:
        with self._issued_lock:
            if request_id in self._issued:
                target, at, _ = self._issued[request_id]
                self._issued[request_id] = (target, at, prompt)

    async def run(self, args: dict[str, Any], *, trace_id: str = "") -> dict[str, Any]:
        action = args.get("action")
        if action in {"inspect", "resolve"}:
            graph = await asyncio.to_thread(self.graph)
            return graph if action == "inspect" else self.resolve(args, graph)
        if action == "create":
            return await self.create(args, trace_id=trace_id)
        if action not in {"send", "context"}:
            raise ValueError("Unknown workspace orchestration action.")
        target, request_id = self._reconcile(args, action)
        project_id, workspace_id, terminal_id = (target[key] for key in _TARGET_KEYS)
        if not project_id or not workspace_id or not terminal_id.startswith("pane:"):
            raise ValueError(
                "Resolve a target first; project_id, workspace_id and terminal_id are required."
            )
        if action == "send":
            prompt = str(args.get("prompt") or "").strip()
            if not prompt:
                raise ValueError("Sending requires a prompt.")
            if not _is_request_id(request_id):
                # Nothing usable to key on: derive a key so a retry of this
                # same task within ten minutes still cannot deliver twice.
                seed = f"{terminal_id}\n{prompt}\n{int(time.time() // 600)}"
                request_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
            self._mark_sent(request_id, prompt)
            previous = await asyncio.to_thread(
                self.ledger.claim,
                "workspace-orchestration",
                request_id,
                "send",
                {**target, "prompt": prompt},
                0,
            )
            if previous is not None:
                return previous
        owner = self.registry.get(workspace_id)
        found = self.registry.find_terminal(terminal_id, workspace_id) if owner else None
        result: dict[str, Any]
        if owner is None or owner.project_id != project_id or found is None or found[1].archived:
            result = {
                "status": "stale_target",
                "target": target,
                "reason": "The resolved coding session is no longer in that project/workspace.",
            }
        elif not accepts_prompts(found[1].agent):
            result = {
                "status": "unavailable",
                "target": target,
                "reason": "This session does not accept coding tasks.",
            }
        else:
            # The low-level gateway enforces idle/asking state and refuses exited
            # agents. It never falls through to a shell or a different pane.
            try:
                delivery = await self.sessions.run(
                    {
                        "action": action,
                        "workspace_id": workspace_id,
                        "terminal_id": terminal_id,
                        **(
                            {"prompt": prompt}
                            if action == "send"
                            else {"limit": args.get("limit", 30)}
                        ),
                    }
                )
                result = {
                    "status": delivery.get("delivery", "observed"),
                    "target": target,
                    "trace_id": trace_id,
                    **delivery,
                }
            except SessionError as exc:
                # The coding-session adapter documents SessionError as a
                # pre-write refusal; post-write uncertainty is a receipt.
                result = {
                    "status": "not_accepted",
                    "target": target,
                    "reason": str(exc),
                    "completed": False,
                }
            except Exception as exc:
                # Preserve uncertainty: an adapter can fail after the write.
                # The durable claim remains, preventing an automatic replay.
                from loguru import logger

                logger.warning("Workspace action {} failed: {}", action, type(exc).__name__)
                if action == "send":
                    return {
                        "status": "uncertain",
                        "target": target,
                        "reason": (
                            "Delivery could not be confirmed. Inspect the session; "
                            "do not resend automatically."
                        ),
                    }
                raise
        if action == "send":
            await asyncio.to_thread(
                self.ledger.finish, "workspace-orchestration", request_id, result
            )
        return result


_orchestrators: dict[int, WorkspaceOrchestrator] = {}
_creation_lock = threading.Lock()


def get_orchestrator() -> WorkspaceOrchestrator:
    """Lazy composition: no filesystem, PTY or model initialization at boot."""
    from jarvis.core.paths import user_data_dir

    from .control import CodingSessionControl
    from .session import get_registry

    registry = get_registry()
    key = id(registry)
    with _creation_lock:
        if key not in _orchestrators:
            root = user_data_dir()
            root.mkdir(parents=True, exist_ok=True)
            _orchestrators[key] = WorkspaceOrchestrator(
                registry,
                CodingSessionControl(registry),
                LiveLedger(root / "workspace-orchestration.sqlite3"),
            )
    return _orchestrators[key]
