; 巨管软件 - Inno Setup 客户安装包（原生 Windows，无需 Docker）

#ifndef AppVersion
  #define AppVersion "2.0.0.8"
#endif

#ifndef AppPublisher
  #define AppPublisher "自行车"
#endif

#ifndef NativePkgRoot
  #define NativePkgRoot "..\..\dist\giant-tube-app-native"
#endif

#define AppName "自行车抽管软件"
#define AppId "{{A7B3C9D1-8E2F-4A5B-9C6D-1E2F3A4B5C6D}"
#define AppIcon "assets\app_icon.ico"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\GiantTubeApp
DefaultGroupName={#AppName}
DisableProgramGroupPage=no
OutputDir=..\..\dist
OutputBaseFilename=GiantTubeAppSetup_{#AppVersion}
SetupIconFile=..\..\deploy\windows\{#AppIcon}
UninstallDisplayIcon={app}\{#AppIcon}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
DiskSpanning=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcut"; GroupDescription: "Additional options:"
Name: "launchafter"; Description: "Launch after install"; GroupDescription: "Additional options:"; Flags: checkedonce

[Files]
Source: "{#NativePkgRoot}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

; ── 关键：Program Files 下的目录默认 Users 只读，SQLite/日志都写不进去。
;    这里对所有运行时需要写入的目录显式声明 users-modify（该权限会被子文件继承）。
;    install.ps1 里还会用 icacls /T 递归一次，双保险覆盖已存在的 mold 模板 .db。
[Dirs]
Name: "{app}\license"; Permissions: users-modify
Name: "{app}\entity_out"; Permissions: users-modify
Name: "{app}\freecad_user"; Permissions: users-modify
Name: "{app}\gnt管件\uploads"; Permissions: users-modify
Name: "{app}\backend\data"; Permissions: users-modify
Name: "{app}\backend\data\logs"; Permissions: users-modify
Name: "{app}\backend\database"; Permissions: users-modify
Name: "{app}\backend\database\pp"; Permissions: users-modify
Name: "{app}\backend\database\normal-mold"; Permissions: users-modify
Name: "{app}\backend\database\tp-mold"; Permissions: users-modify
Name: "{app}\backend\database\taper-mold"; Permissions: users-modify
Name: "{app}\backend\database\user"; Permissions: users-modify

; 安装前清理旧激活/许可证数据，确保客户装完后是"未激活"状态。
; 这样即便老用户在同目录升级，也会重新走激活流程。
[InstallDelete]
Type: files; Name: "{app}\license\license.dat"
Type: files; Name: "{app}\backend\static\license.dat"
Type: files; Name: "{app}\backend\static\license.json"

; 卸载时清理许可证（用户数据除外）
[UninstallDelete]
Type: files; Name: "{app}\license\license.dat"

[Icons]
Name: "{group}\启动软件"; Filename: "{app}\GiantTubeApp.exe"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppIcon}"
Name: "{group}\停止软件"; Filename: "{app}\stop.bat"; WorkingDir: "{app}"
Name: "{group}\打开浏览器"; Filename: "http://127.0.0.1:5890"; IconFilename: "{app}\{#AppIcon}"
Name: "{group}\查看本机机器码"; Filename: "{app}\tools\gt-machine-code.exe"; Check: FileExists(ExpandConstant('{app}\tools\gt-machine-code.exe'))
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\GiantTubeApp.exe"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppIcon}"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; \
  Parameters: "-ExecutionPolicy Bypass -NoProfile -File ""{app}\install.ps1"""; \
  WorkingDir: "{app}"; \
  StatusMsg: "Initializing..."; \
  Flags: runhidden waituntilterminated
Filename: "{app}\GiantTubeApp.exe"; \
  Description: "Launch application"; \
  WorkingDir: "{app}"; \
  StatusMsg: "Starting..."; \
  Flags: postinstall nowait skipifsilent; \
  Tasks: launchafter

; 卸载前必须先杀所有本安装目录下的 pythonw/GiantTubeApp，
; 否则文件被占用会让 Inno Setup 无限卡住进度条。
; 使用专用 uninstall_kill.bat（不含 pause，5 秒内退出）。
[UninstallRun]
Filename: "{app}\uninstall_kill.bat"; \
  WorkingDir: "{app}"; \
  Flags: runhidden waituntilterminated; \
  RunOnceId: "StopApp"
