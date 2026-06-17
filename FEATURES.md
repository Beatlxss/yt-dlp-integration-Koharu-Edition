# One-file EXE (external yt-dlp.exe + ffmpeg)

This folder builds a Windows executable that **calls an external** `yt-dlp.exe` (shipped next to the app) and uses a bundled `ffmpeg.exe` for merging/converting.

Because `yt-dlp.exe` is a separate file, you can update it later by replacing the file in the install folder — no need to rebuild or reinstall the app.

## 1) Prereqs

- Windows
- Python 3.8+ available on PATH (or pass `-Python` to the build script)
- `ffmpeg.exe` (required) and `ffprobe.exe` (optional) placed in `onefile/vendor/`
- `yt-dlp.exe` placed in `onefile/vendor/` (or let the build script download it with `-AutoYtDlp`)

## 2) Put FFmpeg binaries here

Copy these files into `onefile/vendor/`:

- `ffmpeg.exe` (required)
- `ffprobe.exe` (optional but recommended)

If your FFmpeg build also includes DLLs (e.g. `avcodec-*.dll`), copy all those `*.dll` files into `onefile/vendor/` too.

## 3) Build

From PowerShell:

Recommended (folder layout like your screenshot):

```powershell
cd "C:\Users\Beatlxss\Desktop\projects\YT donwload app\onefile"
.\build.ps1 -Clean -Mode onedir
```

If you don't want to manually install FFmpeg, let the script download a static FFmpeg build automatically:

```powershell
.\build.ps1 -Clean -Mode onedir -AutoFfmpeg
```

If you don't want to manually download `yt-dlp.exe`, let the script download it automatically:

```powershell
.\build.ps1 -Clean -Mode onedir -AutoYtDlp
```

Optional (single EXE, self-extracting; sometimes trickier with FFmpeg DLLs):

```powershell
.\build.ps1 -Clean -Mode onefile
```

Output:

- `onedir`: `onefile/dist/ytdlp-onefile/ytdlp-onefile.exe` (plus bundled files next to it)
- `onefile`: `onefile/dist/ytdlp-onefile.exe`

## 4) Run

```powershell
# onedir
.\dist\ytdlp-onefile\ytdlp-onefile.exe

# onefile
.\dist\ytdlp-onefile.exe
```

## Notes

- The produced file is a **single** `.exe`, but PyInstaller "onefile" apps extract their embedded files to a temp directory at runtime.
- `yt-dlp.exe` is **not** embedded into the app exe; it is placed next to it (so you can swap it out to update).
- If you redistribute the resulting exe, ensure you comply with FFmpeg and yt-dlp licensing terms (LGPL/GPL details depend on the FFmpeg build you bundle).
