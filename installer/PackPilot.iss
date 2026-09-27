; PackPilot 1.0.0 Inno Setup 配置（当前用户安装，无需管理员权限）
; 版本号来自 installer/version.iss（由 scripts/build_installer.ps1 从 app/version.py 生成）

#include "version.iss"

#define MyAppName "PackPilot"
#define MyAppPublisher "PackPilot contributors"
#define MyAppExeName "PackPilot.exe"
#define MySourceDir "..\dist\PackPilot"

[Setup]
AppId={{6D2A9C3E-6F51-4E3F-9C9B-5F5D8B2E9A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename={#MyAppName}-{#MyAppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\resources\icons\packpilot.ico
LicenseFile=..\LICENSE
InfoAfterFile=..\README.md
MinVersion=10.0
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："
Name: "associations"; Description: "关联压缩包格式（ZIP、7Z、TAR 等，仅当前用户）"; GroupDescription: "系统集成："
Name: "contextmenu"; Description: "添加资源管理器右键菜单"; GroupDescription: "系统集成："

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--install-associations"; StatusMsg: "正在注册文件关联与右键菜单…"; Flags: runhidden; Tasks: associations
Filename: "{app}\{#MyAppExeName}"; Parameters: "--install-context-menu"; StatusMsg: "正在添加右键菜单…"; Flags: runhidden; Tasks: contextmenu
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--remove-associations"; Flags: runhidden; RunOnceId: "RemoveAssociations"
Filename: "{app}\{#MyAppExeName}"; Parameters: "--remove-context-menu"; Flags: runhidden; RunOnceId: "RemoveContextMenu"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
