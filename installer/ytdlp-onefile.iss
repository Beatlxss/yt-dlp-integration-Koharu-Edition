#define MyAppName "Naughty Koharu"
#define MyAppPublisher "Beatlxss"
#define MyAppDescription "Video/music downloader"
#define MyAppExeName "Naughty Koharu.exe"
#define MyAppId "{{9A7A2F8E-9A3C-4F76-9E7E-2F2C7A7C37E1}}"
#define MyAppIdPlain "{9A7A2F8E-9A3C-4F76-9E7E-2F2C7A7C37E1}"
#ifndef MyAppVersion
  #define MyAppVersion "1.3.0"
#endif

[Setup]
AppId={#MyAppId}
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
OutputBaseFilename=Naughty-Koharu-v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=classic
DisableWelcomePage=no
PrivilegesRequired=lowest
WizardImageFile=illust_94913899_20221010_074001.bmp

; Use the provided icon if present (ok if missing during authoring).
SetupIconFile=..\vendor\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

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

[Code]
var
  InstalledVersion: String;

function GetInstalledVersion(): String;
var
  Ver: String;
begin
  Result := '';
  if RegQueryStringValue(
      HKCU,
      'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppIdPlain}_is1',
      'DisplayVersion',
      Ver
    ) then
  begin
    Result := Trim(Ver);
  end;
end;

function InitializeSetup(): Boolean;
begin
  InstalledVersion := GetInstalledVersion();
  if InstalledVersion <> '' then
    Log(Format('{#MyAppName}: detected installed version %s', [InstalledVersion]))
  else
    Log('{#MyAppName}: no previous version detected');

  Result := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  if (CurPageID = wpReady) and (InstalledVersion <> '') and (not WizardSilent) then
  begin
    Result := MsgBox(
      ExpandConstant(
        '{#MyAppName} version ' + InstalledVersion +
        ' is already installed.'#13#10#13#10 +
        'Click Yes to upgrade to version {#MyAppVersion}.'#13#10 +
        'Click No to cancel setup.'
      ),
      mbConfirmation,
      MB_YESNO
    ) = IDYES;
  end;
end;
