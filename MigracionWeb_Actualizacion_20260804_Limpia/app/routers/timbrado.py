import json
import io
import base64
import os
import re
import shutil
import smtplib
import unicodedata
import uuid
import zipfile
import xml.etree.ElementTree as ET
from html import escape as html_escape
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.core.config import settings
from app.routers.timbrado_core import get_timbrado_connection
from app.legacy_db import LEGACY_CFG, get_legacy_connection, _host_is_reachable, _mysql_hosts, _parse_host
from app.routers.timbrado_pac import (
    PacNoIntegradoError,
    PacTimbradoError,
    aplicar_defaults_pac,
    cancelar_cfdi_pac,
    cargar_config_pac_global,
    consultar_estatus_cfdi_pac,
    diagnosticar_csd_config,
    guardar_config_pac_global,
    obtener_material_csd,
    preparar_paquete_pac,
    probar_conectividad_pac,
    proveedor_pac_integrado,
    obtener_acuse_cancelacion_pac,
    obtener_acuse_recepcion_pac,
    sellar_xml_cfdi,
    timbrar_xml_pac,
    validar_preflight_pac,
)
from app.routers.timbrado_core import (
    _asegurar_tablas_timbrado,
    _asegurar_tabla_correo_documentos,
    _asegurar_tabla_cfdi_defaults_clientes,
    _normalizar_empresa,
    _obtener_siguiente_folio,
    _avanzar_folio_empresa,
    ruta_empresa_fiscal,
    _snapshot_factura,
    _generar_cfdi_simulado_xml,
    buscar_cliente_nombre,
    buscar_clientes_por_term,
    eliminar_addenda_cliente,
    eliminar_consignatario_cliente,
    eliminar_grupo_clientes_timbrado,
    eliminar_regla_redireccion,
    eliminar_receptor_fiscal,
    guardar_addenda_cliente,
    guardar_campos_addenda_factura,
    guardar_consignatario_cliente,
    guardar_config_timbrado,
    guardar_grupo_clientes_timbrado,
    guardar_producto_fiscal,
    guardar_productos_fiscales_lote,
    guardar_regla_redireccion,
    guardar_receptor_fiscal,
    importar_receptores_fiscales,
    importar_catalogo_sat_prodserv,
    importar_catalogo_sat_unidades,
    listar_addendas_clientes_configuradas,
    listar_addendas_disponibles,
    listar_catalogo_sat_prodserv as listar_catalogo_prodserv,
    listar_catalogo_sat_unidades as listar_catalogo_unidades,
    listar_cfdi_emitidos,
    listar_consignatarios_clientes,
    listar_grupos_clientes_timbrado,
    listar_cola_timbrado,
    listar_intentos_pac,
    listar_productos_fiscales,
    listar_reglas_redireccion,
    listar_receptores_fiscales,
    obtener_addenda_cliente,
    obtener_campos_addenda_factura,
    obtener_consignatario_cliente,
    obtener_config_timbrado,
    obtener_grupo_clientes_timbrado,
    obtener_receptor_fiscal,
    actualizar_correo_receptor_fiscal,
    registrar_intento_pac,
    consolidar_facturas_timbrado,
    procesar_siguiente_timbrado,
    renderizar_addenda_factura,
    resolver_receptor_timbrado,
    sincronizar_factura_para_timbrado,
    validar_pre_cfdi_factura,
)

router = APIRouter(prefix="/timbrado", tags=["Timbrado"])
# Los CSD pertenecen a los datos persistentes del servidor, nunca al paquete
# interno de PyInstaller: una actualización del programa no debe borrarlos.
CSD_STORAGE_DIR = settings.storage_dir / "csd"
SECRET_PLACEHOLDER = "********"


def _money(value) -> Decimal:
    """Monto decimal para CFDI; evita las imprecisiones de float."""
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _cfdi_amount(value) -> str:
    return f"{_money(value):.2f}"


def _cfdi_rate(value) -> str:
    try:
        return f"{Decimal(str(value or 0)).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP):.6f}"
    except Exception:
        return "0.000000"


def _to_int(value, default=0):
    try:
        if isinstance(value, str):
            value = value.strip()
            if value.endswith(".0"):
                value = value[:-2]
        return int(value or 0)
    except Exception:
        return default


def _cfdi_datetime(value, fallback: str | None = None, noon_for_date: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    text = text.replace("Z", "")
    candidates = [
        ("%Y-%m-%dT%H:%M:%S", text[:19]),
        ("%Y-%m-%d %H:%M:%S", text[:19]),
        ("%Y-%m-%d", text[:10]),
        ("%d/%m/%Y %H:%M:%S", text[:19]),
        ("%d/%m/%Y", text[:10]),
    ]
    for fmt, candidate in candidates:
        try:
            dt = datetime.strptime(candidate, fmt)
            if fmt in ("%Y-%m-%d", "%d/%m/%Y") and noon_for_date:
                dt = dt.replace(hour=12)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            continue
    return fallback or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _asegurar_tabla_snapshots_timbrado(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timbrado_config_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa VARCHAR(120) NOT NULL,
            etiqueta VARCHAR(255) DEFAULT '',
            config_json LONGTEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_timb_snap_empresa ON timbrado_config_snapshots (empresa)",
        "CREATE INDEX IF NOT EXISTS idx_timb_snap_created ON timbrado_config_snapshots (created_at)",
    ]:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass


def _config_publica_timbrado(cfg: dict | None) -> dict:
    data = dict(cfg or {})
    rfc = str(data.get("rfc_emisor") or "").strip().upper()
    nc_defaults = {
        "nota_credito_no_identificacion": "NC006",
        "nota_credito_clave_unidad": "ACT",
        "nota_credito_unidad": "pz",
        "nota_credito_descripcion": "DESCUENTO SOBRE VENTAS",
        "nota_credito_metodo_pago_99": "PPD",
    }
    if rfc == "GES090312DJ1":
        nc_defaults.update({
            "nota_credito_no_identificacion": "NCD",
            "nota_credito_clave_unidad": "H87",
            "nota_credito_descripcion": "DESCUENTO SOBRE COMPRAS",
        })
    for campo, valor in nc_defaults.items():
        if not str(data.get(campo) or "").strip():
            data[campo] = valor
    if not str(data.get("folio_complemento_pago") or "").strip():
        data["folio_complemento_pago"] = "1"
    if not str(data.get("folio_nota_credito") or "").strip():
        data["folio_nota_credito"] = "1"
    for campo in ("csd_key_password", "pac_password", "pac_cancel_passphrase"):
        tiene = bool(str(data.get(campo) or "").strip())
        data[f"{campo}_configured"] = tiene
        if tiene:
            data[campo] = SECRET_PLACEHOLDER
    return data


def _resolver_secretos_config(actual: dict | None, datos: dict | None) -> dict:
    merged = dict(datos or {})
    actual = actual or {}
    for campo in ("csd_key_password", "pac_password", "pac_cancel_passphrase"):
        val = str(merged.get(campo) or "")
        if val == SECRET_PLACEHOLDER:
            merged[campo] = actual.get(campo) or ""
    # Un guardado parcial de la pantalla de configuración (por ejemplo, al
    # ejecutar un diagnóstico PAC) no debe borrar las rutas de un CSD ya
    # cargado. Los archivos se modifican exclusivamente por los endpoints de
    # carga de CER/KEY; una ruta vacía aquí significa "conservar la actual".
    for campo in ("csd_cer_path", "csd_key_path"):
        if not str(merged.get(campo) or "").strip() and str(actual.get(campo) or "").strip():
            merged[campo] = actual[campo]
    return merged


def _pac_global_publica(cfg: dict | None) -> dict:
    data = dict(cfg or {})
    tiene = bool(str(data.get("pac_password") or "").strip())
    data["pac_password_configured"] = tiene
    if tiene:
        data["pac_password"] = SECRET_PLACEHOLDER
    return data


@router.get("/pac/global-config")
def ver_config_pac_global():
    return _pac_global_publica(cargar_config_pac_global())


@router.put("/pac/global-config")
def guardar_config_pac_global_endpoint(datos: dict):
    actual = cargar_config_pac_global()
    payload = dict(datos or {})
    if str(payload.get("pac_password") or "") == SECRET_PLACEHOLDER:
        payload["pac_password"] = actual.get("pac_password") or ""
    cfg = guardar_config_pac_global(payload)
    return {"ok": True, "mensaje": "Credenciales PAC globales guardadas.", "config": _pac_global_publica(cfg)}


def _asegurar_tabla_cfdi_cobranza(conn):
    """Documentos fiscales de cobranza separados de las facturas de venta."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cfdi_cobranza_emitidos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            recibo_id INT NOT NULL,
            tipo_documento VARCHAR(24) NOT NULL,
            factura VARCHAR(80) NOT NULL,
            empresa VARCHAR(120) NOT NULL,
            cliente_receptor_numero VARCHAR(60) DEFAULT '',
            cliente_receptor_nombre VARCHAR(255) DEFAULT '',
            serie VARCHAR(40) DEFAULT '',
            folio_cfdi VARCHAR(60) DEFAULT '',
            uuid VARCHAR(36) DEFAULT '',
            estatus_cfdi VARCHAR(20) NOT NULL DEFAULT 'TIMBRADA',
            xml_path TEXT,
            pdf_path TEXT,
            acuse_recepcion_path TEXT,
            acuse_cancelacion_path TEXT,
            fecha_timbrado DATETIME NULL,
            forma_pago VARCHAR(5) DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY ux_cfdi_cobranza_recibo_tipo (recibo_id, tipo_documento),
            UNIQUE KEY ux_cfdi_cobranza_folio (folio_cfdi),
            INDEX idx_cfdi_cobranza_empresa (empresa)
        )
    """)
    try:
        indices = conn.execute("SHOW INDEX FROM cfdi_cobranza_emitidos").fetchall()
        nombres = {str(dict(row).get("Key_name") or "") for row in indices}
        if "ux_cfdi_cobranza_folio" in nombres:
            conn.execute("ALTER TABLE cfdi_cobranza_emitidos DROP INDEX ux_cfdi_cobranza_folio")
        if "ux_cfdi_cobranza_empresa_tipo_serie_folio" not in nombres:
            conn.execute(
                "CREATE UNIQUE INDEX ux_cfdi_cobranza_empresa_tipo_serie_folio "
                "ON cfdi_cobranza_emitidos (empresa, tipo_documento, serie, folio_cfdi)"
            )
    except Exception:
        pass
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_cfdi_cobranza_factura ON cfdi_cobranza_emitidos (factura)",
        "CREATE INDEX IF NOT EXISTS idx_cfdi_cobranza_empresa_fecha ON cfdi_cobranza_emitidos (empresa, fecha_timbrado)",
    ]:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass
    try:
        rows = conn.execute("SHOW COLUMNS FROM cfdi_cobranza_emitidos").fetchall()
        columnas = {str(dict(row).get("Field") or "").lower() for row in rows}
        if "pdf_path" not in columnas:
            conn.execute("ALTER TABLE cfdi_cobranza_emitidos ADD COLUMN pdf_path TEXT NULL")
        if "acuse_recepcion_path" not in columnas:
            conn.execute("ALTER TABLE cfdi_cobranza_emitidos ADD COLUMN acuse_recepcion_path TEXT NULL")
        if "acuse_cancelacion_path" not in columnas:
            conn.execute("ALTER TABLE cfdi_cobranza_emitidos ADD COLUMN acuse_cancelacion_path TEXT NULL")
    except Exception:
        try:
            rows = conn.execute("PRAGMA table_info(cfdi_cobranza_emitidos)").fetchall()
            columnas = {str(dict(row).get("name") or "").lower() for row in rows}
            if "pdf_path" not in columnas:
                conn.execute("ALTER TABLE cfdi_cobranza_emitidos ADD COLUMN pdf_path TEXT")
            if "acuse_recepcion_path" not in columnas:
                conn.execute("ALTER TABLE cfdi_cobranza_emitidos ADD COLUMN acuse_recepcion_path TEXT")
            if "acuse_cancelacion_path" not in columnas:
                conn.execute("ALTER TABLE cfdi_cobranza_emitidos ADD COLUMN acuse_cancelacion_path TEXT")
        except Exception:
            pass


def _normalizar_xml_nombre_timbrado(nombre: str) -> str:
    nombre = str(nombre or "").replace("\\", "/").strip()
    if not nombre:
        return ""
    return os.path.basename(nombre)


def _leer_zip_xmls_timbrado(xml_zip: UploadFile | None) -> dict[str, bytes]:
    if not xml_zip:
        return {}
    contenido = xml_zip.file.read() if xml_zip.file else b""
    if not contenido:
        return {}
    xmls: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".xml"):
                    continue
                nombre = _normalizar_xml_nombre_timbrado(info.filename)
                if nombre:
                    xmls[nombre.lower()] = zf.read(info)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="El archivo ZIP de XML no es válido.")
    return xmls


def _xml_attr(node, attr: str, default: str = "") -> str:
    if node is None:
        return default
    return str(node.attrib.get(attr) or default).strip()


def _parsear_cfdi_cobranza_importado(xml_bytes: bytes) -> dict:
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"XML inválido: {exc}")
    ns = {
        "cfdi": "http://www.sat.gob.mx/cfd/4",
        "tfd": "http://www.sat.gob.mx/TimbreFiscalDigital",
        "pago20": "http://www.sat.gob.mx/Pagos20",
    }
    receptor = root.find("cfdi:Receptor", ns)
    tfd = root.find(".//tfd:TimbreFiscalDigital", ns)
    tipo_cfdi = str(root.attrib.get("TipoDeComprobante") or "").upper()
    tipo_documento = "PAGO" if tipo_cfdi == "P" else "NOTA_CREDITO" if tipo_cfdi == "E" else tipo_cfdi
    forma_pago = str(root.attrib.get("FormaPago") or "").strip()
    fecha_pago = ""
    doctos = []
    if tipo_documento == "PAGO":
        pago = root.find(".//pago20:Pago", ns)
        forma_pago = _xml_attr(pago, "FormaDePagoP", forma_pago)
        fecha_pago = _xml_attr(pago, "FechaPago")
        for docto in root.findall(".//pago20:DoctoRelacionado", ns):
            serie = _xml_attr(docto, "Serie")
            folio = _xml_attr(docto, "Folio")
            doctos.append({
                "serie": serie,
                "folio": folio,
                "factura": f"{serie}{folio}".strip() or _xml_attr(docto, "IdDocumento"),
                "uuid": _xml_attr(docto, "IdDocumento"),
                "pagado": _money(_xml_attr(docto, "ImpPagado") or 0),
                "saldo_anterior": _money(_xml_attr(docto, "ImpSaldoAnt") or 0),
                "saldo": _money(_xml_attr(docto, "ImpSaldoInsoluto") or 0),
            })
    elif tipo_documento == "NOTA_CREDITO":
        for rel in root.findall(".//cfdi:CfdiRelacionado", ns):
            doctos.append({"uuid": _xml_attr(rel, "UUID"), "factura": _xml_attr(rel, "UUID")})
    return {
        "tipo_documento": tipo_documento,
        "tipo_cfdi": tipo_cfdi,
        "serie": str(root.attrib.get("Serie") or "").strip(),
        "folio": str(root.attrib.get("Folio") or "").strip(),
        "uuid": _xml_attr(tfd, "UUID").upper(),
        "fecha": str(root.attrib.get("Fecha") or "").strip(),
        "fecha_timbrado": _xml_attr(tfd, "FechaTimbrado") or str(root.attrib.get("Fecha") or "").strip(),
        "total": _money(root.attrib.get("Total") or 0),
        "forma_pago": forma_pago,
        "fecha_pago": fecha_pago,
        "rfc_receptor": _xml_attr(receptor, "Rfc"),
        "cliente_receptor_nombre": _xml_attr(receptor, "Nombre"),
        "doctos": doctos,
    }


def _guardar_xml_cobranza_externo(empresa: str, tipo: str, serie: str, folio: str, nombre: str, xml_bytes: bytes) -> str:
    safe_empresa = re.sub(r"[^A-Za-z0-9_.-]+", "_", _normalizar_empresa(empresa) or "empresa").strip("._") or "empresa"
    safe_tipo = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(tipo or "CFDI")).strip("._") or "CFDI"
    destino_dir = settings.storage_dir / "cfdi" / safe_empresa / "externos" / safe_tipo
    destino_dir.mkdir(parents=True, exist_ok=True)
    nombre_base = _normalizar_xml_nombre_timbrado(nombre) or f"{serie}{folio}.xml" or "cfdi.xml"
    destino = destino_dir / nombre_base
    if destino.exists():
        stem = destino.stem
        suffix = destino.suffix or ".xml"
        destino = destino_dir / f"{stem}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}{suffix}"
    destino.write_bytes(xml_bytes)
    return str(destino)


def _buscar_xml_importado(row: dict, xmls_zip: dict[str, bytes], xmls_por_uuid: dict[str, tuple[str, bytes]]) -> tuple[str, bytes] | None:
    xml_nombre = _normalizar_xml_nombre_timbrado(row.get("xml") or row.get("xml_nombre") or row.get("archivo_xml") or row.get("nombre_xml") or "")
    if xml_nombre and xml_nombre.lower() in xmls_zip:
        return xml_nombre, xmls_zip[xml_nombre.lower()]
    uuid_solicitado = str(row.get("uuid") or row.get("uuid_cfdi") or "").strip().upper()
    if uuid_solicitado and uuid_solicitado in xmls_por_uuid:
        return xmls_por_uuid[uuid_solicitado]
    serie = str(row.get("serie") or "").strip().upper()
    folio = str(row.get("folio") or row.get("folio_cfdi") or "").strip().upper()
    factura = str(row.get("factura") or row.get("documento") or "").strip().upper()
    candidatos = [x for x in (f"{serie}{folio}", folio, factura) if x]
    for nombre, contenido in xmls_zip.items():
        base = os.path.splitext(nombre)[0].upper()
        if any(c in base or base in c for c in candidatos):
            return nombre, contenido
    return None


@router.post("/cobranza/importar-cfdi-externos")
async def importar_cfdi_cobranza_externos(
    payload: str = Form(...),
    xml_zip: UploadFile | None = File(default=None),
):
    try:
        datos = json.loads(payload or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Payload de importación inválido.")
    rows = datos.get("rows") or []
    empresa_default = str(datos.get("empresa") or "").strip()
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=400, detail="No se recibieron filas para importar.")
    xmls_zip = _leer_zip_xmls_timbrado(xml_zip)
    xmls_por_uuid = {}
    for nombre, contenido in xmls_zip.items():
        try:
            info = _parsear_cfdi_cobranza_importado(contenido)
            if info.get("uuid"):
                xmls_por_uuid[info["uuid"]] = (nombre, contenido)
        except Exception:
            continue
    importados = omitidos = duplicados = vinculados = 0
    faltantes = []
    detalles = []
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        _asegurar_tabla_cfdi_cobranza(conn)
        legacy = getattr(conn, "_conn", None)
        cur = legacy.cursor(dictionary=True) if legacy else None
        min_externo = conn.execute("SELECT COALESCE(MIN(recibo_id), 0) AS min_id FROM cfdi_cobranza_emitidos WHERE recibo_id < 0").fetchone()
        siguiente_recibo_externo = int(dict(min_externo or {}).get("min_id") or 0)
        if siguiente_recibo_externo >= 0:
            siguiente_recibo_externo = -1
        try:
            for row in rows:
                if not isinstance(row, dict):
                    omitidos += 1
                    continue
                encontrado = _buscar_xml_importado(row, xmls_zip, xmls_por_uuid)
                if not encontrado:
                    ref = row.get("xml_nombre") or row.get("uuid") or row.get("factura") or row.get("folio") or "fila sin referencia"
                    faltantes.append(str(ref))
                    omitidos += 1
                    continue
                xml_nombre, xml_bytes = encontrado
                info = _parsear_cfdi_cobranza_importado(xml_bytes)
                tipo = str(row.get("tipo") or info.get("tipo_documento") or "").strip().upper()
                if tipo in ("REP", "COMPLEMENTO", "COMPLEMENTO_PAGO"):
                    tipo = "PAGO"
                if tipo in ("NC", "NOTA", "EGRESO"):
                    tipo = "NOTA_CREDITO"
                if tipo not in ("PAGO", "NOTA_CREDITO"):
                    omitidos += 1
                    detalles.append({"xml": xml_nombre, "error": "Tipo de CFDI no soportado"})
                    continue
                empresa = _normalizar_empresa(row.get("empresa") or empresa_default)
                if not empresa and cur:
                    rfc_emisor = ""
                    try:
                        root = ET.fromstring(xml_bytes)
                        emisor = root.find("{http://www.sat.gob.mx/cfd/4}Emisor")
                        rfc_emisor = _xml_attr(emisor, "Rfc")
                    except Exception:
                        pass
                    if rfc_emisor:
                        erow = conn.execute("SELECT empresa FROM empresas_timbrado WHERE UPPER(rfc_emisor)=UPPER(?) LIMIT 1", (rfc_emisor,)).fetchone()
                        empresa = _normalizar_empresa(dict(erow).get("empresa") if erow else "")
                if not empresa:
                    omitidos += 1
                    detalles.append({"xml": xml_nombre, "error": "Empresa no encontrada"})
                    continue
                serie = str(row.get("serie") or info.get("serie") or "").strip()
                folio = str(row.get("folio") or row.get("folio_cfdi") or info.get("folio") or "").strip()
                uuid_cfdi = str(row.get("uuid") or info.get("uuid") or "").strip().upper()
                if not folio and not uuid_cfdi:
                    omitidos += 1
                    detalles.append({"xml": xml_nombre, "error": "Sin folio ni UUID"})
                    continue
                existente = None
                if uuid_cfdi:
                    existente = conn.execute("SELECT id FROM cfdi_cobranza_emitidos WHERE uuid = ? LIMIT 1", (uuid_cfdi,)).fetchone()
                if not existente and folio:
                    existente = conn.execute(
                        "SELECT id FROM cfdi_cobranza_emitidos WHERE empresa = ? AND tipo_documento = ? AND serie = ? AND folio_cfdi = ? LIMIT 1",
                        (empresa, tipo, serie, folio),
                    ).fetchone()
                if existente:
                    duplicados += 1
                    continue
                recibo_id = _to_int(row.get("recibo_id") or 0)
                if not recibo_id and cur:
                    folio_recibo = str(row.get("recibo") or row.get("folio_recibo") or row.get("recibo_folio") or "").strip()
                    if folio_recibo:
                        cur.execute("SELECT id FROM cobranza_recibos WHERE folio = %s LIMIT 1", (folio_recibo,))
                        recibo = cur.fetchone()
                        recibo_id = int((recibo or {}).get("id") or 0)
                if recibo_id:
                    vinculado = conn.execute("SELECT id FROM cfdi_cobranza_emitidos WHERE recibo_id = ? AND tipo_documento = ? LIMIT 1", (recibo_id, tipo)).fetchone()
                    if vinculado:
                        duplicados += 1
                        continue
                    vinculados += 1
                else:
                    recibo_id = siguiente_recibo_externo
                    siguiente_recibo_externo -= 1
                factura_ref = str(row.get("factura") or row.get("documento") or "").strip()
                if not factura_ref:
                    doctos = info.get("doctos") or []
                    factura_ref = ", ".join([str(d.get("factura") or d.get("uuid") or "").strip() for d in doctos if d.get("factura") or d.get("uuid")])[:80]
                if not factura_ref:
                    factura_ref = f"{serie}{folio}".strip() or uuid_cfdi
                cliente_numero = str(row.get("cliente") or row.get("cliente_numero") or row.get("numero_cliente") or "").strip()
                cliente_nombre = str(row.get("cliente_nombre") or row.get("nombre_cliente") or info.get("cliente_receptor_nombre") or "").strip()
                xml_path = _guardar_xml_cobranza_externo(empresa, tipo, serie, folio, xml_nombre, xml_bytes)
                fecha_timbrado = str(info.get("fecha_timbrado") or info.get("fecha") or "").replace("T", " ")[:19] or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute("""
                    INSERT INTO cfdi_cobranza_emitidos
                    (recibo_id, tipo_documento, factura, empresa, cliente_receptor_numero, cliente_receptor_nombre,
                     serie, folio_cfdi, uuid, estatus_cfdi, xml_path, fecha_timbrado, forma_pago)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'TIMBRADA', ?, ?, ?)
                """, (
                    recibo_id, tipo, factura_ref, empresa, cliente_numero, cliente_nombre,
                    serie, folio, uuid_cfdi, xml_path, fecha_timbrado, str(info.get("forma_pago") or "").zfill(2)[:5],
                ))
                importados += 1
                detalles.append({"xml": xml_nombre, "empresa": empresa, "tipo": tipo, "serie": serie, "folio": folio, "uuid": uuid_cfdi, "recibo_id": recibo_id})
        finally:
            if cur:
                cur.close()
    return {
        "ok": True,
        "mensaje": "CFDI de cobranza importados.",
        "importados": importados,
        "omitidos": omitidos,
        "duplicados": duplicados,
        "vinculados": vinculados,
        "xmls_faltantes": sorted(set(faltantes))[:50],
        "detalles": detalles[:100],
    }


def _datos_receptor_desde_xml(xml_path: str, fallback: dict) -> dict:
    ns = {"cfdi": "http://www.sat.gob.mx/cfd/4"}
    datos = {}
    xml_path = _resolver_xml_cfdi_path(xml_path)
    if xml_path and os.path.exists(xml_path):
        try:
            root = ET.parse(xml_path).getroot()
            receptor = root.find("cfdi:Receptor", ns)
            if receptor is not None:
                datos = dict(receptor.attrib)
        except Exception:
            pass
    # ``desde_xml`` es importante para los complementos de pago: el SAT exige
    # que el receptor sea exactamente el que aparece en el CFDI relacionado.
    # Los datos comerciales del cliente sirven para una factura interna, pero
    # nunca deben sustituir al receptor de un CFDI ya timbrado.
    return {
        "rfc": datos.get("Rfc") or fallback.get("rfc") or "",
        "nombre": datos.get("Nombre") or fallback.get("cliente_receptor_nombre") or "",
        "cp": datos.get("DomicilioFiscalReceptor") or fallback.get("domicilio_fiscal") or "",
        "regimen": datos.get("RegimenFiscalReceptor") or fallback.get("regimen_fiscal") or "601",
        "desde_xml": bool(datos),
    }


def _resolver_xml_cfdi_path(xml_path: str) -> str:
    """Resuelve XML guardados por otra instalación del sistema.

    La tabla de CFDI es compartida por las instancias 8010/8011, por lo que
    puede conservar una ruta absoluta de una carpeta de proyecto anterior.
    Los XML vigentes pertenecen al DATA_DIR persistente, nunca al ejecutable.
    """
    path = str(xml_path or "").strip()
    if not path:
        return ""
    if os.path.exists(path):
        return path
    marcador = f"{os.sep}storage{os.sep}cfdi{os.sep}"
    normalizado = path.replace("/", os.sep)
    idx = normalizado.lower().find(marcador.lower())
    if idx >= 0:
        relativo = normalizado[idx + 1:]
        # Primero el directorio persistente configurado (ProgramData en el
        # servicio instalado); después la raíz de desarrollo como respaldo.
        candidatos = [
            settings.storage_dir.parent / relativo,
            Path(__file__).resolve().parents[2] / relativo,
        ]
        for candidato in candidatos:
            if candidato.is_file():
                return str(candidato)
    return path


def _completar_fiscal_desde_factura_legacy(cur, fiscal: dict, app: dict, recibo: dict | None = None) -> dict:
    fiscal = dict(fiscal or {})
    factura_id = int(app.get("factura_id") or fiscal.get("factura_id") or 0)
    if factura_id <= 0:
        return fiscal
    try:
        cur.execute("SELECT * FROM facturas WHERE id = %s LIMIT 1", (factura_id,))
        factura = cur.fetchone() or {}
    except Exception:
        factura = {}
    if factura:
        fiscal.setdefault("total", factura.get("total") or 0)
        fiscal.setdefault("monto_total", factura.get("total") or 0)
        fiscal.setdefault("factura", factura.get("factura") or app.get("factura") or "")
        fiscal.setdefault("empresa", factura.get("empresa") or (recibo or {}).get("empresa") or "")
        fiscal.setdefault("cliente_receptor_numero", factura.get("numero_cliente") or (recibo or {}).get("numero_cliente") or "")
        fiscal.setdefault("cliente_receptor_nombre", factura.get("cliente_nombre") or "")
    if fiscal.get("xml_path"):
        fiscal["xml_path"] = _resolver_xml_cfdi_path(fiscal.get("xml_path"))
    if fiscal.get("rfc") and fiscal.get("domicilio_fiscal"):
        return fiscal
    numero_cliente = str(fiscal.get("cliente_receptor_numero") or (recibo or {}).get("numero_cliente") or "").strip()
    empresa = _normalizar_empresa(fiscal.get("empresa") or (recibo or {}).get("empresa") or "")
    if numero_cliente and empresa:
        try:
            cur.execute(
                "SELECT * FROM clientes WHERE TRIM(CAST(numero AS CHAR)) = %s AND UPPER(TRIM(empresa)) = %s LIMIT 1",
                (numero_cliente, empresa),
            )
            cliente = cur.fetchone() or {}
        except Exception:
            cliente = {}
        if cliente:
            fiscal.setdefault("rfc", str(cliente.get("rfc") or "").strip())
            fiscal.setdefault("cliente_receptor_nombre", str(cliente.get("nombre") or fiscal.get("cliente_receptor_nombre") or "").strip())
            fiscal.setdefault("domicilio_fiscal", str(cliente.get("codigo_postal") or cliente.get("cp") or "").strip())
            fiscal.setdefault("regimen_fiscal", str(cliente.get("regimen_fiscal") or cliente.get("regimen") or "").strip())
    return fiscal


def _buscar_cfdi_emitido_cobranza(conn, factura_id: int, app: dict, recibo: dict | None = None, incluir_no_timbrada: bool = False) -> dict:
    estatus_sql = "" if incluir_no_timbrada else " AND estatus_cfdi = 'TIMBRADA'"
    if factura_id > 0:
        row = conn.execute(
            f"SELECT * FROM cfdi_emitidos WHERE factura_id = ?{estatus_sql} LIMIT 1",
            (factura_id,),
        ).fetchone()
        if row:
            return dict(row)
    empresa = _normalizar_empresa((recibo or {}).get("empresa") or app.get("empresa") or "")
    factura = str(app.get("factura") or "").strip()
    if not factura:
        return {}
    candidatos = [factura]
    limpio = re.sub(r"^[A-Za-z]+0*", "", factura)
    if limpio and limpio not in candidatos:
        candidatos.append(limpio)
    for candidato in candidatos:
        row = conn.execute(
            f"""
            SELECT * FROM cfdi_emitidos
            WHERE UPPER(TRIM(empresa)) = UPPER(TRIM(?))
              AND (UPPER(TRIM(factura)) = UPPER(TRIM(?)) OR UPPER(TRIM(folio_cfdi)) = UPPER(TRIM(?)))
              {estatus_sql}
            ORDER BY id DESC
            LIMIT 1
            """,
            (empresa, candidato, candidato),
        ).fetchone()
        if row:
            return dict(row)
    return {}


def _asegurar_tabla_cuentas_cobranza_mysql(cur):
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cobranza_cuentas_bancarias (
                id INT AUTO_INCREMENT PRIMARY KEY,
                tipo VARCHAR(20) NOT NULL,
                empresa VARCHAR(120) NOT NULL DEFAULT '',
                numero_cliente VARCHAR(50) NOT NULL DEFAULT '',
                cliente_nombre VARCHAR(255) DEFAULT '',
                banco_nombre VARCHAR(120) NOT NULL DEFAULT '',
                rfc_banco VARCHAR(13) DEFAULT '',
                cuenta VARCHAR(50) NOT NULL DEFAULT '',
                alias VARCHAR(120) DEFAULT '',
                activa TINYINT(1) NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_cobranza_cuentas_tipo_empresa_cliente (tipo, empresa, numero_cliente),
                INDEX idx_cobranza_cuentas_activa (activa)
            )
            """
        )
    except Exception:
        pass


def _cuentas_bancarias_cobranza(cur, recibo: dict) -> dict:
    cuentas = {"ordenante": {}, "beneficiario": {}}
    try:
        _asegurar_tabla_cuentas_cobranza_mysql(cur)
        empresa = str(recibo.get("empresa") or "").strip()
        cliente = str(recibo.get("numero_cliente") or "").strip()
        cur.execute(
            """SELECT * FROM cobranza_cuentas_bancarias
               WHERE tipo = 'ORDENANTE' AND activa = 1
                 AND TRIM(numero_cliente) = TRIM(%s)
                 AND (empresa = '' OR UPPER(TRIM(empresa)) = UPPER(TRIM(%s)))
               ORDER BY CASE WHEN UPPER(TRIM(empresa)) = UPPER(TRIM(%s)) THEN 0 ELSE 1 END, id DESC
               LIMIT 1""",
            (cliente, empresa, empresa),
        )
        row = cur.fetchone()
        if row:
            cuentas["ordenante"] = dict(row)
        cur.execute(
            """SELECT * FROM cobranza_cuentas_bancarias
               WHERE tipo = 'BENEFICIARIO' AND activa = 1
                 AND UPPER(TRIM(empresa)) = UPPER(TRIM(%s))
               ORDER BY id DESC
               LIMIT 1""",
            (empresa,),
        )
        row = cur.fetchone()
        if row:
            cuentas["beneficiario"] = dict(row)
    except Exception:
        pass
    return cuentas


def _extraer_datos_rep_factura(xml_path: str, pago_aplicado: Decimal) -> dict:
    datos = {"serie": "", "folio": "", "traslados": []}
    xml_path = _resolver_xml_cfdi_path(xml_path)
    if not xml_path or not os.path.exists(xml_path):
        return datos
    try:
        ns = {"cfdi": "http://www.sat.gob.mx/cfd/4"}
        root = ET.parse(xml_path).getroot()
        datos["serie"] = str(root.attrib.get("Serie") or "")
        datos["folio"] = str(root.attrib.get("Folio") or "")
        total = _money(root.attrib.get("Total"))
        factor_pago = Decimal("1")
        if total > 0:
            factor_pago = min(Decimal("1"), (pago_aplicado / total))
        acumulados = {}
        for traslado in root.findall(".//cfdi:Concepto/cfdi:Impuestos/cfdi:Traslados/cfdi:Traslado", ns):
            impuesto = str(traslado.attrib.get("Impuesto") or "")
            tipo_factor = str(traslado.attrib.get("TipoFactor") or "")
            tasa = str(traslado.attrib.get("TasaOCuota") or "")
            if impuesto != "002" or tipo_factor != "Tasa":
                continue
            base = _money(traslado.attrib.get("Base"))
            importe = _money(traslado.attrib.get("Importe"))
            if base <= 0:
                continue
            key = (impuesto, tipo_factor, tasa)
            item = acumulados.setdefault(key, {"impuesto": impuesto, "tipo_factor": tipo_factor, "tasa": tasa, "base": Decimal("0.00"), "importe": Decimal("0.00")})
            item["base"] = _money(item["base"] + (base * factor_pago))
            item["importe"] = _money(item["importe"] + (importe * factor_pago))
        datos["traslados"] = [x for x in acumulados.values() if _money(x.get("base")) > 0]
    except Exception:
        pass
    return datos


def _xml_comprobante_cobranza(tipo: str, recibo: dict, aplicaciones: list[dict], facturas: list[dict], config: dict, serie: str, folio: str, forma_pago: str, interno: bool = False, cuentas_bancarias: dict | None = None) -> str:
    """Genera CFDI 4.0 de egreso o REP 2.0 para el proveedor SIMULADO.

    El punto de integración PAC permanece centralizado: al sustituir el proveedor
    SIMULADO se debe enviar este mismo XML al PAC antes de persistirlo.
    """
    if not facturas:
        raise HTTPException(status_code=400, detail="El movimiento no tiene facturas fiscales relacionadas.")
    receptor = _datos_receptor_desde_xml(str(facturas[0].get("xml_path") or ""), facturas[0])
    if not interno and not receptor.get("desde_xml"):
        factura_origen = str(facturas[0].get("folio_cfdi") or facturas[0].get("factura") or "la factura relacionada").strip()
        raise HTTPException(
            status_code=400,
            detail=(
                f"No se encontró el XML fiscal de {factura_origen}. No se generó el complemento "
                "para evitar usar el CP o régimen comercial en lugar de los datos fiscales originales."
            ),
        )
    if not receptor["rfc"] or not receptor["cp"]:
        if not interno:
            raise HTTPException(status_code=400, detail="No se pudieron obtener los datos fiscales del receptor desde la factura relacionada.")
        receptor["rfc"] = receptor["rfc"] or "XAXX010101000"
        receptor["cp"] = receptor["cp"] or "00000"
        receptor["regimen"] = receptor["regimen"] or "601"
    fecha_emision = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    fecha_pago = _cfdi_datetime(
        recibo.get("fecha_pago") or recibo.get("fecha_recibo") or recibo.get("fecha_movimiento"),
        fecha_emision,
        noon_for_date=True,
    )
    lugar = str(config.get("cp_fiscal") or config.get("lugar_expedicion") or "").strip()
    if not lugar:
        raise HTTPException(status_code=400, detail="La empresa no tiene código postal fiscal configurado.")
    emisor_rfc = str(config.get("rfc_emisor") or "").strip()
    if not emisor_rfc:
        raise HTTPException(status_code=400, detail="La empresa no tiene RFC emisor configurado.")
    nota_metodo_pago_99 = str(config.get("nota_credito_metodo_pago_99") or "PPD").strip().upper() or "PPD"
    if nota_metodo_pago_99 not in ("PUE", "PPD"):
        nota_metodo_pago_99 = "PPD"
    nota_metodo_pago = nota_metodo_pago_99 if tipo == "NOTA_CREDITO" and str(forma_pago).zfill(2) == "99" else "PUE"
    nota_concepto = {
        "no_identificacion": "NC006",
        "clave_unidad": "ACT",
        "unidad": "pz",
        "descripcion": "DESCUENTO SOBRE VENTAS",
    }
    if tipo == "NOTA_CREDITO" and emisor_rfc.upper() == "GES090312DJ1":
        nota_concepto.update({
            "no_identificacion": "NCD",
            "clave_unidad": "H87",
            "descripcion": "DESCUENTO SOBRE COMPRAS",
        })
    nota_concepto["no_identificacion"] = str(config.get("nota_credito_no_identificacion") or nota_concepto["no_identificacion"]).strip() or nota_concepto["no_identificacion"]
    nota_concepto["clave_unidad"] = str(config.get("nota_credito_clave_unidad") or nota_concepto["clave_unidad"]).strip() or nota_concepto["clave_unidad"]
    nota_concepto["unidad"] = str(config.get("nota_credito_unidad") or nota_concepto["unidad"]).strip() or nota_concepto["unidad"]
    nota_concepto["descripcion"] = str(config.get("nota_credito_descripcion") or nota_concepto["descripcion"]).strip() or nota_concepto["descripcion"]
    if tipo == "NOTA_CREDITO":
        nota_concepto["no_identificacion"] = str(recibo.get("nota_credito_no_identificacion") or nota_concepto["no_identificacion"]).strip() or nota_concepto["no_identificacion"]
        nota_concepto["clave_unidad"] = str(recibo.get("nota_credito_clave_unidad") or nota_concepto["clave_unidad"]).strip() or nota_concepto["clave_unidad"]
        nota_concepto["unidad"] = str(recibo.get("nota_credito_unidad") or nota_concepto["unidad"]).strip() or nota_concepto["unidad"]
        nota_concepto["descripcion"] = str(recibo.get("nota_credito_descripcion") or nota_concepto["descripcion"]).strip() or nota_concepto["descripcion"]
    csd_material = obtener_material_csd(config) if str(config.get("csd_cer_path") or "").strip() else {}
    no_certificado = csd_material.get("no_certificado") or "00000000000000000000"
    certificado = csd_material.get("certificado") or ""
    e = lambda v: __import__("xml.sax.saxutils", fromlist=["escape"]).escape(str(v or ""), {'"': '&quot;'})
    nota_traslados = []
    nota_total_impuestos = Decimal("0.00")
    nota_subtotal = _money(recibo["monto_total"])
    if tipo == "NOTA_CREDITO":
        acumulados = {}
        for app, factura in zip(aplicaciones, facturas):
            datos_rel = _extraer_datos_rep_factura(str(factura.get("xml_path") or ""), _money(app.get("monto_aplicado", 0)))
            for traslado in datos_rel.get("traslados") or []:
                key = (
                    str(traslado.get("impuesto") or ""),
                    str(traslado.get("tipo_factor") or ""),
                    str(traslado.get("tasa") or ""),
                )
                item = acumulados.setdefault(key, {"impuesto": key[0], "tipo_factor": key[1], "tasa": key[2], "base": Decimal("0.00"), "importe": Decimal("0.00")})
                item["base"] = _money(item["base"] + _money(traslado.get("base")))
                item["importe"] = _money(item["importe"] + _money(traslado.get("importe")))
        nota_traslados = [x for x in acumulados.values() if _money(x.get("base")) > 0]
        nota_total_impuestos = _money(sum((_money(x.get("importe")) for x in nota_traslados), Decimal("0.00")))
        if nota_total_impuestos > 0:
            nota_subtotal = _money(_money(recibo["monto_total"]) - nota_total_impuestos)
    base = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        + (' xmlns:pago20="http://www.sat.gob.mx/Pagos20"' if tipo == "PAGO" else "")
        + ' xsi:schemaLocation="http://www.sat.gob.mx/cfd/4 http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd'
        + (' http://www.sat.gob.mx/Pagos20 http://www.sat.gob.mx/sitio_internet/cfd/Pagos/Pagos20.xsd' if tipo == "PAGO" else "")
        + f'" Version="4.0" Serie="{e(serie)}" Folio="{e(folio)}" Fecha="{fecha_emision}" NoCertificado="{e(no_certificado)}" Certificado="{e(certificado)}"'
        + (f' SubTotal="0" Moneda="XXX" Total="0" TipoDeComprobante="P" Exportacion="01" LugarExpedicion="{e(lugar)}">' if tipo == "PAGO" else f' FormaPago="{e(forma_pago)}" SubTotal="{_cfdi_amount(nota_subtotal)}" Moneda="MXN" Total="{_cfdi_amount(recibo["monto_total"])}" TipoDeComprobante="E" Exportacion="01" MetodoPago="{e(nota_metodo_pago)}" LugarExpedicion="{e(lugar)}">')
    ]
    if tipo == "NOTA_CREDITO":
        base.append('  <cfdi:CfdiRelacionados TipoRelacion="01">')
        base += [f'    <cfdi:CfdiRelacionado UUID="{e(x.get("uuid", ""))}"/>' for x in facturas]
        base.append('  </cfdi:CfdiRelacionados>')
    base += [
        f'  <cfdi:Emisor Rfc="{e(emisor_rfc)}" Nombre="{e(config.get("razon_social"))}" RegimenFiscal="{e(config.get("regimen_fiscal") or "601")}"/>',
        f'  <cfdi:Receptor Rfc="{e(receptor["rfc"])}" Nombre="{e(receptor["nombre"])}" DomicilioFiscalReceptor="{e(receptor["cp"])}" RegimenFiscalReceptor="{e(receptor["regimen"])}" UsoCFDI="{"CP01" if tipo == "PAGO" else "G02"}"/>',
    ]
    if tipo == "NOTA_CREDITO":
        objeto_imp = "02" if nota_traslados else "01"
        if nota_traslados:
            base += [
                '  <cfdi:Conceptos>',
                f'    <cfdi:Concepto ClaveProdServ="84111506" NoIdentificacion="{e(nota_concepto["no_identificacion"])}" Cantidad="1" ClaveUnidad="{e(nota_concepto["clave_unidad"])}" Unidad="{e(nota_concepto["unidad"])}" Descripcion="{e(nota_concepto["descripcion"])}" ValorUnitario="{_cfdi_amount(nota_subtotal)}" Importe="{_cfdi_amount(nota_subtotal)}" ObjetoImp="{objeto_imp}">',
                '      <cfdi:Impuestos><cfdi:Traslados>',
            ]
            for traslado in nota_traslados:
                base.append(
                    f'        <cfdi:Traslado Base="{_cfdi_amount(traslado.get("base"))}" Impuesto="{e(traslado.get("impuesto"))}" TipoFactor="{e(traslado.get("tipo_factor"))}" TasaOCuota="{_cfdi_rate(traslado.get("tasa"))}" Importe="{_cfdi_amount(traslado.get("importe"))}"/>'
                )
            base += ['      </cfdi:Traslados></cfdi:Impuestos>', '    </cfdi:Concepto>', '  </cfdi:Conceptos>']
            base.append(f'  <cfdi:Impuestos TotalImpuestosTrasladados="{_cfdi_amount(nota_total_impuestos)}"><cfdi:Traslados>')
            for traslado in nota_traslados:
                base.append(
                    f'    <cfdi:Traslado Base="{_cfdi_amount(traslado.get("base"))}" Impuesto="{e(traslado.get("impuesto"))}" TipoFactor="{e(traslado.get("tipo_factor"))}" TasaOCuota="{_cfdi_rate(traslado.get("tasa"))}" Importe="{_cfdi_amount(traslado.get("importe"))}"/>'
                )
            base.append('  </cfdi:Traslados></cfdi:Impuestos>')
        else:
            base += ['  <cfdi:Conceptos>', f'    <cfdi:Concepto ClaveProdServ="84111506" NoIdentificacion="{e(nota_concepto["no_identificacion"])}" Cantidad="1" ClaveUnidad="{e(nota_concepto["clave_unidad"])}" Unidad="{e(nota_concepto["unidad"])}" Descripcion="{e(nota_concepto["descripcion"])}" ValorUnitario="{_cfdi_amount(recibo["monto_total"])}" Importe="{_cfdi_amount(recibo["monto_total"])}" ObjetoImp="{objeto_imp}"/>', '  </cfdi:Conceptos>']
    else:
        rep_items = []
        total_base_iva16 = Decimal("0.00")
        total_impuesto_iva16 = Decimal("0.00")
        total_base_iva0 = Decimal("0.00")
        total_impuesto_iva0 = Decimal("0.00")
        for app, factura in zip(aplicaciones, facturas):
            datos_rep = _extraer_datos_rep_factura(str(factura.get("xml_path") or ""), _money(app.get("monto_aplicado", 0)))
            traslados = datos_rep.get("traslados") or []
            for traslado in traslados:
                if traslado.get("impuesto") == "002" and traslado.get("tipo_factor") == "Tasa" and str(traslado.get("tasa") or "") == "0.160000":
                    total_base_iva16 = _money(total_base_iva16 + _money(traslado.get("base")))
                    total_impuesto_iva16 = _money(total_impuesto_iva16 + _money(traslado.get("importe")))
                elif traslado.get("impuesto") == "002" and traslado.get("tipo_factor") == "Tasa" and str(traslado.get("tasa") or "") == "0.000000":
                    total_base_iva0 = _money(total_base_iva0 + _money(traslado.get("base")))
                    total_impuesto_iva0 = _money(total_impuesto_iva0 + _money(traslado.get("importe")))
            rep_items.append((app, factura, datos_rep, traslados))
        totales_attrs = f'MontoTotalPagos="{_cfdi_amount(recibo["monto_total"])}"'
        if total_base_iva0 > 0:
            totales_attrs = f'TotalTrasladosBaseIVA0="{_cfdi_amount(total_base_iva0)}" TotalTrasladosImpuestoIVA0="{_cfdi_amount(total_impuesto_iva0)}" {totales_attrs}'
        if total_base_iva16 > 0:
            totales_attrs = f'TotalTrasladosBaseIVA16="{_cfdi_amount(total_base_iva16)}" TotalTrasladosImpuestoIVA16="{_cfdi_amount(total_impuesto_iva16)}" {totales_attrs}'
        cuentas_bancarias = cuentas_bancarias or {}
        ordenante = cuentas_bancarias.get("ordenante") or {}
        beneficiario = cuentas_bancarias.get("beneficiario") or {}
        pago_attrs = [
            f'FechaPago="{fecha_pago}"',
            f'FormaDePagoP="{e(forma_pago)}"',
            'MonedaP="MXN"',
            'TipoCambioP="1"',
            f'Monto="{_cfdi_amount(recibo["monto_total"])}"',
        ]
        referencia = str(recibo.get("referencia") or "").strip()
        if not referencia and facturas:
            factura_ref = facturas[0] or {}
            serie_ref = str(factura_ref.get("serie_cfdi") or factura_ref.get("serie") or "").strip()
            folio_ref = str(factura_ref.get("folio_cfdi") or factura_ref.get("folio") or factura_ref.get("factura") or "").strip()
            if serie_ref and folio_ref.isdigit():
                folio_ref = folio_ref.zfill(10)
            referencia = f"{serie_ref}{folio_ref}".strip()
        if referencia:
            pago_attrs.append(f'NumOperacion="{e(referencia[:100])}"')
        if ordenante.get("rfc_banco") and ordenante.get("cuenta"):
            pago_attrs.append(f'RfcEmisorCtaOrd="{e(ordenante.get("rfc_banco"))}"')
            pago_attrs.append(f'CtaOrdenante="{e(ordenante.get("cuenta"))}"')
        if beneficiario.get("rfc_banco") and beneficiario.get("cuenta"):
            pago_attrs.append(f'RfcEmisorCtaBen="{e(beneficiario.get("rfc_banco"))}"')
            pago_attrs.append(f'CtaBeneficiario="{e(beneficiario.get("cuenta"))}"')
        base += ['  <cfdi:Conceptos>', '    <cfdi:Concepto ClaveProdServ="84111506" Cantidad="1" ClaveUnidad="ACT" Descripcion="Pago" ValorUnitario="0" Importe="0" ObjetoImp="01"/>', '  </cfdi:Conceptos>', '  <cfdi:Complemento>', f'    <pago20:Pagos Version="2.0"><pago20:Totales {totales_attrs}/><pago20:Pago {" ".join(pago_attrs)}>']
        for app, factura, datos_rep, traslados in rep_items:
            if not datos_rep.get("serie"):
                datos_rep["serie"] = str(factura.get("serie_cfdi") or factura.get("serie") or "")
            if not datos_rep.get("folio"):
                datos_rep["folio"] = str(factura.get("folio_cfdi") or factura.get("folio") or "")
            doc_attrs = [
                f'IdDocumento="{e(factura.get("uuid", ""))}"',
            ]
            if datos_rep.get("serie"):
                doc_attrs.append(f'Serie="{e(datos_rep.get("serie"))}"')
            if datos_rep.get("folio"):
                doc_attrs.append(f'Folio="{e(datos_rep.get("folio"))}"')
            doc_attrs += [
                'MonedaDR="MXN"',
                'EquivalenciaDR="1"',
                f'NumParcialidad="{int(factura.get("parcialidad", 1))}"',
                f'ImpSaldoAnt="{_cfdi_amount(factura.get("saldo_anterior", 0))}"',
                f'ImpPagado="{_cfdi_amount(app.get("monto_aplicado", 0))}"',
                f'ImpSaldoInsoluto="{_cfdi_amount(factura.get("saldo_insoluto", 0))}"',
                f'ObjetoImpDR="{"02" if traslados else "01"}"',
            ]
            if not traslados:
                base.append(f'      <pago20:DoctoRelacionado {" ".join(doc_attrs)}/>')
                continue
            base.append(f'      <pago20:DoctoRelacionado {" ".join(doc_attrs)}>')
            base.append('        <pago20:ImpuestosDR><pago20:TrasladosDR>')
            for traslado in traslados:
                base.append(
                    f'          <pago20:TrasladoDR BaseDR="{_cfdi_amount(traslado.get("base"))}" ImpuestoDR="{e(traslado.get("impuesto"))}" TipoFactorDR="{e(traslado.get("tipo_factor"))}" TasaOCuotaDR="{_cfdi_rate(traslado.get("tasa"))}" ImporteDR="{_cfdi_amount(traslado.get("importe"))}"/>'
                )
            base.append('        </pago20:TrasladosDR></pago20:ImpuestosDR>')
            base.append('      </pago20:DoctoRelacionado>')
        if total_base_iva16 > 0 or total_base_iva0 > 0:
            base.append('      <pago20:ImpuestosP><pago20:TrasladosP>')
            if total_base_iva16 > 0:
                base.append(f'        <pago20:TrasladoP BaseP="{_cfdi_amount(total_base_iva16)}" ImpuestoP="002" TipoFactorP="Tasa" TasaOCuotaP="0.160000" ImporteP="{_cfdi_amount(total_impuesto_iva16)}"/>')
            if total_base_iva0 > 0:
                base.append(f'        <pago20:TrasladoP BaseP="{_cfdi_amount(total_base_iva0)}" ImpuestoP="002" TipoFactorP="Tasa" TasaOCuotaP="0.000000" ImporteP="{_cfdi_amount(total_impuesto_iva0)}"/>')
            base.append('      </pago20:TrasladosP></pago20:ImpuestosP>')
        base += ['    </pago20:Pago></pago20:Pagos>', '  </cfdi:Complemento>']
    base.append('</cfdi:Comprobante>')
    return "\n".join(base)


def _saldo_inicial_fiscal_desde_app(cur, app: dict) -> dict:
    saldo_id = int(app.get("saldo_inicial_id") or 0)
    if saldo_id <= 0:
        try:
            saldo_id = abs(int(app.get("factura_id") or 0))
        except Exception:
            saldo_id = 0
    if saldo_id <= 0:
        return {}
    cur.execute("SELECT * FROM cobranza_saldos_iniciales WHERE id = %s LIMIT 1", (saldo_id,))
    saldo = cur.fetchone() or {}
    if not saldo:
        return {}
    return {
        "factura_id": -abs(saldo_id),
        "saldo_inicial_id": saldo_id,
        "factura": saldo.get("factura") or app.get("factura") or "",
        "uuid": str(saldo.get("uuid") or "").strip(),
        "xml_path": str(saldo.get("xml_path") or "").strip(),
        "serie_cfdi": str(saldo.get("serie_cfdi") or "").strip(),
        "folio_cfdi": str(saldo.get("folio_cfdi") or saldo.get("factura") or app.get("factura") or "").strip(),
        "rfc": str(saldo.get("rfc_receptor") or "").strip(),
        "cliente_receptor_nombre": str(saldo.get("nombre_receptor") or saldo.get("cliente_nombre") or "").strip(),
        "domicilio_fiscal": str(saldo.get("cp_receptor") or "").strip(),
        "regimen_fiscal": str(saldo.get("regimen_receptor") or "").strip(),
        "moneda_cfdi": str(saldo.get("moneda_cfdi") or "MXN").strip(),
        "total": saldo.get("total") or 0,
    }


def _total_cfdi_desde_fiscal(fiscal: dict) -> Decimal:
    try:
        xml_path = _resolver_xml_cfdi_path(str(fiscal.get("xml_path") or ""))
        if xml_path and os.path.exists(xml_path):
            root = ET.parse(xml_path).getroot()
            return _money(root.attrib.get("Total"))
    except Exception:
        pass
    return _money(fiscal.get("total") or fiscal.get("monto_total") or 0)


def _validar_pre_cfdi_cobranza(conn, cur, recibo: dict, aplicaciones: list[dict], tipo: str, forma_pago: str, interno: bool = False) -> dict:
    faltantes = []
    advertencias = []

    def falta(campo, mensaje):
        faltantes.append({"campo": campo, "mensaje": mensaje})

    tipo = str(tipo or "").strip().upper()
    empresa = _normalizar_empresa(recibo.get("empresa"))
    config = obtener_config_timbrado(conn, empresa)
    if not config or not config.get("timbrado_activo"):
        falta("empresa.timbrado_activo", "La empresa no tiene timbrado activo.")
    for campo, etiqueta in (
        ("rfc_emisor", "RFC del emisor"),
        ("razon_social", "razón social del emisor"),
        ("regimen_fiscal", "régimen fiscal del emisor"),
    ):
        if not str((config or {}).get(campo) or "").strip():
            falta(f"empresa.{campo}", f"Falta {etiqueta}.")
    if not str((config or {}).get("cp_fiscal") or (config or {}).get("lugar_expedicion") or "").strip():
        falta("empresa.cp_fiscal", "Falta lugar de expedición/CP fiscal del emisor.")
    if tipo not in ("PAGO", "NOTA_CREDITO"):
        falta("recibo.tipo_recibo", "Solo pago y nota de crédito generan CFDI de cobranza.")
    if str(recibo.get("estatus") or "").upper() != "ACTIVO":
        falta("recibo.estatus", "Solo se pueden timbrar movimientos activos.")
    if not aplicaciones:
        falta("aplicaciones", "El movimiento debe tener al menos una factura fiscal relacionada.")
    if tipo == "PAGO" and str(forma_pago or "").strip().zfill(2) in ("", "99"):
        falta("pago.forma_pago", "El complemento de pago requiere una FormaDePagoP SAT distinta de 99.")

    facturas = []
    for idx, app in enumerate(aplicaciones or [], start=1):
        factura_id = int(app.get("factura_id") or 0)
        fiscal = {}
        origen_tipo = str(app.get("origen_tipo") or "").strip().upper()
        if origen_tipo == "SALDO_INICIAL":
            fiscal = _saldo_inicial_fiscal_desde_app(cur, app)
            if not fiscal.get("uuid") and not interno:
                falta(f"aplicaciones[{idx}].uuid", f"El saldo inicial {app.get('factura') or factura_id} necesita XML/UUID fiscal para emitir {tipo.replace('_', ' ').lower()}.")
                continue
        elif factura_id > 0:
            fiscal = _buscar_cfdi_emitido_cobranza(conn, factura_id, app, recibo)
            fiscal = _completar_fiscal_desde_factura_legacy(cur, fiscal, app, recibo)
        if not fiscal.get("uuid") and not interno:
            falta(f"aplicaciones[{idx}].uuid", f"La factura {app.get('factura') or factura_id} debe estar timbrada antes de emitir {tipo.replace('_', ' ').lower()}.")
            continue
        receptor = _datos_receptor_desde_xml(str(fiscal.get("xml_path") or ""), fiscal)
        if tipo == "PAGO" and not interno and not receptor.get("desde_xml"):
            falta(
                f"aplicaciones[{idx}].xml",
                f"No se encontró el XML fiscal de {app.get('factura') or factura_id}; no se usará el CP o régimen comercial para el complemento.",
            )
            continue
        if not receptor.get("rfc") and not interno:
            falta(f"aplicaciones[{idx}].receptor.rfc", f"No se pudo leer RFC receptor de la factura {app.get('factura') or factura_id}.")
        if not receptor.get("cp") and not interno:
            falta(f"aplicaciones[{idx}].receptor.cp", f"No se pudo leer CP receptor de la factura {app.get('factura') or factura_id}.")
        if tipo == "PAGO" and fiscal:
            cur.execute("""
                SELECT COUNT(*) AS parcialidad, COALESCE(SUM(ca.monto_aplicado), 0) AS aplicado
                FROM cobranza_aplicaciones ca
                INNER JOIN cobranza_recibos cr ON cr.id = ca.recibo_id
                WHERE ca.factura_id = %s AND UPPER(ca.origen_tipo) = %s
                  AND cr.estatus = 'ACTIVO' AND cr.id < %s
            """, (factura_id, origen_tipo or "FACTURA", int(recibo.get("id") or 0)))
            hist = cur.fetchone() or {}
            total_factura = _total_cfdi_desde_fiscal(fiscal)
            if total_factura <= 0:
                advertencias.append({"campo": f"aplicaciones[{idx}].xml", "mensaje": f"No se pudo leer total XML de {app.get('factura') or factura_id}."})
            saldo_ant = max(Decimal("0.00"), total_factura - _money(hist.get("aplicado")))
            pagado = _money(app.get("monto_aplicado"))
            if not interno and pagado > saldo_ant:
                falta(f"aplicaciones[{idx}].monto_aplicado", f"El pago aplicado a {app.get('factura')} excede su saldo fiscal pendiente.")
        facturas.append(fiscal)

    return {
        "ok": not faltantes,
        "faltantes": faltantes,
        "advertencias": advertencias,
        "empresa": empresa,
        "tipo_documento": tipo,
        "forma_pago": str(forma_pago or "").strip().zfill(2),
        "facturas_relacionadas": len(facturas),
    }


def _preparar_facturas_cobranza_cfdi(conn, cur, recibo: dict, aplicaciones: list[dict], recibo_id: int, es_interno: bool = False) -> list[dict]:
    facturas = []
    for app in aplicaciones:
        factura_id = int(app["factura_id"])
        origen_tipo = str(app.get("origen_tipo") or "").strip().upper()
        if origen_tipo == "SALDO_INICIAL":
            fiscal = _saldo_inicial_fiscal_desde_app(cur, app)
            if not fiscal.get("uuid") and not es_interno:
                raise HTTPException(
                    status_code=400,
                    detail=f"El saldo inicial {app.get('factura') or app.get('factura_id')} necesita XML/UUID fiscal para emitir el CFDI de cobranza.",
                )
        elif not es_interno:
            fiscal = _buscar_cfdi_emitido_cobranza(conn, factura_id, app, recibo)
            if not fiscal or not dict(fiscal).get("uuid"):
                raise HTTPException(
                    status_code=400,
                    detail=f"La factura {app.get('factura') or app.get('factura_id')} debe estar timbrada antes de emitir el CFDI de cobranza.",
                )
        else:
            fiscal = _buscar_cfdi_emitido_cobranza(conn, factura_id, app, recibo, incluir_no_timbrada=True)
        fiscal = dict(fiscal) if fiscal and not isinstance(fiscal, dict) else (fiscal or {})
        fiscal = _completar_fiscal_desde_factura_legacy(cur, fiscal, app, recibo)
        if not fiscal.get("xml_path") and es_interno:
            num_cli = str(app.get("numero_cliente") or recibo.get("numero_cliente") or "").strip()
            emp_cli = _normalizar_empresa(app.get("empresa") or recibo.get("empresa") or "")
            if num_cli:
                cur.execute(
                    "SELECT rfc, nombre, codigo_postal FROM clientes WHERE TRIM(CAST(numero AS CHAR)) = %s AND UPPER(TRIM(empresa)) = %s LIMIT 1",
                    (num_cli, emp_cli),
                )
                cli_row = cur.fetchone()
                if cli_row:
                    fiscal["rfc"] = str(cli_row.get("rfc") or "")
                    fiscal["cliente_receptor_nombre"] = str(cli_row.get("nombre") or "")
                    fiscal["domicilio_fiscal"] = str(cli_row.get("codigo_postal") or "")
                    fiscal["regimen_fiscal"] = ""
        cur.execute("""
            SELECT COUNT(*) AS parcialidad, COALESCE(SUM(ca.monto_aplicado), 0) AS aplicado
            FROM cobranza_aplicaciones ca
            INNER JOIN cobranza_recibos cr ON cr.id = ca.recibo_id
            WHERE ca.factura_id = %s AND UPPER(ca.origen_tipo) = %s
              AND cr.estatus = 'ACTIVO' AND cr.id < %s
        """, (factura_id, origen_tipo or "FACTURA", recibo_id))
        hist = cur.fetchone() or {}
        total_factura = _total_cfdi_desde_fiscal(fiscal)
        aplicado = _money(hist.get("aplicado"))
        pagado = _money(app.get("monto_aplicado"))
        saldo_ant = max(Decimal("0.00"), total_factura - aplicado)
        if not es_interno and pagado > saldo_ant:
            raise HTTPException(status_code=400, detail=f"El pago aplicado a {app.get('factura')} excede su saldo fiscal pendiente.")
        fiscal.update({
            "parcialidad": int(hist.get("parcialidad") or 0) + 1,
            "saldo_anterior": saldo_ant,
            "saldo_insoluto": saldo_ant - pagado,
        })
        facturas.append(fiscal)
    return facturas


def _resumen_xml_cfdi(xml: str) -> dict:
    ns = {"cfdi": "http://www.sat.gob.mx/cfd/4", "pago20": "http://www.sat.gob.mx/Pagos20"}
    root = ET.fromstring(xml)
    conceptos = root.findall("cfdi:Conceptos/cfdi:Concepto", ns)
    traslados = root.findall("cfdi:Impuestos/cfdi:Traslados/cfdi:Traslado", ns)
    pagos = root.findall("cfdi:Complemento/pago20:Pagos/pago20:Pago", ns)
    doctos = root.findall("cfdi:Complemento/pago20:Pagos/pago20:Pago/pago20:DoctoRelacionado", ns)
    resumen = {
        "tipo_comprobante": root.attrib.get("TipoDeComprobante"),
        "serie": root.attrib.get("Serie"),
        "folio": root.attrib.get("Folio"),
        "fecha": root.attrib.get("Fecha"),
        "subtotal": root.attrib.get("SubTotal"),
        "descuento": root.attrib.get("Descuento") or "0.00",
        "moneda": root.attrib.get("Moneda"),
        "total": root.attrib.get("Total"),
        "conceptos": len(conceptos),
        "importe_conceptos": _cfdi_amount(sum((_money(c.attrib.get("Importe")) for c in conceptos), Decimal("0.00"))),
        "descuento_conceptos": _cfdi_amount(sum((_money(c.attrib.get("Descuento")) for c in conceptos), Decimal("0.00"))),
        "base_traslados": _cfdi_amount(sum((_money(t.attrib.get("Base")) for t in traslados), Decimal("0.00"))),
        "tasas": sorted({str(t.attrib.get("TasaOCuota") or "") for t in traslados if t.attrib.get("TasaOCuota")}),
        "tiene_addenda": root.find("cfdi:Addenda", ns) is not None,
        "pagos20": bool(pagos),
        "pagos": len(pagos),
        "documentos_relacionados": len(doctos),
        "rep_tiene_totales": root.find("cfdi:Complemento/pago20:Pagos/pago20:Totales", ns) is not None,
    }
    return resumen


def _texto_empresa_cmp(valor: str) -> str:
    valor = str(valor or "")
    valor = unicodedata.normalize("NFKD", valor)
    valor = "".join(ch for ch in valor if not unicodedata.combining(ch)).upper().strip()
    return valor.replace("?", "N")


def _normalizar_opciones_cfdi(datos: dict | None) -> dict:
    datos = datos or {}
    return {
        "uso_cfdi": str(datos.get("uso_cfdi") or "").strip().upper()[:5],
        "forma_pago": str(datos.get("forma_pago") or "").strip()[:5],
        "metodo_pago": str(datos.get("metodo_pago") or "").strip().upper()[:5],
        "exportacion": str(datos.get("exportacion") or "").strip()[:5],
        "condiciones_pago": str(datos.get("condiciones_pago") or "").strip()[:160],
        "orden_compra": str(datos.get("orden_compra") or "").strip()[:100],
        "moneda": (str(datos.get("moneda") or "MXN").strip().upper() or "MXN")[:5],
        "usar_fecha_actual": bool(datos.get("usar_fecha_actual", False)),
    }


def _condiciones_pago_desde_dias(dias) -> str:
    try:
        dias_int = int(float(dias or 0))
    except Exception:
        return ""
    if dias_int <= 0:
        return "Contado"
    return f"{dias_int} dias"


def _resolver_factura_para_opciones_cfdi(conn, conn_legacy, folio: str):
    cur = conn_legacy.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM facturas WHERE factura = %s LIMIT 1", (folio,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Factura {folio} no encontrada")
        factura = dict(row)
        cur.execute(
            """
            SELECT d.cantidad, d.piezas, p.unidad, p.clave_unidad_sat
            FROM factura_detalle d
            LEFT JOIN productos p ON CAST(p.cip AS CHAR) = CAST(d.cip AS CHAR)
            WHERE d.factura_id = %s
            ORDER BY d.id
            """,
            (int(row["id"]),),
        )
        factura["productos"] = [dict(r) for r in (cur.fetchall() or [])]
        resolucion = resolver_receptor_timbrado(conn, conn_legacy, factura, incluir_preview=False)
        empresa = _normalizar_empresa(factura.get("empresa"))
        numero_cliente = str(resolucion.get("cliente_receptor_numero") or factura.get("numero_cliente") or "").strip()
        nombre_cliente = str(resolucion.get("cliente_receptor_nombre") or factura.get("consignatario") or "").strip()
        receptor = resolucion.get("cliente_receptor") or {}
        return factura, resolucion, empresa, numero_cliente, nombre_cliente, receptor
    finally:
        cur.close()


def _obtener_defaults_cfdi_cliente(conn, empresa: str, numero_cliente: str) -> dict:
    if not getattr(conn, "_timbrado_schema_ensured", False):
        _asegurar_tabla_cfdi_defaults_clientes(conn)
    row = conn.execute(
        """
        SELECT * FROM timbrado_cfdi_defaults_clientes
        WHERE empresa = ? AND numero_cliente = ?
        LIMIT 1
        """,
        (_normalizar_empresa(empresa), str(numero_cliente or "").strip()),
    ).fetchone()
    return dict(row) if row else {}


def _guardar_defaults_cfdi_cliente(conn, empresa: str, numero_cliente: str, nombre_cliente: str, opciones: dict):
    opciones = _normalizar_opciones_cfdi(opciones)
    if not empresa or not numero_cliente:
        return
    if not any(opciones.get(k) for k in ("uso_cfdi", "forma_pago", "metodo_pago", "exportacion", "condiciones_pago")):
        return
    if not getattr(conn, "_timbrado_schema_ensured", False):
        _asegurar_tabla_cfdi_defaults_clientes(conn)
    conn.execute(
        """
        INSERT INTO timbrado_cfdi_defaults_clientes (
            empresa, numero_cliente, nombre_cliente, uso_cfdi, forma_pago, metodo_pago,
            exportacion, condiciones_pago, moneda
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(empresa, numero_cliente) DO UPDATE SET
            nombre_cliente = excluded.nombre_cliente,
            uso_cfdi = excluded.uso_cfdi,
            forma_pago = excluded.forma_pago,
            metodo_pago = excluded.metodo_pago,
            exportacion = excluded.exportacion,
            condiciones_pago = excluded.condiciones_pago,
            moneda = excluded.moneda
        """,
        (
            _normalizar_empresa(empresa),
            str(numero_cliente or "").strip(),
            str(nombre_cliente or "").strip(),
            opciones.get("uso_cfdi"),
            opciones.get("forma_pago"),
            opciones.get("metodo_pago"),
            opciones.get("exportacion"),
            opciones.get("condiciones_pago"),
            opciones.get("moneda"),
        ),
    )


@router.get("/health")
def health_timbrado():
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
    return {"status": "ok", "modulo": "timbrado"}


@router.get("/empresas")
def listar_empresas_timbrado():
    empresas = {}
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        try:
            rows = conn.execute("SELECT * FROM empresas_timbrado ORDER BY empresa").fetchall()
        except Exception:
            _asegurar_tablas_timbrado(conn)
            rows = conn.execute("SELECT * FROM empresas_timbrado ORDER BY empresa").fetchall()
        for r in rows:
            d = dict(r)
            empresas[d["empresa"]] = d
    try:
        conn2 = get_legacy_connection()
        cur = conn2.cursor(dictionary=True)
        cur.execute(
            """
            SELECT DISTINCT TRIM(empresa) AS empresa
            FROM clientes
            WHERE empresa IS NOT NULL AND TRIM(empresa) <> ''
            ORDER BY TRIM(empresa)
            """
        )
        for row in cur.fetchall():
            emp = row.get("empresa")
            if emp and emp not in empresas:
                empresas[emp] = {"empresa": emp, "timbrado_activo": 0, "modo_pruebas": 1, "proveedor": ""}
        cur.close()
        conn2.close()
    except Exception:
        pass
    return list(empresas.values())


@router.get("/empresas/{empresa}")
def ver_empresa_timbrado(empresa: str):
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        cfg = obtener_config_timbrado(conn, empresa)
    return _config_publica_timbrado(cfg or {"empresa": _normalizar_empresa(empresa), "timbrado_activo": 0, "modo_pruebas": 1, "proveedor": ""})


@router.put("/empresas/{empresa}")
def actualizar_empresa_timbrado(empresa: str, datos: dict):
    with get_timbrado_connection() as conn:
        actual = obtener_config_timbrado(conn, empresa) or {}
        datos_limpios = _resolver_secretos_config(actual, datos or {})
        guardar_config_timbrado(conn, empresa, datos_limpios)
        cfg = obtener_config_timbrado(conn, empresa) or {}
    return {"mensaje": f"Configuracion de timbrado guardada para {empresa}.", "config": _config_publica_timbrado(cfg)}


@router.post("/empresas/{empresa}/config-snapshots")
def crear_snapshot_config_timbrado(empresa: str, datos: dict | None = None):
    datos = datos or {}
    empresa_norm = _normalizar_empresa(empresa)
    etiqueta = str(datos.get("etiqueta") or "").strip() or "snapshot manual"
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        _asegurar_tabla_snapshots_timbrado(conn)
        cfg = obtener_config_timbrado(conn, empresa_norm) or {"empresa": empresa_norm}
        conn.execute(
            """
            INSERT INTO timbrado_config_snapshots (empresa, etiqueta, config_json)
            VALUES (?, ?, ?)
            """,
            (empresa_norm, etiqueta, json.dumps(cfg, ensure_ascii=False, default=str)),
        )
        row = conn.execute("SELECT MAX(id) AS id FROM timbrado_config_snapshots WHERE empresa = ?", (empresa_norm,)).fetchone()
        snapshot_id = int(dict(row).get("id") or 0) if row else 0
        registrar_intento_pac(
            conn,
            {"id": 0, "factura": "CONFIG_SNAPSHOT", "empresa": empresa_norm},
            cfg.get("proveedor"),
            "PAC_CONFIG_SNAPSHOT",
            f"Snapshot de configuracion creado: {etiqueta}",
            response={"snapshot_id": snapshot_id, "etiqueta": etiqueta},
        )
    return {"ok": True, "empresa": empresa_norm, "snapshot_id": snapshot_id, "etiqueta": etiqueta}


@router.get("/empresas/{empresa}/config-snapshots")
def listar_snapshots_config_timbrado(empresa: str, limit: int = 50):
    empresa_norm = _normalizar_empresa(empresa)
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        _asegurar_tabla_snapshots_timbrado(conn)
        rows = conn.execute(
            """
            SELECT id, empresa, etiqueta, created_at
            FROM timbrado_config_snapshots
            WHERE empresa = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (empresa_norm, max(1, min(int(limit or 50), 200))),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/empresas/{empresa}/config-snapshots/{snapshot_id}")
def descargar_snapshot_config_timbrado(empresa: str, snapshot_id: int):
    empresa_norm = _normalizar_empresa(empresa)
    with get_timbrado_connection() as conn:
        _asegurar_tabla_snapshots_timbrado(conn)
        row = conn.execute(
            "SELECT * FROM timbrado_config_snapshots WHERE empresa = ? AND id = ? LIMIT 1",
            (empresa_norm, int(snapshot_id)),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Snapshot no encontrado.")
    data = dict(row)
    try:
        cfg_publica = _config_publica_timbrado(json.loads(data.get("config_json") or "{}"))
        data["config_json"] = json.dumps(cfg_publica, ensure_ascii=False, default=str)
    except Exception:
        pass
    safe = _safe_package_name(f"{empresa_norm}-snapshot-{snapshot_id}")
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{safe}.json"'},
    )


@router.post("/empresas/{empresa}/config-snapshots/{snapshot_id}/restaurar")
def restaurar_snapshot_config_timbrado(empresa: str, snapshot_id: int, datos: dict | None = None):
    datos = datos or {}
    empresa_norm = _normalizar_empresa(empresa)
    frase = str(datos.get("confirmacion") or "").strip().upper()
    frase_esperada = f"RESTAURAR CONFIG {empresa_norm}".upper()
    if frase != frase_esperada:
        raise HTTPException(status_code=400, detail=f"Escribe exactamente: {frase_esperada}")
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        _asegurar_tabla_snapshots_timbrado(conn)
        row = conn.execute(
            "SELECT * FROM timbrado_config_snapshots WHERE empresa = ? AND id = ? LIMIT 1",
            (empresa_norm, int(snapshot_id)),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Snapshot no encontrado.")
        snapshot = dict(row)
        try:
            cfg = json.loads(snapshot.get("config_json") or "{}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Snapshot invalido: {exc}")
        cfg["empresa"] = empresa_norm
        guardar_config_timbrado(conn, empresa_norm, cfg)
        registrar_intento_pac(
            conn,
            {"id": 0, "factura": "CONFIG_RESTORE", "empresa": empresa_norm},
            cfg.get("proveedor"),
            "PAC_CONFIG_RESTORE",
            f"Configuracion restaurada desde snapshot {snapshot_id}.",
            response={"snapshot_id": snapshot_id, "etiqueta": snapshot.get("etiqueta")},
        )
        final_cfg = obtener_config_timbrado(conn, empresa_norm) or {}
    return {"ok": True, "empresa": empresa_norm, "snapshot_id": int(snapshot_id), "mensaje": "Configuracion restaurada.", "config": _config_publica_timbrado(final_cfg)}


def _safe_csd_empresa_dir(empresa: str) -> Path:
    empresa_norm = _normalizar_empresa(empresa)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", empresa_norm).strip("._") or "empresa"
    return CSD_STORAGE_DIR / safe_name


async def _leer_archivo_csd_upload(file: UploadFile, tipo_norm: str) -> bytes:
    ext = Path(file.filename or "").suffix.lower()
    if ext != f".{tipo_norm}":
        raise HTTPException(status_code=400, detail=f"El archivo {file.filename or ''} debe tener extension .{tipo_norm}.")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail=f"El archivo .{tipo_norm} esta vacio.")
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"El archivo .{tipo_norm} excede 2 MB.")
    return content


@router.post("/empresas/{empresa}/csd")
async def subir_csd_empresa_timbrado(empresa: str, tipo: str, file: UploadFile = File(...)):
    tipo_norm = str(tipo or "").strip().lower()
    if tipo_norm not in {"cer", "key"}:
        raise HTTPException(status_code=400, detail="Tipo de archivo CSD invalido. Usa cer o key.")
    content = await _leer_archivo_csd_upload(file, tipo_norm)

    empresa_norm = _normalizar_empresa(empresa)
    dest_dir = _safe_csd_empresa_dir(empresa_norm)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{tipo_norm}.{tipo_norm}"
    dest.write_bytes(content)
    campo = "csd_cer_path" if tipo_norm == "cer" else "csd_key_path"
    with get_timbrado_connection() as conn:
        cfg = obtener_config_timbrado(conn, empresa_norm) or {}
        cfg[campo] = str(dest)
        guardar_config_timbrado(conn, empresa_norm, cfg)
    return {
        "ok": True,
        "empresa": empresa_norm,
        "tipo": tipo_norm,
        "path": str(dest),
        "mensaje": f"Archivo .{tipo_norm} guardado en servidor.",
    }


@router.post("/empresas/{empresa}/csd/completo")
async def subir_csd_completo_empresa_timbrado(
    empresa: str,
    cer: UploadFile = File(...),
    key: UploadFile = File(...),
    password: str = Form(default=""),
    preparar_finkok: bool = Form(default=True),
):
    empresa_norm = _normalizar_empresa(empresa)
    cer_content = await _leer_archivo_csd_upload(cer, "cer")
    key_content = await _leer_archivo_csd_upload(key, "key")
    dest_dir = _safe_csd_empresa_dir(empresa_norm)
    dest_dir.mkdir(parents=True, exist_ok=True)
    cer_dest = dest_dir / "cer.cer"
    key_dest = dest_dir / "key.key"
    cer_dest.write_bytes(cer_content)
    key_dest.write_bytes(key_content)
    with get_timbrado_connection() as conn:
        cfg = obtener_config_timbrado(conn, empresa_norm) or {"empresa": empresa_norm}
        cfg["csd_cer_path"] = str(cer_dest)
        cfg["csd_key_path"] = str(key_dest)
        password_value = str(password or "")
        if not password_value and str(cfg.get("csd_key_password") or "").strip():
            password_value = SECRET_PLACEHOLDER
        cfg = _resolver_secretos_config(cfg, {**cfg, "csd_key_password": password_value})
        if preparar_finkok and str(cfg.get("proveedor") or "").strip().upper() in {"", "SIMULADO"}:
            cfg["proveedor"] = "FINKOK"
            cfg["timbrado_activo"] = True
            cfg["modo_pruebas"] = True
            cfg["facturacion_automatica"] = False
        guardar_config_timbrado(conn, empresa_norm, cfg)
        cfg_final = obtener_config_timbrado(conn, empresa_norm) or {}
    diagnostico = diagnostico_empresa_timbrado(empresa_norm)
    return {
        "ok": True,
        "empresa": empresa_norm,
        "cer_path": str(cer_dest),
        "key_path": str(key_dest),
        "config": _config_publica_timbrado(cfg_final),
        "diagnostico": diagnostico,
        "mensaje": "CSD completo guardado en servidor.",
    }


@router.get("/empresas/{empresa}/diagnostico")
def diagnostico_empresa_timbrado(empresa: str):
    def add(items, campo, mensaje, nivel="faltante"):
        items.append({"campo": campo, "mensaje": mensaje, "nivel": nivel})

    empresa_norm = _normalizar_empresa(empresa)
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        cfg = obtener_config_timbrado(conn, empresa_norm)
        cfg = aplicar_defaults_pac(cfg or {})
        rows = conn.execute(
            "SELECT estatus, COUNT(*) AS total FROM timbrado_queue WHERE empresa = ? GROUP BY estatus",
            (empresa_norm,),
        ).fetchall()
        cola = {str(dict(r).get("estatus") or ""): int(dict(r).get("total") or 0) for r in rows}

    faltantes = []
    advertencias = []
    proveedor = str((cfg or {}).get("proveedor") or "").strip().upper()
    modo_real = bool(proveedor and proveedor != "SIMULADO")
    for campo, etiqueta in (
        ("rfc_emisor", "RFC emisor"),
        ("razon_social", "razón social"),
        ("regimen_fiscal", "régimen fiscal"),
        ("cp_fiscal", "CP fiscal/lugar de expedición"),
        ("serie_cfdi", "serie CFDI"),
        ("folio_actual", "folio siguiente"),
    ):
        if not str((cfg or {}).get(campo) or "").strip():
            add(faltantes, campo, f"Falta {etiqueta}.")
    if not (cfg or {}).get("timbrado_activo"):
        add(advertencias, "timbrado_activo", "Timbrado no está activo para esta empresa.", "advertencia")
    if not proveedor:
        add(advertencias, "proveedor", "Proveedor no seleccionado; usa SIMULADO para pruebas o configura el PAC.", "advertencia")
    if modo_real:
        if not proveedor_pac_integrado(proveedor):
            add(advertencias, "proveedor.integracion", f"Proveedor {proveedor} configurado, pero el adaptador PAC aún está pendiente.", "advertencia")
        for campo, etiqueta in (
            ("csd_cer_path", "archivo .cer del CSD"),
            ("csd_key_path", "archivo .key del CSD"),
            ("csd_key_password", "password del CSD"),
            ("pac_usuario", "usuario PAC"),
            ("pac_password", "password PAC"),
        ):
            if not str((cfg or {}).get(campo) or "").strip():
                add(faltantes, campo, f"Falta {etiqueta}.")
        if proveedor != "FINKOK" and not str((cfg or {}).get("pac_url") or "").strip():
            add(faltantes, "pac_url", "Falta URL del PAC.")
        for campo in ("csd_cer_path", "csd_key_path"):
            path = str((cfg or {}).get(campo) or "").strip()
            if path and not os.path.exists(path):
                add(advertencias, campo, f"No se encontró el archivo en servidor: {path}", "advertencia")
    output_dir = str((cfg or {}).get("output_dir") or ruta_empresa_fiscal(empresa_norm)).strip()
    output_disk = {"total_mb": None, "free_mb": None, "used_mb": None}
    try:
        os.makedirs(output_dir, exist_ok=True)
        test_file = os.path.join(output_dir, ".write_test")
        with open(test_file, "w", encoding="utf-8") as fh:
            fh.write("ok")
        try:
            os.remove(test_file)
        except Exception:
            pass
        try:
            usage = shutil.disk_usage(output_dir)
            output_disk = {
                "total_mb": round(usage.total / (1024 * 1024), 2),
                "used_mb": round(usage.used / (1024 * 1024), 2),
                "free_mb": round(usage.free / (1024 * 1024), 2),
            }
            if output_disk["free_mb"] < 50:
                add(faltantes, "output_dir.espacio", f"Espacio fiscal insuficiente: {output_disk['free_mb']} MB libres.")
            elif output_disk["free_mb"] < 250:
                add(advertencias, "output_dir.espacio", f"Espacio fiscal bajo: {output_disk['free_mb']} MB libres.", "advertencia")
        except Exception as exc:
            add(advertencias, "output_dir.espacio", f"No se pudo revisar espacio disponible: {exc}", "advertencia")
    except Exception as exc:
        add(faltantes, "output_dir", f"No se puede escribir en la carpeta fiscal: {output_dir}. {exc}")
    bloqueadas = int(cola.get("BLOQUEADO_PAC") or 0)
    if bloqueadas:
        add(advertencias, "cola.BLOQUEADO_PAC", f"Hay {bloqueadas} factura(s) con XML pre-PAC esperando integración PAC.", "advertencia")
    csd = diagnosticar_csd_config(cfg or {}) if modo_real else {}
    for mensaje in csd.get("errores", []) if csd else []:
        add(faltantes, "csd", mensaje)
    for mensaje in csd.get("advertencias", []) if csd else []:
        add(advertencias, "csd", mensaje, "advertencia")
    if modo_real:
        preflight = validar_preflight_pac(cfg or {})
        for mensaje in preflight.get("errores", []):
            if not any(x.get("mensaje") == mensaje for x in faltantes):
                add(faltantes, "preflight", mensaje)
        for mensaje in preflight.get("advertencias", []):
            if not any(x.get("mensaje") == mensaje for x in advertencias):
                add(advertencias, "preflight", mensaje, "advertencia")
    else:
        preflight = {"ok": True, "proveedor": proveedor, "errores": [], "advertencias": []}

    return {
        "empresa": empresa_norm,
        "proveedor": proveedor,
        "modo_real": modo_real,
        "ok_pre_pac": not faltantes,
        "faltantes": faltantes,
        "advertencias": advertencias,
        "cola": cola,
        "csd": csd,
        "output_dir": output_dir,
        "output_disk": output_disk,
        "preflight": preflight,
    }


@router.get("/empresas-diagnostico")
def diagnostico_empresas_timbrado():
    empresas = listar_empresas_timbrado()
    resultados = []
    for emp in empresas:
        nombre = emp.get("empresa") if isinstance(emp, dict) else emp
        try:
            resultados.append(diagnostico_empresa_timbrado(str(nombre or "")))
        except Exception as exc:
            resultados.append({
                "empresa": str(nombre or ""),
                "ok_pre_pac": False,
                "faltantes": [{"campo": "diagnostico", "mensaje": str(exc), "nivel": "faltante"}],
                "advertencias": [],
            })
    listas = sum(1 for r in resultados if r.get("ok_pre_pac"))
    reales = sum(1 for r in resultados if r.get("modo_real"))
    return {
        "total": len(resultados),
        "listas": listas,
        "pendientes": len(resultados) - listas,
        "modo_real": reales,
        "empresas": resultados,
    }


@router.post("/empresas/{empresa}/pac/probar")
def probar_pac_empresa_timbrado(empresa: str):
    empresa_norm = _normalizar_empresa(empresa)
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        cfg = obtener_config_timbrado(conn, empresa_norm) or {"empresa": empresa_norm}
        resultado = probar_conectividad_pac(cfg)
        registrar_intento_pac(
            conn,
            {"id": 0, "factura": "CONFIG_PAC", "empresa": empresa_norm},
            resultado.get("proveedor") or cfg.get("proveedor"),
            "PAC_OK" if resultado.get("ok") else "PAC_ERROR",
            "Prueba de conexión PAC correcta." if resultado.get("ok") else "Prueba de conexión PAC con errores.",
            response={
                "proveedor": resultado.get("proveedor"),
                "url": resultado.get("url"),
                "http_status": resultado.get("http_status"),
                "respondio": resultado.get("respondio"),
                "integrado": resultado.get("integrado"),
                "errores": resultado.get("errores") or [],
                "advertencias": resultado.get("advertencias") or [],
            },
        )
    return resultado


@router.post("/empresas/{empresa}/pac/prueba-integral")
def prueba_integral_pac_empresa_timbrado(empresa: str):
    empresa_norm = _normalizar_empresa(empresa)
    diagnostico = diagnostico_empresa_timbrado(empresa_norm)
    etapas = [
        {
            "etapa": "configuracion",
            "ok": not bool(diagnostico.get("faltantes")),
            "detalle": "Configuracion fiscal, serie/folio, CSD y credenciales revisadas.",
            "errores": [x.get("mensaje") for x in diagnostico.get("faltantes") or []],
            "advertencias": [x.get("mensaje") for x in diagnostico.get("advertencias") or []],
        },
        {
            "etapa": "preflight",
            "ok": bool((diagnostico.get("preflight") or {}).get("ok")),
            "detalle": "Preflight PAC local revisado sin emitir documentos.",
            "errores": (diagnostico.get("preflight") or {}).get("errores") or [],
            "advertencias": (diagnostico.get("preflight") or {}).get("advertencias") or [],
        },
    ]
    conectividad = {"ok": False, "omitida": True, "errores": [], "advertencias": []}
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        cfg = obtener_config_timbrado(conn, empresa_norm) or {"empresa": empresa_norm}
        bloqueadas_rows = conn.execute(
            """
            SELECT factura, cliente_receptor_nombre, proveedor, intento_count, ultimo_error, xml_path, queued_at
            FROM timbrado_queue
            WHERE empresa = ? AND estatus = 'BLOQUEADO_PAC'
            ORDER BY queued_at DESC
            LIMIT 10
            """,
            (empresa_norm,),
        ).fetchall()
        bloqueadas = [dict(r) for r in bloqueadas_rows]
        debe_probar_pac = bool(diagnostico.get("modo_real")) and bool((diagnostico.get("preflight") or {}).get("ok"))
        if debe_probar_pac:
            conectividad = probar_conectividad_pac(cfg)
            conectividad["omitida"] = False
            registrar_intento_pac(
                conn,
                {"id": 0, "factura": "PRUEBA_INTEGRAL_PAC", "empresa": empresa_norm},
                conectividad.get("proveedor") or cfg.get("proveedor"),
                "PAC_PRUEBA_INTEGRAL_OK" if conectividad.get("ok") else "PAC_PRUEBA_INTEGRAL_ERROR",
                "Prueba integral PAC correcta." if conectividad.get("ok") else "Prueba integral PAC con errores.",
                response={
                    "diagnostico_ok": diagnostico.get("ok_pre_pac"),
                    "url": conectividad.get("url"),
                    "http_status": conectividad.get("http_status"),
                    "respondio": conectividad.get("respondio"),
                    "errores": conectividad.get("errores") or [],
                    "advertencias": conectividad.get("advertencias") or [],
                    "bloqueadas_muestra": bloqueadas,
                },
            )
        else:
            conectividad["errores"] = ["Conectividad PAC omitida porque la empresa no esta en modo PAC real o el preflight no esta listo."]
    etapas.append({
        "etapa": "conectividad_pac",
        "ok": bool(conectividad.get("ok")) if not conectividad.get("omitida") else False,
        "omitida": bool(conectividad.get("omitida")),
        "detalle": "Prueba de comunicacion con PAC sin enviar XML.",
        "errores": conectividad.get("errores") or [],
        "advertencias": conectividad.get("advertencias") or [],
    })
    ok_produccion = bool(diagnostico.get("ok_pre_pac")) and bool((diagnostico.get("preflight") or {}).get("ok")) and bool(conectividad.get("ok"))
    siguiente = "Lista para prueba real controlada de timbrado." if ok_produccion else "Completa los faltantes antes de enviar XML al PAC."
    return {
        "empresa": empresa_norm,
        "ok": ok_produccion,
        "siguiente_paso": siguiente,
        "diagnostico": diagnostico,
        "conectividad": conectividad,
        "etapas": etapas,
        "bloqueadas_muestra": bloqueadas,
    }


@router.post("/empresas/{empresa}/pac/activar-prueba-controlada")
def activar_prueba_controlada_pac_empresa_timbrado(empresa: str, datos: dict | None = None):
    datos = datos or {}
    empresa_norm = _normalizar_empresa(empresa)
    if datos.get("confirmar") is not True:
        raise HTTPException(status_code=400, detail="Confirma la activacion enviando confirmar=true.")

    prueba = prueba_integral_pac_empresa_timbrado(empresa_norm)
    if not prueba.get("ok"):
        raise HTTPException(
            status_code=400,
            detail={
                "mensaje": "La empresa no esta lista para activacion PAC controlada.",
                "prueba_integral": prueba,
            },
        )

    facturacion_automatica = bool(datos.get("facturacion_automatica"))
    modo_pruebas = bool(datos.get("modo_pruebas", True))
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        cfg = obtener_config_timbrado(conn, empresa_norm) or {"empresa": empresa_norm}
        _asegurar_tabla_snapshots_timbrado(conn)
        conn.execute(
            "INSERT INTO timbrado_config_snapshots (empresa, etiqueta, config_json) VALUES (?, ?, ?)",
            (empresa_norm, "antes de activar prueba PAC", json.dumps(cfg, ensure_ascii=False, default=str)),
        )
        cfg["timbrado_activo"] = True
        cfg["facturacion_automatica"] = facturacion_automatica
        cfg["modo_pruebas"] = modo_pruebas
        guardar_config_timbrado(conn, empresa_norm, cfg)
        registrar_intento_pac(
            conn,
            {"id": 0, "factura": "ACTIVACION_PRUEBA_CONTROLADA", "empresa": empresa_norm},
            cfg.get("proveedor"),
            "PAC_ACTIVACION_CONTROLADA_OK",
            "Empresa activada para prueba controlada PAC.",
            response={
                "timbrado_activo": True,
                "facturacion_automatica": facturacion_automatica,
                "modo_pruebas": modo_pruebas,
                "prueba_integral_ok": True,
            },
        )
        final_cfg = obtener_config_timbrado(conn, empresa_norm) or {}
    return {
        "ok": True,
        "empresa": empresa_norm,
        "mensaje": "Empresa activada para prueba controlada PAC.",
        "timbrado_activo": bool(final_cfg.get("timbrado_activo")),
        "facturacion_automatica": bool(final_cfg.get("facturacion_automatica")),
        "modo_pruebas": bool(final_cfg.get("modo_pruebas", True)),
        "proveedor": final_cfg.get("proveedor"),
        "prueba_integral": prueba,
    }


@router.post("/empresas/{empresa}/pac/checklist-produccion")
def checklist_produccion_pac_empresa_timbrado(empresa: str, datos: dict | None = None):
    datos = datos or {}
    empresa_norm = _normalizar_empresa(empresa)
    forzar_modo_produccion = bool(datos.get("forzar_modo_produccion"))
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        cfg = obtener_config_timbrado(conn, empresa_norm) or {"empresa": empresa_norm}
        cfg_check = dict(cfg)
        if forzar_modo_produccion:
            cfg_check["modo_pruebas"] = False
        cfg_check = aplicar_defaults_pac(cfg_check)
        proveedor = str(cfg_check.get("proveedor") or "").strip().upper()
        cola_rows = conn.execute(
            "SELECT estatus, COUNT(*) AS total FROM timbrado_queue WHERE empresa = ? GROUP BY estatus",
            (empresa_norm,),
        ).fetchall()
        cola = {str(dict(r).get("estatus") or ""): int(dict(r).get("total") or 0) for r in cola_rows}
        bloqueadas_rows = conn.execute(
            """
            SELECT factura, estatus, ultimo_error, last_attempt_at
            FROM timbrado_queue
            WHERE empresa = ? AND estatus IN ('BLOQUEADO_PAC', 'TIMBRANDO', 'ERROR')
            ORDER BY queued_at DESC
            LIMIT 20
            """,
            (empresa_norm,),
        ).fetchall()
        pendientes_cola = [dict(r) for r in bloqueadas_rows]

    csd = diagnosticar_csd_config(cfg_check)
    preflight = validar_preflight_pac(cfg_check)
    conectividad = probar_conectividad_pac(cfg_check) if proveedor and proveedor != "SIMULADO" else {
        "ok": False,
        "errores": ["Proveedor SIMULADO o no configurado; no es valido para produccion."],
        "advertencias": [],
    }
    etapas = [
        {
            "etapa": "proveedor_real",
            "ok": bool(proveedor and proveedor != "SIMULADO" and proveedor_pac_integrado(proveedor)),
            "detalle": f"Proveedor configurado: {proveedor or 'SIN PROVEEDOR'}",
            "errores": [] if proveedor and proveedor != "SIMULADO" and proveedor_pac_integrado(proveedor) else ["Configura un proveedor PAC real integrado."],
        },
        {
            "etapa": "modo_produccion",
            "ok": not bool(cfg_check.get("modo_pruebas", True)),
            "detalle": "Modo pruebas debe estar apagado para produccion.",
            "errores": [] if not bool(cfg_check.get("modo_pruebas", True)) else ["Modo pruebas sigue activo."],
        },
        {
            "etapa": "csd",
            "ok": bool(csd.get("ok")),
            "detalle": "CSD, KEY, password y RFC emisor.",
            "errores": csd.get("errores") or [],
            "advertencias": csd.get("advertencias") or [],
        },
        {
            "etapa": "preflight",
            "ok": bool(preflight.get("ok")),
            "detalle": "Validacion local previa a PAC.",
            "errores": preflight.get("errores") or [],
            "advertencias": preflight.get("advertencias") or [],
        },
        {
            "etapa": "conectividad_pac",
            "ok": bool(conectividad.get("ok")),
            "detalle": "Endpoint PAC de produccion responde.",
            "errores": conectividad.get("errores") or [],
            "advertencias": conectividad.get("advertencias") or [],
        },
        {
            "etapa": "cola_sin_atascos",
            "ok": not int(cola.get("BLOQUEADO_PAC") or 0) and not int(cola.get("TIMBRANDO") or 0),
            "detalle": "No debe haber BLOQUEADO_PAC ni TIMBRANDO antes del pase.",
            "errores": [
                msg for msg in (
                    f"{int(cola.get('BLOQUEADO_PAC') or 0)} factura(s) BLOQUEADO_PAC." if int(cola.get("BLOQUEADO_PAC") or 0) else "",
                    f"{int(cola.get('TIMBRANDO') or 0)} factura(s) TIMBRANDO." if int(cola.get("TIMBRANDO") or 0) else "",
                ) if msg
            ],
            "advertencias": [f"{int(cola.get('ERROR') or 0)} factura(s) ERROR en cola."] if int(cola.get("ERROR") or 0) else [],
        },
        {
            "etapa": "folios",
            "ok": bool(str(cfg_check.get("serie_cfdi") or "").strip() and str(cfg_check.get("folio_actual") or "").strip()),
            "detalle": f"Serie {cfg_check.get('serie_cfdi') or '-'} folio siguiente {cfg_check.get('folio_actual') or '-'}",
            "errores": [] if str(cfg_check.get("serie_cfdi") or "").strip() and str(cfg_check.get("folio_actual") or "").strip() else ["Falta serie CFDI o folio siguiente."],
        },
    ]
    ok = all(bool(x.get("ok")) for x in etapas)
    return {
        "ok": ok,
        "empresa": empresa_norm,
        "proveedor": proveedor,
        "modo_pruebas": bool(cfg_check.get("modo_pruebas", True)),
        "cola": cola,
        "pendientes_cola": pendientes_cola,
        "etapas": etapas,
        "csd": csd,
        "preflight": preflight,
        "conectividad": {
            "ok": conectividad.get("ok"),
            "proveedor": conectividad.get("proveedor"),
            "url": conectividad.get("url"),
            "http_status": conectividad.get("http_status"),
            "respondio": conectividad.get("respondio"),
            "errores": conectividad.get("errores") or [],
            "advertencias": conectividad.get("advertencias") or [],
        },
        "siguiente_paso": "Lista para activar produccion controlada." if ok else "Corrige las etapas pendientes antes de produccion.",
    }


@router.post("/empresas/{empresa}/pac/activar-produccion-controlada")
def activar_produccion_controlada_pac_empresa_timbrado(empresa: str, datos: dict | None = None):
    datos = datos or {}
    empresa_norm = _normalizar_empresa(empresa)
    frase = str(datos.get("confirmacion") or "").strip().upper()
    frase_esperada = f"ACTIVAR PRODUCCION {empresa_norm}".upper()
    if frase != frase_esperada:
        raise HTTPException(status_code=400, detail=f"Escribe exactamente: {frase_esperada}")
    checklist = checklist_produccion_pac_empresa_timbrado(empresa_norm, {"forzar_modo_produccion": True})
    if not checklist.get("ok"):
        raise HTTPException(
            status_code=400,
            detail={
                "mensaje": "La empresa no esta lista para produccion.",
                "checklist": checklist,
            },
        )
    facturacion_automatica = bool(datos.get("facturacion_automatica"))
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        cfg = obtener_config_timbrado(conn, empresa_norm) or {"empresa": empresa_norm}
        _asegurar_tabla_snapshots_timbrado(conn)
        conn.execute(
            "INSERT INTO timbrado_config_snapshots (empresa, etiqueta, config_json) VALUES (?, ?, ?)",
            (empresa_norm, "antes de activar produccion PAC", json.dumps(cfg, ensure_ascii=False, default=str)),
        )
        cfg["timbrado_activo"] = True
        cfg["facturacion_automatica"] = facturacion_automatica
        cfg["modo_pruebas"] = False
        guardar_config_timbrado(conn, empresa_norm, cfg)
        registrar_intento_pac(
            conn,
            {"id": 0, "factura": "ACTIVACION_PRODUCCION_CONTROLADA", "empresa": empresa_norm},
            cfg.get("proveedor"),
            "PAC_ACTIVACION_PRODUCCION_OK",
            "Empresa activada para produccion PAC controlada.",
            response={
                "timbrado_activo": True,
                "facturacion_automatica": facturacion_automatica,
                "modo_pruebas": False,
                "checklist_ok": True,
            },
        )
        final_cfg = obtener_config_timbrado(conn, empresa_norm) or {}
    return {
        "ok": True,
        "empresa": empresa_norm,
        "mensaje": "Empresa activada en produccion PAC controlada.",
        "timbrado_activo": bool(final_cfg.get("timbrado_activo")),
        "facturacion_automatica": bool(final_cfg.get("facturacion_automatica")),
        "modo_pruebas": bool(final_cfg.get("modo_pruebas", True)),
        "proveedor": final_cfg.get("proveedor"),
        "checklist": checklist,
    }


@router.post("/empresas/{empresa}/pac/desactivar-produccion")
def desactivar_produccion_pac_empresa_timbrado(empresa: str, datos: dict | None = None):
    datos = datos or {}
    empresa_norm = _normalizar_empresa(empresa)
    frase = str(datos.get("confirmacion") or "").strip().upper()
    frase_esperada = f"DESACTIVAR PRODUCCION {empresa_norm}".upper()
    if frase != frase_esperada:
        raise HTTPException(status_code=400, detail=f"Escribe exactamente: {frase_esperada}")
    mantener_timbrado_activo = bool(datos.get("mantener_timbrado_activo"))
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        cfg = obtener_config_timbrado(conn, empresa_norm) or {"empresa": empresa_norm}
        _asegurar_tabla_snapshots_timbrado(conn)
        conn.execute(
            "INSERT INTO timbrado_config_snapshots (empresa, etiqueta, config_json) VALUES (?, ?, ?)",
            (empresa_norm, "antes de desactivar produccion PAC", json.dumps(cfg, ensure_ascii=False, default=str)),
        )
        cfg["modo_pruebas"] = True
        cfg["facturacion_automatica"] = False
        cfg["timbrado_activo"] = mantener_timbrado_activo
        guardar_config_timbrado(conn, empresa_norm, cfg)
        registrar_intento_pac(
            conn,
            {"id": 0, "factura": "DESACTIVACION_PRODUCCION", "empresa": empresa_norm},
            cfg.get("proveedor"),
            "PAC_PRODUCCION_DESACTIVADA",
            "Produccion PAC desactivada; facturacion automatica apagada y modo pruebas activo.",
            response={
                "timbrado_activo": mantener_timbrado_activo,
                "facturacion_automatica": False,
                "modo_pruebas": True,
            },
        )
        final_cfg = obtener_config_timbrado(conn, empresa_norm) or {}
    return {
        "ok": True,
        "empresa": empresa_norm,
        "mensaje": "Produccion PAC desactivada.",
        "timbrado_activo": bool(final_cfg.get("timbrado_activo")),
        "facturacion_automatica": bool(final_cfg.get("facturacion_automatica")),
        "modo_pruebas": bool(final_cfg.get("modo_pruebas", True)),
        "proveedor": final_cfg.get("proveedor"),
    }


@router.get("/empresas/{empresa}/pac/reporte-produccion")
def descargar_reporte_produccion_pac(empresa: str):
    empresa_norm = _normalizar_empresa(empresa)
    checklist = checklist_produccion_pac_empresa_timbrado(empresa_norm, {"forzar_modo_produccion": False})
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        cfg = obtener_config_timbrado(conn, empresa_norm) or {}
        cola = listar_cola_timbrado(conn, empresa=empresa_norm, limit=500)
        intentos = listar_intentos_pac(conn, empresa=empresa_norm, limit=200)
    cfg_publica = {k: v for k, v in cfg.items() if k not in {"pac_password", "csd_key_password", "pac_cancel_passphrase"}}
    reporte = {
        "generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "empresa": empresa_norm,
        "configuracion": cfg_publica,
        "checklist_produccion": checklist,
        "cola": cola,
        "intentos_pac_recientes": intentos,
    }
    safe = _safe_package_name(empresa_norm or "empresa")
    content = json.dumps(reporte, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{safe}-reporte-produccion-pac.json"'},
    )


@router.get("/pac/estado-produccion")
def estado_produccion_pac_global():
    empresas = listar_empresas_timbrado()
    resultados = []
    for emp in empresas:
        nombre = emp.get("empresa") if isinstance(emp, dict) else emp
        empresa_norm = _normalizar_empresa(nombre)
        try:
            checklist = checklist_produccion_pac_empresa_timbrado(empresa_norm, {"forzar_modo_produccion": False})
            etapas_pendientes = [
                {
                    "etapa": x.get("etapa"),
                    "errores": x.get("errores") or [],
                    "advertencias": x.get("advertencias") or [],
                }
                for x in checklist.get("etapas") or []
                if not x.get("ok")
            ]
            resultados.append({
                "empresa": empresa_norm,
                "ok": bool(checklist.get("ok")),
                "proveedor": checklist.get("proveedor"),
                "modo_pruebas": bool(checklist.get("modo_pruebas", True)),
                "cola": checklist.get("cola") or {},
                "pendientes": etapas_pendientes,
            })
        except Exception as exc:
            resultados.append({
                "empresa": empresa_norm,
                "ok": False,
                "proveedor": "",
                "modo_pruebas": True,
                "cola": {},
                "pendientes": [{"etapa": "checklist", "errores": [str(exc)], "advertencias": []}],
            })
    listas = sum(1 for r in resultados if r.get("ok"))
    produccion = sum(1 for r in resultados if not r.get("modo_pruebas") and r.get("proveedor"))
    return {
        "ok": listas == len(resultados) if resultados else False,
        "total": len(resultados),
        "listas": listas,
        "pendientes": len(resultados) - listas,
        "modo_produccion": produccion,
        "empresas": resultados,
    }


@router.get("/pac/pase-produccion")
def pase_produccion_pac_global():
    def _accion_para_etapa(etapa: str, errores: list[str]) -> str:
        etapa = str(etapa or "")
        texto = " ".join(str(x or "") for x in errores).lower()
        if etapa == "proveedor_real":
            return "Selecciona FINKOK como proveedor PAC real y captura usuario/password PAC."
        if etapa == "modo_produccion":
            return "Cuando el checklist final pase, usa Activar produccion PAC para apagar modo pruebas."
        if etapa == "csd":
            return "Sube CER, KEY y captura el password del CSD de la empresa en Configuracion por Empresa."
        if etapa == "conectividad_pac":
            return "Verifica credenciales PAC, endpoint Finkok y salida a internet desde el servidor."
        if etapa == "cola_sin_atascos":
            return "Libera BLOQUEADO_PAC o recupera TIMBRANDO antes de activar produccion."
        if etapa == "folios":
            return "Captura serie CFDI y folio actual/siguiente para la empresa."
        if "simulado" in texto:
            return "Cambia proveedor SIMULADO por PAC real antes del pase."
        return "Corrige esta etapa y vuelve a ejecutar el pase."

    hosts = []
    pac_global_cfg = cargar_config_pac_global()
    pac_global_publica = _pac_global_publica(pac_global_cfg)
    for entry in _mysql_hosts():
        host, port = _parse_host(entry)
        hosts.append({
            "entry": entry,
            "host": host,
            "port": port,
            "reachable": _host_is_reachable(host, port, timeout=1.5),
            "tipo": "local" if host.startswith("192.168.") else ("tailscale" if host.startswith("100.") else "otro"),
        })
    mysql_ok = False
    mysql_activo = {}
    mysql_error = ""
    try:
        conn_leg = get_legacy_connection()
        cur_leg = conn_leg.cursor(dictionary=True)
        try:
            cur_leg.execute("SELECT DATABASE() AS database_name, @@hostname AS server_hostname, CONNECTION_ID() AS connection_id")
            mysql_activo = cur_leg.fetchone() or {}
        finally:
            cur_leg.close()
            conn_leg.close()
        mysql_ok = True
    except Exception as exc:
        mysql_error = str(exc)

    empresas = listar_empresas_timbrado()
    resultados = []
    for emp in empresas:
        nombre = emp.get("empresa") if isinstance(emp, dict) else emp
        empresa_norm = _normalizar_empresa(nombre)
        try:
            diagnostico = diagnostico_empresa_timbrado(empresa_norm)
            checklist = checklist_produccion_pac_empresa_timbrado(empresa_norm, {"forzar_modo_produccion": False})
            pendientes = []
            acciones = []
            for etapa in checklist.get("etapas") or []:
                if etapa.get("ok"):
                    continue
                errores = etapa.get("errores") or []
                advertencias = etapa.get("advertencias") or []
                pendientes.append({
                    "etapa": etapa.get("etapa"),
                    "errores": errores,
                    "advertencias": advertencias,
                    "accion": _accion_para_etapa(etapa.get("etapa"), errores + advertencias),
                })
                acciones.append(_accion_para_etapa(etapa.get("etapa"), errores + advertencias))
            acciones_unicas = []
            for accion in acciones:
                if accion and accion not in acciones_unicas:
                    acciones_unicas.append(accion)
            resultados.append({
                "empresa": empresa_norm,
                "lista": bool(checklist.get("ok")),
                "datos_base_ok": bool(diagnostico.get("ok_pre_pac")),
                "proveedor": checklist.get("proveedor") or diagnostico.get("proveedor") or "",
                "modo_pruebas": bool(checklist.get("modo_pruebas", True)),
                "cola": checklist.get("cola") or {},
                "output_disk": diagnostico.get("output_disk") or {},
                "pendientes": pendientes,
                "acciones": acciones_unicas,
                "siguiente_paso": "Puede activarse en produccion controlada." if checklist.get("ok") else (acciones_unicas[0] if acciones_unicas else "Corrige pendientes y vuelve a revisar."),
            })
        except Exception as exc:
            resultados.append({
                "empresa": empresa_norm,
                "lista": False,
                "datos_base_ok": False,
                "proveedor": "",
                "modo_pruebas": True,
                "cola": {},
                "output_disk": {},
                "pendientes": [{"etapa": "checklist", "errores": [str(exc)], "advertencias": [], "accion": "Revisa conectividad y configuracion de la empresa."}],
                "acciones": ["Revisa conectividad y configuracion de la empresa."],
                "siguiente_paso": "Revisa conectividad y configuracion de la empresa.",
            })
    empresas_operativas = [r for r in resultados if r.get("datos_base_ok") or r.get("cola")]
    scope = empresas_operativas or resultados
    listas = sum(1 for r in scope if r.get("lista"))
    total_scope = len(scope)
    return {
        "ok": bool(mysql_ok) and bool(scope) and listas == total_scope,
        "mysql": {
            "ok": mysql_ok,
            "database": "comandas_db",
            "usuario": LEGACY_CFG.get("mysql_user"),
            "hosts": hosts,
            "activo": mysql_activo,
            "error": mysql_error,
        },
        "pac_global": {
            "ok": bool(str(pac_global_cfg.get("pac_usuario") or "").strip() and str(pac_global_cfg.get("pac_password") or "").strip()),
            **pac_global_publica,
        },
        "total": len(resultados),
        "total_operativas": len(empresas_operativas),
        "listas": listas,
        "pendientes": total_scope - listas,
        "operativas_detectadas": [r.get("empresa") for r in empresas_operativas],
        "empresas": resultados,
        "siguiente_paso_global": (
            "Listo para activar produccion controlada por empresa."
            if mysql_ok and scope and listas == total_scope
            else "Carga CSD y credenciales PAC reales en las empresas operativas; despues ejecuta Checklist produccion y Activar produccion PAC."
        ),
    }


@router.get("/pac/eventos-produccion")
def eventos_produccion_pac(empresa: str | None = None, limit: int = 100):
    eventos = [
        "PAC_ACTIVACION_CONTROLADA_OK",
        "PAC_ACTIVACION_PRODUCCION_OK",
        "PAC_PRODUCCION_DESACTIVADA",
        "PAC_BLOQUEO_LIBERADO",
        "PAC_TIMBRANDO_RECUPERADO",
    ]
    placeholders = ",".join(["?"] * len(eventos))
    params = list(eventos)
    sql = f"SELECT * FROM timbrado_pac_intentos WHERE estatus IN ({placeholders})"
    if empresa:
        sql += " AND empresa = ?"
        params.append(_normalizar_empresa(empresa))
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(max(1, min(int(limit or 100), 500)))
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    return rows


@router.get("/correo-documentos")
def listar_correo_documentos(empresa: str | None = None):
    with get_timbrado_connection() as conn:
        _asegurar_tabla_correo_documentos(conn)
        if empresa is not None:
            rows = conn.execute(
                "SELECT * FROM soporte_correo_documentos WHERE empresa = ? ORDER BY tipo_documento",
                (_normalizar_empresa(empresa),),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM soporte_correo_documentos ORDER BY empresa, tipo_documento").fetchall()
        return [dict(r) for r in rows]


@router.get("/correo-documentos/{tipo_documento}")
def ver_correo_documento(tipo_documento: str, empresa: str = ""):
    tipo = str(tipo_documento or "").strip().lower()
    empresa_norm = _normalizar_empresa(empresa)
    if not tipo:
        raise HTTPException(status_code=400, detail="Tipo de documento requerido")
    with get_timbrado_connection() as conn:
        _asegurar_tabla_correo_documentos(conn)
        row = conn.execute(
            "SELECT * FROM soporte_correo_documentos WHERE empresa = ? AND tipo_documento = ? LIMIT 1",
            (empresa_norm, tipo),
        ).fetchone()
        cfg_empresa = obtener_config_timbrado(conn, empresa_norm) if empresa_norm else {}
        return dict(row) if row else {
            "empresa": empresa_norm,
            "tipo_documento": tipo,
            "nombre_remitente": cfg_empresa.get("razon_social") or empresa_norm,
            "correo_remitente": "",
            "smtp_host": "",
            "smtp_port": 587,
            "smtp_usuario": "",
            "smtp_password": "",
            "smtp_ssl": 0,
            "smtp_starttls": 1,
            "asunto_template": "Factura fiscal {folio_serie}",
            "cuerpo_template": (
                "Estimado cliente {cliente_nombre},\n\n"
                "Adjuntamos la factura fiscal {folio_serie}.\n"
                "UUID: {uuid}\n\n"
                "Saludos,\n{empresa_nombre}"
            ),
            "activo": 1,
        }


@router.put("/correo-documentos/{tipo_documento}")
def actualizar_correo_documento(tipo_documento: str, datos: dict):
    tipo = str(tipo_documento or "").strip().lower()
    if not tipo:
        raise HTTPException(status_code=400, detail="Tipo de documento requerido")
    datos = datos or {}
    empresa = _normalizar_empresa(datos.get("empresa"))
    if not empresa:
        raise HTTPException(status_code=400, detail="Empresa requerida")
    with get_timbrado_connection() as conn:
        _asegurar_tabla_correo_documentos(conn)
        conn.execute(
            """
            INSERT INTO soporte_correo_documentos (
                empresa, tipo_documento, nombre_remitente, correo_remitente, smtp_host, smtp_port,
                smtp_usuario, smtp_password, smtp_ssl, smtp_starttls, asunto_template, cuerpo_template, activo
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(empresa, tipo_documento) DO UPDATE SET
                nombre_remitente = excluded.nombre_remitente,
                correo_remitente = excluded.correo_remitente,
                smtp_host = excluded.smtp_host,
                smtp_port = excluded.smtp_port,
                smtp_usuario = excluded.smtp_usuario,
                smtp_password = excluded.smtp_password,
                smtp_ssl = excluded.smtp_ssl,
                smtp_starttls = excluded.smtp_starttls,
                asunto_template = excluded.asunto_template,
                cuerpo_template = excluded.cuerpo_template,
                activo = excluded.activo
            """,
            (
                empresa,
                tipo,
                str(datos.get("nombre_remitente") or "").strip(),
                str(datos.get("correo_remitente") or "").strip(),
                str(datos.get("smtp_host") or "").strip(),
                int(datos.get("smtp_port") or 587),
                str(datos.get("smtp_usuario") or "").strip(),
                str(datos.get("smtp_password") or "").strip(),
                1 if datos.get("smtp_ssl") else 0,
                1 if datos.get("smtp_starttls", True) else 0,
                str(datos.get("asunto_template") or "").strip(),
                str(datos.get("cuerpo_template") or "").strip(),
                1 if datos.get("activo", True) else 0,
            ),
        )
    return {"mensaje": f"Cuenta de correo guardada para {empresa} / {tipo}."}


@router.post("/correo-documentos/{tipo_documento}/probar")
def probar_correo_documento(tipo_documento: str, datos: dict):
    tipo = str(tipo_documento or "").strip().lower()
    empresa = _normalizar_empresa((datos or {}).get("empresa"))
    destinatario = str((datos or {}).get("destinatario") or "").strip()
    if not empresa:
        raise HTTPException(status_code=400, detail="Empresa requerida")
    if not destinatario or "@" not in destinatario:
        raise HTTPException(status_code=400, detail="Correo destino invalido")
    with get_timbrado_connection() as conn:
        cfg = _obtener_correo_documento(conn, tipo, empresa)
    if not cfg:
        raise HTTPException(status_code=400, detail=f"No hay cuenta activa configurada para {empresa} / {tipo}.")
    asunto = f"Prueba de correo {empresa} / {tipo}"
    cuerpo = (
        "Este es un correo de prueba enviado desde Galacticos Web.\n\n"
        f"Empresa: {empresa}\n"
        f"Tipo de documento: {tipo}\n"
        f"Fecha: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
    )
    try:
        envio_info = _enviar_correo_smtp(cfg, destinatario, asunto, cuerpo, [])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo enviar la prueba: {exc}")
    return {
        "mensaje": f"Correo de prueba aceptado por SMTP para {destinatario}.",
        "detalle_envio": envio_info,
    }


@router.get("/opciones-cfdi/{folio}")
def ver_opciones_cfdi_folio(folio: str):
    with get_timbrado_connection() as conn:
        conn_legacy = getattr(conn, "_conn", None)
        close_legacy = False
        if conn_legacy is None:
            conn_legacy = get_legacy_connection()
            close_legacy = True
        try:
            factura, resolucion, empresa, numero_cliente, nombre_cliente, receptor = _resolver_factura_para_opciones_cfdi(
                conn,
                conn_legacy,
                folio,
            )
            guardado = _obtener_defaults_cfdi_cliente(conn, empresa, numero_cliente)
            condiciones_default = _condiciones_pago_desde_dias(receptor.get("dias_credito"))
            opciones = {
                "uso_cfdi": guardado.get("uso_cfdi") or receptor.get("uso_cfdi") or "G01",
                "forma_pago": guardado.get("forma_pago") or "99",
                "metodo_pago": guardado.get("metodo_pago") or "PPD",
                "exportacion": guardado.get("exportacion") or "01",
                "condiciones_pago": guardado.get("condiciones_pago") or condiciones_default,
                "moneda": guardado.get("moneda") or "MXN",
            }
            # El CFDI se construye con el receptor resuelto (incluye la ficha
            # configurada en Receptores Fiscales), no con el nombre que venga
            # solamente en la comanda. Exponemos el resumen al modal para que
            # el usuario pueda revisar la fuente antes de emitir.
            receptor_fiscal = dict(resolucion.get("receptor_fiscal") or {})
            datos_receptor = {
                "clave": str(receptor.get("numero") or numero_cliente or "").strip(),
                "nombre": str(receptor.get("razon_social") or receptor.get("nombre") or nombre_cliente or "").strip(),
                "rfc": str(receptor.get("rfc") or "").strip().upper(),
                "cp_fiscal": str(receptor.get("codigo_postal") or "").strip(),
                "regimen_fiscal": str(receptor.get("regimen_fiscal") or "").strip(),
                "uso_cfdi": str(opciones.get("uso_cfdi") or receptor.get("uso_cfdi") or "").strip().upper(),
                "configurado": bool(receptor_fiscal),
            }
            faltantes_receptor = []
            for campo, etiqueta in (
                ("rfc", "RFC"),
                ("nombre", "razón social"),
                ("cp_fiscal", "CP fiscal"),
                ("regimen_fiscal", "régimen fiscal"),
                ("uso_cfdi", "uso de CFDI"),
            ):
                if not datos_receptor[campo]:
                    faltantes_receptor.append(etiqueta)
            return {
                "factura": factura.get("factura"),
                "empresa": empresa,
                "numero_cliente": numero_cliente,
                "nombre_cliente": nombre_cliente,
                "tiene_guardado": bool(guardado),
                "opciones": opciones,
                "modo_facturacion": resolucion.get("modo_facturacion"),
                "receptor_fiscal": datos_receptor,
                "faltantes_receptor": faltantes_receptor,
            }
        finally:
            if close_legacy:
                conn_legacy.close()


@router.post("/validar-cfdi/{folio}")
def validar_cfdi_folio(folio: str, datos: dict | None = None):
    opciones_cfdi = _normalizar_opciones_cfdi((datos or {}).get("opciones_cfdi") or {})
    with get_timbrado_connection() as conn:
        conn_legacy = getattr(conn, "_conn", None)
        close_legacy = False
        if conn_legacy is None:
            conn_legacy = get_legacy_connection()
            close_legacy = True
        cur = conn_legacy.cursor(dictionary=True)
        try:
            cur.execute("SELECT id FROM facturas WHERE factura = %s LIMIT 1", (folio,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Factura {folio} no encontrada")
            factura = _snapshot_factura(conn_legacy, int(row["id"]))
            resolucion = resolver_receptor_timbrado(conn, conn_legacy, factura, incluir_preview=False)
            empresa = _normalizar_empresa(factura.get("empresa"))
            config = obtener_config_timbrado(conn, empresa)
            validacion = validar_pre_cfdi_factura(factura, config, resolucion=resolucion, opciones_cfdi=opciones_cfdi)
            return {
                "factura": factura.get("factura"),
                "empresa": empresa,
                "cliente_receptor_numero": resolucion.get("cliente_receptor_numero"),
                "cliente_receptor_nombre": resolucion.get("cliente_receptor_nombre"),
                **validacion,
            }
        finally:
            cur.close()
            if close_legacy:
                conn_legacy.close()


@router.post("/prexml-cfdi/{folio}")
def generar_prexml_cfdi_folio(folio: str, datos: dict | None = None):
    opciones_cfdi = _normalizar_opciones_cfdi((datos or {}).get("opciones_cfdi") or {})
    cfdi_folio = str((datos or {}).get("folio_cfdi") or "PREPAC").strip() or "PREPAC"
    with get_timbrado_connection() as conn:
        conn_legacy = getattr(conn, "_conn", None)
        close_legacy = False
        if conn_legacy is None:
            conn_legacy = get_legacy_connection()
            close_legacy = True
        cur = conn_legacy.cursor(dictionary=True)
        try:
            cur.execute("SELECT id FROM facturas WHERE factura = %s LIMIT 1", (folio,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Factura {folio} no encontrada")
            factura = _snapshot_factura(conn_legacy, int(row["id"]))
            resolucion = resolver_receptor_timbrado(conn, conn_legacy, factura, incluir_preview=False)
            empresa = _normalizar_empresa(factura.get("empresa"))
            config = obtener_config_timbrado(conn, empresa)
            validacion = validar_pre_cfdi_factura(factura, config, resolucion=resolucion, opciones_cfdi=opciones_cfdi)
            if (datos or {}).get("bloquear_si_invalido") and not validacion.get("ok"):
                raise HTTPException(status_code=400, detail=validacion)
            addenda_render = renderizar_addenda_factura(conn, conn_legacy, factura)
            item = {
                "modo_facturacion": resolucion.get("modo_facturacion"),
                "cliente_receptor_nombre": resolucion.get("cliente_receptor_nombre"),
                "cfdi_opciones_json": json.dumps(opciones_cfdi, ensure_ascii=False),
            }
            serie = str(config.get("serie_cfdi") or "").strip() or "CFDI"
            xml = _generar_cfdi_simulado_xml(
                factura, config, addenda_render, item, cfdi_folio, serie,
                receptor_resuelto=resolucion.get("cliente_receptor"),
            )
            headers = {
                "X-CFDI-Validacion": "ok" if validacion.get("ok") else "faltantes",
                "X-CFDI-Faltantes": str(len(validacion.get("faltantes") or [])),
                "Content-Disposition": f'inline; filename="{folio}-prepac.xml"',
            }
            return Response(content=xml, media_type="application/xml", headers=headers)
        finally:
            cur.close()
            if close_legacy:
                conn_legacy.close()


@router.post("/prexml-cfdi/{folio}/sellar")
def sellar_prexml_cfdi_folio(folio: str, datos: dict | None = None):
    datos = datos or {}
    respuesta = generar_prexml_cfdi_folio(folio, datos)
    xml = respuesta.body.decode("utf-8") if isinstance(respuesta.body, bytes) else str(respuesta.body)
    conn_legacy = get_legacy_connection()
    cur = conn_legacy.cursor(dictionary=True)
    try:
        cur.execute("SELECT empresa FROM facturas WHERE factura = %s LIMIT 1", (folio,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Factura {folio} no encontrada")
        empresa = _normalizar_empresa(row.get("empresa"))
        with get_timbrado_connection() as conn:
            config = obtener_config_timbrado(conn, empresa)
        sellado = sellar_xml_cfdi(xml, config)
        proveedor = str((config or {}).get("proveedor") or "").strip().upper()
        faltantes_pac = []
        if not proveedor:
            faltantes_pac.append("Falta proveedor PAC.")
        elif proveedor == "SIMULADO":
            faltantes_pac.append("Proveedor en modo SIMULADO; selecciona el PAC real para enviar.")
        for campo, etiqueta in (
            ("pac_url", "URL del PAC"),
            ("pac_usuario", "usuario PAC"),
            ("pac_password", "password PAC"),
        ):
            if proveedor and proveedor != "SIMULADO" and not str((config or {}).get(campo) or "").strip():
                faltantes_pac.append(f"Falta {etiqueta}.")
        if datos.get("descargar_xml"):
            if not sellado.get("ok"):
                raise HTTPException(status_code=400, detail={"mensaje": "No se pudo sellar el XML.", "errores": sellado.get("errores") or []})
            return Response(
                content=sellado["xml"],
                media_type="application/xml",
                headers={"Content-Disposition": f'inline; filename="{folio}-prepac-sellado.xml"'},
            )
        return {
            "factura": folio,
            "empresa": empresa,
            "proveedor": proveedor,
            "ok": bool(sellado.get("ok")),
            "listo_pac": bool(sellado.get("ok")) and not faltantes_pac,
            "faltantes_pac": faltantes_pac,
            "errores": sellado.get("errores") or [],
            "advertencias": sellado.get("advertencias") or [],
            "cadena_original_length": len(sellado.get("cadena_original") or ""),
            "sello_length": len(sellado.get("sello") or ""),
            "xml_length": len(sellado.get("xml") or ""),
            "incluir_xml": bool(datos.get("incluir_xml")),
            **({"xml": sellado.get("xml")} if datos.get("incluir_xml") else {}),
        }
    finally:
        cur.close()
        conn_legacy.close()


@router.post("/prexml-cfdi/{folio}/paquete-pac")
def preparar_paquete_pac_folio(folio: str, datos: dict | None = None):
    datos = datos or {}
    respuesta = sellar_prexml_cfdi_folio(folio, {**datos, "incluir_xml": True})
    if not isinstance(respuesta, dict):
        raise HTTPException(status_code=400, detail="No se pudo preparar XML sellado para paquete PAC.")
    if not respuesta.get("ok"):
        return {
            "factura": folio,
            "empresa": respuesta.get("empresa"),
            "ok": False,
            "xml_sellado": False,
            "errores": respuesta.get("errores") or ["No se pudo sellar el XML."],
            "advertencias": respuesta.get("advertencias") or [],
        }
    empresa = respuesta.get("empresa")
    with get_timbrado_connection() as conn:
        cfg = obtener_config_timbrado(conn, empresa)
        paquete = preparar_paquete_pac(cfg.get("proveedor"), cfg, respuesta.get("xml") or "")
        registrar_intento_pac(
            conn,
            {"id": 0, "factura": folio, "empresa": empresa},
            paquete.get("proveedor"),
            "PAC_PAQUETE_OK" if paquete.get("ok") else "PAC_PAQUETE_ERROR",
            "Paquete PAC preparado en modo seco." if paquete.get("ok") else "Paquete PAC incompleto en modo seco.",
            folio_candidato=str(datos.get("folio_cfdi") or "PREPAC"),
            response={
                "xml_sha256": paquete.get("xml_sha256"),
                "xml_bytes": paquete.get("xml_bytes"),
                "xml_base64_chars": paquete.get("xml_base64_chars"),
                "errores": paquete.get("errores") or [],
                "advertencias": paquete.get("advertencias") or [],
                "request_preview": paquete.get("request_preview") or {},
            },
        )
    return {
        "factura": folio,
        "empresa": empresa,
        "xml_sellado": True,
        **paquete,
    }


@router.post("/simular-cfdi/{folio}")
def simular_cfdi_folio(folio: str, datos: dict | None = None):
    datos = datos or {}
    payload = dict(datos)
    if not payload.get("opciones_cfdi"):
        try:
            payload["opciones_cfdi"] = (ver_opciones_cfdi_folio(folio).get("opciones") or {})
        except Exception:
            payload["opciones_cfdi"] = {}
    payload["bloquear_si_invalido"] = False
    validacion = validar_cfdi_folio(folio, payload)
    respuesta = generar_prexml_cfdi_folio(folio, payload)
    xml = respuesta.body.decode("utf-8") if isinstance(respuesta.body, bytes) else str(respuesta.body)
    resultado = {
        "factura": folio,
        "emitible": bool(validacion.get("ok")),
        "validacion": validacion,
        "resumen_xml": _resumen_xml_cfdi(xml),
        "persistido": False,
        "consume_folio": False,
    }
    if datos.get("incluir_xml"):
        resultado["xml"] = xml
    return resultado


@router.get("/addenda/{empresa}/{numero_cliente}")
def ver_addenda_cliente(empresa: str, numero_cliente: str):
    with get_timbrado_connection() as conn:
        data = obtener_addenda_cliente(conn, empresa, numero_cliente)
    return data or {
        "empresa": _normalizar_empresa(empresa),
        "numero_cliente": str(numero_cliente or "").strip(),
        "addenda_activa": 0,
        "addenda_tipo": None,
        "addenda_config": {},
    }


@router.put("/addenda/{empresa}/{numero_cliente}")
def actualizar_addenda_cliente(empresa: str, numero_cliente: str, datos: dict):
    with get_timbrado_connection() as conn:
        guardar_addenda_cliente(conn, empresa, numero_cliente, datos or {})
    return {"mensaje": f"Addenda guardada para cliente {numero_cliente} en {empresa}."}


@router.delete("/addenda/{empresa}/{numero_cliente}")
def borrar_addenda_cliente(empresa: str, numero_cliente: str):
    with get_timbrado_connection() as conn:
        eliminar_addenda_cliente(conn, empresa, numero_cliente)
    return {"mensaje": f"Addenda eliminada para cliente {numero_cliente} en {empresa}."}


@router.get("/addendas-disponibles")
def ver_addendas_disponibles():
    return listar_addendas_disponibles()


@router.get("/addendas-clientes")
def ver_addendas_clientes_configuradas(empresa: str | None = None):
    with get_timbrado_connection() as conn:
        return listar_addendas_clientes_configuradas(conn, empresa=empresa)


@router.get("/receptores-fiscales")
def ver_receptores_fiscales(empresa: str | None = None):
    with get_timbrado_connection() as conn:
        return listar_receptores_fiscales(conn, empresa=empresa)


@router.get("/receptores-fiscales/{empresa}/{clave_receptor}")
def ver_receptor_fiscal(empresa: str, clave_receptor: str):
    with get_timbrado_connection() as conn:
        data = obtener_receptor_fiscal(conn, empresa, clave_receptor)
    return data or {
        "empresa": _normalizar_empresa(empresa),
        "clave_receptor": str(clave_receptor or "").strip(),
        "addenda_activa": 0,
        "addenda_tipo": None,
        "addenda_config": {},
    }


@router.put("/receptores-fiscales")
def actualizar_receptor_fiscal(datos: dict):
    with get_timbrado_connection() as conn:
        guardar_receptor_fiscal(conn, datos or {})
    return {"mensaje": "Receptor fiscal guardado correctamente."}


@router.patch("/receptores-fiscales/{empresa}/{clave_receptor}/correo")
def actualizar_correo_receptor_fiscal_endpoint(empresa: str, clave_receptor: str, datos: dict):
    with get_timbrado_connection() as conn:
        actualizar_correo_receptor_fiscal(conn, empresa, clave_receptor, (datos or {}).get("correo_envio"))
    return {"mensaje": "Correo del receptor fiscal actualizado correctamente."}


@router.post("/receptores-fiscales/importar")
def importar_receptores_fiscales_archivo(datos: dict):
    empresa = _normalizar_empresa((datos or {}).get("empresa"))
    if empresa != "EZA2007":
        raise HTTPException(status_code=400, detail="La importación masiva de receptores está habilitada únicamente para EZA2007.")
    with get_timbrado_connection() as conn:
        resultado = importar_receptores_fiscales(conn, empresa, (datos or {}).get("registros") or [])
    importados = int(resultado.get("importados") or 0)
    omitidos = int(resultado.get("omitidos") or 0)
    detalle = " ".join(resultado.get("errores") or [] if omitidos <= 12 else (resultado.get("errores") or [])[:12])
    mensaje = f"{importados} receptor(es) fiscal(es) importado(s) para EZA2007."
    if omitidos:
        mensaje += f" {omitidos} registro(s) se omitieron por datos obligatorios faltantes."
    return {"mensaje": mensaje, "total": importados, "omitidos": omitidos, "errores": resultado.get("errores") or [], "detalle": detalle}


@router.delete("/receptores-fiscales/{empresa}/{clave_receptor}")
def borrar_receptor_fiscal(empresa: str, clave_receptor: str):
    with get_timbrado_connection() as conn:
        eliminar_receptor_fiscal(conn, empresa, clave_receptor)
    return {"mensaje": "Receptor fiscal eliminado correctamente."}


@router.get("/productos-fiscales")
def ver_productos_fiscales(texto: str = "", limit: int = 400):
    conn = get_legacy_connection()
    try:
        return listar_productos_fiscales(conn, texto=texto, limit=limit)
    finally:
        conn.close()


@router.put("/productos-fiscales")
def actualizar_producto_fiscal(datos: dict):
    conn = get_legacy_connection()
    try:
        try:
            guardar_producto_fiscal(conn, datos or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"mensaje": "Producto fiscal guardado correctamente."}
    finally:
        conn.close()


@router.put("/productos-fiscales/lote")
def actualizar_productos_fiscales_lote(datos: dict):
    conn = get_legacy_connection()
    try:
        try:
            res = guardar_productos_fiscales_lote(conn, (datos or {}).get("registros") or [])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"mensaje": "Productos fiscales importados correctamente.", **res}
    finally:
        conn.close()


@router.get("/catalogos-sat/prodserv")
def ver_catalogo_sat_prodserv(texto: str = "", limit: int = 500):
    with get_timbrado_connection() as conn:
        return listar_catalogo_prodserv(conn, texto=texto, limit=limit)


@router.put("/catalogos-sat/prodserv")
def actualizar_catalogo_sat_prodserv(datos: dict):
    with get_timbrado_connection() as conn:
        try:
            total = importar_catalogo_sat_prodserv(
                conn,
                (datos or {}).get("registros") or [],
                fuente=(datos or {}).get("fuente"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return {"mensaje": "Catalogo SAT importado correctamente.", "total": total}


@router.get("/catalogos-sat/unidades")
def ver_catalogo_sat_unidades(texto: str = "", limit: int = 200):
    with get_timbrado_connection() as conn:
        return listar_catalogo_unidades(conn, texto=texto, limit=limit)


@router.put("/catalogos-sat/unidades")
def actualizar_catalogo_sat_unidades(datos: dict):
    with get_timbrado_connection() as conn:
        try:
            total = importar_catalogo_sat_unidades(conn, (datos or {}).get("registros") or [])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return {"mensaje": "Catalogo SAT unidades importado correctamente.", "total": total}


@router.get("/consignatarios-clientes")
def ver_consignatarios_clientes(empresa: str | None = None):
    with get_timbrado_connection() as conn:
        rows = listar_consignatarios_clientes(conn, empresa=empresa)
    # Gourmet España conserva su catálogo manual de GLN para addendas. Para las
    # demás empresas el domicilio de entrega vive en la tabla comercial clientes.
    if _texto_empresa_cmp(empresa or "") == _texto_empresa_cmp("Gourmet España"):
        return rows
    resultado = []
    for row in rows:
        cliente = _obtener_cliente_base_completo(row.get("empresa"), row.get("cliente_numero")) or {}
        resultado.append({
            **row,
            "cliente_nombre": cliente.get("nombre") or row.get("cliente_nombre") or "",
            "consignatario_nombre": cliente.get("consignatario") or cliente.get("nombre") or "",
            "direccion_entrega": _direccion_cliente_consignatario(cliente),
        })
    return resultado


def _direccion_cliente_consignatario(cliente: dict) -> str:
    """Arma una dirección de entrega legible sin alterar la ficha comercial."""
    def valor(campo):
        texto = str(cliente.get(campo) or "").strip()
        return "" if texto in {"-", "N/A", "NA"} else texto
    partes = [
        " ".join(valor(k) for k in ("consig_calle", "consig_no_exterior", "consig_no_interior") if valor(k)),
        valor("consig_colonia"),
        valor("consig_delegacion") or valor("consig_municipio"),
        " ".join(valor(k) for k in ("consig_poblacion", "consig_codigo_postal", "consig_estado") if valor(k)),
        valor("consig_pais"),
    ]
    return ", ".join(p for p in partes if p)


def _obtener_cliente_base_completo(empresa: str, cliente_numero: str) -> dict:
    """Obtiene la ficha comercial, incluida la sucursal/consignatario."""
    conn = get_legacy_connection()
    cur = conn.cursor(dictionary=True)
    try:
        empresa_norm = _texto_empresa_cmp(empresa)
        numero_norm = str(cliente_numero or "").strip().replace(",", "")
        cur.execute(
            "SELECT * FROM clientes WHERE REPLACE(CAST(numero AS CHAR), ',', '') = %s ORDER BY id DESC",
            (numero_norm,),
        )
        for row in cur.fetchall() or []:
            if _texto_empresa_cmp(row.get("empresa") or "") == empresa_norm:
                data = dict(row)
                data["numero"] = str(data.get("numero") or "").strip()
                return data
        return {}
    finally:
        cur.close()
        conn.close()


@router.get("/clientes-base")
def buscar_clientes_base(empresa: str, texto: str = ""):
    conn = get_legacy_connection()
    cur = conn.cursor(dictionary=True)
    try:
        empresa = str(empresa or "").strip()
        texto = str(texto or "").strip()
        if not empresa:
            return []
        cur.execute("SELECT DISTINCT empresa FROM clientes ORDER BY empresa")
        empresas_db = [str(r["empresa"] or "").strip() for r in (cur.fetchall() or [])]
        empresa_real = empresa
        empresa_cmp = _texto_empresa_cmp(empresa_real)
        for candidata in empresas_db:
            if _texto_empresa_cmp(candidata) == empresa_cmp:
                empresa_real = candidata
                break
        if texto:
            like = f"%{texto}%"
            cur.execute(
                """
                SELECT CAST(numero AS CHAR) AS numero, nombre, empresa
                FROM clientes
                WHERE CAST(numero AS CHAR) LIKE %s OR nombre LIKE %s
                ORDER BY nombre, numero
                LIMIT 800
                """,
                (like, like),
            )
            empresa_norm = _texto_empresa_cmp(empresa_real)
            return [
                {"numero": str(r["numero"] or "").strip(), "nombre": str(r["nombre"] or "").strip()}
                for r in (cur.fetchall() or [])
                if _texto_empresa_cmp(r["empresa"]) == empresa_norm
            ][:100]
        cur.execute(
            """
            SELECT CAST(numero AS CHAR) AS numero, nombre, empresa
            FROM clientes
            ORDER BY nombre, numero
            LIMIT 1200
            """
        )
        empresa_norm = _texto_empresa_cmp(empresa_real)
        return [
            {"numero": str(r["numero"] or "").strip(), "nombre": str(r["nombre"] or "").strip()}
            for r in (cur.fetchall() or [])
            if _texto_empresa_cmp(r["empresa"]) == empresa_norm
        ][:100]
    finally:
        cur.close()
        conn.close()


@router.get("/clientes-base/{empresa}/{cliente_numero}")
def obtener_cliente_base_fiscal(empresa: str, cliente_numero: str):
    """Obtiene datos fiscales del cliente legado para precargar un receptor."""
    conn = get_legacy_connection()
    cur = conn.cursor(dictionary=True)
    try:
        empresa_norm = _texto_empresa_cmp(empresa)
        numero_norm = str(cliente_numero or "").strip().replace(",", "")
        cur.execute(
            """
            SELECT CAST(numero AS CHAR) AS numero, nombre, empresa, razon_social, rfc,
                   codigo_postal, calle, no_exterior, no_interior, colonia,
                   COALESCE(municipio, alcaldia, '') AS municipio, estado, pais,
                   correo_electronico, dias_credito, consignatario,
                   consig_calle, consig_no_exterior, consig_no_interior, consig_colonia,
                   consig_municipio, consig_delegacion, consig_codigo_postal,
                   consig_poblacion, consig_estado, consig_pais
            FROM clientes
            WHERE REPLACE(CAST(numero AS CHAR), ',', '') = %s
            ORDER BY id DESC
            """,
            (numero_norm,),
        )
        for row in cur.fetchall() or []:
            if _texto_empresa_cmp(row.get("empresa") or "") == empresa_norm:
                data = dict(row)
                data["numero"] = str(data.get("numero") or "").strip()
                data["direccion_consignatario"] = _direccion_cliente_consignatario(data)
                return data
        raise HTTPException(status_code=404, detail="No se encontró el cliente en la empresa seleccionada.")
    finally:
        cur.close()
        conn.close()


@router.get("/consignatarios-clientes/{empresa}/{cliente_numero}")
def ver_consignatario_cliente(empresa: str, cliente_numero: str):
    with get_timbrado_connection() as conn:
        data = obtener_consignatario_cliente(conn, empresa, cliente_numero)
    respuesta = data or {
        "empresa": _normalizar_empresa(empresa),
        "cliente_numero": str(cliente_numero or "").strip(),
        "cliente_nombre": "",
        "gln_consignatario": "",
        "observaciones": "",
    }
    if _texto_empresa_cmp(empresa) != _texto_empresa_cmp("Gourmet España"):
        cliente = _obtener_cliente_base_completo(empresa, cliente_numero)
        if cliente:
            respuesta.update({
                "cliente_nombre": cliente.get("nombre") or respuesta.get("cliente_nombre") or "",
                "consignatario_nombre": cliente.get("consignatario") or cliente.get("nombre") or "",
                "direccion_entrega": _direccion_cliente_consignatario(cliente),
            })
    return respuesta


@router.put("/consignatarios-clientes")
def actualizar_consignatario_cliente(datos: dict):
    with get_timbrado_connection() as conn:
        guardar_consignatario_cliente(conn, datos or {})
    return {"mensaje": "Consignatario guardado correctamente."}


@router.delete("/consignatarios-clientes/{empresa}/{cliente_numero}")
def borrar_consignatario_cliente(empresa: str, cliente_numero: str):
    with get_timbrado_connection() as conn:
        eliminar_consignatario_cliente(conn, empresa, cliente_numero)
    return {"mensaje": "Consignatario eliminado correctamente."}


@router.get("/grupos-clientes")
def ver_grupos_clientes(empresa: str | None = None):
    with get_timbrado_connection() as conn:
        return listar_grupos_clientes_timbrado(conn, empresa=empresa)


@router.get("/grupos-clientes/{grupo_id}")
def ver_grupo_clientes(grupo_id: int):
    with get_timbrado_connection() as conn:
        data = obtener_grupo_clientes_timbrado(conn, grupo_id)
    if not data:
        raise HTTPException(status_code=404, detail="Grupo no encontrado")
    return data


@router.put("/grupos-clientes")
def actualizar_grupo_clientes(datos: dict):
    with get_timbrado_connection() as conn:
        grupo_id = guardar_grupo_clientes_timbrado(conn, datos or {})
    return {"mensaje": "Grupo guardado correctamente.", "grupo_id": grupo_id}


@router.delete("/grupos-clientes/{grupo_id}")
def borrar_grupo_clientes(grupo_id: int):
    with get_timbrado_connection() as conn:
        eliminar_grupo_clientes_timbrado(conn, grupo_id)
    return {"mensaje": "Grupo eliminado correctamente."}


@router.get("/client-name/{cliente_numero}")
def lookup_cliente_nombre(cliente_numero: str, empresa: str | None = None):
    with get_timbrado_connection() as conn:
        nombre = buscar_cliente_nombre(conn, empresa or "", cliente_numero)
    return {"nombre": nombre}


@router.get("/clientes-search")
def search_clientes_timbrado(empresa: str, term: str):
    with get_timbrado_connection() as conn:
        resultados = buscar_clientes_por_term(conn, empresa, term)
    return resultados


@router.get("/reglas-redireccion")
def ver_reglas_redireccion(empresa: str | None = None):
    with get_timbrado_connection() as conn:
        return listar_reglas_redireccion(conn, empresa=empresa)


@router.put("/reglas-redireccion")
def actualizar_regla_redireccion(datos: dict):
    with get_timbrado_connection() as conn:
        try:
            guardar_regla_redireccion(conn, datos or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return {"mensaje": "Regla de redireccion guardada correctamente."}


@router.delete("/reglas-redireccion/{regla_id}")
def borrar_regla_redireccion(regla_id: int):
    with get_timbrado_connection() as conn:
        eliminar_regla_redireccion(conn, regla_id)
    return {"mensaje": "Regla eliminada correctamente."}


@router.get("/resolver-factura/{folio}")
def ver_resolucion_factura(folio: str):
    conn_legacy = get_legacy_connection()
    cur = conn_legacy.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM facturas WHERE factura = %s LIMIT 1", (folio,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Factura {folio} no encontrada")
        factura = _snapshot_factura(conn_legacy, row["id"])
        with get_timbrado_connection() as conn:
            return resolver_receptor_timbrado(conn, conn_legacy, factura)
    finally:
        cur.close()
        conn_legacy.close()


@router.put("/facturas/{folio}/addenda-campos")
def guardar_campos_addenda_factura_folio(folio: str, payload: dict):
    conn_legacy = get_legacy_connection()
    cur = conn_legacy.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, factura, empresa FROM facturas WHERE factura = %s LIMIT 1", (folio,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Factura {folio} no encontrada")
        campos = payload.get("campos") or {}
        if not isinstance(campos, dict):
            raise HTTPException(status_code=400, detail="Los campos de addenda deben enviarse como objeto JSON.")
        with get_timbrado_connection() as conn:
            guardar_campos_addenda_factura(
                conn,
                int(row["id"]),
                row["factura"],
                row["empresa"],
                str(payload.get("addenda_tipo") or "").strip(),
                campos,
            )
        return {
            "mensaje": "Campos de addenda por factura guardados correctamente.",
            "factura": row["factura"],
            "empresa": row["empresa"],
            "campos_guardados": campos,
        }
    finally:
        cur.close()
        conn_legacy.close()


@router.get("/render-addenda/{folio}")
def ver_addenda_renderizada(folio: str):
    conn_legacy = get_legacy_connection()
    cur = conn_legacy.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM facturas WHERE factura = %s LIMIT 1", (folio,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Factura {folio} no encontrada")
        factura = _snapshot_factura(conn_legacy, row["id"])
        with get_timbrado_connection() as conn:
            return renderizar_addenda_factura(conn, conn_legacy, factura)
    finally:
        cur.close()
        conn_legacy.close()


@router.get("/cola")
def ver_cola_timbrado(empresa: str | None = None, estatus: str | None = None, limit: int = 200):
    with get_timbrado_connection() as conn:
        return listar_cola_timbrado(conn, empresa=empresa, estatus=estatus, limit=limit)


@router.get("/cola/{folio}/prexml")
def descargar_prexml_cola(folio: str):
    folio = str(folio or "").strip()
    if not folio:
        raise HTTPException(status_code=400, detail="Falta folio.")
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        row = conn.execute(
            """
            SELECT factura, estatus, xml_path
            FROM timbrado_queue
            WHERE factura = ?
            ORDER BY last_attempt_at DESC, queued_at DESC
            LIMIT 1
            """,
            (folio,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"No hay registro de cola para {folio}.")
    data = dict(row)
    if str(data.get("estatus") or "").upper() != "BLOQUEADO_PAC":
        raise HTTPException(status_code=400, detail="La factura no tiene XML pre-PAC bloqueado.")
    path = str(data.get("xml_path") or "").strip()
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No se encontro el XML pre-PAC en servidor.")
    return FileResponse(
        path,
        media_type="application/xml",
        filename=f"{folio}-prepac.xml",
        headers={"Content-Disposition": f'inline; filename="{folio}-prepac.xml"'},
    )


@router.get("/pac-intentos")
def ver_intentos_pac(factura: str | None = None, empresa: str | None = None, limit: int = 100):
    with get_timbrado_connection() as conn:
        return listar_intentos_pac(conn, factura=factura, empresa=empresa, limit=limit)


@router.get("/pac-intentos/{intento_id}/xml")
def descargar_xml_intento_pac(intento_id: int):
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        row = conn.execute("SELECT id, factura, xml_path FROM timbrado_pac_intentos WHERE id = ? LIMIT 1", (int(intento_id),)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Intento PAC no encontrado.")
    data = dict(row)
    path = str(data.get("xml_path") or "").strip()
    if not path:
        raise HTTPException(status_code=404, detail="El intento PAC no tiene XML asociado.")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No se encontro el XML del intento PAC en servidor.")
    factura = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(data.get("factura") or intento_id))
    return FileResponse(
        path,
        media_type="application/xml",
        filename=f"{factura}-intento-pac-{intento_id}.xml",
        headers={"Content-Disposition": f'inline; filename="{factura}-intento-pac-{intento_id}.xml"'},
    )


@router.post("/cola/procesar")
def procesar_cola(max_items: int = 1):
    resultados = []
    for _ in range(max(1, int(max_items or 1))):
        with get_timbrado_connection() as conn:
            conn_legacy = getattr(conn, "_conn", None)
            close_legacy = False
            if conn_legacy is None:
                conn_legacy = get_legacy_connection()
                close_legacy = True
            try:
                res = procesar_siguiente_timbrado(conn, conn_legacy)
            finally:
                if close_legacy:
                    conn_legacy.close()
        if not res.get("procesado"):
            resultados.append(res)
            break
        resultados.append(res)
    return {"resultados": resultados}


@router.post("/cola/procesar/{folio}")
def procesar_folio_cola(folio: str):
    with get_timbrado_connection() as conn:
        conn_legacy = getattr(conn, "_conn", None)
        close_legacy = False
        if conn_legacy is None:
            conn_legacy = get_legacy_connection()
            close_legacy = True
        try:
            res = procesar_siguiente_timbrado(conn, conn_legacy, folio=folio)
            return {"resultados": [res]}
        finally:
            if close_legacy:
                conn_legacy.close()


def _empresa_folio_cola(conn, folio: str) -> str:
    folio = str(folio or "").strip()
    if not folio:
        raise HTTPException(status_code=400, detail="Falta folio.")
    _asegurar_tablas_timbrado(conn)
    row = conn.execute(
        """
        SELECT empresa
        FROM timbrado_queue
        WHERE factura = ?
        ORDER BY queued_at DESC
        LIMIT 1
        """,
        (folio,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"La factura {folio} no esta en cola de timbrado.")
    empresa = _normalizar_empresa(dict(row).get("empresa"))
    if not empresa:
        raise HTTPException(status_code=400, detail=f"La factura {folio} no tiene empresa en cola.")
    return empresa


@router.post("/cola/liberar-bloqueo-pac/{folio}")
def liberar_bloqueo_pac_folio(folio: str):
    folio = str(folio or "").strip()
    if not folio:
        raise HTTPException(status_code=400, detail="Falta folio.")
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        row = conn.execute(
            """
            SELECT *
            FROM timbrado_queue
            WHERE factura = ?
            ORDER BY queued_at DESC
            LIMIT 1
            """,
            (folio,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"La factura {folio} no esta en cola de timbrado.")
        item = dict(row)
        if str(item.get("estatus") or "").upper() != "BLOQUEADO_PAC":
            raise HTTPException(status_code=400, detail=f"La factura {folio} no esta en BLOQUEADO_PAC.")
        empresa = _normalizar_empresa(item.get("empresa"))
    prueba = prueba_integral_pac_empresa_timbrado(empresa)
    if not prueba.get("ok"):
        raise HTTPException(
            status_code=400,
            detail={
                "mensaje": "La empresa aun no esta lista para liberar el bloqueo PAC.",
                "empresa": empresa,
                "folio": folio,
                "prueba_integral": prueba,
            },
        )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        conn.execute(
            """
            UPDATE timbrado_queue
            SET estatus = 'PENDIENTE',
                ultimo_error = NULL,
                xml_path = '',
                last_attempt_at = ?
            WHERE id = ?
            """,
            (now, item["id"]),
        )
        registrar_intento_pac(
            conn,
            {
                "id": item.get("factura_id") or 0,
                "factura_id": item.get("factura_id") or 0,
                "factura": item.get("factura") or folio,
                "empresa": empresa,
            },
            item.get("proveedor") or (prueba.get("diagnostico") or {}).get("proveedor"),
            "PAC_BLOQUEO_LIBERADO",
            "Bloqueo PAC liberado; la factura vuelve a PENDIENTE para regenerar XML con la configuracion actual.",
            xml_path=item.get("xml_path") or "",
            response={"prueba_integral_ok": True, "estatus_anterior": item.get("estatus")},
        )
    return {
        "ok": True,
        "folio": folio,
        "empresa": empresa,
        "estatus": "PENDIENTE",
        "mensaje": "Bloqueo PAC liberado. La factura puede procesarse nuevamente con XML fresco.",
        "prueba_integral": prueba,
    }


@router.post("/cola/liberar-bloqueos-pac")
def liberar_bloqueos_pac(datos: dict | None = None):
    datos = datos or {}
    empresa = _normalizar_empresa(datos.get("empresa") or "")
    max_items = max(1, min(int(datos.get("max_items") or 25), 50))
    if not empresa:
        raise HTTPException(status_code=400, detail="Selecciona empresa para liberar bloqueos PAC.")
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        rows = conn.execute(
            """
            SELECT factura
            FROM timbrado_queue
            WHERE empresa = ? AND estatus = 'BLOQUEADO_PAC'
            ORDER BY queued_at ASC
            LIMIT ?
            """,
            (empresa, max_items),
        ).fetchall()
    folios = [str(dict(r).get("factura") or "").strip() for r in rows if str(dict(r).get("factura") or "").strip()]
    resultados = []
    for folio in folios:
        try:
            res = liberar_bloqueo_pac_folio(folio)
            resultados.append({"folio": folio, "ok": True, "mensaje": res.get("mensaje")})
        except HTTPException as exc:
            resultados.append({"folio": folio, "ok": False, "mensaje": exc.detail})
    liberadas = sum(1 for r in resultados if r.get("ok"))
    return {
        "ok": True,
        "empresa": empresa,
        "total_encontradas": len(folios),
        "liberadas": liberadas,
        "fallidas": len(resultados) - liberadas,
        "resultados": resultados,
    }


@router.post("/cola/recuperar-timbrando")
def recuperar_timbrando_colgadas(datos: dict | None = None):
    datos = datos or {}
    empresa = _normalizar_empresa(datos.get("empresa") or "")
    minutos = max(5, min(int(datos.get("minutos") or 30), 1440))
    max_items = max(1, min(int(datos.get("max_items") or 25), 100))
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutos)).strftime("%Y-%m-%d %H:%M:%S")
    params = [cutoff]
    sql = """
        SELECT *
        FROM timbrado_queue
        WHERE estatus = 'TIMBRANDO'
          AND (last_attempt_at IS NULL OR last_attempt_at < ?)
    """
    if empresa:
        sql += " AND empresa = ?"
        params.append(empresa)
    sql += " ORDER BY last_attempt_at ASC, queued_at ASC LIMIT ?"
    params.append(max_items)
    with get_timbrado_connection() as conn:
        _asegurar_tablas_timbrado(conn)
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for item in rows:
            mensaje = f"Recuperada de TIMBRANDO atorado despues de {minutos} minutos; vuelve a PENDIENTE para reintento controlado."
            conn.execute(
                """
                UPDATE timbrado_queue
                SET estatus = 'PENDIENTE',
                    ultimo_error = ?,
                    last_attempt_at = ?
                WHERE id = ?
                """,
                (mensaje, now, item["id"]),
            )
            registrar_intento_pac(
                conn,
                {
                    "id": item.get("factura_id") or 0,
                    "factura_id": item.get("factura_id") or 0,
                    "factura": item.get("factura") or "",
                    "empresa": item.get("empresa") or "",
                },
                item.get("proveedor") or "",
                "PAC_TIMBRANDO_RECUPERADO",
                mensaje,
                xml_path=item.get("xml_path") or "",
                response={"minutos": minutos, "estatus_anterior": item.get("estatus"), "last_attempt_at": item.get("last_attempt_at")},
            )
    return {
        "ok": True,
        "empresa": empresa,
        "minutos": minutos,
        "recuperadas": len(rows),
        "folios": [r.get("factura") for r in rows],
    }


@router.post("/cola/procesar-controlado/{folio}")
def procesar_folio_cola_controlado(folio: str):
    with get_timbrado_connection() as conn:
        empresa = _empresa_folio_cola(conn, folio)
    prueba = prueba_integral_pac_empresa_timbrado(empresa)
    if not prueba.get("ok"):
        raise HTTPException(
            status_code=400,
            detail={
                "mensaje": "La empresa de esta factura no esta lista para procesar PAC controlado.",
                "empresa": empresa,
                "folio": folio,
                "prueba_integral": prueba,
            },
        )
    with get_timbrado_connection() as conn:
        conn_legacy = getattr(conn, "_conn", None)
        close_legacy = False
        if conn_legacy is None:
            conn_legacy = get_legacy_connection()
            close_legacy = True
        try:
            res = procesar_siguiente_timbrado(conn, conn_legacy, folio=folio)
            return {"controlado": True, "empresa": empresa, "prueba_integral": prueba, "resultados": [res]}
        finally:
            if close_legacy:
                conn_legacy.close()


@router.post("/cola/procesar-controlado-lote")
def procesar_lote_cola_controlado(folios: list[str]):
    prevalidacion = prevalidar_lote_cola_controlado(folios)
    folios_limpios = prevalidacion.get("folios") or []
    previsualizaciones = prevalidacion.get("previsualizaciones") or []
    pendientes = prevalidacion.get("pendientes") or []
    if pendientes:
        raise HTTPException(
            status_code=400,
            detail={
                "mensaje": "El lote no se proceso porque una o mas facturas no pasan la vista PAC controlada.",
                "pendientes": pendientes,
                "previsualizaciones": previsualizaciones,
            },
        )

    resultados = []
    for folio in folios_limpios:
        res = procesar_folio_cola_controlado(folio)
        resultado = (res.get("resultados") or [{}])[0]
        resultados.append({"folio": folio, **resultado})
        if not resultado.get("procesado"):
            break
    procesadas = sum(1 for r in resultados if r.get("procesado"))
    return {
        "controlado": True,
        "total_solicitadas": len(folios_limpios),
        "procesadas": procesadas,
        "detenido": procesadas != len(folios_limpios),
        "previsualizaciones": previsualizaciones,
        "resultados": resultados,
    }


@router.post("/cola/prevalidar-controlado-lote")
def prevalidar_lote_cola_controlado(folios: list[str]):
    folios_limpios = []
    vistos = set()
    for folio in folios or []:
        clean = str(folio or "").strip()
        if clean and clean not in vistos:
            folios_limpios.append(clean)
            vistos.add(clean)
    if not folios_limpios:
        raise HTTPException(status_code=400, detail="Selecciona al menos una factura pendiente.")
    if len(folios_limpios) > 25:
        raise HTTPException(status_code=400, detail="Prevalida maximo 25 facturas por lote controlado.")

    previsualizaciones = []
    pendientes = []
    listas = []
    for folio in folios_limpios:
        vista = previsualizar_folio_cola_controlado(folio, {"incluir_xml": False})
        previsualizaciones.append(vista)
        resumen = {
            "folio": folio,
            "empresa": vista.get("empresa"),
            "cliente": vista.get("cliente_receptor_nombre"),
            "folio_candidato": f"{vista.get('serie') or ''}{vista.get('folio_candidato') or ''}",
            "etapas": vista.get("etapas") or [],
            "siguiente_paso": vista.get("siguiente_paso"),
        }
        if vista.get("listo_controlado"):
            listas.append(resumen)
        else:
            pendientes.append(resumen)
    return {
        "ok": not pendientes,
        "folios": folios_limpios,
        "total": len(folios_limpios),
        "listas": len(listas),
        "pendientes_count": len(pendientes),
        "listas_detalle": listas,
        "pendientes": pendientes,
        "previsualizaciones": previsualizaciones,
    }


@router.post("/cola/previsualizar-controlado/{folio}")
def previsualizar_folio_cola_controlado(folio: str, datos: dict | None = None):
    datos = datos or {}
    opciones_cfdi = _normalizar_opciones_cfdi((datos or {}).get("opciones_cfdi") or {})
    incluir_xml = bool(datos.get("incluir_xml"))
    with get_timbrado_connection() as conn:
        conn_legacy = getattr(conn, "_conn", None)
        close_legacy = False
        if conn_legacy is None:
            conn_legacy = get_legacy_connection()
            close_legacy = True
        cur = conn_legacy.cursor(dictionary=True)
        try:
            cur.execute("SELECT id FROM facturas WHERE factura = %s LIMIT 1", (folio,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Factura {folio} no encontrada")
            factura = _snapshot_factura(conn_legacy, int(row["id"]))
            empresa = _normalizar_empresa(factura.get("empresa"))
            config = obtener_config_timbrado(conn, empresa) or {}
            proveedor = str(config.get("proveedor") or "").strip().upper()
            cola_row = conn.execute(
                """
                SELECT factura, estatus, intento_count, ultimo_error, queued_at, last_attempt_at
                FROM timbrado_queue
                WHERE factura = ?
                ORDER BY queued_at DESC
                LIMIT 1
                """,
                (folio,),
            ).fetchone()
            cola = dict(cola_row) if cola_row else {}
            diagnostico = diagnostico_empresa_timbrado(empresa)
            resolucion = resolver_receptor_timbrado(conn, conn_legacy, factura, incluir_preview=False)
            validacion = validar_pre_cfdi_factura(factura, config, resolucion=resolucion, opciones_cfdi=opciones_cfdi)
            folio_candidato = _obtener_siguiente_folio(conn, empresa) if config else "PREPAC"
            serie = str(config.get("serie_cfdi") or "").strip() or "CFDI"
            addenda_render = renderizar_addenda_factura(conn, conn_legacy, factura)
            item = {
                "modo_facturacion": resolucion.get("modo_facturacion"),
                "cliente_receptor_nombre": resolucion.get("cliente_receptor_nombre"),
                "cfdi_opciones_json": json.dumps(opciones_cfdi, ensure_ascii=False),
            }
            xml = _generar_cfdi_simulado_xml(
                factura, config, addenda_render, item, folio_candidato, serie,
                receptor_resuelto=resolucion.get("cliente_receptor"),
            )
            sellado = sellar_xml_cfdi(xml, config)
            xml_pac = sellado.get("xml") if sellado.get("ok") else xml
            paquete = preparar_paquete_pac(proveedor, config, xml_pac) if proveedor and proveedor != "SIMULADO" else {
                "ok": False,
                "proveedor": proveedor or "SIN PROVEEDOR",
                "errores": ["Proveedor en modo SIMULADO o no configurado; no se prepara paquete PAC real."],
                "advertencias": [],
                "xml_sha256": "",
                "xml_bytes": len((xml_pac or "").encode("utf-8")),
                "xml_base64_chars": 0,
                "request_preview": {},
            }
            etapas = [
                {
                    "etapa": "cola",
                    "ok": bool(cola) and str(cola.get("estatus") or "").upper() == "PENDIENTE",
                    "detalle": f"Estatus en cola: {cola.get('estatus') or 'NO EN COLA'}",
                    "errores": [] if cola else ["La factura no esta actualmente en cola."],
                },
                {
                    "etapa": "empresa",
                    "ok": bool(diagnostico.get("ok_pre_pac")),
                    "detalle": "Diagnostico de empresa.",
                    "errores": [x.get("mensaje") for x in diagnostico.get("faltantes") or []],
                    "advertencias": [x.get("mensaje") for x in diagnostico.get("advertencias") or []],
                },
                {
                    "etapa": "cfdi",
                    "ok": bool(validacion.get("ok")),
                    "detalle": "Validacion fiscal de la factura.",
                    "errores": [x.get("mensaje") for x in validacion.get("faltantes") or []],
                    "advertencias": [x.get("mensaje") for x in validacion.get("advertencias") or []],
                },
                {
                    "etapa": "sellado",
                    "ok": bool(sellado.get("ok")),
                    "detalle": "Cadena original y sello local.",
                    "errores": sellado.get("errores") or [],
                    "advertencias": sellado.get("advertencias") or [],
                },
                {
                    "etapa": "paquete_pac",
                    "ok": bool(paquete.get("ok")),
                    "detalle": "Solicitud seca para PAC; no se envio XML.",
                    "errores": paquete.get("errores") or [],
                    "advertencias": paquete.get("advertencias") or [],
                },
            ]
            listo_controlado = all(bool(x.get("ok")) for x in etapas)
            return {
                "factura": factura.get("factura"),
                "empresa": empresa,
                "cliente_receptor_numero": resolucion.get("cliente_receptor_numero"),
                "cliente_receptor_nombre": resolucion.get("cliente_receptor_nombre"),
                "proveedor": proveedor,
                "serie": serie,
                "folio_candidato": folio_candidato,
                "listo_controlado": listo_controlado,
                "siguiente_paso": "Puede procesarse con compuerta PAC controlada." if listo_controlado else "Corrige los pendientes antes de procesar.",
                "cola": cola,
                "diagnostico": diagnostico,
                "validacion_cfdi": validacion,
                "sellado": {
                    "ok": bool(sellado.get("ok")),
                    "cadena_original_length": len(sellado.get("cadena_original") or ""),
                    "sello_length": len(sellado.get("sello") or ""),
                    "xml_length": len(sellado.get("xml") or ""),
                    "errores": sellado.get("errores") or [],
                    "advertencias": sellado.get("advertencias") or [],
                },
                "paquete_pac": {
                    "ok": bool(paquete.get("ok")),
                    "proveedor": paquete.get("proveedor"),
                    "url": paquete.get("url"),
                    "xml_sha256": paquete.get("xml_sha256"),
                    "xml_bytes": paquete.get("xml_bytes"),
                    "xml_base64_chars": paquete.get("xml_base64_chars"),
                    "errores": paquete.get("errores") or [],
                    "advertencias": paquete.get("advertencias") or [],
                    "request_preview": paquete.get("request_preview") or {},
                },
                "etapas": etapas,
                **({"xml": xml_pac} if incluir_xml else {}),
            }
        finally:
            cur.close()
            if close_legacy:
                conn_legacy.close()


@router.post("/cola/reintentar/{folio}")
def reintentar_folio_timbrado(folio: str):
    with get_timbrado_connection() as conn:
        conn_legacy = getattr(conn, "_conn", None)
        close_legacy = False
        if conn_legacy is None:
            conn_legacy = get_legacy_connection()
            close_legacy = True
        cur = conn_legacy.cursor(dictionary=True)
        try:
            cur.execute("SELECT id FROM facturas WHERE factura = %s LIMIT 1", (folio,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Factura {folio} no encontrada")
            cola = conn.execute(
                "SELECT cfdi_opciones_json FROM timbrado_queue WHERE factura_id = ? LIMIT 1",
                (int(row["id"]),),
            ).fetchone()
            try:
                opciones_cfdi = _normalizar_opciones_cfdi(json.loads((dict(cola or {}).get("cfdi_opciones_json") or "{}")))
            except Exception:
                opciones_cfdi = {}
            resultado = sincronizar_factura_para_timbrado(
                conn, conn_legacy, int(row["id"]), motivo="REINTENTO", opciones_cfdi=opciones_cfdi,
            )
            return {"mensaje": f"Factura {folio} reenviada a la cola.", "detalle": resultado}
        finally:
            cur.close()
            if close_legacy:
                conn_legacy.close()


@router.post("/cola/enviar/{folio}")
def enviar_folio_timbrado(folio: str, datos: dict | None = None):
    opciones_cfdi = _normalizar_opciones_cfdi((datos or {}).get("opciones_cfdi") or {})
    with get_timbrado_connection() as conn:
        conn_legacy = getattr(conn, "_conn", None)
        close_legacy = False
        if conn_legacy is None:
            conn_legacy = get_legacy_connection()
            close_legacy = True
        cur = conn_legacy.cursor(dictionary=True)
        try:
            cur.execute("SELECT id FROM facturas WHERE factura = %s LIMIT 1", (folio,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Factura {folio} no encontrada")
            resultado = sincronizar_factura_para_timbrado(
                conn,
                conn_legacy,
                int(row["id"]),
                motivo="MANUAL",
                opciones_cfdi=opciones_cfdi,
            )
            _guardar_defaults_cfdi_cliente(
                conn,
                resultado.get("empresa") or "",
                resultado.get("cliente_receptor_numero") or "",
                resultado.get("cliente_receptor_nombre") or "",
                opciones_cfdi,
            )
            return {"mensaje": f"Factura {folio} enviada a la cola.", "detalle": resultado}
        finally:
            cur.close()
            if close_legacy:
                conn_legacy.close()


@router.post("/consolidar")
def consolidar_cola(facturas: list[str]):
    conn_legacy = get_legacy_connection()
    try:
        with get_timbrado_connection() as conn:
            res = consolidar_facturas_timbrado(conn, conn_legacy, facturas)
        if res.get("procesado"):
            return {"mensaje": f"Facturas {', '.join(facturas)} consolidadas. UUID: {res.get('uuid')}", "detalle": res}
        raise HTTPException(status_code=400, detail=res.get("detalle") or res.get("error") or "Error al consolidar")
    finally:
        conn_legacy.close()


@router.get("/cfdi-emitidos")
def ver_cfdi_emitidos(empresa: str | None = None, limit: int = 200):
    with get_timbrado_connection() as conn:
        _asegurar_columnas_cancelacion_cfdi(conn)
        _asegurar_tabla_cfdi_cobranza(conn)
        return listar_cfdi_emitidos(conn, empresa=empresa, limit=limit)


@router.post("/cobranza/{recibo_id}/validar")
def validar_cfdi_cobranza(recibo_id: int, datos: dict | None = None):
    datos = datos or {}
    legacy = get_legacy_connection()
    cur = legacy.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM cobranza_recibos WHERE id = %s LIMIT 1", (recibo_id,))
        recibo = cur.fetchone()
        if not recibo:
            raise HTTPException(status_code=404, detail="Recibo de cobranza no encontrado.")
        tipo_recibo = str(recibo.get("tipo_recibo") or "").upper()
        tipo = "PAGO" if tipo_recibo == "PAGO" else "NOTA_CREDITO" if tipo_recibo == "NOTA_CREDITO" else tipo_recibo
        cur.execute("SELECT * FROM cobranza_aplicaciones WHERE recibo_id = %s AND UPPER(origen_tipo) IN ('FACTURA', 'SALDO_INICIAL') ORDER BY id", (recibo_id,))
        aplicaciones = cur.fetchall() or []
        forma_default = "03" if tipo == "PAGO" else "15"
        forma_pago = str(datos.get("forma_pago") or recibo.get("forma_pago") or forma_default).strip().zfill(2)
        with get_timbrado_connection() as conn:
            _asegurar_tablas_timbrado(conn)
            _asegurar_tabla_cfdi_cobranza(conn)
            validacion = _validar_pre_cfdi_cobranza(
                conn,
                cur,
                recibo,
                aplicaciones,
                tipo,
                forma_pago,
                interno=bool(datos.get("interno")),
            )
            return {
                "recibo_id": recibo_id,
                "folio": recibo.get("folio"),
                **validacion,
            }
    finally:
        cur.close()
        legacy.close()


@router.post("/cobranza/{recibo_id}/prexml")
def generar_prexml_cobranza(recibo_id: int, datos: dict | None = None):
    datos = datos or {}
    legacy = get_legacy_connection()
    cur = legacy.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM cobranza_recibos WHERE id = %s LIMIT 1", (recibo_id,))
        recibo = cur.fetchone()
        if not recibo:
            raise HTTPException(status_code=404, detail="Recibo de cobranza no encontrado.")
        tipo_recibo = str(recibo.get("tipo_recibo") or "").upper()
        tipo = "PAGO" if tipo_recibo == "PAGO" else "NOTA_CREDITO" if tipo_recibo == "NOTA_CREDITO" else tipo_recibo
        cur.execute("SELECT * FROM cobranza_aplicaciones WHERE recibo_id = %s AND UPPER(origen_tipo) IN ('FACTURA', 'SALDO_INICIAL') ORDER BY id", (recibo_id,))
        aplicaciones = cur.fetchall() or []
        forma_default = "03" if tipo == "PAGO" else "15"
        forma_pago = str(datos.get("forma_pago") or recibo.get("forma_pago") or forma_default).strip().zfill(2)
        es_interno = bool(datos.get("interno"))
        with get_timbrado_connection() as conn:
            _asegurar_tablas_timbrado(conn)
            _asegurar_tabla_cfdi_cobranza(conn)
            validacion = _validar_pre_cfdi_cobranza(conn, cur, recibo, aplicaciones, tipo, forma_pago, interno=es_interno)
            if datos.get("bloquear_si_invalido", True) and not validacion.get("ok"):
                raise HTTPException(status_code=400, detail=validacion)
            empresa = _normalizar_empresa(recibo.get("empresa"))
            config = obtener_config_timbrado(conn, empresa)
            if not config:
                raise HTTPException(status_code=400, detail="La empresa no tiene configuración de timbrado.")
            facturas = _preparar_facturas_cobranza_cfdi(conn, cur, recibo, aplicaciones, recibo_id, es_interno=es_interno)
            serie_default = config.get("serie_complemento_pago") if tipo == "PAGO" else config.get("serie_nota_credito")
            serie = str(datos.get("serie") or serie_default or config.get("serie_cfdi") or ("PAG" if tipo == "PAGO" else "NC")).strip() or ("PAG" if tipo == "PAGO" else "NC")
            folio = str(datos.get("folio_cfdi") or _obtener_siguiente_folio(conn, empresa, tipo_documento=tipo) or f"PREPAC-{recibo_id}").strip() or f"PREPAC-{recibo_id}"
            cuentas = _cuentas_bancarias_cobranza(cur, recibo) if tipo == "PAGO" else {}
            xml = _xml_comprobante_cobranza(tipo, recibo, aplicaciones, facturas, config, serie, folio, forma_pago, interno=es_interno, cuentas_bancarias=cuentas)
            headers = {
                "X-CFDI-Validacion": "ok" if validacion.get("ok") else "faltantes",
                "X-CFDI-Faltantes": str(len(validacion.get("faltantes") or [])),
                "Content-Disposition": f'inline; filename="cobranza-{recibo_id}-prepac.xml"',
            }
            return Response(content=xml, media_type="application/xml", headers=headers)
    finally:
        cur.close()
        legacy.close()


@router.post("/cobranza/{recibo_id}/sellar")
def sellar_prexml_cobranza(recibo_id: int, datos: dict | None = None):
    datos = datos or {}
    respuesta = generar_prexml_cobranza(recibo_id, datos)
    xml = respuesta.body.decode("utf-8") if isinstance(respuesta.body, bytes) else str(respuesta.body)
    legacy = get_legacy_connection()
    cur = legacy.cursor(dictionary=True)
    try:
        cur.execute("SELECT empresa, tipo_recibo FROM cobranza_recibos WHERE id = %s LIMIT 1", (recibo_id,))
        recibo = cur.fetchone()
        if not recibo:
            raise HTTPException(status_code=404, detail="Recibo de cobranza no encontrado.")
        empresa = _normalizar_empresa(recibo.get("empresa"))
        tipo = "PAGO" if str(recibo.get("tipo_recibo") or "").upper() == "PAGO" else "NOTA_CREDITO"
        with get_timbrado_connection() as conn:
            config = obtener_config_timbrado(conn, empresa)
        sellado = sellar_xml_cfdi(xml, config)
        proveedor = str((config or {}).get("proveedor") or "").strip().upper()
        faltantes_pac = []
        if not proveedor:
            faltantes_pac.append("Falta proveedor PAC.")
        elif proveedor == "SIMULADO":
            faltantes_pac.append("Proveedor en modo SIMULADO; selecciona el PAC real para enviar.")
        for campo, etiqueta in (
            ("pac_usuario", "usuario PAC"),
            ("pac_password", "password PAC"),
        ):
            if proveedor and proveedor != "SIMULADO" and not str((config or {}).get(campo) or "").strip():
                faltantes_pac.append(f"Falta {etiqueta}.")
        if datos.get("descargar_xml"):
            if not sellado.get("ok"):
                raise HTTPException(status_code=400, detail={"mensaje": "No se pudo sellar el XML.", "errores": sellado.get("errores") or []})
            return Response(
                content=sellado["xml"],
                media_type="application/xml",
                headers={"Content-Disposition": f'inline; filename="cobranza-{recibo_id}-prepac-sellado.xml"'},
            )
        return {
            "recibo_id": recibo_id,
            "tipo_documento": tipo,
            "empresa": empresa,
            "proveedor": proveedor,
            "ok": bool(sellado.get("ok")),
            "listo_pac": bool(sellado.get("ok")) and not faltantes_pac,
            "faltantes_pac": faltantes_pac,
            "errores": sellado.get("errores") or [],
            "advertencias": sellado.get("advertencias") or [],
            "cadena_original_length": len(sellado.get("cadena_original") or ""),
            "sello_length": len(sellado.get("sello") or ""),
            "xml_length": len(sellado.get("xml") or ""),
            "incluir_xml": bool(datos.get("incluir_xml")),
            **({"xml": sellado.get("xml")} if datos.get("incluir_xml") else {}),
        }
    finally:
        cur.close()
        legacy.close()


@router.post("/cobranza/{recibo_id}/paquete-pac")
def preparar_paquete_pac_cobranza(recibo_id: int, datos: dict | None = None):
    datos = datos or {}
    respuesta = sellar_prexml_cobranza(recibo_id, {**datos, "incluir_xml": True})
    if not isinstance(respuesta, dict):
        raise HTTPException(status_code=400, detail="No se pudo preparar XML sellado para paquete PAC.")
    if not respuesta.get("ok"):
        return {
            "recibo_id": recibo_id,
            "tipo_documento": respuesta.get("tipo_documento"),
            "empresa": respuesta.get("empresa"),
            "ok": False,
            "xml_sellado": False,
            "errores": respuesta.get("errores") or ["No se pudo sellar el XML."],
            "advertencias": respuesta.get("advertencias") or [],
        }
    empresa = respuesta.get("empresa")
    with get_timbrado_connection() as conn:
        cfg = obtener_config_timbrado(conn, empresa)
        paquete = preparar_paquete_pac(cfg.get("proveedor"), cfg, respuesta.get("xml") or "")
        folio_doc = f"{respuesta.get('tipo_documento') or 'COB'}-{recibo_id}"
        registrar_intento_pac(
            conn,
            {"id": recibo_id, "factura": folio_doc, "empresa": empresa},
            paquete.get("proveedor"),
            "COBRANZA_PAQUETE_OK" if paquete.get("ok") else "COBRANZA_PAQUETE_ERROR",
            "Paquete PAC de cobranza preparado en modo seco." if paquete.get("ok") else "Paquete PAC de cobranza incompleto en modo seco.",
            folio_candidato=str(datos.get("folio_cfdi") or f"PREPAC-{recibo_id}"),
            response={
                "recibo_id": recibo_id,
                "tipo_documento": respuesta.get("tipo_documento"),
                "xml_sha256": paquete.get("xml_sha256"),
                "xml_bytes": paquete.get("xml_bytes"),
                "xml_base64_chars": paquete.get("xml_base64_chars"),
                "errores": paquete.get("errores") or [],
                "advertencias": paquete.get("advertencias") or [],
                "request_preview": paquete.get("request_preview") or {},
            },
        )
    return {
        "recibo_id": recibo_id,
        "tipo_documento": respuesta.get("tipo_documento"),
        "empresa": empresa,
        "xml_sellado": True,
        **paquete,
    }


@router.post("/cobranza/{recibo_id}/simular")
def simular_cfdi_cobranza(recibo_id: int, datos: dict | None = None):
    datos = datos or {}
    payload = dict(datos)
    payload["bloquear_si_invalido"] = False
    validacion = validar_cfdi_cobranza(recibo_id, payload)
    resultado = {
        "recibo_id": recibo_id,
        "emitible": bool(validacion.get("ok")),
        "validacion": validacion,
        "persistido": False,
        "consume_folio": False,
    }
    try:
        respuesta = generar_prexml_cobranza(recibo_id, payload)
        xml = respuesta.body.decode("utf-8") if isinstance(respuesta.body, bytes) else str(respuesta.body)
        resultado["xml_generado"] = True
        resultado["resumen_xml"] = _resumen_xml_cfdi(xml)
        if datos.get("incluir_xml"):
            resultado["xml"] = xml
    except HTTPException as exc:
        resultado["xml_generado"] = False
        resultado["error_xml"] = exc.detail
    return resultado


def _guardar_prexml_cobranza_pac(config: dict, empresa: str, folio_documento: str, contenido: str) -> str:
    output_dir = str((config or {}).get("output_dir") or ruta_empresa_fiscal(empresa))
    anio = str(datetime.now().year)
    base_dir = os.path.join(output_dir, anio, "prepac")
    os.makedirs(base_dir, exist_ok=True)
    nombre = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(folio_documento or "cobranza"))
    path = os.path.join(base_dir, f"{nombre}-prepac.xml")
    with open(path, "w", encoding="utf-8") as archivo:
        archivo.write(contenido)
    return path


@router.post("/cobranza/{recibo_id}/timbrar")
def timbrar_documento_cobranza(recibo_id: int, datos: dict | None = None):
    """Emite el CFDI fiscal del recibo de cobranza: nota de crédito o REP 2.0."""
    datos = datos or {}
    legacy = get_legacy_connection()
    cur = legacy.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM cobranza_recibos WHERE id = %s LIMIT 1", (recibo_id,))
        recibo = cur.fetchone()
        if not recibo:
            raise HTTPException(status_code=404, detail="Recibo de cobranza no encontrado.")
        if str(recibo.get("estatus") or "").upper() != "ACTIVO":
            raise HTTPException(status_code=400, detail="Solo se pueden timbrar movimientos activos.")
        tipo_recibo = str(recibo.get("tipo_recibo") or "").upper()
        if tipo_recibo not in ("PAGO", "NOTA_CREDITO"):
            raise HTTPException(status_code=400, detail="Solo los pagos y notas de crédito generan este CFDI.")
        cur.execute("SELECT * FROM cobranza_aplicaciones WHERE recibo_id = %s AND UPPER(origen_tipo) IN ('FACTURA', 'SALDO_INICIAL') ORDER BY id", (recibo_id,))
        aplicaciones = cur.fetchall() or []
        if not aplicaciones:
            raise HTTPException(status_code=400, detail="El movimiento debe tener al menos una factura fiscal relacionada.")
        with get_timbrado_connection() as conn:
            _asegurar_tablas_timbrado(conn)
            _asegurar_tabla_cfdi_cobranza(conn)
            tipo = "PAGO" if tipo_recibo == "PAGO" else "NOTA_CREDITO"
            existente = conn.execute("SELECT * FROM cfdi_cobranza_emitidos WHERE recibo_id = ? AND tipo_documento = ? LIMIT 1", (recibo_id, tipo)).fetchone()
            if existente:
                return {"mensaje": "El movimiento ya tiene CFDI emitido.", "detalle": dict(existente), "ya_emitido": True}
            empresa = _normalizar_empresa(recibo.get("empresa"))
            config = obtener_config_timbrado(conn, empresa)
            if not config or not config.get("timbrado_activo"):
                raise HTTPException(status_code=400, detail="La empresa no tiene timbrado activo.")
            proveedor = str(config.get("proveedor") or "").strip().upper()
            es_interno = datos.get("interno") or False
            facturas = _preparar_facturas_cobranza_cfdi(conn, cur, recibo, aplicaciones, recibo_id, es_interno=es_interno)
            forma_default = "03" if tipo == "PAGO" else "15"
            forma_pago = str(datos.get("forma_pago") or recibo.get("forma_pago") or forma_default).strip().zfill(2)
            if not es_interno and tipo == "PAGO" and forma_pago in ("", "99"):
                raise HTTPException(status_code=400, detail="El complemento de pago requiere una FormaDePagoP SAT distinta de 99.")
            if es_interno:
                emp_short = re.sub(r"[^A-Za-z0-9]", "", empresa)[:3].upper()
                serie = f"INT-{emp_short}"
                folio = str(recibo.get("folio") or recibo_id)
                folio_candidato = folio
                seq = 1
                while conn.execute("SELECT 1 FROM cfdi_cobranza_emitidos WHERE folio_cfdi = ? LIMIT 1", (folio_candidato,)).fetchone():
                    seq += 1
                    folio_candidato = f"{folio}-I{seq}"
                folio = folio_candidato
                estatus = "INTERNO"
                uuid_cfdi = ""
            else:
                serie_default = config.get("serie_complemento_pago") if tipo == "PAGO" else config.get("serie_nota_credito")
                serie = str(serie_default or config.get("serie_cfdi") or ("PAG" if tipo == "PAGO" else "NC")).strip() or ("PAG" if tipo == "PAGO" else "NC")
                folio = _obtener_siguiente_folio(conn, empresa, tipo_documento=tipo)
                estatus = "TIMBRADA"
                uuid_cfdi = ""
            folio_documento = f"{tipo[:3]}-{recibo.get('folio') or recibo_id}"
            cuentas = _cuentas_bancarias_cobranza(cur, recibo) if tipo == "PAGO" else {}
            contenido = _xml_comprobante_cobranza(tipo, recibo, aplicaciones, facturas, config, serie, folio, forma_pago, interno=es_interno, cuentas_bancarias=cuentas)
            pac_response = None
            if not es_interno and proveedor != "SIMULADO":
                preflight = validar_preflight_pac(config)
                if not preflight.get("ok"):
                    registrar_intento_pac(
                        conn,
                        {"id": recibo_id, "factura_id": recibo_id, "factura": folio_documento, "empresa": empresa},
                        proveedor,
                        "COBRANZA_PREFLIGHT_ERROR",
                        "Preflight PAC fallido: " + "; ".join(preflight.get("errores") or []),
                        folio_candidato=folio,
                        response={"recibo_id": recibo_id, "tipo_documento": tipo, "preflight": preflight},
                    )
                    raise HTTPException(status_code=400, detail={"mensaje": "Preflight PAC fallido.", "errores": preflight.get("errores") or [], "advertencias": preflight.get("advertencias") or []})
                sellado = sellar_xml_cfdi(contenido, config)
                contenido_pac = sellado.get("xml") if sellado.get("ok") else contenido
                try:
                    resultado_pac = timbrar_xml_pac(proveedor, config, contenido_pac)
                except (PacNoIntegradoError, PacTimbradoError) as exc:
                    prexml_path = _guardar_prexml_cobranza_pac(config, empresa, folio_documento, contenido_pac)
                    mensaje_sellado = "XML sellado." if sellado.get("ok") else "XML sin sello: " + "; ".join(sellado.get("errores") or ["sellado no disponible"])
                    registrar_intento_pac(
                        conn,
                        {"id": recibo_id, "factura_id": recibo_id, "factura": folio_documento, "empresa": empresa},
                        proveedor,
                        "COBRANZA_BLOQUEADO_PAC",
                        f"{exc} {mensaje_sellado} XML pre-PAC generado sin consumir folio.",
                        folio_candidato=folio,
                        xml_path=prexml_path,
                        response={
                            "recibo_id": recibo_id,
                            "tipo_documento": tipo,
                            "sellado": {
                                "ok": bool(sellado.get("ok")),
                                "errores": sellado.get("errores") or [],
                                "advertencias": sellado.get("advertencias") or [],
                            },
                        },
                    )
                    return {
                        "procesado": False,
                        "bloqueo_pac": True,
                        "mensaje": "El documento no fue timbrado por el PAC.",
                        "recibo_id": recibo_id,
                        "tipo_documento": tipo,
                        "modo": proveedor,
                        "serie": serie,
                        "folio_candidato": folio,
                        "prexml_path": prexml_path,
                        "detalle": f"No se pudo timbrar en PAC: {exc}",
                        "folio_consumido": False,
                    }
                contenido = resultado_pac.xml_timbrado
                uuid_cfdi = resultado_pac.uuid
                pac_response = resultado_pac.raw_response
            elif not es_interno:
                uuid_cfdi = str(uuid.uuid4()).upper()
            carpeta = os.path.join(str(config.get("output_dir") or ruta_empresa_fiscal(empresa)), str(datetime.now().year), "xml")
            os.makedirs(carpeta, exist_ok=True)
            xml_path = os.path.join(carpeta, f"{folio_documento}.xml")
            with open(xml_path, "w", encoding="utf-8") as archivo:
                archivo.write(contenido)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("""
                INSERT INTO cfdi_cobranza_emitidos
                (recibo_id, tipo_documento, factura, empresa, cliente_receptor_numero, cliente_receptor_nombre, serie, folio_cfdi, uuid, estatus_cfdi, xml_path, fecha_timbrado, forma_pago)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (recibo_id, tipo, folio_documento, empresa, recibo.get("numero_cliente") or "", facturas[0].get("cliente_receptor_nombre") or "", serie, folio, uuid_cfdi, estatus, xml_path, now, forma_pago))
            if not es_interno:
                _avanzar_folio_empresa(conn, empresa, folio, tipo_documento=tipo)
            registrar_intento_pac(
                conn,
                {"id": recibo_id, "factura_id": recibo_id, "factura": folio_documento, "empresa": empresa},
                proveedor or ("INTERNO" if es_interno else "SIMULADO"),
                "COBRANZA_TIMBRADA" if not es_interno else "COBRANZA_INTERNA",
                f"{tipo} {'timbrado por PAC real' if pac_response else 'generado correctamente'}.",
                folio_candidato=folio,
                uuid_val=uuid_cfdi,
                xml_path=xml_path,
                response={"recibo_id": recibo_id, "tipo_documento": tipo, "pac": pac_response or {}, "interno": bool(es_interno)},
            )
            return {"mensaje": f"{('Complemento de pago REP 2.0' if tipo == 'PAGO' else 'Nota de crédito CFDI')} {'emitido como interno' if es_interno else 'timbrado'}.",
                    "detalle": {"recibo_id": recibo_id, "tipo_documento": tipo, "folio_cfdi": folio, "serie": serie, "uuid": uuid_cfdi, "xml_path": xml_path, "estatus": estatus, "modo": proveedor if pac_response else ("INTERNO" if es_interno else "SIMULADO")}}
    finally:
        cur.close()
        legacy.close()


def _buscar_cfdi_emitido_por_folio(conn, folio: str):
    folio = str(folio or "").strip()
    if not folio:
        raise HTTPException(status_code=400, detail="Falta el folio de la factura.")
    _asegurar_tablas_timbrado(conn)
    _asegurar_columnas_cancelacion_cfdi(conn)
    _asegurar_tabla_cfdi_cobranza(conn)
    row = conn.execute(
        """
        SELECT * FROM cfdi_emitidos
        WHERE factura = ? OR folio_cfdi = ?
        ORDER BY fecha_timbrado DESC
        LIMIT 1
        """,
        (folio, folio),
    ).fetchone()
    # La interfaz muestra normalmente la serie pegada al folio (por ejemplo
    # FE10), mientras que la tabla los guarda por separado. Aceptamos ambas
    # formas para descargas, cancelacion y reenvio por correo.
    if not row:
        coincidencia = re.match(r"^(.+?)[\s\-_]?(\d+)$", folio)
        if coincidencia:
            serie_busqueda = coincidencia.group(1).strip()
            folio_busqueda = coincidencia.group(2)
            alternativas = [folio_busqueda]
            sin_ceros = folio_busqueda.lstrip("0") or "0"
            if sin_ceros not in alternativas:
                alternativas.append(sin_ceros)
            marcadores = ",".join(["?"] * len(alternativas))
            row = conn.execute(
                f"""
                SELECT * FROM cfdi_emitidos
                WHERE UPPER(TRIM(COALESCE(serie, ''))) = UPPER(TRIM(?))
                  AND TRIM(CAST(folio_cfdi AS CHAR)) IN ({marcadores})
                ORDER BY fecha_timbrado DESC
                LIMIT 1
                """,
                tuple([serie_busqueda] + alternativas),
            ).fetchone()
    if not row:
        row = conn.execute(
            """
            SELECT * FROM cfdi_cobranza_emitidos
            WHERE factura = ? OR folio_cfdi = ?
            ORDER BY fecha_timbrado DESC LIMIT 1
            """,
            (folio, folio),
        ).fetchone()
        if not row:
            coincidencia = re.match(r"^(.+?)[\s\-_]?(\d+)$", folio)
            if coincidencia:
                serie_busqueda = coincidencia.group(1).strip()
                folio_busqueda = coincidencia.group(2)
                alternativas = [folio_busqueda]
                sin_ceros = folio_busqueda.lstrip("0") or "0"
                if sin_ceros not in alternativas:
                    alternativas.append(sin_ceros)
                marcadores = ",".join(["?"] * len(alternativas))
                row = conn.execute(
                    f"""
                    SELECT * FROM cfdi_cobranza_emitidos
                    WHERE UPPER(TRIM(COALESCE(serie, ''))) = UPPER(TRIM(?))
                      AND TRIM(CAST(folio_cfdi AS CHAR)) IN ({marcadores})
                    ORDER BY fecha_timbrado DESC
                    LIMIT 1
                    """,
                    tuple([serie_busqueda] + alternativas),
                ).fetchone()
        if row:
            result = dict(row)
            result["origen_cfdi"] = "COBRANZA"
            return result
    if not row:
        raise HTTPException(status_code=404, detail=f"No se encontro CFDI emitido para {folio}.")
    return dict(row)


def _descargar_archivo_cfdi(row: dict, campo: str, extension: str, media_type: str):
    if campo == "xml_path":
        row = dict(row)
        row["xml_path"] = _resolver_xml_cfdi_en_instalacion(row)
    path = str(row.get(campo) or "").strip()
    if not path and campo == "pdf_path":
        xml_path = str(row.get("xml_path") or "").strip()
        if xml_path:
            candidato = os.path.splitext(xml_path)[0] + ".pdf"
            if os.path.exists(candidato):
                path = candidato
    if not path:
        raise HTTPException(status_code=404, detail=f"El CFDI {row.get('factura') or row.get('folio_cfdi')} no tiene archivo {extension.upper()} registrado.")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"No se encontro el archivo en servidor: {path}")
    filename = f"{row.get('folio_cfdi') or row.get('factura') or 'cfdi'}.{extension}"
    disposition = "inline" if extension == "pdf" else "attachment"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


def _resolver_xml_cfdi_en_instalacion(row: dict) -> str:
    """Recupera XML cuando la BD conserva una ruta de otra instalación.

    El historial de CFDI es compartido, pero cada servidor puede tener una
    carpeta de almacenamiento distinta. La búsqueda se limita al directorio
    fiscal de la empresa y al nombre esperado del propio CFDI.
    """
    registrado = str(row.get("xml_path") or "").strip()
    if registrado and os.path.isfile(registrado):
        return registrado
    empresa = str(row.get("empresa") or "SIN_EMPRESA").strip()
    base = Path(ruta_empresa_fiscal(empresa))
    nombre = Path(registrado).name if registrado else ""
    serie_folio = _folio_serie(row) or str(row.get("folio_cfdi") or "").strip()
    factura = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(row.get("factura") or "")).strip("._")
    if not nombre and factura:
        nombre = f"{factura}-{serie_folio}.xml"
    candidatos = list(base.glob(f"*/xml/{nombre}")) if nombre else []
    if not candidatos and factura:
        candidatos = list(base.glob(f"*/xml/{factura}-*.xml"))
    if len(candidatos) == 1 and candidatos[0].is_file():
        return str(candidatos[0])
    return registrado


def _datos_consulta_sat_desde_xml(row: dict) -> dict:
    xml_path = str(row.get("xml_path") or "").strip()
    datos = {"rfc_receptor": "", "total": ""}
    if xml_path and os.path.exists(xml_path):
        try:
            root = ET.parse(xml_path).getroot()
            ns = {"cfdi": "http://www.sat.gob.mx/cfd/4"}
            receptor = root.find("cfdi:Receptor", ns)
            datos["total"] = str(root.attrib.get("Total") or "").strip()
            if receptor is not None:
                datos["rfc_receptor"] = str(receptor.attrib.get("Rfc") or "").strip()
        except Exception:
            pass
    # Los históricos restaurados pueden no conservar el XML local. La cola
    # guarda el receptor y el importe usados al timbrar; es suficiente para
    # consultar SAT sin inventar ni reemplazar el XML fiscal original.
    if (not datos["rfc_receptor"] or not datos["total"]) and row.get("uuid"):
        try:
            with get_timbrado_connection() as conn:
                q = conn.execute(
                    "SELECT addenda_payload_json FROM timbrado_queue WHERE uuid = ? ORDER BY id DESC LIMIT 1",
                    (str(row.get("uuid") or "").strip(),),
                ).fetchone()
            payload = json.loads(str(dict(q).get("addenda_payload_json") or "{}")) if q else {}
            receptor = payload.get("receptor") or {}
            configuracion = payload.get("configuracion") or {}
            datos["rfc_receptor"] = datos["rfc_receptor"] or str(receptor.get("rfc") or "").strip()
            datos["total"] = datos["total"] or str(
                configuracion.get("IMPORTE") or configuracion.get("TOTAL") or payload.get("total") or ""
            ).strip()
        except Exception:
            pass
    return datos


def _obtener_correo_documento(conn, tipo_documento: str, empresa: str = ""):
    _asegurar_tabla_correo_documentos(conn)
    empresa_norm = _normalizar_empresa(empresa)
    if empresa_norm:
        row = conn.execute(
            """
            SELECT * FROM soporte_correo_documentos
            WHERE empresa = ? AND tipo_documento = ? AND activo = 1
            LIMIT 1
            """,
            (empresa_norm, str(tipo_documento or "").strip().lower()),
        ).fetchone()
        if row:
            return dict(row)
    row = conn.execute(
        "SELECT * FROM soporte_correo_documentos WHERE empresa = '' AND tipo_documento = ? AND activo = 1 LIMIT 1",
        (str(tipo_documento or "").strip().lower(),),
    ).fetchone()
    return dict(row) if row else None


def _generar_pdf_cfdi_bytes(row: dict) -> bytes:
    from app.routers.timbrado_pdf import generar_cfdi_pdf
    import xml.etree.ElementTree as ET
    row = dict(row)
    xml_path = _resolver_xml_cfdi_en_instalacion(row)
    row["xml_path"] = xml_path
    if not xml_path or not os.path.exists(xml_path):
        raise HTTPException(status_code=404, detail="No se encontro el XML para generar el PDF.")
    tree = ET.parse(xml_path)
    root = tree.getroot()
    logo_archivo = ""
    try:
        with get_timbrado_connection() as conn:
            logo_archivo = str((obtener_config_timbrado(conn, row.get("empresa") or "") or {}).get("logo_archivo") or "")
    except Exception:
        pass
    buf = generar_cfdi_pdf(root, db_row=row, logo_archivo=logo_archivo)
    return buf.getvalue()


def _enviar_correo_smtp(cfg: dict, destinatario: str, asunto: str, cuerpo: str, adjuntos: list[tuple[str, bytes, str]], cuerpo_html: str = ""):
    host = str(cfg.get("smtp_host") or "").strip()
    remitente = str(cfg.get("correo_remitente") or "").strip()
    if not host or not remitente:
        raise HTTPException(status_code=400, detail="La cuenta de correo para este documento no tiene SMTP o remitente configurado.")
    msg = EmailMessage()
    msg["From"] = f"{cfg.get('nombre_remitente') or remitente} <{remitente}>"
    msg["To"] = destinatario
    msg["Subject"] = asunto
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=remitente.split("@")[-1] if "@" in remitente else None)
    msg["Reply-To"] = remitente
    msg.set_content(cuerpo)
    if str(cuerpo_html or "").strip():
        msg.add_alternative(cuerpo_html, subtype="html")
    for filename, content, mime_type in adjuntos:
        maintype, subtype = mime_type.split("/", 1)
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    port = int(cfg.get("smtp_port") or 587)
    username = str(cfg.get("smtp_usuario") or remitente).strip()
    password = str(cfg.get("smtp_password") or "").strip()
    use_ssl = bool(cfg.get("smtp_ssl"))
    use_starttls = bool(cfg.get("smtp_starttls"))
    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_cls(host, port, timeout=30) as smtp:
        if use_starttls and not use_ssl:
            smtp.starttls()
            smtp.ehlo()
        if username and password:
            # smtplib.login codifica las credenciales con ASCII. Algunos
            # proveedores permiten contraseñas UTF-8; envíalas en AUTH PLAIN
            # ya codificadas en base64 para no fallar por caracteres como ¿.
            try:
                username.encode("ascii")
                password.encode("ascii")
                smtp.login(username, password)
            except UnicodeEncodeError:
                smtp.ehlo_or_helo_if_needed()
                payload = ("\x00" + username + "\x00" + password).encode("utf-8")
                auth = base64.b64encode(payload).decode("ascii")
                code, response = smtp.docmd("AUTH", "PLAIN " + auth)
                if int(code) not in {235, 503}:
                    detail = response.decode("utf-8", errors="replace") if isinstance(response, bytes) else str(response or "")
                    raise smtplib.SMTPAuthenticationError(code, detail.encode("utf-8", errors="replace"))
        refused = smtp.send_message(msg, from_addr=remitente, to_addrs=[destinatario])
        if refused:
            raise RuntimeError(f"SMTP rechazo destinatarios: {refused}")
    return {
        "message_id": msg["Message-ID"],
        "smtp_host": host,
        "smtp_port": port,
        "smtp_ssl": use_ssl,
        "smtp_starttls": use_starttls,
        "remitente": remitente,
        "destinatario": destinatario,
    }


def _render_template_correo(template: str, variables: dict) -> str:
    texto = str(template or "")
    for key, value in (variables or {}).items():
        texto = texto.replace("{" + str(key) + "}", str(value or ""))
    return texto


def _variables_correo_cfdi(row: dict, folio_serie: str) -> dict:
    empresa = str(row.get("empresa") or "").strip()
    return {
        "folio": row.get("factura") or "",
        "folio_cfdi": row.get("folio_cfdi") or "",
        "serie": row.get("serie") or "",
        "folio_serie": folio_serie,
        "uuid": row.get("uuid") or "",
        "empresa": empresa,
        "empresa_nombre": empresa,
        "cliente_numero": row.get("cliente_receptor_numero") or "",
        "cliente_nombre": row.get("cliente_receptor_nombre") or "",
        "tipo_documento": "Complemento de pago" if row.get("origen_cfdi") == "COBRANZA" and str(row.get("tipo_documento") or "").upper() == "PAGO" else "Factura",
    }


def _enriquecer_variables_correo_desde_xml(variables: dict, xml_bytes: bytes) -> dict:
    """Obtiene del CFDI los datos que deben verse en el aviso de correo."""
    datos = dict(variables or {})
    try:
        root = ET.fromstring(xml_bytes)
        ns = {"cfdi": "http://www.sat.gob.mx/cfd/4"}
        receptor = root.find("cfdi:Receptor", ns)
        fecha_cfdi = str(root.attrib.get("Fecha") or "").replace("T", " ")
        try:
            datos["fecha_emision"] = datetime.fromisoformat(fecha_cfdi).strftime("%d/%m/%Y")
        except Exception:
            datos["fecha_emision"] = fecha_cfdi
        datos["monto_total"] = str(root.attrib.get("Total") or "0")
        if receptor is not None:
            datos["cliente_nombre"] = receptor.attrib.get("Nombre") or datos.get("cliente_nombre") or ""
            datos["rfc_receptor"] = receptor.attrib.get("Rfc") or ""
    except Exception:
        datos.setdefault("fecha_emision", "")
        datos.setdefault("monto_total", "0")
        datos.setdefault("rfc_receptor", "")
    return datos


def _monto_correo_html(value) -> str:
    try:
        return f"{Decimal(str(value or 0)):,.2f}"
    except Exception:
        return str(value or "0.00")


def _cuerpo_html_correo_cfdi(variables: dict, emisor: str) -> str:
    """Plantilla visual de factura fiscal compatible con clientes de correo."""
    v = variables or {}
    nombre = html_escape(str(v.get("cliente_nombre") or "Cliente"))
    rfc = html_escape(str(v.get("rfc_receptor") or ""))
    destinatario = f"{nombre}{' &nbsp; ( ' + rfc + ' )' if rfc else ''}"
    empresa = html_escape(str(emisor or v.get("empresa_nombre") or v.get("empresa") or ""))
    folio = html_escape(str(v.get("folio_serie") or ""))
    fecha = html_escape(str(v.get("fecha_emision") or ""))
    monto = html_escape(_monto_correo_html(v.get("monto_total")))
    tipo_documento = html_escape(str(v.get("tipo_documento") or "Factura"))
    return f"""<!doctype html>
<html><body style=\"margin:0;padding:0;background:#ffffff;font-family:Arial,Helvetica,sans-serif;color:#444444;\">
  <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" border=\"0\"><tr><td style=\"padding:28px 14px;\">
    <table role=\"presentation\" width=\"640\" cellspacing=\"0\" cellpadding=\"0\" border=\"0\" style=\"max-width:640px;width:100%;margin:0 auto;\">
      <tr><td style=\"font-size:16px;color:#1f1f1f;padding:0 0 24px 0;\">Para:&nbsp;&nbsp; {destinatario}</td></tr>
      <tr><td style=\"font-size:15px;line-height:1.55;padding-bottom:20px;\">Estimado Cliente,</td></tr>
      <tr><td style=\"font-size:13px;line-height:1.6;padding-bottom:20px;\">{empresa} emitió para Usted un(os) documento(s) de tipo <strong>{tipo_documento}</strong> con las siguientes características:</td></tr>
      <tr><td style=\"padding:0 34px 22px 34px;\">
        <table role=\"presentation\" cellspacing=\"0\" cellpadding=\"0\" border=\"0\" style=\"font-size:16px;line-height:1.5;width:100%;\">
          <tr><td style=\"font-weight:bold;width:58%;\">Serie y Folio:</td><td>{folio}</td></tr>
          <tr><td style=\"font-weight:bold;\">Fecha de Emisión:</td><td>{fecha}</td></tr>
          <tr><td style=\"font-weight:bold;\">Monto total:</td><td>{monto}</td></tr>
        </table>
      </td></tr>
      <tr><td style=\"font-size:14px;line-height:1.55;padding-bottom:24px;\">Consulte los datos adjuntos, por favor.</td></tr>
      <tr><td style=\"border-top:1px solid #e5e5e5;padding-top:12px;font-size:10px;color:#666666;\">Este correo fue emitido desde el sistema de Facturación Web. Derechos reservados.</td></tr>
    </table>
  </td></tr></table>
</body></html>"""


def _safe_package_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._") or "cfdi"


def _zip_add_file(zf: zipfile.ZipFile, path: str, arcname: str):
    path = str(path or "").strip()
    if path and os.path.exists(path):
        zf.write(path, arcname)


def _asegurar_columnas_cancelacion_cfdi(conn):
    try:
        rows = conn.execute("SHOW COLUMNS FROM cfdi_emitidos").fetchall()
        columnas_info = {str(dict(row).get("Field") or "").lower(): dict(row) for row in rows}
        columnas = set(columnas_info)
        if "cancelacion_motivo" not in columnas:
            conn.execute("ALTER TABLE cfdi_emitidos ADD COLUMN cancelacion_motivo TEXT NULL")
        else:
            tipo_motivo = str(columnas_info["cancelacion_motivo"].get("Type") or "").lower()
            if "text" not in tipo_motivo:
                conn.execute("ALTER TABLE cfdi_emitidos MODIFY COLUMN cancelacion_motivo TEXT NULL")
        if "cancelacion_motivo_codigo" not in columnas:
            conn.execute("ALTER TABLE cfdi_emitidos ADD COLUMN cancelacion_motivo_codigo VARCHAR(2) NULL")
        if "uuid_sustitucion" not in columnas:
            conn.execute("ALTER TABLE cfdi_emitidos ADD COLUMN uuid_sustitucion VARCHAR(36) NULL")
        if "fecha_cancelacion" not in columnas:
            conn.execute("ALTER TABLE cfdi_emitidos ADD COLUMN fecha_cancelacion DATETIME NULL")
        if "acuse_recepcion_path" not in columnas:
            conn.execute("ALTER TABLE cfdi_emitidos ADD COLUMN acuse_recepcion_path TEXT NULL")
        if "acuse_cancelacion_path" not in columnas:
            conn.execute("ALTER TABLE cfdi_emitidos ADD COLUMN acuse_cancelacion_path TEXT NULL")
    except Exception:
        try:
            rows = conn.execute("PRAGMA table_info(cfdi_emitidos)").fetchall()
            columnas = {str(dict(row).get("name") or "").lower() for row in rows}
            if "cancelacion_motivo" not in columnas:
                conn.execute("ALTER TABLE cfdi_emitidos ADD COLUMN cancelacion_motivo TEXT")
            if "cancelacion_motivo_codigo" not in columnas:
                conn.execute("ALTER TABLE cfdi_emitidos ADD COLUMN cancelacion_motivo_codigo TEXT")
            if "uuid_sustitucion" not in columnas:
                conn.execute("ALTER TABLE cfdi_emitidos ADD COLUMN uuid_sustitucion TEXT")
            if "fecha_cancelacion" not in columnas:
                conn.execute("ALTER TABLE cfdi_emitidos ADD COLUMN fecha_cancelacion TEXT")
            if "acuse_recepcion_path" not in columnas:
                conn.execute("ALTER TABLE cfdi_emitidos ADD COLUMN acuse_recepcion_path TEXT")
            if "acuse_cancelacion_path" not in columnas:
                conn.execute("ALTER TABLE cfdi_emitidos ADD COLUMN acuse_cancelacion_path TEXT")
        except Exception:
            pass


def _guardar_acuse_archivo(row: dict, acuse_xml: str, tipo: str) -> str:
    tipo = "recepcion" if str(tipo or "").lower().startswith("r") else "cancelacion"
    xml_path = str(row.get("xml_path") or "").strip()
    carpeta = f"acuse_{tipo}"
    if xml_path:
        base_dir = os.path.join(os.path.dirname(xml_path), "..", carpeta)
    else:
        empresa = _normalizar_empresa(row.get("empresa"))
        base_dir = os.path.join(str(ruta_empresa_fiscal(empresa)), str(datetime.now().year), carpeta)
    os.makedirs(base_dir, exist_ok=True)
    nombre = re.sub(r"[^A-Za-z0-9_.-]+", "_", _folio_serie(row) or str(row.get("factura") or row.get("folio_cfdi") or "cfdi"))
    path = os.path.abspath(os.path.join(base_dir, f"{nombre}-acuse-{tipo}.xml"))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(acuse_xml or ""))
    return path


def _guardar_acuse_cancelacion_archivo(row: dict, acuse_xml: str) -> str:
    return _guardar_acuse_archivo(row, acuse_xml, "cancelacion")


def _guardar_acuse_recepcion_archivo(row: dict, acuse_xml: str) -> str:
    return _guardar_acuse_archivo(row, acuse_xml, "recepcion")


def _actualizar_acuse_path(conn, row: dict, path: str, tipo: str):
    columna = "acuse_recepcion_path" if str(tipo or "").lower().startswith("r") else "acuse_cancelacion_path"
    if row.get("origen_cfdi") == "COBRANZA":
        _asegurar_tabla_cfdi_cobranza(conn)
        conn.execute(f"UPDATE cfdi_cobranza_emitidos SET {columna} = ? WHERE id = ?", (path, row["id"]))
    else:
        _asegurar_columnas_cancelacion_cfdi(conn)
        conn.execute(f"UPDATE cfdi_emitidos SET {columna} = ? WHERE id = ?", (path, row["id"]))


def _actualizar_acuse_cancelacion_path(conn, row: dict, path: str):
    _actualizar_acuse_path(conn, row, path, "cancelacion")


def _actualizar_acuse_recepcion_path(conn, row: dict, path: str):
    _actualizar_acuse_path(conn, row, path, "recepcion")


def _folio_serie(row: dict) -> str:
    serie = str(row.get("serie") or "").strip()
    folio = str(row.get("folio_cfdi") or "").strip()
    return f"{serie}{folio}" if serie else folio


def _limpiar_sae_mio_por_cfdi(row: dict):
    folio_sae = _folio_serie(row)
    factura_ids = set()
    if row.get("factura_id"):
        try:
            factura_ids.add(int(row.get("factura_id")))
        except Exception:
            pass
    try:
        with get_timbrado_connection() as conn:
            uuid = str(row.get("uuid") or "").strip()
            if uuid:
                for q in conn.execute("SELECT factura_id FROM timbrado_queue WHERE uuid = ?", (uuid,)).fetchall() or []:
                    try:
                        factura_ids.add(int(dict(q).get("factura_id")))
                    except Exception:
                        pass
            try:
                for relacion in conn.execute(
                    "SELECT factura_id FROM cfdi_consolidacion_facturas WHERE cfdi_emitido_id = ?",
                    (row.get("id"),),
                ).fetchall() or []:
                    try:
                        factura_ids.add(int(dict(relacion).get("factura_id")))
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass

    conn_legacy = get_legacy_connection()
    cur = conn_legacy.cursor()
    try:
        affected = 0
        if factura_ids:
            placeholders = ",".join(["%s"] * len(factura_ids))
            params = [""] + list(factura_ids)
            cur.execute(f"UPDATE facturas SET sae_codigo = %s WHERE id IN ({placeholders})", tuple(params))
            affected += cur.rowcount or 0
        if folio_sae:
            cur.execute("UPDATE facturas SET sae_codigo = '' WHERE COALESCE(sae_codigo, '') = %s", (folio_sae,))
            affected += cur.rowcount or 0
        conn_legacy.commit()
        return affected
    finally:
        cur.close()
        conn_legacy.close()


def _cancelar_facturas_internas_por_cfdi(row: dict) -> int:
    """Cancela todas las facturas base de un CFDI, incluso si fue consolidado."""
    factura_ids = set()
    if row.get("factura_id"):
        try:
            factura_ids.add(int(row.get("factura_id")))
        except Exception:
            pass
    with get_timbrado_connection() as conn:
        uuid_cfdi = str(row.get("uuid") or "").strip()
        if uuid_cfdi:
            for q in conn.execute("SELECT factura_id FROM timbrado_queue WHERE uuid = ?", (uuid_cfdi,)).fetchall() or []:
                try:
                    factura_ids.add(int(dict(q).get("factura_id")))
                except Exception:
                    pass
        try:
            for relacion in conn.execute(
                "SELECT factura_id FROM cfdi_consolidacion_facturas WHERE cfdi_emitido_id = ?",
                (row.get("id"),),
            ).fetchall() or []:
                try:
                    factura_ids.add(int(dict(relacion).get("factura_id")))
                except Exception:
                    pass
        except Exception:
            pass
    if not factura_ids:
        return 0
    conn_legacy = get_legacy_connection()
    cur = conn_legacy.cursor()
    try:
        placeholders = ",".join(["%s"] * len(factura_ids))
        cur.execute(
            f"UPDATE facturas SET estatus = %s, sae_codigo = %s WHERE id IN ({placeholders})",
            tuple(["Cancelada", "CANCELADO"] + list(factura_ids)),
        )
        conn_legacy.commit()
        return cur.rowcount or 0
    finally:
        cur.close()
        conn_legacy.close()


def _sat_indica_cancelado(estatus: dict) -> bool:
    texto = " ".join(str((estatus or {}).get(k) or "") for k in ("estado", "estatus_cancelacion", "codigo_estatus")).lower()
    return "cancelado" in texto or "cancelada" in texto


def _sat_indica_cancelacion_en_proceso(estatus: dict) -> bool:
    texto = " ".join(str((estatus or {}).get(k) or "") for k in ("estado", "estatus_cancelacion", "codigo_estatus")).lower()
    return "en proceso" in texto or "solicitud" in texto and ("cancel" in texto or "acept" in texto)


def _marcar_cfdi_local_cancelado(conn, row: dict, motivo: str = "Sincronizado desde SAT/PAC") -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if row.get("origen_cfdi") == "COBRANZA":
        conn.execute("UPDATE cfdi_cobranza_emitidos SET estatus_cfdi = ? WHERE id = ?", ("CANCELADA", row["id"]))
        limpiadas = 0
    else:
        _asegurar_columnas_cancelacion_cfdi(conn)
        conn.execute(
            """
            UPDATE cfdi_emitidos
            SET estatus_cfdi = ?, cancelacion_motivo = COALESCE(cancelacion_motivo, ?), fecha_cancelacion = COALESCE(fecha_cancelacion, ?)
            WHERE id = ?
            """,
            ("CANCELADA", motivo, now, row["id"]),
        )
        limpiadas = _limpiar_sae_mio_por_cfdi(row)
    uuid_cfdi = str(row.get("uuid") or "").strip()
    if uuid_cfdi:
        try:
            conn.execute("UPDATE timbrado_queue SET estatus = ? WHERE uuid = ?", ("CANCELADA", uuid_cfdi))
        except Exception:
            pass
    return limpiadas


@router.post("/cfdi-emitidos/{folio}/cancelar")
def cancelar_cfdi_emitido(folio: str, datos: dict | None = None):
    motivos_sat = {
        "01": "Comprobante emitido con errores con relacion",
        "02": "Comprobante emitido con errores sin relacion",
        "03": "No se llevo a cabo la operacion",
        "04": "Operacion nominativa relacionada en una factura global",
    }
    motivo_codigo = str((datos or {}).get("motivo_codigo") or "").strip()[:2]
    if motivo_codigo not in motivos_sat:
        raise HTTPException(status_code=400, detail="Motivo SAT invalido. Usa 01, 02, 03 o 04.")
    uuid_sustitucion = str((datos or {}).get("uuid_sustitucion") or "").strip()
    cancelar_internas = bool((datos or {}).get("cancelar_internas"))
    if motivo_codigo == "01" and not uuid_sustitucion:
        raise HTTPException(status_code=400, detail="El motivo 01 requiere UUID sustituto.")
    motivo = f"{motivo_codigo} - {motivos_sat[motivo_codigo]}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_timbrado_connection() as conn:
        _asegurar_columnas_cancelacion_cfdi(conn)
        row = _buscar_cfdi_emitido_por_folio(conn, folio)
        uuid_cfdi = str(row.get("uuid") or "").strip()
        if str(row.get("estatus_cfdi") or "").upper() == "CANCELADA":
            limpiadas = 0 if row.get("origen_cfdi") == "COBRANZA" else _limpiar_sae_mio_por_cfdi(row)
            return {
                "mensaje": f"El CFDI {row.get('serie') or ''}{row.get('folio_cfdi') or folio} ya estaba cancelado.",
                "limpiadas_mio": limpiadas,
                "detalle": row,
            }
        pac_cancelacion = None
        acuse_cancelacion_path = ""
        config = obtener_config_timbrado(conn, row.get("empresa")) or {}
        proveedor = str(config.get("proveedor") or "").strip().upper()
        if proveedor and proveedor != "SIMULADO" and uuid_cfdi:
            factura_info = {
                "id": row.get("factura_id") or row.get("id") or 0,
                "factura_id": row.get("factura_id") or row.get("id") or 0,
                "factura": row.get("factura") or row.get("folio_cfdi") or folio,
                "empresa": row.get("empresa"),
            }
            try:
                pac_cancelacion = cancelar_cfdi_pac(proveedor, config, uuid_cfdi, motivo_codigo, uuid_sustitucion)
                registrar_intento_pac(
                    conn,
                    factura_info,
                    proveedor,
                    "CANCELACION_OK",
                    pac_cancelacion.estatus_cancelacion or pac_cancelacion.estatus_uuid or "Cancelación aceptada por PAC",
                    uuid_val=uuid_cfdi,
                    response=pac_cancelacion.raw_response,
                )
                if pac_cancelacion.acuse:
                    acuse_cancelacion_path = _guardar_acuse_cancelacion_archivo(row, pac_cancelacion.acuse)
                    _actualizar_acuse_cancelacion_path(conn, row, acuse_cancelacion_path)
            except (PacNoIntegradoError, PacTimbradoError) as exc:
                # Una respuesta transitoria del PAC (por ejemplo, Finkok 708) no
                # siempre significa que el SAT rechazó la operación. Verificamos
                # el estado antes de devolver un error para no dejar cancelaciones
                # ya aplicadas en SAT como si continuaran vigentes localmente.
                sat_cancelado = False
                estatus_sat = None
                try:
                    datos_sat = _datos_consulta_sat_desde_xml(row)
                    estatus_sat = consultar_estatus_cfdi_pac(
                        proveedor,
                        config,
                        uuid_cfdi,
                        datos_sat.get("rfc_receptor") or "",
                        datos_sat.get("total") or "",
                    )
                    sat_cancelado = _sat_indica_cancelado({
                        "codigo_estatus": estatus_sat.codigo_estatus,
                        "estado": estatus_sat.estado,
                        "estatus_cancelacion": estatus_sat.estatus_cancelacion,
                    })
                except (PacNoIntegradoError, PacTimbradoError):
                    pass
                error_pac = str(exc or "")
                en_cola_finkok = "buffer_cancellation" in error_pac.lower() or "buffer cancellation" in error_pac.lower()
                if sat_cancelado:
                    registrar_intento_pac(
                        conn,
                        factura_info,
                        proveedor,
                        "CANCELACION_CONFIRMADA_SAT",
                        "El SAT confirmó la cancelación después de una respuesta transitoria del PAC.",
                        uuid_val=uuid_cfdi,
                        response=estatus_sat.raw_response if estatus_sat else {},
                    )
                elif en_cola_finkok or _sat_indica_cancelacion_en_proceso({
                    "codigo_estatus": estatus_sat.codigo_estatus if estatus_sat else "",
                    "estado": estatus_sat.estado if estatus_sat else "",
                    "estatus_cancelacion": estatus_sat.estatus_cancelacion if estatus_sat else "",
                }):
                    # BufferCancellation confirma que Finkok ya recibió la
                    # solicitud. Al elegir "Cancelar ambas" se libera el folio
                    # interno aunque SAT aún deba concluir el proceso.
                    internas_canceladas = 0
                    if cancelar_internas and row.get("origen_cfdi") != "COBRANZA":
                        internas_canceladas = _cancelar_facturas_internas_por_cfdi(row)
                    registrar_intento_pac(
                        conn,
                        factura_info,
                        proveedor,
                        "CANCELACION_EN_PROCESO",
                        "La solicitud de cancelación está en proceso y requiere la resolución del receptor/SAT.",
                        uuid_val=uuid_cfdi,
                        response=estatus_sat.raw_response if estatus_sat else {},
                    )
                    return {
                        "mensaje": "La solicitud de cancelación fue recibida y está en proceso. El CFDI sigue vigente hasta que el receptor/SAT la resuelva." + (" Las facturas internas ya fueron liberadas." if internas_canceladas else ""),
                        "folio_serie": _folio_serie(row),
                        "uuid": uuid_cfdi,
                        "cancelacion_pendiente": True,
                        "facturas_internas_canceladas": internas_canceladas,
                        "estatus_sat": {
                            "codigo_estatus": estatus_sat.codigo_estatus if estatus_sat else "",
                            "estado": estatus_sat.estado if estatus_sat else "",
                            "es_cancelable": estatus_sat.es_cancelable if estatus_sat else "",
                            "estatus_cancelacion": estatus_sat.estatus_cancelacion if estatus_sat else "",
                        },
                    }
                else:
                    registrar_intento_pac(
                        conn,
                        factura_info,
                        proveedor,
                        "CANCELACION_ERROR",
                        str(exc),
                        uuid_val=uuid_cfdi,
                        response={
                            "motivo_codigo": motivo_codigo,
                            "uuid_sustitucion": uuid_sustitucion,
                        },
                    )
                    raise HTTPException(status_code=500, detail=f"No se pudo cancelar el CFDI en PAC: {exc}")
        if row.get("origen_cfdi") == "COBRANZA":
            conn.execute("UPDATE cfdi_cobranza_emitidos SET estatus_cfdi = ? WHERE id = ?", ("CANCELADA", row["id"]))
        else:
            conn.execute(
                """
                UPDATE cfdi_emitidos
                SET estatus_cfdi = ?, cancelacion_motivo = ?, cancelacion_motivo_codigo = ?, uuid_sustitucion = ?, fecha_cancelacion = ?
                WHERE id = ?
                """,
                ("CANCELADA", motivo, motivo_codigo, uuid_sustitucion or None, now, row["id"]),
            )
        if uuid_cfdi:
            try:
                conn.execute("UPDATE timbrado_queue SET estatus = ? WHERE uuid = ?", ("CANCELADA", uuid_cfdi))
            except Exception:
                pass
        limpiadas = 0 if row.get("origen_cfdi") == "COBRANZA" else _limpiar_sae_mio_por_cfdi(row)
        internas_canceladas = 0 if row.get("origen_cfdi") == "COBRANZA" or not cancelar_internas else _cancelar_facturas_internas_por_cfdi(row)
    return {
        "mensaje": f"CFDI {_folio_serie(row) or folio} cancelado." + (" Se limpio SAE en MIO." if row.get("origen_cfdi") != "COBRANZA" else ""),
        "folio_serie": _folio_serie(row),
        "uuid": row.get("uuid"),
        "motivo_codigo": motivo_codigo,
        "motivo": motivo,
        "uuid_sustitucion": uuid_sustitucion,
        "limpiadas_mio": limpiadas,
        "facturas_internas_canceladas": internas_canceladas,
        "acuse_cancelacion_path": acuse_cancelacion_path,
        "pac_cancelacion": {
            "proveedor": pac_cancelacion.proveedor,
            "estatus_uuid": pac_cancelacion.estatus_uuid,
            "estatus_cancelacion": pac_cancelacion.estatus_cancelacion,
        } if pac_cancelacion else None,
    }


@router.get("/cfdi-emitidos/{folio}/xml")
def descargar_cfdi_xml(folio: str):
    with get_timbrado_connection() as conn:
        row = _buscar_cfdi_emitido_por_folio(conn, folio)
    return _descargar_archivo_cfdi(row, "xml_path", "xml", "application/xml")


@router.post("/cfdi-emitidos/{folio}/xml")
async def reponer_cfdi_xml_en_servidor(folio: str, file: UploadFile = File(...)):
    """Restaura en el servidor un XML timbrado emitido antes de una migración.

    Se valida el UUID cuando existe, de modo que no pueda asociarse por error
    el XML de otro CFDI al folio elegido.
    """
    if Path(file.filename or "").suffix.lower() != ".xml":
        raise HTTPException(status_code=400, detail="Selecciona un archivo XML timbrado.")
    content = await file.read()
    if not content or len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="El XML está vacío o excede 5 MB.")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise HTTPException(status_code=400, detail=f"El archivo no es un XML CFDI válido: {exc}")

    uuid_xml = ""
    for node in root.iter():
        if str(node.tag).rsplit("}", 1)[-1] == "TimbreFiscalDigital":
            uuid_xml = str(node.attrib.get("UUID") or "").strip().upper()
            break

    with get_timbrado_connection() as conn:
        row = _buscar_cfdi_emitido_por_folio(conn, folio)
        uuid_registrado = str(row.get("uuid") or "").strip().upper()
        if uuid_registrado and uuid_xml and uuid_registrado != uuid_xml:
            raise HTTPException(status_code=400, detail="El UUID del XML no coincide con el CFDI seleccionado.")
        if uuid_registrado and not uuid_xml:
            raise HTTPException(status_code=400, detail="El XML no contiene TimbreFiscalDigital/UUID.")

        fecha = str(root.attrib.get("Fecha") or "")[:4]
        anio = fecha if fecha.isdigit() and len(fecha) == 4 else str(datetime.now().year)
        base_dir = Path(ruta_empresa_fiscal(row.get("empresa") or "SIN_EMPRESA")) / anio / "xml"
        base_dir.mkdir(parents=True, exist_ok=True)
        nombre_base = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(row.get("factura") or folio)).strip("._") or "cfdi"
        serie_folio = re.sub(r"[^A-Za-z0-9_.-]+", "_", _folio_serie(row) or str(folio))
        destino = base_dir / f"{nombre_base}-{serie_folio}.xml"
        destino.write_bytes(content)

        tabla = "cfdi_cobranza_emitidos" if row.get("origen_cfdi") == "COBRANZA" else "cfdi_emitidos"
        if tabla == "cfdi_cobranza_emitidos":
            conn.execute("UPDATE cfdi_cobranza_emitidos SET xml_path = ? WHERE id = ?", (str(destino), row["id"]))
        else:
            conn.execute("UPDATE cfdi_emitidos SET xml_path = ?, pdf_path = NULL WHERE id = ?", (str(destino), row["id"]))

    return {
        "ok": True,
        "folio_serie": _folio_serie(row) or folio,
        "uuid": uuid_xml or uuid_registrado,
        "xml_path": str(destino),
        "mensaje": "XML timbrado restaurado en el servidor. Ya puede generar PDF y consultar SAT.",
    }


@router.get("/cfdi-emitidos/{folio}/estatus-sat")
def consultar_estatus_sat_cfdi_emitido(folio: str):
    with get_timbrado_connection() as conn:
        row = _buscar_cfdi_emitido_por_folio(conn, folio)
        uuid_cfdi = str(row.get("uuid") or "").strip()
        if not uuid_cfdi:
            raise HTTPException(status_code=400, detail="El CFDI no tiene UUID para consultar estatus SAT.")
        config = obtener_config_timbrado(conn, row.get("empresa")) or {}
        proveedor = str(config.get("proveedor") or "").strip().upper()
        if not proveedor or proveedor == "SIMULADO":
            return {
                "ok": True,
                "modo": "LOCAL",
                "mensaje": "Documento simulado/local; no hay consulta PAC real.",
                "folio_serie": _folio_serie(row),
                "uuid": uuid_cfdi,
                "estatus_local": row.get("estatus_cfdi"),
            }
        datos_sat = _datos_consulta_sat_desde_xml(row)
        factura_info = {
            "id": row.get("factura_id") or row.get("id") or 0,
            "factura_id": row.get("factura_id") or row.get("id") or 0,
            "factura": row.get("factura") or row.get("folio_cfdi") or folio,
            "empresa": row.get("empresa"),
        }
        try:
            estatus = consultar_estatus_cfdi_pac(
                proveedor,
                config,
                uuid_cfdi,
                datos_sat.get("rfc_receptor") or "",
                datos_sat.get("total") or "",
            )
            registrar_intento_pac(
                conn,
                factura_info,
                proveedor,
                "ESTATUS_OK",
                estatus.estado or estatus.codigo_estatus or "Consulta SAT correcta",
                uuid_val=uuid_cfdi,
                response=estatus.raw_response,
            )
        except (PacNoIntegradoError, PacTimbradoError) as exc:
            registrar_intento_pac(
                conn,
                factura_info,
                proveedor,
                "ESTATUS_ERROR",
                str(exc),
                uuid_val=uuid_cfdi,
                response={"rfc_receptor": datos_sat.get("rfc_receptor") or "", "total": datos_sat.get("total") or ""},
            )
            raise HTTPException(status_code=500, detail=f"No se pudo consultar estatus SAT/PAC: {exc}")
    return {
        "ok": True,
        "modo": "PAC",
        "folio_serie": _folio_serie(row),
        "uuid": uuid_cfdi,
        "proveedor": estatus.proveedor,
        "codigo_estatus": estatus.codigo_estatus,
        "estado": estatus.estado,
        "es_cancelable": estatus.es_cancelable,
        "estatus_cancelacion": estatus.estatus_cancelacion,
        "consulta": estatus.raw_response,
    }


@router.post("/cfdi-emitidos/{folio}/sincronizar-estatus-sat")
def sincronizar_estatus_sat_cfdi_emitido(folio: str):
    with get_timbrado_connection() as conn:
        row = _buscar_cfdi_emitido_por_folio(conn, folio)
        uuid_cfdi = str(row.get("uuid") or "").strip()
        if not uuid_cfdi:
            raise HTTPException(status_code=400, detail="El CFDI no tiene UUID para sincronizar estatus SAT.")
        config = obtener_config_timbrado(conn, row.get("empresa")) or {}
        proveedor = str(config.get("proveedor") or "").strip().upper()
        if not proveedor or proveedor == "SIMULADO":
            return {
                "ok": True,
                "sincronizado": False,
                "modo": "LOCAL",
                "mensaje": "Documento simulado/local; no hay estatus PAC real para sincronizar.",
                "folio_serie": _folio_serie(row),
                "uuid": uuid_cfdi,
                "estatus_local": row.get("estatus_cfdi"),
            }
        datos_sat = _datos_consulta_sat_desde_xml(row)
        factura_info = {
            "id": row.get("factura_id") or row.get("id") or 0,
            "factura_id": row.get("factura_id") or row.get("id") or 0,
            "factura": row.get("factura") or row.get("folio_cfdi") or folio,
            "empresa": row.get("empresa"),
        }
        try:
            estatus = consultar_estatus_cfdi_pac(
                proveedor,
                config,
                uuid_cfdi,
                datos_sat.get("rfc_receptor") or "",
                datos_sat.get("total") or "",
            )
            estatus_dict = {
                "proveedor": estatus.proveedor,
                "codigo_estatus": estatus.codigo_estatus,
                "estado": estatus.estado,
                "es_cancelable": estatus.es_cancelable,
                "estatus_cancelacion": estatus.estatus_cancelacion,
            }
            sat_cancelado = _sat_indica_cancelado(estatus_dict)
            limpiadas = 0
            if sat_cancelado:
                limpiadas = _marcar_cfdi_local_cancelado(conn, row, "Sincronizado desde SAT/PAC")
            registrar_intento_pac(
                conn,
                factura_info,
                proveedor,
                "ESTATUS_SYNC_CANCELADA" if sat_cancelado else "ESTATUS_SYNC_SIN_CAMBIO",
                estatus.estado or estatus.estatus_cancelacion or estatus.codigo_estatus or "Consulta SAT sincronizada",
                uuid_val=uuid_cfdi,
                response={**estatus.raw_response, "sincronizado_cancelada": sat_cancelado, "limpiadas_mio": limpiadas},
            )
        except (PacNoIntegradoError, PacTimbradoError) as exc:
            registrar_intento_pac(
                conn,
                factura_info,
                proveedor,
                "ESTATUS_SYNC_ERROR",
                str(exc),
                uuid_val=uuid_cfdi,
                response={"rfc_receptor": datos_sat.get("rfc_receptor") or "", "total": datos_sat.get("total") or ""},
            )
            raise HTTPException(status_code=500, detail=f"No se pudo sincronizar estatus SAT/PAC: {exc}")
    return {
        "ok": True,
        "sincronizado": bool(sat_cancelado),
        "mensaje": "CFDI marcado como CANCELADA segun SAT/PAC." if sat_cancelado else "SAT/PAC no reporta cancelacion; no se modifico el estatus local.",
        "folio_serie": _folio_serie(row),
        "uuid": uuid_cfdi,
        "estatus_sat": estatus_dict,
        "limpiadas_mio": limpiadas,
    }


@router.get("/cfdi-emitidos/{folio}/acuse-cancelacion")
def descargar_acuse_cancelacion_cfdi_emitido(folio: str, refresh: int = 0):
    with get_timbrado_connection() as conn:
        row = _buscar_cfdi_emitido_por_folio(conn, folio)
        local_path = str(row.get("acuse_cancelacion_path") or "").strip()
        if not int(refresh or 0) and local_path and os.path.exists(local_path):
            filename = f"acuse-cancelacion-{_folio_serie(row) or folio}.xml"
            return FileResponse(
                local_path,
                media_type="application/xml",
                filename=filename,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        uuid_cfdi = str(row.get("uuid") or "").strip()
        if not uuid_cfdi:
            raise HTTPException(status_code=400, detail="El CFDI no tiene UUID para recuperar acuse.")
        config = obtener_config_timbrado(conn, row.get("empresa")) or {}
        proveedor = str(config.get("proveedor") or "").strip().upper()
        if not proveedor or proveedor == "SIMULADO":
            raise HTTPException(status_code=400, detail="El documento es local/simulado; no existe acuse PAC real.")
        factura_info = {
            "id": row.get("factura_id") or row.get("id") or 0,
            "factura_id": row.get("factura_id") or row.get("id") or 0,
            "factura": row.get("factura") or row.get("folio_cfdi") or folio,
            "empresa": row.get("empresa"),
        }
        try:
            acuse = obtener_acuse_cancelacion_pac(proveedor, config, uuid_cfdi)
            registrar_intento_pac(
                conn,
                factura_info,
                proveedor,
                "ACUSE_CANCELACION_REFRESH_OK" if int(refresh or 0) else "ACUSE_CANCELACION_OK",
                acuse.fecha or ("Acuse de cancelación actualizado" if int(refresh or 0) else "Acuse de cancelación recuperado"),
                uuid_val=uuid_cfdi,
                response=acuse.raw_response,
            )
            local_path = _guardar_acuse_cancelacion_archivo(row, acuse.acuse)
            _actualizar_acuse_cancelacion_path(conn, row, local_path)
        except (PacNoIntegradoError, PacTimbradoError) as exc:
            registrar_intento_pac(
                conn,
                factura_info,
                proveedor,
                "ACUSE_CANCELACION_REFRESH_ERROR" if int(refresh or 0) else "ACUSE_CANCELACION_ERROR",
                str(exc),
                uuid_val=uuid_cfdi,
            )
            raise HTTPException(status_code=500, detail=f"No se pudo recuperar acuse de cancelación: {exc}")
    filename = f"acuse-cancelacion-{_folio_serie(row) or folio}.xml"
    return FileResponse(
        local_path,
        media_type="application/xml",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/cfdi-emitidos/{folio}/acuse-recepcion")
def descargar_acuse_recepcion_cfdi_emitido(folio: str, refresh: int = 0):
    with get_timbrado_connection() as conn:
        row = _buscar_cfdi_emitido_por_folio(conn, folio)
        local_path = str(row.get("acuse_recepcion_path") or "").strip()
        if not int(refresh or 0) and local_path and os.path.exists(local_path):
            filename = f"acuse-recepcion-{_folio_serie(row) or folio}.xml"
            return FileResponse(
                local_path,
                media_type="application/xml",
                filename=filename,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        uuid_cfdi = str(row.get("uuid") or "").strip()
        if not uuid_cfdi:
            raise HTTPException(status_code=400, detail="El CFDI no tiene UUID para recuperar acuse.")
        config = obtener_config_timbrado(conn, row.get("empresa")) or {}
        proveedor = str(config.get("proveedor") or "").strip().upper()
        if not proveedor or proveedor == "SIMULADO":
            raise HTTPException(status_code=400, detail="El documento es local/simulado; no existe acuse PAC real.")
        factura_info = {
            "id": row.get("factura_id") or row.get("id") or 0,
            "factura_id": row.get("factura_id") or row.get("id") or 0,
            "factura": row.get("factura") or row.get("folio_cfdi") or folio,
            "empresa": row.get("empresa"),
        }
        try:
            acuse = obtener_acuse_recepcion_pac(proveedor, config, uuid_cfdi)
            registrar_intento_pac(
                conn,
                factura_info,
                proveedor,
                "ACUSE_RECEPCION_REFRESH_OK" if int(refresh or 0) else "ACUSE_RECEPCION_OK",
                acuse.fecha or ("Acuse de recepción actualizado" if int(refresh or 0) else "Acuse de recepción recuperado"),
                uuid_val=uuid_cfdi,
                response=acuse.raw_response,
            )
            local_path = _guardar_acuse_recepcion_archivo(row, acuse.acuse)
            _actualizar_acuse_recepcion_path(conn, row, local_path)
        except (PacNoIntegradoError, PacTimbradoError) as exc:
            registrar_intento_pac(
                conn,
                factura_info,
                proveedor,
                "ACUSE_RECEPCION_REFRESH_ERROR" if int(refresh or 0) else "ACUSE_RECEPCION_ERROR",
                str(exc),
                uuid_val=uuid_cfdi,
            )
            raise HTTPException(status_code=500, detail=f"No se pudo recuperar acuse de recepción: {exc}")
    filename = f"acuse-recepcion-{_folio_serie(row) or folio}.xml"
    return FileResponse(
        local_path,
        media_type="application/xml",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/cfdi-emitidos/{folio}/paquete")
def descargar_paquete_cfdi_emitido(folio: str):
    with get_timbrado_connection() as conn:
        row = _buscar_cfdi_emitido_por_folio(conn, folio)
        intentos = listar_intentos_pac(
            conn,
            factura=row.get("factura") or row.get("folio_cfdi") or folio,
            empresa=row.get("empresa"),
            limit=200,
        )
    folio_serie = _folio_serie(row) or str(row.get("factura") or folio)
    safe = _safe_package_name(folio_serie)
    buf = io.BytesIO()
    metadata = {
        "folio_consulta": folio,
        "folio_serie": folio_serie,
        "cfdi": {k: v for k, v in row.items() if k not in {"response_json"}},
        "generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "intentos_pac": intentos,
    }
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        xml_path = str(row.get("xml_path") or "").strip()
        _zip_add_file(zf, xml_path, f"{safe}/xml/{safe}.xml")
        acuse_recepcion_path = str(row.get("acuse_recepcion_path") or "").strip()
        _zip_add_file(zf, acuse_recepcion_path, f"{safe}/acuse_recepcion/{safe}-acuse-recepcion.xml")
        acuse_path = str(row.get("acuse_cancelacion_path") or "").strip()
        _zip_add_file(zf, acuse_path, f"{safe}/acuse_cancelacion/{safe}-acuse-cancelacion.xml")
        try:
            pdf_bytes = _generar_pdf_cfdi_bytes(row)
            zf.writestr(f"{safe}/pdf/{safe}.pdf", pdf_bytes)
        except Exception as exc:
            zf.writestr(f"{safe}/pdf_error.txt", f"No se pudo generar PDF: {exc}")
        zf.writestr(f"{safe}/metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2, default=str))
        zf.writestr(
            f"{safe}/bitacora_pac.json",
            json.dumps(intentos, ensure_ascii=False, indent=2, default=str),
        )
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe}-paquete-fiscal.zip"'},
    )


@router.post("/cfdi-emitidos/{folio}/enviar-correo")
def enviar_correo_cfdi_emitido(folio: str, datos: dict):
    payload = datos or {}
    raw_destinatarios = payload.get("destinatarios")
    if isinstance(raw_destinatarios, (list, tuple)):
        candidatos = raw_destinatarios
    else:
        # Conserva compatibilidad con los envios anteriores de un solo correo.
        candidatos = re.split(r"[;,]", str(raw_destinatarios or payload.get("destinatario") or ""))
    destinatarios = []
    vistos = set()
    for candidato in candidatos:
        correo = str(candidato or "").strip()
        clave = correo.lower()
        if not correo:
            continue
        if "@" not in correo or clave in vistos:
            continue
        vistos.add(clave)
        destinatarios.append(correo)
    if not destinatarios:
        raise HTTPException(status_code=400, detail="Indica al menos un correo destino valido.")
    recibo_cobranza = None
    with get_timbrado_connection() as conn:
        row = _buscar_cfdi_emitido_por_folio(conn, folio)
        xml_registrado = str(row.get("xml_path") or "").strip()
        xml_recuperado = _resolver_xml_cfdi_en_instalacion(row)
        if xml_recuperado and xml_recuperado != xml_registrado and os.path.isfile(xml_recuperado):
            tabla = "cfdi_cobranza_emitidos" if row.get("origen_cfdi") == "COBRANZA" else "cfdi_emitidos"
            conn.execute("UPDATE " + tabla + " SET xml_path = ? WHERE id = ?", (xml_recuperado, row["id"]))
            row["xml_path"] = xml_recuperado
        cfg = _obtener_correo_documento(conn, "factura_fiscal", row.get("empresa"))
        if row.get("origen_cfdi") == "COBRANZA":
            recibo_cobranza = conn.execute(
                "SELECT monto_total, tipo_recibo FROM cobranza_recibos WHERE id = ? LIMIT 1",
                (row.get("recibo_id"),),
            ).fetchone()
    if not cfg:
        raise HTTPException(status_code=400, detail="No hay cuenta activa configurada para factura fiscal.")

    folio_serie = _folio_serie(row) or str(row.get("factura") or folio)
    xml_path = str(row.get("xml_path") or "").strip()
    if not xml_path or not os.path.exists(xml_path):
        raise HTTPException(
            status_code=404,
            detail="No se encontró el XML timbrado en el servidor. El CFDI ya puede estar timbrado, pero requiere restaurar su XML para enviarlo por correo.",
        )
    with open(xml_path, "rb") as fh:
        xml_bytes = fh.read()
    pdf_bytes = _generar_pdf_cfdi_bytes(row)
    variables = _enriquecer_variables_correo_desde_xml(_variables_correo_cfdi(row, folio_serie), xml_bytes)
    # En un CFDI tipo P el Total fiscal es obligatoriamente 0.00. Para que el
    # correo informe lo realmente recibido, tomamos el importe del recibo de
    # cobranza y no el atributo Total del comprobante.
    if recibo_cobranza:
        datos_recibo = dict(recibo_cobranza)
        variables["monto_total"] = str(datos_recibo.get("monto_total") or "0")
        if str(datos_recibo.get("tipo_recibo") or "").upper() == "PAGO":
            variables["tipo_documento"] = "Complemento de pago"
        elif str(datos_recibo.get("tipo_recibo") or "").upper() == "NOTA_CREDITO":
            variables["tipo_documento"] = "Nota de crédito"
    asunto_tpl = str(cfg.get("asunto_template") or "Factura fiscal {folio_serie}").strip()
    cuerpo_tpl = str(cfg.get("cuerpo_template") or "Adjuntamos la factura fiscal {folio_serie}.").strip()
    asunto = str(payload.get("asunto") or _render_template_correo(asunto_tpl, variables)).strip()
    cuerpo = str(payload.get("cuerpo") or _render_template_correo(cuerpo_tpl, variables)).strip()
    cuerpo_html = _cuerpo_html_correo_cfdi(variables, str(cfg.get("nombre_remitente") or variables.get("empresa_nombre") or ""))
    try:
        envios = [
            _enviar_correo_smtp(
                cfg,
                destinatario,
                asunto,
                cuerpo,
                [
                    (f"{folio_serie}.pdf", pdf_bytes, "application/pdf"),
                    (f"{folio_serie}.xml", xml_bytes, "application/xml"),
                ],
                cuerpo_html=cuerpo_html,
            )
            for destinatario in destinatarios
        ]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo enviar el correo: {exc}")
    return {
        "mensaje": f"Factura fiscal {folio_serie} aceptada por SMTP para {len(destinatarios)} destinatario(s).",
        "destinatarios": destinatarios,
        "detalle_envio": envios,
    }


@router.get("/cfdi-emitidos/{folio}/pdf")
def descargar_cfdi_pdf(folio: str):
    from app.routers.timbrado_pdf import generar_cfdi_pdf
    import xml.etree.ElementTree as ET
    with get_timbrado_connection() as conn:
        row = _buscar_cfdi_emitido_por_folio(conn, folio)
    xml_path = str(row.get("xml_path") or "").strip()
    if not xml_path or not os.path.exists(xml_path):
        return _descargar_archivo_cfdi(row, "pdf_path", "pdf", "application/pdf")
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        buf = generar_cfdi_pdf(root, db_row=row)
        filename = f"{row.get('folio_cfdi') or row.get('factura') or 'cfdi'}.pdf"
        return StreamingResponse(buf, media_type="application/pdf", headers={
            "Content-Disposition": f'inline; filename="{filename}"',
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando PDF: {e}")


@router.post("/cfdi-emitidos/descarga-lote")
def descargar_lote_cfdi_emitidos(payload: dict):
    """Entrega en ZIP los XML y/o PDF de varios CFDI seleccionados desde MIO."""
    folios = []
    for value in (payload.get("folios") or []):
        folio = str(value or "").strip()
        if folio and folio not in folios:
            folios.append(folio)
    tipos = {str(value or "").strip().lower() for value in (payload.get("tipos") or [])}
    tipos &= {"xml", "pdf"}
    if not folios:
        raise HTTPException(status_code=400, detail="Selecciona al menos un CFDI.")
    if len(folios) > 100:
        raise HTTPException(status_code=400, detail="El límite de descarga es de 100 CFDI por archivo ZIP.")
    if not tipos:
        raise HTTPException(status_code=400, detail="Selecciona PDF, XML o ambos formatos.")

    pendientes = []
    buf = io.BytesIO()
    usados: set[str] = set()
    with get_timbrado_connection() as conn, zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for folio in folios:
            try:
                row = _buscar_cfdi_emitido_por_folio(conn, folio)
            except HTTPException:
                pendientes.append(f"{folio}: no se encontró CFDI emitido")
                continue
            nombre = _safe_package_name(_folio_serie(row) or folio) or "cfdi"
            nombre_base = nombre
            secuencia = 2
            while nombre.lower() in usados:
                nombre = f"{nombre_base}-{secuencia}"
                secuencia += 1
            usados.add(nombre.lower())
            xml_path = _resolver_xml_cfdi_en_instalacion(row)
            row["xml_path"] = xml_path

            if "xml" in tipos:
                if xml_path and os.path.isfile(xml_path):
                    zf.write(xml_path, f"{nombre}/{nombre}.xml")
                else:
                    pendientes.append(f"{folio}: XML no disponible en servidor")
            if "pdf" in tipos:
                try:
                    pdf_bytes = _generar_pdf_cfdi_bytes(row)
                    zf.writestr(f"{nombre}/{nombre}.pdf", pdf_bytes)
                except Exception as exc:
                    pendientes.append(f"{folio}: PDF no disponible ({exc})")
        if pendientes:
            zf.writestr("incidencias.txt", "\n".join(pendientes))
    buf.seek(0)
    fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"CFDI_seleccionados_{fecha}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
