import unicodedata

from fastapi import APIRouter, Depends

from app.dependencies import require_user
from app.legacy_db import get_legacy_connection


router = APIRouter(prefix="/api/companies", tags=["companies"])


def normalizar(value: str) -> str:
    return unicodedata.normalize("NFD", str(value or "")).encode("ascii", "ignore").decode("ascii").strip().upper()


def empresa_visible(value: str) -> bool:
    key = " ".join(normalizar(value).replace("_", " ").split())
    return bool(key) and not (key.startswith("TEST ") or key.startswith("PRUEBA ") or " CODEX" in key)


def _list_companies_from_mysql():
    conn = get_legacy_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT DISTINCT TRIM(empresa) AS name
        FROM clientes
        WHERE empresa IS NOT NULL AND TRIM(empresa) <> ''
        ORDER BY TRIM(empresa)
        """
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    visible_rows = [row for row in rows if empresa_visible(row["name"])]
    items = [
        {
            "id": i + 1,
            "name": row["name"],
            "code": row["name"].upper().replace(" ", "_").replace("Ñ", "N"),
            "active": 1,
        }
        for i, row in enumerate(visible_rows)
    ]
    # Las remisiones históricas pueden tener empresa EZA2007; se ofrece siempre
    # una opción propia para consultarlas sin mezclarlas con CFDI.
    if not any(normalizar(item.get("name")) in {"REMISION", "REMISIONES"} for item in items):
        items.append({"id": len(items) + 1, "name": "Remisiones", "code": "REMISIONES", "active": 1})
    return items


@router.get("")
def companies(user=Depends(require_user)):
    return {
        "items": _list_companies_from_mysql(),
        "user": {
            "username": user["username"],
            "full_name": user["full_name"],
        },
    }
