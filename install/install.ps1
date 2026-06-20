# Personal Jarvis — Windows quick-install bootstrap (Stage 1)
#
# Usage (from PowerShell):
#   irm https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.ps1 | iex
#
# This bootstrap is intentionally small. It:
#   1. Verifies Python 3.11+ is available.
#   2. Verifies git is available.
#   3. Clones (or updates) personal-jarvis into ~\.personal-jarvis.
#   4. Creates a Python venv, installs `rich` + `packaging`.
#   5. Hands control to install/installer.py (the Stage 2 orchestrator).
#
# All heavy logic lives in installer.py so it can be unit-tested and
# kept cross-platform. This file is meant to be read top-to-bottom in
# under 60 seconds before you paste it into a terminal.

$ErrorActionPreference = 'Stop'
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'

# ----------------------------------------------------------------- branding
$banner = @'

 ____                                  _   _                  _
|  _ \ ___ _ __ ___  ___  _ __   __ _ | | | | __ _ _ ____   _(_)___
| |_) / _ \ '__/ __|/ _ \| '_ \ / _` || |_| |/ _` | '__\ \ / / / __|
|  __/  __/ |  \__ \ (_) | | | | (_| ||  _  | (_| | |   \ V /| \__ \
|_|   \___|_|  |___/\___/|_| |_|\__,_||_| |_|\__,_|_|    \_/ |_|___/

  Quick install (Windows)
'@
Write-Host $banner -ForegroundColor Cyan

# ----------------------------------------------------------------- config
$RepoUrl    = if ($env:JARVIS_INSTALL_REPO) { $env:JARVIS_INSTALL_REPO } else { 'https://github.com/PersonalJarvis/PersonalJarvis.git' }
$Branch     = if ($env:JARVIS_INSTALL_REF)  { $env:JARVIS_INSTALL_REF }  else { 'main' }
$InstallDir = if ($env:JARVIS_INSTALL_DIR)  { $env:JARVIS_INSTALL_DIR }  else { Join-Path $env:USERPROFILE '.personal-jarvis' }

# Forward any extra args to installer.py (e.g. --no-launch, --dry-run, --with-voice-local)
$ExtraArgs = $args

function Test-Tool {
    param([string]$Name, [string]$VersionArg = '--version')
    try {
        $out = & $Name $VersionArg 2>&1
        return @{ Found = $true; Version = ($out | Select-Object -First 1) }
    } catch {
        return @{ Found = $false; Version = $null }
    }
}

function Test-PythonVersion {
    param([string]$Exe)
    $check = Test-Tool $Exe
    if (-not $check.Found) { return $false }
    # Match X.Y where X >= 3 and Y >= 11
    if ($check.Version -match 'Python\s+(\d+)\.(\d+)\.\d+') {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        return ($major -gt 3) -or ($major -eq 3 -and $minor -ge 11)
    }
    return $false
}

# ----------------------------------------------------------------- preflight
Write-Host ''
Write-Host '[1/5] Checking prerequisites...' -ForegroundColor Yellow

# Python: try `python` first, then `py -3.11`, then `py -3`.
$pythonExe = $null
foreach ($candidate in @('python', 'py')) {
    if (Test-PythonVersion $candidate) { $pythonExe = $candidate; break }
}
if (-not $pythonExe) {
    Write-Host ''
    Write-Host '  Python 3.11+ not found.' -ForegroundColor Red
    Write-Host '  Install it from https://www.python.org/downloads/ then re-run this command.' -ForegroundColor Red
    Write-Host '  (After install, open a NEW PowerShell window so PATH refreshes.)'
    exit 1
}
Write-Host "      Python OK ($pythonExe)" -ForegroundColor Green

# Git
$gitCheck = Test-Tool 'git'
if (-not $gitCheck.Found) {
    Write-Host ''
    Write-Host '  git not found.' -ForegroundColor Red
    Write-Host '  Install from https://git-scm.com/download/win then re-run this command.' -ForegroundColor Red
    exit 1
}
Write-Host '      git OK' -ForegroundColor Green

# ----------------------------------------------------------------- clone / update
Write-Host ''
Write-Host "[2/5] Preparing repo at $InstallDir ..." -ForegroundColor Yellow

if (Test-Path (Join-Path $InstallDir '.git')) {
    Write-Host '      existing checkout found — pulling latest...' -ForegroundColor Green
    Push-Location $InstallDir
    try {
        & git fetch --depth 1 origin $Branch
        & git checkout $Branch
        & git reset --hard "origin/$Branch"
    } finally {
        Pop-Location
    }
} else {
    if (Test-Path $InstallDir) {
        Write-Host "  $InstallDir exists but is not a git repo. Aborting to avoid clobbering your files." -ForegroundColor Red
        Write-Host '  Remove or move that directory, then re-run.' -ForegroundColor Red
        exit 1
    }
    & git clone --depth 1 --branch $Branch $RepoUrl $InstallDir
}

# WAVE 5 — payload-commit pin (axis E, Wave-5 audit Finding 2).
#
# install-verify.ps1 sets $env:JARVIS_PAYLOAD_COMMIT containing the
# signed payload commit SHA (Wave 1+2+4-authenticated). If set, bind the
# cloned tree to that exact commit so an attacker who flips `main` post-
# release cannot influence what we install. The signed SHA may be 40-char
# (git SHA-1) or 64-char (git SHA-256 repos).
if ($env:JARVIS_PAYLOAD_COMMIT) {
    $PayloadCommit = $env:JARVIS_PAYLOAD_COMMIT
    if ($PayloadCommit -notmatch '^[0-9a-f]{40}([0-9a-f]{24})?$') {
        Write-Host "  JARVIS_PAYLOAD_COMMIT is not a well-formed git SHA: '$PayloadCommit' - refusing." -ForegroundColor Red
        exit 1
    }
    Write-Host "      pinning clone to signed commit $PayloadCommit..." -ForegroundColor Yellow
    Push-Location $InstallDir
    try {
        # Shallow clones don't carry full history; deepen to retrieve the
        # target SHA explicitly. github.com defaults allow fetching arbitrary
        # SHAs on most repos; fall back to unshallow if direct-SHA fetch
        # fails (e.g. tag pushed but workflow ref-name was the merge commit).
        & git fetch --depth 1 origin $PayloadCommit 2>$null
        if ($LASTEXITCODE -ne 0) {
            & git fetch --unshallow origin
            if ($LASTEXITCODE -ne 0) { & git fetch origin }
        }
        & git checkout --detach $PayloadCommit
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  failed to checkout payload-commit $PayloadCommit - refusing." -ForegroundColor Red
            Write-Host '  the cloned tree does not contain the signed commit; release may be inconsistent.' -ForegroundColor Red
            exit 1
        }
        # Defensive verify: HEAD must be exactly the signed SHA byte-for-byte.
        $ActualHead = (& git rev-parse HEAD).Trim()
        if ($ActualHead -ne $PayloadCommit) {
            Write-Host "  HEAD drift detected: pinned=$PayloadCommit, actual=$ActualHead - refusing." -ForegroundColor Red
            exit 1
        }
        Write-Host "      clone pinned to $PayloadCommit" -ForegroundColor Green
    } finally {
        Pop-Location
    }
}
Write-Host '      repo ready' -ForegroundColor Green

# ----------------------------------------------------------------- venv
Write-Host ''
Write-Host '[3/5] Creating Python virtual environment...' -ForegroundColor Yellow

$VenvPath = Join-Path $InstallDir '.venv'
$VenvPython = Join-Path $VenvPath 'Scripts\python.exe'

if (-not (Test-Path $VenvPython)) {
    & $pythonExe -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { Write-Host '  venv creation failed.' -ForegroundColor Red; exit 1 }
}
Write-Host '      venv OK' -ForegroundColor Green

# ----------------------------------------------------------------- bootstrap deps
Write-Host ''
Write-Host '[4/5] Installing bootstrap dependencies (rich, packaging)...' -ForegroundColor Yellow
& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet rich packaging
if ($LASTEXITCODE -ne 0) { Write-Host '  bootstrap pip install failed.' -ForegroundColor Red; exit 1 }
Write-Host '      bootstrap deps OK' -ForegroundColor Green

# ----------------------------------------------------------------- hand off
Write-Host ''
Write-Host '[5/5] Handing off to the Python installer...' -ForegroundColor Yellow
Write-Host ''
$InstallerPy = Join-Path $InstallDir 'install\installer.py'
if (-not (Test-Path $InstallerPy)) {
    Write-Host "  $InstallerPy not found in the clone." -ForegroundColor Red
    Write-Host '  The repo seems incomplete. File a bug.' -ForegroundColor Red
    exit 1
}

Push-Location $InstallDir
try {
    & $VenvPython $InstallerPy @ExtraArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
