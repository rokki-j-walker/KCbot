; =============================================================================
; KCbot.iss - инсталлер KCbot (Inno Setup 7)
;
; Компиляция:
;   1. Собрать портабл:   py -m PyInstaller KCbot.spec --noconfirm --clean
;   2. Собрать инсталлер: "C:\Program Files\utils\Inno Setup 7\ISCC.exe" KCbot.iss
;   Результат: Setup\KCbot-<версия>.exe
;
; Требование: Inno Setup 7 (https://jrsoftware.org/isinfo.php) - в проекте
; не входит, ставится отдельно.
; =============================================================================

#define MyAppName "KCbot"
#define MyAppVersion "0.1"
#define MyAppPublisher "rokki-j-walker"
#define MyAppURL "https://github.com/rokki-j-walker/KCbot"
#define MyAppExeName "KCbot.exe"
#define MyBuildDir "dist\KCbot"

[Setup]
AppId={{7B2E4C10-9A5D-4E7B-8F31-5C6D2A9E04B1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Setup
OutputBaseFilename=KCbot-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Приложение - оконное (без консоли), но лаунчер порождает python-процессы,
; поэтому при закрытии во время обучения нужно успеть убить дочерние.
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=assets\kc_bot.ico

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MyBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Состояние обучения живёт в %APPDATA%, а не в каталоге установки - трогаем
; только то, что приложение создаёт рядом с собой (если запускалось оттуда).
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\build"
