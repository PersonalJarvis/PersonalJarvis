"""Obsidian install + vault registration detector (Phase B9.1 + B9.2).

Greenfield read-only detector for the Phase B9 Obsidian Setup Wizard.

Scope of this module:
  * Detect whether Obsidian.exe is installed on this Windows machine
    (probes the three common install locations + Uninstall registry).
  * Read the user-level vault index (platform-aware:
    ``%APPDATA%\\obsidian\\obsidian.json`` on Windows,
    ``~/Library/Application Support/obsidian/obsidian.json`` on macOS,
    ``$XDG_CONFIG_HOME/obsidian/obsidian.json`` or ``~/.config/obsidian/obsidian.json`` on Linux)
    and return its registered vaults as typed Pydantic models.
  * Decide whether a given expected vault path is already registered
    (case-insensitive, trailing-slash tolerant — Windows-friendly).

What this module deliberately does NOT do:
  * No mutation of ``obsidian.json`` (that lives in Sub-Agent 2's writer).
  * No FastAPI / HTTP surface (that is Sub-Agent 3's route).
  * No subprocess launches of Obsidian.exe.
  * No network calls.

All functions are safe to call on a machine without Obsidian installed.
``detect_obsidian()`` never raises; ``read_obsidian_vaults()`` only raises
on a corrupt ``obsidian.json`` (treated as a real configuration error the
caller must surface to the user).

Win32 imports (``win32api`` for file-version extraction, ``winreg`` for
registry probing) are lazy-imported inside the functions so this module
stays importable from non-Windows CI runners and pytest collection
contexts.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public Pydantic models
# ---------------------------------------------------------------------------
class ObsidianDetection(BaseModel):
    """Result of ``detect_obsidian()``.

    ``installed`` is True iff an ``Obsidian.exe`` was located at one of the
    known install paths or in the Uninstall registry. ``version`` is a
    best-effort string from ``win32api.GetFileVersionInfo`` — ``None`` if
    the lookup failed (missing pywin32, unreadable file, etc.).
    """

    installed: bool
    exe_path: Path | None = None
    version: str | None = None


class VaultEntry(BaseModel):
    """A single entry in Obsidian's ``vaults`` dictionary.

    The ``id`` is the JSON object key (Obsidian's internal short UUID).
    ``ts`` is the last-opened millisecond timestamp; ``is_open`` mirrors
    the ``open`` flag from the JSON file.
    """

    id: str
    path: Path
    ts: int = 0
    is_open: bool = False


class ObsidianVaultsState(BaseModel):
    """Parsed view of ``%APPDATA%\\obsidian\\obsidian.json``.

    ``config_path`` always reflects the file path we probed (even when it
    does not exist) so callers can surface it to the user. ``vaults`` is
    an ordered list reflecting the dict iteration order of the source
    JSON (insertion order in Python 3.7+).
    """

    config_path: Path
    config_exists: bool
    vaults: list[VaultEntry] = []


# ---------------------------------------------------------------------------
# Detection — Obsidian.exe install
# ---------------------------------------------------------------------------
def _candidate_install_paths(platform: str | None = None) -> list[Path]:
    """Return ordered platform-specific Obsidian install candidates.

    Order matters: per-user installs win over machine-wide installs only
    because the per-user path is the official installer default. Order is
    documented in the public docstring of ``detect_obsidian``.
    """
    plat = platform if platform is not None else sys.platform
    candidates: list[Path] = []

    if plat == "darwin":
        return [
            Path.home() / "Applications" / "Obsidian.app" / "Contents" / "MacOS" / "Obsidian",
            Path("/Applications/Obsidian.app/Contents/MacOS/Obsidian"),
        ]
    if plat != "win32":
        return [
            Path("/usr/bin/obsidian"),
            Path("/usr/local/bin/obsidian"),
            Path("/opt/Obsidian/obsidian"),
        ]

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "Programs" / "Obsidian" / "Obsidian.exe")

    program_files = os.environ.get("PROGRAMFILES")
    if program_files:
        candidates.append(Path(program_files) / "Obsidian" / "Obsidian.exe")

    # Note: env var name on Windows literally contains parentheses.
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)")
    if program_files_x86:
        candidates.append(Path(program_files_x86) / "Obsidian" / "Obsidian.exe")

    return candidates


def _probe_uninstall_registry() -> Path | None:
    """Best-effort registry probe for Obsidian's InstallLocation.

    Tries HKCU then HKLM under ``SOFTWARE\\Microsoft\\Windows\\
    CurrentVersion\\Uninstall\\Obsidian``. Reads ``InstallLocation`` and
    appends ``Obsidian.exe``. Returns ``None`` on any failure — registry
    probing must never raise from this module.
    """
    try:
        import winreg  # lazy: non-Windows hosts must still import this module
    except ImportError:
        logger.debug("winreg not available; skipping registry probe")
        return None

    subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Obsidian"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, subkey) as key:
                install_location, _ = winreg.QueryValueEx(key, "InstallLocation")
        except OSError:
            continue
        if not install_location:
            continue
        exe = Path(install_location) / "Obsidian.exe"
        if exe.exists():
            return exe
    return None


def _read_version_best_effort(exe_path: Path) -> str | None:
    """Best-effort version-string extraction from a PE file.

    Returns ``None`` if pywin32 is missing or anything goes wrong — the
    detector must never bubble a version-probe failure up to the caller.
    """
    try:
        import win32api  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("win32api not available; version unknown for %s", exe_path)
        return None

    try:
        info = win32api.GetFileVersionInfo(str(exe_path), "\\")
        ms = info["FileVersionMS"]
        ls = info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception as exc:  # noqa: BLE001 — version is best-effort
        logger.debug("FileVersionInfo failed for %s: %s", exe_path, exc)
        return None


def detect_obsidian(platform: str | None = None) -> ObsidianDetection:
    """Locate Obsidian on this machine and return install status.

    Probe order:
        1. ``%LOCALAPPDATA%\\Programs\\Obsidian\\Obsidian.exe`` (per-user)
        2. ``%PROGRAMFILES%\\Obsidian\\Obsidian.exe`` (machine-wide x64)
        3. ``%PROGRAMFILES(X86)%\\Obsidian\\Obsidian.exe`` (legacy 32-bit)
        4. Uninstall registry: ``HKCU`` then ``HKLM`` under
           ``SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Obsidian``
           — reads ``InstallLocation`` and appends ``Obsidian.exe``.

    First hit wins. If every probe misses, returns
    ``ObsidianDetection(installed=False, exe_path=None, version=None)``.
    This function NEVER raises and NEVER runs a subprocess.
    """
    plat = platform if platform is not None else sys.platform
    if plat != "win32":
        path_hit = shutil.which("obsidian")
        if path_hit:
            return ObsidianDetection(installed=True, exe_path=Path(path_hit))

    for candidate in _candidate_install_paths(platform=plat):
        if candidate.exists():
            return ObsidianDetection(
                installed=True,
                exe_path=candidate,
                version=_read_version_best_effort(candidate) if plat == "win32" else None,
            )

    registry_hit = _probe_uninstall_registry() if plat == "win32" else None
    if registry_hit is not None:
        return ObsidianDetection(
            installed=True,
            exe_path=registry_hit,
            version=_read_version_best_effort(registry_hit),
        )

    return ObsidianDetection(installed=False, exe_path=None, version=None)


# ---------------------------------------------------------------------------
# Detection — obsidian.json vault index
# ---------------------------------------------------------------------------
def _default_obsidian_config_path(platform: str | None = None) -> Path:
    """Return the canonical ``obsidian.json`` path for this OS.

    Obsidian stores its vault index in the per-user config dir:
    Windows ``%APPDATA%/obsidian``, macOS
    ``~/Library/Application Support/obsidian``, Linux
    ``$XDG_CONFIG_HOME/obsidian`` (fallback ``~/.config/obsidian``).
    ``platform`` is injectable for tests; production callers omit it.
    """
    plat = platform if platform is not None else sys.platform
    if plat == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "obsidian" / "obsidian.json"
        return Path.home() / "AppData" / "Roaming" / "obsidian" / "obsidian.json"
    if plat == "darwin":
        return (
            Path.home() / "Library" / "Application Support"
            / "obsidian" / "obsidian.json"
        )
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "obsidian" / "obsidian.json"


def read_obsidian_vaults(config_path: Path | None = None) -> ObsidianVaultsState:
    """Read Obsidian's vault index and return a typed view.

    Args:
        config_path: Optional override for the JSON file location. When
            ``None`` (the default), reads
            ``%APPDATA%\\obsidian\\obsidian.json``.

    Returns:
        ``ObsidianVaultsState`` — ``config_exists=False`` and an empty
        ``vaults`` list when the file is missing. The returned
        ``config_path`` is always populated so callers can show it to the
        user.

    Raises:
        ValueError: If the file exists but cannot be parsed as JSON. The
            exception message embeds ``"obsidian.json"`` and the original
            parse error so the wizard can surface a useful explanation.
    """
    cfg_path = config_path if config_path is not None else _default_obsidian_config_path()

    if not cfg_path.exists():
        return ObsidianVaultsState(config_path=cfg_path, config_exists=False, vaults=[])

    try:
        raw = cfg_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"obsidian.json could not be parsed: {exc}"
        ) from exc

    vaults_dict = data.get("vaults", {}) if isinstance(data, dict) else {}
    entries: list[VaultEntry] = []
    for vault_id, payload in vaults_dict.items():
        if not isinstance(payload, dict):
            # Defensive: skip malformed entries rather than failing the whole probe.
            logger.warning("Skipping malformed vault entry %s in obsidian.json", vault_id)
            continue
        path_str = payload.get("path")
        if not path_str:
            logger.warning("Vault %s has no 'path' field; skipping", vault_id)
            continue
        entries.append(
            VaultEntry(
                id=str(vault_id),
                path=Path(path_str),
                ts=int(payload.get("ts", 0) or 0),
                is_open=bool(payload.get("open", False)),
            )
        )

    return ObsidianVaultsState(
        config_path=cfg_path,
        config_exists=True,
        vaults=entries,
    )


# ---------------------------------------------------------------------------
# Membership check — is our Jarvis vault already registered?
# ---------------------------------------------------------------------------
def _normalize_for_compare(path: Path, platform: str | None = None) -> str:
    """Normalise a path using the host platform's case semantics.

    * ``Path.resolve()`` to absolutize and collapse ``..`` segments. We
      call ``resolve(strict=False)`` so non-existent paths still
      normalise (the expected vault path may not yet exist on disk).
    * Strip trailing backslashes and forward slashes — both Obsidian and
      Python sometimes emit a trailing separator on directory paths.
    * Lowercase the whole string (Windows file system is case-insensitive
      and this is a pure Windows feature).
    """
    plat = platform if platform is not None else sys.platform
    raw = str(path)
    windows_style = plat == "win32" or (
        len(raw) >= 3 and raw[1] == ":" and raw[2] in ("\\", "/")
    )
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        # resolve() can raise on weird UNC inputs — fall back to absolute().
        resolved = path.absolute()
    s = str(resolved)
    while s.endswith(("\\", "/")):
        s = s[:-1]
    return s.lower() if windows_style else s


def is_vault_registered(vaults: list[VaultEntry], expected_vault_path: Path) -> bool:
    """Return True when Obsidian can reach ``expected_vault_path``.

    An exact match is reachable, but so is a Jarvis-owned subdirectory inside
    an already-registered user vault. The latter is the shape created by the
    setup wizard's ``mode="existing"`` flow: Obsidian registers
    ``<user-vault>`` while Jarvis writes under ``<user-vault>/Jarvis``.
    """
    return find_registered_vault(vaults, expected_vault_path) is not None


def find_registered_vault(
    vaults: list[VaultEntry], expected_vault_path: Path,
) -> VaultEntry | None:
    """Return the most specific registered vault containing ``expected``.

    This mirrors Obsidian's URI ``path`` resolution rule and keeps status
    detection aligned with deep links when nested vault candidates exist.
    """
    expected_norm = _normalize_for_compare(expected_vault_path)
    matches: list[tuple[int, VaultEntry]] = []
    for entry in vaults:
        registered_norm = _normalize_for_compare(entry.path)
        try:
            contains = (
                registered_norm == expected_norm
                or os.path.commonpath((registered_norm, expected_norm)) == registered_norm
            )
        except ValueError:
            # Different Windows drives (or otherwise incompatible roots) cannot
            # contain one another.
            contains = False
        if contains:
            matches.append((len(registered_norm), entry))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


# ---------------------------------------------------------------------------
# Vault registration writer (Phase B9.3)
# ---------------------------------------------------------------------------
class RegisterResult(BaseModel):
    """Outcome of ``register_vault()``.

    ``status`` values:
        * ``"added"`` — vault was newly registered (or would have been, in
          dry-run mode). ``vault_uuid`` is populated; ``backup_path`` is
          populated unless this was a dry-run.
        * ``"already_registered"`` — the vault was already present in
          ``obsidian.json``; no write happened.
        * ``"config_missing"`` — ``obsidian.json`` does not exist. The
          caller should ask the user to start Obsidian once first.
        * ``"rolled_back"`` — write attempt failed (pre-validate, post-
          write verification, or unexpected exception). The original
          ``obsidian.json`` was restored from backup when possible.
          ``error`` carries a human-readable explanation.
    """

    status: Literal["added", "already_registered", "config_missing", "rolled_back"]
    vault_uuid: str | None = None
    backup_path: Path | None = None
    error: str | None = None


def _next_backup_path(config_path: Path) -> Path:
    """Return a free backup path next to ``config_path``.

    Format: ``<config_name>.b9-backup-YYYYMMDD-HHMMSS``. If that exact
    name already exists, append ``-1``, ``-2``, … until a free slot is
    found. The datetime import is deferred so module import stays cheap.
    """
    from datetime import datetime

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = config_path.with_name(f"{config_path.name}.b9-backup-{ts}")
    if not base.exists():
        return base
    counter = 1
    while True:
        candidate = base.with_name(f"{base.name}-{counter}")
        if not candidate.exists():
            return candidate
        counter += 1


def register_vault(
    vault_path: Path,
    *,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> RegisterResult:
    """Register ``vault_path`` in Obsidian's vault index (atomic + backed up).

    Pipeline (every step is mandatory — no shortcuts):

    1. Default ``config_path`` to ``%APPDATA%\\obsidian\\obsidian.json``.
    2. If the file does not exist → return ``status="config_missing"``.
    3. Parse the JSON via :func:`read_obsidian_vaults`. On parse error
       return ``status="rolled_back"`` with the error message.
    4. If the vault is already registered → return
       ``status="already_registered"`` without writing.
    5. On ``dry_run=True`` short-circuit with ``status="added"`` (no UUID
       persisted, no disk touched).
    6. Generate a 16-char hex UUID (Obsidian's format) via
       :func:`secrets.token_hex`.
    7. Compute a timestamped backup name and copy the original aside.
    8. Build a new top-level dict that preserves unknown keys and inserts
       the new vault entry into ``data["vaults"]``.
    9. Atomic write: tempfile + ``flush + fsync`` + :func:`os.replace`.
    10. Post-write verification: re-read and check membership. On failure
        restore from backup and return ``status="rolled_back"``.
    11. Success → ``status="added"`` with UUID + backup path.

    Any exception raised between step 6 and step 10 triggers an attempt
    to restore from backup and a ``status="rolled_back"`` result. The
    tempfile is cleaned up in ``finally`` if it still exists.

    Args:
        vault_path: Absolute path to the vault directory to register.
        config_path: Optional override for ``obsidian.json``. Defaults to
            ``%APPDATA%\\obsidian\\obsidian.json``.
        dry_run: When True, the pipeline runs through validation but no
            disk mutation happens.

    Returns:
        :class:`RegisterResult` — the caller MUST inspect ``status``.
    """
    cfg_path = config_path if config_path is not None else _default_obsidian_config_path()

    # Step 2: config presence gate.
    if not cfg_path.exists():
        return RegisterResult(status="config_missing")

    # Step 3: parse existing state.
    try:
        state = read_obsidian_vaults(config_path=cfg_path)
    except ValueError as exc:
        return RegisterResult(status="rolled_back", error=str(exc))

    # Step 4: idempotency check.
    if is_vault_registered(state.vaults, vault_path):
        return RegisterResult(status="already_registered")

    # Step 5: dry-run short-circuit. Generate a "preview" UUID but do
    # not persist it; the caller can confirm intent without disk churn.
    if dry_run:
        return RegisterResult(status="added", vault_uuid=secrets.token_hex(8))

    # Step 6: generate the persistent UUID.
    new_uuid = secrets.token_hex(8)

    backup_path: Path | None = None
    tempfile_path: Path | None = None
    try:
        # Re-load the raw dict so unknown top-level keys (settings, etc.)
        # survive the round-trip — Pydantic would drop them.
        raw_text = cfg_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
        if not isinstance(data, dict):
            return RegisterResult(
                status="rolled_back",
                error="obsidian.json top-level is not a JSON object",
            )

        # Step 7: backup before any mutation.
        backup_path = _next_backup_path(cfg_path)
        shutil.copy2(cfg_path, backup_path)

        # Step 8: build the new tree.
        vaults_dict = data.setdefault("vaults", {})
        if not isinstance(vaults_dict, dict):
            raise ValueError("obsidian.json 'vaults' key is not an object")
        now_ms = int(time.time() * 1000)
        vaults_dict[new_uuid] = {
            "path": str(vault_path.resolve()),
            "ts": now_ms,
            "open": False,
        }

        # Step 9: atomic write to a sibling tempfile.
        tempfile_path = cfg_path.with_suffix(f".json.tmp-{secrets.token_hex(4)}")
        with open(tempfile_path, "w", encoding="utf-8", newline="") as fp:
            json.dump(data, fp, indent=2, ensure_ascii=False)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tempfile_path, cfg_path)
        tempfile_path = None  # successfully consumed

        # Step 10: post-write verification — re-read and assert membership.
        verify_state = read_obsidian_vaults(config_path=cfg_path)
        if not is_vault_registered(verify_state.vaults, vault_path):
            # Restore from backup and surface the failure.
            shutil.copy2(backup_path, cfg_path)
            try:
                backup_path.unlink()
            except OSError:
                logger.debug("Could not delete backup after verification failure: %s", backup_path)
            return RegisterResult(
                status="rolled_back",
                error="post-write verification failed",
            )

        # Step 11: success.
        return RegisterResult(
            status="added",
            vault_uuid=new_uuid,
            backup_path=backup_path,
        )

    except Exception as exc:  # noqa: BLE001 — we must always return a result
        # Best-effort restore: if we already wrote the backup, copy it back.
        if backup_path is not None and backup_path.exists():
            try:
                shutil.copy2(backup_path, cfg_path)
            except OSError as restore_exc:
                logger.error(
                    "Restore from backup failed for %s: %s",
                    cfg_path,
                    restore_exc,
                )
        return RegisterResult(status="rolled_back", error=str(exc))

    finally:
        # Tempfile cleanup — only if it still exists (i.e. os.replace didn't run).
        if tempfile_path is not None and tempfile_path.exists():
            try:
                tempfile_path.unlink()
            except OSError:
                logger.debug("Could not clean up tempfile: %s", tempfile_path)
