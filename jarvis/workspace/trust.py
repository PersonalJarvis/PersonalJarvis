"""Pre-seed each agent's "trusted folder" config so no trust dialog appears.

Detached external terminals can't be answered programmatically, so the robust
way to "skip the trust prompt" is to mark the project folder as trusted in each
CLI's own config BEFORE launching — exactly what the CLI writes when the user
clicks "trust" once.

**Which file, and what to put in it, is registry data** — a
:class:`~jarvis.workspace.agents.TrustSpec` on the agent entry — not a chain of
``if name == ...`` here. Two things fall out of that. A CLI whose config happens
to have the same shape as another's (a launch profile over the same binary)
reuses the writer instead of earning a fourth branch, and a newly registered CLI
gets trust seeding by declaring where its file lives, with no edit to this
module at all. Only two *shapes* exist, and they are the two file formats:

- a **JSON** object with a per-project entry — Claude Code's ``~/.claude.json``,
  ``projects[<path>].hasTrustDialogAccepted = true``. It keys by the cwd string
  as it saw it, and the form (drive case / slash style) matters, so a spec may
  ask for both the native and forward-slash variants to be seeded.
- a **TOML** table — Codex's ``$CODEX_HOME/config.toml``,
  ``[projects.'<path>'] trust_level = "trusted"``.

Both writes are atomic (temp file + ``os.replace``) and idempotent, and never
clobber unrelated keys. A spec may ask for a one-time backup before the first
mutation — worth it for a file the user's own CLI configuration shares, and
pointless for one we created. If a write fails we report it honestly — we never
claim "skipped" when it wasn't.

``config_dirs`` covers the second place a CLI reads that file from. A terminal
running on an ADDED subscription resolves its whole configuration from the
account's own directory (:mod:`jarvis.agent_accounts`), so trust seeded into the
machine's default config is invisible to it and the pane opens on the very
dialog this module exists to skip. Callers that know which account a terminal
will run on pass its directory here; every other caller keeps the old behaviour
untouched.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agents import TrustSpec

log = logging.getLogger(__name__)


@dataclass(slots=True)
class TrustResult:
    agent: str
    ok: bool
    method: str  # "config" | "noop" | "error"
    detail: str


def ensure_trusted(
    repo_root: Path,
    agents: list[str],
    *,
    home: Path | None = None,
    config_dirs: Mapping[str, Iterable[Path]] | None = None,
) -> list[TrustResult]:
    """Mark ``repo_root`` as trusted for each agent. ``home`` overrides the home
    dir (tests pass a tmp dir; production uses the real home + ``$CODEX_HOME``).

    ``config_dirs`` names EXTRA config directories per agent — the ones added
    subscriptions run from (see the module docstring). Each of them is seeded in
    addition to the machine's default, and contributes its own result, so a
    caller can see per directory whether the dialog will really be skipped."""
    from .agents import get_agent

    test_mode = home is not None
    home = home or Path.home()

    results: list[TrustResult] = []
    for name in agents:
        entry = get_agent(name)
        spec = entry.trust if entry is not None else None
        if spec is None:
            if entry is None:  # pragma: no cover - guarded upstream
                results.append(
                    TrustResult(name, False, "error", f"unknown agent: {name}")
                )
            elif entry.needs_trust:
                # The entry says a dialog exists but never said where the answer
                # goes. The pane still opens — the user just answers the dialog
                # once — but this must be loud rather than indistinguishable
                # from "nothing to do": a silent noop here is exactly how a
                # provider ships looking finished and greets every new folder
                # with a prompt nobody expected.
                log.warning(
                    "%s wants folder trust but declares no trust spec; "
                    "the CLI will ask in the pane.", name,
                )
                results.append(
                    TrustResult(name, True, "noop", "no trust spec — CLI will ask")
                )
            else:
                # A registered entry with no trust dialog to skip — a plain
                # shell, or a CLI that simply never asks. Nothing to write, and
                # saying so is not the same answer as "I do not know this agent".
                results.append(
                    TrustResult(name, True, "noop", "nothing to pre-trust")
                )
            continue
        cfg = _config_path(spec, home, test_mode=test_mode)
        write = _WRITERS[spec.fmt]
        results.append(write(name, repo_root, cfg, spec))
        for extra in _extra_configs(name, spec.filename, cfg, config_dirs):
            results.append(write(name, repo_root, extra, spec))
    return results


def _config_path(spec: TrustSpec, home: Path, *, test_mode: bool) -> Path:
    """Where this CLI's trust file lives on this machine.

    The CLI's own "move my config elsewhere" variable wins when it is set, which
    is the whole reason the variable is on the spec: seeding the default
    location for a CLI the user has redirected writes a file nothing reads, and
    the dialog this module exists to skip appears anyway. Tests pass an explicit
    home and are deliberately kept away from the real environment.
    """
    if spec.home_env and not test_mode:
        if raw := os.environ.get(spec.home_env):
            return Path(raw).expanduser() / spec.filename
    base = home / spec.subdir if spec.subdir else home
    return base / spec.filename


def _extra_configs(
    agent: str,
    filename: str,
    default: Path,
    config_dirs: Mapping[str, Iterable[Path]] | None,
) -> list[Path]:
    """The additional trust files to seed for ``agent``, de-duplicated.

    A workspace can hold several panes on ONE added account, and seeding the
    same file once per pane would parse and rewrite a config that grows to tens
    of kilobytes. The machine's default is dropped too — it is already covered
    by the caller above, and a host started under a profile manager can name it
    here without meaning "do it twice".
    """
    if not config_dirs:
        return []
    seen: set[str] = {os.path.normcase(str(default))}
    extras: list[Path] = []
    for directory in config_dirs.get(agent, ()):
        candidate = Path(directory) / filename
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        extras.append(candidate)
    return extras


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _project_keys(repo_root: Path, spec: TrustSpec) -> set[str]:
    """The cwd spellings to seed — one, or both slash forms when asked."""
    native = str(repo_root)
    if not spec.both_path_forms:
        return {native}
    return {native, repo_root.as_posix()}


def _trust_json(
    agent: str, repo_root: Path, cfg: Path, spec: TrustSpec
) -> TrustResult:
    """Seed trust into a JSON config with a per-project object."""
    native = str(repo_root)
    try:
        if cfg.exists():
            raw = cfg.read_text(encoding="utf-8")
            data = json.loads(raw)  # dup keys: last wins, no error
            if spec.backup:
                backup = cfg.with_name(cfg.name + ".jarvis-bak")
                if not backup.exists():
                    backup.write_text(raw, encoding="utf-8")
        else:
            data = {}
        if not isinstance(data, dict):
            return TrustResult(agent, False, "error", "config root is not an object")

        projects: Any = data
        for step in spec.section:
            nested = projects.setdefault(step, {})
            if not isinstance(nested, dict):
                return TrustResult(
                    agent, False, "error", f"{step!r} is not an object"
                )
            projects = nested

        changed = False
        for key in _project_keys(repo_root, spec):
            entry = projects.get(key)
            if not isinstance(entry, dict):
                entry = {}
            if entry.get(spec.key) != spec.value:
                entry[spec.key] = spec.value
                changed = True
            for extra_key, extra_value in spec.extra_defaults:
                entry.setdefault(extra_key, extra_value)
            projects[key] = entry

        if changed or not cfg.exists():
            _atomic_write_text(cfg, json.dumps(data, indent=2, ensure_ascii=False))
            return TrustResult(agent, True, "config", f"trusted {native}")
        return TrustResult(agent, True, "noop", "already trusted")
    except Exception as exc:  # noqa: BLE001
        log.warning("%s trust pre-seed failed: %s", agent, exc)
        return TrustResult(agent, False, "error", str(exc))


def _already_trusted_in_toml(text: str, native: str, spec: TrustSpec) -> bool:
    """Read-only "is this folder already trusted?" — the fast path.

    Deliberately ``tomllib`` and not ``tomlkit``. Both parse the same file, but
    tomlkit rebuilds it as an editable document that remembers every comment and
    every space, which is exactly what the WRITE below needs and pure waste for
    a lookup. The difference is not academic: on a real config that has
    accumulated a few hundred projects (71 KB) tomlkit took 4.6 seconds and
    tomllib 6 milliseconds — and this runs before every workspace opens, so that
    was five seconds of apparently-frozen UI on a folder that needed no change
    at all.

    A file this cannot read is not an answer, just an absence: the caller falls
    through to the slow path, which will report a real failure honestly.
    """
    import tomllib

    try:
        section: Any = tomllib.loads(text)
    except (ValueError, TypeError):
        return False
    for step in spec.section:
        if not isinstance(section, dict):
            return False
        section = section.get(step)
    if not isinstance(section, dict):
        return False
    entry = section.get(native)
    return isinstance(entry, dict) and entry.get(spec.key) == spec.value


def _trust_toml(
    agent: str, repo_root: Path, cfg: Path, spec: TrustSpec
) -> TrustResult:
    """Seed trust into a TOML config with a per-project table."""
    native = str(repo_root)
    try:
        existing_text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
        if existing_text and _already_trusted_in_toml(existing_text, native, spec):
            return TrustResult(agent, True, "noop", "already trusted")

        # Only now, when something actually has to change, is the
        # formatting-preserving parser worth its cost.
        import tomlkit

        if existing_text:
            doc = tomlkit.parse(existing_text)
        else:
            cfg.parent.mkdir(parents=True, exist_ok=True)
            doc = tomlkit.document()

        section: Any = doc
        for step in spec.section:
            nested = section.get(step)
            if nested is None:
                nested = tomlkit.table()
                section[step] = nested
            section = nested

        existing = section.get(native)
        if existing is not None and dict(existing).get(spec.key) == spec.value:
            return TrustResult(agent, True, "noop", "already trusted")

        entry = tomlkit.table()
        entry[spec.key] = spec.value
        for extra_key, extra_value in spec.extra_defaults:
            entry.setdefault(extra_key, extra_value)
        section[native] = entry
        _atomic_write_text(cfg, tomlkit.dumps(doc))
        return TrustResult(agent, True, "config", f"trusted {native}")
    except Exception as exc:  # noqa: BLE001
        log.warning("%s trust pre-seed failed: %s", agent, exc)
        return TrustResult(agent, False, "error", str(exc))


#: One writer per FILE FORMAT — never per product. A CLI that stores trust the
#: way another already does reuses the writer by declaring the same ``fmt``.
_WRITERS: dict[str, Callable[[str, Path, Path, TrustSpec], TrustResult]] = {
    "json": _trust_json,
    "toml": _trust_toml,
}


__all__ = ["TrustResult", "ensure_trusted"]
