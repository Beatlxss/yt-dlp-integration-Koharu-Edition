Put these files in this folder before building:

- ffmpeg.exe (required)
- ffprobe.exe (optional but recommended)
- app.ico (optional) - used as the EXE icon

Important:
Some Windows FFmpeg downloads are "shared" builds. In that case, `ffmpeg.exe` requires extra DLLs next to it.
If you see errors like:

- avcodec-60.dll was not found
- avdevice-60.dll was not found
- avformat-60.dll was not found
- avfilter-9.dll was not found

Then copy ALL `*.dll` files that came with your FFmpeg package (usually in its `bin\` folder) into this `vendor\` folder too, then rebuild.

Alternative:
Use a "static" FFmpeg build (ffmpeg.exe + ffprobe.exe only), which does not require those extra DLLs.

You can use a prebuilt Windows FFmpeg build, or build FFmpeg yourself.

Licensing note:
If you redistribute the resulting exe, you must comply with FFmpeg's license (LGPL/GPL depending on build) and yt-dlp's license. Make sure the FFmpeg build you use matches your intended distribution and that you provide required notices/source offers when applicable.
