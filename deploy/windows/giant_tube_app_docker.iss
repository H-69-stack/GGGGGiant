; 巨管软件 - Inno Setup（Docker 离线包，开发/内部用）
; 构建：powershell -File deploy\make_windows_setup.ps1

#ifndef AppVersion
  #define AppVersion "1.0.2"
#endif

#ifndef AppPublisher
  #define AppPublisher "捷安特"
#endif

#ifndef OfflinePkgRoot
  #define OfflinePkgRoot "..\..\dist\giant-tube-app-win"
#endif

#define AppName "捷安特抽管软件 (Docker)"
#define AppId "{{B8C4D0E2-9F3G-5B6C-0D7E-2F3G4B5C6D7E}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
OutputDir=..\..\dist
OutputBaseFilename=GiantTubeAppSetup_Docker_{#AppVersion}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin

[Files]
Source: "{#OfflinePkgRoot}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Run]
Filename: "powershell.exe"; \
  Parameters: "-ExecutionPolicy Bypass -NoProfile -File ""{app}\install.ps1"""; \
  WorkingDir: "{app}"; \
  Flags: runhidden waituntilterminated

[Code]
function InitializeSetup(): Boolean;
begin
  Result := DirExists(ExpandConstant('{#OfflinePkgRoot}'));
end;
