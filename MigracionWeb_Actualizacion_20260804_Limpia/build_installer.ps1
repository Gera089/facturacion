$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$distPath = Join-Path $root ("package_dist_" + $stamp)
$workPath = Join-Path $root ("package_build_" + $stamp)
$env:FACTURACION_API_DIST = Join-Path $distPath "FacturacionWebApi"
New-Item -ItemType Directory -Path $workPath -Force | Out-Null

& pyinstaller --noconfirm --clean --console --onedir --name FacturacionWebApi --distpath $distPath --workpath $workPath `
  --add-data "app;app" `
  --collect-all fastapi `
  --collect-all starlette `
  --collect-all uvicorn `
  --collect-all lxml `
  --collect-submodules mysql.connector.locales `
  --collect-submodules mysql.connector.plugins `
  --hidden-import app.main `
  --hidden-import mysql.connector `
  api_server.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller terminó con código $LASTEXITCODE." }

& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "installer\FacturacionWeb.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup terminó con código $LASTEXITCODE." }

Get-ChildItem (Join-Path $root "release") -Filter "Instalador_Facturacion_Web_*.exe" |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1 FullName, Length, LastWriteTime
