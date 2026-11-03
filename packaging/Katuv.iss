#ifndef ReleaseVersion
  #error ReleaseVersion is required
#endif
#ifndef PayloadDir
  #error PayloadDir is required
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{94F202D7-E65B-4350-AE91-38010D7FB26B}
AppName=כתוב
AppVersion={#ReleaseVersion}
AppPublisher=Dvir Cabessa
AppPublisherURL=https://github.com/dvicabi/katuv
AppSupportURL=https://github.com/dvicabi/katuv/issues
DefaultDirName={localappdata}\Katuv
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
DisableDirPage=yes
DisableProgramGroupPage=yes
UninstallDisplayName=כתוב
OutputDir={#OutputDir}
#ifdef SignedRelease
OutputBaseFilename=Katuv-{#ReleaseVersion}-Windows
SignTool=katuv
SignedUninstaller=yes
#else
OutputBaseFilename=Katuv-{#ReleaseVersion}-Windows-UNSIGNED
SignedUninstaller=no
#endif
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
SetupLogging=yes
LicenseFile={#PayloadDir}\LICENSE.txt

[Languages]
Name: "hebrew"; MessagesFile: "compiler:Languages\Hebrew.isl"

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{tmp}\katuv-payload"; Flags: dontcopy
Source: "{#PayloadDir}\START-HERE.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\THIRD_PARTY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[UninstallDelete]
Type: files; Name: "{app}\Start-Katuv.ps1"
Type: files; Name: "{app}\Katuv.Common.ps1"
Type: files; Name: "{app}\Uninstall-Katuv.ps1"

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File ""{app}\Start-Katuv.ps1"""; Description: "פתיחת כתוב"; Flags: postinstall nowait skipifsilent

[Code]
var
  PreparationPage: TOutputProgressWizardPage;
  InstallationError: String;

procedure InitializeWizard;
begin
  PreparationPage := CreateOutputProgressPage('מכינים את כתוב', 'ההתקנה הראשונה דורשת אינטרנט ולפחות 10 GB פנויים.');
end;

procedure InstallationOutput(const S: String; const Error, FirstLine: Boolean);
var
  Tail: String;
  Separator, Percent: Integer;
begin
  Log(S);
  if Pos('KATUV_ERROR|', S) = 1 then
    InstallationError := Copy(S, 13, Length(S));
  if Pos('KATUV_PROGRESS|', S) = 1 then begin
    Tail := Copy(S, 16, Length(S));
    Separator := Pos('|', Tail);
    if Separator > 0 then begin
      Percent := StrToIntDef(Copy(Tail, 1, Separator - 1), 0);
      PreparationPage.SetText(Copy(Tail, Separator + 1, Length(Tail)), 'הפרויקטים הקיימים נשמרים.');
      PreparationPage.SetProgress(Percent, 100);
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
  PowerShell, Params: String;
begin
  Result := '';
  InstallationError := '';
  ExtractTemporaryFiles('{tmp}\katuv-payload\*');
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\katuv-payload\Install-Katuv.ps1') +
    '" -InstallRoot "' + ExpandConstant('{app}') + '" -NoLaunch';
  PreparationPage.Show;
  try
    if not ExecAndLogOutput(PowerShell, Params, '', SW_SHOWNORMAL, ewWaitUntilTerminated, ExitCode, @InstallationOutput) or (ExitCode <> 0) then begin
      if InstallationError = '' then InstallationError := 'ההתקנה לא הושלמה. בדקו אינטרנט ומקום פנוי, ונסו שוב.';
      Result := InstallationError + #13#10 + ExpandConstant('יומן: {app}\installation.log');
    end;
  finally
    PreparationPage.Hide;
  end;
end;

function RunUninstallHelper(const CheckOnly: Boolean): Boolean;
var
  ExitCode: Integer;
  Params: String;
begin
  Params := '-NoProfile -ExecutionPolicy RemoteSigned -File "' + ExpandConstant('{app}\Uninstall-Katuv.ps1') + '"';
  if CheckOnly then Params := Params + ' -CheckOnly';
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode) and (ExitCode = 0);
  if not Result then
    MsgBox('לא ניתן להסיר כעת. המתינו לסיום עיבוד סרטון או הכנת מודלים, וסגרו מתקינים פתוחים. הפרויקטים נשמרו.', mbError, MB_OK);
end;

function InitializeUninstall: Boolean;
begin
  { Initial checks must not remove anything before the confirmation dialog. }
  Result := RunUninstallHelper(True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { Recheck and remove only after the user confirms; Abort prevents registry/file cleanup on failure. }
  if CurUninstallStep = usUninstall then
    if not RunUninstallHelper(False) then Abort;
end;
