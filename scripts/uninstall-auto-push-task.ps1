<#
.SYNOPSIS
    Entfernt den Personal-Jarvis-EoD-Push Task aus dem Windows Task Scheduler.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$taskName = "Personal-Jarvis-EoD-Push"

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Task '$taskName' ist nicht registriert. Nichts zu tun."
    exit 0
}

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Host "Task '$taskName' wurde entfernt."
exit 0
