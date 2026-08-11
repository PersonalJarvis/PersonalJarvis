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

import asyncio
import logging
import time
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
    #: True when readiness requires the terminal cursor to be visible on the
    #: input-marker row. Ratatui CLIs can draw the same marker while disabling
    #: keyboard input; cursor visibility is the stable distinction.
    requires_visible_input_cursor: bool = False
    #: Maximum resumed panes of this CLI and account that may still be starting.
    #: Zero means no provider-specific limit beyond the shared machine gate.
    #: Use this when parallel starts contend on one vendor runtime store; the
    #: slot is held until the pane exposes its real input line, not merely until
    #: its process exists.
    resume_start_limit: int = 0
    #: TUI furniture to strip before a transcript is summarised — banners,
    #: status bars, key hints. Additive to the shared set.
    chrome_fragments: tuple[str, ...] = ()
    #: The hint this CLI draws while — and only while — it is busy, so a pane
    #: that stopped working can be told apart from one that never started.
    #: Additive to the shared set in :mod:`jarvis.agentic_ide.activity`, which
    #: already covers the near-universal "esc to interrupt" wording; an entry
    #: names only its own peculiarity.
    busy_fragments: tuple[str, ...] = ()
    #: The per-project instructions file this CLI reads (CLAUDE.md, AGENTS.md).
    instruction_filename: str = ""

    # --- Where the entry came from ------------------------------------------
    #: True for an entry the USER added (:mod:`jarvis.workspace.custom_clis`)
    #: rather than one this app ships. Read by the surfaces that may offer to
    #: edit or remove it, and by detection, which cannot run a stranger's
    #: ``--version`` on the event loop the wake microphone is delivered on.
    custom: bool = False
    #: Where the UI fetches this entry's mark. Empty for a built-in, whose logo
    #: is a local asset the frontend already ships.
    logo_url: str = ""
    #: Start this through a shell instead of as the pane's own process. Needed
    #: by a command that is shell SOURCE rather than an argv — a pipeline, a
    #: variable expansion, two commands chained — which cannot be exec'd.
    shell_launch: bool = False

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


#: The npm package that IS the current generation. Its presence on the resolved
#: binary path is the only positive, subprocess-free proof of which one runs.
_KIMI_CURRENT_PACKAGE = "kimi-code"


def kimi_generation() -> str | None:
    """Which Kimi is installed: the current CLI, the wound-down one, or unknown.

    Moonshot AI replaced its Python ``kimi-cli`` with a TypeScript ``kimi-code``
    and BOTH put a binary named ``kimi`` on PATH. They do not share a data
    directory and they do not share every flag — the short forms differ, so an
    argv built for one silently means something else to the other, and
    ``kimi --version`` succeeds either way. Detecting the generation is
    therefore not polish: it is the difference between resuming a conversation
    and opening a stranger's.

    **The BINARY decides, not the data directory**, and that order was learned
    the hard way. Deciding on the data directory looks cheaper and is wrong for
    the single most likely state a machine is in: someone upgrades, so the new
    binary is on PATH — but its data root does not exist until the first run,
    while the old generation's root is still lying there from before. A
    directory-first probe reports the WOUND-DOWN generation on a machine that
    can no longer run it, and every pane then reads the wrong history and offers
    to resume conversations belonging to a CLI that is gone. Measured on the
    maintainer's own box the moment the upgrade landed.

    So: the resolved binary path first (an npm install carries the package name
    in it, which is positive proof and costs no subprocess), then the data roots
    as a fallback for an install laid out some other way. ``None`` means nothing
    on this machine says either way, and the caller reads no history at all
    rather than guessing at one.
    """
    import shutil
    from pathlib import Path

    try:
        from jarvis.core.path_augment import ensure_cli_paths

        ensure_cli_paths()
    except Exception:  # noqa: BLE001, S110 - PATH augmentation is best-effort
        pass
    binary = shutil.which("kimi")
    if binary:
        try:
            resolved = Path(binary).resolve()
        except OSError:
            resolved = Path(binary)
        # The shim itself is just "kimi", so the package name shows up either in
        # the resolved path (a POSIX symlink into the package) or in a sibling
        # node_modules tree (the Windows .cmd shim, which stays where it is).
        parts = {part.lower() for part in resolved.parts}
        if _KIMI_CURRENT_PACKAGE in parts:
            return KIMI_CURRENT
        package = (
            resolved.parent / "node_modules" / "@moonshot-ai" / _KIMI_CURRENT_PACKAGE
        )
        if package.is_dir():
            return KIMI_CURRENT

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
        # Its footer row ("<folder> 🌿 <branch> <model> (1M context)") carries
        # plenty of letters, so without these the deterministic recap of a busy
        # pane quoted the footer as its "activity". The branch glyph is the
        # stable marker; the tip prefix covers the rotating "Tip: Use /btw …"
        # hint rows. The key-hint half of the footer already matches the shared
        # "shift+tab" fragment.
        chrome_fragments=("🌿", "tip: use"),
        spoken_aliases=("claude", "cloude", "claud", "clode", "klaude", "kloude"),
        spoken_aliases_needing_suffix=(
            "cloud",
            "clawed",
            "clod",
            "loud",
            "cloth",
            "glow",
        ),
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
        input_markers=("›", "»"),
        requires_visible_input_cursor=True,
        # Two simultaneous resumes against Codex's shared SQLite/runtime store
        # were measured beyond 90 s while one resume initialized substantially
        # faster. Serialize only this shared-store boot phase. Claude and every
        # unrelated CLI keep the normal parallel path.
        resume_start_limit=1,
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
        # The npm install puts a .cmd shim on PATH that runs node against the
        # package's own entrypoint; going straight there keeps the pane's own
        # process the agent rather than a cmd wrapper around it.
        win_shim=WinShim(
            relative_path=(
                "node_modules", "@moonshot-ai", "kimi-code", "dist", "main.mjs",
            ),
            kind="node",
        ),
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
        # A shell draws no TUI input line, so waiting for one would wait
        # forever. It is never prompted programmatically anyway (see
        # ``accepts_prompts``), but "ready" is asked about it, and the honest
        # answer is that a live shell is ready the moment it exists.
        needs_input_line_wait=False,
    ),
}


#: The entries the USER added, rebuilt from their store whenever it changes.
#:
#: Kept apart from ``_AGENTS`` rather than merged into it, because the two are
#: owned by different people: ``_AGENTS`` holds what this app ships plus
#: whatever a plugin registered in code, and re-reading a FILE must never be
#: able to drop one of those on the floor.
_CUSTOM_AGENTS: dict[str, WorkspaceAgent] = {}
#: Store revision the dict above was built from; ``-1`` means "never built".
_custom_revision: int = -1


def builtin_names() -> tuple[str, ...]:
    """Every registry key this app ships or a plugin registered in code.

    Read by the custom-CLI store, which has to know which names are already
    spoken for before it hands one to a new entry.
    """
    return tuple(_AGENTS)


def _custom_aliases(display_name: str, entry_id: str) -> tuple[str, ...]:
    """Spellings a transcript may carry for a user-added entry.

    Deliberately conservative, because these spellings are matched against
    everything the user says. The full name counts (a whole phrase matching by
    accident is unlikely), and a SINGLE-word name counts only from four letters
    up — an entry someone called "Test" or "Go" would otherwise turn every
    sentence containing that ordinary word into a request to open a terminal.
    Anything richer is the built-ins' business: they list their misspellings by
    hand because someone checked how speech recognition writes them.
    """
    out: list[str] = []
    name = " ".join(str(display_name or "").lower().split())
    words = name.split()
    if len(words) > 1:
        out.append(name)
    elif words and len(words[0]) >= 4 and words[0].isalpha():
        out.append(words[0])
    slug = str(entry_id or "").lower()
    if len(slug) >= 4 and slug.isalpha() and slug not in out:
        out.append(slug)
    return tuple(out)


def _custom_agent(entry: Any) -> WorkspaceAgent:
    """Turn one stored :class:`~jarvis.workspace.custom_clis.CustomCli` into an
    entry the rest of this module cannot tell apart from a built-in.

    Deliberately ``spec=None``. A spec is a DETECTION plan — "run this with
    ``--version`` and parse the answer" — and we have no promise a stranger's
    CLI answers that flag rather than opening its TUI and holding the event
    loop the wake microphone runs on. Detection for these entries is a PATH
    lookup instead (see :func:`_sweep_agents`), which costs no subprocess and
    reports no version rather than a guessed one.
    """
    from jarvis.workspace import custom_clis

    through_shell = custom_clis.needs_shell(entry.command)
    return WorkspaceAgent(
        name=entry.id,
        display_name=entry.display_name,
        kind="cli",
        spoken_aliases=_custom_aliases(entry.display_name, entry.id),
        spec=None,
        launch_command=entry.command,
        # Nothing is known about this CLI's trust file, so there is nothing to
        # pre-seed. Its own dialog, if it has one, is answered by the user in
        # the pane — which is exactly what happens today outside this app.
        needs_trust=False,
        description=entry.description,
        binary=entry.binary,
        # Empty for a shell launch: the whole line goes to the shell verbatim,
        # so a second, half-parsed copy of it here would only be something a
        # future caller could pick up and get wrong.
        launch_args=()
        if through_shell
        else custom_clis.split_command(entry.command)[1:],
        file_reference=entry.file_reference,
        custom=True,
        logo_url=custom_clis.logo_url(entry),
        shell_launch=through_shell,
    )


def _sync_custom_agents() -> None:
    """Rebuild the user's entries when their store has changed.

    Called from every read below rather than once at import, because the store
    is edited while the app runs: a CLI added in the wizard has to be openable
    from the pane menu in the same session, without a restart.
    """
    global _custom_revision
    from jarvis.workspace import custom_clis

    revision = custom_clis.revision()
    if revision == _custom_revision:
        return
    try:
        entries = custom_clis.list_custom_clis()
    except Exception as exc:  # noqa: BLE001 - a broken store must not hide the built-ins
        log.warning("workspace agents: custom entries unavailable: %s", exc)
        entries = []
    _custom_revision = revision
    _CUSTOM_AGENTS.clear()
    for entry in entries:
        if entry.id in _AGENTS:
            # A name a built-in already answers to. The store refuses to create
            # one, so this is a hand-edited file; the shipped entry wins,
            # because a pane running the wrong tool is the worse failure.
            log.warning(
                "workspace agents: custom entry %r shadows a built-in; ignoring it",
                entry.id,
            )
            continue
        _CUSTOM_AGENTS[entry.id] = _custom_agent(entry)
    _forget_command_catalog()


def refresh_custom_agents() -> None:
    """Re-read the user's entries now, and forget the last detection sweep.

    For the routes that just changed the store: the next question about "what
    can this machine run" has to see the new entry, and the sweep's answer for
    it (installed or not) has not been asked yet.
    """
    global _custom_revision
    _custom_revision = -1
    _sync_custom_agents()
    invalidate_agent_detection()


def _registry() -> dict[str, WorkspaceAgent]:
    """Every entry, built-ins first, in the order the UI should list them.

    The plain terminal stays LAST even though it is registered among the
    built-ins: it is the "no agent at all" choice, and a menu that buries it
    between coding CLIs reads as though it were one of them.
    """
    _sync_custom_agents()
    merged: dict[str, WorkspaceAgent] = {}
    shell = _AGENTS.get(PLAIN_TERMINAL)
    for name, agent in _AGENTS.items():
        if name != PLAIN_TERMINAL:
            merged[name] = agent
    merged.update(_CUSTOM_AGENTS)
    if shell is not None:
        merged[PLAIN_TERMINAL] = shell
    return merged


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
    return list(_registry().values())


def get_agent(name: str) -> WorkspaceAgent | None:
    return _registry().get(name)


def agent_names() -> tuple[str, ...]:
    """Every registered name, plain terminal included."""
    return tuple(_registry())


def coding_agent_names() -> tuple[str, ...]:
    """Only the entries that run a coding agent — no plain shell.

    The "Make It Yours" launcher plans a grid of agents that extend Jarvis, so
    it asks this rather than :func:`agent_names`.
    """
    return tuple(
        name for name, agent in _registry().items() if agent.is_coding_agent
    )


def needs_trust(name: str) -> bool:
    """Does opening this entry require pre-seeding folder trust?"""
    agent = get_agent(name)
    return bool(agent and agent.needs_trust)


# Kept for the call sites that import this name directly. It is a snapshot of
# the SHIPPED coding agents at import time and cannot see an entry registered
# afterwards — neither one a plugin added nor one the user typed into the
# wizard. Anything deciding whether a name is runnable must call
# ``coding_agent_names()``, which asks the live registry; a membership test
# against this tuple rejects the user's own CLI with "unknown agent".
AGENT_NAMES: tuple[str, ...] = tuple(
    name for name, agent in _AGENTS.items() if agent.is_coding_agent
)


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
    agent = get_agent(name)
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
    return [a for a in _registry().values() if a.is_coding_agent]


def generation_of(name: str) -> str | None:
    """Which generation of ``name``'s CLI this machine has, when that differs.

    ``None`` for every entry whose vendor ships exactly one thing under one
    binary name — which is all of them but Kimi.
    """
    agent = get_agent(name)
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
    for agent in _registry().values():
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
        for agent in _registry().values()
        for spelling in agent.spoken_aliases_needing_suffix
    }


def reserved_call_signs() -> frozenset[str]:
    """Names a pane may never carry, because a product already answers to them.

    Addressing a pane called "Kimi" by saying "Kimi" is a coin flip once a CLI
    is also called that, so the pool of call-signs excludes every registered
    product name and every spelling of one.

    Deliberately NARROW. The obvious implementation — every word of every
    display name — reserves "Coding" and "Plan" out of "GLM Coding Plan", and
    since call-sign matching is fuzzy by design that in turn poisons unrelated
    names ("coding" scores high enough to reach the call-sign "Cody"). A list
    meant to prevent one collision would then be creating others, so only the
    identifying token counts: the registry key, the first word of the display
    name, and the spellings people actually say.
    """
    names: set[str] = set()
    for agent in _registry().values():
        if not agent.is_coding_agent:
            continue
        names.add(agent.name.lower())
        first = agent.display_name.split()
        if first:
            names.add(first[0].lower())
        names.update(spelling.lower() for spelling in agent.spoken_aliases)
    # Single letters and anything with a space in it are not call-sign shaped,
    # so reserving them protects nothing and only shrinks the pool.
    return frozenset(n for n in names if n.isalpha() and len(n) > 1)


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
    #: True for an entry the user added, so a surface can offer to edit it.
    custom: bool = False
    #: Where to fetch this entry's mark; empty means the UI's own asset or the
    #: monogram it falls back to.
    logo_url: str = ""


#: How long a completed detection sweep answers for.
#:
#: A sweep is not a lookup — it starts ONE subprocess per registered CLI, and on
#: Windows every one of those is an npm shim, so `claude --version` is really
#: cmd.exe -> conhost -> node. Measured on the maintainer's machine: 1.1-1.4 s
#: wall for five CLIs, and while it runs the shared event loop stalls for up to
#: 1.7 s (probed with /api/health, which is answered on that same loop).
#:
#: That loop is where the wake microphone is delivered, three hops per 100 ms
#: block with two seconds of buffer behind it, so an uncached sweep is heard as
#: "the wake word takes longer in the Agentic IDE" — the view re-reads its whole
#: state on every workspace change, and each of those reads paid for a fresh
#: sweep. Nothing a sweep answers changes on that timescale: a CLI is installed
#: or it is not.
_DETECTION_TTL_S = 30.0

#: Last completed sweep: (monotonic timestamp, result).
_detection_cache: tuple[float, list[AgentInfo]] | None = None
#: The sweep currently running, so N callers arriving together (a grid of panes
#: all asking at once) share ONE burst of subprocesses instead of N.
_detection_task: asyncio.Task[list[AgentInfo]] | None = None


def invalidate_agent_detection() -> None:
    """Forget the cached sweep — the next read probes the machine again.

    For callers that KNOW the answer just changed (a CLI was installed or
    removed from inside the app). A user watching an install does not want to
    wait out the TTL to see it appear.
    """
    global _detection_cache
    _detection_cache = None


async def detect_agents(
    prober: CliStatusProber | None = None, *, force: bool = False
) -> list[AgentInfo]:
    """Probe every registered entry and report what this machine can run.

    CLI entries go through the shared prober; the plain terminal answers from
    :func:`~jarvis.terminal.shells.default_shell`, because "is a shell
    installed" is not a question a ``--version`` probe can ask. Its reported
    version is the shell that would actually open ("PowerShell 7", "zsh"), which
    is the one thing a user picking it wants to know.

    Answered from a short-lived cache (``_DETECTION_TTL_S``) unless ``force`` is
    set, because the sweep is expensive enough to be felt elsewhere in the app —
    see the constant. An explicit ``prober`` (tests, and any caller supplying
    its own probing strategy) always runs a real sweep and never touches the
    cache: a caller that hands over the prober is asking for THAT prober's
    answer.
    """
    if prober is not None:
        return await _sweep_agents(prober)

    global _detection_cache, _detection_task
    cached = _detection_cache
    if (
        not force
        and cached is not None
        and time.monotonic() - cached[0] < _DETECTION_TTL_S
    ):
        return list(cached[1])

    running = _detection_task
    if running is not None and not running.done():
        try:
            same_loop = running.get_loop() is asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - no loop is not reachable here
            same_loop = False
        if same_loop:
            # Shielded: a browser that navigates away mid-sweep must not cancel
            # the sweep the other five panes are waiting on.
            return list(await asyncio.shield(running))

    task = asyncio.ensure_future(_sweep_agents(CliStatusProber()))
    _detection_task = task
    try:
        infos = await asyncio.shield(task)
    finally:
        if _detection_task is task:
            _detection_task = None
    _detection_cache = (time.monotonic(), infos)
    return list(infos)


def _on_path(binary: str) -> bool:
    """Is ``binary`` resolvable the way a pane would resolve it?

    The whole detection story for a user-added entry. No subprocess: see
    :func:`_custom_agent` for why a stranger's ``--version`` is not something
    this sweep may run. ``ensure_cli_paths`` first, because a GUI-launched
    process starts with a minimal PATH and would otherwise report a correctly
    installed CLI as missing on macOS and Linux.
    """
    import shutil

    if not binary:
        return False
    try:
        from jarvis.core.path_augment import ensure_cli_paths

        ensure_cli_paths()
    except Exception:  # noqa: BLE001, S110 - PATH augmentation is best-effort
        pass
    return shutil.which(binary) is not None


async def _sweep_agents(prober: CliStatusProber) -> list[AgentInfo]:
    """One real detection pass — a subprocess per SHIPPED CLI.

    Three kinds of answer, and what an entry gets is decided by what it
    declares rather than by its name:

    * a detection spec — the shared prober runs it and reports a version;
    * a user-added CLI (no spec, still a coding agent) — its first word is
      looked up on PATH, off the event loop because that touches the disk;
    * the plain terminal — "does this host have a shell", which is not a
      question a ``--version`` probe can ask.
    """
    registry = _registry()
    specs = [a.spec for a in registry.values() if a.spec is not None]
    statuses = await prober.probe_all(specs) if specs else {}
    shell = default_shell()
    unspecced = [a for a in registry.values() if a.spec is None and a.is_coding_agent]
    found: dict[str, bool] = {}
    if unspecced:
        found = await asyncio.to_thread(
            lambda: {a.name: _on_path(a.executable) for a in unspecced}
        )

    out: list[AgentInfo] = []
    for agent in registry.values():
        if agent.spec is None and agent.is_coding_agent:
            out.append(
                AgentInfo(
                    name=agent.name,
                    display_name=agent.display_name,
                    installed=found.get(agent.name, False),
                    # Deliberately none. Asking would be the subprocess this
                    # path exists to avoid, and a made-up number beside
                    # someone's own command is worse than a blank one.
                    version=None,
                    install_command=None,
                    launch_command=agent.launch_command or "",
                    kind=agent.kind,
                    description=agent.description,
                    custom=agent.custom,
                    logo_url=agent.logo_url,
                )
            )
            continue
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


def shell_run_argv(command: str) -> tuple[str, ...] | None:
    """Shell argv that runs ``command`` and EXITS with it.

    The counterpart to :func:`_build_pty_argv`, which keeps the shell open
    afterwards. Both exist because the two callers want opposite things: the
    workspace launcher opens a terminal the user goes on typing in, while an
    Agentic-IDE pane IS its agent — when the agent exits the pane is finished,
    and a shell surviving it would leave a prompt that looks like a running
    agent to every readiness check in the app.

    Used for a user-added CLI whose command is shell source rather than an argv
    (a pipeline, ``VAR=x cmd``, two commands chained), which cannot be exec'd
    directly. ``None`` on a host with no shell, which reads the same as a
    missing binary.
    """
    shell = default_shell()
    if shell is None:
        return None
    path = shell.argv[0]
    if shell.id in ("pwsh", "powershell"):
        return (path, "-NoLogo", "-NoProfile", "-Command", command)
    if shell.id == "cmd":
        # /c, never /k — see the docstring: the shell has to die with the agent.
        return (path, "/c", command)
    return (path, "-c", command)


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
    agent = get_agent(name)
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
    "builtin_names",
    "coding_agent_names",
    "coding_agents",
    "detect_agents",
    "generation_of",
    "get_agent",
    "glm_spawn_env",
    "install_command",
    "invalidate_agent_detection",
    "kimi_generation",
    "list_agents",
    "make_cli_agent",
    "needs_trust",
    "plain_terminal_argv",
    "pty_available",
    "refresh_custom_agents",
    "register_agent",
    "reserved_call_signs",
    "shell_run_argv",
    "spoken_aliases",
]
