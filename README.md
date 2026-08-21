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
