"""Persistent setup-state flags for first-run heuristics (Phase B9.7).

A tiny JSON-backed key/value store used by the Desktop App to decide
whether a one-shot wizard has already been completed for the current
user. The only consumer today is the Obsidian Setup Dialog
(:mod:`jarvis.ui.web.setup_routes`), which auto-opens on the first
visit to the Wiki tab when Obsidian is not yet wired up — but only
once. After the user dismisses the wizard with the explicit "Hat
geklappt" button, the flag ``obsidian_setup_seen_at`` is set and the
dialog never auto-opens again. The user can still re-open it manually
from the status pill.

Scope of this module:
  * One file on disk: ``data/setup_state.json`` (relative to CWD,
    which equals the repo root at runtime).
  * Pure functional API — no singletons, no background threads, no
    event-bus integration.
  * Atomic writes via the same tempfile + ``os.replace`` pattern used
    by :func:`jarvis.setup.obsidian.register_vault`.

What this module deliberately does NOT do:
  * No schema validation — keys are free-form. The Obsidian wizard
    uses ``obsidian_setup_seen_at`` (ISO-8601 UTC string). Future
    wizards may add their own keys without coordination.
  * No file-locking. Concurrent writes from two Jarvis instances
    would race; the atomic ``os.replace`` guarantees the file is
    never corrupt, but the loser's update is discarded. That is
    acceptable for one-shot first-run flags.
  * No corruption recovery. A malformed JSON file is silently
    treated as an empty state — the next ``mark_*`` call will
    overwrite it. Logging the parse error here would be noisy; the
    flags are not load-bearing.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_STATE_PATH = Path("data") / "setup_state.json"


def state_path(override: Path | None = None) -> Path:
    """Resolve the state-file location.

    Returns ``override`` when supplied (used by unit tests with
    ``tmp_path``), otherwise the package default
    ``data/setup_state.json`` relative to the current working
    directory. At runtime the CWD is always the repo root because the
    Desktop App is launched from there; in tests the helper is fed
    an explicit absolute path.
    """
    return override if override is not None else _DEFAULT_STATE_PATH


def load_setup_state(path: Path | None = None) -> dict[str, Any]:
    """Read the setup-state file and return its dict view.

    Missing file or invalid JSON both return ``{}`` — corrupt state is
    not worth crashing on. The function never raises. The returned
    dict is a fresh object; callers may mutate it freely.
    """
    target = state_path(path)
    if not target.exists():
        return {}
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("load_setup_state: ignoring unreadable %s: %s", target, exc)
        return {}
    if not isinstance(data, dict):
        # Non-object top-level (list/scalar) is also treated as empty.
        return {}
    return data


def has_seen_obsidian_setup(path: Path | None = None) -> bool:
    """Return True iff the Obsidian setup wizard was explicitly completed.

    The flag is set by :func:`mark_obsidian_seen` only when the user
    clicks the final "Hat geklappt" button — not on Escape, click-
    outside, or browser refresh. An ISO timestamp string is stored
    under ``obsidian_setup_seen_at``; presence + non-empty value is
    the truth predicate.
    """
    state = load_setup_state(path)
    value = state.get("obsidian_setup_seen_at")
    return isinstance(value, str) and bool(value)


def mark_obsidian_seen(path: Path | None = None) -> None:
    """Record that the Obsidian setup wizard finished successfully.

    Writes ``obsidian_setup_seen_at`` to the state file with the
    current UTC ISO-8601 timestamp. Preserves any other top-level
    keys that may have been added by future wizards.

    Atomic-write pipeline (matches :func:`jarvis.setup.obsidian.register_vault`):
      1. ``mkdir -p`` the parent directory.
      2. Load existing state (or ``{}`` on missing/corrupt file).
      3. Set the timestamp key.
      4. Write to a sibling tempfile with ``flush + fsync``.
      5. ``os.replace`` over the real path.
      6. On any failure, the tempfile is cleaned up in ``finally``.

    Never raises; failures are logged at warning level. The wizard
    is best-effort UX, not a load-bearing invariant.
    """
    target = state_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("mark_obsidian_seen: cannot mkdir %s: %s", target.parent, exc)
        return

    state = load_setup_state(path)
    state["obsidian_setup_seen_at"] = datetime.now(timezone.utc).isoformat()

    tempfile_path: Path | None = None
    try:
        # The .tmp suffix carries a random token so two concurrent
        # writers cannot collide on the same tempfile name.
        tempfile_path = target.with_suffix(target.suffix + f".tmp-{secrets.token_hex(4)}")
        with open(tempfile_path, "w", encoding="utf-8", newline="") as fp:
            json.dump(state, fp, indent=2, ensure_ascii=False)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tempfile_path, target)
        tempfile_path = None  # successfully consumed
    except OSError as exc:
        logger.warning("mark_obsidian_seen: write failed for %s: %s", target, exc)
    finally:
        if tempfile_path is not None and tempfile_path.exists():
            try:
                tempfile_path.unlink()
            except OSError:
                logger.debug("mark_obsidian_seen: tempfile cleanup failed for %s", tempfile_path)


# ---------------------------------------------------------------------------
# Shared atomic merge-writer (same pattern as mark_obsidian_seen).
# ---------------------------------------------------------------------------
_ONBOARDING_KEYS = (
    "onboarding_completed_at",
    "onboarding_step",
    "onboarding_skipped_steps",
    "terms_accepted_at",
    "terms_version",
    "wake_word_acknowledged_at",
)


def _merge_state(updates: dict[str, Any], path: Path | None = None) -> None:
    """Merge ``updates`` into the state file atomically. Never raises."""
    target = state_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("_merge_state: cannot mkdir %s: %s", target.parent, exc)
        return

    state = load_setup_state(path)
    state.update(updates)

    tempfile_path: Path | None = None
    try:
        tempfile_path = target.with_suffix(target.suffix + f".tmp-{secrets.token_hex(4)}")
        with open(tempfile_path, "w", encoding="utf-8", newline="") as fp:
            json.dump(state, fp, indent=2, ensure_ascii=False)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tempfile_path, target)
        tempfile_path = None
    except OSError as exc:
        logger.warning("_merge_state: write failed for %s: %s", target, exc)
    finally:
        if tempfile_path is not None and tempfile_path.exists():
            try:
                tempfile_path.unlink()
            except OSError:
                logger.debug("_merge_state: tempfile cleanup failed for %s", tempfile_path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# First-time onboarding flags.
# ---------------------------------------------------------------------------
def get_onboarding_state(path: Path | None = None) -> dict[str, Any]:
    """Return a normalized onboarding view (missing keys default to None/[])."""
    s = load_setup_state(path)
    skipped = s.get("onboarding_skipped_steps")
    return {
        "completed_at": s.get("onboarding_completed_at") or None,
        "current_step": s.get("onboarding_step") or None,
        "skipped_steps": list(skipped) if isinstance(skipped, list) else [],
        "terms_accepted_at": s.get("terms_accepted_at") or None,
        "terms_version": s.get("terms_version") or None,
        "wake_word_acknowledged_at": s.get("wake_word_acknowledged_at") or None,
    }


def is_onboarding_complete(path: Path | None = None) -> bool:
    value = load_setup_state(path).get("onboarding_completed_at")
    return isinstance(value, str) and bool(value)


def set_onboarding_step(
    step: str, skipped: list[str] | None = None, path: Path | None = None
) -> None:
    updates: dict[str, Any] = {"onboarding_step": step}
    if skipped is not None:
        updates["onboarding_skipped_steps"] = list(skipped)
    _merge_state(updates, path)


def accept_terms(version: str, path: Path | None = None) -> None:
    _merge_state({"terms_accepted_at": _now_iso(), "terms_version": version}, path)


def acknowledge_wake_word(path: Path | None = None) -> None:
    _merge_state({"wake_word_acknowledged_at": _now_iso()}, path)


def mark_onboarding_complete(path: Path | None = None) -> None:
    _merge_state({"onboarding_completed_at": _now_iso()}, path)


def reset_onboarding(path: Path | None = None) -> list[str]:
    """Remove all onboarding keys from the state file. Returns the removed keys."""
    s = load_setup_state(path)
    removed = [k for k in _ONBOARDING_KEYS if k in s]
    for k in removed:
        s.pop(k, None)
    target = state_path(path)
    tempfile_path: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tempfile_path = target.with_suffix(target.suffix + f".tmp-{secrets.token_hex(4)}")
        with open(tempfile_path, "w", encoding="utf-8", newline="") as fp:
            json.dump(s, fp, indent=2, ensure_ascii=False)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tempfile_path, target)
        tempfile_path = None
    except OSError as exc:
        logger.warning("reset_onboarding: write failed for %s: %s", target, exc)
    finally:
        if tempfile_path is not None and tempfile_path.exists():
            try:
                tempfile_path.unlink()
            except OSError:
                logger.debug("reset_onboarding: tempfile cleanup failed for %s", tempfile_path)
    return removed


__all__ = [
    "state_path",
    "load_setup_state",
    "has_seen_obsidian_setup",
    "mark_obsidian_seen",
    "get_onboarding_state",
    "is_onboarding_complete",
    "set_onboarding_step",
    "accept_terms",
    "acknowledge_wake_word",
    "mark_onboarding_complete",
    "reset_onboarding",
]
