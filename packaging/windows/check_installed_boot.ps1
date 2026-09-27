#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)] [string] $AppDir,
    [Parameter(Mandatory = $true)] [string] $ExpectedVersion
)

$ErrorActionPreference = 'Stop'
$server = Join-Path $AppDir 'jarvis.exe'
if (-not (Test-Path -LiteralPath $server)) { throw "Installed CLI is missing: $server" }
function Test-SameProcessCreation {
    param([datetime] $Left, [datetime] $Right)
    $leftTicks = $Left.ToUniversalTime().Ticks
    $rightTicks = $Right.ToUniversalTime().Ticks
    # CIM timestamps retain microseconds; process handles retain 100ns ticks.
    return ($leftTicks - ($leftTicks % 10)) -eq ($rightTicks - ($rightTicks % 10))
}

function Get-SmokeDescendants {
    param([object[]] $Rows, [int] $RootId, [datetime] $RootStartTime, [Nullable[datetime]] $RootExitTime)

    $owned = @{}
    $owned[[string] $RootId] = $RootStartTime
    $descendants = [System.Collections.Generic.List[object]]::new()
    do {
        $added = $false
        foreach ($row in $Rows) {
            $childId = [string] $row.ProcessId
            $parentId = [string] $row.ParentProcessId
            if ($owned.ContainsKey($childId) -or -not $owned.ContainsKey($parentId)) { continue }
            # A recycled parent PID cannot adopt a child created before that
            # parent's recorded creation (or after this root already exited).
            if ($row.CreationDate -lt $owned[$parentId]) { continue }
            if ($parentId -eq [string] $RootId -and $null -ne $RootExitTime -and
                $row.CreationDate -gt $RootExitTime) { continue }
            $owned[$childId] = $row.CreationDate
            $descendants.Add($row)
            $added = $true
        }
    } while ($added)
    return $descendants.ToArray()
}

function Stop-SmokeProcessTree {
    param(
        $Root,
        [Nullable[datetime]] $RootStartTime,
        [scriptblock] $EnumerateProcesses = { Get-CimInstance -ClassName Win32_Process -OperationTimeoutSec 5 -ErrorAction Stop },
        [scriptblock] $OpenProcess = { param($ProcessId) [System.Diagnostics.Process]::GetProcessById($ProcessId) }
    )

    $handles = [System.Collections.Generic.List[object]]::new()
    $cleanupError = $null
    try {
        if ($null -eq $RootStartTime) { throw 'Could not identify installed backend creation time' }
        $rows = @(& $EnumerateProcesses)
        $rootRow = $rows | Where-Object { $_.ProcessId -eq $Root.Id } | Select-Object -First 1
        if ($rootRow -and -not (Test-SameProcessCreation $rootRow.CreationDate $RootStartTime)) {
            if (-not $Root.HasExited) { throw 'Installed backend PID no longer matches its process handle' }
            $rows = @($rows | Where-Object { $_.ProcessId -ne $Root.Id })
        }
        $exitTime = if ($Root.HasExited) { [Nullable[datetime]] $Root.ExitTime } else { [Nullable[datetime]] $null }
        foreach ($row in (Get-SmokeDescendants -Rows $rows -RootId $Root.Id -RootStartTime $RootStartTime -RootExitTime $exitTime)) {
            $handle = $null
            $retained = $false
            try {
                $handle = & $OpenProcess ([int] $row.ProcessId)
                # Force a retained native handle before checking identity.
                $nativeHandle = $handle.Handle
                if ($null -eq $nativeHandle -or [long] $nativeHandle -eq 0) {
                    throw 'Could not retain installed backend child handle'
                }
                if (Test-SameProcessCreation $handle.StartTime $row.CreationDate) {
                    $handles.Add($handle)
                    $retained = $true
                }
            } catch [System.ArgumentException] {
                continue  # GetProcessById reports that the child already exited.
            } finally {
                if ($null -ne $handle -and -not $retained) { $handle.Dispose() }
            }
        }
    } finally {
        # Handles identify the original processes even if Windows reuses a PID
        # after the CIM snapshot. Stop children first, then the exact root.
        for ($index = $handles.Count - 1; $index -ge 0; $index--) {
            $handle = $handles[$index]
            try {
                if (-not $handle.HasExited) {
                    $handle.Kill()
                    if (-not $handle.WaitForExit(5000)) { throw 'Installed backend child did not terminate' }
                }
            } catch { $cleanupError = $_ }
            finally { $handle.Dispose() }
        }
        try {
            if (-not $Root.HasExited) {
                $Root.Kill()
                if (-not $Root.WaitForExit(5000)) { throw 'Installed backend did not terminate' }
            }
        } catch { $cleanupError = $_ }
        finally { $Root.Dispose() }
        if ($cleanupError) { throw $cleanupError }
    }
}
$process = Start-Process -FilePath $server -ArgumentList 'serve' -PassThru -WindowStyle Hidden
$rootStartTime = $null
try {
    $nativeRootHandle = $process.Handle
    if ($null -eq $nativeRootHandle -or [long] $nativeRootHandle -eq 0) {
        throw 'Could not retain installed backend process handle'
    }
    $rootStartTime = $process.StartTime
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
    Stop-SmokeProcessTree -Root $process -RootStartTime $rootStartTime
}
