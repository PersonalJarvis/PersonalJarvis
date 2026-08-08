"""Give a redirected coding-CLI config dir the user's OWN setup.

An Agentic-IDE pane has to be the same session the user gets by typing
``claude`` or ``codex`` in an ordinary terminal: the same skills, subagents,
slash commands, plugins and connectors, hooks, output styles and user-level
memory file. A pane on the built-in login gets that for free — it spawns with a
plain inherited environment, byte for byte what a terminal would have.

A pane on an ADDED subscription does not. Switching accounts works by pointing
the CLI's own config-dir override (``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME``) at a
directory of that account's own (:mod:`jarvis.agent_accounts`) — and that
directory carries the CLI's whole USER LEVEL, not just its login. So the
redirect that buys a second subscription silently costs the user's entire setup.
Measured on a real install (2026-07-27): a pane on an added account saw the
CLI's built-in skills plus the project's ``.claude/`` and nothing else, while
the same CLI in a normal terminal saw 93 user skills, thirteen plugins and the
user's global instructions. That is not a smaller session, it is a different
product — and it is the gap this module closes.

**An explicit allowlist, never a denylist.** Only what a CLI reads as
user-level CONFIGURATION or EXTENSIONS is shared (:data:`USER_SETUP`).
Everything that identifies the account or belongs to its conversations —
``.credentials.json``, ``auth.json``, ``.claude.json``, ``projects/``,
``sessions/``, ``history.jsonl`` — is untouched by construction, because it is
not on the list. That direction matters: sharing a credential file is exactly
the failure the account feature exists to prevent (see
:mod:`jarvis.claude_credentials` — a copied OAuth token starts dying the moment
it is made), whereas a config entry nobody listed yet merely stays missing until
someone adds it.

**Directories are LINKED, single files are MIRRORED.** The difference is not
cosmetic:

* A directory link (symlink, or a junction on Windows where symlinks need a
  privilege) means the pane reads the user's real ``skills/`` folder. A skill
  installed today shows up in every account tomorrow, nothing is duplicated on
  disk, and a plugin the CLI installs from inside a pane lands where the user's
  other terminals will see it. Where no link can be made at all, the tree is
  copied instead — except for entries marked :attr:`Shared.copy_fallback`
  ``False``, which are large and mutable enough (``plugins/``) that a copy would
  be a lie dressed as a feature; those are reported as skipped.
* A single file is COPIED, and deliberately not linked: several components write
  into a config dir with an atomic ``os.replace``, which silently replaces a
  link with a private file. A copy cannot be broken that way. The copy is
  guarded by a digest recorded in :data:`STATE_FILE`, so a later change to the
  user's global file follows through, while a value written for that account —
  by hand, or by the CLI itself inside a pane — is never overwritten.

**Idempotent and self-healing.** It runs before every pane spawn: a few stat
calls when everything is already in place, a re-link when something replaced a
share with a copy, and a repair when a link went dangling because the user
deleted the entry it pointed at.

**Never raises.** A pane must open even when a link cannot be created or a file
cannot be read; every failure degrades to that entry staying unshared, with a
log line naming it. Callers get a :class:`ParityReport` and can say out loud
what did not make it across.

Cross-platform by construction: ``pathlib`` throughout, POSIX symlinks,
Windows junctions where symlink creation is refused, and a copy as the last
resort on any host that allows neither. Nothing here is specific to one OS's
filesystem layout — the entry names come from the CLIs, not from the platform.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from jarvis.agent_accounts import Platform, native_dir, resolve

#: Bumped when the state file's shape changes incompatibly. An unknown version
#: reads as "nothing has been mirrored yet", which costs one refresh of the
#: mirrored files and never a wrong "this was the user's own value" verdict.
STATE_VERSION = 1

#: Where this module records what it last copied across. Lives inside the
#: account's own directory (next to :data:`jarvis.agent_accounts._MIRROR_FILE`)
#: so forgetting the account forgets its bookkeeping too. Losing the file is
#: harmless: every mirrored file then counts as the account's own.
STATE_FILE = "jarvis-shared-setup.json"

#: Appended to an account-local folder that the user's own tree replaces. Kept
#: beside it rather than deleted — see :func:`_supersede`.
SUPERSEDED_SUFFIX = ".jarvis-superseded"

#: One lock per account directory. See :func:`setup_lock`.
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def setup_lock(config_dir: Path) -> threading.RLock:
    """The lock every writer into ONE account's config dir must hold.

    Panes attach CONCURRENTLY — a restored workspace re-attaches all of them at
    once, deliberately, so that eight agents do not start one after the other.
    Several of them can therefore prepare the same account's directory in
    different threads at the same moment, and two of the things that happen
    there are read-modify-write cycles on the SAME file: Claude Code's
    ``.claude.json`` receives the folder's trust entry (via
    :mod:`jarvis.workspace.trust`) and the user's MCP servers (via
    :func:`ensure_parity`). Unserialized, the later write is built on a document
    read before the earlier one landed, and it silently drops it — a lost trust
    entry is a dialog the user has to click, a lost merge is a pane without its
    connectors.

    Re-entrant on purpose: the registry holds this across BOTH steps, and the
    parity run inside it takes the same lock again on the same thread.

    Per directory rather than global, so two accounts still prepare in parallel;
    the map is bounded by how many accounts exist (a handful, capped at
    :data:`jarvis.agent_accounts.MAX_ACCOUNTS_PER_PLATFORM`).
    """
    key = os.path.normcase(str(config_dir))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


@dataclass(frozen=True, slots=True)
class Shared:
    """One entry of a CLI's user level that a redirected dir must also have."""

    #: Entry name inside the config directory, exactly as the CLI reads it.
    name: str
    kind: Literal["dir", "file"]
    #: How a FILE is structured, which decides what happens when the account
    #: already has one of its own: a document can be filled key by key, plain
    #: text can only be taken whole or left alone. Ignored for directories.
    fmt: Literal["json", "toml", "text"] = "text"
    #: May this entry be COPIED on a host where no link can be created? False
    #: for trees that are large or that the CLI rewrites constantly — a copy of
    #: those goes stale invisibly, and "missing, and said so" beats that.
    copy_fallback: bool = True
    #: Does this tree only work as a WHOLE? Set for a directory whose files and
    #: subfolders reference each other, so half of it shared and half of it the
    #: account's own is not a partial success but a broken state. Such a tree
    #: supersedes a copy the CLI made for the account (kept beside it under
    #: :data:`SUPERSEDED_SUFFIX`) instead of being merged into it.
    whole: bool = False


@dataclass(frozen=True, slots=True)
class MergedKey:
    """One top-level key of a JSON document that must carry the user's value.

    For documents that mix user configuration with account IDENTITY. Claude
    Code's ``.claude.json`` is the case that forces this: user-scope MCP servers
    (the "connectors" a pane is expected to have) live in it beside
    ``oauthAccount`` and ``userID``, so the document cannot be shared as a whole
    — one key can.
    """

    name: str
    key: str


#: What "the user's setup" IS, per CLI. Order is presentation only.
#:
#: Deliberately absent, and it must stay that way: credentials
#: (``.credentials.json``, ``auth.json``), the per-account conversation record
#: (``projects/``, ``sessions/``, ``history.jsonl``, ``shell-snapshots/``), and
#: caches. Those are what makes two accounts two accounts.
USER_SETUP: dict[Platform, tuple[Shared, ...]] = {
    "claude": (
        # User-level memory: the instructions every session of the user's starts
        # with. A pane without it answers differently from their terminal.
        Shared("CLAUDE.md", "file"),
        # User settings: model, output style, permissions, hooks, statusLine,
        # env, enabled plugins and known marketplaces. This is also what carries
        # the default operating mode, which is why this module supersedes the
        # narrow mode mirror in jarvis.agent_accounts.inherit_default_mode.
        Shared("settings.json", "file", fmt="json"),
        Shared("settings.local.json", "file", fmt="json"),
        Shared("skills", "dir"),
        Shared("agents", "dir"),
        Shared("commands", "dir"),
        Shared("hooks", "dir"),
        Shared("output-styles", "dir"),
        # Installed plugins and their marketplaces — the connectors a pane is
        # expected to have. Never copied: the tree holds cloned repositories and
        # the CLI rewrites its state files, so a copy would be a stale snapshot
        # of somebody's plugin set rather than their plugin set. And never half
        # shared: which plugins are ACTIVE lives in state files beside the
        # marketplaces they name, so the user's marketplaces next to the
        # account's own state list nothing the CLI can load.
        Shared("plugins", "dir", copy_fallback=False, whole=True),
    ),
    "codex": (
        Shared("AGENTS.md", "file"),
        # Codex keeps its whole configuration in one file — model, approval
        # policy, sandbox, and [mcp_servers] — and its login in a separate
        # auth.json, so the config file can be shared as a whole.
        Shared("config.toml", "file", fmt="toml"),
        Shared("hooks.json", "file", fmt="json"),
        Shared("prompts", "dir"),
        Shared("skills", "dir"),
        # User-level rules — the standing instructions a Codex session starts
        # with, the counterpart of Claude Code's user memory file.
        Shared("rules", "dir"),
        # Same reasoning as Claude Code's plugins above, and the same treatment.
        Shared("plugins", "dir", copy_fallback=False, whole=True),
    ),
}

#: Keys merged out of a document that also holds identity. See :class:`MergedKey`.
MERGED_KEYS: dict[Platform, tuple[MergedKey, ...]] = {
    "claude": (
        MergedKey(".claude.json", "mcpServers"),
        # The CLI's first-run wizard is keyed on these markers, NOT on the
        # credentials: an account that is fully signed in but lacks them still
        # boots every new pane into "Select login method" (2026-08-08 report —
        # the user read a healthy account as a failed login). Carrying the
        # user's own markers makes a pane on an added account start where their
        # ordinary terminal starts. `_merge_key` never overwrites a value the
        # account already holds, so an account that walked the wizard itself
        # keeps its own record.
        MergedKey(".claude.json", "hasCompletedOnboarding"),
        MergedKey(".claude.json", "lastOnboardingVersion"),
        MergedKey(".claude.json", "theme"),
    ),
    # Codex's mcp_servers live in config.toml, which is shared whole above.
    "codex": (),
}


@dataclass(frozen=True, slots=True)
class ParityReport:
    """What a redirected config dir now shares with the user's own.

    ``shared`` maps an entry name to HOW it is shared — ``linked`` / ``junction``
    (the pane reads the user's real folder), ``copied``, ``mirrored`` (a file
    copy that tracks the user's), ``merged`` (one key of a document), or
    ``current`` (already in place, nothing to do). ``skipped`` carries a reason
    per entry that could not come across, so a caller can report it instead of
    quietly delivering less.
    """

    shared: dict[str, str] = field(default_factory=dict)
    skipped: tuple[tuple[str, str], ...] = ()
    #: False when nothing was redirected in the first place (the built-in login),
    #: which is the case where a pane already spawns exactly like a terminal.
    redirected: bool = False

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.shared)


# ----------------------------------------------------------------- state file


def _state_path(config_dir: Path) -> Path:
    return config_dir / STATE_FILE


def _read_state(config_dir: Path) -> dict[str, str]:
    """The digests of the files this module last copied across.

    Every failure mode — missing, truncated, hand-edited, written by a newer
    build — answers the same empty mapping, which costs one refresh and never a
    wrong verdict about whose value a file holds.
    """
    try:
        raw = _state_path(config_dir).read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        return {}
    digests = data.get("digests")
    if not isinstance(digests, dict):
        return {}
    return {str(k): str(v) for k, v in digests.items()}


def _write_state(config_dir: Path, digests: dict[str, str]) -> None:
    payload = json.dumps(
        {"version": STATE_VERSION, "digests": digests}, indent=2, ensure_ascii=False
    )
    _atomic_write(_state_path(config_dir), payload)


def _atomic_write(path: Path, text: str) -> bool:
    """Replace ``path`` in one step — a half-written config must never be read."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("Agent setup: {} could not be written: {}", path, exc)
        return False
    return True


def _digest(path: Path) -> str | None:
    """Content digest of one file, or ``None`` when it cannot be read."""
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


# ---------------------------------------------------------------- link layer


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _resolves_to(target: Path, source: Path) -> bool:
    """Is ``target`` already a link that lands on ``source``?

    Resolved rather than read: this must answer True for a POSIX symlink AND for
    a Windows junction, and ``os.path.islink`` is documented to answer False for
    junctions. ``Path.resolve`` follows both.
    """
    try:
        return _same_path(target.resolve(strict=True), source.resolve(strict=True))
    except OSError:
        return False


def _dangling(path: Path) -> bool:
    """A link whose target is gone — the user deleted the entry we pointed at."""
    return os.path.lexists(path) and not path.exists()


def _remove(path: Path) -> bool:
    """Drop a link (never its target). Directory links unlink on POSIX, rmdir on
    Windows, so both are tried before giving up."""
    for attempt in (os.unlink, os.rmdir):
        try:
            attempt(path)
            return True
        except OSError:
            continue
    logger.debug("Agent setup: {} could not be removed", path)
    return False


def _link(source: Path, target: Path) -> str | None:
    """Point ``target`` at ``source``. Returns how, or ``None`` if impossible.

    A symlink first, because it is the one mechanism both filesystems and both
    CLIs treat identically. On Windows creating one needs Developer Mode or an
    elevated process, so a refusal falls back to a junction — a reparse point
    that needs no privilege, is transparent to every file API, and only works
    for directories, which is all this path is used for.
    """
    is_dir = source.is_dir()
    try:
        os.symlink(source, target, target_is_directory=is_dir)
        return "linked"
    except (OSError, NotImplementedError, AttributeError) as exc:
        logger.debug("Agent setup: symlink {} -> {} refused ({})", target, source, exc)
    if is_dir and sys.platform == "win32":
        try:
            import _winapi  # noqa: PLC0415 - CPython-internal, Windows-only

            _winapi.CreateJunction(str(source), str(target))  # type: ignore[attr-defined]
            return "junction"
        except (OSError, AttributeError, ImportError) as exc:
            logger.debug("Agent setup: junction {} -> {} failed ({})", target, source, exc)
    return None


def _copy(source: Path, target: Path) -> str | None:
    """Last resort where no link can be made. Returns ``"copied"`` or ``None``."""
    try:
        if source.is_dir():
            # A user's skills folder can itself contain symlinks; following them
            # keeps the copy usable, and a dangling one is skipped rather than
            # failing the whole tree.
            shutil.copytree(
                source, target, symlinks=False, dirs_exist_ok=True, ignore_dangling_symlinks=True
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    except (OSError, shutil.Error) as exc:
        logger.debug("Agent setup: copying {} to {} failed ({})", source, target, exc)
        return None
    return "copied"


def _supersede(target: Path) -> Path | None:
    """Move an account-local copy aside so the user's own tree can take its place.

    Kept rather than deleted, and only ever done when the backup name is free —
    so this can run before every spawn without stacking up folders, and a
    surprised user can always look at what was replaced. Returns where it went,
    or ``None`` when it could not be moved (the caller then merges instead).
    """
    backup = target.with_name(target.name + SUPERSEDED_SUFFIX)
    if os.path.lexists(backup):
        return None
    try:
        os.rename(target, backup)
    except OSError as exc:
        logger.debug("Agent setup: {} could not be moved aside ({})", target, exc)
        return None
    return backup


def _share_dir(
    source: Path, target: Path, *, copy_fallback: bool, whole: bool
) -> tuple[str | None, str]:
    """Make ``target`` carry ``source``'s directory. Returns ``(how, reason)``.

    Four states, and the third is the one that keeps this safe:

    1. Nothing there — link the whole directory (one reparse point, live).
    2. Already our link — nothing to do.
    3. A REAL directory the CLI created for this account. Its own entries stay,
       and only the children the account is missing are linked in — so an
       account-local skill or subagent is never discarded. A tree marked
       :attr:`Shared.whole` takes the other route: it is moved aside and
       replaced by the user's, because half of that tree is worse than none of
       it (see ``plugins`` in :data:`USER_SETUP`).
    4. A link somewhere else entirely — left alone and reported, because it is
       not this module's to redirect.
    """
    if _dangling(target):
        _remove(target)
    if not os.path.lexists(target):
        how = _link(source, target)
        if how is None and copy_fallback:
            how = _copy(source, target)
        if how is None:
            return None, "this host allows neither a link nor a usable copy"
        return how, ""
    if _resolves_to(target, source):
        return "current", ""
    if not target.is_dir():
        return None, "the account has a file of its own by that name"
    if whole:
        moved = _supersede(target)
        if moved is not None:
            how = _link(source, target)
            if how is None and copy_fallback:
                how = _copy(source, target)
            if how is not None:
                logger.info(
                    "Agent setup: {} now comes from your own setup — what was there is kept at {}",
                    target.name,
                    moved.name,
                )
                return how, ""
            # Nothing could take its place, so put back exactly what was there.
            try:
                os.rename(moved, target)
            except OSError as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Agent setup: {} was moved to {} and could not be put back: {}",
                    target,
                    moved,
                    exc,
                )
                return None, "this host allows neither a link nor a usable copy"
        return None, "this host cannot share that folder as a whole"
    linked = 0
    for child in _children(source):
        spot = target / child.name
        if _dangling(spot):
            _remove(spot)
        if os.path.lexists(spot):
            continue
        how = _link(child, spot)
        if how is None and copy_fallback:
            how = _copy(child, spot)
        if how is not None:
            linked += 1
    if linked:
        return "linked", ""
    return "current", ""


def _children(source: Path) -> list[Path]:
    try:
        return sorted(source.iterdir())
    except OSError as exc:
        logger.debug("Agent setup: {} could not be listed ({})", source, exc)
        return []


# ---------------------------------------------------------------- file layer


def _share_file(
    source: Path,
    target: Path,
    *,
    key: str,
    fmt: str,
    state: dict[str, str],
) -> tuple[str | None, str]:
    """Give the account ``source``'s content without overwriting its own choices.

    Three outcomes, and the third is the one that matters most in practice:

    * the account has no such file, or still has exactly what was last copied
      across — then it is a mirror of the user's file and simply follows it
      (``mirrored`` / ``current``);
    * the file has its own content AND a structure to merge into — then the keys
      the account is MISSING are filled in from the user's, recursively, and
      every key it already has is left exactly as it is (``merged``). This is the
      normal state of a config a pane has ever written to: a pane that changed
      its theme must keep that theme, and must still get the user's plugins,
      permissions and status line;
    * the file has its own content and no structure (a plain-text memory file) —
      then there is nothing to merge and it stays the account's own.

    A merged file is deliberately NOT recorded in ``state``: it is nobody's copy
    any more, so the next run fills what is missing again rather than replacing
    the hybrid with the user's file wholesale.
    """
    source_digest = _digest(source)
    if source_digest is None:
        return None, "the user's own file could not be read"
    target_digest = _digest(target) if target.exists() else None
    if target_digest == source_digest:
        state[key] = source_digest
        return "current", ""
    if target_digest is None or target_digest == state.get(key):
        if _copy(source, target) is None:
            return None, "the copy could not be written"
        state[key] = source_digest
        return "mirrored", ""
    if fmt == "text":
        return None, "this account has its own version of that file"
    filled, why = _fill_missing(source, target, fmt)
    if filled is None:
        return None, why
    state.pop(key, None)
    return filled, ""


def _read_document(path: Path, fmt: str) -> dict[str, Any] | None:
    """A config document as plain Python values, or ``None`` if unusable."""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    try:
        if fmt == "toml":
            import tomllib  # noqa: PLC0415 - stdlib, only for TOML platforms

            data: Any = tomllib.loads(raw)
        else:
            data = json.loads(raw or "{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _fill(source: Any, target: Any) -> bool:
    """Add the keys ``target`` lacks, recursively. Returns whether it changed.

    Only ADDS. A value the account already holds is never replaced — not even a
    nested one — and a list is treated as one value rather than merged item by
    item, because a permission list the account narrowed on purpose must not
    silently regain the entries it dropped.
    """
    changed = False
    for name, value in source.items():
        if name not in target:
            target[name] = value
            changed = True
        elif isinstance(value, dict) and isinstance(target[name], dict):
            changed = _fill(value, target[name]) or changed
    return changed


def _fill_missing(source: Path, target: Path, fmt: str) -> tuple[str | None, str]:
    """Fill the keys the account's own file is missing from the user's."""
    native = _read_document(source, fmt)
    if native is None:
        return None, "the user's own file could not be read"
    if fmt == "toml":
        import tomlkit  # noqa: PLC0415 - only needed to WRITE a TOML document

        try:
            doc: Any = tomlkit.parse(target.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None, "this account's own file could not be read"
        if not _fill(native, doc):
            return "current", ""
        rendered = tomlkit.dumps(doc)
    else:
        own = _read_document(target, fmt)
        if own is None:
            return None, "this account's own file could not be read"
        if not _fill(native, own):
            return "current", ""
        rendered = json.dumps(own, indent=2, ensure_ascii=False)
    if not _atomic_write(target, rendered):
        return None, "the merged file could not be written"
    return "merged", ""


def _merge_key(
    source: Path, target: Path, key: str, *, state_key: str, state: dict[str, str]
) -> tuple[str | None, str]:
    """Carry ONE key across, leaving every other key of both documents alone."""
    native = _read_document(source, "json")
    if not native or key not in native:
        return None, ""  # nothing on the user's side to carry — not a failure
    wanted = native[key]
    try:
        encoded = json.dumps(wanted, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return None, "that setting cannot be represented"
    digest = sha256(encoded.encode("utf-8")).hexdigest()
    doc = _read_document(target, "json")
    if doc is None:
        doc = {}
    if key in doc:
        try:
            current = json.dumps(doc[key], sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            current = ""
        if current == encoded:
            state[state_key] = digest
            return "current", ""
        if sha256(current.encode("utf-8")).hexdigest() != state.get(state_key):
            return None, "this account has its own version of that setting"
    doc[key] = wanted
    if not _atomic_write(target, json.dumps(doc, indent=2, ensure_ascii=False)):
        return None, "the document could not be written"
    state[state_key] = digest
    return "merged", ""


# --------------------------------------------------------------------- public


def ensure_parity(platform: Platform, account_id: str | None) -> ParityReport:
    """Make ``account_id``'s config dir carry the user's own setup for ``platform``.

    A no-op — and an empty report — for the built-in login, for an unknown
    account, and for a host where the account's directory IS the native one.
    Nothing was redirected in those cases, so nothing was lost.

    Safe to call before every spawn: idempotent, cheap when everything is in
    place, and never raising.
    """
    account = resolve(account_id)
    if account is None or account.platform != platform or account.builtin:
        return ParityReport()
    native = native_dir(platform)
    if _same_path(native, account.config_dir):
        return ParityReport()
    if not native.is_dir():
        # The user has no setup of their own yet (a fresh machine): there is
        # nothing to share, and the account's own defaults are the whole truth.
        return ParityReport(redirected=True)
    try:
        account.config_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Agent setup: {} is not usable: {}", account.config_dir, exc)
        return ParityReport(redirected=True, skipped=((str(account.config_dir), str(exc)),))

    with setup_lock(account.config_dir):
        shared, skipped = _provision(platform, native, account.config_dir)

    fresh = {name: how for name, how in shared.items() if how != "current"}
    if fresh:
        logger.info(
            "Agent setup: {!r} now shares your own {} setup ({})",
            account.label,
            platform,
            ", ".join(f"{name} [{how}]" for name, how in fresh.items()),
        )
    for name, why in skipped:
        logger.info("Agent setup: {} stayed the account's own — {}", name, why)
    return ParityReport(shared=shared, skipped=tuple(skipped), redirected=True)


def _provision(
    platform: Platform, native: Path, config_dir: Path
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Share every allowlisted entry into ``config_dir``. Caller holds its lock."""
    state = _read_state(config_dir)
    before = dict(state)
    shared: dict[str, str] = {}
    skipped: list[tuple[str, str]] = []

    for entry in USER_SETUP.get(platform, ()):
        source = native / entry.name
        target = config_dir / entry.name
        try:
            if entry.kind == "dir":
                if not source.is_dir():
                    continue
                how, why = _share_dir(
                    source,
                    target,
                    copy_fallback=entry.copy_fallback,
                    whole=entry.whole,
                )
            else:
                if not source.is_file():
                    continue
                how, why = _share_file(source, target, key=entry.name, fmt=entry.fmt, state=state)
        except OSError as exc:  # pragma: no cover - defensive: a pane must open
            how, why = None, str(exc)
        if how is not None:
            shared[entry.name] = how
        elif why:
            skipped.append((entry.name, why))

    for merged in MERGED_KEYS.get(platform, ()):
        source = native / merged.name
        target = config_dir / merged.name
        label = f"{merged.name}#{merged.key}"
        try:
            how, why = _merge_key(source, target, merged.key, state_key=label, state=state)
        except OSError as exc:  # pragma: no cover - defensive
            how, why = None, str(exc)
        if how is not None:
            shared[label] = how
        elif why:
            skipped.append((label, why))

    if state != before:
        _write_state(config_dir, state)
    return shared, skipped


__all__ = [
    "MERGED_KEYS",
    "STATE_FILE",
    "STATE_VERSION",
    "SUPERSEDED_SUFFIX",
    "USER_SETUP",
    "MergedKey",
    "ParityReport",
    "Shared",
    "ensure_parity",
    "setup_lock",
]
