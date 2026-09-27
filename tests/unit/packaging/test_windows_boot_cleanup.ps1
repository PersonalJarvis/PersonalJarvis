#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$scriptPath = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\packaging\windows\check_installed_boot.ps1')).Path
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref] $tokens, [ref] $errors)
if ($errors.Count) { throw 'Could not parse the installed boot check' }
foreach ($name in @('Test-SameProcessCreation', 'Get-SmokeDescendants', 'Stop-SmokeProcessTree')) {
    $definition = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if (-not $definition) { throw "Missing $name" }
    . ([scriptblock]::Create($definition.Extent.Text))
}

$start = [datetime] '2026-09-27T10:00:00'
$rows = @(
    [pscustomobject] @{ ProcessId = 200; ParentProcessId = 100; CreationDate = $start.AddMinutes(-1) },
    [pscustomobject] @{ ProcessId = 201; ParentProcessId = 100; CreationDate = $start.AddMinutes(2) },
    [pscustomobject] @{ ProcessId = 202; ParentProcessId = 201; CreationDate = $start.AddMinutes(1) },
    [pscustomobject] @{ ProcessId = 203; ParentProcessId = 201; CreationDate = $start.AddMinutes(3) },
    [pscustomobject] @{ ProcessId = 204; ParentProcessId = 100; CreationDate = $start.AddMinutes(11) }
)
$found = @(Get-SmokeDescendants -Rows $rows -RootId 100 -RootStartTime $start -RootExitTime $start.AddMinutes(10))
$ids = @($found | ForEach-Object { $_.ProcessId })
if ($ids.Count -ne 2 -or $ids[0] -ne 201 -or $ids[1] -ne 203) {
    throw "Recycled parent PID admitted the wrong children: $($ids -join ',')"
}

function New-FakeProcess([int] $Id, [datetime] $StartTime) {
    $fake = [pscustomobject] @{ Id = $Id; StartTime = $StartTime; Handle = 1; HasExited = $false; Killed = $false; Disposed = $false; WaitResult = $true }
    $fake | Add-Member ScriptMethod Kill { $this.Killed = $true; $this.HasExited = $true }
    $fake | Add-Member ScriptMethod WaitForExit { param($Milliseconds) return $this.WaitResult }
    $fake | Add-Member ScriptMethod Dispose { $this.Disposed = $true }
    return $fake
}

$root = New-FakeProcess 100 $start
try {
    Stop-SmokeProcessTree -Root $root -RootStartTime $start -EnumerateProcesses { throw 'synthetic CIM failure' } -OpenProcess { throw 'must not open' }
    throw 'Enumeration failure did not propagate'
} catch {
    if ($_.Exception.Message -ne 'synthetic CIM failure') { throw }
}
if (-not $root.Killed -or -not $root.Disposed) { throw 'CIM failure leaked the root process handle' }

$root = New-FakeProcess 100 $start
$reused = New-FakeProcess 201 $start.AddMinutes(2).AddSeconds(1)
$snapshot = @([pscustomobject] @{ ProcessId = 201; ParentProcessId = 100; CreationDate = $start.AddMinutes(2) })
Stop-SmokeProcessTree -Root $root -RootStartTime $start -EnumerateProcesses { return $snapshot } -OpenProcess { param($ProcessId) return $reused }
if ($reused.Killed -or -not $reused.Disposed -or -not $root.Killed -or -not $root.Disposed) {
    throw 'Reused child PID was killed or a process handle leaked'
}

$root = New-FakeProcess 100 $start
$child = New-FakeProcess 201 $start.AddMinutes(2)
$child.WaitResult = $false
try {
    Stop-SmokeProcessTree -Root $root -RootStartTime $start -EnumerateProcesses { return $snapshot } -OpenProcess { param($ProcessId) return $child }
    throw 'Child termination timeout did not propagate'
} catch {
    if ($_.Exception.Message -ne 'Installed backend child did not terminate') { throw }
}
if (-not $child.Disposed -or -not $root.Killed -or -not $root.Disposed) { throw 'Timeout leaked process handles' }

$root = New-FakeProcess 100 $start
$child = New-FakeProcess 201 $start.AddMinutes(2)
$child | Add-Member ScriptProperty Handle { throw 'synthetic handle failure' } -Force
try {
    Stop-SmokeProcessTree -Root $root -RootStartTime $start -EnumerateProcesses { return $snapshot } -OpenProcess { param($ProcessId) return $child }
    throw 'Handle failure did not propagate'
} catch {
    if ($_.Exception.Message -ne 'Could not retain installed backend child handle') { throw }
}
if (-not $child.Disposed -or -not $root.Killed -or -not $root.Disposed) { throw 'Handle acquisition failure leaked processes' }
Write-Host 'Windows boot cleanup synthetic regressions passed'
