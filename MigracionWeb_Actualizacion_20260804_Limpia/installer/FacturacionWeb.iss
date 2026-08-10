; Instalador de servidor API. Basado en el patrón de Comandas060625: NSSM
; administra el proceso, registra servicio automático y conserva logs.

#define MyAppName "Facturación Web API"
#define MyAppVersion "2026.08.10.3"
#define MyBuildId "2026.08.10-descarga-cfdi-sin-cache-1"
#define MyApiExeName "FacturacionWebApi.exe"
#define MyApiServiceName "FacturacionWebAPI"
#define ProjectRoot (SourcePath + "\\..")
#define ApiDistDir GetEnv("FACTURACION_API_DIST")
#define NssmSource "..\..\AspelAPI\instaladores\nssm.exe"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\Galacticos\FacturacionWeb
DefaultGroupName=Facturación Web API
OutputDir=..\release
OutputBaseFilename=Instalador_Facturacion_Web_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayName=Facturación Web API

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Dirs]
Name: "{commonappdata}\Galacticos\FacturacionWeb\logs"
Name: "{commonappdata}\Galacticos\FacturacionWeb\storage"
Name: "{commonappdata}\Galacticos\FacturacionWeb\storage\addendas"
Name: "{commonappdata}\Galacticos\FacturacionWeb\storage\csd"
Name: "{commonappdata}\Galacticos\FacturacionWeb\storage\fiel"
Name: "{commonappdata}\Galacticos\FacturacionWeb\storage\sat"

[Files]
Source: "{#ApiDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#NssmSource}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\AspelAPI\logos\*"; DestDir: "{app}\logos"; Flags: ignoreversion recursesubdirs createallsubdirs
; OpenSSL portátil para validar y sellar CFDI, sin depender de instalaciones del servidor.
Source: "C:\Program Files\Git\usr\bin\openssl.exe"; DestDir: "{app}\tools\openssl"; Flags: ignoreversion
Source: "C:\Program Files\Git\usr\bin\msys-2.0.dll"; DestDir: "{app}\tools\openssl"; Flags: ignoreversion
Source: "C:\Program Files\Git\usr\bin\msys-crypto-3.dll"; DestDir: "{app}\tools\openssl"; Flags: ignoreversion
Source: "C:\Program Files\Git\usr\bin\msys-ssl-3.dll"; DestDir: "{app}\tools\openssl"; Flags: ignoreversion
Source: "..\..\config.json"; DestDir: "{app}"; DestName: "config.json"; Flags: ignoreversion
Source: "..\storage\addendas\*"; DestDir: "{commonappdata}\Galacticos\FacturacionWeb\storage\addendas"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "..\storage\csd\*"; DestDir: "{commonappdata}\Galacticos\FacturacionWeb\storage\csd"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "..\storage\fiel\*"; DestDir: "{commonappdata}\Galacticos\FacturacionWeb\storage\fiel"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "..\storage\sat\*"; DestDir: "{commonappdata}\Galacticos\FacturacionWeb\storage\sat"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "verify_api.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Code]
procedure MostrarEstado(const Texto: string);
begin
  WizardForm.StatusLabel.Caption := Texto;
  WizardForm.Update;
end;

function EjecutarNSSM(const Params: string): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{app}\nssm.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

procedure QuitarServicioAnterior;
var
  ResultCode: Integer;
begin
  MostrarEstado('Quitando servicio anterior si existe...');
  Exec(ExpandConstant('{sys}\sc.exe'), 'stop "GalacticosFacturacionWeb"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\sc.exe'), 'delete "GalacticosFacturacionWeb"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure DetenerServicioExistente;
var
  ResultCode: Integer;
begin
  MostrarEstado('Deteniendo servicio existente...');
  { En una actualización el ejecutable anterior puede estar bloqueado por NSSM. }
  Exec(ExpandConstant('{app}\nssm.exe'), 'stop "{#MyApiServiceName}"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\sc.exe'), 'stop "{#MyApiServiceName}"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function VerificarApi: Boolean;
var
  ResultCode: Integer;
begin
  MostrarEstado('Verificando que el API responda en http://127.0.0.1:8010/health...');
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\verify_api.ps1') + '" -Port 8010 -ExpectedVersion "{#MyBuildId}"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

procedure InstalarServicioAPI;
var
  ApiExePath: string;
  ApiDirPath: string;
  LogStdoutPath: string;
  LogStderrPath: string;
begin
  ApiExePath := ExpandConstant('{app}\{#MyApiExeName}');
  ApiDirPath := ExpandConstant('{app}');
  LogStdoutPath := ExpandConstant('{commonappdata}\Galacticos\FacturacionWeb\logs\api_stdout.log');
  LogStderrPath := ExpandConstant('{commonappdata}\Galacticos\FacturacionWeb\logs\api_stderr.log');

  QuitarServicioAnterior;
  MostrarEstado('Removiendo registro anterior de NSSM...');
  EjecutarNSSM('stop "{#MyApiServiceName}"');
  EjecutarNSSM('remove "{#MyApiServiceName}" confirm');

  MostrarEstado('Creando servicio Windows FacturacionWebAPI...');
  if not EjecutarNSSM('install "{#MyApiServiceName}" "' + ApiExePath + '"') then
  begin
    MsgBox('No se pudo crear el servicio de Facturación Web con NSSM.', mbError, MB_OK);
    exit;
  end;

  EjecutarNSSM('set "{#MyApiServiceName}" AppDirectory "' + ApiDirPath + '"');
  EjecutarNSSM('set "{#MyApiServiceName}" DisplayName "Facturación Web API"');
  EjecutarNSSM('set "{#MyApiServiceName}" Description "API central de Facturación Web y timbrado CFDI."');
  EjecutarNSSM('set "{#MyApiServiceName}" Start SERVICE_AUTO_START');
  EjecutarNSSM('set "{#MyApiServiceName}" AppStdout "' + LogStdoutPath + '"');
  EjecutarNSSM('set "{#MyApiServiceName}" AppStderr "' + LogStderrPath + '"');
  EjecutarNSSM('set "{#MyApiServiceName}" AppRotateFiles 1');
  EjecutarNSSM('set "{#MyApiServiceName}" AppRotateOnline 1');
  EjecutarNSSM('set "{#MyApiServiceName}" AppExit Default Restart');
  MostrarEstado('Iniciando servicio FacturacionWebAPI...');
  if not EjecutarNSSM('start "{#MyApiServiceName}"') then
    MsgBox('El servicio se creó, pero no pudo iniciar. Revisa los logs del servidor.', mbError, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
    DetenerServicioExistente
  else if CurStep = ssPostInstall then
  begin
    InstalarServicioAPI;
    MostrarEstado('Creando regla de firewall para el puerto 8010...');
    Exec(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall add rule name="Facturacion Web API 8010" dir=in action=allow protocol=TCP localport=8010', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if VerificarApi then
      MsgBox('Instalación completada.'#13#10#13#10 +
             'Servicio Windows: {#MyApiServiceName}'#13#10 +
             'Estado: iniciado y API verificada.'#13#10 +
             'Acceso local: http://127.0.0.1:8010/app'#13#10 +
             'Acceso red: http://IP-DEL-SERVIDOR:8010/app', mbInformation, MB_OK)
    else
      MsgBox('El servicio fue creado, pero la API no respondió en 45 segundos.'#13#10 +
             'Revisa: C:\ProgramData\Galacticos\FacturacionWeb\logs\api_stderr.log', mbError, MB_OK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    Exec(ExpandConstant('{app}\nssm.exe'), 'stop "{#MyApiServiceName}"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(ExpandConstant('{app}\nssm.exe'), 'remove "{#MyApiServiceName}" confirm', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall delete rule name="Facturacion Web API 8010"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
