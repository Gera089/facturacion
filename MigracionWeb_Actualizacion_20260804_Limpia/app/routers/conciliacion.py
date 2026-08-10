import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.db import get_connection
from app.dependencies import require_user
from app.legacy_db import get_legacy_connection

router = APIRouter(prefix="/api/conciliacion", tags=["conciliacion"])

DOCS_DIR = Path(__file__).resolve().parents[2] / "storage" / "conciliacion_docs"


def _ensure_docs_dir():
    DOCS_DIR.mkdir(parents=True, exist_ok=True)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_xml_amount(content: bytes) -> float:
    try:
        root = ET.fromstring(content)
        ns = {"cfdi": "http://www.sat.gob.mx/cfd/4"}
        total = root.attrib.get("Total")
        if total is None:
            ns = {"cfdi": "http://www.sat.gob.mx/cfd/3"}
            total = root.attrib.get("Total")
        if total is None:
            total = root.attrib.get("total")
        return float(total) if total else 0.0
    except Exception:
        return 0.0


@router.get("/lookup-test/{folio}")
def lookup_test(folio: str):
    cur = None
    conn = None
    try:
        conn = get_legacy_connection()
        cur = conn.cursor()
        sql = """
            SELECT f.factura, f.numero_cliente, f.total, f.consignatario, f.empresa,
                   c.nombre AS c_nombre,
                   c_eza.nombre AS c_eza_nombre,
                   c_ibe.nombre AS c_ibe_nombre
            FROM facturas f
            LEFT JOIN clientes c
                ON TRIM(CAST(c.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR))
                AND UPPER(TRIM(c.empresa)) = UPPER(TRIM(f.empresa))
            LEFT JOIN clientes c_eza
                ON TRIM(CAST(c_eza.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR))
                AND UPPER(TRIM(c_eza.empresa)) = 'EZA2007'
            LEFT JOIN clientes c_ibe
                ON TRIM(CAST(c_ibe.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR))
                AND UPPER(TRIM(c_ibe.empresa)) = 'IBERSUR'
            WHERE UPPER(TRIM(f.factura)) LIKE %s
            LIMIT 10
        """
        cur.execute(sql, [f"%{folio.strip().upper()}%"])
        columns = [col[0] for col in cur.description]
        rows = [dict(zip(columns, r)) for r in cur.fetchall()]
        return {"sql_params": folio, "count": len(rows), "rows": rows}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        if cur:
            try: cur.close()
            except: pass
        if conn:
            try: conn.close()
            except: pass


@router.post("/lookup-folios")
def lookup_folios(data: dict, user=Depends(require_user)):
    foliosPares = data.get("foliosPares") or data.get("folios", [])
    if not foliosPares:
        return {}
    cur = None
    conn = None
    try:
        conn = get_legacy_connection()
        cur = conn.cursor()
        clauses = []
        params = []
        for item in foliosPares:
            if isinstance(item, str):
                folio = item.strip()
                empresa = ""
            else:
                folio = (item.get("folio") or "").strip()
                empresa = (item.get("empresa") or "").strip()
            if not folio:
                continue
            upper_val = folio.upper()
            if empresa:
                clauses.append("(UPPER(TRIM(f.factura)) = %s AND UPPER(TRIM(f.empresa)) = %s)")
                params.extend([upper_val, empresa.upper()])
                clauses.append("(UPPER(TRIM(f.factura)) LIKE %s AND UPPER(TRIM(f.empresa)) = %s)")
                params.extend([f"%{upper_val}%", empresa.upper()])
            else:
                clauses.append("UPPER(TRIM(f.factura)) = %s")
                params.append(upper_val)
                clauses.append("UPPER(TRIM(f.factura)) LIKE %s")
                params.append(f"%{upper_val}%")
        if not clauses:
            return {}
        sql = f"""
            SELECT f.factura, f.empresa, f.numero_cliente, f.total, f.consignatario,
                   COALESCE(
                       c.nombre,
                       CASE
                           WHEN UPPER(TRIM(f.empresa)) IN ('REMISION', 'REMISIÓN')
                           THEN COALESCE(c_eza.nombre, c_ibe.nombre)
                           ELSE NULL
                       END,
                       f.consignatario,
                       ''
                   ) AS cliente_nombre
            FROM facturas f
            LEFT JOIN clientes c
                ON TRIM(CAST(c.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR))
                AND UPPER(TRIM(c.empresa)) = UPPER(TRIM(f.empresa))
            LEFT JOIN clientes c_eza
                ON TRIM(CAST(c_eza.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR))
                AND UPPER(TRIM(c_eza.empresa)) = 'EZA2007'
            LEFT JOIN clientes c_ibe
                ON TRIM(CAST(c_ibe.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR))
                AND UPPER(TRIM(c_ibe.empresa)) = 'IBERSUR'
            WHERE {' OR '.join(clauses)}
        """
        cur.execute(sql, params)
        columns = [col[0] for col in cur.description]
        result = {}
        for row in cur.fetchall():
            d = dict(zip(columns, row))
            folio = d["factura"].strip()
            if folio not in result:
                result[folio] = d
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cur:
            try: cur.close()
            except: pass
        if conn:
            try: conn.close()
            except: pass


@router.get("")
def list_conciliaciones(user=Depends(require_user)):
    with get_connection() as conn:
        if user["role"] == "admin":
            rows = conn.execute(
                """SELECT c.*,
                          (SELECT COUNT(*) FROM conciliacion_partidas WHERE conciliacion_id = c.id) AS _partida_count,
                          (SELECT COALESCE(SUM(pago), 0) FROM conciliacion_partidas WHERE conciliacion_id = c.id) AS _total_partidas
                   FROM conciliaciones c ORDER BY c.created_at DESC"""
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT c.*,
                          (SELECT COUNT(*) FROM conciliacion_partidas WHERE conciliacion_id = c.id) AS _partida_count,
                          (SELECT COALESCE(SUM(pago), 0) FROM conciliacion_partidas WHERE conciliacion_id = c.id) AS _total_partidas
                   FROM conciliaciones c
                   JOIN conciliacion_visibilidad v ON v.conciliacion_id = c.id
                   WHERE v.user_id = ?
                   ORDER BY c.created_at DESC""",
                (user["id"],),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            conceptos_sum = conn.execute(
                "SELECT COALESCE(SUM(monto), 0) FROM conciliacion_conceptos WHERE conciliacion_id = ?",
                (d["id"],),
            ).fetchone()[0]
            d["_total_aplicado"] = (d.get("_total_partidas") or 0) + (conceptos_sum or 0)
            result.append(d)
        return result


@router.get("/{conc_id}")
def get_conciliacion(conc_id: int, user=Depends(require_user)):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM conciliaciones WHERE id = ?", (conc_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Conciliacion no encontrada.")
        partidas = conn.execute(
            "SELECT * FROM conciliacion_partidas WHERE conciliacion_id = ? ORDER BY id",
            (conc_id,),
        ).fetchall()
        visibilidad = conn.execute(
            "SELECT user_id FROM conciliacion_visibilidad WHERE conciliacion_id = ?",
            (conc_id,),
        ).fetchall()
        conceptos = conn.execute(
            "SELECT * FROM conciliacion_conceptos WHERE conciliacion_id = ? ORDER BY id",
            (conc_id,),
        ).fetchall()
    result = dict(row)
    result["partidas"] = [dict(p) for p in partidas]
    result["visibilidad"] = [v["user_id"] for v in visibilidad]
    result["conceptos"] = [dict(c) for c in conceptos]
    amazon_raw = result.get("amazon_data", "") or "{}"
    try:
        result["amazon"] = json.loads(amazon_raw)
    except Exception:
        result["amazon"] = {}
    return result


@router.post("")
def crear_conciliacion(data: dict, user=Depends(require_user)):
    now = _now()
    with get_connection() as conn:
        amazon_json = json.dumps(data.get("amazon", {}))
        cur = conn.execute(
            """INSERT INTO conciliaciones (cliente_id, cliente_nombre, monto_pago, fecha, notas, created_at, created_by, updated_at, amazon_data)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                data.get("cliente_id", ""),
                data.get("cliente_nombre", ""),
                data.get("monto_pago", 0),
                data.get("fecha", now[:10]),
                data.get("notas", ""),
                now,
                user["id"],
                now,
                amazon_json,
            ),
        )
        conc_id = cur.lastrowid

        for p in data.get("partidas", []):
            conn.execute(
                """INSERT INTO conciliacion_partidas
                   (conciliacion_id, factura_folio, factura_id, monto_factura, comision, iva, total, envio, producto_no_enviado, total_envio, pago, documento_nombre, documento_monto)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    conc_id,
                    p.get("factura_folio", ""),
                    p.get("factura_id", ""),
                    p.get("monto_factura", 0),
                    p.get("comision", 0),
                    p.get("iva", 0),
                    p.get("total", 0),
                    p.get("envio", 0),
                    p.get("producto_no_enviado", 0),
                    p.get("total_envio", 0),
                    p.get("pago", 0),
                    p.get("documento_nombre", ""),
                    p.get("documento_monto", 0),
                ),
            )

        for uid in data.get("visibilidad", []):
            conn.execute(
                "INSERT INTO conciliacion_visibilidad (conciliacion_id, user_id) VALUES (?,?)",
                (conc_id, uid),
            )

        for c in data.get("conceptos", []):
            conn.execute(
                """INSERT INTO conciliacion_conceptos (conciliacion_id, nombre, descripcion, monto)
                   VALUES (?,?,?,?)""",
                (
                    conc_id,
                    c.get("nombre", ""),
                    c.get("descripcion", ""),
                    c.get("monto", 0),
                ),
            )

    _marcar_conciliadas_mysql(data.get("partidas", []), data.get("cliente_id", ""), conc_id)
    return {"ok": True, "id": conc_id}


def _mysql_conciliadas_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conciliacion_conciliadas (
            factura_folio VARCHAR(255) NOT NULL PRIMARY KEY,
            cliente_numero VARCHAR(50) NOT NULL,
            factura_id VARCHAR(255) DEFAULT '',
            conciliado_el DATETIME NOT NULL,
            conciliacion_id INT DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)


def _marcar_conciliadas_mysql(partidas, cliente_numero, conc_id):
    if not partidas:
        return
    now = _now()
    conn = get_legacy_connection()
    try:
        cur = conn.cursor()
        _mysql_conciliadas_table(cur)
        for p in partidas:
            folio = p.get("factura_folio", "") or p.get("factura_id", "")
            if not folio:
                continue
            cur.execute(
                "INSERT IGNORE INTO conciliacion_conciliadas (factura_folio, cliente_numero, factura_id, conciliado_el, conciliacion_id) VALUES (%s, %s, %s, %s, %s)",
                (folio, cliente_numero, p.get("factura_id", ""), now, conc_id),
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()


@router.put("/{conc_id}")
def actualizar_conciliacion(conc_id: int, data: dict, user=Depends(require_user)):
    now = _now()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM conciliaciones WHERE id = ?", (conc_id,)
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Conciliacion no encontrada.")

        amazon_json = json.dumps(data.get("amazon", {}))
        conn.execute(
            """UPDATE conciliaciones SET cliente_id=?, cliente_nombre=?, monto_pago=?, fecha=?, notas=?, updated_at=?, amazon_data=?
               WHERE id=?""",
            (
                data.get("cliente_id", ""),
                data.get("cliente_nombre", ""),
                data.get("monto_pago", 0),
                data.get("fecha", now[:10]),
                data.get("notas", ""),
                now,
                amazon_json,
                conc_id,
            ),
        )

        conn.execute(
            "DELETE FROM conciliacion_partidas WHERE conciliacion_id = ?",
            (conc_id,),
        )
        conn.execute(
            "DELETE FROM conciliacion_conceptos WHERE conciliacion_id = ?",
            (conc_id,),
        )
        for p in data.get("partidas", []):
            conn.execute(
                """INSERT INTO conciliacion_partidas
                   (conciliacion_id, factura_folio, factura_id, monto_factura, comision, iva, total, envio, producto_no_enviado, total_envio, pago, documento_nombre, documento_monto)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    conc_id,
                    p.get("factura_folio", ""),
                    p.get("factura_id", ""),
                    p.get("monto_factura", 0),
                    p.get("comision", 0),
                    p.get("iva", 0),
                    p.get("total", 0),
                    p.get("envio", 0),
                    p.get("producto_no_enviado", 0),
                    p.get("total_envio", 0),
                    p.get("pago", 0),
                    p.get("documento_nombre", ""),
                    p.get("documento_monto", 0),
                ),
            )
        for c in data.get("conceptos", []):
            conn.execute(
                """INSERT INTO conciliacion_conceptos (conciliacion_id, nombre, descripcion, monto)
                   VALUES (?,?,?,?)""",
                (
                    conc_id,
                    c.get("nombre", ""),
                    c.get("descripcion", ""),
                    c.get("monto", 0),
                ),
            )

        conn.execute(
            "DELETE FROM conciliacion_visibilidad WHERE conciliacion_id = ?",
            (conc_id,),
        )
        for uid in data.get("visibilidad", []):
            conn.execute(
                "INSERT INTO conciliacion_visibilidad (conciliacion_id, user_id) VALUES (?,?)",
                (conc_id, uid),
            )

    _marcar_conciliadas_mysql(data.get("partidas", []), data.get("cliente_id", ""), conc_id)
    return {"ok": True, "id": conc_id}


@router.delete("/{conc_id}")
def eliminar_conciliacion(conc_id: int, user=Depends(require_user)):
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM conciliaciones WHERE id = ?", (conc_id,)
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Conciliacion no encontrada.")
        conn.execute("DELETE FROM conciliacion_partidas WHERE conciliacion_id = ?", (conc_id,))
        conn.execute("DELETE FROM conciliacion_visibilidad WHERE conciliacion_id = ?", (conc_id,))
        conn.execute("DELETE FROM conciliaciones WHERE id = ?", (conc_id,))
    return {"ok": True}


@router.post("/{conc_id}/documento")
async def subir_documento(
    conc_id: int,
    partida_id: int = Form(...),
    file: UploadFile = File(...),
    user=Depends(require_user),
):
    _ensure_docs_dir()
    ext = Path(file.filename or "doc").suffix.lower()
    if ext not in (".xml", ".pdf"):
        raise HTTPException(400, "Solo se aceptan archivos XML o PDF.")

    filename = f"{uuid.uuid4().hex}{ext}"
    dest = DOCS_DIR / filename
    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)

    monto_doc = 0.0
    if ext == ".xml":
        monto_doc = _parse_xml_amount(content)

    with get_connection() as conn:
        conn.execute(
            """UPDATE conciliacion_partidas
               SET documento_nombre = ?, documento_monto = ?
               WHERE id = ? AND conciliacion_id = ?""",
            (filename, monto_doc, partida_id, conc_id),
        )

    return {"ok": True, "filename": filename, "monto_documento": monto_doc}


@router.get("/documento/{filename}")
def descargar_documento(filename: str, user=Depends(require_user)):
    _ensure_docs_dir()
    path = DOCS_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Documento no encontrado.")
    return FileResponse(str(path))


@router.get("/{conc_id}/documento/{partida_id}")
def descargar_documento_partida(conc_id: int, partida_id: int, user=Depends(require_user)):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT documento_nombre FROM conciliacion_partidas WHERE id = ? AND conciliacion_id = ?",
            (partida_id, conc_id),
        ).fetchone()
    if not row or not row["documento_nombre"]:
        raise HTTPException(404, "Documento no encontrado.")
    return descargar_documento(row["documento_nombre"], user)


@router.get("/conciliadas/{cliente_numero}")
def listar_conciliadas(cliente_numero: str, user=Depends(require_user)):
    conn = get_legacy_connection()
    try:
        cur = conn.cursor()
        _mysql_conciliadas_table(cur)
        cur.execute(
            "SELECT factura_folio FROM conciliacion_conciliadas WHERE cliente_numero = %s",
            (cliente_numero,),
        )
        return [row[0] for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


@router.get("/{conc_id}/partidas")
def listar_partidas(conc_id: int, user=Depends(require_user)):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM conciliacion_partidas WHERE conciliacion_id = ? ORDER BY id",
            (conc_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/prorratear")
def prorratear(data: dict):
    monto_documento = data.get("monto_documento", 0)
    montos_factura = data.get("montos_factura", [])
    if not montos_factura or monto_documento <= 0:
        return {"partes": []}
    total_facturas = sum(montos_factura)
    if total_facturas <= 0:
        return {"partes": []}
    partes = []
    for m in montos_factura:
        partes.append(round(monto_documento * (m / total_facturas), 2))
    return {"partes": partes}
