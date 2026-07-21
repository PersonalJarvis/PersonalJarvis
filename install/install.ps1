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
    # The mascot is the REAL Jarvis logo (the gold-eyed ghost), machine-
    # generated as xterm-256 half-block pixel art from the brand PNG and
    # stored base64-encoded (the decoded UTF-8 carries real ESC bytes) so
    # this source file stays ASCII. Regenerate from a new logo file rather
    # than hand-editing. Skipped when output is redirected: without color
    # escapes the half blocks would render as shapeless mush.
    if (-not [Console]::IsOutputRedirected) {
        $mascotB64 = @'
ICAgICAgICAgICAgICAgIBtbMG0gICAgICAgICAgIBtbMG0bWzM4OzU7MTAwbeKWhBtbMG0bWzM4OzU7NTht4paE4paEG1sw
bRtbMzg7NTsyMzVt4paE4paE4paE4paE4paE4paEG1swbRtbMzg7NTs1OG3iloTiloQbWzBtG1szODs1OzEwMG3iloQbWzBt
ICAgICAgICAgICAbWzBtCiAgICAgICAgICAgICAgICAbWzBtICAgICAgIBtbMG0bWzM4OzU7OTRt4paEG1swbRtbMzg7NTsy
MzRt4paEG1swbRtbMzg7NTs1OG0bWzQ4OzU7MjM0beKWgBtbMG0bWzM4OzU7MjM0bRtbNDg7NTsyMzRt4paA4paA4paA4paA
4paA4paA4paA4paA4paA4paA4paA4paA4paA4paAG1swbRtbMzg7NTs1OG0bWzQ4OzU7MjM0beKWgBtbMG0bWzM4OzU7MjM0
beKWhBtbMG0bWzM4OzU7OTRt4paEG1swbSAgICAgICAbWzBtCiAgICAgICAgICAgICAgICAbWzBtICAgICAbWzBtG1szODs1
OzEwMG3iloQbWzBtG1szODs1Ozk0bRtbNDg7NTsyMzNt4paAG1swbRtbMzg7NTsyMzRtG1s0ODs1OzIzNG3iloDiloDiloDi
loDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloAbWzBtG1szODs1Ozk0bRtbNDg7NTsy
MzNt4paAG1swbRtbMzg7NTsxMDBt4paEG1swbSAgICAgG1swbQogICAgICAgICAgICAgICAgG1swbSAgICAgG1swbRtbMzg7
NTs1OG0bWzQ4OzU7MjMzbeKWgBtbMG0bWzM4OzU7MjM0bRtbNDg7NTsyMzRt4paA4paA4paA4paA4paA4paA4paA4paA4paA
4paA4paA4paA4paA4paA4paA4paA4paA4paA4paA4paA4paA4paAG1swbRtbMzg7NTs1OG0bWzQ4OzU7MjM0beKWgBtbMG0g
ICAgIBtbMG0KICAgICAgICAgICAgICAgIBtbMG0gICAgG1swbRtbMzg7NTs1OG0bWzQ4OzU7NTht4paAG1swbRtbMzg7NTsy
MzRtG1s0ODs1OzIzNG3iloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDiloDi
loDiloDiloDiloDiloAbWzBtG1szODs1OzU4bRtbNDg7NTs1OG3iloAbWzBtICAgIBtbMG0KICAgICAgICAgICAgICAgIBtb
MG0gICAgG1swbRtbMzg7NTs1OG0bWzQ4OzU7NTht4paAG1swbRtbMzg7NTsyMzRtG1s0ODs1OzIzNG3iloDiloDiloDiloDi
loDiloAbWzBtG1szODs1OzIzNG0bWzQ4OzU7NTht4paAG1swbRtbMzg7NTsyMzRtG1s0ODs1OzEwMG3iloAbWzBtG1szODs1
OzIzNG0bWzQ4OzU7MjM2beKWgBtbMG0bWzM4OzU7MjM0bRtbNDg7NTsyMzRt4paA4paA4paA4paA4paA4paAG1swbRtbMzg7
NTsyMzRtG1s0ODs1OzIzNm3iloAbWzBtG1szODs1OzIzNG0bWzQ4OzU7MTAwbeKWgBtbMG0bWzM4OzU7MjM0bRtbNDg7NTs1
OG3iloAbWzBtG1szODs1OzIzNG0bWzQ4OzU7MjM0beKWgOKWgOKWgOKWgOKWgOKWgBtbMG0bWzM4OzU7NThtG1s0ODs1OzU4
beKWgBtbMG0gICAgG1swbQogICAgICAgICAgICAgICAgG1swbSAgG1swbRtbMzg7NTsxNzht4paAG1swbSAbWzBtG1szODs1
OzU4bRtbNDg7NTs1OG3iloAbWzBtG1szODs1OzIzNG0bWzQ4OzU7MjM0beKWgOKWgOKWgOKWgOKWgBtbMG0bWzM4OzU7NTht
G1s0ODs1OzE0Mm3iloAbWzBtG1szODs1OzIyMG0bWzQ4OzU7MjIwbeKWgBtbMG0bWzM4OzU7MjI2bRtbNDg7NTsyMjZt4paA
G1swbRtbMzg7NTsxNDJtG1s0ODs1OzIyMG3iloAbWzBtG1szODs1OzIzNm0bWzQ4OzU7NTht4paAG1swbRtbMzg7NTsyMzRt
G1s0ODs1OzIzNG3iloDiloDiloDiloAbWzBtG1szODs1OzIzNm0bWzQ4OzU7NTht4paAG1swbRtbMzg7NTsxNDJtG1s0ODs1
OzIyMG3iloAbWzBtG1szODs1OzIyNm0bWzQ4OzU7MjIwbeKWgBtbMG0bWzM4OzU7MjIwbRtbNDg7NTsyMjdt4paAG1swbRtb
Mzg7NTs1OG0bWzQ4OzU7MTQybeKWgBtbMG0bWzM4OzU7MjM0bRtbNDg7NTsyMzRt4paA4paA4paA4paA4paAG1swbRtbMzg7
NTs1OG0bWzQ4OzU7NTht4paAG1swbSAgICAbWzBtCiAgICAgICAgICAgICAgICAbWzBtICAgIBtbMG0bWzM4OzU7NThtG1s0
ODs1OzU4beKWgBtbMG0bWzM4OzU7MjM0bRtbNDg7NTsyMzRt4paA4paA4paA4paA4paAG1swbRtbMzg7NTsxNzhtG1s0ODs1
OzE0Mm3iloAbWzBtG1szODs1OzIyMG0bWzQ4OzU7MjE0beKWgBtbMG0bWzM4OzU7MjM4bRtbNDg7NTsyMzRt4paAG1swbRtb
Mzg7NTsxNzhtG1s0ODs1OzEzNm3iloAbWzBtG1szODs1OzEwMG0bWzQ4OzU7OTRt4paAG1swbRtbMzg7NTsyMzRtG1s0ODs1
OzIzNG3iloDiloDiloDiloAbWzBtG1szODs1Ozk0bRtbNDg7NTs1OG3iloAbWzBtG1szODs1OzIyMG0bWzQ4OzU7MjIwbeKW
gBtbMG0bWzM4OzU7MTAwbRtbNDg7NTs1OG3iloAbWzBtG1szODs1OzEwMW0bWzQ4OzU7MjM0beKWgBtbMG0bWzM4OzU7MTc4
bRtbNDg7NTsxNzht4paAG1swbRtbMzg7NTsyMzRtG1s0ODs1OzIzNG3iloDiloDiloDiloDiloAbWzBtG1szODs1OzU4bRtb
NDg7NTs1OG3iloAbWzBtG1szODs1OzIyNm3iloAbWzBtICAgG1swbQogICAgICAgICAgICAgICAgG1swbSAgICAbWzBtG1sz
ODs1OzU4bRtbNDg7NTsyMzRt4paAG1swbRtbMzg7NTsyMzNtG1s0ODs1OzIzNW3iloAbWzBtG1szODs1OzIzNW0bWzQ4OzU7
NTht4paA4paAG1swbRtbMzg7NTsyMzRtG1s0ODs1OzIzNW3iloAbWzBtG1szODs1OzIzNG0bWzQ4OzU7MjM0beKWgBtbMG0b
WzM4OzU7NThtG1s0ODs1OzIzNG3iloAbWzBtG1szODs1OzIxNG0bWzQ4OzU7NTht4paAG1swbRtbMzg7NTsxNzJtG1s0ODs1
Ozk0beKWgBtbMG0bWzM4OzU7MTM2bRtbNDg7NTsyMzZt4paAG1swbRtbMzg7NTsyMzZtG1s0ODs1OzIzNG3iloAbWzBtG1sz
ODs1OzIzNG0bWzQ4OzU7MjM0beKWgOKWgOKWgOKWgBtbMG0bWzM4OzU7MjM2bRtbNDg7NTsyMzRt4paAG1swbRtbMzg7NTsx
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
MjM1bRtbNDg7NTs1OG3iloAbWzBtG1szODs1OzIzNG0bWzQ4OzU7MjM0beKWgOKWgOKWgOKWgOKWgOKWgOKWgOKWgOKWgOKW
gOKWgOKWgOKWgOKWgOKWgOKWgOKWgOKWgOKWgOKWgOKWgOKWgOKWgOKWgBtbMG0bWzM4OzU7OTRtG1s0ODs1OzU4beKWgBtb
MG0gG1swbRtbMzg7NTsxODRt4paEG1swbSAgG1swbQogICAgICAgICAgICAgICAgG1swbSAgICAbWzBtG1szODs1OzU4bRtb
NDg7NTs5NG3iloAbWzBtG1szODs1OzIzNG0bWzQ4OzU7MjM0beKWgOKWgOKWgOKWgOKWgOKWgBtbMG0bWzM4OzU7MjM0bRtb
NDg7NTs1OG3iloDiloAbWzBtG1szODs1OzIzNG0bWzQ4OzU7MjM0beKWgOKWgOKWgOKWgOKWgBtbMG0bWzM4OzU7MjM0bRtb
NDg7NTs1OG3iloAbWzBtG1szODs1OzIzNG0bWzQ4OzU7MTAwbeKWgBtbMG0bWzM4OzU7MjM0bRtbNDg7NTsyMzRt4paA4paA
4paA4paA4paA4paAG1swbRtbMzg7NTsyMzRtG1s0ODs1OzEwMG3iloAbWzBtG1szODs1OzIzNG0bWzQ4OzU7MjM0beKWgOKW
gBtbMG0bWzM4OzU7NThtG1s0ODs1OzU4beKWgBtbMG0gICAgG1swbQogICAgICAgICAgICAgICAgG1swbSAgICAgG1swbRtb
Mzg7NTsxMDBt4paAG1swbRtbMzg7NTsyMzRtG1s0ODs1Ozk0beKWgBtbMG0bWzM4OzU7MjM0bRtbNDg7NTsyMzRt4paA4paA
G1swbRtbMzg7NTsyMzRtG1s0ODs1OzU4beKWgBtbMG0bWzM4OzU7NTht4paAG1swbSAgG1swbRtbMzg7NTs1OG3iloAbWzBt
G1szODs1OzIzNG0bWzQ4OzU7MjM1beKWgBtbMG0bWzM4OzU7MjM0bRtbNDg7NTsyMzRt4paAG1swbRtbMzg7NTsyMzRtG1s0
ODs1OzIzNW3iloAbWzBtG1szODs1OzIzNW3iloAbWzBtICAbWzBtG1szODs1OzU4beKWgBtbMG0bWzM4OzU7MjM0bRtbNDg7
NTs1OG3iloAbWzBtG1szODs1OzIzNG0bWzQ4OzU7MjM0beKWgOKWgBtbMG0bWzM4OzU7MjM0bRtbNDg7NTsxMDBt4paAG1sw
bRtbMzg7NTsxMDBt4paAG1swbSAbWzBtG1szODs1Ozk0beKWgBtbMG0bWzM4OzU7MjM0bRtbNDg7NTs1OG3iloAbWzBtG1sz
ODs1OzIzNW0bWzQ4OzU7MjM1beKWgBtbMG0gICAgG1swbQogICAgICAgICAgICAgICAgG1swbSAgICAgICAbWzBtG1szODs1
OzU4beKWgOKWgBtbMG0gICAgICAbWzBtG1szODs1OzU4beKWgBtbMG0bWzM4OzU7MTAwbeKWgBtbMG0gICAgIBtbMG0bWzM4
OzU7NTht4paA4paAG1swbSAgICAgG1swbRtbMzg7NTsxMDBt4paAG1swbSAgICAbWzBt
'@
        try {
            $mascot = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($mascotB64))
            Write-Host ""
            Write-Host $mascot
        } catch {
            # Best-effort decoration only - never let the mascot block an install.
        }
    }
    # Wordmark glyphs are machine-generated (figlet ANSI Shadow, the full
    # PERSONAL JARVIS - maintainer request 2026-07-16) and live inside this
    # here-string, where non-ASCII is syntactically inert. Do not hand-edit --
    # that is how the historical Harvis typo crept in. The 12 rows carry ONE
    # continuous vertical gradient (hi -> brand -> deep) so both words read
    # as a single forged-gold wordmark.
    $art = @"

$GoldHi██████╗ ███████╗██████╗ ███████╗ ██████╗ ███╗   ██╗ █████╗ ██╗$Rst
$GoldHi██╔══██╗██╔════╝██╔══██╗██╔════╝██╔═══██╗████╗  ██║██╔══██╗██║$Rst
$GoldHi██████╔╝█████╗  ██████╔╝███████╗██║   ██║██╔██╗ ██║███████║██║$Rst
$GoldHi██╔═══╝ ██╔══╝  ██╔══██╗╚════██║██║   ██║██║╚██╗██║██╔══██║██║$Rst
$Gold██║     ███████╗██║  ██║███████║╚██████╔╝██║ ╚████║██║  ██║███████╗$Rst
$Gold╚═╝     ╚══════╝╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝╚══════╝$Rst
$Gold                ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗$Rst
$Gold                ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝$Rst
$GoldDeep                ██║███████║██████╔╝██║   ██║██║███████╗$Rst
$GoldDeep           ██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║$Rst
$GoldDeep           ╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║$Rst
$GoldDeep            ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝$Rst

$Dim     talk to your computer · installs the full profile · launches when done$Rst

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
# Standalone bootstrap projections of jarvis/core/branding.py. The branding
# contract test rejects drift because this script runs before Python is installed.
$OfficialRepoSlug = "$env:JARVIS_OFFICIAL_REPO_SLUG"
if (-not $OfficialRepoSlug) { $OfficialRepoSlug = 'PersonalJarvis/PersonalJarvis' }
if ($OfficialRepoSlug -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw 'JARVIS_OFFICIAL_REPO_SLUG must be an exact owner/repository slug'
}
$RepoUrl    = if ($env:JARVIS_INSTALL_REPO) { $env:JARVIS_INSTALL_REPO } else { "https://github.com/$OfficialRepoSlug.git" }
$ConfigFileName = 'jarvis.toml'
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

# The payload is a single ~80 MB stream and git cannot resume a clone, so a
# flaky network kills installs two ways (maintainer's Mac, 2026-07-18:
# "Receiving objects: 45%" frozen for minutes, then "early EOF" /
# "unexpected disconnect while reading sideband packet"):
#   1. a stalled stream hangs silently -> low-speed limits turn that into a
#      fast, visible failure (under 1 KB/s for 30 s = dead connection);
#   2. a mid-transfer disconnect aborts the install -> retry a clean clone
#      up to 3 times before giving up with an honest message.
$GitNetOpts = @('-c', 'http.lowSpeedLimit=1024', '-c', 'http.lowSpeedTime=30')

function Invoke-CloneWithRetry {
    Write-Note 'downloading ~80 MB - a few minutes on slow connections'
    $Attempt = 1
    while ($true) {
        # Attempt 1 uses git's default transport; later attempts force
        # HTTP/1.1 - the known cure for a family of "RPC failed; curl 28 /
        # early EOF" aborts that only bite the bulk pack stream while small
        # requests sail through (observed on the test Mac, 2026-07-18).
        $HttpMode = if ($Attempt -gt 1) { @('-c', 'http.version=HTTP/1.1') } else { @() }
        & git @GitNetOpts @HttpMode clone $GitVerbosity --depth 1 --branch $Branch $RepoUrl $InstallDir
        if ($LASTEXITCODE -eq 0) { return $true }
        try { Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction Stop } catch {}
        if ($Attempt -ge 3) {
            Write-Note 'git could not finish the download on this network (3 attempts).'
            return $false
        }
        $Attempt++
        Write-Note "connection dropped mid-download - retrying ($Attempt/3, compatibility transfer mode) ..."
        Start-Sleep -Seconds 3
    }
}

# Last-resort transport when git cannot get the pack through AT ALL: a plain
# HTTPS archive download. The release asset (uploaded per release) supports
# HTTP ranges, so curl -C - RESUMES after every disconnect - even a crawling
# connection eventually finishes, which a restarted git clone never does.
# Falls back to the branch snapshot (codeload, not resumable) when no release
# asset is reachable. The tree lands WITHOUT .git metadata; the next installer
# run detects that and repairs it in place (salvage path), so updates keep
# working. Windows 10+ ships both curl.exe and tar.exe.
function Invoke-TarballFallback {
    if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { return $false }
    if (-not (Get-Command tar.exe -ErrorAction SilentlyContinue)) { return $false }
    if ($RepoUrl -notmatch 'github\.com/') { return $false }
    $RepoPath = ($RepoUrl -split 'github\.com/', 2)[1] -replace '\.git$', ''
    $AssetUrl = "https://github.com/$RepoPath/releases/latest/download/personal-jarvis-src.tar.gz"
    $SnapshotUrl = "https://codeload.github.com/$RepoPath/tar.gz/refs/heads/$Branch"
    if ($env:JARVIS_PAYLOAD_COMMIT) {
        # A verified install is pinned to ONE signed commit; the "latest"
        # release asset cannot honor that pin, so go straight to the
        # commit-addressed snapshot - the URL itself names the exact tree.
        $AssetUrl = ''
        $SnapshotUrl = "https://codeload.github.com/$RepoPath/tar.gz/$($env:JARVIS_PAYLOAD_COMMIT)"
    }
    $Tmp = "$InstallDir.payload.tar.gz"
    Write-Note 'git transfer keeps stalling on this network - switching to a resumable archive download'
    $Got = $false
    $Try = 1
    while ($AssetUrl -and $Try -le 8) {
        & curl.exe -fL --speed-limit 1024 --speed-time 60 -C - -o $Tmp $AssetUrl
        if ($LASTEXITCODE -eq 0) { $Got = $true; break }
        if ($LASTEXITCODE -eq 22) { break }  # HTTP error (e.g. asset missing): not retryable
        Write-Note "archive download interrupted - resuming where it stopped ($Try/8) ..."
        $Try++
        Start-Sleep -Seconds 3
    }
    if (-not $Got) {
        try { Remove-Item -LiteralPath $Tmp -Force -ErrorAction Stop } catch {}
        Write-Note 'no resumable release archive reachable - trying the direct snapshot (single stream)'
        & curl.exe -fL --speed-limit 1024 --speed-time 60 -o $Tmp $SnapshotUrl
        if ($LASTEXITCODE -ne 0) {
            try { Remove-Item -LiteralPath $Tmp -Force -ErrorAction Stop } catch {}
            return $false
        }
    }
    try { Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction Stop } catch {}
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    & tar.exe -xzf $Tmp -C $InstallDir --strip-components=1
    $TarOk = ($LASTEXITCODE -eq 0)
    try { Remove-Item -LiteralPath $Tmp -Force -ErrorAction Stop } catch {}
    if (-not $TarOk) { return $false }
    Write-Note 'installed from the release archive (git metadata is repaired on a future update)'
    return $true
}

function Invoke-FetchPayload {
    if (Invoke-CloneWithRetry) { return $true }
    if (Invoke-TarballFallback) { return $true }
    Write-Err 'the download kept failing (connection dropped mid-transfer).'
    Write-Note 'Check your internet connection (Wi-Fi, VPN, proxy), then re-run the'
    Write-Note 'installer - it is safe to re-run and picks up where it makes sense.'
    return $false
}

# Self-heal a broken install dir (leftover non-git folder from an earlier or
# aborted install, or a checkout whose git state no longer updates): keep the
# old tree as a timestamped sibling backup - never delete - clone fresh, then
# carry the user's local state (data/, jarvis.toml, .env) into the new
# checkout. A stale folder must never require manual cleanup to install.
function Invoke-SalvageReclone {
    $StaleBackup = "$InstallDir.stale-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    try {
        Move-Item -LiteralPath $InstallDir -Destination $StaleBackup -ErrorAction Stop
    } catch {
        Write-Err "cannot move the broken install dir aside ($InstallDir -> $StaleBackup)."
        Write-Note 'Close any program using that folder (or move it yourself), then re-run.'
        exit 1
    }
    Write-Note "moved the old directory to $StaleBackup (nothing was deleted)"
    if (-not (Invoke-FetchPayload)) { exit 1 }
    # Everything a user can lose: config, credentials file-fallback + DBs
    # (inside data/), the dotenv, AND the wiki vault (its absence from this
    # list is what destroyed a user's wiki pages on 2026-07-20). A failed
    # copy is a loud warning pointing at the backup dir - never silent.
    # NOTE: a "skip when the destination exists" guard is WRONG for wiki -
    # the fresh clone ships a tracked seed wiki/ skeleton, so the guard
    # would silently never restore the user's real vault. Directories that
    # exist on both sides are overlay-MERGED (user files win over seed).
    foreach ($Item in @('data', $ConfigFileName, '.env', 'wiki')) {
        $Old = Join-Path $StaleBackup $Item
        $New = Join-Path $InstallDir $Item
        if (-not (Test-Path $Old)) { continue }
        if (-not (Test-Path $New)) {
            try {
                Copy-Item -LiteralPath $Old -Destination $New -Recurse -ErrorAction Stop
                Write-Note "kept your $Item from the previous install"
            } catch {
                Write-Err "could NOT carry over $Item - it is still safe in $Old; copy it back manually."
            }
        } elseif ((Test-Path $Old -PathType Container) -and (Test-Path $New -PathType Container)) {
            try {
                Copy-Item -Path (Join-Path $Old '*') -Destination $New -Recurse -Force -ErrorAction Stop
                Write-Note "merged your $Item from the previous install over the fresh seed"
            } catch {
                Write-Err "could NOT merge $Item - it is still safe in $Old; copy it back manually."
            }
        }
    }
    Write-Ok 'reinstalled fresh (previous state preserved in the backup dir)'
}

if (Test-Path (Join-Path $InstallDir '.git')) {
    $UpdateOk = $true
    Push-Location $InstallDir
    try {
        & git @GitNetOpts fetch $GitVerbosity --depth 1 origin $Branch
        if ($LASTEXITCODE -ne 0) { $UpdateOk = $false }
        if ($UpdateOk) {
            & git checkout --quiet $Branch
            if ($LASTEXITCODE -ne 0) { $UpdateOk = $false }
        }
        if ($UpdateOk) {
            & git reset --quiet --hard "origin/$Branch"
            if ($LASTEXITCODE -ne 0) { $UpdateOk = $false }
        }
    } finally {
        Pop-Location
    }
    if ($UpdateOk) {
        Write-Ok 'updated existing checkout to latest'
    } else {
        Write-Note 'existing checkout would not update (broken git state) - reinstalling in place.'
        Invoke-SalvageReclone
    }
} else {
    if (Test-Path $InstallDir) {
        Write-Note "$InstallDir exists but is not a git repo (leftover from an earlier install) - reinstalling in place."
        Invoke-SalvageReclone
    } else {
        if (-not (Invoke-FetchPayload)) { exit 1 }
        Write-Ok 'downloaded'
    }
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
    if (-not (Test-Path (Join-Path $InstallDir '.git'))) {
        # Tarball-fallback install: the tree was fetched from the commit-
        # addressed archive URL, so the pin is embedded in the download
        # itself; there are no git objects to re-verify against.
        Write-Ok "pinned via commit-addressed archive ($($PayloadCommit.Substring(0,12)))"
        $PinnedViaArchive = $true
    } else {
        $PinnedViaArchive = $false
    }
    if (-not $PinnedViaArchive) {
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
