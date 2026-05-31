<#
.SYNOPSIS
    Self-test for jarvis-config-drift-guard.ps1 scalar handling (Bug H11).

.DESCRIPTION
    Pester-free mini-test that builds a sandbox TOML + soll-JSON in a
    temp dir, runs the drift-guard, and asserts the post-state.
    Covers the H11 fix (2026-05-18): unquoted scalar bool/int/float must
    be both detected AND repaired.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-drift-guard-scalars.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$failures = @()

$guardScript = Join-Path $PSScriptRoot "jarvis-config-drift-guard.ps1"
if (-not (Test-Path $guardScript)) {
    Write-Host "FATAL: $guardScript not found" -ForegroundColor Red
    exit 2
}

# --------- Sandbox helper -----------------------------------------------
function New-Sandbox {
    param([string]$TomlContent, [string]$SollJson)
    $sb = New-Item -ItemType Directory -Path (Join-Path $env:TEMP "drift-guard-test-$([guid]::NewGuid().Guid.Substring(0,8))") -Force
    $tomlPath = Join-Path $sb "jarvis.toml"
    $sollPath = Join-Path $sb "config-soll.json"
    [System.IO.File]::WriteAllText($tomlPath, $TomlContent, (New-Object System.Text.UTF8Encoding($false)))
    [System.IO.File]::WriteAllText($sollPath, $SollJson, (New-Object System.Text.UTF8Encoding($false)))
    New-Item -ItemType Directory -Path (Join-Path $sb "logs") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $sb "scripts") -Force | Out-Null
    return $sb.FullName
}

function Invoke-Guard {
    param([string]$Sandbox)
    & powershell -NoProfile -ExecutionPolicy Bypass -File $guardScript `
        -RepoRoot $Sandbox -SollFile (Join-Path $Sandbox "config-soll.json") *>&1 | Out-Null
}

function Assert-TomlContains {
    param([string]$Sandbox, [string]$Pattern, [string]$Label)
    $text = Get-Content (Join-Path $Sandbox "jarvis.toml") -Raw -Encoding utf8
    if ($text -notmatch $Pattern) {
        $script:failures += "$Label -- pattern '$Pattern' NOT found in TOML"
        Write-Host "  FAIL $Label" -ForegroundColor Red
    } else {
        Write-Host "  PASS $Label" -ForegroundColor Green
    }
}

# --------- TEST 1 -------------------------------------------------------
# Bool drift: enabled = true must become enabled = false (unquoted!).
Write-Host "TEST 1: bool drift (enabled true -> false)" -ForegroundColor Cyan
$sb1 = New-Sandbox `
    -TomlContent "[memory.legacy_curator]`nenabled = true`n" `
    -SollJson '{"memory.legacy_curator": {"enabled": false}}'
# Drift-guard will set the TOML read-only at end -- clear that first.
$tomlItem = Get-Item (Join-Path $sb1 "jarvis.toml")
if ($tomlItem.IsReadOnly) { Set-ItemProperty (Join-Path $sb1 "jarvis.toml") -Name IsReadOnly -Value $false }
Invoke-Guard -Sandbox $sb1
# Clear read-only post-run so we can read freely.
$tomlItem = Get-Item (Join-Path $sb1 "jarvis.toml")
if ($tomlItem.IsReadOnly) { Set-ItemProperty (Join-Path $sb1 "jarvis.toml") -Name IsReadOnly -Value $false }
Assert-TomlContains -Sandbox $sb1 -Pattern '(?m)^enabled\s*=\s*false\s*$' -Label "bool unquoted (false)"
Assert-TomlContains -Sandbox $sb1 -Pattern '(?m)^\[memory\.legacy_curator\]\s*$' -Label "section preserved"
if ((Get-Content (Join-Path $sb1 "jarvis.toml") -Raw) -match 'enabled\s*=\s*true') {
    $failures += "TEST 1 -- 'enabled = true' still present"
    Write-Host "  FAIL stale 'enabled = true' still in TOML" -ForegroundColor Red
}
Remove-Item -Recurse -Force $sb1

# --------- TEST 2 -------------------------------------------------------
# Int drift: timeout_ms = 1000 must become timeout_ms = 4000.
Write-Host "TEST 2: int drift (timeout_ms 1000 -> 4000)" -ForegroundColor Cyan
$sb2 = New-Sandbox `
    -TomlContent "[ack_brain]`ntimeout_ms = 1000`nprovider = `"gemini`"`n" `
    -SollJson '{"ack_brain": {"timeout_ms": 4000, "provider": "gemini"}}'
$tomlItem = Get-Item (Join-Path $sb2 "jarvis.toml")
if ($tomlItem.IsReadOnly) { Set-ItemProperty (Join-Path $sb2 "jarvis.toml") -Name IsReadOnly -Value $false }
Invoke-Guard -Sandbox $sb2
$tomlItem = Get-Item (Join-Path $sb2 "jarvis.toml")
if ($tomlItem.IsReadOnly) { Set-ItemProperty (Join-Path $sb2 "jarvis.toml") -Name IsReadOnly -Value $false }
Assert-TomlContains -Sandbox $sb2 -Pattern '(?m)^timeout_ms\s*=\s*4000\s*$' -Label "int unquoted (4000)"
Assert-TomlContains -Sandbox $sb2 -Pattern '(?m)^provider\s*=\s*"gemini"\s*$' -Label "string still quoted"
Remove-Item -Recurse -Force $sb2

# --------- TEST 3 -------------------------------------------------------
# String drift (regression: ensure old behavior still works).
Write-Host "TEST 3: string drift (provider grok -> gemini)" -ForegroundColor Cyan
$sb3 = New-Sandbox `
    -TomlContent "[tts]`nprovider = `"grok-voice`"`n" `
    -SollJson '{"tts": {"provider": "gemini-flash-tts"}}'
$tomlItem = Get-Item (Join-Path $sb3 "jarvis.toml")
if ($tomlItem.IsReadOnly) { Set-ItemProperty (Join-Path $sb3 "jarvis.toml") -Name IsReadOnly -Value $false }
Invoke-Guard -Sandbox $sb3
$tomlItem = Get-Item (Join-Path $sb3 "jarvis.toml")
if ($tomlItem.IsReadOnly) { Set-ItemProperty (Join-Path $sb3 "jarvis.toml") -Name IsReadOnly -Value $false }
Assert-TomlContains -Sandbox $sb3 -Pattern '(?m)^provider\s*=\s*"gemini-flash-tts"\s*$' -Label "string still quoted post-fix"
Remove-Item -Recurse -Force $sb3

# --------- Result -------------------------------------------------------
if ($failures.Count -gt 0) {
    Write-Host "`n$($failures.Count) FAILURE(S):" -ForegroundColor Red
    foreach ($f in $failures) { Write-Host "  - $f" -ForegroundColor Red }
    exit 1
}
Write-Host "`nAll drift-guard scalar tests PASSED" -ForegroundColor Green
exit 0
