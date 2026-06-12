#define MyAppName "Naughty Koharu"
#define MyAppPublisher "Beatlxss"
#define MyAppDescription "Video/music downloader"
#define MyAppExeName "Naughty Koharu.exe"
#ifndef MyAppVersion
  #define MyAppVersion "1.3.0"
#endif

[Setup]
AppId={{9A7A2F8E-9A3C-4F76-9E7E-2F2C7A7C37E1}}
AppName={#MyAppName}
AppVerName={#MyAppName} {#MyAppVersion}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppComments={#MyAppDescription}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableDirPage=no
DisableProgramGroupPage=yes
OutputDir=..\dist-installer
OutputBaseFilename=Naughty-Koharu
Compression=lzma2
SolidCompression=yes
WizardStyle=classic
DisableWelcomePage=no
PrivilegesRequired=lowest
WizardImageFile=illust_94913899_20221010_074001.bmp

; Use the provided icon if present (ok if missing during authoring).
SetupIconFile=..\vendor\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Install the main exe under the branded filename.
Source: "..\dist\ytdlp-onefile\ytdlp-onefile.exe"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion
; Install yt-dlp.exe as a separate file so users can drop-in update it later.
; onlyifdoesntexist prevents overwriting a user-updated yt-dlp.exe during app upgrades.
Source: "..\dist\ytdlp-onefile\yt-dlp.exe"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
; Copy remaining onedir files/folders (including _internal/), excluding original exe name.
Source: "..\dist\ytdlp-onefile\*"; DestDir: "{app}"; Excludes: "ytdlp-onefile.exe,yt-dlp.exe"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"

[Run]
; Optional: let user start after install.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
