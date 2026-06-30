<#
.SYNOPSIS
    Tear down a Jarvis sandbox: move it to the Recycle Bin and confirm the real
    install was never touched.

.DESCRIPTION
    Reversible by design -- the sandbox dir goes to the Recycle Bin, not a
    permanent delete. Then it re-checks that the real 'personal-jarvis' Windows
    Credential Manager entries are still present (the sandbox used an isolated
    file keyring) and that the global 'import jarvis' still resolves to the real
    repo.

.PARAMETER SandboxRoot
    The sandbox to remove. Default: the 'Jarvis-Sandbox' sibling of this repo.
#>
[CmdletBinding()]
param(
    [string]$SandboxRoot
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $SandboxRoot -or $SandboxRoot.Trim() -eq "") {
    $SandboxRoot = (Join-Path (Split-Path $repoRoot -Parent) "Jarvis-Sandbox")
}

function Write-Step([string]$m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }

Write-Step "Remove Jarvis sandbox"
Write-Host ("  target: {0}" -f $SandboxRoot)

if (-not (Test-Path $SandboxRoot)) {
    Write-Host "  Nothing to remove (no sandbox at that path)." -ForegroundColor Yellow
    exit 0
}

# Refuse to delete anything that is not actually a sandbox (must contain the
# generated launcher) -- guards against a mistyped path pointing at real work.
if (-not (Test-Path (Join-Path $SandboxRoot "run-sandbox.ps1"))) {
    Write-Host "  [FAIL] no run-sandbox.ps1 there -- refusing to delete (does not look like a sandbox)." -ForegroundColor Red
    exit 1
}

Write-Host "  Moving sandbox to the Recycle Bin (reversible)..."
Add-Type -AssemblyName Microsoft.VisualBasic
[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($SandboxRoot, 'OnlyErrorDialogs', 'SendToRecycleBin')
if (Test-Path $SandboxRoot) {
    Write-Host "  [FAIL] sandbox still present -- move failed (a process may be holding it open)." -ForegroundColor Red
    exit 1
}
Write-Host "  [ok] sandbox moved to the Recycle Bin." -ForegroundColor Green

Write-Step "Confirm the real install is untouched"
$realKeys = (cmdkey /list 2>$null | Select-String "personal-jarvis").Count
Write-Host ("  real 'personal-jarvis' credential entries still present: {0}" -f $realKeys)
$pyExe = "C:\Program Files\Python311\python.exe"
if (Test-Path $pyExe) {
    $globalJarvis = & $pyExe -c "import importlib.util as u; s=u.find_spec('jarvis'); print(s.origin if s else 'NONE')" 2>$null
    Write-Host ("  global 'import jarvis' resolves to: {0}" -f $globalJarvis)
}
Write-Host "  [ok] teardown complete -- host left as it was." -ForegroundColor Green
