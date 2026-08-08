"""Several subscriptions per coding CLI, switchable without logging out.

A user can hold more than one seat for the same CLI — two Claude Max plans, two
ChatGPT/Codex plans. Out of the box only one of them is reachable: the CLI keeps
exactly one login, so getting to the other means a full ``logout`` / ``login``
round trip that throws the first one away. Holding two plans and being able to
use only one at a time is the problem this module exists to remove.

**One account is one directory.** A CLI that qualifies resolves its whole
identity — credentials, account file, conversation history — from a single
directory, and publishes the environment variable that moves it. WHICH CLIs
those are, and which variables move them, is registry data
(``jarvis.workspace.agents.AccountSpec``) rather than a list here; ask
:func:`platforms`. A CLI with no such variable simply has no directory to
switch and is honestly absent from this feature instead of being offered a
switcher that would silently keep spending one login.

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

**A redirected directory moves the settings too, so the mode is carried over.**
The directory holds more than a login: the user-level configuration lives there
as well, including the mode the CLI starts a session in. Left alone, a pane on
an added account therefore opened in the CLI's built-in fallback — manual mode —
however the user had equipped it globally, and had to be switched over by hand
every time. :func:`inherit_default_mode` copies exactly those mode settings
across (nothing else, and never over a value set for that account by hand), so a
pane starts the way the same CLI starts in an ordinary terminal.

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

#: A registry entry's name. Deliberately not a ``Literal`` any more: the set of
#: CLIs that own a switchable login is registry DATA, and a closed type here was
#: a second, silently drifting copy of it — exactly the multi-layer enum drift
#: the project has been bitten by repeatedly (AP-4). Values are validated
#: against :func:`platforms` at every entry point instead.
Platform = str


def _account_entries() -> list[Any]:
    """Registry entries that declare how their whole identity moves."""
    from jarvis.workspace import agents as workspace_agents

    return [a for a in workspace_agents.coding_agents() if a.account is not None]


def _platforms() -> tuple[str, ...]:
    """The platforms that own a switchable subscription login, live.

    Read from the registry at CALL time rather than snapshotted at import, so a
    CLI registered after this module loads is offerable without a restart —
    the same promise the workspace registry makes everywhere else. Anything
    with no config-dir override (an API-key provider, or a CLI whose whole
    identity cannot be moved by an environment variable) has no directory to
    switch and is deliberately absent.
    """
    return tuple(a.name for a in _account_entries())


def _account_spec(platform: Platform) -> Any:
    from jarvis.workspace import agents as workspace_agents

    entry = workspace_agents.get_agent(platform)
    return entry.account if entry is not None else None


def platforms() -> tuple[Platform, ...]:
    """The platforms that own a switchable subscription login.

    A FUNCTION rather than a constant, and that is the whole point: a module
    constant is a snapshot taken at import, and the set it snapshots is registry
    data that can grow afterwards. Callers ask when they need to know.
    """
    return _platforms()


def env_var(platform: Platform) -> str | None:
    """The PRIMARY config-dir override this CLI publishes.

    Only the primary one, because that is what a caller asking this question
    means. A CLI that needs several variables to move (a data root AND a config
    root) must go through :func:`env_overrides`, which applies the whole
    mapping — setting half of them relocates half the identity and leaves the
    CLI spending the previous login, which is the worst possible outcome of an
    account switch and looks exactly like a switch that worked.
    """
    spec = _account_spec(platform)
    if spec is None or not spec.env:
        return None
    return str(spec.env[0][0])


def _native_dir_template(platform: Platform) -> str:
    """Where the CLI looks when its override is unset."""
    spec = _account_spec(platform)
    return spec.native_dir if spec is not None else "~"


def _display(platform: Platform) -> str:
    """What this CLI is called on screen."""
    from jarvis.workspace import agents as workspace_agents

    entry = workspace_agents.get_agent(platform)
    return entry.display_name if entry is not None else platform


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
    inherited = os.environ.get(env_var(platform) or "", "").strip()
    if inherited:
        return Path(inherited).expanduser()
    return Path(os.path.expanduser(_native_dir_template(platform)))


def native_dir(platform: Platform) -> Path:
    """Public name for the directory the CLI reads when nothing is redirected.

    A thin wrapper on purpose: it delegates at CALL time, so a test that
    redirects the private implementation still governs every caller — including
    :mod:`jarvis.agent_config_parity`, which needs this to know whose setup a
    redirected account dir has to carry.
    """
    return _native_dir(platform)


def builtin_id(platform: Platform) -> str:
    """The stable id of a platform's synthetic default account."""
    return f"{platform}:default"


def _builtin(platform: Platform) -> AgentAccount:
    return AgentAccount(
        id=builtin_id(platform),
        platform=platform,
        label=f"Default {_display(platform)} login",
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
    if platform not in platforms():
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
    return [account for platform in platforms() for account in list_accounts(platform)]


def resolve(account_id: str | None) -> AgentAccount | None:
    """An id back to its account; ``None`` when the id is unknown or empty."""
    if not account_id:
        return None
    for platform in platforms():
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
    return {platform: active_account(platform).id for platform in platforms()}


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
    if platform not in platforms():
        raise AccountError(f"Unknown platform: {platform}")
    clean_label = (label or "").strip()[:MAX_LABEL_CHARS]
    if not clean_label:
        raise AccountError("An account needs a name.")
    existing = [a for a in list_accounts(platform) if not a.builtin]
    if len(existing) >= MAX_ACCOUNTS_PER_PLATFORM:
        raise AccountError(
            f"{_display(platform)} already has the maximum of "
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
            platform: value for platform, value in state["active"].items() if value != account_id
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
    return {
        var: template.format(dir=str(account.config_dir))
        for var, template in (_account_spec(platform).env or ())
    }


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


# ------------------------------------------------- inherited operating mode


@dataclass(frozen=True, slots=True)
class _ModeFile:
    """Where ONE CLI records the mode it starts a fresh session in."""

    #: File inside the config directory that carries the setting.
    name: str
    #: How to read and write it. Both formats are round-tripped in place.
    fmt: Literal["json", "toml"]
    #: The setting paths to carry over, each a sequence of nested keys.
    keys: tuple[tuple[str, ...], ...]


#: What "the mode this CLI starts in" is, per platform.
#:
#: Claude Code's companion flag is listed on purpose: a default of
#: ``bypassPermissions`` that has never been acknowledged opens on a warning
#: screen, so carrying the mode without the acknowledgement would swap one
#: manual step for another. Only a value the user already set globally is ever
#: copied — this never invents an acknowledgement they did not give.
_MODE_FILES: dict[Platform, _ModeFile] = {
    "claude": _ModeFile(
        name="settings.json",
        fmt="json",
        keys=(("permissions", "defaultMode"), ("skipDangerousModePermissionPrompt",)),
    ),
    "codex": _ModeFile(
        name="config.toml",
        fmt="toml",
        keys=(("approval_policy",), ("sandbox_mode",)),
    ),
}

#: Written beside the account's own config: the values this module last mirrored
#: from the native directory. It is what tells a value the user typed themselves
#: apart from one we put there, so a later global change follows through while a
#: deliberate per-account choice is left alone. Losing the file is harmless — the
#: account's existing values then simply count as the user's own.
_MIRROR_FILE = "jarvis-inherited-mode.json"

#: Distinguishes "this setting is absent" from "this setting is set to None".
_MISSING: Any = object()


def _read_settings(path: Path, fmt: str) -> dict[str, Any] | None:
    """The mapping in *path* as plain Python values, or ``None`` if unusable.

    Unreadable, unparsable and not-a-mapping all answer the same ``None``. A
    hand-broken settings file is a bad reason to fail a pane spawn, so every one
    of them degrades to "nothing to inherit".
    """
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    try:
        if fmt == "json":
            data: Any = json.loads(raw or "{}")
        else:
            import tomllib

            data = tomllib.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        logger.debug("Agent accounts: {} is not readable ({}) — not inherited", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _open_settings(path: Path, fmt: str) -> Any:
    """The account's own file as an editable document, or ``None`` if unusable.

    A missing file is an EMPTY document rather than a failure — that is the
    normal case for an account that has never been written to. TOML goes through
    ``tomlkit`` so an existing file keeps its comments and layout.
    """
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        raw = ""
    try:
        if fmt == "json":
            data: Any = json.loads(raw or "{}")
            return data if isinstance(data, dict) else None
        import tomlkit

        return tomlkit.parse(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        logger.debug("Agent accounts: {} is not editable ({}) — left alone", path, exc)
        return None


def _setting_at(data: Any, key: tuple[str, ...]) -> Any:
    """The value at a nested key path, or ``_MISSING`` if nothing lives there."""
    node = data
    for part in key:
        if not isinstance(node, Mapping) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _put_setting(doc: Any, key: tuple[str, ...], value: Any, fmt: str) -> None:
    """Write *value* at a nested key path, creating the tables on the way."""
    node = doc
    for part in key[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            if fmt == "toml":
                import tomlkit

                child = tomlkit.table()
            else:
                child = {}
            node[part] = child
        node = child
    node[key[-1]] = value


def _dump_settings(doc: Any, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    import tomlkit

    return tomlkit.dumps(doc)


def _write_settings(path: Path, text: str) -> bool:
    """Atomic replace — a half-written settings file must never be readable."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{uuid4().hex[:8]}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("Agent accounts: {} could not be updated: {}", path, exc)
        return False
    return True


def mode_file_name(platform: Platform) -> str | None:
    """Which file inside a config dir carries that CLI's default mode.

    Read by callers that share the WHOLE file (see
    :mod:`jarvis.agent_config_parity`): once the user's own settings file is
    mirrored across, the narrow per-key mirror below has nothing left to do, and
    running it anyway would only risk disagreeing with the file it just copied.
    """
    spec = _MODE_FILES.get(platform)
    return None if spec is None else spec.name


def inherit_default_mode(platform: Platform, account_id: str | None) -> bool:
    """Carry the user's default operating mode into a redirected config home.

    Superseded for the common case by :func:`jarvis.agent_config_parity.
    ensure_parity`, which shares the entire settings file and therefore carries
    the mode with it. This stays for the case that one cannot serve: an account
    whose settings file is its OWN (hand-edited, or written by the CLI inside a
    running pane) is never overwritten wholesale, and the mode keys are still
    worth carrying into it one at a time.

    **The gap this closes.** Pointing ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME`` at
    an account's own directory moves the CLI's whole user-level configuration,
    not only its login — and the default operating mode lives there. So a pane on
    an added account started in the CLI's built-in fallback (Claude Code's manual
    mode, Codex's ask-every-time) even when the user had globally equipped
    something else, and every single pane had to be switched over by hand.
    Running the same CLI from an ordinary terminal never had that problem, which
    is exactly the inconsistency this removes.

    Only the mode settings are mirrored — never credentials, never history, never
    the rest of the file. Project-level settings are untouched by the redirect
    (the CLI reads those from the folder it runs in) and need nothing here.

    **A value the user typed into the account is never overwritten.** The mirror
    file records what was last copied across, so a setting that no longer matches
    it is a deliberate per-account choice and is left exactly as it is; one that
    still matches follows a later change of the global default. An account with
    no such setting at all simply receives the global one.

    Returns True when something was actually written. Never raises: a spawn must
    not fail because a settings file could not be read or updated — the pane then
    opens in the CLI's own fallback, which is the behaviour that existed before.
    """
    account = resolve(account_id)
    if account is None or account.platform != platform or account.builtin:
        # The built-in account reads the native directory itself. Nothing was
        # redirected, so nothing was lost, so there is nothing to carry over.
        return False
    spec = _MODE_FILES.get(platform)
    if spec is None:
        return False
    native_dir = _native_dir(platform)
    if os.path.normcase(str(native_dir)) == os.path.normcase(str(account.config_dir)):
        return False
    native = _read_settings(native_dir / spec.name, spec.fmt)
    if not native:
        # No global preference recorded — the CLI's own default is the answer.
        return False

    mirror_path = account.config_dir / _MIRROR_FILE
    mirrored = _read_settings(mirror_path, "json") or {}
    settings_path = account.config_dir / spec.name
    doc = _open_settings(settings_path, spec.fmt)
    if doc is None:
        return False

    updated = dict(mirrored)
    applied: list[str] = []
    for key in spec.keys:
        wanted = _setting_at(native, key)
        if wanted is _MISSING:
            continue
        dotted = ".".join(key)
        current = _setting_at(doc, key)
        previous = mirrored[dotted] if dotted in mirrored else _MISSING
        if current is not _MISSING and current != previous:
            # Set by hand for this account. That outranks the global default.
            continue
        if current != wanted:
            _put_setting(doc, key, wanted, spec.fmt)
            applied.append(dotted)
        updated[dotted] = wanted

    if not applied and updated == mirrored:
        return False
    if applied and not _write_settings(settings_path, _dump_settings(doc, spec.fmt)):
        return False
    try:
        payload = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"
    except (TypeError, ValueError):
        # A value we cannot record is a value we must not claim to own later.
        return bool(applied)
    _write_settings(mirror_path, payload)
    if applied:
        logger.info(
            "Agent accounts: {!r} inherited your default {} mode ({})",
            account.label,
            _display(platform),
            ", ".join(applied),
        )
    return bool(applied)


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
    if account.platform == "codex":
        return _describe_codex(account)
    return _describe_generic(account)


def _describe_generic(account: AgentAccount) -> AccountSnapshot:
    """Sign-in state for a CLI this build has no dedicated reader for.

    The important thing here is what it does NOT do. Falling through to another
    CLI's reader — which is what an ``if claude / else codex`` pair quietly does
    to every third platform — makes every seat of a new provider read as
    permanently "not signed in", because it is looking for a file that CLI never
    writes. Nothing raises, nothing logs, and the switcher shows a red dot next
    to an account that works perfectly.

    So a spec that declares no login markers gets an honest "cannot tell" rather
    than a confident wrong answer. Switching seats still works — the environment
    variable genuinely redirects the CLI — and the pane itself will say if a
    login is missing, which is the one place that can actually know.
    """
    spec = _account_spec(account.platform)
    markers = tuple(getattr(spec, "login_markers", ()) or ())
    if not markers:
        return AccountSnapshot(
            account=account,
            connected=False,
            mode="unknown",
            message=(
                f"{_display(account.platform)} reports its sign-in state only in "
                "the pane — open one to check. Switching seats works either way."
            ),
        )
    for marker in markers:
        try:
            if (account.config_dir / marker).exists():
                return AccountSnapshot(
                    account=account,
                    connected=True,
                    mode="subscription",
                    message=f"Signed in for this {_display(account.platform)} seat.",
                )
        except OSError:  # noqa: PERF203 - an unreadable path is simply not proof
            continue
    return AccountSnapshot(
        account=account,
        connected=False,
        mode="unknown",
        message=_not_signed_in_message(account),
    )


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
    if snapshot.status == "expired" and snapshot.refresh_token_present:
        # A stale ACCESS token beside a refresh token is a signed-in account
        # between sessions, not a signed-out one: the CLI renews the bearer the
        # moment a terminal runs on this directory. Reporting it disconnected
        # painted every idle account red for most of the day and pointed its
        # owner at a Sign-in button that only ever re-authorized the browser's
        # live session (the "permanently logged out" report, 2026-07-31).
        label = subscription_label(tier)
        return AccountSnapshot(
            account=account,
            connected=True,
            mode="subscription",
            message=(f"Signed in via {label} ({email})." if email else f"Signed in via {label}."),
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
        return f"Not signed in — use Sign in to connect this {_display(account.platform)} plan."
    return "Not signed in yet — use Sign in, and finish the flow in the window that opens."


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


def _generic_login_argv(account: AgentAccount) -> list[str]:
    """The CLI's own sign-in command, built from what its entry declared.

    Raises the same honest ``FileNotFoundError`` the built-in paths do when the
    binary is missing, and an ``AccountError`` when the entry declares no login
    command at all — a CLI whose only sign-in lives inside its TUI cannot be
    driven from here, and saying so beats opening an empty terminal.
    """
    import shutil

    from jarvis.core.path_augment import ensure_cli_paths
    from jarvis.workspace import agents as workspace_agents

    entry = workspace_agents.get_agent(account.platform)
    spec = entry.account if entry is not None else None
    login_argv = tuple(getattr(spec, "login_argv", ()) or ())
    if entry is None or not login_argv:
        raise AccountError(
            f"{_display(account.platform)} has no sign-in command that can be "
            "started from here — open a pane and sign in inside it."
        )
    ensure_cli_paths()
    binary = shutil.which(entry.executable)
    if binary is None:
        hint = workspace_agents.install_command(entry.name)
        raise FileNotFoundError(
            f"{entry.display_name} is not installed" + (f" (run: {hint})." if hint else ".")
        )
    return [binary, *login_argv]


def login_command(account: AgentAccount) -> tuple[list[str], str]:
    """The CLI's own sign-in argv for *account*, plus a display title.

    Shared by the two ways a sign-in can be presented — the external terminal
    window (:func:`start_login`) and the in-app guided flow
    (:mod:`jarvis.agent_login_flow`) — so the command they run can never drift
    apart. Raises ``FileNotFoundError`` when the CLI is absent and
    ``AccountError`` when the platform declares no login command.
    """
    if account.platform == "claude":
        from jarvis.claude_auth import (
            ClaudeAuthService,
            claude_account_identity,
            claude_install_hint,
        )

        service = ClaudeAuthService()
        binary = service._resolve_binary()  # noqa: SLF001 — the module's own seam
        if binary is None:
            raise FileNotFoundError(f"Claude CLI is not installed. {claude_install_hint()}")
        # Aim the sign-in at the identity this seat already holds (a re-login is
        # the common case). Without the hint, a browser with a live claude.com
        # session re-authorizes THAT account without ever asking — and a second
        # row silently lands on the first row's plan. A first-ever sign-in has
        # no recorded email yet, so there the hint is honestly absent.
        known_email, _name = claude_account_identity(account.config_dir)
        argv = service._login_argv(binary, email=known_email)  # noqa: SLF001 — capability-selected argv
        title = f"Claude sign-in — {account.label}"
    elif account.platform == "codex":
        from jarvis.codex_auth import CodexAuthService

        service_codex = CodexAuthService()
        binary = service_codex._resolve_binary()  # noqa: SLF001 — the module's own seam
        if binary is None:
            raise FileNotFoundError("Codex CLI is not installed (run: npm i -g @openai/codex).")
        argv = [binary, "login"]
        title = f"Codex sign-in — {account.label}"
    else:
        # Same reasoning as _describe_generic: an unrecognised platform must not
        # inherit a neighbour's login command. Spawning `codex login` for a Kimi
        # seat would sign the user into the wrong product, into the wrong
        # directory, and report success.
        argv = _generic_login_argv(account)
        title = f"{_display(account.platform)} sign-in — {account.label}"
    return list(argv), title


def ensure_config_dir(account: AgentAccount) -> None:
    """Create the account directory a sign-in is about to write into.

    The directory must exist before the CLI writes into it; a login that fails
    on a missing folder looks to the user like a rejected account.
    """
    try:
        account.config_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AccountError(f"The account folder is not usable: {exc}") from exc


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

    argv, title = login_command(account)
    ensure_config_dir(account)
    env = env_overrides(account.platform, account.id)
    logger.info("Agent accounts: starting sign-in for {!r}", account.label)
    return launch_interactive_terminal(argv, title=title, env=env)


__all__ = [
    "MAX_ACCOUNTS_PER_PLATFORM",
    "MAX_LABEL_CHARS",
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
    "ensure_config_dir",
    "env_overrides",
    "env_var",
    "inherit_default_mode",
    "list_accounts",
    "login_command",
    "mode_file_name",
    "native_dir",
    "platforms",
    "rename_account",
    "resolve",
    "set_active",
    "snapshots",
    "spawn_env",
    "start_login",
]
