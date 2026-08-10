from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.db import init_db
from app.routers import auth, billing, cadenas, collections, comandas, companies, conciliacion, crm, customers, dashboard, health, impresion, migrations, products, reports, support, tarifador_envios, timbrado, users


app = FastAPI(title=settings.app_name, version=settings.version)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(companies.router)
app.include_router(customers.router)
app.include_router(products.router)
app.include_router(billing.router)
app.include_router(cadenas.router)
app.include_router(collections.router)
app.include_router(comandas.router)
app.include_router(tarifador_envios.router)
app.include_router(conciliacion.router)
app.include_router(dashboard.router)
app.include_router(users.router)
app.include_router(reports.router)
app.include_router(impresion.router)
app.include_router(migrations.router)
app.include_router(support.router)
app.include_router(crm.router)
app.include_router(timbrado.router)

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def root():
    return {
        "message": "Nueva API de migracion web en linea",
        "docs": "/docs",
        "web": "/app",
    }


@app.get("/app")
@app.api_route("/app/{full_path:path}", methods=["GET"])
def web_app(full_path: str = ""):
    return FileResponse(static_dir / "index.html")
