from fastapi import APIRouter, HTTPException
from database import conectar_comandas, conectar_facturacion   # ✅ Conexiones centralizadas
from models import Comanda
import mysql.connector

router = APIRouter(prefix="/facturas", tags=["Facturas"])


# ======================================================
# 🔍 Buscar factura por FOLIO
# ======================================================
@router.get("/folio/{folio}")
def detalle_factura_por_folio(folio: str):
    conn = conectar_comandas()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a la base de datos comandas_db")

    cursor = conn.cursor(dictionary=True)

    try:
        # 1️⃣ Factura principal
        cursor.execute("SELECT * FROM facturas WHERE factura = %s", (folio,))
        factura = cursor.fetchone()

        if not factura:
            raise HTTPException(status_code=404, detail=f"Factura {folio} no encontrada")

        factura_id = factura["id"]

        # 2️⃣ Productos asociados
        cursor.execute("""
            SELECT 
                cip,
                descripcion,
                cantidad,
                piezas,
                precio,
                importe
            FROM factura_detalle
            WHERE factura_id = %s
        """, (factura_id,))
        productos = cursor.fetchall()

        factura["productos"] = productos or []
        return factura

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


# ======================================================
# 🧾 Obtener facturas por cliente y empresa
# ======================================================
@router.get("/cliente/{numero}/{empresa}")
def facturas_por_cliente(numero: str, empresa: str):
    conn = conectar_comandas()
    if not conn:
        raise HTTPException(status_code=500, detail="Error al conectar a BD comandas_db")

    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT 
                f.id,
                f.factura,
                f.numero_cliente AS cliente,
                f.fecha,
                f.total,
                f.estatus,
                f.empresa
            FROM facturas f
            WHERE f.numero_cliente = %s
              AND (
                    CASE 
                        WHEN f.empresa IN ('GOURMET', 'Gourmet España')
                        THEN 'GOURMET'
                        ELSE f.empresa
                    END
                  ) = %s
            ORDER BY f.fecha DESC
        """, (numero, empresa))

        facturas = cursor.fetchall()
        return facturas or []

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


# ======================================================
# 🆕 Crear nueva factura
# ======================================================
@router.post("/")
def crear_factura(datos: dict):
    conn = conectar_comandas()
    if not conn:
        raise HTTPException(status_code=500, detail="Error de conexión con comandas_db")

    cursor = conn.cursor()

    try:
        factura = datos.get("factura") or datos.get("folio")
        if not factura:
            raise HTTPException(status_code=400, detail="Falta el campo 'factura' o 'folio'.")

        numero_cliente = datos.get("numero_cliente") or datos.get("cliente_numero")
        cliente_nombre = datos.get("cliente_nombre") or datos.get("consignatario")
        empresa = datos.get("empresa")

        subtotal = float(datos.get("subtotal", 0) or 0)
        descuento_pct = float(datos.get("descuento_pct", 0) or 0)
        descuento_total = float(datos.get("descuento_total", 0) or 0)
        iva_total = float(datos.get("iva", 0) or 0)
        total = float(datos.get("total", 0) or 0)

        # Insert encabezado
        cursor.execute("""
            INSERT INTO facturas (
                fecha, numero_cliente, consignatario, factura, empresa,
                subtotal, descuento_pct, descuento, iva, total, estatus
            )
            VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Activa')
        """, (
            numero_cliente,
            cliente_nombre,
            factura,
            empresa,
            subtotal,
            descuento_pct,
            descuento_total,
            iva_total,
            total,
        ))

        factura_id = cursor.lastrowid

        # Guardar detalle
        productos = datos.get("productos", []) or []

        for p in productos:
            cursor.execute("""
                INSERT INTO factura_detalle
                    (factura_id, cip, descripcion, cantidad, piezas, precio, importe)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                factura_id,
                p.get("cip"),
                p.get("descripcion"),
                float(p.get("cantidad") or 0),
                int(p.get("piezas") or 0),
                float(p.get("precio") or 0),
                float(p.get("importe") or 0)
            ))

        conn.commit()
        return {
            "mensaje": f"Factura {factura} creada correctamente.",
            "factura": factura,
            "factura_id": factura_id
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


# ======================================================
# ✏️ Actualizar factura existente
# ======================================================
@router.put("/folio/{folio}")
def actualizar_factura(folio: str, datos: dict):
    conn = conectar_comandas()
    if not conn:
        raise HTTPException(status_code=500, detail="Error de conexión con comandas_db")

    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM facturas WHERE factura=%s", (folio,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"Factura {folio} no encontrada")

        factura_id = row[0]

        numero_cliente = datos.get("numero_cliente") or datos.get("cliente_numero")
        cliente_nombre = datos.get("cliente_nombre") or datos.get("consignatario")
        empresa = datos.get("empresa")

        subtotal = float(datos.get("subtotal", 0))
        descuento_pct = float(datos.get("descuento_pct", 0))
        descuento_total = float(datos.get("descuento_total", 0))
        iva_total = float(datos.get("iva", 0))
        total = float(datos.get("total", 0))

        cursor.execute("""
            UPDATE facturas SET
                numero_cliente=%s,
                consignatario=%s,
                empresa=%s,
                subtotal=%s,
                descuento_pct=%s,
                descuento=%s,
                iva=%s,
                total=%s
            WHERE id=%s
        """, (
            numero_cliente,
            cliente_nombre,
            empresa,
            subtotal,
            descuento_pct,
            descuento_total,
            iva_total,
            total,
            factura_id,
        ))

        # Borrar detalle previo
        cursor.execute("DELETE FROM factura_detalle WHERE factura_id=%s", (factura_id,))

        # Insertar detalle nuevo
        productos = datos.get("productos", []) or []

        for p in productos:
            cursor.execute("""
                INSERT INTO factura_detalle
                    (factura_id, cip, descripcion, cantidad, piezas, precio, importe)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                factura_id,
                p.get("cip"),
                p.get("descripcion"),
                float(p.get("cantidad") or 0),
                int(p.get("piezas") or 0),
                float(p.get("precio") or 0),
                float(p.get("importe") or 0)
            ))

        conn.commit()

        return {"mensaje": f"Factura {folio} actualizada correctamente."}

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


# ======================================================
# 📨 Enviar a SAE (crear remisión)
# ======================================================
@router.post("/enviar_a_sae")
def enviar_a_sae(datos: dict):
    """
    Envía los datos a SAE creando una remisión.
    El módulo sae_remision.py ya hace la conversión y conexión Firebird.
    """
    return crear_remision_sae(datos)