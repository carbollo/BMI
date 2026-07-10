; ===================================================================
;  Instalador de BMI (Inno Setup) sobre el ONEFILE autoejecutable
;  (dist\BMI.exe). Genera dist\BMI-Setup.exe: instala por-usuario (sin
;  admin) el .exe unico, crea accesos directos, registra el protocolo
;  nxm:// y trae desinstalador.
;  Compilar:  "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
; ===================================================================
#define MyAppName "BMI - Bethesda Mod Installer"
#define MyAppShort "BMI"
#define MyAppVersion "1.3.2"
#define MyAppPublisher "carbollo"
#define MyAppURL "https://github.com/carbollo/BMI"
#define MyAppExe "BMI.exe"

[Setup]
AppId={{8F3B9A2C-1D4E-4A6B-9C7D-2E5F0A1B3C4D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppShort} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
; Instalacion por-usuario (sin UAC). Recomendado para gestores de mods
; (evita problemas de permisos frente a Program Files).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#MyAppShort}
DefaultGroupName={#MyAppShort}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=BMI-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; El onefile es autoejecutable y se lleva todo dentro (usvfs incluido).
Source: "dist\BMI.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppShort}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\{cm:UninstallProgram,{#MyAppShort}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppShort}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
; Protocolo nxm:// (por-usuario, misma clave que registra la propia app en nxm.py).
Root: HKCU; Subkey: "Software\Classes\nxm"; ValueType: string; ValueName: ""; ValueData: "URL:Nexus Mod Manager Protocol"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\nxm"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\nxm\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExe}"" ""%1"""

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppShort}}"; Flags: nowait postinstall skipifsilent
