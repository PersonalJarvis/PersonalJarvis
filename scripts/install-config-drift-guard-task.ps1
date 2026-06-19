<#
.SYNOPSIS
    Registriert den Personal-Jarvis-Config-Drift-Guard Task im Windows Task Scheduler.

.DESCRIPTION
    Legt einen Task an, der scripts/jarvis-config-drift-guard.ps1 alle 5 Minuten
    unter dem aktuellen User ausfuehrt. Default-Trigger: AtLogOn + Once mit
    Repetition alle 5 Min, unbegrenzte Dauer.

    User-Auth wird beibehalten, weil der Task NICHT als SYSTEM laeuft — wir
    brauchen die User-Scope-ENV-Variablen.

.PARAMETER IntervalMinutes
    Wiederhol-Intervall in Minuten. Default: 5.

.PARAMETER RepoRoot
    Pfad zum Repo-Root. Default: C:\Users\Administrator\Desktop\Personal Jarvis

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-config-drift-guard-task.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-config-drift-guard-task.ps1 -IntervalMinutes 10
#>

[CmdletBinding()]
param(
    [int]$IntervalMinutes = 5,
    [string]$RepoRoot     = "C:\Users\Administrator\Desktop\Personal Jarvis"
)

$ErrorActionPreference = "Stop"

$taskName    = "Personal-Jarvis-Config-Drift-Guard"
$scriptPath  = Join-Path $RepoRoot "scripts\jarvis-config-drift-guard.ps1"

if (-not (Test-Path $scriptPath)) {
    Write-Host "FEHLER: $scriptPath nicht gefunden."
    exit 2
}

if ($IntervalMinutes -lt 1 -or $IntervalMinutes -gt 60) {
    Write-Host "FEHLER: --IntervalMinutes muss zwischen 1 und 60 liegen. Bekommen: $IntervalMinutes"
    exit 3
}

# Action: powershell -NoProfile -ExecutionPolicy Bypass -File <scriptPath>
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`"" `
    -WorkingDirectory $RepoRoot

# Trigger: Once-At-Now mit RepetitionInterval — laeuft alle X Min bis Sessions-Ende
$trigger = New-ScheduledTaskTrigger `
    -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

# Trigger #2: Beim Login starten, damit nach Reboot direkt aktiv
$triggerLogin = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
    -MultipleInstances IgnoreNew
$settings.WakeToRun = $false
# Hidden: kein Konsole-Fenster pro Lauf
$settings.Hidden    = $true

$currentUser = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited

$task = New-ScheduledTask `
    -Action $action `
    -Trigger @($trigger, $triggerLogin) `
    -Settings $settings `
    -Principal $principal `
    -Description "Prueft alle $IntervalMinutes Min jarvis.toml + ENV-Overrides gegen scripts/config-soll.json. Selbstheilend bei Drift. Quelle: $scriptPath"

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Task '$taskName' existiert bereits - wird aktualisiert."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName -InputObject $task | Out-Null

Write-Host ""
Write-Host "==================================================================="
Write-Host "Task '$taskName' wurde registriert."
Write-Host "  Trigger:     Once + RepetitionInterval = alle $IntervalMinutes Min"
Write-Host "               + AtLogOn (Reboot-Recovery)"
Write-Host "  Skript:      $scriptPath"
Write-Host "  Soll-Datei:  $(Join-Path $RepoRoot 'scripts\config-soll.json')"
Write-Host "  Run as:      $currentUser (interaktiv, nur wenn eingeloggt)"
Write-Host "  Log:         $(Join-Path $RepoRoot 'logs\config-drift-guard.log')"
Write-Host "  Wake PC:     NEIN"
Write-Host ""
Write-Host "Manuelle Pruefung:"
Write-Host "  Get-ScheduledTask -TaskName '$taskName'"
Write-Host "  Get-ScheduledTaskInfo -TaskName '$taskName'"
Write-Host ""
Write-Host "Manuell triggern (sofort ausfuehren):"
Write-Host "  Start-ScheduledTask -TaskName '$taskName'"
Write-Host ""
Write-Host "Deinstallieren:"
Write-Host "  Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
Write-Host ""
Write-Host "Soll-Werte aendern (z.B. zurueck zu Gemini):"
Write-Host "  Editiere $(Join-Path $RepoRoot 'scripts\config-soll.json')"
Write-Host "==================================================================="
exit 0
