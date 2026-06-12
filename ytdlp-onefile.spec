# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\Beatlxss\\Desktop\\projects\\yt-dlp-integration-Koharu-Edition\\yt-dlp-integration-Koharu-Edition\\main.py'],
    pathex=[],
    binaries=[('C:\\Users\\Beatlxss\\Desktop\\projects\\yt-dlp-integration-Koharu-Edition\\yt-dlp-integration-Koharu-Edition\\vendor\\ffmpeg.exe', '.'), ('C:\\Users\\Beatlxss\\Desktop\\projects\\yt-dlp-integration-Koharu-Edition\\yt-dlp-integration-Koharu-Edition\\vendor\\ffprobe.exe', '.')],
    datas=[('C:\\Users\\Beatlxss\\Desktop\\projects\\yt-dlp-integration-Koharu-Edition\\yt-dlp-integration-Koharu-Edition\\vendor\\app.ico', '.'), ('C:\\Users\\Beatlxss\\Desktop\\projects\\yt-dlp-integration-Koharu-Edition\\yt-dlp-integration-Koharu-Edition\\vendor\\blue-archive-koharu.gif', 'vendor')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['yt_dlp'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ytdlp-onefile',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:\\Users\\Beatlxss\\Desktop\\projects\\yt-dlp-integration-Koharu-Edition\\yt-dlp-integration-Koharu-Edition\\vendor\\app.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ytdlp-onefile',
)
