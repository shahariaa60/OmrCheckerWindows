[Setup]
AppName=Byabodhan OMR Scanner
AppVersion=2.0.0
AppPublisher=Byabodhan Shikkhalay
DefaultDirName={localappdata}\ByabodhanOMR
DefaultGroupName=Byabodhan OMR Scanner
UninstallDisplayIcon={app}\ByabodhanOMR.exe
SetupIconFile=assets\logo.ico
OutputDir=installer_out
OutputBaseFilename=ByabodhanOMR_Setup
Compression=lzma2/fast
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\ByabodhanOMR\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Byabodhan OMR Scanner"; Filename: "{app}\ByabodhanOMR.exe"; IconFilename: "{app}\assets\logo.ico"
Name: "{autodesktop}\Byabodhan OMR Scanner"; Filename: "{app}\ByabodhanOMR.exe"; IconFilename: "{app}\assets\logo.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\ByabodhanOMR.exe"; Description: "Launch Byabodhan OMR Scanner"; Flags: nowait postinstall skipifsilent
