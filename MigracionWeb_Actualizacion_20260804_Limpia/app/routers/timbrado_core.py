import json
import os
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape as _xml_escape

from app.core.config import settings
from app.routers.timbrado_pac import PacNoIntegradoError, PacTimbradoError, obtener_material_csd, sellar_xml_cfdi, timbrar_xml_pac, validar_preflight_pac


ESTATUS_PENDIENTE = "PENDIENTE"
ESTATUS_TIMBRANDO = "TIMBRANDO"
ESTATUS_TIMBRADA = "TIMBRADA"
ESTATUS_ERROR = "ERROR"
ESTATUS_BLOQUEADO_PAC = "BLOQUEADO_PAC"


def _ahora_cfdi_mexico() -> datetime:
    """Hora de emisión CFDI en la zona fiscal mexicana, sin depender del DST de Windows."""
    return datetime.now(ZoneInfo("America/Mexico_City")).replace(tzinfo=None, microsecond=0)


def xml_escape(value) -> str:
    """Escapa texto utilizado dentro de atributos XML de CFDI.

    La función estándar no escapa comillas por defecto; eso puede invalidar una
    descripción de producto cuando contiene comillas dentro de un atributo XML.
    """
    return _xml_escape(str(value or ""), {'"': "&quot;", "'": "&apos;"})

LIMITES_PLACEHOLDERS_ADDENDA = {
    "RECEPCALLE": 50,
    "RECEPCOL": 50,
    "RECEPMUNICIPIO": 50,
    "RECEPESTADO": 50,
    "RECEPCP": 10,
    "RECEPRFC": 13,
    "EMISORNOMBRE": 50,
    "EMISORCALLE": 50,
    "EMISORCOL": 50,
    "EMISORMUNICIPIO": 50,
    "EMISORESTADO": 50,
    "EMISORCP": 10,
    "EMISORRFC": 13,
    "CONSIGNARNOMBRE": 50,
    "CONSIGNARDIRECCION": 50,
    "CONSIGNARCOLONIA": 50,
    "CONSIGNARPOBLA": 50,
    "CONSIGNARCODIGO": 10,
    "PRODALTERNA": 30,
    "PRODDESCRIP": 150,
    "PRODPRECIO": 18,
    "PRODSUBTOTAL": 18,
    "CANTIDAD": 18,
    "CLAVEPRODSERV": 10,
    "CLAVEUNIDAD": 5,
    "UNIDAD": 5,
    "NO_IDENTIFICACION": 30,
    "PRODPORCENIMP4": 10,
    "PRODMONTOIMP4": 18,
    "CONDICION": 30,
}

LIMITES_LINEAS_ADDENDA = {k: v for k, v in LIMITES_PLACEHOLDERS_ADDENDA.items() if k in {
    "PRODALTERNA", "PRODDESCRIP", "PRODPRECIO", "PRODSUBTOTAL", "CANTIDAD",
    "CLAVEPRODSERV", "CLAVEUNIDAD", "UNIDAD", "NO_IDENTIFICACION",
    "PRODPORCENIMP4", "PRODMONTOIMP4", "NUMPARTIDA", "CONDICION",
}}

PLACEHOLDERS_ADDENDA_POR_FACTURA = {
    "FACTURA", "FECHA_YYYYMMDD", "FECHA_YYMMDD", "HORA_HHMM", "HORA_HHMMSS",
    "FECHADOCTO(YYYYMMDD)", "FECHADOCTO(YYMMDD)", "HORADOCTO(HHMM)", "HORADOCTO(HHMMSS)",
    "GLN_BY", "GLN_ST", "GLN_SU",
}

# Estos datos provienen del acuse/recibo de cada entrega, por lo que no deben
# heredarse de otra factura ni sustituirse silenciosamente por la comanda.
CAMPOS_MANUALES_OBLIGATORIOS_ADDENDA = {
    "CF000NUEVA": ("CONDICION", "ENVIARADIRECCION"),
    # Walmart requiere que la referencia (RFF+DQ) y la fecha de recibo
    # (DTM+171) correspondan a cada entrega; no pueden derivarse de la salida.
    "WAJ01NUEVA": ("CONDICION", "ENVIARADIRECCION"),
    "W001NUEVA": ("CONDICION", "ENVIARADIRECCION"),
}

_TIMBRADO_SCHEMA_READY = False


def _ruta_addendas() -> Path:
    path = settings.storage_dir / "addendas"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ruta_base_fiscal() -> Path:
    path = settings.storage_dir / "cfdi"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ruta_empresa_fiscal(empresa: str) -> str:
    p = _ruta_base_fiscal() / _normalizar_empresa(empresa)
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def _texto_cmp(valor: str) -> str:
    valor = str(valor or "").strip().upper()
    valor = unicodedata.normalize("NFKD", valor)
    valor = "".join(ch for ch in valor if not unicodedata.combining(ch))
    return valor.replace("?", "N")


def _normalizar_empresa(valor: str) -> str:
    # SAE puede entregar el mismo nombre de empresa con espacios o guiones bajos.
    # La configuración fiscal debe resolverse igual en ambos casos.
    return " ".join(_texto_cmp(valor).replace("_", " ").split())


def _normalizar_clave_addenda(valor: str) -> str:
    v = str(valor or "").strip().upper().replace(" ", "_").replace("-", "_")
    return re.sub(r"[^A-Z0-9_]", "", v)


def _solo_digitos(valor) -> str:
    return "".join(ch for ch in str(valor or "") if ch.isdigit())


def _codigo_sat_3(valor) -> str:
    texto = str(valor or "").strip()
    match = re.search(r"\b(\d{3})\b", texto)
    return match.group(1) if match else texto[:3]


def _normalizar_codigo_barras(valor) -> str:
    texto = str(valor or "").strip()
    if not texto:
        return ""
    if re.fullmatch(r"\d+\.0+", texto):
        return texto.split(".", 1)[0]
    digitos = _solo_digitos(texto)
    if digitos and re.fullmatch(r"[\d\-. ]+", texto):
        return digitos
    return texto


def _fmt_num(valor, decimales: int = 2) -> str:
    try:
        return f"{float(valor or 0):.{decimales}f}"
    except (ValueError, TypeError):
        return f"{0:.{decimales}f}"


def _money_cfdi(valor) -> Decimal:
    try:
        return Decimal(str(valor or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _fmt_money_cfdi(valor) -> str:
    return f"{_money_cfdi(valor):.2f}"


def _fmt_valor_unitario_cfdi(valor) -> str:
    try:
        numero = Decimal(str(valor or 0)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    except Exception:
        numero = Decimal("0.000000")
    texto = f"{numero:.6f}".rstrip("0").rstrip(".")
    if "." not in texto:
        texto += ".00"
    return texto


def _fmt_cantidad(valor) -> str:
    try:
        s = f"{float(valor or 0):.6f}"
        return s.rstrip("0").rstrip(".") or "0"
    except (ValueError, TypeError):
        return "0"


def _truncar(texto: str, limite: int) -> str:
    t = str(texto or "")
    return t[:limite] if len(t) > limite else t


class _MySQLWrapper:
    def __init__(self, conn):
        self._conn = conn
        self.is_mysql = True

    def _cur(self):
        return self._conn.cursor(dictionary=True)

    def execute(self, sql, params=None):
        sql = self._traducir(sql)
        cur = self._cur()
        if params is not None:
            if isinstance(params, (list, tuple)):
                cur.execute(sql, params)
            else:
                cur.execute(sql, (params,))
        else:
            cur.execute(sql)
        self._last_result = cur
        return cur

    def executemany(self, sql, params_list):
        sql = self._traducir(sql)
        cur = self._cur()
        cur.executemany(sql, params_list)
        self._last_result = cur
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self.close()

    @staticmethod
    def _traducir(sql):
        if sql.strip().upper().startswith('PRAGMA'):
            return 'SELECT 1'
        sql = sql.replace('?', '%s')
        sql = sql.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'INT AUTO_INCREMENT PRIMARY KEY')
        sql = sql.replace("datetime('now','localtime')", 'NOW()')
        sql = re.sub(
            r"ON CONFLICT\([^)]+\) DO UPDATE SET\s+(.+)",
            lambda m: "ON DUPLICATE KEY UPDATE " + re.sub(
                r"\bexcluded\.(\w+)",
                r"VALUES(\1)",
                m.group(1)
            ),
            sql,
            flags=re.DOTALL
        )
        return sql


def get_timbrado_connection():
    from app.legacy_db import get_legacy_connection
    conn = get_legacy_connection()
    return _MySQLWrapper(conn)


def _asegurar_tabla_addenda_modelos(conn):
    if getattr(conn, "is_mysql", False):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS timbrado_addenda_modelos (
                clave VARCHAR(80) NOT NULL PRIMARY KEY,
                nombre VARCHAR(160) DEFAULT '',
                archivo VARCHAR(255) DEFAULT '',
                descripcion VARCHAR(500) DEFAULT '',
                contenido LONGTEXT NOT NULL,
                placeholders_json LONGTEXT,
                lineas_json LONGTEXT,
                origen VARCHAR(255) DEFAULT '',
                activo TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timbrado_addenda_modelos (
            clave TEXT NOT NULL PRIMARY KEY,
            nombre TEXT DEFAULT '',
            archivo TEXT DEFAULT '',
            descripcion TEXT DEFAULT '',
            contenido TEXT NOT NULL,
            placeholders_json TEXT DEFAULT '[]',
            lineas_json TEXT DEFAULT '[]',
            origen TEXT DEFAULT '',
            activo INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)


def _asegurar_columnas_empresas_timbrado(conn):
    try:
        rows = conn.execute("SHOW COLUMNS FROM empresas_timbrado").fetchall()
        columnas = {str(dict(row).get("Field") or "").lower() for row in rows}
        if "facturacion_automatica" not in columnas:
            conn.execute("ALTER TABLE empresas_timbrado ADD COLUMN facturacion_automatica TINYINT(1) NOT NULL DEFAULT 0")
        if "pac_cancel_passphrase" not in columnas:
            conn.execute("ALTER TABLE empresas_timbrado ADD COLUMN pac_cancel_passphrase TEXT")
        if "logo_archivo" not in columnas:
            conn.execute("ALTER TABLE empresas_timbrado ADD COLUMN logo_archivo VARCHAR(160) NOT NULL DEFAULT ''")
        if "serie_complemento_pago" not in columnas:
            conn.execute("ALTER TABLE empresas_timbrado ADD COLUMN serie_complemento_pago VARCHAR(20) NOT NULL DEFAULT 'PAG'")
        if "serie_nota_credito" not in columnas:
            conn.execute("ALTER TABLE empresas_timbrado ADD COLUMN serie_nota_credito VARCHAR(20) NOT NULL DEFAULT 'NC'")
    except Exception:
        try:
            rows = conn.execute("PRAGMA table_info(empresas_timbrado)").fetchall()
            columnas = {str(dict(row).get("name") or "").lower() for row in rows}
            if "facturacion_automatica" not in columnas:
                conn.execute("ALTER TABLE empresas_timbrado ADD COLUMN facturacion_automatica INTEGER DEFAULT 0")
            if "pac_cancel_passphrase" not in columnas:
                conn.execute("ALTER TABLE empresas_timbrado ADD COLUMN pac_cancel_passphrase TEXT DEFAULT ''")
            if "logo_archivo" not in columnas:
                conn.execute("ALTER TABLE empresas_timbrado ADD COLUMN logo_archivo TEXT DEFAULT ''")
            if "serie_complemento_pago" not in columnas:
                conn.execute("ALTER TABLE empresas_timbrado ADD COLUMN serie_complemento_pago TEXT DEFAULT 'PAG'")
            if "serie_nota_credito" not in columnas:
                conn.execute("ALTER TABLE empresas_timbrado ADD COLUMN serie_nota_credito TEXT DEFAULT 'NC'")
        except Exception:
            pass


def _asegurar_columnas_timbrado_queue(conn):
    try:
        rows = conn.execute("SHOW COLUMNS FROM timbrado_queue").fetchall()
        columnas = {str(dict(row).get("Field") or "").lower() for row in rows}
        if "cfdi_opciones_json" not in columnas:
            conn.execute("ALTER TABLE timbrado_queue ADD COLUMN cfdi_opciones_json LONGTEXT")
    except Exception:
        try:
            rows = conn.execute("PRAGMA table_info(timbrado_queue)").fetchall()
            columnas = {str(dict(row).get("name") or "").lower() for row in rows}
            if "cfdi_opciones_json" not in columnas:
                conn.execute("ALTER TABLE timbrado_queue ADD COLUMN cfdi_opciones_json TEXT DEFAULT '{}'")
        except Exception:
            pass


def _asegurar_historial_cfdi_emitidos(conn):
    """Permite conservar cada timbrado aunque la factura se vuelva a emitir.

    Un CFDI cancelado debe mantenerse como evidencia fiscal. La restricción
    histórica de un solo registro por ``factura_id`` hacía que una reemisión
    sustituyera el UUID, folio y estatus de la cancelación anterior.
    """
    if getattr(conn, "is_mysql", False):
        try:
            indices = conn.execute("SHOW INDEX FROM cfdi_emitidos").fetchall()
            for row in indices:
                data = dict(row)
                if (
                    str(data.get("Column_name") or "").lower() == "factura_id"
                    and int(data.get("Non_unique") or 0) == 0
                    and str(data.get("Key_name") or "") != "PRIMARY"
                ):
                    conn.execute(f"ALTER TABLE cfdi_emitidos DROP INDEX `{data['Key_name']}`")
                    break
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE cfdi_emitidos ADD INDEX idx_cfdi_factura_id (factura_id)")
        except Exception:
            pass
        try:
            columnas = {str(dict(row).get("Field") or "").lower() for row in conn.execute("SHOW COLUMNS FROM cfdi_emitidos").fetchall()}
            if "orden_compra" not in columnas:
                conn.execute("ALTER TABLE cfdi_emitidos ADD COLUMN orden_compra VARCHAR(100) DEFAULT ''")
        except Exception:
            pass
    else:
        try:
            columnas = {str(dict(row).get("name") or "").lower() for row in conn.execute("PRAGMA table_info(cfdi_emitidos)").fetchall()}
            if "orden_compra" not in columnas:
                conn.execute("ALTER TABLE cfdi_emitidos ADD COLUMN orden_compra TEXT DEFAULT ''")
        except Exception:
            pass


def _asegurar_tabla_cfdi_consolidacion_facturas(conn):
    """Relaciona cada factura interna con el CFDI consolidado que la contiene."""
    if getattr(conn, "is_mysql", False):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cfdi_consolidacion_facturas (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                cfdi_emitido_id BIGINT NOT NULL,
                factura_id BIGINT NOT NULL,
                factura VARCHAR(80) NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_cfdi_consolidacion_factura (cfdi_emitido_id, factura_id),
                KEY idx_cfdi_consolidacion_factura_id (factura_id)
            )
        """)
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cfdi_consolidacion_facturas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cfdi_emitido_id INTEGER NOT NULL,
            factura_id INTEGER NOT NULL,
            factura TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(cfdi_emitido_id, factura_id)
        )
    """)


def _asegurar_tabla_pac_intentos(conn):
    if getattr(conn, "is_mysql", False):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS timbrado_pac_intentos (
                id INT AUTO_INCREMENT PRIMARY KEY,
                factura_id INT NOT NULL DEFAULT 0,
                factura VARCHAR(80) DEFAULT '',
                empresa VARCHAR(120) DEFAULT '',
                proveedor VARCHAR(80) DEFAULT '',
                estatus VARCHAR(40) DEFAULT '',
                mensaje TEXT,
                folio_candidato VARCHAR(80) DEFAULT '',
                uuid VARCHAR(36) DEFAULT '',
                xml_path TEXT,
                response_json LONGTEXT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_pac_intentos_factura (factura),
                INDEX idx_pac_intentos_empresa_fecha (empresa, created_at)
            )
        """)
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timbrado_pac_intentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factura_id INTEGER DEFAULT 0,
            factura TEXT DEFAULT '',
            empresa TEXT DEFAULT '',
            proveedor TEXT DEFAULT '',
            estatus TEXT DEFAULT '',
            mensaje TEXT DEFAULT '',
            folio_candidato TEXT DEFAULT '',
            uuid TEXT DEFAULT '',
            xml_path TEXT DEFAULT '',
            response_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)


def _asegurar_tabla_correo_documentos(conn):
    if getattr(conn, "is_mysql", False):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS soporte_correo_documentos (
                id INT AUTO_INCREMENT PRIMARY KEY,
                empresa VARCHAR(120) NOT NULL DEFAULT '',
                tipo_documento VARCHAR(60) NOT NULL,
                nombre_remitente VARCHAR(160) DEFAULT '',
                correo_remitente VARCHAR(180) DEFAULT '',
                smtp_host VARCHAR(180) DEFAULT '',
                smtp_port INT DEFAULT 587,
                smtp_usuario VARCHAR(180) DEFAULT '',
                smtp_password VARCHAR(255) DEFAULT '',
                smtp_ssl TINYINT(1) DEFAULT 0,
                smtp_starttls TINYINT(1) DEFAULT 1,
                asunto_template VARCHAR(255) DEFAULT '',
                cuerpo_template TEXT,
                activo TINYINT(1) DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_correo_empresa_tipo (empresa, tipo_documento)
            )
        """)
        try:
            rows = conn.execute("SHOW COLUMNS FROM soporte_correo_documentos").fetchall()
            columnas = {str(dict(row).get("Field") or "").lower() for row in rows}
            if "empresa" not in columnas:
                conn.execute("ALTER TABLE soporte_correo_documentos ADD COLUMN empresa VARCHAR(120) NOT NULL DEFAULT '' AFTER id")
            if "asunto_template" not in columnas:
                conn.execute("ALTER TABLE soporte_correo_documentos ADD COLUMN asunto_template VARCHAR(255) DEFAULT ''")
            if "cuerpo_template" not in columnas:
                conn.execute("ALTER TABLE soporte_correo_documentos ADD COLUMN cuerpo_template TEXT")
        except Exception:
            pass
        try:
            indexes = conn.execute("SHOW INDEX FROM soporte_correo_documentos").fetchall()
            for row in indexes:
                d = dict(row)
                key = str(d.get("Key_name") or "")
                col = str(d.get("Column_name") or "")
                non_unique = int(d.get("Non_unique") or 0)
                if key != "PRIMARY" and non_unique == 0 and col == "tipo_documento" and key != "uq_correo_empresa_tipo":
                    conn.execute(f"ALTER TABLE soporte_correo_documentos DROP INDEX {key}")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE soporte_correo_documentos ADD UNIQUE KEY uq_correo_empresa_tipo (empresa, tipo_documento)")
        except Exception:
            pass
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS soporte_correo_documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa TEXT NOT NULL DEFAULT '',
            tipo_documento TEXT NOT NULL,
            nombre_remitente TEXT DEFAULT '',
            correo_remitente TEXT DEFAULT '',
            smtp_host TEXT DEFAULT '',
            smtp_port INTEGER DEFAULT 587,
            smtp_usuario TEXT DEFAULT '',
            smtp_password TEXT DEFAULT '',
            smtp_ssl INTEGER DEFAULT 0,
            smtp_starttls INTEGER DEFAULT 1,
            asunto_template TEXT DEFAULT '',
            cuerpo_template TEXT DEFAULT '',
            activo INTEGER DEFAULT 1,
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(empresa, tipo_documento)
        )
    """)
    try:
        rows = conn.execute("PRAGMA table_info(soporte_correo_documentos)").fetchall()
        columnas = {str(dict(row).get("name") or "").lower() for row in rows}
        if "empresa" not in columnas:
            conn.execute("ALTER TABLE soporte_correo_documentos ADD COLUMN empresa TEXT NOT NULL DEFAULT ''")
        if "asunto_template" not in columnas:
            conn.execute("ALTER TABLE soporte_correo_documentos ADD COLUMN asunto_template TEXT DEFAULT ''")
        if "cuerpo_template" not in columnas:
            conn.execute("ALTER TABLE soporte_correo_documentos ADD COLUMN cuerpo_template TEXT DEFAULT ''")
    except Exception:
        pass


def _asegurar_tabla_cfdi_defaults_clientes(conn):
    if getattr(conn, "is_mysql", False):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS timbrado_cfdi_defaults_clientes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                empresa VARCHAR(120) NOT NULL,
                numero_cliente VARCHAR(60) NOT NULL,
                nombre_cliente VARCHAR(240) DEFAULT '',
                uso_cfdi VARCHAR(5) DEFAULT '',
                forma_pago VARCHAR(5) DEFAULT '',
                metodo_pago VARCHAR(5) DEFAULT '',
                exportacion VARCHAR(5) DEFAULT '',
                condiciones_pago VARCHAR(160) DEFAULT '',
                moneda VARCHAR(5) DEFAULT 'MXN',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_cfdi_defaults_cliente (empresa, numero_cliente)
            )
        """)
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timbrado_cfdi_defaults_clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa TEXT NOT NULL,
            numero_cliente TEXT NOT NULL,
            nombre_cliente TEXT DEFAULT '',
            uso_cfdi TEXT DEFAULT '',
            forma_pago TEXT DEFAULT '',
            metodo_pago TEXT DEFAULT '',
            exportacion TEXT DEFAULT '',
            condiciones_pago TEXT DEFAULT '',
            moneda TEXT DEFAULT 'MXN',
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(empresa, numero_cliente)
        )
    """)


def _asegurar_tablas_timbrado(conn):
    global _TIMBRADO_SCHEMA_READY
    if _TIMBRADO_SCHEMA_READY:
        setattr(conn, "_timbrado_schema_ensured", True)
        return
    if getattr(conn, "_timbrado_schema_ensured", False):
        return
    if getattr(conn, 'is_mysql', False):
        _asegurar_tabla_addenda_modelos(conn)
        _asegurar_columnas_empresas_timbrado(conn)
        _asegurar_columnas_timbrado_queue(conn)
        _asegurar_historial_cfdi_emitidos(conn)
        _asegurar_tabla_cfdi_consolidacion_facturas(conn)
        _asegurar_tabla_correo_documentos(conn)
        _asegurar_tabla_cfdi_defaults_clientes(conn)
        _asegurar_tabla_pac_intentos(conn)
        setattr(conn, "_timbrado_schema_ensured", True)
        _TIMBRADO_SCHEMA_READY = True
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS empresas_timbrado (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa TEXT NOT NULL UNIQUE,
            timbrado_activo INTEGER DEFAULT 0,
            facturacion_automatica INTEGER DEFAULT 0,
            proveedor TEXT DEFAULT '',
            modo_pruebas INTEGER DEFAULT 1,
            rfc_emisor TEXT DEFAULT '',
            razon_social TEXT DEFAULT '',
            regimen_fiscal TEXT DEFAULT '',
            cp_fiscal TEXT DEFAULT '',
            calle TEXT DEFAULT '',
            no_exterior TEXT DEFAULT '',
            no_interior TEXT DEFAULT '',
            colonia TEXT DEFAULT '',
            municipio TEXT DEFAULT '',
            estado TEXT DEFAULT '',
            pais TEXT DEFAULT 'México',
            lugar_expedicion TEXT DEFAULT '',
            serie_cfdi TEXT DEFAULT '',
            serie_complemento_pago TEXT DEFAULT 'PAG',
            serie_nota_credito TEXT DEFAULT 'NC',
            folio_actual TEXT DEFAULT '',
            csd_cer_path TEXT DEFAULT '',
            csd_key_path TEXT DEFAULT '',
            csd_key_password TEXT DEFAULT '',
            gln_emisor_supplier TEXT DEFAULT '',
            pac_url TEXT DEFAULT '',
            pac_usuario TEXT DEFAULT '',
            pac_password TEXT DEFAULT '',
            pac_cancel_passphrase TEXT DEFAULT '',
            logo_archivo TEXT DEFAULT '',
            output_dir TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _asegurar_columnas_empresas_timbrado(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timbrado_addendas_clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa TEXT NOT NULL,
            numero_cliente TEXT NOT NULL,
            addenda_activa INTEGER DEFAULT 0,
            addenda_tipo TEXT DEFAULT '',
            addenda_config_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(empresa, numero_cliente)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timbrado_addenda_modelos (
            clave TEXT NOT NULL PRIMARY KEY,
            nombre TEXT DEFAULT '',
            archivo TEXT DEFAULT '',
            descripcion TEXT DEFAULT '',
            contenido TEXT NOT NULL,
            placeholders_json TEXT DEFAULT '[]',
            lineas_json TEXT DEFAULT '[]',
            origen TEXT DEFAULT '',
            activo INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timbrado_receptores_fiscales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa TEXT NOT NULL,
            clave_receptor TEXT NOT NULL,
            alias_receptor TEXT DEFAULT '',
            razon_social TEXT DEFAULT '',
            rfc TEXT DEFAULT '',
            regimen_fiscal TEXT DEFAULT '',
            cp_fiscal TEXT DEFAULT '',
            uso_cfdi TEXT DEFAULT '',
            calle TEXT DEFAULT '',
            no_exterior TEXT DEFAULT '',
            no_interior TEXT DEFAULT '',
            colonia TEXT DEFAULT '',
            municipio TEXT DEFAULT '',
            estado TEXT DEFAULT '',
            pais TEXT DEFAULT 'México',
            gln_receptor TEXT DEFAULT '',
            gln_consignatario TEXT DEFAULT '',
            gln_emisor_buyer TEXT DEFAULT '',
            dias_credito INTEGER,
            correo_envio TEXT DEFAULT '',
            addenda_activa INTEGER DEFAULT 0,
            addenda_tipo TEXT DEFAULT '',
            addenda_config_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(empresa, clave_receptor)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timbrado_consignatarios_clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa TEXT NOT NULL,
            cliente_numero TEXT NOT NULL,
            cliente_nombre TEXT DEFAULT '',
            gln_consignatario TEXT DEFAULT '',
            observaciones TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(empresa, cliente_numero)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timbrado_grupos_clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa TEXT NOT NULL,
            nombre_grupo TEXT NOT NULL,
            activa INTEGER DEFAULT 1,
            observaciones TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(empresa, nombre_grupo)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timbrado_grupos_clientes_detalle (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grupo_id INTEGER NOT NULL,
            cliente_numero TEXT NOT NULL,
            cliente_nombre TEXT DEFAULT '',
            FOREIGN KEY(grupo_id) REFERENCES timbrado_grupos_clientes(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timbrado_reglas_redireccion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa TEXT NOT NULL,
            activa INTEGER DEFAULT 1,
            prioridad INTEGER DEFAULT 100,
            patron_cliente TEXT DEFAULT '',
            grupo_id INTEGER,
            cliente_destino_piezas TEXT DEFAULT '',
            cliente_destino_kilos TEXT DEFAULT '',
            receptor_fiscal_piezas_clave TEXT DEFAULT '',
            receptor_fiscal_kilos_clave TEXT DEFAULT '',
            observaciones TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(empresa, patron_cliente)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timbrado_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factura_id INTEGER NOT NULL,
            factura TEXT DEFAULT '',
            empresa TEXT DEFAULT '',
            numero_cliente TEXT DEFAULT '',
            cliente_origen_numero TEXT DEFAULT '',
            cliente_origen_nombre TEXT DEFAULT '',
            cliente_receptor_numero TEXT DEFAULT '',
            cliente_receptor_nombre TEXT DEFAULT '',
            modo_facturacion TEXT DEFAULT '',
            regla_redireccion_id INTEGER,
            estatus TEXT DEFAULT 'PENDIENTE',
            requiere_addenda INTEGER DEFAULT 0,
            addenda_tipo TEXT DEFAULT '',
            addenda_payload_json TEXT DEFAULT '',
            cfdi_opciones_json TEXT DEFAULT '{}',
            proveedor TEXT DEFAULT '',
            prioridad INTEGER DEFAULT 0,
            intento_count INTEGER DEFAULT 0,
            uuid TEXT DEFAULT '',
            xml_path TEXT DEFAULT '',
            snapshot_path TEXT DEFAULT '',
            ultimo_error TEXT DEFAULT '',
            queued_at TEXT DEFAULT (datetime('now','localtime')),
            last_attempt_at TEXT
        )
    """)
    _asegurar_columnas_timbrado_queue(conn)
    _asegurar_tabla_cfdi_consolidacion_facturas(conn)
    _asegurar_tabla_correo_documentos(conn)
    _asegurar_tabla_cfdi_defaults_clientes(conn)
    _asegurar_tabla_pac_intentos(conn)
    setattr(conn, "_timbrado_schema_ensured", True)
    _TIMBRADO_SCHEMA_READY = True
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timbrado_factura_addenda_campos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factura_id INTEGER NOT NULL UNIQUE,
            factura TEXT DEFAULT '',
            empresa TEXT DEFAULT '',
            addenda_tipo TEXT DEFAULT '',
            campos_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cfdi_emitidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factura_id INTEGER NOT NULL UNIQUE,
            factura TEXT DEFAULT '',
            empresa TEXT DEFAULT '',
            cliente_receptor_numero TEXT DEFAULT '',
            cliente_receptor_nombre TEXT DEFAULT '',
            serie TEXT DEFAULT '',
            folio_cfdi TEXT DEFAULT '',
            uuid TEXT DEFAULT '',
            estatus_cfdi TEXT DEFAULT '',
            xml_path TEXT DEFAULT '',
            pdf_path TEXT DEFAULT '',
            addenda_tipo TEXT DEFAULT '',
            orden_compra TEXT DEFAULT '',
            fecha_timbrado TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS catalogo_sat_clave_prodserv (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clave TEXT NOT NULL UNIQUE,
            descripcion TEXT DEFAULT '',
            fuente TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS catalogo_sat_unidades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clave TEXT NOT NULL UNIQUE,
            nombre TEXT DEFAULT '',
            simbolo TEXT DEFAULT ''
        )
    """)


def obtener_config_timbrado(conn, empresa):
    try:
        row = conn.execute(
            "SELECT * FROM empresas_timbrado WHERE empresa = ? LIMIT 1",
            (_normalizar_empresa(empresa),),
        ).fetchone()
    except Exception:
        _asegurar_tablas_timbrado(conn)
        row = conn.execute(
            "SELECT * FROM empresas_timbrado WHERE empresa = ? LIMIT 1",
            (_normalizar_empresa(empresa),),
        ).fetchone()
    return dict(row) if row else {}


def guardar_config_timbrado(conn, empresa, datos):
    _asegurar_tablas_timbrado(conn)
    empresa = _normalizar_empresa(empresa)
    output_dir = ruta_empresa_fiscal(empresa)
    cp_fiscal = str(datos.get("cp_fiscal") or "").strip()
    lugar_expedicion = cp_fiscal or str(datos.get("lugar_expedicion") or "").strip()
    regimen_fiscal = _codigo_sat_3(datos.get("regimen_fiscal"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO empresas_timbrado (
            empresa, timbrado_activo, facturacion_automatica, proveedor, modo_pruebas,
            rfc_emisor, razon_social, regimen_fiscal, cp_fiscal,
            calle, no_exterior, no_interior, colonia, municipio, estado, pais,
            lugar_expedicion, serie_cfdi, serie_complemento_pago, serie_nota_credito, folio_actual,
            csd_cer_path, csd_key_path, csd_key_password, gln_emisor_supplier,
            pac_url, pac_usuario, pac_password, pac_cancel_passphrase, logo_archivo, output_dir, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(empresa) DO UPDATE SET
            timbrado_activo = excluded.timbrado_activo,
            facturacion_automatica = excluded.facturacion_automatica,
            proveedor = excluded.proveedor,
            modo_pruebas = excluded.modo_pruebas,
            rfc_emisor = excluded.rfc_emisor,
            razon_social = excluded.razon_social,
            regimen_fiscal = excluded.regimen_fiscal,
            cp_fiscal = excluded.cp_fiscal,
            calle = excluded.calle,
            no_exterior = excluded.no_exterior,
            no_interior = excluded.no_interior,
            colonia = excluded.colonia,
            municipio = excluded.municipio,
            estado = excluded.estado,
            pais = excluded.pais,
            lugar_expedicion = excluded.lugar_expedicion,
            serie_cfdi = excluded.serie_cfdi,
            serie_complemento_pago = excluded.serie_complemento_pago,
            serie_nota_credito = excluded.serie_nota_credito,
            folio_actual = excluded.folio_actual,
            csd_cer_path = excluded.csd_cer_path,
            csd_key_path = excluded.csd_key_path,
            csd_key_password = excluded.csd_key_password,
            gln_emisor_supplier = excluded.gln_emisor_supplier,
            pac_url = excluded.pac_url,
            pac_usuario = excluded.pac_usuario,
            pac_password = excluded.pac_password,
            pac_cancel_passphrase = excluded.pac_cancel_passphrase,
            logo_archivo = excluded.logo_archivo,
            output_dir = excluded.output_dir,
            updated_at = excluded.updated_at
        """,
        (
            empresa,
            1 if datos.get("timbrado_activo") else 0,
            1 if datos.get("facturacion_automatica") else 0,
            datos.get("proveedor"),
            1 if datos.get("modo_pruebas", True) else 0,
            datos.get("rfc_emisor"),
            datos.get("razon_social"),
            regimen_fiscal,
            cp_fiscal,
            datos.get("calle"),
            datos.get("no_exterior"),
            datos.get("no_interior"),
            datos.get("colonia"),
            datos.get("municipio"),
            datos.get("estado"),
            datos.get("pais"),
            lugar_expedicion,
            datos.get("serie_cfdi"),
            str(datos.get("serie_complemento_pago") or "PAG").strip() or "PAG",
            str(datos.get("serie_nota_credito") or "NC").strip() or "NC",
            datos.get("folio_actual"),
            datos.get("csd_cer_path"),
            datos.get("csd_key_path"),
            datos.get("csd_key_password"),
            datos.get("gln_emisor_supplier"),
            datos.get("pac_url"),
            datos.get("pac_usuario"),
            datos.get("pac_password"),
            datos.get("pac_cancel_passphrase"),
            str(datos.get("logo_archivo") or "").strip()[:160],
            output_dir,
            now,
        ),
    )


def obtener_addenda_cliente(conn, empresa, numero_cliente):
    _asegurar_tablas_timbrado(conn)
    row = conn.execute(
        "SELECT * FROM timbrado_addendas_clientes WHERE empresa = ? AND numero_cliente = ? AND addenda_activa = 1 LIMIT 1",
        (_normalizar_empresa(empresa), str(numero_cliente or "").strip()),
    ).fetchone()
    if row:
        row = dict(row)
        if row.get("addenda_config_json"):
            try:
                row["addenda_config"] = json.loads(row["addenda_config_json"])
            except Exception:
                row["addenda_config"] = {}
        row["addenda_tipo"] = _normalizar_clave_addenda(row.get("addenda_tipo"))
        return row
    return {}


def listar_addendas_clientes_configuradas(conn, empresa=None):
    _asegurar_tablas_timbrado(conn)
    empresa_norm = _normalizar_empresa(empresa) if empresa else ""
    registros = {}

    sql = "SELECT * FROM timbrado_addendas_clientes WHERE addenda_activa = 1"
    params = []
    if empresa_norm:
        sql += " AND empresa = ?"
        params.append(empresa_norm)
    for row in conn.execute(sql, tuple(params)).fetchall() or []:
        row = dict(row)
        numero = str(row.get("numero_cliente") or "").strip()
        if not numero:
            continue
        key = (row.get("empresa"), numero)
        try:
            cfg = json.loads(row.get("addenda_config_json") or "{}")
        except Exception:
            cfg = {}
        registros[key] = {
            "empresa": row.get("empresa"),
            "numero_cliente": numero,
            "nombre": "",
            "origen": "Cliente",
            "addenda_activa": 1,
            "addenda_tipo": _normalizar_clave_addenda(row.get("addenda_tipo")),
            "campos": len(cfg),
            "updated_at": row.get("updated_at"),
        }

    sql = "SELECT * FROM timbrado_receptores_fiscales WHERE addenda_activa = 1"
    params = []
    if empresa_norm:
        sql += " AND empresa = ?"
        params.append(empresa_norm)
    for row in conn.execute(sql, tuple(params)).fetchall() or []:
        row = dict(row)
        numero = str(row.get("clave_receptor") or "").strip()
        if not numero:
            continue
        key = (row.get("empresa"), numero)
        try:
            cfg = json.loads(row.get("addenda_config_json") or "{}")
        except Exception:
            cfg = {}
        registros[key] = {
            "empresa": row.get("empresa"),
            "numero_cliente": numero,
            "nombre": row.get("alias_receptor") or row.get("razon_social") or "",
            "origen": "Receptor fiscal",
            "addenda_activa": 1,
            "addenda_tipo": _normalizar_clave_addenda(row.get("addenda_tipo")),
            "campos": len(cfg),
            "updated_at": row.get("updated_at"),
        }

    return sorted(
        registros.values(),
        key=lambda r: (str(r.get("empresa") or ""), str(r.get("nombre") or ""), str(r.get("numero_cliente") or "")),
    )


def eliminar_addenda_cliente(conn, empresa, numero_cliente):
    _asegurar_tablas_timbrado(conn)
    conn.execute(
        "DELETE FROM timbrado_addendas_clientes WHERE empresa = ? AND numero_cliente = ?",
        (_normalizar_empresa(empresa), str(numero_cliente or "").strip()),
    )


def guardar_addenda_cliente(conn, empresa, numero_cliente, datos):
    _asegurar_tablas_timbrado(conn)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO timbrado_addendas_clientes (empresa, numero_cliente, addenda_activa, addenda_tipo, addenda_config_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(empresa, numero_cliente) DO UPDATE SET
            addenda_activa = excluded.addenda_activa,
            addenda_tipo = excluded.addenda_tipo,
            addenda_config_json = excluded.addenda_config_json,
            updated_at = excluded.updated_at
        """,
        (
            _normalizar_empresa(empresa),
            str(numero_cliente or "").strip(),
            1 if datos.get("addenda_activa", True) else 0,
            _normalizar_clave_addenda(datos.get("addenda_tipo")),
            json.dumps(datos.get("addenda_config") or {}, ensure_ascii=False),
            now,
        ),
    )


def _extraer_placeholders_addenda(contenido):
    texto = str(contenido or "")
    curly = re.findall(r"\{([\w\-().#]+)\}", texto)
    bracket = re.findall(r"\[([A-Za-z0-9_\-().#]+)\]", texto)
    return sorted(set(curly + bracket))


def _extraer_lineas_addenda(contenido):
    texto = str(contenido or "")
    encontrados = []
    m = re.search(r"<INILISTAPROD>(.*?)</INILISTAPROD>", texto, re.DOTALL | re.IGNORECASE)
    if m:
        encontrados.extend(_extraer_placeholders_addenda(m.group(1)))
    m2 = re.search(
        r"<!--\s*LINEAS_ADDENDA_START\s*-->(.*?)<!--\s*LINEAS_ADDENDA_END\s*-->",
        texto,
        re.DOTALL,
    )
    if m2:
        encontrados.extend(_extraer_placeholders_addenda(m2.group(1)))
    return sorted(set(encontrados))


def guardar_modelo_addenda(conn, clave, nombre, archivo, contenido, descripcion="", origen="", activo=True):
    _asegurar_tabla_addenda_modelos(conn)
    clave_norm = _normalizar_clave_addenda(clave or Path(str(archivo or "")).stem)
    if not clave_norm:
        raise ValueError("La clave del modelo de addenda es obligatoria.")
    contenido = str(contenido or "")
    if not contenido.strip():
        raise ValueError(f"El modelo {clave_norm} no tiene contenido.")
    placeholders = _extraer_placeholders_addenda(contenido)
    lineas = _extraer_lineas_addenda(contenido)
    conn.execute(
        """
        INSERT INTO timbrado_addenda_modelos (
            clave, nombre, archivo, descripcion, contenido,
            placeholders_json, lineas_json, origen, activo, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(clave) DO UPDATE SET
            nombre = excluded.nombre,
            archivo = excluded.archivo,
            descripcion = excluded.descripcion,
            contenido = excluded.contenido,
            placeholders_json = excluded.placeholders_json,
            lineas_json = excluded.lineas_json,
            origen = excluded.origen,
            activo = excluded.activo,
            updated_at = excluded.updated_at
        """,
        (
            clave_norm,
            str(nombre or clave_norm).strip(),
            str(archivo or "").strip(),
            str(descripcion or "").strip(),
            contenido,
            json.dumps(placeholders, ensure_ascii=False),
            json.dumps(lineas, ensure_ascii=False),
            str(origen or "").strip(),
            1 if activo else 0,
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    return {"clave": clave_norm, "placeholders": placeholders, "lineas": lineas}


def importar_modelos_addenda_desde_directorio(conn, directorio):
    base = Path(directorio)
    if not base.exists():
        raise FileNotFoundError(str(base))
    total = 0
    importados = []
    for archivo in sorted(base.glob("*.xml")):
        contenido = archivo.read_text(encoding="utf-8-sig")
        info = guardar_modelo_addenda(
            conn,
            archivo.stem,
            archivo.stem,
            archivo.name,
            contenido,
            descripcion=f"Modelo importado desde {archivo.name}",
            origen=str(base),
            activo=True,
        )
        importados.append(info)
        total += 1
    return {"total": total, "modelos": importados}


def _listar_addendas_servidor(include_template=False):
    try:
        with get_timbrado_connection() as conn:
            _asegurar_tabla_addenda_modelos(conn)
            cols = "clave, nombre, archivo, descripcion, placeholders_json, lineas_json"
            if include_template:
                cols += ", contenido"
            rows = conn.execute(
                f"SELECT {cols} FROM timbrado_addenda_modelos WHERE activo = 1 ORDER BY clave"
            ).fetchall()
    except Exception:
        rows = []
    addendas = []
    for row in rows or []:
        try:
            placeholders = json.loads(row.get("placeholders_json") or "[]")
        except Exception:
            placeholders = []
        try:
            lineas = json.loads(row.get("lineas_json") or "[]")
        except Exception:
            lineas = []
        item = {
            "clave": _normalizar_clave_addenda(row.get("clave")),
            "nombre": row.get("nombre") or row.get("clave"),
            "archivo": row.get("archivo") or "",
            "ruta": f"db://{_normalizar_clave_addenda(row.get('clave'))}",
            "placeholders": placeholders,
            "lineas": lineas,
            "descripcion": row.get("descripcion") or "",
            "origen": "servidor",
        }
        if include_template:
            item["template_xml"] = row.get("contenido") or ""
        addendas.append(item)
    return addendas


def listar_addendas_disponibles(include_template=False):
    server_addendas = _listar_addendas_servidor(include_template=include_template)
    if server_addendas:
        return server_addendas
    import glob as gmod
    addendas = []
    dir_path = _ruta_addendas()
    for fpath in sorted(gmod.glob(str(dir_path / "*.addenda.json"))):
        archivo = Path(fpath)
        try:
            with open(archivo, "r", encoding="utf-8") as f:
                template = json.load(f)
        except Exception:
            continue
        nom = archivo.stem.replace(".addenda", "")
        addendas.append({
            "clave": _normalizar_clave_addenda(nom),
            "nombre": nom,
            "archivo": archivo.name,
            "ruta": str(archivo),
            "placeholders": template.get("placeholders") or [],
            "lineas": template.get("lineas") or [],
            "descripcion": template.get("descripcion") or "",
        })
    return addendas


def listar_receptores_fiscales(conn, empresa=None):
    _asegurar_tablas_timbrado(conn)
    sql = "SELECT * FROM timbrado_receptores_fiscales"
    params = []
    if empresa:
        sql += " WHERE empresa = ?"
        params.append(_normalizar_empresa(empresa))
    sql += " ORDER BY empresa ASC, alias_receptor ASC, clave_receptor ASC"
    rows = conn.execute(sql, tuple(params)).fetchall()
    result = []
    for row in rows:
        row = dict(row)
        try:
            row["addenda_config"] = json.loads(row.get("addenda_config_json") or "{}")
        except Exception:
            row["addenda_config"] = {}
        row["addenda_tipo"] = _normalizar_clave_addenda(row.get("addenda_tipo"))
        result.append(row)
    return result


def obtener_receptor_fiscal(conn, empresa, clave_receptor):
    _asegurar_tablas_timbrado(conn)
    row = conn.execute(
        "SELECT * FROM timbrado_receptores_fiscales WHERE empresa = ? AND clave_receptor = ? LIMIT 1",
        (_normalizar_empresa(empresa), str(clave_receptor or "").strip()),
    ).fetchone()
    if row:
        row = dict(row)
        try:
            row["addenda_config"] = json.loads(row.get("addenda_config_json") or "{}")
        except Exception:
            row["addenda_config"] = {}
        row["addenda_tipo"] = _normalizar_clave_addenda(row.get("addenda_tipo"))
        return row
    return {}


def guardar_receptor_fiscal(conn, datos):
    _asegurar_tablas_timbrado(conn)
    empresa = _normalizar_empresa(datos.get("empresa"))
    clave = str(datos.get("clave_receptor") or "").strip()
    dias_credito = datos.get("dias_credito")
    try:
        dias_credito = int(str(dias_credito).strip()) if str(dias_credito or "").strip() else None
    except Exception as exc:
        raise ValueError("Los dias de credito deben ser un numero entero.") from exc
    if not empresa:
        raise ValueError("Falta la empresa del receptor.")
    if not clave:
        raise ValueError("Falta la clave del receptor fiscal.")
    if not str(datos.get("razon_social") or "").strip():
        raise ValueError("Falta la razón social del receptor fiscal.")
    if not str(datos.get("rfc") or "").strip():
        raise ValueError("Falta el RFC del receptor fiscal.")
    if not str(datos.get("regimen_fiscal") or "").strip():
        raise ValueError("Falta el régimen fiscal del receptor.")
    if not str(datos.get("cp_fiscal") or "").strip():
        raise ValueError("Falta el código postal fiscal del receptor.")
    if not str(datos.get("uso_cfdi") or "").strip():
        raise ValueError("Falta el uso CFDI del receptor.")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    addenda_activa = datos.get("addenda_activa")
    addenda_tipo = datos.get("addenda_tipo")
    addenda_config = datos.get("addenda_config")
    addenda_activa_set = addenda_activa is not None or addenda_tipo is not None or addenda_config is not None
    if addenda_activa_set:
        sql_insert = """
            INSERT INTO timbrado_receptores_fiscales (
                empresa, clave_receptor, alias_receptor, razon_social, rfc, regimen_fiscal,
                cp_fiscal, uso_cfdi, calle, no_exterior, no_interior, colonia, municipio,
                estado, pais, gln_receptor, gln_consignatario, gln_emisor_buyer, dias_credito, correo_envio,
                addenda_activa, addenda_tipo, addenda_config_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(empresa, clave_receptor) DO UPDATE SET
                alias_receptor = excluded.alias_receptor,
                razon_social = excluded.razon_social,
                rfc = excluded.rfc,
                regimen_fiscal = excluded.regimen_fiscal,
                cp_fiscal = excluded.cp_fiscal,
                uso_cfdi = excluded.uso_cfdi,
                calle = excluded.calle,
                no_exterior = excluded.no_exterior,
                no_interior = excluded.no_interior,
                colonia = excluded.colonia,
                municipio = excluded.municipio,
                estado = excluded.estado,
                pais = excluded.pais,
                gln_receptor = excluded.gln_receptor,
                gln_consignatario = excluded.gln_consignatario,
                gln_emisor_buyer = excluded.gln_emisor_buyer,
                dias_credito = excluded.dias_credito,
                correo_envio = excluded.correo_envio,
                addenda_activa = excluded.addenda_activa,
                addenda_tipo = excluded.addenda_tipo,
                addenda_config_json = excluded.addenda_config_json,
                updated_at = excluded.updated_at
            """
        params = (
            empresa, clave,
            str(datos.get("alias_receptor") or "").strip() or None,
            str(datos.get("razon_social") or "").strip(),
            str(datos.get("rfc") or "").strip(),
            str(datos.get("regimen_fiscal") or "").strip() or None,
            str(datos.get("cp_fiscal") or "").strip() or None,
            str(datos.get("uso_cfdi") or "").strip() or None,
            str(datos.get("calle") or "").strip() or None,
            str(datos.get("no_exterior") or "").strip() or None,
            str(datos.get("no_interior") or "").strip() or None,
            str(datos.get("colonia") or "").strip() or None,
            str(datos.get("municipio") or "").strip() or None,
            str(datos.get("estado") or "").strip() or None,
            str(datos.get("pais") or "").strip() or None,
            str(datos.get("gln_receptor") or "").strip() or None,
            None,
            str(datos.get("gln_emisor_buyer") or "").strip() or None,
            dias_credito,
            str(datos.get("correo_envio") or "").strip() or None,
            1 if addenda_activa else 0,
            _normalizar_clave_addenda(addenda_tipo) or None,
            json.dumps(addenda_config or {}, ensure_ascii=False),
            now,
        )
    else:
        sql_insert = """
            INSERT INTO timbrado_receptores_fiscales (
                empresa, clave_receptor, alias_receptor, razon_social, rfc, regimen_fiscal,
                cp_fiscal, uso_cfdi, calle, no_exterior, no_interior, colonia, municipio,
                estado, pais, gln_receptor, gln_consignatario, gln_emisor_buyer, dias_credito, correo_envio,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(empresa, clave_receptor) DO UPDATE SET
                alias_receptor = excluded.alias_receptor,
                razon_social = excluded.razon_social,
                rfc = excluded.rfc,
                regimen_fiscal = excluded.regimen_fiscal,
                cp_fiscal = excluded.cp_fiscal,
                uso_cfdi = excluded.uso_cfdi,
                calle = excluded.calle,
                no_exterior = excluded.no_exterior,
                no_interior = excluded.no_interior,
                colonia = excluded.colonia,
                municipio = excluded.municipio,
                estado = excluded.estado,
                pais = excluded.pais,
                gln_receptor = excluded.gln_receptor,
                gln_consignatario = excluded.gln_consignatario,
                gln_emisor_buyer = excluded.gln_emisor_buyer,
                dias_credito = excluded.dias_credito,
                correo_envio = excluded.correo_envio,
                updated_at = excluded.updated_at
            """
        params = (
            empresa, clave,
            str(datos.get("alias_receptor") or "").strip() or None,
            str(datos.get("razon_social") or "").strip(),
            str(datos.get("rfc") or "").strip(),
            str(datos.get("regimen_fiscal") or "").strip() or None,
            str(datos.get("cp_fiscal") or "").strip() or None,
            str(datos.get("uso_cfdi") or "").strip() or None,
            str(datos.get("calle") or "").strip() or None,
            str(datos.get("no_exterior") or "").strip() or None,
            str(datos.get("no_interior") or "").strip() or None,
            str(datos.get("colonia") or "").strip() or None,
            str(datos.get("municipio") or "").strip() or None,
            str(datos.get("estado") or "").strip() or None,
            str(datos.get("pais") or "").strip() or None,
            str(datos.get("gln_receptor") or "").strip() or None,
            None,
            str(datos.get("gln_emisor_buyer") or "").strip() or None,
            dias_credito,
            str(datos.get("correo_envio") or "").strip() or None,
            now,
        )
    conn.execute(sql_insert, params)


def importar_receptores_fiscales(conn, empresa, registros):
    """Valida el lote completo antes de guardar, evitando importaciones parciales."""
    _asegurar_tablas_timbrado(conn)
    empresa = _normalizar_empresa(empresa)
    if empresa != "EZA2007":
        raise ValueError("La importación masiva de receptores está habilitada únicamente para EZA2007.")
    if not isinstance(registros, list) or not registros:
        raise ValueError("No se recibieron registros para importar.")
    campos_requeridos = {
        "clave_receptor": "Clave receptor",
        "razon_social": "Razón social",
        "rfc": "RFC",
        "regimen_fiscal": "Régimen fiscal",
        "cp_fiscal": "CP fiscal",
        "uso_cfdi": "Uso CFDI",
    }
    preparados = []
    errores = []
    for indice, registro in enumerate(registros, start=2):
        item = dict(registro or {})
        item["empresa"] = empresa
        faltantes = [etiqueta for campo, etiqueta in campos_requeridos.items() if not str(item.get(campo) or "").strip()]
        if faltantes:
            errores.append(f"Fila {indice}: falta " + ", ".join(faltantes) + ".")
            continue
        try:
            dias = str(item.get("dias_credito") or "").strip()
            if dias:
                item["dias_credito"] = str(int(float(dias)))
        except Exception:
            errores.append(f"Fila {indice}: días de crédito debe ser un número entero.")
            continue
        preparados.append(item)
    for item in preparados:
        guardar_receptor_fiscal(conn, item)
    return {
        "importados": len(preparados),
        "omitidos": len(errores),
        "errores": errores,
    }


def eliminar_receptor_fiscal(conn, empresa, clave_receptor):
    _asegurar_tablas_timbrado(conn)
    conn.execute(
        "DELETE FROM timbrado_receptores_fiscales WHERE empresa = ? AND clave_receptor = ?",
        (_normalizar_empresa(empresa), str(clave_receptor or "").strip()),
    )


def listar_consignatarios_clientes(conn, empresa=None):
    _asegurar_tablas_timbrado(conn)
    sql = "SELECT empresa, cliente_numero, cliente_nombre, gln_consignatario, observaciones FROM timbrado_consignatarios_clientes"
    params = []
    if empresa:
        sql += " WHERE empresa = ?"
        params.append(_normalizar_empresa(empresa))
    sql += " ORDER BY empresa ASC, cliente_numero ASC"
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def obtener_consignatario_cliente(conn, empresa, cliente_numero):
    _asegurar_tablas_timbrado(conn)
    row = conn.execute(
        "SELECT empresa, cliente_numero, cliente_nombre, gln_consignatario, observaciones FROM timbrado_consignatarios_clientes WHERE empresa = ? AND cliente_numero = ? LIMIT 1",
        (_normalizar_empresa(empresa), str(cliente_numero or "").strip()),
    ).fetchone()
    return dict(row) if row else {}


def guardar_consignatario_cliente(conn, datos):
    _asegurar_tablas_timbrado(conn)
    empresa = _normalizar_empresa(datos.get("empresa"))
    cliente_numero = str(datos.get("cliente_numero") or "").strip()
    cliente_nombre = str(datos.get("cliente_nombre") or "").strip()
    if not empresa:
        raise ValueError("Falta la empresa del consignatario.")
    if not cliente_numero:
        raise ValueError("Falta el número de cliente.")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO timbrado_consignatarios_clientes (empresa, cliente_numero, cliente_nombre, gln_consignatario, observaciones, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(empresa, cliente_numero) DO UPDATE SET
            cliente_nombre = excluded.cliente_nombre,
            gln_consignatario = excluded.gln_consignatario,
            observaciones = excluded.observaciones,
            updated_at = excluded.updated_at
        """,
        (
            empresa,
            cliente_numero,
            cliente_nombre or None,
            str(datos.get("gln_consignatario") or "").strip() or None,
            str(datos.get("observaciones") or "").strip() or None,
            now,
        ),
    )


def eliminar_consignatario_cliente(conn, empresa, cliente_numero):
    _asegurar_tablas_timbrado(conn)
    conn.execute(
        "DELETE FROM timbrado_consignatarios_clientes WHERE empresa = ? AND cliente_numero = ?",
        (_normalizar_empresa(empresa), str(cliente_numero or "").strip()),
    )


def listar_productos_fiscales(conn, texto=None, limit=400):
    cur = conn.cursor(dictionary=True)
    try:
        sql = """
            SELECT CAST(cip AS CHAR) AS cip, descripcion, unidad, codigo_barras,
                   no_identificacion, clave_prod_serv, clave_unidad_sat
            FROM productos
        """
        params = []
        texto = str(texto or "").strip()
        if texto:
            like = f"%{texto}%"
            sql += " WHERE CAST(cip AS CHAR) LIKE %s OR descripcion LIKE %s OR codigo_barras LIKE %s OR no_identificacion LIKE %s"
            params.extend([like, like, like, like])
        sql += " ORDER BY descripcion ASC, cip ASC LIMIT %s"
        params.append(int(limit or 400))
        cur.execute(sql, tuple(params))
        return [dict(r) for r in (cur.fetchall() or [])]
    finally:
        cur.close()


def guardar_producto_fiscal(conn, datos):
    cur = conn.cursor(dictionary=True)
    try:
        cip = str(datos.get("cip") or "").strip()
        if not cip:
            raise ValueError("El CIP es obligatorio.")
        cur.execute("SELECT 1 FROM productos WHERE CAST(cip AS CHAR) = %s LIMIT 1", (cip,))
        if cur.fetchone() is None:
            raise ValueError(f"No se encontró el producto {cip}.")
        cur.execute(
            """
            UPDATE productos
            SET unidad = %s, codigo_barras = %s, no_identificacion = %s, clave_prod_serv = %s, clave_unidad_sat = %s
            WHERE CAST(cip AS CHAR) = %s
            """,
            (
                str(datos.get("unidad") or "").strip() or None,
                str(datos.get("codigo_barras") or "").strip() or None,
                str(datos.get("no_identificacion") or "").strip() or None,
                str(datos.get("clave_prod_serv") or "").strip() or None,
                str(datos.get("clave_unidad_sat") or "").strip() or None,
                cip,
            ),
        )
        conn.commit()
    finally:
        cur.close()


def guardar_productos_fiscales_lote(conn, registros):
    cur = conn.cursor(dictionary=True)
    try:
        filas = []
        vistos = set()
        for item in (registros or []):
            cip = str((item or {}).get("cip") or "").strip()
            if not cip or cip.lower() == "nan":
                continue
            if cip in vistos:
                continue
            vistos.add(cip)
            filas.append((
                str((item or {}).get("unidad") or "").strip() or None,
                str((item or {}).get("clave_prod_serv") or "").strip() or None,
                str((item or {}).get("clave_unidad_sat") or "").strip() or None,
                cip,
            ))
        if not filas:
            raise ValueError("No se encontraron registros válidos para importar.")
        cips = [fila[3] for fila in filas]
        marcadores = ",".join(["%s"] * len(cips))
        cur.execute(f"SELECT CAST(cip AS CHAR) AS cip FROM productos WHERE CAST(cip AS CHAR) IN ({marcadores})", tuple(cips))
        existentes = {str(r["cip"]) for r in (cur.fetchall() or [])}
        filas_ok = [fila for fila in filas if fila[3] in existentes]
        omitidos = [fila[3] for fila in filas if fila[3] not in existentes]
        if not filas_ok:
            raise ValueError("Ningún CIP del archivo existe en la base de productos.")
        cur.executemany(
            """
            UPDATE productos
            SET unidad = %s, clave_prod_serv = %s, clave_unidad_sat = %s
            WHERE CAST(cip AS CHAR) = %s
            """,
            filas_ok,
        )
        conn.commit()
        return {"actualizados": len(filas_ok), "omitidos": omitidos}
    finally:
        cur.close()


def listar_catalogo_sat_prodserv(conn, texto=None, limit=500):
    sql = "SELECT clave, descripcion, fuente FROM catalogo_sat_clave_prodserv"
    params = []
    texto = str(texto or "").strip()
    if texto:
        like = f"%{texto}%"
        sql += " WHERE clave LIKE ? OR descripcion LIKE ?"
        params.extend([like, like])
    sql += " ORDER BY clave ASC LIMIT ?"
    params.append(int(limit or 500))
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def importar_catalogo_sat_prodserv(conn, registros, fuente=None):
    datos = []
    for item in (registros or []):
        clave = str((item or {}).get("clave") or "").strip()
        descripcion = str((item or {}).get("descripcion") or "").strip()
        if not clave or not descripcion:
            continue
        datos.append((clave, descripcion[:500], str(fuente or "").strip() or None))
    if not datos:
        raise ValueError("No se encontraron registros válidos para importar.")
    conn.executemany(
        """
        INSERT INTO catalogo_sat_clave_prodserv (clave, descripcion, fuente)
        VALUES (?, ?, ?)
        ON CONFLICT(clave) DO UPDATE SET
            descripcion = excluded.descripcion,
            fuente = excluded.fuente
        """,
        datos,
    )
    return len(datos)


def listar_catalogo_sat_unidades(conn, texto=None, limit=200):
    sql = "SELECT clave, nombre, simbolo FROM catalogo_sat_unidades"
    params = []
    texto = str(texto or "").strip()
    if texto:
        like = f"%{texto}%"
        sql += " WHERE clave LIKE ? OR nombre LIKE ? OR simbolo LIKE ?"
        params.extend([like, like, like])
    sql += " ORDER BY clave ASC LIMIT ?"
    params.append(int(limit or 200))
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def importar_catalogo_sat_unidades(conn, registros):
    datos = []
    for item in (registros or []):
        clave = str((item or {}).get("clave") or "").strip()
        nombre = str((item or {}).get("nombre") or "").strip()
        if not clave:
            continue
        simbolo = str((item or {}).get("simbolo") or "").strip()
        datos.append((clave[:20], nombre[:255], simbolo[:20] or None))
    if not datos:
        raise ValueError("No se encontraron registros válidos para importar.")
    conn.executemany(
        """
        INSERT INTO catalogo_sat_unidades (clave, nombre, simbolo)
        VALUES (?, ?, ?)
        ON CONFLICT(clave) DO UPDATE SET
            nombre = excluded.nombre,
            simbolo = excluded.simbolo
        """,
        datos,
    )
    return len(datos)


def listar_grupos_clientes_timbrado(conn, empresa=None):
    _asegurar_tablas_timbrado(conn)
    sql = """
        SELECT g.id, g.empresa, g.nombre_grupo, g.activa, g.observaciones,
               COUNT(d.id) AS total_clientes
        FROM timbrado_grupos_clientes g
        LEFT JOIN timbrado_grupos_clientes_detalle d ON d.grupo_id = g.id
    """
    params = []
    if empresa:
        sql += " WHERE g.empresa = ?"
        params.append(_normalizar_empresa(empresa))
    sql += " GROUP BY g.id, g.empresa, g.nombre_grupo, g.activa, g.observaciones ORDER BY g.empresa ASC, g.nombre_grupo ASC"
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def buscar_cliente_nombre(conn, empresa, numero):
    _asegurar_tablas_timbrado(conn)
    try:
        row = conn.execute(
            "SELECT nombre FROM clientes WHERE CAST(numero AS CHAR) = ? AND UPPER(TRIM(empresa)) = UPPER(TRIM(?)) LIMIT 1",
            (str(numero or "").strip(), empresa),
        ).fetchone()
        return row.get("nombre") if row else ""
    except Exception:
        return ""


def buscar_clientes_por_term(conn, empresa, term):
    _asegurar_tablas_timbrado(conn)
    try:
        term = str(term or "").strip()
        if not term:
            return []
        pattern = f"%{term}%"
        rows = conn.execute(
            "SELECT CAST(numero AS CHAR) AS numero, nombre FROM clientes WHERE UPPER(TRIM(empresa)) = UPPER(TRIM(?)) AND (CAST(numero AS CHAR) LIKE ? OR nombre LIKE ?) LIMIT 20",
            (empresa, pattern, pattern),
        ).fetchall()
        return [{"numero": r["numero"], "nombre": r["nombre"]} for r in rows]
    except Exception:
        return []


def obtener_grupo_clientes_timbrado(conn, grupo_id):
    _asegurar_tablas_timbrado(conn)
    row = conn.execute("SELECT * FROM timbrado_grupos_clientes WHERE id = ? LIMIT 1", (int(grupo_id),)).fetchone()
    if not row:
        return {}
    grupo = dict(row)
    det = conn.execute(
        "SELECT cliente_numero, cliente_nombre FROM timbrado_grupos_clientes_detalle WHERE grupo_id = ? ORDER BY cliente_numero",
        (int(grupo_id),),
    ).fetchall()
    grupo["clientes"] = [dict(r) for r in det]
    clientes_sin_nombre = [cli for cli in grupo["clientes"] if not cli.get("cliente_nombre") and cli.get("cliente_numero")]
    if clientes_sin_nombre:
        empresa = grupo.get("empresa", "")
        numeros = [str(cli["cliente_numero"]).strip() for cli in clientes_sin_nombre]
        placeholders = ",".join(["?"] * len(numeros))
        batch = conn.execute(
            f"SELECT CAST(numero AS CHAR) AS numero_str, nombre FROM clientes WHERE UPPER(TRIM(empresa)) = UPPER(TRIM(?)) AND CAST(numero AS CHAR) IN ({placeholders})",
            (empresa, *numeros),
        ).fetchall()
        nombre_por_numero = {str(r["numero_str"]).strip(): r["nombre"] for r in batch}
        for cli in clientes_sin_nombre:
            cli["cliente_nombre"] = nombre_por_numero.get(str(cli["cliente_numero"]).strip(), "")
    return grupo


def guardar_grupo_clientes_timbrado(conn, datos):
    _asegurar_tablas_timbrado(conn)
    grupo_id = datos.get("id")
    empresa = _normalizar_empresa(datos.get("empresa"))
    nombre = str(datos.get("nombre_grupo") or "").strip()
    if not empresa:
        raise ValueError("Falta la empresa del grupo.")
    if not nombre:
        raise ValueError("Falta el nombre del grupo.")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if grupo_id:
        conn.execute(
            "UPDATE timbrado_grupos_clientes SET empresa=?, nombre_grupo=?, activa=?, observaciones=?, updated_at=? WHERE id=?",
            (empresa, nombre, 1 if datos.get("activa", True) else 0, str(datos.get("observaciones") or "").strip() or None, now, int(grupo_id)),
        )
        grupo_pk = int(grupo_id)
    else:
        conn.execute(
            """
            INSERT INTO timbrado_grupos_clientes (empresa, nombre_grupo, activa, observaciones, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(empresa, nombre_grupo) DO UPDATE SET activa = excluded.activa, observaciones = excluded.observaciones, updated_at = excluded.updated_at
            """,
            (empresa, nombre, 1 if datos.get("activa", True) else 0, str(datos.get("observaciones") or "").strip() or None, now),
        )
        grupo_pk = conn.execute(
            "SELECT id FROM timbrado_grupos_clientes WHERE empresa = ? AND nombre_grupo = ? LIMIT 1",
            (empresa, nombre),
        ).fetchone()
        grupo_pk = int(grupo_pk["id"]) if grupo_pk else None

    if grupo_pk:
        conn.execute("DELETE FROM timbrado_grupos_clientes_detalle WHERE grupo_id = ?", (grupo_pk,))
        for cli in (datos.get("clientes") or []):
            numero = str((cli or {}).get("cliente_numero") or "").strip()
            if not numero:
                continue
            conn.execute(
                "INSERT INTO timbrado_grupos_clientes_detalle (grupo_id, cliente_numero, cliente_nombre) VALUES (?, ?, ?)",
                (grupo_pk, numero, str((cli or {}).get("cliente_nombre") or "").strip() or None),
            )
    return grupo_pk


def eliminar_grupo_clientes_timbrado(conn, grupo_id):
    _asegurar_tablas_timbrado(conn)
    conn.execute("DELETE FROM timbrado_grupos_clientes_detalle WHERE grupo_id = ?", (int(grupo_id),))
    conn.execute("DELETE FROM timbrado_grupos_clientes WHERE id = ?", (int(grupo_id),))


def listar_reglas_redireccion(conn, empresa=None):
    _asegurar_tablas_timbrado(conn)
    sql = """
        SELECT id, empresa, activa, prioridad, patron_cliente, grupo_id,
               cliente_destino_piezas, cliente_destino_kilos,
               receptor_fiscal_piezas_clave, receptor_fiscal_kilos_clave,
               observaciones, created_at, updated_at
        FROM timbrado_reglas_redireccion
    """
    params = []
    if empresa:
        sql += " WHERE empresa = ?"
        params.append(_normalizar_empresa(empresa))
    sql += " ORDER BY prioridad ASC, patron_cliente ASC, id ASC"
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def _grupos_con_cliente(conn, grupo_ids, numero_cliente):
    grupo_ids = sorted({int(gid) for gid in (grupo_ids or []) if str(gid or "").strip()})
    numero = str(numero_cliente or "").strip()
    if not grupo_ids or not numero:
        return set()
    placeholders = ",".join(["?"] * len(grupo_ids))
    rows = conn.execute(
        f"""
        SELECT DISTINCT grupo_id
        FROM timbrado_grupos_clientes_detalle
        WHERE cliente_numero = ? AND grupo_id IN ({placeholders})
        """,
        (numero, *grupo_ids),
    ).fetchall()
    return {int(r["grupo_id"]) for r in rows}


def guardar_regla_redireccion(conn, datos):
    _asegurar_tablas_timbrado(conn)
    regla_id = datos.get("id")
    empresa = _normalizar_empresa(datos.get("empresa"))
    patron = str(datos.get("patron_cliente") or "").strip()
    grupo_id = int(datos.get("grupo_id")) if str(datos.get("grupo_id") or "").strip() else None
    if not empresa:
        raise ValueError("Falta la empresa de la regla.")
    if not patron and not grupo_id:
        raise ValueError("Falta el grupo o el texto del cliente origen.")
    if not patron and grupo_id:
        patron = f"__GRUPO__:{grupo_id}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    payload = (
        empresa,
        1 if datos.get("activa", True) else 0,
        int(datos.get("prioridad") or 100),
        patron,
        grupo_id,
        str(datos.get("cliente_destino_piezas") or "").strip() or None,
        str(datos.get("cliente_destino_kilos") or "").strip() or None,
        str(datos.get("receptor_fiscal_piezas_clave") or "").strip() or None,
        str(datos.get("receptor_fiscal_kilos_clave") or "").strip() or None,
        str(datos.get("observaciones") or "").strip() or None,
        now,
    )
    if regla_id:
        conn.execute(
            """
            UPDATE timbrado_reglas_redireccion
            SET empresa=?, activa=?, prioridad=?, patron_cliente=?, grupo_id=?,
                cliente_destino_piezas=?, cliente_destino_kilos=?,
                receptor_fiscal_piezas_clave=?, receptor_fiscal_kilos_clave=?,
                observaciones=?, updated_at=?
            WHERE id=?
            """,
            payload + (int(regla_id),),
        )
    else:
        conn.execute(
            """
            INSERT INTO timbrado_reglas_redireccion (
                empresa, activa, prioridad, patron_cliente, grupo_id,
                cliente_destino_piezas, cliente_destino_kilos,
                receptor_fiscal_piezas_clave, receptor_fiscal_kilos_clave, observaciones, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(empresa, patron_cliente) DO UPDATE SET
                activa = excluded.activa,
                prioridad = excluded.prioridad,
                grupo_id = excluded.grupo_id,
                cliente_destino_piezas = excluded.cliente_destino_piezas,
                cliente_destino_kilos = excluded.cliente_destino_kilos,
                receptor_fiscal_piezas_clave = excluded.receptor_fiscal_piezas_clave,
                receptor_fiscal_kilos_clave = excluded.receptor_fiscal_kilos_clave,
                observaciones = excluded.observaciones,
                updated_at = excluded.updated_at
            """,
            payload,
        )


def eliminar_regla_redireccion(conn, regla_id):
    _asegurar_tablas_timbrado(conn)
    conn.execute("DELETE FROM timbrado_reglas_redireccion WHERE id = ?", (int(regla_id),))


def _obtener_cliente_por_numero(conn_legacy, empresa, numero_cliente):
    cur = conn_legacy.cursor(dictionary=True)
    try:
        empresa_norm = _texto_cmp(empresa)
        numero_norm = str(numero_cliente or "").strip()
        cur.execute(
            """
            SELECT numero, nombre, empresa, razon_social, rfc, codigo_postal, consignatario,
                   calle, no_exterior, no_interior, colonia, municipio, estado, pais,
                   consig_calle, consig_no_exterior, consig_no_interior, consig_colonia,
                   consig_municipio, consig_estado, consig_pais, consig_codigo_postal,
                   no_proveedor, dias_credito
            FROM clientes
            WHERE CAST(numero AS CHAR) = %s AND UPPER(TRIM(empresa)) = UPPER(TRIM(%s))
            LIMIT 1
            """,
            (numero_norm, _normalizar_empresa(empresa)),
        )
        row = cur.fetchone()
        if row:
            return dict(row)
        cur.execute(
            """
            SELECT numero, nombre, empresa, razon_social, rfc, codigo_postal, consignatario,
                   calle, no_exterior, no_interior, colonia, municipio, estado, pais,
                   consig_calle, consig_no_exterior, consig_no_interior, consig_colonia,
                   consig_municipio, consig_estado, consig_pais, consig_codigo_postal,
                   no_proveedor, dias_credito
            FROM clientes
            WHERE CAST(numero AS CHAR) = %s
            """,
            (numero_norm,),
        )
        candidatos = cur.fetchall() or []
        for cand in candidatos:
            if _texto_cmp(cand["empresa"]) == empresa_norm:
                return dict(cand)
        if candidatos:
            return dict(candidatos[0])
        return {}
    finally:
        cur.close()


def _detectar_modo_facturacion(factura):
    productos = factura.get("productos") or []
    modos = []
    for producto in productos:
        modos.append(_modo_producto_cfdi(producto, ""))
    if modos and all(modo == "KILOS" for modo in modos):
        return "KILOS"
    if modos and all(modo == "PIEZAS" for modo in modos):
        return "PIEZAS"
    piezas_total = sum(_float_producto(producto, "piezas") for producto in productos)
    kilos_total = sum(_float_producto(producto, "cantidad") for producto in productos)
    return "KILOS" if kilos_total > piezas_total else "PIEZAS"


def _es_venta_mostrador(factura, receptor=None):
    """Identifica el cliente genérico que usa el legado para venta de mostrador."""
    receptor = receptor or {}
    empresa = _normalizar_empresa(factura.get("empresa"))
    numeros = (
        factura.get("numero_cliente"),
        factura.get("numero_cliente_cfdi"),
        receptor.get("numero"),
    )
    numeros_normalizados = {str(numero or "").strip().replace(",", "") for numero in numeros}
    if "100000" in numeros_normalizados:
        return True
    # 160006 es otra clave de venta de mostrador, pero únicamente en EZA2007.
    return empresa == "EZA2007" and "160006" in numeros_normalizados


def _receptor_publico_general(config):
    return {
        "numero": "100000",
        "nombre": "PUBLICO EN GENERAL",
        "razon_social": "PUBLICO EN GENERAL",
        "rfc": "XAXX010101000",
        "codigo_postal": str(config.get("cp_fiscal") or config.get("lugar_expedicion") or "").strip(),
        "regimen_fiscal": "616",
        "uso_cfdi": "S01",
    }


def resolver_receptor_timbrado(conn, conn_legacy, factura, incluir_preview=True):
    _asegurar_tablas_timbrado(conn)
    empresa = _normalizar_empresa(factura.get("empresa"))
    numero_origen = str(factura.get("numero_cliente") or "").strip()
    nombre_origen = str(factura.get("consignatario") or "").strip() or str(factura.get("cliente_nombre") or "").strip()
    modo = _detectar_modo_facturacion(factura)
    reglas = listar_reglas_redireccion(conn, empresa=empresa)
    nombre_cmp = nombre_origen.upper()
    aplicada = {}
    destino_numero = numero_origen
    receptor_fiscal = {}
    grupos_con_origen = _grupos_con_cliente(conn, [r.get("grupo_id") for r in reglas], numero_origen)
    for regla in reglas:
        if not bool(regla.get("activa")):
            continue
        aplica = False
        gid = regla.get("grupo_id")
        if gid:
            aplica = int(gid) in grupos_con_origen
        else:
            patron = str(regla.get("patron_cliente") or "").strip().upper()
            if patron and (patron in nombre_cmp or patron == numero_origen.upper()):
                aplica = True
        if aplica:
            aplicada = regla
            if modo == "PIEZAS":
                destino_numero = str(regla.get("cliente_destino_piezas") or "").strip() or numero_origen
                receptor_clave = str(regla.get("receptor_fiscal_piezas_clave") or "").strip()
            else:
                destino_numero = str(regla.get("cliente_destino_kilos") or "").strip() or numero_origen
                receptor_clave = str(regla.get("receptor_fiscal_kilos_clave") or "").strip()
            if receptor_clave:
                receptor_fiscal = obtener_receptor_fiscal(conn, empresa, receptor_clave)
            break
    cliente_receptor = _obtener_cliente_por_numero(conn_legacy, empresa, destino_numero) if destino_numero else {}
    # Algunas tiendas comparten el mismo RFC fiscal, pero no siempre quedaron dadas de
    # alta en un grupo de redirección. Si el RFC de la tienda identifica de forma
    # unívoca a un receptor fiscal configurado, usarlo conserva la addenda sin
    # depender de que el grupo esté completo. No se aplica cuando hay más de un
    # receptor para el RFC (por ejemplo, los modos piezas/kilos de Walmart).
    if not receptor_fiscal:
        rfc_cliente = str(cliente_receptor.get("rfc") or "").strip().upper()
        if rfc_cliente:
            receptores_rfc = [
                receptor
                for receptor in listar_receptores_fiscales(conn, empresa=empresa)
                if str(receptor.get("rfc") or "").strip().upper() == rfc_cliente
            ]
            if len(receptores_rfc) == 1:
                receptor_fiscal = receptores_rfc[0]
    consignatario_cfg = obtener_consignatario_cliente(conn, empresa, destino_numero or numero_origen) if incluir_preview else {}
    gln_consignatario = str(consignatario_cfg.get("gln_consignatario") or "").strip()
    if receptor_fiscal:
        cliente_fiscal_base = _obtener_cliente_por_numero(
            conn_legacy, empresa,
            str(receptor_fiscal.get("clave_receptor") or "").strip(),
        ) if str(receptor_fiscal.get("clave_receptor") or "").strip() else {}
        cfg_receptor = {}
        cfg_factura = {}
        if incluir_preview:
            cfg_receptor = dict(receptor_fiscal.get("addenda_config") or {})
            for clave in PLACEHOLDERS_ADDENDA_POR_FACTURA:
                cfg_receptor.pop(clave, None)
                cfg_receptor.pop(str(clave).lower(), None)
            factura_id = factura.get("id")
            if factura_id:
                factura_cfg_row = obtener_campos_addenda_factura(conn, int(factura_id)) or {}
                cfg_factura = dict(factura_cfg_row.get("campos") or {})
            cfg_receptor.update(cfg_factura)
        addenda = {}
        if incluir_preview and receptor_fiscal.get("addenda_activa") and receptor_fiscal.get("addenda_tipo"):
            addenda_info = _localizar_addenda_disponible(receptor_fiscal.get("addenda_tipo"))
            addenda = {
                "addenda_tipo": addenda_info.get("clave") or _normalizar_clave_addenda(receptor_fiscal.get("addenda_tipo")),
                "addenda_archivo": addenda_info.get("archivo"),
                "addenda_ruta": addenda_info.get("ruta"),
                "addenda_placeholders": _placeholders_addenda_xml(addenda_info.get("ruta")),
                "addenda_config": cfg_receptor,
                "addenda_config_factura": cfg_factura,
            }
        cliente_receptor_resuelto = {
            "numero": str(receptor_fiscal.get("clave_receptor") or "").strip() or str(destino_numero or "").strip(),
            "nombre": str(receptor_fiscal.get("alias_receptor") or "").strip() or str(receptor_fiscal.get("razon_social") or "").strip(),
            "empresa": empresa,
            "razon_social": str(receptor_fiscal.get("razon_social") or cliente_fiscal_base.get("razon_social") or "").strip(),
            "rfc": str(receptor_fiscal.get("rfc") or cliente_fiscal_base.get("rfc") or "").strip(),
            "codigo_postal": str(receptor_fiscal.get("cp_fiscal") or cliente_fiscal_base.get("codigo_postal") or "").strip(),
            "regimen_fiscal": str(receptor_fiscal.get("regimen_fiscal") or "").strip(),
            "uso_cfdi": str(receptor_fiscal.get("uso_cfdi") or "").strip(),
            "calle": str(receptor_fiscal.get("calle") or cliente_fiscal_base.get("calle") or "").strip(),
            "no_exterior": str(receptor_fiscal.get("no_exterior") or cliente_fiscal_base.get("no_exterior") or "").strip(),
            "no_interior": str(receptor_fiscal.get("no_interior") or cliente_fiscal_base.get("no_interior") or "").strip(),
            "colonia": str(receptor_fiscal.get("colonia") or cliente_fiscal_base.get("colonia") or "").strip(),
            "municipio": str(receptor_fiscal.get("municipio") or cliente_fiscal_base.get("municipio") or "").strip(),
            "estado": str(receptor_fiscal.get("estado") or cliente_fiscal_base.get("estado") or "").strip(),
            "pais": str(receptor_fiscal.get("pais") or cliente_fiscal_base.get("pais") or "").strip(),
            "consignatario": str(cliente_receptor.get("consignatario") or nombre_origen or "").strip(),
            "consig_calle": str(cliente_receptor.get("consig_calle") or "").strip(),
            "consig_no_exterior": str(cliente_receptor.get("consig_no_exterior") or "").strip(),
            "consig_no_interior": str(cliente_receptor.get("consig_no_interior") or "").strip(),
            "consig_colonia": str(cliente_receptor.get("consig_colonia") or "").strip(),
            "consig_municipio": str(cliente_receptor.get("consig_municipio") or "").strip(),
            "consig_estado": str(cliente_receptor.get("consig_estado") or "").strip(),
            "consig_pais": str(cliente_receptor.get("consig_pais") or "").strip(),
            "consig_codigo_postal": str(cliente_receptor.get("consig_codigo_postal") or "").strip(),
            "no_proveedor": str(cliente_receptor.get("no_proveedor") or "").strip(),
            "dias_credito": receptor_fiscal.get("dias_credito") if receptor_fiscal.get("dias_credito") is not None else cliente_receptor.get("dias_credito"),
            "gln_receptor": str(receptor_fiscal.get("gln_receptor") or "").strip(),
            "gln_consignatario": gln_consignatario,
            "gln_emisor_buyer": str(receptor_fiscal.get("gln_emisor_buyer") or "").strip(),
        }
    else:
        addenda = obtener_addenda_cliente(conn, empresa, destino_numero) if incluir_preview and destino_numero else {}
        if addenda and incluir_preview:
            addenda_info = _localizar_addenda_disponible(addenda.get("addenda_tipo"))
            addenda["addenda_tipo"] = addenda_info.get("clave") or _normalizar_clave_addenda(addenda.get("addenda_tipo"))
            addenda["addenda_archivo"] = addenda_info.get("archivo")
            addenda["addenda_ruta"] = addenda_info.get("ruta")
            addenda["addenda_placeholders"] = _placeholders_addenda_xml(addenda_info.get("ruta"))
        elif addenda:
            addenda = {}
        cliente_receptor_resuelto = dict(cliente_receptor or {})
        cliente_receptor_resuelto["gln_consignatario"] = gln_consignatario
    # El cliente 100000 es la venta de mostrador del sistema legado. No debe
    # depender de que tenga datos fiscales capturados en la tabla de clientes.
    if _es_venta_mostrador(factura, cliente_receptor_resuelto):
        config_empresa = obtener_config_timbrado(conn, empresa)
        cliente_receptor_resuelto.update(_receptor_publico_general(config_empresa))
    resultado = {
        "empresa": empresa,
        "modo_facturacion": modo,
        "cliente_origen_numero": numero_origen,
        "cliente_origen_nombre": nombre_origen,
        "cliente_receptor_numero": (
            str(receptor_fiscal.get("clave_receptor") or "").strip()
            or str(destino_numero or "").strip()
        ),
        "cliente_receptor_nombre": (
            str(receptor_fiscal.get("razon_social") or "").strip()
            or str(receptor_fiscal.get("alias_receptor") or "").strip()
            or str(cliente_receptor.get("razon_social") or "").strip()
            or str(cliente_receptor.get("nombre") or "").strip()
            or nombre_origen
        ),
        "cliente_receptor": cliente_receptor_resuelto,
        "receptor_fiscal": receptor_fiscal,
        "regla": aplicada,
        "addenda": addenda or {},
    }
    if addenda and incluir_preview:
        try:
            cfg_empresa = obtener_config_timbrado(conn, empresa)
            resultado["addenda_preview"] = _construir_payload_addenda(addenda, factura, resultado, cfg_empresa)
        except Exception as exc:
            resultado["addenda_preview_error"] = str(exc)
    return resultado


def obtener_campos_addenda_factura(conn, factura_id):
    _asegurar_tablas_timbrado(conn)
    row = conn.execute(
        "SELECT * FROM timbrado_factura_addenda_campos WHERE factura_id = ? LIMIT 1",
        (int(factura_id),),
    ).fetchone()
    if row:
        row = dict(row)
        try:
            row["campos"] = json.loads(row.get("campos_json") or "{}")
        except Exception:
            row["campos"] = {}
        return row
    return {}


def guardar_campos_addenda_factura(conn, factura_id, factura, empresa, addenda_tipo, campos):
    _asegurar_tablas_timbrado(conn)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO timbrado_factura_addenda_campos (factura_id, factura, empresa, addenda_tipo, campos_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(factura_id) DO UPDATE SET
            factura = excluded.factura,
            empresa = excluded.empresa,
            addenda_tipo = excluded.addenda_tipo,
            campos_json = excluded.campos_json,
            updated_at = excluded.updated_at
        """,
        (
            int(factura_id),
            str(factura or "").strip(),
            _normalizar_empresa(empresa),
            _normalizar_clave_addenda(addenda_tipo) or None,
            json.dumps(campos or {}, ensure_ascii=False),
            now,
        ),
    )


def _localizar_addenda_disponible(clave_addenda):
    for a in listar_addendas_disponibles(include_template=True):
        if a["clave"] == _normalizar_clave_addenda(clave_addenda):
            return a
    return {}


def _placeholders_addenda_xml(ruta_xml):
    contenido = _leer_template_addenda(ruta_xml)
    return _extraer_placeholders_addenda(contenido)


def _leer_template_addenda(ruta_xml):
    if not ruta_xml:
        return ""
    ruta_txt = str(ruta_xml)
    if ruta_txt.startswith("db://"):
        clave = _normalizar_clave_addenda(ruta_txt.replace("db://", "", 1))
        for modelo in _listar_addendas_servidor(include_template=True):
            if modelo.get("clave") == clave:
                return str(modelo.get("template_xml") or "")
        return ""
    try:
        with open(ruta_txt, "r", encoding="utf-8") as f:
            contenido = f.read()
    except Exception:
        return ""
    if ruta_txt.lower().endswith(".json"):
        try:
            data = json.loads(contenido)
            return str(data.get("template_xml") or "")
        except Exception:
            return ""
    return contenido


def _snapshot_factura(conn_legacy, factura_id):
    cur = conn_legacy.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM facturas WHERE id = %s LIMIT 1", (factura_id,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"Factura id={factura_id} no encontrada")
        factura = dict(row)
        cur.execute(
            """
            SELECT d.cip, d.descripcion, d.cantidad, d.piezas, d.precio, d.importe,
                   p.unidad, p.iva, p.codigo_barras, p.no_identificacion, p.clave_prod_serv, p.clave_unidad_sat
            FROM factura_detalle d
            LEFT JOIN productos p ON CAST(p.cip AS CHAR) = CAST(d.cip AS CHAR)
            WHERE d.factura_id = %s
            ORDER BY d.id
            """,
            (factura_id,),
        )
        productos = cur.fetchall() or []
        factura["productos"] = [dict(r) for r in productos]
        for prod in factura["productos"]:
            if not str(prod.get("codigo_barras") or "").strip():
                try:
                    prod["codigo_barras"] = _resolver_codigo_barras_timbrado(
                        cur,
                        prod.get("cip"),
                        factura.get("empresa"),
                        factura.get("lista_precios"),
                        factura.get("cliente_nombre"),
                    )
                except Exception:
                    prod["codigo_barras"] = str(prod.get("codigo_barras") or "").strip()
        return factura
    finally:
        cur.close()


def _resolver_codigo_barras_timbrado(cur, cip, empresa=None, lista_precios=None, cliente_nombre=None):
    if not cip:
        return ""
    cip_txt = str(cip).strip()
    pistas = [
        _texto_cmp(lista_precios),
        _texto_cmp(cliente_nombre),
        _texto_cmp(empresa),
    ]
    pistas = [p for p in pistas if p]

    def _score_lista(nombre):
        nombre_cmp = _texto_cmp(nombre)
        if not nombre_cmp:
            return 0
        score = 0
        for pista in pistas:
            if nombre_cmp == pista:
                score = max(score, 100)
            elif nombre_cmp in pista or pista in nombre_cmp:
                score = max(score, 80)
            else:
                tokens = [t for t in re.split(r"\s+", nombre_cmp) if len(t) >= 4]
                if any(t in pista for t in tokens):
                    score = max(score, 60)
        return score

    cur.execute(
        """
        SELECT lp.nombre, pp.codigo_barras
        FROM precios_productos pp
        INNER JOIN listas_precios lp ON lp.id = pp.lista_id
        WHERE CAST(pp.cip AS CHAR) = %s
          AND pp.codigo_barras IS NOT NULL
          AND pp.codigo_barras != ''
        ORDER BY lp.nombre
        """,
        (cip_txt,),
    )
    rows_lista = cur.fetchall() or []
    if rows_lista:
        scored = sorted(
            (
                (_score_lista(row.get("nombre")), row)
                for row in rows_lista
                if str(row.get("codigo_barras") or "").strip()
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if scored and scored[0][0] > 0:
            return _normalizar_codigo_barras(scored[0][1].get("codigo_barras"))

    cur.execute(
        "SELECT codigo_barras FROM productos WHERE CAST(cip AS CHAR) = %s AND codigo_barras IS NOT NULL AND codigo_barras != '' LIMIT 1",
        (cip_txt,),
    )
    row = cur.fetchone()
    if row and str(row["codigo_barras"] or "").strip():
        return _normalizar_codigo_barras(row["codigo_barras"])
    if rows_lista:
        return _normalizar_codigo_barras(rows_lista[0].get("codigo_barras"))
    return ""


def _fecha_referencia_cfdi(factura):
    try:
        return datetime.fromisoformat(str(factura.get("fecha_cfdi") or factura.get("fecha") or ""))
    except Exception:
        return datetime.now()


def _piezas_por_exhibidor(producto):
    """Obtiene las piezas internas declaradas en un exhibidor, por ejemplo `(24 pzas)`."""
    unidad = str(producto.get("unidad") or "").strip().upper()
    if unidad not in {"EXH", "EXHIB", "EXHIBIDOR"}:
        return 1
    descripcion = str(producto.get("descripcion") or "").upper()
    match = re.search(r"(?:\(|\b)(\d+)\s*(?:PZA|PZAS|PIEZA|PIEZAS)\b", descripcion)
    try:
        return max(1, int(match.group(1))) if match else 1
    except Exception:
        return 1


def _cantidad_base_producto(producto, modo_producto):
    if modo_producto == "KILOS":
        return _float_producto(producto, "cantidad")
    piezas = _float_producto(producto, "piezas")
    return piezas if piezas else _float_producto(producto, "cantidad")


def _importe_linea_producto(producto, cantidad_modo, modo_producto=None):
    """Conserva el importe de venta del exhibidor aunque se desglose por pieza."""
    try:
        modo = modo_producto or _modo_producto_cfdi(producto)
        cantidad_base = _cantidad_base_producto(producto, modo)
        return round(float(producto.get("precio") or 0) * cantidad_base, 2)
    except Exception:
        return 0.0


def _float_producto(producto, campo):
    try:
        return float(producto.get(campo) or 0)
    except Exception:
        return 0.0


def _texto_unidad_producto(unidad):
    u = str(unidad or "").strip().upper()
    if u in ("PZ", "PZA", "PIEZA", "PIEZAS", "UN", "UNIDAD", "UND", "UNDS"):
        return "pz"
    if u in ("KG", "KGS", "KILO", "KILOS", "KILOGRAMO", "KILOGRAMOS"):
        return "kg"
    return u.lower() or "pz"


def _no_identificacion_producto(producto):
    valor_bd = str(producto.get("no_identificacion") or "").strip()
    if valor_bd:
        return valor_bd
    cip = producto.get("cip")
    texto = str(cip or "").strip()
    return _solo_digitos(texto) or texto


def _codigo_alterno_producto(producto):
    return _normalizar_codigo_barras(producto.get("codigo_barras")) or str(producto.get("cip") or "").strip()


def _modo_producto_cfdi(producto, modo_global=""):
    unidad = str(producto.get("unidad") or "").strip().upper()
    clave_unidad = str(producto.get("clave_unidad_sat") or "").strip().upper()
    if clave_unidad == "KGM" or unidad in ("KG", "KGS", "KILO", "KILOS", "KILOGRAMO", "KILOGRAMOS"):
        return "KILOS"
    if unidad in ("PZ", "PZA", "PIEZA", "PIEZAS", "EXH", "EXHIB", "EXHIBIDOR", "UN", "UNIDAD", "UND", "UNDS", "H87"):
        return "PIEZAS"
    return str(modo_global or "PIEZAS").strip().upper() or "PIEZAS"


def _cantidad_producto_cfdi(producto, modo_global=""):
    modo_producto = _modo_producto_cfdi(producto, modo_global)
    cantidad_base = _cantidad_base_producto(producto, modo_producto)
    if modo_producto == "PIEZAS":
        cantidad_base *= _piezas_por_exhibidor(producto)
    return cantidad_base, modo_producto


def _unidad_cfdi_texto(unidad, modo):
    if str(modo or "").upper() == "PIEZAS":
        return "pz"
    return _texto_unidad_producto(unidad)


def _clave_unidad_sat(unidad, modo, producto):
    prod_clave = str(producto.get("clave_unidad_sat") or "").strip().upper()
    if prod_clave:
        return prod_clave
    if str(unidad or "").upper() in ("KG", "KGS", "KILO", "KILOS", "KILOGRAMO", "KILOGRAMOS"):
        return "KGM"
    if str(modo or "").upper() == "KILOS":
        return "KGM"
    return "H87"


def _clave_prod_serv_sat(producto):
    clave = str(producto.get("clave_prod_serv") or "").strip()
    return clave or "01010101"


def _producto_tiene_iva(producto):
    texto = str(producto.get("iva") or "").strip().lower()
    return texto in {"si", "sí", "s", "1", "true", "t", "yes", "y", "16", "16%", "gravado", "coniva"}


def _distribuir_iva_cfdi(factura, bases_gravadas):
    iva_total = _money_cfdi(factura.get("iva"))
    bases = [_money_cfdi(base) for base in bases_gravadas]
    if iva_total <= 0 or not bases:
        return [Decimal("0.00") for _ in bases]
    suma_base = sum(bases, Decimal("0.00"))
    if suma_base <= 0:
        return [Decimal("0.00") for _ in bases]
    acumulado = Decimal("0.00")
    impuestos = []
    for idx, base in enumerate(bases):
        if idx == len(bases) - 1:
            impuesto = iva_total - acumulado
        else:
            impuesto = (iva_total * base / suma_base).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            acumulado += impuesto
        impuestos.append(max(Decimal("0.00"), impuesto))
    return impuestos


def _distribuir_descuento_cfdi(descuento_total, importes):
    descuento_total = _money_cfdi(descuento_total)
    bases = [_money_cfdi(base) for base in importes]
    if descuento_total <= 0 or not bases:
        return [Decimal("0.00") for _ in bases]
    suma_base = sum(bases, Decimal("0.00"))
    if suma_base <= 0:
        return [Decimal("0.00") for _ in bases]
    acumulado = Decimal("0.00")
    descuentos = []
    for idx, base in enumerate(bases):
        if idx == len(bases) - 1:
            descuento = descuento_total - acumulado
        else:
            descuento = (descuento_total * base / suma_base).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            acumulado += descuento
        descuentos.append(min(base, max(Decimal("0.00"), descuento)))
    return descuentos


def _descuento_cfdi_compatible_con_total(subtotal, descuento_origen, impuestos, total):
    """Conserva el total de la remisión y corrige únicamente un redondeo de descuento.

    SAE puede guardar el descuento redondeado a dos decimales y, al mismo
    tiempo, conservar el total calculado desde los importes por partida. Si
    ambos difieren por un centavo, el PAC rechaza el CFDI porque la identidad
    Total = SubTotal - Descuento + Impuestos debe ser exacta. Ajustamos solo
    esa diferencia de redondeo (máximo dos centavos), nunca el total ni las
    cantidades/precios de la remisión.
    """
    subtotal_dec = _money_cfdi(subtotal)
    descuento_dec = _money_cfdi(descuento_origen)
    impuestos_dec = _money_cfdi(impuestos)
    total_dec = _money_cfdi(total)
    if descuento_dec <= 0:
        return descuento_dec
    descuento_requerido = _money_cfdi(subtotal_dec + impuestos_dec - total_dec)
    if descuento_requerido < 0:
        return descuento_dec
    if abs(descuento_requerido - descuento_dec) <= Decimal("0.02"):
        return descuento_requerido
    return descuento_dec


def validar_pre_cfdi_factura(factura, config, resolucion=None, opciones_cfdi=None):
    resolucion = resolucion or {}
    opciones_cfdi = opciones_cfdi or {}
    receptor = dict(resolucion.get("cliente_receptor") or resolucion.get("receptor") or {})
    if _es_venta_mostrador(factura, receptor):
        receptor.update(_receptor_publico_general(config))
        # Para RFC genérico el SAT requiere el uso S01, independientemente de
        # la selección previamente guardada en la pantalla fiscal.
        opciones_cfdi = dict(opciones_cfdi)
        opciones_cfdi["uso_cfdi"] = "S01"
    faltantes = []
    advertencias = []

    def requiere(condicion, campo, mensaje):
        if not condicion:
            faltantes.append({"campo": campo, "mensaje": mensaje})

    requiere(str(config.get("rfc_emisor") or "").strip(), "empresa.rfc_emisor", "Falta RFC del emisor.")
    requiere(str(config.get("razon_social") or "").strip(), "empresa.razon_social", "Falta razón social del emisor.")
    requiere(str(config.get("regimen_fiscal") or "").strip(), "empresa.regimen_fiscal", "Falta régimen fiscal del emisor.")
    requiere(str(config.get("cp_fiscal") or config.get("lugar_expedicion") or "").strip(), "empresa.cp_fiscal", "Falta lugar de expedición/CP fiscal del emisor.")
    requiere(str(config.get("serie_cfdi") or "").strip(), "empresa.serie_cfdi", "Falta serie CFDI.")
    requiere(str(config.get("folio_actual") or "").strip(), "empresa.folio_actual", "Falta folio actual para CFDI.")
    requiere(str(receptor.get("rfc") or "").strip(), "receptor.rfc", "Falta RFC del receptor.")
    requiere(str(receptor.get("razon_social") or receptor.get("nombre") or "").strip(), "receptor.razon_social", "Falta razón social del receptor.")
    requiere(str(receptor.get("codigo_postal") or "").strip(), "receptor.codigo_postal", "Falta CP fiscal del receptor.")
    requiere(str(receptor.get("regimen_fiscal") or "").strip(), "receptor.regimen_fiscal", "Falta régimen fiscal del receptor.")
    requiere(str(opciones_cfdi.get("uso_cfdi") or receptor.get("uso_cfdi") or "").strip(), "cfdi.uso_cfdi", "Falta uso CFDI.")
    requiere(str(opciones_cfdi.get("forma_pago") or "").strip(), "cfdi.forma_pago", "Falta forma de pago.")
    requiere(str(opciones_cfdi.get("metodo_pago") or "").strip(), "cfdi.metodo_pago", "Falta método de pago.")
    requiere(str(opciones_cfdi.get("exportacion") or "01").strip(), "cfdi.exportacion", "Falta exportación SAT.")

    productos = factura.get("productos") or []
    requiere(productos, "conceptos", "La factura no tiene productos.")
    productos_con_iva = 0
    suma_importes = Decimal("0.00")
    for idx, prod in enumerate(productos, start=1):
        pref = f"conceptos[{idx}]"
        cantidad_fiscal = _cantidad_producto_cfdi(prod, "")[0]
        importe_linea = _money_cfdi(_importe_linea_producto(prod, cantidad_fiscal))
        suma_importes += importe_linea
        if _producto_tiene_iva(prod):
            productos_con_iva += 1
        requiere(str(prod.get("descripcion") or "").strip(), f"{pref}.descripcion", f"Producto {idx}: falta descripción.")
        requiere(cantidad_fiscal > 0, f"{pref}.cantidad", f"Producto {idx}: cantidad fiscal inválida.")
        requiere(_money_cfdi(prod.get("precio")) > 0, f"{pref}.precio", f"Producto {idx}: precio fiscal inválido.")
        if _clave_prod_serv_sat(prod) == "01010101":
            advertencias.append({"campo": f"{pref}.clave_prod_serv", "mensaje": f"Producto {idx}: usa clave SAT genérica 01010101."})
        if not str(prod.get("clave_unidad_sat") or "").strip():
            advertencias.append({"campo": f"{pref}.clave_unidad_sat", "mensaje": f"Producto {idx}: se usará clave unidad inferida."})
    if _money_cfdi(factura.get("iva")) > 0 and productos_con_iva <= 0:
        requiere(False, "conceptos.iva", "La factura tiene IVA, pero ningún producto está marcado con IVA.")
    if _money_cfdi(factura.get("iva")) <= 0 and productos_con_iva > 0:
        advertencias.append({"campo": "conceptos.iva", "mensaje": "Hay productos marcados con IVA, pero la factura tiene IVA en cero."})
    subtotal_factura = _money_cfdi(factura.get("subtotal") or factura.get("total"))
    if productos and abs(suma_importes - subtotal_factura) > Decimal("0.05"):
        advertencias.append({
            "campo": "cfdi.subtotal",
            "mensaje": f"La suma fiscal de conceptos ({_fmt_money_cfdi(suma_importes)}) difiere del subtotal/total base ({_fmt_money_cfdi(subtotal_factura)}).",
        })
    descuento_factura = _money_cfdi(factura.get("descuento"))
    iva_factura = _money_cfdi(factura.get("iva"))
    total_factura = _money_cfdi(factura.get("total"))
    total_esperado = subtotal_factura - descuento_factura + iva_factura
    requiere(
        abs(total_factura - total_esperado) <= Decimal("0.01"),
        "cfdi.total",
        "La remision no cuadra para CFDI: "
        f"SubTotal {_fmt_money_cfdi(subtotal_factura)} - Descuento {_fmt_money_cfdi(descuento_factura)} "
        f"+ Impuestos {_fmt_money_cfdi(iva_factura)} = {_fmt_money_cfdi(total_esperado)}, "
        f"pero el Total de la remision es {_fmt_money_cfdi(total_factura)}. "
        "Corrige la remision de origen antes de timbrar; no se modificaran cantidades ni centavos automaticamente.",
    )

    return {"ok": not faltantes, "faltantes": faltantes, "advertencias": advertencias}


def _opciones_cfdi_desde_item(item):
    try:
        opciones = json.loads(item.get("cfdi_opciones_json") or "{}")
        return opciones if isinstance(opciones, dict) else {}
    except Exception:
        return {}


def _compactar_descripcion_addenda(desc):
    t = _texto_addenda_base(desc)
    t = re.sub(r"\(\s*(\d+)\s*gr\s*\)", r"\1gr", t, flags=re.IGNORECASE)
    t = re.sub(r"\(\s*(\d+)\s*g\s*\)", r"\1gr", t, flags=re.IGNORECASE)
    t = re.sub(r"\bDE QUESO\b", "Q", t)
    t = re.sub(r"\bQ VASCO LOS OVEJEROS\b", "Q VASCO OVEJEROS", t)
    t = re.sub(r"\bGRAN FLOR 180GR\b", "GRAN FLOR (180gr)", t, flags=re.IGNORECASE)
    t = re.sub(r"\bCUÑA MR OVEJA 200GR\b", "CUÑA QUESO MR OVEJA", t, flags=re.IGNORECASE)
    return t[:150]


UNIDADES = (
    "", "UN", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE",
    "DIEZ", "ONCE", "DOCE", "TRECE", "CATORCE", "QUINCE", "DIECISEIS",
    "DIECISIETE", "DIECIOCHO", "DIECINUEVE",
)
DECENAS = ("", "", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA", "NOVENTA")
CENTENAS = ("", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS", "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS")


def _numero_entero_letras(n):
    n = int(n or 0)
    if n == 0:
        return "CERO"
    if n < 20:
        return UNIDADES[n]
    if n < 30:
        return "VEINTE" if n == 20 else "VEINTI" + UNIDADES[n - 20].lower().upper()
    if n < 100:
        d, u = divmod(n, 10)
        return DECENAS[d] if u == 0 else f"{DECENAS[d]} Y {UNIDADES[u]}"
    if n == 100:
        return "CIEN"
    if n < 1000:
        c, r = divmod(n, 100)
        return CENTENAS[c] if r == 0 else f"{CENTENAS[c]} {_numero_entero_letras(r)}"
    if n < 1_000_000:
        m, r = divmod(n, 1000)
        pref = "MIL" if m == 1 else f"{_numero_entero_letras(m)} MIL"
        return pref if r == 0 else f"{pref} {_numero_entero_letras(r)}"
    mill, r = divmod(n, 1_000_000)
    pref = "UN MILLON" if mill == 1 else f"{_numero_entero_letras(mill)} MILLONES"
    return pref if r == 0 else f"{pref} {_numero_entero_letras(r)}"


def _texto_importe_mxn(importe):
    try:
        total = round(float(importe or 0), 2)
    except Exception:
        total = 0.0
    entero = int(total)
    centavos = int(round((total - entero) * 100))
    if centavos >= 100:
        entero += 1
        centavos = 0
    letras = _numero_entero_letras(entero)
    if letras.endswith("UNO"):
        letras = letras[:-3] + "UN"
    return f"{letras} Pesos {centavos:02d}/100 M.N."


def _folio_addenda_default(factura, config_empresa):
    for valor in (factura.get("folio_cfdi"), config_empresa.get("folio_actual"), factura.get("factura")):
        digitos = _parte_numerica_folio(valor)
        if digitos:
            return digitos
    return ""


def _texto_addenda_upper(texto):
    t = str(texto or "").strip().replace("?", "Ñ")
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\bSTA\.", "SANTA", t, flags=re.IGNORECASE)
    return t.upper()


def _fecha_addenda_yyyymmdd(valor):
    v = str(valor or "").strip()
    if not v:
        return ""
    if re.match(r"^\d{8}$", v):
        return v
    return v


def _valor_cfg(cfg, clave, defecto=""):
    for k in (clave, clave.upper(), clave.lower()):
        if k in cfg:
            v = cfg[k]
            if v is not None:
                return v
    return defecto


def _texto_addenda_base(texto):
    return _texto_addenda_upper(texto)


def _municipio_addenda(texto):
    t = str(texto or "").strip()
    t = re.sub(r"\s+MUNICIPIO$|\s+CIUDAD$|\s+CD\.$", "", t, flags=re.IGNORECASE)
    return _texto_addenda_base(t)


def _municipio_consignatario_addenda(texto):
    t = _municipio_addenda(texto)
    mapa = {
        "BENITO JUAREZ": "BENI JUAR",
        "MIGUEL HIDALGO": "MIGU HIDA",
    }
    return mapa.get(t, t)


def _estado_addenda(texto):
    t = str(texto or "").strip()
    t = re.sub(r"\s+ESTADO$", "", t, flags=re.IGNORECASE)
    t = _texto_addenda_base(t)
    mapa = {
        "CIUDAD DE MEXICO": "CDMX",
        "MEXICO D.F.": "CDMX",
        "DISTRITO FEDERAL": "CDMX",
    }
    return mapa.get(t, t)


def _estado_consignatario_addenda(texto):
    t = _estado_addenda(texto)
    mapa = {"CDMX": "DF"}
    return mapa.get(t, t)


def _nombre_consignatario_addenda(texto):
    t = _texto_addenda_base(texto)
    t = re.sub(r"\bSUPERAMA\b", "SUP", t)
    t = re.sub(r"\bDET\.?\s*", "", t)
    t = re.sub(r"[()]+", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _direccion_consignatario_addenda(calle, no_exterior):
    c = _texto_addenda_base(calle)
    ne = _texto_addenda_base(no_exterior)
    if c and ne:
        return f"{c} # {ne}"
    return c or ne


def _direccion_linea(calle, no_exterior):
    return f"{_texto_addenda_base(calle)} {_texto_addenda_base(no_exterior)}".strip()


def _poblacion_consignatario_addenda(municipio, estado):
    return _municipio_consignatario_addenda(municipio) or _estado_consignatario_addenda(estado) or ""


def _fmt_folio_addenda(factura):
    folio_val = str(factura.get("folio_cfdi") or "").strip()
    if not folio_val:
        return f"CFDI{str(factura.get('factura') or '').strip()}"
    solo_num = re.sub(r"[^0-9]", "", folio_val)
    if solo_num:
        return f"CFDI{solo_num.zfill(10)}"
    return f"CFDI{folio_val}"


def _parte_numerica_folio(folio):
    return re.sub(r"[^0-9]", "", str(folio or ""))


def _cfg_addenda_normalizado(cfg):
    return {str(k).upper().strip(): v for k, v in (cfg or {}).items()}


def _aplicar_limites_campos_addenda(campos, limites):
    return {k: _truncar(v, limites.get(k, 255)) if isinstance(v, str) else v for k, v in campos.items()}


def _limpiar_linea_xml(linea: str) -> str:
    return re.sub(r"\s+", " ", linea).strip()


def _contar_segmentos_addenda(contenido: str) -> int:
    if not contenido:
        return 0
    segmentos_xml = len(re.findall(r"<Segmento\b", contenido, re.IGNORECASE))
    if segmentos_xml:
        return segmentos_xml
    total = 0
    for linea in str(contenido or "").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("<") or not linea.endswith("'"):
            continue
        if linea.startswith("UNB+") or linea.startswith("UNZ+"):
            continue
        total += 1
    return total


def _fecha_yyyymmdd_con_guiones(valor):
    texto = str(valor or "").strip()
    if re.match(r"^\d{8}$", texto):
        return f"{texto[0:4]}-{texto[4:6]}-{texto[6:8]}"
    return texto


def _reemplazar_placeholders_addenda(texto, valores):
    resultado = str(texto or "")
    valores = valores or {}
    especiales = {
        "FECHADOCTO(YYMMDD)": valores.get("FECHA_YYMMDD") or "",
        "FECHADOCTO(YYYYMMDD)": valores.get("FECHA_YYYYMMDD") or "",
        "HORADOCTO(HHMM)": valores.get("HORA_HHMM") or "",
        "HORADOCTO(HHMMSS)": valores.get("HORA_HHMMSS") or "",
        "FECHADOCTO(yyyy-mm-dd)": _fecha_yyyymmdd_con_guiones(valores.get("FECHA_YYYYMMDD")),
        "FECHAPEDIDO(yyyy-mm-dd)": _fecha_yyyymmdd_con_guiones(valores.get("ENVIARADIRECCION") or valores.get("FECHA_YYYYMMDD")),
        "FECHARECEP(yyyy-mm-dd)": _fecha_yyyymmdd_con_guiones(valores.get("ENVIARADIRECCION") or valores.get("FECHA_YYYYMMDD")),
    }
    merged = {**valores, **especiales}
    for k, v in merged.items():
        valor = str(v if v is not None else "")
        resultado = resultado.replace("{" + str(k) + "}", valor)
        resultado = resultado.replace("[" + str(k) + "]", valor)
    resultado = re.sub(r"\{[\w\-().#]+\}", "", resultado)
    resultado = re.sub(r"\[[A-Za-z0-9_\-().#]+\]", "", resultado)
    return resultado


def _fmt_gtin(valor):
    # Walmart valida el código exactamente contra el catálogo recibido. No se
    # debe completar a 14 posiciones: un cero agregado altera el artículo.
    # Conservamos los ceros que ya existan y solo normalizamos valores que
    # Excel haya convertido a texto con sufijo decimal (por ejemplo, 750...0).
    return _normalizar_codigo_barras(valor)


def _render_addenda_text(ruta_xml, placeholders, lineas):
    template = _leer_template_addenda(ruta_xml)
    if not template:
        return ""
    m_old = re.search(r"<INILISTAPROD>(.*?)</INILISTAPROD>", template, re.DOTALL | re.IGNORECASE)
    m_new = re.search(
        r"<!--\s*LINEAS_ADDENDA_START\s*-->(.*?)<!--\s*LINEAS_ADDENDA_END\s*-->",
        template,
        re.DOTALL,
    )
    m = m_old or m_new
    if m:
        bloque_linea = m.group(1)
        partes_linea = []
        for item in lineas or []:
            valores_linea = {
                k: _truncar(str(v or ""), LIMITES_PLACEHOLDERS_ADDENDA.get(k, 255))
                for k, v in (item or {}).items()
            }
            partes_linea.append(_reemplazar_placeholders_addenda(bloque_linea, valores_linea))
        template = template[: m.start()] + "".join(partes_linea) + template[m.end() :]
    resultado = _reemplazar_placeholders_addenda(template, placeholders)
    resultado = re.sub(r"^\s*<Addenda>\s*", "", resultado)
    resultado = re.sub(r"\s*</Addenda>\s*$", "", resultado)
    return resultado


def _construir_payload_addenda(addenda, factura, resolucion=None, config_empresa=None):
    if not addenda:
        return {}
    cfg = addenda.get("addenda_config") or {}
    cfg_factura = addenda.get("addenda_config_factura") or {}
    resolucion = resolucion or {}
    config_empresa = config_empresa or {}
    cliente_receptor = resolucion.get("cliente_receptor") or {}
    receptor_fiscal = resolucion.get("receptor_fiscal") or {}
    modo = str(resolucion.get("modo_facturacion") or "").strip()
    fecha_cfdi_ref = _fecha_referencia_cfdi(factura)
    lineas = []
    for idx, producto in enumerate(factura.get("productos") or [], start=1):
        cantidad_modo, modo_producto = _cantidad_producto_cfdi(producto, modo)
        importe_linea = _importe_linea_producto(producto, cantidad_modo, modo_producto)
        unidad_txt = _unidad_cfdi_texto(producto.get("unidad"), modo_producto)
        lineas.append({
            "partida": idx,
            "cip": str(producto.get("cip") or "").strip(),
            "no_identificacion": _no_identificacion_producto(producto),
            "codigo_alterno": _codigo_alterno_producto(producto),
            "unidad": unidad_txt,
            "clave_unidad": _clave_unidad_sat(unidad_txt, modo_producto, producto),
            "clave_prod_serv": _clave_prod_serv_sat(producto),
            "descripcion": _compactar_descripcion_addenda(producto.get("descripcion")),
            "cantidad_modo": cantidad_modo,
            "piezas": int(cantidad_modo) if modo_producto == "PIEZAS" else int(producto.get("piezas") or 0),
            "kilos": float(producto.get("cantidad") or 0),
            "precio": (importe_linea / cantidad_modo) if cantidad_modo else float(producto.get("precio") or 0),
            "importe": importe_linea,
        })
    payload = {
        "tipo": _normalizar_clave_addenda(addenda.get("addenda_tipo")),
        "archivo": addenda.get("addenda_archivo"),
        "ruta_xml": addenda.get("addenda_ruta"),
        "placeholders": addenda.get("addenda_placeholders") or [],
        "cliente_numero": factura.get("numero_cliente_cfdi") or factura.get("numero_cliente"),
        "factura": factura.get("factura"),
        "empresa": factura.get("empresa"),
        "modo_facturacion": modo,
        "fecha_cfdi_referencia": fecha_cfdi_ref.isoformat(sep="T", timespec="seconds"),
        "emisor": {
            "rfc": str(config_empresa.get("rfc_emisor") or "").strip(),
            "razon_social": str(config_empresa.get("razon_social") or "").strip(),
            "regimen_fiscal": str(config_empresa.get("regimen_fiscal") or "").strip(),
            "cp_fiscal": str(config_empresa.get("cp_fiscal") or "").strip(),
            "lugar_expedicion": str(config_empresa.get("cp_fiscal") or config_empresa.get("lugar_expedicion") or "").strip(),
            "serie_cfdi": str(config_empresa.get("serie_cfdi") or "").strip(),
            "gln_supplier": str(config_empresa.get("gln_emisor_supplier") or "").strip(),
        },
        "receptor": {
            "clave": str(receptor_fiscal.get("clave_receptor") or cliente_receptor.get("numero") or "").strip(),
            "alias": str(receptor_fiscal.get("alias_receptor") or cliente_receptor.get("nombre") or "").strip(),
            "razon_social": str(cliente_receptor.get("razon_social") or "").strip(),
            "rfc": str(cliente_receptor.get("rfc") or "").strip(),
            "codigo_postal": str(cliente_receptor.get("codigo_postal") or "").strip(),
            "regimen_fiscal": str(cliente_receptor.get("regimen_fiscal") or "").strip(),
            "uso_cfdi": str(cliente_receptor.get("uso_cfdi") or "").strip(),
            "consignatario": str(cliente_receptor.get("consignatario") or "").strip(),
            "consig_calle": str(cliente_receptor.get("consig_calle") or "").strip(),
            "consig_no_exterior": str(cliente_receptor.get("consig_no_exterior") or "").strip(),
            "consig_no_interior": str(cliente_receptor.get("consig_no_interior") or "").strip(),
            "consig_colonia": str(cliente_receptor.get("consig_colonia") or "").strip(),
            "consig_municipio": str(cliente_receptor.get("consig_municipio") or "").strip(),
            "consig_estado": str(cliente_receptor.get("consig_estado") or "").strip(),
            "consig_pais": str(cliente_receptor.get("consig_pais") or "").strip(),
            "consig_codigo_postal": str(cliente_receptor.get("consig_codigo_postal") or "").strip(),
            "no_proveedor": str(cliente_receptor.get("no_proveedor") or "").strip(),
            "dias_credito": cliente_receptor.get("dias_credito"),
        },
        "lineas": lineas,
        "configuracion": dict(cfg or {}),
    }
    cfg_norm = _cfg_addenda_normalizado(cfg)
    tipo_addenda = _normalizar_clave_addenda(addenda.get("addenda_tipo"))
    parte_numerica_default = _folio_addenda_default(factura, config_empresa)
    condicion_predeterminada = "" if tipo_addenda in {"CF000NUEVA", "WAJ01NUEVA", "W001NUEVA"} else str(factura.get("numero_salida") or "")
    placeholders = {
        "PARTENUMERICA": _valor_cfg(cfg_norm, "PARTENUMERICA", parte_numerica_default),
        "IMPORTELETRAS": _valor_cfg(cfg_norm, "IMPORTELETRAS", _texto_importe_mxn(factura.get("total"))),
        "CONDICION": _valor_cfg(cfg_norm, "CONDICION", condicion_predeterminada),
        "ENVIARADIRECCION": _fecha_addenda_yyyymmdd(_valor_cfg(cfg_norm, "ENVIARADIRECCION", "")),
        "PARNONUMERICA": "CFDI",
        "CAMPOLIBRE2CLIE": _valor_cfg(cfg_norm, "CAMPOLIBRE2CLIE", cliente_receptor.get("gln_receptor") or ""),
        "RECEPCALLE": _valor_cfg(cfg_norm, "RECEPCALLE", _texto_addenda_base(cliente_receptor.get("calle") or "")),
        "RECEPNUMEXT": _valor_cfg(cfg_norm, "RECEPNUMEXT", cliente_receptor.get("no_exterior") or ""),
        "RECEPNUMINT": _valor_cfg(cfg_norm, "RECEPNUMINT", cliente_receptor.get("no_interior") or ""),
        "RECEPCOL": _valor_cfg(cfg_norm, "RECEPCOL", _texto_addenda_base(cliente_receptor.get("colonia") or "")),
        "RECEPMUNICIPIO": _valor_cfg(cfg_norm, "RECEPMUNICIPIO", _municipio_addenda(cliente_receptor.get("municipio") or "")),
        "RECEPESTADO": _valor_cfg(cfg_norm, "RECEPESTADO", _estado_addenda(cliente_receptor.get("estado") or "")),
        "RECEPCP": _valor_cfg(cfg_norm, "RECEPCP", cliente_receptor.get("codigo_postal") or ""),
        "RECEPRFC": _valor_cfg(cfg_norm, "RECEPRFC", cliente_receptor.get("rfc") or ""),
        "EMISORNOMBRE": _valor_cfg(cfg_norm, "EMISORNOMBRE", _texto_addenda_base(config_empresa.get("razon_social") or "")),
        "EMISORCALLE": _valor_cfg(cfg_norm, "EMISORCALLE", _texto_addenda_base(config_empresa.get("calle") or "")),
        "EMISORNUMEXT": _valor_cfg(cfg_norm, "EMISORNUMEXT", config_empresa.get("no_exterior") or ""),
        "EMISORNUMINT": _valor_cfg(cfg_norm, "EMISORNUMINT", ""),
        "EMISORCOL": _valor_cfg(cfg_norm, "EMISORCOL", _texto_addenda_base(config_empresa.get("colonia") or "")),
        "EMISORMUNICIPIO": _valor_cfg(cfg_norm, "EMISORMUNICIPIO", _municipio_addenda(config_empresa.get("municipio") or "")),
        "EMISORESTADO": _valor_cfg(cfg_norm, "EMISORESTADO", _estado_addenda(config_empresa.get("estado") or "")),
        "EMISORCP": _valor_cfg(cfg_norm, "EMISORCP", config_empresa.get("cp_fiscal") or ""),
        "EMISORRFC": _valor_cfg(cfg_norm, "EMISORRFC", config_empresa.get("rfc_emisor") or ""),
        "CAMPOLIBRE2CONSIG": (
            cliente_receptor.get("gln_consignatario")
            if cliente_receptor.get("gln_consignatario") not in (None, "")
            else _valor_cfg(cfg_norm, "CAMPOLIBRE2CONSIG", "")
        ),
        "CONSIGNARNOMBRE": (
            _nombre_consignatario_addenda(cliente_receptor.get("consignatario") or "")
            or _valor_cfg(cfg_norm, "CONSIGNARNOMBRE", "")
        ),
        "CONSIGNARDIRECCION": (
            _direccion_consignatario_addenda(cliente_receptor.get("consig_calle"), cliente_receptor.get("consig_no_exterior"))
            or _valor_cfg(cfg_norm, "CONSIGNARDIRECCION", "")
        ),
        "CONSIGNARCOLONIA": (
            _texto_addenda_base(cliente_receptor.get("consig_colonia") or "")
            or _valor_cfg(cfg_norm, "CONSIGNARCOLONIA", "")
        ),
        "CONSIGNARPOBLA": (
            _poblacion_consignatario_addenda(cliente_receptor.get("consig_municipio") or "", cliente_receptor.get("consig_estado") or "")
            or _valor_cfg(cfg_norm, "CONSIGNARPOBLA", "")
        ),
        "CONSIGNARCODIGO": (
            cliente_receptor.get("consig_codigo_postal")
            if cliente_receptor.get("consig_codigo_postal") not in (None, "")
            else _valor_cfg(cfg_norm, "CONSIGNARCODIGO", "")
        ),
        "DIASCREDITO": (
            cliente_receptor.get("dias_credito")
            if cliente_receptor.get("dias_credito") not in (None, "")
            else _valor_cfg(cfg_norm, "DIASCREDITO", 0)
        ),
        "LINEASPRODUCTOS": len(lineas),
        "IMPORTE": _fmt_num(factura.get("total"), 2),
        "IMPORTE(#0.00)": _fmt_num(factura.get("total"), 2),
        "SUBTOTAL": _fmt_num(factura.get("subtotal") or factura.get("total"), 2),
        "SUBTOTAL(#0.00)": _fmt_num(factura.get("subtotal") or factura.get("total"), 2),
        "TOTALDESCUENTOS(#0.00)": _fmt_num(factura.get("descuento"), 2),
        "PORCENIMPUESTO4": _valor_cfg(cfg_norm, "PORCENIMPUESTO4", "0.00"),
        "MONTOIMPUESTO4": _fmt_num(factura.get("iva"), 2),
        "MONTOIMPUESTO4(#0.00)": _fmt_num(factura.get("iva"), 2),
        "CONTSEGMENTOS": _valor_cfg(cfg_norm, "CONTSEGMENTOS", ""),
        "FACTURA": str(factura.get("factura") or "").strip(),
        "FECHA_YYYYMMDD": fecha_cfdi_ref.strftime("%Y%m%d"),
        "FECHA_YYMMDD": fecha_cfdi_ref.strftime("%y%m%d"),
        "HORA_HHMM": fecha_cfdi_ref.strftime("%H%M"),
        "HORA_HHMMSS": fecha_cfdi_ref.strftime("%H%M%S"),
        "FECHADOCTO(YYYYMMDD)": fecha_cfdi_ref.strftime("%Y%m%d"),
        "FECHADOCTO(YYMMDD)": fecha_cfdi_ref.strftime("%y%m%d"),
        "FECHADOCTO(yyyy-mm-dd)": fecha_cfdi_ref.strftime("%Y-%m-%d"),
        "FECHAPEDIDO(yyyy-mm-dd)": _fecha_yyyymmdd_con_guiones(_valor_cfg(cfg_norm, "ENVIARADIRECCION", fecha_cfdi_ref.strftime("%Y%m%d"))),
        "FECHARECEP(yyyy-mm-dd)": _fecha_yyyymmdd_con_guiones(_valor_cfg(cfg_norm, "ENVIARADIRECCION", fecha_cfdi_ref.strftime("%Y%m%d"))),
        "HORADOCTO(HHMM)": fecha_cfdi_ref.strftime("%H%M"),
        "HORADOCTO(HHMMSS)": fecha_cfdi_ref.strftime("%H%M%S"),
        "GLN_BY": cliente_receptor.get("gln_receptor") or "",
        "GLN_ST": cliente_receptor.get("gln_consignatario") or "",
        "GLN_SU": (payload.get("emisor") or {}).get("gln_supplier") or "",
        "FOLIO": _valor_cfg(cfg_norm, "FOLIO", _fmt_folio_addenda(factura)),
        "IMPORTEANTESIMPUESTOS": _fmt_num(factura.get("subtotal") or factura.get("total"), 2),
        "MONTOIMPUESTO1": _valor_cfg(cfg_norm, "MONTOIMPUESTO1", "0.00"),
        "PORCENIMPUESTO1": _valor_cfg(cfg_norm, "PORCENIMPUESTO1", "0.00"),
        "TIPODOCTO": _valor_cfg(cfg_norm, "TIPODOCTO", "INVOICE"),
    }
    placeholders = _aplicar_limites_campos_addenda(placeholders, LIMITES_PLACEHOLDERS_ADDENDA)
    payload["placeholders_resueltos"] = placeholders
    config_efectiva = dict(payload.get("configuracion") or {})
    config_efectiva.update({clave: valor for clave, valor in placeholders.items() if clave not in {
        "FACTURA", "FECHA_YYYYMMDD", "FECHA_YYMMDD", "HORA_HHMM", "HORA_HHMMSS", "GLN_BY", "GLN_ST", "GLN_SU",
    }})
    payload["configuracion_guardada"] = dict(payload.get("configuracion") or {})
    payload["configuracion"] = config_efectiva
    condicion_linea = str(placeholders.get("CONDICION") or factura.get("numero_salida") or "").strip()
    payload["lineas_addenda"] = [
        _aplicar_limites_campos_addenda({
            "NUMPARTIDA": item["partida"],
            "PRODALTERNA": _fmt_gtin(item["codigo_alterno"]),
            "PRODDESCRIP": item["descripcion"],
            "CANTIDAD": _fmt_num(item["cantidad_modo"], 4),
            "PRODSUBTOTAL": _fmt_num(item["importe"], 2),
            "PRODPRECIO": _fmt_num(item["precio"], 3),
            "PRODPORCENIMP4": "0.00",
            "PRODPORCENIMP4(#0.00)": "0.00",
            "PRODMONTOIMP4": "0.00",
            "NO_IDENTIFICACION": item["no_identificacion"],
            "CLAVEPRODSERV": item["clave_prod_serv"],
            "CLAVEUNIDAD": item["clave_unidad"],
            "UNIDAD": item["unidad"],
            "PRODIMPORTE": _fmt_num(item["importe"], 2),
            "PRODLIBRE2": str(item["codigo_alterno"] or "").strip(),
            "NUMEMPAQUESPROD": str(int(item.get("piezas") or item["cantidad_modo"] or 0)),
            "CONDICION": condicion_linea,
        }, LIMITES_LINEAS_ADDENDA)
        for item in lineas
    ]
    render_previo = _render_addenda_text(addenda.get("addenda_ruta"), placeholders, payload["lineas_addenda"])
    placeholders["CONTSEGMENTOS"] = _contar_segmentos_addenda(render_previo) if not str(placeholders.get("CONTSEGMENTOS", "")).strip() else placeholders["CONTSEGMENTOS"]
    payload["campos_sugeridos_cfg"] = {
        "CONDICION": "Folio de recibo capturado manualmente para esta factura",
        "ENVIARADIRECCION": "Fecha de recibo en formato AAAAMMDD",
        "PARNONUMERICA": "Serie fija para addenda (CFDI)",
        "PARTENUMERICA": "Folio numerico del CFDI fiscal",
        "CAMPOLIBRE2CLIE": "GLN del buyer/cliente receptor (BY)",
        "RECEPCALLE": "Calle fiscal del receptor si la addenda la exige",
        "RECEPNUMEXT": "Número exterior fiscal del receptor",
        "RECEPNUMINT": "Número interior fiscal del receptor",
        "RECEPCOL": "Colonia fiscal del receptor",
        "RECEPMUNICIPIO": "Municipio fiscal del receptor",
        "RECEPESTADO": "Estado fiscal del receptor",
        "EMISORCALLE": "Calle del emisor",
        "EMISORNUMEXT": "Número exterior del emisor",
        "EMISORNUMINT": "Número interior del emisor",
        "EMISORCOL": "Colonia del emisor",
        "EMISORMUNICIPIO": "Municipio del emisor",
        "EMISORESTADO": "Estado del emisor",
        "CAMPOLIBRE2CONSIG": "GLN del consignatario / shipTo (ST)",
        "CONTSEGMENTOS": "Conteo total de segmentos de la addenda si se requiere exacto",
    }
    payload["placeholders_faltantes"] = [
        clave for clave in (payload.get("placeholders") or [])
        if str(placeholders.get(clave, "")).strip() == ""
        and clave not in {"NUMPARTIDA", "PRODALTERNA", "PRODDESCRIP", "CANTIDAD", "PRODSUBTOTAL", "PRODPRECIO", "PRODPORCENIMP4", "PRODMONTOIMP4", "EMISORNUMINT", "PRODIMPORTE", "PRODLIBRE2", "NUMEMPAQUESPROD"}
    ]
    campos_manuales = CAMPOS_MANUALES_OBLIGATORIOS_ADDENDA.get(
        _normalizar_clave_addenda(addenda.get("addenda_tipo")), ()
    )
    for clave in campos_manuales:
        if not str(_valor_cfg(_cfg_addenda_normalizado(cfg_factura), clave, "")).strip():
            payload["placeholders_faltantes"].append(clave)
    payload["placeholders_faltantes"] = list(dict.fromkeys(payload["placeholders_faltantes"]))
    payload["xml_renderizado"] = _render_addenda_text(addenda.get("addenda_ruta"), placeholders, payload["lineas_addenda"])
    return payload


def sincronizar_factura_para_timbrado(conn, conn_legacy, factura_id, motivo="ALTA", opciones_cfdi=None):
    _asegurar_tablas_timbrado(conn)
    factura = _snapshot_factura(conn_legacy, factura_id)
    empresa = _normalizar_empresa(factura.get("empresa"))
    config = obtener_config_timbrado(conn, empresa)
    resolucion = resolver_receptor_timbrado(conn, conn_legacy, factura)
    addenda = resolucion.get("addenda") or {}
    factura["numero_cliente_cfdi"] = resolucion.get("cliente_receptor_numero")
    factura["cliente_nombre_cfdi"] = resolucion.get("cliente_receptor_nombre")
    timbrado_requerido = 1 if config and config.get("timbrado_activo") else 0
    requiere_addenda = 1 if addenda else 0
    addenda_payload = _construir_payload_addenda(addenda, factura, resolucion, config)
    opciones_cfdi_json = json.dumps(opciones_cfdi or {}, ensure_ascii=False)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if timbrado_requerido:
        conn.execute(
            """
            INSERT INTO timbrado_queue (
                factura_id, factura, empresa, numero_cliente,
                cliente_origen_numero, cliente_origen_nombre,
                cliente_receptor_numero, cliente_receptor_nombre,
                modo_facturacion, regla_redireccion_id, estatus,
                requiere_addenda, addenda_tipo, addenda_payload_json, cfdi_opciones_json, proveedor,
                queued_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(factura_id) DO UPDATE SET
                factura = excluded.factura,
                empresa = excluded.empresa,
                numero_cliente = excluded.numero_cliente,
                cliente_origen_numero = excluded.cliente_origen_numero,
                cliente_origen_nombre = excluded.cliente_origen_nombre,
                cliente_receptor_numero = excluded.cliente_receptor_numero,
                cliente_receptor_nombre = excluded.cliente_receptor_nombre,
                modo_facturacion = excluded.modo_facturacion,
                regla_redireccion_id = excluded.regla_redireccion_id,
                estatus = excluded.estatus,
                requiere_addenda = excluded.requiere_addenda,
                addenda_tipo = excluded.addenda_tipo,
                addenda_payload_json = excluded.addenda_payload_json,
                cfdi_opciones_json = excluded.cfdi_opciones_json,
                proveedor = excluded.proveedor,
                intento_count = 0,
                uuid = '',
                xml_path = '',
                snapshot_path = '',
                ultimo_error = NULL,
                last_attempt_at = NULL
            """,
            (
                factura_id,
                factura.get("factura"),
                empresa,
                str(resolucion.get("cliente_receptor_numero") or factura.get("numero_cliente") or "").strip(),
                str(resolucion.get("cliente_origen_numero") or factura.get("numero_cliente") or "").strip(),
                str(resolucion.get("cliente_origen_nombre") or factura.get("consignatario") or "").strip(),
                str(resolucion.get("cliente_receptor_numero") or factura.get("numero_cliente") or "").strip(),
                str(resolucion.get("cliente_receptor_nombre") or factura.get("consignatario") or "").strip(),
                str(resolucion.get("modo_facturacion") or "").strip() or None,
                resolucion.get("regla", {}).get("id"),
                ESTATUS_PENDIENTE,
                requiere_addenda,
                addenda.get("addenda_tipo") if addenda else None,
                json.dumps(addenda_payload, ensure_ascii=False) if addenda_payload else None,
                opciones_cfdi_json,
                config.get("proveedor"),
                now,
            ),
        )
    return {
        "factura_id": factura_id,
        "factura": factura.get("factura"),
        "empresa": empresa,
        "timbrado_requerido": bool(timbrado_requerido),
        "requiere_addenda": bool(requiere_addenda),
        "addenda_tipo": addenda.get("addenda_tipo") if addenda else None,
        "cliente_receptor_numero": resolucion.get("cliente_receptor_numero"),
        "cliente_receptor_nombre": resolucion.get("cliente_receptor_nombre"),
        "modo_facturacion": resolucion.get("modo_facturacion"),
        "motivo": motivo,
    }


def listar_cola_timbrado(conn, empresa=None, estatus=None, limit=200):
    _asegurar_tablas_timbrado(conn)
    sql = "SELECT q.* FROM timbrado_queue q WHERE 1=1"
    params = []
    if empresa:
        sql += " AND q.empresa = ?"
        params.append(_normalizar_empresa(empresa))
    if estatus:
        sql += " AND q.estatus = ?"
        params.append(estatus)
    sql += " ORDER BY q.prioridad ASC, q.queued_at ASC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def registrar_intento_pac(conn, factura, proveedor, estatus, mensaje="", folio_candidato="", uuid_val="", xml_path="", response=None):
    _asegurar_tabla_pac_intentos(conn)
    conn.execute(
        """
        INSERT INTO timbrado_pac_intentos (
            factura_id, factura, empresa, proveedor, estatus, mensaje,
            folio_candidato, uuid, xml_path, response_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(factura.get("id") or factura.get("factura_id") or 0),
            str(factura.get("factura") or ""),
            _normalizar_empresa(factura.get("empresa")),
            str(proveedor or "").strip().upper(),
            str(estatus or "").strip().upper(),
            str(mensaje or ""),
            str(folio_candidato or ""),
            str(uuid_val or ""),
            str(xml_path or ""),
            json.dumps(response or {}, ensure_ascii=False, default=str),
        ),
    )


def listar_intentos_pac(conn, factura=None, empresa=None, limit=100):
    _asegurar_tablas_timbrado(conn)
    sql = "SELECT * FROM timbrado_pac_intentos WHERE 1=1"
    params = []
    if factura:
        sql += " AND factura = ?"
        params.append(str(factura).strip())
    if empresa:
        sql += " AND empresa = ?"
        params.append(_normalizar_empresa(empresa))
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def listar_cfdi_emitidos(conn, empresa=None, limit=200):
    _asegurar_tablas_timbrado(conn)
    sql = "SELECT * FROM cfdi_emitidos WHERE 1=1"
    params = []
    if empresa:
        sql += " AND empresa = ?"
        params.append(_normalizar_empresa(empresa))
    sql += " ORDER BY fecha_timbrado DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def renderizar_addenda_factura(conn, conn_legacy, factura):
    empresa = _normalizar_empresa(factura.get("empresa"))
    resolucion = resolver_receptor_timbrado(conn, conn_legacy, factura)
    addenda = resolucion.get("addenda") or {}
    if not addenda:
        return {
            "tiene_addenda": False,
            "empresa": empresa,
            "factura": factura.get("factura"),
            "addenda_tipo": None,
            "xml_renderizado": "",
            "preview": {},
        }
    config = obtener_config_timbrado(conn, empresa)
    factura_preview = dict(factura or {})
    preview = _construir_payload_addenda(addenda, factura_preview, resolucion, config)
    return {
        "tiene_addenda": True,
        "empresa": empresa,
        "factura": factura.get("factura"),
        "addenda_tipo": preview.get("tipo"),
        "ruta_xml": preview.get("ruta_xml"),
        "xml_renderizado": preview.get("xml_renderizado") or "",
        "preview": preview,
    }


def _obtener_siguiente_folio(conn, empresa):
    empresa = _normalizar_empresa(empresa)
    config = obtener_config_timbrado(conn, empresa)
    folio_actual = str(config.get("folio_actual") or "0").strip()
    digitos = re.sub(r"[^0-9]", "", folio_actual)
    if not digitos:
        digitos = "1"
    return digitos


def _avanzar_folio_empresa(conn, empresa, folio_emitido):
    empresa = _normalizar_empresa(empresa)
    digitos = re.sub(r"[^0-9]", "", str(folio_emitido or ""))
    if not digitos:
        return
    nuevo_folio = str(int(digitos) + 1)
    conn.execute(
        "UPDATE empresas_timbrado SET folio_actual = ? WHERE empresa = ?",
        (nuevo_folio, empresa),
    )


def _folio_serie_cfdi(serie, folio):
    serie_txt = str(serie or "").strip()
    folio_txt = str(folio or "").strip()
    return f"{serie_txt}{folio_txt}" if serie_txt else folio_txt


def _guardar_folio_sae_legacy(conn_legacy, factura_ids, serie, folio):
    folio_sae = _folio_serie_cfdi(serie, folio)
    ids = [int(fid) for fid in (factura_ids or []) if fid]
    if not folio_sae or not ids:
        return folio_sae
    cur = conn_legacy.cursor()
    try:
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(f"UPDATE facturas SET sae_codigo = %s WHERE id IN ({placeholders})", tuple([folio_sae] + ids))
        conn_legacy.commit()
    finally:
        cur.close()
    return folio_sae


def _generar_cfdi_simulado_xml(factura, config, addenda_render, item, cfdi_folio, serie):
    preview = addenda_render.get("preview") or {}
    modo = str(item.get("modo_facturacion") or preview.get("modo_facturacion") or "PIEZAS").strip()
    folio = cfdi_folio
    fecha = _fecha_referencia_cfdi(factura)
    fecha_str = fecha.strftime("%Y-%m-%dT%H:%M:%S")
    subtotal_dec = _money_cfdi(factura.get("subtotal") or factura.get("total") or 0)
    iva_total_cfdi = _money_cfdi(factura.get("iva"))
    total_fiscal_dec = _money_cfdi(factura.get("total"))
    descuento_total_cfdi = _descuento_cfdi_compatible_con_total(
        subtotal_dec,
        factura.get("descuento"),
        iva_total_cfdi,
        total_fiscal_dec,
    )
    # No se cambia el total, las cantidades ni los precios de la remisión.
    # Únicamente se alinea un posible redondeo de descuento de uno/dos centavos
    # para que el CFDI cumpla la identidad aritmética exigida por el SAT.
    subtotal = _fmt_money_cfdi(subtotal_dec)
    total = _fmt_money_cfdi(total_fiscal_dec)
    lugar_exp = str(config.get("cp_fiscal") or config.get("lugar_expedicion") or "03810").strip()

    emisor = preview.get("emisor") or {}
    receptor = preview.get("receptor") or {}
    emisor_rfc = xml_escape(str(emisor.get("rfc") or config.get("rfc_emisor") or "").strip())
    emisor_nombre = xml_escape(str(emisor.get("razon_social") or config.get("razon_social") or "").strip())
    emisor_regimen = xml_escape(str(emisor.get("regimen_fiscal") or config.get("regimen_fiscal") or "601").strip())
    opciones_cfdi = _opciones_cfdi_desde_item(item)
    es_factura_global = _es_venta_mostrador(factura, receptor)
    if es_factura_global:
        receptor = {**receptor, **_receptor_publico_general(config)}
        opciones_cfdi = {**opciones_cfdi, "uso_cfdi": "S01"}

    receptor_rfc = xml_escape(str(receptor.get("rfc") or factura.get("rfc") or "").strip())
    receptor_nombre = xml_escape(str(receptor.get("razon_social") or item.get("cliente_receptor_nombre") or factura.get("cliente_nombre") or "").strip())
    receptor_regimen = xml_escape(str(receptor.get("regimen_fiscal") or "601").strip())
    uso_cfdi = xml_escape(str(opciones_cfdi.get("uso_cfdi") or receptor.get("uso_cfdi") or "G01").strip())
    cp_receptor = xml_escape(str(receptor.get("codigo_postal") or factura.get("codigo_postal") or lugar_exp).strip())
    forma_pago = xml_escape(str(opciones_cfdi.get("forma_pago") or "99").strip())
    metodo_pago = xml_escape(str(opciones_cfdi.get("metodo_pago") or "PPD").strip())
    exportacion = xml_escape(str(opciones_cfdi.get("exportacion") or "01").strip())
    moneda = xml_escape(str(opciones_cfdi.get("moneda") or "MXN").strip().upper())
    csd_material = obtener_material_csd(config) if str(config.get("csd_cer_path") or "").strip() else {}
    no_certificado = xml_escape(csd_material.get("no_certificado") or "00000000000000000000")
    certificado = xml_escape(csd_material.get("certificado") or "")

    xml_parts = []
    xml_parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    tipo_addenda = _normalizar_clave_addenda(preview.get("tipo") or preview.get("addenda_tipo"))
    condicion_addenda = str(
        (preview.get("configuracion_guardada") or {}).get("CONDICION")
        or (preview.get("placeholders_resueltos") or {}).get("CONDICION")
        or ""
    ).strip()
    # En Walmart la misma referencia manual alimenta el encabezado CFDI y el
    # RFF+DQ de la addenda. Es el patrón usado por el sistema original.
    if tipo_addenda in {"WAJ01NUEVA", "W001NUEVA"} and condicion_addenda:
        condiciones = condicion_addenda
    else:
        condiciones = str(
            opciones_cfdi.get("condiciones_pago")
            or condicion_addenda
            or factura.get("numero_salida")
            or ""
        ).strip()
    descuento_attr = f' Descuento="{_fmt_money_cfdi(descuento_total_cfdi)}"' if descuento_total_cfdi > 0 else ""
    xml_parts.append(
        '<cfdi:Comprobante'
        ' xmlns:cfdi="http://www.sat.gob.mx/cfd/4"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        ' xsi:schemaLocation="http://www.sat.gob.mx/cfd/4 http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd"'
        f' Version="4.0"'
        f' Serie="{xml_escape(serie)}"'
        f' Folio="{xml_escape(folio)}"'
        f' Fecha="{fecha_str}"'
        f' FormaPago="{forma_pago}"'
        f' NoCertificado="{no_certificado}"'
        f' Certificado="{certificado}"'
        f' SubTotal="{subtotal}"'
        f'{descuento_attr}'
        f' Moneda="{moneda}"'
        f' Total="{total}"'
        f' TipoDeComprobante="I"'
        f' Exportacion="{exportacion}"'
        f' MetodoPago="{metodo_pago}"'
        f' CondicionesDePago="{xml_escape(condiciones)}"'
        f' LugarExpedicion="{lugar_exp}"'
        '>'
    )

    if es_factura_global:
        # CFDI 4.0 exige InformacionGlobal cuando se usa el RFC genérico con
        # el nombre PUBLICO EN GENERAL. Cada venta de mostrador se integra a
        # la factura global mensual del mes de emisión.
        xml_parts.append(
            f'  <cfdi:InformacionGlobal Periodicidad="04" Meses="{fecha.strftime("%m")}" Año="{fecha.strftime("%Y")}"/>'
        )
    xml_parts.append(f'  <cfdi:Emisor Rfc="{emisor_rfc}" Nombre="{emisor_nombre}" RegimenFiscal="{emisor_regimen}"/>')
    xml_parts.append(f'  <cfdi:Receptor Rfc="{receptor_rfc}" Nombre="{receptor_nombre}" DomicilioFiscalReceptor="{cp_receptor}" RegimenFiscalReceptor="{receptor_regimen}" UsoCFDI="{uso_cfdi}"/>')

    xml_parts.append('  <cfdi:Conceptos>')
    conceptos = []
    for prod in (factura.get("productos") or []):
        cantidad_modo, modo_producto = _cantidad_producto_cfdi(prod, modo)
        importe_linea_dec = _money_cfdi(_importe_linea_producto(prod, cantidad_modo, modo_producto))
        tiene_iva = _producto_tiene_iva(prod)
        concepto = {
            "prod": prod,
            "cantidad_modo": cantidad_modo,
            "modo_producto": modo_producto,
            "importe": importe_linea_dec,
            "tiene_iva": tiene_iva,
            "descuento": Decimal("0.00"),
            "base_impuesto": importe_linea_dec,
            "iva": Decimal("0.00"),
        }
        conceptos.append(concepto)
    subtotal_objetivo = _money_cfdi(factura.get("subtotal") or factura.get("total"))
    if conceptos:
        diferencia_subtotal = subtotal_objetivo - sum((c["importe"] for c in conceptos), Decimal("0.00"))
        if abs(diferencia_subtotal) <= Decimal("0.05"):
            conceptos[-1]["importe"] = max(Decimal("0.00"), conceptos[-1]["importe"] + diferencia_subtotal)
    descuentos = _distribuir_descuento_cfdi(descuento_total_cfdi, [c["importe"] for c in conceptos])
    for concepto, descuento_linea in zip(conceptos, descuentos):
        concepto["descuento"] = descuento_linea
        concepto["base_impuesto"] = max(Decimal("0.00"), concepto["importe"] - descuento_linea)
    gravadas_idx = [idx for idx, concepto in enumerate(conceptos) if concepto["tiene_iva"]]
    bases_gravadas = [conceptos[idx]["base_impuesto"] for idx in gravadas_idx]
    iva_por_gravado = _distribuir_iva_cfdi(factura, bases_gravadas)
    for idx, iva_linea in zip(gravadas_idx, iva_por_gravado):
        conceptos[idx]["iva"] = iva_linea

    bases_por_tasa = {"0.160000": Decimal("0.00"), "0.000000": Decimal("0.00")}
    impuestos_por_tasa = {"0.160000": Decimal("0.00"), "0.000000": Decimal("0.00")}
    for concepto in conceptos:
        prod = concepto["prod"]
        cantidad_modo = concepto["cantidad_modo"]
        modo_producto = concepto["modo_producto"]
        importe_linea_dec = concepto["importe"]
        desc = xml_escape(str(prod.get("descripcion") or "").strip())
        no_id = xml_escape(str(prod.get("no_identificacion") or prod.get("cip") or "").strip())
        clave_prod = xml_escape(str(prod.get("clave_prod_serv") or "01010101").strip())
        clave_unidad = xml_escape(_clave_unidad_sat(prod.get("unidad"), modo_producto, prod))
        unidad = xml_escape(_unidad_cfdi_texto(prod.get("unidad"), modo_producto))
        try:
            valor_unitario_dec = importe_linea_dec / Decimal(str(cantidad_modo or 1))
        except Exception:
            valor_unitario_dec = _money_cfdi(prod.get("precio"))
        precio = _fmt_valor_unitario_cfdi(valor_unitario_dec)
        importe_str = _fmt_money_cfdi(importe_linea_dec)
        cantidad_str = _fmt_cantidad(cantidad_modo)
        descuento_linea = concepto["descuento"]
        descuento_concepto_attr = f' Descuento="{_fmt_money_cfdi(descuento_linea)}"' if descuento_linea > 0 else ""
        tasa_ocuota = "0.160000" if concepto["tiene_iva"] and iva_total_cfdi > 0 else "0.000000"
        iva_linea = concepto["iva"] if tasa_ocuota == "0.160000" else Decimal("0.00")
        bases_por_tasa[tasa_ocuota] += concepto["base_impuesto"]
        impuestos_por_tasa[tasa_ocuota] += iva_linea
        xml_parts.append(
            f'    <cfdi:Concepto'
            f' ClaveProdServ="{clave_prod}"'
            f' NoIdentificacion="{no_id}"'
            f' Cantidad="{cantidad_str}"'
            f' ClaveUnidad="{clave_unidad}"'
            f' Unidad="{unidad}"'
            f' Descripcion="{desc}"'
            f' ValorUnitario="{precio}"'
            f' Importe="{importe_str}"'
            f'{descuento_concepto_attr}'
            f' ObjetoImp="02"'
            '>'
        )
        base_grav = _fmt_money_cfdi(concepto["base_impuesto"])
        iva_linea_str = _fmt_money_cfdi(iva_linea)
        xml_parts.append('      <cfdi:Impuestos>')
        xml_parts.append('        <cfdi:Traslados>')
        xml_parts.append(f'          <cfdi:Traslado Base="{base_grav}" Impuesto="002" TipoFactor="Tasa" TasaOCuota="{tasa_ocuota}" Importe="{iva_linea_str}"/>')
        xml_parts.append('        </cfdi:Traslados>')
        xml_parts.append('      </cfdi:Impuestos>')
        xml_parts.append('    </cfdi:Concepto>')
    xml_parts.append('  </cfdi:Conceptos>')

    total_impuestos = _fmt_money_cfdi(iva_total_cfdi)
    xml_parts.append(f'  <cfdi:Impuestos TotalImpuestosTrasladados="{total_impuestos}">')
    xml_parts.append(f'    <cfdi:Traslados>')
    for tasa in ("0.000000", "0.160000"):
        base = bases_por_tasa.get(tasa, Decimal("0.00"))
        impuesto = impuestos_por_tasa.get(tasa, Decimal("0.00"))
        if base <= 0 and impuesto <= 0:
            continue
        xml_parts.append(f'      <cfdi:Traslado Base="{_fmt_money_cfdi(base)}" Impuesto="002" TipoFactor="Tasa" TasaOCuota="{tasa}" Importe="{_fmt_money_cfdi(impuesto)}"/>')
    xml_parts.append(f'    </cfdi:Traslados>')
    xml_parts.append('  </cfdi:Impuestos>')

    addenda_xml = str(addenda_render.get("xml_renderizado") or "").strip()
    if addenda_xml:
        xml_parts.append('  <cfdi:Addenda>')
        xml_parts.append(addenda_xml)
        xml_parts.append('  </cfdi:Addenda>')

    xml_parts.append('</cfdi:Comprobante>')
    return "\n".join(xml_parts)


def procesar_siguiente_timbrado(conn, conn_legacy, folio: str | None = None):
    _asegurar_tablas_timbrado(conn)
    folio = str(folio or "").strip()
    if folio:
        row = conn.execute(
            """
            SELECT * FROM timbrado_queue
            WHERE estatus = ? AND factura = ?
            ORDER BY prioridad ASC, queued_at ASC
            LIMIT 1
            """,
            (ESTATUS_PENDIENTE, folio),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM timbrado_queue WHERE estatus = ? ORDER BY prioridad ASC, queued_at ASC LIMIT 1",
            (ESTATUS_PENDIENTE,),
        ).fetchone()
    if not row:
        return {"procesado": False, "factura": folio, "detalle": "Sin pendientes para este folio" if folio else "Sin pendientes"}
    item = dict(row)
    emitido_row = conn.execute(
        """
        SELECT factura, serie, folio_cfdi, uuid, xml_path
        FROM cfdi_emitidos
        WHERE factura_id = ? AND COALESCE(estatus_cfdi, '') NOT IN ('CANCELADA', 'CANCELADO')
        ORDER BY id DESC
        LIMIT 1
        """,
        (item["factura_id"],),
    ).fetchone()
    if emitido_row:
        emitido = dict(emitido_row)
        conn.execute(
            """
            UPDATE timbrado_queue
            SET estatus = ?, uuid = ?, xml_path = ?, ultimo_error = NULL
            WHERE id = ?
            """,
            (ESTATUS_TIMBRADA, emitido.get("uuid") or "", emitido.get("xml_path") or "", item["id"]),
        )
        return {
            "procesado": True,
            "factura": emitido.get("factura") or item.get("factura"),
            "ya_timbrada": True,
            "serie": emitido.get("serie") or "",
            "folio_cfdi": emitido.get("folio_cfdi") or "",
            "uuid": emitido.get("uuid") or "",
            "xml_path": emitido.get("xml_path") or "",
            "detalle": "La factura ya tenia CFDI emitido; no se envio de nuevo al PAC.",
        }
    en_proceso_previo = conn.execute(
        """
        SELECT factura, last_attempt_at
        FROM timbrado_queue
        WHERE empresa = ? AND estatus = ? AND id <> ?
        ORDER BY last_attempt_at ASC
        LIMIT 1
        """,
        (item.get("empresa") or "", ESTATUS_TIMBRANDO, item["id"]),
    ).fetchone()
    if en_proceso_previo:
        proceso = dict(en_proceso_previo)
        return {
            "procesado": False,
            "factura": item.get("factura"),
            "esperando_empresa": True,
            "empresa": item.get("empresa") or "",
            "factura_en_proceso": proceso.get("factura") or "",
            "last_attempt_at": proceso.get("last_attempt_at") or "",
            "detalle": "Ya hay una factura de esta empresa en TIMBRANDO; se evita procesar en paralelo para no duplicar folio fiscal.",
        }
    factura = _snapshot_factura(conn_legacy, int(item["factura_id"]))
    config = obtener_config_timbrado(conn, item["empresa"])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        if getattr(conn, "is_mysql", False):
            # MySQL no permite actualizar una tabla mientras se consulta a sí
            # misma en el subquery NOT EXISTS. El bloqueo de empresa ya se
            # verificó arriba; aquí se reclama atómicamente solo este renglón.
            cur_claim = conn.execute(
                """
                UPDATE timbrado_queue
                SET estatus = ?, last_attempt_at = ?, intento_count = intento_count + 1
                WHERE id = ? AND estatus = ?
                """,
                (ESTATUS_TIMBRANDO, now, item["id"], ESTATUS_PENDIENTE),
            )
        else:
            cur_claim = conn.execute(
                """
                UPDATE timbrado_queue
                SET estatus = ?, last_attempt_at = ?, intento_count = intento_count + 1
                WHERE id = ? AND estatus = ?
                  AND NOT EXISTS (
                    SELECT 1
                    FROM timbrado_queue tq_lock
                    WHERE tq_lock.empresa = ?
                      AND tq_lock.estatus = ?
                      AND tq_lock.id <> ?
                  )
                """,
                (ESTATUS_TIMBRANDO, now, item["id"], ESTATUS_PENDIENTE, item.get("empresa") or "", ESTATUS_TIMBRANDO, item["id"]),
            )
        if getattr(cur_claim, "rowcount", 0) != 1:
            en_proceso = conn.execute(
                """
                SELECT factura, last_attempt_at
                FROM timbrado_queue
                WHERE empresa = ? AND estatus = ? AND id <> ?
                ORDER BY last_attempt_at ASC
                LIMIT 1
                """,
                (item.get("empresa") or "", ESTATUS_TIMBRANDO, item["id"]),
            ).fetchone()
            if en_proceso:
                proceso = dict(en_proceso)
                return {
                    "procesado": False,
                    "factura": item.get("factura"),
                    "esperando_empresa": True,
                    "empresa": item.get("empresa") or "",
                    "factura_en_proceso": proceso.get("factura") or "",
                    "last_attempt_at": proceso.get("last_attempt_at") or "",
                    "detalle": "Ya hay una factura de esta empresa en TIMBRANDO; se evita procesar en paralelo para no duplicar folio fiscal.",
                }
            return {
                "procesado": False,
                "factura": item.get("factura"),
                "detalle": "La factura ya fue tomada por otro proceso o dejo de estar pendiente.",
            }
        proveedor = (config.get("proveedor") or "").strip().upper()
        if not config or not config.get("timbrado_activo"):
            raise RuntimeError("La empresa no tiene timbrado activo.")
        if proveedor != "SIMULADO":
            preflight = validar_preflight_pac(config)
            if not preflight.get("ok"):
                raise RuntimeError("Preflight PAC fallido: " + "; ".join(preflight.get("errores") or []))
        opciones_cfdi = _opciones_cfdi_desde_item(item)
        resolucion_validacion = resolver_receptor_timbrado(conn, conn_legacy, factura, incluir_preview=False)
        validacion_cfdi = validar_pre_cfdi_factura(
            factura,
            config,
            resolucion=resolucion_validacion,
            opciones_cfdi=opciones_cfdi,
        )
        if proveedor != "SIMULADO" and not validacion_cfdi.get("ok"):
            mensajes = "; ".join(x.get("mensaje", "") for x in validacion_cfdi.get("faltantes") or [])
            raise RuntimeError(f"Validación pre-PAC fallida: {mensajes}")
        # La fecha del CFDI que recibe un PAC debe corresponder al momento de
        # timbrado. La fecha de la remisión se conserva para la addenda.
        fecha_cfdi = _ahora_cfdi_mexico() if (proveedor != "SIMULADO" or opciones_cfdi.get("usar_fecha_actual")) else _fecha_referencia_cfdi(factura)
        addenda_render = renderizar_addenda_factura(conn, conn_legacy, factura)
        faltantes_addenda = (addenda_render.get("preview") or {}).get("placeholders_faltantes") or []
        if faltantes_addenda:
            raise RuntimeError(
                "Faltan datos requeridos de addenda: " + ", ".join(str(x) for x in faltantes_addenda)
            )
        serie = str(config.get("serie_cfdi") or "").strip()
        cfdi_folio = _obtener_siguiente_folio(conn, item["empresa"])
        factura["folio_cfdi"] = cfdi_folio
        factura["fecha_cfdi"] = fecha_cfdi
        contenido = _generar_cfdi_simulado_xml(factura, config, addenda_render, item, cfdi_folio, serie)
        if proveedor != "SIMULADO":
            # Detectar si la addenda es EDIFACT (como Walmart) o XML (como City Market)
            addenda_es_edifact = False
            if addenda_render.get("tiene_addenda") and addenda_render.get("xml_renderizado"):
                addenda_xml = addenda_render.get("xml_renderizado", "")
                # Si contiene segmentos EDIFACT típicos, es EDIFACT
                if any(seg in addenda_xml for seg in ["UNB+", "UNH+", "BGM+", "DTM+", "NAD+", "LIN+", "UNT+", "UNZ+"]):
                    addenda_es_edifact = True
            
            # Para addendas EDIFACT, generar XML sin addenda para timbrado
            if addenda_es_edifact:
                addenda_vacia = {"tiene_addenda": False, "xml_renderizado": "", "preview": addenda_render.get("preview", {})}
                contenido = _generar_cfdi_simulado_xml(factura, config, addenda_vacia, item, cfdi_folio, serie)
            
            sellado = sellar_xml_cfdi(contenido, config)
            contenido_pac = sellado.get("xml") if sellado.get("ok") else contenido
            try:
                resultado_pac = timbrar_xml_pac(proveedor, config, contenido_pac)
            except (PacNoIntegradoError, PacTimbradoError) as exc:
                pac_error = str(exc)
                prexml_path = _guardar_prexml_pac(config, factura, contenido_pac)
                sellado_msg = "XML sellado." if sellado.get("ok") else "XML sin sello: " + "; ".join(sellado.get("errores") or ["sellado no disponible"])
                mensaje = f"{pac_error} {sellado_msg} XML pre-PAC generado sin consumir folio."
                registrar_intento_pac(
                    conn,
                    factura,
                    proveedor,
                    ESTATUS_BLOQUEADO_PAC,
                    mensaje,
                    folio_candidato=cfdi_folio,
                    xml_path=prexml_path,
                    response={
                        "validacion_cfdi": validacion_cfdi,
                        "sellado": {
                            "ok": bool(sellado.get("ok")),
                            "errores": sellado.get("errores") or [],
                            "advertencias": sellado.get("advertencias") or [],
                        },
                    },
                )
                conn.execute(
                    "UPDATE timbrado_queue SET estatus = ?, xml_path = ?, ultimo_error = ?, last_attempt_at = ? WHERE id = ?",
                    (ESTATUS_BLOQUEADO_PAC, prexml_path, mensaje, now, item["id"]),
                )
                return {
                    "procesado": False,
                    "factura": factura.get("factura"),
                    "bloqueo_pac": True,
                    "modo": proveedor,
                    "serie": serie,
                    "folio_candidato": cfdi_folio,
                    "prexml_path": prexml_path,
                    "validacion_cfdi": validacion_cfdi,
                    "detalle": mensaje,
                }
            
            # Para addendas EDIFACT, insertar la addenda después del timbrado
            xml_timbrado = resultado_pac.xml_timbrado
            if addenda_es_edifact and addenda_render.get("tiene_addenda") and addenda_render.get("xml_renderizado"):
                xml_timbrado = _insertar_addenda_en_xml_timbrado(xml_timbrado, addenda_render.get("xml_renderizado"))
            
            output_dir = str(config.get("output_dir") or ruta_empresa_fiscal(item["empresa"]))
            anio = str(fecha_cfdi.year)
            xml_dir = os.path.join(output_dir, anio, "xml")
            os.makedirs(xml_dir, exist_ok=True)
            xml_path = os.path.join(xml_dir, f"{factura.get('factura')}-{serie}{cfdi_folio}.xml")
            with open(xml_path, "w", encoding="utf-8") as f:
                f.write(xml_timbrado)
            snapshot_path = _guardar_snapshot_json(config, factura)
            conn.execute(
                """
                INSERT INTO cfdi_emitidos (
                    factura_id, factura, empresa, cliente_receptor_numero, cliente_receptor_nombre,
                    serie, folio_cfdi, uuid, estatus_cfdi, xml_path, addenda_tipo, orden_compra, fecha_timbrado
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'TIMBRADA', ?, ?, ?, ?)
                """,
                (
                    item["factura_id"],
                    factura.get("factura"),
                    item["empresa"],
                    item.get("cliente_receptor_numero"),
                    item.get("cliente_receptor_nombre"),
                    serie,
                    cfdi_folio,
                    resultado_pac.uuid,
                    xml_path,
                    item.get("addenda_tipo"),
                    str(opciones_cfdi.get("orden_compra") or "").strip(),
                    now,
                ),
            )
            _avanzar_folio_empresa(conn, item["empresa"], cfdi_folio)
            folio_sae = _guardar_folio_sae_legacy(conn_legacy, [item["factura_id"]], serie, cfdi_folio)
            conn.execute(
                "UPDATE timbrado_queue SET estatus = ?, xml_path = ?, uuid = ?, ultimo_error = NULL, last_attempt_at = ? WHERE id = ?",
                (ESTATUS_TIMBRADA, xml_path, resultado_pac.uuid, now, item["id"]),
            )
            registrar_intento_pac(
                conn,
                factura,
                proveedor,
                ESTATUS_TIMBRADA,
                "CFDI timbrado correctamente por PAC real.",
                folio_candidato=cfdi_folio,
                uuid_val=resultado_pac.uuid,
                xml_path=xml_path,
                response={
                    "snapshot_path": snapshot_path,
                    "folio_sae": folio_sae,
                    "pac": resultado_pac.raw_response,
                },
            )
            return {
                "procesado": True,
                "factura": factura.get("factura"),
                "modo": proveedor,
                "serie": serie,
                "folio_cfdi": cfdi_folio,
                "folio_sae": folio_sae,
                "uuid": resultado_pac.uuid,
                "xml_path": xml_path,
            }
        if proveedor == "SIMULADO":
            uuid_cfdi = str(uuid.uuid4()).upper()
            output_dir = str(config.get("output_dir") or ruta_empresa_fiscal(item["empresa"]))
            anio = str(fecha_cfdi.year)
            xml_dir = os.path.join(output_dir, anio, "xml")
            os.makedirs(xml_dir, exist_ok=True)
            xml_path = os.path.join(xml_dir, f"{factura.get('factura')}-{serie}{cfdi_folio}.xml")
            with open(xml_path, "w", encoding="utf-8") as f:
                f.write(contenido)
            snapshot_path = _guardar_snapshot_json(config, factura)
            conn.execute(
                """
                INSERT INTO cfdi_emitidos (
                    factura_id, factura, empresa, cliente_receptor_numero, cliente_receptor_nombre,
                    serie, folio_cfdi, uuid, estatus_cfdi, xml_path, addenda_tipo, orden_compra, fecha_timbrado
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'TIMBRADA', ?, ?, ?, ?)
                """,
                (
                    factura["id"],
                    factura.get("factura"),
                    item["empresa"],
                    item.get("cliente_receptor_numero"),
                    item.get("cliente_receptor_nombre"),
                    serie,
                    cfdi_folio,
                    uuid_cfdi,
                    xml_path,
                    item.get("addenda_tipo"),
                    str(opciones_cfdi.get("orden_compra") or "").strip(),
                    now,
                ),
            )
            conn.execute(
                "UPDATE timbrado_queue SET estatus = ?, uuid = ?, xml_path = ?, snapshot_path = ?, ultimo_error = NULL WHERE id = ?",
                (ESTATUS_TIMBRADA, uuid_cfdi, xml_path, snapshot_path, item["id"]),
            )
            folio_sae = _guardar_folio_sae_legacy(conn_legacy, [factura["id"]], serie, cfdi_folio)
            _avanzar_folio_empresa(conn, item["empresa"], cfdi_folio)
            registrar_intento_pac(
                conn,
                factura,
                proveedor,
                ESTATUS_TIMBRADA,
                "Timbrado simulado generado correctamente.",
                folio_candidato=cfdi_folio,
                uuid_val=uuid_cfdi,
                xml_path=xml_path,
                response={"modo": "SIMULADO", "validacion_cfdi": validacion_cfdi},
            )
            return {
                "procesado": True,
                "factura": factura.get("factura"),
                "uuid": uuid_cfdi,
                "modo": "SIMULADO",
                "serie": serie,
                "folio_cfdi": cfdi_folio,
                "folio_serie": folio_sae,
                "validacion_cfdi": validacion_cfdi,
            }
        raise RuntimeError("PAC no configurado o proveedor todavia no integrado. Usa proveedor SIMULADO para pruebas.")
    except Exception as exc:
        try:
            registrar_intento_pac(
                conn,
                factura,
                (config or {}).get("proveedor"),
                ESTATUS_ERROR,
                str(exc),
                response={"factura": factura.get("factura")},
            )
        except Exception:
            pass
        conn.execute(
            "UPDATE timbrado_queue SET estatus = ?, ultimo_error = ? WHERE id = ?",
            (ESTATUS_ERROR, str(exc), item["id"]),
        )
        return {"procesado": False, "factura": factura.get("factura"), "error": str(exc)}


def _merge_facturas(facturas):
    if not facturas:
        return {}
    base = dict(facturas[0])
    subtotal = 0.0
    total = 0.0
    iva = 0.0
    productos = []
    for f in facturas:
        subtotal += float(f.get("subtotal") or 0)
        total += float(f.get("total") or 0)
        iva += float(f.get("iva") or 0)
        productos.extend(f.get("productos") or [])
    base["subtotal"] = round(subtotal, 2)
    base["total"] = round(total, 2)
    base["iva"] = round(iva, 2)
    base["productos"] = productos
    return base


def consolidar_facturas_timbrado(conn, conn_legacy, facturas_list):
    _asegurar_tablas_timbrado(conn)
    if len(facturas_list) < 2:
        return {"procesado": False, "detalle": "Se requieren al menos 2 facturas para consolidar"}
    placeholders = ",".join("?" * len(facturas_list))
    rows = conn.execute(
        f"SELECT * FROM timbrado_queue WHERE factura IN ({placeholders}) AND estatus = ?",
        tuple(facturas_list) + (ESTATUS_PENDIENTE,),
    ).fetchall()
    if len(rows) != len(facturas_list):
        return {"procesado": False, "detalle": "Algunas facturas no estan PENDIENTE en la cola"}
    items = [dict(r) for r in rows]
    empresa = items[0]["empresa"]
    cliente_num = items[0].get("cliente_receptor_numero") or items[0].get("numero_cliente")
    consignatario = items[0].get("cliente_origen_nombre") or ""
    for item in items:
        if item["empresa"] != empresa:
            return {"procesado": False, "detalle": "Todas las facturas deben ser de la misma empresa"}
        c = item.get("cliente_receptor_numero") or item.get("numero_cliente")
        if c != cliente_num:
            return {"procesado": False, "detalle": "Todas las facturas deben ser del mismo cliente"}
        if (item.get("cliente_origen_nombre") or "") != consignatario:
            return {"procesado": False, "detalle": "Todas las facturas deben tener el mismo consignatario"}
    facturas = [_snapshot_factura(conn_legacy, int(item["factura_id"])) for item in items]
    id_list = [item["id"] for item in items]
    factura_ids = [factura["id"] for factura in facturas]
    config = obtener_config_timbrado(conn, empresa)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    folio_nombre = f"CONSOLIDADO-{'-'.join(facturas_list)}"

    try:
        if not config or not config.get("timbrado_activo"):
            raise RuntimeError("La empresa no tiene timbrado activo.")
        proveedor = (config.get("proveedor") or "").strip().upper()
        if proveedor not in {"SIMULADO", "FINKOK"}:
            raise RuntimeError("PAC no configurado o proveedor no integrado para consolidacion.")

        # Un CFDI consolidado solamente puede llevar una misma configuracion fiscal.
        opciones = [_opciones_cfdi_desde_item(item) for item in items]
        opciones_cfdi = opciones[0] if opciones else {}
        campos_fiscales = ("uso_cfdi", "forma_pago", "metodo_pago", "exportacion", "moneda", "condiciones_pago")
        for idx, opcion in enumerate(opciones[1:], start=2):
            if any(str(opcion.get(campo) or "").strip() != str(opciones_cfdi.get(campo) or "").strip() for campo in campos_fiscales):
                raise RuntimeError(f"La factura {facturas_list[idx - 1]} tiene datos fiscales distintos; no se puede consolidar.")

        # City Fresko requiere un solo folio/fecha de recibo para toda la addenda.
        addendas = [renderizar_addenda_factura(conn, conn_legacy, factura) for factura in facturas]
        for factura, addenda in zip(facturas, addendas):
            faltantes = (addenda.get("preview") or {}).get("placeholders_faltantes") or []
            if faltantes:
                raise RuntimeError(f"La factura {factura.get('factura')} tiene datos de addenda pendientes: {', '.join(faltantes)}.")
        if any(item.get("addenda_tipo") == "CF000NUEVA" for item in items):
            referencias = {
                (
                    str((addenda.get("preview") or {}).get("configuracion_guardada", {}).get("CONDICION") or "").strip(),
                    str((addenda.get("preview") or {}).get("configuracion_guardada", {}).get("ENVIARADIRECCION") or "").strip(),
                )
                for addenda in addendas
            }
            if len(referencias) != 1:
                raise RuntimeError("Las facturas City Fresko deben tener el mismo folio y fecha de recibo para consolidarse.")

        placeholders_ids = ",".join("?" * len(factura_ids))
        ya_emitidas = conn.execute(
            f"SELECT factura FROM cfdi_emitidos WHERE factura_id IN ({placeholders_ids}) AND COALESCE(estatus_cfdi, '') NOT IN ('CANCELADA', 'CANCELADO') LIMIT 1",
            tuple(factura_ids),
        ).fetchone()
        if ya_emitidas:
            raise RuntimeError(f"Una factura seleccionada ya tiene CFDI emitido: {dict(ya_emitidas).get('factura') or ''}.")

        if proveedor != "SIMULADO":
            preflight = validar_preflight_pac(config)
            if not preflight.get("ok"):
                raise RuntimeError("Preflight PAC fallido: " + "; ".join(preflight.get("errores") or []))

        merged = _merge_facturas(facturas)
        merged["factura"] = folio_nombre
        fecha_cfdi = _ahora_cfdi_mexico() if (proveedor != "SIMULADO" or opciones_cfdi.get("usar_fecha_actual")) else _fecha_referencia_cfdi(merged)
        resolucion = resolver_receptor_timbrado(conn, conn_legacy, merged, incluir_preview=False)
        validacion_cfdi = validar_pre_cfdi_factura(merged, config, resolucion=resolucion, opciones_cfdi=opciones_cfdi)
        if proveedor != "SIMULADO" and not validacion_cfdi.get("ok"):
            mensajes = "; ".join(x.get("mensaje", "") for x in validacion_cfdi.get("faltantes") or [])
            raise RuntimeError(f"Validacion pre-PAC fallida: {mensajes}")

        # Reclamar todos los renglones antes de enviar al PAC evita timbrar una parte de la seleccion.
        cur_claim = conn.execute(
            f"UPDATE timbrado_queue SET estatus = ?, last_attempt_at = ?, intento_count = intento_count + 1 WHERE id IN ({','.join('?' * len(id_list))}) AND estatus = ?",
            (ESTATUS_TIMBRANDO, now) + tuple(id_list) + (ESTATUS_PENDIENTE,),
        )
        if getattr(cur_claim, "rowcount", 0) != len(id_list):
            raise RuntimeError("Alguna factura dejo de estar PENDIENTE; actualiza la cola antes de consolidar.")

        serie = str(config.get("serie_cfdi") or "").strip()
        cfdi_folio = _obtener_siguiente_folio(conn, empresa)
        merged["folio_cfdi"] = cfdi_folio
        addenda_render = renderizar_addenda_factura(conn, conn_legacy, merged)
        # La addenda se prepara con la fecha comercial original; sólo el
        # comprobante fiscal lleva la fecha y hora actuales del timbrado.
        merged["fecha_cfdi"] = fecha_cfdi
        item_cfdi = dict(items[0])
        item_cfdi["cfdi_opciones_json"] = json.dumps(opciones_cfdi, ensure_ascii=False)
        contenido = _generar_cfdi_simulado_xml(merged, config, addenda_render, item_cfdi, cfdi_folio, serie)
        output_dir = str(config.get("output_dir") or ruta_empresa_fiscal(empresa))
        anio = str(merged["fecha_cfdi"].year)
        xml_dir = os.path.join(output_dir, anio, "xml")
        os.makedirs(xml_dir, exist_ok=True)
        xml_path = os.path.join(xml_dir, f"{folio_nombre}-{serie}{cfdi_folio}.xml")

        if proveedor != "SIMULADO":
            sellado = sellar_xml_cfdi(contenido, config)
            contenido_pac = sellado.get("xml") if sellado.get("ok") else contenido
            try:
                resultado_pac = timbrar_xml_pac(proveedor, config, contenido_pac)
            except (PacNoIntegradoError, PacTimbradoError) as exc:
                prexml_path = _guardar_prexml_pac(config, merged, contenido_pac)
                mensaje = f"{exc} XML pre-PAC de consolidacion generado sin consumir folio."
                conn.execute(
                    f"UPDATE timbrado_queue SET estatus = ?, xml_path = ?, ultimo_error = ? WHERE id IN ({','.join('?' * len(id_list))})",
                    (ESTATUS_BLOQUEADO_PAC, prexml_path, mensaje) + tuple(id_list),
                )
                registrar_intento_pac(conn, merged, proveedor, ESTATUS_BLOQUEADO_PAC, mensaje, folio_candidato=cfdi_folio, xml_path=prexml_path)
                return {"procesado": False, "facturas": facturas_list, "bloqueo_pac": True, "detalle": mensaje, "prexml_path": prexml_path}
            with open(xml_path, "w", encoding="utf-8") as f:
                f.write(resultado_pac.xml_timbrado)
            uuid_cfdi = resultado_pac.uuid
            respuesta_pac = resultado_pac.raw_response
        else:
            with open(xml_path, "w", encoding="utf-8") as f:
                f.write(contenido)
            uuid_cfdi = str(uuid.uuid4()).upper()
            respuesta_pac = {"modo": "SIMULADO"}

        snapshot_path = _guardar_snapshot_json(config, merged)
        cursor_cfdi = conn.execute(
            """
            INSERT INTO cfdi_emitidos (
                factura_id, factura, empresa, cliente_receptor_numero, cliente_receptor_nombre,
                serie, folio_cfdi, uuid, estatus_cfdi, xml_path, addenda_tipo, orden_compra, fecha_timbrado
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'TIMBRADA', ?, ?, ?, ?)
            """,
            (facturas[0]["id"], folio_nombre, empresa, items[0].get("cliente_receptor_numero"),
             items[0].get("cliente_receptor_nombre"), serie, cfdi_folio, uuid_cfdi, xml_path,
             items[0].get("addenda_tipo"), str(opciones_cfdi.get("orden_compra") or "").strip(), now),
        )
        cfdi_emitido_id = getattr(cursor_cfdi, "lastrowid", None)
        if not cfdi_emitido_id:
            row_cfdi = conn.execute(
                "SELECT id FROM cfdi_emitidos WHERE uuid = ? ORDER BY id DESC LIMIT 1",
                (uuid_cfdi,),
            ).fetchone()
            cfdi_emitido_id = (dict(row_cfdi).get("id") if row_cfdi else None)
        if not cfdi_emitido_id:
            raise RuntimeError("No se pudo identificar el CFDI consolidado recien emitido.")
        for factura_consolidada in facturas:
            conn.execute(
                """
                INSERT INTO cfdi_consolidacion_facturas (cfdi_emitido_id, factura_id, factura)
                VALUES (?, ?, ?)
                """,
                (cfdi_emitido_id, factura_consolidada["id"], factura_consolidada["factura"]),
            )
        conn.execute(
            f"UPDATE timbrado_queue SET estatus = ?, uuid = ?, xml_path = ?, snapshot_path = ?, ultimo_error = NULL WHERE id IN ({','.join('?' * len(id_list))})",
            (ESTATUS_TIMBRADA, uuid_cfdi, xml_path, snapshot_path) + tuple(id_list),
        )
        folio_sae = _guardar_folio_sae_legacy(conn_legacy, factura_ids, serie, cfdi_folio)
        _avanzar_folio_empresa(conn, empresa, cfdi_folio)
        registrar_intento_pac(conn, merged, proveedor, ESTATUS_TIMBRADA, "CFDI consolidado timbrado correctamente.", folio_candidato=cfdi_folio, uuid_val=uuid_cfdi, xml_path=xml_path, response={"facturas": facturas_list, "pac": respuesta_pac, "validacion_cfdi": validacion_cfdi})
        return {"procesado": True, "facturas": facturas_list, "uuid": uuid_cfdi, "modo": f"CONSOLIDADO-{proveedor}", "xml_path": xml_path, "serie": serie, "folio_cfdi": cfdi_folio, "folio_serie": folio_sae}
    except Exception as exc:
        conn.execute(
            f"UPDATE timbrado_queue SET estatus = ?, ultimo_error = ? WHERE id IN ({','.join('?' * len(id_list))}) AND estatus = ?",
            (ESTATUS_ERROR, str(exc)) + tuple(id_list) + (ESTATUS_TIMBRANDO,),
        )
        return {"procesado": False, "facturas": facturas_list, "error": str(exc)}


def _insertar_addenda_en_xml_timbrado(xml_timbrado: str, addenda_xml: str) -> str:
    """Inserta la addenda en el XML timbrado después del timbrado exitoso.
    
    La addenda se inserta como elemento XML real dentro de <cfdi:Addenda>.
    Esto permite que Finkok timbre el XML sin problemas y luego se agregue
    la addenda comercial sin afectar el Timbre Fiscal Digital.
    """
    try:
        from lxml import etree
    except ImportError:
        etree = None
    
    if etree is None:
        # Fallback: insertar addenda como texto si lxml no está disponible
        if "</cfdi:Comprobante>" in xml_timbrado:
            addenda_completa = f"<cfdi:Addenda>{addenda_xml}</cfdi:Addenda>"
            return xml_timbrado.replace("</cfdi:Comprobante>", f"{addenda_completa}</cfdi:Comprobante>")
        return xml_timbrado
    
    try:
        root = etree.fromstring(xml_timbrado.encode("utf-8"))
        ns_cfdi = "http://www.sat.gob.mx/cfd/4"
        
        # Buscar o crear el nodo Addenda
        addenda = root.find(f"{{{ns_cfdi}}}Addenda")
        if addenda is None:
            # Crear el nodo Addenda
            addenda = etree.SubElement(root, f"{{{ns_cfdi}}}Addenda")
        else:
            # Limpiar el contenido existente
            addenda.clear()
        
        # Parsear la addenda y agregarla como elementos XML reales
        if addenda_xml.strip().startswith("<"):
            try:
                # Envolver en un elemento temporal para parsear
                temp_xml = f"<root xmlns:cfdi='{ns_cfdi}'>{addenda_xml}</root>"
                temp_root = etree.fromstring(temp_xml.encode("utf-8"))
                for child in temp_root:
                    addenda.append(child)
            except Exception:
                # Si falla el parseo, agregar como texto
                addenda.text = addenda_xml
        
        # Serializar el XML con la addenda insertada
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True).decode("utf-8")
    except Exception:
        # Si falla el parseo completo, devolver el XML original
        return xml_timbrado


def _guardar_snapshot_json(config, factura):
    empresa = _normalizar_empresa(factura.get("empresa"))
    output_dir = str(config.get("output_dir") or ruta_empresa_fiscal(empresa))
    try:
        anio = str(datetime.fromisoformat(str(factura.get("fecha") or "")).year)
    except Exception:
        anio = str(datetime.now().year)
    base_dir = os.path.join(output_dir, anio, "snapshots")
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, f"{factura.get('factura')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(factura, f, ensure_ascii=False, indent=2, default=str)
    return path


def _guardar_prexml_pac(config, factura, contenido):
    empresa = _normalizar_empresa(factura.get("empresa"))
    output_dir = str(config.get("output_dir") or ruta_empresa_fiscal(empresa))
    fecha = factura.get("fecha_cfdi") or _fecha_referencia_cfdi(factura)
    anio = str(fecha.year)
    base_dir = os.path.join(output_dir, anio, "prepac")
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, f"{factura.get('factura')}-prepac.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(contenido)
    return path

