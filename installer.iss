; Inno Setup Script for Harvick Farms Video Studio
; Compiles the standalone PyInstaller distribution into an installer wizard

#define MyAppName "Harvick Farms Video Studio"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Harvick Farms"
#define MyAppExeName "HarvickFarmsVideoStudio.exe"

[Setup]
AppId={{E58A4035-71D2-4C3D-9477-9E492D858A9F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Harvick Farms Video Studio
DefaultGroupName=Harvick Farms
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=HarvickFarmsVideoStudio_Setup_v1.0.0
SetupIconFile=assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\HarvickFarmsVideoStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
