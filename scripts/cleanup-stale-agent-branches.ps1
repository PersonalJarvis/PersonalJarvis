# scripts/cleanup-stale-agent-branches.ps1
#
# H7 (2026-05-17 audit): `git worktree remove` does NOT delete the branch
# the worktree was on. Over time this accumulates: today's audit counted
# 157 `agent/*` branches in the local repo, the vast majority belonging
# to worktrees that have long since been removed or rm-rf'd.
#
# This script enumerates local branches matching `agent/*`, subtracts the
# branches still backed by an active worktree, and force-deletes the
# rest. `git branch -D` is the right hammer here because every agent
# branch is intentionally unmerged into main (the diff lives in
# sub-agents-outputs/, not in the branch).
#
# Usage:
#   pwsh scripts/cleanup-stale-agent-branches.ps1                # delete stale branches (with a confirmation prompt above 50)
#   pwsh scripts/cleanup-stale-agent-branches.ps1 -DryRun        # list-only
#   pwsh scripts/cleanup-stale-agent-branches.ps1 -Force         # skip confirmation prompt
#
# Hooked into scripts/preflight.ps1 as a best-effort step.

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Force,
    [int]$ConfirmThreshold = 50
)

$ErrorActionPreference = 'Stop'

function Write-Info($msg) { Write-Host "[cleanup-stale-agent-branches] $msg" }

# 1. All local branches matching agent/*
$localAgentBranches = & git for-each-ref --format='%(refname:short)' refs/heads/agent/ 2>$null
if (-not $localAgentBranches) {
    Write-Info "No local agent/* branches found. Nothing to do."
    exit 0
}
$localAgentBranches = @($localAgentBranches | Where-Object { $_ })

# 2. Branches that are backed by an active worktree
#    `git worktree list --porcelain` emits "branch refs/heads/<name>" lines.
$activeBranches = New-Object 'System.Collections.Generic.HashSet[string]'
$wtList = & git worktree list --porcelain 2>$null
foreach ($line in $wtList) {
    if ($line -match '^branch refs/heads/(.+)$') {
        [void]$activeBranches.Add($Matches[1])
    }
}

# 3. Stale = local minus active
$stale = $localAgentBranches | Where-Object { -not $activeBranches.Contains($_) }
$stale = @($stale)

if ($stale.Count -eq 0) {
    Write-Info "All $($localAgentBranches.Count) agent/* branches still back an active worktree. Nothing to delete."
    exit 0
}

Write-Info "Found $($stale.Count) stale agent/* branches (out of $($localAgentBranches.Count) total)."

if ($DryRun) {
    Write-Info "Dry-run -- would delete:"
    foreach ($b in $stale) { Write-Host "  $b" }
    exit 0
}

if ($stale.Count -gt $ConfirmThreshold -and -not $Force) {
    Write-Info "Stale count ($($stale.Count)) exceeds -ConfirmThreshold ($ConfirmThreshold)."
    Write-Info "Re-run with -Force to delete unconditionally, or -DryRun to inspect."
    exit 2
}

# 4. Delete in batches so a single bad branch name doesn't break the whole sweep.
$deleted = 0
$failed = @()
foreach ($branch in $stale) {
    try {
        & git branch -D $branch 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $deleted += 1
        } else {
            $failed += $branch
        }
    } catch {
        $failed += $branch
    }
}

Write-Info "Deleted $deleted stale branch(es)."
if ($failed.Count -gt 0) {
    Write-Info "Failed to delete $($failed.Count) branch(es) -- they may be checked out elsewhere or have invalid names:"
    foreach ($f in $failed) { Write-Host "  $f" }
}
exit 0
