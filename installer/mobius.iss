; Möbius 安装包（P6 v0.1）
; 构建：D:\AI\InnoSetup\ISCC.exe installer\mobius.iss
; 产物：installer\dist\MobiusSetup-0.1.0.exe

#define AppName "Mobius"
#define AppVersion "0.2.0"
#define AppPublisher "Mobius"
#define SourceRoot ".."

[Setup]
AppId={{8F3C2A71-4D2B-4B7E-9C1A-6E5D7A1B2C31}
AppName=Möbius
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName=D:\AI\Mobius
DefaultGroupName=Möbius
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist
OutputBaseFilename=MobiusSetup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=Möbius
LicenseFile={#SourceRoot}\LICENSE
SetupIconFile={#SourceRoot}\launcher\mobius.ico
; 模型约 50GB，不打进安装包；安装后由 preflight 检查并提示路径
InfoBeforeFile=安装说明.txt

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: checkedonce

[Files]
; 启动器
Source: "{#SourceRoot}\launcher\dist\Mobius.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\launcher\dist\MobiusCLI.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\launcher\mobius.ico"; DestDir: "{app}\launcher"; Flags: ignoreversion
Source: "{#SourceRoot}\launcher\README.md"; DestDir: "{app}\launcher"; Flags: ignoreversion
; Core / 网关 / 运维脚本 / 配置
Source: "{#SourceRoot}\core\*"; DestDir: "{app}\core"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,*.pyc,console\_mock\screenshots,*_mock.json"
Source: "{#SourceRoot}\media_gateway\*"; DestDir: "{app}\media_gateway"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,tts_output,data,logs,*.db,*.db-*"
Source: "{#SourceRoot}\ops\*"; DestDir: "{app}\ops"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,*.pyc,l2\requirements\*.txt,voice_eval,*.wav,*.mp3"
Source: "{#SourceRoot}\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\data\characters\*"; DestDir: "{app}\data\characters"; Flags: ignoreversion
Source: "{#SourceRoot}\docs\L2-PLAN.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "{#SourceRoot}\docs\L3-PLAN.md"; DestDir: "{app}\docs"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#SourceRoot}\docs\L3-LICENSES.md"; DestDir: "{app}\docs"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#SourceRoot}\docs\L3-COMPONENTS.md"; DestDir: "{app}\docs"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#SourceRoot}\ops\l3\downloader.py"; DestDir: "{app}\ops\l3"; Flags: ignoreversion
Source: "{#SourceRoot}\ops\l3\packs\*.json"; DestDir: "{app}\ops\l3\packs"; Flags: ignoreversion
Source: "{#SourceRoot}\ops\l3\licenses\THIRD-PARTY.md"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "{#SourceRoot}\ops\l3\licenses\licenses.json"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "{#SourceRoot}\ops\l3\licenses\texts\*"; DestDir: "{app}\licenses\texts"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\config.example.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\docs\STATUS.md"; DestDir: "{app}\docs"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#SourceRoot}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
; 环境检查脚本
Source: "preflight.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Möbius 启动器"; Filename: "{app}\Mobius.exe"; WorkingDir: "{app}"; IconFilename: "{app}\launcher\mobius.ico"
Name: "{group}\Möbius 控制台"; Filename: "http://127.0.0.1:8900/console"; IconFilename: "{app}\launcher\mobius.ico"
Name: "{group}\环境检查"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\preflight.ps1"""; WorkingDir: "{app}"
Name: "{group}\许可与第三方声明"; Filename: "notepad.exe"; Parameters: """{app}\licenses\THIRD-PARTY.md"""; WorkingDir: "{app}"
Name: "{group}\卸载 Möbius"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Möbius"; Filename: "{app}\Mobius.exe"; WorkingDir: "{app}"; IconFilename: "{app}\launcher\mobius.ico"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\preflight.ps1"""; WorkingDir: "{app}"; Description: "运行环境检查"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"












