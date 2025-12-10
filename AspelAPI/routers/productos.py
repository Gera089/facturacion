from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
import pandas as pd
import io

from database import conectar_mysql, conectar_comandas


router = APIRouter(prefix="/productos", tags=["productos"])


# =========================================================
# 1️⃣ LISTAR PRODUCTOS (MYSQL)
# =========================================================
@router.get("/")
def listar_productos():
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="Error conectando a MySQL")

    cursor = conn.cursor(dictionary=True)

    try:
        # ------------------------------
        # Obtener productos base
        # ------------------------------
        cursor.execute("""
            SELECT 
                p.cip, 
                p.descripcion, 
                p.unidad, 
                IFNULL(p.tipo_lista, 'Estándar') AS tipo_lista,
                IFNULL(p.iva, 'No') AS iva,
                IFNULL(p.codigo_barras, '') AS codigo_barras
            FROM productos p
            ORDER BY p.cip ASC
        """)
        productos = cursor.fetchall()

        productos_dict = {p["cip"]: p for p in productos}

        # ------------------------------
        # Obtener precios x lista
        # ------------------------------
        cursor.execute("""
            SELECT 
                pp.cip,
                lp.nombre AS lista_nombre,
                pp.precio,
                COALESCE(pp.codigo_barras, '') AS codigo_barras
            FROM precios_productos pp
            INNER JOIN listas_precios lp ON lp.id = pp.lista_id
        """)
        precios = cursor.fetchall()

        # Combinar con seguridad
        for p in precios:
            cip = p.get("cip")
            lista = p.get("lista_nombre")

            if cip not in productos_dict:
                continue
            if not lista:
                continue

            productos_dict[cip].setdefault("precios", {})[lista] = {
                "precio": float(p.get("precio", 0)),
                "codigo_barras": p.get("codigo_barras", "")
            }

        return list(productos_dict.values())

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listando productos: {e}")

    finally:
        cursor.close()
        conn.close()


# =========================================================
# 2️⃣ OBTENER PRODUCTO POR CIP
# =========================================================
@router.get("/{cip}")
def obtener_producto(cip: str):
    conn = conectar_mysql()
    cursor = conn.cursor(dictionary=True)

    try:
        # Producto base
        cursor.execute("""
            SELECT 
                p.cip, 
                p.descripcion, 
                p.unidad, 
                IFNULL(p.tipo_lista, 'Estándar') AS tipo_lista,
                IFNULL(p.iva, 'No') AS iva,
                IFNULL(p.codigo_barras, '') AS codigo_barras
            FROM productos p
            WHERE p.cip = %s
        """, (cip,))
        producto = cursor.fetchone()

        if not producto:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        # Precios asociados
        cursor.execute("""
            SELECT 
                lp.nombre AS lista_nombre,
                pp.precio,
                COALESCE(pp.codigo_barras, '') AS codigo_barras
            FROM precios_productos pp
            INNER JOIN listas_precios lp ON lp.id = pp.lista_id
            WHERE pp.cip = %s
        """, (cip,))
        precios = cursor.fetchall()

        producto["precios"] = {
            p["lista_nombre"]: {
                "precio": float(p["precio"]),
                "codigo_barras": p["codigo_barras"]
            }
            for p in precios
            if p.get("lista_nombre")
        }

        return producto

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo producto: {e}")

    finally:
        cursor.close()
        conn.close()


# =========================================================
# 3️⃣ AGREGAR PRODUCTO
# =========================================================
@router.post("/agregar")
def agregar_producto(producto: dict):
    conn = conectar_mysql()
    cursor = conn.cursor()

    try:
        tipo_lista = producto.get("tipo_lista", "Estándar")
        iva = producto.get("iva", "No")
        codigo = producto.get("codigo_barras", "")

        cursor.execute("""
            INSERT INTO productos (cip, descripcion, unidad, tipo_lista, iva, codigo_barras)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
                descripcion=%s, unidad=%s, tipo_lista=%s, iva=%s, codigo_barras=%s
        """, (
            producto["cip"], producto["descripcion"], producto["unidad"],
            tipo_lista, iva, codigo,
            producto["descripcion"], producto["unidad"], tipo_lista, iva, codigo
        ))

        # Precios por lista
        if "precios" in producto:
            for lista_id, datos in producto["precios"].items():
                precio = datos.get("precio", 0)
                cod = datos.get("codigo_barras", "")

                cursor.execute("""
                    INSERT INTO precios_productos (lista_id, cip, precio, codigo_barras)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE precio=%s, codigo_barras=%s
                """, (
                    lista_id, producto["cip"], precio, cod,
                    precio, cod
                ))

        conn.commit()
        return {"mensaje": "Producto agregado correctamente"}

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error agregando producto: {e}")

    finally:
        conn.close()


# =========================================================
# 4️⃣ EDITAR PRODUCTO
# =========================================================
@router.put("/{cip}")
def editar_producto(cip: str, producto: dict):
    conn = conectar_mysql()
    cursor = conn.cursor()

    try:
        tipo_lista = producto.get("tipo_lista", "Estándar")
        iva = producto.get("iva", "No")
        codigo = producto.get("codigo_barras", "")

        cursor.execute("""
            UPDATE productos
            SET descripcion=%s, unidad=%s, tipo_lista=%s, iva=%s, codigo_barras=%s
            WHERE cip=%s
        """, (
            producto["descripcion"], producto["unidad"],
            tipo_lista, iva, codigo,
            cip
        ))

        # Precios
        if "precios" in producto:
            for lista_id, datos in producto["precios"].items():
                precio = datos.get("precio", 0)
                cod = datos.get("codigo_barras", "")

                cursor.execute("""
                    INSERT INTO precios_productos (lista_id, cip, precio, codigo_barras)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE precio=%s, codigo_barras=%s
                """, (
                    lista_id, cip, precio, cod,
                    precio, cod
                ))

        conn.commit()
        return {"mensaje": "Producto actualizado"}

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error editando producto: {e}")

    finally:
        conn.close()


# =========================================================
# 5️⃣ ACTUALIZAR SOLO TIPO LISTA
# =========================================================
@router.put("/actualizar_tipo/{cip}")
def actualizar_tipo_lista(cip: str, tipo: dict):
    nueva = tipo.get("tipo_lista", "Estándar")
    conn = conectar_mysql()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE productos SET tipo_lista=%s WHERE cip=%s", (nueva, cip))
        conn.commit()
        return {"mensaje": "Tipo de lista actualizado"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error actualizando tipo lista: {e}")
    finally:
        conn.close()


# =========================================================
# 6️⃣ ACTUALIZAR SOLO IVA
# =========================================================
@router.put("/actualizar_iva/{cip}")
def actualizar_iva(cip: str, data: dict):
    iva = data.get("iva", "No")
    if iva not in ["Sí", "No"]:
        raise HTTPException(status_code=400, detail="IVA debe ser 'Sí' o 'No'")

    conn = conectar_mysql()
    cursor = conn.cursor()

    try:
        cursor.execute("UPDATE productos SET iva=%s WHERE cip=%s", (iva, cip))
        conn.commit()
        return {"mensaje": f"IVA actualizado para {cip}"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error actualizando IVA: {e}")
    finally:
        conn.close()


# =========================================================
# 7️⃣ ELIMINAR PRODUCTO
# =========================================================
@router.delete("/{cip}")
def eliminar_producto(cip: str):
    conn = conectar_mysql()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM productos WHERE cip=%s", (cip,))
    conn.commit()
    conn.close()

    return {"mensaje": "Producto eliminado"}


# =========================================================
# 8️⃣ EXPORTAR A EXCEL
# =========================================================
@router.get("/exportar")
def exportar_productos():
    conn = conectar_mysql()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM productos ORDER BY cip ASC")
    productos = cursor.fetchall()
    conn.close()

    df = pd.DataFrame(productos)
    output = io.BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=productos.xlsx"}
    )


# =========================================================
# 9️⃣ IMPORTAR DESDE EXCEL
# =========================================================
@router.post("/importar")
def importar_productos(file: UploadFile = File(...)):
    try:
        df = pd.read_excel(io.BytesIO(file.file.read()))

        conn = conectar_mysql()
        cursor = conn.cursor()

        for _, row in df.iterrows():
            cursor.execute("""
                INSERT INTO productos (cip, descripcion, unidad, iva, codigo_barras)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    descripcion=%s, unidad=%s, iva=%s, codigo_barras=%s
            """, (
                row["cip"], row["descripcion"], row["unidad"],
                row.get("iva", "No"), row.get("codigo_barras", ""),
                row["descripcion"], row["unidad"],
                row.get("iva", "No"), row.get("codigo_barras", "")
            ))

        conn.commit()
        conn.close()
        return {"mensaje": "Productos importados correctamente"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error importando productos: {e}")


# =========================================================
# 🔟 PRODUCTOS COMPRADOS POR CLIENTE (COMANDAS)
# =========================================================
@router.get("/cliente/{numero}/{empresa}")
def productos_por_cliente(numero: str, empresa: str):
    conn = conectar_comandas()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT 
                f.factura,
                f.fecha,
                d.cip,
                d.descripcion AS producto,
                d.cantidad,
                d.piezas,
                d.precio,
                (d.cantidad * d.precio) AS monto
            FROM facturas f
            INNER JOIN factura_detalle d 
                ON d.factura_id = f.id
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

        return cursor.fetchall() or []

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error consultando historial: {e}")

    finally:
        cursor.close()
        conn.close()