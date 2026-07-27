"""Several subscriptions per coding CLI, switchable without logging out.

A user can hold more than one seat for the same CLI — two Claude Max plans, two
ChatGPT/Codex plans. Out of the box only one of them is reachable: the CLI keeps
exactly one login, so getting to the other means a full ``logout`` / ``login``
round trip that throws the first one away. Holding two plans and being able to
use only one at a time is the problem this module exists to remove.

**One account is one directory.** Both CLIs resolve their whole identity —
credentials, account file, conversation history — from a single directory, and
both publish the environment variable that moves it:

===========  ======================  ==============
CLI          Variable                Platform default
===========  ======================  ==============
Claude Code  ``CLAUDE_CONFIG_DIR``   ``~/.claude``
Codex        ``CODEX_HOME``          ``~/.codex``
===========  ======================  ==============

So switching is not an auth operation at all: it decides which directory the
NEXT spawn points at. Every login stays valid, side by side, indefinitely — and
because the choice is per spawn, two panes can run two different subscriptions
at the same moment, which is the actual reason to hold two.

**Tokens are never copied between accounts.** That is the obvious shortcut and
it is a trap: Claude Code refreshes its OAuth access token *in place*, so a copy
starts dying the moment it is made — the 2026-07-06 and 2026-07-10 incidents
behind :mod:`jarvis.claude_credentials` are exactly that failure, one directory
further left each time. Separate directories each refresh themselves and cannot
go stale relative to one another.

**The built-in account is synthetic.** Every platform always offers the CLI's
own default directory as an account that was never created here, cannot be
deleted, and is never written to the store. A user who never opens this feature
therefore has exactly the behaviour they had before, and an empty store is a
complete, valid state rather than a broken one.

Storage follows the pattern ``recents.py`` and ``resume_store.py`` already
proved in this codebase: one small JSON file under the per-user data directory
(never the repo, never ``jarvis.toml``, never a secret in it — only labels and
paths), atomic writes, and defensive reads where a truncated, hand-edited or
newer-version file degrades to "only the built-in account exists" instead of
breaking the view that reads it.

Cross-platform by construction: ``pathlib`` throughout, no hardcoded user paths,
and no OS-specific branch. The one honest caveat is macOS, where Claude Code
keeps credentials in the Keychain rather than in the config directory — see
:func:`describe`, which reports an account with no readable login as exactly
that instead of quietly routing panes to whichever login the CLI finds.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from loguru import logger

Platform = Literal["claude", "codex"]

#: The platforms that own a switchable subscription login. Anything else — an
#: API-key provider — has no directory to switch and is deliberately absent.
PLATFORMS: tuple[Platform, ...] = ("claude", "codex")

#: The official config-dir override each CLI publishes. This mapping IS the
#: mechanism; everything else in this module is bookkeeping around it.
ENV_VAR: dict[Platform, str] = {
    "claude": "CLAUDE_CONFIG_DIR",
    "codex": "CODEX_HOME",
}

#: Where each CLI looks when its override is unset.
_NATIVE_DIR: dict[Platform, str] = {"claude": "~/.claude", "codex": "~/.codex"}

_DISPLAY: dict[Platform, str] = {"claude": "Claude Code", "codex": "Codex"}

#: Bumped when the stored shape changes incompatibly. An unknown version reads
#: as "no added accounts": half-understanding a newer build's file would offer
#: accounts whose directories this build cannot interpret, and a pane would then
#: launch against the wrong login — worse than offering only the built-in one.
SCHEMA_VERSION = 1

#: A label the user can actually read back in a pane header. Long enough for
#: "Work account (Max 20x)", short enough not to break the switcher's layout.
MAX_LABEL_CHARS = 60

#: Runaway guard, not a product ceiling — nobody holds this many seats, but a
#: scripted loop against the REST route must not fill the disk with config dirs.
MAX_ACCOUNTS_PER_PLATFORM = 20

# Writes arrive from the REST routes and from pane creation, and the last one has
# to be the one that lands rather than the one that finished renaming first.
_WRITE_LOCK = threading.Lock()


class AccountError(RuntimeError):
    """A requested account operation cannot be carried out (surfaced as 4xx)."""


@dataclass(frozen=True, slots=True)
class AgentAccount:
    """One subscription of one coding CLI, identified by its config directory."""

    id: str
    platform: Platform
    label: str
    config_dir: Path
    builtin: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "platform": self.platform,
            "label": self.label,
            "config_dir": str(self.config_dir),
            "builtin": self.builtin,
        }


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Display-safe state of one account. Never carries a token."""

    account: AgentAccount
    connected: bool
    mode: str  # "subscription" | "api_key" | "expired" | "unknown"
    message: str
    email: str | None = None
    tier: str | None = None
    #: Set when this account is signed in as the SAME identity as another one.
    #: Two rows, one subscription — see :func:`snapshots`.
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.account.to_dict(),
            "connected": self.connected,
            "mode": self.mode,
            "message": self.message,
            "email": self.email,
            "tier": self.tier,
            "warning": self.warning,
        }


# ---------------------------------------------------------------- storage


def _store_path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agent_accounts.json"


def _accounts_root() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agent-accounts"


def _native_dir(platform: Platform) -> Path:
    """Where the CLI reads its login from when this module does nothing.

    That includes an override the app was STARTED with. A host running under a
    profile manager already has one set, and the login it points at genuinely is
    that session's default — reporting ``~/.claude`` there (and clearing the
    variable at spawn) would move panes onto a different, probably stale login
    than the one everything else on the machine uses.
    """
    inherited = os.environ.get(ENV_VAR[platform], "").strip()
    if inherited:
        return Path(inherited).expanduser()
    return Path(os.path.expanduser(_NATIVE_DIR[platform]))


def builtin_id(platform: Platform) -> str:
    """The stable id of a platform's synthetic default account."""
    return f"{platform}:default"


def _builtin(platform: Platform) -> AgentAccount:
    return AgentAccount(
        id=builtin_id(platform),
        platform=platform,
        label=f"Default {_DISPLAY[platform]} login",
        config_dir=_native_dir(platform),
        builtin=True,
    )


def _read_store() -> dict[str, Any]:
    """The stored state, or an empty one for every unreadable shape."""
    try:
        raw = _store_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"accounts": [], "active": {}}
    except OSError as exc:
        logger.warning("Agent accounts: store could not be read ({}) — ignoring it", exc)
        return {"accounts": [], "active": {}}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        logger.warning("Agent accounts: store is not valid JSON ({}) — ignoring it", exc)
        return {"accounts": [], "active": {}}
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        return {"accounts": [], "active": {}}
    accounts = data.get("accounts")
    active = data.get("active")
    return {
        "accounts": accounts if isinstance(accounts, list) else [],
        "active": active if isinstance(active, dict) else {},
    }


def _write_store(state: Mapping[str, Any]) -> None:
    """Atomic write — a half-written store must never be a readable one."""
    path = _store_path()
    payload = json.dumps(
        {
            "version": SCHEMA_VERSION,
            "accounts": list(state.get("accounts", [])),
            "active": dict(state.get("active", {})),
        },
        indent=2,
        ensure_ascii=False,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{uuid4().hex[:8]}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("Agent accounts: store could not be written: {}", exc)


def _parse_account(raw: Any) -> AgentAccount | None:
    """One stored entry, or ``None`` for anything that is not a usable account."""
    if not isinstance(raw, dict):
        return None
    account_id = raw.get("id")
    platform = raw.get("platform")
    config_dir = raw.get("config_dir")
    if not (isinstance(account_id, str) and account_id):
        return None
    if platform not in PLATFORMS:
        return None
    if not (isinstance(config_dir, str) and config_dir):
        return None
    label = raw.get("label")
    label = label if isinstance(label, str) and label.strip() else account_id
    try:
        directory = Path(config_dir).expanduser()
    except (OSError, ValueError):
        return None
    return AgentAccount(
        id=account_id,
        platform=platform,  # type: ignore[arg-type]  # narrowed by the check above
        label=label.strip()[:MAX_LABEL_CHARS],
        config_dir=directory,
        builtin=False,
    )


# ------------------------------------------------------------------ query


def list_accounts(platform: Platform) -> list[AgentAccount]:
    """Every account of *platform*, the built-in one first.

    The order is what the switcher renders, and it is deliberately stable: the
    default login stays at the top so the familiar entry never moves under the
    cursor as accounts are added.
    """
    stored = _read_store()["accounts"]
    added = [
        account
        for account in (_parse_account(entry) for entry in stored)
        if account is not None and account.platform == platform
    ]
    return [_builtin(platform), *added]


def all_accounts() -> list[AgentAccount]:
    """Every account of every platform, grouped by platform."""
    return [account for platform in PLATFORMS for account in list_accounts(platform)]


def resolve(account_id: str | None) -> AgentAccount | None:
    """An id back to its account; ``None`` when the id is unknown or empty."""
    if not account_id:
        return None
    for platform in PLATFORMS:
        if account_id == builtin_id(platform):
            return _builtin(platform)
    for account in all_accounts():
        if account.id == account_id:
            return account
    return None


def active_account(platform: Platform) -> AgentAccount:
    """The account new panes of *platform* use unless told otherwise.

    Falls back to the built-in account whenever the pinned id no longer resolves
    — a deleted or hand-edited account must degrade to the login that always
    exists, never to "no account" (which has no directory to spawn against).
    """
    pinned = _read_store()["active"].get(platform)
    if isinstance(pinned, str):
        account = resolve(pinned)
        if account is not None and account.platform == platform:
            return account
    return _builtin(platform)


def active_ids() -> dict[str, str]:
    """``{platform: active account id}`` for every platform."""
    return {platform: active_account(platform).id for platform in PLATFORMS}


# ----------------------------------------------------------------- mutate


def _slug(label: str) -> str:
    """A filesystem-safe directory stem derived from a human label."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    return cleaned[:24] or "account"


def create_account(platform: Platform, label: str) -> AgentAccount:
    """Register a new account and mint its (empty) config directory.

    The directory is created but NOT signed in — that is a separate, deliberate
    step, because a sign-in opens a browser and must never be a side effect of
    adding a row to a list. Until then the account reports itself as not signed
    in, which is exactly true.
    """
    if platform not in PLATFORMS:
        raise AccountError(f"Unknown platform: {platform}")
    clean_label = (label or "").strip()[:MAX_LABEL_CHARS]
    if not clean_label:
        raise AccountError("An account needs a name.")
    existing = [a for a in list_accounts(platform) if not a.builtin]
    if len(existing) >= MAX_ACCOUNTS_PER_PLATFORM:
        raise AccountError(
            f"{_DISPLAY[platform]} already has the maximum of "
            f"{MAX_ACCOUNTS_PER_PLATFORM} extra accounts."
        )
    account_id = f"{platform}:{uuid4().hex[:12]}"
    directory = _accounts_root() / platform / f"{_slug(clean_label)}-{account_id[-6:]}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AccountError(f"The account folder could not be created: {exc}") from exc
    account = AgentAccount(
        id=account_id,
        platform=platform,
        label=clean_label,
        config_dir=directory,
        builtin=False,
    )
    with _WRITE_LOCK:
        state = _read_store()
        state["accounts"] = [*state["accounts"], account.to_dict()]
        _write_store(state)
    logger.info("Agent accounts: added {} account {!r}", platform, clean_label)
    return account


def rename_account(account_id: str, label: str) -> AgentAccount:
    """Give an added account a new display name. The built-in one is fixed."""
    account = resolve(account_id)
    if account is None:
        raise AccountError("That account no longer exists.")
    if account.builtin:
        raise AccountError("The default login cannot be renamed.")
    clean_label = (label or "").strip()[:MAX_LABEL_CHARS]
    if not clean_label:
        raise AccountError("An account needs a name.")
    with _WRITE_LOCK:
        state = _read_store()
        entries = []
        for entry in state["accounts"]:
            if isinstance(entry, dict) and entry.get("id") == account_id:
                entries.append({**entry, "label": clean_label})
            else:
                entries.append(entry)
        state["accounts"] = entries
        _write_store(state)
    return AgentAccount(
        id=account.id,
        platform=account.platform,
        label=clean_label,
        config_dir=account.config_dir,
        builtin=False,
    )


def set_active(platform: Platform, account_id: str) -> AgentAccount:
    """Point new panes of *platform* at *account_id*. This is the switch.

    Nothing that is already running changes — a pane carries the account it was
    created with, so flipping this cannot re-point an agent mid-conversation.
    """
    account = resolve(account_id)
    if account is None or account.platform != platform:
        raise AccountError("That account no longer exists.")
    with _WRITE_LOCK:
        state = _read_store()
        state["active"] = {**state["active"], platform: account_id}
        _write_store(state)
    logger.info("Agent accounts: {} now defaults to {!r}", platform, account.label)
    return account


def delete_account(account_id: str, *, remove_files: bool = False) -> AgentAccount:
    """Forget an added account, optionally erasing its directory.

    ``remove_files=False`` is the default on purpose: forgetting is reversible
    (re-add and point at the same folder), erasing a login is not.
    """
    account = resolve(account_id)
    if account is None:
        raise AccountError("That account no longer exists.")
    if account.builtin:
        raise AccountError("The default login cannot be removed.")
    with _WRITE_LOCK:
        state = _read_store()
        state["accounts"] = [
            entry
            for entry in state["accounts"]
            if not (isinstance(entry, dict) and entry.get("id") == account_id)
        ]
        state["active"] = {
            platform: value
            for platform, value in state["active"].items()
            if value != account_id
        }
        _write_store(state)
    if remove_files:
        try:
            shutil.rmtree(account.config_dir)
        except OSError as exc:
            logger.warning(
                "Agent accounts: {} was forgotten but its folder stayed: {}",
                account.label,
                exc,
            )
    logger.info("Agent accounts: removed {!r}", account.label)
    return account


# ------------------------------------------------------------------ spawn


def config_dir_for(platform: Platform, account_id: str | None) -> Path:
    """The directory a spawn for *account_id* must read its login from.

    An unknown id resolves to the platform default rather than raising: a stale
    id in a resumed workspace must reopen the pane on the login that always
    exists, not kill it.
    """
    account = resolve(account_id)
    if account is None or account.platform != platform:
        return _native_dir(platform)
    return account.config_dir


def env_overrides(platform: Platform, account_id: str | None) -> dict[str, str]:
    """The environment delta for a spawn — EMPTY for the built-in account.

    Empty, not "set it to the default path", and the difference is load-bearing
    twice over. Pinning ``CLAUDE_CONFIG_DIR=~/.claude`` is not the same as
    leaving it unset: unset lets the CLI use its true platform default, which on
    macOS is the Keychain rather than a directory, so an explicit path would send
    it looking for a file that platform never writes. And on a host that was
    started WITH an override, touching the variable at all would move the default
    account off the login the rest of the machine is using.

    In one sentence: the built-in account means "spawn exactly as this app would
    have spawned before this feature existed".
    """
    account = resolve(account_id)
    if account is None or account.platform != platform or account.builtin:
        return {}
    return {ENV_VAR[platform]: str(account.config_dir)}


def spawn_env(
    platform: Platform,
    account_id: str | None,
    *,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The full child environment for a spawn on *account_id*.

    Inherits (``os.environ`` unless *base* is given) and then applies the
    override. Inheriting is not a convenience — the PTY backends REPLACE the
    child environment with whatever they are handed, so a bare ``{VAR: dir}``
    would strip ``PATH`` and the agent binary would stop resolving.
    """
    env = dict(os.environ if base is None else base)
    env.update(env_overrides(platform, account_id))
    return env


# ------------------------------------------------------------------ status


def describe(account: AgentAccount) -> AccountSnapshot:
    """Display-safe state of one account, read straight from its directory.

    Deliberately asks a narrower question than the app's connection cards do:
    not "is this machine signed in" but "is THIS account signed in". The
    cross-directory search that powers the cards would report a neighbouring
    account's healthy login here, and the switcher would show a green row for an
    account that cannot run a single pane.

    Never raises, never returns a token, and never spawns the CLI — a handful of
    small file reads, so the switcher can refresh freely.
    """
    if account.platform == "claude":
        return _describe_claude(account)
    return _describe_codex(account)


def _describe_claude(account: AgentAccount) -> AccountSnapshot:
    from jarvis.claude_auth import claude_account_identity, subscription_label
    from jarvis.claude_credentials import claude_login_in

    snapshot = claude_login_in(account.config_dir)
    email, _name = claude_account_identity(account.config_dir)
    tier = snapshot.subscription_type
    if snapshot.status == "valid":
        label = subscription_label(tier)
        return AccountSnapshot(
            account=account,
            connected=True,
            mode="subscription",
            message=f"Signed in via {label} ({email})." if email else f"Signed in via {label}.",
            email=email,
            tier=tier,
        )
    if snapshot.status == "expired":
        return AccountSnapshot(
            account=account,
            connected=False,
            mode="expired",
            # The advice matters: an access token refreshes itself the next time
            # the CLI runs, so telling this user to sign in again would be wrong
            # (the 2026-07-18 confusion on the Windows test machine).
            message=(
                "This login's token has expired — it refreshes itself the next "
                "time a terminal runs on this account. Sign in again only if that fails."
            ),
            email=email,
            tier=tier,
        )
    return AccountSnapshot(
        account=account,
        connected=False,
        mode="unknown",
        message=_not_signed_in_message(account),
        email=email,
    )


def _describe_codex(account: AgentAccount) -> AccountSnapshot:
    from jarvis.codex_auth import codex_login_in

    connected, mode, email = codex_login_in(account.config_dir)
    if not connected:
        return AccountSnapshot(
            account=account,
            connected=False,
            mode="unknown",
            message=_not_signed_in_message(account),
        )
    if mode == "chatgpt":
        return AccountSnapshot(
            account=account,
            connected=True,
            mode="subscription",
            message=f"Signed in via ChatGPT ({email})." if email else "Signed in via ChatGPT.",
            email=email,
        )
    return AccountSnapshot(
        account=account,
        connected=True,
        mode="api_key",
        message="Signed in via an OpenAI API key.",
    )


def _not_signed_in_message(account: AgentAccount) -> str:
    """Why an account has no login — the built-in and an added one differ.

    The macOS wording is not hedging. Claude Code stores credentials in the
    Keychain there, and whether a second config directory earns its own Keychain
    entry is not something this module can prove, so an added account that comes
    back empty after a sign-in says so plainly rather than sending panes to a
    login that is silently the first account's. Tracked in docs/os-parity.md.
    """
    if account.builtin:
        return f"Not signed in — use Sign in to connect this {_DISPLAY[account.platform]} plan."
    return (
        "Not signed in yet — use Sign in, and finish the flow in the window that opens."
    )


def _duplicate_warning(label: str) -> str:
    return (
        f"This is the same subscription as {label!r} — both are signed in as the "
        "same account, so they share one plan's usage. To connect a second "
        "subscription, sign out at claude.com (or use a private window) before "
        "signing in here, otherwise the browser silently reuses the account you "
        "are already signed in with."
    )


def snapshots(platform: Platform) -> list[AccountSnapshot]:
    """Every account of *platform*, described, with same-identity rows flagged.

    Two accounts holding the SAME login is the failure this feature exists to
    prevent, and nothing about it looks wrong: both rows go green, both name a
    valid subscription, and the only visible symptom is a usage figure that
    drops twice as fast as it should — which is how the 2026-07-27 report
    surfaced, after the browser's live session auto-approved the second sign-in
    against the first account without ever showing a code to paste.

    So it is stated on the row rather than left to be inferred. Detection is by
    email, the one identity the CLI writes down; accounts with no readable email
    are never grouped, since "both unknown" is not evidence of sameness.
    """
    described = [describe(account) for account in list_accounts(platform)]
    first_seen: dict[str, str] = {}
    result: list[AccountSnapshot] = []
    for snapshot in described:
        email = (snapshot.email or "").strip().lower()
        if not (snapshot.connected and email):
            result.append(snapshot)
            continue
        owner = first_seen.get(email)
        if owner is None:
            first_seen[email] = snapshot.account.label
            result.append(snapshot)
            continue
        result.append(
            AccountSnapshot(
                account=snapshot.account,
                connected=snapshot.connected,
                mode=snapshot.mode,
                message=snapshot.message,
                email=snapshot.email,
                tier=snapshot.tier,
                warning=_duplicate_warning(owner),
            )
        )
    return result


# ------------------------------------------------------------------- login


def start_login(account: AgentAccount) -> Any:
    """Open the CLI's own sign-in flow POINTED AT this account's directory.

    Nothing about the flow itself is reimplemented — it is each CLI's real
    interactive login, in a real visible terminal, with one environment variable
    set. That is what makes a second subscription cost a sign-in rather than a
    credential-handling scheme of our own, and it is why the first account is
    untouched while the second is being added.

    Raises ``FileNotFoundError`` when the CLI is absent and
    ``InteractiveTerminalUnavailable`` on a headless host — both honest
    capability answers rather than an invisible process nobody can complete.
    """
    from jarvis.core.interactive_terminal import launch_interactive_terminal

    env = env_overrides(account.platform, account.id)
    if account.platform == "claude":
        from jarvis.claude_auth import ClaudeAuthService, claude_install_hint

        service = ClaudeAuthService()
        binary = service._resolve_binary()  # noqa: SLF001 — the module's own seam
        if binary is None:
            raise FileNotFoundError(f"Claude CLI is not installed. {claude_install_hint()}")
        argv = service._login_argv(binary)  # noqa: SLF001 — capability-selected argv
        title = f"Claude sign-in — {account.label}"
    else:
        from jarvis.codex_auth import CodexAuthService

        service_codex = CodexAuthService()
        binary = service_codex._resolve_binary()  # noqa: SLF001 — the module's own seam
        if binary is None:
            raise FileNotFoundError(
                "Codex CLI is not installed (run: npm i -g @openai/codex)."
            )
        argv = [binary, "login"]
        title = f"Codex sign-in — {account.label}"
    # The directory must exist before the CLI writes into it; a login that fails
    # on a missing folder looks to the user like a rejected account.
    try:
        account.config_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AccountError(f"The account folder is not usable: {exc}") from exc
    logger.info("Agent accounts: starting sign-in for {!r}", account.label)
    return launch_interactive_terminal(argv, title=title, env=env)


__all__ = [
    "ENV_VAR",
    "MAX_ACCOUNTS_PER_PLATFORM",
    "MAX_LABEL_CHARS",
    "PLATFORMS",
    "SCHEMA_VERSION",
    "AccountError",
    "AccountSnapshot",
    "AgentAccount",
    "Platform",
    "active_account",
    "active_ids",
    "all_accounts",
    "builtin_id",
    "config_dir_for",
    "create_account",
    "delete_account",
    "describe",
    "env_overrides",
    "list_accounts",
    "rename_account",
    "resolve",
    "set_active",
    "snapshots",
    "spawn_env",
    "start_login",
]
