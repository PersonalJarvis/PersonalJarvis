; Personal Jarvis — Windows installer (Inno Setup).
;
; Wraps the PyInstaller onedir bundle (dist\Jarvis\) into a double-click
; Setup.exe that installs into Program Files, creates Start-Menu and optional
; desktop shortcuts, and offers to launch the app at the end.
;
; Build (locally or in CI, after build.bat has produced dist\Jarvis\):
;   iscc /DAppVersion=0.1.0 packaging\windows\jarvis.iss
; Output: packaging\windows\Output\PersonalJarvis-Setup-<version>.exe
;
; The Inno Setup compiler (iscc.exe) is installed on the CI runner via
; chocolatey (`choco install innosetup`); see .github/workflows/build-app.yml.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

#define AppName "Personal Jarvis"
#define AppPublisher "Personal Jarvis Maintainers"
#define AppExeName "Jarvis.exe"
; Stable upgrade GUID — keep constant across releases so updates replace cleanly.
#define AppId "{{8B2F1E54-9C3A-4D77-B1E6-3A9D2C7F4E10}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install needs no elevation (asInvoker), matching the app's UAC model.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=PersonalJarvis-Setup-{#AppVersion}
SetupIconFile=..\..\assets\icons\jarvis.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The entire PyInstaller onedir bundle.
Source: "..\..\dist\Jarvis\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
