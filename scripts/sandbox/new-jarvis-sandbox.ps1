<#
.SYNOPSIS
    Provision a fully isolated, throwaway Personal Jarvis sandbox from a fresh
    clone of the PUBLIC distribution repo -- so the maintainer experiences exactly
    what a stranger downloading from GitHub gets, WITHOUT touching the real
    install's config, data, or credentials.

.DESCRIPTION
    Design: docs/superpowers/specs/2026-06-24-jarvis-sandbox-testing-design.md.

    The sandbox seals the four shared-state seams that would otherwise let a
    second native instance collide with the real install:

      1. Python import  -- a dedicated venv inside the sandbox dir. The editable
                           install pins ONLY that venv; the global 'import jarvis'
                           is untouched (defeats the BUG-006/014/015 restore trap).
      2. Credentials    -- PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring
                           redirects keyring to an isolated throwaway file. The
                           real 'personal-jarvis' Windows Credential Manager
                           namespace is NEVER read or written.
      3. Config         -- JARVIS_CONFIG points at <sandbox>\jarvis.toml.
      4. Data           -- a separate clone makes PROJECT_ROOT resolve into the
                           sandbox, so data\ lands in <sandbox>\data.

    Computer-Use is forced OFF (the sandbox can talk and show its UI, but never
    drives the physical desktop). Voice stays ON.

    The provisioner runs the documented stranger steps, redirected into the
    sandbox, then proves isolation BEFORE generating the launch command. It does
    NOT auto-launch: voice shares the one set of speakers/mic with the real app,
    so YOU close the real Jarvis first, then run the printed run-sandbox.ps1.

.PARAMETER SandboxRoot
    Where to build the sandbox. Default: a 'Jarvis-Sandbox' sibling of this repo.

.PARAMETER RepoUrl
    Source repo. Default: the public flagship PersonalJarvis/PersonalJarvis.

.PARAMETER Ref
    Branch or tag to clone. Default: main (the published default branch).

.PARAMETER Port
    Web/admin port for the sandbox (kept off the real app's 47821). Default 47830.

.PARAMETER Force
    Delete an existing sandbox dir (to the Recycle Bin) and re-provision.

.PARAMETER Launch
    After provisioning + proofs, launch immediately. Default OFF -- the script
    prints the launch command so YOU control the mic handoff.

.EXAMPLE
    powershell -File scripts\sandbox\new-jarvis-sandbox.ps1
#>
[CmdletBinding()]
param(
    [string]$SandboxRoot,
    [string]$RepoUrl = "https://github.com/PersonalJarvis/PersonalJarvis.git",
    [string]$Ref = "main",
    [int]$Port = 47830,
    [switch]$Force,
    [switch]$Launch
)

$ErrorActionPreference = "Stop"
$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Write-Step([string]$msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "  [ok] $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg) { Write-Host "  [warn] $msg" -ForegroundColor Yellow }
function Fail([string]$msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red; exit 1 }

# Default sandbox root = sibling of the repo (keeps the editable-install pin away
# from the real tree; the parent dir is not a git repo, so no collision).
if (-not $SandboxRoot -or $SandboxRoot.Trim() -eq "") {
    $SandboxRoot = (Join-Path (Split-Path $script:RepoRoot -Parent) "Jarvis-Sandbox")
}

Write-Step "Personal Jarvis -- isolated sandbox provisioner"
Write-Host ("  source : {0} (ref {1})" -f $RepoUrl, $Ref)
Write-Host ("  target : {0}" -f $SandboxRoot)
Write-Host ("  port   : {0}  (real app stays on 47821)" -f $Port)

# --- 1. Preconditions ------------------------------------------------------
Write-Step "1. Preconditions"
$gitCmd = (Get-Command git -ErrorAction SilentlyContinue)
if (-not $gitCmd) { Fail "git not found on PATH." }
Write-Ok ("git: {0}" -f $gitCmd.Source)

# Host Python >= 3.11. Prefer the known interpreter, fall back to PATH.
$pyExe = "C:\Program Files\Python311\python.exe"
if (-not (Test-Path $pyExe)) {
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pyCmd) { Fail "Python 3.11+ not found." }
    $pyExe = $pyCmd.Source
}
$pyVerRaw = & $pyExe -c "import sys; print(sys.version_info[0], sys.version_info[1])"
$pyParts = $pyVerRaw.Trim() -split '\s+'
$pyMajor = [int]$pyParts[0]; $pyMinor = [int]$pyParts[1]
if ($pyMajor -lt 3 -or ($pyMajor -eq 3 -and $pyMinor -lt 11)) { Fail ("Python {0}.{1} found; need >= 3.11." -f $pyMajor, $pyMinor) }
Write-Ok ("python: {0} ({1}.{2})" -f $pyExe, $pyMajor, $pyMinor)

# Capture the GLOBAL 'import jarvis' origin BEFORE we touch anything. Run from a
# NEUTRAL dir so the current directory's ./jarvis cannot shadow the real answer
# (python -c puts the CWD on sys.path[0]).
Push-Location $env:TEMP
$globalJarvisBefore = & $pyExe -c "import importlib.util as u; s=u.find_spec('jarvis'); print(s.origin if s else 'NONE')" 2>$null
Pop-Location
if (-not $globalJarvisBefore) { $globalJarvisBefore = "NONE" }
Write-Ok ("global 'import jarvis' baseline: {0}" -f $globalJarvisBefore)

# --- 2. Sandbox dir --------------------------------------------------------
Write-Step "2. Sandbox directory"
if (Test-Path $SandboxRoot) {
    if (-not $Force) { Fail ("{0} already exists. Re-run with -Force to replace it." -f $SandboxRoot) }
    Write-Warn2 "exists -- moving the old sandbox to the Recycle Bin"
    Add-Type -AssemblyName Microsoft.VisualBasic
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($SandboxRoot, 'OnlyErrorDialogs', 'SendToRecycleBin')
}

# --- 3. Fresh clone (the truest clean room) --------------------------------
Write-Step "3. Fresh clone from GitHub"
& git clone --branch $Ref --single-branch $RepoUrl $SandboxRoot
if ($LASTEXITCODE -ne 0) { Fail "git clone failed (private repo? run 'gh auth status' / 'gh auth setup-git')." }
$clonedHead = (& git -C $SandboxRoot rev-parse --short HEAD)
Write-Ok ("cloned {0} at {1}" -f $Ref, $clonedHead)

# --- 4. Isolated venv + install (the documented stranger steps) ------------
Write-Step "4. Isolated venv + install"
$venv = Join-Path $SandboxRoot ".venv"
& $pyExe -m venv $venv
if ($LASTEXITCODE -ne 0) { Fail "venv creation failed." }
$venvPy = Join-Path $venv "Scripts\python.exe"
Write-Ok ("venv: {0}" -f $venv)

Write-Host "  installing (this takes a few minutes)..."
& $venvPy -m pip install --upgrade pip --quiet
Push-Location $SandboxRoot
try {
    & $venvPy -m pip install -e ".[full]" --quiet
    if ($LASTEXITCODE -ne 0) { Fail "pip install -e .[full] failed." }
    & $venvPy -m pip install "keyrings.alt" --quiet
    if ($LASTEXITCODE -ne 0) { Fail "pip install keyrings.alt failed." }
} finally { Pop-Location }
Write-Ok "installed .[full] + keyrings.alt into the sandbox venv"

# --- 5. Seed isolated config (Computer-Use OFF) ----------------------------
Write-Step "5. Seed sandbox config"
$sandboxConfig = Join-Path $SandboxRoot "jarvis.toml"
$exampleConfig = Join-Path $SandboxRoot "jarvis.toml.example"
if (Test-Path $exampleConfig) { Copy-Item $exampleConfig $sandboxConfig -Force }
else { Set-Content -Path $sandboxConfig -Value "# sandbox config" -Encoding utf8 }
$cuOverride = "`r`n# --- sandbox override: never drive the physical desktop ---`r`n[computer_use]`r`nenabled = false`r`n"
Add-Content -Path $sandboxConfig -Value $cuOverride -Encoding utf8
Write-Ok ("wrote {0} (computer_use.enabled = false)" -f $sandboxConfig)

$sandboxEnv = Join-Path $SandboxRoot ".env"
if (-not (Test-Path $sandboxEnv)) {
    $envTemplate = "# Sandbox-local keys (optional). Paste ONE brain-provider key to talk to the`r`n# test copy without onboarding, e.g. GEMINI_API_KEY=... / ANTHROPIC_API_KEY=...`r`n# These never touch your real Credential Manager.`r`n"
    Set-Content -Path $sandboxEnv -Value $envTemplate -Encoding utf8
    Write-Ok "wrote a blank sandbox .env (add a lent key, or use first-run onboarding)"
}

# --- 6. Generate the launch command ----------------------------------------
Write-Step "6. Generate run-sandbox.ps1"
$runScript = Join-Path $SandboxRoot "run-sandbox.ps1"
$dataDir = Join-Path $SandboxRoot "data"
# Redirect the OS user-data root into the sandbox too. Several subsystems (board
# stats, user skills, contacts, cli_ctl config) resolve their location from
# user_data_dir() == %LOCALAPPDATA%\Jarvis (platformdirs), NOT from
# JARVIS_DATA_DIR -- so without this they would write into the REAL shared
# %LOCALAPPDATA%\Jarvis and contaminate the real install's board/skills. Pointing
# LOCALAPPDATA at a sandbox-local dir seals that last seam (a fresh stranger has a
# fresh AppData).
$appLocal = Join-Path $SandboxRoot "appdata\Local"
New-Item -ItemType Directory -Force -Path $appLocal | Out-Null
$runLines = @(
    "# Launch the ISOLATED Jarvis sandbox. Generated by new-jarvis-sandbox.ps1.",
    "# Close your REAL Jarvis first -- voice shares the one mic/speakers.",
    "`$ErrorActionPreference = 'Stop'",
    ("Set-Location '{0}'" -f $SandboxRoot),
    ("`$env:JARVIS_CONFIG = '{0}'" -f $sandboxConfig),
    ("`$env:JARVIS_DATA_DIR = '{0}'" -f $dataDir),
    ("`$env:LOCALAPPDATA = '{0}'" -f $appLocal),
    "`$env:PYTHON_KEYRING_BACKEND = 'keyrings.alt.file.PlaintextKeyring'",
    "`$env:JARVIS_VOICE = '1'",
    ("Write-Host 'Launching isolated Jarvis sandbox on port {0} (Computer-Use OFF)...' -ForegroundColor Cyan" -f $Port),
    ("& '{0}' -m jarvis.ui.web.launcher --no-lock --port {1}" -f $venvPy, $Port)
)
Set-Content -Path $runScript -Value ($runLines -join "`r`n") -Encoding utf8
Write-Ok ("wrote {0}" -f $runScript)

# --- 7. Prove isolation BEFORE any launch ----------------------------------
Write-Step "7. Isolation proofs"
$allPass = $true

# Run the import checks from a NEUTRAL dir so neither the real repo's nor the
# sandbox's ./jarvis on sys.path[0] (the CWD) can mask the editable install each
# interpreter actually resolves. This is what makes the proof trustworthy.
Push-Location $env:TEMP
$sandboxImport = & $venvPy -c "import jarvis; print(jarvis.__file__)" 2>$null
$globalAfter = & $pyExe -c "import importlib.util as u; s=u.find_spec('jarvis'); print(s.origin if s else 'NONE')" 2>$null
Pop-Location
if ($sandboxImport -and $sandboxImport.StartsWith($SandboxRoot)) { Write-Ok ("sandbox 'import jarvis' -> {0}" -f $sandboxImport) }
else { Write-Warn2 ("sandbox import origin unexpected: {0}" -f $sandboxImport); $allPass = $false }
if (-not $globalAfter) { $globalAfter = "NONE" }
if ($globalAfter -eq $globalJarvisBefore) { Write-Ok ("global 'import jarvis' unchanged ({0})" -f $globalAfter) }
else { Write-Warn2 ("global import CHANGED: '{0}' -> '{1}'" -f $globalJarvisBefore, $globalAfter); $allPass = $false }

$env:PYTHON_KEYRING_BACKEND = 'keyrings.alt.file.PlaintextKeyring'
$kr = & $venvPy -c "import keyring; print(type(keyring.get_keyring()).__name__)" 2>$null
$env:PYTHON_KEYRING_BACKEND = $null
if ($kr -and $kr -notmatch "WinVault") { Write-Ok ("sandbox keyring backend: {0} (not WinVaultKeyring)" -f $kr) }
else { Write-Warn2 ("keyring backend looks wrong: {0}" -f $kr); $allPass = $false }

# user_data_dir() (board stats, skills, contacts, cli config) must resolve INTO
# the sandbox, not the shared %LOCALAPPDATA%\Jarvis. Check it with the sandbox's
# LOCALAPPDATA override in effect.
$savedLAD = $env:LOCALAPPDATA
$env:LOCALAPPDATA = $appLocal
Push-Location $env:TEMP
$udd = & $venvPy -c "from jarvis.core.paths import user_data_dir; print(user_data_dir())" 2>$null
Pop-Location
$env:LOCALAPPDATA = $savedLAD
if ($udd -and $udd.StartsWith($SandboxRoot)) { Write-Ok ("sandbox user-data dir -> {0}" -f $udd) }
else { Write-Warn2 ("user-data dir NOT isolated (board/skills would touch the real install): {0}" -f $udd); $allPass = $false }

if (Test-Path (Join-Path $SandboxRoot "jarvis\ui\web\dist\index.html")) { Write-Ok "prebuilt UI present -- no Node build needed" }
else { Write-Warn2 "no prebuilt UI found -- the clone may need a frontend build"; $allPass = $false }

# --- 8. Summary ------------------------------------------------------------
Write-Step "Done"
if ($allPass) { Write-Host "  All isolation proofs PASSED." -ForegroundColor Green }
else { Write-Warn2 "Some proofs did not pass cleanly -- review the [warn] lines above." }

Write-Host ""
Write-Host ("  Your isolated test copy is ready at: {0}" -f $SandboxRoot) -ForegroundColor White
Write-Host "  To talk to it:" -ForegroundColor White
Write-Host "    1. CLOSE your real Jarvis (it owns the mic/speakers)." -ForegroundColor White
Write-Host ("    2. (optional) add a provider key to: {0}" -f $sandboxEnv) -ForegroundColor White
Write-Host ("    3. Run:  powershell -File `"{0}`"" -f $runScript) -ForegroundColor White
Write-Host ""
Write-Host ("  Runs on port {0}, Computer-Use OFF, walled off from your real config/data/keys." -f $Port) -ForegroundColor DarkGray
Write-Host "  Remove it any time with remove-jarvis-sandbox.ps1 (goes to the Recycle Bin)." -ForegroundColor DarkGray

if ($Launch) {
    Write-Step "Launching now (-Launch)"
    Write-Warn2 "Make sure your real Jarvis is closed (shared mic)."
    & $runScript
}
