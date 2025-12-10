@echo off
cd /d %~dp0

echo ==========================================
echo 🚀 Iniciando API de Facturacion (SAE)
echo ==========================================

:: Iniciar API con el entorno virtual
start cmd /k ".\venv\Scripts\python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"

:: Esperar 5 segundos para que Uvicorn levante
timeout /t 5 >nul

:: Abrir Swagger en el navegador
start http://127.0.0.1:8000/docs

exit