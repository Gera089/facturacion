from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from database import conectar_mysql

router = APIRouter(prefix="/precios", tags=["Precios"])


# =======================================================
# 1️⃣ Obtener todas las listas de precios
# =======================================================
@router.get("/listas_precios/")
def obtener_listas_precios():
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(500, "Error de conexión")

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, nombre, descripcion
            FROM listas_precios
            ORDER BY id
        """)
        return cursor.fetchall()
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()


# =======================================================
# 2️⃣ Productos + precios por lista + IVA + tipo_lista
# =======================================================
@router.get("/productos/precios")
def obtener_productos_con_precios():
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(500, "Error de conexión")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT 
                p.cip,
                p.descripcion,
                p.unidad,
                p.tipo_lista,
                p.iva
            FROM productos p
            ORDER BY p.cip
        """)
        productos = cursor.fetchall()

        # Precios por lista
        cursor.execute("""
            SELECT 
                lp.nombre AS lista_nombre,
                pp.cip,
                pp.precio
            FROM precios_productos pp
            JOIN listas_precios lp ON lp.id = pp.lista_id
        """)
        precios = cursor.fetchall()

        # Mapa {cip: {lista: precio}}
        precios_map = {}
        for p in precios:
            precios_map.setdefault(p["cip"], {})[p["lista_nombre"]] = float(p["precio"])

        # Mezclar precios
        for p in productos:
            p["precios"] = precios_map.get(p["cip"], {})

        return productos

    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()


# =======================================================
# 3️⃣ Actualizar múltiples precios
# =======================================================
class PrecioItem(BaseModel):
    lista_id: int
    cip: str
    precio: float


@router.put("/actualizar_multiples")
def actualizar_multiples_precios(items: List[PrecioItem]):
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(500, "Error de conexión")

    try:
        cursor = conn.cursor()

        for item in items:
            cursor.execute("""
                INSERT INTO precios_productos (lista_id, cip, precio)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE precio = VALUES(precio)
            """, (item.lista_id, item.cip, item.precio))

        conn.commit()
        return {"message": f"{len(items)} precios actualizados"}

    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))

    finally:
        conn.close()


# =======================================================
# 4️⃣ Crear una nueva lista de precios
# =======================================================
class NuevaLista(BaseModel):
    nombre: str
    descripcion: str | None = None


@router.post("/listas_precios/nueva")
def crear_lista_precios(lista: NuevaLista):
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(500, "Error de conexión")

    try:
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO listas_precios (nombre, descripcion)
            VALUES (%s, %s)
        """, (lista.nombre, lista.descripcion))

        lista_id = cursor.lastrowid

        # Inicializar precios = 0
        cursor.execute("SELECT cip FROM productos")
        for (cip,) in cursor.fetchall():
            cursor.execute("""
                INSERT IGNORE INTO precios_productos (lista_id, cip, precio)
                VALUES (%s, %s, 0.00)
            """, (lista_id, cip))

        conn.commit()
        return {"message": "Lista creada", "lista_id": lista_id}

    except Exception as e:
        conn.rollback()
        if "Duplicate entry" in str(e):
            raise HTTPException(400, "La lista ya existe")
        raise HTTPException(500, str(e))

    finally:
        conn.close()


# =======================================================
# 5️⃣ Eliminar lista de precios
# =======================================================
@router.delete("/listas_precios/{lista_id}")
def eliminar_lista_precios(lista_id: int):
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(500, "Error de conexión")

    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM listas_precios WHERE id = %s", (lista_id,))

        if cursor.rowcount == 0:
            raise HTTPException(404, "No existe la lista")

        conn.commit()
        return {"message": "Lista eliminada"}

    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))

    finally:
        conn.close()


# =======================================================
# 6️⃣ Obtener precio por lista
# =======================================================
@router.get("/obtener_precio/{lista_id}/{cip}")
def obtener_precio(lista_id: int, cip: str):
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(500, "Error de conexión")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT precio
            FROM precios_productos
            WHERE lista_id = %s AND cip = %s
        """, (lista_id, cip))

        row = cursor.fetchone()
        return {"precio": float(row["precio"]) if row else 0.00}

    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()


# =======================================================
# 7️⃣ Precio por cliente (CORREGIDO Y COMPLETO)
# =======================================================
@router.get("/precio_cliente/{numero}/{empresa}/{cip}")
def obtener_precio_por_cliente(numero: str, empresa: str, cip: str):
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(500, "Error de conexión")

    try:
        cursor = conn.cursor(dictionary=True)

        # 1. Lista del cliente
        cursor.execute("""
            SELECT 
                IFNULL(especial, 'Lista General') AS lista_nombre,
                descuento
            FROM clientes
            WHERE numero = %s AND empresa = %s
        """, (numero, empresa))
        cliente = cursor.fetchone()

        if not cliente:
            raise HTTPException(404, "Cliente no encontrado")

        lista = cliente["lista_nombre"]

        # 2. Datos del producto
        cursor.execute("""
            SELECT descripcion, unidad, tipo_lista, iva
            FROM productos
            WHERE cip = %s
        """, (cip,))
        producto = cursor.fetchone()

        if not producto:
            raise HTTPException(404, "Producto no encontrado")

        # 3. Precio según lista
        cursor.execute("""
            SELECT pp.precio
            FROM precios_productos pp
            JOIN listas_precios lp ON lp.id = pp.lista_id
            WHERE lp.nombre = %s AND pp.cip = %s
        """, (lista, cip))
        precio = cursor.fetchone()

        # Si no aparece → Lista General
        if not precio:
            cursor.execute("""
                SELECT pp.precio
                FROM precios_productos pp
                JOIN listas_precios lp ON lp.id = pp.lista_id
                WHERE lp.nombre = 'Lista General' AND pp.cip = %s
            """, (cip,))
            precio = cursor.fetchone()

        return {
            "cip": cip,
            "descripcion": producto["descripcion"],
            "unidad": producto["unidad"],
            "tipo_lista": producto["tipo_lista"],
            "iva": producto["iva"],
            "lista": lista,
            "precio": float(precio["precio"]) if precio else 0.0
        }

    except Exception as e:
        raise HTTPException(500, str(e))

    finally:
        conn.close()