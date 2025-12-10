import subprocess
import requests
import time
import os

API_URL = "http://127.0.0.1:8000"
API_EXE = r"C:\AspelAPI\main.exe"

def api_esta_activa():
    try:
        r = requests.get(f"{API_URL}/test", timeout=1)
        return r.status_code == 200 and r.json().get("msg") == "OK"
    except:
        return False


def iniciar_api():
    """Inicia la API en segundo plano sin consola."""
    if not os.path.exists(API_EXE):
        print("❌ ERROR: No se encontró el ejecutable de la API:", API_EXE)
        return False

    # Flag para NO mostrar ventana de consola
    CREATE_NO_WINDOW = 0x08000000

    try:
        subprocess.Popen(
            [API_EXE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW
        )
        return True
    except Exception as e:
        print("❌ Error al iniciar API:", e)
        return False


def asegurar_api_activa():
    """
    1. Checa si la API ya está activa.
    2. Si no, la inicia.
    3. Espera hasta que responda.
    """
    # 1️⃣ Revisar si ya está activa
    if api_esta_activa():
        print("✔ API ya está arriba.")
        return True

    print("🔄 API no responde. Intentando iniciar main.exe...")

    # 2️⃣ Si no está activa, iniciar executabe
    if not iniciar_api():
        return False

    print("⏳ Esperando a que la API arranque...")

    # 3️⃣ Esperar hasta que esté arriba
    for _ in range(60):  # ~15 segundos
        time.sleep(0.5)
        if api_esta_activa():
            print("✔ API iniciada correctamente.")
            return True

    print("❌ No se pudo confirmar que la API está activa.")
    return False