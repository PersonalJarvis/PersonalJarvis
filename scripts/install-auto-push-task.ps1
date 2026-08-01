<#
.SYNOPSIS
    Registriert den Personal-Jarvis-EoD-Push Task im Windows Task Scheduler.

.DESCRIPTION
    Legt einen taeglichen Task an, der scripts/auto-push-eod.ps1 unter dem aktuellen
    User ausfuehrt. Default-Trigger: 22:00 Uhr. Bei bereits existierendem Task wird
    er aktualisiert (kein Fehler). User-Auth fuer git wird beibehalten, weil der Task
    NICHT als SYSTEM laeuft.

.PARAMETER Time
    Trigger-Zeit im Format HH:mm. Default: 22:00.

.PARAMETER RepoRoot
    Pfad zum Repo-Root. Default: <USER_HOME>\Desktop\Personal Jarvis

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-auto-push-task.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-auto-push-task.ps1 -Time "23:30"
#>

[CmdletBinding()]
param(
    [string]$Time     = "22:00",
    [string]$RepoRoot = "<USER_HOME>\Desktop\Personal Jarvis"
)

$ErrorActionPreference = "Stop"

$taskName    = "Personal-Jarvis-EoD-Push"
$scriptPath  = Join-Path $RepoRoot "scripts\auto-push-eod.ps1"

if (-not (Test-Path $scriptPath)) {
    Write-Host "FEHLER: $scriptPath nicht gefunden."
    exit 2
}

# Trigger-Zeit validieren
try {
    $triggerTime = [datetime]::ParseExact($Time, "HH:mm", $null)
} catch {
    Write-Host "FEHLER: --Time muss Format HH:mm haben (z.B. 22:00). Bekommen: '$Time'"
    exit 3
}

# Action: powershell.exe -NoProfile -ExecutionPolicy Bypass -File <scriptPath>
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" `
    -WorkingDirectory $RepoRoot

# Trigger: taeglich zu --Time
$trigger = New-ScheduledTaskTrigger -Daily -At $triggerTime

# Settings: nur wenn User logged-on, NICHT aufwecken, abbrechbar nach 30 min
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

# Wake-the-computer explizit aus
$settings.WakeToRun = $false

# Principal: aktueller User, interaktiv (Run only when user is logged on)
$currentUser = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited

$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Spiegelt taeglich alle lokalen Branches + Backup-Tags auf GitHub. Quelle: $scriptPath"

# Existing? -> Re-Register
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Task '$taskName' existiert bereits - wird aktualisiert."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName -InputObject $task | Out-Null

Write-Host ""
Write-Host "==================================================================="
Write-Host "Task '$taskName' wurde registriert."
Write-Host "  Trigger:     taeglich um $Time"
Write-Host "  Skript:      $scriptPath"
Write-Host "  Run as:      $currentUser (interaktiv, nur wenn eingeloggt)"
Write-Host "  Wake PC:     NEIN"
Write-Host ""
Write-Host "Manuelle Pruefung:"
Write-Host "  Get-ScheduledTask -TaskName '$taskName'"
Write-Host ""
Write-Host "Manuell triggern (sofort ausfuehren):"
Write-Host "  Start-ScheduledTask -TaskName '$taskName'"
Write-Host ""
Write-Host "Letzten Lauf-Status anzeigen:"
Write-Host "  Get-ScheduledTaskInfo -TaskName '$taskName'"
Write-Host ""
Write-Host "Deinstallieren:"
Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\uninstall-auto-push-task.ps1"
Write-Host "==================================================================="
exit 0
