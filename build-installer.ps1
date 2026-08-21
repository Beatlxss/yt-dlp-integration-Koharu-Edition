param(
    [string]$Version,
    [ValidateSet("onedir")][string]$Mode = "onedir",
    [switch]$BuildFirst
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Get-ProjectVersion {
    $VersionFile = Join-Path $Root "app_version.py"
    if (!(Test-Path $VersionFile)) {
        throw "Missing canonical version file: $VersionFile"
    }
    $content = Get-Content -Raw -Path $VersionFile
    $match = [regex]::Match(
        $content,
        '^\s*APP_VERSION\s*=\s*["''](?<version>[^"'']+)["'']\s*$',
        [System.Text.RegularExpressions.RegexOptions]::Multiline
    )
    if (!$match.Success) {
        throw "APP_VERSION was not found in $VersionFile"
    }
    return $match.Groups["version"].Value.Trim()
}

$ProjectVersion = Get-ProjectVersion
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = $ProjectVersion
}
elseif ($Version.Trim() -ne $ProjectVersion) {
    throw "-Version '$($Version.Trim())' does not match APP_VERSION '$ProjectVersion'. Update app_version.py instead."
}

if ($BuildFirst) {
    & "$Root\build.ps1" -Mode $Mode
    if ($LASTEXITCODE -ne 0) {
        throw "Application build failed."
    }
}

$Iss = Join-Path $Root "installer\ytdlp-onefile.iss"
if (!(Test-Path $Iss)) {
    throw "Missing installer script: $Iss"
}

$DistExe = Join-Path $Root "dist\ytdlp-onefile\ytdlp-onefile.exe"
if (!(Test-Path $DistExe)) {
    throw "Missing build output: $DistExe. Run .\\build.ps1 -Mode onedir first (or pass -BuildFirst)."
}

$DistYtDlp = Join-Path $Root "dist\ytdlp-onefile\yt-dlp.exe"
if (!(Test-Path $DistYtDlp)) {
    throw "Missing build output: $DistYtDlp. Ensure onefile\\vendor\\yt-dlp.exe exists then rebuild (or pass -AutoYtDlp to build.ps1)."
}

$DistUpdater = Join-Path $Root "dist\ytdlp-onefile\Koharu Updater.exe"
if (!(Test-Path $DistUpdater)) {
    throw "Missing build output: $DistUpdater. Run .\build.ps1 -Mode onedir first (or pass -BuildFirst)."
}

function Find-Iscc {
    $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $common = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 5\ISCC.exe"),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
        "C:\Program Files\Inno Setup 5\ISCC.exe"
    )
    foreach ($p in $common) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

$Iscc = Find-Iscc
if (-not $Iscc) {
    Write-Error "Inno Setup Compiler (ISCC.exe) not found. Install Inno Setup 6, then re-run this script."
    Write-Host "Download: https://jrsoftware.org/isdl.php"
    exit 1
}

New-Item -ItemType Directory -Force -Path (Join-Path $Root "dist-installer") | Out-Null

$Version = $Version.Trim()
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$') {
    throw "Invalid version '$Version'. Use format like 1.3.1 or 1.3.1-beta.1"
}

& $Iscc "/DMyAppVersion=$Version" $Iss
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed while building the installer."
}

Write-Host "Built installer in: $Root\dist-installer"
