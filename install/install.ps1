# Personal Jarvis - Windows quick-install bootstrap (Stage 1)
#
# Usage (from PowerShell):
#   irm https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.ps1 | iex
#
# This bootstrap is intentionally small. It:
#   1. Verifies Python 3.11+ and git are available.
#   2. Offers to install either missing prerequisite, then re-checks in place.
#   3. Checks for Node.js 18+ (optional - a missing Node never blocks the install).
#   4. Clones (or updates) personal-jarvis into ~\.personal-jarvis.
#   5. Creates a Python venv, installs `rich` + `packaging`.
#   6. Hands control to install/installer.py (the Stage 2 orchestrator).
#
# All heavy logic lives in installer.py so it can be unit-tested and
# kept cross-platform. The shell-native prerequisite block is delimited so it
# can be tested directly even on machines where Python is not installed yet.
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

# 24-bit brand palette (docs/BRAND.md): Signal Yellow on matte black, with the
# forged-gold wordmark gradient #FFE552 -> #FFD60A -> #B8960A. Embedded as ANSI
# escapes so the whole line is one gold sweep. NB: brace the var -- "$e[..."
# would parse as an array index.
$e        = [char]27
$GoldHi   = "${e}[38;2;255;229;82m"
$Gold     = "${e}[38;2;255;214;10m"
$GoldDeep = "${e}[38;2;184;150;10m"
$Green    = "${e}[38;2;122;200;140m"
$Dim      = "${e}[38;2;143;143;143m"
$Red      = "${e}[38;2;224;122;110m"
$Bold     = "${e}[1m"
$Rst      = "${e}[0m"

# Status glyphs from code points (keeps the source ASCII; see encoding rule).
$Chk = [char]0x2713   # check mark
$Crs = [char]0x2717   # cross mark

# Connected-journey glyphs (maintainer request 2026-07-16, visuals only):
# every line hangs off one continuous dim vertical gutter, phases are gold
# diamonds — the clack-style wizard grammar, recolored to the brand gold.
# Twins: the phase/ok/note/err helpers in install.sh and installer.py.
$Dia = [char]0x25C6   # black diamond - phase marker
$Gut = "$Dim$([char]0x2502)$Rst"   # dim gutter bar

# ----------------------------------------------------------------- helpers
function Write-Banner {
    # Banner glyphs are machine-generated (figlet ANSI Shadow) and live inside
    # this here-string, where non-ASCII is syntactically inert. Do not
    # hand-edit -- that is how the historical Harvis typo crept in. Rows are
    # colored as a vertical gradient (hi -> brand -> deep) to match the
    # forged-gold wordmark.
    $art = @"

$GoldHi                          ╷$Rst
$GoldHi                       ╭──┴──╮$Rst
$Gold                       │ ● ● │$Rst
$Gold                       │  ─  │$Rst
$GoldDeep                       ╰─────╯$Rst

$GoldHi     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗$Rst
$GoldHi     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝$Rst
$Gold     ██║███████║██████╔╝██║   ██║██║███████╗$Rst
$Gold██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║$Rst
$GoldDeep╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║$Rst
$GoldDeep ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝$Rst

$Dim     P E R S O N A L  J A R V I S   ·   talk to your computer$Rst
$Dim     Checks prerequisites · installs the full profile · launches when done$Rst

$Dim┌$Rst  ${Gold}Personal Jarvis installer$Rst
"@
    Write-Host $art
}

# One six-phase journey spans BOTH installer stages: this shell owns phases
# 1-3, installer.py continues with 4-6 -- keep the numbering in sync there.
function Write-Phase([string]$Num, [string]$Text) { Write-Host $Gut; Write-Host "$Gold$Dia$Rst  $Gold$Num$Rst  $Bold$Text$Rst" }
function Write-Ok([string]$Text)     { Write-Host "$Gut  $Green$Chk$Rst $Dim$Text$Rst" }
function Write-Note([string]$Text)   { Write-Host "$Gut    $Dim$Text$Rst" }
function Write-Err([string]$Text)    { Write-Host "$Gut  $Red$Crs $Text$Rst" }

Write-Banner

# ----------------------------------------------------------------- config
$RepoUrl    = if ($env:JARVIS_INSTALL_REPO) { $env:JARVIS_INSTALL_REPO } else { 'https://github.com/PersonalJarvis/PersonalJarvis.git' }
$Branch     = if ($env:JARVIS_INSTALL_REF)  { $env:JARVIS_INSTALL_REF }  else { 'main' }
$InstallDir = if ($env:JARVIS_INSTALL_DIR)  { $env:JARVIS_INSTALL_DIR }  else { Join-Path $env:USERPROFILE '.personal-jarvis' }
$PrerequisiteMode = if ($env:JARVIS_INSTALL_PREREQS) { $env:JARVIS_INSTALL_PREREQS.ToLowerInvariant() } else { 'ask' }
$InitialPath = $env:Path

# Forward any extra args to installer.py (e.g. --no-launch, --dry-run, --with-voice-local)
$ExtraArgs = $args

# --- prerequisite-bootstrap begin ------------------------------------------
# This block stays shell-native because Python may not exist yet. Tests extract
# it directly and exercise the retry/continuation state machine with fakes.
function Test-Tool {
    param([string]$Name, [string[]]$VersionArgs = @('--version'))
    try {
        $out = & $Name @VersionArgs 2>&1
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        return @{
            Found = ($code -eq 0)
            Version = [string]($out | Select-Object -First 1)
        }
    } catch {
        return @{ Found = $false; Version = $null }
    }
}

function Test-PythonCandidate {
    param([string]$Exe)
    try {
        # Keep the Python snippet quote-free. Windows PowerShell 5's legacy
        # native-argument marshaller can strip nested quote characters even
        # when the PowerShell string itself is correctly quoted.
        $probe = 'import sys; print(sys.version_info.major, sys.version_info.minor,' +
            'sys.version_info.micro, sep=chr(46))'
        $out = & $Exe -c $probe 2>&1
        if ($LASTEXITCODE -ne 0) { return $null }
        $version = [string]($out | Select-Object -First 1)
        if ($version -notmatch '^(\d+)\.(\d+)\.(\d+)$') { return $null }
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        return [pscustomobject]@{
            Exe = $Exe
            Version = $version
            Compatible = (($major -gt 3) -or ($major -eq 3 -and $minor -ge 11))
        }
    } catch {
        return $null
    }
}

function Find-CompatiblePython {
    $candidates = @()
    if ($env:JARVIS_PYTHON) {
        # An explicit pin is authoritative: never silently substitute another
        # interpreter for the one the user selected.
        $candidates += $env:JARVIS_PYTHON
    } else {
        $candidates += @('python', 'python3', 'py')
        $patterns = @()
        if ($env:LOCALAPPDATA) {
            $patterns += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python*\python.exe'
        }
        if ($env:ProgramFiles) {
            $patterns += Join-Path $env:ProgramFiles 'Python*\python.exe'
        }
        foreach ($pattern in $patterns) {
            $candidates += @(Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue |
                Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName)
        }
    }

    $closest = $null
    $seen = @{}
    foreach ($candidate in $candidates) {
        if (-not $candidate) { continue }
        $key = $candidate.ToLowerInvariant()
        if ($seen[$key]) { continue }
        $seen[$key] = $true
        $probe = Test-PythonCandidate $candidate
        if ($null -eq $probe) { continue }
        if ($probe.Compatible) {
            return [pscustomobject]@{
                Found = $true
                Exe = $probe.Exe
                Version = $probe.Version
                Closest = $closest
            }
        }
        if ($null -eq $closest) { $closest = $probe }
    }
    return [pscustomobject]@{
        Found = $false
        Exe = $null
        Version = $null
        Closest = $closest
    }
}

function Get-PrerequisiteState {
    $python = Find-CompatiblePython
    $gitCheck = Test-Tool 'git'
    return [pscustomobject]@{
        Python = $python
        GitFound = $gitCheck.Found
        GitVersion = $gitCheck.Version
        Ready = ($python.Found -and $gitCheck.Found)
    }
}

function Write-PrerequisiteState {
    param($State, [switch]$ShowMissing)
    if ($State.Python.Found) {
        Write-Ok "Python $($State.Python.Version) ($($State.Python.Exe))"
    } elseif ($ShowMissing) {
        Write-Err 'Python 3.11+ not found.'
        if ($null -ne $State.Python.Closest) {
            Write-Note "Closest match: Python $($State.Python.Closest.Version) via '$($State.Python.Closest.Exe)' - too old."
            Write-Note 'Python versions count 3.8 < 3.9 < 3.10 < 3.11.'
        }
    }
    if ($State.GitFound) {
        Write-Ok $State.GitVersion
    } elseif ($ShowMissing) {
        Write-Err 'git not found.'
    }
}

function Get-MissingPrerequisiteLabels {
    param($State)
    $missing = @()
    if (-not $State.Python.Found) { $missing += 'Python 3.12 (satisfies 3.11+)' }
    if (-not $State.GitFound) { $missing += 'Git' }
    return $missing
}

function Test-InstallPromptAvailable {
    if (-not [Environment]::UserInteractive) { return $false }
    try {
        # `irm ... | iex` is a PowerShell object pipeline, not redirected
        # console input, so it remains prompt-capable. CI/stdin pipelines do
        # not, and must opt in explicitly with JARVIS_INSTALL_PREREQS=auto.
        return (-not [Console]::IsInputRedirected)
    } catch {
        # Hosts such as PowerShell ISE have no console but provide Read-Host.
        return $true
    }
}

function Request-PrerequisiteConsent {
    param([string[]]$Missing)
    switch ($PrerequisiteMode) {
        'auto' {
            Write-Note 'Automatic prerequisite installation was enabled by JARVIS_INSTALL_PREREQS=auto.'
            return $true
        }
        'never' { return $false }
        'ask' { }
        default {
            Write-Err "Invalid JARVIS_INSTALL_PREREQS value '$PrerequisiteMode'. Use ask, auto, or never."
            return $false
        }
    }

    if (-not (Test-InstallPromptAvailable)) {
        Write-Note 'This shell cannot ask for consent. Re-run interactively or set JARVIS_INSTALL_PREREQS=auto.'
        return $false
    }
    Write-Note "Missing required software: $($Missing -join ', ')."
    Write-Note 'Jarvis can install it with WinGet, wait for completion, and continue this same run.'
    Write-Note 'Continuing also accepts the package and WinGet source agreements for these packages.'
    try {
        $answer = Read-Host '  Install the missing prerequisites now? [Y/n]'
    } catch {
        return $false
    }
    return ([string]::IsNullOrWhiteSpace($answer) -or $answer -match '^(?i:y|yes)$')
}

function Refresh-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $commonGitPaths = @()
    if ($env:ProgramFiles) {
        $commonGitPaths += Join-Path $env:ProgramFiles 'Git\cmd'
    }
    if ($env:LOCALAPPDATA) {
        $commonGitPaths += Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd'
    }
    $commonGitPaths = @($commonGitPaths | Where-Object { Test-Path $_ })
    $env:Path = ((@($InitialPath, $userPath, $machinePath) + $commonGitPaths |
        Where-Object { $_ }) -join ';')
}

function Invoke-PrerequisitePackage {
    param([string]$PackageId, [string]$Label)
    Write-Note "installing $Label (this may open one Windows approval prompt)"
    $output = & winget install --id $PackageId --exact --source winget --silent `
        --disable-interactivity --accept-source-agreements --accept-package-agreements 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Write-Err "$Label installation did not complete (WinGet exit $code)."
        @($output | Select-Object -Last 8) | ForEach-Object { Write-Note ([string]$_) }
        return $false
    }
    Write-Ok "$Label installer completed"
    return $true
}

function Invoke-MissingPrerequisiteInstall {
    param($State)
    $wingetCheck = Test-Tool 'winget'
    if (-not $wingetCheck.Found) {
        Write-Err 'WinGet is not available, so Jarvis cannot install the prerequisites automatically.'
        return $false
    }
    $installOk = $true
    if (-not $State.Python.Found) {
        if (-not (Invoke-PrerequisitePackage 'Python.Python.3.12' 'Python 3.12')) {
            $installOk = $false
        }
    }
    if (-not $State.GitFound) {
        if (-not (Invoke-PrerequisitePackage 'Git.Git' 'Git')) {
            $installOk = $false
        }
    }
    return $installOk
}

function Wait-ForPrerequisites {
    param([int]$Seconds = 10)
    $attempts = [Math]::Max(1, [int][Math]::Ceiling($Seconds / 2.0))
    $state = $null
    for ($i = 0; $i -lt $attempts; $i++) {
        Refresh-ProcessPath
        $state = Get-PrerequisiteState
        if ($state.Ready) { return $state }
        if ($i -lt ($attempts - 1)) { Start-Sleep -Seconds 2 }
    }
    return $state
}

function Write-ManualPrerequisiteHelp {
    param($State)
    if (-not $State.Python.Found) {
        Write-Note 'Python: https://www.python.org/downloads/windows/'
    }
    if (-not $State.GitFound) {
        Write-Note 'Git:    https://git-scm.com/download/win'
    }
}

function Ensure-Prerequisites {
    Refresh-ProcessPath
    $state = Get-PrerequisiteState
    Write-PrerequisiteState $state -ShowMissing
    if ($state.Ready) { return $state }
    if ($env:JARVIS_PYTHON -and -not $state.Python.Found) {
        Write-Note "JARVIS_PYTHON is pinned to '$($env:JARVIS_PYTHON)' and is not a compatible interpreter."
        Write-Note 'Update or unset that pin before prerequisite installation.'
        return $null
    }

    $missing = @(Get-MissingPrerequisiteLabels $state)
    if (-not (Request-PrerequisiteConsent $missing)) {
        Write-ManualPrerequisiteHelp $state
        Write-Note 'Nothing was installed. Run this command again after adding the prerequisites.'
        return $null
    }

    [void](Invoke-MissingPrerequisiteInstall $state)
    $state = Wait-ForPrerequisites

    while (-not $state.Ready) {
        Write-Err 'The required commands are still unavailable in this terminal.'
        Write-ManualPrerequisiteHelp $state
        if ($PrerequisiteMode -eq 'auto' -or -not (Test-InstallPromptAvailable)) {
            return $null
        }
        try {
            $answer = Read-Host '  Finish any manual installer, then press Enter to re-check; R retries WinGet, Q stops'
        } catch {
            return $null
        }
        if ($answer -match '^(?i:q|quit)$') { return $null }
        if ($answer -match '^(?i:r|retry)$') {
            [void](Invoke-MissingPrerequisiteInstall $state)
        }
        $state = Wait-ForPrerequisites
    }

    Write-PrerequisiteState $state
    return $state
}
# --- prerequisite-bootstrap end --------------------------------------------

# ----------------------------------------------------------------- preflight
Write-Phase '1/6' 'Prerequisites'

$prerequisites = Ensure-Prerequisites
if ($null -eq $prerequisites) {
    Write-Err 'Prerequisite setup was not completed.'
    exit 1
}
$pythonExe = $prerequisites.Python.Exe
$pythonVer = "Python $($prerequisites.Python.Version)"

# Node.js 18+ -- powers only the OPTIONAL Jarvis-Agent worker CLIs (Claude
# Code / Codex) that heavy missions delegate to, plus the Node-based
# marketplace integrations. Everything else in Jarvis runs without it, so a
# missing Node must NEVER turn a new user away at the door: we note it and
# continue -- the worker CLI can be added later in-app once Node is installed.
# Skipped entirely on the headless / tiny-VPS path (--headless): a cloud-only
# base install that never spawns a local CLI worker.
if ($ExtraArgs -contains '--headless') {
    Write-Note 'Node.js check skipped (--headless): the cloud-only base install does not use it.'
} else {
    $nodeOk = $false
    $nodeCheck = Test-Tool 'node'
    if ($nodeCheck.Found -and $nodeCheck.Version -match 'v?(\d+)\.\d+\.\d+') {
        $nodeOk = ([int]$Matches[1] -ge 18)
    }
    if ($nodeOk) {
        Write-Ok "Node.js $($nodeCheck.Version)"
    } else {
        Write-Note 'Node.js 18+ not found - continuing, Jarvis runs fine without it.'
        Write-Note 'It only powers the optional coding-agent worker (Claude Code / Codex).'
        Write-Note 'Install the LTS build any time from https://nodejs.org/ and add the'
        Write-Note 'worker later in-app.'
    }
}

# ----------------------------------------------------------------- clone / update
Write-Phase '2/6' 'Fetching Personal Jarvis'
Write-Note $InstallDir

# On an interactive console, git paints its own "Receiving objects: NN%"
# progress (on stderr) so a slow download never looks hung (maintainer
# report 2026-07-15); a redirected/CI run keeps --quiet for a clean
# transcript. Real errors still surface on stderr either way.
$GitVerbosity = if ([Console]::IsErrorRedirected) { '--quiet' } else { '--progress' }
if (Test-Path (Join-Path $InstallDir '.git')) {
    Push-Location $InstallDir
    try {
        & git fetch $GitVerbosity --depth 1 origin $Branch
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
    & git clone $GitVerbosity --depth 1 --branch $Branch $RepoUrl $InstallDir
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

# ----------------------------------------------------------------- venv + bootstrap deps
Write-Phase '3/6' 'Python environment'

$VenvPath = Join-Path $InstallDir '.venv'
$VenvPython = Join-Path $VenvPath 'Scripts\python.exe'

# Update runs: stop any Jarvis still running out of THIS install before we
# touch its environment. A live app (often revived by the login autostart)
# keeps serving stale, half-updated features while pip rewrites the venv
# under it - the "app is already open but nothing works yet" field report
# (2026-07-14; POSIX twin: the pkill block in install.sh). On Windows the
# running pythonw.exe additionally holds venv DLLs open, which can make
# pip's in-place upgrade fail. The installer relaunches a fresh instance
# as its very last step. Best-effort: a lookup/stop failure never blocks
# the install.
if (Test-Path $VenvPython) {
    try {
        $venvPrefix = $VenvPath.TrimEnd('\') + '\'
        $stale = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop |
            Where-Object {
                $_.ExecutablePath -and
                $_.ExecutablePath.StartsWith($venvPrefix, [System.StringComparison]::OrdinalIgnoreCase) -and
                $_.ProcessId -ne $PID
            })
        if ($stale.Count -gt 0) {
            foreach ($proc in $stale) {
                try { Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop } catch {}
            }
            # Give the OS a beat to release file handles before pip writes.
            Start-Sleep -Milliseconds 500
            Write-Note 'stopped the running Jarvis app for the update'
        }
    } catch {
        Write-Note 'could not check for a running Jarvis app - continuing'
    }
}

if (-not (Test-Path $VenvPython)) {
    & $pythonExe -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { Write-Err 'venv creation failed.'; exit 1 }
}
Write-Ok 'virtual environment ready'

Write-Note 'installing bootstrap dependencies (rich, packaging) - this can take a moment'
& $VenvPython -m pip install --quiet --upgrade pip rich packaging
if ($LASTEXITCODE -ne 0) { Write-Err 'bootstrap pip install failed.'; exit 1 }
Write-Ok 'bootstrap dependencies ready'

# ----------------------------------------------------------------- hand off
$InstallerPy = Join-Path $InstallDir 'install\installer.py'
if (-not (Test-Path $InstallerPy)) {
    Write-Err "$InstallerPy not found in the clone."
    Write-Note 'The repo seems incomplete. File a bug.'
    exit 1
}

Write-Note 'handing over to the Python installer (phases 4-6)'

Push-Location $InstallDir
try {
    & $VenvPython $InstallerPy @ExtraArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
