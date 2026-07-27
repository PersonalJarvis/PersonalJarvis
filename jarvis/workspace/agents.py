"""What a workspace terminal can RUN — a small, open registry.

Two kinds of entry live here, and the difference is the whole design:

* **A coding-agent CLI** (Claude Code, Codex, whatever is registered next) — a
  binary that has to be detected, may have to be installed, and is launched by
  name inside a shell.
* **A plain terminal** — this machine's own shell and nothing else. Nothing to
  detect beyond "does this host have a shell", nothing to install, and no agent
  wrapped around it. It is what you want when the job is `git rebase -i`, not
  "ask an agent to do it".

The registry is deliberately open (:func:`register_agent`): a new interactive
CLI is a spec, not a code change spread across detection, launching and the UI.
Everything downstream — the ``/agents`` endpoints, the pane split menu, the
Agentic-IDE grid — reads this list rather than a hardcoded pair of names, so
registering one entry is enough to make it offerable everywhere.

CLI entries reuse ``CliSpec`` so the existing ``CliStatusProber`` can detect
them, but they are deliberately NOT registered in the shared CLI catalog
(``jarvis/clis/catalog/seed_catalog.json``) — see ``__init__`` for why.

Cross-platform: the shell entry resolves through
:func:`jarvis.terminal.shells.default_shell`, which knows pwsh/PowerShell/cmd/
Git Bash on Windows and ``$SHELL``/``/etc/shells`` elsewhere. On a host with no
shell at all (a stripped container) the entry reports itself as not installed
rather than handing the PTY an argv that cannot start.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from jarvis.clis.prober import CliStatusProber
from jarvis.clis.spec import AuthConfig, CliSpec, InstallMethods, RiskConfig
from jarvis.terminal.shells import default_shell

log = logging.getLogger(__name__)

# A semver anywhere in the --version output. Matches both
# "2.1.195 (Claude Code)" and "codex-cli 0.142.3".
_SEMVER_RE = r"(\d+\.\d+\.\d+)"

# The same, with the patch component optional. Not every CLI ships three parts:
# the Python-generation Kimi answers ``kimi, version 1.3``, which the strict
# pattern above misses entirely — and a missed version is not a cosmetic loss.
# It reads downstream as ``version=None``, which the wizard and the resume offer
# render as a blank field next to an installed binary, i.e. exactly what a
# broken install looks like. Entries whose CLI numbers itself in two parts opt
# into this instead.
_LOOSE_SEMVER_RE = r"(\d+\.\d+(?:\.\d+)?)"

# The registry key of the plain-shell entry. A name rather than a literal
# everywhere, because several layers (the IDE session registry, the pane split
# menu, the tests) have to agree on it.
PLAIN_TERMINAL = "shell"


@dataclass(frozen=True, slots=True)
class TrustSpec:
    """Where and how one CLI records "this folder is trusted".

    Every CLI that shows a trust dialog also writes the answer somewhere, and
    writing it ourselves BEFORE launch is the only way to skip a dialog inside a
    PTY we cannot answer programmatically. The shape of that write is the only
    thing that differs between CLIs, so it is data here rather than a branch in
    :mod:`jarvis.workspace.trust`.

    ``home_env`` is the CLI's own "move my whole config elsewhere" variable; when
    it is set in the environment the file lives under THAT directory instead of
    ``~/<subdir>``. ``both_path_forms`` exists because some CLIs key the entry on
    the cwd string exactly as they saw it, so a Windows path stored with
    backslashes does not match the same folder written with forward slashes —
    seeding both costs one extra key and removes a whole class of "the dialog
    appeared anyway".
    """

    filename: str
    fmt: Literal["json", "toml"]
    # Key path to the table of per-project entries inside the file.
    section: tuple[str, ...] = ("projects",)
    # The key/value inside a project's entry that means "trusted".
    key: str = "trust_level"
    value: Any = "trusted"
    # Keys written only when absent — never used to decide "already trusted".
    extra_defaults: tuple[tuple[str, Any], ...] = ()
    home_env: str | None = None
    # Where the file lives relative to home. "" means home itself.
    subdir: str = ""
    both_path_forms: bool = False
    # Take a one-time copy before the first mutation. Worth it for a file the
    # user's own CLI configuration shares; pointless for one we created.
    backup: bool = False


@dataclass(frozen=True, slots=True)
class AccountSpec:
    """How one CLI's entire identity moves to a different directory.

    The mechanism behind several subscriptions of the same CLI
    (:mod:`jarvis.agent_accounts`): the CLI resolves credentials, settings and
    conversation history from ONE directory, and publishes a variable that moves
    it. Deliberately a mapping and not a single variable — OpenCode needs two
    (its data root and its config root), and modelling that as one variable is
    how a "switched" account keeps spending the previous login.

    ``shared_env`` names the variables that are NOT dedicated overrides but
    general-purpose ones the CLI happens to honour (``XDG_DATA_HOME``).
    Redirecting those also redirects any other tool the agent spawns inside that
    pane, so an entry that can only be moved through a shared variable is
    offered as single-login until that blast radius has been measured.
    """

    #: ``{VAR: "{dir}"}`` — the value template gets the account directory.
    env: tuple[tuple[str, str], ...]
    #: Where the CLI looks when no override is set, e.g. ``~/.claude``.
    native_dir: str
    #: Subset of ``env`` whose variables are shared with other tools.
    shared_env: tuple[str, ...] = ()
    #: Paths inside the account directory whose presence proves a login, tried
    #: in order. EMPTY is a meaningful value: it says this build cannot read
    #: this CLI's sign-in state, and the account is then reported as exactly
    #: that. Switching still works — the variable genuinely redirects the CLI —
    #: only the green/red dot is unavailable, which is a far better answer than
    #: a confident one derived from a file layout nobody verified.
    login_markers: tuple[str, ...] = ()
    #: Arguments appended to the CLI's binary to start its own interactive
    #: sign-in, pointed at the account directory. Empty means this CLI has no
    #: login command to offer.
    login_argv: tuple[str, ...] = ()

    @property
    def dedicated(self) -> bool:
        """True when every variable that moves this CLI is its own."""
        return not self.shared_env


@dataclass(frozen=True, slots=True)
class WinShim:
    """How to reach the real executable behind a Windows ``.cmd`` shim.

    ConPTY cannot exec a batch file, so a pane whose CLI installed itself as
    ``<name>.cmd`` has to be launched some other way. ``cmd /c <shim>`` works and
    is the fallback, but it puts a second process between the pane and the agent,
    which costs signal delivery and a clean exit. When the package layout is
    known we skip the shim entirely and launch what it would have launched.

    ``kind`` says what sits at the end of the path: a Node script that needs
    ``node.exe`` in front of it (Codex), or a native executable that is simply
    run (OpenCode's Bun-compiled binary).
    """

    #: Path components relative to the directory the shim lives in.
    relative_path: tuple[str, ...]
    kind: Literal["node", "exe"] = "node"


@dataclass(frozen=True, slots=True)
class WorkspaceAgent:
    """One thing a workspace terminal can run.

    ``kind`` decides how every question about it is answered:

    * ``"cli"`` — detect with ``spec``, install with ``spec.install``, launch
      ``launch_command`` inside a shell.
    * ``"shell"`` — no detection spec and no launch command; the terminal IS the
      host's shell, so "installed" means "this host has one".

    ``needs_trust`` says whether the folder has to be pre-marked as trusted for
    this entry before a pane opens (``jarvis.workspace.trust``). A shell has no
    trust dialog to skip, and neither does a CLI that never asks.

    Everything below ``description`` is what a SECOND, THIRD or SIXTH coding CLI
    turned out to need. Each field defaults to the behaviour the two original
    entries already had, so adding them changed nothing for Claude Code and
    Codex — and each one exists because the alternative was another
    ``if agent == "<name>"`` branch in a layer that has no business knowing
    product names (AP-21).
    """

    name: str
    display_name: str
    kind: str = "cli"
    # Detection spec — CLI entries only.
    spec: CliSpec | None = None
    # The bare command run inside each terminal, for CLI entries. Trust is
    # pre-seeded by ``trust.py`` beforehand, so no permission/trust flags are
    # needed.
    launch_command: str | None = None
    needs_trust: bool = True
    # One line the UI can show under the name in a picker.
    description: str = ""

    # --- What to actually execute -------------------------------------------
    #: The executable to look up on PATH. Split from ``launch_command`` because
    #: a multi-token command ("uv tool run kimi") is not something
    #: ``shutil.which`` can find, and feeding it one used to fail as "not
    #: installed" rather than as the configuration error it is.
    binary: str = ""
    #: Arguments that always precede the per-pane ones.
    launch_args: tuple[str, ...] = ()
    win_shim: WinShim | None = None
    #: Directories to add to PATH before looking, for CLIs that install outside
    #: the usual places (Kimi's installer drops into ``~/.local/bin``).
    extra_path_dirs: tuple[str, ...] = ()
    #: Fixed environment for every pane of this entry — e.g. switching off an
    #: auto-updater that would otherwise swap the binary under a live pane.
    spawn_env: tuple[tuple[str, str], ...] = ()
    #: Resolved per spawn AND per resume, for an entry whose environment depends
    #: on user configuration (a key, an endpoint). Returning ``None`` means "not
    #: configured" and must stop the launch — never a half-set environment,
    #: which is how a pane silently talks to the wrong vendor.
    spawn_env_factory: Callable[[], dict[str, str] | None] | None = None
    #: Answers which GENERATION of the CLI is installed, when a vendor ships two
    #: under one binary name. ``None`` when there is only ever one.
    generation_probe: Callable[[], str | None] | None = None

    # --- How it behaves once it is running ----------------------------------
    trust: TrustSpec | None = None
    account: AccountSpec | None = None
    #: Which session adapter reopens this CLI's conversations. Empty means "the
    #: one named after this entry"; naming another entry's adapter is how a
    #: launch profile over an existing binary (GLM over Claude Code) inherits
    #: resume for free instead of duplicating it.
    resume_adapter: str = ""
    #: How a dropped file is written into the prompt: ``@path`` or ``"path"``.
    file_reference: Literal["at", "quoted"] = "quoted"
    #: True when a pane must show its input line before a prompt may be typed.
    #: Every CLI observed so far swallows a paste that arrives while it is still
    #: booting, so this defaults on; an entry proven not to needs it off.
    needs_input_line_wait: bool = True
    #: The prompt sigils this CLI's input line starts with. Empty means "the
    #: shared default set", which covers every TUI observed so far.
    input_markers: tuple[str, ...] = ()
    #: TUI furniture to strip before a transcript is summarised — banners,
    #: status bars, key hints. Additive to the shared set.
    chrome_fragments: tuple[str, ...] = ()
    #: The per-project instructions file this CLI reads (CLAUDE.md, AGENTS.md).
    instruction_filename: str = ""

    # --- How a person refers to it ------------------------------------------
    #: Spellings a transcript may carry for this product. Matching DATA, not
    #: prose: speech recognition writes a product name by ear, and one unmatched
    #: word costs the whole spawn group it appeared in.
    spoken_aliases: tuple[str, ...] = ()
    #: Spellings that are ordinary words on their own ("cloud", "open") and only
    #: mean the product with its second word behind them.
    spoken_aliases_needing_suffix: tuple[str, ...] = ()
    #: The word those aliases need behind them.
    alias_suffix: str = "code"

    @property
    def is_coding_agent(self) -> bool:
        """True for an entry that runs an agent rather than a bare shell."""
        return self.kind == "cli"

    @property
    def executable(self) -> str:
        """The name to resolve on PATH — explicit, else the spec's, else empty."""
        if self.binary:
            return self.binary
        return self.spec.binary_name if self.spec is not None else ""

    @property
    def adapter_key(self) -> str:
        """Which session adapter this entry resumes through."""
        return self.resume_adapter or self.name


def make_cli_agent(
    name: str,
    display_name: str,
    *,
    binary: str,
    npm_package: str = "",
    homepage: str = "",
    launch_command: str | None = None,
    description: str = "",
    install: InstallMethods | None = None,
    version_regex: str = _SEMVER_RE,
    needs_trust: bool = True,
    **extra: Any,
) -> WorkspaceAgent:
    """Build a registrable entry for an interactive CLI.

    The convenience path for plugging in a new coding agent: give it a name, a
    binary and (optionally) the npm package that installs it, and detection,
    the install command and the launch argv all follow. A CLI installed some
    other way passes ``install=`` with whatever methods it does have — or
    nothing at all, and is then detected and launched while honestly reporting
    "not installed" rather than offering a command that cannot work.

    ``extra`` forwards the behavioural fields of :class:`WorkspaceAgent`
    (``trust``, ``resume_adapter``, ``spawn_env``, ``spoken_aliases``, ...) so a
    new provider stays one call rather than a constructor plus a pile of
    ``dataclasses.replace``.
    """
    spec = CliSpec(
        name=name,
        display_name=display_name,
        description=description or f"{display_name} coding-agent CLI.",
        homepage=homepage,
        binary_name=binary,
        check_command=(binary, "--version"),
        version_parse_regex=version_regex,
        install=install or InstallMethods(
            npm_package=npm_package or None, recommended="npm"
        ),
        # We only care whether the binary is installed; the agent handles its
        # own login interactively on first launch in the terminal.
        auth=AuthConfig(type="none"),
        risk=RiskConfig(default_tier="monitor"),
        category="agent",
    )
    return WorkspaceAgent(
        name=name,
        display_name=display_name,
        kind="cli",
        spec=spec,
        launch_command=launch_command or binary,
        needs_trust=needs_trust,
        description=description,
        binary=binary,
        **extra,
    )


#: Claude Code keeps per-project trust in ``~/.claude.json``. Named rather than
#: inlined because a launch PROFILE over the same binary (the GLM entry) has to
#: seed the very same file — a second descriptor saying the same thing is a
#: second thing to keep in step.
CLAUDE_TRUST = TrustSpec(
    filename=".claude.json",
    fmt="json",
    section=("projects",),
    key="hasTrustDialogAccepted",
    value=True,
    extra_defaults=(("hasCompletedProjectOnboarding", True),),
    # Deliberately no ``home_env``. ``CLAUDE_CONFIG_DIR`` moves the ``.claude``
    # DIRECTORY, while this file sits beside it in the home directory, and the
    # per-account copies are seeded through the caller's explicit ``config_dirs``
    # instead. Naming the variable here would silently relocate the machine's
    # default trust file whenever an account happens to be active.
    home_env=None,
    subdir="",
    both_path_forms=True,
    backup=True,
)

#: Codex keeps it in ``$CODEX_HOME/config.toml`` under a per-project table.
CODEX_TRUST = TrustSpec(
    filename="config.toml",
    fmt="toml",
    section=("projects",),
    key="trust_level",
    value="trusted",
    home_env="CODEX_HOME",
    subdir=".codex",
)

#: Kimi ships under two generations that both install a binary called ``kimi``.
KIMI_CURRENT = "kimi-code"
KIMI_LEGACY = "kimi-cli"


def kimi_generation() -> str | None:
    """Which Kimi is installed: the current CLI, the wound-down one, or unknown.

    Moonshot AI replaced its Python ``kimi-cli`` with a TypeScript ``kimi-code``
    and BOTH put a binary named ``kimi`` on PATH. They do not share a data
    directory and they do not share every flag — the short forms differ, so an
    argv built for one silently means something else to the other, and
    ``kimi --version`` succeeds either way. Detecting the generation is
    therefore not polish: it is the difference between resuming a conversation
    and opening a stranger's.

    Decided on the data directory, which is the thing that actually differs and
    the one signal that costs no subprocess. ``None`` means neither has been run
    yet on this machine — a fresh install, where the honest answer is "whatever
    the installer just put there", and the caller falls back to the current
    generation's behaviour because that is what a fresh install gets.
    """
    from pathlib import Path

    home = Path.home()
    if (home / ".kimi-code").is_dir():
        return KIMI_CURRENT
    if (home / ".kimi").is_dir():
        return KIMI_LEGACY
    return None


def glm_spawn_env() -> dict[str, str] | None:
    """Environment that turns a Claude Code process into a GLM one.

    Returns ``None`` when no key is configured, which stops the launch. That is
    deliberate and is the whole safety property of this function: the CLI reads
    its endpoint ONCE at process start and never mentions which backend it got,
    so a pane launched with a half-set environment would answer perfectly well —
    from, and billed to, the wrong vendor, with nothing anywhere saying so.
    Refusing to start is recoverable; a silent wrong-vendor pane is not.

    ``ANTHROPIC_API_KEY`` is blanked on purpose. This host may well have one set
    for other reasons, it outranks the token we are passing, and the result
    would be exactly the silent fallback above. An empty value means "remove
    this variable from the child" — see :attr:`WorkspaceAgent.spawn_env`.
    """
    from jarvis.core.config import get_secret, load_config

    token = (get_secret("zai_api_key", "ZAI_API_KEY") or "").strip()
    if not token:
        return None
    try:
        settings = load_config().agentic_ide.glm
    except Exception as exc:  # noqa: BLE001 - a broken config must not brick the pane
        log.warning("GLM pane: falling back to default endpoint settings: %s", exc)
        settings = None

    def _setting(field: str, fallback: str) -> str:
        value = getattr(settings, field, "") if settings is not None else ""
        return str(value or fallback).strip()

    env = {
        "ANTHROPIC_BASE_URL": _setting("base_url", "https://api.z.ai/api/anthropic"),
        "ANTHROPIC_AUTH_TOKEN": token,
        "API_TIMEOUT_MS": _setting("request_timeout_ms", "3000000"),
        "ANTHROPIC_API_KEY": "",
    }
    # Only pin a model when the user actually chose one: vendor documentation
    # disagrees with itself about the current ids, and a wrong id fails at
    # request time in a way that reads like a bug in this app.
    for field, var in (
        ("opus_model", "ANTHROPIC_DEFAULT_OPUS_MODEL"),
        ("sonnet_model", "ANTHROPIC_DEFAULT_SONNET_MODEL"),
        ("haiku_model", "ANTHROPIC_DEFAULT_HAIKU_MODEL"),
    ):
        if chosen := _setting(field, ""):
            env[var] = chosen
    return env


_AGENTS: dict[str, WorkspaceAgent] = {
    "claude": make_cli_agent(
        "claude",
        "Claude Code",
        binary="claude",
        npm_package="@anthropic-ai/claude-code",
        homepage="https://claude.com/claude-code",
        trust=CLAUDE_TRUST,
        account=AccountSpec(
            env=(("CLAUDE_CONFIG_DIR", "{dir}"),), native_dir="~/.claude"
        ),
        # Verified live: an ``@path`` is resolved into the file's contents and
        # the file-picker popup does not eat an injected reference.
        file_reference="at",
        # The measured exception, not the rule: this CLI has a stable
        # launch-and-prompt path, so waiting for its input line to appear would
        # only slow down behaviour that already works.
        needs_input_line_wait=False,
        instruction_filename="CLAUDE.md",
        spoken_aliases=("claude", "cloude", "claud", "clode", "klaude", "kloude"),
        spoken_aliases_needing_suffix=("cloud", "clawed", "clod", "loud"),
    ),
    "codex": make_cli_agent(
        "codex",
        "Codex",
        binary="codex",
        npm_package="@openai/codex",
        homepage="https://github.com/openai/codex",
        trust=CODEX_TRUST,
        account=AccountSpec(env=(("CODEX_HOME", "{dir}"),), native_dir="~/.codex"),
        win_shim=WinShim(
            relative_path=("node_modules", "@openai", "codex", "bin", "codex.js"),
            kind="node",
        ),
        instruction_filename="AGENTS.md",
        spoken_aliases=("codex", "kodex", "codecs", "codeks"),
    ),
    "opencode": make_cli_agent(
        "opencode",
        "OpenCode",
        binary="opencode",
        homepage="https://opencode.ai",
        description="Open-source terminal coding agent — bring your own model.",
        install=InstallMethods(
            npm_package="opencode-ai",
            script_url="https://opencode.ai/install",
            # npm rather than the script: it is the one method that behaves the
            # same on all three OSes, and this app installs by running a command
            # in a terminal the user is watching.
            recommended="npm",
        ),
        # No trust dialog exists — the folder is simply opened. What gates
        # execution here is its permission system, which is a config file the
        # user owns rather than a prompt in the way.
        needs_trust=False,
        win_shim=WinShim(
            relative_path=("node_modules", "opencode-ai", "bin", "opencode.exe"),
            kind="exe",
        ),
        # Its updater is ON by default and would swap the binary underneath a
        # pane that is mid-conversation.
        spawn_env=(("OPENCODE_DISABLE_AUTOUPDATE", "1"),),
        file_reference="at",
        instruction_filename="AGENTS.md",
        spoken_aliases=("opencode",),
        # "open" is the verb half of "open a terminal", so on its own it means
        # nothing here — only "open code" is unmistakable, and even that needs
        # the guard in the intent parser that keeps a sentence from matching its
        # own opening verb.
        spoken_aliases_needing_suffix=("open",),
    ),
    "kimi": make_cli_agent(
        "kimi",
        "Kimi Code",
        binary="kimi",
        npm_package="@moonshot-ai/kimi-code",
        homepage="https://moonshotai.github.io/kimi-code/en/",
        description="Moonshot AI's terminal coding agent.",
        # Measured: the wound-down generation answers "kimi, version 1.3", which
        # the strict three-part pattern drops to None — and a blank version next
        # to an installed binary reads as a broken install.
        version_regex=_LOOSE_SEMVER_RE,
        needs_trust=False,
        # Its update preflight can check, install and PROMPT mid-session.
        spawn_env=(("KIMI_CODE_NO_AUTO_UPDATE", "1"),),
        # DELIBERATELY no AccountSpec — several seats of this CLI are not
        # offered yet, and the reason is worth writing down because the data to
        # enable it looks complete: ``KIMI_CODE_HOME`` really does move the whole
        # data root. Three things stop it being honest today.
        #
        # 1. The wound-down generation does not read that variable at all. On a
        #    machine that has it — including the one this was written on — every
        #    "separate" seat would quietly resolve to the same login, and the
        #    switcher would report a switch that did not happen.
        # 2. Its configuration and its credentials live in the SAME file, so
        #    there is no subset of setup that can be carried to a new seat
        #    without carrying the key with it.
        # 3. Its credential layout has not been verified against a live install,
        #    so a sign-in indicator here would be a guess.
        #
        # A seat switcher that silently spends one login is worse than none.
        # Tracked in docs/os-parity.md.
        # Its own installer drops the binary here, which is not on the PATH a
        # GUI-launched process inherits on macOS or Linux.
        extra_path_dirs=("~/.local/bin",),
        generation_probe=kimi_generation,
        spoken_aliases=("kimi", "kimmy", "kimmi", "kimme", "kimmie"),
    ),
    "glm": make_cli_agent(
        "glm",
        "GLM Coding Plan",
        # The whole design of this entry: the binary IS Claude Code. Z.ai ships
        # no CLI of its own — its own documentation says to run Anthropic's
        # binary against a different endpoint — so detection, the install
        # command, trust seeding and conversation resume are all INHERITED
        # rather than re-implemented against an executable that cannot exist.
        binary="claude",
        npm_package="@anthropic-ai/claude-code",
        homepage="https://docs.z.ai/devpack/tool/claude",
        description="Z.ai's GLM models, driven through the Claude Code CLI.",
        trust=CLAUDE_TRUST,
        resume_adapter="claude",
        file_reference="at",
        needs_input_line_wait=False,
        instruction_filename="CLAUDE.md",
        # Resolved fresh on every spawn AND every resume — see the docstring.
        spawn_env_factory=glm_spawn_env,
        spoken_aliases=("glm", "g l m", "gee ell em", "jlm"),
    ),
    PLAIN_TERMINAL: WorkspaceAgent(
        name=PLAIN_TERMINAL,
        display_name="Plain Terminal",
        kind="shell",
        needs_trust=False,
        description="This machine's own shell — no agent, just a prompt.",
    ),
}


def register_agent(agent: WorkspaceAgent, *, replace: bool = False) -> WorkspaceAgent:
    """Add ``agent`` to the registry and return it.

    Refuses to overwrite an existing name unless ``replace`` is set: two
    different things answering to "codex" is the kind of collision that shows up
    as a pane running the wrong tool, which is worse than an early error.
    """
    if not agent.name:
        raise ValueError("A workspace agent needs a name.")
    if agent.name in _AGENTS and not replace:
        raise ValueError(f"A workspace agent called {agent.name!r} is already registered.")
    _AGENTS[agent.name] = agent
    _forget_command_catalog()
    return agent


def _forget_command_catalog() -> None:
    """Drop the cached command catalog, which spells this registry out.

    The voice/LLM schema for "open a pane running X" lists the registered
    coding CLIs by name, and that catalog is built once and cached. An agent
    registered after the first build would therefore be openable from the UI,
    resumable, promptable — and invisible to the one surface meant to drive it.
    Never raises: the catalog is optional to this module, and a registration
    that fails because of it would be the worse outcome.
    """
    try:
        from jarvis.commands.registry import get_registry

        get_registry.cache_clear()
    except Exception:  # noqa: BLE001 - the catalog is not this module's job
        return


def list_agents() -> list[WorkspaceAgent]:
    """Every registered entry, in registration order."""
    return list(_AGENTS.values())


def get_agent(name: str) -> WorkspaceAgent | None:
    return _AGENTS.get(name)


def agent_names() -> tuple[str, ...]:
    """Every registered name, plain terminal included."""
    return tuple(_AGENTS)


def coding_agent_names() -> tuple[str, ...]:
    """Only the entries that run a coding agent — no plain shell.

    The "Make It Yours" launcher plans a grid of agents that extend Jarvis, so
    it asks this rather than :func:`agent_names`.
    """
    return tuple(name for name, agent in _AGENTS.items() if agent.is_coding_agent)


def needs_trust(name: str) -> bool:
    """Does opening this entry require pre-seeding folder trust?"""
    agent = _AGENTS.get(name)
    return bool(agent and agent.needs_trust)


# Kept as a module constant for the many call sites that read it directly. It is
# a snapshot of the CODING agents at import time, which is what every existing
# reader means by "the agents" — a plain terminal is not one, and a dynamically
# registered CLI is reachable through ``coding_agent_names()``.
AGENT_NAMES: tuple[str, ...] = coding_agent_names()


def install_command(name: str) -> str | None:
    """The shell command that installs the agent (for display + terminal run).

    Delegates to the shared installer rather than assembling an npm line here.
    Not every coding CLI is an npm package — some ship a winget id, a scoop
    package or their own install script — and hardcoding one package manager
    meant every other kind of CLI reported "no install command" while its spec
    carried a perfectly good one.

    ``None`` stays an honest answer: an entry that declares no reachable install
    method for THIS platform is shown as not installed without being offered a
    command that cannot work.
    """
    agent = _AGENTS.get(name)
    if agent is None or agent.spec is None:
        return None
    methods = agent.spec.install
    chosen = methods.recommended
    available = methods.available_methods()
    if chosen not in available:
        chosen = available[0] if available else None
    if chosen in (None, "manual"):
        return None
    from jarvis.clis.installer import CliInstaller

    argv = CliInstaller().build_command(agent.spec, chosen)  # type: ignore[arg-type]
    if not argv:
        return None
    return " ".join(argv)


def coding_agents() -> list[WorkspaceAgent]:
    """Every registered entry that runs a coding agent, in registration order."""
    return [a for a in _AGENTS.values() if a.is_coding_agent]


def generation_of(name: str) -> str | None:
    """Which generation of ``name``'s CLI this machine has, when that differs.

    ``None`` for every entry whose vendor ships exactly one thing under one
    binary name — which is all of them but Kimi.
    """
    agent = _AGENTS.get(name)
    if agent is None or agent.generation_probe is None:
        return None
    try:
        return agent.generation_probe()
    except Exception as exc:  # noqa: BLE001 - a probe must never block a launch
        log.warning("generation probe for %s failed: %s", name, exc)
        return None


def spoken_aliases() -> dict[str, str]:
    """Every spelling a transcript may carry, mapped to the entry it names.

    Includes the spellings that are ordinary words on their own — the caller
    decides what to do with those (see :func:`aliases_needing_suffix`), because
    only the caller knows whether the product's second word followed.
    """
    out: dict[str, str] = {}
    for agent in _AGENTS.values():
        for spelling in (*agent.spoken_aliases, *agent.spoken_aliases_needing_suffix):
            out.setdefault(spelling, agent.name)
    return out


def aliases_needing_suffix() -> dict[str, tuple[str, str]]:
    """Ambiguous spellings → ``(entry name, the word that must follow)``.

    "cloud" is not a request for a Claude Code pane and "open" is not a request
    for an OpenCode one; "cloud code" and "open code" are. Keeping the required
    word beside the spelling means a new entry can pick its own without every
    caller learning about it.
    """
    return {
        spelling: (agent.name, agent.alias_suffix)
        for agent in _AGENTS.values()
        for spelling in agent.spoken_aliases_needing_suffix
    }


def reserved_call_signs() -> frozenset[str]:
    """Names a pane may never carry, because a product already answers to them.

    Addressing a pane called "Kimi" by saying "Kimi" is a coin flip once a CLI
    is also called that, so the pool of call-signs excludes every registered
    product name and every spelling of one.
    """
    names: set[str] = set()
    for agent in _AGENTS.values():
        names.add(agent.name.lower())
        names.update(part.lower() for part in agent.display_name.split())
        names.update(spelling.lower() for spelling in agent.spoken_aliases)
    return frozenset(n for n in names if n.isalpha())


@dataclass(slots=True)
class AgentInfo:
    """Runtime status of one entry, returned by the /agents endpoints."""

    name: str
    display_name: str
    installed: bool
    version: str | None
    install_command: str | None
    launch_command: str
    kind: str = "cli"
    description: str = ""


async def detect_agents(prober: CliStatusProber | None = None) -> list[AgentInfo]:
    """Probe every registered entry and report what this machine can run.

    CLI entries go through the shared prober; the plain terminal answers from
    :func:`~jarvis.terminal.shells.default_shell`, because "is a shell
    installed" is not a question a ``--version`` probe can ask. Its reported
    version is the shell that would actually open ("PowerShell 7", "zsh"), which
    is the one thing a user picking it wants to know.
    """
    prober = prober or CliStatusProber()
    specs = [a.spec for a in _AGENTS.values() if a.spec is not None]
    statuses = await prober.probe_all(specs) if specs else {}
    shell = default_shell()
    out: list[AgentInfo] = []
    for agent in _AGENTS.values():
        if agent.spec is None:
            out.append(
                AgentInfo(
                    name=agent.name,
                    display_name=agent.display_name,
                    installed=shell is not None,
                    version=shell.label if shell else None,
                    install_command=None,
                    launch_command="",
                    kind=agent.kind,
                    description=agent.description,
                )
            )
            continue
        st = statuses.get(agent.name)
        out.append(
            AgentInfo(
                name=agent.name,
                display_name=agent.display_name,
                installed=bool(st and st.installed),
                version=st.version if st else None,
                install_command=install_command(agent.name),
                launch_command=agent.launch_command or "",
                kind=agent.kind,
                description=agent.description,
            )
        )
    return out


def _build_pty_argv(command: str) -> tuple[str, ...] | None:
    """Wrap a command in the platform's default shell so it runs inside a PTY
    and the shell stays open afterwards (the user can re-run / keep typing)."""
    shell = default_shell()  # pwsh>powershell>cmd / $SHELL first
    if shell is None:
        return None
    path = shell.argv[0]
    if shell.id in ("pwsh", "powershell"):
        return (path, "-NoLogo", "-NoExit", "-Command", command)
    if shell.id == "cmd":
        return (path, "/k", command)
    # POSIX: run the command, then drop to an interactive shell.
    return (path, "-c", f"{command}; exec {path}")


def plain_terminal_argv() -> tuple[str, ...] | None:
    """Full PTY argv for a plain shell session — no agent wrapped around it.

    The shell's OWN interactive argv, exactly as ``discover_shells()`` describes
    it, rather than the ``-Command``/``/k`` wrapper CLI entries need: there is no
    command to run first, and adding one would only cost the user a startup line
    they did not ask for.
    """
    shell = default_shell()
    return tuple(shell.argv) if shell is not None else None


def build_agent_argv(name: str) -> tuple[str, ...] | None:
    """Full PTY argv that opens this entry, or None when it cannot run here.

    A CLI is launched inside a shell (trust pre-seeded); the plain terminal IS
    the shell.
    """
    agent = _AGENTS.get(name)
    if agent is None:
        return None
    if agent.launch_command is None:
        return plain_terminal_argv()
    return _build_pty_argv(agent.launch_command)


def build_install_argv(name: str) -> tuple[str, ...] | None:
    """Full PTY argv that runs the agent's install command in a shell."""
    cmd = install_command(name)
    return _build_pty_argv(cmd) if cmd else None


def pty_available() -> bool:
    """True when this host can run an in-app PTY (a shell + a real PTY backend).

    Works on a headless Linux VPS too — a PTY is a kernel feature, not a GUI —
    which is why the in-app terminal grid fits the cloud-first doctrine better
    than spawning OS terminal windows."""
    if default_shell() is None:
        return False
    try:
        from jarvis.terminal.backend import make_pty_backend

        return type(make_pty_backend()).__name__ != "NullPtyBackend"
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "AGENT_NAMES",
    "CLAUDE_TRUST",
    "CODEX_TRUST",
    "KIMI_CURRENT",
    "KIMI_LEGACY",
    "PLAIN_TERMINAL",
    "AccountSpec",
    "AgentInfo",
    "TrustSpec",
    "WinShim",
    "WorkspaceAgent",
    "agent_names",
    "aliases_needing_suffix",
    "build_agent_argv",
    "build_install_argv",
    "coding_agent_names",
    "coding_agents",
    "detect_agents",
    "generation_of",
    "get_agent",
    "glm_spawn_env",
    "install_command",
    "kimi_generation",
    "list_agents",
    "make_cli_agent",
    "needs_trust",
    "plain_terminal_argv",
    "pty_available",
    "register_agent",
    "reserved_call_signs",
    "spoken_aliases",
]
