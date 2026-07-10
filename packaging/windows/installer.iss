; Inno Setup script for Crypto Quant Desk (Windows).
;
; Prerequisite: build the onedir bundle first, so dist\crypto-quant-desk\ exists:
;     pyinstaller packaging\windows\cqd.spec
; Then compile this script with Inno Setup 6:
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\windows\installer.iss
; Output: dist\installer\crypto-quant-desk-2.0.0-setup.exe
;
; Per-user install (no admin), so a fresh Windows account can install and run in
; minutes. Uninstall removes only the installed program files: the user's data
; under %LOCALAPPDATA%\CryptoQuantDesk and the API keys in Windows Credential
; Manager are deliberately left in place (PRD F9).

#define AppName "Crypto Quant Desk"
#define AppVersion "2.0.0"
#define AppPublisher "ccharafeddine"
#define AppExeName "crypto-quant-desk.exe"
#define AppUrl "https://github.com/ccharafeddine/crypto-quant-desk"

[Setup]
; A stable AppId keeps upgrades and uninstall entries consistent across versions.
AppId={{8F3A1C2E-5B4D-4E6F-9A7B-1C2D3E4F5A6B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user, no elevation. Installs under %LOCALAPPDATA%\Programs.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist\installer
OutputBaseFilename=crypto-quant-desk-{#AppVersion}-setup
SetupIconFile=cqd.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; The entire PyInstaller onedir output.
Source: "..\..\dist\crypto-quant-desk\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

; No [UninstallDelete]: user data (%LOCALAPPDATA%\CryptoQuantDesk) and Credential
; Manager entries are intentionally preserved on uninstall.
