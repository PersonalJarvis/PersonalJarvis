# telephony-tunnel.ps1 — start a public HTTPS tunnel to the local FastAPI port.
#
# Twilio must reach Jarvis over a public HTTPS URL. On a VPS you use a real
# domain + Caddy/Let's Encrypt (see docs/telephony.md — the cloud-first default).
# On a home PC for development, this script opens a cloudflared (or ngrok)
# tunnel and prints the public URL to paste into [integrations.twilio]
# public_base_url.
#
# This is a maintainer DEV tool (PowerShell-only is fine per the doctrine line
# at scripts/ vs jarvis/). It does not run on the consumer's VPS runtime.
#
# Usage:
#   pwsh scripts/telephony-tunnel.ps1 -Port 8765
#   pwsh scripts/telephony-tunnel.ps1 -Port 8765 -Provider ngrok

[CmdletBinding()]
param(
    [int]$Port = 8765,
    [ValidateSet("cloudflared", "ngrok")]
    [string]$Provider = "cloudflared"
)

$ErrorActionPreference = "Stop"

function Test-Command([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host "Telephony tunnel: exposing local port $Port via $Provider" -ForegroundColor Cyan

if ($Provider -eq "cloudflared") {
    if (-not (Test-Command "cloudflared")) {
        Write-Host "cloudflared is not installed." -ForegroundColor Yellow
        Write-Host "Install it: winget install --id Cloudflare.cloudflared" -ForegroundColor Yellow
        Write-Host "Then re-run this script. Docs: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Starting cloudflared quick tunnel. Copy the https URL below into" -ForegroundColor Green
    Write-Host "the Telephony section -> Public base URL (without a trailing slash)." -ForegroundColor Green
    Write-Host "Then point your Twilio number's Voice webhook at <url>/api/telephony/voice." -ForegroundColor Green
    Write-Host ""
    & cloudflared tunnel --url "http://localhost:$Port"
}
elseif ($Provider -eq "ngrok") {
    if (-not (Test-Command "ngrok")) {
        Write-Host "ngrok is not installed." -ForegroundColor Yellow
        Write-Host "Install it: winget install --id Ngrok.Ngrok" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Starting ngrok. Copy the https forwarding URL into Public base URL." -ForegroundColor Green
    Write-Host ""
    & ngrok http $Port
}
