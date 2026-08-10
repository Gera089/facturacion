import io
import os
import re

import httpx
import pandas as pd
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.db import list_companies
from app.dependencies import require_user
from app.legacy_db import get_legacy_connection


router = APIRouter(prefix="/api/customers", tags=["customers"])
WEEK_DAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


VISIT_FIELDS = [
    "horarios_pago_desde",
    "horarios_pago_hasta",
    "dia_pago",
    "forma_pago",
    "horarios_revision_desde",
    "horarios_revision_hasta",
    "dia_revision",
    "compras_nombre",
    "compras_telefono",
    "recibo_nombre",
    "recibo_telefono",
    "gerente_nombre",
    "gerente_telefono",
    "observaciones_visita",
    "pedido_realizado_visita",
]

PREALTA_EDITABLE_FIELDS = [
    "empresa", "nombre", "razon_social", "calle", "no_exterior", "no_interior",
    "colonia", "alcaldia", "municipio", "codigo_postal", "poblacion", "estado", "pais",
    "rfc", "telefono", "correo_electronico", "contacto1", "contacto2", "dias_credito",
    "consignatario", "consig_calle", "consig_no_exterior", "consig_no_interior",
    "consig_colonia", "consig_delegacion", "consig_municipio", "consig_codigo_postal",
    "consig_poblacion", "consig_estado", "consig_pais", "zona", "no_proveedor", "agente",
    "descuento", "especial", "tipo", "vendedor", "direccion_entrega", "observaciones",
    "horarios_pago_desde", "horarios_pago_hasta", "dia_pago", "forma_pago",
    "horarios_revision_desde", "horarios_revision_hasta", "dia_revision",
    "compras_nombre", "compras_telefono", "recibo_nombre", "recibo_telefono",
    "gerente_nombre", "gerente_telefono", "observaciones_visita", "pedido_realizado_visita",
    "numero_cliente_sugerido",
]

PREALTA_TO_CLIENTE_FIELDS = [
    "numero", "nombre", "empresa", "razon_social", "calle", "no_exterior", "no_interior",
    "colonia", "alcaldia", "municipio", "codigo_postal", "poblacion", "estado", "pais",
    "rfc", "telefono", "correo_electronico", "contacto1", "contacto2", "dias_credito",
    "consignatario", "consig_calle", "consig_no_exterior", "consig_no_interior",
    "consig_colonia", "consig_delegacion", "consig_municipio", "consig_codigo_postal",
    "consig_poblacion", "consig_estado", "consig_pais", "zona", "no_proveedor", "agente",
    "descuento", "especial", "tipo", "vendedor", "direccion_entrega", "observaciones",
]

CUSTOMER_COLUMNS = [
    "numero",
    "nombre",
    "empresa",
    "razon_social",
    "calle",
    "no_exterior",
    "no_interior",
    "colonia",
    "alcaldia",
    "municipio",
    "codigo_postal",
    "poblacion",
    "estado",
    "pais",
    "rfc",
    "telefono",
    "correo_electronico",
    "contacto1",
    "contacto2",
    "dias_credito",
    "consignatario",
    "consig_calle",
    "consig_no_exterior",
    "consig_no_interior",
    "consig_colonia",
    "consig_delegacion",
    "consig_municipio",
    "consig_codigo_postal",
    "consig_poblacion",
    "consig_estado",
    "consig_pais",
    "zona",
    "no_proveedor",
    "agente",
    "descuento",
    "especial",
    "tipo",
    "vendedor",
    "direccion_entrega",
    "observaciones",
]

WRITABLE_FIELDS = CUSTOMER_COLUMNS + VISIT_FIELDS

CUSTOMER_SELECT = """
    SELECT
        c.numero,
        c.nombre,
        c.empresa,
        c.razon_social,
        c.calle,
        c.no_exterior,
        c.no_interior,
        c.colonia,
        c.alcaldia,
        c.municipio,
        c.codigo_postal,
        c.poblacion,
        c.estado,
        c.pais,
        c.rfc,
        c.telefono,
        c.correo_electronico,
        c.contacto1,
        c.contacto2,
        c.dias_credito,
        c.consignatario,
        c.consig_calle,
        c.consig_no_exterior,
        c.consig_no_interior,
        c.consig_colonia,
        c.consig_delegacion,
        c.consig_municipio,
        c.consig_codigo_postal,
        c.consig_poblacion,
        c.consig_estado,
        c.consig_pais,
        c.zona,
        c.no_proveedor,
        c.agente,
        c.descuento,
        c.especial,
        c.tipo,
        c.vendedor,
        c.direccion_entrega,
        c.observaciones,
        COALESCE(cv.horarios_pago_desde, '') AS horarios_pago_desde,
        COALESCE(cv.horarios_pago_hasta, '') AS horarios_pago_hasta,
        COALESCE(cv.dia_pago, '') AS dia_pago,
        COALESCE(cv.forma_pago, '') AS forma_pago,
        COALESCE(cv.horarios_revision_desde, '') AS horarios_revision_desde,
        COALESCE(cv.horarios_revision_hasta, '') AS horarios_revision_hasta,
        COALESCE(cv.dia_revision, '') AS dia_revision,
        COALESCE(cv.compras_nombre, '') AS compras_nombre,
        COALESCE(cv.compras_telefono, '') AS compras_telefono,
        COALESCE(cv.recibo_nombre, '') AS recibo_nombre,
        COALESCE(cv.recibo_telefono, '') AS recibo_telefono,
        COALESCE(cv.gerente_nombre, '') AS gerente_nombre,
        COALESCE(cv.gerente_telefono, '') AS gerente_telefono,
        COALESCE(cv.observaciones_visita, '') AS observaciones_visita,
        COALESCE(cv.pedido_realizado_visita, '') AS pedido_realizado_visita
    FROM clientes c
    LEFT JOIN clientes_visitas cv
      ON UPPER(TRIM(cv.empresa)) = UPPER(TRIM(c.empresa))
     AND TRIM(cv.cliente_numero) = TRIM(CAST(c.numero AS CHAR))
"""


def _dict_rows(cursor):
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _resolve_company_aliases(raw_company: str) -> list[str]:
    company = str(raw_company or "").strip()
    if not company:
        return []

    aliases = {company}
    for item in list_companies():
        name = str(item.get("name") or "").strip()
        code = str(item.get("code") or "").strip()
        if company.lower() == name.lower() or company.lower() == code.lower():
            if name:
                aliases.add(name)
            if code:
                aliases.add(code)
    return list(aliases)


def _clean_value(key: str, value):
    if value in (None, "", "null", "None", "NaN"):
        return None
    text = str(value).strip()
    if key == "dias_credito":
        nums = re.findall(r"\d+", text)
        return int(nums[0]) if nums else 0
    if key == "descuento":
        try:
            return float(str(text).replace(",", "."))
        except Exception:
            return 0.0
    if key == "numero":
        try:
            num = float(text)
            if num.is_integer():
                return str(int(num))
        except Exception:
            pass
        return text
    return text


def _ensure_support_tables(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS clientes_visitas (
            id INT AUTO_INCREMENT PRIMARY KEY,
            empresa VARCHAR(255) NOT NULL,
            cliente_numero VARCHAR(255) NOT NULL,
            cliente_nombre VARCHAR(255) DEFAULT '',
            direccion TEXT DEFAULT NULL,
            telefono VARCHAR(80) DEFAULT '',
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
            creado_por VARCHAR(255) DEFAULT NULL,
            actualizado_por VARCHAR(255) DEFAULT NULL,
            fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY ux_cliente_visita (empresa, cliente_numero)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS clientes_visitas_historial (
            id INT AUTO_INCREMENT PRIMARY KEY,
            empresa VARCHAR(255) NOT NULL,
            cliente_numero VARCHAR(255) NOT NULL,
            campo VARCHAR(120) NOT NULL,
            valor_anterior TEXT DEFAULT NULL,
            valor_nuevo TEXT DEFAULT NULL,
            cambiado_por VARCHAR(255) DEFAULT NULL,
            fecha_cambio DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
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


def _ensure_prealta_tables(cursor):
    cursor.execute(
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
    cursor.execute(
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
            UNIQUE KEY ux_prealta_documento (prealta_id, tipo_documento)
        )
        """
    )
    for sql in [
        "CREATE INDEX idx_prealta_estatus_fecha ON clientes_prealta_vendedor (estatus, fecha_alta, id)",
        "CREATE INDEX idx_prealta_empresa ON clientes_prealta_vendedor (empresa)",
    ]:
        try:
            cursor.execute(sql)
        except Exception:
            pass


def _upsert_visit(cursor, company: str, number: str, payload: dict, user_label: str):
    visit_data = {field: str(payload.get(field) or "").strip() for field in VISIT_FIELDS}
    customer_name = str(payload.get("nombre") or payload.get("cliente_nombre") or "").strip()
    address = str(payload.get("direccion_entrega") or "").strip()
    phone = str(payload.get("telefono") or "").strip()

    cursor.execute(
        """
        SELECT id, cliente_nombre, direccion, telefono,
               horarios_pago_desde, horarios_pago_hasta, dia_pago, forma_pago,
               horarios_revision_desde, horarios_revision_hasta, dia_revision,
               compras_nombre, compras_telefono, recibo_nombre, recibo_telefono,
               gerente_nombre, gerente_telefono, observaciones_visita, pedido_realizado_visita
        FROM clientes_visitas
        WHERE UPPER(TRIM(empresa)) = UPPER(TRIM(%s))
          AND TRIM(cliente_numero) = TRIM(%s)
        LIMIT 1
        """,
        (company, number),
    )
    row = cursor.fetchone()

    if row:
        current_values = {
            "cliente_nombre": row[1] or "",
            "direccion": row[2] or "",
            "telefono": row[3] or "",
            "horarios_pago_desde": row[4] or "",
            "horarios_pago_hasta": row[5] or "",
            "dia_pago": row[6] or "",
            "forma_pago": row[7] or "",
            "horarios_revision_desde": row[8] or "",
            "horarios_revision_hasta": row[9] or "",
            "dia_revision": row[10] or "",
            "compras_nombre": row[11] or "",
            "compras_telefono": row[12] or "",
            "recibo_nombre": row[13] or "",
            "recibo_telefono": row[14] or "",
            "gerente_nombre": row[15] or "",
            "gerente_telefono": row[16] or "",
            "observaciones_visita": row[17] or "",
            "pedido_realizado_visita": row[18] or "",
        }

        new_values = {
            "cliente_nombre": customer_name,
            "direccion": address,
            "telefono": phone,
            **visit_data,
        }
        for field, new_value in new_values.items():
            old_value = str(current_values.get(field, "") or "")
            if old_value != str(new_value or ""):
                cursor.execute(
                    """
                    INSERT INTO clientes_visitas_historial
                    (empresa, cliente_numero, campo, valor_anterior, valor_nuevo, cambiado_por)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (company, number, field, old_value, str(new_value or ""), user_label),
                )

        cursor.execute(
            """
            UPDATE clientes_visitas
            SET cliente_nombre = %s,
                direccion = %s,
                telefono = %s,
                horarios_pago_desde = %s,
                horarios_pago_hasta = %s,
                dia_pago = %s,
                forma_pago = %s,
                horarios_revision_desde = %s,
                horarios_revision_hasta = %s,
                dia_revision = %s,
                compras_nombre = %s,
                compras_telefono = %s,
                recibo_nombre = %s,
                recibo_telefono = %s,
                gerente_nombre = %s,
                gerente_telefono = %s,
                observaciones_visita = %s,
                pedido_realizado_visita = %s,
                actualizado_por = %s
            WHERE id = %s
            """,
            (
                customer_name,
                address,
                phone,
                visit_data["horarios_pago_desde"],
                visit_data["horarios_pago_hasta"],
                visit_data["dia_pago"],
                visit_data["forma_pago"],
                visit_data["horarios_revision_desde"],
                visit_data["horarios_revision_hasta"],
                visit_data["dia_revision"],
                visit_data["compras_nombre"],
                visit_data["compras_telefono"],
                visit_data["recibo_nombre"],
                visit_data["recibo_telefono"],
                visit_data["gerente_nombre"],
                visit_data["gerente_telefono"],
                visit_data["observaciones_visita"],
                visit_data["pedido_realizado_visita"],
                user_label,
                row[0],
            ),
        )
        return

    cursor.execute(
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
        """,
        (
            company,
            number,
            customer_name,
            address,
            phone,
            visit_data["horarios_pago_desde"],
            visit_data["horarios_pago_hasta"],
            visit_data["dia_pago"],
            visit_data["forma_pago"],
            visit_data["horarios_revision_desde"],
            visit_data["horarios_revision_hasta"],
            visit_data["dia_revision"],
            visit_data["compras_nombre"],
            visit_data["compras_telefono"],
            visit_data["recibo_nombre"],
            visit_data["recibo_telefono"],
            visit_data["gerente_nombre"],
            visit_data["gerente_telefono"],
            visit_data["observaciones_visita"],
            visit_data["pedido_realizado_visita"],
            user_label,
            user_label,
        ),
    )


def _fetch_customer(cursor, company: str, number: str):
    sql = (
        CUSTOMER_SELECT
        + """
        WHERE TRIM(CAST(c.numero AS CHAR)) = %s
          AND UPPER(TRIM(c.empresa) COLLATE utf8mb4_unicode_ci) =
              UPPER(TRIM(%s) COLLATE utf8mb4_unicode_ci)
        LIMIT 1
        """
    )
    cursor.execute(sql, (_normalizar_numero_cliente(number), company.strip()))
    rows = _dict_rows(cursor)
    return rows[0] if rows else None


def _normalize_prealta_value(field: str, value):
    if value in (None, "", "null", "None", "NaN"):
        return None
    if field == "dias_credito":
        try:
            return int(float(value))
        except Exception:
            return 0
    if field == "descuento":
        try:
            return float(value)
        except Exception:
            return 0.0
    return str(value).strip()


def _normalizar_numero_cliente(valor) -> str:
    """Quita separadores de miles de identificadores numéricos de cliente."""
    texto = str(valor or "").strip()
    return re.sub(r"[,\s]", "", texto) if re.fullmatch(r"[\d,\s]+", texto) else texto


def _build_visit_history(history_rows, current_visit):
    visit_fields = VISIT_FIELDS + ["cliente_nombre", "productos_cip"]
    snapshots = []
    state = {field: (current_visit.get(field, "") or "") for field in visit_fields}
    current_group_key = None
    current_group_user = ""
    current_group_fields = []

    def push_snapshot(group_key, group_user, changed_fields):
        if not group_key:
            return
        snapshot = {field: state.get(field, "") for field in visit_fields}
        snapshot["fecha"] = str(group_key or "")
        snapshot["actualizado_por"] = group_user or ""
        snapshot["campos_actualizados"] = ", ".join(changed_fields)
        snapshots.append(snapshot)

    for row in history_rows or []:
        group_key = str(row.get("fecha_cambio") or "")
        if current_group_key is None:
            current_group_key = group_key
            current_group_user = row.get("cambiado_por", "") or ""
            current_group_fields = []
        elif group_key != current_group_key:
            push_snapshot(current_group_key, current_group_user, current_group_fields)
            current_group_key = group_key
            current_group_user = row.get("cambiado_por", "") or ""
            current_group_fields = []

        field = row.get("campo", "") or ""
        if field in state:
            state[field] = row.get("valor_nuevo", "") or ""
            if field not in current_group_fields:
                current_group_fields.append(field)

    push_snapshot(current_group_key, current_group_user, current_group_fields)

    if not snapshots and current_visit:
        if any((current_visit.get(field) or "") for field in visit_fields if field not in ("cliente_nombre", "productos_cip")):
            snapshot = {field: current_visit.get(field, "") or "" for field in visit_fields}
            snapshot["fecha"] = str(current_visit.get("fecha_actualizacion") or current_visit.get("fecha_creacion") or "")
            snapshot["actualizado_por"] = current_visit.get("actualizado_por") or current_visit.get("creado_por") or ""
            snapshot["campos_actualizados"] = "registro inicial"
            snapshots.append(snapshot)

    snapshots.reverse()
    return snapshots


@router.get("/price-lists")
def list_price_lists(user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, nombre, descripcion
            FROM listas_precios
            ORDER BY id
            """
        )
        items = _dict_rows(cursor)
        return {"items": items, "week_days": WEEK_DAYS}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.get("/prealtas")
def list_prealtas(
    estatus: str = Query(default="", description="PENDIENTE, AUTORIZADA, RECHAZADA, CANCELADA"),
    user=Depends(require_user),
):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_prealta_tables(cursor)

        params = []
        where = ""
        estatus = (estatus or "").strip().upper()
        if estatus and estatus != "TODAS":
            where = "WHERE estatus = %s"
            params.append(estatus)

        cursor.execute(
            f"""
            SELECT
                id,
                empresa,
                IFNULL(numero_cliente_sugerido, '') AS numero_cliente_sugerido,
                nombre,
                IFNULL(razon_social, '') AS razon_social,
                IFNULL(vendedor, '') AS vendedor,
                IFNULL(agente, '') AS agente,
                estatus,
                IFNULL(usuario_alta, '') AS usuario_alta,
                fecha_alta,
                IFNULL(usuario_revision, '') AS usuario_revision,
                fecha_revision,
                IFNULL(comentario_revision, '') AS comentario_revision,
                IFNULL(numero_cliente_final, '') AS numero_cliente_final,
                IFNULL(empresa_cliente_final, '') AS empresa_cliente_final
            FROM clientes_prealta_vendedor
            {where}
            ORDER BY
                CASE estatus
                    WHEN 'PENDIENTE' THEN 0
                    WHEN 'RECHAZADA' THEN 1
                    WHEN 'AUTORIZADA' THEN 2
                    ELSE 3
                END,
                fecha_alta DESC,
                id DESC
            """,
            tuple(params),
        )
        return {"items": _dict_rows(cursor)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.get("/prealtas/{prealta_id}")
def get_prealta(prealta_id: int, user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_prealta_tables(cursor)
        cursor.execute("SELECT * FROM clientes_prealta_vendedor WHERE id = %s LIMIT 1", (prealta_id,))
        rows = _dict_rows(cursor)
        if not rows:
            raise HTTPException(status_code=404, detail="Prealta no encontrada")
        return rows[0]
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.get("/prealtas/{prealta_id}/documentos")
def list_prealta_documents(prealta_id: int, user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_prealta_tables(cursor)
        cursor.execute(
            """
            SELECT
                id,
                prealta_id,
                tipo_documento,
                IFNULL(nombre_original, '') AS nombre_original,
                IFNULL(ruta_archivo, '') AS ruta_archivo,
                IFNULL(mime_type, '') AS mime_type,
                IFNULL(tamano_bytes, 0) AS tamano_bytes,
                IFNULL(usuario_alta, '') AS usuario_alta,
                fecha_alta
            FROM clientes_prealta_documentos
            WHERE prealta_id = %s
            ORDER BY tipo_documento
            """,
            (prealta_id,),
        )
        return {"items": _dict_rows(cursor)}
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.get("/prealtas/documentos/{documento_id}/download")
def download_prealta_document(documento_id: int, user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, IFNULL(nombre_original, '') AS nombre_original, ruta_archivo, IFNULL(mime_type, '') AS mime_type
            FROM clientes_prealta_documentos
            WHERE id = %s
            LIMIT 1
            """,
            (documento_id,),
        )
        rows = _dict_rows(cursor)
        if not rows:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
        row = rows[0]
        filename = row.get("nombre_original") or "documento"
        upstream = f"http://100.69.142.19:8000/catalog/clientes/documentos/{documento_id}"
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(upstream)
                if resp.status_code == 200:
                    return StreamingResponse(
                        iter([resp.content]),
                        media_type=resp.headers.get("content-type", row.get("mime_type") or "application/octet-stream"),
                        headers={"Content-Disposition": f'inline; filename="{filename}"'},
                    )
        except Exception:
            pass
        raise HTTPException(status_code=404, detail="El archivo fisico no existe")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.put("/prealtas/{prealta_id}")
def update_prealta(prealta_id: int, payload: dict = Body(...), user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload invalido.")
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_prealta_tables(cursor)
        cursor.execute("SELECT id FROM clientes_prealta_vendedor WHERE id = %s LIMIT 1", (prealta_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Prealta no encontrada")

        clean = {}
        for key, value in payload.items():
            field = "numero_cliente_sugerido" if key == "numero" else key
            if field not in PREALTA_EDITABLE_FIELDS:
                continue
            clean[field] = _normalize_prealta_value(field, value)
        if not clean:
            raise HTTPException(status_code=400, detail="No hay campos validos para actualizar")

        assignments = ", ".join(f"{field}=%s" for field in clean.keys())
        values = list(clean.values()) + [prealta_id]
        cursor.execute(f"UPDATE clientes_prealta_vendedor SET {assignments} WHERE id = %s", values)
        conn.commit()
        return {"ok": True, "message": "Prealta actualizada correctamente"}
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
            cursor.close()
        if conn:
            conn.close()


@router.post("/prealtas/{prealta_id}/rechazar")
def reject_prealta(prealta_id: int, payload: dict = Body(...), user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        username = str((payload or {}).get("usuario_revision") or user.get("username") or "").strip()
        comment = str((payload or {}).get("comentario_revision") or "").strip()
        if not username:
            raise HTTPException(status_code=400, detail="Falta usuario_revision")

        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_prealta_tables(cursor)
        cursor.execute("SELECT id FROM clientes_prealta_vendedor WHERE id = %s LIMIT 1", (prealta_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Prealta no encontrada")

        cursor.execute(
            """
            UPDATE clientes_prealta_vendedor
            SET estatus = 'RECHAZADA',
                usuario_revision = %s,
                fecha_revision = NOW(),
                comentario_revision = %s
            WHERE id = %s
            """,
            (username, comment, prealta_id),
        )
        conn.commit()
        return {"ok": True, "message": "Prealta rechazada correctamente"}
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
            cursor.close()
        if conn:
            conn.close()


@router.post("/prealtas/{prealta_id}/aprobar")
def approve_prealta(prealta_id: int, payload: dict = Body(...), user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        username = str((payload or {}).get("usuario_revision") or user.get("username") or "").strip()
        comment = str((payload or {}).get("comentario_revision") or "").strip()
        number = str((payload or {}).get("numero") or "").strip()
        tipo = str((payload or {}).get("tipo") or "").strip()
        if not username:
            raise HTTPException(status_code=400, detail="Falta usuario_revision")
        if not number or not number.isdigit():
            raise HTTPException(status_code=400, detail="El numero del cliente debe ser numerico")

        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_prealta_tables(cursor)
        _ensure_support_tables(cursor)

        cursor.execute("SELECT * FROM clientes_prealta_vendedor WHERE id = %s LIMIT 1", (prealta_id,))
        rows = _dict_rows(cursor)
        if not rows:
            raise HTTPException(status_code=404, detail="Prealta no encontrada")
        row = rows[0]

        company = str(row.get("empresa") or "").strip()
        name = str(row.get("nombre") or "").strip()
        if not company or not name:
            raise HTTPException(status_code=400, detail="La prealta no tiene empresa o nombre validos")

        cursor.execute(
            "SELECT 1 FROM clientes WHERE numero = %s AND UPPER(TRIM(empresa)) = UPPER(TRIM(%s)) LIMIT 1",
            (number, company),
        )
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail=f"Ya existe un cliente {number} en {company}")

        payload_customer = {
            "numero": number,
            "nombre": name,
            "empresa": company,
            "razon_social": row.get("razon_social"),
            "calle": row.get("calle"),
            "no_exterior": row.get("no_exterior"),
            "no_interior": row.get("no_interior"),
            "colonia": row.get("colonia"),
            "alcaldia": row.get("alcaldia"),
            "municipio": row.get("municipio"),
            "codigo_postal": row.get("codigo_postal"),
            "poblacion": row.get("poblacion"),
            "estado": row.get("estado"),
            "pais": row.get("pais"),
            "rfc": row.get("rfc"),
            "telefono": row.get("telefono"),
            "correo_electronico": row.get("correo_electronico"),
            "contacto1": row.get("contacto1"),
            "contacto2": row.get("contacto2"),
            "dias_credito": int(row.get("dias_credito") or 0),
            "consignatario": row.get("consignatario"),
            "consig_calle": row.get("consig_calle"),
            "consig_no_exterior": row.get("consig_no_exterior"),
            "consig_no_interior": row.get("consig_no_interior"),
            "consig_colonia": row.get("consig_colonia"),
            "consig_delegacion": row.get("consig_delegacion"),
            "consig_municipio": row.get("consig_municipio"),
            "consig_codigo_postal": row.get("consig_codigo_postal"),
            "consig_poblacion": row.get("consig_poblacion"),
            "consig_estado": row.get("consig_estado"),
            "consig_pais": row.get("consig_pais"),
            "zona": row.get("zona"),
            "no_proveedor": row.get("no_proveedor"),
            "agente": row.get("agente"),
            "descuento": float(row.get("descuento") or 0),
            "especial": row.get("especial"),
            "tipo": tipo or str(row.get("tipo") or "").strip(),
            "vendedor": row.get("vendedor"),
            "direccion_entrega": row.get("direccion_entrega"),
            "observaciones": row.get("observaciones"),
            "horarios_pago_desde": row.get("horarios_pago_desde"),
            "horarios_pago_hasta": row.get("horarios_pago_hasta"),
            "dia_pago": row.get("dia_pago"),
            "forma_pago": row.get("forma_pago"),
            "horarios_revision_desde": row.get("horarios_revision_desde"),
            "horarios_revision_hasta": row.get("horarios_revision_hasta"),
            "dia_revision": row.get("dia_revision"),
            "compras_nombre": row.get("compras_nombre"),
            "compras_telefono": row.get("compras_telefono"),
            "recibo_nombre": row.get("recibo_nombre"),
            "recibo_telefono": row.get("recibo_telefono"),
            "gerente_nombre": row.get("gerente_nombre"),
            "gerente_telefono": row.get("gerente_telefono"),
            "observaciones_visita": row.get("observaciones_visita"),
            "pedido_realizado_visita": row.get("pedido_realizado_visita"),
        }

        insert_cols = ", ".join(PREALTA_TO_CLIENTE_FIELDS)
        placeholders = ", ".join(["%s"] * len(PREALTA_TO_CLIENTE_FIELDS))
        values = [payload_customer.get(col) for col in PREALTA_TO_CLIENTE_FIELDS]
        cursor.execute(f"INSERT INTO clientes ({insert_cols}) VALUES ({placeholders})", values)
        _upsert_visit(cursor, company, number, payload_customer, username)
        cursor.execute(
            """
            UPDATE clientes_prealta_vendedor
            SET estatus = 'AUTORIZADA',
                usuario_revision = %s,
                fecha_revision = NOW(),
                comentario_revision = %s,
                numero_cliente_sugerido = %s,
                numero_cliente_final = %s,
                empresa_cliente_final = %s,
                tipo = %s
            WHERE id = %s
            """,
            (username, comment, number, number, company, payload_customer["tipo"], prealta_id),
        )
        conn.commit()
        return {"ok": True, "message": "Prealta autorizada y convertida en cliente", "numero": number, "empresa": company}
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
            cursor.close()
        if conn:
            conn.close()


@router.get("")
def list_customers(
    user=Depends(require_user),
    company: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=150, ge=1, le=500),
):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()

        where = []
        params: list[str] = []

        if company:
            aliases = _resolve_company_aliases(company)
            if aliases:
                placeholders = ", ".join(["UPPER(TRIM(%s) COLLATE utf8mb4_unicode_ci)"] * len(aliases))
                where.append(
                    "UPPER(TRIM(c.empresa) COLLATE utf8mb4_unicode_ci) IN "
                    f"({placeholders})"
                )
                params.extend(aliases)

        query_text = _normalizar_numero_cliente(q)
        if query_text:
            where.append(
                "("
                "TRIM(CAST(c.numero AS CHAR)) = %s "
                "OR UPPER(c.nombre) LIKE UPPER(%s) "
                "OR UPPER(COALESCE(c.razon_social, '')) LIKE UPPER(%s) "
                "OR UPPER(TRIM(c.empresa)) LIKE UPPER(%s)"
                ")"
            )
            params.extend([query_text, f"%{query_text}%", f"%{query_text}%", f"%{query_text}%"])

        sql = CUSTOMER_SELECT
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY UPPER(TRIM(c.empresa)), CAST(TRIM(c.numero) AS UNSIGNED), TRIM(c.numero) LIMIT %s"
        params.append(limit)

        cursor.execute(sql, tuple(params))
        items = _dict_rows(cursor)

        count_params = params[:-1]  # exclude limit
        count_sql = "SELECT COUNT(*) AS total FROM clientes c"
        if where:
            count_sql += " WHERE " + " AND ".join(where)
        cursor.execute(count_sql, tuple(count_params))
        total_count = cursor.fetchone()[0]

        return {
            "items": items,
            "count": len(items),
            "total_count": total_count,
            "filters": {"company": company, "q": query_text, "limit": limit},
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.post("")
def create_customer(payload: dict = Body(...), user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload invalido.")
        data = {field: _clean_value(field, payload.get(field)) for field in WRITABLE_FIELDS if field in payload}
        number = str(data.get("numero") or "").strip()
        company = str(data.get("empresa") or "").strip()
        name = str(data.get("nombre") or "").strip()
        if not number or not company or not name:
            raise HTTPException(status_code=400, detail="Numero, empresa y nombre son obligatorios.")

        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_support_tables(cursor)
        cursor.execute(
            "SELECT 1 FROM clientes WHERE numero = %s AND UPPER(TRIM(empresa)) = UPPER(TRIM(%s)) LIMIT 1",
            (number, company),
        )
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Ya existe un cliente con ese numero en esa empresa.")

        fields = [field for field in CUSTOMER_COLUMNS if field in data]
        values = [data.get(field) for field in fields]
        sql = f"INSERT INTO clientes ({','.join(fields)}) VALUES ({','.join(['%s'] * len(fields))})"
        cursor.execute(sql, values)
        _upsert_visit(cursor, company, number, data, user["username"])
        conn.commit()

        customer = _fetch_customer(cursor, company, number)
        return {"ok": True, "message": "Cliente creado correctamente.", "item": customer}
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
            cursor.close()
        if conn:
            conn.close()


@router.get("/export")
def export_customers(
    user=Depends(require_user),
    company: str | None = Query(default=None),
    q: str | None = Query(default=None),
):
    data = list_customers(user=user, company=company, q=q, limit=5000)
    df = pd.DataFrame(data["items"])
    if df.empty:
      raise HTTPException(status_code=404, detail="No hay clientes para exportar.")
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Clientes")
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=clientes_migracion.xlsx"},
    )


@router.post("/import")
async def import_customers(
    file: UploadFile = File(...),
    user=Depends(require_user),
):
    conn = None
    cursor = None
    try:
        content = await file.read()
        filename = (file.filename or "").lower()
        if filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content), dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(content), dtype=str)
        df = df.fillna("")
        df.columns = [str(col).strip().lower() for col in df.columns]

        for field in WRITABLE_FIELDS:
            if field not in df.columns:
                df[field] = ""

        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_support_tables(cursor)
        processed = 0

        for _, row in df.iterrows():
            data = {field: _clean_value(field, row.get(field)) for field in WRITABLE_FIELDS}
            number = str(data.get("numero") or "").strip()
            company = str(data.get("empresa") or "").strip()
            name = str(data.get("nombre") or "").strip()
            if not number or not company or not name:
                continue

            customer_values = [data.get(field) for field in CUSTOMER_COLUMNS]
            update_clause = ", ".join(
                [f"{field}=VALUES({field})" for field in CUSTOMER_COLUMNS if field not in ("numero", "empresa")]
            )
            cursor.execute(
                f"""
                INSERT INTO clientes ({','.join(CUSTOMER_COLUMNS)})
                VALUES ({','.join(['%s'] * len(CUSTOMER_COLUMNS))})
                ON DUPLICATE KEY UPDATE {update_clause}
                """,
                customer_values,
            )
            _upsert_visit(cursor, company, number, data, user["username"])
            processed += 1

        conn.commit()
        return {"ok": True, "message": "Importacion terminada.", "processed": processed}
    except Exception as exc:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.get("/{company}/{number}/info")
def get_customer_info(company: str, number: str, user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_support_tables(cursor)
        customer = _fetch_customer(cursor, company, number)
        if not customer:
            raise HTTPException(status_code=404, detail="Cliente no encontrado.")

        cursor.execute(
            """
            SELECT campo, valor_anterior, valor_nuevo, cambiado_por, fecha_cambio
            FROM clientes_visitas_historial
            WHERE TRIM(cliente_numero) = TRIM(%s)
              AND UPPER(TRIM(empresa) COLLATE utf8mb4_unicode_ci) =
                  UPPER(TRIM(%s) COLLATE utf8mb4_unicode_ci)
            ORDER BY fecha_cambio DESC, id DESC
            LIMIT 50
            """,
            (number, company),
        )
        history = _dict_rows(cursor)

        visit = {
            "horarios_pago_desde": customer.get("horarios_pago_desde", ""),
            "horarios_pago_hasta": customer.get("horarios_pago_hasta", ""),
            "dia_pago": customer.get("dia_pago", ""),
            "forma_pago": customer.get("forma_pago", ""),
            "horarios_revision_desde": customer.get("horarios_revision_desde", ""),
            "horarios_revision_hasta": customer.get("horarios_revision_hasta", ""),
            "dia_revision": customer.get("dia_revision", ""),
            "compras_nombre": customer.get("compras_nombre", ""),
            "compras_telefono": customer.get("compras_telefono", ""),
            "recibo_nombre": customer.get("recibo_nombre", ""),
            "recibo_telefono": customer.get("recibo_telefono", ""),
            "gerente_nombre": customer.get("gerente_nombre", ""),
            "gerente_telefono": customer.get("gerente_telefono", ""),
            "observaciones_visita": customer.get("observaciones_visita", ""),
            "pedido_realizado_visita": customer.get("pedido_realizado_visita", ""),
        }

        cursor.execute(
            """
            SELECT cip, descripcion, ultima_compra_producto
            FROM (
                SELECT
                    COALESCE(fd.cip, '') AS cip,
                    COALESCE(fd.descripcion, '') AS descripcion,
                    MAX(f.fecha) AS ultima_compra_producto
                FROM facturas f
                JOIN factura_detalle fd ON fd.factura_id = f.id
                WHERE TRIM(f.numero_cliente) = TRIM(%s)
                  AND UPPER(TRIM(f.empresa) COLLATE utf8mb4_unicode_ci) =
                      UPPER(TRIM(%s) COLLATE utf8mb4_unicode_ci)
                GROUP BY COALESCE(fd.cip, ''), COALESCE(fd.descripcion, '')
            ) productos_cliente
            ORDER BY
                CASE WHEN cip = '' THEN 1 ELSE 0 END,
                cip ASC,
                descripcion ASC
            """,
            (number, company),
        )
        product_rows = _dict_rows(cursor)
        products = []
        for row in product_rows:
            cip = str(row.get("cip") or "").strip()
            desc = str(row.get("descripcion") or "").strip()
            fecha = row.get("ultima_compra_producto")
            fecha_txt = str(fecha or "").strip()
            parts = [part for part in (cip, desc, fecha_txt) if part]
            if parts:
                products.append(" - ".join(parts))
        visit["productos_cip"] = "\n".join(products)
        history_visits = _build_visit_history(history, visit)

        cursor.execute(
            """
            SELECT id, cliente_nombre, solicitud_texto, solicitado_por, fecha_solicitud, estado, resuelto_por, fecha_resolucion
            FROM clientes_solicitudes_modificacion
            WHERE TRIM(cliente_numero) = TRIM(%s)
              AND UPPER(TRIM(empresa) COLLATE utf8mb4_unicode_ci) =
                  UPPER(TRIM(%s) COLLATE utf8mb4_unicode_ci)
            ORDER BY fecha_solicitud DESC, id DESC
            LIMIT 30
            """,
            (number, company),
        )
        requests = _dict_rows(cursor)

        customer["lista_precios"] = customer.get("especial") or "Lista General"
        customer["cliente_nombre"] = customer.get("nombre") or ""
        return {
            "customer": customer,
            "visit": visit,
            "history": history,
            "history_visits": history_visits,
            "requests": requests,
            "solicitudes_modificacion": requests,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.get("/{company}/{number}")
def get_customer(company: str, number: str, user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        customer = _fetch_customer(cursor, company, number)
        if not customer:
            raise HTTPException(status_code=404, detail="Cliente no encontrado.")
        return customer
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.put("/{company}/{number}")
def update_customer(company: str, number: str, payload: dict = Body(...), user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload invalido.")
        data = {field: _clean_value(field, payload.get(field)) for field in WRITABLE_FIELDS if field in payload}
        conn = get_legacy_connection()
        cursor = conn.cursor()
        _ensure_support_tables(cursor)

        customer_data = {field: value for field, value in data.items() if field in CUSTOMER_COLUMNS}
        if customer_data:
            assignments = ", ".join([f"{field}=%s" for field in customer_data.keys()])
            values = list(customer_data.values()) + [number.strip(), company.strip()]
            cursor.execute(
                f"""
                UPDATE clientes
                SET {assignments}
                WHERE TRIM(CAST(numero AS CHAR)) = TRIM(%s)
                  AND UPPER(TRIM(empresa) COLLATE utf8mb4_unicode_ci) =
                      UPPER(TRIM(%s) COLLATE utf8mb4_unicode_ci)
                """,
                values,
            )
            if cursor.rowcount <= 0:
                raise HTTPException(status_code=404, detail="Cliente no encontrado para actualizar.")

        if any(field in data for field in VISIT_FIELDS) or any(field in data for field in ("nombre", "telefono", "direccion_entrega")):
            data["empresa"] = company
            data["numero"] = number
            _upsert_visit(cursor, company, number, data, user["username"])

        conn.commit()
        customer = _fetch_customer(cursor, company, number)
        return {"ok": True, "message": "Cliente actualizado correctamente.", "item": customer}
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
            cursor.close()
        if conn:
            conn.close()


@router.delete("/{company}/{number}")
def delete_customer(company: str, number: str, user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM clientes
            WHERE TRIM(CAST(numero AS CHAR)) = TRIM(%s)
              AND UPPER(TRIM(empresa) COLLATE utf8mb4_unicode_ci) =
                  UPPER(TRIM(%s) COLLATE utf8mb4_unicode_ci)
            """,
            (number.strip(), company.strip()),
        )
        if cursor.rowcount <= 0:
            raise HTTPException(status_code=404, detail="Cliente no encontrado para eliminar.")
        conn.commit()
        return {"ok": True, "message": "Cliente eliminado correctamente."}
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
            cursor.close()
        if conn:
            conn.close()
