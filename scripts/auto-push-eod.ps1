<#
.SYNOPSIS
    End-of-Day Auto-Push: spiegelt alle lokalen Branches mit Backup-Tags auf GitHub.

.DESCRIPTION
    Iteriert ueber alle lokalen Branches, setzt pro Branch einen safety/eod-*-Tag
    und pusht ihn nach origin. Branches ohne Upstream werden mit -u angelegt.
    Schreibt strukturiertes Log nach logs/auto-push-eod.log.

    Working-Tree-Dirty-Check: Bricht ab, damit nichts uncommittetes "verpasst" wird.
    Detached HEAD wird uebersprungen. main wird nie geforced.

    Phase 11 (Welle 4 Backstop): Worktrees mit aktiver Jarvis-Agent-Session
    (.openclaw_state\<sid>\openclaw_state\ existiert UND wurde innerhalb der
    letzten -JarvisAgentActiveMinutes geaendert) werden vom Push uebersprungen,
    damit ein laufender Sub-Agent nicht mitten im Run einen halben Stand
    upstream pusht. Mit -JarvisAgentWarnOnly wird nur gewarnt statt geskippt.

.PARAMETER RepoRoot
    Pfad zum Repo-Root. Default: C:\Users\Administrator\Desktop\Personal Jarvis

.PARAMETER DryRun
    Nur loggen was getan wuerde, kein Tag und kein Push.

.PARAMETER JarvisAgentActiveMinutes
    Schwellwert fuer "aktive" Jarvis-Agent-Session: Modify-Time juenger als X
    Minuten. Default: 30. Setze auf 0 um die Jarvis-Agent-Erkennung komplett zu
    deaktivieren.

.PARAMETER JarvisAgentWarnOnly
    Wenn gesetzt: aktive Jarvis-Agent-Worktrees werden nur geloggt aber trotzdem
    gepusht. Ohne diesen Switch wird der zugehoerige Branch geskippt.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/auto-push-eod.ps1 -DryRun

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/auto-push-eod.ps1
#>

[CmdletBinding()]
param(
    [string]$RepoRoot = "C:\Users\Administrator\Desktop\Personal Jarvis",
    [switch]$DryRun,
    [int]$JarvisAgentActiveMinutes = 30,
    [switch]$JarvisAgentWarnOnly
)

# ---------- Setup ----------
$ErrorActionPreference = "Stop"
$OutputEncoding        = [System.Text.UTF8Encoding]::new($false)

if (-not (Test-Path $RepoRoot)) {
    Write-Host "FEHLER: Repo-Root nicht gefunden: $RepoRoot"
    exit 2
}

Set-Location -LiteralPath $RepoRoot

# Log-Verzeichnis sicherstellen
$logDir  = Join-Path $RepoRoot "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$logFile = Join-Path $logDir "auto-push-eod.log"

$ts          = Get-Date -Format "yyyyMMdd-HHmmss"
$tsHuman     = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$runMode     = if ($DryRun) { "DRY-RUN" } else { "LIVE" }
$pushedCount = 0
$failedCount = 0
$skippedCount= 0
$failures    = @()

function Write-Log {
    param([string]$Level, [string]$Message)
    $line = "[$tsHuman] [$Level] $Message"
    Write-Host $line
    $line | Out-File -FilePath $logFile -Append -Encoding utf8
}

# PowerShell-5.1-Quirk: `git ... 2>&1` wrappt jede stderr-Zeile in einen
# NativeCommandError und setzt $? = $false. Mit ErrorActionPreference="Continue"
# wird das zu einer Warnung statt einem terminating Error. `Out-String` weiter
# unten neutralisiert die Wrapper komplett, so dass -match-Checks sauber laufen.
$ErrorActionPreference = "Continue"

# ---------- Pre-Flight ----------
Write-Log "INFO" "=== Auto-Push EoD START ($runMode) ==="
Write-Log "INFO" "Repo-Root: $RepoRoot"

# Git-Repo?
try {
    $null = git rev-parse --git-dir 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "kein Git-Repo" }
} catch {
    Write-Log "FATAL" "Kein gueltiges Git-Repository in $RepoRoot. Abbruch."
    exit 3
}

# Detached HEAD? -> ueberspringen, aber nicht hart abbrechen (nur Status loggen)
$headRef = (git symbolic-ref -q HEAD 2>$null)
if ([string]::IsNullOrWhiteSpace($headRef)) {
    Write-Log "WARN" "HEAD ist detached. Aktueller Checkout wird nicht gepusht; Branch-Liste wird trotzdem verarbeitet."
}

# Working Tree dirty?
$dirty = git status --porcelain 2>$null
if (-not [string]::IsNullOrWhiteSpace($dirty)) {
    $dirtyLines = ($dirty -split "`n") | Where-Object { $_ -ne "" }
    Write-Log "SKIP" "Working tree dirty ($($dirtyLines.Count) Datei(en) geaendert/untracked). Bitte committen oder stashen, dann erneut."
    foreach ($d in $dirtyLines) { Write-Log "SKIP" "  $d" }
    Write-Log "INFO" "=== Auto-Push EoD ENDE (skipped: dirty) ==="
    # Exit 0 ist hier korrekt: Skript hat sauber erkannt, dass es nichts tun darf.
    exit 0
}

# ---------- Sync mit Remote ----------
Write-Log "INFO" "git fetch --all --prune --tags ..."
if (-not $DryRun) {
    $fetchOut = git fetch --all --prune --tags 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Log "FATAL" "git fetch fehlgeschlagen: $fetchOut"
        exit 4
    }
} else {
    Write-Log "INFO" "  [dry-run] uebersprungen"
}

# ---------- Branch-Liste ----------
$branchInfo = git for-each-ref --format='%(refname:short)|%(upstream:short)' refs/heads/ 2>$null
if ([string]::IsNullOrWhiteSpace($branchInfo)) {
    Write-Log "WARN" "Keine lokalen Branches gefunden."
    Write-Log "INFO" "=== Auto-Push EoD ENDE ==="
    exit 0
}

$branches = $branchInfo -split "`n" | Where-Object { $_ -match '\S' }
Write-Log "INFO" "$($branches.Count) lokale Branch(es) gefunden."

# ---------- Phase-11-Backstop: aktive Jarvis-Agent-Worktrees ----------
# Idee: Worktree hat .openclaw_state\<session>\openclaw_state\ und das Verzeichnis
# wurde innerhalb der letzten -JarvisAgentActiveMinutes geaendert (Subprocess
# schreibt run.log + state-Files). Solange die Welle laeuft, wuerde ein push
# einen halben Stand spiegeln. Skip dieser Branches (oder warn-only).

$jarvisAgentBlockedBranches = @{}  # Hash<branch, reason>
if ($JarvisAgentActiveMinutes -gt 0) {
    Write-Log "INFO" "Jarvis-Agent-Backstop aktiv (Schwelle: $JarvisAgentActiveMinutes Min, Modus: $(if ($JarvisAgentWarnOnly) { 'WARN' } else { 'SKIP' }))."

    $worktreeRaw = git worktree list --porcelain 2>$null
    if (-not [string]::IsNullOrWhiteSpace($worktreeRaw)) {
        $worktreeLines = $worktreeRaw -split "`n"
        $cutoff = (Get-Date).AddMinutes(-1 * $JarvisAgentActiveMinutes)
        $currentWt = ""
        $currentBranch = ""

        foreach ($wtLine in $worktreeLines) {
            $wtLine = $wtLine.TrimEnd()
            if ($wtLine -match '^worktree\s+(.+)$') {
                $currentWt = $matches[1]
                $currentBranch = ""
                continue
            }
            if ($wtLine -match '^branch\s+refs/heads/(.+)$') {
                $currentBranch = $matches[1]
            }
            if ($wtLine -eq "" -or $wtLine -eq $null) {
                # Block-Ende: pruefen
                if ($currentWt -and $currentBranch) {
                    $stateRoot = Join-Path $currentWt ".openclaw_state"
                    if (Test-Path -LiteralPath $stateRoot) {
                        $recentSession = Get-ChildItem -LiteralPath $stateRoot -Directory -ErrorAction SilentlyContinue |
                            Where-Object { $_.LastWriteTime -gt $cutoff } |
                            Select-Object -First 1
                        if ($recentSession) {
                            $age = [int]((Get-Date) - $recentSession.LastWriteTime).TotalMinutes
                            $jarvisAgentBlockedBranches[$currentBranch] = "active Jarvis-Agent session in $($recentSession.Name) (age: ${age}min, worktree: $currentWt)"
                        }
                    }
                }
                $currentWt = ""
                $currentBranch = ""
            }
        }
        # Letzten Block (ohne trailing blank line) auch verarbeiten
        if ($currentWt -and $currentBranch) {
            $stateRoot = Join-Path $currentWt ".openclaw_state"
            if (Test-Path -LiteralPath $stateRoot) {
                $recentSession = Get-ChildItem -LiteralPath $stateRoot -Directory -ErrorAction SilentlyContinue |
                    Where-Object { $_.LastWriteTime -gt $cutoff } |
                    Select-Object -First 1
                if ($recentSession) {
                    $age = [int]((Get-Date) - $recentSession.LastWriteTime).TotalMinutes
                    $jarvisAgentBlockedBranches[$currentBranch] = "active Jarvis-Agent session in $($recentSession.Name) (age: ${age}min, worktree: $currentWt)"
                }
            }
        }
    }

    if ($jarvisAgentBlockedBranches.Count -eq 0) {
        Write-Log "INFO" "Keine aktiven Jarvis-Agent-Sessions in irgendeinem Worktree gefunden."
    } else {
        $action = if ($JarvisAgentWarnOnly) { "WARN" } else { "SKIP" }
        foreach ($entry in $jarvisAgentBlockedBranches.GetEnumerator()) {
            Write-Log $action "Jarvis-Agent-Backstop trifft Branch '$($entry.Key)': $($entry.Value)"
        }
    }
}

# ---------- Pro Branch: Tag + Push ----------
foreach ($entry in $branches) {
    $parts = $entry -split '\|', 2
    $branch   = $parts[0].Trim()
    $upstream = if ($parts.Length -gt 1) { $parts[1].Trim() } else { "" }
    $hasUpstream = -not [string]::IsNullOrWhiteSpace($upstream)

    # Sanitize Branch-Name fuer Tag (Slashes erlaubt, aber wir nutzen den Originalnamen):
    $tagName = "safety/eod-$branch-$ts"

    Write-Log "INFO" "--- Branch: $branch (upstream: $(if ($hasUpstream) { $upstream } else { '<none>' })) ---"

    # Phase-11-Backstop: aktive Jarvis-Agent-Session?
    if ($jarvisAgentBlockedBranches.ContainsKey($branch) -and -not $JarvisAgentWarnOnly) {
        Write-Log "SKIP" "Branch '$branch' uebersprungen: $($jarvisAgentBlockedBranches[$branch])"
        $skippedCount++
        continue
    }

    # main divergiert?
    if ($branch -eq "main" -and $hasUpstream) {
        $aheadBehind = (git rev-list --left-right --count "origin/main...main" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $aheadBehind) {
            $ab = $aheadBehind -split '\s+'
            $behind = [int]$ab[0]
            $ahead  = [int]$ab[1]
            if ($behind -gt 0 -and $ahead -gt 0) {
                Write-Log "FAILED" "main divergiert von origin/main ($ahead ahead, $behind behind). Manuelle Inspektion noetig."
                $failedCount++
                $failures += "$branch (main divergent)"
                continue
            }
        }
    }

    # 1) Backup-Tag setzen
    if ($DryRun) {
        Write-Log "INFO" "  [dry-run] git tag '$tagName' $branch"
    } else {
        $tagOut = git tag $tagName $branch 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            # Tag existiert evtl. schon (idempotent: ok), oder anderer Fehler
            if ($tagOut -match "already exists") {
                Write-Log "INFO" "  Tag $tagName existiert bereits (idempotent ok)."
            } else {
                Write-Log "WARN" "  Tag-Anlage fehlgeschlagen: $tagOut"
            }
        } else {
            Write-Log "INFO" "  Backup-Tag gesetzt: $tagName"
        }
    }

    # 2) Push
    if ($DryRun) {
        if ($hasUpstream) {
            Write-Log "INFO" "  [dry-run] git push origin $branch"
        } else {
            Write-Log "INFO" "  [dry-run] git push -u origin $branch  (Erst-Push)"
        }
        $pushedCount++
        continue
    }

    if ($hasUpstream) {
        $pushOut = git push origin $branch 2>&1 | Out-String
    } else {
        $pushOut = git push -u origin $branch 2>&1 | Out-String
    }

    if ($LASTEXITCODE -eq 0) {
        Write-Log "OK" "  Push erfolgreich."
        $pushedCount++
    } else {
        $msg = ($pushOut -join " ").Trim()
        # Auth-Klassifikation
        if ($msg -match "Authentication failed|could not read Username|403|401|Permission to .* denied") {
            Write-Log "FAILED" "  AUTH-FEHLER bei Branch '$branch': $msg"
            $failedCount++
            $failures += "$branch (auth)"
        } elseif ($msg -match "non-fast-forward|rejected|fetch first") {
            Write-Log "FAILED" "  Branch '$branch' divergiert von Remote (non-fast-forward). Kein --force gesetzt."
            $failedCount++
            $failures += "$branch (non-ff)"
        } else {
            Write-Log "FAILED" "  Push-Fehler bei '$branch': $msg"
            $failedCount++
            $failures += "$branch (other)"
        }
    }
}

# ---------- Tags pushen ----------
if ($DryRun) {
    Write-Log "INFO" "[dry-run] git push --tags"
} else {
    Write-Log "INFO" "git push --tags ..."
    $tagsPushOut = git push --tags 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        Write-Log "OK" "Tags gepusht."
    } else {
        Write-Log "WARN" "Tag-Push hatte Fehler: $($tagsPushOut -join ' ')"
    }
}

# ---------- Zusammenfassung + Toast ----------
$total = $branches.Count
Write-Log "INFO" "=== Zusammenfassung (${runMode}): $pushedCount/$total gepusht, $failedCount Fehler, $skippedCount uebersprungen ==="
if ($failedCount -gt 0) {
    Write-Log "INFO" "Fehlgeschlagene Branches: $($failures -join ', ')"
}
Write-Log "INFO" "=== Auto-Push EoD ENDE ==="

# Toast-Notification (best-effort, niemals fatal)
$toastMsg = "Auto-Push ${runMode}: $pushedCount/$total gespiegelt, $failedCount Fehler"
try {
    if (Get-Module -ListAvailable -Name BurntToast -ErrorAction SilentlyContinue) {
        Import-Module BurntToast -ErrorAction Stop
        New-BurntToastNotification -Text "Personal Jarvis EoD-Push", $toastMsg | Out-Null
    } else {
        # Fallback: Windows-Forms-MessageBox NUR wenn UI-Session vorhanden ist
        # (Task Scheduler ohne UI ueberspringt das implizit ueber den Catch)
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        # MessageBox waere blockierend -> wir nutzen MessageBoxOptions.DefaultDesktopOnly
        # und nur wenn ein Window-Station existiert. Sonst bleibt es bei Console-Output.
        if ([Environment]::UserInteractive) {
            [System.Windows.Forms.MessageBox]::Show(
                $toastMsg,
                "Personal Jarvis EoD-Push",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Information,
                [System.Windows.Forms.MessageBoxDefaultButton]::Button1,
                [System.Windows.Forms.MessageBoxOptions]::DefaultDesktopOnly
            ) | Out-Null
        } else {
            Write-Host "TOAST: $toastMsg"
        }
    }
} catch {
    Write-Host "TOAST: $toastMsg"
}

# Exit-Code: 0 wenn alles ok, 1 wenn mind. 1 Push fehlgeschlagen.
if ($failedCount -gt 0) { exit 1 } else { exit 0 }
