<#
  Rasterize a brand HTML file to PNG via headless Chrome.
  Usage:  pwsh assets/brand/render.ps1 [-In banner.html] [-Out banner-css.png] [-W 1280] [-H 480]
  NOTE: defaults to banner-css.png so it never overwrites the AI-generated banner.png.
  Writes to a space-free temp path first (Chrome --screenshot mishandles spaces),
  then copies into assets/brand/.
#>
param(
  [string]$In = "banner.html",
  [string]$Out = "banner-css.png",
  [int]$W = 1280,
  [int]$H = 480,
  [int]$Scale = 2
)

$ErrorActionPreference = "Stop"
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chrome)) { throw "Chrome not found at $chrome" }

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$inPath = Join-Path $dir $In
if (-not (Test-Path $inPath)) { throw "Input not found: $inPath" }

$tmpProfile = Join-Path $env:TEMP ("cprof-" + [guid]::NewGuid().ToString("N"))
$tmpOut = Join-Path $env:TEMP ("pjshot-" + [guid]::NewGuid().ToString("N") + ".png")
$fileUrl = "file:///" + (($inPath -replace '\\','/') -replace ' ','%20')

& $chrome "--headless=new" "--hide-scrollbars" "--disable-gpu" "--no-sandbox" `
  "--force-device-scale-factor=$Scale" "--virtual-time-budget=4500" `
  "--user-data-dir=$tmpProfile" "--window-size=$W,$H" `
  "--screenshot=$tmpOut" "$fileUrl" | Out-Null

if (Test-Path $tmpOut) {
  Copy-Item $tmpOut (Join-Path $dir $Out) -Force
  Write-Output ("OK {0}x{1}@{2}x -> {3} ({4} bytes)" -f $W, $H, $Scale, $Out, (Get-Item $tmpOut).Length)
  Remove-Item $tmpOut -Force -ErrorAction SilentlyContinue
} else {
  Write-Output "RENDER FAILED (exit $LASTEXITCODE)"
}
Remove-Item $tmpProfile -Recurse -Force -ErrorAction SilentlyContinue
