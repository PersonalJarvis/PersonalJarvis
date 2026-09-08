#Requires -Version 5.1
<#
.SYNOPSIS
    Pack the frozen Personal Jarvis bundle into an MSIX for the Microsoft Store.

.DESCRIPTION
    Runs after build.ps1 (or on its own with the PyInstaller step) and:
        1. stages dist\Jarvis into dist\msix\ unchanged,
        2. renders the Store tile images from assets\icons\jarvis-gigi-256.png,
        3. fills packaging\windows\msix\AppxManifest.xml,
        4. packs the folder with makeappx from the Windows SDK.

    The result is dist\installers\PersonalJarvis-x64.msix.

    The package is NOT signed. The Store signs an MSIX with Microsoft's own
    certificate after certification, and that is the signature that makes a
    Store install free of the SmartScreen prompt. An unsigned MSIX cannot be
    installed by double-click, so this file is only ever uploaded to Partner
    Center - it is not a release asset and the website never links to it.

.PARAMETER SkipPyInstaller
    Package the existing dist\Jarvis instead of freezing again. CI passes this
    because build.ps1 has just produced the bundle the setup wraps, and the
    Store package must be that same bundle.

.NOTES
    Identity comes from the environment so the values live in one place
    (GitHub repository variables), never in a file:

        MSSTORE_IDENTITY_NAME            Package/Identity/Name from Partner Center
        MSSTORE_PUBLISHER                Package/Identity/Publisher (CN=...)
        MSSTORE_PUBLISHER_DISPLAY_NAME   Package/Properties/PublisherDisplayName

    Without them the script uses placeholders that makeappx accepts, so the
    packaging path is exercised on every CI run; the Store rejects a package
    whose identity does not match the reserved app name, which is the intended
    safety net.
#>
[CmdletBinding()]
param(
    [switch] $SkipPyInstaller
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step([string] $Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Stop-WithError([string] $Message) { Write-Error $Message; exit 1 }

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BundleDir = Join-Path $RepoRoot "dist\Jarvis"
$StageDir = Join-Path $RepoRoot "dist\msix"
$InstallerDir = Join-Path $RepoRoot "dist\installers"
$ManifestTemplate = Join-Path $PSScriptRoot "msix\AppxManifest.xml"
$IconSource = Join-Path $RepoRoot "assets\icons\jarvis-gigi-256.png"
$GuiExeName = "PersonalJarvis.exe"
$OutputFile = Join-Path $InstallerDir "PersonalJarvis-x64.msix"

if (-not (Test-Path $ManifestTemplate)) { Stop-WithError "manifest template not found at $ManifestTemplate" }
if (-not (Test-Path $IconSource)) { Stop-WithError "icon source not found at $IconSource" }

# --- Version ---------------------------------------------------------------
# Same single source of truth as build.ps1. MSIX wants four numeric parts and
# reserves the last one for the Store, so 1.6.0 becomes 1.6.0.0.
$InitText = Get-Content (Join-Path $RepoRoot "jarvis\__init__.py") -Raw
$VersionMatch = [regex]::Match($InitText, '__version__\s*=\s*"([^"]+)"')
if (-not $VersionMatch.Success) { Stop-WithError "jarvis/__init__.py does not define __version__" }
$AppVersion = $VersionMatch.Groups[1].Value
$NumericMatch = [regex]::Match($AppVersion, '^(\d+)\.(\d+)\.(\d+)')
if (-not $NumericMatch.Success) { Stop-WithError "version '$AppVersion' does not start with MAJOR.MINOR.PATCH" }
$MsixVersion = "{0}.{1}.{2}.0" -f $NumericMatch.Groups[1].Value, $NumericMatch.Groups[2].Value, $NumericMatch.Groups[3].Value
Write-Step "Personal Jarvis $AppVersion -> MSIX version $MsixVersion"

# --- Identity --------------------------------------------------------------
function Get-EnvOrDefault([string] $Name, [string] $Default) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value.Trim()
}
$IdentityName = Get-EnvOrDefault "MSSTORE_IDENTITY_NAME" "PersonalJarvis.PersonalJarvis"
# A plain distinguished name: makeappx validates the Publisher as X.500, so
# the placeholder must already be one.
$Publisher = Get-EnvOrDefault "MSSTORE_PUBLISHER" "CN=PersonalJarvisPlaceholder"
$PublisherDisplayName = Get-EnvOrDefault "MSSTORE_PUBLISHER_DISPLAY_NAME" "Personal Jarvis"
if ($Publisher -eq "CN=PersonalJarvisPlaceholder") {
    Write-Warning "MSSTORE_PUBLISHER is not set - packing with a placeholder identity. The Store will reject this package; set the repository variables from Partner Center for a real submission."
}

# --- 1. The frozen bundle --------------------------------------------------
if ($SkipPyInstaller) {
    Write-Step "1/4 reusing the existing bundle (-SkipPyInstaller)"
} else {
    Write-Step "1/4 freezing with PyInstaller"
    Push-Location $RepoRoot
    try {
        & python -m PyInstaller jarvis.spec --noconfirm --clean
        if ($LASTEXITCODE -ne 0) { Stop-WithError "pyinstaller exited with $LASTEXITCODE" }
    } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $BundleDir $GuiExeName))) {
    Stop-WithError "the frozen bundle is missing $GuiExeName (expected under $BundleDir) - run packaging\windows\build.ps1 first"
}

# --- 2. Stage --------------------------------------------------------------
Write-Step "2/4 staging the bundle into $StageDir"
if (Test-Path $StageDir) { Remove-Item -Recurse -Force $StageDir }
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $StageDir "Assets") | Out-Null
# robocopy exit codes below 8 mean "copied" - it is the one tool here whose
# success is not exit 0.
& robocopy $BundleDir $StageDir /E /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Stop-WithError "robocopy failed with $LASTEXITCODE" }
$global:LASTEXITCODE = 0

# --- 3. Tiles + manifest ---------------------------------------------------
# The Store wants the tiles as PNGs at fixed sizes. Pillow is a core
# dependency of the app, so the interpreter that built the bundle can render
# them; the mascot is centred on the brand's matte black, never stretched.
Write-Step "3/4 rendering tiles and writing AppxManifest.xml"
$TileScript = @'
import sys
from pathlib import Path
from PIL import Image

src = Path(sys.argv[1])
out = Path(sys.argv[2])
icon = Image.open(src).convert("RGBA")
tiles = {
    "Square44x44Logo.png": (44, 44),
    "Square150x150Logo.png": (150, 150),
    "Wide310x150Logo.png": (310, 150),
    "StoreLogo.png": (50, 50),
}
for name, (w, h) in tiles.items():
    canvas = Image.new("RGBA", (w, h), (10, 10, 10, 255))
    side = int(min(w, h) * 0.8)
    glyph = icon.resize((side, side), Image.LANCZOS)
    canvas.alpha_composite(glyph, ((w - side) // 2, (h - side) // 2))
    canvas.save(out / name, "PNG")
print("tiles:", ", ".join(tiles))
'@
$TileScriptPath = Join-Path $StageDir "..\msix-tiles.py"
Set-Content -Path $TileScriptPath -Value $TileScript -Encoding UTF8
& python $TileScriptPath $IconSource (Join-Path $StageDir "Assets")
if ($LASTEXITCODE -ne 0) { Stop-WithError "tile rendering failed with $LASTEXITCODE" }
Remove-Item $TileScriptPath -Force

$Manifest = Get-Content $ManifestTemplate -Raw
$Manifest = $Manifest.Replace("{{IdentityName}}", $IdentityName)
$Manifest = $Manifest.Replace("{{Publisher}}", $Publisher)
$Manifest = $Manifest.Replace("{{PublisherDisplayName}}", $PublisherDisplayName)
$Manifest = $Manifest.Replace("{{Version}}", $MsixVersion)
$Manifest = $Manifest.Replace("{{Executable}}", $GuiExeName)
if ($Manifest -match "\{\{[A-Za-z]+\}\}") { Stop-WithError "unfilled placeholder left in the manifest: $($Matches[0])" }
# makeappx reads the manifest as UTF-8 without a byte-order mark.
[IO.File]::WriteAllText((Join-Path $StageDir "AppxManifest.xml"), $Manifest, (New-Object Text.UTF8Encoding $false))

# --- 4. Pack ---------------------------------------------------------------
Write-Step "4/4 packing with makeappx"
$KitsRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
$MakeAppx = $null
if (Test-Path $KitsRoot) {
    $MakeAppx = Get-ChildItem -Path $KitsRoot -Directory |
        Where-Object { $_.Name -match '^10\.' } |
        Sort-Object { [version] $_.Name } -Descending |
        ForEach-Object { Join-Path $_.FullName "x64\makeappx.exe" } |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1
}
if (-not $MakeAppx) {
    Stop-WithError "makeappx.exe not found under $KitsRoot - install the Windows 10/11 SDK (it is preinstalled on GitHub's windows-latest runners)"
}
Write-Host "makeappx: $MakeAppx"
New-Item -ItemType Directory -Force -Path $InstallerDir | Out-Null
if (Test-Path $OutputFile) { Remove-Item $OutputFile -Force }
& $MakeAppx pack /d $StageDir /p $OutputFile /o
if ($LASTEXITCODE -ne 0) { Stop-WithError "makeappx exited with $LASTEXITCODE" }
if (-not (Test-Path $OutputFile)) { Stop-WithError "makeappx reported success but $OutputFile is missing" }

$SizeMb = [math]::Round((Get-Item $OutputFile).Length / 1MB, 1)
Write-Step "done: $OutputFile ($SizeMb MB, identity $IdentityName, publisher $Publisher)"
