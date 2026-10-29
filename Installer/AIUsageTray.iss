#define MyAppName "AI Usage Tray"
#define MyAppVersion "1.0.11"
#define MyAppPublisher "AI Usage Tray"
#define MyAppExeName "AIUsageTray.exe"

[Setup]
AppId={{8F3A8F80-4B17-4D72-9E0C-1E2B2F8F4A71}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\AI Usage Tray
DefaultGroupName={#MyAppName}
OutputDir=.
OutputBaseFilename=AIUsageTray-Windows
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayName={#MyAppName} version {#MyAppVersion}

[Files]
Source: "..\python\dist-onedir-final\AIUsageTray\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait skipifsilent
