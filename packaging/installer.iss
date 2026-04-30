#define MyAppName "Video Rotation Saver"
#define MyAppExeName "VideoRotationSaver.exe"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Video Rotation Saver"

[Setup]
AppId={{B02AA3E3-91F9-4D2A-9E81-2DFBD8A0E66F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=VideoRotationSaver-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\build\assets\app.ico
WizardImageFile=..\build\assets\wizard-image.bmp
WizardSmallImageFile=..\build\assets\wizard-small-image.bmp
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "Run {#MyAppName} when Windows starts"; GroupDescription: "Startup options:"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\config.sample.ini"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\assets\app-icon.png"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "..\build\assets\installer-banner.png"; DestDir: "{app}\assets"; Flags: ignoreversion

[Dirs]
Name: "{userappdata}\VideoRotationSaver"
Name: "{localappdata}\VideoRotationSaver"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\View log"; Filename: "{localappdata}\VideoRotationSaver\log.txt"; Check: LogFileExists
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "VideoRotationSaver"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: startup; Flags: uninsdeletevalue

[INI]
Filename: "{userappdata}\VideoRotationSaver\config.ini"; Section: "potplayer"; Key: "potplayer_path"; String: "{code:GetPotPlayerPath}"
Filename: "{userappdata}\VideoRotationSaver\config.ini"; Section: "ffmpeg"; Key: "ffmpeg_path"; String: "ffmpeg"
Filename: "{userappdata}\VideoRotationSaver\config.ini"; Section: "ffmpeg"; Key: "ffprobe_path"; String: "ffprobe"
Filename: "{userappdata}\VideoRotationSaver\config.ini"; Section: "ffmpeg"; Key: "mkvpropedit_path"; String: "mkvpropedit"
Filename: "{userappdata}\VideoRotationSaver\config.ini"; Section: "safety"; Key: "backup_behavior"; String: "keep_until_next_run"
Filename: "{userappdata}\VideoRotationSaver\config.ini"; Section: "ui"; Key: "popup_position"; String: "auto"
Filename: "{userappdata}\VideoRotationSaver\config.ini"; Section: "ui"; Key: "rotation_hotkey"; String: "{code:GetRotationHotkey}"
Filename: "{userappdata}\VideoRotationSaver\config.ini"; Section: "ui"; Key: "rename_hotkey"; String: "{code:GetRenameHotkey}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  PotPlayerPage: TInputFileWizardPage;
  HotkeyPage: TInputQueryWizardPage;

function FirstExistingPath(Param: String): String;
begin
  Result := '';
  if FileExists(ExpandConstant('{pf}\DAUM\PotPlayer\PotPlayerMini64.exe')) then
    Result := ExpandConstant('{pf}\DAUM\PotPlayer\PotPlayerMini64.exe')
  else if FileExists(ExpandConstant('{pf32}\DAUM\PotPlayer\PotPlayerMini64.exe')) then
    Result := ExpandConstant('{pf32}\DAUM\PotPlayer\PotPlayerMini64.exe')
  else if FileExists(ExpandConstant('{pf}\DAUM\PotPlayer\PotPlayerMini.exe')) then
    Result := ExpandConstant('{pf}\DAUM\PotPlayer\PotPlayerMini.exe')
  else if FileExists(ExpandConstant('{pf32}\DAUM\PotPlayer\PotPlayerMini.exe')) then
    Result := ExpandConstant('{pf32}\DAUM\PotPlayer\PotPlayerMini.exe');
end;

procedure InitializeWizard;
begin
  PotPlayerPage := CreateInputFilePage(
    wpSelectDir,
    'Locate PotPlayer',
    'Choose the PotPlayer executable if it was not detected automatically.',
    'Video Rotation Saver needs PotPlayerMini64.exe or PotPlayerMini.exe to reopen videos after rotation.'
  );
  PotPlayerPage.Add('PotPlayer executable:', 'Executable files|*.exe|All files|*.*', '.exe');
  PotPlayerPage.Values[0] := FirstExistingPath('');

  HotkeyPage := CreateInputQueryPage(
    PotPlayerPage.ID,
    'Choose hotkeys',
    'Set the keyboard shortcuts Video Rotation Saver should claim.',
    'Use modifier-based hotkeys to avoid conflicts with Windows and other apps. You can change these later from the tray menu.'
  );
  HotkeyPage.Add('Rotate current video:', False);
  HotkeyPage.Add('Rename current video:', False);
  HotkeyPage.Values[0] := 'ctrl+alt+numpad 2';
  HotkeyPage.Values[1] := 'ctrl+alt+numpad 4';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = PotPlayerPage.ID then
  begin
    if (Trim(PotPlayerPage.Values[0]) <> '') and not FileExists(PotPlayerPage.Values[0]) then
    begin
      MsgBox('The selected PotPlayer executable was not found. Choose a valid file, or leave the field blank and configure it later.', mbError, MB_OK);
      Result := False;
    end;
  end;
  if CurPageID = HotkeyPage.ID then
  begin
    if Trim(HotkeyPage.Values[0]) = '' then
    begin
      MsgBox('Choose a rotation hotkey.', mbError, MB_OK);
      Result := False;
    end
    else if Trim(HotkeyPage.Values[1]) = '' then
    begin
      MsgBox('Choose a rename hotkey.', mbError, MB_OK);
      Result := False;
    end
    else if CompareText(Trim(HotkeyPage.Values[0]), Trim(HotkeyPage.Values[1])) = 0 then
    begin
      MsgBox('Rotation and rename need different hotkeys.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function GetPotPlayerPath(Param: String): String;
begin
  Result := PotPlayerPage.Values[0];
end;

function GetRotationHotkey(Param: String): String;
begin
  Result := Trim(HotkeyPage.Values[0]);
end;

function GetRenameHotkey(Param: String): String;
begin
  Result := Trim(HotkeyPage.Values[1]);
end;

function LogFileExists: Boolean;
begin
  Result := FileExists(ExpandConstant('{localappdata}\VideoRotationSaver\log.txt'));
end;
