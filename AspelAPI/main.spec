# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
import os
import glob

block_cipher = None

# 📂 Carpeta del proyecto
project_dir = r"C:\Users\Maquina 10\Desktop\Proyecto facturacion\AspelAPI"

# 🔹 Archivos JSON de config que usa tu API
datas_files = [
    ('config.json', '.'),
    ('config_impresion.json', '.'),
    ('config_impresora.json', '.'),
]

# 🔹 Copiar datos del paquete fdb (archivos internos)
datas_files += collect_data_files('fdb')

# 🔹 Submódulos de FastAPI / Uvicorn / Starlette
fastapi_submods   = collect_submodules('fastapi')
uvicorn_submods   = collect_submodules('uvicorn')
starlette_submods = collect_submodules('starlette')

# 🔹 Hidden imports explícitos
extra_hidden = [
    'fastapi',
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops.auto',
    'uvicorn.workers',
    'pydantic',
    'pydantic_core',
    'starlette',
    'anyio',
    'idna',

    # MySQL
    'mysql',
    'mysql.connector',
    'mysql.connector.errors',

    # Firebird
    'fdb',
    'sae_remision',

    # Routers
    'routers.sae',
    'routers.clientes',
    'routers.comandas',
    'routers.facturas',
    'routers.precios',
    'routers.productos',

    'typing_extensions',
    'multipart',
    'numpy',
    'pandas',
]

hiddenimports = fastapi_submods + uvicorn_submods + starlette_submods + extra_hidden

# ==========================================================
# 🔥 🔥 🔥 AGREGAR DLLs DE FIREBIRD (BIN + PLUGINS) 🔥 🔥 🔥
# ==========================================================

firebird_dir = r"C:\FirebirdPython"
firebird_binaries = []

# DLLs dentro de C:\FirebirdPython
if os.path.isdir(firebird_dir):
    for file in os.listdir(firebird_dir):
        full = os.path.join(firebird_dir, file)
        if os.path.isfile(full):
            firebird_binaries.append((full, "FirebirdPython"))

    # Subcarpeta plugins
    plugins_dir = os.path.join(firebird_dir, "plugins")
    if os.path.isdir(plugins_dir):
        for file in os.listdir(plugins_dir):
            full = os.path.join(plugins_dir, file)
            if os.path.isfile(full):
                firebird_binaries.append(
                    (full, "FirebirdPython/plugins")
                )
else:
    print("⚠ ADVERTENCIA: No existe C:\\FirebirdPython. No se incluirán las DLLs de Firebird.")

# ==========================================================

a = Analysis(
    ['run_api.py'],     # 👉 Punto de entrada
    pathex=[project_dir],
    binaries=firebird_binaries,      
    datas=datas_files,
    hiddenimports=hiddenimports,
    collect_data=True,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
)