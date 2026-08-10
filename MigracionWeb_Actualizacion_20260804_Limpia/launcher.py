"""Lanzador de escritorio para la instalación local de Facturación Web."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _puerto_ocupado(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.8):
            return True
    except OSError:
        return False


def main() -> None:
    # Los archivos operativos nunca se escriben dentro de Program Files:
    # esto preserva base SQLite, acuses y configuración al actualizar.
    data_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Galacticos" / "FacturacionWebActualizacion"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("FACTURACION_DATA_DIR", str(data_dir))
    host = "127.0.0.1"
    port = 8011
    url = f"http://{host}:{port}/app"
    if _puerto_ocupado(host, port):
        webbrowser.open(url)
        return

    import uvicorn
    from app.core.config import settings
    from app.main import app

    def abrir_cuando_este_lista() -> None:
        for _ in range(40):
            if _puerto_ocupado(host, port):
                webbrowser.open(url)
                return
            time.sleep(0.25)

    threading.Thread(target=abrir_cuando_este_lista, daemon=True).start()
    uvicorn.run(app, host=settings.host, port=settings.port, reload=False, log_config=None)


def _directorio_datos_servicio() -> Path:
    return Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData") / "Galacticos" / "FacturacionWeb"


class FacturacionWebService:  # Se completa dinámicamente sólo al ejecutar como servicio.
    pass


def ejecutar_servicio() -> None:
    """Punto de entrada del Servicio de Windows, sin ventana ni consola."""
    import logging
    import servicemanager
    import win32service
    import win32serviceutil

    class _ServicioFacturacionWeb(win32serviceutil.ServiceFramework):
        _svc_name_ = "GalacticosFacturacionWeb"
        _svc_display_name_ = "Galácticos Facturación Web"
        _svc_description_ = "API central de Facturación Web y timbrado CFDI."

        def __init__(self, args):
            super().__init__(args)
            self._server = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            if self._server is not None:
                self._server.should_exit = True

        def SvcDoRun(self):
            data_dir = _directorio_datos_servicio()
            data_dir.mkdir(parents=True, exist_ok=True)
            os.environ["FACTURACION_DATA_DIR"] = str(data_dir)
            logging.basicConfig(
                filename=str(data_dir / "facturacion_web_service.log"),
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(message)s",
            )
            import uvicorn
            from app.core.config import settings
            from app.main import app

            config = uvicorn.Config(
                app,
                host=settings.host,
                port=settings.port,
                log_config=None,
                access_log=False,
            )
            self._server = uvicorn.Server(config)
            self._server.run()

    # El ejecutable es registrado por sc.exe y se inicia con --service.
    # PrepareToHostSingle enlaza el proceso con el Service Control Manager.
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(_ServicioFacturacionWeb)
    servicemanager.StartServiceCtrlDispatcher()


if __name__ == "__main__":
    if "--service" in sys.argv:
        ejecutar_servicio()
    elif "--verify-package" in sys.argv:
        # Verificación de empaquetado para el proceso de instalación: fuerza la
        # carga de FastAPI, routers y estáticos sin iniciar un segundo servidor.
        from app.main import app as _app  # noqa: F401
    else:
        main()
