from datetime import datetime
import unicodedata
from fastapi import APIRouter, Depends, Query

from app.db import list_companies, list_migration_modules
from app.dependencies import require_user
from app.legacy_db import get_legacy_connection
from app.migrations import MODULES


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _empresa_key(value):
    text = unicodedata.normalize("NFD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    return " ".join(text.replace("_", " ").split()).upper()


def _empresa_visible(value):
    key = _empresa_key(value)
    return key and not (key.startswith("TEST ") or key.startswith("PRUEBA ") or " CODEX" in key)


@router.get("/summary")
def summary(anio: int | None = None, user=Depends(require_user)):
    companies = list_companies()
    modules = list_migration_modules()
    registered_keys = set(MODULES.keys())
    filtered = [m for m in modules if m["module_key"] in registered_keys]
    if not anio:
        anio = datetime.now().year
    total_empresas = 0
    try:
        conn = get_legacy_connection()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT empresa FROM facturas WHERE YEAR(fecha) = %s", (anio,))
        total_empresas = len([row[0] for row in cur.fetchall() if _empresa_visible(row[0])])
        cur.close()
        conn.close()
    except Exception:
        total_empresas = len(companies)
    return {
        "welcome": f"Bienvenido {user['full_name']}",
        "stats": {
            "companies": total_empresas,
            "modules": len(filtered),
            "completed": len([m for m in filtered if m["status"] == "listo"]),
            "pending": len([m for m in filtered if m["status"] == "pendiente"]),
        },
        "modules": filtered,
    }
