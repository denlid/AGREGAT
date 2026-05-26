[Setup]
AppId={{5D2F6B8A-1C9E-434B-9A4B-3D4F5A6B7C8D}
AppName=AGREGAT Platform (Beta)
AppVersion=7.7.8
AppPublisher=AGREGAT LLC
DefaultDirName={localappdata}\Programs\AGREGAT
DisableDirPage=no
UsePreviousAppDir=yes
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\app.exe
OutputDir=dist\setup
OutputBaseFilename=AGREGAT_Beta_Installer
SetupIconFile=assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
DisableReadyPage=no
AlwaysShowDirOnReadyPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\app.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\AGREGAT"; Filename: "{app}\app.exe"; IconFilename: "{app}\app.exe"
Name: "{autodesktop}\AGREGAT"; Filename: "{app}\app.exe"; IconFilename: "{app}\app.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\app.exe"; Description: "{cm:LaunchProgram,AGREGAT}"; Flags: nowait postinstall skipifsilent

[Code]
//
