import io
import json
import sys
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from PyPDF2 import PdfReader
except Exception:
    PdfReader = None

from app.legacy_db import get_legacy_connection
from app.routers import timbrado
from app.routers.health import legacy_mysql_health
from app.routers.timbrado import get_timbrado_connection
from app.routers.timbrado_core import _asegurar_tablas_timbrado
from app.routers.timbrado_pdf import generar_cfdi_pdf


CFDI_NS = "{http://www.sat.gob.mx/cfd/4}"


def money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def one(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()


def rows(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall() or []


def find_invoice(cur, preferred, where_clause):
    for folio in preferred:
        row = one(
            cur,
            f"""
            SELECT f.factura
            FROM facturas f
            WHERE f.factura = %s
              AND EXISTS (SELECT 1 FROM factura_detalle d WHERE d.factura_id = f.id)
              AND {where_clause}
            LIMIT 1
            """,
            (folio,),
        )
        if row:
            return str(row["factura"])
    row = one(
        cur,
        f"""
        SELECT f.factura
        FROM facturas f
        WHERE f.factura IS NOT NULL AND f.factura <> ''
          AND EXISTS (SELECT 1 FROM factura_detalle d WHERE d.factura_id = f.id)
          AND {where_clause}
        ORDER BY f.id DESC
        LIMIT 1
        """,
    )
    if not row:
        raise AssertionError(f"No hay factura para probar: {where_clause}")
    return str(row["factura"])


def cfdi_options(folio):
    try:
        return timbrado.ver_opciones_cfdi_folio(folio).get("opciones") or {}
    except Exception:
        return {
            "uso_cfdi": "G01",
            "forma_pago": "99",
            "metodo_pago": "PPD",
            "exportacion": "01",
            "moneda": "MXN",
        }


def prexml_for(folio):
    resp = timbrado.generar_prexml_cfdi_folio(folio, {"opciones_cfdi": cfdi_options(folio)})
    return resp.body.decode("utf-8") if isinstance(resp.body, bytes) else str(resp.body)


def assert_invoice_flow(folio, expects_discount):
    sim = timbrado.simular_cfdi_folio(folio, {})
    summary = sim["resumen_xml"]
    subtotal = money(summary["subtotal"])
    descuento = money(summary["descuento"])
    importe_sum = money(summary["importe_conceptos"])
    descuento_sum = money(summary["descuento_conceptos"])
    base_sum = money(summary["base_traslados"])
    if abs(importe_sum - subtotal) > Decimal("0.01"):
        raise AssertionError(f"{folio}: suma conceptos {importe_sum} != subtotal {subtotal}")
    if abs(descuento_sum - descuento) > Decimal("0.01"):
        raise AssertionError(f"{folio}: descuento conceptos {descuento_sum} != descuento {descuento}")
    if abs(base_sum - (subtotal - descuento)) > Decimal("0.01"):
        raise AssertionError(f"{folio}: base impuestos {base_sum} != subtotal-descuento {subtotal - descuento}")

    xml = prexml_for(folio)
    root = ET.fromstring(xml)
    if root.attrib.get("TipoDeComprobante") != "I":
        raise AssertionError(f"{folio}: el XML de factura no es comprobante I")
    conceptos = root.find(f"{CFDI_NS}Conceptos")
    if conceptos is None or len(list(conceptos)) == 0:
        raise AssertionError(f"{folio}: XML sin conceptos")

    emisor = root.find(f"{CFDI_NS}Emisor")
    empresa = emisor.attrib.get("Nombre", "") if emisor is not None else ""
    pdf = generar_cfdi_pdf(root, db_row={"factura": folio, "empresa": empresa}).getvalue()
    if not pdf.startswith(b"%PDF"):
        raise AssertionError(f"{folio}: PDF fiscal invalido")
    discount_visible = None
    if PdfReader is not None:
        text = "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(pdf)).pages)
        discount_visible = "% Desc" in text or "Descuento" in text
        if expects_discount and not discount_visible:
            raise AssertionError(f"{folio}: debe mostrar descuento en PDF")
        if not expects_discount and discount_visible:
            raise AssertionError(f"{folio}: no debe mostrar descuento en PDF")
    return {
        "folio": folio,
        "emitible": sim.get("emitible"),
        "faltantes": len((sim.get("validacion") or {}).get("faltantes") or []),
        "subtotal": str(subtotal),
        "descuento": str(descuento),
        "base_traslados": str(base_sum),
        "pdf_ok": True,
        "descuento_visible": discount_visible,
    }


def assert_cobranza_flow(recibo_id, tipo, payload):
    validacion = timbrado.validar_cfdi_cobranza(recibo_id, payload)
    bloqueo_fiscal = None
    if not validacion.get("ok"):
        bloqueo_fiscal = validacion.get("faltantes") or []
    sim = timbrado.simular_cfdi_cobranza(recibo_id, {**payload, "interno": True})
    if not sim.get("xml_generado"):
        raise AssertionError(f"Recibo {recibo_id}: no genero XML {sim.get('error_xml')}")
    summary = sim.get("resumen_xml") or {}
    if tipo == "PAGO":
        if summary.get("tipo_comprobante") != "P" or not summary.get("pagos20") or not summary.get("rep_tiene_totales"):
            raise AssertionError(f"Recibo {recibo_id}: REP incompleto {summary}")
    if tipo == "NOTA_CREDITO":
        if summary.get("tipo_comprobante") != "E":
            raise AssertionError(f"Recibo {recibo_id}: nota de credito debe ser E {summary}")
    return {
        "recibo_id": recibo_id,
        "tipo": tipo,
        "emitible_fiscal": bool(validacion.get("ok")),
        "bloqueo_fiscal": bloqueo_fiscal,
        "emitible_interno": sim.get("emitible"),
        "xml_generado": sim.get("xml_generado"),
        "resumen": summary,
    }


def main():
    report = {"health": {}, "facturacion": [], "cobranza": [], "cola": {}, "saldos_iniciales": {}}
    report["health"]["legacy_mysql"] = legacy_mysql_health()

    legacy = get_legacy_connection()
    cur = legacy.cursor(dictionary=True)
    try:
        no_discount = find_invoice(cur, ["00A32056"], "COALESCE(f.descuento, 0) = 0")
        with_discount = find_invoice(cur, ["9067"], "COALESCE(f.descuento, 0) > 0")
        report["facturacion"].append(assert_invoice_flow(no_discount, expects_discount=False))
        report["facturacion"].append(assert_invoice_flow(with_discount, expects_discount=True))

        pago = one(
            cur,
            """
            SELECT r.id
            FROM cobranza_recibos r
            WHERE r.estatus = 'ACTIVO' AND r.tipo_recibo = 'PAGO'
              AND EXISTS (
                SELECT 1 FROM cobranza_aplicaciones a
                WHERE a.recibo_id = r.id AND UPPER(a.origen_tipo) = 'FACTURA'
              )
            ORDER BY r.id DESC
            LIMIT 1
            """,
        )
        if pago:
            report["cobranza"].append(assert_cobranza_flow(int(pago["id"]), "PAGO", {"forma_pago": "03"}))

        nota = one(
            cur,
            """
            SELECT r.id
            FROM cobranza_recibos r
            WHERE r.estatus = 'ACTIVO' AND r.tipo_recibo = 'NOTA_CREDITO'
              AND EXISTS (
                SELECT 1 FROM cobranza_aplicaciones a
                WHERE a.recibo_id = r.id AND UPPER(a.origen_tipo) = 'FACTURA'
              )
            ORDER BY r.id DESC
            LIMIT 1
            """,
        )
        if nota:
            report["cobranza"].append(assert_cobranza_flow(int(nota["id"]), "NOTA_CREDITO", {"forma_pago": "99"}))

        saldos = one(
            cur,
            """
            SELECT COUNT(*) total, COALESCE(SUM(saldo_inicial), 0) saldo
            FROM cobranza_saldos_iniciales
            WHERE estatus = 'ACTIVO'
            """,
        )
        aplicaciones_saldos = one(
            cur,
            """
            SELECT COUNT(*) total
            FROM cobranza_aplicaciones
            WHERE UPPER(origen_tipo) <> 'FACTURA' OR saldo_inicial_id IS NOT NULL
            """,
        )
        report["saldos_iniciales"] = {
            "activos": int(saldos["total"] or 0),
            "saldo": str(money(saldos["saldo"] or 0)),
            "aplicaciones_no_timbrables": int(aplicaciones_saldos["total"] or 0),
        }
    finally:
        cur.close()
        legacy.close()

    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        queue = conn.execute(
            "SELECT estatus, COUNT(*) total FROM timbrado_queue GROUP BY estatus ORDER BY estatus"
        ).fetchall()
        report["cola"] = {str(row["estatus"]): int(row["total"]) for row in queue}
        bad = [
            row["estatus"]
            for row in queue
            if str(row["estatus"]).upper() in {"PENDIENTE", "TIMBRANDO", "BLOQUEADO_PAC", "ERROR"}
        ]
        if bad:
            raise AssertionError(f"Cola fiscal con estatus por atender: {bad}")

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
