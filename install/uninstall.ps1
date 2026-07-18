# Personal Jarvis - Windows uninstaller
#
# Usage (from PowerShell), on a machine where Jarvis is installed:
#   & "$env:USERPROFILE\.personal-jarvis\install\uninstall.ps1"
#   & "$env:USERPROFILE\.personal-jarvis\install\uninstall.ps1" --dry-run   # preview
#   & "$env:USERPROFILE\.personal-jarvis\install\uninstall.ps1" --yes       # no prompt
#
# It removes four things a plain folder-delete would miss:
#   1. the install folder (~\.personal-jarvis)
#   2. the Start-menu launcher and Installed Apps registration
#   3. the login-autostart entry (a logon scheduled task / Startup shortcut)
#   4. the API keys saved in the OS keychain (Windows Credential Manager, service
#      "personal-jarvis")
#
# Heavy logic lives in `python -m jarvis --uninstall` (cross-platform, tested).
# This bootstrap runs that for the autostart + keys (it asks for confirmation),
# then removes the folder itself from OUTSIDE the venv so the running python.exe
# never locks its own tree.
#
# SOURCE-ENCODING RULE: served BOM-less, read as cp1252 by Windows PowerShell, so
# the source outside here-strings stays ASCII; glyphs are built from code points.

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$e     = [char]27
$Gold  = "${e}[38;2;255;214;10m"; $Green = "${e}[38;2;122;200;140m"
$Dim   = "${e}[38;2;143;143;143m"; $Red   = "${e}[38;2;224;122;110m"
$Bold  = "${e}[1m"; $Rst = "${e}[0m"
$Dot = [char]0x25CF; $Chk = [char]0x2713; $Crs = [char]0x2717

function Write-Step([string]$Text) { Write-Host ""; Write-Host "$Gold  $Dot$Rst $Bold$Text$Rst" }
function Write-Ok([string]$Text)    { Write-Host "$Green    $Chk$Rst $Dim$Text$Rst" }
function Write-Note([string]$Text)  { Write-Host "$Dim      $Text$Rst" }
function Write-Err([string]$Text)   { Write-Host "$Red    $Crs $Text$Rst" }

function Stop-JarvisProcesses([string]$Root) {
    # Any process whose executable lives inside the install dir is the app
    # itself (tray, server, worker). Windows keeps a running .exe and every
    # loaded .dll/.pyd locked, so the folder delete fails with "access denied"
    # while one is alive. Best effort: never this PowerShell, never elevated
    # processes we cannot see.
    $Prefix = $Root.TrimEnd('\') + '\'
    $Procs = @()
    foreach ($p in (Get-Process -ErrorAction SilentlyContinue)) {
        if ($p.Id -eq $PID) { continue }
        $ProcPath = $null
        try { $ProcPath = $p.Path } catch {}
        if ($ProcPath -and $ProcPath.StartsWith($Prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            $Procs += $p
        }
    }
    if ($Procs.Count -eq 0) { return }
    foreach ($p in $Procs) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    foreach ($p in $Procs) {
        try { Wait-Process -Id $p.Id -Timeout 10 -ErrorAction SilentlyContinue } catch {}
    }
    Write-Ok "Stopped the running Jarvis app ($($Procs.Count) process(es))."
}

$InstallDir = if ($env:JARVIS_INSTALL_DIR) { $env:JARVIS_INSTALL_DIR } else { Join-Path $env:USERPROFILE '.personal-jarvis' }
$VenvPython = Join-Path $InstallDir '.venv\Scripts\python.exe'

Write-Step 'Uninstall Personal Jarvis'
Write-Note $InstallDir

$DryRun    = $args -contains '--dry-run'
$AssumeYes = ($args -contains '--yes') -or ($args -contains '-y')

if (-not (Test-Path -LiteralPath $InstallDir)) {
    Write-Err "No install found at $InstallDir - nothing to do."
    exit 0
}

# 2 + 3 + 4: run the tested cleanup (app registration, autostart, and keys),
#            keeping the folder so we can delete it below.
$Rc = 0
if (Test-Path -LiteralPath $VenvPython) {
    & $VenvPython -m jarvis --uninstall --keep-folder @args
    $Rc = $LASTEXITCODE
} else {
    Write-Err 'Python environment missing - skipping autostart/key cleanup.'
    Write-Note 'The app registration and folder can still be removed; saved API keys may remain.'
    if ($DryRun) { exit 0 }
    if (-not $AssumeYes) {
        $ans = Read-Host "Type 'yes' to delete $InstallDir"
        if ($ans -ne 'yes') { Write-Note 'Cancelled.'; exit 1 }
    }
    if ($env:APPDATA) {
        $Shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Personal Jarvis.lnk'
        Remove-Item -LiteralPath $Shortcut -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath 'Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall\PersonalJarvis' -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath 'Registry::HKEY_CURRENT_USER\Software\Classes\AppUserModelId\PersonalJarvis.PersonalJarvis' -Recurse -Force -ErrorAction SilentlyContinue
    Write-Ok 'Removed the desktop app registration.'
    $Rc = 0
}

# The Python step returns 1 when the user cancels at its prompt. Respect that.
if ($Rc -ne 0) {
    Write-Note 'Cancelled - nothing was changed.'
    exit $Rc
}

# 1: delete the folder from OUTSIDE the venv. Leave the install dir first so it
#    is not the current location during removal. The Python step above already
#    stops the running app, but anything (re)started since - and the no-venv
#    fallback path - is caught here again. Windows can also hold file locks for
#    a moment after a process dies, so the delete retries before giving up, and
#    a final failure says plainly WHY instead of a red stacktrace.
if (-not $DryRun) {
    Set-Location -LiteralPath $env:USERPROFILE
    Stop-JarvisProcesses $InstallDir
    $Deleted = $false
    for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
        try {
            Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction Stop
            $Deleted = $true
            break
        } catch {
            if (-not (Test-Path -LiteralPath $InstallDir)) { $Deleted = $true; break }
            Stop-JarvisProcesses $InstallDir
            Start-Sleep -Seconds 2
        }
    }
    if (-not $Deleted -and (Test-Path -LiteralPath $InstallDir)) {
        Write-Err "Could not fully remove $InstallDir - a program is still using files inside it."
        Write-Note 'Close every Jarvis window (including the tray icon near the clock) and any terminal opened inside that folder, then run this uninstaller again.'
        Write-Note 'Signing out of Windows and back in also releases leftover file locks.'
        exit 3
    }
    Write-Ok "Removed $InstallDir"
    Write-Step 'Done. Personal Jarvis has been uninstalled.'
}
