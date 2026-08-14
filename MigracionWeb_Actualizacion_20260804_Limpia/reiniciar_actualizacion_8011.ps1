$ErrorActionPreference = "Stop"

# Solo opera sobre la instancia de actualización: puerto 8011.
# Si se abre desde una consola normal, solicita elevación y continúa automáticamente.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Start-Process -FilePath "powershell.exe" `
    -Verb RunAs `
    -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`""
  exit 0
}

$projectRoot = Split-Path -Parent $PSCommandPath
$logDir = Join-Path $projectRoot "logs"
$stdoutLog = Join-Path $logDir "actualizacion_8011_stdout.log"
$stderrLog = Join-Path $logDir "actualizacion_8011_stderr.log"

# 8011 es la instancia de pruebas, pero debe utilizar el almacenamiento
# persistente del servidor para no guardar CER/KEY dentro de la carpeta del
# proyecto actualizable. Sin esta variable DATA_DIR cae en $projectRoot y una
# carga/guardado de configuración termina apuntando al extraíble.
$persistentDataDir = Join-Path $env:ProgramData "Galacticos\FacturacionWeb"
if (Test-Path -LiteralPath $persistentDataDir) {
  $env:FACTURACION_DATA_DIR = $persistentDataDir
} else {
  # En una estación de desarrollo que todavía no tiene instalación, se
  # conserva el comportamiento local sin inventar una ruta inexistente.
  $env:FACTURACION_DATA_DIR = $projectRoot
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$listener = Get-NetTCPConnection -LocalPort 8011 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -First 1
if ($listener) {
  Stop-Process -Id $listener.OwningProcess -Force
  Start-Sleep -Milliseconds 750
}

$process = Start-Process -FilePath "python" `
  -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8011" `
  -WorkingDirectory $projectRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdoutLog `
  -RedirectStandardError $stderrLog `
  -PassThru

for ($attempt = 0; $attempt -lt 20; $attempt += 1) {
  Start-Sleep -Milliseconds 500
  try {
    $health = Invoke-RestMethod "http://127.0.0.1:8011/health" -TimeoutSec 2
    $health | Format-List
    $openApi = Invoke-RestMethod "http://127.0.0.1:8011/openapi.json" -TimeoutSec 2
    $comandasRoutes = @($openApi.paths.PSObject.Properties.Name | Where-Object { $_ -like "/api/comandas*" }).Count
    if ($comandasRoutes -lt 21) {
      throw "La API inició, pero cargó $comandasRoutes ruta(s) de Comandas; se esperaban 21."
    }
    Write-Host "Actualización iniciada en http://127.0.0.1:8011/app (PID $($process.Id)). Rutas Comandas: $comandasRoutes."
    exit 0
  } catch {
    # Se sigue esperando hasta completar 10 segundos.
  }
}

Get-Content $stderrLog -Tail 100 -ErrorAction SilentlyContinue
throw "La actualización no respondió en el puerto 8011."
