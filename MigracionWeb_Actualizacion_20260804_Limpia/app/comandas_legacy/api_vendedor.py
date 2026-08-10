# api_vendedor.py
from __future__ import annotations

import io
import os
import re
import time
import bcrypt
import secrets
import unicodedata
import base64
import hashlib
import hmac
import mimetypes
import importlib.util
import urllib.parse
import urllib.request
import urllib.error
from urllib.parse import urlparse, unquote
from datetime import datetime
from io import BytesIO
from typing import Optional, Dict, Any, List
import json
import sys

import mysql.connector
from fastapi import FastAPI, HTTPException, Depends, Query, UploadFile, File as FastAPIFile, Request, Form
from fastapi.responses import Response, FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, conint, confloat, constr, field_validator
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

try:
    from PIL import Image
except Exception:
    Image = None


# =========================
# Config / DB
# =========================
DEFAULT_MYSQL_HOSTS = ("192.168.1.146", "100.69.142.19", "192.168.1.105", "127.0.0.1", "localhost")
MYSQL_CONNECTION_TIMEOUT = 5
_MYSQL_HOST_CACHE: dict[str, str] = {}


def _runtime_base_dir() -> str:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return meipass
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _runtime_config_paths() -> list[str]:
    paths = [os.path.join(_runtime_base_dir(), "config.json")]
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(os.path.join(appdata, "comandas", "config.json"))
    return paths


def _load_runtime_config() -> dict[str, Any]:
    config: dict[str, Any] = {}
    for path in _runtime_config_paths():
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                config.update(loaded)
        except Exception as e:
            print(f"[WARN] No se pudo leer configuracion desde {path}: {e}", flush=True)
    return config


def _web_root_dir() -> str:
    return os.path.join(_runtime_base_dir(), "web")


def _deploy_root_dir() -> str:
    return os.path.join(_runtime_base_dir(), "deploy")


def _config_bool(key: str, default: bool = False) -> bool:
    value = _load_runtime_config().get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "si", "sÃ­", "yes", "on"}


def _jwt_secret_key() -> str:
    config = _load_runtime_config()
    secret = (
        os.environ.get("COMANDAS_JWT_SECRET_KEY")
        or str(config.get("jwt_secret_key") or "").strip()
    )
    return secret or "CAMBIAR_ESTA_CLAVE"


def _jwt_exp_hours() -> int:
    config = _load_runtime_config()
    try:
        return int(config.get("jwt_exp_hours") or 8)
    except Exception:
        return 8


def _prealtas_docs_base_dir(config: Optional[dict[str, Any]] = None) -> str:
    config = config or _load_runtime_config()
    custom = str(config.get("prealtas_docs_root") or "").strip()
    if custom:
        return custom
    return r"\\SERVER_GALACTIC\Proyectos\uploads_clientes_prealta"


def _iter_mysql_hosts(config: Optional[dict[str, Any]] = None):
    config = config or _load_runtime_config()
    mysql_cfg = config.get("mysql") if isinstance(config.get("mysql"), dict) else {}
    candidates = [
        os.environ.get("COMANDAS_MYSQL_HOST"),
        config.get("mysql_host"),
        config.get("db_host"),
        mysql_cfg.get("host"),
    ]
    seen = set()
    for host in candidates + list(DEFAULT_MYSQL_HOSTS):
        host = str(host or "").strip()
        if not host or host in seen:
            continue
        seen.add(host)
        yield host


def _build_mysql_kwargs(database_name: str, host: str) -> dict[str, Any]:
    config = _load_runtime_config()
    mysql_cfg = config.get("mysql") if isinstance(config.get("mysql"), dict) else {}
    return {
        "host": host,
        "user": os.environ.get("COMANDAS_MYSQL_USER")
        or mysql_cfg.get("user")
        or config.get("mysql_user")
        or "Facturacion",
        "password": os.environ.get("COMANDAS_MYSQL_PASSWORD")
        or mysql_cfg.get("password")
        or config.get("mysql_password")
        or "ALD2013*",
        "database": database_name,
        "port": int(
            os.environ.get("COMANDAS_MYSQL_PORT")
            or mysql_cfg.get("port")
            or config.get("mysql_port")
            or 3307
        ),
        "use_pure": True,
        "connection_timeout": MYSQL_CONNECTION_TIMEOUT,
    }


def _connect_mysql_database(database_name: str):
    last_error = None
    ordered_hosts: list[str] = []
    cached_host = _MYSQL_HOST_CACHE.get(database_name)
    if cached_host:
        ordered_hosts.append(cached_host)
    for host in _iter_mysql_hosts():
        if host not in ordered_hosts:
            ordered_hosts.append(host)

    for host in ordered_hosts:
        try:
            conn = mysql.connector.connect(**_build_mysql_kwargs(database_name, host))
            _MYSQL_HOST_CACHE[database_name] = host
            return conn
        except mysql.connector.Error as e:
            last_error = e
            print(f"[MYSQL] {database_name} no respondio en {host}: {e}", flush=True)

    if last_error:
        print(f"[MYSQL] Error final al conectar a {database_name}: {last_error}", flush=True)
    return None


def _inventory_gateway_secret() -> str:
    """Secreto compartido para el respaldo REST de InventarioApp.

    Puede independizarse de MySQL con INVENTARIO_GATEWAY_SECRET o con
    inventory_gateway_secret en config.json. Mientras no se configure, usa la
    contraseña MySQL que ya comparten el API y la aplicación de inventario.
    """
    config = _load_runtime_config()
    mysql_cfg = config.get("mysql") if isinstance(config.get("mysql"), dict) else {}
    return str(
        os.environ.get("INVENTARIO_GATEWAY_SECRET")
        or config.get("inventory_gateway_secret")
        or os.environ.get("COMANDAS_MYSQL_PASSWORD")
        or mysql_cfg.get("password")
        or config.get("mysql_password")
        or "ALD2013*"
    )

def conectar_mysql_editor():
    return _connect_mysql_database("comandas_editor_db")

def conectar_mysql():
    return _connect_mysql_database("comandas_db")




def _safe_path_component(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return value or "sin_valor"


def _normalizar_numero_cliente(valor: Any) -> Optional[int]:
    try:
        s = str(valor or "").replace(",", "").strip()
        if not s:
            return None
        if s.endswith(".0"):
            s = s[:-2]
        return int(float(s))
    except Exception:
        return None


def asegurar_tabla_clientes_prealta():
    conn = conectar_mysql()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS clientes_prealta_vendedor (
                id INT AUTO_INCREMENT PRIMARY KEY,
                empresa VARCHAR(255) NOT NULL,
                nombre VARCHAR(255) NOT NULL,
                razon_social VARCHAR(255) DEFAULT '',
                calle VARCHAR(255) DEFAULT '',
                no_exterior VARCHAR(50) DEFAULT '',
                no_interior VARCHAR(50) DEFAULT '',
                colonia VARCHAR(255) DEFAULT '',
                alcaldia VARCHAR(255) DEFAULT '',
                municipio VARCHAR(255) DEFAULT '',
                codigo_postal VARCHAR(30) DEFAULT '',
                poblacion VARCHAR(255) DEFAULT '',
                estado VARCHAR(255) DEFAULT '',
                pais VARCHAR(255) DEFAULT '',
                rfc VARCHAR(80) DEFAULT '',
                telefono VARCHAR(80) DEFAULT '',
                correo_electronico VARCHAR(255) DEFAULT '',
                contacto1 VARCHAR(255) DEFAULT '',
                contacto2 VARCHAR(255) DEFAULT '',
                dias_credito INT NOT NULL DEFAULT 0,
                consignatario VARCHAR(255) DEFAULT '',
                consig_calle VARCHAR(255) DEFAULT '',
                consig_no_exterior VARCHAR(50) DEFAULT '',
                consig_no_interior VARCHAR(50) DEFAULT '',
                consig_colonia VARCHAR(255) DEFAULT '',
                consig_delegacion VARCHAR(255) DEFAULT '',
                consig_municipio VARCHAR(255) DEFAULT '',
                consig_codigo_postal VARCHAR(30) DEFAULT '',
                consig_poblacion VARCHAR(255) DEFAULT '',
                consig_estado VARCHAR(255) DEFAULT '',
                consig_pais VARCHAR(255) DEFAULT '',
                zona VARCHAR(255) DEFAULT '',
                no_proveedor VARCHAR(255) DEFAULT '',
                agente VARCHAR(255) DEFAULT '',
                descuento DECIMAL(10,2) NOT NULL DEFAULT 0,
                especial VARCHAR(255) DEFAULT '',
                tipo VARCHAR(255) DEFAULT '',
                vendedor VARCHAR(255) DEFAULT '',
                numero_cliente_sugerido VARCHAR(50) DEFAULT NULL,
                direccion_entrega TEXT DEFAULT NULL,
                observaciones TEXT DEFAULT NULL,
                horarios_pago_desde VARCHAR(20) DEFAULT '',
                horarios_pago_hasta VARCHAR(20) DEFAULT '',
                dia_pago VARCHAR(50) DEFAULT '',
                forma_pago VARCHAR(120) DEFAULT '',
                horarios_revision_desde VARCHAR(20) DEFAULT '',
                horarios_revision_hasta VARCHAR(20) DEFAULT '',
                dia_revision VARCHAR(50) DEFAULT '',
                compras_nombre VARCHAR(255) DEFAULT '',
                compras_telefono VARCHAR(80) DEFAULT '',
                recibo_nombre VARCHAR(255) DEFAULT '',
                recibo_telefono VARCHAR(80) DEFAULT '',
                gerente_nombre VARCHAR(255) DEFAULT '',
                gerente_telefono VARCHAR(80) DEFAULT '',
                observaciones_visita TEXT DEFAULT NULL,
                pedido_realizado_visita TEXT DEFAULT NULL,
                estatus VARCHAR(40) NOT NULL DEFAULT 'PENDIENTE',
                usuario_alta VARCHAR(255) DEFAULT NULL,
                usuario_revision VARCHAR(255) DEFAULT NULL,
                fecha_revision DATETIME DEFAULT NULL,
                comentario_revision TEXT DEFAULT NULL,
                numero_cliente_final VARCHAR(50) DEFAULT NULL,
                empresa_cliente_final VARCHAR(255) DEFAULT NULL,
                fecha_alta DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS clientes_prealta_documentos (
                id INT AUTO_INCREMENT PRIMARY KEY,
                prealta_id INT NOT NULL,
                tipo_documento VARCHAR(80) NOT NULL,
                nombre_original VARCHAR(255) DEFAULT NULL,
                ruta_archivo TEXT NOT NULL,
                mime_type VARCHAR(120) DEFAULT NULL,
                tamano_bytes BIGINT NOT NULL DEFAULT 0,
                usuario_alta VARCHAR(255) DEFAULT NULL,
                fecha_alta DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY ux_prealta_documento (prealta_id, tipo_documento),
                CONSTRAINT fk_prealta_documento
                    FOREIGN KEY (prealta_id) REFERENCES clientes_prealta_vendedor(id)
                    ON DELETE CASCADE
            )
            """
        )
        alter_statements = [
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN tipo VARCHAR(255) DEFAULT ''",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN numero_cliente_sugerido VARCHAR(50) DEFAULT NULL",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN direccion_entrega TEXT DEFAULT NULL",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN observaciones TEXT DEFAULT NULL",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN estatus VARCHAR(40) NOT NULL DEFAULT 'PENDIENTE'",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN usuario_alta VARCHAR(255) DEFAULT NULL",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN usuario_revision VARCHAR(255) DEFAULT NULL",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN fecha_revision DATETIME DEFAULT NULL",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN comentario_revision TEXT DEFAULT NULL",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN numero_cliente_final VARCHAR(50) DEFAULT NULL",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN empresa_cliente_final VARCHAR(255) DEFAULT NULL",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN horarios_pago_desde VARCHAR(20) DEFAULT ''",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN horarios_pago_hasta VARCHAR(20) DEFAULT ''",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN dia_pago VARCHAR(50) DEFAULT ''",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN forma_pago VARCHAR(120) DEFAULT ''",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN horarios_revision_desde VARCHAR(20) DEFAULT ''",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN horarios_revision_hasta VARCHAR(20) DEFAULT ''",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN dia_revision VARCHAR(50) DEFAULT ''",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN compras_nombre VARCHAR(255) DEFAULT ''",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN compras_telefono VARCHAR(80) DEFAULT ''",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN recibo_nombre VARCHAR(255) DEFAULT ''",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN recibo_telefono VARCHAR(80) DEFAULT ''",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN gerente_nombre VARCHAR(255) DEFAULT ''",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN gerente_telefono VARCHAR(80) DEFAULT ''",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN observaciones_visita TEXT DEFAULT NULL",
            "ALTER TABLE clientes_prealta_vendedor ADD COLUMN pedido_realizado_visita TEXT DEFAULT NULL",
        ]
        for sql in alter_statements:
            try:
                cur.execute(sql)
            except mysql.connector.Error:
                pass
        conn.commit()
        return True
    except mysql.connector.Error as e:
        print(f"[CLIENTES] Error asegurando tablas de prealta: {e}", flush=True)
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def guardar_documento_cliente(
    prealta_id: int,
    empresa: str,
    tipo_documento: str,
    archivo: UploadFile,
    usuario_alta: str,
) -> dict[str, Any]:
    if archivo is None:
        return {}

    docs_root = _prealtas_docs_base_dir()
    base_dir = os.path.join(
        docs_root,
        _safe_path_component(empresa),
        f"prealta_{prealta_id}",
    )
    os.makedirs(base_dir, exist_ok=True)

    original_name = os.path.basename(archivo.filename or f"{tipo_documento}.bin")
    ext = os.path.splitext(original_name)[1][:12] or ".bin"
    saved_name = f"{tipo_documento}{ext}"
    target_path = os.path.join(base_dir, saved_name)

    content = archivo.file.read()
    with open(target_path, "wb") as f:
        f.write(content)

    print(
        f"[PREALTA_DOC] prealta={prealta_id} tipo={tipo_documento} "
        f"root={docs_root} base_dir={base_dir} target_path={target_path}",
        flush=True,
    )

    if not asegurar_tabla_clientes_prealta():
        raise HTTPException(status_code=500, detail="No se pudo preparar el almacenamiento de documentos")

    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar para guardar documentos")
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO clientes_prealta_documentos
                (prealta_id, tipo_documento, nombre_original, ruta_archivo,
                 mime_type, tamano_bytes, usuario_alta)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                nombre_original = VALUES(nombre_original),
                ruta_archivo = VALUES(ruta_archivo),
                mime_type = VALUES(mime_type),
                tamano_bytes = VALUES(tamano_bytes),
                usuario_alta = VALUES(usuario_alta)
            """,
            (
                prealta_id,
                tipo_documento,
                original_name,
                target_path,
                getattr(archivo, "content_type", None) or "application/octet-stream",
                len(content),
                usuario_alta,
            ),
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "tipo": tipo_documento,
        "nombre_original": original_name,
        "ruta_archivo": target_path,
        "tamano_bytes": len(content),
    }


def asegurar_tabla_productos_ficha():
    conn = conectar_mysql()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS productos_ficha_base (
                id INT AUTO_INCREMENT PRIMARY KEY,
                cip VARCHAR(255) NOT NULL,
                titulo_ficha VARCHAR(255) DEFAULT NULL,
                subtitulo VARCHAR(255) DEFAULT NULL,
                descripcion_corta TEXT DEFAULT NULL,
                tipo_producto VARCHAR(255) DEFAULT NULL,
                origen VARCHAR(255) DEFAULT NULL,
                maduracion VARCHAR(255) DEFAULT NULL,
                presentacion VARCHAR(255) DEFAULT NULL,
                peso_aprox VARCHAR(255) DEFAULT NULL,
                ingredientes TEXT DEFAULT NULL,
                conservacion TEXT DEFAULT NULL,
                texto_comercial TEXT DEFAULT NULL,
                nombre_producto VARCHAR(255) DEFAULT NULL,
                marca VARCHAR(255) DEFAULT NULL,
                categoria VARCHAR(255) DEFAULT NULL,
                contenido_neto VARCHAR(255) DEFAULT NULL,
                ean VARCHAR(255) DEFAULT NULL,
                observaciones_ficha TEXT DEFAULT NULL,
                badge_1 VARCHAR(80) DEFAULT NULL,
                badge_2 VARCHAR(80) DEFAULT NULL,
                badge_3 VARCHAR(80) DEFAULT NULL,
                etiquetas_retail TEXT DEFAULT NULL,
                activo TINYINT(1) NOT NULL DEFAULT 1,
                fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY ux_cip (cip)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS productos_ficha (
                id INT AUTO_INCREMENT PRIMARY KEY,
                empresa VARCHAR(255) NOT NULL,
                cip VARCHAR(255) NOT NULL,
                titulo_ficha VARCHAR(255) DEFAULT NULL,
                subtitulo VARCHAR(255) DEFAULT NULL,
                descripcion_corta TEXT DEFAULT NULL,
                tipo_producto VARCHAR(255) DEFAULT NULL,
                origen VARCHAR(255) DEFAULT NULL,
                maduracion VARCHAR(255) DEFAULT NULL,
                presentacion VARCHAR(255) DEFAULT NULL,
                peso_aprox VARCHAR(255) DEFAULT NULL,
                ingredientes TEXT DEFAULT NULL,
                conservacion TEXT DEFAULT NULL,
                texto_comercial TEXT DEFAULT NULL,
                imagen_path TEXT DEFAULT NULL,
                extension VARCHAR(10) DEFAULT NULL,
                nombre_producto VARCHAR(255) DEFAULT NULL,
                marca VARCHAR(255) DEFAULT NULL,
                categoria VARCHAR(255) DEFAULT NULL,
                contenido_neto VARCHAR(255) DEFAULT NULL,
                ean VARCHAR(255) DEFAULT NULL,
                observaciones_ficha TEXT DEFAULT NULL,
                activo TINYINT(1) NOT NULL DEFAULT 1,
                fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY ux_empresa_cip (empresa, cip)
            )
            """
        )
        cur.execute("SHOW COLUMNS FROM productos_ficha")
        existentes = {row[0] for row in (cur.fetchall() or [])}
        columnas = {
            'extension': 'ALTER TABLE productos_ficha ADD COLUMN extension VARCHAR(10) DEFAULT NULL',
            'nombre_producto': 'ALTER TABLE productos_ficha ADD COLUMN nombre_producto VARCHAR(255) DEFAULT NULL',
            'marca': 'ALTER TABLE productos_ficha ADD COLUMN marca VARCHAR(255) DEFAULT NULL',
            'categoria': 'ALTER TABLE productos_ficha ADD COLUMN categoria VARCHAR(255) DEFAULT NULL',
            'contenido_neto': 'ALTER TABLE productos_ficha ADD COLUMN contenido_neto VARCHAR(255) DEFAULT NULL',
            'ean': 'ALTER TABLE productos_ficha ADD COLUMN ean VARCHAR(255) DEFAULT NULL',
            'observaciones_ficha': 'ALTER TABLE productos_ficha ADD COLUMN observaciones_ficha TEXT DEFAULT NULL',
            'titulo_ficha': 'ALTER TABLE productos_ficha ADD COLUMN titulo_ficha VARCHAR(255) DEFAULT NULL',
            'subtitulo': 'ALTER TABLE productos_ficha ADD COLUMN subtitulo VARCHAR(255) DEFAULT NULL',
            'descripcion_corta': 'ALTER TABLE productos_ficha ADD COLUMN descripcion_corta TEXT DEFAULT NULL',
            'tipo_producto': 'ALTER TABLE productos_ficha ADD COLUMN tipo_producto VARCHAR(255) DEFAULT NULL',
            'origen': 'ALTER TABLE productos_ficha ADD COLUMN origen VARCHAR(255) DEFAULT NULL',
            'maduracion': 'ALTER TABLE productos_ficha ADD COLUMN maduracion VARCHAR(255) DEFAULT NULL',
            'presentacion': 'ALTER TABLE productos_ficha ADD COLUMN presentacion VARCHAR(255) DEFAULT NULL',
            'peso_aprox': 'ALTER TABLE productos_ficha ADD COLUMN peso_aprox VARCHAR(255) DEFAULT NULL',
            'ingredientes': 'ALTER TABLE productos_ficha ADD COLUMN ingredientes TEXT DEFAULT NULL',
            'conservacion': 'ALTER TABLE productos_ficha ADD COLUMN conservacion TEXT DEFAULT NULL',
            'texto_comercial': 'ALTER TABLE productos_ficha ADD COLUMN texto_comercial TEXT DEFAULT NULL',
            'imagen_path': 'ALTER TABLE productos_ficha ADD COLUMN imagen_path TEXT DEFAULT NULL',
            'badge_1': 'ALTER TABLE productos_ficha ADD COLUMN badge_1 VARCHAR(80) DEFAULT NULL',
            'badge_2': 'ALTER TABLE productos_ficha ADD COLUMN badge_2 VARCHAR(80) DEFAULT NULL',
            'badge_3': 'ALTER TABLE productos_ficha ADD COLUMN badge_3 VARCHAR(80) DEFAULT NULL',
            'etiquetas_retail': 'ALTER TABLE productos_ficha ADD COLUMN etiquetas_retail TEXT DEFAULT NULL',
            'premium_sort': 'ALTER TABLE productos_ficha ADD COLUMN premium_sort INT NOT NULL DEFAULT 0',
            'premium_activo': 'ALTER TABLE productos_ficha ADD COLUMN premium_activo TINYINT(1) NOT NULL DEFAULT 1',
            'activo': 'ALTER TABLE productos_ficha ADD COLUMN activo TINYINT(1) NOT NULL DEFAULT 1',
            'fecha_actualizacion': 'ALTER TABLE productos_ficha ADD COLUMN fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP',
        }
        for col, sql in columnas.items():
            if col not in existentes:
                cur.execute(sql)

        cur.execute(
            """
            INSERT INTO productos_ficha_base (
                cip, titulo_ficha, subtitulo, descripcion_corta, tipo_producto,
                origen, maduracion, presentacion, peso_aprox, ingredientes,
                conservacion, texto_comercial, nombre_producto, marca, categoria,
                contenido_neto, ean, observaciones_ficha, badge_1, badge_2,
                badge_3, etiquetas_retail, activo, fecha_actualizacion
            )
            SELECT
                pf.cip,
                pf.titulo_ficha,
                pf.subtitulo,
                pf.descripcion_corta,
                pf.tipo_producto,
                pf.origen,
                pf.maduracion,
                pf.presentacion,
                pf.peso_aprox,
                pf.ingredientes,
                pf.conservacion,
                pf.texto_comercial,
                pf.nombre_producto,
                pf.marca,
                pf.categoria,
                pf.contenido_neto,
                pf.ean,
                pf.observaciones_ficha,
                pf.badge_1,
                pf.badge_2,
                pf.badge_3,
                pf.etiquetas_retail,
                1,
                pf.fecha_actualizacion
            FROM productos_ficha pf
            LEFT JOIN productos_ficha_base pfb
              ON pfb.cip = pf.cip
            WHERE pfb.id IS NULL
            """
        )

        cur.execute("SHOW TABLES LIKE 'productos_fichas'")
        if cur.fetchone():
            cur.execute(
                """
                INSERT INTO productos_ficha (
                    empresa, cip, extension, nombre_producto, marca, categoria,
                    contenido_neto, presentacion, ingredientes, conservacion,
                    origen, ean, descripcion_corta, observaciones_ficha,
                    titulo_ficha, subtitulo, tipo_producto, peso_aprox,
                    texto_comercial, activo, fecha_actualizacion
                )
                SELECT
                    pf.empresa,
                    pf.cip,
                    pf.extension,
                    pf.nombre_producto,
                    pf.marca,
                    pf.categoria,
                    pf.contenido_neto,
                    pf.presentacion,
                    pf.ingredientes,
                    pf.conservacion,
                    pf.origen,
                    pf.ean,
                    pf.descripcion_corta,
                    pf.observaciones_ficha,
                    COALESCE(NULLIF(pf.nombre_producto, ''), pf.descripcion_corta, pf.cip),
                    pf.marca,
                    pf.categoria,
                    pf.contenido_neto,
                    pf.observaciones_ficha,
                    1,
                    pf.fecha_actualizacion
                FROM productos_fichas pf
                LEFT JOIN productos_ficha pn
                  ON pn.empresa = pf.empresa AND pn.cip = pf.cip
                WHERE pn.id IS NULL
                """
            )
        conn.commit()
        return True
    except mysql.connector.Error as e:
        print(f"[FICHAS] Error asegurando productos_ficha: {e}", flush=True)
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def asegurar_tablas_visitas_clientes():
    conn = conectar_mysql()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS clientes_visitas (
                id INT AUTO_INCREMENT PRIMARY KEY,
                empresa VARCHAR(255) NOT NULL,
                cliente_numero VARCHAR(255) NOT NULL,
                cliente_nombre VARCHAR(255) DEFAULT '',
                direccion VARCHAR(500) DEFAULT '',
                telefono VARCHAR(80) DEFAULT '',
                horarios_pago_desde VARCHAR(10) DEFAULT '',
                horarios_pago_hasta VARCHAR(10) DEFAULT '',
                dia_pago VARCHAR(40) DEFAULT '',
                forma_pago VARCHAR(120) DEFAULT '',
                horarios_revision_desde VARCHAR(10) DEFAULT '',
                horarios_revision_hasta VARCHAR(10) DEFAULT '',
                dia_revision VARCHAR(40) DEFAULT '',
                compras_nombre VARCHAR(255) DEFAULT '',
                compras_telefono VARCHAR(80) DEFAULT '',
                recibo_nombre VARCHAR(255) DEFAULT '',
                recibo_telefono VARCHAR(80) DEFAULT '',
                gerente_nombre VARCHAR(255) DEFAULT '',
                gerente_telefono VARCHAR(80) DEFAULT '',
                observaciones_visita TEXT DEFAULT NULL,
                pedido_realizado_visita TEXT DEFAULT NULL,
                creado_por VARCHAR(80) DEFAULT '',
                actualizado_por VARCHAR(80) DEFAULT '',
                fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY ux_visita_cliente (empresa, cliente_numero)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS clientes_visitas_historial (
                id INT AUTO_INCREMENT PRIMARY KEY,
                empresa VARCHAR(255) NOT NULL,
                cliente_numero VARCHAR(255) NOT NULL,
                campo VARCHAR(80) NOT NULL,
                valor_anterior TEXT DEFAULT NULL,
                valor_nuevo TEXT DEFAULT NULL,
                cambiado_por VARCHAR(80) DEFAULT '',
                fecha_cambio DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_visitas_historial_cliente (empresa, cliente_numero, fecha_cambio)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS clientes_solicitudes_modificacion (
                id INT AUTO_INCREMENT PRIMARY KEY,
                empresa VARCHAR(255) NOT NULL,
                cliente_numero VARCHAR(255) NOT NULL,
                cliente_nombre VARCHAR(255) DEFAULT '',
                solicitud_texto TEXT NOT NULL,
                solicitado_por VARCHAR(255) DEFAULT '',
                fecha_solicitud DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                estado VARCHAR(30) NOT NULL DEFAULT 'PENDIENTE',
                resuelto_por VARCHAR(255) DEFAULT '',
                fecha_resolucion DATETIME DEFAULT NULL
            )
            """
        )
        for sql in [
            "CREATE INDEX idx_solicitudes_cliente_estado ON clientes_solicitudes_modificacion (empresa, cliente_numero, estado)",
            "CREATE INDEX idx_solicitudes_fecha ON clientes_solicitudes_modificacion (fecha_solicitud, id)",
        ]:
            try:
                cur.execute(sql)
            except mysql.connector.Error:
                pass
        conn.commit()
        return True
    except Exception as e:
        print(f"[VISITAS] No se pudo asegurar tablas de visitas: {e}", flush=True)
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


# =========================
# Auth (token simple)
# =========================
security = HTTPBearer()

# token -> {"user": {...}, "exp": epoch}
TOKENS: Dict[str, Dict[str, Any]] = {}
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 365  # 1 aÃ±o
OTP_CODES: Dict[str, Dict[str, Any]] = {}
OTP_REQUESTS: Dict[str, Dict[str, Any]] = {}
OTP_TTL_SECONDS = 60 * 15


def autenticar_usuario(usuario: str, password: str) -> Optional[dict] | str:
    conn = conectar_mysql()
    if not conn:
        print("[AUTH] No se pudo conectar a MySQL para login", flush=True)
        return None

    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, usuario, password, rol, activo
            FROM usuarios
            WHERE TRIM(usuario)=%s
            LIMIT 1
            """,
            (usuario.strip(),),
        )
        row = cur.fetchone()
        cur.close()

        if not row:
            print(f"[AUTH] Usuario no encontrado: {usuario!r}", flush=True)
            return None

        if int(row.get("activo", 1)) != 1:
            print(f"[AUTH] Usuario inactivo: {usuario!r}", flush=True)
            return "INACTIVO"

        hash_db = row.get("password")
        if not hash_db:
            print(f"[AUTH] Usuario sin password en DB: {usuario!r}", flush=True)
            return None

        if isinstance(hash_db, str):
            stored = hash_db.encode("utf-8")
        else:
            stored = bytes(hash_db)

        try:
            if not bcrypt.checkpw((password or "").encode("utf-8"), stored):
                stored_text = hash_db if isinstance(hash_db, str) else stored.decode("utf-8", errors="ignore")
                if (password or "") != stored_text:
                    print(f"[AUTH] Password invÃ¡lido para usuario: {usuario!r}", flush=True)
                    return None
        except Exception as e:
            stored_text = hash_db if isinstance(hash_db, str) else stored.decode("utf-8", errors="ignore")
            if (password or "") != stored_text:
                print(f"[AUTH] Error validando password de {usuario!r}: {type(e).__name__}: {e}", flush=True)
                return None

        return {
            "id": row["id"],
            "usuario": row["usuario"],
            "rol": (row["rol"] or "").lower(),
        }
    except Exception as e:
        print(f"[AUTH] Error interno autenticando {usuario!r}: {type(e).__name__}: {e}", flush=True)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def crear_token(user: dict) -> str:
    token = secrets.token_urlsafe(32)
    TOKENS[token] = {"user": user, "exp": int(time.time()) + TOKEN_TTL_SECONDS}
    return token


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    token = creds.credentials
    data = TOKENS.get(token)
    if not data:
        raise HTTPException(status_code=401, detail="Token invÃ¡lido")

    if int(time.time()) > int(data["exp"]):
        TOKENS.pop(token, None)
        raise HTTPException(status_code=401, detail="Token expirado")

    return data["user"]


def _automation_internal_key() -> str:
    config = _load_runtime_config()
    return str(
        os.environ.get("COMANDAS_AUTOMATION_INTERNAL_KEY")
        or config.get("automation_internal_key")
        or _inventory_gateway_secret()
    )


def require_automation_internal_key(request: Request) -> None:
    expected = _automation_internal_key()
    provided = str(request.headers.get("X-Internal-Key") or "")
    if not expected or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Clave interna invalida")


def cleanup_expired_otp_requests() -> None:
    now = int(time.time())
    expired_codes = [
        request_id for request_id, payload in OTP_CODES.items()
        if int(payload.get("expires_at") or 0) <= now
    ]
    for request_id in expired_codes:
        OTP_CODES.pop(request_id, None)

    expired_requests = [
        request_id for request_id, payload in OTP_REQUESTS.items()
        if int(payload.get("expires_at") or 0) <= now or bool(payload.get("consumed"))
    ]
    for request_id in expired_requests:
        OTP_REQUESTS.pop(request_id, None)

    conn = conectar_mysql()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE automatizacion_solicitudes
            SET estado='EXPIRADO'
            WHERE estado='PENDIENTE' AND expires_at_epoch <= %s
            """,
            (now,),
        )
        conn.commit()
        cur.close()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def asegurar_tabla_automatizacion_movimientos() -> bool:
    conn = conectar_mysql()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS automatizacion_movimientos (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                request_id VARCHAR(120) NULL,
                site_name VARCHAR(150) NULL,
                usuario_id INT NULL,
                usuario VARCHAR(100) NULL,
                rol VARCHAR(50) NULL,
                accion VARCHAR(80) NOT NULL,
                detalle TEXT NULL,
                ip VARCHAR(50) NULL,
                user_agent TEXT NULL,
                fecha DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_auto_req (request_id),
                INDEX idx_auto_usuario (usuario),
                INDEX idx_auto_accion (accion),
                INDEX idx_auto_fecha (fecha)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS automatizacion_solicitudes (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                request_id VARCHAR(120) NOT NULL UNIQUE,
                site_name VARCHAR(150) NOT NULL,
                tipo VARCHAR(50) NOT NULL DEFAULT 'OTP',
                estado VARCHAR(50) NOT NULL DEFAULT 'PENDIENTE',
                detalle TEXT NULL,
                created_at_epoch BIGINT NOT NULL,
                expires_at_epoch BIGINT NOT NULL,
                atendido_por_usuario_id INT NULL,
                atendido_por VARCHAR(100) NULL,
                atendido_en DATETIME NULL,
                consumido_en DATETIME NULL,
                fecha DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_auto_sol_site (site_name),
                INDEX idx_auto_sol_estado (estado),
                INDEX idx_auto_sol_fecha (fecha)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        print(f"[AUTOMATIZACION] No se pudo asegurar historial: {e}", flush=True)
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def registrar_movimiento_automatizacion(
    accion: str,
    request_id: str | None = None,
    site_name: str | None = None,
    usuario: Optional[dict[str, Any]] = None,
    detalle: str | None = None,
    request: Request | None = None,
) -> None:
    asegurar_tabla_automatizacion_movimientos()
    conn = conectar_mysql()
    if not conn:
        return
    try:
        ip = ""
        user_agent = ""
        if request is not None:
            ip = str(request.client.host if request.client else "")
            user_agent = str(request.headers.get("user-agent") or "")

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO automatizacion_movimientos (
                request_id, site_name, usuario_id, usuario, rol, accion, detalle, ip, user_agent
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                str(request_id or "").strip() or None,
                str(site_name or "").strip() or None,
                int(usuario.get("id") or 0) if usuario else None,
                str(usuario.get("usuario") or "") if usuario else None,
                str(usuario.get("rol") or "") if usuario else None,
                str(accion or "").strip(),
                str(detalle or "").strip() or None,
                ip or None,
                user_agent or None,
            ),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[AUTOMATIZACION] No se pudo registrar movimiento {accion}: {e}", flush=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def guardar_solicitud_automatizacion(
    request_id: str,
    site_name: str,
    detalle: str | None,
    created_at: int,
    expires_at: int,
) -> None:
    asegurar_tabla_automatizacion_movimientos()
    conn = conectar_mysql()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO automatizacion_solicitudes (
                request_id, site_name, tipo, estado, detalle, created_at_epoch, expires_at_epoch
            )
            VALUES (%s,%s,'OTP','PENDIENTE',%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                site_name=VALUES(site_name),
                estado='PENDIENTE',
                detalle=VALUES(detalle),
                created_at_epoch=VALUES(created_at_epoch),
                expires_at_epoch=VALUES(expires_at_epoch),
                atendido_por_usuario_id=NULL,
                atendido_por=NULL,
                atendido_en=NULL,
                consumido_en=NULL
            """,
            (request_id, site_name, detalle or "", created_at, expires_at),
        )
        conn.commit()
        cur.close()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def actualizar_estado_solicitud_automatizacion(
    request_id: str,
    estado: str,
    usuario: Optional[dict[str, Any]] = None,
) -> None:
    asegurar_tabla_automatizacion_movimientos()
    conn = conectar_mysql()
    if not conn:
        return
    try:
        cur = conn.cursor()
        if estado == "CAPTURADO":
            cur.execute(
                """
                UPDATE automatizacion_solicitudes
                SET estado=%s,
                    atendido_por_usuario_id=%s,
                    atendido_por=%s,
                    atendido_en=NOW()
                WHERE request_id=%s
                """,
                (
                    estado,
                    int(usuario.get("id") or 0) if usuario else None,
                    str(usuario.get("usuario") or "") if usuario else None,
                    request_id,
                ),
            )
        elif estado == "CONSUMIDO":
            cur.execute(
                """
                UPDATE automatizacion_solicitudes
                SET estado=%s, consumido_en=NOW()
                WHERE request_id=%s
                """,
                (estado, request_id),
            )
        else:
            cur.execute(
                "UPDATE automatizacion_solicitudes SET estado=%s WHERE request_id=%s",
                (estado, request_id),
            )
        conn.commit()
        cur.close()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def obtener_solicitud_automatizacion(request_id: str) -> Optional[dict[str, Any]]:
    asegurar_tabla_automatizacion_movimientos()
    conn = conectar_mysql()
    if not conn:
        return None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT request_id, site_name, estado, detalle, expires_at_epoch
            FROM automatizacion_solicitudes
            WHERE request_id=%s
            LIMIT 1
            """,
            (request_id,),
        )
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _normalizar_descripcion_producto(valor: str) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or "").strip().lower())
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return " ".join(texto.split())


def _parse_bool_automatizacion(valor: Any) -> bool:
    return str(valor or "").strip().lower() in {"1", "si", "sí", "s", "true", "yes", "iva"}


def _parse_piezas_automatizacion(valor: Any) -> float:
    try:
        piezas = float(str(valor or "1").replace(",", "."))
    except Exception:
        piezas = 1
    return piezas if piezas > 0 else 1


def asegurar_tabla_automatizacion_codigos_producto() -> bool:
    conn = conectar_mysql()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS automatizacion_codigos_producto (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                plataforma VARCHAR(80) NOT NULL,
                descripcion_producto TEXT NOT NULL,
                descripcion_normalizada VARCHAR(255) NOT NULL,
                codigo_interno VARCHAR(120) NOT NULL DEFAULT '',
                descripcion_galactico TEXT NULL,
                lleva_iva TINYINT(1) NOT NULL DEFAULT 0,
                piezas DECIMAL(12,3) NOT NULL DEFAULT 1,
                fuente VARCHAR(80) NOT NULL DEFAULT 'manual',
                fecha DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_auto_codigo_producto (plataforma, descripcion_normalizada),
                INDEX idx_auto_codigo_plataforma (plataforma),
                INDEX idx_auto_codigo_actualizado (actualizado_en)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        for statement in (
            "ALTER TABLE automatizacion_codigos_producto ADD COLUMN descripcion_galactico TEXT NULL",
            "ALTER TABLE automatizacion_codigos_producto ADD COLUMN lleva_iva TINYINT(1) NOT NULL DEFAULT 0",
            "ALTER TABLE automatizacion_codigos_producto ADD COLUMN piezas DECIMAL(12,3) NOT NULL DEFAULT 1",
        ):
            try:
                cur.execute(statement)
            except mysql.connector.Error:
                pass
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        print(f"[AUTOMATIZACION] No se pudo asegurar codigos de producto: {e}", flush=True)
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def listar_codigos_producto_automatizacion(plataforma: str | None = None) -> list[dict[str, Any]]:
    if not asegurar_tabla_automatizacion_codigos_producto():
        raise HTTPException(status_code=500, detail="No se pudo preparar la tabla de codigos")
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a MySQL")
    try:
        cur = conn.cursor(dictionary=True)
        params: list[Any] = []
        where = ""
        if str(plataforma or "").strip():
            where = "WHERE plataforma=%s"
            params.append(str(plataforma or "").strip())
        cur.execute(
            f"""
            SELECT id, plataforma, descripcion_producto, descripcion_normalizada,
                   codigo_interno, descripcion_galactico, lleva_iva, piezas,
                   fuente, fecha, actualizado_en
            FROM automatizacion_codigos_producto
            {where}
            ORDER BY plataforma ASC, descripcion_producto ASC
            """,
            tuple(params),
        )
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        try:
            conn.close()
        except Exception:
            pass


def guardar_codigo_producto_automatizacion(
    plataforma: str,
    descripcion_producto: str,
    codigo_interno: str,
    descripcion_galactico: str | None = None,
    lleva_iva: Any = None,
    piezas: Any = None,
    fuente: str = "manual",
) -> dict[str, Any]:
    plataforma_limpia = str(plataforma or "").strip()
    descripcion_limpia = str(descripcion_producto or "").strip()
    codigo_limpio = str(codigo_interno or "").strip()
    galactico_limpio = str(descripcion_galactico or "").strip()
    iva_limpio = _parse_bool_automatizacion(lleva_iva) if lleva_iva is not None else None
    piezas_limpio = _parse_piezas_automatizacion(piezas) if piezas is not None else None
    fuente_limpia = str(fuente or "manual").strip() or "manual"
    descripcion_normalizada = _normalizar_descripcion_producto(descripcion_limpia)

    if not plataforma_limpia:
        raise HTTPException(status_code=422, detail="La plataforma es obligatoria")
    if not descripcion_limpia:
        raise HTTPException(status_code=422, detail="La descripcion del producto es obligatoria")
    if not descripcion_normalizada:
        raise HTTPException(status_code=422, detail="La descripcion del producto no es valida")

    if not asegurar_tabla_automatizacion_codigos_producto():
        raise HTTPException(status_code=500, detail="No se pudo preparar la tabla de codigos")
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a MySQL")
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO automatizacion_codigos_producto (
                plataforma, descripcion_producto, descripcion_normalizada, codigo_interno,
                descripcion_galactico, lleva_iva, piezas, fuente
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                descripcion_producto=VALUES(descripcion_producto),
                codigo_interno=VALUES(codigo_interno),
                descripcion_galactico=CASE
                    WHEN VALUES(descripcion_galactico) IS NULL OR VALUES(descripcion_galactico)='' THEN descripcion_galactico
                    ELSE VALUES(descripcion_galactico)
                END,
                lleva_iva=CASE
                    WHEN %s IS NULL THEN lleva_iva
                    ELSE VALUES(lleva_iva)
                END,
                piezas=CASE
                    WHEN %s IS NULL THEN piezas
                    ELSE VALUES(piezas)
                END,
                fuente=VALUES(fuente)
            """,
            (
                plataforma_limpia,
                descripcion_limpia,
                descripcion_normalizada,
                codigo_limpio,
                galactico_limpio,
                int(bool(iva_limpio)),
                float(piezas_limpio or 1),
                fuente_limpia,
                iva_limpio,
                piezas_limpio,
            ),
        )
        conn.commit()
        cur.close()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "plataforma": plataforma_limpia,
        "descripcion_producto": descripcion_limpia,
        "descripcion_normalizada": descripcion_normalizada,
        "codigo_interno": codigo_limpio,
        "descripcion_galactico": galactico_limpio,
        "lleva_iva": bool(iva_limpio),
        "piezas": float(piezas_limpio or 1),
        "fuente": fuente_limpia,
    }


def eliminar_codigo_producto_automatizacion(item_id: int) -> bool:
    if not asegurar_tabla_automatizacion_codigos_producto():
        raise HTTPException(status_code=500, detail="No se pudo preparar la tabla de codigos")
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a MySQL")
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM automatizacion_codigos_producto WHERE id=%s", (int(item_id),))
        conn.commit()
        removed = cur.rowcount > 0
        cur.close()
        return removed
    finally:
        try:
            conn.close()
        except Exception:
            pass


# =========================
# Auth Web (JWT)
# =========================
web_security = HTTPBearer(auto_error=False)
WEB_ADMIN_ROLES = {"admin", "administrador", "supervisor"}


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _jwt_encode(payload: dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(_jwt_secret_key().encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"


def _jwt_decode(token: str) -> dict[str, Any]:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError:
        raise HTTPException(status_code=401, detail="Token invalido")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_signature = hmac.new(_jwt_secret_key().encode("utf-8"), signing_input, hashlib.sha256).digest()
    received_signature = _b64url_decode(signature_b64)
    if not hmac.compare_digest(expected_signature, received_signature):
        raise HTTPException(status_code=401, detail="Token invalido")

    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=401, detail="Token invalido")

    exp = int(payload.get("exp") or 0)
    if exp <= int(time.time()):
        raise HTTPException(status_code=401, detail="Token expirado")

    return payload


def crear_jwt_web(user: dict[str, Any]) -> str:
    ahora = int(time.time())
    exp = ahora + (_jwt_exp_hours() * 60 * 60)
    payload = {
        "sub": str(user.get("usuario") or ""),
        "uid": int(user.get("id") or 0),
        "rol": str(user.get("rol") or ""),
        "exp": exp,
        "iat": ahora,
        "tipo": "web",
    }
    return _jwt_encode(payload)


def obtener_usuario_actual_web(
    creds: HTTPAuthorizationCredentials = Depends(web_security),
) -> dict[str, Any]:
    if not creds or not creds.credentials:
        raise HTTPException(status_code=401, detail="Token requerido")
    payload = _jwt_decode(creds.credentials)
    return {
        "id": int(payload.get("uid") or 0),
        "usuario": str(payload.get("sub") or ""),
        "rol": str(payload.get("rol") or "").lower(),
        "exp": int(payload.get("exp") or 0),
    }


def obtener_usuario_actual_web_opcional(
    creds: HTTPAuthorizationCredentials | None = Depends(web_security),
) -> Optional[dict[str, Any]]:
    if not creds or not creds.credentials:
        return None
    try:
        return obtener_usuario_actual_web(creds)
    except HTTPException:
        return None


def requiere_admin(usuario: dict[str, Any] = Depends(obtener_usuario_actual_web)) -> dict[str, Any]:
    rol = str(usuario.get("rol") or "").strip().lower()
    if rol not in WEB_ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Permisos insuficientes")
    return usuario


def asegurar_tabla_bitacora_catalogos():
    conn = conectar_mysql()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bitacora_catalogos (
                id INT AUTO_INCREMENT PRIMARY KEY,
                usuario_id INT NULL,
                usuario VARCHAR(100),
                nivel VARCHAR(50),
                accion VARCHAR(100),
                empresa VARCHAR(100),
                cip VARCHAR(100) NULL,
                cips TEXT NULL,
                detalle TEXT NULL,
                ip VARCHAR(50),
                user_agent TEXT,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_bitacora_usuario (usuario),
                INDEX idx_bitacora_accion (accion),
                INDEX idx_bitacora_empresa (empresa),
                INDEX idx_bitacora_cip (cip),
                INDEX idx_bitacora_fecha (fecha)
            )
            """
        )
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        print(f"[BITACORA] No se pudo asegurar tabla: {e}", flush=True)
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def registrar_bitacora(
    usuario: Optional[dict[str, Any]],
    accion: str,
    empresa: str | None = None,
    cip: str | None = None,
    cips: list[str] | str | None = None,
    detalle: str | None = None,
    request: Request | None = None,
):
    asegurar_tabla_bitacora_catalogos()
    conn = conectar_mysql()
    if not conn:
        return
    try:
        ip = ""
        user_agent = ""
        if request is not None:
            try:
                ip = str(request.client.host if request.client else "")
            except Exception:
                ip = ""
            try:
                user_agent = str(request.headers.get("user-agent") or "")
            except Exception:
                user_agent = ""

        if isinstance(cips, list):
            cips_text = ",".join(str(x).strip() for x in cips if str(x).strip()) or None
        else:
            cips_text = str(cips).strip() if cips is not None and str(cips).strip() else None

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO bitacora_catalogos (
                usuario_id, usuario, nivel, accion, empresa, cip, cips, detalle, ip, user_agent
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(usuario.get("id") or 0) if usuario else None,
                str(usuario.get("usuario") or "") if usuario else "",
                str(usuario.get("rol") or "") if usuario else "",
                str(accion or "").strip(),
                str(empresa or "").strip() or None,
                str(cip or "").strip() or None,
                cips_text,
                str(detalle or "").strip() or None,
                ip or None,
                user_agent or None,
            ),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[BITACORA] No se pudo registrar evento {accion}: {e}", flush=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# =========================
# FastAPI
# =========================
app = FastAPI(title="API Vendedor - Comandas")

from .inventory_gateway import create_inventory_gateway_router

app.include_router(
    create_inventory_gateway_router(_connect_mysql_database, _inventory_gateway_secret)
)

from .facturas_pdf import router as facturas_pdf_router

app.include_router(facturas_pdf_router)

if os.path.isdir(_web_root_dir()):
    app.mount("/web-static", StaticFiles(directory=_web_root_dir()), name="web-static")
_web_logos_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logos")
if os.path.isdir(_web_logos_dir):
    app.mount("/web-logos", StaticFiles(directory=_web_logos_dir), name="web-logos")


def _crm_root_dir() -> str:
    return os.path.join(_runtime_base_dir(), "crm_ventas")


def _load_crm_module():
    module_path = os.path.join(_crm_root_dir(), "crm_app.py")
    if not os.path.isfile(module_path):
        return None
    spec = importlib.util.spec_from_file_location("crm_ventas_app", module_path)
    if not spec or not spec.loader:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.init_db()
    return module


CRM_APP = _load_crm_module()


class _CrmFakeHandler:
    def __init__(self, request: Request, body: bytes = b""):
        self.headers = request.headers
        self.rfile = BytesIO(body)
        self.wfile = BytesIO()
        self.status_code = 200
        self.response_headers: dict[str, str] = {}

    def send_response(self, status_code: int, message: str | None = None):
        self.status_code = status_code

    def send_header(self, key: str, value: str):
        self.response_headers[key] = value

    def end_headers(self):
        return None


def _crm_response(fake: _CrmFakeHandler) -> Response:
    content = fake.wfile.getvalue()
    media_type = fake.response_headers.get("Content-Type") or "application/octet-stream"
    headers = {
        key: value
        for key, value in fake.response_headers.items()
        if key.lower() not in {"content-type", "content-length"}
    }
    return Response(content=content, status_code=fake.status_code, media_type=media_type, headers=headers)


def _crm_required():
    if CRM_APP is None:
        raise HTTPException(status_code=404, detail="Modulo CRM no instalado")
    return CRM_APP


@app.api_route("/crm/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def crm_api_proxy(path: str, request: Request):
    crm = _crm_required()
    body = await request.body()
    fake = _CrmFakeHandler(request, body)
    parsed = urlparse(f"/api/{path}" + (f"?{request.url.query}" if request.url.query else ""))
    method = request.method.upper()
    if method == "GET":
        crm.SalesHandler.handle_api_get(fake, parsed)
    elif method == "POST":
        crm.SalesHandler.handle_api_post(fake, parsed)
    elif method == "PUT":
        crm.SalesHandler.handle_api_put(fake, parsed)
    elif method == "DELETE":
        crm.SalesHandler.handle_api_delete(fake, parsed)
    else:
        raise HTTPException(status_code=405, detail="Metodo no permitido")
    return _crm_response(fake)


@app.get("/crm/exports/{filename:path}")
async def crm_exports(filename: str):
    crm = _crm_required()
    name = os.path.basename(unquote(filename))
    target = os.path.join(crm.EXPORT_DIR, name)
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    with open(target, "rb") as f:
        content = f.read()
    return Response(
        content=content,
        media_type=mimetypes.guess_type(target)[0] or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )


@app.get("/crm", include_in_schema=False)
async def crm_index_no_slash():
    return Response(status_code=307, headers={"Location": "/crm/"})


@app.get("/crm/{path:path}", include_in_schema=False)
async def crm_static(path: str = ""):
    crm = _crm_required()
    rel_path = path.strip("/") or "index.html"
    target = os.path.abspath(os.path.join(crm.PUBLIC_DIR, rel_path))
    public_root = os.path.abspath(crm.PUBLIC_DIR)
    if not (target == public_root or target.startswith(public_root + os.sep)):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    if os.path.isdir(target):
        target = os.path.join(target, "index.html")
    if not os.path.isfile(target):
        target = os.path.join(crm.PUBLIC_DIR, "index.html")
    return FileResponse(target, media_type=mimetypes.guess_type(target)[0] or "text/html")


def _serve_web_html(filename: str, sso_token: str = "") -> HTMLResponse:
    if not _config_bool("web_enabled", True):
        raise HTTPException(status_code=404, detail="Modulo web deshabilitado")

    ruta = os.path.join(_web_root_dir(), filename)
    if not os.path.isfile(ruta):
        raise HTTPException(status_code=404, detail="Pagina no encontrada")
    with open(ruta, "r", encoding="utf-8") as f:
        # La aplicación se monta bajo /comandas dentro de Migración Web. Los
        # recursos y enlaces del proyecto original eran absolutos desde /.
        contenido = f.read()
    for origen, destino in (
        ('href="/web-static/', 'href="/comandas/web-static/'),
        ('src="/web-static/', 'src="/comandas/web-static/'),
        ('src="/web-logos/', 'src="/comandas/web-logos/'),
        ('href="/catalogo-web', 'href="/comandas/catalogo-web'),
        ('href="/admin-fichas', 'href="/comandas/admin-fichas'),
        ('href="/admin-bitacora', 'href="/comandas/admin-bitacora'),
    ):
        contenido = contenido.replace(origen, destino)
    if sso_token:
        # Solo se acepta un token firmado por esta instancia. Se guarda en la
        # sesión del iframe para que las llamadas posteriores no pidan login.
        _jwt_decode(sso_token)
        bootstrap = (
            "<script>sessionStorage.setItem('comandas_web_token',"
            + json.dumps(sso_token)
            + ");</script>"
        )
        contenido = contenido.replace("</body>", bootstrap + "</body>")
    return HTMLResponse(contenido)



# =========================
# Models auth / reportes
# =========================
class LoginIn(BaseModel):
    usuario: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=1, max_length=200)


class LoginOut(BaseModel):
    token: str
    usuario: str
    rol: str


class LoginWebOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    usuario: str
    rol: str


class AuthMeOut(BaseModel):
    id: int
    usuario: str
    rol: str


class AutomationOtpRequestIn(BaseModel):
    request_id: str = Field(..., min_length=8, max_length=120)
    site_name: str = Field(..., min_length=1, max_length=150)
    detalle: Optional[str] = Field(default="", max_length=500)


class AutomationOtpSubmitIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=16)


class AutomationOtpPendingOut(BaseModel):
    request_id: str
    site_name: str
    created_at: int
    expires_at: int
    detalle: Optional[str] = ""


class AutomationMovimientoOut(BaseModel):
    id: int
    request_id: Optional[str] = None
    site_name: Optional[str] = None
    usuario_id: Optional[int] = None
    usuario: Optional[str] = None
    rol: Optional[str] = None
    accion: str
    detalle: Optional[str] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    fecha: str


class AutomationProductCodeIn(BaseModel):
    plataforma: str = Field(..., min_length=1, max_length=80)
    descripcion_producto: str = Field(..., min_length=1, max_length=2000)
    codigo_interno: str = Field(default="", max_length=120)
    descripcion_galactico: Optional[str] = Field(default="", max_length=2000)
    lleva_iva: Optional[bool] = None
    piezas: Optional[float] = None
    fuente: str = Field(default="manual", max_length=80)


class AutomationProductCodeOut(BaseModel):
    id: Optional[int] = None
    plataforma: str
    descripcion_producto: str
    descripcion_normalizada: str
    codigo_interno: str = ""
    descripcion_galactico: str = ""
    lleva_iva: bool = False
    piezas: float = 1
    fuente: str = "manual"
    fecha: Optional[str] = None
    actualizado_en: Optional[str] = None


class BitacoraOut(BaseModel):
    id: int
    usuario_id: Optional[int] = None
    usuario: Optional[str] = None
    nivel: Optional[str] = None
    accion: Optional[str] = None
    empresa: Optional[str] = None
    cip: Optional[str] = None
    cips: Optional[str] = None
    detalle: Optional[str] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    fecha: Optional[str] = None


class ReporteClienteResumenOut(BaseModel):
    numero_cliente: str
    cliente_nombre: str
    ultima_compra: Optional[str] = None


class ReporteClienteProductoOut(BaseModel):
    cip: str = ""
    descripcion: str
    cantidad_total: float
    piezas_total: int
    ultima_compra: str | None = None


class FacturaResumenOut(BaseModel):
    factura: str
    fecha: str | None = None
    numero_cliente: str | None = None
    consignatario: str | None = None
    importe: float | None = None
    subtotal: float | None = None
    descuento: float | None = None
    iva: float | None = None
    total: float | None = None
    empresa: str | None = None
    numero_salida: str | None = None
    estatus: str | None = None
    sae_codigo: str | None = None


class ReporteClienteDetalleOut(BaseModel):
    numero_cliente: str
    cliente_nombre: str
    mes: str | None = None
    ultima_compra: str | None = None
    productos: list[ReporteClienteProductoOut]
    facturas: list[FacturaResumenOut] = []


class ReporteClienteMesOut(BaseModel):
    value: str
    label: str

class ComandaEditableItemOut(BaseModel):
    id: Optional[int]
    cip: str
    descripcion: str
    kgs: float
    piezas: float
    observaciones: str = ""


class ComandaEditableOut(BaseModel):
    id: int
    folio: str
    vendedor: Optional[str] = ""
    empresa: Optional[str] = ""
    cliente_numero: Optional[str] = ""
    cliente_nombre: Optional[str] = ""
    fecha: Optional[str] = ""
    observaciones_pedido: Optional[str] = ""
    productos: List[ComandaEditableItemOut]


class ComandaEditableItemIn(BaseModel):
    id: Optional[int]
    cip: str
    descripcion: str
    kgs: float = 0
    piezas: float = 0
    observaciones: str = ""


class ComandaEditableUpdateIn(BaseModel):
    vendedor: Optional[str] = ""
    empresa: Optional[str] = ""
    cliente_numero: Optional[str] = ""
    cliente_nombre: Optional[str] = ""
    observaciones_pedido: Optional[str] = ""
    productos: List[ComandaEditableItemIn]

class ComandaHistorialOut(BaseModel):
    id: int
    comanda_id: int
    usuario: Optional[str] = ""
    accion: Optional[str] = ""
    detalle: Optional[str] = ""
    fecha: Optional[str] = ""


@app.get("/")
def root():
    return {"ok": True, "service": "API Vendedor - Comandas"}


@app.get("/health")
def health():
    return {"status": "ok"}


def _conteo_conn():
    conn = _connect_mysql_database("inventarios")
    if not conn:
        raise HTTPException(status_code=503, detail="No se pudo conectar a la base de inventarios")
    return conn


def _conteo_num(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(str(value).replace(",", "."))
    except Exception:
        return 0.0


def _conteo_fmt_codigo(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def _conteo_lista_fabricantes(value: Any) -> List[str]:
    partes = re.split(r"[,;\n|]+", str(value or ""))
    fabricantes: List[str] = []
    vistos = set()
    for parte in partes:
        nombre = parte.strip()
        clave = nombre.upper()
        if nombre and clave not in vistos:
            vistos.add(clave)
            fabricantes.append(nombre)
    return fabricantes


def _conteo_combinar_fabricantes(actuales: Any, nuevo: Any) -> str:
    fabricantes = _conteo_lista_fabricantes(actuales)
    nuevo_txt = str(nuevo or "").strip()
    if nuevo_txt and nuevo_txt.upper() not in {f.upper() for f in fabricantes}:
        fabricantes.append(nuevo_txt)
    return ", ".join(fabricantes)


def _conteo_fabricantes_detalle(cur, producto: Dict[str, Any]) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT fabricante, piezas_por_caja, cajas_por_palet
        FROM inventario_fabricantes
        WHERE (inventario_id=%s AND %s <> '') OR sku=%s
        ORDER BY fabricante
        """,
        (producto.get("id") or "", producto.get("id") or "", _conteo_fmt_codigo(producto.get("sku"))),
    )
    detalles: List[Dict[str, Any]] = []
    vistos = set()
    for row in cur.fetchall():
        fabricante = str(row.get("fabricante") or "").strip()
        clave = fabricante.upper()
        if fabricante and clave not in vistos:
            vistos.add(clave)
            detalles.append({
                "fabricante": fabricante,
                "piezas_por_caja": _conteo_num(row.get("piezas_por_caja")),
                "cajas_por_palet": _conteo_num(row.get("cajas_por_palet")),
            })
    return detalles


def _conteo_fecha_text(value: Any) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%d-%m-%Y %H:%M")
    return str(value or "")


def _conteo_ensure_tables():
    conn = _conteo_conn()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS conteos_fisicos_lotes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                nombre VARCHAR(120) NOT NULL,
                fecha_inicio DATETIME NOT NULL,
                fecha_cierre DATETIME NULL,
                estado VARCHAR(20) DEFAULT 'abierto',
                observaciones TEXT,
                created_at DATETIME NOT NULL,
                INDEX idx_conteo_lote_fecha (fecha_inicio),
                INDEX idx_conteo_lote_estado (estado)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS conteos_fisicos (
                id INT AUTO_INCREMENT PRIMARY KEY,
                conteo_id INT NULL,
                fecha_conteo DATETIME NOT NULL,
                sku VARCHAR(120) NOT NULL,
                tipo VARCHAR(255),
                presentacion VARCHAR(255),
                descripcion TEXT,
                fabricante VARCHAR(255),
                piezas_por_caja DECIMAL(10,2) DEFAULT 0,
                cajas_por_palet DECIMAL(10,2) DEFAULT 0,
                palets DECIMAL(10,2) DEFAULT 0,
                cajas DECIMAL(10,2) DEFAULT 0,
                cajas_piezas_buenas DECIMAL(10,2) DEFAULT 0,
                cajas_piezas_malas DECIMAL(10,2) DEFAULT 0,
                piezas_buenas DECIMAL(10,2) DEFAULT 0,
                piezas_malas DECIMAL(10,2) DEFAULT 0,
                piezas DECIMAL(10,2) DEFAULT 0,
                fracciones DECIMAL(10,2) DEFAULT 0,
                total_piezas DECIMAL(12,2) DEFAULT 0,
                observaciones TEXT,
                created_at DATETIME NOT NULL,
                INDEX idx_conteos_sku (sku),
                INDEX idx_conteos_fecha (fecha_conteo)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
        for column_name, ddl in (
            ("piezas_por_caja", "ALTER TABLE inventario_general ADD COLUMN piezas_por_caja DECIMAL(10,2) DEFAULT 0"),
            ("cajas_por_palet", "ALTER TABLE inventario_general ADD COLUMN cajas_por_palet DECIMAL(10,2) DEFAULT 0"),
            ("fabricantes", "ALTER TABLE inventario_general ADD COLUMN fabricantes TEXT"),
        ):
            cur.execute(
                """
                SELECT COUNT(*) AS existe
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME='inventario_general' AND COLUMN_NAME=%s
                """,
                ("inventarios", column_name),
            )
            exists = cur.fetchone() or {}
            if not exists.get("existe"):
                cur.execute(ddl)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS inventario_fabricantes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                inventario_id INT NULL,
                sku VARCHAR(120) NOT NULL,
                fabricante VARCHAR(255) NOT NULL,
                piezas_por_caja DECIMAL(10,2) DEFAULT 0,
                cajas_por_palet DECIMAL(10,2) DEFAULT 0,
                updated_at DATETIME NOT NULL,
                UNIQUE KEY ux_inv_fabricante_sku (sku, fabricante),
                INDEX idx_inv_fabricante_producto (inventario_id),
                INDEX idx_inv_fabricante_sku (sku)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
        for column_name, ddl in (
            ("conteo_id", "ALTER TABLE conteos_fisicos ADD COLUMN conteo_id INT NULL AFTER id"),
            ("fabricante", "ALTER TABLE conteos_fisicos ADD COLUMN fabricante VARCHAR(255) NULL AFTER descripcion"),
            ("cajas_piezas_buenas", "ALTER TABLE conteos_fisicos ADD COLUMN cajas_piezas_buenas DECIMAL(10,2) DEFAULT 0 AFTER cajas"),
            ("cajas_piezas_malas", "ALTER TABLE conteos_fisicos ADD COLUMN cajas_piezas_malas DECIMAL(10,2) DEFAULT 0 AFTER cajas_piezas_buenas"),
            ("piezas_buenas", "ALTER TABLE conteos_fisicos ADD COLUMN piezas_buenas DECIMAL(10,2) DEFAULT 0 AFTER cajas"),
            ("piezas_malas", "ALTER TABLE conteos_fisicos ADD COLUMN piezas_malas DECIMAL(10,2) DEFAULT 0 AFTER piezas_buenas"),
        ):
            cur.execute(
                """
                SELECT COUNT(*) AS existe
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME='conteos_fisicos' AND COLUMN_NAME=%s
                """,
                ("inventarios", column_name),
            )
            exists = cur.fetchone() or {}
            if not exists.get("existe"):
                cur.execute(ddl)
        cur.execute("SELECT COUNT(*) AS pendientes FROM conteos_fisicos WHERE conteo_id IS NULL")
        pendientes = (cur.fetchone() or {}).get("pendientes", 0)
        if pendientes:
            cur.execute(
                """
                INSERT INTO conteos_fisicos_lotes (nombre, fecha_inicio, fecha_cierre, estado, observaciones, created_at)
                SELECT 'Conteo historico', MIN(fecha_conteo), MAX(fecha_conteo), 'cerrado',
                       'Conteos registrados antes de agrupar por conteo general.', NOW()
                FROM conteos_fisicos
                WHERE conteo_id IS NULL
                """
            )
            lote_historico_id = cur.lastrowid
            cur.execute("UPDATE conteos_fisicos SET conteo_id=%s WHERE conteo_id IS NULL", (lote_historico_id,))
        conn.commit()
    finally:
        if cur:
            cur.close()
        conn.close()


@app.get("/conteo/productos")
def conteo_productos(q: str = ""):
    _conteo_ensure_tables()
    filtro = str(q or "").strip()
    conn = _conteo_conn()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        if filtro:
            like = f"%{filtro}%"
            cur.execute(
                """
                SELECT id, sku, tipo, presentacion, descripcion, piezas_por_caja, cajas_por_palet, fabricantes
                FROM inventario_general
                WHERE sku LIKE %s OR descripcion LIKE %s OR tipo LIKE %s OR presentacion LIKE %s OR fabricantes LIKE %s
                ORDER BY
                    CASE WHEN sku = %s THEN 0 ELSE 1 END,
                    descripcion, sku
                LIMIT 50
                """,
                (like, like, like, like, like, filtro),
            )
        else:
            cur.execute(
                """
                SELECT id, sku, tipo, presentacion, descripcion, piezas_por_caja, cajas_por_palet, fabricantes
                FROM inventario_general
                ORDER BY descripcion, sku
                LIMIT 50
                """
            )
        rows = cur.fetchall()
        for row in rows:
            row["sku"] = _conteo_fmt_codigo(row.get("sku"))
            row["piezas_por_caja"] = _conteo_num(row.get("piezas_por_caja"))
            row["cajas_por_palet"] = _conteo_num(row.get("cajas_por_palet"))
            row["fabricantes_detalle"] = _conteo_fabricantes_detalle(cur, row)
        return {"count": len(rows), "rows": rows}
    finally:
        if cur:
            cur.close()
        conn.close()


@app.get("/conteo/catalogo")
def conteo_catalogo():
    _conteo_ensure_tables()
    conn = _conteo_conn()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, sku, tipo, presentacion, descripcion, piezas_por_caja, cajas_por_palet, fabricantes
            FROM inventario_general
            ORDER BY descripcion, sku
            """
        )
        rows = cur.fetchall()
        for row in rows:
            row["sku"] = _conteo_fmt_codigo(row.get("sku"))
            row["piezas_por_caja"] = _conteo_num(row.get("piezas_por_caja"))
            row["cajas_por_palet"] = _conteo_num(row.get("cajas_por_palet"))
            row["fabricantes_detalle"] = _conteo_fabricantes_detalle(cur, row)
        return {
            "count": len(rows),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "rows": rows,
        }
    finally:
        if cur:
            cur.close()
        conn.close()


def _conteo_ensure_lote_actual(cur, payload: Dict[str, Any]) -> int:
    conteo_id = payload.get("conteo_id")
    if conteo_id:
        cur.execute("SELECT id FROM conteos_fisicos_lotes WHERE id=%s LIMIT 1", (conteo_id,))
        row = cur.fetchone()
        if row:
            return int(row["id"])

    nombre = str(payload.get("conteo_nombre") or "").strip()
    if not nombre:
        nombre = f"Conteo Android {datetime.now().strftime('%Y-%m-%d')}"
    cur.execute(
        """
        SELECT id
        FROM conteos_fisicos_lotes
        WHERE nombre=%s AND estado='abierto' AND DATE(fecha_inicio)=CURDATE()
        ORDER BY id DESC
        LIMIT 1
        """,
        (nombre,),
    )
    row = cur.fetchone()
    if row:
        return int(row["id"])
    cur.execute(
        """
        INSERT INTO conteos_fisicos_lotes (nombre, fecha_inicio, estado, created_at)
        VALUES (%s, NOW(), 'abierto', NOW())
        """,
        (nombre,),
    )
    return int(cur.lastrowid)


@app.post("/conteo/conteos")
def conteo_guardar(payload: Dict[str, Any]):
    _conteo_ensure_tables()
    producto_id = payload.get("id")
    sku = _conteo_fmt_codigo(payload.get("sku"))
    if not sku and not producto_id:
        raise HTTPException(status_code=400, detail="Producto requerido")

    piezas_por_caja = _conteo_num(payload.get("piezas_por_caja"))
    cajas_por_palet = _conteo_num(payload.get("cajas_por_palet"))
    palets = _conteo_num(payload.get("palets"))
    cajas = _conteo_num(payload.get("cajas"))
    cajas_piezas_buenas = _conteo_num(payload.get("cajas_piezas_buenas"))
    cajas_piezas_malas = _conteo_num(payload.get("cajas_piezas_malas"))
    piezas_buenas = _conteo_num(payload.get("piezas_buenas"))
    piezas_malas = _conteo_num(payload.get("piezas_malas"))
    fabricante = str(payload.get("fabricante") or "").strip()
    piezas = _conteo_num(payload.get("piezas"))
    if piezas == 0 and (piezas_buenas > 0 or piezas_malas > 0):
        piezas = piezas_buenas + piezas_malas
    fracciones = _conteo_num(payload.get("fracciones"))
    if piezas_por_caja <= 0 and (palets > 0 or cajas > 0):
        raise HTTPException(status_code=400, detail="Configura piezas por caja")
    if cajas_por_palet <= 0 and palets > 0:
        raise HTTPException(status_code=400, detail="Configura cajas por palet")

    total = (palets * cajas_por_palet * piezas_por_caja) + (cajas * piezas_por_caja) + piezas + fracciones
    conn = _conteo_conn()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        producto = None
        if producto_id:
            cur.execute(
                """
                SELECT id, sku, tipo, presentacion, descripcion, fabricantes
                FROM inventario_general
                WHERE id=%s
                LIMIT 1
                """,
                (producto_id,),
            )
            producto = cur.fetchone()
        if not producto:
            cur.execute(
                """
                SELECT id, sku, tipo, presentacion, descripcion, fabricantes
                FROM inventario_general
                WHERE sku=%s
                LIMIT 1
                """,
                (sku,),
            )
            producto = cur.fetchone()
        if not producto:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        conteo_id = _conteo_ensure_lote_actual(cur, payload)
        cur.execute(
            """
            INSERT INTO conteos_fisicos (
                conteo_id, fecha_conteo, sku, tipo, presentacion, descripcion, fabricante,
                piezas_por_caja, cajas_por_palet, palets, cajas,
                cajas_piezas_buenas, cajas_piezas_malas,
                piezas_buenas, piezas_malas, piezas, fracciones,
                total_piezas, observaciones, created_at
            )
            VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                conteo_id,
                _conteo_fmt_codigo(producto.get("sku")),
                producto.get("tipo"),
                producto.get("presentacion"),
                producto.get("descripcion"),
                fabricante,
                piezas_por_caja,
                cajas_por_palet,
                palets,
                cajas,
                cajas_piezas_buenas,
                cajas_piezas_malas,
                piezas_buenas,
                piezas_malas,
                piezas,
                fracciones,
                total,
                str(payload.get("observaciones") or "").strip(),
            ),
        )
        cur.execute(
            """
            UPDATE inventario_general
            SET piezas_por_caja=%s, cajas_por_palet=%s, fabricantes=%s
            WHERE id=%s
            """,
            (piezas_por_caja, cajas_por_palet, _conteo_combinar_fabricantes(producto.get("fabricantes"), fabricante), producto.get("id")),
        )
        if fabricante:
            cur.execute(
                """
                INSERT INTO inventario_fabricantes (
                    inventario_id, sku, fabricante, piezas_por_caja, cajas_por_palet, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    inventario_id=VALUES(inventario_id),
                    piezas_por_caja=VALUES(piezas_por_caja),
                    cajas_por_palet=VALUES(cajas_por_palet),
                    updated_at=NOW()
                """,
                (
                    producto.get("id"),
                    _conteo_fmt_codigo(producto.get("sku")),
                    fabricante,
                    piezas_por_caja,
                    cajas_por_palet,
                ),
            )
        conn.commit()
        return {"ok": True, "conteo_id": conteo_id, "total_piezas": total}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cur:
            cur.close()
        conn.close()


@app.get("/conteo/conteos")
def conteo_listar(q: str = ""):
    _conteo_ensure_tables()
    filtro = str(q or "").strip()
    conn = _conteo_conn()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        if filtro:
            like = f"%{filtro}%"
            cur.execute(
                """
                SELECT fecha_conteo, sku, descripcion, presentacion, fabricante, piezas_por_caja,
                       cajas_por_palet, palets, cajas, cajas_piezas_buenas, cajas_piezas_malas,
                       piezas_buenas, piezas_malas,
                       piezas, fracciones, total_piezas, observaciones
                FROM conteos_fisicos
                WHERE sku LIKE %s OR descripcion LIKE %s OR presentacion LIKE %s OR fabricante LIKE %s
                ORDER BY fecha_conteo DESC, id DESC
                LIMIT 100
                """,
                (like, like, like, like),
            )
        else:
            cur.execute(
                """
                SELECT fecha_conteo, sku, descripcion, presentacion, fabricante, piezas_por_caja,
                       cajas_por_palet, palets, cajas, cajas_piezas_buenas, cajas_piezas_malas,
                       piezas_buenas, piezas_malas,
                       piezas, fracciones, total_piezas, observaciones
                FROM conteos_fisicos
                ORDER BY fecha_conteo DESC, id DESC
                LIMIT 100
                """
            )
        rows = cur.fetchall()
        for row in rows:
            row["fecha_conteo"] = _conteo_fecha_text(row.get("fecha_conteo"))
            row["sku"] = _conteo_fmt_codigo(row.get("sku"))
            for numeric_field in (
                "piezas_por_caja", "cajas_por_palet", "palets", "cajas",
                "cajas_piezas_buenas", "cajas_piezas_malas",
                "piezas_buenas", "piezas_malas", "piezas", "fracciones", "total_piezas"
            ):
                row[numeric_field] = _conteo_num(row.get(numeric_field))
        return {"count": len(rows), "rows": rows}
    finally:
        if cur:
            cur.close()
        conn.close()


@app.post("/auth/login", response_model=LoginOut)
def login(payload: LoginIn):
    print(f"[AUTH] Intento login usuario={payload.usuario!r}", flush=True)
    res = autenticar_usuario(payload.usuario, payload.password)
    if res == "INACTIVO":
        raise HTTPException(status_code=403, detail="Usuario inactivo")
    if not res:
        raise HTTPException(status_code=401, detail="Credenciales invÃ¡lidas")

    token = crear_token(res)
    return LoginOut(token=token, usuario=res["usuario"], rol=res["rol"])


@app.post("/auth/login-web", response_model=LoginWebOut)
def login_web(payload: LoginIn, request: Request):
    res = autenticar_usuario(payload.usuario, payload.password)
    if res == "INACTIVO":
        registrar_bitacora(None, "LOGIN_WEB", detalle=f"Usuario inactivo: {payload.usuario}", request=request)
        raise HTTPException(status_code=403, detail="Usuario inactivo")
    if not res:
        registrar_bitacora(None, "LOGIN_WEB", detalle=f"Credenciales invalidas: {payload.usuario}", request=request)
        raise HTTPException(status_code=401, detail="Credenciales invalidas")

    user = {
        "id": int(res["id"]),
        "usuario": str(res["usuario"]),
        "rol": str(res["rol"]).lower(),
    }
    registrar_bitacora(user, "LOGIN_WEB", detalle="Inicio de sesion web", request=request)
    return LoginWebOut(
        access_token=crear_jwt_web(user),
        usuario=user["usuario"],
        rol=user["rol"],
    )


@app.get("/auth/me", response_model=AuthMeOut)
def auth_me(usuario: dict = Depends(obtener_usuario_actual_web)):
    return AuthMeOut(id=int(usuario["id"]), usuario=usuario["usuario"], rol=usuario["rol"])


@app.post("/automatizacion/otp/request")
def automatizacion_registrar_otp_request(
    payload: AutomationOtpRequestIn,
    request: Request,
):
    require_automation_internal_key(request)
    cleanup_expired_otp_requests()
    now = int(time.time())
    OTP_REQUESTS[payload.request_id] = {
        "request_id": payload.request_id,
        "site_name": payload.site_name,
        "detalle": payload.detalle or "",
        "created_at": now,
        "expires_at": now + OTP_TTL_SECONDS,
        "has_code": False,
        "consumed": False,
    }
    guardar_solicitud_automatizacion(
        request_id=payload.request_id,
        site_name=payload.site_name,
        detalle=payload.detalle or "",
        created_at=now,
        expires_at=now + OTP_TTL_SECONDS,
    )
    registrar_movimiento_automatizacion(
        "OTP_SOLICITADO",
        request_id=payload.request_id,
        site_name=payload.site_name,
        detalle=payload.detalle or "Solicitud de codigo 2 pasos",
        request=request,
    )
    return {"ok": True}


@app.get("/automatizacion/otp/pending", response_model=list[AutomationOtpPendingOut])
def automatizacion_otp_pendientes(user: dict = Depends(get_current_user)):
    cleanup_expired_otp_requests()
    asegurar_tabla_automatizacion_movimientos()
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a MySQL")
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT request_id, site_name, created_at_epoch, expires_at_epoch, detalle
            FROM automatizacion_solicitudes
            WHERE tipo='OTP' AND estado='PENDIENTE' AND expires_at_epoch > %s
            ORDER BY site_name ASC, created_at_epoch ASC
            """,
            (int(time.time()),),
        )
        rows = cur.fetchall()
        cur.close()
        return [
            AutomationOtpPendingOut(
                request_id=str(row["request_id"]),
                site_name=str(row["site_name"]),
                created_at=int(row["created_at_epoch"]),
                expires_at=int(row["expires_at_epoch"]),
                detalle=str(row.get("detalle") or ""),
            )
            for row in rows
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.post("/automatizacion/otp/{request_id}")
def automatizacion_enviar_otp(
    request_id: str,
    payload: AutomationOtpSubmitIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    cleanup_expired_otp_requests()
    persisted_request = obtener_solicitud_automatizacion(request_id)
    if request_id not in OTP_REQUESTS and not persisted_request:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada o expirada")
    if persisted_request and str(persisted_request.get("estado") or "").upper() != "PENDIENTE":
        raise HTTPException(status_code=409, detail="Solicitud ya atendida")

    code = str(payload.code or "").strip()
    OTP_CODES[request_id] = {
        "code": code,
        "expires_at": int(time.time()) + OTP_TTL_SECONDS,
    }
    site_name = str((persisted_request or {}).get("site_name") or "")
    if request_id in OTP_REQUESTS:
        OTP_REQUESTS[request_id]["has_code"] = True
        site_name = str(OTP_REQUESTS[request_id].get("site_name") or site_name)
    actualizar_estado_solicitud_automatizacion(request_id, "CAPTURADO", usuario=user)
    registrar_movimiento_automatizacion(
        "OTP_CAPTURADO",
        request_id=request_id,
        site_name=site_name,
        usuario=user,
        detalle="Codigo temporal capturado desde app Android",
        request=request,
    )
    return {"ok": True}


@app.get("/automatizacion/otp/{request_id}/consume")
def automatizacion_consumir_otp(request_id: str, request: Request):
    require_automation_internal_key(request)
    cleanup_expired_otp_requests()
    payload = OTP_CODES.pop(request_id, None)
    if not payload:
        return Response(status_code=204)

    site_name = ""
    if request_id in OTP_REQUESTS:
        OTP_REQUESTS[request_id]["consumed"] = True
        site_name = str(OTP_REQUESTS[request_id].get("site_name") or "")

    actualizar_estado_solicitud_automatizacion(request_id, "CONSUMIDO")
    registrar_movimiento_automatizacion(
        "OTP_CONSUMIDO",
        request_id=request_id,
        site_name=site_name,
        detalle="Codigo temporal consumido por automatizacion",
        request=request,
    )
    return {"code": payload["code"]}


@app.get("/automatizacion/solicitudes")
def automatizacion_solicitudes(
    request: Request,
    estado: str = Query("PENDIENTE", min_length=1, max_length=50),
    limit: int = Query(200, ge=1, le=500),
):
    require_automation_internal_key(request)
    cleanup_expired_otp_requests()
    asegurar_tabla_automatizacion_movimientos()
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a MySQL")
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT request_id, site_name, tipo, estado, detalle,
                   created_at_epoch, expires_at_epoch, atendido_por,
                   atendido_en, consumido_en, fecha, actualizado_en
            FROM automatizacion_solicitudes
            WHERE estado=%s
            ORDER BY site_name ASC, fecha DESC
            LIMIT %s
            """,
            (estado.upper(), int(limit)),
        )
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "request_id": str(row.get("request_id") or ""),
                "site_name": str(row.get("site_name") or ""),
                "tipo": str(row.get("tipo") or ""),
                "estado": str(row.get("estado") or ""),
                "detalle": str(row.get("detalle") or ""),
                "created_at": int(row.get("created_at_epoch") or 0),
                "expires_at": int(row.get("expires_at_epoch") or 0),
                "atendido_por": row.get("atendido_por") or "",
                "atendido_en": str(row.get("atendido_en") or ""),
                "consumido_en": str(row.get("consumido_en") or ""),
                "fecha": str(row.get("fecha") or ""),
                "actualizado_en": str(row.get("actualizado_en") or ""),
            }
            for row in rows
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/automatizacion/historial", response_model=list[AutomationMovimientoOut])
def automatizacion_historial(
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(get_current_user),
):
    asegurar_tabla_automatizacion_movimientos()
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a MySQL")
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, request_id, site_name, usuario_id, usuario, rol, accion,
                   detalle, ip, user_agent, fecha
            FROM automatizacion_movimientos
            ORDER BY fecha DESC, id DESC
            LIMIT %s
            """,
            (int(limit),),
        )
        rows = cur.fetchall()
        cur.close()
        return [
            AutomationMovimientoOut(
                id=int(row["id"]),
                request_id=row.get("request_id"),
                site_name=row.get("site_name"),
                usuario_id=row.get("usuario_id"),
                usuario=row.get("usuario"),
                rol=row.get("rol"),
                accion=str(row.get("accion") or ""),
                detalle=row.get("detalle"),
                ip=row.get("ip"),
                user_agent=row.get("user_agent"),
                fecha=str(row.get("fecha") or ""),
            )
            for row in rows
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/automatizacion/product-code-associations", response_model=list[AutomationProductCodeOut])
def automatizacion_product_code_associations(
    request: Request,
    plataforma: str = Query("", max_length=80),
):
    require_automation_internal_key(request)
    rows = listar_codigos_producto_automatizacion(plataforma or None)
    return [
        AutomationProductCodeOut(
            id=int(row["id"]) if row.get("id") is not None else None,
            plataforma=str(row.get("plataforma") or ""),
            descripcion_producto=str(row.get("descripcion_producto") or ""),
            descripcion_normalizada=str(row.get("descripcion_normalizada") or ""),
            codigo_interno=str(row.get("codigo_interno") or ""),
            descripcion_galactico=str(row.get("descripcion_galactico") or ""),
            lleva_iva=bool(row.get("lleva_iva")),
            piezas=float(row.get("piezas") or 1),
            fuente=str(row.get("fuente") or ""),
            fecha=str(row.get("fecha") or ""),
            actualizado_en=str(row.get("actualizado_en") or ""),
        )
        for row in rows
    ]


@app.post("/automatizacion/product-code-associations", response_model=AutomationProductCodeOut)
def automatizacion_guardar_product_code_association(
    payload: AutomationProductCodeIn,
    request: Request,
):
    require_automation_internal_key(request)
    saved = guardar_codigo_producto_automatizacion(
        plataforma=payload.plataforma,
        descripcion_producto=payload.descripcion_producto,
        codigo_interno=payload.codigo_interno,
        descripcion_galactico=payload.descripcion_galactico,
        lleva_iva=payload.lleva_iva,
        piezas=payload.piezas,
        fuente=payload.fuente,
    )
    registrar_movimiento_automatizacion(
        "CODIGO_PRODUCTO_GUARDADO",
        site_name=saved["plataforma"],
        detalle=f"{saved['descripcion_producto']} -> {saved['codigo_interno'] or 'sin codigo'}",
        request=request,
    )
    return AutomationProductCodeOut(**saved)


@app.delete("/automatizacion/product-code-associations/{item_id}")
def automatizacion_eliminar_product_code_association(
    item_id: int,
    request: Request,
):
    require_automation_internal_key(request)
    removed = eliminar_codigo_producto_automatizacion(item_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Asociacion no encontrada")
    registrar_movimiento_automatizacion(
        "CODIGO_PRODUCTO_ELIMINADO",
        detalle=f"Asociacion eliminada: {item_id}",
        request=request,
    )
    return {"ok": True}


@app.get("/login-web", response_class=HTMLResponse)
def login_web_page():
    return _serve_web_html("login.html")


@app.get("/portal", response_class=HTMLResponse)
def portal_comandas_page():
    """Punto de entrada integrado para las funciones web de Comandas."""
    return HTMLResponse("""<!doctype html><html lang='es'><head><meta charset='utf-8'>
    <meta name='viewport' content='width=device-width,initial-scale=1'><title>Comandas</title>
    <style>body{margin:0;background:#f3f6fa;font-family:Arial,sans-serif;color:#17233a}.wrap{max-width:1100px;margin:auto;padding:32px}.head{margin-bottom:26px}.head h1{margin:0 0 8px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:16px}.card{display:block;text-decoration:none;color:inherit;background:#fff;border:1px solid #dbe3ed;border-radius:12px;padding:20px;box-shadow:0 4px 15px #1d2a3911}.card:hover{border-color:#2365d8;transform:translateY(-1px)}.tag{font-size:12px;color:#2365d8;font-weight:700;text-transform:uppercase}.card h2{font-size:18px;margin:10px 0}.card p{margin:0;line-height:1.45;color:#5b677a}.notice{margin-top:22px;padding:14px;background:#fff8dd;border-radius:8px;color:#6a5200;font-size:14px}</style></head><body><main class='wrap'><div class='head'><div class='tag'>Proyecto Comandas</div><h1>Operación comercial</h1><p>Accesos integrados del proyecto original, aislados dentro de la actualización.</p></div><section class='grid'>
    <a class='card' href='/comandas/crm/'><div class='tag'>CRM</div><h2>Ventas y cotizaciones</h2><p>Prospectos, clientes, cotizaciones, seguimientos, facturas y exportaciones.</p></a>
    <a class='card' href='/comandas/login-web'><div class='tag'>Catálogo</div><h2>Catálogo de productos</h2><p>Consulta de productos, fichas técnicas y generación de catálogos PDF.</p></a>
    <a class='card' href='/comandas/login-web'><div class='tag'>Administración</div><h2>Fichas y bitácora</h2><p>Administración protegida de fichas e imágenes de producto.</p></a>
    </section><p class='notice'>La captura web de la comanda, diario, clientes y vendedores se migrará como pantallas nativas de este menú; el origen los tenía como aplicación de escritorio.</p></main></body></html>""")


@app.get("/catalogo-web", response_class=HTMLResponse)
def catalogo_web_page(sso: str = ""):
    return _serve_web_html("catalogo.html", sso)


@app.get("/admin-fichas", response_class=HTMLResponse)
def admin_fichas_page(sso: str = ""):
    return _serve_web_html("admin_fichas.html", sso)


@app.get("/admin-bitacora", response_class=HTMLResponse)
def admin_bitacora_page(sso: str = ""):
    return _serve_web_html("bitacora.html", sso)


# =========================
# Ejemplo endpoint protegido
# =========================
class PedidoIn(BaseModel):
    vendedor: str
    empresa: str
    cliente_numero: str
    cliente_nombre: str
    observaciones_pedido: str = ""


@app.post("/vendedor/pedido")
def crear_pedido(payload: PedidoIn, user: dict = Depends(get_current_user)):
    return {
        "ok": True,
        "msg": "Pedido recibido",
        "auth_user": {
            "id": user["id"],
            "usuario": user["usuario"],
            "rol": user["rol"],
        },
        "pedido": payload.model_dump(),
    }


# =========================
# CatÃ¡logo (misma lÃ³gica que Comandas)
# =========================
class ClienteOut(BaseModel):
    numero: str
    nombre: str
    empresa: str
    telefono: str = ""
    direccion_entrega: str = ""
    observaciones: str = ""
    calle: str = ""
    no_exterior: str = ""
    no_interior: str = ""
    colonia: str = ""
    alcaldia: str = ""
    municipio: str = ""
    codigo_postal: str = ""
    poblacion: str = ""
    estado: str = ""
    pais: str = ""
    consignatario: str = ""
    consig_calle: str = ""
    consig_no_exterior: str = ""
    consig_no_interior: str = ""
    consig_colonia: str = ""
    consig_delegacion: str = ""
    consig_municipio: str = ""
    consig_codigo_postal: str = ""
    consig_poblacion: str = ""
    consig_estado: str = ""
    consig_pais: str = ""


class VisitaClienteOut(BaseModel):
    empresa: str
    cliente_numero: str
    cliente_nombre: str = ""
    direccion: str = ""
    telefono: str = ""
    calle: str = ""
    no_exterior: str = ""
    no_interior: str = ""
    colonia: str = ""
    alcaldia: str = ""
    municipio: str = ""
    codigo_postal: str = ""
    poblacion: str = ""
    estado: str = ""
    pais: str = ""
    consignatario: str = ""
    consig_calle: str = ""
    consig_no_exterior: str = ""
    consig_no_interior: str = ""
    consig_colonia: str = ""
    consig_delegacion: str = ""
    consig_municipio: str = ""
    consig_codigo_postal: str = ""
    consig_poblacion: str = ""
    consig_estado: str = ""
    consig_pais: str = ""
    horarios_pago_desde: str = ""
    horarios_pago_hasta: str = ""
    dia_pago: str = ""
    forma_pago: str = ""
    horarios_revision_desde: str = ""
    horarios_revision_hasta: str = ""
    dia_revision: str = ""
    compras_nombre: str = ""
    compras_telefono: str = ""
    recibo_nombre: str = ""
    recibo_telefono: str = ""
    gerente_nombre: str = ""
    gerente_telefono: str = ""
    observaciones_visita: str = ""
    pedido_realizado_visita: str = ""
    solicitud_modificacion_datos: str = ""


class VisitaClienteSaveIn(BaseModel):
    empresa: str
    cliente_numero: str
    cliente_nombre: str = ""
    direccion: str = ""
    telefono: str = ""
    horarios_pago_desde: str = ""
    horarios_pago_hasta: str = ""
    dia_pago: str = ""
    forma_pago: str = ""
    horarios_revision_desde: str = ""
    horarios_revision_hasta: str = ""
    dia_revision: str = ""
    compras_nombre: str = ""
    compras_telefono: str = ""
    recibo_nombre: str = ""
    recibo_telefono: str = ""
    gerente_nombre: str = ""
    gerente_telefono: str = ""
    observaciones_visita: str = ""
    pedido_realizado_visita: str = ""
    solicitud_modificacion_datos: str = ""


class VisitaClienteSaveOut(BaseModel):
    ok: bool
    mensaje: str
    cambios: int = 0


class ListaPrecioOut(BaseModel):
    id: Optional[int] = None
    nombre: str


class UsuarioCatalogoOut(BaseModel):
    id: int
    usuario: str
    rol: str = ""


class ClienteAltaIn(BaseModel):
    nombre: str
    empresa: str
    numero_cliente_sugerido: str = ""
    razon_social: str = ""
    calle: str = ""
    no_exterior: str = ""
    no_interior: str = ""
    colonia: str = ""
    alcaldia: str = ""
    municipio: str = ""
    codigo_postal: str = ""
    poblacion: str = ""
    estado: str = ""
    pais: str = ""
    rfc: str = ""
    telefono: str = ""
    correo_electronico: str = ""
    contacto1: str = ""
    contacto2: str = ""
    dias_credito: int = 0
    consignatario: str = ""
    consig_calle: str = ""
    consig_no_exterior: str = ""
    consig_no_interior: str = ""
    consig_colonia: str = ""
    consig_delegacion: str = ""
    consig_municipio: str = ""
    consig_codigo_postal: str = ""
    consig_poblacion: str = ""
    consig_estado: str = ""
    consig_pais: str = ""
    zona: str = ""
    no_proveedor: str = ""
    agente: str = ""
    descuento: float = 0.0
    especial: str = ""
    tipo: str = ""
    vendedor: str = ""
    direccion_entrega: str = ""
    observaciones: str = ""
    horarios_pago_desde: str = ""
    horarios_pago_hasta: str = ""
    dia_pago: str = ""
    forma_pago: str = ""
    horarios_revision_desde: str = ""
    horarios_revision_hasta: str = ""
    dia_revision: str = ""
    compras_nombre: str = ""
    compras_telefono: str = ""
    recibo_nombre: str = ""
    recibo_telefono: str = ""
    gerente_nombre: str = ""
    gerente_telefono: str = ""
    observaciones_visita: str = ""
    pedido_realizado_visita: str = ""


class ClienteAltaOut(BaseModel):
    ok: bool
    prealta_id: int
    empresa: str
    estatus: str
    mensaje: str


class ClienteDocumentoUploadOut(BaseModel):
    ok: bool
    prealta_id: int
    guardados: list[str] = Field(default_factory=list)
    mensaje: str


class ProductoOut(BaseModel):
    cip: str
    descripcion: str
    unidad: str = ""
    badge_1: Optional[str] = None
    badge_2: Optional[str] = None
    badge_3: Optional[str] = None
    etiquetas_retail: list[str] = Field(default_factory=list)
    premium_sort: int = 0
    premium_activo: int = 1


@app.get("/catalog/empresas", response_model=List[str])
def catalog_empresas(user: dict = Depends(get_current_user)):
    conn = conectar_mysql()
    try:
        cur = conn.cursor()
        empresas = set()

        cur.execute(
            """
            SELECT DISTINCT empresa
            FROM clientes
            WHERE empresa IS NOT NULL AND TRIM(empresa) <> ''
            """
        )
        for r in (cur.fetchall() or []):
            txt = normalizar_nombre_empresa_catalogo(str(r[0] or "").strip())
            if txt:
                empresas.add(txt)

        try:
            cur.execute(
                """
                SELECT DISTINCT empresa
                FROM productos_ficha
                WHERE empresa IS NOT NULL AND TRIM(empresa) <> ''
                """
            )
            for r in (cur.fetchall() or []):
                txt = normalizar_nombre_empresa_catalogo(str(r[0] or "").strip())
                if txt:
                    empresas.add(txt)
        except Exception:
            pass

        # Aseguramos que las empresas de catálogo clave siempre aparezcan aunque
        # su origen no esté todavía completo en clientes.
        empresas.update(["Gourmet España", "EZA2007", "Alimentos Europeos"])
        return sorted(empresas, key=lambda x: x.lower())
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/catalog/listas-precios", response_model=List[ListaPrecioOut])
def catalog_listas_precios(user: dict = Depends(get_current_user)):
    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, nombre
            FROM listas_precios
            WHERE nombre IS NOT NULL AND TRIM(nombre) <> ''
            ORDER BY nombre
            """
        )
        rows = cur.fetchall() or []
        return [
            ListaPrecioOut(
                id=int(r["id"]) if r.get("id") is not None else None,
                nombre=str(r.get("nombre") or "").strip(),
            )
            for r in rows
            if str(r.get("nombre") or "").strip()
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/catalog/vendedores", response_model=List[UsuarioCatalogoOut])
def catalog_vendedores(user: dict = Depends(get_current_user)):
    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, nombre
            FROM vendedores
            ORDER BY id ASC
            """,
        )
        rows = cur.fetchall() or []
        return [
            UsuarioCatalogoOut(
                id=int(r.get("id") or 0),
                usuario=str(r.get("nombre") or "").strip(),
                rol="vendedor",
            )
            for r in rows
            if str(r.get("nombre") or "").strip()
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/catalog/clientes/search", response_model=List[ClienteOut])
def buscar_clientes_catalogo(
    empresa: str,
    q: str = "",
    limit: int = 25,
    user: dict = Depends(get_current_user),
):
    empresa = (empresa or "").strip()
    q = (q or "").strip()
    limit = max(1, min(100, int(limit)))

    if not empresa:
        raise HTTPException(status_code=400, detail="Falta empresa")

    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)

        like_q = f"%{q}%"
        if q:
            cur.execute(
                """
                SELECT numero, nombre, empresa, IFNULL(telefono,'') AS telefono,
                       IFNULL(direccion_entrega,'') AS direccion_entrega,
                       IFNULL(observaciones,'') AS observaciones,
                       IFNULL(calle,'') AS calle,
                       IFNULL(no_exterior,'') AS no_exterior,
                       IFNULL(no_interior,'') AS no_interior,
                       IFNULL(colonia,'') AS colonia,
                       IFNULL(alcaldia,'') AS alcaldia,
                       IFNULL(municipio,'') AS municipio,
                       IFNULL(codigo_postal,'') AS codigo_postal,
                       IFNULL(poblacion,'') AS poblacion,
                       IFNULL(estado,'') AS estado,
                       IFNULL(pais,'') AS pais,
                       IFNULL(consignatario,'') AS consignatario,
                       IFNULL(consig_calle,'') AS consig_calle,
                       IFNULL(consig_no_exterior,'') AS consig_no_exterior,
                       IFNULL(consig_no_interior,'') AS consig_no_interior,
                       IFNULL(consig_colonia,'') AS consig_colonia,
                       IFNULL(consig_delegacion,'') AS consig_delegacion,
                       IFNULL(consig_municipio,'') AS consig_municipio,
                       IFNULL(consig_codigo_postal,'') AS consig_codigo_postal,
                       IFNULL(consig_poblacion,'') AS consig_poblacion,
                       IFNULL(consig_estado,'') AS consig_estado,
                       IFNULL(consig_pais,'') AS consig_pais
                FROM clientes
                WHERE empresa = %s
                  AND (numero LIKE %s OR nombre LIKE %s)
                ORDER BY numero
                LIMIT %s
                """,
                (empresa, like_q, like_q, limit),
            )
        else:
            cur.execute(
                """
                SELECT numero, nombre, empresa, IFNULL(telefono,'') AS telefono,
                       IFNULL(direccion_entrega,'') AS direccion_entrega,
                       IFNULL(observaciones,'') AS observaciones,
                       IFNULL(calle,'') AS calle,
                       IFNULL(no_exterior,'') AS no_exterior,
                       IFNULL(no_interior,'') AS no_interior,
                       IFNULL(colonia,'') AS colonia,
                       IFNULL(alcaldia,'') AS alcaldia,
                       IFNULL(municipio,'') AS municipio,
                       IFNULL(codigo_postal,'') AS codigo_postal,
                       IFNULL(poblacion,'') AS poblacion,
                       IFNULL(estado,'') AS estado,
                       IFNULL(pais,'') AS pais,
                       IFNULL(consignatario,'') AS consignatario,
                       IFNULL(consig_calle,'') AS consig_calle,
                       IFNULL(consig_no_exterior,'') AS consig_no_exterior,
                       IFNULL(consig_no_interior,'') AS consig_no_interior,
                       IFNULL(consig_colonia,'') AS consig_colonia,
                       IFNULL(consig_delegacion,'') AS consig_delegacion,
                       IFNULL(consig_municipio,'') AS consig_municipio,
                       IFNULL(consig_codigo_postal,'') AS consig_codigo_postal,
                       IFNULL(consig_poblacion,'') AS consig_poblacion,
                       IFNULL(consig_estado,'') AS consig_estado,
                       IFNULL(consig_pais,'') AS consig_pais
                FROM clientes
                WHERE empresa = %s
                ORDER BY numero
                LIMIT %s
                """,
                (empresa, limit),
            )

        rows = cur.fetchall() or []
        return [
            ClienteOut(
                numero=str(r["numero"] or ""),
                nombre=str(r["nombre"] or ""),
                empresa=str(r["empresa"] or ""),
                telefono=str(r.get("telefono") or ""),
                direccion_entrega=str(r.get("direccion_entrega") or ""),
                observaciones=str(r.get("observaciones") or ""),
                calle=str(r.get("calle") or ""),
                no_exterior=str(r.get("no_exterior") or ""),
                no_interior=str(r.get("no_interior") or ""),
                colonia=str(r.get("colonia") or ""),
                alcaldia=str(r.get("alcaldia") or ""),
                municipio=str(r.get("municipio") or ""),
                codigo_postal=str(r.get("codigo_postal") or ""),
                poblacion=str(r.get("poblacion") or ""),
                estado=str(r.get("estado") or ""),
                pais=str(r.get("pais") or ""),
                consignatario=str(r.get("consignatario") or ""),
                consig_calle=str(r.get("consig_calle") or ""),
                consig_no_exterior=str(r.get("consig_no_exterior") or ""),
                consig_no_interior=str(r.get("consig_no_interior") or ""),
                consig_colonia=str(r.get("consig_colonia") or ""),
                consig_delegacion=str(r.get("consig_delegacion") or ""),
                consig_municipio=str(r.get("consig_municipio") or ""),
                consig_codigo_postal=str(r.get("consig_codigo_postal") or ""),
                consig_poblacion=str(r.get("consig_poblacion") or ""),
                consig_estado=str(r.get("consig_estado") or ""),
                consig_pais=str(r.get("consig_pais") or ""),
            )
            for r in rows
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/catalog/clientes/by-numero", response_model=ClienteOut)
def cliente_por_numero(
    empresa: str,
    numero: str,
    user: dict = Depends(get_current_user),
):
    empresa = (empresa or "").strip()
    numero = (numero or "").strip()
    if not empresa or not numero:
        raise HTTPException(status_code=400, detail="Faltan empresa o numero")

    numero_normalizado = _normalizar_numero_cliente(numero)

    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        if numero_normalizado is not None:
            cur.execute(
                """
                SELECT numero, nombre, empresa,
                       IFNULL(telefono,'') AS telefono,
                       IFNULL(direccion_entrega,'') AS direccion_entrega,
                       IFNULL(observaciones,'') AS observaciones,
                       IFNULL(calle,'') AS calle,
                       IFNULL(no_exterior,'') AS no_exterior,
                       IFNULL(no_interior,'') AS no_interior,
                       IFNULL(colonia,'') AS colonia,
                       IFNULL(alcaldia,'') AS alcaldia,
                       IFNULL(municipio,'') AS municipio,
                       IFNULL(codigo_postal,'') AS codigo_postal,
                       IFNULL(poblacion,'') AS poblacion,
                       IFNULL(estado,'') AS estado,
                       IFNULL(pais,'') AS pais,
                       IFNULL(consignatario,'') AS consignatario,
                       IFNULL(consig_calle,'') AS consig_calle,
                       IFNULL(consig_no_exterior,'') AS consig_no_exterior,
                       IFNULL(consig_no_interior,'') AS consig_no_interior,
                       IFNULL(consig_colonia,'') AS consig_colonia,
                       IFNULL(consig_delegacion,'') AS consig_delegacion,
                       IFNULL(consig_municipio,'') AS consig_municipio,
                       IFNULL(consig_codigo_postal,'') AS consig_codigo_postal,
                       IFNULL(consig_poblacion,'') AS consig_poblacion,
                       IFNULL(consig_estado,'') AS consig_estado,
                       IFNULL(consig_pais,'') AS consig_pais
                FROM clientes
                WHERE TRIM(empresa) = TRIM(%s)
                  AND (
                        TRIM(numero) = TRIM(%s)
                        OR CAST(TRIM(numero) AS UNSIGNED) = %s
                  )
                ORDER BY CASE WHEN TRIM(numero) = TRIM(%s) THEN 0 ELSE 1 END, id ASC
                LIMIT 1
                """,
                (empresa, numero, numero_normalizado, numero),
            )
        else:
            cur.execute(
                """
                SELECT numero, nombre, empresa,
                       IFNULL(telefono,'') AS telefono,
                       IFNULL(direccion_entrega,'') AS direccion_entrega,
                       IFNULL(observaciones,'') AS observaciones,
                       IFNULL(calle,'') AS calle,
                       IFNULL(no_exterior,'') AS no_exterior,
                       IFNULL(no_interior,'') AS no_interior,
                       IFNULL(colonia,'') AS colonia,
                       IFNULL(alcaldia,'') AS alcaldia,
                       IFNULL(municipio,'') AS municipio,
                       IFNULL(codigo_postal,'') AS codigo_postal,
                       IFNULL(poblacion,'') AS poblacion,
                       IFNULL(estado,'') AS estado,
                       IFNULL(pais,'') AS pais,
                       IFNULL(consignatario,'') AS consignatario,
                       IFNULL(consig_calle,'') AS consig_calle,
                       IFNULL(consig_no_exterior,'') AS consig_no_exterior,
                       IFNULL(consig_no_interior,'') AS consig_no_interior,
                       IFNULL(consig_colonia,'') AS consig_colonia,
                       IFNULL(consig_delegacion,'') AS consig_delegacion,
                       IFNULL(consig_municipio,'') AS consig_municipio,
                       IFNULL(consig_codigo_postal,'') AS consig_codigo_postal,
                       IFNULL(consig_poblacion,'') AS consig_poblacion,
                       IFNULL(consig_estado,'') AS consig_estado,
                       IFNULL(consig_pais,'') AS consig_pais
                FROM clientes
                WHERE TRIM(empresa) = TRIM(%s)
                  AND TRIM(numero) = TRIM(%s)
                LIMIT 1
                """,
                (empresa, numero),
            )
        r = cur.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        return ClienteOut(
            numero=str(r["numero"] or ""),
            nombre=str(r["nombre"] or ""),
            empresa=str(r["empresa"] or ""),
            telefono=str(r.get("telefono") or ""),
            direccion_entrega=str(r.get("direccion_entrega") or ""),
            observaciones=str(r.get("observaciones") or ""),
            calle=str(r.get("calle") or ""),
            no_exterior=str(r.get("no_exterior") or ""),
            no_interior=str(r.get("no_interior") or ""),
            colonia=str(r.get("colonia") or ""),
            alcaldia=str(r.get("alcaldia") or ""),
            municipio=str(r.get("municipio") or ""),
            codigo_postal=str(r.get("codigo_postal") or ""),
            poblacion=str(r.get("poblacion") or ""),
            estado=str(r.get("estado") or ""),
            pais=str(r.get("pais") or ""),
            consignatario=str(r.get("consignatario") or ""),
            consig_calle=str(r.get("consig_calle") or ""),
            consig_no_exterior=str(r.get("consig_no_exterior") or ""),
            consig_no_interior=str(r.get("consig_no_interior") or ""),
            consig_colonia=str(r.get("consig_colonia") or ""),
            consig_delegacion=str(r.get("consig_delegacion") or ""),
            consig_municipio=str(r.get("consig_municipio") or ""),
            consig_codigo_postal=str(r.get("consig_codigo_postal") or ""),
            consig_poblacion=str(r.get("consig_poblacion") or ""),
            consig_estado=str(r.get("consig_estado") or ""),
            consig_pais=str(r.get("consig_pais") or ""),
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/visitas/clientes/{numero}", response_model=VisitaClienteOut)
def obtener_visita_cliente(
    numero: str,
    empresa: str,
    user: dict = Depends(get_current_user),
):
    asegurar_tablas_visitas_clientes()
    empresa = (empresa or "").strip()
    numero = (numero or "").strip()
    if not empresa or not numero:
        raise HTTPException(status_code=400, detail="Faltan empresa o numero")
    numero_normalizado = _normalizar_numero_cliente(numero)

    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a MySQL")
    try:
        cur = conn.cursor(dictionary=True)
        if numero_normalizado is not None:
            cur.execute(
                """
                SELECT
                    c.empresa,
                    c.numero AS cliente_numero,
                    c.nombre AS cliente_nombre,
                    IFNULL(c.direccion_entrega, '') AS direccion,
                    IFNULL(c.telefono, '') AS telefono,
                    IFNULL(c.calle, '') AS calle,
                    IFNULL(c.no_exterior, '') AS no_exterior,
                    IFNULL(c.no_interior, '') AS no_interior,
                    IFNULL(c.colonia, '') AS colonia,
                    IFNULL(c.alcaldia, '') AS alcaldia,
                    IFNULL(c.municipio, '') AS municipio,
                    IFNULL(c.codigo_postal, '') AS codigo_postal,
                    IFNULL(c.poblacion, '') AS poblacion,
                    IFNULL(c.estado, '') AS estado,
                    IFNULL(c.pais, '') AS pais,
                    IFNULL(c.consignatario, '') AS consignatario,
                    IFNULL(c.consig_calle, '') AS consig_calle,
                    IFNULL(c.consig_no_exterior, '') AS consig_no_exterior,
                    IFNULL(c.consig_no_interior, '') AS consig_no_interior,
                    IFNULL(c.consig_colonia, '') AS consig_colonia,
                    IFNULL(c.consig_delegacion, '') AS consig_delegacion,
                    IFNULL(c.consig_municipio, '') AS consig_municipio,
                    IFNULL(c.consig_codigo_postal, '') AS consig_codigo_postal,
                    IFNULL(c.consig_poblacion, '') AS consig_poblacion,
                    IFNULL(c.consig_estado, '') AS consig_estado,
                    IFNULL(c.consig_pais, '') AS consig_pais,
                    IFNULL(v.horarios_pago_desde, '') AS horarios_pago_desde,
                    IFNULL(v.horarios_pago_hasta, '') AS horarios_pago_hasta,
                    IFNULL(v.dia_pago, '') AS dia_pago,
                    IFNULL(v.forma_pago, '') AS forma_pago,
                    IFNULL(v.horarios_revision_desde, '') AS horarios_revision_desde,
                    IFNULL(v.horarios_revision_hasta, '') AS horarios_revision_hasta,
                    IFNULL(v.dia_revision, '') AS dia_revision,
                    IFNULL(v.compras_nombre, '') AS compras_nombre,
                    IFNULL(v.compras_telefono, '') AS compras_telefono,
                    IFNULL(v.recibo_nombre, '') AS recibo_nombre,
                    IFNULL(v.recibo_telefono, '') AS recibo_telefono,
                    IFNULL(v.gerente_nombre, '') AS gerente_nombre,
                    IFNULL(v.gerente_telefono, '') AS gerente_telefono,
                    IFNULL(v.observaciones_visita, '') AS observaciones_visita,
                    IFNULL(v.pedido_realizado_visita, '') AS pedido_realizado_visita,
                    IFNULL((
                        SELECT sm.solicitud_texto
                        FROM clientes_solicitudes_modificacion sm
                        WHERE UPPER(TRIM(sm.empresa)) = UPPER(TRIM(c.empresa))
                          AND TRIM(sm.cliente_numero) = TRIM(CAST(c.numero AS CHAR))
                          AND UPPER(TRIM(sm.estado)) = 'PENDIENTE'
                        ORDER BY sm.fecha_solicitud DESC, sm.id DESC
                        LIMIT 1
                    ), '') AS solicitud_modificacion_datos
                FROM clientes c
                LEFT JOIN clientes_visitas v
                  ON v.empresa = c.empresa AND v.cliente_numero = c.numero
                WHERE TRIM(c.empresa) = TRIM(%s)
                  AND (
                        TRIM(c.numero) = TRIM(%s)
                        OR CAST(TRIM(c.numero) AS UNSIGNED) = %s
                  )
                ORDER BY CASE WHEN TRIM(c.numero) = TRIM(%s) THEN 0 ELSE 1 END, c.id ASC
                LIMIT 1
                """,
                (empresa, numero, numero_normalizado, numero),
            )
        else:
            cur.execute(
                """
                SELECT
                    c.empresa,
                    c.numero AS cliente_numero,
                    c.nombre AS cliente_nombre,
                    IFNULL(c.direccion_entrega, '') AS direccion,
                    IFNULL(c.telefono, '') AS telefono,
                    IFNULL(c.calle, '') AS calle,
                    IFNULL(c.no_exterior, '') AS no_exterior,
                    IFNULL(c.no_interior, '') AS no_interior,
                    IFNULL(c.colonia, '') AS colonia,
                    IFNULL(c.alcaldia, '') AS alcaldia,
                    IFNULL(c.municipio, '') AS municipio,
                    IFNULL(c.codigo_postal, '') AS codigo_postal,
                    IFNULL(c.poblacion, '') AS poblacion,
                    IFNULL(c.estado, '') AS estado,
                    IFNULL(c.pais, '') AS pais,
                    IFNULL(c.consignatario, '') AS consignatario,
                    IFNULL(c.consig_calle, '') AS consig_calle,
                    IFNULL(c.consig_no_exterior, '') AS consig_no_exterior,
                    IFNULL(c.consig_no_interior, '') AS consig_no_interior,
                    IFNULL(c.consig_colonia, '') AS consig_colonia,
                    IFNULL(c.consig_delegacion, '') AS consig_delegacion,
                    IFNULL(c.consig_municipio, '') AS consig_municipio,
                    IFNULL(c.consig_codigo_postal, '') AS consig_codigo_postal,
                    IFNULL(c.consig_poblacion, '') AS consig_poblacion,
                    IFNULL(c.consig_estado, '') AS consig_estado,
                    IFNULL(c.consig_pais, '') AS consig_pais,
                    IFNULL(v.horarios_pago_desde, '') AS horarios_pago_desde,
                    IFNULL(v.horarios_pago_hasta, '') AS horarios_pago_hasta,
                    IFNULL(v.dia_pago, '') AS dia_pago,
                    IFNULL(v.forma_pago, '') AS forma_pago,
                    IFNULL(v.horarios_revision_desde, '') AS horarios_revision_desde,
                    IFNULL(v.horarios_revision_hasta, '') AS horarios_revision_hasta,
                    IFNULL(v.dia_revision, '') AS dia_revision,
                    IFNULL(v.compras_nombre, '') AS compras_nombre,
                    IFNULL(v.compras_telefono, '') AS compras_telefono,
                    IFNULL(v.recibo_nombre, '') AS recibo_nombre,
                    IFNULL(v.recibo_telefono, '') AS recibo_telefono,
                    IFNULL(v.gerente_nombre, '') AS gerente_nombre,
                    IFNULL(v.gerente_telefono, '') AS gerente_telefono,
                    IFNULL(v.observaciones_visita, '') AS observaciones_visita,
                    IFNULL(v.pedido_realizado_visita, '') AS pedido_realizado_visita,
                    IFNULL((
                        SELECT sm.solicitud_texto
                        FROM clientes_solicitudes_modificacion sm
                        WHERE UPPER(TRIM(sm.empresa)) = UPPER(TRIM(c.empresa))
                          AND TRIM(sm.cliente_numero) = TRIM(CAST(c.numero AS CHAR))
                          AND UPPER(TRIM(sm.estado)) = 'PENDIENTE'
                        ORDER BY sm.fecha_solicitud DESC, sm.id DESC
                        LIMIT 1
                    ), '') AS solicitud_modificacion_datos
                FROM clientes c
                LEFT JOIN clientes_visitas v
                  ON v.empresa = c.empresa AND v.cliente_numero = c.numero
                WHERE TRIM(c.empresa) = TRIM(%s) AND TRIM(c.numero) = TRIM(%s)
                LIMIT 1
                """,
                (empresa, numero),
            )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        return VisitaClienteOut(**{k: ("" if v is None else str(v)) for k, v in row.items()})
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.post("/visitas/clientes/{numero}", response_model=VisitaClienteSaveOut)
def guardar_visita_cliente(
    numero: str,
    payload: VisitaClienteSaveIn,
    user: dict = Depends(get_current_user),
):
    asegurar_tablas_visitas_clientes()
    empresa = (payload.empresa or "").strip()
    numero = (numero or payload.cliente_numero or "").strip()
    if not empresa or not numero:
        raise HTTPException(status_code=400, detail="Faltan empresa o numero")

    campos = {
        "cliente_nombre": (payload.cliente_nombre or "").strip(),
        "direccion": (payload.direccion or "").strip(),
        "telefono": (payload.telefono or "").strip(),
        "horarios_pago_desde": (payload.horarios_pago_desde or "").strip(),
        "horarios_pago_hasta": (payload.horarios_pago_hasta or "").strip(),
        "dia_pago": (payload.dia_pago or "").strip(),
        "forma_pago": (payload.forma_pago or "").strip(),
        "horarios_revision_desde": (payload.horarios_revision_desde or "").strip(),
        "horarios_revision_hasta": (payload.horarios_revision_hasta or "").strip(),
        "dia_revision": (payload.dia_revision or "").strip(),
        "compras_nombre": (payload.compras_nombre or "").strip(),
        "compras_telefono": (payload.compras_telefono or "").strip(),
        "recibo_nombre": (payload.recibo_nombre or "").strip(),
        "recibo_telefono": (payload.recibo_telefono or "").strip(),
        "gerente_nombre": (payload.gerente_nombre or "").strip(),
        "gerente_telefono": (payload.gerente_telefono or "").strip(),
        "observaciones_visita": (payload.observaciones_visita or "").strip(),
        "pedido_realizado_visita": (payload.pedido_realizado_visita or "").strip(),
        "solicitud_modificacion_datos": (payload.solicitud_modificacion_datos or "").strip(),
    }

    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a MySQL")
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT numero, nombre, empresa, IFNULL(direccion_entrega,'') AS direccion_entrega,
                   IFNULL(telefono,'') AS telefono
            FROM clientes
            WHERE empresa = %s AND numero = %s
            LIMIT 1
            """,
            (empresa, numero),
        )
        cliente = cur.fetchone()
        if not cliente:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        cur.execute(
            """
            SELECT *
            FROM clientes_visitas
            WHERE empresa = %s AND cliente_numero = %s
            LIMIT 1
            """,
            (empresa, numero),
        )
        anterior = cur.fetchone() or {}

        cambios = []
        comparables = dict(campos)
        comparables["direccion"] = campos["direccion"]
        comparables["telefono"] = campos["telefono"]

        for campo, nuevo in comparables.items():
            viejo = str(anterior.get(campo) or "")
            if campo == "direccion" and not anterior:
                viejo = str(cliente.get("direccion_entrega") or "")
            if campo == "telefono" and not anterior:
                viejo = str(cliente.get("telefono") or "")
            if viejo != nuevo:
                cambios.append((campo, viejo, nuevo))

        nombre_cliente_final = campos["cliente_nombre"] or str(cliente.get("nombre") or "")

        cur.execute(
            """
            INSERT INTO clientes_visitas (
                empresa, cliente_numero, cliente_nombre, direccion, telefono,
                horarios_pago_desde, horarios_pago_hasta, dia_pago, forma_pago,
                horarios_revision_desde, horarios_revision_hasta, dia_revision,
                compras_nombre, compras_telefono, recibo_nombre, recibo_telefono,
                gerente_nombre, gerente_telefono, observaciones_visita, pedido_realizado_visita,
                creado_por, actualizado_por
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON DUPLICATE KEY UPDATE
                cliente_nombre = VALUES(cliente_nombre),
                direccion = VALUES(direccion),
                telefono = VALUES(telefono),
                horarios_pago_desde = VALUES(horarios_pago_desde),
                horarios_pago_hasta = VALUES(horarios_pago_hasta),
                dia_pago = VALUES(dia_pago),
                forma_pago = VALUES(forma_pago),
                horarios_revision_desde = VALUES(horarios_revision_desde),
                horarios_revision_hasta = VALUES(horarios_revision_hasta),
                dia_revision = VALUES(dia_revision),
                compras_nombre = VALUES(compras_nombre),
                compras_telefono = VALUES(compras_telefono),
                recibo_nombre = VALUES(recibo_nombre),
                recibo_telefono = VALUES(recibo_telefono),
                gerente_nombre = VALUES(gerente_nombre),
                gerente_telefono = VALUES(gerente_telefono),
                observaciones_visita = VALUES(observaciones_visita),
                pedido_realizado_visita = VALUES(pedido_realizado_visita),
                actualizado_por = VALUES(actualizado_por)
            """,
            (
                empresa, numero, nombre_cliente_final, campos["direccion"], campos["telefono"],
                campos["horarios_pago_desde"], campos["horarios_pago_hasta"], campos["dia_pago"], campos["forma_pago"],
                campos["horarios_revision_desde"], campos["horarios_revision_hasta"], campos["dia_revision"],
                campos["compras_nombre"], campos["compras_telefono"], campos["recibo_nombre"], campos["recibo_telefono"],
                campos["gerente_nombre"], campos["gerente_telefono"], campos["observaciones_visita"], campos["pedido_realizado_visita"],
                str(user.get("usuario") or ""), str(user.get("usuario") or "")
            ),
        )

        cur.execute(
            """
            UPDATE clientes
            SET nombre = %s,
                direccion_entrega = %s,
                telefono = %s
            WHERE empresa = %s AND numero = %s
            """,
            (nombre_cliente_final, campos["direccion"], campos["telefono"], empresa, numero),
        )

        for campo, viejo, nuevo in cambios:
            cur.execute(
                """
                INSERT INTO clientes_visitas_historial
                    (empresa, cliente_numero, campo, valor_anterior, valor_nuevo, cambiado_por)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (empresa, numero, campo, viejo, nuevo, str(user.get("usuario") or "")),
            )

        solicitud_texto = campos["solicitud_modificacion_datos"]
        if solicitud_texto:
            cur.execute(
                """
                SELECT id
                FROM clientes_solicitudes_modificacion
                WHERE UPPER(TRIM(empresa)) = UPPER(TRIM(%s))
                  AND TRIM(cliente_numero) = TRIM(%s)
                  AND UPPER(TRIM(estado)) = 'PENDIENTE'
                  AND TRIM(solicitud_texto) = TRIM(%s)
                LIMIT 1
                """,
                (empresa, numero, solicitud_texto),
            )
            solicitud_existente = cur.fetchone()
            if not solicitud_existente:
                cur.execute(
                    """
                    INSERT INTO clientes_solicitudes_modificacion
                        (empresa, cliente_numero, cliente_nombre, solicitud_texto, solicitado_por, estado)
                    VALUES (%s, %s, %s, %s, %s, 'PENDIENTE')
                    """,
                    (
                        empresa,
                        numero,
                        nombre_cliente_final,
                        solicitud_texto,
                        str(user.get("usuario") or ""),
                    ),
                )

        conn.commit()
        return VisitaClienteSaveOut(
            ok=True,
            mensaje="Visita guardada correctamente",
            cambios=len(cambios),
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.post("/catalog/clientes/alta", response_model=ClienteAltaOut)
def alta_cliente_catalogo(
    payload: ClienteAltaIn,
    user: dict = Depends(get_current_user),
):
    if not asegurar_tabla_clientes_prealta():
        raise HTTPException(status_code=500, detail="No se pudo preparar la tabla de prealtas")

    data = payload.model_dump()
    data = {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}

    if not data["empresa"]:
        raise HTTPException(status_code=400, detail="La empresa es obligatoria")
    if not data["nombre"]:
        raise HTTPException(status_code=400, detail="El nombre del cliente es obligatorio")

    conn = conectar_mysql()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO clientes_prealta_vendedor (
                empresa, nombre, razon_social, calle, no_exterior, no_interior,
                colonia, alcaldia, municipio, codigo_postal, poblacion, estado, pais, rfc,
                telefono, correo_electronico, contacto1, contacto2, dias_credito,
                consignatario, consig_calle, consig_no_exterior, consig_no_interior,
                consig_colonia, consig_delegacion, consig_municipio, consig_codigo_postal,
                consig_poblacion, consig_estado, consig_pais, zona, no_proveedor, agente,
                descuento, especial, tipo, vendedor, numero_cliente_sugerido,
                direccion_entrega, observaciones,
                horarios_pago_desde, horarios_pago_hasta, dia_pago, forma_pago,
                horarios_revision_desde, horarios_revision_hasta, dia_revision,
                compras_nombre, compras_telefono, recibo_nombre, recibo_telefono,
                gerente_nombre, gerente_telefono, observaciones_visita, pedido_realizado_visita,
                estatus, usuario_alta
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                data["empresa"],
                data["nombre"],
                data["razon_social"],
                data["calle"],
                data["no_exterior"],
                data["no_interior"],
                data["colonia"],
                data["alcaldia"],
                data["municipio"],
                data["codigo_postal"],
                data["poblacion"],
                data["estado"],
                data["pais"],
                data["rfc"],
                data["telefono"],
                data["correo_electronico"],
                data["contacto1"],
                data["contacto2"],
                int(data.get("dias_credito") or 0),
                data["consignatario"],
                data["consig_calle"],
                data["consig_no_exterior"],
                data["consig_no_interior"],
                data["consig_colonia"],
                data["consig_delegacion"],
                data["consig_municipio"],
                data["consig_codigo_postal"],
                data["consig_poblacion"],
                data["consig_estado"],
                data["consig_pais"],
                data["zona"],
                data["no_proveedor"],
                data["agente"],
                float(data.get("descuento") or 0),
                data["especial"],
                data["tipo"],
                data["vendedor"],
                data["numero_cliente_sugerido"],
                data["direccion_entrega"],
                data["observaciones"],
                data["horarios_pago_desde"],
                data["horarios_pago_hasta"],
                data["dia_pago"],
                data["forma_pago"],
                data["horarios_revision_desde"],
                data["horarios_revision_hasta"],
                data["dia_revision"],
                data["compras_nombre"],
                data["compras_telefono"],
                data["recibo_nombre"],
                data["recibo_telefono"],
                data["gerente_nombre"],
                data["gerente_telefono"],
                data["observaciones_visita"],
                data["pedido_realizado_visita"],
                "PENDIENTE",
                str(user.get("usuario") or ""),
            ),
        )
        prealta_id = int(cur.lastrowid)
        conn.commit()
        return ClienteAltaOut(
            ok=True,
            prealta_id=prealta_id,
            empresa=data["empresa"],
            estatus="PENDIENTE",
            mensaje="Prealta guardada correctamente",
        )
    except mysql.connector.Error as e:
        print(f"[PREALTA] Error MySQL guardando prealta: {e}", flush=True)
        print(f"[PREALTA] Payload recibido: {data}", flush=True)
        raise HTTPException(status_code=500, detail=f"MySQL: {e}")
    except Exception as e:
        print(f"[PREALTA] Error general guardando prealta: {type(e).__name__}: {e}", flush=True)
        print(f"[PREALTA] Payload recibido: {data}", flush=True)
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.post("/catalog/clientes/{prealta_id}/documentos", response_model=ClienteDocumentoUploadOut)
def subir_documentos_cliente(
    prealta_id: int,
    comprobante_domicilio: UploadFile | None = FastAPIFile(default=None),
    ine_representante_legal: UploadFile | None = FastAPIFile(default=None),
    acta_constitutiva: UploadFile | None = FastAPIFile(default=None),
    constancia_situacion_fiscal: UploadFile | None = FastAPIFile(default=None),
    user: dict = Depends(get_current_user),
):
    if prealta_id <= 0:
        raise HTTPException(status_code=400, detail="Prealta invalida")

    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, empresa
            FROM clientes_prealta_vendedor
            WHERE id = %s
            LIMIT 1
            """,
            (prealta_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Prealta no encontrada")
        empresa = str(row.get("empresa") or "").strip()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    guardados: list[str] = []
    archivos = {
        "comprobante_domicilio": comprobante_domicilio,
        "ine_representante_legal": ine_representante_legal,
        "acta_constitutiva": acta_constitutiva,
        "constancia_situacion_fiscal": constancia_situacion_fiscal,
    }
    for tipo_documento, archivo in archivos.items():
        if archivo is None:
            continue
        guardar_documento_cliente(
            prealta_id=prealta_id,
            empresa=empresa,
            tipo_documento=tipo_documento,
            archivo=archivo,
            usuario_alta=str(user.get("usuario") or ""),
        )
        guardados.append(tipo_documento)

    return ClienteDocumentoUploadOut(
        ok=True,
        prealta_id=prealta_id,
        guardados=guardados,
        mensaje="Documentos guardados correctamente" if guardados else "No se recibieron documentos",
    )


@app.get("/catalog/clientes/documentos/{documento_id}")
def descargar_documento_cliente(documento_id: int):
    if documento_id <= 0:
        raise HTTPException(status_code=400, detail="Documento invÃ¡lido")

    if not asegurar_tabla_clientes_prealta():
        raise HTTPException(status_code=500, detail="No se pudo preparar la tabla de documentos")

    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a la base de datos")

    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, nombre_original, ruta_archivo, mime_type
            FROM clientes_prealta_documentos
            WHERE id = %s
            LIMIT 1
            """,
            (documento_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Documento no encontrado")

        ruta = str(row.get("ruta_archivo") or "").strip()
        if not ruta or not os.path.exists(ruta):
            raise HTTPException(status_code=404, detail="El archivo del documento no existe en el servidor")

        nombre = str(row.get("nombre_original") or os.path.basename(ruta) or f"documento_{documento_id}")
        mime_type = str(row.get("mime_type") or "").strip() or "application/octet-stream"

        return FileResponse(
            ruta,
            media_type=mime_type,
            filename=nombre,
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/catalog/productos/search", response_model=List[ProductoOut])
def buscar_productos_catalogo(
    q: str = "",
    limit: int = 25,
    user: dict = Depends(get_current_user),
):
    q = (q or "").strip()
    limit = max(1, min(100, int(limit)))

    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)

        sql = """
            SELECT
                p.cip,
                p.descripcion,
                IFNULL(p.unidad,'') AS unidad,
                COALESCE(pf.badge_1, p.badge_1) AS badge_1,
                COALESCE(pf.badge_2, p.badge_2) AS badge_2,
                COALESCE(pf.badge_3, p.badge_3) AS badge_3,
                COALESCE(pf.etiquetas_retail, p.etiquetas_retail) AS etiquetas_retail,
                COALESCE(pf.premium_sort, p.premium_sort, 0) AS premium_sort,
                COALESCE(pf.premium_activo, p.premium_activo, 1) AS premium_activo
            FROM productos p
            LEFT JOIN productos_ficha pf
                ON pf.cip = p.cip
               AND pf.activo = 1
        """

        if q:
            like_q = f"%{q}%"
            starts_q = f"{q}%"

            sql += """
                WHERE
                    p.cip LIKE %s
                    OR p.descripcion LIKE %s
                ORDER BY
                    CASE
                        WHEN p.cip = %s THEN 0
                        WHEN p.descripcion = %s THEN 1
                        WHEN p.cip LIKE %s THEN 2
                        WHEN p.descripcion LIKE %s THEN 3
                        ELSE 4
                    END,
                    CHAR_LENGTH(p.cip) ASC,
                    p.cip ASC,
                    p.descripcion ASC
                LIMIT %s
            """

            cur.execute(
                sql,
                (
                    like_q,      # p.cip LIKE %s
                    like_q,      # p.descripcion LIKE %s
                    q,           # p.cip = %s
                    q,           # p.descripcion = %s
                    starts_q,    # p.cip LIKE %s
                    starts_q,    # p.descripcion LIKE %s
                    limit,
                ),
            )
        else:
            sql += """
                ORDER BY
                    CHAR_LENGTH(p.cip) ASC,
                    p.cip ASC
                LIMIT %s
            """
            cur.execute(sql, (limit,))

        rows = cur.fetchall() or []

        return [
            ProductoOut(
                cip=str(r["cip"] or ""),
                descripcion=str(r["descripcion"] or ""),
                unidad=str(r.get("unidad") or ""),
                badge_1=str(r.get("badge_1") or "") or None,
                badge_2=str(r.get("badge_2") or "") or None,
                badge_3=str(r.get("badge_3") or "") or None,
                etiquetas_retail=csv_a_lista(r.get("etiquetas_retail")),
                premium_sort=int(r.get("premium_sort") or 0),
                premium_activo=int(r.get("premium_activo") or 1),
            )
            for r in rows
        ]

    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/catalog/productos/{cip}", response_model=ProductoOut)
def producto_por_cip(
    cip: str,
    user: dict = Depends(get_current_user),
):
    cip = (cip or "").strip()
    if not cip:
        raise HTTPException(status_code=400, detail="Falta cip")

    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT
                p.cip,
                p.descripcion,
                IFNULL(p.unidad,'') AS unidad,
                COALESCE(pf.badge_1, p.badge_1) AS badge_1,
                COALESCE(pf.badge_2, p.badge_2) AS badge_2,
                COALESCE(pf.badge_3, p.badge_3) AS badge_3,
                COALESCE(pf.etiquetas_retail, p.etiquetas_retail) AS etiquetas_retail,
                COALESCE(pf.premium_sort, p.premium_sort, 0) AS premium_sort,
                COALESCE(pf.premium_activo, p.premium_activo, 1) AS premium_activo
            FROM productos p
            LEFT JOIN productos_ficha pf ON pf.cip = p.cip AND pf.activo = 1
            WHERE p.cip=%s
            LIMIT 1
            """,
            (cip,),
        )
        r = cur.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        return ProductoOut(
            cip=str(r["cip"] or ""),
            descripcion=str(r["descripcion"] or ""),
            unidad=str(r.get("unidad") or ""),
            badge_1=str(r.get("badge_1") or "") or None,
            badge_2=str(r.get("badge_2") or "") or None,
            badge_3=str(r.get("badge_3") or "") or None,
            etiquetas_retail=csv_a_lista(r.get("etiquetas_retail")),
            premium_sort=int(r.get("premium_sort") or 0),
            premium_activo=int(r.get("premium_activo") or 1),
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


# =========================
# Reportes clientes
# =========================
@app.get("/reportes/clientes")
def reporte_clientes(q: str = Query(..., min_length=1)):
    q = (q or "").strip()
    q_like = f"%{q}%"
    q_prefix = f"{q}%"

    conn = conectar_mysql()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                t.numero_cliente,
                t.cliente_nombre,
                t.empresa
            FROM (
                SELECT
                    f.numero_cliente,
                    COALESCE(c.nombre, f.consignatario, '') AS cliente_nombre,
                    f.empresa,
                    CASE
                        WHEN f.numero_cliente = %s THEN 0
                        WHEN COALESCE(c.nombre, f.consignatario, '') = %s THEN 1
                        WHEN COALESCE(f.consignatario, '') = %s THEN 2
                        WHEN f.numero_cliente LIKE %s THEN 3
                        WHEN COALESCE(c.nombre, f.consignatario, '') LIKE %s THEN 4
                        WHEN COALESCE(f.consignatario, '') LIKE %s THEN 5
                        ELSE 6
                    END AS prioridad
                FROM facturas f
                LEFT JOIN clientes c
                    ON c.numero = f.numero_cliente
                   AND c.empresa = f.empresa
                WHERE f.numero_cliente LIKE %s
                   OR COALESCE(c.nombre, f.consignatario, '') LIKE %s
                   OR COALESCE(f.consignatario, '') LIKE %s
            ) t
            GROUP BY t.numero_cliente, t.cliente_nombre, t.empresa
            ORDER BY
                MIN(t.prioridad) ASC,
                t.cliente_nombre ASC,
                t.numero_cliente ASC
            LIMIT 50
            """,
            (q, q, q, q_prefix, q_prefix, q_prefix, q_like, q_like, q_like),
        )

        clientes = cursor.fetchall()
        resultado = []

        for cli in clientes:
            numero_cliente = (cli.get("numero_cliente") or "").strip()
            empresa = (cli.get("empresa") or "").strip()
            cliente_nombre = (cli.get("cliente_nombre") or "").strip()

            cursor.execute(
                """
                SELECT MAX(fecha) AS ultima_compra
                FROM facturas
                WHERE numero_cliente = %s
                  AND empresa = %s
                """,
                (numero_cliente, empresa),
            )
            row_ultima = cursor.fetchone() or {}
            ultima_compra = row_ultima.get("ultima_compra")

            cursor.execute(
                """
                SELECT
                    fd.descripcion,
                    SUM(COALESCE(fd.cantidad, 0)) AS cantidad_total,
                    SUM(COALESCE(fd.piezas, 0)) AS piezas_total
                FROM facturas f
                INNER JOIN factura_detalle fd
                    ON fd.factura_id = f.id
                WHERE f.numero_cliente = %s
                  AND f.empresa = %s
                GROUP BY fd.descripcion
                ORDER BY cantidad_total DESC, piezas_total DESC, fd.descripcion ASC
                """,
                (numero_cliente, empresa),
            )

            productos = cursor.fetchall() or []

            resultado.append(
                {
                    "numero_cliente": numero_cliente,
                    "cliente_nombre": cliente_nombre,
                    "empresa": empresa,
                    "ultima_compra": ultima_compra.isoformat() if ultima_compra else None,
                    "productos": [
                        {
                            "descripcion": (p.get("descripcion") or "").strip(),
                            "cantidad_total": float(p.get("cantidad_total") or 0),
                            "piezas_total": float(p.get("piezas_total") or 0),
                        }
                        for p in productos
                    ],
                }
            )

        return resultado

    finally:
        cursor.close()
        conn.close()


@app.get("/reportes/clientes/{numero_cliente}/productos", response_model=ReporteClienteDetalleOut)
def reporte_cliente_productos(
    numero_cliente: str,
    mes: str | None = None,
    empresa: str | None = None,
    vista: str | None = None,
):
    conn = conectar_mysql()
    cursor = conn.cursor(dictionary=True)

    try:
        condiciones = ["f.numero_cliente = %s"]
        params = [numero_cliente]

        if empresa and empresa.strip():
            condiciones.append("f.empresa = %s")
            params.append(empresa.strip())

        if mes and mes.strip():
            condiciones.append("DATE_FORMAT(f.fecha, '%Y-%m') = %s")
            params.append(mes.strip())

        where_sql = " AND ".join(condiciones)

        cursor.execute(
            f"""
            SELECT
                f.numero_cliente,
                COALESCE(
                    MAX(NULLIF(c.nombre, '')),
                    MAX(NULLIF(f.consignatario, '')),
                    MAX(f.numero_cliente)
                ) AS cliente_nombre,
                MAX(f.fecha) AS ultima_compra
            FROM facturas f
            LEFT JOIN clientes c
                ON c.numero = f.numero_cliente
               AND c.empresa = f.empresa
            WHERE {where_sql}
            GROUP BY f.numero_cliente
            LIMIT 1
            """,
            params,
        )

        encabezado = cursor.fetchone()

        if not encabezado:
            return ReporteClienteDetalleOut(
                numero_cliente=numero_cliente,
                cliente_nombre="",
                mes=mes,
                ultima_compra=None,
                productos=[],
            )

        vista = (vista or "").strip().lower()

        productos = []
        facturas = []

        if vista != "facturas":
            cursor.execute(
                f"""
                SELECT
                    COALESCE(fd.cip, '') AS cip,
                    fd.descripcion,
                    COALESCE(SUM(fd.cantidad), 0) AS cantidad_total,
                    COALESCE(SUM(fd.piezas), 0) AS piezas_total,
                    MAX(f.fecha) AS ultima_compra_producto
                FROM facturas f
                JOIN factura_detalle fd ON fd.factura_id = f.id
                WHERE {where_sql}
                GROUP BY COALESCE(fd.cip, ''), fd.descripcion
                ORDER BY cantidad_total DESC, piezas_total DESC, fd.descripcion ASC
                """,
                params,
            )

            productos = cursor.fetchall()

        if vista != "productos":
            cursor.execute(
                f"""
                SELECT
                    COALESCE(f.factura, '') AS factura,
                    f.fecha,
                    COALESCE(f.numero_cliente, '') AS numero_cliente,
                    COALESCE(
                        NULLIF(c.nombre, ''),
                        NULLIF(f.consignatario, ''),
                        COALESCE(f.numero_cliente, '')
                    ) AS consignatario,
                    COALESCE(f.total, f.subtotal, 0) AS importe,
                    f.subtotal,
                    f.descuento,
                    f.iva,
                    f.total,
                    COALESCE(f.empresa, '') AS empresa,
                    COALESCE(f.numero_salida, '') AS numero_salida,
                    COALESCE(f.estatus, '') AS estatus,
                    COALESCE(f.sae_codigo, '') AS sae_codigo
                FROM facturas f
                LEFT JOIN clientes c
                    ON c.numero = f.numero_cliente
                   AND c.empresa = f.empresa
                INNER JOIN (
                    SELECT
                        factura,
                        MAX(id) AS max_id
                    FROM facturas f
                    WHERE {where_sql}
                    GROUP BY factura
                ) ult
                    ON ult.max_id = f.id
                WHERE {where_sql}
                ORDER BY f.fecha DESC, f.id DESC
                LIMIT 30
                """,
                params + params,
            )

            facturas = cursor.fetchall()

        return ReporteClienteDetalleOut(
            numero_cliente=str(encabezado["numero_cliente"] or ""),
            cliente_nombre=encabezado["cliente_nombre"] or "",
            mes=mes,
            ultima_compra=encabezado["ultima_compra"].isoformat() if encabezado.get("ultima_compra") else None,
            productos=[
                ReporteClienteProductoOut(
                    cip=str(p.get("cip") or ""),
                    descripcion=p["descripcion"] or "",
                    cantidad_total=float(p["cantidad_total"] or 0),
                    piezas_total=int(p["piezas_total"] or 0),
                    ultima_compra=(p["ultima_compra_producto"].isoformat() if p.get("ultima_compra_producto") else None),
                )
                for p in productos
            ],
            facturas=[
                FacturaResumenOut(
                    factura=str(f.get("factura") or ""),
                    fecha=(f["fecha"].isoformat() if f.get("fecha") else None),
                    numero_cliente=str(f.get("numero_cliente") or ""),
                    consignatario=str(f.get("consignatario") or ""),
                    importe=(float(f["importe"]) if f.get("importe") is not None else None),
                    subtotal=(float(f["subtotal"]) if f.get("subtotal") is not None else None),
                    descuento=(float(f["descuento"]) if f.get("descuento") is not None else None),
                    iva=(float(f["iva"]) if f.get("iva") is not None else None),
                    total=(float(f["total"]) if f.get("total") is not None else None),
                    empresa=str(f.get("empresa") or ""),
                    numero_salida=str(f.get("numero_salida") or ""),
                    estatus=str(f.get("estatus") or ""),
                    sae_codigo=str(f.get("sae_codigo") or ""),
                )
                for f in facturas
            ],
        )

    finally:
        cursor.close()
        conn.close()


@app.get("/reportes/clientes/{numero_cliente}/meses", response_model=list[ReporteClienteMesOut])
def reporte_cliente_meses(numero_cliente: str, empresa: str | None = None):
    conn = conectar_mysql()
    cursor = conn.cursor(dictionary=True)

    try:
        condiciones = ["f.numero_cliente = %s"]
        params = [numero_cliente]

        if empresa and empresa.strip():
            condiciones.append("f.empresa = %s")
            params.append(empresa.strip())

        where_sql = " AND ".join(condiciones)

        cursor.execute(
            f"""
            SELECT DISTINCT
                DATE_FORMAT(f.fecha, '%Y-%m') AS value,
                DATE_FORMAT(f.fecha, '%m/%Y') AS label,
                MAX(f.fecha) AS fecha_orden
            FROM facturas f
            WHERE {where_sql}
            GROUP BY DATE_FORMAT(f.fecha, '%Y-%m'), DATE_FORMAT(f.fecha, '%m/%Y')
            ORDER BY fecha_orden DESC
            """,
            params,
        )

        rows = cursor.fetchall()

        return [ReporteClienteMesOut(value=r["value"] or "", label=r["label"] or "") for r in rows]

    finally:
        cursor.close()
        conn.close()


# =========================
# Pedidos reales (encabezado + detalle)
# =========================
OBS_REGEX = r"^[\w\s\-\.,/#()]*$"


class PedidoItemIn(BaseModel):
    cip: constr(min_length=1, max_length=30, pattern=r"^\d+$")
    descripcion: str = Field(..., min_length=1, max_length=255)
    kgs: confloat(ge=0) = 0.0
    piezas: conint(ge=0) = 0
    observaciones: constr(max_length=200, pattern=OBS_REGEX) = ""


class PedidoVendedorIn(BaseModel):
    uuid: str = Field(..., min_length=6, max_length=80)
    vendedor: str = ""
    empresa: str = Field(..., min_length=1, max_length=120)
    cliente_numero: str = Field(..., min_length=1, max_length=40)
    cliente_nombre: str = Field(..., min_length=1, max_length=255)
    observaciones_pedido: constr(max_length=300, pattern=OBS_REGEX) = ""
    items: list[PedidoItemIn] = Field(..., min_length=1)


@app.post("/vendedor/pedidos")
def crear_pedido_vendedor(payload: PedidoVendedorIn, user: dict = Depends(get_current_user)):
    vendedor = user["usuario"]

    if not payload.empresa or not payload.cliente_numero or not payload.cliente_nombre:
        raise HTTPException(status_code=400, detail="Faltan datos del cliente.")

    if not payload.items:
        raise HTTPException(status_code=400, detail="El pedido no tiene productos.")

    conn = conectar_mysql()
    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT 1
            FROM clientes
            WHERE empresa=%s
            LIMIT 1
            """,
            (payload.empresa.strip(),),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=400, detail="Empresa invÃ¡lida.")

        cur.execute(
            """
            SELECT 1
            FROM clientes
            WHERE empresa=%s AND numero=%s
            LIMIT 1
            """,
            (payload.empresa.strip(), payload.cliente_numero.strip()),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=400, detail="Cliente no pertenece a la empresa.")

        cur.execute("SELECT id, estado FROM pedidos_vendedor WHERE uuid=%s LIMIT 1", (payload.uuid,))
        row = cur.fetchone()
        if row:
            return {
                "ok": True,
                "pedido_id": int(row[0]),
                "estado": row[1],
                "duplicado": True,
            }

        cur.execute(
            """
            INSERT INTO pedidos_vendedor
                (uuid, vendedor, empresa, cliente_numero, cliente_nombre,
                 fecha, observaciones_pedido, estado)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, 'PENDIENTE')
            """,
            (
                payload.uuid,
                vendedor,
                payload.empresa.strip(),
                payload.cliente_numero.strip(),
                payload.cliente_nombre.strip(),
                datetime.now(),
                (payload.observaciones_pedido or "").strip(),
            ),
        )

        pedido_id = cur.lastrowid

        for item in payload.items:
            cur.execute(
                """
                INSERT INTO pedidos_vendedor_detalle
                    (pedido_id, cip, descripcion, kgs, piezas, observaciones)
                VALUES
                    (%s, %s, %s, %s, %s, %s)
                """,
                (
                    pedido_id,
                    item.cip.strip(),
                    item.descripcion.strip(),
                    float(item.kgs or 0),
                    int(item.piezas or 0),
                    (item.observaciones or "").strip(),
                ),
            )

        conn.commit()

        return {
            "ok": True,
            "pedido_id": int(pedido_id),
            "estado": "PENDIENTE",
            "duplicado": False,
        }

    finally:
        try:
            conn.close()
        except Exception:
            pass


class PedidoResumenOut(BaseModel):
    id: int
    fecha: str
    empresa: str
    cliente_numero: str
    cliente_nombre: str
    estado: str
    comanda_id: Optional[int] = None
    folio_usado: Optional[str] = None


class ComandaProductoOut(BaseModel):
    cip: str = ""
    descripcion: str = ""
    kgs: float = 0.0
    piezas: float = 0.0
    observaciones: str = ""


class ComandaPreviewOut(BaseModel):
    comanda_id: int
    folio: str
    fecha: str
    vendedor: str
    empresa: str
    cliente_numero: str
    cliente_nombre: str
    observaciones_pedido: str = ""
    productos: List[ComandaProductoOut]


@app.get("/vendedor/pedidos", response_model=List[PedidoResumenOut])
def listar_pedidos_vendedor(
    limit: int = 200,
    user: dict = Depends(get_current_user),
):
    vendedor = user["usuario"]
    limit = max(1, min(500, int(limit)))

    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT
                id,
                DATE_FORMAT(fecha, '%Y-%m-%d %H:%i') AS fecha,
                empresa,
                cliente_numero,
                cliente_nombre,
                estado,
                comanda_id,
                folio_usado
            FROM pedidos_vendedor
            WHERE vendedor=%s
            ORDER BY id DESC
            LIMIT %s
            """,
            (vendedor, limit),
        )

        rows = cur.fetchall() or []
        return [
            PedidoResumenOut(
                id=int(r["id"]),
                fecha=str(r["fecha"] or ""),
                empresa=str(r["empresa"] or ""),
                cliente_numero=str(r["cliente_numero"] or ""),
                cliente_nombre=str(r["cliente_nombre"] or ""),
                estado=str(r["estado"] or ""),
                comanda_id=(int(r["comanda_id"]) if r.get("comanda_id") is not None else None),
                folio_usado=(str(r["folio_usado"]) if r.get("folio_usado") else None),
            )
            for r in rows
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/comandas/{comanda_id}/preview", response_model=ComandaPreviewOut)
def preview_comanda_por_id(
    comanda_id: int,
    user: dict = Depends(get_current_user),
):
    vendedor = user["usuario"]

    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)

        cur.execute(
            """
            SELECT id, folio, DATE_FORMAT(fecha,'%Y-%m-%d %H:%i') AS fecha,
                   vendedor, empresa, cliente_numero, cliente_nombre,
                   IFNULL(observaciones_pedido,'') AS observaciones_pedido
            FROM comandas
            WHERE id=%s
            LIMIT 1
            """,
            (int(comanda_id),),
        )
        c = cur.fetchone()
        if not c:
            raise HTTPException(status_code=404, detail="Comanda no encontrada")

        if str(c.get("vendedor") or "").strip() and str(c["vendedor"]).strip() != vendedor:
            raise HTTPException(status_code=403, detail="No autorizado para ver esta comanda")

        cur.execute(
            """
            SELECT
                IFNULL(cip,'') AS cip,
                IFNULL(descripcion,'') AS descripcion,
                IFNULL(kgs,0) AS kgs,
                IFNULL(piezas,0) AS piezas,
                IFNULL(observaciones,'') AS observaciones
            FROM productos_comanda
            WHERE comanda_id=%s
            ORDER BY id
            """,
            (int(comanda_id),),
        )
        prows = cur.fetchall() or []

        return ComandaPreviewOut(
            comanda_id=int(c["id"]),
            folio=str(c["folio"]),
            fecha=str(c["fecha"] or ""),
            vendedor=str(c["vendedor"] or ""),
            empresa=str(c["empresa"] or ""),
            cliente_numero=str(c["cliente_numero"] or ""),
            cliente_nombre=str(c["cliente_nombre"] or ""),
            observaciones_pedido=str(c.get("observaciones_pedido") or ""),
            productos=[
                ComandaProductoOut(
                    cip=str(p["cip"] or ""),
                    descripcion=str(p["descripcion"] or ""),
                    kgs=float(p["kgs"] or 0),
                    piezas=float(p["piezas"] or 0),
                    observaciones=str(p["observaciones"] or ""),
                )
                for p in prows
            ],
        )

    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/vendedor/pedidos/{pedido_id}/preview", response_model=ComandaPreviewOut)
def preview_pedido(
    pedido_id: int,
    user: dict = Depends(get_current_user),
):
    vendedor = user["usuario"]

    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)

        cur.execute(
            """
            SELECT
                id,
                DATE_FORMAT(fecha,'%Y-%m-%d %H:%i') AS fecha,
                vendedor,
                empresa,
                cliente_numero,
                cliente_nombre,
                IFNULL(observaciones_pedido,'') AS observaciones_pedido,
                comanda_id,
                IFNULL(folio_usado,'') AS folio_usado
            FROM pedidos_vendedor
            WHERE id=%s
            LIMIT 1
            """,
            (int(pedido_id),),
        )
        p = cur.fetchone()
        if not p:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")

        if str(p.get("vendedor") or "").strip() != vendedor:
            raise HTTPException(status_code=403, detail="No autorizado para ver este pedido")

        comanda_id = p.get("comanda_id")

        if comanda_id is not None:
            cur.execute(
                """
                SELECT id, folio, DATE_FORMAT(fecha,'%Y-%m-%d %H:%i') AS fecha,
                       vendedor, empresa, cliente_numero, cliente_nombre,
                       IFNULL(observaciones_pedido,'') AS observaciones_pedido
                FROM comandas
                WHERE id=%s
                LIMIT 1
                """,
                (int(comanda_id),),
            )
            c = cur.fetchone()
            if not c:
                raise HTTPException(status_code=404, detail="Comanda no encontrada")

            if str(c.get("vendedor") or "").strip() and str(c["vendedor"]).strip() != vendedor:
                raise HTTPException(status_code=403, detail="No autorizado para ver esta comanda")

            cur.execute(
                """
                SELECT
                    IFNULL(cip,'') AS cip,
                    IFNULL(descripcion,'') AS descripcion,
                    IFNULL(kgs,0) AS kgs,
                    IFNULL(piezas,0) AS piezas,
                    IFNULL(observaciones,'') AS observaciones
                FROM productos_comanda
                WHERE comanda_id=%s
                ORDER BY id
                """,
                (int(comanda_id),),
            )
            prows = cur.fetchall() or []

            return ComandaPreviewOut(
                comanda_id=int(c["id"]),
                folio=str(c["folio"]),
                fecha=str(c["fecha"] or ""),
                vendedor=str(c["vendedor"] or ""),
                empresa=str(c["empresa"] or ""),
                cliente_numero=str(c["cliente_numero"] or ""),
                cliente_nombre=str(c["cliente_nombre"] or ""),
                observaciones_pedido=str(c.get("observaciones_pedido") or ""),
                productos=[
                    ComandaProductoOut(
                        cip=str(r["cip"] or ""),
                        descripcion=str(r["descripcion"] or ""),
                        kgs=float(r["kgs"] or 0),
                        piezas=float(r["piezas"] or 0),
                        observaciones=str(r["observaciones"] or ""),
                    )
                    for r in prows
                ],
            )

        cur.execute(
            """
            SELECT
                IFNULL(cip,'') AS cip,
                IFNULL(descripcion,'') AS descripcion,
                IFNULL(kgs,0) AS kgs,
                IFNULL(piezas,0) AS piezas,
                IFNULL(observaciones,'') AS observaciones
            FROM pedidos_vendedor_detalle
            WHERE pedido_id=%s
            ORDER BY id
            """,
            (int(pedido_id),),
        )
        drows = cur.fetchall() or []

        folio = (p.get("folio_usado") or "").strip()
        if not folio:
            folio = f"PEDIDO #{p['id']} (SIN COMANDA)"

        return ComandaPreviewOut(
            comanda_id=0,
            folio=folio,
            fecha=str(p["fecha"] or ""),
            vendedor=str(p.get("vendedor") or ""),
            empresa=str(p.get("empresa") or ""),
            cliente_numero=str(p.get("cliente_numero") or ""),
            cliente_nombre=str(p.get("cliente_nombre") or ""),
            observaciones_pedido=str(p.get("observaciones_pedido") or ""),
            productos=[
                ComandaProductoOut(
                    cip=str(r["cip"] or ""),
                    descripcion=str(r["descripcion"] or ""),
                    kgs=float(r["kgs"] or 0),
                    piezas=float(r["piezas"] or 0),
                    observaciones=str(r["observaciones"] or ""),
                )
                for r in drows
            ],
        )

    finally:
        try:
            conn.close()
        except Exception:
            pass


# ======================================================
# Facturas
# ======================================================
@app.get("/facturas/por-comanda/{folio}")
def factura_por_comanda(folio: str):
    conn = None
    cursor = None
    try:
        conn = conectar_mysql()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SET collation_connection = 'utf8mb4_unicode_ci'")

        cursor.execute(
            """
            SELECT factura
            FROM facturas
            WHERE numero_salida = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (folio.strip(),),
        )

        row = cursor.fetchone()
        return {"factura": row["factura"] if row else None}

    except Exception:
        return {"factura": None}

    finally:
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except Exception:
            pass


@app.get("/facturas/folio/{folio}")
def factura_por_folio(folio: str):
    conn = None
    cursor = None

    try:
        conn = conectar_mysql()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SET collation_connection = 'utf8mb4_unicode_ci'")
        cursor.execute(
            """
            SELECT *
            FROM facturas
            WHERE factura = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (folio.strip(),),
        )

        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Factura no encontrada")

        factura_id = row.get("id")
        items = []
        if factura_id:
            cursor.execute(
                """
                SELECT *
                FROM factura_detalle
                WHERE factura_id = %s
                ORDER BY id ASC
                """,
                (factura_id,),
            )
            drows = cursor.fetchall()
            items = [
                {
                    "cip": str(r.get("cip") or ""),
                    "descripcion": str(r.get("descripcion") or ""),
                    "cantidad": float(r.get("cantidad") or 0),
                    "piezas": float(r.get("piezas") or 0),
                    "precio": float(r.get("precio") or r.get("precio_unitario") or 0),
                    "importe": float(r.get("importe") or r.get("subtotal") or 0),
                }
                for r in drows
            ]

        row["items"] = items

        return row

    finally:
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except Exception:
            pass


@app.get("/facturas/folio/{folio}/pdf")
def factura_pdf_por_folio(folio: str, user: dict = Depends(get_current_user)):
    folio = (folio or "").strip().upper()

    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SET collation_connection = 'utf8mb4_unicode_ci'")
        cur.execute(
            """
            SELECT *
            FROM facturas
            WHERE factura = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (folio,),
        )
        f = cur.fetchone()
        if not f:
            raise HTTPException(status_code=404, detail="Factura no encontrada")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    buff = BytesIO()
    c = canvas.Canvas(buff, pagesize=letter)
    w, h = letter

    y = h - 40
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, f"Factura: {f.get('factura', '')}")
    y -= 22

    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Fecha: {str(f.get('fecha', ''))}")
    y -= 16
    c.drawString(40, y, f"Cliente: {str(f.get('numero_cliente', ''))}")
    y -= 16
    c.drawString(40, y, f"Salida: {str(f.get('numero_salida', ''))}")
    y -= 16

    y -= 8
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Totales")
    y -= 16
    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Subtotal: {float(f.get('subtotal') or 0):.2f}")
    y -= 14
    c.drawString(40, y, f"IVA: {float(f.get('iva') or 0):.2f}")
    y -= 14
    c.drawString(40, y, f"Total: {float(f.get('total') or 0):.2f}")
    y -= 14

    c.showPage()
    c.save()

    pdf_bytes = buff.getvalue()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{folio}.pdf"'},
    )


# ======================================================
# Fichas viejas por archivo
# ======================================================
RUTA_BASE_FICHAS = os.environ.get("FACTURACION_FICHAS_DIR", r"\\192.168.1.146\FichasTecnicas")
RUTAS_BASE_FICHAS_FALLBACK = [
    RUTA_BASE_FICHAS,
    r"\\192.168.1.146\FichasTecnicas",
    os.path.join(_runtime_base_dir(), "FichasTecnicas"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "FichasTecnicas"),
    r"\\SERVER_GALACTIC\FichasTecnicas",
    r"\\Server_galactic\FichasTecnicas",
    r"\\server_galactic\FichasTecnicas",
    r"\\ServerGalactic\FichasTecnicas",
    r"\\Server_galactico\FichasTecnicas",
    r"\\100.69.142.19\FichasTecnicas",
]
_RESOLVED_BASE_FICHAS_CACHE = ""


def resolver_base_fichas() -> str:
    global _RESOLVED_BASE_FICHAS_CACHE
    if _RESOLVED_BASE_FICHAS_CACHE:
        return _RESOLVED_BASE_FICHAS_CACHE
    vistos = set()
    fallback_local = ""
    for ruta in RUTAS_BASE_FICHAS_FALLBACK:
        ruta = str(ruta or "").strip()
        if not ruta:
            continue
        try:
            clave = os.path.normcase(os.path.abspath(ruta))
        except Exception:
            clave = os.path.normcase(ruta)
        if clave in vistos:
            continue
        vistos.add(clave)
        if not ruta.startswith("\\\\") and not fallback_local:
            fallback_local = ruta
        try:
            if os.path.isdir(ruta):
                _RESOLVED_BASE_FICHAS_CACHE = ruta
                return ruta
        except Exception:
            continue
    if fallback_local:
        try:
            os.makedirs(fallback_local, exist_ok=True)
            _RESOLVED_BASE_FICHAS_CACHE = fallback_local
            return fallback_local
        except Exception:
            pass
    _RESOLVED_BASE_FICHAS_CACHE = RUTA_BASE_FICHAS
    return RUTA_BASE_FICHAS


def ruta_fichas(*partes: str) -> str:
    return os.path.join(resolver_base_fichas(), *[str(p) for p in partes])


def rutas_equivalentes_ficha(ruta: str | None) -> list[str]:
    original = str(ruta or "").strip()
    if not original:
        return []
    candidatos = [original]
    reemplazos = {
        r"\\SERVER_GALACTIC\FichasTecnicas": r"\\100.69.142.19\FichasTecnicas",
        r"\\server_galactic\FichasTecnicas": r"\\100.69.142.19\FichasTecnicas",
        r"\\Server_galactic\FichasTecnicas": r"\\100.69.142.19\FichasTecnicas",
        r"\\ServerGalactic\FichasTecnicas": r"\\100.69.142.19\FichasTecnicas",
        r"\\Server_galactico\FichasTecnicas": r"\\100.69.142.19\FichasTecnicas",
        r"\\server_galactico\FichasTecnicas": r"\\100.69.142.19\FichasTecnicas",
        r"\\100.69.142.19\FichasTecnicas": r"\\SERVER_GALACTIC\FichasTecnicas",
    }
    for origen, destino in reemplazos.items():
        if original.lower().startswith(origen.lower()):
            candidatos.append(destino + original[len(origen):])
    prefijo_local = r"C:\FichasTecnicas"
    if original.lower().startswith(prefijo_local.lower()):
        relativa = original[len(prefijo_local):].lstrip("\\/")
        candidatos.append(ruta_fichas(*re.split(r"[\\/]+", relativa)))
        candidatos.append(os.path.join(r"\\100.69.142.19\FichasTecnicas", *re.split(r"[\\/]+", relativa)))
    vistos = set()
    salida = []
    for candidato in candidatos:
        key = os.path.normcase(os.path.abspath(candidato))
        if key not in vistos:
            vistos.add(key)
            salida.append(candidato)
    return salida


def reparar_mojibake(texto: str) -> str:
    t = str(texto or "")
    reemplazos = {
        "Ã¡": "á",
        "Ã©": "é",
        "Ã­": "í",
        "Ã³": "ó",
        "Ãº": "ú",
        "Ã": "Á",
        "Ã‰": "É",
        "Ã": "Í",
        "Ã“": "Ó",
        "Ãš": "Ú",
        "Ã±": "ñ",
        "Ã‘": "Ñ",
        "Ã¼": "ü",
        "Ãœ": "Ü",
        "â€¢": "•",
        "â€“": "–",
        "â€”": "—",
        "â€œ": "\"",
        "â€": "\"",
        "â€˜": "'",
        "â€™": "'",
        "Â¿": "¿",
        "Â¡": "¡",
        "Â°": "°",
    }
    for malo, bueno in reemplazos.items():
        t = t.replace(malo, bueno)
    return t


def normalizar_nombre_empresa_catalogo(nombre: str) -> str:
    limpio = reparar_mojibake(texto_seguro(nombre))
    clave = unicodedata.normalize("NFKD", limpio).encode("ascii", "ignore").decode("ascii").strip().lower()
    clave = re.sub(r"\s+", " ", clave)
    mapa = {
        "gourmet espana": "Gourmet España",
        "ibersur": "Ibersur",
        "eza2007": "EZA2007",
        "alimentos europeos": "Alimentos Europeos",
        "aldeu": "Aldeu",
    }
    return mapa.get(clave, limpio)


def normalizar_empresa_para_carpeta(nombre: str) -> str:
    texto = unicodedata.normalize("NFKD", normalizar_nombre_empresa_catalogo(nombre)).encode("ascii", "ignore").decode("ascii")
    texto = texto.replace(" ", "_")
    texto = re.sub(r"[^A-Za-z0-9_\-]", "", texto)
    return texto


def _directorios_logos_empresa() -> list[str]:
    candidatos = [
        os.environ.get("FACTURACION_CATALOGOS_LOGOS_DIR", ""),
        r"\\SERVER_GALACTIC\FichasTecnicas\LogosEmpresas",
        r"\\SERVER_GALACTIC\FichasTecnicas\logosempresa",
        r"\\SERVER_GALACTIC\FichasTecnicas\LogosEmpresa",
        r"\\Server_galactic\FichasTecnicas\LogosEmpresas",
        r"\\Server_galactic\FichasTecnicas\logosempresa",
        r"\\Server_galactic\FichasTecnicas\LogosEmpresa",
        r"\\server_galactic\FichasTecnicas\LogosEmpresas",
        r"\\server_galactic\FichasTecnicas\logosempresa",
        r"\\server_galactic\FichasTecnicas\LogosEmpresa",
        r"\\100.69.142.19\FichasTecnicas\LogosEmpresas",
        r"\\100.69.142.19\FichasTecnicas\logosempresa",
        r"\\100.69.142.19\FichasTecnicas\LogosEmpresa",
        os.path.join(_runtime_base_dir(), "FichasTecnicas", "LogosEmpresas"),
        os.path.join(_runtime_base_dir(), "FichasTecnicas", "logosempresa"),
        os.path.join(_runtime_base_dir(), "FichasTecnicas", "LogosEmpresa"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "FichasTecnicas", "LogosEmpresas"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "FichasTecnicas", "logosempresa"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "FichasTecnicas", "LogosEmpresa"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "logos_catalogo"),
    ]
    vistos = set()
    dirs = []
    for ruta in candidatos:
        if not ruta:
            continue
        clave = os.path.normcase(os.path.abspath(ruta))
        if clave in vistos:
            continue
        vistos.add(clave)
        if os.path.isdir(ruta):
            dirs.append(ruta)
    return dirs


_VPS_LOGOS_EMPRESA_CACHE: dict[str, Any] = {"loaded_at": 0.0, "items": []}


def _normalizar_clave_logo_empresa(value: str) -> str:
    text = reparar_mojibake(texto_seguro(value))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def _vps_catalogo_settings():
    try:
        from app.core.config import settings
        return settings
    except Exception:
        return None


def _vps_catalogo_request(opener, path_or_url: str, *, payload: dict | None = None, timeout: int = 25) -> tuple[bytes, str]:
    settings = _vps_catalogo_settings()
    if not settings or not settings.catalog_vps_url:
        raise RuntimeError("catalog_vps_url no configurado")
    if path_or_url.lower().startswith(("http://", "https://")):
        url = path_or_url
    else:
        url = f"{settings.catalog_vps_url.rstrip('/')}/{path_or_url.lstrip('/')}"
    data = None
    headers = {"Accept": "application/json"}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with opener.open(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type") or "application/octet-stream"


def _vps_catalogo_opener():
    settings = _vps_catalogo_settings()
    if not settings or not settings.catalog_vps_url or not settings.catalog_vps_email or not settings.catalog_vps_password:
        return None
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    _vps_catalogo_request(
        opener,
        "/api/auth/login",
        payload={"email": settings.catalog_vps_email, "password": settings.catalog_vps_password},
        timeout=20,
    )
    return opener


def _logos_empresa_vps() -> list[dict]:
    now = time.time()
    cached_at = float(_VPS_LOGOS_EMPRESA_CACHE.get("loaded_at") or 0)
    if now - cached_at < 600 and isinstance(_VPS_LOGOS_EMPRESA_CACHE.get("items"), list):
        return _VPS_LOGOS_EMPRESA_CACHE["items"]
    try:
        opener = _vps_catalogo_opener()
        if not opener:
            return []
        raw, _ = _vps_catalogo_request(opener, "/api/admin/company-logos", timeout=25)
        data = json.loads(raw.decode("utf-8", errors="ignore"))
        items = data if isinstance(data, list) else []
        _VPS_LOGOS_EMPRESA_CACHE.update({"loaded_at": now, "items": items})
        return items
    except Exception as e:
        print(f"[CATALOGO] No se pudieron leer logos de empresa desde VPS: {e}", flush=True)
        return []


def _candidatos_clave_empresa(empresa: str) -> set[str]:
    nombre = normalizar_nombre_empresa_catalogo(empresa)
    base = _normalizar_clave_logo_empresa(nombre)
    candidatos = {base}
    if "gourmet" in base and "espana" in base:
        candidatos.update({"gourmetespana", "gourmetespana", "ges", "gourmet"})
    if "eza" in base:
        candidatos.update({"eza2007", "eza"})
    if "ibersur" in base:
        candidatos.add("ibersur")
    if "alimentoseuropeos" in base or "alimentos" in base:
        candidatos.update({"alimentoseuropeos", "alimentos"})
    return {c for c in candidatos if c}


def _extension_logo_vps(url: str, content_type: str) -> str:
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    guessed = mimetypes.guess_extension(content_type) or ""
    if guessed.lower() == ".jpe":
        guessed = ".jpg"
    path_ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    for ext in (guessed, path_ext):
        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            return ext
    return ".png"


def _cache_logo_empresa_vps(empresa: str, logo: dict) -> str | None:
    settings = _vps_catalogo_settings()
    image_url = texto_seguro(logo.get("image_url"))
    if not settings or not settings.catalog_vps_url or not image_url:
        return None
    absolute_url = urllib.parse.urljoin(settings.catalog_vps_url.rstrip("/") + "/", image_url.lstrip("/"))
    cache_dir = os.path.join(_runtime_base_dir(), "FichasTecnicas", "LogosEmpresasVps")
    os.makedirs(cache_dir, exist_ok=True)
    cache_key = _safe_path_component(normalizar_nombre_empresa_catalogo(empresa))
    png_path = os.path.join(cache_dir, f"{cache_key}.png")
    marker_path = os.path.join(cache_dir, f"{cache_key}.source_url.txt")
    try:
        if os.path.isfile(png_path) and os.path.isfile(marker_path):
            if open(marker_path, "r", encoding="utf-8").read().strip() == absolute_url:
                return png_path
    except Exception:
        pass
    try:
        opener = urllib.request.build_opener()
        raw, content_type = _vps_catalogo_request(opener, absolute_url, timeout=30)
        if not raw:
            return png_path if os.path.isfile(png_path) else None
        ext = _extension_logo_vps(absolute_url, content_type)
        if Image is not None:
            with Image.open(BytesIO(raw)) as img:
                img.convert("RGBA").save(png_path, "PNG")
        else:
            raw_path = os.path.join(cache_dir, f"{cache_key}{ext}")
            with open(raw_path, "wb") as fh:
                fh.write(raw)
            if ext == ".png":
                png_path = raw_path
            else:
                return raw_path
        with open(marker_path, "w", encoding="utf-8") as fh:
            fh.write(absolute_url)
        return png_path
    except Exception as e:
        print(f"[CATALOGO] No se pudo descargar logo VPS para {empresa}: {e}", flush=True)
        return png_path if os.path.isfile(png_path) else None


def resolver_logo_empresa_vps(empresa: str) -> str | None:
    claves = _candidatos_clave_empresa(empresa)
    if not claves:
        return None
    for logo in _logos_empresa_vps():
        if not isinstance(logo, dict):
            continue
        company_key = _normalizar_clave_logo_empresa(logo.get("company_name") or "")
        if company_key in claves:
            cached = _cache_logo_empresa_vps(empresa, logo)
            if cached and os.path.isfile(cached):
                return cached
    return None


def resolver_logo_empresa(empresa: str) -> str | None:
    logo_vps = resolver_logo_empresa_vps(empresa)
    if logo_vps:
        return logo_vps

    emp_raw = str(empresa or "").strip().lower()
    emp_norm = unicodedata.normalize("NFKD", str(empresa or "")).encode("ascii", "ignore").decode("ascii").strip().lower()
    candidatos = []

    if ("gourmet" in emp_raw or "gourmet" in emp_norm) and ("espa" in emp_raw or "espa" in emp_norm):
        candidatos.extend(["gourmet_espana.png", "gourmet.png", "gourmet(2).png", "GE LOGO.png"])
    if "ibersur" in emp_raw or "ibersur" in emp_norm:
        candidatos.append("ibersur.png")
    if "eza2007" in emp_raw or "eza2007" in emp_norm:
        candidatos.extend(["eza2007.png", "eza 2007 logo blanco.png"])
    if ("alimentos" in emp_raw or "alimentos" in emp_norm) and ("europe" in emp_raw or "europe" in emp_norm):
        candidatos.extend([
            "alimentos_europeos.png",
            "alimentos europeos.png",
            "alimentos-europeos.png",
            "alimentos_europeos_logo.png",
            "alimentos europeos logo.png",
        ])
    if "aldeu" in emp_raw or "aldeu" in emp_norm:
        candidatos.append("aldeu.png")
    candidatos.extend([
        "logo-alimentos-europeos.png",
        "alimentos_europeos.png",
        "alimentos europeos.png",
    ])

    for logos_dir in _directorios_logos_empresa():
        for nombre_logo in candidatos:
            ruta = os.path.join(logos_dir, nombre_logo)
            if os.path.isfile(ruta):
                return ruta

        for nombre_logo in os.listdir(logos_dir):
            nombre_norm = unicodedata.normalize("NFKD", nombre_logo).encode("ascii", "ignore").decode("ascii").strip().lower()
            if "gourmet" in emp_norm and "espa" in emp_norm and ("gourmet" in nombre_norm or "ge logo" in nombre_norm):
                ruta = os.path.join(logos_dir, nombre_logo)
                if os.path.isfile(ruta):
                    return ruta
            if emp_norm and emp_norm.replace(" ", "") in nombre_norm.replace("_", "").replace(" ", ""):
                ruta = os.path.join(logos_dir, nombre_logo)
                if os.path.isfile(ruta):
                    return ruta
    return None


def obtener_ficha_producto_db(empresa: str, cip: str):
    asegurar_tabla_productos_ficha()
    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT empresa, cip, extension, fecha_actualizacion
            FROM productos_ficha
            WHERE empresa = %s AND cip = %s
            LIMIT 1
            """,
            (empresa, str(cip)),
        )
        return cur.fetchone()
    finally:
        conn.close()


def ficha_completa_para_empresa(empresa: str, cip: str) -> dict | None:
    ficha = obtener_ficha_producto_nueva(empresa, cip)
    if not ficha:
        return None
    if int(ficha.get("activo") or 0) != 1:
        return None
    imagen_principal = resolver_imagen_producto(ficha)
    if not imagen_principal or not os.path.isfile(imagen_principal):
        return None
    tiene_datos = any(
        texto_seguro(ficha.get(campo))
        for campo in (
            "titulo_ficha",
            "nombre_producto",
            "descripcion_corta",
            "marca",
            "tipo_producto",
            "texto_comercial",
            "categoria",
        )
    )
    if not tiene_datos:
        return None
    ficha = dict(ficha)
    ficha["imagen_path"] = imagen_principal
    return ficha


def ficha_completa_desde_dict(ficha: dict | None) -> dict | None:
    if not ficha:
        return None
    if int(ficha.get("activo") or 0) != 1:
        return None
    imagen_principal = resolver_imagen_producto(ficha)
    if not imagen_principal or not os.path.isfile(imagen_principal):
        return None
    tiene_datos = any(
        texto_seguro(ficha.get(campo))
        for campo in (
            "titulo_ficha",
            "nombre_producto",
            "descripcion_corta",
            "marca",
            "tipo_producto",
            "texto_comercial",
            "categoria",
        )
    )
    if not tiene_datos:
        return None
    data = dict(ficha)
    data["imagen_path"] = imagen_principal
    return data


def listar_cips_disponibles_catalogo(empresa: str, cips: list[str]) -> list[str]:
    cips_limpios = [str(c).strip() for c in (cips or []) if str(c).strip()]
    if not empresa or not cips_limpios:
        return []
    fichas = obtener_fichas_productos_nueva(empresa, cips_limpios)
    disponibles = []
    for ficha in fichas:
        ficha_ok = ficha_completa_desde_dict(ficha)
        if ficha_ok:
            disponibles.append(str(ficha_ok.get("cip")))
    return disponibles


@app.get("/catalogos/ficha")
def get_ficha_producto(empresa: str, cip: str):
    info = obtener_ficha_producto_db(empresa, cip)
    if not info:
        raise HTTPException(status_code=404, detail="No existe ficha para ese producto y empresa")

    empresa_dir = normalizar_empresa_para_carpeta(empresa)
    ext = info["extension"] or ""
    if ext and not ext.startswith("."):
        ext = "." + ext

    ruta = ruta_fichas(empresa_dir, str(cip), f"principal{ext}")

    if not os.path.exists(ruta):
        raise HTTPException(status_code=404, detail="El archivo de ficha no existe")

    return FileResponse(
        ruta,
        media_type="application/octet-stream",
        filename=os.path.basename(ruta),
    )


@app.get("/catalogos/productos")
def listar_productos_catalogo(q: str = "", empresa: str = "", limit: int = 200):
    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)

        cur.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci")
        cur.execute("SET collation_connection = 'utf8mb4_unicode_ci'")

        query = """
            SELECT
                p.cip,
                p.descripcion,
                p.unidad,
                CASE
                    WHEN pf.cip IS NULL OR COALESCE(pf.activo, 0) <> 1 THEN 0
                    ELSE 1
                END AS tiene_ficha
            FROM productos p
            LEFT JOIN productos_ficha pf
                ON pf.cip COLLATE utf8mb4_unicode_ci = p.cip COLLATE utf8mb4_unicode_ci
               AND pf.empresa COLLATE utf8mb4_unicode_ci = %s COLLATE utf8mb4_unicode_ci
            WHERE 1=1
        """
        params = [empresa]

        if q.strip():
            query += """
                AND (
                    p.cip LIKE %s
                    OR p.descripcion COLLATE utf8mb4_unicode_ci LIKE %s COLLATE utf8mb4_unicode_ci
                )
            """
            params.append(f"%{q.strip()}%")
            params.append(f"%{q.strip()}%")

        query += " ORDER BY p.descripcion COLLATE utf8mb4_unicode_ci LIMIT %s"
        params.append(limit)

        cur.execute(query, params)
        rows = cur.fetchall()

        resultado = []
        for r in rows:
            ficha_valida = None
            if bool(r["tiene_ficha"]):
                try:
                    ficha_valida = ficha_completa_para_empresa(empresa, r["cip"])
                except Exception:
                    ficha_valida = None
            tiene_ficha = bool(ficha_valida)
            resultado.append(
                {
                    "cip": r["cip"],
                    "descripcion": r["descripcion"],
                    "unidad": r["unidad"],
                    "tieneFicha": tiene_ficha,
                    "fichaUrl": f"/catalogos/ficha?empresa={empresa}&cip={r['cip']}" if tiene_ficha else None,
                }
            )

        cur.close()
        return resultado
    finally:
        conn.close()


@app.get("/catalogos/productos-base")
def listar_productos_catalogo_base(q: str = "", limit: int = 5000):
    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci")
        cur.execute("SET collation_connection = 'utf8mb4_unicode_ci'")

        query = """
            SELECT
                p.cip,
                p.descripcion,
                p.unidad
            FROM productos p
            WHERE 1=1
        """
        params = []

        if q.strip():
            query += """
                AND (
                    p.cip LIKE %s
                    OR p.descripcion COLLATE utf8mb4_unicode_ci LIKE %s COLLATE utf8mb4_unicode_ci
                )
            """
            params.append(f"%{q.strip()}%")
            params.append(f"%{q.strip()}%")

        query += " ORDER BY p.descripcion COLLATE utf8mb4_unicode_ci LIMIT %s"
        params.append(limit)
        cur.execute(query, params)
        rows = cur.fetchall() or []
        cur.close()
        return [
            {
                "cip": r["cip"],
                "descripcion": r["descripcion"],
                "unidad": r.get("unidad"),
                "tieneFicha": False,
                "fichaUrl": None,
            }
            for r in rows
        ]
    finally:
        conn.close()


@app.get("/catalogos/productos-disponibilidad")
def catalogos_productos_disponibilidad(empresa: str, cips: str = ""):
    lista = [x.strip() for x in str(cips or "").split(",") if x.strip()]
    disponibles = listar_cips_disponibles_catalogo(empresa, lista)
    return {
        "empresa": empresa,
        "disponibles": disponibles,
    }


# ======================================================
# Fichas nuevas estructuradas
# ======================================================
class ProductoFichaOut(BaseModel):
    cip: str
    empresa: str
    empresas_relacionadas: list[str] = Field(default_factory=list)
    extension: Optional[str] = None
    nombre_producto: Optional[str] = None
    marca: Optional[str] = None
    categoria: Optional[str] = None
    contenido_neto: Optional[str] = None
    presentacion: Optional[str] = None
    ingredientes: Optional[str] = None
    conservacion: Optional[str] = None
    origen: Optional[str] = None
    ean: Optional[str] = None
    descripcion_corta: Optional[str] = None
    observaciones_ficha: Optional[str] = None
    titulo_ficha: Optional[str] = None
    subtitulo: Optional[str] = None
    tipo_producto: Optional[str] = None
    maduracion: Optional[str] = None
    peso_aprox: Optional[str] = None
    texto_comercial: Optional[str] = None
    imagen_path: Optional[str] = None
    imagenes_adicionales: list[str] = Field(default_factory=list)
    imagenes_disponibles: list[str] = Field(default_factory=list)
    badge_1: Optional[str] = None
    badge_2: Optional[str] = None
    badge_3: Optional[str] = None
    etiquetas_retail: list[str] = Field(default_factory=list)
    premium_sort: int = 0
    premium_activo: int = 1
    activo: int = 1
    fecha_actualizacion: Optional[str] = None


class ProductoFichaIn(BaseModel):
    empresa: str = Field(..., min_length=1, max_length=255)
    cip: str = Field(..., min_length=1, max_length=255)
    empresas_relacionadas: list[str] = Field(default_factory=list)
    extension: Optional[str] = None
    nombre_producto: Optional[str] = None
    marca: Optional[str] = None
    categoria: Optional[str] = None
    contenido_neto: Optional[str] = None
    presentacion: Optional[str] = None
    ingredientes: Optional[str] = None
    conservacion: Optional[str] = None
    origen: Optional[str] = None
    ean: Optional[str] = None
    descripcion_corta: Optional[str] = None
    observaciones_ficha: Optional[str] = None
    titulo_ficha: Optional[str] = None
    subtitulo: Optional[str] = None
    tipo_producto: Optional[str] = None
    maduracion: Optional[str] = None
    peso_aprox: Optional[str] = None
    texto_comercial: Optional[str] = None
    imagen_path: Optional[str] = None
    badge_1: Optional[str] = None
    badge_2: Optional[str] = None
    badge_3: Optional[str] = None
    etiquetas_retail: list[str] = Field(default_factory=list)
    premium_sort: int = 0
    premium_activo: int = 1
    activo: int = 1

    @field_validator("etiquetas_retail", mode="before")
    @classmethod
    def normalizar_etiquetas_retail(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return csv_a_lista(v)
        if isinstance(v, list):
            return [" ".join(str(x).strip().split()) for x in v if str(x).strip()]
        return []

    @field_validator("empresas_relacionadas", mode="before")
    @classmethod
    def normalizar_empresas_relacionadas(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [" ".join(v.strip().split())] if v.strip() else []
        if isinstance(v, list):
            limpias = []
            vistos = set()
            for item in v:
                nombre = " ".join(str(item or "").strip().split())
                if not nombre:
                    continue
                clave = nombre.casefold()
                if clave in vistos:
                    continue
                vistos.add(clave)
                limpias.append(nombre)
            return limpias
        return []


class CatalogoPdfIn(BaseModel):
    empresa: str
    cips: List[str] = Field(..., min_length=1)


class SeleccionarPrincipalIn(BaseModel):
    empresa: str
    cip: str
    archivo: str

class CatalogoPdfItemOut(BaseModel):
    cip: str
    nombre_producto: Optional[str] = None
    marca: Optional[str] = None
    categoria: Optional[str] = None
    contenido_neto: Optional[str] = None
    presentacion: Optional[str] = None
    origen: Optional[str] = None
    descripcion_corta: Optional[str] = None
    observaciones_ficha: Optional[str] = None
    titulo_ficha: Optional[str] = None
    subtitulo: Optional[str] = None
    tipo_producto: Optional[str] = None
    maduracion: Optional[str] = None
    peso_aprox: Optional[str] = None
    texto_comercial: Optional[str] = None
    imagen_path: Optional[str] = None
    imagenes_adicionales: list[str] = Field(default_factory=list)


def _resolver_ficha_admin_para_empresa(empresa: str, cip: str) -> dict:
    ficha = obtener_ficha_producto_nueva(empresa, cip)
    if ficha:
        return _adjuntar_empresas_relacionadas_ficha(ficha, empresa)
    ficha_base = obtener_ficha_base_db(cip)
    if ficha_base:
        data = {
            **ficha_base,
            "empresa": empresa,
            "cip": texto_seguro(cip),
            "imagen_path": "",
            "imagenes_adicionales": [],
            "imagenes_disponibles": [],
            "premium_sort": int(ficha_base.get("premium_sort") or 0),
            "premium_activo": int(ficha_base.get("premium_activo") or 1),
            "activo": 1,
        }
        return _adjuntar_empresas_relacionadas_ficha(enriquecer_imagenes_ficha(data), empresa)
    base = _obtener_producto_base_para_ficha(empresa, cip)
    if base:
        return _adjuntar_empresas_relacionadas_ficha(enriquecer_imagenes_ficha(base), empresa)
    raise HTTPException(status_code=404, detail="Producto o ficha no encontrado")


def _normalizar_extension(ext: str | None) -> str | None:
    ext = (ext or '').strip().lower()
    if not ext:
        return None
    if not ext.startswith('.'):
        ext = '.' + ext
    return ext


def _ruta_ficha_desde_db(empresa: str, cip: str, extension: str | None) -> str | None:
    ext = _normalizar_extension(extension)
    if not ext:
        return None
    empresa_dir = normalizar_empresa_para_carpeta(empresa)
    return ruta_fichas(empresa_dir, str(cip), f"principal{ext}")


def obtener_ficha_base_db(cip: str) -> dict | None:
    asegurar_tabla_productos_ficha()
    cip = texto_seguro(cip)
    if not cip:
        return None
    conn = conectar_mysql()
    if not conn:
        return None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT
                cip,
                titulo_ficha,
                subtitulo,
                descripcion_corta,
                tipo_producto,
                origen,
                maduracion,
                presentacion,
                peso_aprox,
                ingredientes,
                conservacion,
                texto_comercial,
                nombre_producto,
                marca,
                categoria,
                contenido_neto,
                ean,
                observaciones_ficha,
                badge_1,
                badge_2,
                badge_3,
                etiquetas_retail,
                activo,
                fecha_actualizacion
            FROM productos_ficha_base
            WHERE TRIM(COALESCE(cip, '')) = TRIM(%s)
              AND COALESCE(activo, 1) = 1
            LIMIT 1
            """,
            (cip,),
        )
        row = cur.fetchone()
        cur.close()
        return _mapear_ficha_row(row) if row else None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _guardar_ficha_base(payload: ProductoFichaIn):
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a MySQL")
    try:
        cur = conn.cursor()
        nombre_producto = texto_seguro(payload.nombre_producto) or texto_seguro(payload.titulo_ficha)
        marca = texto_seguro(payload.marca) or texto_seguro(payload.subtitulo)
        categoria = texto_seguro(payload.categoria) or texto_seguro(payload.tipo_producto)
        contenido_neto = texto_seguro(payload.contenido_neto) or texto_seguro(payload.peso_aprox)
        observaciones_ficha = texto_seguro(payload.observaciones_ficha) or texto_seguro(payload.texto_comercial)
        titulo_ficha = texto_seguro(payload.titulo_ficha) or nombre_producto
        subtitulo = texto_seguro(payload.subtitulo) or marca
        tipo_producto = texto_seguro(payload.tipo_producto) or categoria
        peso_aprox = texto_seguro(payload.peso_aprox) or contenido_neto
        texto_comercial = texto_seguro(payload.texto_comercial) or observaciones_ficha
        cur.execute(
            """
            INSERT INTO productos_ficha_base (
                cip, titulo_ficha, subtitulo, descripcion_corta, tipo_producto,
                origen, maduracion, presentacion, peso_aprox, ingredientes,
                conservacion, texto_comercial, nombre_producto, marca, categoria,
                contenido_neto, ean, observaciones_ficha, badge_1, badge_2,
                badge_3, etiquetas_retail, activo, fecha_actualizacion
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, NOW()
            )
            ON DUPLICATE KEY UPDATE
                titulo_ficha = VALUES(titulo_ficha),
                subtitulo = VALUES(subtitulo),
                descripcion_corta = VALUES(descripcion_corta),
                tipo_producto = VALUES(tipo_producto),
                origen = VALUES(origen),
                maduracion = VALUES(maduracion),
                presentacion = VALUES(presentacion),
                peso_aprox = VALUES(peso_aprox),
                ingredientes = VALUES(ingredientes),
                conservacion = VALUES(conservacion),
                texto_comercial = VALUES(texto_comercial),
                nombre_producto = VALUES(nombre_producto),
                marca = VALUES(marca),
                categoria = VALUES(categoria),
                contenido_neto = VALUES(contenido_neto),
                ean = VALUES(ean),
                observaciones_ficha = VALUES(observaciones_ficha),
                badge_1 = VALUES(badge_1),
                badge_2 = VALUES(badge_2),
                badge_3 = VALUES(badge_3),
                etiquetas_retail = VALUES(etiquetas_retail),
                activo = VALUES(activo),
                fecha_actualizacion = NOW()
            """,
            (
                texto_seguro(payload.cip),
                titulo_ficha or None,
                subtitulo or None,
                texto_seguro(payload.descripcion_corta) or None,
                tipo_producto or None,
                texto_seguro(payload.origen) or None,
                texto_seguro(payload.maduracion) or None,
                texto_seguro(payload.presentacion) or None,
                peso_aprox or None,
                texto_seguro(payload.ingredientes) or None,
                texto_seguro(payload.conservacion) or None,
                texto_comercial or None,
                nombre_producto or None,
                marca or None,
                categoria or None,
                contenido_neto or None,
                texto_seguro(payload.ean) or None,
                observaciones_ficha or None,
                texto_seguro(payload.badge_1) or None,
                texto_seguro(payload.badge_2) or None,
                texto_seguro(payload.badge_3) or None,
                lista_a_csv(payload.etiquetas_retail),
                int(payload.activo or 0),
            ),
        )
        conn.commit()
        cur.close()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _mapear_ficha_row(row: dict | None) -> dict | None:
    if not row:
        return None
    row = dict(row)
    row['extension'] = _normalizar_extension(row.get('extension'))

    row['nombre_producto'] = texto_seguro(row.get('nombre_producto')) or texto_seguro(row.get('titulo_ficha'))
    row['marca'] = texto_seguro(row.get('marca')) or texto_seguro(row.get('subtitulo'))
    row['categoria'] = texto_seguro(row.get('categoria')) or texto_seguro(row.get('tipo_producto'))
    row['contenido_neto'] = texto_seguro(row.get('contenido_neto')) or texto_seguro(row.get('peso_aprox'))
    row['observaciones_ficha'] = texto_seguro(row.get('observaciones_ficha')) or texto_seguro(row.get('texto_comercial'))

    row['titulo_ficha'] = texto_seguro(row.get('titulo_ficha')) or row['nombre_producto']
    row['subtitulo'] = texto_seguro(row.get('subtitulo')) or row['marca']
    row['tipo_producto'] = texto_seguro(row.get('tipo_producto')) or row['categoria']
    row['peso_aprox'] = texto_seguro(row.get('peso_aprox')) or row['contenido_neto']
    row['texto_comercial'] = texto_seguro(row.get('texto_comercial')) or row['observaciones_ficha']
    row['imagenes_adicionales'] = []

    if not texto_seguro(row.get('imagen_path')):
        ruta = _ruta_ficha_desde_db(texto_seguro(row.get('empresa')), texto_seguro(row.get('cip')), row.get('extension'))
        if ruta:
            row['imagen_path'] = ruta

    fa = row.get('fecha_actualizacion')
    if fa and hasattr(fa, 'isoformat'):
        row['fecha_actualizacion'] = fa.isoformat()
    elif fa is not None:
        row['fecha_actualizacion'] = str(fa)
    else:
        row['fecha_actualizacion'] = None

    row['badge_1'] = texto_seguro(row.get('badge_1'))
    row['badge_2'] = texto_seguro(row.get('badge_2'))
    row['badge_3'] = texto_seguro(row.get('badge_3'))
    row['etiquetas_retail'] = csv_a_lista(row.get('etiquetas_retail'))
    row['premium_sort'] = int(row.get('premium_sort') or 0)
    row['premium_activo'] = int(row.get('premium_activo') or 1)
    row['activo'] = int(row.get('activo') or 0)
    return row


_CAMPOS_TECNICOS_COMPARTIDOS = (
    "nombre_producto", "titulo_ficha", "marca", "subtitulo", "categoria",
    "tipo_producto", "contenido_neto", "presentacion", "origen", "maduracion",
    "peso_aprox", "ean", "descripcion_corta", "ingredientes", "conservacion",
    "texto_comercial", "observaciones_ficha", "badge_1", "badge_2", "badge_3",
    "etiquetas_retail",
)


def _completar_ficha_con_datos_compartidos(cur, ficha: dict | None) -> dict | None:
    """Completa una ficha de empresa que sólo tiene imagen con los datos del
    mismo CIP ya capturados en otra empresa. La imagen y empresa destino nunca
    se sustituyen; sólo se heredan los datos técnicos compartidos."""
    if not ficha or not ficha.get("cip"):
        return ficha
    try:
        cur.execute(
            """
            SELECT *
            FROM productos_ficha
            WHERE cip = %s
              AND activo = 1
              AND empresa <> %s
            ORDER BY fecha_actualizacion DESC
            """,
            (str(ficha.get("cip")), str(ficha.get("empresa") or "")),
        )
        candidatas = [_mapear_ficha_row(row) for row in (cur.fetchall() or [])]
    except Exception:
        return ficha

    def puntaje(item: dict) -> int:
        return sum(1 for campo in _CAMPOS_TECNICOS_COMPARTIDOS if texto_seguro(item.get(campo)))

    candidatas = [item for item in candidatas if item]
    if not candidatas:
        return ficha
    fuente = max(candidatas, key=puntaje)
    if puntaje(fuente) == 0:
        return ficha

    for campo in _CAMPOS_TECNICOS_COMPARTIDOS:
        actual = texto_seguro(ficha.get(campo))
        origen = fuente.get(campo)
        # descripcion_corta igual al nombre suele ser el fallback del catálogo
        # de productos, no una descripción real de ficha.
        es_fallback_nombre = campo == "descripcion_corta" and actual and actual == texto_seguro(ficha.get("nombre_producto"))
        if (not actual or es_fallback_nombre) and texto_seguro(origen):
            ficha[campo] = origen
    return _mapear_ficha_row(ficha)


def obtener_ficha_producto_nueva(empresa: str, cip: str):
    asegurar_tabla_productos_ficha()
    conn = conectar_mysql()
    if not conn:
        return None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT
                pf.cip,
                pf.empresa,
                pf.extension,
                COALESCE(NULLIF(TRIM(pfb.nombre_producto), ''), NULLIF(TRIM(pf.nombre_producto), ''), NULLIF(TRIM(p.descripcion), '')) AS nombre_producto,
                COALESCE(pfb.marca, pf.marca) AS marca,
                COALESCE(pfb.categoria, pf.categoria) AS categoria,
                COALESCE(pfb.contenido_neto, pf.contenido_neto) AS contenido_neto,
                COALESCE(pfb.presentacion, pf.presentacion) AS presentacion,
                COALESCE(pfb.ingredientes, pf.ingredientes) AS ingredientes,
                COALESCE(pfb.conservacion, pf.conservacion) AS conservacion,
                COALESCE(pfb.origen, pf.origen) AS origen,
                COALESCE(pfb.ean, pf.ean) AS ean,
                COALESCE(NULLIF(TRIM(pfb.descripcion_corta), ''), NULLIF(TRIM(pf.descripcion_corta), ''), NULLIF(TRIM(p.descripcion), '')) AS descripcion_corta,
                COALESCE(pfb.observaciones_ficha, pf.observaciones_ficha) AS observaciones_ficha,
                COALESCE(pfb.titulo_ficha, pf.titulo_ficha) AS titulo_ficha,
                COALESCE(pfb.subtitulo, pf.subtitulo) AS subtitulo,
                COALESCE(pfb.tipo_producto, pf.tipo_producto) AS tipo_producto,
                COALESCE(pfb.maduracion, pf.maduracion) AS maduracion,
                COALESCE(pfb.peso_aprox, pf.peso_aprox) AS peso_aprox,
                COALESCE(pfb.texto_comercial, pf.texto_comercial) AS texto_comercial,
                pf.imagen_path,
                COALESCE(pfb.badge_1, pf.badge_1, p.badge_1) AS badge_1,
                COALESCE(pfb.badge_2, pf.badge_2, p.badge_2) AS badge_2,
                COALESCE(pfb.badge_3, pf.badge_3, p.badge_3) AS badge_3,
                COALESCE(pfb.etiquetas_retail, pf.etiquetas_retail, p.etiquetas_retail) AS etiquetas_retail,
                COALESCE(pf.premium_sort, p.premium_sort, 0) AS premium_sort,
                COALESCE(pf.premium_activo, p.premium_activo, 1) AS premium_activo,
                pf.activo,
                GREATEST(
                    COALESCE(pf.fecha_actualizacion, '1900-01-01'),
                    COALESCE(pfb.fecha_actualizacion, '1900-01-01')
                ) AS fecha_actualizacion
            FROM productos_ficha pf
            LEFT JOIN productos_ficha_base pfb ON pfb.cip = pf.cip
            LEFT JOIN productos p ON p.cip = pf.cip
            WHERE pf.empresa = %s
            AND pf.cip = %s
            AND pf.activo = 1
            LIMIT 1
            """,
            (empresa, str(cip)),
        )
        ficha = _mapear_ficha_row(cur.fetchone())
        ficha = _completar_ficha_con_datos_compartidos(cur, ficha)
        return enriquecer_imagenes_ficha(ficha)
    finally:
        if conn:
            conn.close()

def resolver_imagenes_producto(ficha: dict) -> dict:
    """
    Devuelve una estructura con imagen principal y adicionales.
    Si solo existe una imagen, el comportamiento queda igual que antes.
    """
    imagen_path = texto_seguro(ficha.get("imagen_path"))
    empresa = texto_seguro(ficha.get("empresa"))
    cip = texto_seguro(ficha.get("cip"))
    extensiones_validas = {".jpg", ".jpeg", ".png", ".webp"}
    principal = None
    adicionales: list[str] = []
    vistas = set()

    def agregar_unica(ruta: str | None, destino: str = "principal"):
        nonlocal principal, adicionales
        ruta_ok = next((candidate for candidate in rutas_equivalentes_ficha(ruta) if os.path.isfile(candidate)), None)
        if not ruta_ok:
            return
        ruta = ruta_ok
        key = os.path.normcase(os.path.abspath(ruta))
        if key in vistas:
            return
        vistas.add(key)
        if destino == "principal" and principal is None:
            principal = ruta
        else:
            adicionales.append(ruta)

    # imagen_path puede llegar desde otro equipo o desde una ruta UNC antigua.
    # agregar_unica resuelve las rutas equivalentes antes de validar el archivo;
    # no se debe descartar aquí sólo porque la ruta original no exista localmente.
    if imagen_path:
        agregar_unica(imagen_path, "principal")

    if not empresa or not cip:
        return {"principal": principal, "adicionales": adicionales, "todas": ([principal] if principal else []) + adicionales}

    empresa_dir = normalizar_empresa_para_carpeta(empresa)
    # La imagen sincronizada y la subida desde Administración usan esta misma
    # carpeta. No recorras todos los respaldos de red: uno sin conexión puede
    # bloquear la generación del PDF durante más de un minuto.
    carpetas_producto = [ruta_fichas(empresa_dir, cip)]
    if imagen_path and not str(imagen_path).startswith("\\\\"):
        carpetas_producto.append(os.path.dirname(imagen_path))
    vistas_carpetas = set()
    carpetas_producto = [
        carpeta for carpeta in carpetas_producto
        if not (os.path.normcase(os.path.abspath(carpeta)) in vistas_carpetas or vistas_carpetas.add(os.path.normcase(os.path.abspath(carpeta))))
        and os.path.isdir(carpeta)
    ]
    if not carpetas_producto:
        return {"principal": principal, "adicionales": adicionales, "todas": ([principal] if principal else []) + adicionales}

    candidatos_principales = [
        "producto.jpg",
        "producto.jpeg",
        "producto.png",
        "producto.webp",
        "principal.jpg",
        "principal.jpeg",
        "principal.png",
        "principal.webp",
        f"{cip}.jpg",
        f"{cip}.jpeg",
        f"{cip}.png",
        f"{cip}.webp",
    ]
    for carpeta_producto in carpetas_producto:
        for nombre in candidatos_principales:
            ruta = os.path.join(carpeta_producto, nombre)
            if os.path.isfile(ruta):
                agregar_unica(ruta, "principal")
                break

    try:
        adicionales_detectadas = []
        otros_validos = []
        for carpeta_producto in carpetas_producto:
            for archivo in sorted(os.listdir(carpeta_producto)):
                ruta = os.path.join(carpeta_producto, archivo)
                _, ext = os.path.splitext(archivo)
                if not os.path.isfile(ruta) or ext.lower() not in extensiones_validas:
                    continue
                nombre = archivo.lower()
                if nombre.startswith("adicional_"):
                    adicionales_detectadas.append(ruta)
                elif nombre.startswith("principal_") and ".bak" not in nombre:
                    otros_validos.append(ruta)
                elif nombre not in {x.lower() for x in candidatos_principales}:
                    otros_validos.append(ruta)

        for ruta in adicionales_detectadas:
            agregar_unica(ruta, "adicional")
        for ruta in otros_validos:
            if principal is None:
                agregar_unica(ruta, "principal")
            else:
                agregar_unica(ruta, "adicional")
    except Exception as e:
        print("ERROR BUSCANDO IMAGENES DEL PRODUCTO:", e)

    todas = ([principal] if principal else []) + adicionales
    return {"principal": principal, "adicionales": adicionales, "todas": todas}


def resolver_imagen_producto(ficha: dict) -> str | None:
    return resolver_imagenes_producto(ficha).get("principal")


def enriquecer_imagenes_ficha(ficha: dict | None) -> dict | None:
    if not ficha:
        return ficha
    ficha = dict(ficha)
    imagenes = resolver_imagenes_producto(ficha)
    ficha["imagen_path"] = imagenes.get("principal")
    ficha["imagenes_adicionales"] = list(imagenes.get("adicionales") or [])
    ficha["imagenes_disponibles"] = ([imagenes.get("principal")] if imagenes.get("principal") else []) + list(imagenes.get("adicionales") or [])
    return ficha


def dibujar_imagen_ajustada(c, ruta_imagen: str, x: float, y: float, w: float, h: float, radius: float = 12):
    if not ruta_imagen or not os.path.isfile(ruta_imagen):
        return
    try:
        # WebP no siempre conserva su canal alfa al pasarlo directo a ReportLab.
        # Se normaliza a PNG RGBA, conservando la transparencia original (sin
        # agregar un fondo blanco, negro o de ningún otro color).
        imagen_origen = ruta_imagen
        buffer_imagen = None
        if Image is not None:
            with Image.open(ruta_imagen) as original:
                original.load()
                es_webp = str(ruta_imagen).lower().endswith(".webp")
                if es_webp or original.mode in {"RGBA", "LA"} or "transparency" in original.info:
                    buffer_imagen = BytesIO()
                    original.convert("RGBA").save(buffer_imagen, format="PNG")
                    buffer_imagen.seek(0)
                    imagen_origen = buffer_imagen
        c.saveState()
        p = c.beginPath()
        p.roundRect(x, y, w, h, radius)
        c.clipPath(p, stroke=0, fill=0)
        c.drawImage(
            ImageReader(imagen_origen),
            x,
            y,
            width=w,
            height=h,
            preserveAspectRatio=True,
            anchor="c",
            mask="auto",
        )
        c.restoreState()
        if buffer_imagen is not None:
            buffer_imagen.close()
    except Exception as e:
        print("ERROR CARGANDO IMAGEN:", e)


def dibujar_galeria_miniaturas(c, rutas: list[str], x: float, y: float, w: float, h: float, colores: dict, max_items: int = 3):
    items = [r for r in (rutas or []) if r and os.path.isfile(r)][:max_items]
    if not items or h <= 0 or w <= 0:
        return
    gap = 8
    total_gap = gap * (len(items) - 1)
    thumb_w = max(38, (w - total_gap) / max(1, len(items)))
    thumb_h = h
    cur_x = x
    for ruta in items:
        c.setFillColor(colores["box_bg"])
        c.roundRect(cur_x, y, thumb_w, thumb_h, 8, stroke=0, fill=1)
        c.setStrokeColor(colores["line"])
        c.setLineWidth(0.6)
        c.roundRect(cur_x, y, thumb_w, thumb_h, 8, stroke=1, fill=0)
        dibujar_imagen_ajustada(c, ruta, cur_x + 3, y + 3, thumb_w - 6, thumb_h - 6, radius=6)
        cur_x += thumb_w + gap

def obtener_fichas_productos_nueva(empresa: str, cips: List[str]) -> List[dict]:
    asegurar_tabla_productos_ficha()
    cips_limpios = [str(c).strip() for c in cips if str(c).strip()]
    if not cips_limpios:
        return []

    placeholders = ",".join(["%s"] * len(cips_limpios))

    conn = conectar_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            f"""
            SELECT
                pf.cip,
                pf.empresa,
                pf.extension,
                COALESCE(NULLIF(TRIM(pfb.nombre_producto), ''), NULLIF(TRIM(pf.nombre_producto), ''), NULLIF(TRIM(p.descripcion), '')) AS nombre_producto,
                COALESCE(pfb.marca, pf.marca) AS marca,
                COALESCE(pfb.categoria, pf.categoria) AS categoria,
                COALESCE(pfb.contenido_neto, pf.contenido_neto) AS contenido_neto,
                COALESCE(pfb.presentacion, pf.presentacion) AS presentacion,
                COALESCE(pfb.ingredientes, pf.ingredientes) AS ingredientes,
                COALESCE(pfb.conservacion, pf.conservacion) AS conservacion,
                COALESCE(pfb.origen, pf.origen) AS origen,
                COALESCE(pfb.ean, pf.ean) AS ean,
                COALESCE(NULLIF(TRIM(pfb.descripcion_corta), ''), NULLIF(TRIM(pf.descripcion_corta), ''), NULLIF(TRIM(p.descripcion), '')) AS descripcion_corta,
                COALESCE(pfb.observaciones_ficha, pf.observaciones_ficha) AS observaciones_ficha,
                COALESCE(pfb.titulo_ficha, pf.titulo_ficha) AS titulo_ficha,
                COALESCE(pfb.subtitulo, pf.subtitulo) AS subtitulo,
                COALESCE(pfb.tipo_producto, pf.tipo_producto) AS tipo_producto,
                COALESCE(pfb.maduracion, pf.maduracion) AS maduracion,
                COALESCE(pfb.peso_aprox, pf.peso_aprox) AS peso_aprox,
                COALESCE(pfb.texto_comercial, pf.texto_comercial) AS texto_comercial,
                pf.imagen_path,
                COALESCE(pfb.badge_1, pf.badge_1, p.badge_1) AS badge_1,
                COALESCE(pfb.badge_2, pf.badge_2, p.badge_2) AS badge_2,
                COALESCE(pfb.badge_3, pf.badge_3, p.badge_3) AS badge_3,
                COALESCE(pfb.etiquetas_retail, pf.etiquetas_retail, p.etiquetas_retail) AS etiquetas_retail,
                COALESCE(pf.premium_sort, p.premium_sort, 0) AS premium_sort,
                COALESCE(pf.premium_activo, p.premium_activo, 1) AS premium_activo,
                pf.activo,
                GREATEST(
                    COALESCE(pf.fecha_actualizacion, '1900-01-01'),
                    COALESCE(pfb.fecha_actualizacion, '1900-01-01')
                ) AS fecha_actualizacion
            FROM productos_ficha pf
            LEFT JOIN productos_ficha_base pfb ON pfb.cip = pf.cip
            LEFT JOIN productos p ON p.cip = pf.cip
            WHERE pf.empresa = %s
              AND pf.activo = 1
              AND pf.cip IN ({placeholders})
            """,
            [empresa] + cips_limpios,
        )
        rows = cur.fetchall() or []
        mapa = {str(r['cip']): enriquecer_imagenes_ficha(_mapear_ficha_row(r)) for r in rows}
        return [mapa[cip] for cip in cips_limpios if cip in mapa]
    finally:
        conn.close()


def _upsert_ficha_data_impl(payload: ProductoFichaIn):
    asegurar_tabla_productos_ficha()
    _guardar_ficha_base(payload)
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a MySQL")

    empresa = texto_seguro(payload.empresa)
    cip = texto_seguro(payload.cip)
    if not empresa or not cip:
        raise HTTPException(status_code=400, detail="empresa y cip son obligatorios")

    extension = _normalizar_extension(payload.extension)
    imagen_path = texto_seguro(payload.imagen_path)
    if not imagen_path and extension:
        imagen_path = _ruta_ficha_desde_db(empresa, cip, extension) or ""

    nombre_producto = texto_seguro(payload.nombre_producto) or texto_seguro(payload.titulo_ficha)
    marca = texto_seguro(payload.marca) or texto_seguro(payload.subtitulo)
    categoria = texto_seguro(payload.categoria) or texto_seguro(payload.tipo_producto)
    contenido_neto = texto_seguro(payload.contenido_neto) or texto_seguro(payload.peso_aprox)
    observaciones_ficha = texto_seguro(payload.observaciones_ficha) or texto_seguro(payload.texto_comercial)

    titulo_ficha = texto_seguro(payload.titulo_ficha) or nombre_producto
    subtitulo = texto_seguro(payload.subtitulo) or marca
    tipo_producto = texto_seguro(payload.tipo_producto) or categoria
    peso_aprox = texto_seguro(payload.peso_aprox) or contenido_neto
    texto_comercial = texto_seguro(payload.texto_comercial) or observaciones_ficha
    badge_1 = texto_seguro(payload.badge_1) or None
    badge_2 = texto_seguro(payload.badge_2) or None
    badge_3 = texto_seguro(payload.badge_3) or None
    etiquetas_retail = lista_a_csv(payload.etiquetas_retail)
    premium_sort = int(payload.premium_sort or 0)
    premium_activo = int(payload.premium_activo or 0)

    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO productos_ficha (
                empresa, cip, extension,
                nombre_producto, marca, categoria, contenido_neto, presentacion,
                ingredientes, conservacion, origen, ean,
                descripcion_corta, observaciones_ficha,
                titulo_ficha, subtitulo, tipo_producto, maduracion,
                peso_aprox, texto_comercial, imagen_path,
                badge_1, badge_2, badge_3, etiquetas_retail, premium_sort, premium_activo,
                activo, fecha_actualizacion
            )
            VALUES (
                %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, NOW()
            )
            ON DUPLICATE KEY UPDATE
                extension = VALUES(extension),
                nombre_producto = VALUES(nombre_producto),
                marca = VALUES(marca),
                categoria = VALUES(categoria),
                contenido_neto = VALUES(contenido_neto),
                presentacion = VALUES(presentacion),
                ingredientes = VALUES(ingredientes),
                conservacion = VALUES(conservacion),
                origen = VALUES(origen),
                ean = VALUES(ean),
                descripcion_corta = VALUES(descripcion_corta),
                observaciones_ficha = VALUES(observaciones_ficha),
                titulo_ficha = VALUES(titulo_ficha),
                subtitulo = VALUES(subtitulo),
                tipo_producto = VALUES(tipo_producto),
                maduracion = VALUES(maduracion),
                peso_aprox = VALUES(peso_aprox),
                texto_comercial = VALUES(texto_comercial),
                imagen_path = VALUES(imagen_path),
                badge_1 = VALUES(badge_1),
                badge_2 = VALUES(badge_2),
                badge_3 = VALUES(badge_3),
                etiquetas_retail = VALUES(etiquetas_retail),
                premium_sort = VALUES(premium_sort),
                premium_activo = VALUES(premium_activo),
                activo = VALUES(activo),
                fecha_actualizacion = NOW()
            """,
            (
                empresa, cip, extension,
                nombre_producto or None,
                marca or None,
                categoria or None,
                contenido_neto or None,
                texto_seguro(payload.presentacion) or None,
                texto_seguro(payload.ingredientes) or None,
                texto_seguro(payload.conservacion) or None,
                texto_seguro(payload.origen) or None,
                texto_seguro(payload.ean) or None,
                texto_seguro(payload.descripcion_corta) or None,
                observaciones_ficha or None,
                titulo_ficha or None,
                subtitulo or None,
                tipo_producto or None,
                texto_seguro(payload.maduracion) or None,
                peso_aprox or None,
                texto_comercial or None,
                imagen_path or None,
                badge_1,
                badge_2,
                badge_3,
                etiquetas_retail,
                premium_sort,
                premium_activo,
                int(payload.activo or 0),
            ),
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    ficha = obtener_ficha_producto_nueva(empresa, cip)
    if not ficha:
        raise HTTPException(status_code=500, detail="No se pudo guardar la ficha")
    return _adjuntar_empresas_relacionadas_ficha(ficha, empresa)


@app.post("/catalogos/ficha-data", response_model=ProductoFichaOut)
def upsert_ficha_data(payload: ProductoFichaIn, user: dict = Depends(get_current_user)):
    return _upsert_ficha_data_impl(payload)


@app.get("/catalogos/ficha-data", response_model=ProductoFichaOut)
def get_ficha_data(empresa: str, cip: str):
    ficha = obtener_ficha_producto_nueva(empresa, cip)
    if not ficha:
        raise HTTPException(status_code=404, detail="Ficha no encontrada")
    return _adjuntar_empresas_relacionadas_ficha(ficha, empresa)


def _obtener_producto_base_para_ficha(empresa: str, cip: str) -> dict[str, Any] | None:
    conn = conectar_mysql()
    if not conn:
        return None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT p.cip, p.descripcion
            FROM productos p
            WHERE p.cip = %s
            LIMIT 1
            """,
            (str(cip).strip(),),
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        return {
            "cip": str(row.get("cip") or ""),
            "empresa": empresa,
            "empresas_relacionadas": [empresa] if empresa else [],
            "nombre_producto": str(row.get("descripcion") or "").strip(),
            "titulo_ficha": str(row.get("descripcion") or "").strip(),
            "descripcion_corta": str(row.get("descripcion") or "").strip(),
            "activo": 1,
            "premium_activo": 1,
            "premium_sort": 0,
            "etiquetas_retail": [],
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def listar_empresas_relacionadas_ficha(cip: str, solo_activas: bool = True) -> list[str]:
    asegurar_tabla_productos_ficha()
    cip = texto_seguro(cip)
    if not cip:
        return []
    conn = conectar_mysql()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        query = """
            SELECT DISTINCT empresa
            FROM productos_ficha
            WHERE TRIM(COALESCE(cip, '')) = TRIM(%s)
              AND TRIM(COALESCE(empresa, '')) <> ''
        """
        params = [cip]
        if solo_activas:
            query += " AND COALESCE(activo, 1) = 1"
        query += " ORDER BY empresa"
        cur.execute(query, params)
        empresas = []
        vistos = set()
        for row in (cur.fetchall() or []):
            nombre = " ".join(str((row[0] if row else "") or "").strip().split())
            if not nombre:
                continue
            clave = nombre.casefold()
            if clave in vistos:
                continue
            vistos.add(clave)
            empresas.append(nombre)
        cur.close()
        return empresas
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _adjuntar_empresas_relacionadas_ficha(ficha: dict | None, empresa_actual: str | None = None) -> dict | None:
    if not ficha:
        return ficha
    data = dict(ficha)
    empresas = listar_empresas_relacionadas_ficha(data.get("cip"), solo_activas=True)
    empresa_actual = " ".join(str(empresa_actual or data.get("empresa") or "").strip().split())
    if empresa_actual and empresa_actual.casefold() not in {e.casefold() for e in empresas}:
        empresas.insert(0, empresa_actual)
    data["empresas_relacionadas"] = empresas
    return data


@app.get("/web/api/catalogo/productos")
def web_catalogo_productos(
    request: Request,
    empresa: str = Query(..., min_length=1),
    q: str = Query("", max_length=200),
    limit: int = Query(200, ge=1, le=1000),
    usuario: dict = Depends(obtener_usuario_actual_web),
):
    resultado = listar_productos_catalogo(q=q, empresa=empresa, limit=limit)
    registrar_bitacora(usuario, "BUSCAR_PRODUCTO", empresa=empresa, detalle=f"q={q.strip()} limit={limit}", request=request)
    return resultado


@app.get("/web/api/catalogo/empresas")
def web_catalogo_empresas(usuario: dict = Depends(obtener_usuario_actual_web)):
    # Para web no dependemos solo de productos_ficha, porque eso puede ocultar
    # empresas que sí existen en el sistema pero aún no tienen fichas cargadas.
    base_empresas = ["Gourmet España", "Ibersur", "EZA2007", "Alimentos Europeos", "Aldeu"]
    conn = conectar_mysql()
    if not conn:
        return base_empresas
    try:
        cur = conn.cursor()
        empresas = []
        vistos = set()

        for nombre in base_empresas:
            nombre = normalizar_nombre_empresa_catalogo(nombre)
            key = texto_seguro(nombre).lower()
            if key and key not in vistos:
                vistos.add(key)
                empresas.append(nombre)

        for sql in [
            "SELECT DISTINCT empresa FROM productos_ficha WHERE TRIM(COALESCE(empresa,'')) <> '' ORDER BY empresa",
            "SELECT DISTINCT empresa FROM clientes WHERE TRIM(COALESCE(empresa,'')) <> '' ORDER BY empresa",
        ]:
            try:
                cur.execute(sql)
                for r in cur.fetchall():
                    nombre = normalizar_nombre_empresa_catalogo(str(r[0] or "").strip())
                    key = texto_seguro(nombre).lower()
                    if nombre and key not in vistos:
                        vistos.add(key)
                        empresas.append(nombre)
            except Exception:
                pass

        cur.close()
        return empresas
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/web/api/catalogo/ficha-pdf")
def web_catalogo_ficha_pdf(
    empresa: str,
    cip: str,
    request: Request,
    usuario: dict = Depends(obtener_usuario_actual_web),
):
    registrar_bitacora(usuario, "VER_FICHA", empresa=empresa, cip=cip, detalle="Vista de ficha PDF", request=request)
    return get_ficha_pdf(empresa=empresa, cip=cip)


@app.post("/web/api/catalogo/catalogo-pdf")
def web_catalogo_pdf(
    payload: CatalogoPdfIn,
    request: Request,
    usuario: dict = Depends(obtener_usuario_actual_web),
):
    registrar_bitacora(
        usuario,
        "GENERAR_CATALOGO",
        empresa=payload.empresa,
        cips=payload.cips,
        detalle=f"{len(payload.cips)} productos",
        request=request,
    )
    return post_catalogo_pdf(payload)


@app.get("/admin/api/fichas/{empresa}/{cip}", response_model=ProductoFichaOut)
def admin_api_ficha_detalle(
    empresa: str,
    cip: str,
    usuario: dict = Depends(requiere_admin),
):
    return _resolver_ficha_admin_para_empresa(empresa, cip)


@app.get("/admin/api/fichas/preview-imagen")
def admin_api_preview_imagen(
    empresa: str,
    cip: str,
    archivo: str,
    usuario: dict = Depends(requiere_admin),
):
    empresa = str(empresa or "").strip()
    cip = str(cip or "").strip()
    archivo = os.path.basename(str(archivo or "").strip())
    if not empresa or not cip or not archivo:
        raise HTTPException(status_code=400, detail="Faltan datos para la vista previa")

    carpeta = ruta_fichas(normalizar_empresa_para_carpeta(empresa), cip)
    ruta = os.path.join(carpeta, archivo)
    if not os.path.isfile(ruta):
        raise HTTPException(status_code=404, detail="La imagen no existe")

    media_type = mimetypes.guess_type(ruta)[0] or "application/octet-stream"
    return FileResponse(ruta, media_type=media_type)


@app.post("/admin/api/fichas/guardar", response_model=ProductoFichaOut)
def admin_api_guardar_ficha(
    payload: ProductoFichaIn,
    request: Request,
    usuario: dict = Depends(requiere_admin),
):
    empresa_actual = texto_seguro(payload.empresa)
    cip = texto_seguro(payload.cip)
    if not empresa_actual or not cip:
        raise HTTPException(status_code=400, detail="empresa y cip son obligatorios")

    seleccionadas = []
    vistos = set()
    for nombre in list(payload.empresas_relacionadas or []):
        limpio = " ".join(str(nombre or "").strip().split())
        if not limpio:
            continue
        clave = limpio.casefold()
        if clave in vistos:
            continue
        vistos.add(clave)
        seleccionadas.append(limpio)
    if not seleccionadas:
        raise HTTPException(status_code=400, detail="Selecciona al menos una empresa para la ficha")

    existentes = listar_empresas_relacionadas_ficha(cip, solo_activas=False)
    accion = "EDITAR_FICHA" if obtener_ficha_producto_nueva(payload.empresa, payload.cip) else "CREAR_FICHA"

    resultado = None
    for empresa_destino in seleccionadas:
        existente = obtener_ficha_producto_nueva(empresa_destino, cip) or {}
        payload_empresa = payload.model_copy(
            update={
                "empresa": empresa_destino,
                "empresas_relacionadas": seleccionadas,
                "extension": payload.extension if empresa_destino == empresa_actual else (existente.get("extension") or payload.extension),
                "imagen_path": payload.imagen_path if empresa_destino == empresa_actual else (existente.get("imagen_path") or payload.imagen_path),
                "activo": 1,
            }
        )
        guardada = _upsert_ficha_data_impl(payload_empresa)
        if empresa_destino.casefold() == empresa_actual.casefold():
            resultado = guardada

    empresas_deseleccionadas = [
        emp for emp in existentes
        if emp.casefold() not in {s.casefold() for s in seleccionadas}
    ]
    if empresas_deseleccionadas:
        conn = conectar_mysql()
        if conn:
            try:
                cur = conn.cursor()
                for empresa_destino in empresas_deseleccionadas:
                    cur.execute(
                        """
                        UPDATE productos_ficha
                           SET activo = 0,
                               fecha_actualizacion = NOW()
                         WHERE TRIM(COALESCE(empresa, '')) = TRIM(%s)
                           AND TRIM(COALESCE(cip, '')) = TRIM(%s)
                        """,
                        (empresa_destino, cip),
                    )
                conn.commit()
                cur.close()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    if not resultado:
        resultado = _resolver_ficha_admin_para_empresa(empresa_actual, cip)

    registrar_bitacora(
        usuario,
        accion,
        empresa=payload.empresa,
        cip=payload.cip,
        detalle=f"Guardado desde admin web para empresas: {', '.join(seleccionadas)}",
        request=request,
    )
    return resultado


@app.post("/admin/api/fichas/subir-imagen")
def admin_api_subir_imagen(
    request: Request,
    empresa: str = Form(...),
    cip: str = Form(...),
    tipo_imagen: str = Form("principal"),
    indice: int = Form(1),
    archivo: UploadFile = FastAPIFile(...),
    usuario: dict = Depends(requiere_admin),
):
    empresa = str(empresa or "").strip()
    cip = str(cip or "").strip()
    tipo_imagen = str(tipo_imagen or "principal").strip().lower()
    if tipo_imagen not in {"principal", "adicional"}:
        raise HTTPException(status_code=400, detail="tipo_imagen debe ser principal o adicional")
    if tipo_imagen == "adicional":
        indice = max(1, int(indice or 1))

    nombre_original = str(archivo.filename or "")
    ext = os.path.splitext(nombre_original)[1].lower().strip()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=400, detail="Extension no permitida")

    carpeta = ruta_fichas(normalizar_empresa_para_carpeta(empresa), cip)
    os.makedirs(carpeta, exist_ok=True)
    prefijo = "principal" if tipo_imagen == "principal" else f"adicional_{indice}"
    destino = os.path.join(carpeta, f"{prefijo}{ext}")

    existentes = [x for x in os.listdir(carpeta) if x.lower().startswith(f"{prefijo.lower()}.")]
    for anterior in existentes:
        ruta_anterior = os.path.join(carpeta, anterior)
        if os.path.abspath(ruta_anterior).lower() == os.path.abspath(destino).lower():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = os.path.join(carpeta, f"{prefijo}_{timestamp}.bak{os.path.splitext(anterior)[1]}")
            try:
                os.replace(ruta_anterior, bak)
            except Exception:
                pass

    contenido = archivo.file.read()
    with open(destino, "wb") as f:
        f.write(contenido)

    payload = ProductoFichaIn(
        empresa=empresa,
        cip=cip,
        extension=ext,
        imagen_path=destino,
        activo=1,
        premium_activo=1,
    )
    ficha_existente = obtener_ficha_producto_nueva(empresa, cip)
    if ficha_existente:
        payload = ProductoFichaIn(**{**ficha_existente, "empresa": empresa, "cip": cip, "extension": ext, "imagen_path": destino})
    else:
        base = _obtener_producto_base_para_ficha(empresa, cip)
        if base:
            payload = ProductoFichaIn(**{**base, "empresa": empresa, "cip": cip, "extension": ext, "imagen_path": destino})

    ficha_guardada = _upsert_ficha_data_impl(payload)
    registrar_bitacora(
        usuario,
        "SUBIR_IMAGEN",
        empresa=empresa,
        cip=cip,
        detalle=f"Imagen {tipo_imagen} actualizada: {os.path.basename(destino)}",
        request=request,
    )
    return {"ok": True, "ruta": destino, "ficha": _adjuntar_empresas_relacionadas_ficha(ficha_guardada, empresa)}


@app.post("/admin/api/fichas/seleccionar-principal")
def admin_api_seleccionar_principal(
    payload: SeleccionarPrincipalIn,
    request: Request,
    usuario: dict = Depends(requiere_admin),
):
    empresa = str(payload.empresa or "").strip()
    cip = str(payload.cip or "").strip()
    archivo = os.path.basename(str(payload.archivo or "").strip())
    if not empresa or not cip or not archivo:
        raise HTTPException(status_code=400, detail="Faltan datos para seleccionar la imagen principal")

    carpeta = ruta_fichas(normalizar_empresa_para_carpeta(empresa), cip)
    if not os.path.isdir(carpeta):
        raise HTTPException(status_code=404, detail="No existe la carpeta del producto")

    ruta_seleccionada = os.path.join(carpeta, archivo)
    if not os.path.isfile(ruta_seleccionada):
        raise HTTPException(status_code=404, detail="La imagen seleccionada no existe")

    nombre_sel, ext_sel = os.path.splitext(archivo)
    if ext_sel.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=400, detail="La imagen seleccionada no tiene una extensiÃ³n vÃ¡lida")

    if nombre_sel.lower() == "principal":
        ficha = _adjuntar_empresas_relacionadas_ficha(obtener_ficha_producto_nueva(empresa, cip), empresa)
        return {"ok": True, "ficha": ficha}

    actuales_principales = [
        os.path.join(carpeta, x)
        for x in os.listdir(carpeta)
        if x.lower().startswith("principal.") and os.path.isfile(os.path.join(carpeta, x))
    ]
    ruta_principal_actual = actuales_principales[0] if actuales_principales else None
    temp_swap = os.path.join(carpeta, f"swap_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{ext_sel}")
    nuevo_principal = os.path.join(carpeta, f"principal{ext_sel.lower()}")

    try:
        os.replace(ruta_seleccionada, temp_swap)
        if ruta_principal_actual and os.path.isfile(ruta_principal_actual):
            os.replace(ruta_principal_actual, ruta_seleccionada)
        os.replace(temp_swap, nuevo_principal)
    except Exception as e:
        try:
            if os.path.isfile(temp_swap) and not os.path.isfile(ruta_seleccionada):
                os.replace(temp_swap, ruta_seleccionada)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"No se pudo cambiar la imagen principal: {e}")

    ficha_existente = obtener_ficha_producto_nueva(empresa, cip) or {}
    payload_upsert = ProductoFichaIn(**{
        **ficha_existente,
        "empresa": empresa,
        "cip": cip,
        "extension": ext_sel.lower(),
        "imagen_path": nuevo_principal,
        "activo": 1,
        "premium_activo": int(ficha_existente.get("premium_activo", 1) or 1),
    })
    ficha_guardada = _upsert_ficha_data_impl(payload_upsert)
    ficha_actualizada = _adjuntar_empresas_relacionadas_ficha(
        enriquecer_imagenes_ficha(obtener_ficha_producto_nueva(empresa, cip) or ficha_guardada),
        empresa
    )
    registrar_bitacora(
        usuario,
        "EDITAR_FICHA",
        empresa=empresa,
        cip=cip,
        detalle=f"Imagen principal actualizada manualmente a {os.path.basename(nuevo_principal)} desde {archivo}",
        request=request,
    )
    return {"ok": True, "ficha": ficha_actualizada}


@app.post("/admin/api/fichas/eliminar-imagen")
def admin_api_eliminar_imagen(
    payload: SeleccionarPrincipalIn,
    request: Request,
    usuario: dict = Depends(requiere_admin),
):
    empresa = str(payload.empresa or "").strip()
    cip = str(payload.cip or "").strip()
    archivo = os.path.basename(str(payload.archivo or "").strip())
    if not empresa or not cip or not archivo:
        raise HTTPException(status_code=400, detail="Faltan datos para eliminar la imagen")

    carpeta = ruta_fichas(normalizar_empresa_para_carpeta(empresa), cip)
    ruta = os.path.join(carpeta, archivo)
    if not os.path.isfile(ruta):
        raise HTTPException(status_code=404, detail="La imagen seleccionada no existe")

    try:
        os.remove(ruta)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo eliminar la imagen: {e}")

    ficha = obtener_ficha_producto_nueva(empresa, cip)
    ficha = enriquecer_imagenes_ficha(ficha)
    if ficha and os.path.normcase(os.path.abspath(texto_seguro(ficha.get("imagen_path")))) == os.path.normcase(os.path.abspath(ruta)):
        payload_upsert = ProductoFichaIn(**{
            **ficha,
            "empresa": empresa,
            "cip": cip,
            "imagen_path": "",
            "activo": 1,
            "premium_activo": int(ficha.get("premium_activo", 1) or 1),
        })
        ficha = _upsert_ficha_data_impl(payload_upsert)
        ficha = _adjuntar_empresas_relacionadas_ficha(enriquecer_imagenes_ficha(ficha), empresa)
    registrar_bitacora(
        usuario,
        "ELIMINAR_IMAGEN",
        empresa=empresa,
        cip=cip,
        detalle=f"Imagen eliminada: {archivo}",
        request=request,
    )
    return {"ok": True, "ficha": ficha}


@app.get("/admin/api/bitacora", response_model=List[BitacoraOut])
def admin_api_bitacora(
    usuario_filtro: str | None = Query(None, alias="usuario"),
    accion: str | None = None,
    empresa: str | None = None,
    cip: str | None = None,
    fecha_inicio: str | None = None,
    fecha_fin: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
    usuario: dict = Depends(requiere_admin),
):
    asegurar_tabla_bitacora_catalogos()
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a MySQL")
    try:
        cur = conn.cursor(dictionary=True)
        query = """
            SELECT id, usuario_id, usuario, nivel, accion, empresa, cip, cips, detalle, ip, user_agent, fecha
            FROM bitacora_catalogos
            WHERE 1=1
        """
        params: list[Any] = []
        if usuario_filtro:
            query += " AND usuario LIKE %s"
            params.append(f"%{usuario_filtro.strip()}%")
        if accion:
            query += " AND accion = %s"
            params.append(accion.strip())
        if empresa:
            query += " AND empresa = %s"
            params.append(empresa.strip())
        if cip:
            query += " AND cip = %s"
            params.append(cip.strip())
        if fecha_inicio:
            query += " AND DATE(fecha) >= %s"
            params.append(fecha_inicio.strip())
        if fecha_fin:
            query += " AND DATE(fecha) <= %s"
            params.append(fecha_fin.strip())
        query += " ORDER BY fecha DESC, id DESC LIMIT %s"
        params.append(limit)
        cur.execute(query, params)
        rows = cur.fetchall() or []
        cur.close()
        resultado = []
        for row in rows:
            row = dict(row)
            if row.get("fecha") and hasattr(row["fecha"], "isoformat"):
                row["fecha"] = row["fecha"].isoformat(sep=" ")
            resultado.append(row)
        return resultado
    finally:
        try:
            conn.close()
        except Exception:
            pass


def texto_seguro(v):
    return reparar_mojibake((v or "").strip())


def csv_a_lista(valor: str | None) -> list[str]:
    if not valor:
        return []
    partes = re.split(r"[,|;\n]+", str(valor))
    salida = []
    for p in partes:
        t = " ".join(str(p).strip().split())
        if t and t not in salida:
            salida.append(t)
    return salida[:12]


def lista_a_csv(items: list[str] | None) -> str | None:
    if not items:
        return None
    limpios = []
    for item in items:
        t = " ".join(str(item).strip().split())
        if t and t not in limpios:
            limpios.append(t)
    return ", ".join(limpios) if limpios else None


def badges_manuales_de_ficha(ficha: dict) -> list[str]:
    salida = []
    for key in ("badge_1", "badge_2", "badge_3"):
        val = texto_seguro(ficha.get(key))
        if val and val not in salida:
            salida.append(val)
    return salida[:3]


def _directorios_badges() -> list[str]:
    candidatos = [
        r"\\Server_galactico\Proyectos\COMANDAS060625\icons_badges",
        os.path.join(_runtime_base_dir(), "icons_badges"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons_badges"),
        os.path.join(os.getcwd(), "icons_badges"),
    ]
    vistos = set()
    dirs = []
    for ruta in candidatos:
        if not ruta:
            continue
        normalizada = os.path.normcase(os.path.normpath(ruta))
        if normalizada in vistos:
            continue
        vistos.add(normalizada)
        dirs.append(ruta)
    return dirs


def obtener_icono_badge(tipo: str) -> str | None:
    nombres = {
        "bandera_es": "bandera_es.png",
        "queso_entero": "queso_entero.png",
        "jamon_entero": "jamon_entero.png",
        "jamon_medio": "jamon_medio.png",
        "jamon_rebanado": "jamon_rebanado.png",
        "embutido": "embutidos.png",
    }
    nombre_archivo = nombres.get(tipo)
    if not nombre_archivo:
        return None
    for base in _directorios_badges():
        ruta = os.path.join(base, nombre_archivo)
        if os.path.isfile(ruta):
            return ruta
    return None


def detectar_badges_con_icono(ficha: dict) -> list[dict]:
    badges = []

    origen = texto_seguro(ficha.get("origen")).lower()
    presentacion = texto_seguro(ficha.get("presentacion")).lower()
    categoria = " ".join([
        texto_seguro(ficha.get("categoria")),
        texto_seguro(ficha.get("tipo_producto")),
        texto_seguro(ficha.get("nombre_producto")),
        texto_seguro(ficha.get("titulo_ficha")),
    ]).lower()

    # BADGE ORIGEN
    if "espa" in origen:
        badges.append({
            "texto": "España",
            "icono": obtener_icono_badge("bandera_es"),
            "kind": "origen_es",
        })
    elif origen:
        badges.append({
            "texto": texto_seguro(ficha.get("origen")),
            "icono": None,
            "kind": "origen",
        })

    # BADGE QUESO
    if "queso" in categoria:
        if any(x in presentacion for x in ["pieza entera", "pieza completa", "entero", "rueda completa"]):
            badges.append({
                "texto": "Queso entero",
                "icono": obtener_icono_badge("queso_entero"),
                "kind": "queso_entero",
            })

    # BADGE JAMON
    if any(x in categoria for x in ["jamon", "jamÃ³n", "paleta"]):
        if any(x in presentacion for x in ["deshuesado", "deshuesada"]):
            badges.append({
                "texto": "JamÃ³n deshuesado",
                "icono": obtener_icono_badge("jamon_medio"),
                "kind": "jamon_deshuesado",
            })
        elif any(x in presentacion for x in ["pieza entera", "pieza completa", "entero"]):
            badges.append({
                "texto": "JamÃ³n entero",
                "icono": obtener_icono_badge("jamon_entero"),
                "kind": "jamon_entero",
            })
        elif any(x in presentacion for x in ["medio", "mitad", "media pieza"]):
            badges.append({
                "texto": "Medio jamÃ³n",
                "icono": obtener_icono_badge("jamon_medio"),
                "kind": "jamon_medio",
            })
        elif any(x in presentacion for x in ["rebanado", "loncheado", "fileteado", "corte", "rebanadas", "estuche"]):
            badges.append({
                "texto": "Jam?n rebanado",
                "icono": obtener_icono_badge("jamon_rebanado"),
                "kind": "jamon_rebanado",
            })

    # BADGE EMBUTIDO
    if any(x in categoria for x in ["embutido", "embutidos", "chorizo", "salchichon", "salchich?n", "fuet", "longaniza", "sobrasada", "lomo embuchado"]):
        badges.append({
            "texto": "Embutido",
            "icono": obtener_icono_badge("embutido"),
            "kind": "embutido",
        })

    return badges[:3]

def dibujar_bloque_texto(c, x, y, ancho, titulo, valor, font_size=10, leading=13):
    titulo = texto_seguro(titulo)
    valor = texto_seguro(valor)
    if not valor:
        return y

    if titulo:
        c.setFont("Helvetica-Bold", font_size)
        c.drawString(x, y, f"{titulo}:")
        y -= leading

    c.setFont("Helvetica", font_size)
    palabras = valor.split()
    linea = ""
    for palabra in palabras:
        prueba = f"{linea} {palabra}".strip()
        if c.stringWidth(prueba, "Helvetica", font_size) <= ancho:
            linea = prueba
        else:
            c.drawString(x, y, linea)
            y -= leading
            linea = palabra
    if linea:
        c.drawString(x, y, linea)
        y -= leading

    y -= 4
    return y


def dibujar_bloque_texto_formateado(c, x, y, ancho, titulo, valor, font_size=9, leading=11):
    titulo = texto_seguro(titulo)
    valor = texto_seguro(valor).replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    if not valor:
        return y

    font_name = "Helvetica"
    if titulo:
        c.setFont("Helvetica-Bold", font_size + 2)
        c.drawString(x, y, titulo)
        y -= leading + 5

    c.setFont(font_name, font_size)

    for raw_line in valor.split("\n"):
        if raw_line == "":
            y -= leading
            continue

        expanded = raw_line.rstrip()
        leading_spaces = len(expanded) - len(expanded.lstrip(" "))
        indent_text = " " * leading_spaces
        indent_width = min(c.stringWidth(indent_text, font_name, font_size), ancho * 0.35)
        text = expanded.lstrip(" ")

        if not text:
            y -= leading
            continue

        available = max(24, ancho - indent_width)
        words = text.split(" ")
        line = ""

        for word in words:
            if word == "":
                if line:
                    line += " "
                continue
            candidate = f"{line} {word}".strip()
            if not line or c.stringWidth(candidate, font_name, font_size) <= available:
                line = candidate
                continue

            draw_x = x + indent_width
            c.drawString(draw_x, y, line)
            y -= leading
            line = word

        if line:
            draw_x = x + indent_width
            c.drawString(draw_x, y, line)
            y -= leading

    y -= 4
    return y


def colores_por_empresa(empresa: str) -> dict:
    emp = texto_seguro(empresa).lower()

    if emp == "gourmet españa" or emp == "gourmet espana":
        return {
            "header_bg": HexColor("#2E7D32"),
            "accent": HexColor("#1B5E20"),
            "accent_soft": HexColor("#E2F3E5"),
            "box_bg": HexColor("#F1F8F2"),
            "line": HexColor("#C8E6C9"),
            "text_on_header": white,
            "text_main": black,
        }

    if emp == "ibersur":
        return {
            "header_bg": HexColor("#1F3A5F"),
            "accent": HexColor("#16304D"),
            "accent_soft": HexColor("#DDE8F3"),
            "box_bg": HexColor("#EEF3F8"),
            "line": HexColor("#C7D4E3"),
            "text_on_header": white,
            "text_main": black,
        }

    if emp == "eza2007":
        return {
            "header_bg": HexColor("#6D213C"),
            "accent": HexColor("#54172D"),
            "accent_soft": HexColor("#F1DDE4"),
            "box_bg": HexColor("#F8EEF1"),
            "line": HexColor("#E4C7D0"),
            "text_on_header": white,
            "text_main": black,
        }

    if emp == "alimentos europeos":
        return {
            "header_bg": HexColor("#111111"),
            "accent": HexColor("#000000"),
            "accent_soft": HexColor("#E6E6E6"),
            "box_bg": HexColor("#F5F5F5"),
            "line": HexColor("#CFCFCF"),
            "text_on_header": white,
            "text_main": black,
        }

    return {
        "header_bg": HexColor("#1F2937"),
        "accent": HexColor("#111827"),
        "accent_soft": HexColor("#E5E7EB"),
        "box_bg": HexColor("#F3F4F6"),
        "line": HexColor("#D1D5DB"),
        "text_on_header": white,
        "text_main": black,
    }





def construir_badges_automaticos(ficha: dict) -> list[str]:
    manuales = badges_manuales_de_ficha(ficha)
    auto = []

    categoria = texto_seguro(ficha.get("categoria") or ficha.get("tipo_producto")).lower()
    origen = texto_seguro(ficha.get("origen")).lower()
    conservacion = texto_seguro(ficha.get("conservacion")).lower()
    contenido = texto_seguro(ficha.get("contenido_neto") or ficha.get("peso_aprox")).lower()

    if "iber" in categoria:
        auto.append("SelecciÃ³n ibÃ©rica")
    if "espa" in origen:
        auto.append("Origen España")
    if "refriger" in conservacion or "0" in conservacion or "4" in conservacion:
        auto.append("Cadena de frÃ­o")
    if any(x in contenido for x in ["kg", "g", "ml"]):
        auto.append("Presentación retail")

    salida = []
    for b in manuales + auto:
        b = texto_seguro(b)
        if b and b not in salida:
            salida.append(b)
    return salida[:6]


def construir_etiquetas_retail(ficha: dict) -> list[str]:
    etiquetas = csv_a_lista(ficha.get("etiquetas_retail"))
    categoria = " ".join([
        texto_seguro(ficha.get("categoria")),
        texto_seguro(ficha.get("tipo_producto")),
        texto_seguro(ficha.get("titulo_ficha")),
        texto_seguro(ficha.get("nombre_producto")),
    ]).lower()
    comercial = " ".join([
        texto_seguro(ficha.get("texto_comercial")),
        texto_seguro(ficha.get("observaciones_ficha")),
        texto_seguro(ficha.get("descripcion_corta")),
    ]).lower()

    if any(k in categoria for k in ["iberico", "ibÃ©rico", "reserva", "gran reserva", "curado", "mancha"]):
        etiquetas.append("Premium")
    if "alta rotaciÃ³n" in comercial or "alta rotacion" in comercial:
        etiquetas.append("Top ventas")
    if any(k in comercial for k in ["ideal", "recomend", "seleccion", "selecciÃ³n"]):
        etiquetas.append("Recomendado")

    out = []
    seen = set()
    for e in etiquetas:
        key = texto_seguro(e).lower()
        if key and key not in seen:
            seen.add(key)
            out.append(" ".join(str(e).strip().split()))
    return out[:6]


def dibujar_pills(c, pills: list[str], x: float, y: float, max_width: float,
                  text_color, bg_color, font_name="Helvetica-Bold", font_size=7.5,
                  height=12, gap=5, max_items=4) -> float:
    cx = x
    items = 0
    for pill in pills:
        pill = texto_seguro(pill)
        if not pill:
            continue
        if items >= max_items:
            break
        width = min(max(46, c.stringWidth(pill, font_name, font_size) + 12), 120)
        if cx + width > x + max_width:
            break
        c.setFillColor(bg_color)
        c.roundRect(cx, y, width, height, height / 2.0, stroke=0, fill=1)
        c.setFillColor(text_color)
        c.setFont(font_name, font_size)
        c.drawCentredString(cx + width / 2.0, y + 3, pill[:26])
        cx += width + gap
        items += 1
    return cx

def preparar_ficha_visual(ficha: dict) -> dict:
    ficha = dict(ficha or {})

    titulo = texto_seguro(ficha.get("nombre_producto")) or texto_seguro(ficha.get("titulo_ficha")) or f"CIP {texto_seguro(ficha.get('cip'))}"
    subtitulo = texto_seguro(ficha.get("marca")) or texto_seguro(ficha.get("subtitulo")) or "Ficha tecnica"
    categoria = texto_seguro(ficha.get("categoria")) or texto_seguro(ficha.get("tipo_producto"))
    tipo = texto_seguro(ficha.get("tipo_producto")) or categoria
    contenido_neto = texto_seguro(ficha.get("contenido_neto"))
    peso_aprox = texto_seguro(ficha.get("peso_aprox"))
    descripcion = texto_seguro(ficha.get("descripcion_corta"))
    comercial = texto_seguro(ficha.get("texto_comercial")) or texto_seguro(ficha.get("observaciones_ficha"))
    observaciones = texto_seguro(ficha.get("observaciones_ficha"))
    ean = texto_seguro(ficha.get("ean"))

    badge_secundario = contenido_neto or peso_aprox or texto_seguro(ficha.get("presentacion"))

    campos_detalle = [
        ("CIP", texto_seguro(ficha.get("cip"))),
        ("Empresa", texto_seguro(ficha.get("empresa"))),
        ("Categoría", categoria),
        ("Tipo de producto", tipo),
        ("Origen", texto_seguro(ficha.get("origen"))),
        ("Maduración", texto_seguro(ficha.get("maduracion"))),
        ("Presentación", texto_seguro(ficha.get("presentacion"))),
        ("Contenido neto", contenido_neto),
        ("Peso aprox.", peso_aprox),
        ("EAN", ean),
    ]
    campos_detalle = [(k, v) for k, v in campos_detalle if texto_seguro(v)]

    campos_catalogo = [
        ("Marca", texto_seguro(ficha.get("marca"))),
        ("Categoría", categoria),
        ("Presentación", texto_seguro(ficha.get("presentacion"))),
        ("Contenido", contenido_neto or peso_aprox),
        ("Origen", texto_seguro(ficha.get("origen"))),
        ("EAN", ean),
    ]
    campos_catalogo = [(k, v) for k, v in campos_catalogo if texto_seguro(v)]

    ficha["_visual"] = {
        "cip": texto_seguro(ficha.get("cip")),
        "titulo": titulo,
        "subtitulo": subtitulo,
        "categoria": categoria,
        "tipo": tipo,
        "contenido_neto": contenido_neto,
        "peso_aprox": peso_aprox,
        "descripcion": descripcion,
        "comercial": comercial,
        "observaciones": observaciones,
        "ean": ean,
        "badge_secundario": badge_secundario,
        "campos_detalle": campos_detalle,
        "campos_catalogo": campos_catalogo,
        "badges_manuales": badges_manuales_de_ficha(ficha),
        "badges_icono": detectar_badges_con_icono(ficha),
        "badges_automaticos": construir_badges_automaticos(ficha),
        "etiquetas_retail": construir_etiquetas_retail(ficha),
    }
    return ficha

def dibujar_chips_premium(c, items, x, y, colores, max_items=4, max_width=360):
    procesados = []
    for item in (items or [])[:max_items]:
        if isinstance(item, dict):
            texto = texto_seguro(item.get("texto"))
            icono = item.get("icono")
            kind = texto_seguro(item.get("kind"))
        else:
            texto = texto_seguro(item)
            icono = None
            kind = ""

        if texto:
            procesados.append({"texto": texto[:28], "icono": icono, "kind": kind})

    if not procesados:
        return y

    def colores_badge(item):
        kind = (item.get("kind") or "").lower()
        texto = (item.get("texto") or "").lower()

        if kind == "origen_es" or "espa" in texto:
            return HexColor("#FFF1F1"), HexColor("#B42318")
        if "queso" in kind or "queso" in texto:
            return HexColor("#FFF7E6"), HexColor("#B26A00")
        if "deshuesado" in kind or "deshuesado" in texto:
            return HexColor("#FCE7F3"), HexColor("#9D174D")
        if "jamon" in kind or "jamÃ³n" in texto or "jamon" in texto:
            return HexColor("#FDF2F2"), HexColor("#9F1239")
        if texto.startswith("cip "):
            return HexColor("#F3F4F6"), HexColor("#374151")
        return colores["accent_soft"], colores["accent"]

    chip_x = x
    chip_y = y
    row_height = 30
    chip_height = 26
    bottom_y = y   # ðŸ”¥ ESTA ES LA QUE FALTA
    icon_size = 18
    icon_gap = 8
    pad_x = 12

    for item in procesados:
        texto = item["texto"]
        icono = item.get("icono")
        c.setFont("Helvetica-Bold", 8.2)
        ancho_texto = c.stringWidth(texto, "Helvetica-Bold", 8.2)

        tiene_icono = bool(icono and os.path.isfile(icono))
        ancho_icono = (icon_size + icon_gap) if tiene_icono else 0
        ancho = max(92, min(max_width, ancho_texto + ancho_icono + (pad_x * 2)))

        if chip_x > x and (chip_x + ancho) > (x + max_width):
            chip_x = x
            chip_y -= row_height

        bg_color, text_color = colores_badge(item)

        # fondo
        c.setFillColor(bg_color)
        c.roundRect(chip_x, chip_y - chip_height, ancho, chip_height, 10, stroke=0, fill=1)

        # borde sutil
        c.setStrokeColor(HexColor("#00000012"))
        c.setLineWidth(0.6)
        c.roundRect(chip_x, chip_y - chip_height, ancho, chip_height, 10, stroke=1, fill=0)


        ancho_total = ancho_texto + ancho_icono
        inicio_x = chip_x + ((ancho - ancho_total) / 2.0)

        if tiene_icono:
            try:
                dibujar_imagen_ajustada(
                    c, icono, inicio_x, chip_y - 22,
                    icon_size, icon_size, radius=4,
                )
                texto_x = inicio_x + icon_size + icon_gap
            except Exception:
                texto_x = inicio_x
        else:
            texto_x = inicio_x

        c.setFillColor(text_color)
        c.setFont("Helvetica-Bold", 9)
        text_y = chip_y - 15
        c.drawString(texto_x, text_y, texto)

        bottom_y = min(bottom_y, chip_y - chip_height)
        chip_x += ancho + 8

    return bottom_y - 16


def generar_pdf_ficha_bytes(ficha: dict, logo_path: str | None = None) -> bytes:
    ficha = preparar_ficha_visual(ficha)
    visual = ficha["_visual"]
    imagenes_producto = resolver_imagenes_producto(ficha)
    imagen_path = imagenes_producto.get("principal")
    imagenes_adicionales = imagenes_producto.get("adicionales") or []

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    page_w, page_h = A4

    margin = 34
    content_w = page_w - (margin * 2)
    colores = colores_por_empresa(ficha.get("empresa"))

    header_h = 122
    c.setFillColor(colores["header_bg"])
    c.rect(0, page_h - header_h, page_w, header_h, stroke=0, fill=1)

    logo_w = 255
    logo_h = 78
    logo_x = margin
    logo_y = page_h - 92

    if logo_path and os.path.exists(logo_path):
        try:
            c.drawImage(
                ImageReader(logo_path),
                logo_x,
                logo_y,
                width=logo_w,
                height=logo_h,
                preserveAspectRatio=True,
                anchor="w",
                mask="auto",
            )
        except Exception as e:
            print("ERROR CARGANDO LOGO EN FICHA PDF:", e)

    text_header_x = margin + 270
    header_text_w = page_w - text_header_x - margin
    c.setFillColor(colores["text_on_header"])
    header_y = page_h - 42
    header_y = dibujar_texto_multilinea(
        c,
        visual["titulo"],
        text_header_x,
        header_y,
        header_text_w,
        font_name="Helvetica-Bold",
        font_size=18,
        leading=20,
        max_lineas=2,
    )

    header_y = dibujar_texto_multilinea(
        c,
        visual["subtitulo"],
        text_header_x,
        header_y + 4,
        header_text_w,
        font_name="Helvetica",
        font_size=10,
        leading=12,
        max_lineas=2,
    )

    badge_y = max(page_h - 98, header_y - 8)

    dibujar_pills(
        c,
        visual["badges_manuales"],
        text_header_x,
        badge_y,
        max_width=page_w - text_header_x - margin,
        text_color=white,
        bg_color=colores["accent"],
        font_size=7.5,
        height=13,
        gap=6,
        max_items=3,
    )

    img_x = margin
    img_w = 214
    img_h = 214

    # alineada arriba, al mismo nivel visual del encabezado de contenido
    img_top = page_h - 154
    img_y = img_top - img_h
    c.setFillColor(colores["box_bg"])
    c.roundRect(img_x, img_y, img_w, img_h, 16, stroke=0, fill=1)

    # borde suave
    c.setStrokeColor(colores["line"])
    c.setLineWidth(0.8)
    c.roundRect(img_x, img_y, img_w, img_h, 16, stroke=1, fill=0)

    print("IMAGEN_FICHA =", imagen_path)
    if imagen_path and os.path.isfile(imagen_path):
        dibujar_imagen_ajustada(c, imagen_path, img_x + 6, img_y + 6, img_w - 12, img_h - 12, radius=14)

    text_x = img_x + img_w + 24
    y = page_h - 150
    text_w = page_w - text_x - margin

    c.setFillColor(colores["accent"])
    c.setFont("Helvetica-Bold", 12)
    c.drawString(text_x, y, "Información del producto")
    y -= 18

    c.setFillColor(colores["text_main"])
    for etiqueta, valor in visual["campos_detalle"]:
        y = dibujar_bloque_texto(c, text_x, y, text_w, etiqueta, valor, font_size=9, leading=11)

    if visual["badges_automaticos"]:
        c.setFillColor(colores["accent"])
        c.setFont("Helvetica-Bold", 11)
        y -= 12
        c.drawString(text_x, y, "Highlights")
        y -= 18
        dibujar_pills(
            c,
            visual["badges_automaticos"],
            text_x,
            y - 2,
            max_width=text_w,
            text_color=white,
            bg_color=colores["header_bg"],
            font_size=7.5,
            height=13,
            gap=5,
            max_items=4,
        )
        y -= 20

    y2 = min(y, img_y) - 6
    bloques = [
        ("Descripción", visual["descripcion"]),
        ("Ingredientes", texto_seguro(ficha.get("ingredientes"))),
        ("Conservación", texto_seguro(ficha.get("conservacion"))),
        ("Observaciones", visual["observaciones"]),
    ]

    for titulo, valor in bloques:
        valor = texto_seguro(valor)
        if not valor:
            continue
        c.setFillColor(colores["text_main"])
        y2 = dibujar_bloque_texto_formateado(c, margin, y2, content_w, titulo, valor, font_size=9, leading=11)
        y2 -= 2

    c.setStrokeColor(colores["line"])
    c.line(margin, 42, page_w - margin, 42)
    c.setFillColor(colores["text_main"])
    c.setFont("Helvetica", 8)
    c.drawString(margin, 28, f"{texto_seguro(ficha.get('empresa'))}  •  CIP {texto_seguro(ficha.get('cip'))}")

    c.showPage()
    c.save()
    pdf = buffer.getvalue()
    buffer.close()
    return pdf


def chunks(lista, n):
    for i in range(0, len(lista), n):
        yield lista[i:i+n]

def truncar_texto(texto: str, max_chars: int = 180) -> str:
    t = texto_seguro(texto)
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 3].rstrip() + "..."

def dibujar_texto_multilinea(c, texto, x, y, ancho, font_name="Helvetica", font_size=9, leading=12, max_lineas=None):
    texto = texto_seguro(texto)
    if not texto:
        return y

    c.setFont(font_name, font_size)
    palabras = texto.split()
    linea = ""
    lineas_dibujadas = 0

    for palabra in palabras:
        prueba = f"{linea} {palabra}".strip()
        if c.stringWidth(prueba, font_name, font_size) <= ancho:
            linea = prueba
        else:
            if max_lineas is not None and lineas_dibujadas >= max_lineas:
                return y
            c.drawString(x, y, linea)
            y -= leading
            lineas_dibujadas += 1
            linea = palabra

    if linea:
        if max_lineas is None or lineas_dibujadas < max_lineas:
            c.drawString(x, y, linea)
            y -= leading

    return y

def dibujar_producto_en_bloque(c, ficha: dict, x: float, y_top: float, w: float, h: float, layout: str = "compact"):
    ficha = preparar_ficha_visual(ficha)
    visual = ficha["_visual"]
    colores = colores_por_empresa(ficha.get("empresa"))
    imagenes_producto = resolver_imagenes_producto(ficha)
    imagen_path = imagenes_producto.get("principal")
    imagenes_adicionales = imagenes_producto.get("adicionales") or []

    c.setStrokeColor(colores["line"])
    c.setLineWidth(0.8)
    c.roundRect(x, y_top - h, w, h, 10, stroke=1, fill=0)

    padding = 12

    if layout == "full":
        img_w = 168
        img_h = h - (padding * 2)
        title_size = 14
        body_size = 9.2
        body_leading = 11
        desc_max_lines = None
        show_campos = 5
        include_ingredientes = True
        include_conservacion = True
        include_comercial = True
    elif layout == "medium":
        img_w = 136
        img_h = h - (padding * 2)
        title_size = 12.5
        body_size = 8.7
        body_leading = 10
        desc_max_lines = None
        show_campos = 5
        include_ingredientes = True
        include_conservacion = True
        include_comercial = True
    else:
        img_w = 108
        img_h = h - (padding * 2)
        title_size = 11
        body_size = 8.2
        body_leading = 9.5
        desc_max_lines = 6
        show_campos = 6
        include_ingredientes = False
        include_conservacion = True
        include_comercial = False
        visual["subtitulo"] = ""

    img_x = x + padding

    # fija la parte superior de la imagen al arranque del bloque
    img_top = y_top - padding
    img_y = img_top - img_h

    # La fotografía se imprime sobre el fondo natural de la hoja. Esto permite
    # que sus zonas transparentes permanezcan transparentes en el catálogo.
    c.setStrokeColor(colores["line"])
    c.setLineWidth(0.7)
    c.roundRect(img_x, img_y, img_w, img_h, 12, stroke=1, fill=0)

    print("IMAGEN_FICHA =", imagen_path)
    if imagen_path and os.path.isfile(imagen_path):
        dibujar_imagen_ajustada(c, imagen_path, img_x + 6, img_y + 6, img_w - 12, img_h - 12, radius=14)

    tx = img_x + img_w + 14
    ty = y_top - 18
    tw = w - (tx - x) - padding

    c.setFillColor(colores["accent"])
    ty = dibujar_texto_multilinea(
        c, visual["titulo"], tx, ty, tw,
        font_name="Helvetica-Bold",
        font_size=title_size,
        leading=body_leading + 2,
        max_lineas=2
    )

    c.setFillColor(colores["text_main"])
    ty = dibujar_texto_multilinea(
        c, visual["subtitulo"], tx, ty, tw,
        font_name="Helvetica",
        font_size=body_size,
        leading=body_leading,
        max_lineas=2
    )

    badges_icono = list(visual.get("badges_icono") or [])
    badges_texto = list(visual.get("badges_manuales") or [])

    cip = texto_seguro(ficha.get("cip"))
    if cip:
        badges_texto.append(f"CIP {cip}")

    items_badges = []
    items_badges.extend(badges_icono)
    items_badges.extend([{"texto": b, "icono": None, "kind": "manual"} for b in badges_texto])
    

    print("BADGES_ICONO =", badges_icono)
    print("BADGES_TEXTO =", badges_texto)
    ty = dibujar_chips_premium(c, items_badges, tx, ty, colores, max_items=4, max_width=tw)
    
    c.setFillColor(colores["text_main"])
    for etiqueta, valor in visual["campos_catalogo"][:show_campos]:
        c.setFont("Helvetica-Bold", body_size)
        c.drawString(tx, ty, f"{etiqueta}:")
        c.setFont("Helvetica", body_size)
        c.drawString(tx + 58, ty, texto_seguro(valor)[:64])
        ty -= body_leading

    secciones = []
    if visual["badges_automaticos"]:
        secciones.append(("Highlights", " • ".join(visual["badges_automaticos"])))
    # La descripción corta se muestra dentro del detalle, justo antes de la
    # conservación, no arriba del CIP y las etiquetas.
    if visual["descripcion"]:
        secciones.append(("Descripción", visual["descripcion"]))
    if include_ingredientes and texto_seguro(ficha.get("ingredientes")):
        secciones.append(("Ingredientes", texto_seguro(ficha.get("ingredientes"))))
    if include_conservacion and texto_seguro(ficha.get("conservacion")):
        secciones.append(("Conservación", texto_seguro(ficha.get("conservacion"))))

    for titulo, valor in secciones:
        valor = texto_seguro(valor)
        if not valor:
            continue
        ty -= 2
        c.setFillColor(colores["accent"])
        c.setFont("Helvetica-Bold", body_size)
        c.drawString(tx, ty, titulo)
        ty -= body_leading
        c.setFillColor(colores["text_main"])
        ty = dibujar_texto_multilinea(
            c, valor, tx, ty, tw,
            font_name="Helvetica",
            font_size=body_size,
            leading=body_leading,
            max_lineas=desc_max_lines
        )
        ty -= 2

def normalizar_texto_catalogo(v: str) -> str:
    t = texto_seguro(v).lower()
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    return " ".join(t.split())


def clasificar_producto_catalogo(ficha: dict) -> tuple[int, int, str]:
    categoria_real = normalizar_texto_catalogo(
        " ".join([
            ficha.get("categoria") or "",
            ficha.get("tipo_producto") or "",
        ])
    )

    texto_aux = normalizar_texto_catalogo(
        " ".join([
            ficha.get("nombre_producto") or "",
            ficha.get("titulo_ficha") or "",
            ficha.get("descripcion_corta") or "",
        ])
    )

    presentacion = normalizar_texto_catalogo(ficha.get("presentacion") or "")
    titulo = normalizar_texto_catalogo(ficha.get("nombre_producto") or ficha.get("titulo_ficha") or "")
    cip = texto_seguro(ficha.get("cip"))

    # 6) ACCESORIOS (PRIORIDAD ABSOLUTA Y AL FINAL)
    if any(x in categoria_real for x in ["accesorio", "accesorios"]):
        return (6, 1, titulo or cip)

    # 5) UTENSILIOS
    if any(x in categoria_real for x in [
        "utensilio", "utensilios", "cuchillo", "cuchillos",
        "jamonero", "tabla"
    ]):
        return (5, 1, titulo or cip)

    categoria = f"{categoria_real} {texto_aux}".strip()

    # 1) QUESOS
    if "queso" in categoria:
        if any(x in presentacion for x in [
            "pieza entera", "pieza completa", "entero", "rueda completa", "rueda"
        ]):
            return (1, 1, titulo or cip)
        if any(x in presentacion for x in [
            "cuna", "cuña", "fraccion", "fracción", "media pieza", "1/2", "1/4", "porcion", "porcionado"
        ]):
            return (1, 2, titulo or cip)
        return (1, 3, titulo or cip)

    # 2) JAMONES
    if any(x in categoria for x in ["jamon", "jamon serrano", "jamon iberico", "paleta", "jamón"]):
        if any(x in presentacion for x in [
            "pieza entera", "pieza completa", "entero", "pata", "pierna"
        ]):
            return (2, 1, titulo or cip)
        if any(x in presentacion for x in [
            "deshuesado", "deshuesada"
        ]):
            return (2, 2, titulo or cip)
        if any(x in presentacion for x in [
            "rebanado", "loncheado", "fileteado", "corte", "rebanadas", "estuche"
        ]):
            return (2, 3, titulo or cip)
        return (2, 4, titulo or cip)

    # 3) EMBUTIDOS
    if any(x in categoria for x in [
        "embutido", "chorizo", "salchichon", "salchichón", "lomo", "fuet", "longaniza", "sobrasada"
    ]):
        if any(x in presentacion for x in [
            "pieza entera", "pieza completa", "entero"
        ]):
            return (3, 1, titulo or cip)
        if any(x in presentacion for x in [
            "rebanado", "loncheado", "fileteado", "corte", "estuche"
        ]):
            return (3, 2, titulo or cip)
        return (3, 3, titulo or cip)

    # 4) VARIOS
    if any(x in categoria for x in [
        "azafran", "azafrÃ¡n", "miel", "mermelada", "salsa", "conserva",
        "varios", "otros", "especialidad", "especialidades"
    ]):
        return (4, 1, titulo or cip)

    return (4, 9, titulo or cip)

def ordenar_productos_catalogo(productos: list[dict]) -> list[dict]:
    return sorted(productos, key=clasificar_producto_catalogo)

def obtener_seccion_catalogo(ficha: dict) -> tuple[int, int, str]:
    categoria_real = normalizar_texto_catalogo(
        " ".join([
            ficha.get("categoria") or "",
            ficha.get("tipo_producto") or "",
        ])
    )

    texto_aux = normalizar_texto_catalogo(
        " ".join([
            ficha.get("nombre_producto") or "",
            ficha.get("titulo_ficha") or "",
            ficha.get("descripcion_corta") or "",
        ])
    )

    presentacion = normalizar_texto_catalogo(ficha.get("presentacion") or "")

    # 6) ACCESORIOS (PRIORIDAD ABSOLUTA)
    if any(x in categoria_real for x in ["accesorio", "accesorios"]):
        return (6, 1, "Accesorios")

    # 5) UTENSILIOS
    if any(x in categoria_real for x in [
        "utensilio", "utensilios", "cuchillo", "cuchillos",
        "jamonero", "tabla"
    ]):
        return (5, 1, "Utensilios")

    categoria = f"{categoria_real} {texto_aux}".strip()

    # 1) QUESOS
    if "queso" in categoria:
        if any(x in presentacion for x in ["pieza entera", "pieza completa", "entero", "rueda completa", "rueda"]):
            return (1, 1, "Quesos enteros")
        if any(x in presentacion for x in ["cuna", "cuña", "fraccion", "fracción", "media pieza", "1/2", "1/4", "porcion", "porcionado"]):
            return (1, 2, "Quesos en cuñas o fracciones")
        return (1, 3, "Quesos")

    # 2) JAMONES
    if any(x in categoria for x in ["jamon", "jamÃ³n", "paleta"]):
        if any(x in presentacion for x in ["pieza entera", "pieza completa", "entero", "pata", "pierna"]):
            return (2, 1, "Jamones en pata")
        if any(x in presentacion for x in ["deshuesado", "deshuesada"]):
            return (2, 2, "Jamones deshuesados")
        if any(x in presentacion for x in ["rebanado", "loncheado", "fileteado", "corte", "rebanadas", "estuche"]):
            return (2, 3, "Jamones rebanados")
        return (2, 4, "Jamones")

    # 3) EMBUTIDOS
    if any(x in categoria for x in ["embutido", "chorizo", "salchichon", "salchichÃ³n", "lomo", "fuet", "longaniza", "sobrasada"]):
        if any(x in presentacion for x in ["pieza entera", "pieza completa", "entero"]):
            return (3, 1, "Embutidos enteros")
        if any(x in presentacion for x in ["rebanado", "loncheado", "fileteado", "corte", "estuche"]):
            return (3, 2, "Embutidos rebanados")
        return (3, 3, "Embutidos")

    # 4) VARIOS
    if any(x in categoria for x in [
        "azafran", "azafrÃ¡n", "miel", "mermelada", "salsa", "conserva",
        "varios", "otros", "especialidad", "especialidades"
    ]):
        return (4, 1, "Varios")

    return (4, 9, "Varios")

def construir_bloques_catalogo(productos: list[dict]) -> list[dict]:
    bloques = []
    ultima_seccion = None

    for ficha in productos:
        _, _, seccion = obtener_seccion_catalogo(ficha)
        if seccion != ultima_seccion:
            bloques.append({
                "tipo": "seccion",
                "titulo": seccion,
            })
            ultima_seccion = seccion

        bloques.append({
            "tipo": "producto",
            "data": ficha,
        })

    return bloques


def dibujar_titulo_seccion_catalogo(c, titulo: str, x: float, y_top: float, w: float, colores: dict) -> float:
    alto = 18

    c.setFillColor(colores["accent_soft"])
    c.roundRect(x, y_top - alto, w, alto, 6, stroke=0, fill=1)

    c.setFillColor(colores["accent"])
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x + 8, y_top - 12, texto_seguro(titulo))

    return y_top - alto - 6

def generar_catalogo_pdf_bytes(productos: List[dict], empresa: str, logo_path: str | None = None) -> bytes:
    productos = ordenar_productos_catalogo(productos)
    bloques = construir_bloques_catalogo(productos)

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    page_w, page_h = A4

    margin = 24
    header_h = 70
    footer_h = 18
    usable_w = page_w - (margin * 2)

    colores = colores_por_empresa(empresa)

    def nueva_pagina(pagina_idx: int):
        c.setFillColor(colores["header_bg"])
        c.rect(0, page_h - header_h, page_w, header_h, stroke=0, fill=1)

        if logo_path and os.path.exists(logo_path):
            try:
                dibujar_imagen_ajustada(
                    c, logo_path, margin, page_h - 52, 140, 42, radius=0,
                )
            except Exception as e:
                print("ERROR LOGO:", e)

        c.setFillColor(colores["text_on_header"])
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margin + 155, page_h - 30, f"Catálogo de productos - {empresa}")

        c.setFont("Helvetica", 8)
        c.drawRightString(page_w - margin, page_h - 30, f"Página {pagina_idx}")

        c.setStrokeColor(colores["line"])
        c.line(margin, footer_h + 4, page_w - margin, footer_h + 4)

        c.setFillColor(colores["text_main"])
        c.setFont("Helvetica", 7)
        c.drawString(margin, 10, empresa)

        return page_h - header_h - 8

    pagina_idx = 1
    y_cursor = nueva_pagina(pagina_idx)

    i = 0
    while i < len(bloques):
        bloque = bloques[i]

        if bloque["tipo"] == "seccion":
            alto_seccion = 24
            block_h = 230
            espacio_necesario = alto_seccion

            # mirar si despuÃ©s del tÃ­tulo viene un producto
            if i + 1 < len(bloques) and bloques[i + 1]["tipo"] == "producto":
                espacio_necesario += block_h + 8

            salto_hecho = False

            # si no cabe tÃ­tulo + al menos 1 producto -> nueva pÃ¡gina
            if y_cursor - espacio_necesario < footer_h + 10:
                c.showPage()
                pagina_idx += 1
                y_cursor = nueva_pagina(pagina_idx)
                salto_hecho = True

            # Cada grupo arranca en hoja nueva para separar familias de producto.
            if i > 0 and not salto_hecho:
                c.showPage()
                pagina_idx += 1
                y_cursor = nueva_pagina(pagina_idx)

            y_cursor = dibujar_titulo_seccion_catalogo(
                c, bloque["titulo"], margin, y_cursor, usable_w, colores
            )
            i += 1
            continue

        ficha = bloque["data"]
        block_h = 230

        if y_cursor - block_h < footer_h + 10:
            c.showPage()
            pagina_idx += 1
            y_cursor = nueva_pagina(pagina_idx)

        dibujar_producto_en_bloque(
            c,
            ficha,
            margin,
            y_cursor,
            usable_w,
            block_h,
            layout="compact"
        )

        y_cursor -= (block_h + 10)
        i += 1

    c.save()
    pdf = buffer.getvalue()
    buffer.close()
    return pdf

@app.get("/catalogos/ficha-pdf")
def get_ficha_pdf(empresa: str, cip: str):
    ficha = obtener_ficha_producto_nueva(empresa, cip)
    if not ficha:
        raise HTTPException(status_code=404, detail="Ficha no encontrada")

    logo_path = resolver_logo_empresa(empresa)
    pdf_bytes = generar_pdf_ficha_bytes(ficha, logo_path=logo_path)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="ficha_{cip}.pdf"',
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

@app.post("/catalogos/catalogo-pdf")
def post_catalogo_pdf(payload: CatalogoPdfIn):
    empresa = (payload.empresa or "").strip()
    cips = [str(c).strip() for c in payload.cips if str(c).strip()]

    if not empresa:
        raise HTTPException(status_code=400, detail="Falta empresa")

    if not cips:
        raise HTTPException(status_code=400, detail="No se recibieron productos")

    # El catálogo debe leer exactamente la misma ficha que "Ver ficha PDF".
    # La consulta por lote puede devolver una fila incompleta cuando existen
    # variantes compartidas entre empresas; al resolver cada CIP individualmente
    # se aplican los mismos valores base y específicos de la Administración.
    productos = []
    faltantes = []
    for cip in cips:
        ficha = obtener_ficha_producto_nueva(empresa, cip)
        if ficha:
            productos.append(ficha)
        else:
            faltantes.append(cip)
    if not productos:
        raise HTTPException(status_code=404, detail="No se encontraron fichas para los productos enviados")

    productos = ordenar_productos_catalogo(productos)

    logo_path = resolver_logo_empresa(empresa)

    pdf_bytes = generar_catalogo_pdf_bytes(productos, empresa=empresa, logo_path=logo_path)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="catalogo_productos.pdf"',
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )
@app.get("/catalogos/catalogo-pdf-test")
def get_catalogo_pdf_test(
    empresa: str,
    cips: str
):
    lista_cips = [x.strip() for x in cips.split(",") if x.strip()]
    if not lista_cips:
        raise HTTPException(status_code=400, detail="No se recibieron CIPs")

    productos = obtener_fichas_productos_nueva(empresa, lista_cips)
    if not productos:
        raise HTTPException(status_code=404, detail="No se encontraron fichas")

    productos = ordenar_productos_catalogo(productos)

    logo_path = resolver_logo_empresa(empresa)

    pdf_bytes = generar_catalogo_pdf_bytes(
        productos,
        empresa=empresa,
        logo_path=logo_path
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="catalogo_test.pdf"',
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )

@app.post("/editor/comandas/importar/{folio}")
def importar_comanda(folio: str, user: dict = Depends(get_current_user)):
    conn_editor = conectar_mysql_editor()
    conn_main = conectar_mysql()

    if not conn_editor:
        raise HTTPException(status_code=500, detail="No se pudo conectar a comandas_editor_db")

    if not conn_main:
        raise HTTPException(status_code=500, detail="No se pudo conectar a comandas_db")

    try:
        cur_e = conn_editor.cursor(dictionary=True)
        cur_m = conn_main.cursor(dictionary=True)

        cur_m.execute("""
            SELECT * FROM comandas WHERE folio=%s LIMIT 1
        """, (folio,))
        c = cur_m.fetchone()

        if not c:
            raise HTTPException(status_code=404, detail="Comanda no encontrada")

        cur_e.execute("SELECT id FROM comandas_editables WHERE folio=%s LIMIT 1", (folio,))
        existente = cur_e.fetchone()
        if existente:
            comanda_id = existente["id"]
            cur_e.execute(
                "SELECT COUNT(*) AS total FROM comandas_editables_detalle WHERE comanda_id=%s",
                (comanda_id,),
            )
            detalle_existente = int((cur_e.fetchone() or {}).get("total") or 0)
            if detalle_existente > 0:
                return {"ok": True, "id": comanda_id, "existente": True}
            print(f"[EDITOR] Rehidratando detalle vacio para folio={folio!r}", flush=True)
        else:
            cur_e.execute("""
                INSERT INTO comandas_editables
                (comanda_original_id, folio, vendedor, empresa, cliente_numero, cliente_nombre, fecha, observaciones_pedido)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                c["id"], c["folio"], c.get("vendedor", ""), c.get("empresa", ""),
                c.get("cliente_numero", ""), c.get("cliente_nombre", ""), c.get("fecha"),
                c.get("observaciones_pedido", "")
            ))

            comanda_id = cur_e.lastrowid

        cur_m.execute("""
            SELECT * FROM productos_comanda WHERE comanda_id=%s
        """, (c["id"],))

        for p in cur_m.fetchall():
            cip = str(p.get("cip") or "").strip()
            descripcion = str(p.get("descripcion") or "").strip()
            observaciones = str(p.get("observaciones") or "").strip()
            kgs = float(p.get("kgs") or 0)
            piezas = float(p.get("piezas") or 0)

            # saltar renglones totalmente vacÃ­os
            if not cip and not descripcion and not observaciones and kgs == 0 and piezas == 0:
                continue

            # saltar lÃ­neas tipo TOTAL
            texto_control = f"{descripcion} {observaciones}".upper()
            if not cip and not descripcion and ("TOTAL" in texto_control or "SUBTOTAL" in texto_control):
                continue

            cur_e.execute("""
                INSERT INTO comandas_editables_detalle
                (comanda_id, cip, descripcion, kgs, piezas, observaciones)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (
                comanda_id,
                cip,
                descripcion,
                kgs,
                piezas,
                observaciones
            ))

        conn_editor.commit()
        return {"ok": True, "id": comanda_id, "existente": bool(existente)}

    finally:
        try:
            conn_editor.close()
        except Exception:
            pass
        try:
            conn_main.close()
        except Exception:
            pass

@app.get("/editor/comandas/{folio}", response_model=ComandaEditableOut)
def obtener_comanda_editable(folio: str, user: dict = Depends(get_current_user)):
    conn = conectar_mysql_editor()

    try:
        cur = conn.cursor(dictionary=True)

        cur.execute("""
            SELECT * FROM comandas_editables WHERE folio=%s LIMIT 1
        """, (folio,))
        c = cur.fetchone()

        if not c:
            raise HTTPException(status_code=404, detail="No existe en editor")

        cur.execute("""
            SELECT * FROM comandas_editables_detalle WHERE comanda_id=%s
        """, (c["id"],))

        productos = cur.fetchall()

        return ComandaEditableOut(
            id=c["id"],
            folio=c["folio"],
            vendedor=c["vendedor"],
            empresa=c["empresa"],
            cliente_numero=c["cliente_numero"],
            cliente_nombre=c["cliente_nombre"],
            fecha=str(c["fecha"]),
            observaciones_pedido=c.get("observaciones_pedido", ""),
            productos=[
                ComandaEditableItemOut(**p) for p in productos
            ]
        )

    finally:
        conn.close()

@app.put("/editor/comandas/{comanda_id}")
def actualizar_comanda(
    comanda_id: int,
    data: ComandaEditableUpdateIn,
    user: dict = Depends(get_current_user)
):
    conn = conectar_mysql_editor()

    try:
        cur = conn.cursor()

        # actualizar encabezado
        cur.execute("""
            UPDATE comandas_editables
            SET vendedor=%s,
                empresa=%s,
                cliente_numero=%s,
                cliente_nombre=%s,
                observaciones_pedido=%s
            WHERE id=%s
        """, (
            data.vendedor,
            data.empresa,
            data.cliente_numero,
            data.cliente_nombre,
            data.observaciones_pedido,
            comanda_id
        ))

        # borrar detalle actual
        cur.execute("DELETE FROM comandas_editables_detalle WHERE comanda_id=%s", (comanda_id,))

        # insertar nuevo detalle
        for p in data.productos:
            cur.execute("""
                INSERT INTO comandas_editables_detalle
                (comanda_id, cip, descripcion, kgs, piezas, observaciones)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (
                comanda_id,
                p.cip,
                p.descripcion,
                p.kgs,
                p.piezas,
                p.observaciones
            ))

        # historial
        cur.execute("""
            INSERT INTO comandas_editables_historial
            (comanda_id, usuario, accion, detalle)
            VALUES (%s,%s,'UPDATE','EdiciÃ³n desde app')
        """, (comanda_id, user["usuario"]))

        conn.commit()

        return {"ok": True}

    finally:
        conn.close()

@app.get("/editor/comandas/{comanda_id}/historial", response_model=List[ComandaHistorialOut])
def obtener_historial_comanda(comanda_id: int, user: dict = Depends(get_current_user)):
    conn = conectar_mysql_editor()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a comandas_editor_db")

    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT id, comanda_id, usuario, accion, detalle, fecha
            FROM comandas_editables_historial
            WHERE comanda_id = %s
            ORDER BY fecha DESC, id DESC
        """, (comanda_id,))
        rows = cur.fetchall()

        return [
            ComandaHistorialOut(
                id=r["id"],
                comanda_id=r["comanda_id"],
                usuario=r.get("usuario", ""),
                accion=r.get("accion", ""),
                detalle=r.get("detalle", ""),
                fecha=str(r["fecha"]) if r.get("fecha") else ""
            )
            for r in rows
        ]
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
