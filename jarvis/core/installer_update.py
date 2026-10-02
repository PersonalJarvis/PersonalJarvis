"""Self-update for a FROZEN install — the one that arrived as a native installer.

A frozen install (Windows ``Setup.exe``, macOS ``.dmg``, Linux ``.AppImage``)
has no git checkout, so ``jarvis/ui/web/update_routes.py``'s managed path cannot
update it. What it does have is a GitHub Release carrying exactly one installer
asset per platform plus a ``installers-SHA256SUMS.txt`` manifest:

* ``PersonalJarvis-Setup-x64.exe``          Windows 10/11 x64
* ``PersonalJarvis-macOS-arm64.dmg``        Apple Silicon
* ``PersonalJarvis-macOS-x64.dmg``          Intel Mac
* ``PersonalJarvis-Linux-x86_64.AppImage``  Linux x86_64

This module resolves which of those belongs to the running machine, downloads
it, proves it byte-for-byte against the checksum manifest of the SAME release,
and then hands control to the platform's own upgrade mechanism.

Two rules run through everything here:

* **Fail closed.** A missing asset, a missing or unparsable checksum manifest, a
  digest that does not match, an oversized body, an unexpected redirect target —
  every one of them raises :class:`InstallerUpdateError` and installs nothing.
  Executing an unverified binary is the single worst thing an updater can do.
* **Injectable edges.** Network and process spawning arrive as small protocols
  (:class:`AssetFetcher`, :class:`CommandRunner`), so the whole flow — including
  the macOS and Linux handovers — is exercised on any OS with fakes and without
  a network.

Every subprocess goes out with ``NO_WINDOW_CREATIONFLAGS`` (AP-1) so a windowed
build never flashes a console.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import platform as platform_module
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from jarvis.core.branding import UPDATER_USER_AGENT
from jarvis.core.frozen import bundle_root, is_frozen
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

__all__ = [
    "CHECKSUMS_ASSET_NAME",
    "AssetFetcher",
    "CommandRunner",
    "DownloadProgress",
    "HttpxAssetFetcher",
    "InstallerAsset",
    "InstallerUpdateError",
    "SubprocessCommandRunner",
    "apply_installer",
    "download_and_verify",
    "handover_env",
    "installer_asset_name",
    "machine_for_update",
    "parse_sha256sums",
    "select_asset",
    "select_installer_asset",
    "sweep_stale_downloads",
    "update_blocker",
]

# The checksum manifest published alongside the installers. Its lines are plain
# ``sha256sum`` output, so `sha256sum -c` verifies the same file by hand.
CHECKSUMS_ASSET_NAME = "installers-SHA256SUMS.txt"

# A whole onedir bundle compresses to a few hundred MB. The cap exists to stop a
# hostile or broken endpoint from filling the disk, not to be a tight budget.
MAX_INSTALLER_BYTES = 1_500_000_000
# The manifest is a handful of lines; anything larger is not the manifest.
MAX_CHECKSUMS_BYTES = 256_000

# Whole-download budget. Generous on purpose: a 400 MB installer over a slow
# connection is normal, a download that never ends is not.
DOWNLOAD_TIMEOUT_S = 1800.0
CONNECT_TIMEOUT_S = 15.0
READ_TIMEOUT_S = 120.0

_SHA256_LENGTH = 64


class InstallerUpdateError(RuntimeError):
    """A frozen update could not be completed. The message is user-facing."""


#: Called with ``(bytes_written, total_bytes_or_None)`` as the download streams.
#: ``total`` is the release asset's published size when the caller knows it and
#: the server confirms it, and ``None`` when it does not — a progress bar built
#: on this must therefore survive an unknown total rather than assume one.
#: Never raises into the download: :func:`_report` swallows a bad callback.
DownloadProgress = Callable[[int, int | None], None]


def _report(on_progress: DownloadProgress | None, written: int, total: int | None) -> None:
    """Deliver one progress tick without ever endangering the download.

    A progress consumer is cosmetic; the download is not. A callback that raises
    (a UI store torn down mid-flight, a bad lambda) must not abort an otherwise
    healthy 400 MB transfer, so its exception is logged and dropped here.
    """
    if on_progress is None:
        return
    try:
        on_progress(written, total)
    except Exception:  # noqa: BLE001 - a cosmetic callback never fails the update
        log.debug("[update] progress callback raised; continuing the download")


@dataclass(frozen=True)
class InstallerAsset:
    """One downloadable file on a GitHub Release."""

    name: str
    url: str
    size: int

    def as_dict(self) -> dict[str, Any]:
        """The shape the update status route publishes to the UI."""
        return {"name": self.name, "url": self.url, "size": self.size}


# --------------------------------------------------------------------------- #
# Asset selection
# --------------------------------------------------------------------------- #
def installer_asset_name(platform_name: str, machine: str) -> str | None:
    """The release asset this OS/CPU pair installs, or ``None`` when unsupported.

    ``machine`` is compared case-insensitively because the same CPU reports
    different spellings per OS: Windows says ``AMD64``, Linux says ``x86_64``,
    macOS says ``arm64`` while ``platform.machine()`` under Rosetta says
    ``x86_64``. An unrecognised pair returns ``None`` — the caller then reports
    "no installer for this platform" instead of guessing.
    """
    arch = machine.strip().lower()
    intel64 = {"x86_64", "amd64", "x64"}

    if platform_name == "win32":
        if arch in intel64:
            return "PersonalJarvis-Setup-x64.exe"
        return None
    if platform_name == "darwin":
        if arch in {"arm64", "aarch64"}:
            return "PersonalJarvis-macOS-arm64.dmg"
        if arch in intel64:
            return "PersonalJarvis-macOS-x64.dmg"
        return None
    if platform_name.startswith("linux"):
        if arch in intel64:
            return "PersonalJarvis-Linux-x86_64.AppImage"
        return None
    return None


def _running_under_rosetta() -> bool:
    """True when this process is an x86_64 build translated by Rosetta 2.

    ``sysctl.proc_translated`` is 1 for a translated process, 0 for a native
    one, and absent on an Intel Mac (the call then fails, which reads as
    "not translated"). Read through ``sysctlbyname`` so the status poll spawns
    no process.
    """
    if sys.platform != "darwin":
        return False
    try:
        import ctypes

        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        value = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(value))
        rc = libc.sysctlbyname(
            b"sysctl.proc_translated", ctypes.byref(value), ctypes.byref(size), None, 0
        )
    except (OSError, AttributeError) as exc:
        log.debug("[update] could not ask whether Rosetta translates this process: %s", exc)
        return False
    return rc == 0 and value.value == 1


def machine_for_update(
    platform_name: str | None = None,
    machine: str | None = None,
    *,
    translated: Callable[[], bool] = _running_under_rosetta,
) -> str:
    """The CPU whose installer this machine should receive.

    Normally ``platform.machine()``. The exception is an Intel build running on
    Apple Silicon: Rosetta makes it report ``x86_64``, so it would keep
    updating to the Intel DMG forever and never leave emulation. Asking the
    kernel whether this process is translated moves it to the native arm64
    build on its next update instead.
    """
    active_platform = sys.platform if platform_name is None else platform_name
    active_machine = platform_module.machine() if machine is None else machine
    if (
        active_platform == "darwin"
        and active_machine.strip().lower() in {"x86_64", "amd64"}
        and translated()
    ):
        return "arm64"
    return active_machine


def select_asset(assets: Iterable[Any], name: str) -> InstallerAsset | None:
    """Find one asset by exact name in a GitHub Release ``assets`` array.

    Accepts the raw JSON list so callers do not have to pre-shape it. Entries
    that are not objects, or that lack a usable download URL, are skipped rather
    than crashing the status poll on a malformed release.
    """
    for entry in assets:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name") or "") != name:
            continue
        url = str(entry.get("browser_download_url") or "")
        if not url.startswith("https://"):
            # A non-HTTPS or missing URL is not something to "fix" by guessing.
            log.warning("[update] release asset %s has no usable https URL", name)
            return None
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            # Size is display-only. A release asset whose size GitHub reports
            # oddly still downloads, so an unreadable one reads as unknown
            # rather than failing the update.
            size = 0
        return InstallerAsset(name=name, url=url, size=size)
    return None


def select_installer_asset(
    assets: Iterable[Any],
    *,
    platform_name: str | None = None,
    machine: str | None = None,
) -> InstallerAsset | None:
    """The installer asset for the running machine, or ``None``."""
    active_platform = sys.platform if platform_name is None else platform_name
    name = installer_asset_name(
        active_platform,
        machine_for_update(active_platform) if machine is None else machine,
    )
    if name is None:
        return None
    return select_asset(assets, name)


# --------------------------------------------------------------------------- #
# Checksum manifest
# --------------------------------------------------------------------------- #
def parse_sha256sums(text: str) -> dict[str, str]:
    """Parse ``sha256sum`` output into ``{file name: lowercase digest}``.

    Handles both the binary (``digest *name``) and text (``digest  name``)
    markers and ignores blank or malformed lines, because one bad line must not
    hide the digest of the file we actually need.
    """
    digests: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].strip().lower(), parts[1].strip()
        if len(digest) != _SHA256_LENGTH:
            continue
        try:
            int(digest, 16)
        except ValueError:
            # Not a hex digest, so not a checksum line. SHA256SUMS files carry
            # headers and blank lines too; skipping a non-entry is parsing,
            # not swallowing a failure.
            continue
        if name.startswith("*"):
            name = name[1:]
        # Release manifests are flat, but a path-prefixed entry must still match
        # by file name rather than being dropped.
        name = name.replace("\\", "/").rsplit("/", 1)[-1]
        if name:
            digests[name] = digest
    return digests


# --------------------------------------------------------------------------- #
# Network edge
# --------------------------------------------------------------------------- #
def _content_length(raw: str | None) -> int | None:
    """The response's byte total, or ``None`` when the server did not state one."""
    try:
        total = int(str(raw))
    except (TypeError, ValueError):
        # Absent or malformed: the caller falls back to the release metadata,
        # and failing that shows an indeterminate bar. Not an error.
        return None
    return total if total > 0 else None


class AssetFetcher(Protocol):
    """The only network this module performs. Faked wholesale in tests."""

    async def get_text(self, url: str, *, max_bytes: int) -> str:
        """Fetch a small text body (the checksum manifest)."""

    async def download(
        self,
        url: str,
        dest: Path,
        *,
        max_bytes: int,
        on_progress: DownloadProgress | None = None,
    ) -> int:
        """Stream ``url`` into ``dest``; return the byte count written.

        ``on_progress`` is called as the bytes arrive so a caller can show a
        real percentage. It is optional in the protocol so an implementation
        that cannot report progress stays valid.
        """


class HttpxAssetFetcher:
    """Default :class:`AssetFetcher` on top of httpx.

    ``httpx`` is imported lazily: the base install must boot on a host where the
    updater never runs, and this module sits on an import path the desktop app
    touches at startup (AP-26).

    GitHub answers an asset URL with a redirect to its download CDN, so
    redirects are followed — but every hop, the first request included, must
    stay on ``https``. A hop to plain ``http`` is refused before it is sent.

    ``transport`` exists for tests (an ``httpx.MockTransport``); production
    leaves it ``None`` and gets httpx's default network transport.
    """

    def __init__(self, *, transport: Any | None = None) -> None:
        self._transport = transport

    def _client(self) -> Any:
        import httpx

        return httpx.AsyncClient(
            timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            follow_redirects=True,
            transport=self._transport,
            event_hooks={"request": [_require_https]},
        )

    async def get_text(self, url: str, *, max_bytes: int) -> str:
        body = bytearray()
        async with self._client() as client:
            async with client.stream("GET", url, headers=self._headers()) as response:
                response.raise_for_status()
                # Streamed with the cap applied per chunk: reading the whole
                # body first would let a hostile endpoint fill memory before
                # the size check ever ran.
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise InstallerUpdateError(
                            f"checksum manifest is larger than {max_bytes} bytes — refusing it"
                        )
        return bytes(body).decode("utf-8", errors="replace")

    async def download(
        self,
        url: str,
        dest: Path,
        *,
        max_bytes: int,
        on_progress: DownloadProgress | None = None,
    ) -> int:
        written = 0
        async with self._client() as client:
            async with client.stream("GET", url, headers=self._headers()) as response:
                response.raise_for_status()
                # A GitHub asset redirect always answers with a Content-Length,
                # but a proxy or a chunked transfer need not: an unparsable or
                # absent header means "total unknown", never a guessed size.
                total = _content_length(response.headers.get("content-length"))
                _report(on_progress, 0, total)
                with dest.open("wb") as handle:
                    async for chunk in response.aiter_bytes(1024 * 256):
                        written += len(chunk)
                        if written > max_bytes:
                            raise InstallerUpdateError(
                                f"installer download exceeded {max_bytes} bytes — refusing it"
                            )
                        handle.write(chunk)
                        _report(on_progress, written, total)
        return written

    @staticmethod
    def _headers() -> dict[str, str]:
        return {"User-Agent": UPDATER_USER_AGENT, "Accept": "application/octet-stream"}


async def _require_https(request: Any) -> None:
    """httpx request hook: refuse any hop that is not ``https``.

    Runs for the first request and for every redirect httpx follows, so a
    redirect chain that ends on plain ``http`` never sends that request.
    """
    if request.url.scheme != "https":
        raise InstallerUpdateError(
            f"refusing a non-https download hop to {request.url.host or 'an unknown host'}"
        )


async def download_and_verify(
    asset: InstallerAsset,
    checksums: InstallerAsset,
    *,
    dest_dir: Path,
    fetcher: AssetFetcher | None = None,
    max_bytes: int = MAX_INSTALLER_BYTES,
    on_progress: DownloadProgress | None = None,
    download_timeout_s: float = DOWNLOAD_TIMEOUT_S,
) -> Path:
    """Download ``asset`` and prove it against ``checksums``. Fail closed.

    ``checksums`` must come from the SAME release as ``asset`` — verifying
    against another release's manifest would authorise the wrong bytes. The
    caller (``update_routes``) reads both out of one release payload.

    ``on_progress`` receives ``(written, total)`` while the installer streams in.
    ``total`` falls back to the release metadata's ``asset.size`` when the server
    states no Content-Length, and stays ``None`` when neither source knows it.

    ``download_timeout_s`` bounds the WHOLE transfer, not one read: httpx's
    read timeout restarts with every chunk, so a server trickling one byte a
    minute would otherwise hold the update — and the apply lock with it —
    forever, leaving the button stuck at the same percentage until a restart.

    Returns the path of the verified file inside ``dest_dir``. Raises
    :class:`InstallerUpdateError` on every failure, leaving nothing executable
    behind.
    """
    active = HttpxAssetFetcher() if fetcher is None else fetcher

    def tick(written: int, total: int | None) -> None:
        _report(on_progress, written, total or (asset.size or None))

    if asset.size and asset.size > max_bytes:
        raise InstallerUpdateError(
            f"{asset.name} is {asset.size} bytes, over the {max_bytes} byte cap"
        )

    try:
        manifest_text = await asyncio.wait_for(
            active.get_text(checksums.url, max_bytes=MAX_CHECKSUMS_BYTES),
            timeout=READ_TIMEOUT_S,
        )
    except InstallerUpdateError:
        raise
    except TimeoutError as exc:
        raise InstallerUpdateError(
            f"{CHECKSUMS_ASSET_NAME} did not arrive within {READ_TIMEOUT_S:.0f}s"
        ) from exc
    except Exception as exc:  # noqa: BLE001 — every transport error is the same refusal
        raise InstallerUpdateError(f"could not download {CHECKSUMS_ASSET_NAME}: {exc}") from exc

    digests = parse_sha256sums(manifest_text)
    expected = digests.get(asset.name)
    if not expected:
        raise InstallerUpdateError(
            f"{CHECKSUMS_ASSET_NAME} of this release has no entry for {asset.name} — "
            "refusing to install an unverified file"
        )

    target = _prepare_target(dest_dir, asset.name)
    try:
        written = await asyncio.wait_for(
            active.download(asset.url, target, max_bytes=max_bytes, on_progress=tick),
            timeout=download_timeout_s,
        )
    except InstallerUpdateError:
        _discard(target)
        raise
    except TimeoutError as exc:
        _discard(target)
        raise InstallerUpdateError(
            f"{asset.name} did not finish downloading within "
            f"{download_timeout_s / 60:.0f} minutes — the connection is too slow or stalled"
        ) from exc
    except Exception as exc:  # noqa: BLE001 — same refusal for any transport error
        _discard(target)
        raise InstallerUpdateError(f"could not download {asset.name}: {exc}") from exc

    # Hashing several hundred MB takes seconds of pure CPU. On the event loop
    # that freezes every other request — including the progress poll this very
    # download is feeding — so it goes to a thread.
    actual = await asyncio.to_thread(_sha256_file, target)
    if actual != expected:
        _discard(target)
        raise InstallerUpdateError(
            f"{asset.name} failed its SHA-256 check (expected {expected}, got "
            f"{actual}) — the download was NOT installed"
        )

    log.info("[update] verified %s (%d bytes, sha256 %s)", asset.name, written, actual)
    return target


def _prepare_target(dest_dir: Path, name: str) -> Path:
    """Create the download directory and return the file path inside it.

    Split out of the async caller so the blocking filesystem call does not sit
    on the event loop (ruff ASYNC240); it is a single mkdir, so a thread would
    cost more than it saves.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir / name


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _discard(path: Path) -> None:
    """Remove a partial or rejected download so nothing unverified survives."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        # Worth a line: a leftover rejected installer in the temp directory is
        # harmless on its own but confusing in a support log.
        log.warning("[update] could not remove %s: %s", path, exc)


# --------------------------------------------------------------------------- #
# Process edge
# --------------------------------------------------------------------------- #
class CommandRunner(Protocol):
    """Spawning, isolated so the handovers are testable without side effects."""

    def run(self, command: Sequence[str], *, timeout_s: float) -> tuple[int, str, str]:
        """Run to completion; return ``(returncode, stdout, stderr)``."""

    def spawn_detached(self, command: Sequence[str]) -> None:
        """Start ``command`` and return immediately, outliving this process."""


def handover_env(
    base: dict[str, str] | None = None, *, frozen: bool | None = None
) -> dict[str, str]:
    """Environment for every process the handover starts.

    The installer, ``hdiutil`` and the relaunch waiter are spawned by a
    PyInstaller build, and they — or the new version they start — inherit its
    environment. Two parts of that environment belong to the OLD process only:

    * PyInstaller's own bookkeeping (``_PYI_*``). The new version's bootloader
      would read it as "I am a child of that build" — on Windows the install
      path is identical, so the match is exact. ``PYINSTALLER_RESET_ENVIRONMENT``
      (PyInstaller >= 6.10) tells it to start as a fresh top-level process.
    * ``LD_LIBRARY_PATH`` on Linux, which the bootloader pointed into the old
      AppImage's private mount. PyInstaller keeps the user's value in
      ``LD_LIBRARY_PATH_ORIG``; that is what a system program must see, and
      the old mount is gone by the time the new AppImage starts anyway.

    Outside a frozen build nothing was rewritten, so only the reset flag is
    added and the library path is left exactly as the user set it.
    """
    env = dict(os.environ if base is None else base)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if not (is_frozen() if frozen is None else frozen):
        return env
    for var in ("LD_LIBRARY_PATH", "LIBPATH"):
        original = env.pop(f"{var}_ORIG", None)
        if original is not None:
            env[var] = original
        else:
            env.pop(var, None)
    return env


class SubprocessCommandRunner:
    """Default :class:`CommandRunner`. Every spawn is console-free (AP-1)."""

    def run(self, command: Sequence[str], *, timeout_s: float) -> tuple[int, str, str]:
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                list(command),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
                stdin=subprocess.DEVNULL,
                env=handover_env(),
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except FileNotFoundError as exc:  # returned to the caller, not swallowed
            return -1, "", f"{command[0]} is not available: {exc}"
        except subprocess.TimeoutExpired:  # returned to the caller, not swallowed
            return -1, "", f"{command[0]} timed out after {timeout_s:.0f}s"
        return completed.returncode, completed.stdout or "", completed.stderr or ""

    def spawn_detached(self, command: Sequence[str]) -> None:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
            "env": handover_env(),
        }
        if sys.platform == "win32":
            detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            kwargs["creationflags"] = NO_WINDOW_CREATIONFLAGS | detached | new_group
        else:
            # Leave the process group so the handover survives this app exiting.
            kwargs["start_new_session"] = True
        subprocess.Popen(list(command), **kwargs)  # noqa: S603 - fixed argv, no shell


# --------------------------------------------------------------------------- #
# Platform handovers
# --------------------------------------------------------------------------- #
def apply_installer(
    installer: Path,
    *,
    platform_name: str | None = None,
    runner: CommandRunner | None = None,
    app_path: Path | None = None,
    appimage_path: Path | None = None,
    relaunch: bool = True,
    wait_for_pid: int | None = None,
    installer_log: Path | None = None,
) -> str:
    """Install the verified ``installer`` and hand control to the new version.

    Returns a one-line, user-facing description of what was handed over. Raises
    :class:`InstallerUpdateError` when the platform is unsupported or the
    handover could not be completed — the running app then keeps working on the
    old version, which is always better than a half-replaced install.

    The caller must QUIT the running app once this returns. The new version is
    started only after ``wait_for_pid`` (default: this process) has exited:
    started any earlier, it would find the single-instance lock still held,
    focus the old window and exit — leaving the user on the old version.
    """
    active_platform = sys.platform if platform_name is None else platform_name
    active_runner = SubprocessCommandRunner() if runner is None else runner
    old_pid = os.getpid() if wait_for_pid is None else wait_for_pid

    if not installer.is_file():
        raise InstallerUpdateError(f"{installer} does not exist")

    if active_platform == "win32":
        return _handover_windows(
            installer, runner=active_runner, wait_for_pid=old_pid, log_path=installer_log
        )
    if active_platform == "darwin":
        return _handover_macos(
            installer,
            runner=active_runner,
            app_path=app_path if app_path is not None else _running_macos_app(),
            relaunch=relaunch,
            wait_for_pid=old_pid,
        )
    if active_platform.startswith("linux"):
        return _handover_linux(
            installer,
            runner=active_runner,
            appimage_path=(appimage_path if appimage_path is not None else _running_appimage()),
            relaunch=relaunch,
            wait_for_pid=old_pid,
        )
    raise InstallerUpdateError(f"no installer handover exists for platform {active_platform!r}")


# The Inno Setup parameter that tells the installer to start the app again
# once the files are in place (see the RelaunchRequested check in
# packaging/windows/PersonalJarvis.iss).
WINDOWS_RELAUNCH_PARAM = "/RELAUNCH=1"
# The Inno Setup parameter naming the process the installer must outlive (see
# InitializeSetup in packaging/windows/PersonalJarvis.iss).
WINDOWS_WAIT_PID_PARAM = "/WAITPID"
# Where the silent installer writes its log. A silent upgrade that fails shows
# nothing at all, so this file is the only trace of what went wrong.
WINDOWS_INSTALLER_LOG_NAME = "update-installer.log"


def _windows_installer_log() -> Path | None:
    """The installer log path in the user's log directory, or ``None``."""
    try:
        from jarvis.core.paths import user_logs_dir

        logs = user_logs_dir()
        logs.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # The update still works without a log; it is only harder to debug.
        log.warning("[update] no log directory for the installer: %s", exc)
        return None
    return logs / WINDOWS_INSTALLER_LOG_NAME


def _handover_windows(
    installer: Path,
    *,
    runner: CommandRunner,
    wait_for_pid: int,
    log_path: Path | None = None,
) -> str:
    """Start the Inno Setup wizard silently and let it replace the running app.

    The app quits right after this returns. ``/WAITPID`` makes the installer
    wait until this process has actually exited before it touches a file: a
    shutdown that takes longer than Setup's start-up would otherwise meet
    files still in use, and a silent install answers that with Abort — the
    app gone, nothing installed, nothing relaunched. ``/CLOSEAPPLICATIONS``
    stays as the fallback for anything else holding a file.
    ``/SUPPRESSMSGBOXES`` keeps a silent upgrade from stopping on a dialog
    nobody asked for, and ``/NORESTART`` guarantees the machine is never
    rebooted behind the user's back.

    The app comes back through ``/RELAUNCH=1``, which the installer's own
    ``[Run]`` entry honours. ``/RESTARTAPPLICATIONS`` is deliberately absent: it
    only restarts apps registered with ``RegisterApplicationRestart``, which
    this one is not, so it promised a relaunch that never happened.
    """
    command = [
        str(installer),
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/CLOSEAPPLICATIONS",
        "/NORESTART",
        WINDOWS_RELAUNCH_PARAM,
        f"{WINDOWS_WAIT_PID_PARAM}={wait_for_pid}",
    ]
    log_file = _windows_installer_log() if log_path is None else log_path
    if log_file is not None:
        command.append(f"/LOG={log_file}")
    try:
        runner.spawn_detached(command)
    except OSError as exc:
        raise InstallerUpdateError(f"could not start {installer.name}: {exc}") from exc
    log.info("[update] handed over to %s (log: %s)", installer.name, log_file)
    return "the Windows installer is running; Personal Jarvis restarts by itself"


# Absolute, so the relaunch never depends on the PATH a Finder-started app
# inherits — the same binary the restart helper uses.
MACOS_OPEN = "/usr/bin/open"


def _is_mounted(path: Path) -> bool:
    """Whether a volume is mounted at ``path`` (a seam for the tests)."""
    return os.path.ismount(path)


def _can_write(folder: Path) -> bool:
    """Whether this process may create and rename entries in ``folder``."""
    return os.access(folder, os.W_OK)


def _remove_empty_mountpoint(mountpoint: Path) -> None:
    """Delete the temporary mountpoint directory — only if it is empty.

    ``rmdir`` instead of ``rmtree``: when a detach failed the volume is still
    mounted there, and walking into it to "clean up" is never what we want.
    """
    try:
        mountpoint.rmdir()
    except OSError as exc:
        log.warning("[update] left %s in place: %s", mountpoint, exc)


def _handover_macos(
    dmg: Path,
    *,
    runner: CommandRunner,
    app_path: Path | None,
    relaunch: bool,
    wait_for_pid: int,
) -> str:
    """Mount the DMG, swap the running ``.app`` for the one inside, relaunch."""
    if app_path is None:
        raise InstallerUpdateError(
            "could not locate the running Personal Jarvis.app — refusing to update"
        )

    mountpoint = Path(tempfile.mkdtemp(prefix="jarvis-dmg-"))
    attached = False
    try:
        rc, _out, err = runner.run(
            [
                "hdiutil",
                "attach",
                str(dmg),
                "-nobrowse",
                "-readonly",
                "-mountpoint",
                str(mountpoint),
            ],
            timeout_s=180.0,
        )
        if rc != 0:
            raise InstallerUpdateError(
                f"could not mount {dmg.name}: {err.strip()[:200] or 'hdiutil failed'}"
            )
        attached = True

        source = mountpoint / app_path.name
        if not source.is_dir():
            candidates = [p for p in mountpoint.glob("*.app") if p.is_dir()]
            if len(candidates) != 1:
                raise InstallerUpdateError(
                    f"{dmg.name} does not contain exactly one application bundle"
                )
            source = candidates[0]

        _replace_directory(source, app_path)
    finally:
        # ``attached`` misses one case: hdiutil mounted the image and then
        # reported failure (or timed out after mounting). The mountpoint is the
        # proof either way, so a mounted one is always released.
        if attached or _is_mounted(mountpoint):
            rc, _out, err = runner.run(
                ["hdiutil", "detach", str(mountpoint), "-force"], timeout_s=120.0
            )
            if rc != 0:
                # Not fatal — the swap already happened. Say it so a stuck mount
                # in a support log has an explanation.
                log.warning("[update] could not detach %s: %s", mountpoint, err.strip())
        _remove_empty_mountpoint(mountpoint)

    if relaunch:
        try:
            runner.spawn_detached(
                relaunch_after_exit_command(wait_for_pid, [MACOS_OPEN, "-n", str(app_path)])
            )
        except OSError as exc:
            raise InstallerUpdateError(
                f"the update was installed but could not be relaunched: {exc}"
            ) from exc
    log.info("[update] replaced %s from %s", app_path, dmg.name)
    return f"{app_path.name} was replaced; it restarts as soon as this window closes"


def _handover_linux(
    downloaded: Path,
    *,
    runner: CommandRunner,
    appimage_path: Path | None,
    relaunch: bool,
    wait_for_pid: int,
) -> str:
    """Atomically swap the running AppImage for the downloaded one, relaunch."""
    if appimage_path is None:
        raise InstallerUpdateError(
            "this process is not running from an AppImage ($APPIMAGE is unset) — refusing to update"
        )

    staged = appimage_path.with_name(f".{appimage_path.name}.new")
    try:
        # Same directory, so os.replace below is a real atomic rename rather
        # than a cross-device copy that can leave a half-written binary.
        shutil.copyfile(downloaded, staged)
        # An AppImage that is not executable cannot be launched at all, and it
        # is the user's own file in their own directory. 0o755 is the mode the
        # AppImage project itself documents.
        os.chmod(staged, 0o755)  # noqa: S103
        os.replace(staged, appimage_path)
    except OSError as exc:
        try:
            staged.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            log.warning("[update] could not remove %s: %s", staged, cleanup_exc)
        raise InstallerUpdateError(f"could not replace {appimage_path}: {exc}") from exc

    if relaunch:
        try:
            runner.spawn_detached(relaunch_after_exit_command(wait_for_pid, [str(appimage_path)]))
        except OSError as exc:
            raise InstallerUpdateError(
                f"the update was installed but could not be relaunched: {exc}"
            ) from exc
    log.info("[update] replaced %s", appimage_path)
    return f"{appimage_path.name} was replaced; it restarts as soon as this window closes"


# How long the relaunch waiter gives the old app to exit (240 x 0.5 s). Past
# that it launches anyway: the new instance then finds the old one still
# holding the lock and simply focuses it, which is harmless.
RELAUNCH_WAIT_TICKS = 240

# POSIX sh, so it exists on every macOS and Linux box — including a frozen
# build that ships no Python of its own outside the bundle. ``$1`` is the PID
# to outlive, the rest is the command to exec.
_WAIT_THEN_EXEC = (
    'pid="$1"; shift; n=0; '
    f'while kill -0 "$pid" 2>/dev/null && [ "$n" -lt {RELAUNCH_WAIT_TICKS} ]; '
    "do sleep 0.5; n=$((n+1)); done; "
    'exec "$@"'
)


def relaunch_after_exit_command(pid: int, command: Sequence[str]) -> list[str]:
    """Argv that waits for ``pid`` to exit, then execs ``command`` (POSIX only).

    Spawned detached right before the app quits. The single-instance lock only
    frees once the old process is gone, so the new version must not start a
    moment earlier — otherwise it focuses the dying window and exits.
    """
    return ["/bin/sh", "-c", _WAIT_THEN_EXEC, "jarvis-relaunch", str(pid), *command]


def _replace_directory(source: Path, target: Path) -> None:
    """Put ``source`` where ``target`` is, keeping a rollback until it succeeds.

    ``os.replace`` refuses a non-empty destination directory, so the swap is two
    renames on the same filesystem: move the live bundle aside, move the new one
    in, then delete the old one. If the second rename fails the original is put
    straight back, so the app is never left missing.
    """
    staging = target.with_name(f".{target.name}.new")
    previous = target.with_name(f".{target.name}.old")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(previous, ignore_errors=True)

    try:
        shutil.copytree(source, staging, symlinks=True)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise InstallerUpdateError(f"could not stage {target.name}: {exc}") from exc

    moved_aside = False
    try:
        if target.exists():
            os.replace(target, previous)
            moved_aside = True
        os.replace(staging, target)
    except OSError as exc:
        if moved_aside:
            try:
                os.replace(previous, target)
            except OSError as rollback_exc:
                raise InstallerUpdateError(
                    f"{target.name} could not be restored after a failed update: {rollback_exc}"
                ) from rollback_exc
        shutil.rmtree(staging, ignore_errors=True)
        raise InstallerUpdateError(f"could not replace {target.name}: {exc}") from exc

    shutil.rmtree(previous, ignore_errors=True)


def _running_macos_app() -> Path | None:
    """``/Applications/Personal Jarvis.app`` for the RUNNING process, if any.

    ``bundle_root()`` is ``<app>/Contents/MacOS``; the bundle is two levels up.
    """
    root = bundle_root()
    if root is None:
        return None
    for parent in (root, *root.parents):
        if parent.suffix == ".app":
            return parent
    return None


def _running_appimage() -> Path | None:
    """The AppImage file this process was launched from (``$APPIMAGE``)."""
    raw = os.environ.get("APPIMAGE", "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    return candidate if candidate.is_file() else None


def supported_here() -> bool:
    """True when this process is frozen AND an installer exists for its platform."""
    if not is_frozen():
        return False
    return installer_asset_name(sys.platform, machine_for_update()) is not None


def update_blocker(
    platform_name: str | None = None,
    *,
    app_path: Path | None = None,
    appimage_path: Path | None = None,
) -> str | None:
    """Why the handover would fail on THIS install, or ``None`` when it can run.

    Asked before a single byte is downloaded: every case below otherwise
    surfaces only after a several-hundred-MB download, as a refusal the user
    cannot act on. Each answer is a user-facing sentence that says what to do.

    Windows has no such case — the per-user installer replaces its own
    directory — so it always answers ``None``.
    """
    active_platform = sys.platform if platform_name is None else platform_name
    if active_platform == "darwin":
        app = app_path if app_path is not None else _running_macos_app()
        if app is None:
            return "Personal Jarvis could not find its own app bundle, so it cannot replace it."
        if "/AppTranslocation/" in app.as_posix():
            # Gatekeeper runs a quarantined app that was never moved from a
            # read-only, randomised copy; nothing there can be replaced.
            return (
                "macOS is running Personal Jarvis from a temporary copy. Move it to "
                "the Applications folder, open it from there, then update."
            )
        if not _can_write(app.parent):
            return (
                f"Personal Jarvis cannot write to {app.parent} (a disk image or a "
                "protected folder). Move it to the Applications folder, then update."
            )
        return None
    if active_platform.startswith("linux"):
        appimage = appimage_path if appimage_path is not None else _running_appimage()
        if appimage is None:
            return (
                "This copy of Personal Jarvis was not started from its AppImage file, "
                "so there is no file to update. Download the new AppImage instead."
            )
        if not _can_write(appimage.parent):
            return (
                f"Personal Jarvis cannot write to {appimage.parent}. Move the AppImage "
                "to a folder you own (for example ~/Applications), then update."
            )
        return None
    return None


# A download directory younger than this may belong to an update still in
# flight in another instance (the whole download may take DOWNLOAD_TIMEOUT_S).
STALE_DOWNLOAD_AGE_S = 6 * 3600.0
_DOWNLOAD_DIR_PREFIX = "jarvis-update-"


def download_workdir() -> Path:
    """A fresh private directory for one update's download."""
    return Path(tempfile.mkdtemp(prefix=_DOWNLOAD_DIR_PREFIX))


def sweep_stale_downloads(
    temp_root: Path | None = None,
    *,
    max_age_s: float = STALE_DOWNLOAD_AGE_S,
    now: float | None = None,
) -> int:
    """Delete download directories earlier updates left behind; return how many.

    On Windows the installer runs FROM its download, so that directory cannot
    be removed while the update happens — and nothing removes it later, which
    left one installer of several hundred MB in the temp folder per update.
    The next run of the app reclaims them. Never raises: this is housekeeping.
    """
    root = Path(tempfile.gettempdir()) if temp_root is None else temp_root
    current = time.time() if now is None else now
    removed = 0
    try:
        candidates = [p for p in root.glob(f"{_DOWNLOAD_DIR_PREFIX}*") if p.is_dir()]
    except OSError as exc:
        log.debug("[update] could not list %s for old downloads: %s", root, exc)
        return 0
    for folder in candidates:
        try:
            newest = max(
                [folder.stat().st_mtime, *(entry.stat().st_mtime for entry in folder.iterdir())]
            )
        except OSError as exc:
            log.debug("[update] skipping %s: %s", folder, exc)
            continue
        if current - newest < max_age_s:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        if folder.exists():
            log.debug("[update] %s is still in use; leaving it", folder)
            continue
        removed += 1
    if removed:
        log.info("[update] removed %d old update download(s) from %s", removed, root)
    return removed
