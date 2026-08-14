import json
import threading
import os
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form

from app.dependencies import require_user
from app.core.config import settings
from app.legacy_db import get_legacy_connection

# ==== Cartera Cache (in-memory) ====
_cartera_cache_data = None
_cartera_cache_built_at = None
_cartera_cache_stale = False
_cartera_cache_building = False
_cartera_cache_error = None
_cartera_cache_lock = threading.Lock()
CARTERA_CACHE_TTL = timedelta(minutes=15)

def _cc_valid():
    if _cartera_cache_data is None or _cartera_cache_built_at is None:
        return False
    if _cartera_cache_stale:
        return False
    return datetime.now() - _cartera_cache_built_at < CARTERA_CACHE_TTL

_cadena_map_cache = {"data": None, "built_at": None}

def _cc_get_cadena_map():
    cm = _cadena_map_cache
    if cm["data"] is not None and cm["built_at"] and (datetime.now() - cm["built_at"]).total_seconds() < 3600:
        return cm["data"]
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        mapa = _obtener_mapa_cadenas(cursor)
        cm["data"] = mapa
        cm["built_at"] = datetime.now()
        return mapa
    except Exception:
        return cm["data"] or {}
    finally:
        if cursor:
            try: cursor.close()
            except: pass
        if conn:
            try: conn.close()
            except: pass

def _cc_filter(empresa=None, cadena_id=None, numero_cliente=None, documento=None, desde=None, hasta=None, estatus=None):
    if _cartera_cache_data is None:
        return None
    result = list(_cartera_cache_data)
    hoy = datetime.now().date()
    if empresa:
        e = _empresa_cadena_clave(empresa)
        result = [r for r in result if _empresa_cadena_clave(r.get("empresa")) == e]
    if numero_cliente:
        nc = str(numero_cliente).strip()
        result = [r for r in result if str(r.get("numero_cliente","")).strip() == nc]
    if documento:
        d = str(documento).strip().upper()
        result = [r for r in result if d in str(r.get("factura","")).upper()]
    if desde:
        result = [r for r in result if r.get("fecha") and str(r["fecha"]) >= str(desde)]
    if hasta:
        result = [r for r in result if r.get("fecha") and str(r["fecha"]) <= str(hasta)]
    for r in result:
        saldo = r.get("saldo", 0)
        fv = r.get("fecha_vencimiento")
        if saldo > 0 and fv:
            dd = (hoy - fv).days
            r["dias_vencido"] = max(dd, 0)
            if dd > 0:
                r["estatus_cobranza"] = "VENCIDA"
            elif dd == 0:
                r["estatus_cobranza"] = "VENCE_HOY"
            else:
                r["estatus_cobranza"] = "POR_VENCER"
    if estatus:
        s = str(estatus).strip().upper()
        if s == "VENCIDAS":
            result = [r for r in result if r["saldo"] > 0 and r.get("fecha_vencimiento") and r["fecha_vencimiento"] < hoy]
        elif s == "PAGADAS":
            result = [r for r in result if r["saldo"] <= 0]
        elif s == "POR_VENCER":
            result = [r for r in result if r["saldo"] > 0 and r["estatus_cobranza"] == "POR_VENCER"]
    if cadena_id:
        mapa = _cc_get_cadena_map()
        if mapa:
            result = _filtrar_por_cadena(result, cadena_id, mapa)
    return result

def _cc_invalidate():
    global _cartera_cache_stale
    _cartera_cache_stale = True


router = APIRouter(prefix="/api/collections", tags=["collections"])


def _to_float(value):
    if isinstance(value, str):
        value = value.strip().replace("$", "").replace(" ", "")
        if "," in value and "." in value:
            value = value.replace(",", "")
        elif "," in value:
            value = value.replace(",", ".")
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _to_int(value, default=0):
    try:
        if isinstance(value, str):
            value = value.strip()
            if value.endswith(".0"):
                value = value[:-2]
        return int(value or 0)
    except Exception:
        return default


def _dict_rows(cursor):
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _anexar_cfdi_cobranza(recibos: list[dict]) -> list[dict]:
    ids = [int(r.get("id") or 0) for r in recibos if int(r.get("id") or 0) > 0]
    if not ids:
        return recibos
    try:
        from app.routers.timbrado import get_timbrado_connection, _asegurar_tabla_cfdi_cobranza

        placeholders = ",".join(["?"] * len(ids))
        with get_timbrado_connection() as conn:
            _asegurar_tabla_cfdi_cobranza(conn)
            rows = conn.execute(
                f"""
                SELECT recibo_id, tipo_documento, factura, serie, folio_cfdi, uuid,
                       estatus_cfdi, fecha_timbrado, xml_path
                FROM cfdi_cobranza_emitidos
                WHERE recibo_id IN ({placeholders})
                ORDER BY fecha_timbrado DESC, id DESC
                """,
                tuple(ids),
            ).fetchall()
        por_recibo = {}
        for row in rows:
            data = dict(row)
            por_recibo.setdefault(int(data.get("recibo_id") or 0), data)
        for recibo in recibos:
            cfdi = por_recibo.get(int(recibo.get("id") or 0))
            if cfdi:
                recibo["cfdi_tipo"] = cfdi.get("tipo_documento") or ""
                recibo["cfdi_factura"] = cfdi.get("factura") or ""
                recibo["cfdi_serie"] = cfdi.get("serie") or ""
                recibo["cfdi_folio"] = cfdi.get("folio_cfdi") or ""
                recibo["cfdi_uuid"] = cfdi.get("uuid") or ""
                recibo["cfdi_estatus"] = cfdi.get("estatus_cfdi") or ""
                recibo["cfdi_fecha"] = cfdi.get("fecha_timbrado")
                recibo["cfdi_documento"] = f"{recibo['cfdi_serie']}{recibo['cfdi_folio']}".strip()
            else:
                recibo["cfdi_tipo"] = ""
                recibo["cfdi_factura"] = ""
                recibo["cfdi_serie"] = ""
                recibo["cfdi_folio"] = ""
                recibo["cfdi_uuid"] = ""
                recibo["cfdi_estatus"] = ""
                recibo["cfdi_fecha"] = None
                recibo["cfdi_documento"] = ""
    except Exception:
        for recibo in recibos:
            recibo.setdefault("cfdi_documento", "")
            recibo.setdefault("cfdi_estatus", "")
            recibo.setdefault("cfdi_uuid", "")
    return recibos


BANCOS_MEXICO_SEED = [
    ("002", "BANAMEX", "Banco Nacional de Mexico, S.A.", "BNM840515VB1", "SAT/Banxico/RFC publico"),
    ("006", "BANCOMEXT", "Banco Nacional de Comercio Exterior, S.N.C.", "BNC8507311M4", "SAT/Banxico/RFC publico"),
    ("009", "BANOBRAS", "Banco Nacional de Obras y Servicios Publicos, S.N.C.", "BNO670315CDO", "SAT/Banxico/RFC publico"),
    ("012", "BBVA", "BBVA Mexico, S.A.", "BBA830831LJ2", "SAT/Banxico/RFC publico"),
    ("014", "SANTANDER", "Banco Santander Mexico, S.A.", "BSM970519DU8", "SAT/Banxico/RFC publico"),
    ("019", "BANJERCITO", "Banco Nacional del Ejercito, Fuerza Aerea y Armada, S.N.C.", "BNE820901682", "SAT/Banxico/RFC publico"),
    ("021", "HSBC", "HSBC Mexico, S.A.", "HMI950125KG8", "SAT/Banxico/RFC publico"),
    ("030", "BAJIO", "Banco del Bajio, S.A.", "BBA940707IE1", "SAT/Banxico/RFC publico"),
    ("036", "INBURSA", "Banco Inbursa, S.A.", "BII931004P61", "SAT/Banxico/RFC publico"),
    ("042", "MIFEL", "Banca Mifel, S.A.", "BMI9312038R3", "SAT/Banxico/RFC publico"),
    ("044", "SCOTIABANK", "Scotiabank Inverlat, S.A.", "SIN9412025I4", "SAT/Banxico/RFC publico"),
    ("058", "BANREGIO", "Banco Regional, S.A.", "BRM940216EQ6", "SAT/Banxico/RFC publico"),
    ("059", "INVEX", "Banco Invex, S.A.", "BIN940223KE0", "SAT/Banxico/RFC publico"),
    ("060", "BANSI", "Bansi, S.A.", "BSI940526D24", "SAT/Banxico/RFC publico"),
    ("062", "AFIRME", "Banca Afirme, S.A.", "BAF950102JP5", "SAT/Banxico/RFC publico"),
    ("072", "BANORTE", "Banco Mercantil del Norte, S.A.", "BMN930209927", "SAT/Banxico/RFC publico"),
    ("102", "AMERICAN EXPRESS", "American Express Bank Mexico, S.A.", "AEB960223JP7", "SAT/Banxico/RFC publico"),
    ("103", "BANK OF AMERICA", "Bank of America Mexico, S.A.", "", "SAT/Banxico"),
    ("106", "BANK OF AMERICA", "Bank of America Mexico, S.A.", "", "SAT/Banxico"),
    ("108", "MUFG", "MUFG Bank Mexico, S.A.", "", "SAT/Banxico"),
    ("110", "JP MORGAN", "Banco J.P. Morgan, S.A.", "", "SAT/Banxico"),
    ("112", "MONEX", "Banco Monex, S.A.", "BMI9704113PA", "SAT/Banxico/RFC publico"),
    ("113", "VE POR MAS", "Banco Ve por Mas, S.A.", "", "SAT/Banxico"),
    ("127", "AZTECA", "Banco Azteca, S.A.", "BAI0205236Y8", "SAT/Banxico/RFC publico"),
    ("128", "AUTOFIN", "Banco Autofin Mexico, S.A.", "BAM0511076B3", "SAT/Banxico/RFC publico"),
    ("129", "BARCLAYS", "Barclays Bank Mexico, S.A.", "", "SAT/Banxico"),
    ("130", "COMPARTAMOS", "Banco Compartamos, S.A.", "", "SAT/Banxico"),
    ("133", "ACTINVER", "Banco Actinver, S.A.", "PBI061115SC6", "SAT/Banxico/RFC publico"),
    ("137", "BANCOPPEL", "BanCoppel, S.A.", "", "SAT/Banxico"),
    ("143", "CIBANCO", "CIBanco, S.A.", "", "SAT/Banxico"),
    ("145", "BBASE", "Banco Base, S.A.", "", "SAT/Banxico"),
    ("147", "BANKAOOL", "Bankaool, S.A.", "", "SAT/Banxico"),
    ("152", "BANCREA", "Banco Bancrea, S.A.", "", "SAT/Banxico"),
    ("154", "BANCO COVALTO", "Banco Covalto, S.A.", "", "SAT/Banxico"),
    ("156", "SABADELL", "Banco Sabadell, S.A.", "", "SAT/Banxico"),
    ("159", "BANK OF CHINA", "Bank of China Mexico, S.A.", "", "SAT/Banxico"),
    ("160", "BANCO S3", "Banco S3 Mexico, S.A.", "", "SAT/Banxico"),
]


def _ensure_tables(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cobranza_recibos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            folio VARCHAR(50) NOT NULL,
            numero_cliente VARCHAR(50) NOT NULL,
            empresa VARCHAR(120) NOT NULL,
            tipo_recibo VARCHAR(20) NOT NULL,
            fecha_recibo DATE NOT NULL,
            monto_total DECIMAL(14,2) NOT NULL DEFAULT 0,
            monto_aplicado DECIMAL(14,2) NOT NULL DEFAULT 0,
            saldo_disponible DECIMAL(14,2) NOT NULL DEFAULT 0,
            forma_pago VARCHAR(5) DEFAULT '',
            nota_credito_no_identificacion VARCHAR(40) DEFAULT '',
            nota_credito_clave_unidad VARCHAR(10) DEFAULT '',
            nota_credito_unidad VARCHAR(20) DEFAULT '',
            nota_credito_descripcion VARCHAR(255) DEFAULT '',
            referencia VARCHAR(120) DEFAULT '',
            observaciones TEXT NULL,
            usuario VARCHAR(120) DEFAULT '',
            estatus VARCHAR(20) NOT NULL DEFAULT 'ACTIVO',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY ux_cobranza_recibos_folio (folio),
            INDEX idx_cobranza_recibos_cliente (numero_cliente, empresa),
            INDEX idx_cobranza_recibos_fecha (fecha_recibo)
        )
        """
    )
    # Instalaciones existentes se actualizan sin requerir una migración manual.
    try:
        cursor.execute("SHOW COLUMNS FROM cobranza_recibos LIKE 'forma_pago'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE cobranza_recibos ADD COLUMN forma_pago VARCHAR(5) DEFAULT '' AFTER saldo_disponible")
    except Exception:
        pass
    for col, ddl in (
        ("nota_credito_no_identificacion", "ALTER TABLE cobranza_recibos ADD COLUMN nota_credito_no_identificacion VARCHAR(40) DEFAULT '' AFTER forma_pago"),
        ("nota_credito_clave_unidad", "ALTER TABLE cobranza_recibos ADD COLUMN nota_credito_clave_unidad VARCHAR(10) DEFAULT '' AFTER nota_credito_no_identificacion"),
        ("nota_credito_unidad", "ALTER TABLE cobranza_recibos ADD COLUMN nota_credito_unidad VARCHAR(20) DEFAULT '' AFTER nota_credito_clave_unidad"),
        ("nota_credito_descripcion", "ALTER TABLE cobranza_recibos ADD COLUMN nota_credito_descripcion VARCHAR(255) DEFAULT '' AFTER nota_credito_unidad"),
    ):
        try:
            cursor.execute(f"SHOW COLUMNS FROM cobranza_recibos LIKE '{col}'")
            if not cursor.fetchone():
                cursor.execute(ddl)
        except Exception:
            pass
    _ensure_indexes(cursor)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cobranza_aplicaciones (
            id INT AUTO_INCREMENT PRIMARY KEY,
            recibo_id INT NOT NULL,
            factura_id INT NOT NULL,
            factura VARCHAR(80) NOT NULL,
            origen_tipo VARCHAR(20) NOT NULL DEFAULT 'FACTURA',
            saldo_inicial_id INT NULL,
            monto_aplicado DECIMAL(14,2) NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_cobranza_aplicaciones_recibo (recibo_id),
            INDEX idx_cobranza_aplicaciones_factura (factura_id),
            CONSTRAINT fk_cobranza_aplicaciones_recibo
                FOREIGN KEY (recibo_id) REFERENCES cobranza_recibos(id)
                ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cobranza_saldos_iniciales (
            id INT AUTO_INCREMENT PRIMARY KEY,
            factura VARCHAR(80) NOT NULL,
            folio_interno VARCHAR(80) DEFAULT '',
            numero_cliente VARCHAR(50) NOT NULL,
            cliente_nombre VARCHAR(255) DEFAULT '',
            empresa VARCHAR(120) NOT NULL,
            fecha_factura DATE NULL,
            fecha_vencimiento DATE NULL,
            dias_credito INT NOT NULL DEFAULT 0,
            total DECIMAL(14,2) NOT NULL DEFAULT 0,
            pagos_iniciales DECIMAL(14,2) NOT NULL DEFAULT 0,
            saldo_inicial DECIMAL(14,2) NOT NULL DEFAULT 0,
            vendedor VARCHAR(255) DEFAULT '',
            xml_nombre VARCHAR(255) DEFAULT '',
            xml_path TEXT NULL,
            uuid VARCHAR(40) DEFAULT '',
            serie_cfdi VARCHAR(40) DEFAULT '',
            folio_cfdi VARCHAR(80) DEFAULT '',
            rfc_receptor VARCHAR(13) DEFAULT '',
            nombre_receptor VARCHAR(255) DEFAULT '',
            cp_receptor VARCHAR(10) DEFAULT '',
            regimen_receptor VARCHAR(5) DEFAULT '',
            moneda_cfdi VARCHAR(10) DEFAULT 'MXN',
            observaciones TEXT NULL,
            usuario VARCHAR(120) DEFAULT '',
            estatus VARCHAR(20) NOT NULL DEFAULT 'ACTIVO',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_saldos_empresa_cliente (empresa, numero_cliente),
            INDEX idx_saldos_factura (factura),
            INDEX idx_saldos_vencimiento (fecha_vencimiento)
        )
        """
    )
    try:
        cursor.execute("SHOW COLUMNS FROM cobranza_saldos_iniciales")
        columnas_si = {str((row[0] if not isinstance(row, dict) else row.get("Field")) or "").lower() for row in cursor.fetchall()}
        for col, ddl in [
            ("folio_interno", "ALTER TABLE cobranza_saldos_iniciales ADD COLUMN folio_interno VARCHAR(80) DEFAULT '' AFTER factura"),
            ("xml_nombre", "ALTER TABLE cobranza_saldos_iniciales ADD COLUMN xml_nombre VARCHAR(255) DEFAULT '' AFTER vendedor"),
            ("xml_path", "ALTER TABLE cobranza_saldos_iniciales ADD COLUMN xml_path TEXT NULL AFTER xml_nombre"),
            ("uuid", "ALTER TABLE cobranza_saldos_iniciales ADD COLUMN uuid VARCHAR(40) DEFAULT '' AFTER xml_path"),
            ("serie_cfdi", "ALTER TABLE cobranza_saldos_iniciales ADD COLUMN serie_cfdi VARCHAR(40) DEFAULT '' AFTER uuid"),
            ("folio_cfdi", "ALTER TABLE cobranza_saldos_iniciales ADD COLUMN folio_cfdi VARCHAR(80) DEFAULT '' AFTER serie_cfdi"),
            ("rfc_receptor", "ALTER TABLE cobranza_saldos_iniciales ADD COLUMN rfc_receptor VARCHAR(13) DEFAULT '' AFTER folio_cfdi"),
            ("nombre_receptor", "ALTER TABLE cobranza_saldos_iniciales ADD COLUMN nombre_receptor VARCHAR(255) DEFAULT '' AFTER rfc_receptor"),
            ("cp_receptor", "ALTER TABLE cobranza_saldos_iniciales ADD COLUMN cp_receptor VARCHAR(10) DEFAULT '' AFTER nombre_receptor"),
            ("regimen_receptor", "ALTER TABLE cobranza_saldos_iniciales ADD COLUMN regimen_receptor VARCHAR(5) DEFAULT '' AFTER cp_receptor"),
            ("moneda_cfdi", "ALTER TABLE cobranza_saldos_iniciales ADD COLUMN moneda_cfdi VARCHAR(10) DEFAULT 'MXN' AFTER regimen_receptor"),
        ]:
            if col not in columnas_si:
                cursor.execute(ddl)
    except Exception:
        pass
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cobranza_config (
            id INT AUTO_INCREMENT PRIMARY KEY,
            empresa VARCHAR(120) NOT NULL,
            fecha_inicio_facturas DATE NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY ux_cobranza_config_empresa (empresa)
        )
        """
    )
    cursor.execute(
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
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS catalogo_bancos_mexico (
            id INT AUTO_INCREMENT PRIMARY KEY,
            clave VARCHAR(10) NOT NULL,
            nombre_corto VARCHAR(120) NOT NULL,
            razon_social VARCHAR(255) NOT NULL DEFAULT '',
            rfc VARCHAR(13) NOT NULL DEFAULT '',
            fuente VARCHAR(120) NOT NULL DEFAULT '',
            activo TINYINT(1) NOT NULL DEFAULT 1,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY ux_catalogo_bancos_clave (clave)
        )
        """
    )
    try:
        cursor.executemany(
            """INSERT INTO catalogo_bancos_mexico
               (clave, nombre_corto, razon_social, rfc, fuente, activo)
               VALUES (%s, %s, %s, %s, %s, 1)
               ON DUPLICATE KEY UPDATE
                   nombre_corto = VALUES(nombre_corto),
                   razon_social = VALUES(razon_social),
                   rfc = CASE WHEN catalogo_bancos_mexico.rfc = '' THEN VALUES(rfc) ELSE catalogo_bancos_mexico.rfc END,
                   fuente = VALUES(fuente),
                   activo = 1""",
            BANCOS_MEXICO_SEED,
        )
    except Exception:
        pass
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS para_sergio_config (
            id INT AUTO_INCREMENT PRIMARY KEY,
            tipo_map JSON NOT NULL,
            categorias_orden JSON NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS modelos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nombre VARCHAR(255) NOT NULL,
            descripcion VARCHAR(500) DEFAULT '',
            componentes JSON NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """
    )


def _ensure_indexes(cursor):
    _ix = [
        ("cobranza_recibos", "idx_cr_est_tipo_fecha", "CREATE INDEX idx_cr_est_tipo_fecha ON cobranza_recibos (estatus, tipo_recibo, fecha_recibo)"),
        ("cobranza_recibos", "idx_cr_empresa_fecha", "CREATE INDEX idx_cr_empresa_fecha ON cobranza_recibos (empresa, fecha_recibo)"),
        ("cobranza_aplicaciones", "idx_ca_recibo_factura_monto", "CREATE INDEX idx_ca_recibo_factura_monto ON cobranza_aplicaciones (recibo_id, factura_id, monto_aplicado)"),
        ("cobranza_aplicaciones", "idx_ca_factura_origen", "CREATE INDEX idx_ca_factura_origen ON cobranza_aplicaciones (factura_id, origen_tipo, monto_aplicado)"),
        ("cobranza_aplicaciones", "idx_ca_saldo_origen", "CREATE INDEX idx_ca_saldo_origen ON cobranza_aplicaciones (saldo_inicial_id, origen_tipo)"),
        ("clientes", "idx_clientes_empresa_numero", "CREATE INDEX idx_clientes_empresa_numero ON clientes (empresa, numero)"),
        ("clientes", "idx_clientes_empresa_nombre", "CREATE INDEX idx_clientes_empresa_nombre ON clientes (empresa, nombre)"),
    ]
    for tabla, nombre, sql in _ix:
        try:
            cursor.execute(f"SELECT 1 FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s LIMIT 1", (tabla, nombre))
            if not cursor.fetchone():
                cursor.execute(sql)
        except Exception:
            pass


def _pagos_por_documento(cursor):
    pagos_facturas = {}
    pagos_saldos = {}
    cursor.execute(
        """
        SELECT
            ca.factura_id,
            ca.saldo_inicial_id,
            COALESCE(ca.origen_tipo, 'FACTURA') AS origen_tipo,
            COALESCE(SUM(ca.monto_aplicado), 0) AS pagado
        FROM cobranza_aplicaciones ca
        INNER JOIN cobranza_recibos cr ON cr.id = ca.recibo_id
        WHERE cr.estatus = 'ACTIVO'
        GROUP BY ca.factura_id, ca.saldo_inicial_id, ca.origen_tipo
        """
    )
    for row in _dict_rows(cursor):
        origen = str(row.get("origen_tipo") or "FACTURA").strip().upper()
        if origen == "SALDO_INICIAL" or row.get("saldo_inicial_id"):
            saldo_id = int(row.get("saldo_inicial_id") or abs(int(row.get("factura_id") or 0)) or 0)
            if saldo_id > 0:
                pagos_saldos[saldo_id] = _to_float(row.get("pagado"))
        else:
            factura_id = int(row.get("factura_id") or 0)
            if factura_id > 0:
                pagos_facturas[factura_id] = _to_float(row.get("pagado"))
    return pagos_facturas, pagos_saldos


def _fecha_inicio_por_empresa(cursor):
    cursor.execute("SELECT empresa, fecha_inicio_facturas FROM cobranza_config")
    return {
        str(row["empresa"] or "").strip().upper(): row["fecha_inicio_facturas"]
        for row in _dict_rows(cursor)
        if row.get("empresa") and row.get("fecha_inicio_facturas")
    }


def _consultar_cartera(cursor, conn, empresa=None, numero_cliente=None, documento=None, desde=None, hasta=None, cadena_id=None):
    sql = """
        SELECT
            f.id, f.factura, f.numero_cliente,
            COALESCE(
                c.nombre,
                CASE
                    WHEN UPPER(TRIM(f.empresa)) IN ('REMISION', 'REMISIÓN') THEN COALESCE(c_eza.nombre, c_ibe.nombre)
                    ELSE NULL
                END,
                f.consignatario,
                ''
            ) AS cliente_nombre,
            f.empresa, DATE(f.fecha) AS fecha,
            COALESCE(
                c.dias_credito,
                CASE
                    WHEN UPPER(TRIM(f.empresa)) IN ('REMISION', 'REMISIÓN') THEN COALESCE(c_eza.dias_credito, c_ibe.dias_credito)
                    ELSE NULL
                END,
                0
            ) AS dias_credito,
            DATE_ADD(
                DATE(f.fecha),
                INTERVAL COALESCE(
                    c.dias_credito,
                    CASE
                        WHEN UPPER(TRIM(f.empresa)) IN ('REMISION', 'REMISIÓN') THEN COALESCE(c_eza.dias_credito, c_ibe.dias_credito)
                        ELSE NULL
                    END,
                    0
                ) DAY
            ) AS fecha_vencimiento,
            f.total,
            COALESCE(
                c.vendedor,
                CASE
                    WHEN UPPER(TRIM(f.empresa)) IN ('REMISION', 'REMISIÓN') THEN COALESCE(c_eza.vendedor, c_ibe.vendedor)
                    ELSE NULL
                END,
                ''
            ) AS vendedor
        FROM facturas f
        LEFT JOIN clientes c
            ON TRIM(CAST(c.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR))
            AND (
                UPPER(TRIM(c.empresa)) = UPPER(TRIM(f.empresa))
                OR (
                    UPPER(TRIM(f.empresa)) = 'GOURMET_ESPANA'
                    AND UPPER(TRIM(c.empresa)) LIKE 'GOURMET%'
                )
            )
        LEFT JOIN clientes c_eza
            ON TRIM(CAST(c_eza.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR))
            AND UPPER(TRIM(c_eza.empresa)) = 'EZA2007'
        LEFT JOIN clientes c_ibe
            ON TRIM(CAST(c_ibe.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR))
            AND UPPER(TRIM(c_ibe.empresa)) = 'IBERSUR'
        WHERE f.estatus = 'Activa'
    """
    params = []
    if empresa and str(empresa).strip().lower() not in ("", "todas"):
        sql += " AND UPPER(REPLACE(TRIM(f.empresa), '_', ' ')) = UPPER(REPLACE(TRIM(%s), '_', ' '))"
        params.append(str(empresa).strip())
    if cadena_id:
        sql += """
            AND EXISTS (
                SELECT 1
                FROM cadenas_clientes cc
                WHERE cc.cadena_id = %s
                  AND TRIM(CAST(cc.cliente_numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR))
                  AND UPPER(TRIM(cc.empresa)) = UPPER(TRIM(f.empresa))
            )
        """
        params.append(int(cadena_id))
    if numero_cliente:
        sql += " AND TRIM(CAST(f.numero_cliente AS CHAR)) = TRIM(CAST(%s AS CHAR))"
        params.append(str(numero_cliente).strip())
    if documento:
        sql += " AND UPPER(TRIM(f.factura)) LIKE UPPER(%s)"
        params.append(f"%{str(documento).strip()}%")
    else:
        if desde:
            sql += " AND DATE(f.fecha) >= %s"
            params.append(desde)
        if hasta:
            sql += " AND DATE(f.fecha) <= %s"
            params.append(hasta)
    sql += " ORDER BY f.fecha DESC, f.id DESC"
    cursor.execute(sql, params)
    rows = _dict_rows(cursor)

    # Los saldos iniciales son la fotografía real de cartera al iniciar el
    # módulo. Cuando traen un folio interno que ya existe en `facturas`, no se
    # deben agregar ambos importes: el saldo inicial sustituye a esa factura
    # para evitar duplicar total, saldo y métricas.
    params_si = []
    if empresa and str(empresa).strip().lower() not in ("", "todas"):
        sql_si_where = " AND UPPER(REPLACE(TRIM(empresa), '_', ' ')) = UPPER(REPLACE(TRIM(%s), '_', ' '))"
        params_si.append(str(empresa).strip())
    else:
        sql_si_where = ""
    cursor.execute(
        "SELECT id, factura, folio_interno, numero_cliente, cliente_nombre, empresa, fecha_factura AS fecha, dias_credito, fecha_vencimiento, total, pagos_iniciales, saldo_inicial, vendedor FROM cobranza_saldos_iniciales WHERE estatus = 'ACTIVO' " + sql_si_where,
        params_si,
    )
    saldos_iniciales = _dict_rows(cursor)
    folios_saldo_inicial = {
        (_empresa_cadena_clave(row.get("empresa")), str(row.get("folio_interno") or row.get("factura") or "").strip().upper())
        for row in saldos_iniciales
        if str(row.get("folio_interno") or row.get("factura") or "").strip()
    }

    pagos_facturas, pagos_saldos = _pagos_por_documento(cursor)
    fechas_inicio = _fecha_inicio_por_empresa(cursor)
    hoy = datetime.now().date()
    cartera = []

    for row in rows:
        llave_factura = (_empresa_cadena_clave(row.get("empresa")), str(row.get("factura") or "").strip().upper())
        if llave_factura in folios_saldo_inicial:
            continue
        empresa_row = str(row.get("empresa") or "").strip().upper()
        fecha_inicio = fechas_inicio.get(empresa_row)
        fecha_factura = row.get("fecha")
        if fecha_inicio and fecha_factura and fecha_factura <= fecha_inicio:
            continue
        factura_id = int(row["id"])
        total = _to_float(row.get("total"))
        pagos = round(_to_float(pagos_facturas.get(factura_id, 0.0)), 2)
        saldo = round(total - pagos, 2)
        fecha_vencimiento = row.get("fecha_vencimiento")
        dias_vencido = 0
        estatus_cobranza = "AL_CORRIENTE"
        if saldo <= 0.009:
            saldo = 0.0
            estatus_cobranza = "PAGADA"
        elif fecha_vencimiento:
            dias_vencido = (hoy - fecha_vencimiento).days
            if dias_vencido > 0:
                estatus_cobranza = "VENCIDA"
            elif dias_vencido == 0:
                estatus_cobranza = "VENCE_HOY"
            else:
                estatus_cobranza = "POR_VENCER"

        cartera.append({
            **row,
            "origen": "FACTURA",
            "total": total,
            "pagos_aplicados": pagos,
            "saldo": saldo,
            "dias_vencido": max(dias_vencido, 0) if saldo > 0 else 0,
            "estatus_cobranza": estatus_cobranza,
        })

    for row in saldos_iniciales:
        saldo_id = int(row["id"])
        total = _to_float(row.get("total"))
        pagos_base = _to_float(row.get("pagos_iniciales", 0.0))
        pagos_aplicados = _to_float(pagos_saldos.get(saldo_id, 0.0))
        pagos = round(pagos_base + pagos_aplicados, 2)
        saldo_base = _to_float(row.get("saldo_inicial"))
        if saldo_base > 0:
            saldo = round(max(saldo_base - pagos, 0.0), 2)
        else:
            saldo = round(max(total - pagos, 0.0), 2)
        fecha_vencimiento = row.get("fecha_vencimiento")
        dias_vencido = 0
        estatus_cobranza = "AL_CORRIENTE"
        if saldo <= 0.009:
            saldo = 0.0
            estatus_cobranza = "PAGADA"
        elif fecha_vencimiento:
            dias_vencido = (hoy - fecha_vencimiento).days
            if dias_vencido > 0:
                estatus_cobranza = "VENCIDA"
            elif dias_vencido == 0:
                estatus_cobranza = "VENCE_HOY"
            else:
                estatus_cobranza = "POR_VENCER"
        cartera.append({
            **row,
            "id": -saldo_id,
            "saldo_inicial_id": saldo_id,
            "origen": "SALDO_INICIAL",
            "total": total,
            "pagos_aplicados": pagos,
            "saldo": saldo,
            "dias_vencido": max(dias_vencido, 0) if saldo > 0 else 0,
            "estatus_cobranza": estatus_cobranza,
        })

    cartera.sort(key=lambda x: (0 if str(x.get("origen") or "").upper() == "SALDO_INICIAL" else 1, str(x.get("fecha") or ""), str(x.get("factura") or "")))
    return cartera


def _generar_folio_recibo(cursor, tipo_recibo, fecha_recibo):
    tipo_upper = str(tipo_recibo).upper()
    prefijo = {"ANTICIPO": "ANT", "NOTA_CREDITO": "NCR"}.get(tipo_upper, "PAG")
    fecha_str = str(fecha_recibo).replace("-", "")
    cursor.execute(
        "SELECT COUNT(*) + 1 FROM cobranza_recibos WHERE fecha_recibo = %s AND tipo_recibo = %s",
        (fecha_recibo, tipo_recibo),
    )
    row = cursor.fetchone()
    consecutivo = int(row[0] if row else 1 or 1)
    return f"{prefijo}-{fecha_str}-{consecutivo:04d}"


def _normalizar_tipo_cuenta_banco(tipo: str) -> str:
    tipo_norm = str(tipo or "").strip().upper()
    if tipo_norm in ("ORDENANTE", "CUENTA_ORDENANTE"):
        return "ORDENANTE"
    if tipo_norm in ("BENEFICIARIO", "BENEFICIARIA", "CUENTA_BENEFICIARIO"):
        return "BENEFICIARIO"
    raise HTTPException(status_code=400, detail="Tipo de cuenta invalido.")


def _payload_cuenta_banco(payload: dict) -> dict:
    tipo = _normalizar_tipo_cuenta_banco(payload.get("tipo"))
    empresa = str(payload.get("empresa") or "").strip()
    numero_cliente = str(payload.get("numero_cliente") or "").strip()
    cliente_nombre = str(payload.get("cliente_nombre") or "").strip()
    banco_nombre = str(payload.get("banco_nombre") or payload.get("banco") or "").strip()
    rfc_banco = str(payload.get("rfc_banco") or "").strip().upper()
    cuenta = str(payload.get("cuenta") or payload.get("clabe") or "").strip()
    alias = str(payload.get("alias") or "").strip()
    activa = 1 if payload.get("activa", True) else 0
    if tipo == "ORDENANTE" and not numero_cliente:
        raise HTTPException(status_code=400, detail="Captura el cliente para la cuenta ordenante.")
    if tipo == "BENEFICIARIO" and not empresa:
        raise HTTPException(status_code=400, detail="Selecciona la empresa para la cuenta beneficiaria.")
    if not banco_nombre:
        raise HTTPException(status_code=400, detail="Captura el banco.")
    if not cuenta:
        raise HTTPException(status_code=400, detail="Captura la cuenta o CLABE.")
    return {
        "tipo": tipo,
        "empresa": empresa,
        "numero_cliente": numero_cliente,
        "cliente_nombre": cliente_nombre,
        "banco_nombre": banco_nombre,
        "rfc_banco": rfc_banco,
        "cuenta": cuenta,
        "alias": alias,
        "activa": activa,
    }


def _obtener_cartera(cursor, conn, empresa=None, cadena_id=None, numero_cliente=None, documento=None, desde=None, hasta=None):
    global _cartera_cache_data, _cartera_cache_built_at, _cartera_cache_stale, _cartera_cache_building, _cartera_cache_error
    if _cc_valid():
        cached = _cc_filter(empresa=empresa, cadena_id=cadena_id, numero_cliente=numero_cliente, documento=documento, desde=desde, hasta=hasta)
        if cached is not None:
            return cached
    if _cartera_cache_building:
        return _consultar_cartera(cursor, conn, empresa=empresa, cadena_id=cadena_id, numero_cliente=numero_cliente, documento=documento, desde=desde, hasta=hasta)
    _cartera_cache_building = True
    _cartera_cache_error = None
    try:
        data = _consultar_cartera(cursor, conn)
        with _cartera_cache_lock:
            _cartera_cache_data = data
            _cartera_cache_built_at = datetime.now()
            _cartera_cache_stale = False
        result = _cc_filter(empresa=empresa, cadena_id=cadena_id, numero_cliente=numero_cliente, documento=documento, desde=desde, hasta=hasta)
        return result if result is not None else data
    except Exception as exc:
        _cartera_cache_error = str(exc)
        return _consultar_cartera(cursor, conn, empresa=empresa, cadena_id=cadena_id, numero_cliente=numero_cliente, documento=documento, desde=desde, hasta=hasta)
    finally:
        _cartera_cache_building = False


@router.get("/cache-status")
def cache_status(user=Depends(require_user)):
    remaining = 0
    if _cartera_cache_built_at:
        remaining = round(max((CARTERA_CACHE_TTL - (datetime.now() - _cartera_cache_built_at)).total_seconds() / 60, 0), 1)
    return {
        "ok": True,
        "valid": _cc_valid(),
        "stale": _cartera_cache_stale,
        "building": _cartera_cache_building,
        "count": len(_cartera_cache_data) if _cartera_cache_data else 0,
        "built_at": _cartera_cache_built_at.isoformat() if _cartera_cache_built_at else None,
        "ttl_minutes": CARTERA_CACHE_TTL.total_seconds() / 60,
        "remaining_minutes": remaining,
        "error": _cartera_cache_error,
    }


@router.post("/rebuild-cache")
def rebuild_cache(empresa: str | None = Query(None), user=Depends(require_user)):
    global _cartera_cache_data, _cartera_cache_built_at, _cartera_cache_building, _cartera_cache_error, _cartera_cache_stale
    if _cartera_cache_building:
        return {"ok": False, "status": "building", "message": "La cache ya se esta reconstruyendo"}
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        _cartera_cache_building = True
        _cartera_cache_error = None
        try:
            data = _consultar_cartera(cursor, conn, empresa=empresa)
            with _cartera_cache_lock:
                _cartera_cache_data = data
                _cartera_cache_built_at = datetime.now()
                _cartera_cache_stale = False
            return {"ok": True, "count": len(data), "built_at": _cartera_cache_built_at.isoformat()}
        except Exception as exc:
            _cartera_cache_error = str(exc)
            raise
        finally:
            _cartera_cache_building = False
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try: cursor.close()
            except: pass
        if conn:
            try: conn.close()
            except: pass


@router.get("/summary")
def collections_summary(
    empresa: str | None = None,
    cadena_id: int | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    user=Depends(require_user),
):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cartera = _obtener_cartera(cursor, conn, empresa=empresa, cadena_id=cadena_id, desde=desde, hasta=hasta)
        hoy = datetime.now().date()

        total_facturado = round(sum(_to_float(x["total"]) for x in cartera), 2)
        saldo_total = round(sum(_to_float(x["saldo"]) for x in cartera), 2)
        saldo_vencido = round(sum(_to_float(x["saldo"]) for x in cartera if x["saldo"] > 0 and x.get("fecha_vencimiento") and x["fecha_vencimiento"] < hoy), 2)
        proximos = round(sum(_to_float(x["saldo"]) for x in cartera if x["saldo"] > 0 and x.get("fecha_vencimiento") and 0 <= (x["fecha_vencimiento"] - hoy).days <= 7), 2)
        morosos_count = len([x for x in cartera if x["saldo"] > 0 and x.get("fecha_vencimiento") and x["fecha_vencimiento"] < hoy])

        cursor.execute(
            """
            SELECT COALESCE(SUM(saldo_disponible), 0) AS anticipos
            FROM cobranza_recibos
            WHERE estatus = 'ACTIVO' AND tipo_recibo = 'ANTICIPO'
            """
        )
        row = cursor.fetchone()
        anticipos = round(_to_float(row[0] if row else 0), 2)

        cursor.execute(
            """
            SELECT COALESCE(SUM(monto_total), 0) AS total_cobrado
            FROM cobranza_recibos
            WHERE estatus = 'ACTIVO' AND tipo_recibo IN ('PAGO', 'ANTICIPO')
            """
        )
        row = cursor.fetchone()
        total_cobrado = round(_to_float(row[0] if row else 0), 2)

        return {
            "total_facturado": total_facturado,
            "total_cobrado": total_cobrado,
            "saldo_total": saldo_total,
            "saldo_vencido": saldo_vencido,
            "proximos_7_dias": proximos,
            "morosos": morosos_count,
            "anticipos_disponibles": anticipos,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/portfolio")
def collections_portfolio(
    empresa: str | None = None,
    cadena_id: int | None = None,
    numero_cliente: str | None = None,
    documento: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    estatus: str | None = None,
    user=Depends(require_user),
):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cartera = _obtener_cartera(cursor, conn, empresa=empresa, cadena_id=cadena_id, numero_cliente=numero_cliente, documento=documento, desde=desde, hasta=hasta)
        if estatus:
            estatus_filtro = str(estatus).strip().upper()
            if estatus_filtro == "VENCIDAS":
                hoy = datetime.now().date()
                cartera = [x for x in cartera if x["saldo"] > 0 and x.get("fecha_vencimiento") and x["fecha_vencimiento"] < hoy]
            elif estatus_filtro == "PAGADAS":
                cartera = [x for x in cartera if x["saldo"] <= 0]
            elif estatus_filtro == "POR_VENCER":
                cartera = [x for x in cartera if x["saldo"] > 0 and x["estatus_cobranza"] == "POR_VENCER"]
        return cartera
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/pending-invoices")
def pending_invoices(
    empresa: str = Query(...),
    numero_cliente: str = Query(...),
    user=Depends(require_user),
):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute(
            """
            SELECT f.id, f.factura, f.numero_cliente,
                   COALESCE(c.nombre, f.consignatario, '') AS cliente_nombre,
                   f.empresa, DATE(f.fecha) AS fecha, COALESCE(c.dias_credito, 0) AS dias_credito,
                   DATE_ADD(DATE(f.fecha), INTERVAL COALESCE(c.dias_credito, 0) DAY) AS fecha_vencimiento,
                   f.total
            FROM facturas f
            LEFT JOIN clientes c
                ON c.numero = f.numero_cliente AND c.empresa = f.empresa
            WHERE f.estatus = 'Activa'
              AND f.empresa = %s
              AND f.numero_cliente = %s
            ORDER BY f.fecha DESC, f.id DESC
            """,
            (str(empresa).strip(), str(numero_cliente).strip()),
        )
        facturas = _dict_rows(cursor)
        if not facturas:
            return []
        fids = [int(r["id"]) for r in facturas]
        placeholders = ",".join(["%s"] * len(fids))
        cursor.execute(
            f"""
            SELECT ca.factura_id, COALESCE(SUM(ca.monto_aplicado), 0) AS pagado
            FROM cobranza_aplicaciones ca
            INNER JOIN cobranza_recibos cr ON cr.id = ca.recibo_id AND cr.estatus = 'ACTIVO'
            WHERE ca.factura_id IN ({placeholders})
            GROUP BY ca.factura_id
            """,
            tuple(fids),
        )
        pagos_map = {int(r["factura_id"]): _to_float(r["pagado"]) for r in _dict_rows(cursor)}
        hoy = datetime.now().date()
        result = []
        for row in facturas:
            fid = int(row["id"])
            total = _to_float(row.get("total"))
            pagado = round(pagos_map.get(fid, 0.0), 2)
            saldo = round(total - pagado, 2)
            if saldo <= 0.009:
                continue
            fv = row.get("fecha_vencimiento")
            dias_vencido = (hoy - fv).days if fv else 0
            estatus = "VENCIDA" if dias_vencido > 0 else "VENCE_HOY" if dias_vencido == 0 else "POR_VENCER"
            result.append({
                "id": fid,
                "factura": row["factura"],
                "numero_cliente": row["numero_cliente"],
                "cliente_nombre": row.get("cliente_nombre", ""),
                "empresa": row["empresa"],
                "fecha": row["fecha"],
                "fecha_vencimiento": fv,
                "total": total,
                "pagos_aplicados": pagado,
                "saldo": saldo,
                "dias_vencido": max(dias_vencido, 0),
                "estatus_cobranza": estatus,
            })
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass

@router.get("/cadenas")
def list_cadenas(user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT c.id, c.nombre FROM cadenas c ORDER BY COALESCE(c.activa_cobranza, 0) DESC, c.nombre"
        )
        return _dict_rows(cursor)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/client-name/{numero_cliente}")
def client_name_lookup(numero_cliente: str, empresa: str = Query(...), user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute("""
            SELECT COALESCE(
                c.nombre,
                c_eza.nombre,
                c_ibe.nombre,
                ''
            ) AS nombre
            FROM (SELECT %s AS num) dummy
            LEFT JOIN clientes c
                ON TRIM(CAST(c.numero AS CHAR)) = TRIM(CAST(%s AS CHAR))
                AND UPPER(TRIM(c.empresa)) = UPPER(TRIM(%s))
            LEFT JOIN clientes c_eza
                ON TRIM(CAST(c_eza.numero AS CHAR)) = TRIM(CAST(%s AS CHAR))
                AND UPPER(TRIM(c_eza.empresa)) = 'EZA2007'
            LEFT JOIN clientes c_ibe
                ON TRIM(CAST(c_ibe.numero AS CHAR)) = TRIM(CAST(%s AS CHAR))
                AND UPPER(TRIM(c_ibe.empresa)) = 'IBERSUR'
            LIMIT 1
        """, (numero_cliente, numero_cliente, empresa, numero_cliente, numero_cliente))
        row = cursor.fetchone()
        nombre = str(row[0]).strip() if row and row[0] else ""
        return {"nombre": nombre, "numero_cliente": numero_cliente, "empresa": empresa}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try: cursor.close()
            except: pass
        if conn:
            try: conn.close()
            except: pass

@router.get("/client-search")
def client_search(q: str = Query(...), empresa: str = Query(...), user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        texto = str(q or "").strip()
        if not texto:
            return []
        like_any = f"%{texto}%"
        like_prefix = f"{texto}%"
        es_numero = texto.isdigit()
        where_busqueda = "TRIM(CAST(numero AS CHAR)) LIKE %s" if es_numero else "UPPER(TRIM(nombre)) LIKE UPPER(TRIM(%s))"
        busqueda_param = like_prefix if es_numero else like_any
        cursor.execute("""
            SELECT numero, nombre, empresa
            FROM clientes
            WHERE """ + where_busqueda + """
              AND UPPER(TRIM(empresa)) = UPPER(TRIM(%s))
            ORDER BY
              CASE WHEN UPPER(TRIM(numero)) = UPPER(TRIM(%s)) THEN 0
                   WHEN UPPER(TRIM(numero)) LIKE UPPER(TRIM(%s)) THEN 1
                   WHEN UPPER(TRIM(nombre)) LIKE UPPER(TRIM(%s)) THEN 2
                   ELSE 2 END,
              nombre
            LIMIT 20
        """, (busqueda_param, empresa, texto, like_prefix, like_prefix))
        rows = cursor.fetchall()
        return [{"numero": str(r[0] or "").strip(), "nombre": str(r[1] or "").strip(), "empresa": str(r[2] or "").strip()} for r in rows]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try: cursor.close()
            except: pass
        if conn:
            try: conn.close()
            except: pass

@router.get("/customer/{numero_cliente}")
def customer_statement(numero_cliente: str, empresa: str = Query(...), user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cartera = _obtener_cartera(cursor, conn, empresa=empresa, numero_cliente=numero_cliente)
        cursor.execute(
            """
            SELECT id, folio, numero_cliente, empresa, tipo_recibo, fecha_recibo,
                   monto_total, monto_aplicado, saldo_disponible, referencia, usuario, estatus
            FROM cobranza_recibos
            WHERE numero_cliente = %s
              AND empresa = %s
              AND estatus = 'ACTIVO'
            ORDER BY fecha_recibo DESC, id DESC
            """,
            (str(numero_cliente).strip(), str(empresa).strip()),
        )
        recibos = _anexar_cfdi_cobranza(_dict_rows(cursor))
        return {
            "cliente": numero_cliente,
            "empresa": empresa,
            "facturas": cartera,
            "recibos": recibos,
            "resumen": {
                "total_adeudo": round(sum(_to_float(x["saldo"]) for x in cartera), 2),
                "total_vencido": round(sum(_to_float(x["saldo"]) for x in cartera if x["estatus_cobranza"] == "VENCIDA"), 2),
                "facturas_pendientes": len([x for x in cartera if _to_float(x["saldo"]) > 0]),
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/catalogo-bancos")
def listar_catalogo_bancos(q: str | None = None, limit: int = 200, user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        sql = """
            SELECT clave, nombre_corto, razon_social, rfc, fuente, activo
            FROM catalogo_bancos_mexico
            WHERE activo = 1
        """
        params = []
        texto = str(q or "").strip()
        if texto:
            like = f"%{texto}%"
            sql += " AND (clave LIKE %s OR nombre_corto LIKE %s OR razon_social LIKE %s OR rfc LIKE %s)"
            params.extend([like, like, like, like])
        sql += " ORDER BY nombre_corto LIMIT %s"
        params.append(max(1, min(int(limit or 200), 500)))
        cursor.execute(sql, params)
        return _dict_rows(cursor)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/cuentas-bancarias")
def listar_cuentas_bancarias(
    tipo: str | None = None,
    empresa: str | None = None,
    numero_cliente: str | None = None,
    user=Depends(require_user),
):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        sql = "SELECT * FROM cobranza_cuentas_bancarias WHERE 1=1"
        params = []
        if tipo:
            sql += " AND tipo = %s"
            params.append(_normalizar_tipo_cuenta_banco(tipo))
        if empresa:
            sql += " AND UPPER(TRIM(empresa)) = UPPER(TRIM(%s))"
            params.append(str(empresa).strip())
        if numero_cliente:
            sql += " AND TRIM(numero_cliente) = TRIM(%s)"
            params.append(str(numero_cliente).strip())
        sql += " ORDER BY tipo, empresa, numero_cliente, activa DESC, banco_nombre"
        cursor.execute(sql, params)
        return _dict_rows(cursor)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.post("/cuentas-bancarias", status_code=201)
def crear_cuenta_bancaria(payload: dict, user=Depends(require_user)):
    conn = cursor = None
    try:
        data = _payload_cuenta_banco(payload)
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute(
            """INSERT INTO cobranza_cuentas_bancarias
               (tipo, empresa, numero_cliente, cliente_nombre, banco_nombre, rfc_banco, cuenta, alias, activa)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (data["tipo"], data["empresa"], data["numero_cliente"], data["cliente_nombre"],
             data["banco_nombre"], data["rfc_banco"], data["cuenta"], data["alias"], data["activa"]),
        )
        conn.commit()
        return {"id": cursor.lastrowid, **data}
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as exc:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.put("/cuentas-bancarias/{cuenta_id}")
def actualizar_cuenta_bancaria(cuenta_id: int, payload: dict, user=Depends(require_user)):
    conn = cursor = None
    try:
        data = _payload_cuenta_banco(payload)
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute("SELECT id FROM cobranza_cuentas_bancarias WHERE id = %s LIMIT 1", (cuenta_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Cuenta bancaria no encontrada.")
        cursor.execute(
            """UPDATE cobranza_cuentas_bancarias
               SET tipo=%s, empresa=%s, numero_cliente=%s, cliente_nombre=%s, banco_nombre=%s,
                   rfc_banco=%s, cuenta=%s, alias=%s, activa=%s
               WHERE id=%s""",
            (data["tipo"], data["empresa"], data["numero_cliente"], data["cliente_nombre"],
             data["banco_nombre"], data["rfc_banco"], data["cuenta"], data["alias"], data["activa"], cuenta_id),
        )
        conn.commit()
        return {"id": cuenta_id, **data}
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as exc:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.delete("/cuentas-bancarias/{cuenta_id}")
def eliminar_cuenta_bancaria(cuenta_id: int, user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute("DELETE FROM cobranza_cuentas_bancarias WHERE id = %s", (cuenta_id,))
        conn.commit()
        return {"ok": True}
    except Exception as exc:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/receipts")
def list_receipts(
    empresa: str | None = None,
    numero_cliente: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    tipo: str | None = None,
    user=Depends(require_user),
):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        sql = """
            SELECT id, folio, numero_cliente, empresa, tipo_recibo, fecha_recibo,
                   monto_total, monto_aplicado, saldo_disponible, forma_pago, referencia, usuario, estatus, created_at
            FROM cobranza_recibos
            WHERE estatus = 'ACTIVO'
        """
        params = []
        if empresa and str(empresa).strip().lower() not in ("", "todas"):
            sql += " AND empresa = %s"
            params.append(str(empresa).strip())
        if numero_cliente:
            sql += " AND numero_cliente = %s"
            params.append(str(numero_cliente).strip())
        if desde:
            sql += " AND fecha_recibo >= %s"
            params.append(desde)
        if hasta:
            sql += " AND fecha_recibo <= %s"
            params.append(hasta)
        tipo_txt = str(tipo or "").strip().upper()
        if tipo_txt:
            if tipo_txt == "PAGOS":
                sql += " AND tipo_recibo IN ('PAGO', 'ANTICIPO')"
            else:
                sql += " AND tipo_recibo = %s"
                params.append(tipo_txt)
        sql += " ORDER BY fecha_recibo DESC, id DESC"
        cursor.execute(sql, params)
        return _anexar_cfdi_cobranza(_dict_rows(cursor))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/receipts/{recibo_id}")
def receipt_detail(recibo_id: int, user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute("SELECT * FROM cobranza_recibos WHERE id = %s LIMIT 1", (recibo_id,))
        recibo = _dict_rows(cursor)
        if not recibo:
            raise HTTPException(status_code=404, detail="Recibo no encontrado")
        recibo = recibo[0]
        cursor.execute(
            """
            SELECT ca.factura_id, ca.factura, ca.monto_aplicado, ca.origen_tipo, ca.saldo_inicial_id,
                   ROUND(f.total, 2) AS factura_total,
                   COALESCE(ROUND(f.total - COALESCE(pg.pagado, 0), 2), 0) AS saldo_pendiente
            FROM cobranza_aplicaciones ca
            LEFT JOIN facturas f ON ca.factura_id = f.id
            LEFT JOIN (
                SELECT ca2.factura_id, SUM(ca2.monto_aplicado) AS pagado
                FROM cobranza_aplicaciones ca2
                INNER JOIN cobranza_recibos cr2 ON cr2.id = ca2.recibo_id AND cr2.estatus = 'ACTIVO'
                WHERE ca2.factura_id IN (
                    SELECT DISTINCT ca3.factura_id FROM cobranza_aplicaciones ca3 WHERE ca3.recibo_id = %s
                )
                GROUP BY ca2.factura_id
            ) pg ON pg.factura_id = ca.factura_id
            WHERE ca.recibo_id = %s
            ORDER BY ca.id ASC
            """,
            (recibo_id, recibo_id),
        )
        aplicaciones = _dict_rows(cursor)
        _anexar_cfdi_cobranza([recibo])
        return {"recibo": recibo, "aplicaciones": aplicaciones}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.post("/payments", status_code=201)
def register_payment(payload: dict, user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        tipo = str(payload.get("tipo_movimiento") or "").strip().upper()
        if tipo not in ("PAGO", "ANTICIPO", "AJUSTE", "NOTA_CREDITO"):
            raise HTTPException(status_code=400, detail="tipo_movimiento invalido: PAGO, ANTICIPO, AJUSTE o NOTA_CREDITO")
        monto_total = round(_to_float(payload.get("monto")), 2)
        if monto_total <= 0:
            raise HTTPException(status_code=400, detail="El monto debe ser mayor a cero")
        empresa = _empresa_operativa(str(payload.get("empresa") or "").strip())
        numero_cliente = str(payload.get("numero_cliente") or "").strip()
        if not empresa or not numero_cliente:
            raise HTTPException(status_code=400, detail="Faltan empresa o numero_cliente")

        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)

        fecha_movimiento = str(payload.get("fecha_movimiento") or datetime.now().date().isoformat())
        folio = _generar_folio_recibo(cursor, tipo, fecha_movimiento)
        referencia = str(payload.get("referencia") or "").strip()
        forma_pago_raw = str(payload.get("forma_pago") or "").strip()
        forma_pago = forma_pago_raw.zfill(2)[:5] if forma_pago_raw else ""
        if tipo == "PAGO" and forma_pago == "99":
            raise HTTPException(status_code=400, detail="Para un pago selecciona una forma de pago SAT distinta de 99.")
        if tipo in ("PAGO", "NOTA_CREDITO") and not forma_pago:
            raise HTTPException(status_code=400, detail="Selecciona una forma de pago SAT.")
        observaciones = str(payload.get("observaciones") or "").strip()
        nota_noid = str(payload.get("nota_credito_no_identificacion") or "").strip()[:40]
        nota_clave_unidad = str(payload.get("nota_credito_clave_unidad") or "").strip()[:10]
        nota_unidad = str(payload.get("nota_credito_unidad") or "").strip()[:20]
        nota_descripcion = str(payload.get("nota_credito_descripcion") or "").strip()[:255]
        usuario = user["username"]

        aplicaciones = payload.get("aplicaciones") or []
        aplicaciones_ok = []
        for app in aplicaciones:
            factura_id = int(app.get("factura_id") or 0)
            monto_aplicado = round(_to_float(app.get("monto_aplicado")), 2)
            origen_tipo = str(app.get("origen") or app.get("origen_tipo") or "FACTURA").strip().upper()
            saldo_inicial_id = int(app.get("saldo_inicial_id") or 0)
            if origen_tipo == "SALDO_INICIAL" and saldo_inicial_id <= 0 and factura_id < 0:
                saldo_inicial_id = abs(factura_id)
            if monto_aplicado <= 0:
                continue
            factura = str(app.get("factura") or "").strip()
            if origen_tipo == "SALDO_INICIAL":
                if saldo_inicial_id <= 0:
                    continue
                if not factura:
                    cursor.execute("SELECT factura FROM cobranza_saldos_iniciales WHERE id = %s LIMIT 1", (saldo_inicial_id,))
                    row = cursor.fetchone()
                    factura = str(row[0] if row else "")
                aplicaciones_ok.append({
                    "factura_id": -abs(saldo_inicial_id),
                    "factura": factura,
                    "monto_aplicado": monto_aplicado,
                    "origen_tipo": "SALDO_INICIAL",
                    "saldo_inicial_id": saldo_inicial_id,
                })
                continue
            if factura_id <= 0:
                continue
            if not factura:
                cursor.execute("SELECT factura FROM facturas WHERE id = %s LIMIT 1", (factura_id,))
                row = cursor.fetchone()
                factura = str(row[0] if row else "")
            aplicaciones_ok.append({
                "factura_id": factura_id,
                "factura": factura,
                "monto_aplicado": monto_aplicado,
                "origen_tipo": "FACTURA",
                "saldo_inicial_id": None,
            })

        total_aplicado = round(sum(x["monto_aplicado"] for x in aplicaciones_ok), 2)
        if tipo in ("PAGO", "NOTA_CREDITO") and not aplicaciones_ok:
            raise HTTPException(status_code=400, detail="Selecciona al menos una factura para aplicar el movimiento")
        if total_aplicado - monto_total > 0.009:
            raise HTTPException(status_code=400, detail="El total aplicado no puede ser mayor al monto capturado")
        if tipo == "NOTA_CREDITO":
            monto_total = total_aplicado

        saldo_disponible = round(monto_total - total_aplicado, 2)
        cursor.execute(
            """INSERT INTO cobranza_recibos
               (folio, numero_cliente, empresa, tipo_recibo, fecha_recibo, monto_total,
                monto_aplicado, saldo_disponible, forma_pago,
                nota_credito_no_identificacion, nota_credito_clave_unidad, nota_credito_unidad,
                nota_credito_descripcion, referencia, observaciones, usuario)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (folio, numero_cliente, empresa, tipo, fecha_movimiento, monto_total,
             total_aplicado, saldo_disponible, forma_pago,
             nota_noid, nota_clave_unidad, nota_unidad, nota_descripcion,
             referencia, observaciones, usuario),
        )
        recibo_id = cursor.lastrowid

        for app in aplicaciones_ok:
            cursor.execute(
                """INSERT INTO cobranza_aplicaciones
                   (recibo_id, factura_id, factura, origen_tipo, saldo_inicial_id, monto_aplicado)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (recibo_id, app["factura_id"], app["factura"],
                 app.get("origen_tipo") or "FACTURA", app.get("saldo_inicial_id"), app["monto_aplicado"]),
            )

        conn.commit()
        _cc_invalidate()
        return {"id": recibo_id, "folio": folio, "total_aplicado": total_aplicado, "saldo_disponible": saldo_disponible}
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as exc:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.post("/receipts/{recibo_id}/cancel")
def cancel_receipt(recibo_id: int, payload: dict | None = None, user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute("SELECT id, folio, estatus FROM cobranza_recibos WHERE id = %s LIMIT 1", (recibo_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Recibo no encontrado")
        estatus = str(row[2] or "").upper() if len(row) > 2 else ""
        if estatus == "CANCELADO":
            return {"ok": True, "mensaje": "Ya estaba cancelado"}
        usuario = user["username"]
        motivo = ""
        if isinstance(payload, dict):
            motivo = str(payload.get("motivo") or "").strip()
        sufijo = f"\n[CANCELADO {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}][{usuario}] {motivo}".strip()
        cursor.execute(
            "UPDATE cobranza_recibos SET estatus = 'CANCELADO', observaciones = CONCAT(COALESCE(observaciones, ''), %s) WHERE id = %s",
            (sufijo, recibo_id),
        )
        cursor.execute("DELETE FROM cobranza_aplicaciones WHERE recibo_id = %s", (recibo_id,))
        conn.commit()
        _cc_invalidate()
        return {"ok": True, "mensaje": "Movimiento cancelado, aplicaciones eliminadas y saldos actualizados", "folio": row[1]}
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as exc:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.delete("/receipts/{recibo_id}")
def delete_receipt(recibo_id: int, user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute("SELECT id, folio FROM cobranza_recibos WHERE id = %s LIMIT 1", (recibo_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Recibo no encontrado")
        cursor.execute("DELETE FROM cobranza_aplicaciones WHERE recibo_id = %s", (recibo_id,))
        cursor.execute("DELETE FROM cobranza_recibos WHERE id = %s", (recibo_id,))
        conn.commit()
        _cc_invalidate()
        return {"ok": True, "mensaje": "Eliminado definitivamente, aplicaciones eliminadas y saldos actualizados", "folio": row[1]}
    except HTTPException:
        raise
    except Exception as exc:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/morosos")
def list_morosos(empresa: str | None = None, user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cartera = _obtener_cartera(cursor, conn, empresa=empresa)
        hoy = datetime.now().date()
        return [x for x in cartera if x["saldo"] > 0 and x.get("fecha_vencimiento") and x["fecha_vencimiento"] < hoy]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/proximos-cobros")
def proximos_cobros(empresa: str | None = None, cadena_ids: str | None = None, anio: int | None = None, mes: int | None = None, user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cartera = _obtener_cartera(cursor, conn, empresa=empresa)
        hoy = datetime.now().date()
        # Show items by selected month/year, default to current month
        target_anio = anio if anio is not None else hoy.year
        target_mes = mes if mes is not None else hoy.month
        items = [
            x for x in cartera
            if x["saldo"] > 0
            and x.get("fecha_vencimiento")
            and x["fecha_vencimiento"].year == target_anio
            and x["fecha_vencimiento"].month == target_mes
        ]
        mapa = _obtener_mapa_cadenas(cursor)
        # Filter by selected cadenas
        sel_ids = None
        if cadena_ids:
            try:
                sel_ids = set(int(x) for x in cadena_ids.split(",") if x.strip())
            except (ValueError, TypeError):
                pass
        if sel_ids:
            items = [i for i in items if any(
                c["id"] in sel_ids and (not c["empresa"] or c["empresa"] == _empresa_cadena_clave(i.get("empresa")))
                for c in (mapa.get(_normalizar_numero_cliente(i.get("numero_cliente"))) or [])
            )]
        cadenas_vivas = {}
        cursor.execute("SELECT c.id, c.nombre FROM cadenas c WHERE COALESCE(c.activa_cobranza, 0) = 1 ORDER BY c.nombre")
        for r in _dict_rows(cursor):
            cadenas_vivas[r["id"]] = r["nombre"]
        por_cadena = {}
        for i in items:
            num = _normalizar_numero_cliente(i.get("numero_cliente"))
            emp_clave = _empresa_cadena_clave(i.get("empresa"))
            for c in mapa.get(num) or []:
                if c["empresa"] and c["empresa"] != emp_clave:
                    continue
                cid = c["id"]
                if cid not in por_cadena:
                    por_cadena[cid] = {"id": cid, "nombre": c["nombre"], "saldo": 0.0}
                por_cadena[cid]["saldo"] += float(i.get("saldo", 0) or 0)
        return {
            "items": items,
            "por_cadena": sorted(por_cadena.values(), key=lambda x: x["saldo"], reverse=True),
            "cadenas": [{"id": k, "nombre": v} for k, v in cadenas_vivas.items()],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/factura/{factura_id}/comprobantes")
def comprobantes_factura(factura_id: int, user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        if factura_id < 0:
            cursor.execute(
                """SELECT cr.id AS recibo_id, cr.folio, cr.tipo_recibo, cr.fecha_recibo,
                          cr.monto_total, cr.referencia, cr.usuario, ca.monto_aplicado
                   FROM cobranza_aplicaciones ca
                   INNER JOIN cobranza_recibos cr ON cr.id = ca.recibo_id
                    WHERE ca.saldo_inicial_id = %s
                      AND COALESCE(ca.origen_tipo, 'FACTURA') = 'SALDO_INICIAL'
                      AND cr.estatus = 'ACTIVO'
                   ORDER BY cr.fecha_recibo DESC, cr.id DESC""",
                (abs(factura_id),),
            )
        else:
            cursor.execute(
                """SELECT cr.id AS recibo_id, cr.folio, cr.tipo_recibo, cr.fecha_recibo,
                          cr.monto_total, cr.referencia, cr.usuario, ca.monto_aplicado
                   FROM cobranza_aplicaciones ca
                   INNER JOIN cobranza_recibos cr ON cr.id = ca.recibo_id
                    WHERE ca.factura_id = %s
                      AND COALESCE(ca.origen_tipo, 'FACTURA') = 'FACTURA'
                      AND cr.estatus = 'ACTIVO'
                   ORDER BY cr.fecha_recibo DESC, cr.id DESC""",
                (factura_id,),
            )
        return _dict_rows(cursor)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/factura/{factura_id}/productos")
def productos_factura(factura_id: int, user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute(
            """SELECT cip, descripcion AS producto, cantidad, piezas, precio,
                      CASE
                        WHEN importe != 0 THEN importe
                        WHEN cantidad != 0 THEN ROUND(precio * cantidad, 2)
                        WHEN piezas != 0 THEN ROUND(precio * piezas, 2)
                        ELSE 0
                      END AS importe
               FROM factura_detalle
               WHERE factura_id = %s
               ORDER BY id""",
            (factura_id,),
        )
        return _dict_rows(cursor)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.post("/recibos-por-facturas")
def recibos_por_facturas(payload: dict, user=Depends(require_user)):
    factura_ids = payload.get("factura_ids") or []
    tipo = str(payload.get("tipo") or "").strip().upper()
    ids_ok = [int(fid) for fid in factura_ids if isinstance(fid, int) or (isinstance(fid, str) and fid.strip().lstrip("-").isdigit())]
    ids_ok = [fid for fid in ids_ok if fid != 0]
    if not ids_ok:
        return []
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        placeholders = ",".join(["%s"] * len(ids_ok))
        sql = f"""SELECT cr.id, cr.folio, cr.numero_cliente, cr.empresa, cr.tipo_recibo,
                         cr.fecha_recibo, cr.monto_total, ROUND(SUM(ca.monto_aplicado), 2) AS monto_aplicado,
                         cr.saldo_disponible, cr.referencia, cr.usuario, cr.estatus, cr.created_at
                  FROM cobranza_aplicaciones ca
                  INNER JOIN cobranza_recibos cr ON cr.id = ca.recibo_id
                  WHERE cr.estatus = 'ACTIVO' AND ca.factura_id IN ({placeholders})"""
        params = list(ids_ok)
        if tipo == "PAGOS":
            sql += " AND cr.tipo_recibo IN ('PAGO', 'ANTICIPO')"
        elif tipo:
            sql += " AND cr.tipo_recibo = %s"
            params.append(tipo)
        sql += " GROUP BY cr.id, cr.folio ORDER BY cr.fecha_recibo DESC, cr.id DESC"
        cursor.execute(sql, params)
        return _dict_rows(cursor)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/saldos-iniciales")
def list_saldos_iniciales(empresa: str | None = None, user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        sql = "SELECT * FROM cobranza_saldos_iniciales WHERE estatus = 'ACTIVO'"
        params = []
        if empresa:
            sql += " AND empresa = %s"
            params.append(str(empresa).strip())
        sql += " ORDER BY fecha_factura DESC, id DESC"
        cursor.execute(sql, params)
        return _dict_rows(cursor)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/saldos-iniciales/{saldo_id}")
def detalle_saldo_inicial(saldo_id: int, user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute("SELECT * FROM cobranza_saldos_iniciales WHERE id = %s LIMIT 1", (saldo_id,))
        rows = _dict_rows(cursor)
        if not rows:
            raise HTTPException(status_code=404, detail="Saldo inicial no encontrado")
        return rows[0]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.post("/saldos-iniciales")
def crear_saldo_inicial(payload: dict, user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        empresa = str(payload.get("empresa") or "").strip()
        factura = str(payload.get("factura") or "").strip()
        numero_cliente = str(payload.get("numero_cliente") or "").strip()
        if not empresa or not factura or not numero_cliente:
            raise HTTPException(status_code=400, detail="empresa, factura y numero_cliente son obligatorios")
        cursor.execute(
            """INSERT INTO cobranza_saldos_iniciales
               (factura, folio_interno, numero_cliente, cliente_nombre, empresa, fecha_factura, fecha_vencimiento,
                dias_credito, total, pagos_iniciales, saldo_inicial, vendedor, observaciones, usuario)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                factura,
                str(payload.get("folio_interno") or "").strip(),
                numero_cliente,
                str(payload.get("cliente_nombre") or "").strip(),
                empresa,
                payload.get("fecha_factura"),
                payload.get("fecha_vencimiento"),
                int(payload.get("dias_credito") or 0),
                _to_float(payload.get("total")),
                _to_float(payload.get("pagos_iniciales")),
                _to_float(payload.get("saldo_inicial")),
                str(payload.get("vendedor") or "").strip(),
                str(payload.get("observaciones") or "").strip(),
                user["username"],
            ),
        )
        conn.commit()
        _cc_invalidate()
        return {"id": cursor.lastrowid, "ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.delete("/saldos-iniciales/{saldo_id}")
def eliminar_saldo_inicial(saldo_id: int, user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute("DELETE FROM cobranza_saldos_iniciales WHERE id = %s", (saldo_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Saldo inicial no encontrado")
        conn.commit()
        _cc_invalidate()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.post("/saldos-iniciales/importar-zip")
async def importar_saldos_iniciales_zip(
    payload: str = Form(...),
    xml_zip: UploadFile | None = File(default=None),
    user=Depends(require_user),
):
    try:
        datos = json.loads(payload or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Payload de importacion invalido.")
    xmls = _leer_zip_xmls(xml_zip)
    return _importar_saldos_iniciales_data(datos, user, xmls_zip=xmls)


def _parse_date_value(value):
    if value in (None, ""):
        return None
    if hasattr(value, "strftime"):
        try:
            return value.date() if hasattr(value, "date") else value
        except Exception:
            pass
    txt = str(value or "").strip()
    if not txt:
        return None
    # Las plantillas históricas de SAE usan meses en español: 23/ABR/2026.
    # datetime.strptime no interpreta esas abreviaturas con la configuración
    # regional del proceso, por eso se convierten explícitamente.
    partes = re.split(r"[/-]", txt.upper())
    meses_es = {
        "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12,
    }
    if len(partes) == 3 and partes[1] in meses_es:
        try:
            return datetime(int(partes[2]), meses_es[partes[1]], int(partes[0])).date()
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(txt, fmt).date()
        except Exception:
            continue
    return None


def _normalizar_numero_cliente(valor):
    txt = str(valor or "").strip()
    if txt.endswith(".0"):
        txt = txt[:-2]
    return txt


def _empresa_cadena_clave(empresa):
    texto = re.sub(r"[_\s]+", " ", str(empresa or "").strip().upper())
    return "".join(
        caracter for caracter in unicodedata.normalize("NFD", texto)
        if unicodedata.category(caracter) != "Mn"
    )


def _empresa_operativa(empresa: str) -> str:
    """Convierte el código técnico del selector al nombre usado en cartera."""
    clave = _empresa_cadena_clave(empresa)
    equivalencias = {
        "GOURMET ESPANA": "Gourmet España",
        "IBERSUR": "Ibersur",
        "REMISION": "Remisiones",
        "REMISIONES": "Remisiones",
        "EZA2007": "EZA2007",
    }
    return equivalencias.get(clave, str(empresa or "").strip())


def _safe_filename(name: str) -> str:
    base = os.path.basename(str(name or "")).strip()
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("._")


def _normalizar_xml_nombre(value: str) -> str:
    nombre = _safe_filename(value)
    if nombre and not nombre.lower().endswith(".xml"):
        nombre += ".xml"
    return nombre


def _normalizar_uuid_cfdi(value: str) -> str:
    return str(value or "").strip().upper()


def _resolver_cfdi_por_folio_interno(cursor, folio_interno: str) -> dict:
    """Obtiene la referencia fiscal y, si existe, el folio histórico SAE."""
    interno = str(folio_interno or "").strip()
    if not interno:
        return {}
    try:
        cursor.execute(
            """SELECT f.sae_codigo, ce.factura, ce.serie, ce.folio_cfdi, ce.uuid
               FROM facturas f
               LEFT JOIN cfdi_emitidos ce
                 ON ce.factura_id = f.id
               WHERE UPPER(TRIM(f.factura)) = UPPER(TRIM(%s))
               ORDER BY ce.id DESC LIMIT 1""",
            (interno,),
        )
        row = cursor.fetchone()
        if not row:
            return {}
        data = row if isinstance(row, dict) else {
            "sae_codigo": row[0], "factura": row[1], "serie": row[2], "folio_cfdi": row[3], "uuid": row[4]
        }
        fiscal = f"{str(data.get('serie') or '').strip()}{str(data.get('folio_cfdi') or '').strip()}"
        return {
            "factura_fiscal": fiscal,
            "uuid": _normalizar_uuid_cfdi(data.get("uuid")),
            "sae_codigo": str(data.get("sae_codigo") or "").strip(),
        }
    except Exception:
        return {}


def _rutas_xml_historicos() -> list[Path]:
    """Fuentes de XML, sin duplicar rutas ni tocar los archivos de origen."""
    rutas = [settings.storage_dir / "cfdi", *(Path(item) for item in (settings.cobranza_xml_historicos_dirs or []))]
    resultado, vistas = [], set()
    for ruta in rutas:
        try:
            clave = str(ruta.resolve()).lower()
        except Exception:
            clave = str(ruta).lower()
        if clave not in vistas:
            vistas.add(clave)
            resultado.append(ruta)
    return resultado


def _leer_xml_historico(ruta: Path) -> tuple[str, bytes] | None:
    try:
        if not ruta.is_file() or ruta.stat().st_size > 10 * 1024 * 1024:
            return None
        datos = ruta.read_bytes()
        # Comprueba que el candidato realmente sea un CFDI antes de asociarlo.
        _extraer_datos_cfdi_xml(datos)
        return (_normalizar_xml_nombre(ruta.name), datos)
    except Exception:
        return None


def _buscar_xml_historico_por_nombre(nombre_xml: str, cache: dict[str, tuple[str, bytes] | None]) -> tuple[str, bytes] | None:
    """Busca el nombre original del XML en el depósito de Aspel y otras fuentes."""
    nombre = _normalizar_xml_nombre(nombre_xml)
    if not nombre:
        return None
    clave_cache = f"nombre:{nombre.lower()}"
    if clave_cache in cache:
        return cache[clave_cache]
    for base in _rutas_xml_historicos():
        try:
            if not base.is_dir():
                continue
            directo = _leer_xml_historico(base / nombre)
            if directo:
                cache[clave_cache] = directo
                return directo
            for ruta in base.rglob(nombre):
                encontrado = _leer_xml_historico(ruta)
                if encontrado:
                    cache[clave_cache] = encontrado
                    return encontrado
        except Exception:
            continue
    cache[clave_cache] = None
    return None


def _buscar_xml_historico_por_sae(sae_codigo: str, cache: dict[str, tuple[str, bytes] | None]) -> tuple[str, bytes] | None:
    """Relaciona un folio SAE (ej. SAE 20542) con el CFDI guardado por Aspel."""
    numeros = "".join(re.findall(r"\d+", str(sae_codigo or "")))
    if not numeros:
        return None
    clave_cache = f"sae:{numeros}"
    if clave_cache in cache:
        return cache[clave_cache]
    objetivo = str(int(numeros))
    patrones = [f"*{objetivo}*.xml", f"*{int(numeros):010d}*.xml"]
    vistos = set()
    for base in _rutas_xml_historicos():
        try:
            if not base.is_dir():
                continue
            for patron in patrones:
                for ruta in base.rglob(patron):
                    clave = str(ruta).lower()
                    if clave in vistos:
                        continue
                    vistos.add(clave)
                    encontrado = _leer_xml_historico(ruta)
                    if not encontrado:
                        continue
                    info = _extraer_datos_cfdi_xml(encontrado[1])
                    folio_xml = "".join(re.findall(r"\d+", str(info.get("folio_cfdi") or "")))
                    if folio_xml and str(int(folio_xml)) == objetivo:
                        cache[clave_cache] = encontrado
                        return encontrado
        except Exception:
            continue
    cache[clave_cache] = None
    return None


def _buscar_xml_historico_por_uuid(cursor, uuid_busqueda: str, cache: dict[str, tuple[str, bytes] | None]) -> tuple[str, bytes] | None:
    """Busca un XML por UUID en CFDI emitidos y en las rutas históricas configuradas."""
    uuid_limpio = _normalizar_uuid_cfdi(uuid_busqueda)
    if not uuid_limpio:
        return None
    if uuid_limpio in cache:
        return cache[uuid_limpio]

    try:
        cursor.execute("SELECT xml_path FROM cfdi_emitidos WHERE UPPER(TRIM(uuid)) = %s ORDER BY id DESC LIMIT 1", (uuid_limpio,))
        row = cursor.fetchone()
        ruta_registrada = (row.get("xml_path") if isinstance(row, dict) else row[0]) if row else ""
        ruta = Path(str(ruta_registrada or ""))
        if ruta.is_file():
            datos = ruta.read_bytes()
            if uuid_limpio.encode("ascii") in datos.upper():
                resultado = (_normalizar_xml_nombre(ruta.name), datos)
                cache[uuid_limpio] = resultado
                return resultado
    except Exception:
        pass

    rutas = _rutas_xml_historicos()
    vistas, revisados = set(), 0
    for base in rutas:
        try:
            base = base.resolve()
        except Exception:
            continue
        if str(base).lower() in vistas or not base.is_dir():
            continue
        vistas.add(str(base).lower())
        try:
            for ruta in base.rglob("*.xml"):
                revisados += 1
                if revisados > 10000:
                    break
                try:
                    if ruta.stat().st_size > 10 * 1024 * 1024:
                        continue
                    datos = ruta.read_bytes()
                    if uuid_limpio.encode("ascii") in datos.upper():
                        resultado = (_normalizar_xml_nombre(ruta.name), datos)
                        cache[uuid_limpio] = resultado
                        return resultado
                except Exception:
                    continue
        except Exception:
            continue
    cache[uuid_limpio] = None
    return None


def _buscar_xml_historico_por_folio(cursor, folio_busqueda: str, cache: dict[str, tuple[str, bytes] | None]) -> tuple[str, bytes] | None:
    """Ubica un XML por la serie y folio fiscal visibles (ej. FE13)."""
    folio_limpio = re.sub(r"\s+", "", str(folio_busqueda or "").upper())
    if not folio_limpio:
        return None
    try:
        cursor.execute(
            """SELECT uuid FROM cfdi_emitidos
               WHERE UPPER(REPLACE(CONCAT(COALESCE(serie,''), COALESCE(folio_cfdi,'')), ' ', '')) = %s
               ORDER BY id DESC LIMIT 1""",
            (folio_limpio,),
        )
        row = cursor.fetchone()
        uuid = (row.get("uuid") if isinstance(row, dict) else row[0]) if row else ""
        if uuid:
            encontrado = _buscar_xml_historico_por_uuid(cursor, uuid, cache)
            if encontrado:
                return encontrado
    except Exception:
        pass

    rutas = _rutas_xml_historicos()
    revisados, vistas = 0, set()
    for base in rutas:
        try:
            base = base.resolve()
        except Exception:
            continue
        if str(base).lower() in vistas or not base.is_dir():
            continue
        vistas.add(str(base).lower())
        try:
            for ruta in base.rglob("*.xml"):
                revisados += 1
                if revisados > 10000:
                    break
                try:
                    if ruta.stat().st_size > 10 * 1024 * 1024:
                        continue
                    datos = ruta.read_bytes()
                    info = _extraer_datos_cfdi_xml(datos)
                    folio_xml = re.sub(r"\s+", "", f"{info.get('serie_cfdi') or ''}{info.get('folio_cfdi') or ''}".upper())
                    if folio_xml == folio_limpio:
                        return (_normalizar_xml_nombre(ruta.name), datos)
                except Exception:
                    continue
        except Exception:
            continue
    return None


def _extraer_datos_cfdi_xml(xml_bytes: bytes) -> dict:
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"XML externo invalido: {exc}")
    ns = {"cfdi": "http://www.sat.gob.mx/cfd/4", "tfd": "http://www.sat.gob.mx/TimbreFiscalDigital"}
    receptor = root.find("cfdi:Receptor", ns)
    uuid = ""
    for node in root.iter():
        if str(node.tag).rsplit("}", 1)[-1] == "TimbreFiscalDigital":
            uuid = str(node.attrib.get("UUID") or "").strip()
            break
    return {
        "uuid": uuid,
        "serie_cfdi": str(root.attrib.get("Serie") or "").strip(),
        "folio_cfdi": str(root.attrib.get("Folio") or "").strip(),
        "total": _to_float(root.attrib.get("Total")),
        "moneda_cfdi": str(root.attrib.get("Moneda") or "MXN").strip(),
        "rfc_receptor": str((receptor.attrib.get("Rfc") if receptor is not None else "") or "").strip(),
        "nombre_receptor": str((receptor.attrib.get("Nombre") if receptor is not None else "") or "").strip(),
        "cp_receptor": str((receptor.attrib.get("DomicilioFiscalReceptor") if receptor is not None else "") or "").strip(),
        "regimen_receptor": str((receptor.attrib.get("RegimenFiscalReceptor") if receptor is not None else "") or "").strip(),
    }


def _guardar_xml_saldo_externo(empresa: str, nombre: str, xml_bytes: bytes) -> str:
    safe_empresa = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(empresa or "empresa")).strip("._") or "empresa"
    safe_nombre = _normalizar_xml_nombre(nombre) or "cfdi.xml"
    destino_dir = settings.storage_dir / "cobranza" / "xml_externos" / safe_empresa
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / safe_nombre
    if destino.exists():
        stem = destino.stem
        suffix = destino.suffix or ".xml"
        destino = destino_dir / f"{stem}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}{suffix}"
    destino.write_bytes(xml_bytes)
    return str(destino)


def _leer_zip_xmls(xml_zip: UploadFile | None) -> dict[str, bytes]:
    if not xml_zip:
        return {}
    try:
        contenido = xml_zip.file.read()
    except Exception:
        contenido = b""
    if not contenido:
        return {}
    from io import BytesIO
    xmls = {}
    try:
        with zipfile.ZipFile(BytesIO(contenido)) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".xml"):
                    continue
                nombre = _normalizar_xml_nombre(info.filename)
                if not nombre:
                    continue
                xmls[nombre.lower()] = zf.read(info)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="El archivo ZIP de XML no es valido.")
    return xmls


@router.post("/saldos-iniciales/importar")
def importar_saldos_iniciales(payload: dict, user=Depends(require_user)):
    return _importar_saldos_iniciales_data(payload, user)


def _importar_saldos_iniciales_data(payload: dict, user: dict, xmls_zip: dict[str, bytes] | None = None):
    conn = cursor = None
    try:
        empresa = str(payload.get("empresa") or "").strip()
        fecha_inicio = _parse_date_value(payload.get("fecha_inicio_facturas"))
        reemplazar = bool(payload.get("reemplazar", True))
        rows = payload.get("rows") or []
        if not empresa:
            raise HTTPException(status_code=400, detail="La empresa es obligatoria")
        if not fecha_inicio:
            raise HTTPException(status_code=400, detail="La fecha de inicio de facturas es obligatoria")
        if not isinstance(rows, list) or not rows:
            raise HTTPException(status_code=400, detail="No se recibieron saldos iniciales para importar")
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute(
            "INSERT INTO cobranza_config (empresa, fecha_inicio_facturas) VALUES (%s, %s) ON DUPLICATE KEY UPDATE fecha_inicio_facturas = VALUES(fecha_inicio_facturas)",
            (empresa, fecha_inicio),
        )
        if reemplazar:
            cursor.execute("DELETE FROM cobranza_saldos_iniciales WHERE UPPER(TRIM(empresa)) = UPPER(TRIM(%s))", (empresa,))
        # La plantilla puede omitir los días de crédito. En ese caso se usan los
        # registrados para el cliente dentro de la misma empresa, igual que la
        # cartera normal de facturas internas.
        cursor.execute("SELECT numero, empresa, dias_credito FROM clientes")
        creditos_clientes = {}
        for cliente in _dict_rows(cursor):
            numero = _normalizar_numero_cliente(cliente.get("numero"))
            empresa_cliente = _empresa_cadena_clave(cliente.get("empresa"))
            if numero and empresa_cliente:
                creditos_clientes[(empresa_cliente, numero)] = _to_int(cliente.get("dias_credito") or 0)
        importados = 0
        omitidos = 0
        xmls_asociados = 0
        xmls_faltantes = []
        xmls_por_uuid = {}
        for nombre_zip, contenido_zip in (xmls_zip or {}).items():
            try:
                uuid_zip = _normalizar_uuid_cfdi(_extraer_datos_cfdi_xml(contenido_zip).get("uuid"))
                if uuid_zip:
                    xmls_por_uuid[uuid_zip] = (nombre_zip, contenido_zip)
            except Exception:
                continue
        cache_xml_uuid: dict[str, tuple[str, bytes] | None] = {}
        for row in rows:
            if not isinstance(row, dict):
                omitidos += 1
                continue
            factura = str(row.get("factura") or row.get("folio") or row.get("documento") or "").strip()
            folio_interno = str(row.get("folio_interno") or row.get("interno") or row.get("folio_sae") or "").strip()
            num_cliente = _normalizar_numero_cliente(row.get("cliente_numero") or row.get("numero_cliente") or row.get("cliente") or "")
            referencia_interna = _resolver_cfdi_por_folio_interno(cursor, folio_interno) if folio_interno else {}
            if not factura:
                factura = str(referencia_interna.get("factura_fiscal") or folio_interno).strip()
            if not factura or not num_cliente:
                omitidos += 1
                continue
            cliente_nombre = str(row.get("cliente_nombre") or row.get("nombre_cliente") or row.get("tienda") or row.get("cliente_nombre_tienda") or "").strip()
            vendedor = str(row.get("vendedor") or "").strip()
            fecha_factura = _parse_date_value(row.get("fecha_factura") or row.get("fecha"))
            fecha_vencimiento = _parse_date_value(row.get("fecha_vencimiento") or row.get("vencimiento") or row.get("fecha_vto"))
            valor_credito = row.get("dias_credito") if row.get("dias_credito") not in (None, "") else row.get("credito")
            dias_credito = _to_int(valor_credito or 0)
            if valor_credito in (None, ""):
                clave_credito = (_empresa_cadena_clave(empresa), num_cliente)
                dias_credito = creditos_clientes.get(clave_credito, 0)
                # Las remisiones usan la cartera operativa de EZA2007 o Ibersur.
                if not dias_credito and _empresa_cadena_clave(empresa) in ("REMISION", "REMISIÓN"):
                    dias_credito = creditos_clientes.get(("EZA2007", num_cliente), creditos_clientes.get(("IBERSUR", num_cliente), 0))
            total = round(_to_float(row.get("total") or row.get("importe") or row.get("monto_total")), 2)
            pagos_iniciales = round(_to_float(row.get("pagado") or row.get("pagos_iniciales") or 0), 2)
            saldo_inicial = round(_to_float(row.get("saldo") or row.get("saldo_inicial") or 0), 2)
            observaciones = str(row.get("observaciones") or row.get("comentarios") or "").strip()
            xml_nombre = _normalizar_xml_nombre(row.get("xml") or row.get("xml_nombre") or row.get("archivo_xml") or row.get("nombre_xml") or "")
            uuid_solicitado = _normalizar_uuid_cfdi(row.get("uuid") or row.get("uuid_cfdi") or referencia_interna.get("uuid") or "")
            xml_path = ""
            xml_data = {}
            if uuid_solicitado:
                encontrado = xmls_por_uuid.get(uuid_solicitado) or _buscar_xml_historico_por_uuid(cursor, uuid_solicitado, cache_xml_uuid)
                if encontrado:
                    xml_nombre, xml_bytes = encontrado
                    xml_data = _extraer_datos_cfdi_xml(xml_bytes)
                    xml_path = _guardar_xml_saldo_externo(empresa, xml_nombre, xml_bytes)
                    xmls_asociados += 1
                    if xml_data.get("total") and total <= 0:
                        total = round(_to_float(xml_data.get("total")), 2)
                else:
                    xmls_faltantes.append(f"UUID {uuid_solicitado}")
            if not xml_path and xml_nombre:
                encontrado = _buscar_xml_historico_por_nombre(xml_nombre, cache_xml_uuid)
                if encontrado:
                    xml_nombre, xml_bytes = encontrado
                    xml_data = _extraer_datos_cfdi_xml(xml_bytes)
                    xml_path = _guardar_xml_saldo_externo(empresa, xml_nombre, xml_bytes)
                    xmls_asociados += 1
            if not xml_path and referencia_interna.get("sae_codigo"):
                encontrado = _buscar_xml_historico_por_sae(referencia_interna["sae_codigo"], cache_xml_uuid)
                if encontrado:
                    xml_nombre, xml_bytes = encontrado
                    xml_data = _extraer_datos_cfdi_xml(xml_bytes)
                    xml_path = _guardar_xml_saldo_externo(empresa, xml_nombre, xml_bytes)
                    xmls_asociados += 1
            if not xml_path and not uuid_solicitado:
                encontrado = _buscar_xml_historico_por_folio(cursor, factura, cache_xml_uuid)
                if encontrado:
                    xml_nombre, xml_bytes = encontrado
                    xml_data = _extraer_datos_cfdi_xml(xml_bytes)
                    xml_path = _guardar_xml_saldo_externo(empresa, xml_nombre, xml_bytes)
                    xmls_asociados += 1
                    if xml_data.get("total") and total <= 0:
                        total = round(_to_float(xml_data.get("total")), 2)
            if xml_nombre and xmls_zip is not None:
                xml_bytes = xmls_zip.get(xml_nombre.lower())
                if xml_bytes and not xml_path:
                    xml_data = _extraer_datos_cfdi_xml(xml_bytes)
                    xml_path = _guardar_xml_saldo_externo(empresa, xml_nombre, xml_bytes)
                    xmls_asociados += 1
                    if xml_data.get("total") and total <= 0:
                        total = round(_to_float(xml_data.get("total")), 2)
                elif not xml_path:
                    xmls_faltantes.append(xml_nombre)
            if saldo_inicial <= 0:
                saldo_inicial = round(max(total - pagos_iniciales, 0.0), 2)
            if total <= 0 and saldo_inicial > 0:
                total = round(saldo_inicial + pagos_iniciales, 2)
            if not fecha_vencimiento and fecha_factura:
                try:
                    fecha_vencimiento = fecha_factura + timedelta(days=max(dias_credito, 0))
                except Exception:
                    fecha_vencimiento = fecha_factura
            if total <= 0 and saldo_inicial <= 0:
                omitidos += 1
                continue
            cursor.execute(
                """INSERT INTO cobranza_saldos_iniciales
                   (factura, folio_interno, numero_cliente, cliente_nombre, empresa, fecha_factura, fecha_vencimiento,
                    dias_credito, total, pagos_iniciales, saldo_inicial, vendedor, xml_nombre, xml_path,
                    uuid, serie_cfdi, folio_cfdi, rfc_receptor, nombre_receptor, cp_receptor, regimen_receptor,
                    moneda_cfdi, observaciones, usuario, estatus)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVO')""",
                (factura, folio_interno, num_cliente, cliente_nombre, empresa, fecha_factura, fecha_vencimiento,
                 dias_credito, total, pagos_iniciales, saldo_inicial, vendedor, xml_nombre, xml_path,
                 xml_data.get("uuid") or "", xml_data.get("serie_cfdi") or "", xml_data.get("folio_cfdi") or "",
                 xml_data.get("rfc_receptor") or "", xml_data.get("nombre_receptor") or "", xml_data.get("cp_receptor") or "",
                 xml_data.get("regimen_receptor") or "", xml_data.get("moneda_cfdi") or "MXN",
                 observaciones, user["username"]),
            )
            importados += 1
        conn.commit()
        _cc_invalidate()
        return {
            "ok": True,
            "mensaje": "Saldos iniciales importados correctamente",
            "importados": importados,
            "omitidos": omitidos,
            "xmls_asociados": xmls_asociados,
            "xmls_faltantes": sorted(set(xmls_faltantes))[:50],
            "empresa": empresa,
            "fecha_inicio_facturas": fecha_inicio.isoformat(),
        }
    except HTTPException:
        if conn: conn.rollback()
        raise
    except Exception as exc:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _obtener_mapa_cadenas(cursor):
    cursor.execute(
        """SELECT c.id, c.nombre, cc.cliente_numero, cc.empresa
           FROM cadenas c JOIN cadenas_clientes cc ON cc.cadena_id = c.id
           ORDER BY c.nombre, cc.empresa, cc.cliente_numero"""
    )
    mapa = {}
    for row in _dict_rows(cursor):
        numero = _normalizar_numero_cliente(row.get("cliente_numero"))
        if not numero:
            continue
        mapa.setdefault(numero, []).append({
            "id": row.get("id"),
            "nombre": str(row.get("nombre") or "").strip(),
            "empresa": _empresa_cadena_clave(row.get("empresa")),
        })
    return mapa


def _cliente_en_cadena(mapa_cadenas, numero_cliente, cadena_id, empresa=None):
    if not cadena_id:
        return True
    numero = _normalizar_numero_cliente(numero_cliente)
    empresa_clave = _empresa_cadena_clave(empresa)
    for item in mapa_cadenas.get(numero) or []:
        if int(item["id"]) == int(cadena_id):
            if not empresa_clave or item["empresa"] == empresa_clave:
                return True
    return False


def _filtrar_por_cadena(rows, cadena_id, mapa_cadenas):
    if not cadena_id:
        return list(rows or [])
    return [r for r in (rows or []) if _cliente_en_cadena(mapa_cadenas, r.get("numero_cliente"), cadena_id, r.get("empresa"))]


def _agrupar_por_cliente(rows):
    grupos = {}
    for row in rows or []:
        numero = _normalizar_numero_cliente(row.get("numero_cliente"))
        emp = str(row.get("empresa") or "").strip()
        key = (numero, emp)
        bucket = grupos.setdefault(key, {
            "numero_cliente": numero,
            "cliente_nombre": str(row.get("cliente_nombre") or "").strip(),
            "empresa": emp,
            "total": 0.0, "pagos_aplicados": 0.0, "saldo": 0.0, "saldo_vencido": 0.0,
            "vendedor": str(row.get("vendedor") or "").strip(),
        })
        bucket["cliente_nombre"] = bucket["cliente_nombre"] or str(row.get("cliente_nombre") or "").strip()
        bucket["vendedor"] = bucket["vendedor"] or str(row.get("vendedor") or "").strip()
        total = _to_float(row.get("total"))
        pagado = _to_float(row.get("pagos_aplicados"))
        saldo = _to_float(row.get("saldo"))
        bucket["total"] += total
        bucket["pagos_aplicados"] += pagado
        bucket["saldo"] += saldo
        if str(row.get("estatus_cobranza") or "").upper() == "VENCIDA":
            bucket["saldo_vencido"] += saldo
    out = []
    for _, item in grupos.items():
        for k in ("total", "pagos_aplicados", "saldo", "saldo_vencido"):
            item[k] = round(item[k], 2)
        out.append(item)
    out.sort(key=lambda x: (str(x["empresa"]).upper(), int("".join(c for c in x["numero_cliente"] if c.isdigit()) or 0), x["cliente_nombre"].upper()))
    return out


@router.get("/cartera-clientes")
def cartera_clientes(empresa: str | None = None, cadena_id: int | None = None, user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        mapa = _obtener_mapa_cadenas(cursor)
        cartera = _obtener_cartera(cursor, conn, empresa=empresa)
        cartera = _filtrar_por_cadena(cartera, cadena_id, mapa)
        return _agrupar_por_cliente(cartera)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/proximos-cobros-clientes")
def proximos_cobros_clientes(empresa: str | None = None, dias: int = 7, cadena_id: int | None = None, user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        hoy = datetime.now().date()
        mapa = _obtener_mapa_cadenas(cursor)
        cartera = _obtener_cartera(cursor, conn, empresa=empresa)
        cartera = [x for x in cartera if x["saldo"] > 0 and x.get("fecha_vencimiento") and 0 <= (x["fecha_vencimiento"] - hoy).days <= dias]
        cartera = _filtrar_por_cadena(cartera, cadena_id, mapa)
        return _agrupar_por_cliente(cartera)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _ensure_morosos_tables(cursor):
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS clientes_morosos (
            id INT AUTO_INCREMENT PRIMARY KEY, empresa VARCHAR(255) NOT NULL,
            cliente_numero VARCHAR(255) NOT NULL, cliente_nombre VARCHAR(255) DEFAULT '',
            motivo TEXT, activo TINYINT(1) NOT NULL DEFAULT 1,
            registrado_por VARCHAR(255) DEFAULT '',
            fecha_registro DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY ux_cliente_moroso (empresa, cliente_numero))"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS autorizaciones_pedidos (
            id INT AUTO_INCREMENT PRIMARY KEY, empresa VARCHAR(255) NOT NULL,
            cliente_numero VARCHAR(255) NOT NULL, cliente_nombre VARCHAR(255) DEFAULT '',
            vendedor VARCHAR(255) DEFAULT '', folio_solicitado VARCHAR(50) DEFAULT '',
            observaciones_pedido TEXT, comentario_solicitud TEXT,
            solicitado_por VARCHAR(255) DEFAULT '',
            estado VARCHAR(30) NOT NULL DEFAULT 'pendiente',
            autorizado_por VARCHAR(255) DEFAULT '', comentario_autorizacion TEXT,
            fecha_solicitud DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            fecha_resolucion DATETIME NULL, KEY idx_aut_cliente (empresa, cliente_numero, estado),
            KEY idx_aut_estado (estado, fecha_solicitud))"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS autorizaciones_pedidos_detalle (
            id INT AUTO_INCREMENT PRIMARY KEY, autorizacion_id INT NOT NULL,
            orden_fila INT NOT NULL DEFAULT 0, cip VARCHAR(255) DEFAULT '',
            descripcion TEXT, kgs DECIMAL(12,3) NOT NULL DEFAULT 0,
            piezas DECIMAL(12,3) NOT NULL DEFAULT 0, observaciones TEXT,
            KEY idx_autdet_aut (autorizacion_id, orden_fila))"""
    )


@router.get("/morosos-clientes")
def listar_morosos_clientes(user=Depends(require_user)):
    import app.legacy_db as db
    conn = cursor = None
    try:
        conn = db.get_morosos_connection()
        cursor = conn.cursor()
        _ensure_morosos_tables(cursor)
        cursor.execute(
            """SELECT id, empresa, cliente_numero, cliente_nombre,
                      COALESCE(motivo,'') AS motivo, activo,
                      COALESCE(registrado_por,'') AS registrado_por, fecha_registro
               FROM clientes_morosos ORDER BY empresa, CAST(cliente_numero AS UNSIGNED), cliente_numero"""
        )
        rows = _dict_rows(cursor)
        for r in rows:
            if r.get("fecha_registro") and hasattr(r["fecha_registro"], "strftime"):
                r["fecha_registro"] = r["fecha_registro"].strftime("%d/%m/%Y %H:%M")
        return rows
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.post("/morosos-clientes")
def guardar_moroso_cliente(payload: dict, user=Depends(require_user)):
    import app.legacy_db as db
    conn = cursor = None
    try:
        empresa = str(payload.get("empresa") or "").strip()
        cliente_numero = _normalizar_numero_cliente(payload.get("cliente_numero"))
        cliente_nombre = str(payload.get("cliente_nombre") or "").strip()
        motivo = str(payload.get("motivo") or "").strip()
        if not empresa or not cliente_numero or not cliente_nombre:
            raise HTTPException(status_code=400, detail="empresa, cliente_numero y cliente_nombre obligatorios")
        conn = db.get_morosos_connection()
        cursor = conn.cursor()
        _ensure_morosos_tables(cursor)
        cursor.execute(
            "INSERT INTO clientes_morosos (empresa, cliente_numero, cliente_nombre, motivo, activo, registrado_por) VALUES (%s,%s,%s,%s,1,%s) ON DUPLICATE KEY UPDATE cliente_nombre=VALUES(cliente_nombre), motivo=VALUES(motivo), activo=1, registrado_por=VALUES(registrado_por)",
            (empresa, cliente_numero, cliente_nombre, motivo, user["username"]),
        )
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.delete("/morosos-clientes/{moroso_id}")
def eliminar_moroso_cliente(moroso_id: int, user=Depends(require_user)):
    import app.legacy_db as db
    conn = cursor = None
    try:
        conn = db.get_morosos_connection()
        cursor = conn.cursor()
        _ensure_morosos_tables(cursor)
        cursor.execute("DELETE FROM clientes_morosos WHERE id = %s", (moroso_id,))
        conn.commit()
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/autorizaciones-pedidos")
def listar_autorizaciones(estado: str | None = None, user=Depends(require_user)):
    import app.legacy_db as db
    conn = cursor = None
    try:
        conn = db.get_morosos_connection()
        cursor = conn.cursor()
        _ensure_morosos_tables(cursor)
        sql = "SELECT id, fecha_solicitud, empresa, cliente_numero, cliente_nombre, vendedor, solicitado_por, estado FROM autorizaciones_pedidos"
        params = []
        e = str(estado or "").strip().lower()
        if e and e != "todos":
            sql += " WHERE estado = %s"
            params.append(e)
        sql += " ORDER BY CAST(cliente_numero AS UNSIGNED), empresa, fecha_solicitud DESC, id DESC"
        cursor.execute(sql, params)
        rows = _dict_rows(cursor)
        for r in rows:
            f = r.get("fecha_solicitud")
            if f and hasattr(f, "strftime"):
                r["fecha"] = f.strftime("%d/%m/%Y %H:%M")
            else:
                r["fecha"] = str(f or "")
            r.pop("fecha_solicitud", None)
        return rows
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/autorizaciones-pedidos/pendientes/count")
def contar_autorizaciones_pendientes(user=Depends(require_user)):
    import app.legacy_db as db
    conn = cursor = None
    try:
        conn = db.get_morosos_connection()
        cursor = conn.cursor()
        _ensure_morosos_tables(cursor)
        cursor.execute("SELECT COUNT(*) FROM autorizaciones_pedidos WHERE estado = 'pendiente'")
        row = cursor.fetchone()
        return {"pendientes": int(row[0] if row else 0)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/autorizaciones-pedidos/{aut_id}")
def detalle_autorizacion(aut_id: int, user=Depends(require_user)):
    import app.legacy_db as db
    conn = cursor = None
    try:
        conn = db.get_morosos_connection()
        cursor = conn.cursor()
        _ensure_morosos_tables(cursor)
        cursor.execute("SELECT * FROM autorizaciones_pedidos WHERE id = %s", (aut_id,))
        rows = _dict_rows(cursor)
        if not rows:
            raise HTTPException(status_code=404, detail="Autorizacion no encontrada")
        cursor.execute(
            "SELECT orden_fila, cip, descripcion, kgs, piezas, observaciones FROM autorizaciones_pedidos_detalle WHERE autorizacion_id = %s ORDER BY orden_fila, id",
            (aut_id,),
        )
        return {"autorizacion": rows[0], "detalle": _dict_rows(cursor)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.post("/autorizaciones-pedidos/{aut_id}/resolver")
def resolver_autorizacion(aut_id: int, payload: dict, user=Depends(require_user)):
    import app.legacy_db as db
    conn = cursor = None
    try:
        estado = str(payload.get("estado") or "").strip().lower()
        if estado not in ("aprobado", "rechazado"):
            raise HTTPException(status_code=400, detail="Estado invalido: aprobado o rechazado")
        conn = db.get_morosos_connection()
        cursor = conn.cursor()
        _ensure_morosos_tables(cursor)
        cursor.execute(
            "UPDATE autorizaciones_pedidos SET estado = %s, autorizado_por = %s, comentario_autorizacion = %s, fecha_resolucion = NOW() WHERE id = %s",
            (estado, str(payload.get("autorizado_por") or user["username"]).strip(), str(payload.get("comentario_autorizacion") or "").strip(), aut_id),
        )
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.delete("/autorizaciones-pedidos/{aut_id}")
def eliminar_autorizacion(aut_id: int, user=Depends(require_user)):
    import app.legacy_db as db
    conn = cursor = None
    try:
        conn = db.get_morosos_connection()
        cursor = conn.cursor()
        _ensure_morosos_tables(cursor)
        cursor.execute("DELETE FROM autorizaciones_pedidos_detalle WHERE autorizacion_id = %s", (aut_id,))
        cursor.execute("DELETE FROM autorizaciones_pedidos WHERE id = %s", (aut_id,))
        conn.commit()
        return {"ok": True}
    except Exception as exc:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _obtener_vendedor_cliente(cursor, empresa, numero_cliente):
    cursor.execute("SELECT vendedor FROM clientes WHERE numero = %s AND empresa = %s LIMIT 1", (str(numero_cliente).strip(), str(empresa).strip()))
    rows = _dict_rows(cursor)
    return str(rows[0]["vendedor"] or "").strip() if rows else ""


def _primer_nombre_cadena(mapa_cadenas, numero_cliente, empresa=None, cadena_id=None):
    numero = _normalizar_numero_cliente(numero_cliente)
    opciones = mapa_cadenas.get(numero) or []
    if not opciones:
        return ""
    emp_clave = _empresa_cadena_clave(empresa)
    if cadena_id:
        for item in opciones:
            if int(item["id"]) == int(cadena_id):
                if not emp_clave or item["empresa"] == emp_clave:
                    return item["nombre"]
        if emp_clave:
            return ""
    if emp_clave:
        for item in opciones:
            if item["empresa"] == emp_clave:
                return item["nombre"]
        return ""
    return opciones[0]["nombre"]


def _construir_bloques(cursor, conn, cartera, mapa_cadenas, corte=None, marcar_vencido=False, cadena_id=None):
    grupos = {}
    for row in cartera:
        numero = _normalizar_numero_cliente(row.get("numero_cliente"))
        empresa_row = str(row.get("empresa") or "").strip()
        vendedor = str(row.get("vendedor") or "").strip() or _obtener_vendedor_cliente(cursor, empresa_row, numero)
        fv = row.get("fecha_vencimiento")
        estatus_marca = "*VENCIDO" if (marcar_vencido and corte and fv and fv < corte) else ""
        cadena_nombre = _primer_nombre_cadena(mapa_cadenas, numero, empresa_row, cadena_id)
        cliente_nombre = str(row.get("cliente_nombre") or "").strip()
        bloque = cadena_nombre or cliente_nombre or numero
        if cadena_nombre:
            key = ("CADENA", f"CADENA|{cadena_nombre.upper()}")
        else:
            key = (vendedor.upper(), f"CLIENTE|{empresa_row.upper()}|{numero}|{bloque.upper()}")
        grupos.setdefault(key, {"vendedor": vendedor, "bloque": bloque,
            "tipo_bloque": "CADENA" if cadena_nombre else "CLIENTE", "empresa": empresa_row,
            "cliente_numero": numero, "rows": [], "total_restan": 0.0})
        grupos[key]["rows"].append({
            "factura": row.get("factura", ""), "fecha": str(row.get("fecha") or ""),
            "vencimiento": str(fv or ""), "cliente_numero": numero,
            "cliente_nombre": cliente_nombre, "restan": round(_to_float(row.get("saldo")), 2),
            "vendedor": vendedor, "estatus": estatus_marca,
        })
        grupos[key]["total_restan"] += grupos[key]["rows"][-1]["restan"]
    bloques = sorted(grupos.values(), key=lambda b: (
        0 if str(b["tipo_bloque"]).upper() == "CADENA" else 1,
        str(b["empresa"]).upper(), str(b["bloque"]).upper(),
    ))
    for b in bloques:
        b["rows"].sort(key=lambda r: (r["fecha"], r["factura"]))
        b["total_restan"] = round(b["total_restan"], 2)
    return bloques


@router.get("/reporte-morosos")
def reporte_morosos(fecha_corte: str, empresa: str | None = None, excluir_clientes: str | None = None, user=Depends(require_user)):
    conn = cursor = None
    try:
        try:
            corte = datetime.strptime(fecha_corte, "%Y-%m-%d").date()
        except Exception:
            raise HTTPException(status_code=400, detail="fecha_corte invalida (YYYY-MM-DD)")
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        mapa = _obtener_mapa_cadenas(cursor)
        cartera = _obtener_cartera(cursor, conn, empresa=empresa)
        cartera = [x for x in cartera if _to_float(x.get("saldo")) > 0]
        excl = set()
        for item in str(excluir_clientes or "").split(","):
            txt = item.strip()
            if txt:
                excl.add(txt.upper())
        if excl:
            cartera = [x for x in cartera if _normalizar_numero_cliente(x.get("numero_cliente")) not in excl]
        bloques = _construir_bloques(cursor, conn, cartera, mapa, corte=corte, marcar_vencido=True)
        return {"fecha_corte": fecha_corte, "bloques": bloques}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/reporte-por-vencer")
def reporte_por_vencer(empresa: str | None = None, anio: int | None = None, mes: int | None = None, user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        hoy = datetime.now().date()
        target_anio = anio if anio is not None else hoy.year
        target_mes = mes if mes is not None else hoy.month
        mapa = _obtener_mapa_cadenas(cursor)
        cartera = _obtener_cartera(cursor, conn, empresa=empresa)
        cartera = [x for x in cartera if _to_float(x.get("saldo")) > 0 and x.get("fecha_vencimiento") and x["fecha_vencimiento"].year == target_anio and x["fecha_vencimiento"].month == target_mes]
        bloques = _construir_bloques(cursor, conn, cartera, mapa, corte=hoy)
        return {"fecha_corte": hoy.isoformat(), "bloques": bloques}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


SERGIO_TIPO_MAP = {
    "R": "RESTAURANTES", "RESTAURANTE": "RESTAURANTES",
    "T": "TIENDAS", "TIENDA": "TIENDAS",
    "P": "PARTICULARES (OTROS)", "F": "FORANEOS",
    "A": "AMAZON", "L": "LIVERPOOL",
    "W": "WEB TOTAL", "WEB": "WEB TOTAL",
    "CA": "LAS CASTELLANAS", "D": "DISTRIBUIDORES",
}
SERGIO_CATEGORIAS_ORDEN = [
    "RESTAURANTES", "TIENDAS", "PARTICULARES (OTROS)", "FORANEOS",
    "AMAZON", "LIVERPOOL", "WEB TOTAL", "LAS CASTELLANAS", "DISTRIBUIDORES", "VARIOS",
]
GOURMET_GRUPOS_ORDEN = ["CITYS-FRESKOS", "SUPERAMAS + WAL MART", "SORIANA", "VARIOS"]
IBERSUR_GRUPOS_ORDEN = ["RESTAURANTES", "TIENDAS", "OTROS", "FORANEOS", "WEB"]


def _normalizar_texto(value):
    txt = str(value or "").strip().upper()
    for src, dst in {"Á":"A","É":"E","Í":"I","Ó":"O","Ú":"U","Ü":"U","Ñ":"N"}.items():
        txt = txt.replace(src, dst)
    return txt


def _load_sergio_config(cursor):
    cursor.execute("SELECT tipo_map, categorias_orden FROM para_sergio_config LIMIT 1")
    rows = _dict_rows(cursor)
    if rows:
        tm = rows[0]["tipo_map"]
        co = rows[0]["categorias_orden"]
        if isinstance(tm, str):
            tm = json.loads(tm)
        if isinstance(co, str):
            co = json.loads(co)
        return {"tipo_map": tm, "categorias_orden": co}
    return {"tipo_map": SERGIO_TIPO_MAP, "categorias_orden": SERGIO_CATEGORIAS_ORDEN}


def _tipo_sergio(tipo, tipo_map=None):
    t = str(tipo or "").strip().upper()
    if not t or t in {"-", "0", "H"}:
        return "VARIOS"
    m = tipo_map or SERGIO_TIPO_MAP
    return m.get(t, "VARIOS")


def _empresa_efectiva(factura, empresa):
    folio = str(factura or "").strip().upper()
    if folio.startswith("R"):
        return "Remision"
    return str(empresa or "").strip()


def _grupo_gourmet(nombre, tipo=""):
    txt = _normalizar_texto(nombre)
    if any(x in txt for x in ("CITY", "FRESKO", "FRESCOS", "FRESCO")):
        return "CITYS-FRESKOS"
    tipo_txt = _normalizar_texto(tipo)
    es_df = tipo_txt in {"D.F.", "D F", "DF", "CDMX", "CD. DE MEXICO", "CD DE MEXICO"}
    if es_df and any(x in txt for x in ("COMER", "SUMESA")):
        return "CITYS-FRESKOS"
    if any(x in txt for x in ("SUPERAMA", "WAL MART", "WALMART")):
        return "SUPERAMAS + WAL MART"
    if "SORIANA" in txt:
        return "SORIANA"
    return "VARIOS"


def _zona_gourmet(tipo, nombre=""):
    txt = _normalizar_texto(tipo)
    if txt in {"D.F.", "D F", "DF", "CDMX", "CD. DE MEXICO", "CD DE MEXICO"}:
        return "CDMX"
    if txt in {"F", "FORANEO", "FORANEA", "FORANEOS"}:
        return "FORANEO"
    nom = _normalizar_texto(nombre)
    if any(x in nom for x in (" DF", "D.F", "CDMX", "CDMX ")):
        return "CDMX"
    if any(x in nom for x in ("FORANEO", "FORANEOS", " FORANEA")):
        return "FORANEO"
    return ""


def _categoria_ibersur(tipo):
    txt = _normalizar_texto(tipo)
    if txt in {"R", "RESTAURANTE"}:
        return "RESTAURANTES"
    if txt in {"T", "TIENDA"}:
        return "TIENDAS"
    if txt in {"F", "FORANEO", "FORANEA", "FORANEOS"}:
        return "FORANEOS"
    if txt in {"W", "WEB"}:
        return "WEB"
    return "OTROS"


def _subgrupo_web(nombre):
    txt = _normalizar_texto(nombre)
    if any(x in txt for x in ("MERCADO LIBRE", "MERCADONI")):
        return "MERCADO LIBRE (MERCADONI)"
    return "OTROS ON LINE"


@router.get("/para-sergio/config")
def para_sergio_get_config(user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cfg = _load_sergio_config(cursor)
        return {"tipo_map": cfg["tipo_map"], "categorias_orden": cfg["categorias_orden"]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.put("/para-sergio/config")
def para_sergio_put_config(payload: dict, user=Depends(require_user)):
    conn = cursor = None
    try:
        tipo_map = payload.get("tipo_map") or {}
        categorias_orden = payload.get("categorias_orden") or []
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute("DELETE FROM para_sergio_config")
        cursor.execute(
            "INSERT INTO para_sergio_config (tipo_map, categorias_orden) VALUES (%s, %s)",
            (json.dumps(tipo_map), json.dumps(categorias_orden))
        )
        conn.commit()
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/para-sergio/vendedores")
def para_sergio_vendedores(empresa: str = "", user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        query = "SELECT DISTINCT vendedor FROM clientes WHERE vendedor IS NOT NULL AND TRIM(vendedor) <> ''"
        params = []
        if empresa:
            query += " AND UPPER(TRIM(empresa)) = UPPER(TRIM(%s))"
            params.append(empresa)
        query += " ORDER BY vendedor"
        cursor.execute(query, params)
        return [row[0] for row in cursor.fetchall()]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@router.get("/modelos")
def modelos_list(user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute("SELECT id, nombre, descripcion, componentes FROM modelos ORDER BY id")
        cols = [desc[0] for desc in cursor.description]
        rows = []
        for row in cursor.fetchall():
            r = dict(zip(cols, row))
            if isinstance(r.get("componentes"), str):
                r["componentes"] = json.loads(r["componentes"])
            rows.append(r)
        return rows
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try: cursor.close()
            except Exception: pass
        if conn:
            try: conn.close()
            except Exception: pass

@router.post("/modelos")
def modelos_create(payload: dict, user=Depends(require_user)):
    conn = cursor = None
    try:
        nombre = str(payload.get("nombre", "")).strip()
        if not nombre:
            raise HTTPException(status_code=400, detail="Nombre requerido")
        descripcion = str(payload.get("descripcion", "")).strip()
        componentes = payload.get("componentes", [])
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute(
            "INSERT INTO modelos (nombre, descripcion, componentes) VALUES (%s, %s, %s)",
            (nombre, descripcion, json.dumps(componentes))
        )
        conn.commit()
        modelo_id = cursor.lastrowid
        return {"id": modelo_id, "nombre": nombre, "descripcion": descripcion, "componentes": componentes}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try: cursor.close()
            except Exception: pass
        if conn:
            try: conn.close()
            except Exception: pass

@router.put("/modelos/{modelo_id}")
def modelos_update(modelo_id: int, payload: dict, user=Depends(require_user)):
    conn = cursor = None
    try:
        nombre = str(payload.get("nombre", "")).strip()
        if not nombre:
            raise HTTPException(status_code=400, detail="Nombre requerido")
        descripcion = str(payload.get("descripcion", "")).strip()
        componentes = payload.get("componentes", [])
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute("UPDATE modelos SET nombre=%s, descripcion=%s, componentes=%s WHERE id=%s",
                       (nombre, descripcion, json.dumps(componentes), modelo_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Modelo no encontrado")
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try: cursor.close()
            except Exception: pass
        if conn:
            try: conn.close()
            except Exception: pass

@router.delete("/modelos/{modelo_id}")
def modelos_delete(modelo_id: int, user=Depends(require_user)):
    conn = cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        cursor.execute("DELETE FROM modelos WHERE id=%s", (modelo_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Modelo no encontrado")
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try: cursor.close()
            except Exception: pass
        if conn:
            try: conn.close()
            except Exception: pass

@router.get("/para-sergio/resumen")
def para_sergio_resumen(empresa: str, anio: int, mes: int, agrupar_por: str = "categoria",
                        vendedor: str = "", cadena: str = "",
                        tipo_map: str = "", categorias_orden: str = "",
                        user=Depends(require_user)):
    conn = cursor = None
    try:
        empresa_txt = str(empresa or "").strip()
        if not empresa_txt or mes < 1 or mes > 12 or anio < 2000 or anio > 2100:
            raise HTTPException(status_code=400, detail="Parametros invalidos")
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_tables(cursor)
        _sergio_config = _load_sergio_config(cursor)
        _sergio_tipo_map = _sergio_config["tipo_map"]
        _sergio_cat_orden = _sergio_config["categorias_orden"]
        if tipo_map:
            try:
                parsed = json.loads(tipo_map)
                if isinstance(parsed, dict):
                    _sergio_tipo_map = parsed
            except Exception:
                pass
        if categorias_orden:
            try:
                parsed = json.loads(categorias_orden)
                if isinstance(parsed, list):
                    _sergio_cat_orden = parsed
            except Exception:
                pass
        empresa_norm = _normalizar_texto(empresa_txt)

        if "REMISION" in empresa_norm:
            sql = """SELECT f.factura, f.numero_cliente, f.empresa, f.total,
                COALESCE(NULLIF(TRIM(c.tipo),''), NULLIF(TRIM(c_eza.tipo),''), NULLIF(TRIM(c_ibe.tipo),''), '0') AS tipo,
                COALESCE(NULLIF(TRIM(c.vendedor),''), NULLIF(TRIM(c_eza.vendedor),''), NULLIF(TRIM(c_ibe.vendedor),''), '') AS vendedor,
                COALESCE(NULLIF(TRIM(c.nombre),''), NULLIF(TRIM(c_eza.nombre),''), NULLIF(TRIM(c_ibe.nombre),''), NULLIF(TRIM(f.consignatario),''), '') AS cliente_nombre
                FROM facturas f
                LEFT JOIN clientes c ON TRIM(CAST(c.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR)) AND UPPER(TRIM(c.empresa)) = UPPER(TRIM(f.empresa))
                LEFT JOIN clientes c_eza ON TRIM(CAST(c_eza.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR)) AND UPPER(TRIM(c_eza.empresa)) = 'EZA2007'
                LEFT JOIN clientes c_ibe ON TRIM(CAST(c_ibe.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR)) AND UPPER(TRIM(c_ibe.empresa)) = 'IBERSUR'
                WHERE YEAR(f.fecha) = %s AND MONTH(f.fecha) = %s AND UPPER(TRIM(COALESCE(f.estatus, 'Activa'))) = 'ACTIVA' AND UPPER(TRIM(COALESCE(f.factura,''))) LIKE 'R%%'"""
            params = (int(anio), int(mes))
        else:
            sql = """SELECT f.factura, f.numero_cliente, f.empresa, f.total,
                COALESCE(NULLIF(TRIM(c.tipo),''), NULLIF(TRIM(c_eza.tipo),''), NULLIF(TRIM(c_ibe.tipo),''), '0') AS tipo,
                COALESCE(NULLIF(TRIM(c.vendedor),''), NULLIF(TRIM(c_eza.vendedor),''), NULLIF(TRIM(c_ibe.vendedor),''), '') AS vendedor,
                COALESCE(NULLIF(TRIM(c.nombre),''), NULLIF(TRIM(c_eza.nombre),''), NULLIF(TRIM(c_ibe.nombre),''), NULLIF(TRIM(f.consignatario),''), '') AS cliente_nombre
                FROM facturas f
                LEFT JOIN clientes c ON TRIM(CAST(c.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR)) AND UPPER(TRIM(c.empresa)) = UPPER(TRIM(f.empresa))
                LEFT JOIN clientes c_eza ON TRIM(CAST(c_eza.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR)) AND UPPER(TRIM(c_eza.empresa)) = 'EZA2007'
                LEFT JOIN clientes c_ibe ON TRIM(CAST(c_ibe.numero AS CHAR)) = TRIM(CAST(f.numero_cliente AS CHAR)) AND UPPER(TRIM(c_ibe.empresa)) = 'IBERSUR'
                WHERE UPPER(TRIM(f.empresa)) = UPPER(TRIM(%s)) AND YEAR(f.fecha) = %s AND MONTH(f.fecha) = %s AND UPPER(TRIM(COALESCE(f.estatus, 'Activa'))) = 'ACTIVA'"""
            params = (empresa_txt, int(anio), int(mes))
        cursor.execute(sql, params)
        rows = _dict_rows(cursor)
        rows = [r for r in rows if _empresa_efectiva(r.get("factura", ""), r.get("empresa", "")).strip().upper() == empresa_txt.strip().upper()]
        import sys; print(f"[DEBUG] SQL rows: {len(rows)}, empresa_txt: {empresa_txt!r}", file=sys.stderr)

        vendedores_filtro = [x.strip().upper() for x in str(vendedor or "").split(",") if x.strip()] if vendedor else []
        if vendedores_filtro:
            rows = [r for r in rows if str(r.get("vendedor") or "").strip().upper() in vendedores_filtro]

        cadena_raw = str(cadena or "").strip()
        cadena_nombres = []
        if cadena_raw:
            if cadena_raw.startswith("["):
                try:
                    parsed = json.loads(cadena_raw)
                    if isinstance(parsed, list):
                        cadena_nombres = [str(n).strip().upper() for n in parsed if n]
                except Exception:
                    pass
            else:
                cadena_nombres = [cadena_raw.upper()]
        if cadena_nombres:
            mapa_cadenas_f = _obtener_mapa_cadenas(cursor)
            cadena_ids = set()
            for _num, items in mapa_cadenas_f.items():
                for item in items:
                    if any(cn in item["nombre"].upper() for cn in cadena_nombres):
                        cadena_ids.add(item["id"])
            rows = [r for r in rows if any(
                item["id"] in cadena_ids for item in (mapa_cadenas_f.get(_normalizar_numero_cliente(r.get("numero_cliente"))) or [])
                if item["empresa"] == _empresa_cadena_clave(r.get("empresa"))
            )]

        total_general = 0.0
        detalle = []
        agrupar = str(agrupar_por or "categoria").strip().lower()

        if agrupar == "vendedor":
            resumen = {}
            for r in rows:
                total = _to_float(r.get("total"))
                total_general += total
                vend = str(r.get("vendedor") or "").strip()
                if not vend:
                    vend = "SIN VENDEDOR"
                resumen[vend] = round(resumen.get(vend, 0.0) + total, 2)
                detalle.append({"factura": r.get("factura",""), "numero_cliente": str(r.get("numero_cliente","") or "").strip(), "cliente_nombre": r.get("cliente_nombre",""), "tipo": str(r.get("tipo","") or "").strip(), "vendedor": vend, "categoria": vend, "total": round(total, 2)})
            categorias = [{"nombre": n, "total": round(t, 2)} for n, t in sorted(resumen.items(), key=lambda x: -x[1])]
            modo = "vendedor"

        elif agrupar == "cadena":
            mapa_cadenas = _obtener_mapa_cadenas(cursor)
            cliente_cadena = {}
            for numero, items in mapa_cadenas.items():
                for item in items:
                    key = (numero, item["empresa"])
                    if key not in cliente_cadena:
                        cliente_cadena[key] = []
                    if item["nombre"] not in cliente_cadena[key]:
                        cliente_cadena[key].append(item["nombre"])
            resumen = {}
            for r in rows:
                total = _to_float(r.get("total"))
                total_general += total
                num = _normalizar_numero_cliente(r.get("numero_cliente"))
                emp = _empresa_cadena_clave(r.get("empresa"))
                cads = cliente_cadena.get((num, emp), ["SIN CADENA"])
                cad_nombre = cads[0] if cads else "SIN CADENA"
                resumen[cad_nombre] = round(resumen.get(cad_nombre, 0.0) + total, 2)
                detalle.append({"factura": r.get("factura",""), "numero_cliente": str(r.get("numero_cliente","") or "").strip(), "cliente_nombre": r.get("cliente_nombre",""), "tipo": str(r.get("tipo","") or "").strip(), "vendedor": r.get("vendedor",""), "categoria": cad_nombre, "cadena": cad_nombre, "total": round(total, 2)})
            categorias = [{"nombre": n, "total": round(t, 2)} for n, t in sorted(resumen.items(), key=lambda x: -x[1])]
            modo = "cadena"

        else:
            if "GOURMET" in empresa_norm:
                _has_cadena = any(x.startswith("cadena:") for x in _sergio_cat_orden)
                if _has_cadena:
                    _gourmet_orden = []
                    _gourmet_cadena_map = {}
                    for x in _sergio_cat_orden:
                        if x.startswith("vend:"): continue
                        name = x[7:] if x.startswith("cadena:") else x
                        if name not in _gourmet_orden:
                            _gourmet_orden.append(name)
                        if x.startswith("cadena:"):
                            _gourmet_cadena_map[name.upper()] = name
                else:
                    _gourmet_orden = GOURMET_GRUPOS_ORDEN
                _gourmet_mapa_cadenas = _obtener_mapa_cadenas(cursor) if _gourmet_cadena_map else None
                resumen = {k: 0.0 for k in _gourmet_orden}
                resumen_zona = {}
                for r in rows:
                    total = _to_float(r.get("total"))
                    total_general += total
                    grupo = _grupo_gourmet(r.get("cliente_nombre"), r.get("tipo"))
                    if _gourmet_cadena_map:
                        num = _normalizar_numero_cliente(r.get("numero_cliente"))
                        emp_clave = _empresa_cadena_clave(r.get("empresa"))
                        for item in _gourmet_mapa_cadenas.get(num) or []:
                            if item["empresa"] == emp_clave and item["nombre"].upper() in _gourmet_cadena_map:
                                grupo = _gourmet_cadena_map[item["nombre"].upper()]
                                break
                    zona = _zona_gourmet(r.get("tipo"), r.get("cliente_nombre"))
                    if grupo == "CITYS-FRESKOS" and zona == "FORANEO":
                        zona = "NACIONAL"
                    if grupo in resumen:
                        resumen[grupo] = round(resumen.get(grupo, 0.0) + total, 2)
                    if zona and not resumen_zona.get(grupo):
                        resumen_zona[grupo] = zona
                    detalle.append({"factura": r.get("factura",""), "numero_cliente": str(r.get("numero_cliente","") or "").strip(), "cliente_nombre": r.get("cliente_nombre",""), "tipo": str(r.get("tipo","") or "").strip(), "vendedor": r.get("vendedor",""), "categoria": grupo, "zona": zona, "total": round(total, 2)})
                categorias = [{"nombre": n, "zona": resumen_zona.get(n, ""), "total": round(resumen.get(n, 0.0), 2)} for n in _gourmet_orden]
                modo = "gourmet"
            elif "IBERSUR" in empresa_norm:
                resumen = {k: 0.0 for k in IBERSUR_GRUPOS_ORDEN}
                web_sub = {"MERCADO LIBRE (MERCADONI)": 0.0, "OTROS ON LINE": 0.0}
                for r in rows:
                    total = _to_float(r.get("total"))
                    total_general += total
                    cat = _categoria_ibersur(r.get("tipo"))
                    resumen[cat] = round(resumen.get(cat, 0.0) + total, 2)
                    if cat == "WEB":
                        wk = _subgrupo_web(r.get("cliente_nombre"))
                        web_sub[wk] = round(web_sub.get(wk, 0.0) + total, 2)
                    detalle.append({"factura": r.get("factura",""), "numero_cliente": str(r.get("numero_cliente","") or "").strip(), "cliente_nombre": r.get("cliente_nombre",""), "tipo": str(r.get("tipo","") or "").strip(), "vendedor": r.get("vendedor",""), "categoria": cat, "subcategoria": _subgrupo_web(r.get("cliente_nombre")) if cat == "WEB" else "", "total": round(total, 2)})
                categorias = [{"nombre": n, "zona": "", "total": round(resumen.get(n, 0.0), 2)} for n in IBERSUR_GRUPOS_ORDEN]
                modo = "ibersur"
            else:
                _sergio_cat_keys = set(x for x in _sergio_cat_orden if not x.startswith("vend:") and not x.startswith("cadena:"))
                _vend_map = {}
                _cadena_map_ord = {}
                mapa_cadenas_f = None
                for x in _sergio_cat_orden:
                    if x.startswith("vend:"):
                        _vend_map["vend:" + x[5:].strip().upper()] = x
                    elif x.startswith("cadena:"):
                        _cadena_map_ord[x[7:].strip()] = x
                if _cadena_map_ord:
                    mapa_cadenas_f = _obtener_mapa_cadenas(cursor)
                    cadena_ids_f = set()
                    for _num, items in mapa_cadenas_f.items():
                        for item in items:
                            if item["nombre"] in _cadena_map_ord:
                                cadena_ids_f.add(item["id"])
                    rows = [r for r in rows if any(
                        item["id"] in cadena_ids_f for item in (mapa_cadenas_f.get(_normalizar_numero_cliente(r.get("numero_cliente"))) or [])
                        if item["empresa"] == _empresa_cadena_clave(r.get("empresa"))
                    )]
                resumen = {k: 0.0 for k in _sergio_cat_orden}
                for r in rows:
                    total = _to_float(r.get("total"))
                    total_general += total
                    cat = _tipo_sergio(r.get("tipo"), _sergio_tipo_map)
                    if cat in _sergio_cat_keys:
                        resumen[cat] = round(resumen.get(cat, 0.0) + total, 2)
                    vend_raw = str(r.get("vendedor") or "").strip().upper()
                    vk = "vend:" + vend_raw
                    if vend_raw and vk in _vend_map:
                        orig_key = _vend_map[vk]
                        resumen[orig_key] = round(resumen.get(orig_key, 0.0) + total, 2)
                    if _cadena_map_ord and mapa_cadenas_f:
                        num = _normalizar_numero_cliente(r.get("numero_cliente"))
                        emp_clave = _empresa_cadena_clave(r.get("empresa"))
                        for item in mapa_cadenas_f.get(num) or []:
                            if item["empresa"] == emp_clave and item["nombre"] in _cadena_map_ord:
                                orig_key = _cadena_map_ord[item["nombre"]]
                                resumen[orig_key] = round(resumen.get(orig_key, 0.0) + total, 2)
                                break
                    detalle.append({"factura": r.get("factura",""), "numero_cliente": str(r.get("numero_cliente","") or "").strip(), "cliente_nombre": r.get("cliente_nombre",""), "tipo": str(r.get("tipo","") or "").strip(), "vendedor": r.get("vendedor",""), "categoria": cat, "total": round(total, 2)})
                categorias = []
                for n in _sergio_cat_orden:
                    isVend = n.startswith("vend:")
                    isCadena = n.startswith("cadena:")
                    dname = n[5:] if isVend else n[7:] if isCadena else n
                    categorias.append({"nombre": dname, "total": round(resumen.get(n, 0.0), 2), "is_vendedor": isVend, "is_cadena": isCadena})
                modo = "eza"

        payload = {"empresa": empresa_txt, "anio": int(anio), "mes": int(mes), "modo": modo, "agrupar_por": agrupar,
            "suma_total": round(sum(x["total"] for x in categorias), 2),
            "comprobar_mio": round(total_general, 2), "categorias": categorias, "detalle": detalle}
        if modo == "ibersur":
            rows_ui = [
                {"nombre": "SUMA TOTAL", "zona": "", "total": round(payload["suma_total"], 2), "tipo_row": "total"},
                {"nombre": "Comprobar: Viene de la pestana MIO -->", "zona": "", "total": round(total_general, 2), "tipo_row": "check"},
            ]
            rows_ui.extend({"nombre": x["nombre"], "zona": x["zona"], "total": x["total"], "tipo_row": "categoria"} for x in categorias)
            rows_ui.append({"nombre": "DESGLOSE DE LA WEB", "zona": "", "total": 0.0, "tipo_row": "section"})
            rows_ui.append({"nombre": "MERCADO LIBRE (MERCADONI)", "zona": "", "total": round(web_sub["MERCADO LIBRE (MERCADONI)"], 2), "tipo_row": "subcategoria"})
            rows_ui.append({"nombre": "OTROS ON LINE", "zona": "", "total": round(web_sub["OTROS ON LINE"], 2), "tipo_row": "subcategoria"})
            rows_ui.append({"nombre": "TOTAL WEB", "zona": "", "total": round(resumen.get("WEB", 0.0), 2), "tipo_row": "web_total"})
            payload["rows"] = rows_ui
        return payload
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass
