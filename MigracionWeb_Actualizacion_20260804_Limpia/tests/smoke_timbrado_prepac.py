import io
import json
import os
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from PyPDF2 import PdfReader
except Exception:  # pragma: no cover - optional local helper
    PdfReader = None

from app.legacy_db import get_legacy_connection
from app.routers import timbrado
from app.routers.timbrado_core import _asegurar_tablas_timbrado, procesar_siguiente_timbrado
from app.routers.timbrado_pac import PacNoIntegradoError, _parse_openssl_cert_date, cancelar_cfdi_pac, consultar_estatus_cfdi_pac, diagnosticar_csd_config, obtener_acuse_cancelacion_pac, obtener_acuse_recepcion_pac, obtener_material_csd, preparar_paquete_pac, probar_conectividad_pac, proveedor_pac_integrado, sellar_xml_cfdi, timbrar_xml_pac, validar_preflight_pac
from app.routers.timbrado_pdf import generar_cfdi_pdf


CFDI_NS = "{http://www.sat.gob.mx/cfd/4}"


def money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def find_invoice(preferred, where_clause):
    conn = get_legacy_connection()
    cur = conn.cursor(dictionary=True)
    try:
        for folio in preferred:
            cur.execute(
                f"""
                SELECT f.factura
                FROM facturas f
                WHERE f.factura = %s AND EXISTS (
                    SELECT 1 FROM factura_detalle d WHERE d.factura_id = f.id
                ) AND {where_clause}
                LIMIT 1
                """,
                (folio,),
            )
            row = cur.fetchone()
            if row:
                return str(row["factura"])
        cur.execute(
            f"""
            SELECT f.factura
            FROM facturas f
            WHERE f.factura IS NOT NULL AND f.factura <> ''
              AND EXISTS (SELECT 1 FROM factura_detalle d WHERE d.factura_id = f.id)
              AND {where_clause}
            ORDER BY f.id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if not row:
            raise AssertionError(f"No invoice found for condition: {where_clause}")
        return str(row["factura"])
    finally:
        cur.close()
        conn.close()


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


def pdf_text(xml, folio):
    root = ET.fromstring(xml)
    emisor = root.find(f"{CFDI_NS}Emisor")
    empresa = emisor.attrib.get("Nombre", "") if emisor is not None else ""
    buf = generar_cfdi_pdf(root, db_row={"factura": folio, "empresa": empresa})
    data = buf.getvalue()
    if not data.startswith(b"%PDF"):
        raise AssertionError(f"PDF for {folio} does not start with PDF signature")
    if PdfReader is None:
        return ""
    return "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages)


def assert_summary_balances(folio, summary):
    subtotal = money(summary["subtotal"])
    descuento = money(summary["descuento"])
    importe_sum = money(summary["importe_conceptos"])
    descuento_sum = money(summary["descuento_conceptos"])
    base_sum = money(summary["base_traslados"])
    if abs(importe_sum - subtotal) > Decimal("0.01"):
        raise AssertionError(f"{folio}: concept sum {importe_sum} != subtotal {subtotal}")
    if abs(descuento_sum - descuento) > Decimal("0.01"):
        raise AssertionError(f"{folio}: discount sum {descuento_sum} != discount {descuento}")
    if abs(base_sum - (subtotal - descuento)) > Decimal("0.01"):
        raise AssertionError(f"{folio}: tax base {base_sum} != subtotal-discount {subtotal - descuento}")


class ConnWrap:
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def execute(self, *args, **kwargs):
        return self._inner.execute(*args, **kwargs)


def make_memory_timbrado_conn():
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    conn = ConnWrap(raw)
    _asegurar_tablas_timbrado(conn)
    return raw, conn


def assert_queue_idempotency_guard(conn):
    conn.execute(
        """
        INSERT INTO timbrado_queue (factura_id, factura, empresa, estatus)
        VALUES (?, ?, ?, ?)
        """,
        (90001, "IDEMP-1", "EZA2007", "PENDIENTE"),
    )
    conn.execute(
        """
        INSERT INTO cfdi_emitidos (factura_id, factura, empresa, serie, folio_cfdi, uuid, estatus_cfdi, xml_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (90001, "IDEMP-1", "EZA2007", "CFDI", "123", "UUID-IDEMP", "TIMBRADA", "x.xml"),
    )
    res = procesar_siguiente_timbrado(conn, None, folio="IDEMP-1")
    if not res.get("ya_timbrada") or res.get("uuid") != "UUID-IDEMP":
        raise AssertionError("Idempotency guard should return the already emitted CFDI")
    row = conn.execute("SELECT estatus, uuid, xml_path FROM timbrado_queue WHERE factura_id = ?", (90001,)).fetchone()
    if dict(row) != {"estatus": "TIMBRADA", "uuid": "UUID-IDEMP", "xml_path": "x.xml"}:
        raise AssertionError("Idempotency guard should reconcile queue with emitted CFDI")


def assert_company_queue_lock_guard(conn):
    conn.execute(
        "INSERT INTO timbrado_queue (factura_id, factura, empresa, estatus, last_attempt_at) VALUES (?, ?, ?, ?, ?)",
        (90002, "LOCK-1", "EZA2007", "TIMBRANDO", "2026-07-26 20:00:00"),
    )
    conn.execute(
        "INSERT INTO timbrado_queue (factura_id, factura, empresa, estatus) VALUES (?, ?, ?, ?)",
        (90003, "LOCK-2", "EZA2007", "PENDIENTE"),
    )
    res = procesar_siguiente_timbrado(conn, None, folio="LOCK-2")
    if not res.get("esperando_empresa") or res.get("factura_en_proceso") != "LOCK-1":
        raise AssertionError("Company queue lock should block parallel stamping for same company")
    row = conn.execute("SELECT estatus, intento_count FROM timbrado_queue WHERE factura_id = ?", (90003,)).fetchone()
    if dict(row) != {"estatus": "PENDIENTE", "intento_count": 0}:
        raise AssertionError("Blocked queue item should remain pending without consuming attempt")


def main():
    raw_timbrado, memory_timbrado = make_memory_timbrado_conn()
    try:
        assert_queue_idempotency_guard(memory_timbrado)
        assert_company_queue_lock_guard(memory_timbrado)
    finally:
        raw_timbrado.close()
    if _parse_openssl_cert_date("Jul 26 20:00:00 2027 GMT") is None:
        raise AssertionError("OpenSSL certificate date parser should parse GMT dates")
    public_cfg = timbrado._config_publica_timbrado({"csd_key_password": "csd-secret", "pac_password": "pac-secret"})
    if public_cfg.get("csd_key_password") != timbrado.SECRET_PLACEHOLDER or public_cfg.get("pac_password") != timbrado.SECRET_PLACEHOLDER:
        raise AssertionError("Timbrado config responses should mask stored secrets")
    resolved_cfg = timbrado._resolver_secretos_config(
        {"csd_key_password": "old-csd", "pac_password": "old-pac"},
        {"csd_key_password": timbrado.SECRET_PLACEHOLDER, "pac_password": "new-pac"},
    )
    if resolved_cfg.get("csd_key_password") != "old-csd" or resolved_cfg.get("pac_password") != "new-pac":
        raise AssertionError("Timbrado config updates should preserve masked secrets and accept new ones")
    no_discount = find_invoice(["00A32056"], "COALESCE(f.descuento, 0) = 0")
    with_discount = find_invoice(["9067"], "COALESCE(f.descuento, 0) > 0")

    results = []
    for folio, expects_discount in ((no_discount, False), (with_discount, True)):
        sim = timbrado.simular_cfdi_folio(folio, {})
        summary = sim["resumen_xml"]
        assert_summary_balances(folio, summary)
        xml = prexml_for(folio)
        text = pdf_text(xml, folio)
        if text:
            has_discount_column = "% Desc" in text
            has_discount_total = "Descuento" in text
            if expects_discount and not (has_discount_column and has_discount_total):
                raise AssertionError(f"{folio}: expected discount column and total in PDF")
            if not expects_discount and (has_discount_column or has_discount_total):
                raise AssertionError(f"{folio}: unexpected discount column or total in PDF")
        results.append(
            {
                "folio": folio,
                "emitible": sim["emitible"],
                "faltantes": len((sim.get("validacion") or {}).get("faltantes") or []),
                "subtotal": summary["subtotal"],
                "descuento": summary["descuento"],
                "base_traslados": summary["base_traslados"],
                "pdf_text_checked": bool(text),
            }
        )

    rep = timbrado.simular_cfdi_cobranza(40, {"forma_pago": "03", "interno": True})
    if rep.get("xml_generado"):
        rep_summary = rep["resumen_xml"]
        if not rep_summary.get("rep_tiene_totales") or rep_summary.get("documentos_relacionados", 0) <= 0:
            raise AssertionError("REP smoke test did not generate Pagos20 totals/documents")
        results.append({"recibo": 40, "rep": rep_summary})

    if not proveedor_pac_integrado("FINKOK"):
        raise AssertionError("FINKOK should be marked as integrated")
    sim_preflight = validar_preflight_pac({"proveedor": "SIMULADO"})
    if not sim_preflight.get("ok"):
        raise AssertionError("SIMULADO preflight should be ok")
    finkok_preflight = validar_preflight_pac({"proveedor": "FINKOK", "rfc_emisor": "AAA010101AAA"})
    if finkok_preflight.get("ok") or not finkok_preflight.get("errores"):
        raise AssertionError("FINKOK preflight should report missing CSD/PAC credentials")
    try:
        timbrar_xml_pac("SW SAPRO", {}, "<cfdi/>")
        raise AssertionError("Pending SW adapter should not return success")
    except PacNoIntegradoError:
        pass
    class FakeResponse:
        status_code = 200
        text = ""
        content = b"""<?xml version='1.0'?><senv:Envelope xmlns:senv='http://schemas.xmlsoap.org/soap/envelope/'><senv:Body><stampResponse xmlns='http://facturacion.finkok.com/stamp'><stampResult><xml>&lt;cfdi:Comprobante/&gt;</xml><UUID>11111111-2222-3333-4444-555555555555</UUID><CodEstatus>OK</CodEstatus></stampResult></stampResponse></senv:Body></senv:Envelope>"""
    import app.routers.timbrado_pac as pac_mod
    original_post = pac_mod.requests.post
    try:
        pac_mod.requests.post = lambda *args, **kwargs: FakeResponse()
        finkok = timbrar_xml_pac(
            "FINKOK",
            {"pac_url": "https://pac.example.test/stamp", "pac_usuario": "u", "pac_password": "p"},
            "<cfdi:Comprobante Sello=\"abc\"/>",
        )
        if finkok.uuid != "11111111-2222-3333-4444-555555555555" or not finkok.xml_timbrado:
            raise AssertionError("FINKOK mocked adapter did not parse UUID/XML")
    finally:
        pac_mod.requests.post = original_post
    class FakeCancelResponse:
        status_code = 200
        text = ""
        content = b"""<?xml version='1.0'?><senv:Envelope xmlns:senv='http://schemas.xmlsoap.org/soap/envelope/'><senv:Body><cancelResponse xmlns='http://facturacion.finkok.com/cancel'><cancelResult><Folios><Folio><UUID>11111111-2222-3333-4444-555555555555</UUID><EstatusUUID>201</EstatusUUID><EstatusCancelacion>Cancelado</EstatusCancelacion></Folio></Folios><CodEstatus>Solicitud recibida</CodEstatus><Acuse>&lt;acuse/&gt;</Acuse></cancelResult></cancelResponse></senv:Body></senv:Envelope>"""
    with tempfile.TemporaryDirectory() as tmpdir:
        cer_path = Path(tmpdir) / "csd.cer"
        key_path = Path(tmpdir) / "csd.key"
        cer_path.write_bytes(b"fake-cer")
        key_path.write_bytes(b"fake-key")
        original_cancel_credentials = pac_mod._credenciales_cancelacion_finkok
        try:
            pac_mod.requests.post = lambda *args, **kwargs: FakeCancelResponse()
            pac_mod._credenciales_cancelacion_finkok = lambda config: ("Y2Vy", "a2V5")
            cancel = cancelar_cfdi_pac(
                "FINKOK",
                {
                    "pac_cancel_url": "https://pac.example.test/cancel",
                    "pac_usuario": "u",
                    "pac_password": "p",
                    "rfc_emisor": "AAA010101AAA",
                    "csd_cer_path": str(cer_path),
                    "csd_key_path": str(key_path),
                },
                "11111111-2222-3333-4444-555555555555",
                "02",
            )
            if cancel.estatus_uuid != "201" or cancel.estatus_cancelacion != "Cancelado":
                raise AssertionError("FINKOK mocked cancellation did not parse SAT status")
        finally:
            pac_mod.requests.post = original_post
            pac_mod._credenciales_cancelacion_finkok = original_cancel_credentials
    try:
        cancelar_cfdi_pac("SW SAPRO", {}, "11111111-2222-3333-4444-555555555555", "02")
        raise AssertionError("Pending SW cancellation adapter should not return success")
    except PacNoIntegradoError:
        pass
    class FakeStatusResponse:
        status_code = 200
        text = ""
        content = b"""<?xml version='1.0'?><senv:Envelope xmlns:senv='http://schemas.xmlsoap.org/soap/envelope/'><senv:Body><get_sat_statusResponse xmlns='http://facturacion.finkok.com/cancel'><get_sat_statusResult><sat><CodigoEstatus>S - Comprobante obtenido satisfactoriamente.</CodigoEstatus><Estado>Vigente</Estado><EsCancelable>Cancelable sin aceptacion</EsCancelable><EstatusCancelacion></EstatusCancelacion><ValidacionEFOS>200</ValidacionEFOS></sat></get_sat_statusResult></get_sat_statusResponse></senv:Body></senv:Envelope>"""
    try:
        pac_mod.requests.post = lambda *args, **kwargs: FakeStatusResponse()
        status = consultar_estatus_cfdi_pac(
            "FINKOK",
            {"pac_cancel_url": "https://pac.example.test/cancel", "pac_usuario": "u", "pac_password": "p", "rfc_emisor": "AAA010101AAA"},
            "11111111-2222-3333-4444-555555555555",
            "XAXX010101000",
            "123.45",
        )
        if status.estado != "Vigente" or "Comprobante obtenido" not in status.codigo_estatus:
            raise AssertionError("FINKOK mocked status did not parse SAT response")
    finally:
        pac_mod.requests.post = original_post
    try:
        consultar_estatus_cfdi_pac("SW SAPRO", {}, "11111111-2222-3333-4444-555555555555", "XAXX010101000", "1.00")
        raise AssertionError("Pending SW status adapter should not return success")
    except PacNoIntegradoError:
        pass
    class FakeReceiptResponse:
        status_code = 200
        text = ""
        content = b"""<?xml version='1.0'?><senv:Envelope xmlns:senv='http://schemas.xmlsoap.org/soap/envelope/'><senv:Body><get_receiptResponse xmlns='http://facturacion.finkok.com/cancel'><get_receiptResult><uuid>11111111-2222-3333-4444-555555555555</uuid><success>true</success><receipt>&lt;Acuse Fecha=&quot;2026-07-25T15:00:00&quot;/&gt;</receipt><taxpayer_id>AAA010101AAA</taxpayer_id><date>2026-07-25T15:00:00</date></get_receiptResult></get_receiptResponse></senv:Body></senv:Envelope>"""
    try:
        captured = {}
        def fake_receipt_post(url, data=None, headers=None, timeout=None):
            captured["body"] = data.decode("utf-8")
            return FakeReceiptResponse()
        pac_mod.requests.post = fake_receipt_post
        receipt = obtener_acuse_cancelacion_pac(
            "FINKOK",
            {"pac_cancel_url": "https://pac.example.test/cancel", "pac_usuario": "u", "pac_password": "p", "rfc_emisor": "AAA010101AAA"},
            "11111111-2222-3333-4444-555555555555",
        )
        if "<Acuse" not in receipt.acuse or "<can:type>C</can:type>" not in captured["body"]:
            raise AssertionError("FINKOK mocked receipt did not parse acuse or send type C")
        receipt_r = obtener_acuse_recepcion_pac(
            "FINKOK",
            {"pac_cancel_url": "https://pac.example.test/cancel", "pac_usuario": "u", "pac_password": "p", "rfc_emisor": "AAA010101AAA"},
            "11111111-2222-3333-4444-555555555555",
        )
        if "<Acuse" not in receipt_r.acuse or "<can:type>R</can:type>" not in captured["body"]:
            raise AssertionError("FINKOK mocked receipt did not parse acuse or send type R")
    finally:
        pac_mod.requests.post = original_post
    try:
        obtener_acuse_cancelacion_pac("SW SAPRO", {}, "11111111-2222-3333-4444-555555555555")
        raise AssertionError("Pending SW receipt adapter should not return success")
    except PacNoIntegradoError:
        pass
    class FakeConnectivityResponse:
        status_code = 200
    original_get = pac_mod.requests.get
    try:
        pac_mod.requests.get = lambda *args, **kwargs: FakeConnectivityResponse()
        pac_conn = probar_conectividad_pac({"proveedor": "FINKOK", "pac_usuario": "u", "pac_password": "p", "modo_pruebas": True})
        if not pac_conn.get("ok") or "demo-facturacion.finkok.com" not in str(pac_conn.get("url") or ""):
            raise AssertionError("FINKOK connectivity should use default demo URL when pac_url is empty")
    finally:
        pac_mod.requests.get = original_get
    pac_package = preparar_paquete_pac("FINKOK", {"pac_usuario": "u", "pac_password": "p"}, "<xml Sello=\"abc\"/>")
    if not pac_package.get("ok") or pac_package.get("errores") or "xml_sha256" not in pac_package or "finkok.com" not in str(pac_package.get("url") or ""):
        raise AssertionError("PAC package dry-run should use FINKOK default URL and keep XML hash metadata")
    old_fu, old_fp = os.environ.get("FINKOK_USUARIO"), os.environ.get("FINKOK_PASSWORD")
    try:
        os.environ["FINKOK_USUARIO"] = "usuario-global"
        os.environ["FINKOK_PASSWORD"] = "password-global"
        pac_package_defaults = preparar_paquete_pac("FINKOK", {"proveedor": "FINKOK"}, "<xml Sello=\"abc\"/>")
        if not pac_package_defaults.get("ok") or pac_package_defaults.get("errores"):
            raise AssertionError("PAC package should use global FINKOK credentials when company credentials are empty")
    finally:
        if old_fu is None:
            os.environ.pop("FINKOK_USUARIO", None)
        else:
            os.environ["FINKOK_USUARIO"] = old_fu
        if old_fp is None:
            os.environ.pop("FINKOK_PASSWORD", None)
        else:
            os.environ["FINKOK_PASSWORD"] = old_fp
    with tempfile.TemporaryDirectory() as tmpdir:
        old_path = pac_mod.PAC_GLOBAL_CONFIG_PATH
        old_fu_file, old_fp_file = os.environ.get("FINKOK_USUARIO"), os.environ.get("FINKOK_PASSWORD")
        os.environ.pop("FINKOK_USUARIO", None)
        os.environ.pop("FINKOK_PASSWORD", None)
        try:
            pac_mod.PAC_GLOBAL_CONFIG_PATH = Path(tmpdir) / "global_config.json"
            pac_mod.guardar_config_pac_global({"pac_usuario": "u-file", "pac_password": "p-file"})
            pac_package_file_defaults = preparar_paquete_pac("FINKOK", {"proveedor": "FINKOK"}, "<xml Sello=\"abc\"/>")
            if not pac_package_file_defaults.get("ok") or pac_package_file_defaults.get("errores"):
                raise AssertionError("PAC package should use stored global PAC credentials when company credentials are empty")
        finally:
            pac_mod.PAC_GLOBAL_CONFIG_PATH = old_path
            if old_fu_file is not None:
                os.environ["FINKOK_USUARIO"] = old_fu_file
            if old_fp_file is not None:
                os.environ["FINKOK_PASSWORD"] = old_fp_file
    csd_diag = diagnosticar_csd_config({"rfc_emisor": "AAA010101AAA"})
    if not csd_diag.get("errores"):
        raise AssertionError("CSD diagnostic should report missing certificate/key/password")
    csd_material = obtener_material_csd({"rfc_emisor": "AAA010101AAA"})
    if csd_material.get("ok") or not csd_material.get("errores"):
        raise AssertionError("CSD material helper should fail cleanly when certificate is missing")
    sello_missing = sellar_xml_cfdi("<cfdi:Comprobante xmlns:cfdi=\"http://www.sat.gob.mx/cfd/4\" Version=\"4.0\"/>", {"rfc_emisor": "AAA010101AAA"})
    if sello_missing.get("ok") or not sello_missing.get("errores"):
        raise AssertionError("XML sealing helper should fail cleanly when CSD is missing")
    results.append({"pac_adapter": "pending providers block without consuming folio"})

    print(json.dumps({"ok": True, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
