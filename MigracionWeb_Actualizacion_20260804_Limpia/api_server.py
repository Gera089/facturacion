"""Proceso de API para NSSM; no abre navegador ni depende de una consola interactiva."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def _directorio_aplicacion() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main() -> None:
    app_dir = _directorio_aplicacion()
    data_dir = Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData") / "Galacticos" / "FacturacionWeb"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("FACTURACION_DATA_DIR", str(data_dir))
    # El instalador coloca aquí la configuración real y el servicio la toma
    # explícitamente, sin depender de rutas del equipo donde se compiló.
    os.environ.setdefault("FACTURACION_CFG", str(app_dir / "config.json"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    import uvicorn
    from app.core.config import settings
    from app.main import app

    # Comprobación usada al construir el instalador: confirma que los módulos
    # de FastAPI y la aplicación quedaron incluidos sin ocupar el puerto.
    if "--verify-package" in sys.argv:
        # mysql-connector carga este módulo de forma dinámica al recibir un
        # error del servidor. Importarlo aquí confirma que quedó empaquetado.
        from mysql.connector.locales.eng import client_error  # noqa: F401
        return

    uvicorn.run(app, host=settings.host, port=settings.port, reload=False, log_config=None)


if __name__ == "__main__":
    main()
