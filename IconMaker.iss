#define AppVersion "1.0.1"

[Setup]
AppId={{05A985C7-DBA1-447B-B28A-90EF582B82C5}
AppName=IconMaker
AppVersion={#AppVersion}
AppPublisher=InfiniWorks
DefaultDirName={localappdata}\Programs\IconMaker
DefaultGroupName=IconMaker
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
OutputDir=build\release-installer
OutputBaseFilename=IconMaker-Setup-{#AppVersion}
SetupIconFile=assets\iconner.ico
UninstallDisplayIcon={app}\IconMaker.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "build\release-dist\IconMaker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\IconMaker"; Filename: "{app}\IconMaker.exe"
Name: "{autodesktop}\IconMaker"; Filename: "{app}\IconMaker.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\IconMaker.exe"; Description: "Launch IconMaker"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Command: String;
  Executable: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;

  Executable := '"' + ExpandConstant('{app}\IconMaker.exe') + '"';
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run',
    'IconMaker', Command) and
    (Pos(Lowercase(Executable), Lowercase(Command)) = 1) then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'IconMaker');
end;
