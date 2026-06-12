param(
    [string]$Python = "python",
    [ValidateSet("onedir", "onefile")][string]$Mode = "onedir",
    [switch]$AutoFfmpeg,
    [switch]$AutoYtDlp,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if ($Clean) {
    if (Test-Path "$Root\build") { Remove-Item -Recurse -Force "$Root\build" }
    if (Test-Path "$Root\dist") { Remove-Item -Recurse -Force "$Root\dist" }
    if (Test-Path "$Root\__pycache__") { Remove-Item -Recurse -Force "$Root\__pycache__" }
}

# Create a local venv in onefile/.venv
if (!(Test-Path "$Root\.venv")) {
    & $Python -m venv "$Root\.venv"
}

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

# Install build deps
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install --upgrade pyinstaller pywin32 pefile PyQt6

function Initialize-YtDlpInVendor {
    param(
        [string]$VendorDir,
        [switch]$Force
    )

    $ytdlp = Join-Path $VendorDir "yt-dlp.exe"
    if (!$Force -and (Test-Path $ytdlp)) {
        return
    }

    $url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
    Write-Host "Downloading yt-dlp.exe from: $url"
    Invoke-WebRequest -Uri $url -OutFile $ytdlp
}
function Initialize-FfmpegInVendor {
    param(
        [string]$VendorDir,
        [switch]$Force
    )

    $ffmpeg = Join-Path $VendorDir "ffmpeg.exe"
    $ffprobe = Join-Path $VendorDir "ffprobe.exe"

    if (!$Force -and (Test-Path $ffmpeg) -and (Test-Path $ffprobe)) {
        return
    }

    # Static build (no avcodec-*.dll required)
    $url = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
    $tmp = Join-Path $env:TEMP ("ffmpeg-{0}" -f ([Guid]::NewGuid().ToString("N")))
    $zip = Join-Path $tmp "ffmpeg.zip"
    $extract = Join-Path $tmp "extract"

    New-Item -ItemType Directory -Force -Path $extract | Out-Null
    Write-Host "Downloading FFmpeg (static) from: $url"
    Invoke-WebRequest -Uri $url -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $extract -Force

    $bin = Get-ChildItem -Path $extract -Recurse -Directory -Filter "bin" | Select-Object -First 1
    if (-not $bin) {
        throw "FFmpeg download layout unexpected (bin/ not found)."
    }

    $srcFfmpeg = Join-Path $bin.FullName "ffmpeg.exe"
    $srcFfprobe = Join-Path $bin.FullName "ffprobe.exe"
    if (!(Test-Path $srcFfmpeg)) { throw "ffmpeg.exe not found in downloaded archive." }
    if (!(Test-Path $srcFfprobe)) { throw "ffprobe.exe not found in downloaded archive." }

    Copy-Item -Force $srcFfmpeg $ffmpeg
    Copy-Item -Force $srcFfprobe $ffprobe

    # Clean up temp
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

# Optional: bundle ffprobe.exe too if present
$Ffmpeg = Join-Path $Root "vendor\ffmpeg.exe"
$Ffprobe = Join-Path $Root "vendor\ffprobe.exe"
$AppIcon = Join-Path $Root "vendor\app.ico"
$ProgressGif = Join-Path $Root "vendor\progress.gif"
$FallbackGif = Join-Path $Root "vendor\blue-archive-koharu.gif"
$YtDlpExe = Join-Path $Root "vendor\yt-dlp.exe"

if ($AutoFfmpeg) {
    Initialize-FfmpegInVendor -VendorDir (Join-Path $Root "vendor") -Force
}

if ($AutoYtDlp) {
    Initialize-YtDlpInVendor -VendorDir (Join-Path $Root "vendor") -Force
}

if (!(Test-Path $Ffmpeg)) {
    Write-Error "Missing $Ffmpeg. Put ffmpeg.exe in onefile/vendor/ before building (or pass -AutoFfmpeg)."
}

if (!(Test-Path $YtDlpExe)) {
    Write-Error "Missing $YtDlpExe. Put yt-dlp.exe in onefile/vendor/ before building (or pass -AutoYtDlp)."
}

$AddBinaryArgs = @(
    "--add-binary", "$Ffmpeg;."
)

if (Test-Path $Ffprobe) {
    $AddBinaryArgs += "--add-binary"
    $AddBinaryArgs += "$Ffprobe;."
}

# If your FFmpeg build ships with separate DLLs, put them in vendor/ too.
# We'll bundle all DLLs alongside the exe so it works when extracted by PyInstaller.
$Dlls = Get-ChildItem -Path (Join-Path $Root "vendor") -Filter "*.dll" -File -ErrorAction SilentlyContinue
foreach ($dll in $Dlls) {
    $AddBinaryArgs += "--add-binary"
    $AddBinaryArgs += ("{0};." -f $dll.FullName)
}

# Build. `onefile` makes a single exe (self-extracting). `onedir` makes a folder next to the exe (more reliable for FFmpeg shared DLL builds).
$IconArgs = @()
if (Test-Path $AppIcon) {
    $IconArgs += "--icon"
    $IconArgs += $AppIcon

    # Also ship the icon file itself so the tray can load a multi-size .ico reliably.
    $IconArgs += "--add-data"
    $IconArgs += "$AppIcon;."
}

# Ship an animated gif for the floating progress window (Qt tray mode).
# Destination is vendor/ so it is discoverable in both onedir (_internal/vendor)
# and onefile (_MEIPASS/vendor).
$GifArgs = @()
if (Test-Path $ProgressGif) {
    $GifArgs += "--add-data"
    $GifArgs += "$ProgressGif;vendor"
}
elseif (Test-Path $FallbackGif) {
    $GifArgs += "--add-data"
    $GifArgs += "$FallbackGif;vendor"
}

$ModeArgs = @("--onedir")
if ($Mode -eq "onefile") {
    $ModeArgs = @("--onefile")
}

& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    @ModeArgs `
    --noconsole `
    --exclude-module yt_dlp `
    --name "ytdlp-onefile" `
    @IconArgs `
    @GifArgs `
    @AddBinaryArgs `
    "$Root\main.py"

# Ship yt-dlp.exe as a *separate* file next to the app (so it can be updated without rebuilding).
if ($Mode -eq "onefile") {
    $ExternalYtDlpOut = Join-Path $Root "dist\yt-dlp.exe"
    Copy-Item -Force $YtDlpExe $ExternalYtDlpOut
}
else {
    $ExternalYtDlpOut = Join-Path $Root "dist\ytdlp-onefile\yt-dlp.exe"
    Copy-Item -Force $YtDlpExe $ExternalYtDlpOut
}

if ($Mode -eq "onefile") {
    Write-Host "Built: $Root\dist\ytdlp-onefile.exe"
}
else {
    Write-Host "Built: $Root\dist\ytdlp-onefile\ytdlp-onefile.exe"
}
Write-Host "External yt-dlp: $ExternalYtDlpOut"
