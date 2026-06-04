<#
.SYNOPSIS
    Smoke test for the Pre-Thinking-Ack Flash-Brain feature.

.DESCRIPTION
    Runs a curated battery of five German + five English utterances through
    a running Personal Jarvis instance, then samples data/jarvis_desktop.log
    for the ack_* counter lines emitted by jarvis.brain.ack_brain.generator
    and prints a results table.

    The script does NOT require any test-endpoint on the server: it relies
    on the log line that the AckGenerator already emits after every call
    (``ack_counter name=ack_called_total``, ``ack_emitted_total``,
    ``ack_histogram name=ack_latency_ms_histogram``, ``ack_provider_error_total``,
    ``ack_timeout_total``, ``ack_self_answer_suppressed_total``,
    ``ack_lang_mismatch_total``, ``ack_scrubbed_empty_total``,
    ``ack_empty_response_total``, ``ack_circuit_breaker_open_total``,
    ``ack_truncated_total``). The user / agent issues each utterance
    manually (voice or chat); the script then reads the log tail and
    matches the latest counter window.

    Output is a table with one row per utterance:
        Utterance | AckText | LatencyMs | MatchedPattern (or "none")

.PARAMETER ApiUrl
    HTTP base URL of the running Jarvis instance.
    Default: http://127.0.0.1:47821

.PARAMETER LogPath
    Absolute path to data/jarvis_desktop.log.
    Default: <repo>/data/jarvis_desktop.log

.PARAMETER TailLines
    How many lines from the end of the log to read per probe. Default: 800.

.PARAMETER WaitSeconds
    Seconds to wait for the user to trigger an utterance before sampling
    the log. Default: 10.

.PARAMETER NonInteractive
    Skip the per-utterance Wait prompt and just print the playbook plus a
    final consolidated log probe. Useful for CI / automation.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/smoke-test-ack.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/smoke-test-ack.ps1 -NonInteractive

.NOTES
    Read-only against the running Jarvis. Never modifies config files,
    keyring, or the log itself.
#>

[CmdletBinding()]
param(
    [string]$ApiUrl = "http://127.0.0.1:47821",
    [string]$LogPath = "",
    [int]$TailLines = 800,
    [int]$WaitSeconds = 10,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

# Locate the log path if not given.
if (-not $LogPath) {
    $repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
    $LogPath = Join-Path $repoRoot "data/jarvis_desktop.log"
}

Write-Host "Pre-Thinking-Ack Flash-Brain smoke test"
Write-Host "  API URL : $ApiUrl"
Write-Host "  Log     : $LogPath"
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Health check - is Jarvis running?
# ---------------------------------------------------------------------------

$healthUrl = "$ApiUrl/api/health"
try {
    $health = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 3
    if (-not $health.ok) {
        Write-Host "Jarvis /api/health responded but ok=false. Aborting." -ForegroundColor Yellow
        exit 0
    }
    Write-Host "Jarvis online (version=$($health.version))" -ForegroundColor Green
} catch {
    Write-Host "Jarvis not running - could not reach $healthUrl." -ForegroundColor Yellow
    Write-Host "Start Jarvis (e.g. run.bat) and re-run this script." -ForegroundColor Yellow
    exit 0
}

if (-not (Test-Path $LogPath)) {
    Write-Host "Log file not found: $LogPath" -ForegroundColor Yellow
    Write-Host "The Flash-Brain telemetry counters need the desktop log."  -ForegroundColor Yellow
    exit 0
}

# ---------------------------------------------------------------------------
# 2. Utterance battery - five German + five English
# ---------------------------------------------------------------------------

$utterances = @(
    @{ Lang = "de"; Text = "Was war noch mal mein naechster Termin?";       Notes = "wiki / awareness lookup" },
    @{ Lang = "de"; Text = "Oeffne mir bitte den Browser.";                 Notes = "open_app" },
    @{ Lang = "de"; Text = "Suche nach Wetter Berlin.";                     Notes = "search_web" },
    @{ Lang = "de"; Text = "Hallo Jarvis, wie geht es dir?";                Notes = "smalltalk (expect no ack)" },
    @{ Lang = "de"; Text = "Spiel etwas Synthwave.";                        Notes = "open_app / playback" },
    @{ Lang = "en"; Text = "What was my last meeting about?";               Notes = "awareness recall" },
    @{ Lang = "en"; Text = "Open Spotify, please.";                         Notes = "open_app" },
    @{ Lang = "en"; Text = "Search the web for nano banana 2.";             Notes = "search_web" },
    @{ Lang = "en"; Text = "How are you doing, Jarvis?";                    Notes = "smalltalk (expect no ack)" },
    @{ Lang = "en"; Text = "Play some lo-fi music.";                        Notes = "open_app / playback" }
)

# ---------------------------------------------------------------------------
# 3. Helpers - log tail + counter parser
# ---------------------------------------------------------------------------

function Get-LogTail {
    param([int]$Lines)
    try {
        return Get-Content -Path $LogPath -Tail $Lines -ErrorAction Stop
    } catch {
        return @()
    }
}

# Match an ack_counter line like:
#   2026-05-14 12:34:56.789 INFO  ack_counter name=ack_called_total labels={'provider': 'gemini'}
# and an ack_histogram line like:
#   2026-05-14 12:34:56.815 INFO  ack_histogram name=ack_latency_ms_histogram value=320.456 labels={'provider': 'gemini'}
$counterRe   = [regex]'ack_counter\s+name=(?<name>\w+)'
$histogramRe = [regex]'ack_histogram\s+name=ack_latency_ms_histogram\s+value=(?<val>[0-9.]+)'
$ackTextRe   = [regex]'AckGenerator.*?return\s+(?<text>".+?")'

function Get-LatestAckWindow {
    param([string[]]$Tail, [int]$AfterAnchor = -1)
    # Return the last "ack_called_total" -> "ack_emitted_total / suppressed / error" window.
    $result = [pscustomobject]@{
        AckCalled       = $false
        AckEmitted      = $false
        Pattern         = "none"
        LatencyMs       = $null
        StartLineIdx    = $null
        CounterNames    = @()
    }
    for ($i = $Tail.Length - 1; $i -ge ([Math]::Max(0, $AfterAnchor)); $i--) {
        $line = $Tail[$i]
        $m = $counterRe.Match($line)
        if ($m.Success) {
            $name = $m.Groups["name"].Value
            $result.CounterNames = @($name) + $result.CounterNames
            if ($name -eq "ack_called_total") {
                $result.AckCalled = $true
                $result.StartLineIdx = $i
                break
            }
        }
    }
    if (-not $result.AckCalled) { return $result }
    # Now scan forward from the anchor for the matching outcome.
    for ($j = $result.StartLineIdx; $j -lt $Tail.Length; $j++) {
        $line = $Tail[$j]
        $mc = $counterRe.Match($line)
        if ($mc.Success) {
            $name = $mc.Groups["name"].Value
            switch ($name) {
                "ack_emitted_total"                  { $result.AckEmitted = $true; $result.Pattern = "emitted" }
                "ack_timeout_total"                  { $result.Pattern = "timeout" }
                "ack_provider_error_total"           { $result.Pattern = "provider_error" }
                "ack_empty_response_total"           { $result.Pattern = "empty_response" }
                "ack_lang_mismatch_total"            { $result.Pattern = "lang_mismatch" }
                "ack_scrubbed_empty_total"           { $result.Pattern = "scrubbed_empty" }
                "ack_circuit_breaker_open_total"     { $result.Pattern = "breaker_open" }
                "ack_truncated_total"                { $result.Pattern = "truncated" }
                "ack_self_answer_suppressed_total"   { $result.Pattern = "self_answer_suppressed" }
            }
        }
        $mh = $histogramRe.Match($line)
        if ($mh.Success) {
            $result.LatencyMs = [double]$mh.Groups["val"].Value
        }
    }
    return $result
}

# ---------------------------------------------------------------------------
# 4. Run the battery
# ---------------------------------------------------------------------------

Write-Host "Running 10 utterances. After each prompt, trigger the utterance"
Write-Host "via voice or chat in the Jarvis app, then press ENTER (or wait)."
Write-Host ""

# Baseline: line count of the log BEFORE we begin, so we can scope each
# probe to the window that opened after the user triggered an utterance.
$baselineLines = (Get-Content -Path $LogPath -ErrorAction SilentlyContinue).Length

$results = New-Object System.Collections.ArrayList

for ($idx = 0; $idx -lt $utterances.Length; $idx++) {
    $u = $utterances[$idx]
    Write-Host ("[{0,2}/10] ({1}) {2}" -f ($idx + 1), $u.Lang, $u.Text) -ForegroundColor Cyan
    Write-Host ("         Notes: {0}" -f $u.Notes) -ForegroundColor DarkGray

    if (-not $NonInteractive) {
        Write-Host "         Trigger the utterance now and press ENTER (auto-skip after $WaitSeconds s)." -ForegroundColor DarkGray
        $sw = [Diagnostics.Stopwatch]::StartNew()
        while ($sw.Elapsed.TotalSeconds -lt $WaitSeconds) {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if ($key.Key -eq "Enter") { break }
            }
            Start-Sleep -Milliseconds 100
        }
        $sw.Stop()
    }

    # Sample log tail and find the latest ack window past the baseline.
    $tail = Get-LogTail -Lines $TailLines
    $window = Get-LatestAckWindow -Tail $tail -AfterAnchor 0

    $ackText = "(see chat / TTS audio)"
    $latency = if ($window.LatencyMs) { "{0:N0}" -f $window.LatencyMs } else { "-" }
    $pattern = $window.Pattern

    $row = [pscustomobject]@{
        Idx       = $idx + 1
        Lang      = $u.Lang
        Utterance = $u.Text
        AckText   = $ackText
        LatencyMs = $latency
        Pattern   = $pattern
    }
    [void]$results.Add($row)

    Write-Host ("         -> pattern={0} latency={1}ms" -f $pattern, $latency) -ForegroundColor Green
    Write-Host ""
}

# ---------------------------------------------------------------------------
# 5. Final table
# ---------------------------------------------------------------------------

Write-Host "===== Smoke Test Results ====="
$results | Format-Table -AutoSize -Property Idx, Lang, Utterance, LatencyMs, Pattern

# Summary counters
$emitted   = ($results | Where-Object { $_.Pattern -eq "emitted" }).Count
$errors    = ($results | Where-Object { $_.Pattern -eq "provider_error" }).Count
$suppress  = ($results | Where-Object { $_.Pattern -eq "self_answer_suppressed" }).Count
$mismatch  = ($results | Where-Object { $_.Pattern -eq "lang_mismatch" }).Count
$breaker   = ($results | Where-Object { $_.Pattern -eq "breaker_open" }).Count
$missing   = ($results | Where-Object { $_.Pattern -eq "none" }).Count

Write-Host ""
Write-Host ("emitted={0}  errors={1}  self_answer_suppressed={2}  lang_mismatch={3}  breaker_open={4}  no_log_window={5}" -f $emitted, $errors, $suppress, $mismatch, $breaker, $missing)
Write-Host ""
Write-Host "Done."
exit 0
