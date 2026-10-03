; Az Cut installer script (Inno Setup 6)
; Compile with: iscc installer.iss   (build_installer.bat does this)

#define MyAppName "Az Cut"
#define MyAppVersion "1.1"
#define MyAppPublisher "Az Cut"
#define MyAppExeName "AzCut.exe"

[Setup]
AppId={{D13B9EE6-C9D1-4A47-9916-941769661EDC}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Az Cut
DefaultGroupName=Az Cut
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=AzCut-Setup-1.1
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\AzCut\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "build\assets\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{autodesktop}\Az Cut"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"
Name: "{group}\Az Cut"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"

[Run]
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; \
  StatusMsg: "System files install ho rahe hain..."; Check: NeedsVCRedist
Filename: "{app}\{#MyAppExeName}"; Description: "Az Cut kholein"; \
  Flags: nowait postinstall skipifsilent

[Code]
function NeedsVCRedist(): Boolean;
begin
  Result := not RegKeyExists(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64');
end;
