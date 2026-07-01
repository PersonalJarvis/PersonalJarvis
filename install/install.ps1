# Personal Jarvis - Windows quick-install bootstrap (Stage 1)
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
#
# SOURCE-ENCODING RULE: this file is served BOM-less (a BOM breaks
# `irm | iex`), and Windows PowerShell then reads it as cp1252. So the
# source OUTSIDE the banner here-string stays pure ASCII: the glyphs (bullet,
# check, cross) are built from code points at runtime, never pasted as
# literals -- a literal check/dash would cp1252-decode into a smart quote and
# break tokenizing. Console output is forced to UTF-8 so they still render.

$ErrorActionPreference = 'Stop'
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'

# ----------------------------------------------------------------- terminal
# Render UTF-8 box/block glyphs and 24-bit color. Virtual-terminal (ANSI)
# processing is on by default on Windows 10 1511+ / Windows Terminal /
# modern PowerShell hosts.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

# 24-bit brand palette (Charcoal + Gold). Embedded as ANSI escapes so the
# whole line is one gold sweep. NB: brace the var -- "$e[..." would parse as
# an array index.
$e     = [char]27
$Gold  = "${e}[38;2;231;196;110m"
$Green = "${e}[38;2;122;200;140m"
$Dim   = "${e}[38;2;140;140;140m"
$Red   = "${e}[38;2;224;122;110m"
$Bold  = "${e}[1m"
$Rst   = "${e}[0m"

# Status glyphs from code points (keeps the source ASCII; see encoding rule).
$Dot = [char]0x25CF   # round bullet
$Chk = [char]0x2713   # check mark
$Crs = [char]0x2717   # cross mark

# ----------------------------------------------------------------- helpers
function Write-Banner([string]$Subtitle) {
    # Banner glyphs are machine-generated (figlet ANSI Shadow) and live inside
    # this here-string, where non-ASCII is syntactically inert. Do not
    # hand-edit -- that is how the historical Harvis typo crept in.
    $art = @"

$Gold   P  E  R  S  O  N  A  L$Rst
$Gold     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
$Gold     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
$Gold     ██║███████║██████╔╝██║   ██║██║███████╗
$Gold██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
$Gold╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
$Gold ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝$Rst

$Gold  $Dot$Rst $Bold$Subtitle$Rst
"@
    Write-Host $art
}

function Write-Step([string]$Text)  { Write-Host ""; Write-Host "$Gold  $Dot$Rst $Bold$Text$Rst" }
function Write-Ok([string]$Text)     { Write-Host "$Green    $Chk$Rst $Dim$Text$Rst" }
function Write-Note([string]$Text)   { Write-Host "$Dim      $Text$Rst" }
function Write-Err([string]$Text)    { Write-Host "$Red    $Crs $Text$Rst" }

Write-Banner 'Quick install - Windows'

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
Write-Step 'Checking prerequisites'

# Python: try `python` first, then `py -3.11`, then `py -3`.
$pythonExe = $null
$pythonVer = $null
foreach ($candidate in @('python', 'py')) {
    if (Test-PythonVersion $candidate) {
        $pythonExe = $candidate
        $info = Test-Tool $candidate
        if ($info.Version -match '(Python\s+\d+\.\d+\.\d+)') { $pythonVer = $Matches[1] } else { $pythonVer = $candidate }
        break
    }
}
if (-not $pythonExe) {
    Write-Err 'Python 3.11+ not found.'
    Write-Note 'Install it from https://www.python.org/downloads/ then re-run this command.'
    Write-Note '(After install, open a NEW PowerShell window so PATH refreshes.)'
    exit 1
}
Write-Ok "$pythonVer"

# Git
$gitCheck = Test-Tool 'git'
if (-not $gitCheck.Found) {
    Write-Err 'git not found.'
    Write-Note 'Install from https://git-scm.com/download/win then re-run this command.'
    exit 1
}
Write-Ok 'git'

# ----------------------------------------------------------------- clone / update
Write-Step 'Fetching Personal Jarvis'
Write-Note $InstallDir

if (Test-Path (Join-Path $InstallDir '.git')) {
    Push-Location $InstallDir
    try {
        # --quiet keeps the noisy "Receiving objects: NN%" churn out of the
        # clean transcript; real errors still surface on stderr.
        & git fetch --quiet --depth 1 origin $Branch
        & git checkout --quiet $Branch
        & git reset --quiet --hard "origin/$Branch"
    } finally {
        Pop-Location
    }
    Write-Ok 'updated existing checkout to latest'
} else {
    if (Test-Path $InstallDir) {
        Write-Err "$InstallDir exists but is not a git repo."
        Write-Note 'Aborting to avoid clobbering your files. Remove or move that directory, then re-run.'
        exit 1
    }
    & git clone --quiet --depth 1 --branch $Branch $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { Write-Err 'git clone failed.'; exit 1 }
    Write-Ok 'downloaded'
}

# WAVE 5 - payload-commit pin (axis E, Wave-5 audit Finding 2).
#
# install-verify.ps1 sets $env:JARVIS_PAYLOAD_COMMIT containing the
# signed payload commit SHA (Wave 1+2+4-authenticated). If set, bind the
# cloned tree to that exact commit so an attacker who flips `main` post-
# release cannot influence what we install. The signed SHA may be 40-char
# (git SHA-1) or 64-char (git SHA-256 repos).
if ($env:JARVIS_PAYLOAD_COMMIT) {
    $PayloadCommit = $env:JARVIS_PAYLOAD_COMMIT
    if ($PayloadCommit -notmatch '^[0-9a-f]{40}([0-9a-f]{24})?$') {
        Write-Err "JARVIS_PAYLOAD_COMMIT is not a well-formed git SHA: '$PayloadCommit' - refusing."
        exit 1
    }
    Push-Location $InstallDir
    try {
        # Shallow clones don't carry full history; deepen to retrieve the
        # target SHA explicitly. github.com defaults allow fetching arbitrary
        # SHAs on most repos; fall back to unshallow if direct-SHA fetch
        # fails (e.g. tag pushed but workflow ref-name was the merge commit).
        & git fetch --quiet --depth 1 origin $PayloadCommit 2>$null
        if ($LASTEXITCODE -ne 0) {
            & git fetch --quiet --unshallow origin
            if ($LASTEXITCODE -ne 0) { & git fetch --quiet origin }
        }
        & git checkout --quiet --detach $PayloadCommit
        if ($LASTEXITCODE -ne 0) {
            Write-Err "failed to checkout payload-commit $PayloadCommit - refusing."
            Write-Note 'the cloned tree does not contain the signed commit; release may be inconsistent.'
            exit 1
        }
        # Defensive verify: HEAD must be exactly the signed SHA byte-for-byte.
        $ActualHead = (& git rev-parse HEAD).Trim()
        if ($ActualHead -ne $PayloadCommit) {
            Write-Err "HEAD drift detected: pinned=$PayloadCommit, actual=$ActualHead - refusing."
            exit 1
        }
        Write-Ok "pinned to signed commit $($PayloadCommit.Substring(0,12))"
    } finally {
        Pop-Location
    }
}

# ----------------------------------------------------------------- venv
Write-Step 'Creating Python environment'

$VenvPath = Join-Path $InstallDir '.venv'
$VenvPython = Join-Path $VenvPath 'Scripts\python.exe'

if (-not (Test-Path $VenvPython)) {
    & $pythonExe -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { Write-Err 'venv creation failed.'; exit 1 }
}
Write-Ok 'virtual environment ready'

# ----------------------------------------------------------------- bootstrap deps
Write-Step 'Installing bootstrap dependencies'
Write-Note 'rich, packaging'
& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet rich packaging
if ($LASTEXITCODE -ne 0) { Write-Err 'bootstrap pip install failed.'; exit 1 }
Write-Ok 'done'

# ----------------------------------------------------------------- hand off
$InstallerPy = Join-Path $InstallDir 'install\installer.py'
if (-not (Test-Path $InstallerPy)) {
    Write-Err "$InstallerPy not found in the clone."
    Write-Note 'The repo seems incomplete. File a bug.'
    exit 1
}

Write-Step 'Launching the guided installer'

Push-Location $InstallDir
try {
    & $VenvPython $InstallerPy @ExtraArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
