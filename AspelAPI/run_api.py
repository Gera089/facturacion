# run_api.py
import uvicorn
import logging
import traceback
import os
import sys
from datetime import datetime
from main import app

# Ruta del log junto al EXE
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
LOG_PATH = os.path.join(BASE_DIR, "api_full.log")


def log(text):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] {text}\n")
    except:
        pass


# Capturar excepciones no manejadas
def excepthook(exc_type, exc_value, exc_traceback):
    log("💥 EXCEPCIÓN GLOBAL:")
    log("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))


sys.excepthook = excepthook


# ======================================================
#  🔥 ARRANCAR UVICORN AUTOMÁTICAMENTE SI ES EXE/ SCRIPT
# ======================================================
if __name__ == "__main__":
    log("===== INICIO API =====")
    try:
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8000,
            reload=False,
            workers=1,
            log_level="info",
            log_config=None
        )
    except Exception:
        log("💥 ERROR AL INICIAR UVICORN:")
        log(traceback.format_exc())