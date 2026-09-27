<#
.SYNOPSIS
    Build PackPilot-<version>-Setup.exe with Inno Setup.

.DESCRIPTION
    1. Generate installer/version.iss and installer/version_info.txt from app/version.py
       (the single source of truth for the version number);
    2. Locate ISCC.exe (Inno Setup 6);
    3. Compile installer/PackPilot.iss.

    Run `python scripts/build.py` first to produce dist\PackPilot\PackPilot.exe.

    NOTE: this file is intentionally ASCII-only, because Windows PowerShell 5.1
    decodes BOM-less .ps1 files using the system code page.
#>

[CmdletBinding()]
param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$IsccPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Test-Path $Python)) {
    $Python = "python"
}

Write-Host "Generating version files from app/version.py ..."
& $Python "scripts\make_version_info.py"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to generate version files."
}
$version = (& $Python -c "from app.version import __version__; print(__version__)").Trim()
Write-Host "Version: $version"

$exePath = Join-Path $projectRoot "dist\PackPilot\PackPilot.exe"
if (-not (Test-Path $exePath)) {
    throw "Missing $exePath - run 'python scripts/build.py' first."
}

$installerDir = Join-Path $projectRoot "installer"

if (-not $IsccPath) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            $IsccPath = $candidate
            break
        }
    }
}

if (-not $IsccPath) {
    Write-Warning "Inno Setup (ISCC.exe) was not found."
    Write-Host "Install Inno Setup 6: https://jrsoftware.org/isdl.php"
    Write-Host "Then re-run: powershell -ExecutionPolicy Bypass -File scripts/build_installer.ps1"
    Write-Host "The .iss configuration is already generated at installer/PackPilot.iss"
    exit 2
}

Write-Host "Using compiler: $IsccPath"
& $IsccPath (Join-Path $installerDir "PackPilot.iss")
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed (exit code $LASTEXITCODE)."
}

$setup = Join-Path $projectRoot "dist\PackPilot-$version-Setup.exe"
$builtSetup = Join-Path $installerDir "output\PackPilot-$version-Setup.exe"
if (Test-Path $builtSetup) {
    Copy-Item -LiteralPath $builtSetup -Destination $setup -Force
    Write-Host "Installer created: $setup"
} elseif (Test-Path $setup) {
    Write-Host "Installer created: $setup"
} else {
    Write-Host "Installer created (see the installer/output directory)."
}
