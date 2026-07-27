"""Pre-seed each agent's "trusted folder" config so no trust dialog appears.

Detached external terminals can't be answered programmatically, so the robust
way to "skip the trust prompt" is to mark the project folder as trusted in each
CLI's own config BEFORE launching — exactly what the CLI writes when the user
clicks "trust" once:

- **Claude Code** keeps per-project trust in ``~/.claude.json`` under
  ``projects[<path>].hasTrustDialogAccepted = true``. Claude keys by the cwd
  string, and the form (drive case / slash style) matters, so we seed both the
  native and forward-slash variants.
- **Codex** keeps it in ``$CODEX_HOME/config.toml`` (default ``~/.codex``) under
  ``[projects.'<path>'] trust_level = "trusted"``.

Both writes are atomic (temp file + ``os.replace``) and idempotent, and never
clobber unrelated keys. ``~/.claude.json`` is backed up once before the first
mutation. If a write fails we report it honestly — we never claim "skipped"
when it wasn't.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(slots=True)
class TrustResult:
    agent: str
    ok: bool
    method: str  # "config" | "noop" | "error"
    detail: str


def ensure_trusted(
    repo_root: Path, agents: list[str], *, home: Path | None = None
) -> list[TrustResult]:
    """Mark ``repo_root`` as trusted for each agent. ``home`` overrides the home
    dir (tests pass a tmp dir; production uses the real home + ``$CODEX_HOME``)."""
    test_mode = home is not None
    home = home or Path.home()
    claude_cfg = home / ".claude.json"
    if test_mode:
        codex_home = home / ".codex"
    else:
        codex_home = Path(os.environ.get("CODEX_HOME") or (home / ".codex"))

    results: list[TrustResult] = []
    for name in agents:
        if name == "claude":
            results.append(_trust_claude(repo_root, claude_cfg))
        elif name == "codex":
            results.append(_trust_codex(repo_root, codex_home / "config.toml"))
        else:  # pragma: no cover - guarded upstream
            results.append(TrustResult(name, False, "error", f"unknown agent: {name}"))
    return results


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _trust_claude(repo_root: Path, cfg: Path) -> TrustResult:
    native = str(repo_root)
    forward = repo_root.as_posix()
    try:
        if cfg.exists():
            raw = cfg.read_text(encoding="utf-8")
            data = json.loads(raw)  # dup keys: last wins, no error
            backup = cfg.with_name(cfg.name + ".jarvis-bak")
            if not backup.exists():
                backup.write_text(raw, encoding="utf-8")
        else:
            data = {}
        if not isinstance(data, dict):
            return TrustResult("claude", False, "error", "config root is not an object")

        projects = data.setdefault("projects", {})
        if not isinstance(projects, dict):
            return TrustResult("claude", False, "error", "'projects' is not an object")

        changed = False
        for key in {native, forward}:
            entry = projects.get(key)
            if not isinstance(entry, dict):
                entry = {}
            if entry.get("hasTrustDialogAccepted") is not True:
                entry["hasTrustDialogAccepted"] = True
                changed = True
            entry.setdefault("hasCompletedProjectOnboarding", True)
            projects[key] = entry

        if changed or not cfg.exists():
            _atomic_write_text(cfg, json.dumps(data, indent=2, ensure_ascii=False))
            return TrustResult("claude", True, "config", f"trusted {native}")
        return TrustResult("claude", True, "noop", "already trusted")
    except Exception as exc:  # noqa: BLE001
        log.warning("claude trust pre-seed failed: %s", exc)
        return TrustResult("claude", False, "error", str(exc))


def _already_trusted_by_codex(text: str, native: str) -> bool:
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
        projects = tomllib.loads(text).get("projects")
    except (ValueError, TypeError):
        return False
    if not isinstance(projects, dict):
        return False
    entry = projects.get(native)
    return isinstance(entry, dict) and entry.get("trust_level") == "trusted"


def _trust_codex(repo_root: Path, cfg: Path) -> TrustResult:
    native = str(repo_root)
    try:
        existing_text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
        if existing_text and _already_trusted_by_codex(existing_text, native):
            return TrustResult("codex", True, "noop", "already trusted")

        # Only now, when something actually has to change, is the
        # formatting-preserving parser worth its cost.
        import tomlkit

        if existing_text:
            doc = tomlkit.parse(existing_text)
        else:
            cfg.parent.mkdir(parents=True, exist_ok=True)
            doc = tomlkit.document()

        projects = doc.get("projects")
        if projects is None:
            projects = tomlkit.table()
            doc["projects"] = projects

        existing = projects.get(native)
        already = (
            existing is not None
            and dict(existing).get("trust_level") == "trusted"
        )
        if already:
            return TrustResult("codex", True, "noop", "already trusted")

        entry = tomlkit.table()
        entry["trust_level"] = "trusted"
        projects[native] = entry
        _atomic_write_text(cfg, tomlkit.dumps(doc))
        return TrustResult("codex", True, "config", f"trusted {native}")
    except Exception as exc:  # noqa: BLE001
        log.warning("codex trust pre-seed failed: %s", exc)
        return TrustResult("codex", False, "error", str(exc))


__all__ = ["TrustResult", "ensure_trusted"]
