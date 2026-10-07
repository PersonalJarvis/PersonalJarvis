"""Workspace-aware target resolution and durable, addressed task delivery.

Names are references, never execution identities. Resolution returns the three
stable IDs needed by send; moving focus, renaming or reordering cannot retarget
an approval. This layer never activates a workspace or composes a second prompt.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import random
import re
import threading
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from uuid import UUID, uuid4

from jarvis.core.protocols import CodingSessionGateway
from jarvis.live.state import LiveLedger

from .session import (
    MAX_PANES_PER_REQUEST,
    AgentBusyError,
    Registry,
    SessionError,
    accepts_prompts,
    steers_mid_turn,
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
        "der", "die", "das", "mein", "meinem", "meinen", "projekt", "ordner",  # i18n-allow
        "arbeitsbereich",
    }
)  # fmt: skip
# A resolve is remembered this long so a voice model never has to copy its
# 32-character request_id verbatim (live 2026-10-01: one dropped "0" failed
# four sends in a row).
_RESOLVE_TTL_S = 15 * 60
_NEAR_MISS = 3
_TARGET_KEYS = ("project_id", "workspace_id", "terminal_id")
# A send of the same prompt to the same pane counts as a retry only this soon;
# a later "continue" or "/compact" is a new instruction and must be typed.
_RETRY_WINDOW_S = 120
_PANE_ACTIONS = frozenset({"observe", "respond", "keys", "interrupt", "close"})
_WORKSPACE_ACTIONS = frozenset({"open_workspace", "restore", "show"})
# How many times one request may be re-attempted after receipts proving that
# nothing was typed (the pane was busy, closed, or not a coding pane).
_REFUSED_ATTEMPTS = 20
_PRE_WRITE_REFUSALS = frozenset({"not_accepted", "stale_target", "unavailable", "expired"})
# What a send does with a pane that is in a turn. Only "refuse" is automatic;
# the others carry the user's own correction to the running work.
_WHILE_BUSY = ("refuse", "steer", "interrupt", "queue")
# A queued message waits this long for the turn to end, re-checking the pane
# every few seconds (jittered: several queues never poll in lockstep).
_QUEUE_TTL_S = 30 * 60
_QUEUE_POLL_S = 3.0
# The keys a person presses in a pane besides typing: menus, dialogs, the
# permission-mode cycle (Shift+Tab) and Stop (Escape). Nothing else is sendable.
_KEY_SEQUENCES = {
    "enter": "\r",
    "escape": "\x1b",
    "tab": "\t",
    "shift+tab": "\x1b[Z",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    "space": " ",
    "y": "y",
    "n": "n",
    **{str(digit): str(digit) for digit in range(10)},
}
_MAX_KEYS = 10
_KEY_GAP_S = 0.08


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


def _default_cli() -> str | None:
    """The first installed coding CLI, for a request that names none."""
    from jarvis.workspace import agents as workspace_agents

    from .session import agent_argv

    for info in workspace_agents.coding_agents():
        if accepts_prompts(info.name) and agent_argv(info.name) is not None:
            return info.name
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


def _spoken_pane(reference: str, agents: list[dict]) -> tuple[list[dict], bool]:
    """Panes a misheard CUSTOM name means, and whether the match is certain.

    Positional call-signs ("T3") stay exact-only (see ``names.py``): "T1" and
    "T11" are two real panes, never one garbled word.
    """
    from jarvis.core.spoken_names import NameCandidate, resolve_name

    from .names import position_of

    custom = [a for a in agents if a.get("name") and position_of(a["name"]) is None]
    if not custom:
        return [], False
    resolution = resolve_name(
        reference,
        [NameCandidate(key=a["id"], label=a["name"], names=(a["name"],)) for a in custom],
        surface="workspace-orchestrate",
    )
    if resolution.decision == "none":
        return [], False
    keys = [m.key for m in resolution.candidates]
    return [a for key in keys for a in custom if a["id"] == key], resolution.decision == "act"


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
    except ValueError:  # not a UUID is the answer, not a failure
        return False
    return True


def _never_delivered(receipt: dict) -> bool:
    """Does this send receipt prove that nothing reached the pane?

    Only the pre-write refusals qualify (the session adapter raises
    ``SessionError`` before typing anything). An unconfirmed write, a ledger
    collision or an accepted send is never retried.
    """
    return (
        receipt.get("status") in _PRE_WRITE_REFUSALS
        and not receipt.get("input_written")
        and not receipt.get("submitted")
    )


def _assignment(args: dict) -> str:
    """The brief AND its chosen images identify a retry."""
    return json.dumps([str(args.get("prompt") or "").strip(), args.get("image_refs", [])])


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
        # A source update can leave the boot-loaded ledger older than this
        # lazily imported controller. Check the contract before allocating a
        # pane, recording a claim, or sending anything to an agent.
        try:
            inspect.signature(ledger.claim).bind(
                "", "", "", {}, 0, deduplicate_unconfirmed=True,
            )
            self._ledger_compatible = callable(getattr(ledger, "operation", None))
        except (AttributeError, TypeError, ValueError):
            # No ledger (resolve-only use) or an old one: surface a restart
            # requirement below instead of a partial dispatch.
            self._ledger_compatible = False
        # request_id -> (target IDs, issued at, prompt sent under it or "")
        self._issued: dict[str, tuple[dict[str, str], float, str]] = {}
        # request_ids minted for a pane the caller NAMED (call-sign, custom
        # name or ID), never for a first-idle pick or a CLI-kind match.
        self._named: set[str] = set()
        self._issued_lock = threading.Lock()
        # receipt_id -> the task holding a queued message until its pane is
        # free. A "queued" receipt with no task here was left by an earlier
        # run of the app and was never typed.
        self._queued: dict[str, asyncio.Task] = {}
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
        named = []
        if agent_ref:
            named = _best(agent_ref, agents, lambda a: ((a["id"],), (a["name"],)))
            by_cli = [a for a in agents if _matches(agent_ref, a["agent"])]
            if not named and not by_cli:
                named, certain = _spoken_pane(agent_ref, agents)
                if named and not certain:
                    # Close but not certain: a question, never a guess.
                    return self._choice("agent", named, graph)
            agents = named or by_cli
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
        request_id = self._issue(target, named=bool(named))
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

    def _issue(self, target: dict[str, str], *, named: bool = False) -> str:
        """Mint a request_id for ``target`` and remember it for id repair."""
        request_id = uuid4().hex
        with self._issued_lock:
            now = time.monotonic()
            for stale in [k for k, (_, at, _) in self._issued.items() if now - at > _RESOLVE_TTL_S]:
                del self._issued[stale]
                self._named.discard(stale)
            self._issued[request_id] = (target, now, "")
            if named:
                self._named.add(request_id)
        return request_id

    async def create(self, args: dict[str, Any], *, trace_id: str = "") -> dict[str, Any]:
        """Claim creation before allocating panes; a retry only reads its receipt."""
        request_id = str(
            args.get("_dispatch_scope") or args.get("request_id") or trace_id or uuid4().hex
        )
        arguments = {k: v for k, v in args.items() if not k.startswith("_") and k != "request_id"}
        # Scope is part of image authorization, even though it is host-owned.
        if args.get("image_refs"):
            arguments["image_scope"] = args.get("_image_scope", "")
        previous = await asyncio.to_thread(
            self.ledger.claim, "workspace-create", request_id, "create", arguments, 0,
        )
        if previous is not None:
            return previous
        result = await self._create(args, trace_id=trace_id, creation_id=request_id)
        result["request_id"] = request_id
        await asyncio.to_thread(self.ledger.finish, "workspace-create", request_id, result)
        return result

    async def _create(
        self, args: dict[str, Any], *, trace_id: str, creation_id: str,
    ) -> dict[str, Any]:
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
        if any(args.get(key) and args[key] != value for key, value in (
            ("project_id", project["id"]), ("workspace_id", workspace["id"]),
        )):
            return {
                "status": "stale_target", "success": False,
                "reason": "The explicit workspace/project IDs do not match. Nothing was created.",
            }
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
        except (TypeError, ValueError):  # a malformed count falls back to one terminal
            count = 1
        count = max(1, min(count, MAX_PANES_PER_REQUEST))
        name = str(args.get("name") or "").strip()
        created = []
        capped = False
        creation_error = ""
        mixed = args.get("agents")
        if isinstance(mixed, list) and mixed:
            # "Five Claude Code and three Codex" in one call.
            groups = self._groups(args)
            if isinstance(groups, dict):
                return groups
            count = sum(n for _, n in groups)
        try:
            if isinstance(mixed, list) and mixed:
                created, capped = [], False
                for group_cli, group_count in groups:
                    made, cut = await self.registry.add_terminals(
                        group_count, agent=group_cli, workspace_id=workspace["id"]
                    )
                    created.extend(made)
                    capped = capped or cut
            elif count == 1 and name:
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
        except SessionError as exc:  # Return the refusal or partial creation receipt to the caller.
            if not created:
                return {
                    "status": "not_accepted",
                    "reason": str(exc),
                    "project_id": project["id"],
                    "workspace_id": workspace["id"],
                }
            # Earlier groups already exist. Keep their IDs and never recreate
            # the successful groups merely because a later group failed.
            creation_error = str(exc)
        targets = [
            {
                "project_id": project["id"],
                "workspace_id": workspace["id"],
                "terminal_id": "pane:" + term.history_id,
            }
            for term in created
        ]
        delivery_ids = [self._issue(target) for target in targets]
        result: dict[str, Any] = {
            "status": "created",
            "project": project["name"],
            "workspace": workspace["name"],
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "agents": [
                {"terminal_id": target["terminal_id"], "name": term.name, "cli": term.agent,
                 "request_id": request_id}
                for target, term, request_id in zip(targets, created, delivery_ids, strict=True)
            ],
            "requested": count,
            "capped": capped,
            **({"creation_error": creation_error, "success": False} if creation_error else {}),
        }
        # Persist identity before slow CLI startup. Cancellation or a late result
        # must never turn "pane allocated" into permission to allocate another.
        await asyncio.to_thread(
            self.ledger.finish, "workspace-create", creation_id,
            {**result, "status": "uncertain", "success": False, "request_id": creation_id,
             "reason": "New panes exist; startup/delivery is pending. Observe only these IDs. "
                       "Do not recreate, reassign or send correction prompts."},
        )
        owner = self.registry.get(workspace["id"])
        if self.publish is not None and owner is not None and created:
            await self._announce(
                lambda: terminals_added_event(
                    owner, created, source_layer="agentic_ide.orchestration",
                )
            )
        prompt = str(args.get("prompt") or "").strip()
        if prompt:
            result["deliveries"] = list(
                await asyncio.gather(
                    *(
                        self.run(
                            {
                                "action": "send",
                                **target,
                                "request_id": request_id,
                                "prompt": prompt,
                                "image_refs": args.get("image_refs", []),
                                "_image_scope": args.get("_image_scope", ""),
                            },
                            trace_id=trace_id,
                        )
                        for target, request_id in zip(targets, delivery_ids, strict=True)
                    )
                )
            )
            result["success"] = not creation_error and all(
                d.get("status") == "accepted" for d in result["deliveries"]
            )
        return result

    async def _announce(self, event_factory: Callable[[], Any]) -> None:
        """Tell the open UI about a change; the change stands either way (AP-18)."""
        if self.publish is None:
            return
        try:
            await self.publish(event_factory())
        except Exception as exc:  # noqa: BLE001 - notification is not the work
            from loguru import logger

            logger.warning("Workspace change was not announced to the UI: {}", exc)

    async def _pane(self, args: dict[str, Any]) -> tuple[Any, Any, dict[str, str]] | dict:
        """The one open pane a request names, by IDs from resolve or by call-sign."""
        if str(args.get("terminal_id") or "").strip():
            target, _ = self._reconcile(args, "observe")
        elif str(args.get("agent") or "").strip():
            resolved = self.resolve(args, await asyncio.to_thread(self.graph))
            if resolved.get("status") != "resolved":
                return resolved
            target = {key: resolved["target"][key] for key in _TARGET_KEYS}
        else:
            return {
                "status": "needs_clarification",
                "kind": "agent",
                "reason": "Name the coding agent (its call-sign) or pass terminal_id.",
            }
        found = self.registry.find_terminal(target["terminal_id"], target["workspace_id"] or None)
        if found is None:
            return {
                "status": "stale_target",
                "target": target,
                "reason": "That coding agent is no longer open; inspect the workspace again.",
            }
        owner, term = found
        return owner, term, target

    async def pane_action(self, args: dict[str, Any]) -> dict[str, Any]:
        """Everything a person does with one open pane besides typing a task.

        observe reads the screen and the newest recorded events; respond answers
        the question or permission prompt the agent is showing; keys presses
        keys (menus, Shift+Tab, Escape); interrupt stops the current turn
        (Escape, the pane's Stop button); close stops and removes the pane.
        """
        action = str(args.get("action"))
        picked = await self._pane(args)
        if isinstance(picked, dict):
            return picked
        owner, term, target = picked
        pane = {"workspace_id": owner.id, "terminal_id": target["terminal_id"]}
        try:
            if action == "observe":
                seen = await self.sessions.run({"action": "observe", **pane})
                return {"status": "observed", "target": target, **seen}
            if action == "respond":
                answer = str(args.get("prompt") or "").strip()
                if not answer:
                    raise SessionError("Pass the answer to type as prompt.")
                state = await self.sessions.run({"action": "input", **pane})
                delivery = await self.sessions.run(
                    {
                        "action": "respond",
                        **pane,
                        "prompt": answer,
                        "input_token": state["input_token"],
                        "response_mode": state["response_mode"],
                    }
                )
                return {
                    "status": delivery.get("delivery", "uncertain"),
                    "target": target,
                    **delivery,
                }
            if action in {"keys", "interrupt"}:
                keys = ["escape"] if action == "interrupt" else list(args.get("keys") or [])
                unknown = [k for k in keys if str(k).casefold() not in _KEY_SEQUENCES]
                if not keys or unknown or len(keys) > _MAX_KEYS:
                    raise SessionError(
                        f"Press 1-{_MAX_KEYS} keys from: {', '.join(sorted(_KEY_SEQUENCES))}."
                    )
                for index, key in enumerate(keys):
                    if index:
                        await asyncio.sleep(_KEY_GAP_S)
                    if not self.registry.write(
                        term.name, _KEY_SEQUENCES[str(key).casefold()], owner.id
                    ):
                        raise SessionError(f"{term.name} is not running.")
                return {"status": "pressed", "target": target, "keys": keys, "agent": term.name}
            if action == "close":
                from .fleet_actions import terminals_closed_event

                closed, failed = await self.registry.close_terminals(
                    [term.name], workspace_id=owner.id
                )
                if closed:
                    await self._announce(
                        lambda: terminals_closed_event(
                            owner, closed, source_layer="agentic_ide.orchestration"
                        )
                    )
                return {
                    "status": "closed" if closed else "not_accepted",
                    "target": target,
                    "closed": [t.name for t in closed],
                    "failed": failed,
                }
        except SessionError as exc:  # the refusal is returned to the caller with its reason
            return {"status": "not_accepted", "target": target, "reason": str(exc)}
        raise ValueError("Unknown pane action.")

    async def workspace_action(self, args: dict[str, Any], *, trace_id: str = "") -> dict:
        """Open a new workspace, restore a closed one, or bring one on screen."""
        from .session import workspace_changed_event

        action = str(args.get("action"))
        graph = await asyncio.to_thread(self.graph)
        if action == "show":
            picked = self._workspace(args, graph, "")
            if isinstance(picked, dict):
                return picked
            project, workspace = picked
            session = await self.registry.activate(workspace["id"])
            await self._announce(
                lambda: workspace_changed_event(
                    session, "activated", source_layer="agentic_ide.orchestration"
                )
            )
            return {"status": "shown", "project": project["name"], "workspace": workspace["name"]}
        if action == "restore":
            closed = [
                (p, w) for p in graph["projects"] for w in p["workspaces"] if w["status"] != "open"
            ]
            ref = str(args.get("workspace_id") or args.get("workspace") or "")
            matched = (
                _best(ref, closed, lambda pw: ((pw[1]["id"],), (pw[1]["name"],))) if ref else []
            )
            if len(matched) != 1:
                return {
                    "status": "needs_clarification",
                    "kind": "workspace",
                    "reason": "Name one closed workspace to restore.",
                    "candidates": [
                        {"workspace_id": w["id"], "workspace": w["name"], "project": p["name"]}
                        for p, w in (matched or closed)
                    ],
                }
            try:
                session = await self.registry.restore_workspace(matched[0][1]["id"])
            except SessionError as exc:  # the refusal is returned to the caller with its reason
                return {"status": "not_accepted", "reason": str(exc)}
            await self._announce(
                lambda: workspace_changed_event(
                    session, "restored", source_layer="agentic_ide.orchestration"
                )
            )
            return {
                "status": "restored",
                "workspace_id": session.id,
                "workspace": session.name,
                "agents": [t.name for t in session.terminals],
            }
        scope = str(args.get("_dispatch_scope") or "")
        if not scope:
            return await self._open_workspace(args, graph, trace_id=trace_id, creation_id="")
        # One request scope opens at most one workspace: a retry, or a second
        # call in the same turn, reads the receipt instead of opening another.
        arguments = {k: v for k, v in args.items() if not k.startswith("_") and k != "request_id"}
        if args.get("image_refs"):
            arguments["image_scope"] = args.get("_image_scope", "")
        previous = await asyncio.to_thread(
            self.ledger.claim, "workspace-create", scope, "open_workspace", arguments, 0,
        )
        if previous is not None:
            return previous
        result = await self._open_workspace(args, graph, trace_id=trace_id, creation_id=scope)
        result["request_id"] = scope
        await asyncio.to_thread(self.ledger.finish, "workspace-create", scope, result)
        return result

    async def _open_workspace(
        self, args: dict[str, Any], graph: dict[str, Any], *, trace_id: str, creation_id: str,
    ) -> dict[str, Any]:
        """Open a folder by path, or a known project by name or ID, as a NEW workspace."""
        from .session import workspace_changed_event

        folder = str(args.get("folder") or "").strip()
        # An explicit project_id with a folder is checked by registry.start.
        project_id: str | None = str(args.get("project_id") or "").strip() or None
        project_ref = str(args.get("project_id") or args.get("project") or "").strip()
        if not folder and project_ref:
            owners = _best(
                project_ref, graph["projects"], lambda p: ((p["id"], p["path"]), (p["name"],))
            )
            if len(owners) == 1:
                folder, project_id = owners[0]["path"], owners[0]["id"]
        if not folder:
            return {
                "status": "needs_clarification",
                "kind": "folder",
                "reason": (
                    "Give the folder's absolute path or a known project. To start in a folder "
                    "that does not exist yet, create it first (find-app-action 'create folder')."
                ),
                "candidates": [
                    {"project": p["name"], "folder": p["path"]} for p in graph["projects"]
                ],
            }
        groups = self._groups(args)
        if isinstance(groups, dict):
            return groups
        requested = [{"agent": cli} for cli, count in groups for _ in range(count)]
        if len(requested) > MAX_PANES_PER_REQUEST:
            return {
                "status": "not_accepted",
                "reason": f"At most {MAX_PANES_PER_REQUEST} terminals open in one go.",
            }
        try:
            session = await self.registry.start(
                folder,
                requested,
                project_id=project_id,
                name=str(args.get("name") or "").strip() or None,
            )
        except SessionError as exc:  # the refusal is returned to the caller with its reason
            return {"status": "not_accepted", "reason": str(exc), "folder": folder}
        await self._announce(
            lambda: workspace_changed_event(
                session, "opened", source_layer="agentic_ide.orchestration"
            )
        )
        targets = [
            {
                "project_id": session.project_id,
                "workspace_id": session.id,
                "terminal_id": "pane:" + t.history_id,
            }
            for t in session.terminals
        ]
        delivery_ids = [self._issue(target) for target in targets]
        result: dict[str, Any] = {
            "status": "opened",
            "project_id": session.project_id,
            "workspace_id": session.id,
            "workspace": session.name,
            "folder": session.folder,
            "agents": [
                {"terminal_id": target["terminal_id"], "name": t.name, "cli": t.agent,
                 "request_id": request_id}
                for target, t, request_id in zip(
                    targets, session.terminals, delivery_ids, strict=True
                )
            ],
        }
        if creation_id:
            # Persist identity before the brief, as create does: a cancelled or
            # late result must never become permission to open another workspace.
            await asyncio.to_thread(
                self.ledger.finish, "workspace-create", creation_id,
                {**result, "status": "uncertain", "success": False, "request_id": creation_id,
                 "reason": "The new workspace exists; startup/delivery is pending. Observe "
                           "only these IDs. Do not reopen, reassign or send correction prompts."},
            )
        prompt = str(args.get("prompt") or "").strip()
        if prompt:
            result["deliveries"] = await self._brief(
                targets,
                prompt,
                trace_id,
                request_ids=delivery_ids,
                image_refs=args.get("image_refs", []),
                image_scope=args.get("_image_scope", ""),
            )
            result["success"] = all(d.get("status") == "accepted" for d in result["deliveries"])
        return result

    @staticmethod
    def _groups(args: dict[str, Any]) -> list[tuple[str | None, int]] | dict[str, Any]:
        """``agents: [{cli, count}]`` (or one ``cli``/``count``) as (CLI, count) pairs.

        "Five Claude Code and three Codex" is one request; each group keeps the
        CLI as spoken until ``_coding_cli`` names the pane's real CLI.
        """
        raw = args.get("agents")
        entries = raw if isinstance(raw, list) and raw else [args]
        groups: list[tuple[str | None, int]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            spoken = str(entry.get("cli") or "").strip()
            cli = _coding_cli(spoken) if spoken else _default_cli()
            if cli is None:
                from jarvis.workspace import agents as workspace_agents

                return {
                    "status": "needs_clarification",
                    "kind": "cli",
                    "reason": f"'{spoken}' is not a coding CLI this app can open.",
                    "candidates": [
                        a.name for a in workspace_agents.coding_agents() if accepts_prompts(a.name)
                    ],
                }
            try:
                count = int(entry.get("count") or 1)
            except (TypeError, ValueError):  # a malformed count falls back to one terminal
                count = 1
            groups.append((cli, max(1, min(count, MAX_PANES_PER_REQUEST))))
        return groups or [(_default_cli(), 1)]

    async def _brief(
        self, targets: list[dict[str, str]], prompt: str, trace_id: str,
        *, request_ids: list[str], image_refs: list[str] | None = None, image_scope: str = "",
    ) -> list[dict[str, Any]]:
        return list(
            await asyncio.gather(
                *(
                    self.run(
                        {
                            "action": "send",
                            **target,
                            "request_id": request_id,
                            "prompt": prompt,
                            "image_refs": image_refs or [],
                            "_image_scope": image_scope,
                        },
                        trace_id=trace_id,
                    )
                    for target, request_id in zip(targets, request_ids, strict=True)
                )
            )
        )

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
        else a resolve for the exact same terminal — the one this prompt
        already went out under (a retry), or the newest unused one — supplies
        the target. Conflicting real IDs are refused. A request already spent
        on a different prompt is a ledger collision, never a fresh delivery.
        """
        given = {key: str(args.get(key) or "").strip() for key in _TARGET_KEYS}
        request_id = str(args.get("request_id") or "").strip()
        prompt = _assignment(args)
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
                if given["terminal_id"] == target["terminal_id"]
                and all(not given[k] or given[k] == target[k] for k in _TARGET_KEYS)
            ]
            now = time.monotonic()
            retry = [
                (at, rid)
                for at, rid, sent in same_pane
                if sent and sent == prompt and now - at <= _RETRY_WINDOW_S
            ]
            fresh = [(at, rid) for at, rid, sent in same_pane if not sent or action != "send"]
            if retry or fresh:
                match = max(retry or fresh)[1]
        if not match:
            return given, request_id
        target, at, sent = issued[match]
        for key, value in given.items():
            if not value or value == target[key]:
                continue
            known = (
                self.registry.find_terminal(value) is not None if key == "terminal_id"
                else self.registry.get(value) is not None if key == "workspace_id"
                else any(owner.project_id == value for owner in self.registry.sessions)
            )
            if known or _distance(value, target[key]) > _NEAR_MISS:
                raise ValueError(
                    "Request ID and target IDs conflict; nothing was sent. Resolve again."
                )
        # A spent request ID owns its original assignment forever. Changing the
        # prompt must collide with its ledger claim, never mint a new delivery.
        return dict(target), match

    def _mark_sent(self, request_id: str, prompt: str) -> None:
        """Record what went out under ``request_id``; its clock now dates the send."""
        with self._issued_lock:
            if request_id in self._issued:
                target, _, _ = self._issued[request_id]
                self._issued[request_id] = (target, time.monotonic(), prompt)

    async def _gate_existing(
        self, action: str, args: dict[str, Any], creation: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Existing-pane work inside a request that asks for NEW panes.

        Returns the final reply, or ``None`` when the call may proceed. Only a
        pane the caller NAMED qualifies — its call-sign, custom name or ID via
        resolve. A first-idle pick, a CLI-kind match ("the Claude Code agent")
        or a bare terminal_id is how a new task used to land on an old pane, so
        those stay refused, as do restore/show and this request's own brief.
        """
        refusal: dict[str, Any] = {
            "status": "new_agent_required", "success": False,
            "reason": "This request asks for NEW panes: use create (or open_workspace for a "
                      "new workspace). An existing agent is addressed only by its name "
                      "(resolve with agent=<its call-sign or name>); never an idle pick, a CLI "
                      "kind, this request's new task, or a correction to other agents.",
            "creation": creation,
        }
        agent_ref = str(args.get("agent") or "").strip()
        if action == "resolve":
            graph = await asyncio.to_thread(self.graph)
            if agent_ref:
                resolved = self.resolve(args, graph)
                if resolved.get("status") != "resolved" or resolved["request_id"] in self._named:
                    return resolved
                with self._issued_lock:
                    self._issued.pop(resolved["request_id"], None)
            # The IDs a following create needs, without minting a pane target.
            picked = self._workspace(args, graph, "")
            if isinstance(picked, tuple):
                project, workspace = picked
                refusal |= {
                    "project_id": project["id"], "project": project["name"],
                    "workspace_id": workspace["id"], "workspace": workspace["name"],
                }
            return refusal
        if action not in {"send", *_PANE_ACTIONS}:
            return refusal
        if action != "send" and agent_ref and not str(args.get("terminal_id") or "").strip():
            resolved = self.resolve(args, await asyncio.to_thread(self.graph))
            if resolved.get("status") != "resolved":
                return resolved
            return None if resolved["request_id"] in self._named else refusal
        _, match = self._reconcile(args, action)
        if match not in self._named:
            return refusal
        if action == "send":
            assignment = _assignment(args)
            with self._issued_lock:
                briefs = {
                    self._issued[a["request_id"]][2]
                    for a in (creation or {}).get("agents", [])
                    if a.get("request_id") in self._issued
                }
            if assignment in briefs:
                # The new panes' brief must not also reach an old pane.
                return {**refusal, "status": "delivery_already_owned",
                        "reason": "This task already went to the panes this request created. "
                                  "Observe those IDs; do not hand it to another agent."}
        return None

    async def run(self, args: dict[str, Any], *, trace_id: str = "") -> dict[str, Any]:
        if not self._ledger_compatible:
            return {
                "status": "restart_required", "success": False, "executed": False,
                "reason": "Workspace control and its receipt store are from different app "
                          "versions. Restart Personal Jarvis before retrying. This request "
                          "created no workspace or agent and sent no prompt. Do not retry, "
                          "reassign, or use another delivery tool before restarting.",
            }
        action = args.get("action")
        from jarvis.core.image_references import get_store

        refs = args.get("image_refs", [])
        image_scope = str(args.get("_image_scope") or "")
        dispatch_scope = str(args.get("_dispatch_scope") or "")
        creation = (
            await asyncio.to_thread(self.ledger.operation, "workspace-create", dispatch_scope)
            if dispatch_scope else None
        )
        # open_workspace always starts a NEW workspace with NEW panes, so it is
        # a creation like create (live 2026-10-07: a new-agent workspace request
        # was refused as pane reuse and nothing opened).
        created = {a["terminal_id"] for a in (creation or {}).get("agents", [])}
        to_created = action == "send" and str(args.get("terminal_id") or "") in created
        if (args.get("_requires_new") or creation) and not to_created and action not in {
            "inspect", "create", "open_workspace", "context", "observe",
        }:
            # A NEW request cannot borrow an idle pane for its new task, but it
            # may also carry independent work for an agent the user NAMED
            # (live 2026-10-07: "first update the PR #430 session, then start a
            # new bug-fix session" refused the update as pane reuse).
            gated = await self._gate_existing(action, args, creation)
            if gated is not None:
                return gated
        if creation and to_created:
            # create(prompt=...) already owns delivery, including uncertainty.
            if creation and (creation.get("deliveries") or creation.get("status") == "uncertain"):
                return {
                    "status": "delivery_already_owned", "success": False,
                    "reason": "Creation already owns this delivery. Observe its returned IDs; "
                              "do not resend, rewrite the brief, or reassign.",
                    "creation": creation,
                }
            issued = next(a["request_id"] for a in creation["agents"]
                          if a["terminal_id"] == args["terminal_id"])
            if args.get("request_id") and args["request_id"] != issued:
                raise ValueError("Use the request ID returned for this new pane; nothing was sent.")
            args = {**args, "request_id": issued}
        if refs and action not in {"create", "open_workspace", "send"}:
            raise ValueError("Image references belong to create, open_workspace or send only.")
        if refs and not str(args.get("prompt") or "").strip():
            raise ValueError("Image references require a specific task prompt.")
        if action in {"create", "open_workspace"}:
            get_store().resolve(image_scope, refs)
        if action in {"inspect", "resolve"}:
            graph = await asyncio.to_thread(self.graph)
            return graph if action == "inspect" else self.resolve(args, graph)
        if action == "create":
            return await self.create(args, trace_id=trace_id)
        if action in _PANE_ACTIONS:
            return await self.pane_action(args)
        if action in _WORKSPACE_ACTIONS:
            return await self.workspace_action(args, trace_id=trace_id)
        if action not in {"send", "context"}:
            raise ValueError("Unknown workspace orchestration action.")
        while_busy = str(args.get("while_busy") or "refuse").strip().casefold()
        if while_busy not in _WHILE_BUSY:
            raise ValueError(f"while_busy must be one of {', '.join(_WHILE_BUSY)}.")
        if while_busy != "refuse" and action != "send":
            raise ValueError("while_busy belongs to send only.")
        target, request_id = self._reconcile(args, action)
        project_id, workspace_id, terminal_id = (target[key] for key in _TARGET_KEYS)
        if not project_id or not workspace_id or not terminal_id.startswith("pane:"):
            raise ValueError(
                "Resolve a target first; project_id, workspace_id and terminal_id are required."
            )
        prompt = ""
        receipt_id = request_id
        if action == "send":
            prompt = str(args.get("prompt") or "").strip()
            if not prompt:
                raise ValueError("Sending requires a prompt.")
            if not _is_request_id(request_id):
                # Nothing usable to key on: derive a key so an immediate retry
                # of this same task cannot deliver twice, while the same words
                # sent again minutes later are a new instruction.
                seed = f"{terminal_id}\n{prompt}\n{refs}\n{int(time.time() // _RETRY_WINDOW_S)}"
                request_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
            self._mark_sent(request_id, _assignment(args))
            # The assignment, never the delivery mode: resending a refused
            # request with while_busy is the same instruction, not a new one.
            claimed = {**target, "prompt": prompt, "image_refs": refs,
                       **({"image_scope": image_scope} if refs else {})}
            # A receipt proving nothing was typed (busy, closed, not a coding
            # pane) does not spend the request: retrying the same assignment
            # takes the next attempt slot. Anything else, including an
            # unconfirmed write, stays the answer (live 2026-10-07: an approved
            # brief refused as "busy" could never be retried once idle).
            for attempt in range(_REFUSED_ATTEMPTS):
                receipt_id = request_id if not attempt else f"{request_id}~{attempt}"
                previous = await asyncio.to_thread(
                    self.ledger.claim, "workspace-orchestration", receipt_id, "send",
                    claimed, 0, deduplicate_unconfirmed=True,
                )
                if (
                    previous is not None
                    and previous.get("status") == "queued"
                    and receipt_id not in self._queued
                ):
                    # Queued by an earlier run of the app, which stopped before
                    # the turn ended: nothing was typed, so the slot is free.
                    previous = {
                        **previous, "status": "expired", "success": False,
                        "input_written": False,
                        "reason": "The app stopped before the queued message was delivered; "
                                  "nothing was typed.",
                    }
                    await asyncio.to_thread(
                        self.ledger.finish, "workspace-orchestration", receipt_id, previous
                    )
                if previous is None or not _never_delivered(previous):
                    break
            if previous is not None:
                return {"target": target, "request_id": request_id, **previous}
        delivery = {
            "action": action, "target": target, "prompt": prompt, "refs": refs,
            "image_scope": image_scope, "trace_id": trace_id, "request_id": request_id,
            "limit": args.get("limit", 30),
        }
        result = await self._deliver(
            delivery, when_busy="refuse" if while_busy == "queue" else while_busy,
        )
        armed: asyncio.Event | None = None
        if action == "send" and result.get("busy") and (
            while_busy == "queue" or result.get("interrupted")
        ):
            # Stop was pressed, or the user asked to wait: the message is held
            # until the turn ends, under this same receipt.
            result, armed = self._queue(
                receipt_id, delivery, interrupted=bool(result.get("interrupted")),
            )
        try:
            if action == "send":
                result["request_id"] = request_id
                await asyncio.to_thread(
                    self.ledger.finish, "workspace-orchestration", receipt_id, result
                )
        finally:
            if armed is not None:
                # Only now may the queue type it: its outcome must replace the
                # "queued" receipt, never be overwritten by it.
                armed.set()
        return result

    async def _deliver(self, delivery: dict[str, Any], *, when_busy: str) -> dict[str, Any]:
        """One attempt at a send or context read on the resolved pane."""
        action, target = delivery["action"], delivery["target"]
        workspace_id, terminal_id = target["workspace_id"], target["terminal_id"]
        owner = self.registry.get(workspace_id)
        found = self.registry.find_terminal(terminal_id, workspace_id) if owner else None
        if (
            owner is None
            or owner.project_id != target["project_id"]
            or found is None
            or found[1].archived
        ):
            return {
                "status": "stale_target",
                "target": target,
                "reason": "The resolved coding session is no longer in that project/workspace.",
            }
        if not accepts_prompts(found[1].agent):
            return {
                "status": "unavailable",
                "target": target,
                "reason": "This session does not accept coding tasks.",
            }
        # The low-level gateway enforces idle/asking state and refuses exited
        # agents. It never falls through to a shell or a different pane.
        try:
            reply = await self.sessions.run(
                {
                    "action": action,
                    "workspace_id": workspace_id,
                    "terminal_id": terminal_id,
                    **(
                        {"prompt": delivery["prompt"], "image_refs": delivery["refs"],
                         "_image_scope": delivery["image_scope"], "when_busy": when_busy}
                        if action == "send"
                        else {"limit": delivery["limit"]}
                    ),
                }
            )
            return {
                **reply,
                "status": reply.get("delivery", reply.get("status", "observed")),
                "target": target,
                "trace_id": delivery["trace_id"],
                "request_id": delivery["request_id"],
            }
        except AgentBusyError as exc:
            # Pre-write, like every SessionError, and the one refusal the
            # caller can answer with another delivery the USER chose.
            can_steer = steers_mid_turn(found[1].agent)
            return {
                "status": "not_accepted",
                "target": target,
                "reason": str(exc),
                "completed": False,
                "input_written": False,
                "busy": True,
                **({"interrupted": True} if exc.interrupted else {}),
                "while_busy_options": {"steer": can_steer, "interrupt": True, "queue": True},
                "next": (
                    "The agent is in a turn. Only when the USER is correcting or redirecting "
                    "this running work, resend this request with "
                    + ("while_busy='steer' (reaches the running turn; nothing stops) or "
                       if can_steer else "")
                    + "while_busy='interrupt' (stops the turn, keeps the session, then "
                    "delivers). while_busy='queue' delivers it when the turn ends. A new or "
                    "unrelated task waits, goes to another agent, or is the user's call."
                ),
            }
        except SessionError as exc:
            # The coding-session adapter documents SessionError as a
            # pre-write refusal; post-write uncertainty is a receipt.
            return {
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
            if action != "send":
                raise
            return {
                "status": "uncertain",
                "target": target,
                "request_id": delivery["request_id"],
                "reason": (
                    "Delivery could not be confirmed. Inspect the session; "
                    "do not resend automatically."
                ),
            }

    def _queue(
        self, receipt_id: str, delivery: dict[str, Any], *, interrupted: bool,
    ) -> tuple[dict[str, Any], asyncio.Event]:
        """Hold a message until its pane ends the turn, then type it there.

        Registered before the caller files the "queued" receipt, so a retry in
        between never takes it for one an earlier run of the app left behind.
        The returned event releases the queue once that receipt is filed.
        """
        armed = asyncio.Event()
        self._queued[receipt_id] = asyncio.create_task(
            self._drain(receipt_id, delivery, time.monotonic() + _QUEUE_TTL_S, armed),
            name=f"workspace-queue:{receipt_id}",
        )
        return {
            "status": "queued",
            "target": delivery["target"],
            "input_written": False,
            "completed": False,
            **({"interrupted": True} if interrupted else {}),
            "reason": (
                ("Stop was pressed but the turn had not ended yet. " if interrupted else "")
                + "The app holds the message and types it at the agent's prompt as soon as "
                f"its turn ends (for up to {_QUEUE_TTL_S // 60} minutes). This request_id "
                "then reads the delivery result. Do not resend; an app restart before "
                "delivery drops it unsent (reported as expired)."
            ),
        }, armed

    async def _drain(
        self, receipt_id: str, delivery: dict[str, Any], deadline: float, armed: asyncio.Event,
    ) -> None:
        """Deliver one queued message once its pane is free, then file the receipt."""
        target = delivery["target"]
        result: dict[str, Any] | None = None
        try:
            await armed.wait()
            while time.monotonic() < deadline:
                await asyncio.sleep(_QUEUE_POLL_S + random.uniform(0.0, 1.0))  # noqa: S311
                found = self.registry.find_terminal(target["terminal_id"], target["workspace_id"])
                # A cheap fresh check first: an attempt resolves and copies
                # images, which is wasted on a pane that is still working.
                if found is not None and await self.registry.turn_in_progress(found[1]):
                    continue
                outcome = await self._deliver(delivery, when_busy="refuse")
                if outcome.get("busy"):
                    continue  # Someone else's prompt got there first.
                result = outcome
                break
        except asyncio.CancelledError:
            result = {
                "status": "expired", "success": False, "target": target,
                "input_written": False,
                "reason": "The app stopped before the queued message was delivered; "
                          "nothing was typed.",
            }
            raise
        finally:
            if result is None:
                result = {
                    "status": "expired", "success": False, "target": target,
                    "input_written": False,
                    "reason": f"The turn did not end within {_QUEUE_TTL_S // 60} minutes; "
                              "the queued message was not typed.",
                }
            result = {**result, "request_id": delivery["request_id"], "queued": True}
            try:
                # Synchronous on purpose: this also runs while the loop is
                # cancelling the task, where no thread hop can be awaited.
                self.ledger.finish("workspace-orchestration", receipt_id, result)
            except Exception as exc:  # noqa: BLE001 - a lost receipt is logged, never raised
                from loguru import logger

                logger.warning("Queued delivery receipt was not saved: {}", exc)
            self._queued.pop(receipt_id, None)


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
