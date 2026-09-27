#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)] [ValidateSet('true', 'false')] [string] $Ok,
    [Parameter(Mandatory = $true)] [ValidateSet('true', 'false')] [string] $RolledBack
)

$ErrorActionPreference = 'Stop'
$directory = if ($env:JARVIS_DATA_DIR -and $env:JARVIS_DATA_DIR.Trim()) {
    $env:JARVIS_DATA_DIR.Trim()
} else {
    Join-Path $env:LOCALAPPDATA 'Jarvis'
}
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$destination = Join-Path $directory '.jarvis-update-result.json'
$temporary = Join-Path $directory ('.jarvis-update-result-' + [guid]::NewGuid().ToString('N') + '.tmp')
$result = @{
    ok = $Ok -eq 'true'
    rolled_back = $RolledBack -eq 'true'
    completed_at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
}
try {
    [System.IO.File]::WriteAllText(
        $temporary, ($result | ConvertTo-Json -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )
    if ([System.IO.File]::Exists($destination)) {
        [System.IO.File]::Replace($temporary, $destination, $null)
    } else {
        [System.IO.File]::Move($temporary, $destination)
    }
} finally {
    if ([System.IO.File]::Exists($temporary)) {
        [System.IO.File]::Delete($temporary)
    }
}
