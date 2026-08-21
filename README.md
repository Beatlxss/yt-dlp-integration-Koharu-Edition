## What it is

It's an tray system app with pop-up meniu selectable between MP3/MP4 and quality combined with yt-dlp

## What you can do

- Download videos in: 720p, 1080p, 1440p, 2560p
- Download music as MP3 in: 128, 192, 256, 320 kbps
- Download playlist with selected quality and type MP3/MP4

## How it works

- Copy a video/playlist link
- Click the app icon in your system tray
- Choose Video/Audio (or Playlist) and pick the quality
  (Selected quality downloads if possible else it returns to what is capable to download)

## Save location

- Your default download location, can be changed

## Extra

- Autostart
- Button to update yt-dlp.exe or manually change version via explorer where app is installed
- Everything is coded with AI

## Logging

Application logs are written to `%TEMP%\\ytdlp-onefile.log` using searchable, timestamped entries with components and download operation IDs.

- `LOG_LEVEL=INFO` is the default for normal operation.
- Set `LOG_LEVEL=DEBUG` before starting the app to include yt-dlp output and detailed HTTP/Qt diagnostics.
- Available levels are `DEBUG`, `INFO`, `WARN`, and `ERROR`.

## Extensions

- youtube/youtube music, twitter/x, reddit, tiktok (WIP)
- every has independent download button

## Application updates

Naughty Koharu keeps the existing Inno Setup installer for first installs, clean installs, and recovery. Normal application updates use public GitHub Releases and download only the installed files whose SHA-256 hashes differ from the release manifest. The updater never uses `git pull`, never needs Git on a user's machine, and does not download a repository source ZIP.

### Version source

`app_version.py` is the single manually maintained application-version source:

- `APP_VERSION` is compiled into the application and shown in Settings.
- `build-installer.ps1` reads the same value and passes it to Inno Setup.
- `installer/ytdlp-onefile.iss` requires the build script to supply that value, preventing an installer/app version mismatch.
- GitHub release tags must be `v` followed by that same value, such as `v1.3.1`.

Update checking runs in the background after tray startup, uses a six-hour HKCU cache, and can be triggered manually from `Settings > Check for application updates`. Network/API failures do not prevent startup. When an update is available, the menu shows the installed version, available version, and approximate changed-file download size.

### How updates work

1. The app queries GitHub's public `releases/latest` API and downloads the `update-manifest.json` release asset over HTTPS.
2. The app validates the release version with semantic-version comparison, then copies `Koharu Updater.exe` to a generated temporary directory and starts that copy.
3. After the app process exits, the updater validates every manifest path, downloads needed files to staging, and verifies each SHA-256 hash and declared size before changing the installation.
4. The updater moves existing files to backups, replaces only verified files, applies only explicit `deleted_files`, and restarts Naughty Koharu.
5. A failed replacement rolls back already changed files. An interrupted transaction is recovered from its on-disk journal on the next update attempt.

The updater never deletes a file merely because it is absent from a manifest. It only deletes paths listed explicitly in `deleted_files`.

### User data and yt-dlp

Application binaries live in `%LOCALAPPDATA%\Naughty Koharu`. Current mutable data is kept outside the application binary payload:

- Settings, download directory, progress-window location, and update timestamps: `HKCU\Software\ytdlp-onefile`
- Downloads: the selected download directory, defaulting to the Windows Downloads folder
- Logs: `%TEMP%\ytdlp-onefile.log`
- Autostart: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`

`yt-dlp.exe` remains a separate, user-updateable file. Release manifests exclude it by default, so normal Koharu updates do not overwrite or redownload it. If a release truly requires a minimum yt-dlp version, generate that release with `-IncludeYtDlp -YtDlpMinimumVersion <version>`; the updater preserves a newer compatible user-managed yt-dlp executable.

### Build a release

Set the new semantic version in `app_version.py`, then run:

```powershell
.\build-release.ps1 -BuildFirst -PreviousManifestPath "C:\path\to\previous\update-manifest.json"
```

`-BuildFirst` runs the existing onedir PyInstaller build and Inno Setup build. It produces:

- `dist-installer\Naughty-Koharu-vX.Y.Z.exe`: the full installer
- `dist\ytdlp-onefile\Koharu Updater.exe`: the standalone updater installed beside the app
- `dist-update\Naughty-Koharu-vX.Y.Z\update-manifest.json`: the update manifest
- `dist-update\Naughty-Koharu-vX.Y.Z\assets\`: only new or changed update files
- `dist-update\Naughty-Koharu-vX.Y.Z\release-upload-files.txt`: every file to upload

For the first updater-enabled release, omit `-PreviousManifestPath`; it stages all application payload files. For later releases, download the prior release's `update-manifest.json` and supply it with `-PreviousManifestPath`. Unchanged files retain the prior release's HTTPS asset URL in the new full-state manifest, so they are not staged or uploaded again.

Create a public, non-prerelease GitHub Release tagged exactly `vX.Y.Z`. Upload the full installer, `update-manifest.json`, and every asset listed in `release-upload-files.txt`. Keep the manifest asset name exactly `update-manifest.json`. GitHub authentication is only needed by the release publisher, never by application users.

The manifest format is intentionally small and file-based:

```json
{
  "schema_version": 1,
  "version": "1.3.1",
  "release_tag": "v1.3.1",
  "files": [
    {
      "path": "Naughty Koharu.exe",
      "url": "https://github.com/owner/repo/releases/download/v1.3.1/update-0001-....exe",
      "sha256": "...",
      "size": 1756109
    }
  ],
  "deleted_files": []
}
```

Production URLs must use HTTPS. Install paths are validated as relative Windows paths and reject absolute paths, UNC paths, drive prefixes, `..`, reserved updater staging paths, and destinations outside the application directory.

### Test an update locally

`build-release.ps1 -TestMode` creates a manifest whose assets use local `file:` URLs and carry `"test_only": true`. The compiled updater accepts those URLs only with `--test-mode` and refuses the default or Inno-registered production installation directory unless `--allow-production-install` is given explicitly.

```powershell
.\build-release.ps1 -BuildFirst -TestMode

$testInstall = "$env:TEMP\KoharuUpdateTest"
$runner = "$env:TEMP\KoharuUpdaterRunner"
Remove-Item -Recurse -Force $testInstall, $runner -ErrorAction SilentlyContinue
Copy-Item -Recurse .\dist\ytdlp-onefile $testInstall
Rename-Item "$testInstall\ytdlp-onefile.exe" "Naughty Koharu.exe"
Remove-Item "$testInstall\Naughty Koharu.exe" # Simulate a missing app file.
New-Item -ItemType Directory -Force $runner | Out-Null
Copy-Item "$testInstall\Koharu Updater.exe" $runner

& "$runner\Koharu Updater.exe" `
  --install-dir $testInstall `
  --manifest .\dist-update\Naughty-Koharu-vX.Y.Z\update-manifest.json `
  --test-mode `
  --no-launch
```

Use a disposable directory for local testing. Do not point test mode at a real installation.

### Troubleshooting

- Check `%TEMP%\ytdlp-onefile.log` for `APPUPD` and `UPDATER` entries.
- If a build reports an unresolved Git LFS pointer for `vendor\yt-dlp.exe`, `ffmpeg.exe`, or `ffprobe.exe`, first run `git lfs pull`. If the LFS payload is unavailable, build with `.\build.ps1 -AutoYtDlp -AutoFfmpeg`; it downloads and validates fresh Windows binaries from the existing official upstream sources before packaging.
- GitHub rate limits, no internet, missing release assets, invalid tags/manifests, bad hashes, file locks, permissions, disk space, and failed rollback are reported to the user and logged with detail.
- Close Naughty Koharu and any process using its install folder if Windows reports a locked file.
- If an update cannot complete, run the corresponding full installer from the GitHub Release. It remains the supported recovery path.
- If a release cannot be discovered, confirm it is public, not marked as a prerelease, uses the matching `vX.Y.Z` tag, and contains `update-manifest.json` plus all referenced assets.
