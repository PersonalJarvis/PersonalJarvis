<#
.SYNOPSIS
    Compares jarvis.toml and environment overrides with the desired-state file
    in scripts/config-soll.json and repairs drift automatically.

.DESCRIPTION
    BUG-010 recurred three times in 80 minutes because a parallel session
    reverted jarvis.toml values. Environment overrides and a read-only TOML
    file reduced the risk but were not sufficient on their own. This script is
    the third defense layer: a self-healing watchdog.

    Each run:
      1. Reads scripts/config-soll.json, the user-editable approved state.
      2. Reads jarvis.toml as text.
      3. Compares desired scalar keys with the TOML values.
      4. Repairs mismatches while preserving the read-only attribute.
      5. Repairs scalar JARVIS__* overrides in the selected environment scope.
      6. Removes obsolete structured overrides that cannot be represented by
         the scalar environment codec.
      7. Writes a structured log to logs/config-drift-guard.log.

.PARAMETER RepoRoot
    Repository root. Defaults to the parent of this script's directory.

.PARAMETER DesiredFile
    Desired-state JSON path. Defaults to scripts/config-soll.json below RepoRoot.

.PARAMETER EnvironmentTarget
    Environment scope used for reads and writes. User is the production
    default. Process exists for isolated behavioral tests.

.PARAMETER DryRun
    Reports intended repairs without changing TOML or environment state.

.PARAMETER ToastOnDrift
    Attempts a BurntToast notification after a repair.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/jarvis-config-drift-guard.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/jarvis-config-drift-guard.ps1 -DryRun
#>

[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$DesiredFile = "",
    [ValidateSet("User", "Process")]
    [string]$EnvironmentTarget = "User",
    [switch]$DryRun,
    [switch]$ToastOnDrift
)

$ErrorActionPreference = "Stop"

# ---------- Paths ----------------------------------------------------------

if (-not $RepoRoot) {
    $RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
}
if (-not $DesiredFile) {
    $DesiredFile = Join-Path $RepoRoot "scripts\config-soll.json"
}
$tomlFile = Join-Path $RepoRoot "jarvis.toml"
$logDir = Join-Path $RepoRoot "logs"
$logFile = Join-Path $logDir "config-drift-guard.log"

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
if (-not (Test-Path $tomlFile)) {
    Write-Host "FATAL: jarvis.toml was not found at $tomlFile"
    exit 2
}
if (-not (Test-Path $DesiredFile)) {
    Write-Host "FATAL: desired-state file was not found at $DesiredFile"
    exit 3
}

function Write-Log {
    param([string]$Level, [string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$timestamp | $Level | $Message"
    Add-Content -Path $logFile -Value $line -Encoding utf8
    if ($Level -ne "DEBUG") { Write-Host $line }
}

# ---------- Desired state --------------------------------------------------

try {
    $desired = Get-Content -Path $DesiredFile -Raw -Encoding utf8 | ConvertFrom-Json
} catch {
    Write-Log "FATAL" "Desired-state file could not be parsed: $_"
    exit 4
}

# A provider chosen through Jarvis is the authoritative STT selection. Older
# desktop processes may have left a different User-scope environment value or
# desired-state entry behind; repairing TOML from either would silently undo
# the user's explicit choice. Use the marked TOML provider as the in-memory
# desired value for both comparisons. The writer normally keeps all layers in
# sync, while this protects the partial-write and stale-process edge cases.
$tomlSnapshot = Get-Content -Path $tomlFile -Raw -Encoding utf8
$sttSectionMatch = [regex]::Match($tomlSnapshot, '(?ms)^\[stt\][^\[]*')
if ($sttSectionMatch.Success) {
    $sttSectionBody = $sttSectionMatch.Value
    $selectionMarker = [regex]::Match(
        $sttSectionBody,
        '(?im)^\s*provider_user_selected\s*=\s*true\b'
    )
    $selectedProvider = [regex]::Match(
        $sttSectionBody,
        '(?m)^\s*provider\s*=\s*"([^"]+)"'
    )
    if (
        $selectionMarker.Success -and
        $selectedProvider.Success -and
        $null -ne $desired.stt -and
        $null -ne $desired.stt.PSObject.Properties['provider']
    ) {
        $desired.stt.provider = $selectedProvider.Groups[1].Value
    }
}

# A JARVIS__SECTION__KEY override can represent one scalar only. PowerShell
# stringifies nested JSON objects as "@{key=value}"; Python then receives a
# string where the config schema expects a mapping and the desktop app cannot
# boot. Structured values stay in their TOML sub-table and never enter the
# scalar environment layer.
function Test-IsScalarConfigValue {
    param($Value)
    return (
        ($Value -is [string]) -or
        ($Value -is [bool]) -or
        ($Value -is [byte]) -or
        ($Value -is [int16]) -or
        ($Value -is [int32]) -or
        ($Value -is [int64]) -or
        ($Value -is [single]) -or
        ($Value -is [double]) -or
        ($Value -is [decimal])
    )
}

# ---------- TOML desired-versus-actual comparison -------------------------

# Metadata keys use an underscore prefix and are intentionally ignored.
$drifts = @()

foreach ($section in $desired.PSObject.Properties) {
    if ($section.Name.StartsWith("_")) { continue }
    $sectionName = $section.Name
    $sectionData = $section.Value
    foreach ($keyProperty in $sectionData.PSObject.Properties) {
        $key = $keyProperty.Name
        # PowerShell foreach blocks share scope. Keep this value distinct from
        # $desired so the later environment loop still sees the parsed object.
        $desiredValue = $keyProperty.Value
        if (-not (Test-IsScalarConfigValue $desiredValue)) {
            continue
        }

        $tomlText = Get-Content -Path $tomlFile -Raw -Encoding utf8
        $sectionPattern = "(?ms)^\[$sectionName\][^\[]*"
        $sectionMatch = [regex]::Match($tomlText, $sectionPattern)
        if (-not $sectionMatch.Success) {
            Write-Log "WARN" "Section [$sectionName] was not found in TOML; skipped"
            continue
        }
        $sectionBody = $sectionMatch.Value

        # Group 1 captures quoted strings; group 2 captures unquoted scalars.
        $keyPattern = "(?m)^\s*$key\s*=\s*(?:`"([^`"]*)`"|([^\s#`r`n]+))"
        $keyMatch = [regex]::Match($sectionBody, $keyPattern)
        if (-not $keyMatch.Success) {
            Write-Log "WARN" "Key '$key' in [$sectionName] was not found; desired '$desiredValue' was skipped"
            continue
        }
        if ($keyMatch.Groups[1].Success) {
            $actual = $keyMatch.Groups[1].Value
        } else {
            $actual = $keyMatch.Groups[2].Value
        }

        # Normalize booleans and numerics through their lowercase string form.
        $actualNormalized = ([string]$actual).Trim().ToLower()
        $desiredNormalized = ([string]$desiredValue).Trim().ToLower()
        if ($actualNormalized -ne $desiredNormalized) {
            $drifts += [PSCustomObject]@{
                Section = $sectionName
                Key = $key
                Actual = $actual
                Desired = $desiredValue
            }
        }
    }
}

# ---------- Environment comparison ----------------------------------------

function Format-EnvLiteral {
    param($Value)
    if ($Value -is [bool]) {
        return $(if ($Value) { "true" } else { "false" })
    }
    return [string]$Value
}

$missingEnv = @()
$obsoleteStructuredEnv = @()
foreach ($section in $desired.PSObject.Properties) {
    if ($section.Name.StartsWith("_")) { continue }
    foreach ($keyProperty in $section.Value.PSObject.Properties) {
        $envName = "JARVIS__" + $section.Name.ToUpper() + "__" + $keyProperty.Name.ToUpper()
        $envValue = [Environment]::GetEnvironmentVariable($envName, $EnvironmentTarget)
        if (-not (Test-IsScalarConfigValue $keyProperty.Value)) {
            if ($null -ne $envValue) {
                $obsoleteStructuredEnv += $envName
            }
            continue
        }
        $desiredLiteral = Format-EnvLiteral $keyProperty.Value
        if (
            $null -eq $envValue -or
            $envValue.Trim().ToLower() -ne $desiredLiteral.Trim().ToLower()
        ) {
            $missingEnv += [PSCustomObject]@{
                EnvName = $envName
                Desired = $desiredLiteral
                Actual = $envValue
            }
        }
    }
}

# ---------- Apply repairs --------------------------------------------------

$fixesApplied = 0

if ($drifts.Count -gt 0) {
    Write-Log "DRIFT" "Detected $($drifts.Count) divergent TOML key(s)"
    foreach ($drift in $drifts) {
        Write-Log "DRIFT" "  [$($drift.Section)] $($drift.Key): actual='$($drift.Actual)' desired='$($drift.Desired)'"
    }
    if (-not $DryRun) {
        $tomlItem = Get-Item -Path $tomlFile
        $wasReadOnly = $tomlItem.IsReadOnly
        if ($wasReadOnly) {
            Set-ItemProperty -Path $tomlFile -Name IsReadOnly -Value $false
        }

        $tomlText = Get-Content -Path $tomlFile -Raw -Encoding utf8
        foreach ($drift in $drifts) {
            $sectionPattern = "(?ms)^\[$($drift.Section)\][^\[]*"
            $sectionMatch = [regex]::Match($tomlText, $sectionPattern)
            if (-not $sectionMatch.Success) { continue }
            $sectionBody = $sectionMatch.Value

            if ($drift.Desired -is [bool]) { $desiredType = "bool" }
            elseif ($drift.Desired -is [int16]) { $desiredType = "int" }
            elseif ($drift.Desired -is [int32]) { $desiredType = "int" }
            elseif ($drift.Desired -is [int64]) { $desiredType = "int" }
            elseif ($drift.Desired -is [single]) { $desiredType = "float" }
            elseif ($drift.Desired -is [double]) { $desiredType = "float" }
            elseif ($drift.Desired -is [decimal]) { $desiredType = "float" }
            else { $desiredType = "string" }

            # ${1} prevents a leading digit in the value from being parsed as
            # part of the regex group number, for example $1 + 4000 -> $14000.
            if ($desiredType -eq "string") {
                $keyPattern = '(?m)^(\s*' + [regex]::Escape($drift.Key) + '\s*=\s*)"[^"]*"'
                $replacement = '${1}"' + $drift.Desired + '"'
            } else {
                $literal = if ($desiredType -eq "bool") {
                    if ($drift.Desired) { "true" } else { "false" }
                } else {
                    [string]$drift.Desired
                }
                $keyPattern = '(?m)^(\s*' + [regex]::Escape($drift.Key) + '\s*=\s*)(?:"[^"]*"|[^\s#\r\n]+)'
                $replacement = '${1}' + $literal
            }

            $newSectionBody = $sectionBody -replace $keyPattern, $replacement
            if ($newSectionBody -eq $sectionBody) {
                Write-Log "WARN" "  [$($drift.Section)] $($drift.Key) did not match the repair pattern ($desiredType); skipped"
                continue
            }
            $tomlText = $tomlText.Replace($sectionBody, $newSectionBody)
            Write-Log "FIX" "  [$($drift.Section)] $($drift.Key) := '$($drift.Desired)' ($desiredType)"
            $fixesApplied++
        }

        # Windows PowerShell 5.1 adds a BOM for -Encoding utf8. Write explicit
        # BOM-free UTF-8 so Python tomllib can parse the file.
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($tomlFile, $tomlText, $utf8NoBom)
        Set-ItemProperty -Path $tomlFile -Name IsReadOnly -Value $true
    } else {
        Write-Log "INFO" "DryRun -- jarvis.toml was not changed"
    }
} else {
    Write-Log "DEBUG" "TOML is in sync"
}

if ($missingEnv.Count -gt 0) {
    Write-Log "DRIFT" "Detected $($missingEnv.Count) missing or divergent environment override(s)"
    foreach ($entry in $missingEnv) {
        Write-Log "DRIFT" "  $($entry.EnvName): actual='$($entry.Actual)' desired='$($entry.Desired)'"
    }
    if (-not $DryRun) {
        foreach ($entry in $missingEnv) {
            [Environment]::SetEnvironmentVariable(
                $entry.EnvName,
                $entry.Desired,
                $EnvironmentTarget
            )
            Write-Log "FIX" "  $($entry.EnvName) := '$($entry.Desired)' ($EnvironmentTarget scope)"
            $fixesApplied++
        }
    } else {
        Write-Log "INFO" "DryRun -- environment overrides were not changed"
    }
} else {
    Write-Log "DEBUG" "Scalar environment overrides are in sync"
}

if ($obsoleteStructuredEnv.Count -gt 0) {
    Write-Log "DRIFT" "Removing $($obsoleteStructuredEnv.Count) unsupported structured environment override(s)"
    if (-not $DryRun) {
        foreach ($envName in $obsoleteStructuredEnv) {
            [Environment]::SetEnvironmentVariable($envName, $null, $EnvironmentTarget)
            Write-Log "FIX" "  removed $envName from $EnvironmentTarget-scope environment"
            $fixesApplied++
        }
    } else {
        Write-Log "INFO" "DryRun -- structured environment overrides were not removed"
    }
}

# ---------- Preserve TOML read-only state ---------------------------------

# Re-assert the read-only flag as defense in depth. A routine re-lock after a
# synchronized UI write stays at DEBUG and does not count as a repair.
$driftThisRun = (
    ($drifts.Count -gt 0) -or
    ($missingEnv.Count -gt 0) -or
    ($obsoleteStructuredEnv.Count -gt 0)
)
if (-not $DryRun) {
    $tomlItem = Get-Item -Path $tomlFile
    if (-not $tomlItem.IsReadOnly) {
        Set-ItemProperty -Path $tomlFile -Name IsReadOnly -Value $true
        if ($driftThisRun) {
            Write-Log "FIX" "Re-enabled the jarvis.toml read-only flag"
            $fixesApplied++
        } else {
            Write-Log "DEBUG" "Re-enabled the jarvis.toml read-only flag after a synchronized write"
        }
    }
}

# ---------- Optional toast -------------------------------------------------

if ($ToastOnDrift -and $fixesApplied -gt 0) {
    try {
        Import-Module BurntToast -ErrorAction Stop
        New-BurntToastNotification `
            -Text "Personal Jarvis -- config drift repaired", "$fixesApplied repair(s) applied" `
            -AppLogo (Join-Path $RepoRoot "jarvis\assets\icon.ico" -ErrorAction SilentlyContinue) `
            -ErrorAction Stop
    } catch {
        Write-Log "WARN" "BurntToast notification failed: $_"
    }
}

if ($fixesApplied -gt 0) {
    Write-Log "INFO" "Drift-guard run completed -- $fixesApplied repair(s) applied"
} else {
    Write-Log "DEBUG" "Drift-guard run completed -- state is clean"
}

exit 0
