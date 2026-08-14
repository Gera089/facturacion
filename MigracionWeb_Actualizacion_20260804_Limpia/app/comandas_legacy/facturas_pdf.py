from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
import mysql.connector
from datetime import datetime
import os
from pathlib import Path

from .pdf_factura import generar_pdf_factura_bytes
from app.routers.timbrado_core import get_timbrado_connection, obtener_config_timbrado

router = APIRouter()

DB_CFG = dict(
    host="100.69.142.19",
    user="Facturacion",
    password="ALD2013*",
    database="comandas_db",
    port=3307
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGOS_DIR = os.path.join(BASE_DIR, "logos")


def _logo_configurado_empresa(empresa: str) -> str:
    """Devuelve el archivo elegido en Soporte para la vista previa interna."""
    try:
        with get_timbrado_connection() as conn:
            nombre = Path(str((obtener_config_timbrado(conn, empresa) or {}).get("logo_archivo") or "")).name
        if not nombre:
            return ""
        bases = [Path(__file__).resolve().parents[3] / "logos", Path(LOGOS_DIR)]
        for base in bases:
            candidato = base / nombre
            if candidato.is_file():
                return str(candidato)
    except Exception:
        pass
    return ""


def _conn():
    return mysql.connector.connect(**DB_CFG)


def _fecha_impresa_es(dt: datetime) -> str:
    meses = {
        1: "ENE", 2: "FEB", 3: "MAR", 4: "ABR", 5: "MAY", 6: "JUN",
        7: "JUL", 8: "AGO", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DIC"
    }
    return f"{dt.day} {meses.get(dt.month, '')} {dt.year}".strip().upper()


def _limpiar_codigo_barra(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    if "." in s:
        s = s.split(".", 1)[0]
    return s


def db_get_factura_por_folio(folio: str) -> dict | None:
    conn = _conn()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT *
            FROM facturas
            WHERE factura = %s
            LIMIT 1
        """, (folio,))
        return cur.fetchone()
    finally:
        try:
            if cur:
                cur.close()
        except:
            pass
        conn.close()


def db_get_factura_detalle(factura_id: int) -> list[dict]:
    conn = _conn()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT cip, descripcion, cantidad, piezas, precio,
                   COALESCE(descuento_pct, 0) AS descuento_pct,
                   COALESCE(descuento_importe, 0) AS descuento_importe
            FROM factura_detalle
            WHERE factura_id = %s
            ORDER BY id ASC
        """, (factura_id,))
        return cur.fetchall() or []
    finally:
        try:
            if cur:
                cur.close()
        except:
            pass
        conn.close()


def db_get_cliente(numero_cliente: str, empresa: str) -> dict:
    conn = _conn()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT *
            FROM clientes
            WHERE numero = %s AND empresa = %s
            LIMIT 1
        """, (numero_cliente, empresa))
        return cur.fetchone() or {}
    finally:
        try:
            if cur:
                cur.close()
        except:
            pass
        conn.close()


def db_get_productos_info_por_cips(cips: list[str], empresa: str) -> dict:
    """
    Trae unidad y código de barras para TODOS los CIPs en una sola pasada.
    Devuelve:
    {
        "123": {"unidad": "KG", "codigo_barras": "750..."},
        ...
    }
    """
    cips = [str(c).strip() for c in cips if str(c).strip()]
    if not cips:
        return {}

    resultado = {}

    conn = _conn()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)

        # 1) Productos base
        placeholders = ",".join(["%s"] * len(cips))
        cur.execute(f"""
            SELECT cip, unidad, codigo_barras, descuento
            FROM productos
            WHERE cip IN ({placeholders})
        """, tuple(cips))

        for row in (cur.fetchall() or []):
            cip = (row.get("cip") or "").strip()
            if not cip:
                continue
            resultado[cip] = {
                "unidad": (row.get("unidad") or "").strip().upper(),
                "codigo_barras": _limpiar_codigo_barra(row.get("codigo_barras"))
            }

        # Asegurar todos los cips
        for cip in cips:
            resultado.setdefault(cip, {"unidad": "", "codigo_barras": ""})

        # 2) Intentar enriquecer códigos desde precios, si existe esa tabla/estructura
        try:
            emp_low = (empresa or "").lower()
            gourmet_es = ("gourmet españa" in emp_low) or ("gourmet espana" in emp_low)

            if gourmet_es:
                # Prioriza listas tipo España/Gourmet
                cur.execute(f"""
                    SELECT cip, codigo_barras
                    FROM precios
                    WHERE cip IN ({placeholders})
                      AND codigo_barras IS NOT NULL
                      AND codigo_barras <> ''
                    ORDER BY
                        CASE
                            WHEN LOWER(lista) LIKE '%%espa%%' THEN 0
                            WHEN LOWER(lista) LIKE '%%gourmet%%' THEN 1
                            ELSE 2
                        END,
                        cip
                """, tuple(cips))
            else:
                cur.execute(f"""
                    SELECT cip, codigo_barras
                    FROM precios
                    WHERE cip IN ({placeholders})
                      AND codigo_barras IS NOT NULL
                      AND codigo_barras <> ''
                    ORDER BY cip
                """, tuple(cips))

            rows_precios = cur.fetchall() or []

            # Para cada CIP, nos quedamos con el primer código encontrado según el ORDER BY
            for row in rows_precios:
                cip = (row.get("cip") or "").strip()
                if not cip or cip not in resultado:
                    continue
                if not resultado[cip]["codigo_barras"]:
                    resultado[cip]["codigo_barras"] = _limpiar_codigo_barra(row.get("codigo_barras"))

        except Exception:
            # Si la tabla precios o columna lista no existe, no rompemos
            pass

        return resultado

    finally:
        try:
            if cur:
                cur.close()
        except:
            pass
        conn.close()


@router.get("/facturas/pdf/{folio}")
def factura_pdf(folio: str):
    factura = db_get_factura_por_folio(folio)
    if not factura:
        raise HTTPException(status_code=404, detail="Factura no encontrada")

    factura_id = int(factura.get("id"))
    empresa = (factura.get("empresa") or "").strip()
    numero_cliente = (factura.get("numero_cliente") or "").strip()

    detalle = db_get_factura_detalle(factura_id)
    cliente_info = db_get_cliente(numero_cliente, empresa)

    fecha = factura.get("fecha")
    if isinstance(fecha, str):
        try:
            fecha_dt = datetime.fromisoformat(fecha.replace("Z", ""))
        except Exception:
            fecha_dt = datetime.now()
    else:
        fecha_dt = fecha if isinstance(fecha, datetime) else datetime.now()

    # ✅ Traer info de productos en bloque
    cips = [(p.get("cip") or "").strip() for p in detalle if (p.get("cip") or "").strip()]
    productos_info = db_get_productos_info_por_cips(cips, empresa)

    productos = []
    for p in detalle:
        cip = (p.get("cip") or "").strip()
        info_prod = productos_info.get(cip, {})
        unidad = info_prod.get("unidad", "")
        codigo_barras = info_prod.get("codigo_barras", "")
        indicador_descuento = str(info_prod.get("descuento") or "").strip().lower()
        aplica_descuento = indicador_descuento in {"si", "sí", "s", "1", "true", "t", "yes", "y"}

        cantidad = float(p.get("cantidad") or 0)
        precio = float(p.get("precio") or 0)
        total_linea = cantidad * precio

        productos.append({
            "cip": cip,
            "descripcion": p.get("descripcion") or "",
            "cantidad": cantidad,
            "piezas": float(p.get("piezas") or 0),
            "precio": precio,
            "total": total_linea,
            "unidad": unidad,
            "codigo_barras": codigo_barras,
            # Una vez guardada, la partida conserva su propio descuento.
            # Las facturas históricas sin esta columna siguen usando el
            # indicador del catálogo como compatibilidad.
            "descuento_pct": float(p.get("descuento_pct") or 0) if float(p.get("descuento_pct") or 0) > 0 else (float(factura.get("descuento_pct") or 0) if aplica_descuento else 0.0),
        })

    payload = {
        "folio": folio,
        "factura": folio,
        "empresa": empresa,
        "cliente_nombre": (factura.get("consignatario") or ""),
        "cliente_numero": numero_cliente,
        "vendedor": factura.get("vendedor", "") or "",
        "rfc": (cliente_info.get("rfc") or factura.get("rfc") or "").strip(),
        "subtotal": float(factura.get("subtotal") or 0),
        "descuento_pct": float(factura.get("descuento_pct") or 0),
        "descuento_total": float(factura.get("descuento") or 0),
        "iva": float(factura.get("iva") or 0),
        "total": float(factura.get("total") or 0),
        "consignatario": (factura.get("consignatario") or cliente_info.get("consignatario") or ""),
        "numero_salida": (factura.get("numero_salida") or ""),
        "fecha_impresa": _fecha_impresa_es(fecha_dt),
        "cliente_info": cliente_info,
        "productos": productos,
        "logo_path": _logo_configurado_empresa(empresa),
    }

    pdf_bytes = generar_pdf_factura_bytes(payload, logos_dir=LOGOS_DIR)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename=\"Factura_{folio}.pdf\"'}
    )

@router.get("/facturas/por-comanda/{folio_comanda}")
def get_facturas_por_comanda(folio_comanda: str):
    conn = _conn()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                factura,
                fecha,
                numero_cliente,
                consignatario,
                subtotal,
                descuento,
                iva,
                total,
                empresa,
                numero_salida
            FROM facturas
            WHERE numero_salida = %s
            ORDER BY fecha ASC, factura ASC
        """, (folio_comanda,))

        rows = cursor.fetchall()
        return rows or []

    finally:
        cursor.close()
        conn.close()
