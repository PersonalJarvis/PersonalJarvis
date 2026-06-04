<#
.SYNOPSIS
    Pre-boot check: restore any tracked files missing from the working tree.

.DESCRIPTION
    Compares ``git ls-tree HEAD --name-only -r`` against the working tree
    on disk. For every file listed in HEAD that does NOT exist on disk,
    runs ``git checkout HEAD -- <file>`` to bring it back, logs a banner
    line per restored file, and writes a structured log block to
    ``data/working-tree-check.log``.

    Design contract:

    * Exit code is ALWAYS 0 -- must never block boot, even when git fails
      or the log directory is unwritable.
    * Idempotent -- a second consecutive run after a clean tree is a no-op
      (no files written, single ``clean`` log block appended).
    * Rotating log -- keeps the last 10 run blocks; older blocks are
      pruned at the end of every run.
    * Quiet on clean -- banner output to stdout only fires when at least
      one file gets restored, so noise during normal boots is minimal.

    Motivation: 2026-05-14 incident. Five tracked files under
    ``jarvis/memory/wiki/`` (integration.py, lock.py, scheduler.py,
    search.py, voice_bridge.py) had silently disappeared from the working
    tree while still present in HEAD. The running app loaded stale .pyc
    caches and the memory pipeline ran at half effectiveness for hours
    before anyone noticed. This script structurally prevents a repeat:
    the next boot after such a drift restores the files and surfaces a
    banner line that an operator can search the log for.

    ASCII-only on purpose. PowerShell 5.1 reads .ps1 files in the system
    OEM/ANSI codepage when there is no BOM, so any non-ASCII character
    (em-dash, umlaut) breaks the parser. The existing scripts/auto-push-
    eod.ps1 follows the same convention.

.PARAMETER RepoRoot
    Path to the git repository root. Defaults to the parent directory of
    the script's own location, which is the repo root when this script
    lives under ``scripts/``.

.PARAMETER MaxRuns
    Maximum number of past run blocks to retain in the rotating log.
    Default: 10.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-working-tree.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-working-tree.ps1 -RepoRoot "C:\some\worktree"
#>

[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [int]$MaxRuns = 10
)

# ---------------------------------------------------------------------------
# Setup. ErrorActionPreference stays 'Continue' so any git stderr noise
# does not abort us -- we report it and keep going.
# ---------------------------------------------------------------------------

$ErrorActionPreference = "Continue"
$OutputEncoding        = [System.Text.UTF8Encoding]::new($false)

$tsHuman = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

# Resolve the repo root. ``$PSScriptRoot`` is intentionally consulted in
# the body (not as a param default): in PowerShell 5.1 the param-block
# default expression is evaluated before $PSScriptRoot is populated,
# which made ``Split-Path -Parent $PSScriptRoot`` throw a hard
# ParameterArgumentValidationErrorEmptyStringNotAllowed at every boot.
# Body-time evaluation is reliable.
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        $RepoRoot = Split-Path -Parent $PSScriptRoot
    } else {
        $RepoRoot = (Get-Location).Path
    }
}

if (-not (Test-Path -LiteralPath $RepoRoot)) {
    Write-Host "[$tsHuman] working-tree-check: repo root not found: $RepoRoot (exit 0, no-op)"
    exit 0
}

Set-Location -LiteralPath $RepoRoot

$dataDir = Join-Path $RepoRoot "data"
if (-not (Test-Path -LiteralPath $dataDir)) {
    try { New-Item -ItemType Directory -Path $dataDir -Force | Out-Null } catch { }
}
$logFile = Join-Path $dataDir "working-tree-check.log"

# We build the current run's lines in-memory and flush them once at the
# end. That avoids a partial run-block landing in the file when the
# script is interrupted (Ctrl+C, OS shutdown), and makes rotation a
# single atomic write.
$runLines = New-Object System.Collections.Generic.List[string]
$restored = New-Object System.Collections.Generic.List[string]
$failed   = New-Object System.Collections.Generic.List[string]

function Add-RunLine {
    param([string]$Level, [string]$Message)
    $line = "[$tsHuman] [$Level] $Message"
    $runLines.Add($line) | Out-Null
}

Add-RunLine "INFO" "=== working-tree-check START (repo=$RepoRoot) ==="

# ---------------------------------------------------------------------------
# Pre-flight: must be a git repository, otherwise we exit 0 with a no-op
# log line. The launcher must not block on a missing git, only on real
# corruption.
# ---------------------------------------------------------------------------

$isRepo = $true
$null = git rev-parse --git-dir 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    Add-RunLine "SKIP" "not a git repository -- nothing to verify"
    Add-RunLine "INFO" "=== working-tree-check END (skipped) ==="
    $isRepo = $false
}

# ---------------------------------------------------------------------------
# Enumerate HEAD-tracked files and check each against the working tree.
# ``git ls-tree HEAD --name-only -r`` returns POSIX paths (forward
# slashes) even on Windows. Test-Path / git checkout both accept them; no
# conversion needed. Filenames containing newlines are not supported
# (rare in this repo, and would require -z parsing).
# ---------------------------------------------------------------------------

if ($isRepo) {
    $treeRaw = git ls-tree HEAD --name-only -r 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Add-RunLine "WARN" "git ls-tree failed: $($treeRaw.Trim())"
        Add-RunLine "INFO" "=== working-tree-check END (git-error) ==="
    } else {
        $trackedFiles = $treeRaw -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" }
        Add-RunLine "INFO" "$($trackedFiles.Count) tracked file(s) listed in HEAD"

        foreach ($relPath in $trackedFiles) {
            # Test-Path against the repo-relative path. Forward slashes
            # are fine on Windows. -LiteralPath would NOT work here
            # because we explicitly want path expansion for separator
            # normalisation.
            if (-not (Test-Path -Path $relPath -PathType Leaf)) {
                # Banner line -- surfaced to stdout for run.bat output.
                $bannerMsg = "RESTORE $relPath -- was in HEAD but missing from working tree"
                Write-Host "[$tsHuman] working-tree-check: $bannerMsg"
                Add-RunLine "RESTORE" $bannerMsg

                $checkoutOut = git checkout HEAD -- $relPath 2>&1 | Out-String
                if ($LASTEXITCODE -eq 0 -and (Test-Path -Path $relPath -PathType Leaf)) {
                    Add-RunLine "OK" "restored $relPath"
                    $restored.Add($relPath) | Out-Null
                } else {
                    # We tried, it failed. Log + count, never raise.
                    $failedMsg = "git checkout failed for $relPath -- $($checkoutOut.Trim())"
                    Write-Host "[$tsHuman] working-tree-check: $failedMsg"
                    Add-RunLine "FAIL" $failedMsg
                    $failed.Add($relPath) | Out-Null
                }
            }
        }

        if ($restored.Count -eq 0 -and $failed.Count -eq 0) {
            Add-RunLine "OK" "working tree clean -- all HEAD files present on disk"
        } else {
            Add-RunLine "INFO" "summary: $($restored.Count) restored, $($failed.Count) failed"
        }
        Add-RunLine "INFO" "=== working-tree-check END ==="
    }
}

# ---------------------------------------------------------------------------
# Rotation: keep the last $MaxRuns run blocks in the log. A block starts
# with a line matching "=== working-tree-check START" and ends with the
# next start or EOF. Simple line-walk -- robust against regex flavour
# quirks, and reads/writes UTF-8 without BOM to match the rest of the
# repo's log files.
# ---------------------------------------------------------------------------

function Save-RotatedLog {
    param(
        [string]$Path,
        [string[]]$NewBlockLines,
        [int]$KeepRuns
    )

    $existingLines = @()
    if (Test-Path -LiteralPath $Path) {
        try {
            $existingLines = [System.IO.File]::ReadAllLines($Path, [System.Text.UTF8Encoding]::new($false))
        } catch {
            $existingLines = @()
        }
    }

    $allLines = @($existingLines) + @($NewBlockLines)

    $blocks   = [System.Collections.Generic.List[string]]::new()
    $current  = [System.Collections.Generic.List[string]]::new()
    $marker   = "=== working-tree-check START"

    foreach ($line in $allLines) {
        if ($line -match [regex]::Escape($marker)) {
            if ($current.Count -gt 0) {
                $blocks.Add(($current -join "`r`n")) | Out-Null
                $current.Clear()
            }
        }
        $current.Add($line) | Out-Null
    }
    if ($current.Count -gt 0) {
        $blocks.Add(($current -join "`r`n")) | Out-Null
    }

    if ($blocks.Count -gt $KeepRuns) {
        $blocks = [System.Collections.Generic.List[string]]($blocks | Select-Object -Last $KeepRuns)
    }

    $final = ($blocks -join "`r`n`r`n") + "`r`n"
    [System.IO.File]::WriteAllText($Path, $final, [System.Text.UTF8Encoding]::new($false))
}

try {
    Save-RotatedLog -Path $logFile -NewBlockLines $runLines.ToArray() -KeepRuns $MaxRuns
} catch {
    # Logging itself must never crash the boot. We surface to stdout and
    # move on.
    Write-Host "[$tsHuman] working-tree-check: log write failed -- $($_.Exception.Message)"
}

# Always exit 0. The launcher must never block on this script.
exit 0
