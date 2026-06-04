<#
.SYNOPSIS
    Worktree health preflight (BUG-006 / BUG-014 / BUG-015 guard).

.DESCRIPTION
    Verifies that the active git worktree is the one the live Python
    interpreter imports jarvis from. BUG-006, BUG-014, and BUG-015 each
    cost the user hours because the pip editable-install pin had silently
    re-attached to a stale clone (Personal Jarvis-main/ etc.). The exact
    pathology: tests pass on the active tree, but the running Jarvis
    still executes the old code. This script closes that gap.

    Five sequential checks, each printing a [GREEN] or [RED] label:

      1. Git worktree assertion
      2. Editable install repins to the current worktree
      3. import jarvis resolves from under the current worktree
      4. Stale __editable__*.pth scan (user site-packages)
      5. Summary line + exit code

    Exit code:
      0  All checks GREEN.
      1  At least one check RED.

    ASCII-only on purpose. PowerShell 5.1 reads .ps1 files in the system
    OEM/ANSI codepage when there is no BOM, so any non-ASCII character
    breaks the parser. The check-working-tree.ps1 sibling follows the
    same convention. See BUG-018 for the BOM-write trap (file outputs
    must use [System.IO.File]::WriteAllText with UTF8Encoding($false);
    this script writes nothing, but the convention stays).

    Reference: BUG-006, BUG-014, BUG-015 in docs/BUGS.md.

.EXAMPLE
    pwsh scripts/preflight.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/preflight.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$OutputEncoding        = [System.Text.UTF8Encoding]::new($false)

# Worktree root = parent of the script's own directory. Resolved to an
# absolute path so later prefix comparisons are deterministic regardless
# of how the caller invoked the script (relative path, full path, from a
# different CWD).
$WorktreeRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Mutable failure flag. We do not exit on first RED -- the operator sees
# every issue in one pass.
$script:Failed = $false

function Write-Check {
    param(
        [Parameter(Mandatory=$true)][string]$Status,
        [Parameter(Mandatory=$true)][string]$Message
    )
    Write-Host "[$Status] $Message"
    if ($Status -eq "RED") { $script:Failed = $true }
}

# ---------------------------------------------------------------------------
# Check 1: Git worktree assertion.
#
# `git rev-parse --git-dir` prints the .git directory path relative to
# CWD. For the primary checkout the output is `.git` (or an absolute
# path ending with `.git`). For a linked worktree the output contains
# `/worktrees/<name>`. Anything else means we are not inside a git
# worktree at all.
# ---------------------------------------------------------------------------
Push-Location $WorktreeRoot
try {
    $gitDirRaw = git rev-parse --git-dir 2>$null
    $gitExit   = $LASTEXITCODE
    if ($gitExit -ne 0 -or [string]::IsNullOrWhiteSpace($gitDirRaw)) {
        Write-Check "RED" "Not a git worktree (rev-parse --git-dir failed)"
    } else {
        $gitDir     = (($gitDirRaw -join "") -as [string]).Trim()
        $gitDirNorm = $gitDir -replace '\\', '/'
        $isWorktree = ($gitDirNorm -eq ".git") `
            -or ($gitDirNorm -match '\.git/?$') `
            -or ($gitDirNorm -match '/worktrees/')
        if ($isWorktree) {
            Write-Check "GREEN" "Git worktree detected (git-dir=$gitDir)"
        } else {
            Write-Check "RED" "Not a git worktree (git-dir=$gitDir)"
        }
    }
} finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
# Check 2: Editable install.
#
# Running `pip install -e . --no-deps -q` at the worktree root re-pins
# the editable-install entry in the active interpreter's site-packages
# to THIS worktree. If the pin was stale, this fixes it as a side
# effect; if it was already correct, the call is a fast no-op.
# A non-zero exit means setup.py / pyproject.toml is broken or the
# interpreter is wrong -- both are wave-blockers.
# ---------------------------------------------------------------------------
Push-Location $WorktreeRoot
try {
    $pipOut  = pip install -e . --no-deps -q 2>&1
    $pipExit = $LASTEXITCODE
    if ($pipExit -ne 0) {
        Write-Check "RED" "pip install -e . --no-deps failed (exit $pipExit)"
        $tail = ($pipOut | Select-Object -Last 5) -join "`n    "
        if (-not [string]::IsNullOrWhiteSpace($tail)) {
            Write-Host "    $tail"
        }
    } else {
        Write-Check "GREEN" "Editable install OK (pip install -e . --no-deps)"
    }
} finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
# Check 3: Import path assertion.
#
# python -c "import jarvis; print(jarvis.__file__)" must report a path
# that lives under $WorktreeRoot. If the editable-install pin has
# drifted to a stale clone, this is where the drift surfaces -- the
# loud one that BUG-014 needed.
#
# Comparison is case-insensitive (Windows filesystem) and runs on
# canonical absolute paths (GetFullPath).
# ---------------------------------------------------------------------------
$pyOut  = python -c "import jarvis; print(jarvis.__file__)" 2>$null
$pyExit = $LASTEXITCODE
if ($pyExit -ne 0 -or [string]::IsNullOrWhiteSpace($pyOut)) {
    Write-Check "RED" "Cannot import jarvis package (python exit $pyExit)"
} else {
    $importedPath = (($pyOut -join "") -as [string]).Trim()
    try {
        $importedAbs = [System.IO.Path]::GetFullPath($importedPath)
    } catch {
        $importedAbs = $importedPath
    }
    $rootAbs = [System.IO.Path]::GetFullPath($WorktreeRoot)
    if ($importedAbs.ToLowerInvariant().StartsWith($rootAbs.ToLowerInvariant())) {
        Write-Check "GREEN" "jarvis imports from worktree ($importedPath)"
    } else {
        Write-Check "RED" "jarvis imports from wrong path: $importedPath"
    }
}

# ---------------------------------------------------------------------------
# Check 4: Stale __editable__*.pth scan.
#
# PEP 660 editable installs land two files per package in user
# site-packages:
#
#   __editable__.<pkg>-<ver>.pth        -- plain-path or loader-style
#   __editable___<pkg>_<ver>_finder.py  -- MAPPING dict with absolute path
#
# Both encode an absolute filesystem path that must point to a valid
# directory. We scan both kinds, extract anything that looks like a
# Windows absolute path, and assert each target directory exists.
# False positives are acceptable -- the goal is loud detection of stale
# entries, not perfect parsing.
# ---------------------------------------------------------------------------
$siteOut  = python -c "import site; print(site.getusersitepackages())" 2>$null
$siteExit = $LASTEXITCODE
if ($siteExit -ne 0 -or [string]::IsNullOrWhiteSpace($siteOut)) {
    Write-Check "GREEN" "User site-packages not present -- nothing to scan"
} else {
    $userSite = (($siteOut -join "") -as [string]).Trim()
    if (-not (Test-Path -LiteralPath $userSite -PathType Container)) {
        Write-Check "GREEN" "User site-packages directory missing -- nothing to scan"
    } else {
        $pthFiles = @(Get-ChildItem -Path $userSite -Filter "__editable__*.pth" -ErrorAction SilentlyContinue)
        $pyFiles  = @(Get-ChildItem -Path $userSite -Filter "__editable___*_finder.py" -ErrorAction SilentlyContinue)
        $allFiles = @($pthFiles) + @($pyFiles)
        $stale    = $false

        foreach ($f in $allFiles) {
            try {
                $content = [System.IO.File]::ReadAllText($f.FullName, [System.Text.UTF8Encoding]::new($false))
            } catch {
                continue
            }
            # Normalise Python-source escaped backslashes: a literal
            # 'C:\\Users\\X' written in source becomes 'C:\Users\X'
            # after this replacement, so the path regex below catches
            # it uniformly with raw-path .pth lines.
            $normalised = $content -replace '\\\\', '\'

            $candidates = New-Object System.Collections.Generic.HashSet[string]

            # (a) Plain absolute path on its own line (.pth style).
            foreach ($line in ($normalised -split "\r?\n")) {
                $t = $line.Trim()
                if ([string]::IsNullOrWhiteSpace($t)) { continue }
                if ($t.StartsWith("#"))               { continue }
                if ($t.StartsWith("import "))         { continue }
                if ($t -match '^[A-Za-z]:[\\/]') {
                    $null = $candidates.Add($t)
                }
            }

            # (b) Quoted absolute paths embedded in Python source
            #     (finder MAPPING dict, e.g. {'jarvis': 'C:\path'}).
            $quoteRegex = [regex]'["''](?<p>[A-Za-z]:[\\/][^"''\r\n]+)["'']'
            foreach ($m in $quoteRegex.Matches($normalised)) {
                $null = $candidates.Add($m.Groups['p'].Value.TrimEnd())
            }

            foreach ($c in $candidates) {
                if (-not (Test-Path -LiteralPath $c -PathType Container)) {
                    Write-Check "RED" "Stale editable install: $c (from $($f.Name))"
                    $stale = $true
                }
            }
        }

        if (-not $stale) {
            Write-Check "GREEN" "No stale __editable__*.pth files ($($allFiles.Count) scanned)"
        }
    }
}

# ---------------------------------------------------------------------------
# Best-effort housekeeping: prune stale agent/* branches left behind by
# `git worktree remove` (which does not delete the branch). H7 from the
# 2026-05-17 audit. Failures here are warnings only -- the preflight's
# health-check role takes priority.
# ---------------------------------------------------------------------------
$cleanupScript = Join-Path $PSScriptRoot "cleanup-stale-agent-branches.ps1"
if (Test-Path -LiteralPath $cleanupScript) {
    try {
        $cleanupOut = & $cleanupScript -Force 2>&1
        $deletedLine = $cleanupOut | Where-Object { $_ -match "Deleted \d+ stale" } | Select-Object -First 1
        if ($deletedLine) {
            Write-Check "GREEN" "$deletedLine"
        }
    } catch {
        Write-Check "RED" "cleanup-stale-agent-branches.ps1 failed: $($_.Exception.Message)"
        # NB: do NOT set $script:Failed -- housekeeping must not block boot.
    }
}

# ---------------------------------------------------------------------------
# Check 5: Summary + exit code.
# ---------------------------------------------------------------------------
Write-Host ""
if ($script:Failed) {
    Write-Host "[RED] Preflight FAILED -- fix before proceeding"
    exit 1
} else {
    Write-Host "[GREEN] Preflight OK -- worktree is healthy"
    exit 0
}
