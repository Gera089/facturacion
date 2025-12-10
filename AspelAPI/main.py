from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import traceback

from routers import clientes, productos, precios, comandas, facturas
from routers import sae_remision_router  # ← Nuevo router SAE

app = FastAPI(title="API Facturación", version="1.0")

# Registrar routers
app.include_router(clientes.router)
app.include_router(productos.router)
app.include_router(precios.router)
app.include_router(comandas.router)
app.include_router(facturas.router)
app.include_router(sae_remision_router.router)   # ← aquí se registra

@app.get("/test")
def test():
    try:
        from database import conectar_mysql
        conn = conectar_mysql()
        if conn is None:
            return {"msg": "ERROR", "detail": "No se pudo conectar a MySQL"}

        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.fetchone()
        conn.close()

        return {"msg": "OK"}

    except Exception as e:
        return {"msg": "ERROR", "detail": str(e)}

@app.get("/")
def raiz():
    return {"mensaje": "API Facturación en línea"}

# Manejo global de errores
@app.exception_handler(Exception)
async def exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "trace": traceback.format_exc()
        }
    )