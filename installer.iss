; Inno Setup script for the Windows installer. Build it with build.ps1, which runs
; PyInstaller first and passes in the version from nup_gui/__init__.py.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "nup tool"
#define AppExe "nup tool.exe"

[Setup]
; Identifies the app across upgrades. Never change it, or upgrades install side by side.
AppId={{6F1D2C3E-8A47-4B5E-9C21-3D7A8E4F1B90}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
; Installs for the current user with no admin prompt. The wizard offers all users too.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=nup-tool-setup-{#AppVersion}
SetupIconFile=nup_gui\assets\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An upgrade may bundle different files, so don't leave the old set behind.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\nup tool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
