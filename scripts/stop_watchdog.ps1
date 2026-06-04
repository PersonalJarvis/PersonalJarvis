Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" | Where-Object { $_.CommandLine -match 'jarvis.speech.watchdog' } | ForEach-Object {
    $procId = $_.ProcessId
    Write-Host "Stoppe Watchdog PID $procId"
    Stop-Process -Id $procId -Force
}
$still = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" | Where-Object { $_.CommandLine -match 'jarvis.speech.watchdog' }
if ($still) {
    Write-Host "ACHTUNG: $($still.Count) Watchdogs laufen noch."
    exit 1
} else {
    Write-Host "Keine laufenden Watchdogs."
    exit 0
}
