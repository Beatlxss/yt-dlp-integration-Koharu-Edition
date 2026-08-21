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

function Test-WindowsPeFile {
    param([string]$Path)

    try {
        if (!(Test-Path $Path)) { return $false }

        $stream = [System.IO.File]::OpenRead($Path)
        try {
            if ($stream.Length -lt 64) { return $false }

            $dosHeader = New-Object byte[] 64
            if ($stream.Read($dosHeader, 0, $dosHeader.Length) -ne $dosHeader.Length) {
                return $false
            }
            if ($dosHeader[0] -ne 0x4D -or $dosHeader[1] -ne 0x5A) { return $false }

            $peOffset = [System.BitConverter]::ToInt32($dosHeader, 0x3C)
            if ($peOffset -lt 64 -or $peOffset -gt ($stream.Length - 4)) { return $false }

            $stream.Seek($peOffset, [System.IO.SeekOrigin]::Begin) | Out-Null
            $signature = New-Object byte[] 4
            if ($stream.Read($signature, 0, $signature.Length) -ne $signature.Length) {
                return $false
            }
            return ($signature[0] -eq 0x50 -and $signature[1] -eq 0x45 -and $signature[2] -eq 0 -and $signature[3] -eq 0)
        }
        finally {
            $stream.Dispose()
        }
    }
    catch {
        return $false
    }
}

function Get-InvalidBinaryHint {
    param([string]$Path)

    if (!(Test-Path $Path)) { return "missing" }
    try {
        $prefix = [System.Text.Encoding]::ASCII.GetString([System.IO.File]::ReadAllBytes($Path)[0..63])
        if ($prefix.StartsWith("version https://git-lfs.github.com/spec/v1")) {
            return "an unresolved Git LFS pointer"
        }
    }
    catch {
        # Use the generic message below when the file cannot be read.
    }
    return "not a valid Windows executable"
}

function Initialize-YtDlpInVendor {
    param(
        [string]$VendorDir,
        [switch]$Force
    )

    $ytdlp = Join-Path $VendorDir "yt-dlp.exe"
    if (!$Force -and (Test-WindowsPeFile $ytdlp)) {
        return
    }

    if (Test-Path $ytdlp) {
        Write-Warning "Replacing $ytdlp because it is $(Get-InvalidBinaryHint $ytdlp)."
    }

    $url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
    $temporary = "$ytdlp.download-$([Guid]::NewGuid().ToString('N'))"
    Write-Host "Downloading yt-dlp.exe from: $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $temporary
        if (!(Test-WindowsPeFile $temporary)) {
            throw "Downloaded yt-dlp.exe is not a valid Windows executable."
        }
        Move-Item -Force $temporary $ytdlp
    }
    finally {
        Remove-Item -Force $temporary -ErrorAction SilentlyContinue
    }
}
function Initialize-FfmpegInVendor {
    param(
        [string]$VendorDir,
        [switch]$Force
    )

    $ffmpeg = Join-Path $VendorDir "ffmpeg.exe"
    $ffprobe = Join-Path $VendorDir "ffprobe.exe"

    if (!$Force -and (Test-WindowsPeFile $ffmpeg) -and (Test-WindowsPeFile $ffprobe)) {
        return
    }

    foreach ($candidate in @($ffmpeg, $ffprobe)) {
        if (Test-Path $candidate) {
            Write-Warning "Replacing $candidate because it is $(Get-InvalidBinaryHint $candidate)."
        }
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

    if (!(Test-WindowsPeFile $srcFfmpeg) -or !(Test-WindowsPeFile $srcFfprobe)) {
        throw "Downloaded FFmpeg archive did not contain valid Windows executables."
    }

    $tmpFfmpeg = "$ffmpeg.download-$([Guid]::NewGuid().ToString('N'))"
    $tmpFfprobe = "$ffprobe.download-$([Guid]::NewGuid().ToString('N'))"
    try {
        Copy-Item -Force $srcFfmpeg $tmpFfmpeg
        Copy-Item -Force $srcFfprobe $tmpFfprobe
        Move-Item -Force $tmpFfmpeg $ffmpeg
        Move-Item -Force $tmpFfprobe $ffprobe
    }
    finally {
        Remove-Item -Force $tmpFfmpeg, $tmpFfprobe -ErrorAction SilentlyContinue
    }

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

if (!(Test-WindowsPeFile $Ffmpeg)) {
    Write-Error "Invalid $Ffmpeg ($(Get-InvalidBinaryHint $Ffmpeg)). Run git lfs pull or build with -AutoFfmpeg."
}

if ((Test-Path $Ffprobe) -and !(Test-WindowsPeFile $Ffprobe)) {
    Write-Error "Invalid $Ffprobe ($(Get-InvalidBinaryHint $Ffprobe)). Run git lfs pull or build with -AutoFfmpeg."
}

if (!(Test-WindowsPeFile $YtDlpExe)) {
    Write-Error "Invalid $YtDlpExe ($(Get-InvalidBinaryHint $YtDlpExe)). Run git lfs pull or build with -AutoYtDlp."
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

$AppSpec = Join-Path $Root "build\app-spec"

& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    @ModeArgs `
    --noconsole `
    --exclude-module yt_dlp `
    --name "ytdlp-onefile" `
    --specpath $AppSpec `
    @IconArgs `
    @GifArgs `
    @AddBinaryArgs `
    "$Root\main.py"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed while building the application."
}

# Ship yt-dlp.exe as a *separate* file next to the app (so it can be updated without rebuilding).
if ($Mode -eq "onefile") {
    $ExternalYtDlpOut = Join-Path $Root "dist\yt-dlp.exe"
    Copy-Item -Force $YtDlpExe $ExternalYtDlpOut
}
else {
    $ExternalYtDlpOut = Join-Path $Root "dist\ytdlp-onefile\yt-dlp.exe"
    Copy-Item -Force $YtDlpExe $ExternalYtDlpOut
}

# The updater is a separate onefile executable so it can run after the app closes
# and safely replace the onedir executable or its own installed copy.
$UpdaterSource = Join-Path $Root "updater.py"
$UpdaterDist = Join-Path $Root "dist\updater-build"
$UpdaterWork = Join-Path $Root "build\updater"
$UpdaterSpec = Join-Path $Root "build\updater-spec"
if (!(Test-Path $UpdaterSource)) {
    throw "Missing updater source: $UpdaterSource"
}

& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --noconsole `
    --hidden-import tkinter `
    --name "Koharu Updater" `
    @IconArgs `
    --distpath $UpdaterDist `
    --workpath $UpdaterWork `
    --specpath $UpdaterSpec `
    $UpdaterSource

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed while building the updater."
}

$UpdaterBuilt = Join-Path $UpdaterDist "Koharu Updater.exe"
if (!(Test-Path $UpdaterBuilt)) {
    throw "Updater build output is missing: $UpdaterBuilt"
}

if ($Mode -eq "onefile") {
    $UpdaterOut = Join-Path $Root "dist\Koharu Updater.exe"
}
else {
    $UpdaterOut = Join-Path $Root "dist\ytdlp-onefile\Koharu Updater.exe"
}
Copy-Item -Force $UpdaterBuilt $UpdaterOut

if ($Mode -eq "onefile") {
    Write-Host "Built: $Root\dist\ytdlp-onefile.exe"
}
else {
    Write-Host "Built: $Root\dist\ytdlp-onefile\ytdlp-onefile.exe"
}
Write-Host "External yt-dlp: $ExternalYtDlpOut"
Write-Host "Updater: $UpdaterOut"
