#Requires -Version 5.1
<#
.SYNOPSIS
    Build the Windows installer for Personal Jarvis.

.DESCRIPTION
    One command from a clean checkout to a shippable installer:

        1. build the React frontend when jarvis/ui/web/dist is missing,
        2. freeze the app with PyInstaller (jarvis.spec, onedir),
        3. wrap dist/Jarvis in an Inno Setup wizard.

    The result is dist/installers/PersonalJarvis-Setup-x64.exe - the exact asset
    name the website's download button and the in-app updater expect.

    Every failure stops the script with a non-zero exit code; nothing is left
    half-built and reported as success.

.PARAMETER Python
    Interpreter to freeze with. Defaults to the repository's .venv, then to
    whatever `python` resolves to.

.PARAMETER SkipFrontend
    Never run npm, even when jarvis/ui/web/dist is missing. For a CI job that
    built the bundle in an earlier step.

.PARAMETER SkipPyInstaller
    Package the existing dist/Jarvis instead of freezing again. Cuts the
    edit-compile loop on the .iss file from minutes to seconds.

.EXAMPLE
    pwsh packaging/windows/build.ps1
#>
[CmdletBinding()]
param(
    [string] $Python = "",
    [switch] $SkipFrontend,
    [switch] $SkipPyInstaller
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string] $Message)
    Write-Host "[build] $Message"
}

function Stop-WithError {
    param([string] $Message)
    Write-Host "[build] FAILED: $Message" -ForegroundColor Red
    exit 1
}

function Invoke-Checked {
    param(
        [string] $Executable,
        [string[]] $Arguments,
        [string] $WorkingDirectory,
        [string] $What
    )
    Push-Location $WorkingDirectory
    try {
        & $Executable @Arguments
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($code -ne 0) {
        Stop-WithError "$What exited with code $code"
    }
}

# --- Locate the repository -------------------------------------------------
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$SpecFile = Join-Path $RepoRoot "jarvis.spec"
$BundleDir = Join-Path $RepoRoot "dist\Jarvis"
$InstallerDir = Join-Path $RepoRoot "dist\installers"
$FrontendDir = Join-Path $RepoRoot "jarvis\ui\web\frontend"
$FrontendDist = Join-Path $RepoRoot "jarvis\ui\web\dist"
$IssFile = Join-Path $PSScriptRoot "PersonalJarvis.iss"
$IconFile = Join-Path $RepoRoot "assets\icons\jarvis.ico"

if (-not (Test-Path $SpecFile)) { Stop-WithError "jarvis.spec not found at $SpecFile" }
if (-not (Test-Path $IssFile)) { Stop-WithError "PersonalJarvis.iss not found at $IssFile" }

# --- Version ---------------------------------------------------------------
# Single source of truth. A hand-typed version here would drift from the tag
# the release workflow verifies against.
$InitFile = Join-Path $RepoRoot "jarvis\__init__.py"
$InitText = Get-Content -Raw -Encoding UTF8 $InitFile
$VersionMatch = [regex]::Match($InitText, '__version__\s*=\s*"([^"]+)"')
if (-not $VersionMatch.Success) { Stop-WithError "jarvis/__init__.py does not define __version__" }
$AppVersion = $VersionMatch.Groups[1].Value
Write-Step "Personal Jarvis $AppVersion"

# --- Python ----------------------------------------------------------------
if ($Python -eq "") {
    $VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) { $Python = $VenvPython } else { $Python = "python" }
}
Write-Step "interpreter: $Python"

# --- 1. Frontend -----------------------------------------------------------
if (Test-Path (Join-Path $FrontendDist "index.html")) {
    Write-Step "1/3 frontend bundle already present - skipping npm"
} elseif ($SkipFrontend) {
    Stop-WithError "jarvis/ui/web/dist is missing and -SkipFrontend was passed"
} else {
    Write-Step "1/3 building the frontend"
    if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
        Invoke-Checked -Executable "npm" -Arguments @("ci") -WorkingDirectory $FrontendDir -What "npm ci"
    }
    Invoke-Checked -Executable "npm" -Arguments @("run", "build") -WorkingDirectory $FrontendDir -What "npm run build"
    if (-not (Test-Path (Join-Path $FrontendDist "index.html"))) {
        Stop-WithError "npm run build did not produce jarvis/ui/web/dist/index.html"
    }
}

# --- 2. PyInstaller --------------------------------------------------------
if ($SkipPyInstaller) {
    Write-Step "2/3 reusing the existing bundle (-SkipPyInstaller)"
} else {
    Write-Step "2/3 freezing with PyInstaller"
    Invoke-Checked -Executable $Python `
        -Arguments @("-m", "PyInstaller", "jarvis.spec", "--noconfirm", "--clean") `
        -WorkingDirectory $RepoRoot -What "pyinstaller"
}

foreach ($required in @("PersonalJarvis.exe", "jarvis.exe")) {
    $candidate = Join-Path $BundleDir $required
    if (-not (Test-Path $candidate)) {
        Stop-WithError "the frozen bundle is missing $required (expected at $candidate)"
    }
}

# Running the app straight out of dist/Jarvis (the obvious way to smoke-test a
# build) leaves runtime state behind in _internal: a jarvis.db, flight-recorder
# JSONL, logs. Shipping that would put the builder's own data inside every
# downloaded installer. The build output is defined by jarvis.spec, so anything
# below is not part of it and goes before the payload is compressed.
$strayState = @("_internal\data", "_internal\data-dev", "_internal\logs")
foreach ($stray in $strayState) {
    $path = Join-Path $BundleDir $stray
    if (Test-Path $path) {
        Write-Step "removing runtime state left in the bundle: $stray"
        Remove-Item -Recurse -Force $path
    }
}

# --- 3. Inno Setup ---------------------------------------------------------
function Find-Iscc {
    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($null -ne $command) { return $command.Source }
    # ${env:ProgramFiles(x86)} needs the braces: without them PowerShell reads
    # $env:ProgramFiles and leaves a literal "(x86)" behind.
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    return $null
}

$Iscc = Find-Iscc
if ($null -eq $Iscc) {
    Stop-WithError ("Inno Setup 6 is not installed. Install it with " +
        "'winget install --id JRSoftware.InnoSetup -e' and run this script again.")
}
Write-Step "3/3 packaging with $Iscc"

New-Item -ItemType Directory -Force -Path $InstallerDir | Out-Null

$IsccArgs = @(
    "/DAppVersion=$AppVersion",
    "/DSourceDir=$BundleDir",
    "/DOutputDir=$InstallerDir",
    "/DIconFile=$IconFile",
    $IssFile
)
Invoke-Checked -Executable $Iscc -Arguments $IsccArgs -WorkingDirectory $RepoRoot -What "ISCC"

$Artifact = Join-Path $InstallerDir "PersonalJarvis-Setup-x64.exe"
if (-not (Test-Path $Artifact)) {
    Stop-WithError "ISCC reported success but $Artifact does not exist"
}

$SizeMb = [math]::Round((Get-Item $Artifact).Length / 1MB, 1)
Write-Step "done - $Artifact ($SizeMb MB)"
Write-Output $Artifact
exit 0
