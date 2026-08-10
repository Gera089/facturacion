@echo off
cd /d "%~dp0"
set MIGRACION_WEB_PORT=8010
start /B python run_api.py
echo Servicio web iniciado en puerto 8010
