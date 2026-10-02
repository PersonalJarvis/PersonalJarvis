; Inno Setup script for the Personal Jarvis Windows installer.
;
; Shape of the install, and why:
;   * PER-USER (PrivilegesRequired=lowest, {localappdata}\Programs). Exactly how
;     Chrome installs: double-click, no UAC prompt, no administrator account
;     needed. It also means an upgrade never has to elevate, which is what makes
;     the in-app "Update Now" button work unattended.
;   * A fixed AppId. Every future release reuses it, so Windows recognises the
;     new setup as the SAME application and upgrades in place instead of leaving
;     two entries in "Installed apps".
;   * CloseApplications=yes. The in-app updater quits the app and runs this
;     file with /SILENT /SUPPRESSMSGBOXES /CLOSEAPPLICATIONS /RELAUNCH=1
;     /WAITPID=<pid> /LOG=<file>, so Setup first waits for the old app to exit
;     (InitializeSetup), Restart Manager closes anything still holding a file,
;     the files are replaced, and the second [Run] entry starts Personal
;     Jarvis again.
;     (/RESTARTAPPLICATIONS would only restart apps registered with
;     RegisterApplicationRestart, which this one is not.)
;   * The uninstaller removes the program directory ONLY. Settings, memory,
;     skills and logs live in %LOCALAPPDATA%\Jarvis and are deliberately kept, so
;     an uninstall/reinstall cycle does not destroy the user's data.
;
; Compile through packaging/windows/build.ps1, which passes the version:
;   ISCC.exe /DAppVersion=1.5.3 /DSourceDir=<dist\Jarvis> /DOutputDir=<out> ...

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\Jarvis"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist\installers"
#endif
#ifndef IconFile
  #define IconFile "..\..\assets\icons\jarvis.ico"
#endif
; NEVER change this default. It is the identity Windows upgrades against. Only
; the installer end-to-end test overrides it (/DAppGuid=...), so its throwaway
; install can never touch, upgrade or uninstall a real Personal Jarvis.
#ifndef AppGuid
  #define AppGuid "7F1C4E42-2E5B-4F0A-9E1B-1A7B6C0D5E88"
#endif

#define AppName "Personal Jarvis"
#define AppPublisher "Personal Jarvis"
#define AppUrl "https://github.com/PersonalJarvis/PersonalJarvis"
#define GuiExeName "PersonalJarvis.exe"
#define CliExeName "jarvis.exe"
#define UserDataDirDisplay "%LOCALAPPDATA%\Jarvis"

[Setup]
; NEVER change AppGuid's default (see its #define above).
AppId={{{#AppGuid}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
VersionInfoVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}
AppUpdatesURL={#AppUrl}

; Per-user install: no administrator prompt, ever.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes

; 64-bit only. There is no 32-bit Python payload in the bundle, and
; x64compatible also covers ARM64 machines running x64 code under emulation.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

OutputDir={#OutputDir}
OutputBaseFilename=PersonalJarvis-Setup-x64
SetupIconFile={#IconFile}
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#GuiExeName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4

; Restart Manager closes the running app instead of failing on locked files.
CloseApplications=yes
RestartApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
; The PATH task edits the user's environment; tell Windows so open shells and
; Explorer pick it up (this is on top of the explicit broadcast in [Code]).
ChangesEnvironment=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "addtopath"; Description: "Add the ""jarvis"" command to PATH (lets you run ""jarvis serve"" in any terminal)"; GroupDescription: "Command line"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#GuiExeName}"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#GuiExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#GuiExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
; The in-app updater's relaunch. A silent upgrade skips the entry above, so
; without this one the app would quit for the update and never come back.
; runasoriginaluser: an all-users install runs elevated, the app must not.
Filename: "{app}\{#GuiExeName}"; WorkingDir: "{app}"; Flags: nowait runasoriginaluser; Check: RelaunchRequested

[Messages]
ConfirmUninstall=Do you really want to remove %1?%n%nYour settings, memory, skills and logs are NOT deleted - they stay in {#UserDataDirDisplay}.

[Code]
const
  { Prefixed on purpose: Inno Setup's Pascal Script already declares some of
    these Win32 names, and redeclaring one aborts the compile. }
  EnvironmentKey = 'Environment';
  JarvisHwndBroadcast = $FFFF;
  JarvisWmSettingChange = $001A;
  JarvisSmtoAbortIfHung = $0002;

  JarvisSynchronize = $00100000;
  { How long Setup waits for the app it replaces to exit (milliseconds). }
  JarvisWaitForAppMs = 120000;

function SendMessageTimeout(hWnd: Longint; Msg: Cardinal; wParam: Longint;
  lParam: string; fuFlags: Cardinal; uTimeout: Cardinal;
  var lpdwResult: Cardinal): Longint;
  external 'SendMessageTimeoutW@user32.dll stdcall';

function JarvisOpenProcess(dwDesiredAccess: Cardinal; bInheritHandle: BOOL;
  dwProcessId: Cardinal): THandle;
  external 'OpenProcess@kernel32.dll stdcall';

function JarvisWaitForSingleObject(hHandle: THandle; dwMilliseconds: Cardinal): Cardinal;
  external 'WaitForSingleObject@kernel32.dll stdcall';

function JarvisCloseHandle(hObject: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

{ The in-app updater passes /WAITPID=<pid of the running app> and quits right
  after starting this file. Setup starts faster than the app shuts down, and a
  silent install that meets files still in use answers with Abort - the app
  gone, nothing installed, nothing relaunched. So the first thing Setup does is
  wait for that process to end. A pid that no longer exists (already exited)
  opens no handle and costs nothing; a normal interactive install passes no
  /WAITPID at all. }
procedure WaitForReplacedApp;
var
  Pid: Integer;
  Handle: THandle;
begin
  Pid := StrToIntDef(ExpandConstant('{param:WAITPID|0}'), 0);
  if Pid <= 0 then
    Exit;
  Handle := JarvisOpenProcess(JarvisSynchronize, False, Pid);
  if Handle = 0 then
    Exit;
  Log(Format('Waiting up to %d s for process %d to exit.', [JarvisWaitForAppMs div 1000, Pid]));
  JarvisWaitForSingleObject(Handle, JarvisWaitForAppMs);
  JarvisCloseHandle(Handle);
end;

function InitializeSetup: Boolean;
begin
  WaitForReplacedApp;
  Result := True;
end;

{ Windows only re-reads the Environment key when it is told to. Without this
  broadcast the new PATH is invisible until the user signs out and back in. }
procedure BroadcastEnvironmentChange;
var
  Unused: Cardinal;
begin
  SendMessageTimeout(JarvisHwndBroadcast, JarvisWmSettingChange, 0, 'Environment',
    JarvisSmtoAbortIfHung, 5000, Unused);
end;

function NormalizePathEntry(const Value: string): string;
begin
  Result := Lowercase(RemoveBackslashUnlessRoot(Trim(Value)));
end;

{ True when Dir is already one of the semicolon-separated entries of Path.
  Compared entry by entry (not as a substring) so ...\Personal Jarvis Beta
  never counts as ...\Personal Jarvis. }
function PathContainsDir(const Path, Dir: string): Boolean;
var
  Remaining, Entry, Wanted: string;
  Separator: Integer;
begin
  Result := False;
  Wanted := NormalizePathEntry(Dir);
  Remaining := Path;
  while Remaining <> '' do
  begin
    Separator := Pos(';', Remaining);
    if Separator > 0 then
    begin
      Entry := Copy(Remaining, 1, Separator - 1);
      Remaining := Copy(Remaining, Separator + 1, Length(Remaining) - Separator);
    end
    else
    begin
      Entry := Remaining;
      Remaining := '';
    end;
    if NormalizePathEntry(Entry) = Wanted then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

function ReadUserPath(var Path: string): Boolean;
begin
  Result := RegQueryStringValue(HKCU, EnvironmentKey, 'Path', Path);
  if not Result then
    Path := '';
end;

procedure AddDirToUserPath(const Dir: string);
var
  Path: string;
begin
  ReadUserPath(Path);
  { Idempotent: repeated upgrades must not grow PATH by one copy each time. }
  if PathContainsDir(Path, Dir) then
    Exit;
  if (Path <> '') and (Copy(Path, Length(Path), 1) <> ';') then
    Path := Path + ';';
  Path := Path + Dir;
  if RegWriteExpandStringValue(HKCU, EnvironmentKey, 'Path', Path) then
    BroadcastEnvironmentChange;
end;

procedure RemoveDirFromUserPath(const Dir: string);
var
  Path, Rebuilt, Entry, Remaining, Wanted: string;
  Separator: Integer;
  Changed: Boolean;
begin
  if not ReadUserPath(Path) then
    Exit;
  Wanted := NormalizePathEntry(Dir);
  Rebuilt := '';
  Remaining := Path;
  Changed := False;
  while Remaining <> '' do
  begin
    Separator := Pos(';', Remaining);
    if Separator > 0 then
    begin
      Entry := Copy(Remaining, 1, Separator - 1);
      Remaining := Copy(Remaining, Separator + 1, Length(Remaining) - Separator);
    end
    else
    begin
      Entry := Remaining;
      Remaining := '';
    end;
    if NormalizePathEntry(Entry) = Wanted then
      Changed := True
    else if Trim(Entry) <> '' then
    begin
      if Rebuilt <> '' then
        Rebuilt := Rebuilt + ';';
      Rebuilt := Rebuilt + Entry;
    end;
  end;
  if not Changed then
    Exit;
  if Rebuilt = '' then
    RegDeleteValue(HKCU, EnvironmentKey, 'Path')
  else
    RegWriteExpandStringValue(HKCU, EnvironmentKey, 'Path', Rebuilt);
  BroadcastEnvironmentChange;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('addtopath') then
      AddDirToUserPath(ExpandConstant('{app}'))
    else
      { An upgrade where the user cleared the task must also take the entry
        back out, or the choice silently does nothing. }
      RemoveDirFromUserPath(ExpandConstant('{app}'));
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveDirFromUserPath(ExpandConstant('{app}'));
end;

{ True when the in-app updater asked for the app to come back (see the [Run]
  section). Updaters up to v2.4.x passed /RESTARTAPPLICATIONS instead of
  /RELAUNCH=1 and nothing relaunched the app; honouring that flag too means
  an install updating FROM one of those versions also comes back. }
function RelaunchRequested: Boolean;
var
  I: Integer;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
  if Result then
    Exit;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/RESTARTAPPLICATIONS') = 0 then
    begin
      Result := True;
      Exit;
    end;
end;
