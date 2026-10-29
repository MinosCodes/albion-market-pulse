; Inno Setup script for the Albion Analyser installer.
; Build with build_installer.bat (it builds the .exe first and passes the version).

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "Albion Analyser"
#define AppExe "Albion Analyser.exe"
#define NpcapUrl "https://npcap.com/#download"
#define WebView2Url "https://developer.microsoft.com/microsoft-edge/webview2/"

[Setup]
; Keep this AppId for every future version so updates replace the old install.
AppId={{6C1E4B5A-3F2D-4E8B-9A71-2B5D8C0F4A19}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Albion Analyser
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
; Installs for the current user by default (no admin prompt); the user can pick
; "all users" in the dialog. The app then keeps its data in %LOCALAPPDATA%.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
SetupIconFile=..\assets\icon.ico
OutputDir=..\dist
OutputBaseFilename=AlbionAnalyser-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Close a running copy (and let it restart) when updating.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\AlbionMarketPulse.exe"; DestDir: "{app}"; DestName: "{#AppExe}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{#NpcapUrl}"; Description: "Open the Npcap download page (needed for the live market sniffer)"; Flags: postinstall shellexec skipifsilent; Check: not NpcapInstalled
Filename: "{#WebView2Url}"; Description: "Open the WebView2 Runtime download page (needed to show the app window)"; Flags: postinstall shellexec skipifsilent; Check: not WebView2Installed
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Close only copies running from this install folder (the app stops its Albion Data
; Client when it exits).
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-Process | Where-Object {{ $_.Path -like '{app}\*' } | Stop-Process -Force"""; Flags: runhidden; RunOnceId: "StopApp"

[UninstallDelete]
; Files the app creates next to itself (per-user installs).
Type: filesandordirs; Name: "{app}\.cache"
Type: filesandordirs; Name: "{app}\albiondata-client"
Type: filesandordirs; Name: "{app}\webview"
Type: files; Name: "{app}\*.log"
Type: files; Name: "{app}\config.json"

[Code]
function NpcapInstalled: Boolean;
begin
  Result := RegKeyExists(HKLM, 'SOFTWARE\Npcap') or RegKeyExists(HKLM, 'SOFTWARE\WOW6432Node\Npcap');
end;

function WebView2Installed: Boolean;
var
  Version: String;
  Key: String;
begin
  Key := 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\' + Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM, 'SOFTWARE\' + Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, 'SOFTWARE\' + Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;
