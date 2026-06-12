param(
    [string]$Version = "1.3.0",
    [ValidateSet("onedir")][string]$Mode = "onedir",
    [switch]$BuildFirst
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if ($BuildFirst) {
    & "$Root\build.ps1" -Mode $Mode
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

& $Iscc "/DMyAppVersion=$Version" $Iss

Write-Host "Built installer in: $Root\dist-installer"
