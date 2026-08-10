from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException

from app.db import get_connection
from app.dependencies import require_user
from app.legacy_db import get_legacy_connection
from app.migrations import get_migration, list_migrations

router = APIRouter(prefix="/api/migrations", tags=["migrations"])


def _mysql_ok() -> bool:
    try:
        conn = get_legacy_connection()
        conn.close()
        return True
    except Exception:
        return False


@router.get("")
def index(user=Depends(require_user)):
    return {
        "items": list_migrations(),
        "mysql_disponible": _mysql_ok(),
    }


@router.get("/status")
def status(user=Depends(require_user)):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT module_key, title, status, notes, updated_at FROM migration_notes ORDER BY module_key"
        ).fetchall()
    return {
        "mysql_disponible": _mysql_ok(),
        "modules": [dict(r) for r in rows],
    }


@router.get("/{key}/preview")
def preview(key: str, user=Depends(require_user)):
    m = get_migration(key)
    if not m:
        raise HTTPException(404, f"Migracion '{key}' no encontrada.")
    try:
        return m.preview()
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@router.post("/{key}/run")
def run(key: str, user=Depends(require_user)):
    m = get_migration(key)
    if not m:
        raise HTTPException(404, f"Migracion '{key}' no encontrada.")
    try:
        result = m.run()
        if isinstance(result, dict) and not result.get("ok"):
            raise HTTPException(502, result.get("error", "Error en migracion."))
        return result
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@router.post("/run-all")
def run_all(user=Depends(require_user)):
    from app.db import get_connection

    if not _mysql_ok():
        raise HTTPException(502, "MySQL no disponible.")

    # Ensure brand logo files exist in logos directory
    _ensure_logo_files()

    with get_connection() as conn:
        pendientes = conn.execute(
            "SELECT module_key FROM migration_notes WHERE status = 'pendiente' ORDER BY module_key"
        ).fetchall()

    results = []
    for row in pendientes:
        m = get_migration(row["module_key"])
        if not m:
            continue
        try:
            result = m.run()
            results.append({"module": row["module_key"], "ok": result.get("ok"), "detail": result.get("message", "")})
        except Exception as e:
            results.append({"module": row["module_key"], "ok": False, "detail": str(e)})

    return {"results": results}


def _ensure_logo_files():
    """Copy required brand logos into the logos directory if missing."""
    LOGOS_SRC = Path(__file__).resolve().parents[3] / "AspelAPI" / "logos"
    REQUIRED = ["creadopor.png", "quasar_logo.png"]
    if not LOGOS_SRC.is_dir():
        return
    for name in REQUIRED:
        src = LOGOS_SRC / name
        if not src.is_file():
            # Try to find a fallback
            fallback = LOGOS_SRC / "default.png"
            if fallback.is_file():
                import shutil
                shutil.copy(str(fallback), str(src))
