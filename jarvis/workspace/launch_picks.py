"""How a coding CLI takes a model, an effort level and a permission stance.

A workspace pane runs a vendor CLI interactively in a PTY, and every one of
them takes those three picks the same way: as flags on the launch argv. What
differs is the spelling — ``--model`` here, ``-m`` there, a ``-c key=value``
config override somewhere else — and which permission words the binary knows.

So each entry DECLARES its spelling (``WorkspaceAgent.launch_picks``) and this
module turns a pick into argv. Nothing here branches on a product name (AP-21):
an entry that declares nothing simply offers no picks, and a CLI added later
gets them by filling the field in.

The vocabulary is not invented here either. The ladders and the model lists are
the agent chat's (:mod:`jarvis.agent_chat.catalog`, ``effort``, ``permissions``)
via one ``provider`` id per entry, so a pick means the same thing in a workspace
pane as it does in a chat session, and a model added to the catalog appears in
both.

**Every value that reaches an argv is checked against what the entry offers.**
A permission mode has to be one this CLI declared, an effort level one its
ladder holds, and a model either one of the offered ids or — for a CLI whose
list is the user's own (OpenCode's ``provider/model``) — a plain identifier.
Anything else is dropped rather than passed on: these strings arrive from a
browser, and an argv is the one place a stray ``--flag`` would be read as an
instruction.
"""

from __future__ import annotations

import functools
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

_log = logging.getLogger(__name__)

#: The placeholder an argument template carries where the pick belongs.
VALUE: Final[str] = "{value}"

#: Where the chat's ladder says an approval is answered, and where a PANE
#: answers it instead. The stance is identical — only the place differs: a
#: pane is the CLI's own TUI, so it asks there and the chat stage's "show
#: terminal" button is the way to it. One substitution rather than a second
#: ladder to keep in step; ``tests/`` pins the phrases so a reworded ladder
#: fails loudly instead of quietly leaving a wrong sentence in a picker.
_CHAT_PLACE: Final[tuple[tuple[str, str], ...]] = (
    ("an approval card here in the chat", "the CLI's own prompt in the terminal"),
    (
        "in the chat the CLI cannot ask back, so they are declined and reported",
        "in a pane the CLI asks in its own terminal",
    ),
)

#: What a model id may look like when the CLI publishes no list to check
#: against. Deliberately narrow: letters, digits and the punctuation vendors
#: actually use in a model id — a slash for OpenCode's ``provider/model``, a
#: colon for Ollama-style tags, brackets for Claude Code's ``[1m]`` variants.
_MODEL_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]{0,119}$")


@dataclass(frozen=True, slots=True)
class LaunchPicks:
    """The three picks one CLI accepts when a pane opens, in its own spelling.

    ``provider`` names the agent-chat catalog row this entry shares its model
    list and its ladders with — ``""`` for a CLI with no row, which then offers
    the permission modes it declares below and no model or effort list at all.

    The three argument fields are TEMPLATES: each string is passed through with
    :data:`VALUE` replaced by the pick, which covers both the ``("--model",
    "{value}")`` shape and Codex's ``("-c", "model_reasoning_effort={value}")``.
    ``permission_args`` is a mapping written as ordered pairs so a mode maps to
    whatever combination of flags expresses it — one mode is one entry, and a
    mode absent from it is a mode this CLI cannot be launched into.
    """

    provider: str = ""
    model_args: tuple[str, ...] = ()
    effort_args: tuple[str, ...] = ()
    permission_args: tuple[tuple[str, tuple[str, ...]], ...] = field(default_factory=tuple)
    #: Which permission ladder describes the modes above, when it is not the
    #: one behind ``provider``. For a CLI with no catalog row of its own —
    #: OpenCode, Kimi — this is what gives its modes real sentences instead of
    #: their bare ids.
    ladder: str = ""
    #: True for a CLI that REFUSES a model pick unless an effort level comes
    #: with it (``agy``: a base Gemini id without ``--effort`` is an error).
    #: Such an entry gets its default level rather than a pane that dies on
    #: the command line.
    effort_required: bool = False

    @property
    def permission_modes(self) -> tuple[str, ...]:
        """The modes this CLI can be launched into, in the declared order."""
        return tuple(mode for mode, _ in self.permission_args)

    def permission_argv(self, mode: str) -> tuple[str, ...] | None:
        """The flags for ``mode``, or None when this CLI does not know it."""
        for name, argv in self.permission_args:
            if name == mode:
                return argv
        return None


def flag_modes(flag: str, *modes: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Permission pairs for a CLI that takes every mode behind one flag.

    The common shape — ``--permission-mode acceptEdits`` — written once so an
    entry lists the words it accepts instead of repeating the flag beside each.
    """
    return tuple((mode, (flag, mode)) for mode in modes)


def _fill(template: tuple[str, ...], value: str) -> tuple[str, ...]:
    return tuple(part.replace(VALUE, value) for part in template)


def _ladder_key(picks: LaunchPicks) -> str:
    """Which permission ladder describes this entry's modes, if any.

    The entry's own declaration wins; otherwise the runner behind its catalog
    row, which is where a CLI that has one gets the same sentences the chat
    composer shows for it.
    """
    if picks.ladder:
        return picks.ladder
    if not picks.provider:
        return ""
    from jarvis.agent_chat.catalog import provider_row

    row = provider_row(picks.provider)
    return row.runner if row is not None else ""


def picks_for(agent: str) -> LaunchPicks | None:
    """What ``agent`` accepts at launch, or None for an entry that declares nothing."""
    from jarvis.workspace import agents as workspace_agents

    spec = workspace_agents.get_agent(agent)
    return None if spec is None else spec.launch_picks


def effort_levels(agent: str) -> tuple[str, ...]:
    """The effort ladder this entry offers, empty when it takes no effort pick."""
    picks = picks_for(agent)
    if picks is None or not picks.effort_args or not picks.provider:
        return ()
    from jarvis.agent_chat.effort import effort_levels as ladder

    return tuple(level for level in ladder(picks.provider) if level)


def default_effort(agent: str) -> str:
    """The level a fresh pane runs on when nobody picks one."""
    picks = picks_for(agent)
    if picks is None or not picks.effort_args or not picks.provider:
        return ""
    from jarvis.agent_chat.effort import default_effort as fallback

    return fallback(picks.provider)


def default_permission(agent: str) -> str:
    """The stance a fresh pane opens in when nobody picks one.

    The CLI's OWN default — an empty string — rather than a mode this app
    chose for it: a pane is a live TUI the person is sitting in front of, so
    the binary's own out-of-the-box behaviour is the honest starting point and
    every other stance is something they asked for.
    """
    return ""


def normalize_effort(agent: str, level: str | None) -> str:
    """Fold ``level`` onto this entry's ladder; "" when it takes no effort pick."""
    wanted = (level or "").strip().lower()
    if not wanted:
        return ""
    offered = effort_levels(agent)
    if not offered:
        return ""
    from jarvis.agent_chat.effort import snap_to_ladder

    return snap_to_ladder(wanted, list(offered))


def normalize_permission(agent: str, mode: str | None) -> str:
    """Keep ``mode`` when this CLI knows the word, else drop it.

    No folding onto a neighbouring stance here, unlike the chat runner's
    ladder: a pane's permission stance is the one thing where landing NEAR
    what was asked for is worse than landing on the CLI's own default, which
    at least asks before it acts.
    """
    picks = picks_for(agent)
    wanted = (mode or "").strip()
    if picks is None or not wanted:
        return ""
    return wanted if picks.permission_argv(wanted) is not None else ""


def normalize_model(agent: str, model: str | None) -> str:
    """Keep ``model`` when this CLI could plausibly be launched on it.

    An id from the offered list always passes. A CLI whose models are the
    user's own accounts and endpoints (OpenCode, Kimi) publishes no list to
    check against, so the shape is checked instead — which is what keeps a
    browser from putting a second flag on the argv.
    """
    picks = picks_for(agent)
    wanted = (model or "").strip()
    if picks is None or not picks.model_args or not wanted:
        return ""
    offered = {row["id"] for row in offered_models(agent)}
    if wanted in offered:
        return wanted
    if offered:
        # The CLI publishes a list and this is not on it: a stale pick from a
        # client that has not reloaded its catalog. The CLI's own default is
        # the honest answer, not an id it would reject on the command line.
        return ""
    return wanted if _MODEL_RE.match(wanted) else ""


async def live_models() -> dict[str, list[dict[str, Any]]]:
    """The model lists the installed CLIs publish, keyed by runner.

    ``agy`` answers ``agy models`` and OpenCode ``opencode models`` (each a
    ~2 s subprocess, cached ten minutes in the runner module); Codex and
    Grok Build keep ``models_cache.json`` in their home. All are read off
    the event loop, and a failure simply leaves the curated fallback standing.

    One reader for both surfaces on purpose: the chat composer and a workspace
    pane offer the same CLI, so a list either of them read alone would be a
    second answer to one question — and the two would drift the first time an
    account gained a model.
    """
    import asyncio

    from jarvis.agent_chat.runner_cli import (
        read_agy_models,
        read_codex_models,
        read_grok_models,
        read_opencode_models,
    )

    out: dict[str, list[dict[str, Any]]] = {}
    if _installed("claude-cli"):
        # A plan login publishes no model list; the public discovery feed is
        # what brings a new Claude release into the picker (claude_code_models).
        from jarvis.brain.model_catalog import shared_catalog

        try:
            await asyncio.wait_for(shared_catalog().refresh_discovery(), 3.0)
        except Exception as exc:  # noqa: BLE001 — the cached/curated list stands in
            _log.debug("launch picks: model discovery unavailable: %s", exc)
    # Every reader runs at once, and the answer waits at most
    # ``_LIVE_MODELS_BUDGET_S``: a cold ``agy models`` has taken well over a
    # minute on a real box, and summing the readers one after another kept the
    # picker empty for ten seconds and more. A reader still running when the
    # budget ends keeps going in its thread and fills its runner-module cache,
    # so the next catalog request answers from it; this one leaves that
    # runner's curated fallback standing (its key stays absent, never an empty
    # list that would blank the picker).
    readers = {
        "agy-cli": read_agy_models,
        "codex-cli": read_codex_models,
        "grok-cli": read_grok_models,
        "opencode-cli": read_opencode_models,
    }
    installed = await _off_loop(lambda: [r for r in readers if _installed(r)])
    reads = {runner: _shared_read(runner, readers[runner]) for runner in installed}
    tasks = {runner: task for runner, (task, _) in reads.items()}
    # A read an EARLIER request started (still running past its budget) is not
    # waited for again: otherwise every picker open pays the full budget until
    # a minute-long ``agy models`` finally ends.
    fresh = [task for task, started_here in reads.values() if started_here]
    if fresh:
        await asyncio.wait(fresh, timeout=_LIVE_MODELS_BUDGET_S)
    done = {task for task in tasks.values() if task.done()}
    pending = {task for task, started_here in reads.values() if started_here and not task.done()}
    for task in pending:
        # Nobody awaits it any more; keep it referenced until it finishes and
        # log its failure instead of letting asyncio warn about it.
        _BACKGROUND_READS.add(task)
        task.add_done_callback(_finish_background_read)
    for runner, task in tasks.items():
        if task not in done:
            _log.debug("launch picks: %s model list still loading; fallback stands", runner)
            continue
        failure = task.exception()
        if failure is not None:
            _log.debug("launch picks: %s model list unavailable: %s", runner, failure)
        rows = None if failure is not None else task.result()
        if runner == "codex-cli":
            # Codex answered: an empty list means "nothing confirmed" for it.
            out[runner] = rows or []
        elif runner == "opencode-cli":
            # No list is an empty picker, not an error.
            out[runner] = rows or []
        elif rows:
            out[runner] = rows
    return out


#: How long a catalog request waits for the CLIs' own model lists.
_LIVE_MODELS_BUDGET_S: Final = 3.0

#: Model-list reads that outlived their request, kept alive until they finish.
_BACKGROUND_READS: set[Any] = set()


#: One in-flight read per runner and catalog scope: a second request arriving
#: while ``agy models`` still runs joins that read instead of starting another.
_IN_FLIGHT: dict[tuple[Any, ...], Any] = {}


def _shared_read(runner: str, read: Any) -> tuple[Any, bool]:
    """The in-flight read for ``runner`` in this scope, and whether this call started it."""
    import asyncio

    from jarvis.agent_chat import runner_cli

    key = (
        runner,
        runner_cli.ACCOUNT_OVERRIDE.get(),
        runner_cli._CATALOG_CWD.get(),
        runner_cli._CATALOG_IGNORE_CONFIG.get(),
    )
    task = _IN_FLIGHT.get(key)
    if task is not None and not task.done():
        return task, False
    task = asyncio.ensure_future(_off_loop(read))
    _IN_FLIGHT[key] = task
    task.add_done_callback(functools.partial(_forget_read, key))
    return task, True


#: The CLI model-list reads get their own threads. A cold ``agy models`` can
#: run for over a minute, and in the loop's shared default pool a few of them
#: left every other ``to_thread`` caller in the app queueing behind them —
#: including this module's own installed-check, which runs before the budget.
_READ_POOL: Any = None


def _off_loop(fn: Any) -> Any:
    """Run ``fn`` on the model-list pool, carrying the catalog scope's context."""
    import asyncio
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    global _READ_POOL
    if _READ_POOL is None:
        _READ_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="cli-model-list")
    ctx = contextvars.copy_context()
    return asyncio.get_running_loop().run_in_executor(_READ_POOL, ctx.run, fn)


def _forget_read(key: tuple[Any, ...], task: Any) -> None:
    if _IN_FLIGHT.get(key) is task:
        del _IN_FLIGHT[key]


def _finish_background_read(task: Any) -> None:
    _BACKGROUND_READS.discard(task)
    if not task.cancelled() and task.exception() is not None:
        _log.debug("launch picks: background model list failed: %s", task.exception())


def _installed(runner: str) -> bool:
    """Is the binary behind ``runner`` resolvable from here?"""
    from jarvis.agent_chat.runner_cli import cli_installed

    return cli_installed(runner)


def runner_of(agent: str) -> str:
    """Which chat runner this entry's CLI is, "" when it has no catalog row."""
    picks = picks_for(agent)
    if picks is None or not picks.provider:
        return ""
    from jarvis.agent_chat.catalog import provider_row

    row = provider_row(picks.provider)
    return row.runner if row is not None else ""


def offered_models(
    agent: str, live: Mapping[str, list[dict[str, Any]]] | None = None
) -> list[dict[str, Any]]:
    """The models a picker may offer for this entry, newest-first as curated.

    The catalog's list for the entry's provider row, replaced by the CLI's own
    when it publishes one (``agy models``, Codex's ``models_cache.json``) —
    the same two sources the chat composer reads, so the two pickers never
    disagree about what a CLI can run.
    """
    picks = picks_for(agent)
    if picks is None or not picks.model_args or not picks.provider:
        return []
    from jarvis.agent_chat.catalog import claude_code_models, provider_row

    row = provider_row(picks.provider)
    if row is None:
        return []
    # What THIS account can actually pick, when the CLI was asked and answered.
    if live is not None and row.runner in live:
        return list(live[row.runner])
    # Claude Code takes its own ids and aliases rather than the Anthropic
    # API's catalog — the same exception the chat catalog route makes.
    if picks.provider == "claude-api":
        return [m.to_dict() for m in claude_code_models()]
    return [m.to_dict() for m in row.curated_models]


def permission_modes(agent: str) -> list[dict[str, str]]:
    """The permission ladder a picker may offer, in this entry's own order.

    The words come from the runner's ladder in :mod:`jarvis.agent_chat.
    permissions` — the same sentences the chat composer shows — narrowed to
    the modes this entry can actually be LAUNCHED into. A mode the ladder
    describes but the CLI has no flag for is left out rather than offered and
    quietly ignored.
    """
    picks = picks_for(agent)
    if picks is None or not picks.permission_args:
        return []
    described: dict[str, dict[str, str]] = {}
    if key := _ladder_key(picks):
        from jarvis.agent_chat.permissions import permission_modes as ladder

        described = {mode.id: _in_a_pane(mode.to_dict()) for mode in ladder(key)}
    return [
        described.get(mode, {"id": mode, "label": mode, "description": ""})
        for mode in picks.permission_modes
    ]


def _in_a_pane(mode: dict[str, str]) -> dict[str, str]:
    """The ladder's sentence, retold for a pane (see :data:`_CHAT_PLACE`)."""
    text = mode.get("description", "")
    for chat, pane in _CHAT_PLACE:
        text = text.replace(chat, pane)
    return {**mode, "description": text}


def launch_argv(
    agent: str,
    *,
    model: str = "",
    effort: str = "",
    permission_mode: str = "",
) -> tuple[str, ...]:
    """The extra argv that opens ``agent`` on these picks.

    Every value is normalized first, so a pick this CLI cannot express costs
    nothing but itself: the pane still opens, on the CLI's own default.
    """
    picks = picks_for(agent)
    if picks is None:
        return ()
    argv: list[str] = []
    chosen = normalize_model(agent, model)
    if chosen and picks.model_args:
        argv += _fill(picks.model_args, chosen)
    level = normalize_effort(agent, effort)
    if not level and chosen and picks.effort_required:
        # This CLI reads a model pick and an effort level as one instruction
        # and rejects half of it — see ``effort_required``. Its own default
        # level is a pane that opens; no level is a pane that never starts.
        level = default_effort(agent)
    if level and picks.effort_args:
        argv += _fill(picks.effort_args, level)
    if mode := normalize_permission(agent, permission_mode):
        argv += list(picks.permission_argv(mode) or ())
    return tuple(argv)


def offered(agent: str, live: Mapping[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Everything a picker needs for this entry, in the composer's own shape.

    Deliberately the shape of an agent-chat catalog row (``AgentChatProvider``
    in the frontend): the IDE's chat composer is the front page's composer, so
    handing it rows it already knows how to draw is what keeps the two
    surfaces from growing two different model pickers.
    """
    picks = picks_for(agent)
    default_model = ""
    if picks is not None and picks.provider:
        from jarvis.agent_chat.catalog import provider_row

        row = provider_row(picks.provider)
        if row is not None:
            default_model = row.default_model
    return {
        "models": offered_models(agent, live),
        "default_model": default_model,
        "effort_levels": list(effort_levels(agent)),
        "default_effort": default_effort(agent),
        "permission_modes": permission_modes(agent),
        "default_permission_mode": default_permission(agent),
    }


@dataclass(frozen=True, slots=True)
class RuntimePicks:
    """The picks a RUNNING CLI takes as a typed command, in its own spelling.

    The launch flags above are spent the moment a pane opens; from then on the
    process owns its model, effort and permission stance, and the only way in
    is the CLI's own command line — ``/effort max`` typed into Claude Code and
    submitted, which its 2.1 builds apply at once rather than queueing until
    the turn ends (their changelog says so). Each field is a TEMPLATE like the
    launch ones, ``""`` where the CLI has no typed command for that pick:
    Codex opens a picker on ``/model`` and takes no argument, Claude Code's
    permission stance cycles on Shift+Tab, and a cycle is not a pick. An entry
    that declares nothing offers nothing at runtime, and the chat's composer
    then locks that pill and says where the pick is taken instead — never a
    control that swallows a click (maintainer report, 2026-08-27).
    """

    model: str = ""
    effort: str = ""
    permission_mode: str = ""

    def command(self, pick: str, value: str) -> str:
        """The line to type for ``pick`` = ``value``, or ``""`` when there is none."""
        template = getattr(self, pick, "")
        return template.replace(VALUE, value) if template and value else ""

    def offers(self) -> dict[str, bool]:
        """Which of the three picks this CLI takes while it runs."""
        return {
            "model": bool(self.model),
            "effort": bool(self.effort),
            "permission_mode": bool(self.permission_mode),
        }


#: What an entry with no runtime declaration offers: nothing, said plainly.
_NO_RUNTIME_PICKS: Final[RuntimePicks] = RuntimePicks()


def runtime_picks_for(agent: str) -> RuntimePicks:
    """What ``agent`` takes as a typed command once it runs; empty for none."""
    from jarvis.workspace import agents as workspace_agents

    spec = workspace_agents.get_agent(agent)
    picks = None if spec is None else spec.runtime_picks
    return picks if picks is not None else _NO_RUNTIME_PICKS


def runtime_command(agent: str, pick: str, value: str) -> str:
    """The command that sets ``pick`` on a running ``agent`` — ``""`` when it has none.

    ``value`` is checked the way a launch value is: an effort level has to be
    on this entry's ladder and a model one it could be launched on. The string
    ends up on a PTY as keystrokes, which is exactly one place a stray line
    must not reach.
    """
    if pick == "effort":
        checked = normalize_effort(agent, value)
    elif pick == "model":
        checked = normalize_model(agent, value)
    elif pick == "permission_mode":
        checked = normalize_permission(agent, value)
    else:
        return ""
    if not checked:
        return ""
    return runtime_picks_for(agent).command(pick, checked)


__all__ = [
    "VALUE",
    "LaunchPicks",
    "RuntimePicks",
    "runtime_command",
    "runtime_picks_for",
    "default_effort",
    "default_permission",
    "effort_levels",
    "flag_modes",
    "launch_argv",
    "live_models",
    "normalize_effort",
    "normalize_model",
    "normalize_permission",
    "offered",
    "offered_models",
    "permission_modes",
    "picks_for",
    "runner_of",
]
