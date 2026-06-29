<#
  Screenshot a URL via headless Chrome to a PNG under assets/brand/.
  Usage: pwsh scripts/brand-preview/shot.ps1 -Url <url> -Out name.png [-W 1000] [-H 5400] [-Scale 1]
#>
param(
  [Parameter(Mandatory = $true)][string]$Url,
  [Parameter(Mandatory = $true)][string]$Out,
  [int]$W = 1000,
  [int]$H = 5400,
  [int]$Scale = 1
)

$ErrorActionPreference = "Stop"
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chrome)) { throw "Chrome not found" }

$repoRoot = "C:\Users\Administrator\Desktop\Personal Jarvis"
$destDir = Join-Path $repoRoot "assets\brand"
$tmpProfile = Join-Path $env:TEMP ("cprof-" + [guid]::NewGuid().ToString("N"))
$tmpOut = Join-Path $env:TEMP ("urlshot-" + [guid]::NewGuid().ToString("N") + ".png")

& $chrome "--headless=new" "--hide-scrollbars" "--disable-gpu" "--no-sandbox" `
  "--force-device-scale-factor=$Scale" "--virtual-time-budget=6000" `
  "--user-data-dir=$tmpProfile" "--window-size=$W,$H" `
  "--screenshot=$tmpOut" "$Url" | Out-Null

if (Test-Path $tmpOut) {
  Copy-Item $tmpOut (Join-Path $destDir $Out) -Force
  Write-Output ("OK -> assets/brand/{0} ({1} bytes)" -f $Out, (Get-Item $tmpOut).Length)
  Remove-Item $tmpOut -Force -ErrorAction SilentlyContinue
} else {
  Write-Output "NO SHOT (exit $LASTEXITCODE)"
}
Remove-Item $tmpProfile -Recurse -Force -ErrorAction SilentlyContinue
