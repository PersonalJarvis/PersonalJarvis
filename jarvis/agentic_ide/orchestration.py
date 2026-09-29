"""Workspace-aware target resolution and durable, addressed task delivery.

Names are references, never execution identities. Resolution returns the three
stable IDs needed by send; moving focus, renaming or reordering cannot retarget
an approval. This layer never activates a workspace or composes a second prompt.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from uuid import UUID, uuid4

from jarvis.core.protocols import CodingSessionGateway
from jarvis.live.state import LiveLedger

from .session import Registry, SessionError, accepts_prompts
from .workspace_catalog import project_graph


def _matches(reference: str, *values: str) -> bool:
    return reference.strip().casefold() in {v.strip().casefold() for v in values if v}


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

    def resolve(self, args: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
        projects = graph["projects"]
        project_ref = str(args.get("project") or "")
        workspace_ref = str(args.get("workspace") or "")
        agent_ref = str(args.get("agent") or "")
        if project_ref:
            projects = [p for p in projects if _matches(project_ref, p["id"], p["name"], p["path"])]
            if len(projects) != 1:
                return self._choice("project", projects)
        candidates = [(p, w) for p in projects for w in p["workspaces"]]
        if workspace_ref:
            matched = [(p, w) for p, w in candidates if _matches(workspace_ref, w["id"], w["name"])]
            # People often call a project "the Personal Jarvis workspace".
            # Interpret that only when it identifies exactly one project.
            if not matched:
                owners = [p for p in projects if _matches(workspace_ref, p["id"], p["name"])]
                if len(owners) == 1:
                    matched = [(p, w) for p, w in candidates if p["id"] == owners[0]["id"]]
            candidates = matched
        elif not project_ref:
            named_agents = [
                (p, w)
                for p, w in candidates
                if agent_ref and any(_matches(agent_ref, a["id"], a["name"]) for a in w["agents"])
            ]
            candidates = named_agents or [
                (p, w) for p, w in candidates if w["id"] == graph["active_workspace_id"]
            ]
        if len(candidates) != 1:
            return self._choice(
                "workspace",
                [{"project_id": p["id"], "project": p["name"], **w} for p, w in candidates],
            )
        project, workspace = candidates[0]
        if workspace["status"] != "open":
            return {
                "status": "unavailable",
                "reason": "Restore this closed workspace first.",
                "project_id": project["id"],
                "workspace_id": workspace["id"],
            }
        agents = [a for a in workspace["agents"] if a["accepts_tasks"]]
        if agent_ref:
            named = [a for a in agents if _matches(agent_ref, a["id"], a["name"])]
            agents = named or [a for a in agents if _matches(agent_ref, a["agent"])]
            if len(agents) != 1:
                return self._choice("agent", agents)
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
                "reason": "No idle coding agent is available in this workspace.",
                "project_id": project["id"],
                "workspace_id": workspace["id"],
            }
        agent = agents[0]
        return {
            "status": "resolved",
            "request_id": uuid4().hex,
            "target": {
                "project_id": project["id"],
                "workspace_id": workspace["id"],
                "terminal_id": agent["id"],
                "project": project["name"],
                "workspace": workspace["name"],
                "agent": agent["name"],
            },
            "selection": "explicit_agent" if agent_ref else "first_idle_agent",
        }

    @staticmethod
    def _choice(kind: str, choices: list[dict]) -> dict[str, Any]:
        return {
            "status": "needs_clarification",
            "kind": kind,
            "candidates": choices,
            "reason": "Reference is ambiguous."
            if choices
            else "No matching open target. Connect or restore the workspace first.",
        }

    async def run(self, args: dict[str, Any], *, trace_id: str = "") -> dict[str, Any]:
        action = args.get("action")
        if action in {"inspect", "resolve"}:
            graph = await asyncio.to_thread(self.graph)
            return graph if action == "inspect" else self.resolve(args, graph)
        if action not in {"send", "context"}:
            raise ValueError("Unknown workspace orchestration action.")
        project_id, workspace_id, terminal_id = (
            str(args.get(key) or "") for key in ("project_id", "workspace_id", "terminal_id")
        )
        if not project_id or not workspace_id or not terminal_id.startswith("pane:"):
            raise ValueError(
                "Resolve a target first; project_id, workspace_id and terminal_id are required."
            )
        target = {
            "project_id": project_id,
            "workspace_id": workspace_id,
            "terminal_id": terminal_id,
        }
        request_id = str(args.get("request_id") or "").strip()
        if action == "send":
            prompt = str(args.get("prompt") or "").strip()
            if not prompt or not request_id or len(request_id) > 160:
                raise ValueError(
                    "Sending requires a prompt and a stable request_id (at most 160 characters)."
                )
            try:
                UUID(request_id)
            except ValueError as exc:
                raise ValueError("Use the unique request_id returned by resolve.") from exc
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
