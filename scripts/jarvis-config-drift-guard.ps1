<#
.SYNOPSIS
    Config-Drift-Guard: prueft jarvis.toml + ENV-Overrides gegen die Soll-Liste
    in scripts/config-soll.json und repariert automatisch jeden Drift.

.DESCRIPTION
    Hintergrund:
      Am 2026-05-13 hat BUG-010 (Gemini-Flash-TTS leerer Audio-Buffer) sich in
      80 Minuten dreimal wiederholt, weil eine parallele zweite OpenClaw-
      Session jarvis.toml-Werte zurueckgerollt hat. ENV-Overrides + read-only-
      TOML waren noetig, sind aber nicht vollstaendig drift-resistent (Read-
      only kann entlockt werden, ENV kann geloescht werden). Dieses Skript
      ist die dritte Verteidigungs-Schicht: selbstheilender Watchdog.

    Ablauf pro Lauf:
      1. Liest scripts/config-soll.json (User-editierbar -- dort steht der
         "approved State").
      2. Liest jarvis.toml als Text.
      3. Vergleicht alle Soll-Keys mit den Ist-Werten via Section-aware Regex.
      4. Bei Mismatch: entfernt Read-only, schreibt den Soll-Wert per Replace,
         setzt Read-only wieder.
      5. Prueft die JARVIS__* ENV-Overrides im User-Scope. Setzt fehlende.
      6. Schreibt strukturiertes Log nach logs/config-drift-guard.log.

    Notification:
      Wenn -ToastOnDrift gesetzt ist und Drift erkannt wurde, BurntToast wird
      versucht (silent fallback bei Fehler).

.PARAMETER RepoRoot
    Pfad zum Repo-Root. Default: <USER_HOME>\Desktop\Personal Jarvis

.PARAMETER SollFile
    Pfad zur Soll-JSON. Default: $RepoRoot\scripts\config-soll.json

.PARAMETER DryRun
    Nur loggen was getan wuerde, kein Fix.

.PARAMETER ToastOnDrift
    Bei erkanntem Drift Toast-Notification senden (BurntToast falls vorhanden).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/jarvis-config-drift-guard.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/jarvis-config-drift-guard.ps1 -DryRun
#>

[CmdletBinding()]
param(
    [string]$RepoRoot      = "<USER_HOME>\Desktop\Personal Jarvis",
    [string]$SollFile      = "",
    [switch]$DryRun,
    [switch]$ToastOnDrift
)

$ErrorActionPreference = "Stop"

# ---------- Pfade ----------------------------------------------------------

if (-not $SollFile) { $SollFile = Join-Path $RepoRoot "scripts\config-soll.json" }
$tomlFile = Join-Path $RepoRoot "jarvis.toml"
$logDir   = Join-Path $RepoRoot "logs"
$logFile  = Join-Path $logDir "config-drift-guard.log"

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
if (-not (Test-Path $tomlFile)) { Write-Host "FATAL: jarvis.toml nicht gefunden bei $tomlFile"; exit 2 }
if (-not (Test-Path $SollFile)) { Write-Host "FATAL: Soll-Datei nicht gefunden bei $SollFile"; exit 3 }

function Write-Log {
    param([string]$Level, [string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts | $Level | $Message"
    Add-Content -Path $logFile -Value $line -Encoding utf8
    if ($Level -ne "DEBUG") { Write-Host $line }
}

# ---------- Soll lesen -----------------------------------------------------

try {
    $soll = Get-Content -Path $SollFile -Raw -Encoding utf8 | ConvertFrom-Json
} catch {
    Write-Log "FATAL" "Soll-Datei nicht parsebar: $_"
    exit 4
}

# ---------- TOML-Soll-vs-Ist-Vergleich -------------------------------------

# Wir nutzen Section-aware Regex: pro Section iterieren wir die Keys.
# Die Soll-Struktur hat zwei "echte" Sections (tts, brain) plus underscore-
# prefixed Metadata-Keys (_comment, _updated, _reason) die ignoriert werden.

$drifts = @()  # @{section, key, ist, soll}

foreach ($section in $soll.PSObject.Properties) {
    if ($section.Name.StartsWith("_")) { continue }
    $sectionName  = $section.Name
    $sectionData  = $section.Value
    foreach ($keyProp in $sectionData.PSObject.Properties) {
        $key  = $keyProp.Name
        # NOTE: use a distinct loop-local name ($sollValue), NOT $soll.
        # PowerShell foreach has no block scoping, so assigning to $soll here
        # would clobber the parsed-JSON object loaded at the top of the script.
        # The ENV-override loop below re-iterates $soll.PSObject.Properties --
        # if $soll were overwritten with the last scalar key value, that loop
        # would iterate a bare string's members instead of the config sections
        # and the ENV check would silently become a no-op (it always logged
        # "ENV-Overrides clean" regardless of the real ENV). Discovered + fixed
        # 2026-05-28 while hardening the brain.primary three-layer sync.
        $sollValue = $keyProp.Value
        # ist-Wert aus TOML extrahieren -- robuste Regex pro Section
        $tomlText = Get-Content -Path $tomlFile -Raw -Encoding utf8
        # Section-Block: alles von "[<section>]" bis zur naechsten "[..."-Zeile
        $sectionPattern = "(?ms)^\[$sectionName\][^\[]*"
        $sectionMatch = [regex]::Match($tomlText, $sectionPattern)
        if (-not $sectionMatch.Success) {
            Write-Log "WARN" "Section [$sectionName] nicht in TOML gefunden -- uebersprungen"
            continue
        }
        $sectionBody = $sectionMatch.Value
        # 2026-05-17 fix: capture BOTH quoted strings AND unquoted scalars
        # (booleans, integers, floats). The original pattern only matched
        # `key = "value"`, so any `enabled = false` / `timeout_ms = 4000`
        # entry in config-soll.json was silently skipped with a WARN —
        # the drift was never enforced.  Now group 1 = quoted, group 2 =
        # unquoted token (until whitespace / comment-marker / EOL).
        $keyPattern = "(?m)^\s*$key\s*=\s*(?:`"([^`"]*)`"|([^\s#`r`n]+))"
        $keyMatch = [regex]::Match($sectionBody, $keyPattern)
        if (-not $keyMatch.Success) {
            Write-Log "WARN" "Key '$key' in [$sectionName] nicht in TOML gefunden -- uebersprungen (Soll '$sollValue')"
            continue
        }
        if ($keyMatch.Groups[1].Success) {
            $ist = $keyMatch.Groups[1].Value
        } else {
            $ist = $keyMatch.Groups[2].Value
        }
        # Normalize both sides to lower-case strings so the JSON bool
        # `$false` ("False") matches the TOML bool `false`, and ints
        # like 4000 compare as their string form.
        $istNorm  = ([string]$ist).Trim().ToLower()
        $sollNorm = ([string]$sollValue).Trim().ToLower()
        if ($istNorm -ne $sollNorm) {
            $drifts += [PSCustomObject]@{ Section=$sectionName; Key=$key; Ist=$ist; Soll=$sollValue }
        }
    }
}

# ---------- ENV-Overrides pruefen -----------------------------------------

# Die ENV-Overrides haben Format JARVIS__<SECTION>__<KEY> (uppercase).
# 2026-05-18 H11 fix: ENV values are always strings, but $keyProp.Value
# carries the original JSON type (bool/int/float/string). Compare via
# the same lowercase-trim normalization the TOML branch uses -- otherwise
# bool $false stringifies to "False" and never matches the user-scope
# "false" they set manually, looping a no-op FIX every 5 min.
function Format-EnvLiteral {
    param($v)
    if ($v -is [bool]) { return $(if ($v) { "true" } else { "false" }) }
    return [string]$v
}
$envMissing = @()
foreach ($section in $soll.PSObject.Properties) {
    if ($section.Name.StartsWith("_")) { continue }
    foreach ($keyProp in $section.Value.PSObject.Properties) {
        $envName  = "JARVIS__" + $section.Name.ToUpper() + "__" + $keyProp.Name.ToUpper()
        $envValue = [Environment]::GetEnvironmentVariable($envName, "User")
        $sollLit  = Format-EnvLiteral $keyProp.Value
        if (-not $envValue -or $envValue.Trim().ToLower() -ne $sollLit.Trim().ToLower()) {
            $envMissing += [PSCustomObject]@{ EnvName=$envName; Soll=$sollLit; Ist=$envValue }
        }
    }
}

# ---------- Fixes anwenden ------------------------------------------------

$fixesApplied = 0

if ($drifts.Count -gt 0) {
    Write-Log "DRIFT" "TOML-Drift erkannt: $($drifts.Count) Key(s) abweichend"
    foreach ($d in $drifts) {
        Write-Log "DRIFT" "  [$($d.Section)] $($d.Key): ist='$($d.Ist)'  soll='$($d.Soll)'"
    }
    if (-not $DryRun) {
        # Read-only Flag temporaer entfernen
        $tomlItem = Get-Item -Path $tomlFile
        $wasReadOnly = $tomlItem.IsReadOnly
        if ($wasReadOnly) {
            Set-ItemProperty -Path $tomlFile -Name IsReadOnly -Value $false
        }
        # Fixes anwenden -- Section-aware, PowerShell -replace mit Multiline-Flag
        $tomlText = Get-Content -Path $tomlFile -Raw -Encoding utf8
        foreach ($d in $drifts) {
            $sectionPattern = "(?ms)^\[$($d.Section)\][^\[]*"
            $sectionMatch = [regex]::Match($tomlText, $sectionPattern)
            if (-not $sectionMatch.Success) { continue }
            $sectionBody = $sectionMatch.Value
            # 2026-05-18 H11 fix: the FIX regex used to match only quoted
            # strings (`key = "value"`). Combined with the 2026-05-17
            # detection-side fix that started catching unquoted scalars
            # (`enabled = false`, `timeout_ms = 4000`), this meant the
            # drift was DETECTED but never REPAIRED -- the WARN branch
            # below fired on every loop. Now we dispatch on the JSON
            # type of $d.Soll: bool/int/float emit unquoted, strings
            # stay quoted (TOML semantics).
            $sollType = $null
            if ($d.Soll -is [bool])    { $sollType = "bool" }
            elseif ($d.Soll -is [int])     { $sollType = "int" }
            elseif ($d.Soll -is [long])    { $sollType = "int" }
            elseif ($d.Soll -is [double])  { $sollType = "float" }
            elseif ($d.Soll -is [decimal]) { $sollType = "float" }
            else                           { $sollType = "string" }

            # Use ${1} (not $1) so PowerShell's -replace parser does NOT
            # accidentally fuse the backref with leading digits in the
            # replacement value -- e.g. '$1' + '4000' would become
            # '$14000' (= regex group #14 = empty), silently corrupting
            # the file as "...=$14000...". See 2026-05-18 H11 debug run.
            if ($sollType -eq "string") {
                # key = "value"
                $keyPattern = '(?m)^(\s*' + [regex]::Escape($d.Key) + '\s*=\s*)"[^"]*"'
                $replacement = '${1}"' + $d.Soll + '"'
            } else {
                # key = literal (bool/int/float). TOML wants lowercase
                # booleans, so normalize PowerShell's $true / $false.
                $literal = if ($sollType -eq "bool") {
                    if ($d.Soll) { "true" } else { "false" }
                } else {
                    [string]$d.Soll
                }
                # Match the existing assignment regardless of whether it
                # is currently quoted or unquoted -- we always rewrite to
                # the literal form for non-string types.
                $keyPattern = '(?m)^(\s*' + [regex]::Escape($d.Key) + '\s*=\s*)(?:"[^"]*"|[^\s#\r\n]+)'
                $replacement = '${1}' + $literal
            }

            $newSectionBody = $sectionBody -replace $keyPattern, $replacement
            if ($newSectionBody -eq $sectionBody) {
                Write-Log "WARN" "  [$($d.Section)] $($d.Key) -- Pattern matched nicht ($sollType), Fix uebersprungen"
                continue
            }
            $tomlText = $tomlText.Replace($sectionBody, $newSectionBody)
            Write-Log "FIX" "  [$($d.Section)] $($d.Key) := '$($d.Soll)' ($sollType)"
            $fixesApplied++
        }
        # PS 5.1 `-Encoding utf8` writes a BOM, which Python's `tomllib`
        # rejects with "Invalid statement at line 1, column 1". Write raw
        # UTF-8 without BOM via .NET so Jarvis can still parse the file.
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($tomlFile, $tomlText, $utf8NoBom)
        # Read-only wieder setzen
        Set-ItemProperty -Path $tomlFile -Name IsReadOnly -Value $true
    } else {
        Write-Log "INFO" "DryRun -- keine Aenderung an jarvis.toml"
    }
} else {
    Write-Log "DEBUG" "TOML clean -- keine Drift"
}

if ($envMissing.Count -gt 0) {
    Write-Log "DRIFT" "ENV-Override fehlt/abweichend: $($envMissing.Count) Variable(n)"
    foreach ($e in $envMissing) {
        Write-Log "DRIFT" "  $($e.EnvName): ist='$($e.Ist)'  soll='$($e.Soll)'"
    }
    if (-not $DryRun) {
        foreach ($e in $envMissing) {
            [Environment]::SetEnvironmentVariable($e.EnvName, $e.Soll, "User")
            Write-Log "FIX" "  $($e.EnvName) := '$($e.Soll)' (User-Scope)"
            $fixesApplied++
        }
    } else {
        Write-Log "INFO" "DryRun -- keine ENV-Aenderung"
    }
} else {
    Write-Log "DEBUG" "ENV-Overrides clean -- alle gesetzt"
}

# ---------- TOML read-only sicherstellen ----------------------------------

# We re-assert the read-only flag every run as a defence-in-depth measure
# (a rogue session could have cleared it). But the *meaning* of that re-lock
# depends on whether this run actually saw drift:
#
#   * Drift was detected this run  -> the unlocked TOML was an active attack
#     surface; re-locking is part of the FIX. Log it loud, count it.
#   * No drift this run (TOML/ENV fully synced) -> the TOML is simply writable
#     because the desktop app's provider switch (UI) just rewrote it via the
#     atomic config_writer (tempfile + os.replace drops the read-only bit).
#     Re-locking here is routine maintenance, NOT a correction. It must be a
#     true no-op from the operator's point of view: DEBUG-level only, and it
#     MUST NOT increment $fixesApplied (otherwise a synced UI switch would
#     fire a Toast and an INFO "Fix(es) angewendet" line on the very next run,
#     i.e. log spam / churn the task forbids). The provider value is left
#     untouched either way -- this branch only ever flips a file *attribute*.
$driftThisRun = ($drifts.Count -gt 0) -or ($envMissing.Count -gt 0)
if (-not $DryRun) {
    $tomlItem = Get-Item -Path $tomlFile
    if (-not $tomlItem.IsReadOnly) {
        Set-ItemProperty -Path $tomlFile -Name IsReadOnly -Value $true
        if ($driftThisRun) {
            Write-Log "FIX" "jarvis.toml read-only-Flag re-aktiviert"
            $fixesApplied++
        } else {
            # Synced state: routine re-lock after a UI provider switch. Quiet.
            Write-Log "DEBUG" "jarvis.toml read-only-Flag re-aktiviert (routine, kein Drift)"
        }
    }
}

# ---------- Toast bei Drift -----------------------------------------------

if ($ToastOnDrift -and $fixesApplied -gt 0) {
    try {
        Import-Module BurntToast -ErrorAction Stop
        New-BurntToastNotification `
            -Text "Personal Jarvis -- Config-Drift gefixt", "$fixesApplied Aenderung(en) repariert" `
            -AppLogo (Join-Path $RepoRoot "jarvis\assets\icon.ico" -ErrorAction SilentlyContinue) `
            -ErrorAction Stop
    } catch {
        Write-Log "WARN" "BurntToast-Notification fehlgeschlagen (silent): $_"
    }
}

# ---------- Final ----------------------------------------------------------

if ($fixesApplied -gt 0) {
    Write-Log "INFO" "Drift-Guard-Lauf abgeschlossen -- $fixesApplied Fix(es) angewendet"
} else {
    Write-Log "DEBUG" "Drift-Guard-Lauf abgeschlossen -- alles ok"
}

exit 0
