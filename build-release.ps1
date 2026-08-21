param(
    [string]$ReleaseTag,
    [string]$GitHubRepository,
    [string]$PreviousManifestPath,
    [switch]$BuildFirst,
    [switch]$IncludeYtDlp,
    [string]$YtDlpMinimumVersion,
    [switch]$TestMode
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Get-AppMetadata {
    $VersionFile = Join-Path $Root "app_version.py"
    if (!(Test-Path $VersionFile)) {
        throw "Missing canonical version file: $VersionFile"
    }

    $content = Get-Content -Raw -Path $VersionFile
    $versionMatch = [regex]::Match(
        $content,
        '^\s*APP_VERSION\s*=\s*["''](?<value>[^"'']+)["'']\s*$',
        [System.Text.RegularExpressions.RegexOptions]::Multiline
    )
    $repositoryMatch = [regex]::Match(
        $content,
        '^\s*GITHUB_REPOSITORY\s*=\s*["''](?<value>[^"'']+)["'']\s*$',
        [System.Text.RegularExpressions.RegexOptions]::Multiline
    )
    if (!$versionMatch.Success -or !$repositoryMatch.Success) {
        throw "APP_VERSION or GITHUB_REPOSITORY was not found in $VersionFile"
    }
    return [pscustomobject]@{
        Version = $versionMatch.Groups["value"].Value.Trim()
        Repository = $repositoryMatch.Groups["value"].Value.Trim()
    }
}

function Get-PreviousManifestFiles {
    param([string]$ManifestPath)

    $byPath = @{}
    if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
        return $byPath
    }
    if (!(Test-Path $ManifestPath)) {
        throw "Previous manifest not found: $ManifestPath"
    }
    $manifest = Get-Content -Raw -Path $ManifestPath | ConvertFrom-Json
    if ($null -eq $manifest.files) {
        throw "Previous manifest has no files array: $ManifestPath"
    }
    foreach ($entry in @($manifest.files)) {
        $path = [string]$entry.path
        $hash = [string]$entry.sha256
        $url = [string]$entry.url
        if ([string]::IsNullOrWhiteSpace($path) -or [string]::IsNullOrWhiteSpace($hash) -or [string]::IsNullOrWhiteSpace($url)) {
            throw "Previous manifest contains an invalid file entry."
        }
        $key = $path.ToLowerInvariant()
        if ($byPath.ContainsKey($key)) {
            throw "Previous manifest contains duplicate file path: $path"
        }
        $byPath[$key] = $entry
    }
    return $byPath
}

function New-AssetName {
    param(
        [int]$Index,
        [string]$Hash,
        [string]$TargetPath
    )

    $leaf = [IO.Path]::GetFileName($TargetPath) -replace '[^A-Za-z0-9._-]', '_'
    if ([string]::IsNullOrWhiteSpace($leaf)) {
        $leaf = "file.bin"
    }
    return ("update-{0:D4}-{1}-{2}" -f $Index, $Hash.Substring(0, 12), $leaf)
}

$metadata = Get-AppMetadata
$AppVersion = $metadata.Version
$SemVerPattern = '^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$'
if ($AppVersion -notmatch $SemVerPattern) {
    throw "APP_VERSION '$AppVersion' is not valid semantic versioning."
}

if ([string]::IsNullOrWhiteSpace($ReleaseTag)) {
    $ReleaseTag = "v$AppVersion"
}
$ReleaseTag = $ReleaseTag.Trim()
$TagVersion = $ReleaseTag -replace '^[vV]', ''
if ($TagVersion -ne $AppVersion) {
    throw "Release tag '$ReleaseTag' must match APP_VERSION '$AppVersion'."
}

if ([string]::IsNullOrWhiteSpace($GitHubRepository)) {
    $GitHubRepository = $metadata.Repository
}
$GitHubRepository = $GitHubRepository.Trim()
if ($GitHubRepository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw "Invalid GitHub repository '$GitHubRepository'. Use owner/repository."
}

if ($IncludeYtDlp -and [string]::IsNullOrWhiteSpace($YtDlpMinimumVersion)) {
    throw "-IncludeYtDlp requires -YtDlpMinimumVersion so newer compatible user copies are preserved."
}

if ($BuildFirst) {
    & "$Root\build-installer.ps1" -BuildFirst
    if ($LASTEXITCODE -ne 0) {
        throw "Build failed before release staging."
    }
}

$AppBuildDirectory = Join-Path $Root "dist\ytdlp-onefile"
$MainExecutable = Join-Path $AppBuildDirectory "ytdlp-onefile.exe"
$UpdaterExecutable = Join-Path $AppBuildDirectory "Koharu Updater.exe"
$Installer = Join-Path $Root ("dist-installer\Naughty-Koharu-v{0}.exe" -f $AppVersion)
foreach ($required in @($MainExecutable, $UpdaterExecutable, $Installer)) {
    if (!(Test-Path $required)) {
        throw "Missing release build output: $required. Run .\build-release.ps1 -BuildFirst."
    }
}

$ReleaseRoot = Join-Path $Root ("dist-update\Naughty-Koharu-v{0}" -f $AppVersion)
$AssetsDirectory = Join-Path $ReleaseRoot "assets"
if (Test-Path $ReleaseRoot) {
    Remove-Item -Recurse -Force $ReleaseRoot
}
New-Item -ItemType Directory -Force -Path $AssetsDirectory | Out-Null

$PreviousFiles = Get-PreviousManifestFiles -ManifestPath $PreviousManifestPath
$CurrentPaths = @{}
$ManifestFiles = New-Object System.Collections.Generic.List[object]
$AssetCount = 0
$ReusedCount = 0
$ChangedCount = 0

$sourceFiles = Get-ChildItem -LiteralPath $AppBuildDirectory -Recurse -File | Sort-Object FullName
foreach ($sourceFile in $sourceFiles) {
    $relativeSourcePath = $sourceFile.FullName.Substring($AppBuildDirectory.Length).TrimStart('\', '/')
    $targetPath = $relativeSourcePath -replace '\\', '/'
    if ($targetPath -ieq "ytdlp-onefile.exe") {
        $targetPath = "Naughty Koharu.exe"
    }
    if ($targetPath -ieq "yt-dlp.exe" -and !$IncludeYtDlp) {
        continue
    }

    $pathKey = $targetPath.ToLowerInvariant()
    if ($CurrentPaths.ContainsKey($pathKey)) {
        throw "Build output maps to duplicate installed path: $targetPath"
    }
    $CurrentPaths[$pathKey] = $true

    $hash = (Get-FileHash -LiteralPath $sourceFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $size = [Int64]$sourceFile.Length
    $previous = $PreviousFiles[$pathKey]
    $reuse = $false
    $url = ""
    if ($null -ne $previous -and ([string]$previous.sha256).ToLowerInvariant() -eq $hash) {
        $previousUrl = [string]$previous.url
        if ($TestMode -and $previousUrl -match '^file:') {
            $reuse = $true
            $url = $previousUrl
        }
        elseif (!$TestMode -and $previousUrl -match '^https://') {
            $reuse = $true
            $url = $previousUrl
        }
    }

    if ($reuse) {
        $ReusedCount++
    }
    else {
        $AssetCount++
        $assetName = New-AssetName -Index $AssetCount -Hash $hash -TargetPath $targetPath
        $assetPath = Join-Path $AssetsDirectory $assetName
        Copy-Item -LiteralPath $sourceFile.FullName -Destination $assetPath -Force
        if ($TestMode) {
            $url = ([Uri]$assetPath).AbsoluteUri
        }
        else {
            $url = "https://github.com/$GitHubRepository/releases/download/$ReleaseTag/$assetName"
        }
        $ChangedCount++
    }

    $entry = [ordered]@{
        path = $targetPath
        url = $url
        sha256 = $hash
        size = $size
    }
    if ($targetPath -ieq "yt-dlp.exe") {
        $entry.component = "yt-dlp"
        $entry.minimum_version = $YtDlpMinimumVersion.Trim()
    }
    $ManifestFiles.Add([pscustomobject]$entry)
}

$DeletedFiles = New-Object System.Collections.Generic.List[string]
foreach ($previous in $PreviousFiles.Values) {
    $previousPath = [string]$previous.path
    $previousKey = $previousPath.ToLowerInvariant()
    if ($previousKey -eq "yt-dlp.exe") {
        continue
    }
    if (!$CurrentPaths.ContainsKey($previousKey)) {
        $DeletedFiles.Add($previousPath)
    }
}

$manifest = [ordered]@{
    schema_version = 1
    version = $AppVersion
    release_tag = $ReleaseTag
    files = @($ManifestFiles.ToArray())
    deleted_files = @($DeletedFiles.ToArray() | Sort-Object)
}
if ($TestMode) {
    $manifest.test_only = $true
}

$ManifestPath = Join-Path $ReleaseRoot "update-manifest.json"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($ManifestPath, ($manifest | ConvertTo-Json -Depth 8), $Utf8NoBom)

$InstallerCopy = Join-Path $ReleaseRoot (Split-Path -Leaf $Installer)
Copy-Item -LiteralPath $Installer -Destination $InstallerCopy -Force

$UploadList = Join-Path $ReleaseRoot "release-upload-files.txt"
$uploadItems = @($InstallerCopy, $ManifestPath) + @(
    Get-ChildItem -LiteralPath $AssetsDirectory -File | Sort-Object Name | ForEach-Object { $_.FullName }
)
[IO.File]::WriteAllLines($UploadList, [string[]]$uploadItems, $Utf8NoBom)

Write-Host "Release staging created: $ReleaseRoot"
Write-Host "Manifest: $ManifestPath"
Write-Host "Full installer: $InstallerCopy"
Write-Host "New or changed update assets: $ChangedCount"
Write-Host "Reused assets from previous manifest: $ReusedCount"
Write-Host "Explicit obsolete files: $($DeletedFiles.Count)"
if ($TestMode) {
    Write-Host "Test mode manifest uses local file URLs and is accepted only with updater.exe --test-mode."
}
else {
    Write-Host "Upload every path in $UploadList to GitHub Release $ReleaseTag."
}