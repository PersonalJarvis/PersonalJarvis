"""The operating system's own folder dialog, for picking a workspace.

Browsing folders over REST works everywhere and stays the only option on a
headless box, but on a real desktop it is the wrong tool for one specific job:
finding a folder you know by sight rather than by name. The system dialog
already has the sidebar, the search, the recent places and the muscle memory —
rebuilding a worse copy of it inside a web page helps nobody.

So both exist. The REST browser is the floor; this is the shortcut when the
machine can actually show a window.

**Every dialog runs in its own short-lived process.** Not an optimisation — a
requirement, three times over:

1. A window toolkit demands a thread state the web server does not have: a
   single-threaded apartment on Windows, the main thread on macOS. A subprocess
   gets to be both.
2. A modal dialog blocks until someone answers it. In-process that would pin a
   worker for as long as the user leaves it open.
3. A wedged dialog must be *recoverable*, not merely bounded. A hung thread
   cannot be cancelled in Python — killing a child process can (AP-24).

**Availability is probed, never assumed from the platform.** ``sys.platform ==
"linux"`` says nothing about whether a screen exists; a container, an SSH
session and a VPS all report the same platform as a desktop. The probe asks for
the two things that actually matter — a display and a program that can draw the
dialog — and every OS without both degrades to an honest "not available here",
which is what keeps the REST browser the advertised path.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

# A dialog is a human interaction, so the ceiling is generous — it exists to
# reap a dialog nobody is looking at any more, not to rush the user.
DEFAULT_TIMEOUT_S = 300.0

# The chosen path is printed between these, so a helper's own chatter (a shell
# profile banner, a GTK warning on stdout, a PowerShell progress record) can
# never be mistaken for an answer.
_BEGIN = "<<<JARVIS-FOLDER>>>"
_END = "<<<END-JARVIS-FOLDER>>>"


@dataclass(frozen=True, slots=True)
class PickerSupport:
    """Whether this machine can show a folder dialog, and why not when it cannot."""

    available: bool
    backend: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PickResult:
    """The outcome of one dialog. ``path`` is set only when a folder was chosen."""

    path: str | None = None
    cancelled: bool = False
    error: str | None = None


# --------------------------------------------------------------------------- #
# Availability                                                                #
# --------------------------------------------------------------------------- #
def _has_display() -> bool:
    """True when this session can put a window on a screen.

    Windows and macOS carry their window server with the session, so the
    question only has a meaningful answer on Linux and the other X11/Wayland
    systems, where a headless server is the normal case rather than the
    exception.
    """
    if sys.platform in {"win32", "darwin"}:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _linux_helper() -> str | None:
    """The dialog program to use, in order of how well it fits the desktop."""
    for candidate in ("zenity", "kdialog", "qarma", "matedialog"):
        if shutil.which(candidate):
            return candidate
    return None


def _windows_shell() -> str | None:
    """Windows PowerShell first: it is always present and always has WinForms."""
    for candidate in ("powershell", "pwsh"):
        if shutil.which(candidate):
            return candidate
    return None


def support() -> PickerSupport:
    """Can this machine show a native folder dialog right now?

    Answered about the running session, not the platform — the same build of
    Jarvis is expected to say "yes" on a desktop and "no" over SSH.
    """
    if not _has_display():
        return PickerSupport(
            available=False,
            reason="This machine has no desktop session, so it cannot open a folder window.",
        )
    if sys.platform == "win32":
        shell = _windows_shell()
        if shell is None:
            return PickerSupport(
                available=False,
                reason="PowerShell was not found, so no folder window can be opened.",
            )
        return PickerSupport(available=True, backend=shell)
    if sys.platform == "darwin":
        if shutil.which("osascript") is None:
            return PickerSupport(
                available=False,
                reason="osascript was not found, so no folder window can be opened.",
            )
        return PickerSupport(available=True, backend="osascript")
    helper = _linux_helper()
    if helper is None:
        return PickerSupport(
            available=False,
            reason=(
                "No folder-dialog program is installed. Install zenity (GNOME) or "
                "kdialog (KDE) to enable it — browsing here works either way."
            ),
        )
    return PickerSupport(available=True, backend=helper)


# --------------------------------------------------------------------------- #
# The dialog itself                                                           #
# --------------------------------------------------------------------------- #
_PROMPT = "Choose the folder the agents should work in"

# Written with placeholders rather than f-strings: these scripts are dense with
# braces of their own, and doubling every one of them to survive `str.format`
# would make them unreadable and easy to break.
def _fill(script: str, **values: str) -> str:
    out = script.replace("__BEGIN__", _BEGIN).replace("__END__", _END)
    out = out.replace("__PROMPT__", _PROMPT)
    for key, value in values.items():
        out = out.replace(f"__{key.upper()}__", value)
    return out


# Windows has TWO folder dialogs and the difference matters here.
#
# `FolderBrowserDialog` — the one every PowerShell snippet reaches for — is the
# small tree from Windows XP: no address bar, no search, no way to type or paste
# a path, and no Quick Access. Windows PowerShell 5.1 runs on .NET Framework,
# where that is the ONLY thing it can be; the modern version of that class ships
# with .NET Core and never reached the Framework.
#
# The dialog people mean when they say "the Explorer window" is the Common Item
# Dialog (`IFileOpenDialog` with the pick-folders option) — the same one Explorer
# and every modern app use, with the sidebar, the address bar, search, and typed
# paths. It is a COM interface, not a .NET class, so it is declared here and used
# directly. That declaration is the fragile part (the method order IS the binary
# contract), which is why the whole thing sits in a try/catch that falls back to
# the old tree rather than failing: an outdated dialog still picks a folder.
#
# `-STA` is mandatory either way — the shell dialogs refuse to run in a
# multi-threaded apartment — and the invisible topmost owner form exists only to
# lend its z-order, because a dialog opened by a background process otherwise
# appears behind every other window and looks like nothing happened.
_WINDOWS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms

$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.Show()
$owner.Activate()
$ownerHandle = $owner.Handle

$start = $env:JARVIS_PICKER_START
$picked = $null

try {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class JarvisFolderPicker {
    [ComImport, ClassInterface(ClassInterfaceType.None)]
    [Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
    private class FileOpenDialogRCW { }

    [ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IFileDialog {
        [PreserveSig] int Show(IntPtr parent);
        void SetFileTypes(uint cFileTypes, IntPtr rgFilterSpec);
        void SetFileTypeIndex(uint iFileType);
        void GetFileTypeIndex(out uint piFileType);
        void Advise(IntPtr pfde, out uint pdwCookie);
        void Unadvise(uint dwCookie);
        void SetOptions(uint fos);
        void GetOptions(out uint fos);
        void SetDefaultFolder(IShellItem psi);
        void SetFolder(IShellItem psi);
        void GetFolder(out IShellItem ppsi);
        void GetCurrentSelection(out IShellItem ppsi);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string pszText);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
        void GetResult(out IShellItem ppsi);
        void AddPlace(IShellItem psi, int alignment);
        void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string pszDefaultExtension);
        void Close([MarshalAs(UnmanagedType.Error)] int hr);
        void SetClientGuid(ref Guid guid);
        void ClearClientData();
        void SetFilter(IntPtr pFilter);
    }

    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IShellItem {
        void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
        void GetParent(out IShellItem ppsi);
        void GetDisplayName(uint sigdnName, [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);
        void GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
        void Compare(IShellItem psi, uint hint, out int piOrder);
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    private static extern IShellItem SHCreateItemFromParsingName(
        [MarshalAs(UnmanagedType.LPWStr)] string pszPath, IntPtr pbc, ref Guid riid);

    private const uint FOS_PICKFOLDERS   = 0x00000020;
    private const uint FOS_FORCEFILESYSTEM = 0x00000040;
    private const uint FOS_PATHMUSTEXIST = 0x00000800;
    private const uint SIGDN_FILESYSPATH = 0x80058000;

    public static string Show(IntPtr owner, string startPath) {
        IFileDialog dialog = (IFileDialog)new FileOpenDialogRCW();
        dialog.SetOptions(FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST);
        dialog.SetTitle("__PROMPT__");
        dialog.SetOkButtonLabel("Use this folder");
        if (!string.IsNullOrEmpty(startPath)) {
            try {
                Guid iid = new Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE");
                dialog.SetFolder(SHCreateItemFromParsingName(startPath, IntPtr.Zero, ref iid));
            } catch { /* a start folder is a nicety, never a reason to fail */ }
        }
        if (dialog.Show(owner) != 0) return null;   // cancelled
        IShellItem result;
        dialog.GetResult(out result);
        string path;
        result.GetDisplayName(SIGDN_FILESYSPATH, out path);
        return path;
    }
}
'@ -ErrorAction Stop
    $picked = [JarvisFolderPicker]::Show($ownerHandle, $start)
} catch {
    # No Common Item Dialog available (very old Windows, blocked compiler):
    # the XP-era tree still picks a folder, which beats no window at all.
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = '__PROMPT__'
    $dialog.ShowNewFolderButton = $true
    if ($start) { $dialog.SelectedPath = $start }
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        $picked = $dialog.SelectedPath
    }
}

$owner.Dispose()
if ($picked) {
    Write-Output '__BEGIN__'
    Write-Output $picked
    Write-Output '__END__'
}
"""

# `choose folder` returns an alias; `POSIX path of` turns it into a real path.
# Cancelling raises error -128, which is a normal answer here, not a failure.
# `activate` brings the dialog forward — without it a dialog asked for by a
# background process opens behind whatever the user is looking at.
_MACOS_SCRIPT = r"""
on run argv
    set startPath to item 1 of argv
    tell me to activate
    try
        if startPath is not "" then
            set here to POSIX file startPath
            set chosen to choose folder with prompt "__PROMPT__" default location here
        else
            set chosen to choose folder with prompt "__PROMPT__"
        end if
    on error number -128
        return ""
    end try
    return "__BEGIN__" & linefeed & POSIX path of chosen & linefeed & "__END__"
end run
"""


def _command(start: str | None, backend: str) -> tuple[list[str], dict[str, str]] | None:
    """The argv (and any extra environment) that shows the dialog."""
    env_extra: dict[str, str] = {}
    if sys.platform == "win32":
        if start:
            env_extra["JARVIS_PICKER_START"] = start
        return (
            [
                backend,
                "-STA",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _fill(_WINDOWS_SCRIPT),
            ],
            env_extra,
        )
    if sys.platform == "darwin":
        return ["osascript", "-e", _fill(_MACOS_SCRIPT), start or ""], env_extra
    if backend in {"zenity", "qarma", "matedialog"}:
        argv = [
            backend,
            "--file-selection",
            "--directory",
            f"--title={_PROMPT}",
        ]
        if start:
            # zenity wants a trailing separator to treat the value as the folder
            # to open rather than the entry to preselect inside its parent.
            argv.append(f"--filename={start.rstrip('/')}/")
        return argv, env_extra
    if backend == "kdialog":
        return [backend, "--getexistingdirectory", start or str(Path.home())], env_extra
    return None


def _extract(stdout: str) -> str | None:
    """The path between the markers, or None when the dialog produced no answer."""
    if _BEGIN not in stdout:
        # kdialog and zenity have no scripting layer to add markers; they print
        # the bare path and nothing else, so the whole output IS the answer.
        cleaned = stdout.strip()
        return cleaned or None
    body = stdout.split(_BEGIN, 1)[1]
    body = body.split(_END, 1)[0] if _END in body else body
    cleaned = body.strip()
    return cleaned or None


def choose_folder(
    *, start: str | None = None, timeout: float = DEFAULT_TIMEOUT_S
) -> PickResult:
    """Show the system folder dialog and return what the user picked.

    Blocking — call it off the event loop. Cancelling is an ordinary outcome
    (``cancelled=True``), not an error: the user changing their mind is the most
    common way this ends.
    """
    probe = support()
    if not probe.available or probe.backend is None:
        return PickResult(error=probe.reason or "No folder window can be opened here.")

    resolved_start: str | None = None
    if start:
        try:
            candidate = Path(start).expanduser()
            if candidate.is_dir():
                resolved_start = str(candidate)
        except OSError:
            resolved_start = None

    built = _command(resolved_start, probe.backend)
    if built is None:
        return PickResult(error="No folder window can be opened here.")
    argv, env_extra = built

    env = {**os.environ, **env_extra}
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv,
            capture_output=True,
            text=True,
            # Explicit, because the answer is a PATH: Python would otherwise
            # decode with the console codepage (cp1252/cp850 on Windows) and
            # every folder with an umlaut in its name would come back mangled —
            # or, as it did here, take the reader thread down with a decode
            # error and look exactly like a cancelled dialog. Each helper is
            # told to write UTF-8; `replace` keeps a stray byte from any of
            # them a cosmetic problem rather than a crash.
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            creationflags=NO_WINDOW_CREATIONFLAGS,  # AP-1: no console flash
        )
    except subprocess.TimeoutExpired:
        logger.info("Agentic IDE: the folder dialog was left open and timed out")
        return PickResult(
            error="The folder window was left open too long and was closed. Try again."
        )
    except OSError as exc:
        logger.warning("Agentic IDE: could not open the folder dialog: {}", exc)
        return PickResult(error=f"Could not open a folder window: {exc}")

    chosen = _extract(completed.stdout or "")
    if not chosen:
        # Every helper signals "cancelled" with a non-zero exit and no path, so
        # an empty answer is a cancellation unless the helper also complained.
        stderr = (completed.stderr or "").strip()
        if completed.returncode not in (0, 1) and stderr:
            logger.warning("Agentic IDE: folder dialog failed: {}", stderr)
            return PickResult(error="The folder window could not be opened.")
        return PickResult(cancelled=True)

    try:
        picked = Path(chosen).expanduser()
        if not picked.is_dir():
            return PickResult(error=f"That is not a folder any more: {picked}")
    except OSError as exc:
        return PickResult(error=f"Could not read that folder: {exc}")
    return PickResult(path=str(picked))


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "PickResult",
    "PickerSupport",
    "choose_folder",
    "support",
]
