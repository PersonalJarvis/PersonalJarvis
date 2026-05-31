<#
.SYNOPSIS
    Daemon-Loop fuer den Jarvis-Config-Drift-Guard. Laeuft permanent im
    Hintergrund und triggert alle 5 Min das jarvis-config-drift-guard.ps1.

.DESCRIPTION
    Hintergrund: Windows-Task-Scheduler und PowerShell-ScheduledJob brauchen
    beide einen UAC-Elevation-Prompt zur Registrierung (HRESULT 0x80070005).
    Ein Userland-Daemon umgeht das komplett -- laeuft als normaler User-
    Prozess, ueberlebt Reboots durch Startup-Folder-Verknuepfung.

    Logging: nutzt dasselbe logs/config-drift-guard.log wie das eigentliche
    Guard-Skript. Daemon-Lifecycle-Events tragen Praefix "DAEMON".

.PARAMETER RepoRoot
    Pfad zum Repo-Root. Default: <your-home>\Desktop\Personal Jarvis

.PARAMETER IntervalSeconds
    Wartezeit zwischen Guard-Laeufen. Default: 300 (5 Min).

.EXAMPLE
    # Daemon im Vordergrund starten (Debug):
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/drift-guard-daemon.ps1

    # Hidden + persistent (was die Startup-Verknuepfung tut):
    Start-Process powershell -WindowStyle Hidden `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"scripts/drift-guard-daemon.ps1`""
#>

[CmdletBinding()]
param(
    [string]$RepoRoot        = "<your-home>\Desktop\Personal Jarvis",
    [int]   $IntervalSeconds = 300
)

$ErrorActionPreference = "Continue"  # Daemon darf nicht crashen wenn ein Lauf failt

$guardScript = Join-Path $RepoRoot "scripts\jarvis-config-drift-guard.ps1"
$logFile     = Join-Path $RepoRoot "logs\config-drift-guard.log"
$lockFile    = Join-Path $RepoRoot "logs\drift-guard-daemon.lock"

if (-not (Test-Path $guardScript)) { exit 2 }

# Singleton-Lock: wenn Lock existiert und PID darin lebt, sauber beenden.
if (Test-Path $lockFile) {
    try {
        $oldPid = [int](Get-Content $lockFile -Raw -ErrorAction Stop).Trim()
        $oldProc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($oldProc) {
            "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | DAEMON | already running (PID $oldPid) -- this instance exits" |
                Add-Content -Path $logFile -Encoding utf8
            exit 0
        }
    } catch {}
}
# Lock setzen
$PID | Out-File -FilePath $lockFile -Encoding utf8 -Force

"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | DAEMON | start (PID $PID, interval=${IntervalSeconds}s)" |
    Add-Content -Path $logFile -Encoding utf8

try {
    while ($true) {
        try {
            & powershell -NoProfile -ExecutionPolicy Bypass -File $guardScript 2>&1 | Out-Null
        } catch {
            "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | DAEMON | guard run failed: $_" |
                Add-Content -Path $logFile -Encoding utf8
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
} finally {
    Remove-Item $lockFile -ErrorAction SilentlyContinue
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | DAEMON | stop (PID $PID)" |
        Add-Content -Path $logFile -Encoding utf8
}
