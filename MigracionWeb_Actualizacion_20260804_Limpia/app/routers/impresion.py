import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.db import get_connection
from app.dependencies import require_user

router = APIRouter(prefix="/api/impresion", tags=["impresion"])

def _resolve_logos_dir() -> Path:
    """Ruta de logos válida tanto en desarrollo como en el servidor instalado."""
    configured = os.environ.get("FACTURACION_LOGOS_DIR")
    if configured:
        return Path(configured)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "logos"
    return Path(__file__).resolve().parents[3] / "AspelAPI" / "logos"


LOGOS_DIR = _resolve_logos_dir()

GFACTURA_EMPRESA = "Gourmet España"
GFACTURA_TIPO = "factura"
GFACTURA_NOMBRE = "GFACTURA - Gourmet España estándar"
GFACTURA_CONTENIDO = {
    "renderer": "gfactura_pdf",
    "version": 1,
    "descripcion": "Formato estándar de facturas Gourmet España. Usa el generador PDF del módulo de timbrado.",
    "endpoint": "/api/timbrado/cfdi-emitidos/{folio}/pdf",
}
EFACTURA_EMPRESA = "EZA2007"
EFACTURA_NOMBRE = "EFACTURA - EZA2007 estándar"
EFACTURA_CONTENIDO = {
    "renderer": "efactura_pdf",
    "version": 1,
    "descripcion": "Formato estándar de facturas EZA2007. Usa el generador PDF del módulo de timbrado.",
    "endpoint": "/api/timbrado/cfdi-emitidos/{folio}/pdf",
}


class FormatoPayload(BaseModel):
    empresa: str = ""
    tipo_formato: str = ""
    nombre: str = ""
    contenido: str = ""
    activo: int = 1


def _ensure_table():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS impresion_migracion (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                empresa TEXT, tipo_formato TEXT, nombre TEXT,
                contenido TEXT, activo INTEGER DEFAULT 1,
                migrado_en TEXT
            )
        """)
        _ensure_gfactura_standard(conn)
        _ensure_efactura_standard(conn)
        _upgrade_invoice_party_columns(conn)


def _upgrade_invoice_party_columns(conn):
    """Une los bloques antiguos Cliente + Consignatario en una fila de dos columnas.

    Los primeros formatos creados por el constructor guardaban ambos como
    componentes separados. Cada componente usa su propio grid, por lo que se
    mostraban uno debajo de otro aun cuando la hoja permite dos columnas.
    """
    rows = conn.execute(
        "SELECT id, contenido FROM impresion_migracion WHERE tipo_formato = ?",
        ("factura",),
    ).fetchall()
    for row in rows:
        try:
            items = json.loads(row["contenido"] or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(items, list):
            continue

        upgraded = []
        changed = False
        index = 0
        while index < len(items):
            current = items[index]
            following = items[index + 1] if index + 1 < len(items) else None
            if (
                isinstance(current, dict)
                and current.get("type") == "customer"
                and isinstance(following, dict)
                and following.get("type") == "consignatario"
                and not (following.get("props") or {}).get("twoColumns")
            ):
                customer_props = current.get("props") or {}
                consignee_props = following.get("props") or {}
                left_fields = []
                if customer_props.get("showName", True):
                    left_fields.append("name")
                if customer_props.get("showAddress", True):
                    left_fields.append("address")
                if customer_props.get("showRfc", True):
                    left_fields.append("rfc")
                right_fields = []
                if consignee_props.get("showName", True):
                    right_fields.append("name")
                if consignee_props.get("showAddress", True):
                    right_fields.append("address")

                upgraded.append({
                    "id": following.get("id") or current.get("id"),
                    "type": "consignatario",
                    "props": {
                        "twoColumns": True,
                        "col1": "cliente",
                        "col2": "consignatario",
                        "col1Fields": left_fields,
                        "col2Fields": right_fields,
                    },
                })
                changed = True
                index += 2
                continue
            upgraded.append(current)
            index += 1

        if changed:
            conn.execute(
                "UPDATE impresion_migracion SET contenido = ? WHERE id = ?",
                (json.dumps(upgraded, ensure_ascii=False), row["id"]),
            )


def _ensure_gfactura_standard(conn):
    contenido = json.dumps(GFACTURA_CONTENIDO, ensure_ascii=False, indent=2)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT id, nombre, contenido FROM impresion_migracion WHERE empresa = ? AND tipo_formato = ? LIMIT 1",
        (GFACTURA_EMPRESA, GFACTURA_TIPO),
    ).fetchone()
    if row:
        try:
            current = json.loads(row["contenido"] or "{}")
        except Exception:
            current = {}
        if isinstance(current, dict) and current.get("renderer") == GFACTURA_CONTENIDO["renderer"] and row["nombre"] == GFACTURA_NOMBRE:
            return
        conn.execute(
            """
            UPDATE impresion_migracion
               SET nombre = ?, contenido = ?, activo = 1, migrado_en = ?
             WHERE id = ?
            """,
            (GFACTURA_NOMBRE, contenido, now, row["id"]),
        )
        return
    conn.execute(
        """
        INSERT INTO impresion_migracion (empresa, tipo_formato, nombre, contenido, activo, migrado_en)
        VALUES (?, ?, ?, ?, 1, ?)
        """,
        (GFACTURA_EMPRESA, GFACTURA_TIPO, GFACTURA_NOMBRE, contenido, now),
    )


def _ensure_efactura_standard(conn):
    contenido = json.dumps(EFACTURA_CONTENIDO, ensure_ascii=False, indent=2)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT id, nombre, contenido FROM impresion_migracion WHERE empresa = ? AND tipo_formato = ? LIMIT 1",
        (EFACTURA_EMPRESA, GFACTURA_TIPO),
    ).fetchone()
    if row:
        try:
            current = json.loads(row["contenido"] or "{}")
        except Exception:
            current = {}
        if isinstance(current, dict) and current.get("renderer") == EFACTURA_CONTENIDO["renderer"] and row["nombre"] == EFACTURA_NOMBRE:
            return
        conn.execute(
            """
            UPDATE impresion_migracion
               SET nombre = ?, contenido = ?, activo = 1, migrado_en = ?
             WHERE id = ?
            """,
            (EFACTURA_NOMBRE, contenido, now, row["id"]),
        )
        return
    conn.execute(
        """
        INSERT INTO impresion_migracion (empresa, tipo_formato, nombre, contenido, activo, migrado_en)
        VALUES (?, ?, ?, ?, 1, ?)
        """,
        (EFACTURA_EMPRESA, GFACTURA_TIPO, EFACTURA_NOMBRE, contenido, now),
    )


@router.get("/formatos")
def list_formatos(user=Depends(require_user)):
    _ensure_table()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM impresion_migracion ORDER BY empresa, tipo_formato"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/formatos/by-empresa")
def get_formato_by_empresa(empresa: str = "", tipo: str = "factura", user=Depends(require_user)):
    _ensure_table()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM impresion_migracion WHERE empresa = ? AND tipo_formato = ?",
            (empresa, tipo)
        ).fetchone()
    if not row:
        return {"id": None}
    return dict(row)


@router.put("/formatos/upsert")
def upsert_formato(payload: FormatoPayload, user=Depends(require_user)):
    _ensure_table()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM impresion_migracion WHERE empresa = ? AND tipo_formato = ?",
            (payload.empresa, payload.tipo_formato)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE impresion_migracion
                   SET nombre=?, contenido=?, activo=?
                   WHERE id=?""",
                (payload.nombre, payload.contenido, payload.activo, existing[0]),
            )
            return {"ok": True, "id": existing[0]}
        cur = conn.execute(
            """INSERT INTO impresion_migracion (empresa, tipo_formato, nombre, contenido, activo)
               VALUES (?,?,?,?,?)""",
            (payload.empresa, payload.tipo_formato, payload.nombre,
             payload.contenido, payload.activo),
        )
        return {"ok": True, "id": cur.lastrowid}


@router.get("/formatos/{formato_id}")
def get_formato(formato_id: int, user=Depends(require_user)):
    _ensure_table()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM impresion_migracion WHERE id = ?", (formato_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Formato no encontrado")
    return dict(row)


@router.put("/formatos/{formato_id}")
def update_formato(formato_id: int, payload: FormatoPayload, user=Depends(require_user)):
    _ensure_table()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM impresion_migracion WHERE id = ?", (formato_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Formato no encontrado")
        conn.execute(
            """UPDATE impresion_migracion
               SET empresa=?, tipo_formato=?, nombre=?, contenido=?, activo=?
               WHERE id=?""",
            (payload.empresa, payload.tipo_formato, payload.nombre,
             payload.contenido, payload.activo, formato_id),
        )
    return {"ok": True}


@router.post("/formatos")
def create_formato(payload: FormatoPayload, user=Depends(require_user)):
    _ensure_table()
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO impresion_migracion (empresa, tipo_formato, nombre, contenido, activo)
               VALUES (?,?,?,?,?)""",
            (payload.empresa, payload.tipo_formato, payload.nombre,
             payload.contenido, payload.activo),
        )
        return {"ok": True, "id": cur.lastrowid}


@router.get("/logos")
def list_logos(user=Depends(require_user)):
    if not LOGOS_DIR.is_dir():
        return []
    files = sorted(
        p.name for p in LOGOS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    )
    return files


@router.get("/logos/{filename}")
def get_logo(filename: str, user=Depends(require_user)):
    filepath = LOGOS_DIR / filename
    if not filepath.is_file():
        raise HTTPException(404, "Logo no encontrado")
    return FileResponse(str(filepath))


@router.get("/logos/public/{filename}")
def get_logo_public(filename: str):
    filepath = LOGOS_DIR / filename
    if not filepath.is_file():
        raise HTTPException(404, "Logo no encontrado")
    return FileResponse(str(filepath))


@router.put("/logos/upload")
def upload_logo(file: UploadFile = File(...), user=Depends(require_user)):
    if not file.filename or not file.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        raise HTTPException(400, "Formato no soportado (PNG/JPG/GIF/WEBP)")
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    dest = LOGOS_DIR / file.filename
    with open(str(dest), "wb") as f:
        import shutil
        shutil.copyfileobj(file.file, f)
    return {"filename": file.filename}


@router.delete("/formatos/{formato_id}")
def delete_formato(formato_id: int, user=Depends(require_user)):
    _ensure_table()
    with get_connection() as conn:
        conn.execute("DELETE FROM impresion_migracion WHERE id = ?", (formato_id,))
    return {"ok": True}
