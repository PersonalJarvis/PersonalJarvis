<#
.SYNOPSIS
    OpenClaw-Bridge Spike — beantwortet SP-1..SP-8 aus docs/openclaw-bridge.md §6.

.DESCRIPTION
    Resilient: jeder Test laeuft unabhaengig, Fehler in einem Test stoppen das
    Skript nicht. Am Ende landen alle Befunde in einem Markdown-Report.

    Vorbereitung auf der Windows-Box:
      1. Node 24 LTS installiert, `node --version` antwortet.
      2. OpenClaw installiert, `openclaw --version` antwortet.
         (a) `npm i -g openclaw` ODER
         (b) git clone + `pnpm install` + `pnpm link --global` aus dem Repo.
      3. Provider-API-Key aus dem Personal-Jarvis Credential Manager als ENV
         gesetzt. Skript leitet aus -Model den passenden Provider-Slug und
         ENV-Var-Namen ab (siehe Resolve-ProviderEnv unten).

    Aufruf:
      powershell -NoProfile -ExecutionPolicy Bypass -File scripts/spikes/openclaw_probe.ps1

.PARAMETER Model
    Modell fuer den Test-Run. Default: "google/gemini-3.1-pro-preview"
    (matcht jarvis.toml [brain.providers.gemini].deep_model — das Frontier-
    Premium-Modell). Andere getestete Pfade: "google/gemini-3-flash-preview"
    (cheap-Path), "anthropic/claude-opus-4-7", "openai/gpt-5.5".

    Provider->ENV-Mapping (aus jarvis.toml + OpenClaw-Slugs):
      google/*        -> GEMINI_API_KEY (oder GOOGLE_API_KEY als Fallback)
      anthropic/*     -> ANTHROPIC_API_KEY
      openai/*        -> OPENAI_API_KEY
      openrouter/*    -> OPENROUTER_API_KEY
      xai/*           -> XAI_API_KEY (Personal-Jarvis: 'grok'-Slug)
      groq/*          -> GROQ_API_KEY

.PARAMETER LogDir
    Output-Verzeichnis fuer Logs. Default: logs/spike-openclaw/<timestamp>.

.PARAMETER SkipLiveTests
    Wenn gesetzt, werden Tests die echte API-Calls machen uebersprungen
    (z.B. fuer Offline-Diagnose oder wenn kein API-Key da ist).
#>

[CmdletBinding()]
param(
    [string]$ApiKey = $null,  # Auto-resolved from Resolve-ProviderEnv if empty
    [string]$Model = "google/gemini-3.1-pro-preview",
    [string]$LogDir = "logs/spike-openclaw",
    [switch]$SkipLiveTests
)

# Provider-Slug aus dem -Model ableiten und passenden ENV-Var-Namen finden.
# Returns: @{ Provider="google"; EnvName="GEMINI_API_KEY"; EnvValue="..." }
function Resolve-ProviderEnv {
    param([string]$ModelString)
    $providerSlug = ($ModelString -split "/")[0]
    $envCandidates = switch -regex ($providerSlug) {
        "^google$|^google-vertex$" { @("GEMINI_API_KEY", "GOOGLE_API_KEY"); break }
        "^anthropic$"              { @("ANTHROPIC_API_KEY"); break }
        "^openai$"                 { @("OPENAI_API_KEY"); break }
        "^openrouter$"             { @("OPENROUTER_API_KEY"); break }
        "^xai$"                    { @("XAI_API_KEY", "GROK_API_KEY"); break }
        "^groq$"                   { @("GROQ_API_KEY"); break }
        "^mistral$"                { @("MISTRAL_API_KEY"); break }
        default                    { @("${providerSlug}_API_KEY".ToUpper()) }
    }
    foreach ($n in $envCandidates) {
        $v = [System.Environment]::GetEnvironmentVariable($n)
        if ($v) { return @{ Provider = $providerSlug; EnvName = $n; EnvValue = $v } }
    }
    return @{ Provider = $providerSlug; EnvName = $envCandidates[0]; EnvValue = $null }
}

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $LogDir $timestamp
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$reportFile = Join-Path $runDir "report.md"

Add-Content -Path $reportFile -Value "# OpenClaw Spike Report — $timestamp`n"
Add-Content -Path $reportFile -Value "Modell fuer Test-Runs: ``$Model```n"

function Write-Section {
    param([string]$Id, [string]$Title)
    Write-Host ""
    Write-Host "=== $Id : $Title ===" -ForegroundColor Cyan
    Add-Content -Path $reportFile -Value "`n## $Id : $Title`n"
}

function Write-Result {
    param([string]$Key, $Value)
    $val = if ($null -eq $Value) { "(null)" } else { "$Value" }
    Write-Host "  $Key = $val" -ForegroundColor Green
    Add-Content -Path $reportFile -Value "- **${Key}:** ``$val``"
}

function Write-Fail {
    param([string]$Key, [string]$Reason)
    Write-Host "  $Key FAILED — $Reason" -ForegroundColor Red
    Add-Content -Path $reportFile -Value "- **${Key}:** FAILED — $Reason"
}

function Write-Note {
    param([string]$Text)
    Write-Host "  $Text" -ForegroundColor Yellow
    Add-Content -Path $reportFile -Value "- _$Text_"
}

# ============================================================
# SP-1: Native Windows + Node + OpenClaw vorhanden
# ============================================================
Write-Section "SP-1" "Native Windows + Node + OpenClaw"

# WSL-Detection (Wenn WSL_DISTRO_NAME gesetzt, sind wir innerhalb von WSL)
if ($env:WSL_DISTRO_NAME) {
    Write-Result "runtime" "WSL2 ($($env:WSL_DISTRO_NAME)) — Plan-B aktiviert"
} else {
    Write-Result "runtime" "Native Windows"
}

# Node-Version
try {
    $nodeVersion = (& node --version 2>&1) -join " "
    if ($LASTEXITCODE -eq 0) {
        Write-Result "node-version" $nodeVersion
        $nodeMajor = [int]($nodeVersion -replace 'v(\d+)\..*', '$1')
        if ($nodeMajor -lt 22) {
            Write-Note "Node-Major < 22 — OpenClaw README empfiehlt Node 22.14+ oder 24."
        }
    } else {
        Write-Fail "node-version" "node nicht im PATH"
    }
} catch {
    Write-Fail "node-version" "Aufruf fehlgeschlagen: $_"
}

# pnpm-Check (falls Repo-Install verwendet)
try {
    $pnpmVersion = (& pnpm --version 2>&1) -join " "
    if ($LASTEXITCODE -eq 0) {
        Write-Result "pnpm-version" $pnpmVersion
    } else {
        Write-Note "pnpm nicht installiert — relevant nur wenn OpenClaw aus dem Repo gebaut wurde"
    }
} catch {
    Write-Note "pnpm nicht im PATH"
}

# OpenClaw-Binary-Check
$openclawCmd = Get-Command openclaw -ErrorAction SilentlyContinue
if (-not $openclawCmd) {
    Write-Fail "openclaw-cli" "Befehl nicht gefunden im PATH"
    Write-Host ""
    Write-Host "INSTALLATION:" -ForegroundColor Yellow
    Write-Host "  Option A (npm):     npm i -g openclaw"
    Write-Host "  Option B (Repo):    git clone https://github.com/openclaw/openclaw"
    Write-Host "                      cd openclaw"
    Write-Host "                      pnpm install"
    Write-Host "                      pnpm link --global"
    Write-Host ""
    Write-Host "Skript wird mit reduzierten Tests fortfahren." -ForegroundColor Yellow
} else {
    Write-Result "openclaw-path" $openclawCmd.Source
    try {
        $openclawVersion = (& openclaw --version 2>&1) -join " "
        if ($LASTEXITCODE -eq 0) {
            Write-Result "openclaw-version" $openclawVersion
        } else {
            Write-Fail "openclaw-version" "Aufruf liefert Exit $LASTEXITCODE"
        }
    } catch {
        Write-Fail "openclaw-version" "Aufruf fehlgeschlagen: $_"
    }
}

# Wenn OpenClaw fehlt, restliche Tests sinnlos
if (-not $openclawCmd) {
    Add-Content -Path $reportFile -Value "`n> Spike abgebrochen — OpenClaw nicht installiert. Bitte installieren und erneut ausfuehren.`n"
    Write-Host ""
    Write-Host "Report: $reportFile" -ForegroundColor Cyan
    exit 1
}

# ============================================================
# SP-4: --help auswerten (Modell-Flag, Workdir-Flag, MCP-Flag)
# ============================================================
Write-Section "SP-4" "CLI-Flags aus --help (vorab fuer alle weiteren Tests)"

try {
    $helpOutput = (& openclaw agent --help 2>&1) -join "`n"
    $helpFile = Join-Path $runDir "help-output.txt"
    Set-Content -Path $helpFile -Value $helpOutput -Encoding UTF8
    Write-Result "help-output-file" $helpFile

    $expectedFlags = @(
        "--model", "--workdir", "--cwd", "--working-directory",
        "--mcp", "--mcp-config", "--mcps",
        "--verbose", "--debug", "--json", "--stream",
        "--message", "--prompt", "--input",
        "--thinking", "--max-tokens", "--temperature",
        "--no-ui", "--headless"
    )

    $detected = @()
    foreach ($flag in $expectedFlags) {
        if ($helpOutput -match [regex]::Escape($flag)) {
            $detected += $flag
        }
    }
    Write-Result "flags-detected" ($detected -join ", ")

    $missing = @()
    foreach ($mustHave in @("--model", "--message")) {
        if ($helpOutput -notmatch [regex]::Escape($mustHave)) {
            $missing += $mustHave
        }
    }
    if ($missing.Count -gt 0) {
        Write-Fail "must-have-flags" "FEHLEND: $($missing -join ', ')"
    }
} catch {
    Write-Fail "help-output" "Aufruf fehlgeschlagen: $_"
}

# ============================================================
# Live-Tests (brauchen API-Key) — koennen uebersprungen werden
# ============================================================
$providerEnv = Resolve-ProviderEnv -ModelString $Model
if (-not $ApiKey -and $providerEnv.EnvValue) { $ApiKey = $providerEnv.EnvValue }

Write-Section "Provider" "API-Key-Resolution fuer $($Model)"
Write-Result "provider-slug" $providerEnv.Provider
Write-Result "env-var-name" $providerEnv.EnvName
Write-Result "env-var-set" ([bool]$providerEnv.EnvValue)
Write-Result "key-length" $(if ($ApiKey) { $ApiKey.Length } else { 0 })

if ($SkipLiveTests) {
    Write-Section "Live-Tests" "uebersprungen (-SkipLiveTests)"
    Add-Content -Path $reportFile -Value "`n_Live-Tests SP-2/3/6/7/8 wurden uebersprungen._`n"
} elseif (-not $ApiKey) {
    Write-Section "Live-Tests" "uebersprungen (kein API-Key)"
    Write-Fail "api-key" "$($providerEnv.EnvName) nicht gesetzt — Live-Tests skipped"
    Write-Note "Setze `$env:$($providerEnv.EnvName) = '...' und re-run"
} else {
    # Setze die ENV-Var die OpenClaw fuer diesen Provider erwartet.
    [System.Environment]::SetEnvironmentVariable($providerEnv.EnvName, $ApiKey, "Process")

    # ========================================================
    # SP-2: stdout-Format bei einfachem Run
    # ========================================================
    Write-Section "SP-2" "stdout-Format bei einfachem Run"

    $stdoutFile = Join-Path $runDir "sp2-stdout.txt"
    $stderrFile = Join-Path $runDir "sp2-stderr.txt"
    $sp2SessionId = [Guid]::NewGuid().ToString()
    $simpleArgs = @("agent", "--local", "--session-id", $sp2SessionId, "--message", "Sage `"OK`" und sonst nichts.", "--model", $Model, "--json")

    try {
        $startTime = Get-Date
        & openclaw @simpleArgs > $stdoutFile 2> $stderrFile
        $exit = $LASTEXITCODE
        $duration = ((Get-Date) - $startTime).TotalSeconds

        Write-Result "exit-code" $exit
        Write-Result "duration-sec" ([math]::Round($duration, 2))

        $stdout = Get-Content $stdoutFile -Raw -Encoding UTF8
        if (-not $stdout) { $stdout = "" }
        Write-Result "stdout-bytes" $stdout.Length

        if ($stdout.Length -eq 0) {
            Write-Fail "stdout-format" "leer — pruefe stderr-Datei"
        } else {
            $isJson = $false
            try { $null = $stdout | ConvertFrom-Json -ErrorAction Stop; $isJson = $true } catch {}

            $lines = ($stdout -split "`r?`n" | Where-Object { $_.Trim() })
            $isNDJson = $false
            if (-not $isJson -and $lines.Count -gt 1) {
                $allParse = $true
                foreach ($line in $lines) {
                    try { $null = $line | ConvertFrom-Json -ErrorAction Stop } catch { $allParse = $false; break }
                }
                $isNDJson = $allParse
            }

            if ($isJson) { Write-Result "stdout-format" "JSON (single document)" }
            elseif ($isNDJson) { Write-Result "stdout-format" "NDJSON ($($lines.Count) lines)" }
            else { Write-Result "stdout-format" "Plain text or mixed" }

            $preview = $stdout.Substring(0, [Math]::Min(200, $stdout.Length)) -replace "`r", "" -replace "`n", "\n"
            Write-Result "stdout-preview" $preview
        }
    } catch {
        Write-Fail "sp2-run" "Exception: $_"
    }

    # ========================================================
    # SP-3: Streaming-Verhalten via --verbose
    # ========================================================
    Write-Section "SP-3" "Streaming-Verhalten"

    $verboseStdoutFile = Join-Path $runDir "sp3-verbose-stdout.txt"
    $sp3SessionId = [Guid]::NewGuid().ToString()
    $verboseArgs = @("agent", "--local", "--session-id", $sp3SessionId, "--message", "Schreibe drei Saetze ueber Hummer.", "--model", $Model, "--verbose", "on")

    try {
        & openclaw @verboseArgs > $verboseStdoutFile 2>&1
        $vExit = $LASTEXITCODE
        Write-Result "verbose-exit" $vExit

        $verboseContent = Get-Content $verboseStdoutFile -Raw -Encoding UTF8
        if ($verboseContent) {
            $vLines = ($verboseContent -split "`r?`n" | Where-Object { $_.Trim() })
            Write-Result "verbose-line-count" $vLines.Count

            $hasProgressMarkers = $verboseContent -match "(thinking|tool_use|tool_call|step|reasoning|progress)"
            Write-Result "progress-markers" $hasProgressMarkers
            Write-Result "verbose-file" $verboseStdoutFile
        }
    } catch {
        Write-Fail "sp3-run" "Exception: $_"
    }

    # ========================================================
    # SP-6: Token/Cost-Info in Output
    # ========================================================
    Write-Section "SP-6" "Token/Cost-Tracking in Output"

    if (Test-Path $stdoutFile) {
        $allText = (Get-Content $stdoutFile -Raw -Encoding UTF8) + "`n" + (Get-Content $verboseStdoutFile -Raw -Encoding UTF8 -ErrorAction SilentlyContinue)

        $tokenPattern = '(?i)(input_tokens|output_tokens|prompt_tokens|completion_tokens|usage|cost)'
        $matchesFound = [regex]::Matches($allText, $tokenPattern)
        Write-Result "token-keywords-found" ($matchesFound.Count)

        if ($matchesFound.Count -gt 0) {
            $samples = $matchesFound | Select-Object -First 5 | ForEach-Object { $_.Value }
            Write-Result "token-keyword-samples" ($samples -join ", ")
        }
    } else {
        Write-Note "SP-6 uebersprungen — kein stdout-Output verfuegbar"
    }

    # ========================================================
    # SP-7: SIGTERM / Hard-Kill Verhalten
    # ========================================================
    Write-Section "SP-7" "Cancellation via Stop-Process"

    $sp7Stdout = Join-Path $runDir "sp7-stdout.txt"
    $sp7SessionId = [Guid]::NewGuid().ToString()
    # Lange Task: --thinking max + 2000-Wort-Essay erzwingt mindestens 30s
    # Laufzeit auch bei schnellen Modellen wie Gemini-Flash. Sonst ist der
    # Process schon vor Sleep 4s durch und Cancellation kann nicht getestet
    # werden (Befund 2026-05-09: Gemini-Flash 7s Total-Duration).
    $sp7LongMsg = "Schreibe einen ausfuehrlichen 2000-Wort-Essay ueber Hummerernaehrung mit fuenf wissenschaftlich fundierten Hauptkapiteln, jedem mit eigenem Quellenverzeichnis."

    # Start-Process kann openclaw (das .ps1-Wrapper ist) nicht direkt
    # spawnen. Die .cmd-Variante ist ein cmd-Batch-Wrapper. Aber: Start-Process
    # joined die ArgumentList mit Spaces statt einzeln zu uebergeben — bei
    # langen Args mit Quotes wird das zu falschem Splitting. Robust: PowerShell
    # Background-Job mit splat-Operator @args.
    try {
        $job = Start-Job -ScriptBlock {
            param($Model, $SessionId, $Msg, $StdoutFile)
            & openclaw agent --local --session-id $SessionId --message $Msg --model $Model --thinking max *> $StdoutFile
        } -ArgumentList $Model, $sp7SessionId, $sp7LongMsg, $sp7Stdout

        Start-Sleep -Seconds 8
        $jobAlive = ($job.State -eq "Running")
        Write-Result "alive-after-8s" $jobAlive

        if ($jobAlive) {
            # Find node.exe child of the job's PowerShell host (the job runs in a child
            # PowerShell.exe; the openclaw wrapper inside spawns node.exe).
            $jobChildProcesses = Get-CimInstance Win32_Process |
                Where-Object { $_.ParentProcessId -eq $job.ChildJobs[0].JobStateInfo.PSBeginInvocationInfo.PSP } |
                Select-Object -ExpandProperty ProcessId
            # Fallback: kill all node.exe processes spawned in last 30 seconds
            $candidatePids = (Get-Process -Name node, openclaw -ErrorAction SilentlyContinue |
                Where-Object { $_.StartTime -gt (Get-Date).AddSeconds(-30) }).Id

            $killOutput = ""
            foreach ($p in $candidatePids) {
                $killOutput += (& taskkill /F /T /PID $p 2>&1 | Out-String)
            }
            Stop-Job -Job $job -ErrorAction SilentlyContinue
            Write-Result "taskkill-attempted-pids" ($candidatePids -join ",")
            Write-Result "taskkill-output" ($killOutput -replace "`r?`n", " | " | Select-Object -First 300)
            Start-Sleep -Seconds 3
            $remainingNode = (Get-Process -Name node -ErrorAction SilentlyContinue |
                Where-Object { $_.StartTime -gt (Get-Date).AddSeconds(-60) }).Count
            Write-Result "node-procs-after-kill" $remainingNode
        } else {
            Write-Note "Job war nach 8s schon fertig — Task zu kurz fuer Cancellation-Test mit Modell $Model"
        }
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Fail "sp7-run" "Exception: $_"
    }

    # ========================================================
    # SP-8: Workdir-Verhalten
    # ========================================================
    Write-Section "SP-8" "Worktree-Pfad-Uebergabe"

    $workdirTest = Join-Path $runDir "workdir-test"
    New-Item -ItemType Directory -Path $workdirTest -Force | Out-Null

    foreach ($flag in @("--workdir", "--cwd", "--working-directory")) {
        $sp8Stdout = Join-Path $runDir "sp8-$($flag.TrimStart('-')).txt"
        $sp8SessionId = [Guid]::NewGuid().ToString()
        $sp8Args = @("agent", "--local", "--session-id", $sp8SessionId, "--message", "Schreibe `"hello`" in eine Datei test.txt im aktuellen Verzeichnis.", "--model", $Model, $flag, $workdirTest)
        try {
            & openclaw @sp8Args > $sp8Stdout 2>&1
            $sp8Exit = $LASTEXITCODE
            $createdFile = Test-Path (Join-Path $workdirTest "test.txt")
            Write-Result "$flag-exit" $sp8Exit
            Write-Result "$flag-file-created" $createdFile

            if ($createdFile) {
                Remove-Item (Join-Path $workdirTest "test.txt") -Force -ErrorAction SilentlyContinue
            }
        } catch {
            Write-Fail "$flag-run" "Exception: $_"
        }
    }
}

# ============================================================
# SP-5: MCP-Uebergabe (vereinfacht — nur Flag-Existenz)
# ============================================================
Write-Section "SP-5" "MCP-Uebergabe (Flag-Detection)"

if (Test-Path (Join-Path $runDir "help-output.txt")) {
    $help = Get-Content (Join-Path $runDir "help-output.txt") -Raw -Encoding UTF8
    foreach ($mcpFlag in @("--mcp", "--mcp-config", "--mcps", "--mcp-server")) {
        if ($help -match [regex]::Escape($mcpFlag)) {
            $context = ""
            if ($help -match "(?<=[\r\n])[^\r\n]*$([regex]::Escape($mcpFlag))[^\r\n]*") {
                $context = $matches[0].Trim()
            }
            Write-Result "$mcpFlag" "FOUND — $context"
        }
    }
    Write-Note "Falls kein --mcp Flag: OpenClaw erwartet vermutlich Config-File. Nachpruefen via README."
}

# ============================================================
# Abschluss
# ============================================================
Write-Section "Abschluss" "Zusammenfassung & naechster Schritt"

Write-Result "report-file" $reportFile
Write-Result "log-dir" $runDir

Add-Content -Path $reportFile -Value "`n## Naechster Schritt`n"
Add-Content -Path $reportFile -Value "1. Diesen Report-File-Inhalt in den Chat pasten:`n"
Add-Content -Path $reportFile -Value "   ``Get-Content '$reportFile' | Set-Clipboard```n"
Add-Content -Path $reportFile -Value "2. Befunde fuer SP-1..SP-8 in ``docs/spike-results-openclaw.md`` uebernehmen.`n"
Add-Content -Path $reportFile -Value "3. Welle 2 (Tote-Hose-End-to-End mit Mock-Bridge) starten.`n"

Write-Host ""
Write-Host "Spike abgeschlossen." -ForegroundColor Cyan
Write-Host "Report:    $reportFile"
Write-Host "Log-Dir:   $runDir"
Write-Host ""
Write-Host "Inhalt in Chat kopieren:" -ForegroundColor Yellow
Write-Host "  Get-Content '$reportFile' | Set-Clipboard"
Write-Host ""
