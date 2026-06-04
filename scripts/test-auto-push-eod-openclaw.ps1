<#
.SYNOPSIS
    Smoke-Test fuer den OpenClaw-Backstop in auto-push-eod.ps1 (Phase 11).

.DESCRIPTION
    Baut ein temporaeres Mini-Repo mit zwei Worktrees, davon einer mit
    .openclaw_state\<sid>\ Verzeichnis. Ruft auto-push-eod.ps1 -DryRun auf
    und prueft per Log-Output, dass der OpenClaw-Branch korrekt geskippt
    wird (bzw. mit -OpenClawWarnOnly nur warnen).

    Idempotent: temp-Repo wird vor + nach dem Test geloescht.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-auto-push-eod-openclaw.ps1

.NOTES
    Kein git-push wird tatsaechlich ausgefuehrt (DryRun + lokaler Origin).
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$failures = @()

function Assert-Contains {
    param([string]$Haystack, [string]$Needle, [string]$What)
    if ($Haystack -notmatch [regex]::Escape($Needle)) {
        $script:failures += "FAIL: $What -- Output enthaelt '$Needle' nicht."
        Write-Host "FAIL: $What -- '$Needle' nicht im Output." -ForegroundColor Red
    } else {
        Write-Host "OK:   $What" -ForegroundColor Green
    }
}

function Assert-NotContains {
    param([string]$Haystack, [string]$Needle, [string]$What)
    if ($Haystack -match [regex]::Escape($Needle)) {
        $script:failures += "FAIL: $What -- Output enthaelt unerwartet '$Needle'."
        Write-Host "FAIL: $What -- '$Needle' wurde nicht erwartet." -ForegroundColor Red
    } else {
        Write-Host "OK:   $What" -ForegroundColor Green
    }
}

# --- Setup: temp-Repo ---
$repoRoot = Join-Path $env:TEMP "jarvis-eod-test-$([guid]::NewGuid().ToString('N').Substring(0,8))"
$wtClean  = Join-Path $env:TEMP "jarvis-eod-test-wt-clean-$([guid]::NewGuid().ToString('N').Substring(0,8))"
$wtOC     = Join-Path $env:TEMP "jarvis-eod-test-wt-oc-$([guid]::NewGuid().ToString('N').Substring(0,8))"

try {
    Write-Host "=== SETUP ===" -ForegroundColor Cyan
    Write-Host "Repo-Root: $repoRoot"
    New-Item -ItemType Directory -Path $repoRoot -Force | Out-Null
    Set-Location -LiteralPath $repoRoot
    git init -b main 2>&1 | Out-Null
    git config user.email "test@example.com"
    git config user.name "Test"
    "x" | Out-File -FilePath "README.md" -Encoding utf8
    # logs/ + .openclaw_state/ ignorieren, sonst loest der dirty-tree-check aus
    "logs/`n.openclaw_state/`n" | Out-File -FilePath ".gitignore" -Encoding utf8
    git add README.md .gitignore 2>&1 | Out-Null
    git commit -m "init" 2>&1 | Out-Null

    # Branches anlegen
    git branch feature/clean 2>&1 | Out-Null
    git branch feature/openclaw-active 2>&1 | Out-Null

    # Worktrees auschecken
    git worktree add $wtClean feature/clean 2>&1 | Out-Null
    git worktree add $wtOC feature/openclaw-active 2>&1 | Out-Null

    # In wtOC eine "aktive" OpenClaw-Session simulieren
    $stateDir = Join-Path $wtOC ".openclaw_state\sess-deadbeef"
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    "fake openclaw run" | Out-File -FilePath (Join-Path $stateDir "run.log") -Encoding utf8

    # --- Test 1: SKIP-Modus (Default) ---
    Write-Host "`n=== TEST 1: SKIP-Modus ===" -ForegroundColor Cyan
    $scriptPath = Resolve-Path (Join-Path $PSScriptRoot "auto-push-eod.ps1")
    $output1 = & powershell -NoProfile -ExecutionPolicy Bypass -File $scriptPath -RepoRoot $repoRoot -DryRun -OpenClawActiveMinutes 60 2>&1 | Out-String

    Assert-Contains $output1 "OpenClaw-Backstop aktiv" "Backstop wird aktiviert"
    Assert-Contains $output1 "OpenClaw-Backstop trifft Branch 'feature/openclaw-active'" "Aktiver OpenClaw-Worktree wird erkannt"
    Assert-Contains $output1 "uebersprungen" "Branch wird geskippt"
    Assert-NotContains $output1 "Branch 'feature/clean' uebersprungen" "Clean-Branch wird NICHT geskippt"
    Assert-Contains $output1 "[dry-run] git push -u origin feature/clean" "Clean-Branch landet in dry-run-Push-Liste"

    # --- Test 2: WARN-only-Modus ---
    Write-Host "`n=== TEST 2: WARN-only-Modus ===" -ForegroundColor Cyan
    $output2 = & powershell -NoProfile -ExecutionPolicy Bypass -File $scriptPath -RepoRoot $repoRoot -DryRun -OpenClawActiveMinutes 60 -OpenClawWarnOnly 2>&1 | Out-String

    Assert-Contains $output2 "Modus: WARN" "WARN-only-Modus wird angezeigt"
    Assert-Contains $output2 "[WARN]" "WARN-Log-Level wird verwendet"
    # Im WARN-only-Modus sollte der Branch trotzdem in der dry-run-Liste auftauchen
    Assert-Contains $output2 "[dry-run] git push" "Branch wird trotzdem zur Push-Liste hinzugefuegt"

    # --- Test 3: Disabled (OpenClawActiveMinutes=0) ---
    Write-Host "`n=== TEST 3: Disabled-Modus ===" -ForegroundColor Cyan
    $output3 = & powershell -NoProfile -ExecutionPolicy Bypass -File $scriptPath -RepoRoot $repoRoot -DryRun -OpenClawActiveMinutes 0 2>&1 | Out-String

    Assert-NotContains $output3 "OpenClaw-Backstop aktiv" "Backstop ist deaktiviert"
    Assert-NotContains $output3 "OpenClaw-Backstop trifft" "Keine Branches werden geblockt"

    # --- Summary ---
    Write-Host "`n=== SUMMARY ===" -ForegroundColor Cyan
    if ($failures.Count -eq 0) {
        Write-Host "ALLE 3 TESTS GRUEN" -ForegroundColor Green
        exit 0
    } else {
        foreach ($f in $failures) { Write-Host $f -ForegroundColor Red }
        Write-Host "$($failures.Count) FEHLER" -ForegroundColor Red
        exit 1
    }

} finally {
    # --- Cleanup ---
    Write-Host "`n=== CLEANUP ===" -ForegroundColor Cyan
    Set-Location -LiteralPath $env:TEMP
    foreach ($p in @($wtClean, $wtOC, $repoRoot)) {
        if (Test-Path -LiteralPath $p) {
            try {
                Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue
            } catch {
                Write-Host "Cleanup-Warnung: $p konnte nicht entfernt werden ($_)" -ForegroundColor Yellow
            }
        }
    }
}
