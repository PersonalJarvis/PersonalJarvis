#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)] [string] $AppDir,
    [Parameter(Mandatory = $true)] [string] $ExpectedVersion
)

$ErrorActionPreference = 'Stop'
$server = Join-Path $AppDir 'jarvis.exe'
if (-not (Test-Path -LiteralPath $server)) { throw "Installed CLI is missing: $server" }
$process = Start-Process -FilePath $server -ArgumentList 'serve' -PassThru -WindowStyle Hidden
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(75)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($process.HasExited) { throw "Installed backend exited with $($process.ExitCode) before health passed" }
        # The effective port may come from the user's existing config. Discover
        # only this server process's listeners so another Jarvis instance cannot
        # make a broken replacement appear healthy.
        $listeners = Get-NetTCPConnection -State Listen -OwningProcess $process.Id -ErrorAction SilentlyContinue
        foreach ($listener in $listeners) {
            try {
                $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($listener.LocalPort)/api/health" -TimeoutSec 2
                if ($health.ok -eq $true -and $health.version -eq $ExpectedVersion) { exit 0 }
            } catch {
                # This listener is still warming or serves another endpoint.
            }
        }
        Start-Sleep -Seconds 1
    }
    throw "Installed backend did not serve version $ExpectedVersion within 75 seconds"
} finally {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
}
