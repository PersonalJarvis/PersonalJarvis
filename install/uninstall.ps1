# Personal Jarvis - Windows uninstaller
#
# Usage (from PowerShell), on a machine where Jarvis is installed:
#   & "$env:USERPROFILE\.personal-jarvis\install\uninstall.ps1"
#   & "$env:USERPROFILE\.personal-jarvis\install\uninstall.ps1" --dry-run   # preview
#   & "$env:USERPROFILE\.personal-jarvis\install\uninstall.ps1" --yes       # no prompt
#
# It removes three things a plain folder-delete would miss:
#   1. the install folder (~\.personal-jarvis)
#   2. the login-autostart entry (a logon scheduled task / Startup shortcut)
#   3. the API keys saved in the OS keychain (Windows Credential Manager, service
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
$Gold  = "${e}[38;2;231;196;110m"; $Green = "${e}[38;2;122;200;140m"
$Dim   = "${e}[38;2;140;140;140m"; $Red   = "${e}[38;2;224;122;110m"
$Bold  = "${e}[1m"; $Rst = "${e}[0m"
$Dot = [char]0x25CF; $Chk = [char]0x2713; $Crs = [char]0x2717

function Write-Step([string]$Text) { Write-Host ""; Write-Host "$Gold  $Dot$Rst $Bold$Text$Rst" }
function Write-Ok([string]$Text)    { Write-Host "$Green    $Chk$Rst $Dim$Text$Rst" }
function Write-Note([string]$Text)  { Write-Host "$Dim      $Text$Rst" }
function Write-Err([string]$Text)   { Write-Host "$Red    $Crs $Text$Rst" }

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

# 1 + 2 + 3: run the tested cleanup (autostart + keys), keeping the folder so we
#            delete it ourselves below (the running venv must not self-delete).
$Rc = 0
if (Test-Path -LiteralPath $VenvPython) {
    & $VenvPython -m jarvis --uninstall --keep-folder @args
    $Rc = $LASTEXITCODE
} else {
    Write-Err 'Python environment missing - skipping autostart/key cleanup.'
    Write-Note 'Removing the folder only; saved API keys may remain in Credential Manager.'
    if ($DryRun) { exit 0 }
    if (-not $AssumeYes) {
        $ans = Read-Host "Type 'yes' to delete $InstallDir"
        if ($ans -ne 'yes') { Write-Note 'Cancelled.'; exit 1 }
    }
    $Rc = 0
}

# The Python step returns 1 when the user cancels at its prompt. Respect that.
if ($Rc -ne 0) {
    Write-Note 'Cancelled - nothing was changed.'
    exit $Rc
}

# 1: delete the folder from OUTSIDE the venv (nothing is locked now). Leave the
#    install dir first so it is not the current location during removal.
if (-not $DryRun) {
    Set-Location -LiteralPath $env:USERPROFILE
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
    Write-Ok "Removed $InstallDir"
    Write-Step 'Done. Personal Jarvis has been uninstalled.'
}
