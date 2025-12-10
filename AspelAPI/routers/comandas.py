from fastapi import APIRouter, HTTPException
from database import conectar_mysql, conectar_facturacion   # ✅ Conexiones centralizadas
from models import Comanda
import mysql.connector

router = APIRouter(prefix="/comandas", tags=["Comandas"])


# ======================================================
# 🔹 Listar empresas
# ======================================================
@router.get("/empresas")
def listar_empresas():
    print("📌 ENTRO A /comandas/empresas") 

    conn = None
    cursor = None
    try:
        conn = conectar_mysql()
        if not conn:
            raise HTTPException(status_code=500, detail="No se pudo conectar a MySQL")

        print("📌 CONEXIÓN MYSQL:", conn)

        cursor = conn.cursor(dictionary=True)
        print("📌 CURSOR CREADO")

        cursor.execute("SELECT DISTINCT empresa FROM clientes ORDER BY empresa ASC")
        print("📌 QUERY EJECUTADA")

        empresas = cursor.fetchall()
        print("📌 EMPRESAS:", empresas)

        return [e["empresa"] for e in empresas]

    except Exception as e:
        print("❌ ERROR EN /empresas:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        try:
            if cursor: cursor.close()
            if conn: conn.close()
        except:
            pass


# ======================================================
# 🔹 Obtener precio para cliente (helper)
# ======================================================
def obtener_precio_cliente(numero_cliente: str, empresa: str, cip: str):
    conn = conectar_mysql()
    if not conn:
        return 0.00

    try:
        cursor = conn.cursor(dictionary=True)

        # 1️⃣ Lista asignada al cliente
        cursor.execute("""
            SELECT IFNULL(especial, 'Lista General') AS lista_nombre
            FROM clientes
            WHERE numero = %s AND empresa = %s
        """, (numero_cliente, empresa))
        cliente = cursor.fetchone()

        if not cliente:
            return 0.00

        lista_nombre = cliente["lista_nombre"]

        # 2️⃣ Buscar precio en esa lista
        cursor.execute("""
            SELECT pp.precio
            FROM precios_productos pp
            JOIN listas_precios lp ON lp.id = pp.lista_id
            WHERE lp.nombre = %s AND pp.cip = %s
        """, (lista_nombre, cip))
        precio = cursor.fetchone()

        if not precio:
            # 3️⃣ Si no existe → usar lista General
            cursor.execute("""
                SELECT pp.precio
                FROM precios_productos pp
                JOIN listas_precios lp ON lp.id = pp.lista_id
                WHERE lp.nombre = 'Lista General' AND pp.cip = %s
            """, (cip,))
            precio = cursor.fetchone()

        return float(precio["precio"]) if precio else 0.00

    except Exception as e:
        print(f"Error al obtener precio para cliente {numero_cliente}: {e}")
        return 0.00

    finally:
        cursor.close()
        conn.close()


# ======================================================
# 🔹 Guardar comanda → Factura
# ======================================================
@router.post("/guardar")
def guardar_comanda(comanda: Comanda):
    total = 0.0
    productos_con_precios = []

    # Calcular precios según cliente
    for p in comanda.productos:
        precio = obtener_precio_cliente(comanda.cliente_numero, comanda.empresa, p.cip)

        cantidad = float(p.kgs or 0) if p.kgs else float(p.piezas or 0)

        importe = precio * cantidad
        total += importe

        productos_con_precios.append({
            "cip": p.cip,
            "descripcion": p.descripcion,
            "cantidad": cantidad,
            "precio_unitario": precio,
            "importe": importe
        })

    # Guardar en facturacion_db
    conn = conectar_facturacion()
    if not conn:
        raise HTTPException(status_code=500, detail="Error al conectar con facturacion_db")

    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO facturas (
                folio, cliente_numero, cliente_nombre,
                empresa, vendedor, fecha, observaciones, total
            )
            VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s)
        """, (
            comanda.folio,
            comanda.cliente_numero,
            comanda.cliente_nombre,
            comanda.empresa,
            comanda.vendedor,
            comanda.observaciones,
            total
        ))

        factura_id = cursor.lastrowid

        for item in productos_con_precios:
            cursor.execute("""
                INSERT INTO productos_factura (
                    factura_id, cip, descripcion, cantidad, precio_unitario, importe
                )
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                factura_id,
                item["cip"],
                item["descripcion"],
                item["cantidad"],
                item["precio_unitario"],
                item["importe"]
            ))

        conn.commit()
        return {
            "mensaje": "Factura guardada correctamente",
            "factura_id": factura_id,
            "total": total
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error al guardar factura: {e}")

    finally:
        conn.close()


# ======================================================
# 🔹 Obtener comanda por folio
# ======================================================
@router.get("/{folio}")
def obtener_comanda(folio: str):
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="Error al conectar con MySQL")

    try:
        cursor = conn.cursor(dictionary=True)
        print(f"🔍 Buscando comanda folio: {folio}")

        # Encabezado
        print("🔥 ESTOY EN EL ARCHIVO CORRECTO – SE EJECUTA ESTA LÍNEA")

        cursor.execute("""
            SELECT 
                c.id,
                c.folio,
                c.cliente_numero,
                c.cliente_nombre,
                c.empresa,
                c.vendedor,
                cl.rfc
            FROM comandas c
            LEFT JOIN clientes cl
                ON CONVERT(cl.numero USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                CONVERT(c.cliente_numero USING utf8mb4) COLLATE utf8mb4_unicode_ci
            AND CONVERT(cl.empresa USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                CONVERT(c.empresa USING utf8mb4) COLLATE utf8mb4_unicode_ci
            WHERE 
                CONVERT(c.folio USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                CONVERT(%s USING utf8mb4) COLLATE utf8mb4_unicode_ci
        """, (folio,))
        comanda = cursor.fetchone()

        if not comanda:
            raise HTTPException(status_code=404, detail=f"Comanda {folio} no encontrada")

        print("✅ Comanda encontrada:", comanda)

        # Productos
        cursor.execute("""
            SELECT 
                p.cip,
                p.descripcion,
                pc.kgs AS cantidad,
                pc.piezas AS pzas
            FROM productos_comanda pc
            INNER JOIN productos p ON p.cip = pc.cip
            WHERE pc.comanda_id = %s
        """, (comanda["id"],))

        productos = cursor.fetchall()
        comanda["productos"] = productos

        print("✅ Productos:", productos)

        return comanda

    except Exception as e:
        print("❌ ERROR GENERAL:", e)
        raise HTTPException(status_code=500, detail=f"Error: {e}")

    finally:
        cursor.close()
        conn.close()


# ======================================================
# 🔹 Obtener producto por cip + cliente + empresa
# ======================================================
@router.get("/producto/{cip}/{cliente}/{empresa}")
def obtener_producto(cip: str, cliente: str, empresa: str):
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="Error al conectar con MySQL")

    try:
        cursor = conn.cursor(dictionary=True)

        # Lista del cliente
        cursor.execute("""
            SELECT IFNULL(especial, 'Lista General') AS lista_nombre
            FROM clientes
            WHERE numero = %s AND empresa = %s
        """, (cliente, empresa))
        data_cliente = cursor.fetchone()

        lista_nombre = data_cliente["lista_nombre"] if data_cliente else "Lista General"

        # Obtener producto + precio
        cursor.execute("""
            SELECT 
                p.descripcion,
                IFNULL(p.tipo_lista, 'Estándar') AS tipo_lista,
                IFNULL(p.iva, 'No') AS iva,
                (
                    SELECT pp.precio
                    FROM precios_productos pp
                    INNER JOIN listas_precios lp ON lp.id = pp.lista_id
                    WHERE lp.nombre = %s AND pp.cip = p.cip
                    LIMIT 1
                ) AS precio
            FROM productos p
            WHERE p.cip = %s
        """, (lista_nombre, cip))

        producto = cursor.fetchone()

        if not producto:
            raise HTTPException(status_code=404, detail=f"Producto {cip} no encontrado")

        # Si no hay precio → usar Lista General
        if producto["precio"] is None:
            cursor.execute("""
                SELECT precio
                FROM precios_productos pp
                INNER JOIN listas_precios lp ON lp.id = pp.lista_id
                WHERE lp.nombre = 'Lista General' AND pp.cip = %s
                LIMIT 1
            """, (cip,))
            alt = cursor.fetchone()
            producto["precio"] = alt["precio"] if alt else 0.00

        return producto

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error SQL: {e}")

    finally:
        cursor.close()
        conn.close()