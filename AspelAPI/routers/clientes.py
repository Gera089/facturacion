from fastapi import APIRouter, HTTPException, UploadFile, File, Body
from fastapi.responses import FileResponse
from models import Cliente
from database import conectar_clientes as conectar_mysql
import pandas as pd
import os
import tempfile
import numpy as np
import io

# 🔹 Prefijo global: todas las rutas estarán bajo /clientes
router = APIRouter(prefix="/clientes", tags=["Clientes"])


# ============================================================
# ✅ LISTAR CLIENTES
# ============================================================
@router.get("/")
def listar_clientes():
    conn = conectar_mysql()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT 
            numero,
            nombre,
            empresa,
            razon_social,
            calle,
            no_exterior,
            no_interior,
            colonia,
            alcaldia,
            municipio,
            codigo_postal,
            poblacion,
            estado,
            pais,
            rfc,
            telefono,
            correo_electronico,
            contacto1,
            contacto2,
            dias_credito,
            consignatario,
            consig_calle,
            consig_no_exterior,
            consig_no_interior,
            consig_colonia,
            consig_delegacion,
            consig_municipio,
            consig_codigo_postal,
            consig_poblacion,
            consig_estado,
            consig_pais,
            zona,
            no_proveedor,
            agente,
            descuento,
            especial,
            tipo,
            vendedor,
            direccion_entrega,
            observaciones
        FROM clientes
    """)
    clientes = cursor.fetchall()
    conn.close()
    return clientes


# ============================================================
# ✅ AGREGAR CLIENTE
# ============================================================
@router.post("/")
def agregar_cliente(cliente: Cliente):
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a la base de datos.")

    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO clientes (
                numero, nombre, empresa, razon_social, calle, no_exterior, no_interior,
                colonia, alcaldia, municipio, codigo_postal, poblacion, estado, pais,
                rfc, telefono, correo_electronico, contacto1, contacto2, dias_credito,
                consignatario, consig_calle, consig_no_exterior, consig_no_interior,
                consig_colonia, consig_delegacion, consig_municipio, consig_codigo_postal,
                consig_poblacion, consig_estado, consig_pais, zona, no_proveedor, agente,
                descuento, especial, tipo, vendedor, direccion_entrega, observaciones
            ) VALUES (
                %(numero)s, %(nombre)s, %(empresa)s, %(razon_social)s, %(calle)s, %(no_exterior)s, %(no_interior)s,
                %(colonia)s, %(alcaldia)s, %(municipio)s, %(codigo_postal)s, %(poblacion)s, %(estado)s, %(pais)s,
                %(rfc)s, %(telefono)s, %(correo_electronico)s, %(contacto1)s, %(contacto2)s, %(dias_credito)s,
                %(consignatario)s, %(consig_calle)s, %(consig_no_exterior)s, %(consig_no_interior)s,
                %(consig_colonia)s, %(consig_delegacion)s, %(consig_municipio)s, %(consig_codigo_postal)s,
                %(consig_poblacion)s, %(consig_estado)s, %(consig_pais)s, %(zona)s, %(no_proveedor)s, %(agente)s,
                %(descuento)s, %(especial)s, %(tipo)s, %(vendedor)s, %(direccion_entrega)s, %(observaciones)s
            )
        """, cliente.dict())
        conn.commit()
        return {"mensaje": "Cliente agregado con éxito"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Error al insertar cliente: {e}")
    finally:
        conn.close()


# ============================================================
# ✅ EXPORTAR CLIENTES A EXCEL
# ============================================================
@router.get("/exportar")
def exportar_clientes():
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="No se pudo conectar a la base de datos.")

    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM clientes")
    clientes = cursor.fetchall()
    conn.close()

    if not clientes:
        raise HTTPException(status_code=404, detail="No hay clientes para exportar")

    columnas = [
        "numero", "nombre", "empresa", "razon_social", "calle", "no_exterior", "no_interior",
        "colonia", "alcaldia", "municipio", "codigo_postal", "poblacion", "estado", "pais",
        "rfc", "telefono", "correo_electronico", "contacto1", "contacto2", "dias_credito",
        "consignatario", "consig_calle", "consig_no_exterior", "consig_no_interior",
        "consig_colonia", "consig_delegacion", "consig_municipio", "consig_codigo_postal",
        "consig_poblacion", "consig_estado", "consig_pais", "zona", "no_proveedor", "agente",
        "descuento", "especial", "tipo", "vendedor", "direccion_entrega", "observaciones"
    ]

    df = pd.DataFrame(clientes, columns=columnas)
    df["numero"] = pd.to_numeric(df["numero"], errors="coerce")

    tmp_dir = tempfile.gettempdir()
    file_path = os.path.join(tmp_dir, "clientes_exportados.xlsx")

    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Clientes")
        ws = writer.sheets["Clientes"]
        for cell in ws["A"][1:]:
            cell.number_format = "0"

    return FileResponse(
        file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="clientes_exportados.xlsx"
    )


# ============================================================
# ✅ IMPORTAR CLIENTES SIN BORRAR OBSERVACIONES EXISTENTES
#    Y LIMPIEZA COMPLETA DE CAMPOS
# ============================================================

import re
import io
import pandas as pd
from fastapi import UploadFile, File, HTTPException, APIRouter
from database import conectar_clientes as conectar_mysql


# ------------------------------------------------------------
# 🔧 Normalizadores genéricos
# ------------------------------------------------------------

def limpiar_campo_corto(valor, max_len=50):
    """Limpia campos tipo no_exterior, no_interior, teléfonos, etc."""
    if valor is None:
        return None

    v = str(valor).strip()

    if v == "" or v.upper() == "BAJA":
        return None

    # Quitar saltos de línea
    v = v.replace("\n", " ").replace("\r", " ")

    # Reemplazar espacios múltiples por uno
    v = re.sub(r"\s+", " ", v)

    return v[:max_len]


def limpiar_codigo_postal(valor):
    if not valor:
        return None
    v = re.sub(r"[^0-9]", "", str(valor).strip())
    return v[:20] if v else None


def limpiar_telefono(valor):
    if not valor:
        return None
    v = re.sub(r"[^0-9]", "", str(valor).strip())
    return v[:50] if v else None


def limpiar_rfc(valor):
    if not valor:
        return None

    v = str(valor).upper().strip()
    v = v.replace(".", "").replace(" ", "").replace("RFC", "")
    v = re.sub(r"[^A-Z0-9]", "", v)

    return v[:20] if v else None

def limpiar_observaciones(valor, max_len=500):
    """Limpia el texto de observaciones sin eliminar información útil."""

    if valor is None:
        return None

    v = str(valor).strip()

    # Consideramos vacío/irrelevante
    if v.upper() in ["", "NAN", "NONE", "NULL", "N/A", "BAJA"]:
        return None

    # Quitar saltos de línea
    v = v.replace("\n", " ").replace("\r", " ")

    # Quitar múltiples espacios
    v = re.sub(r"\s+", " ", v)

    # Quitar caracteres no imprimibles
    v = re.sub(r"[^\x20-\x7EáéíóúÁÉÍÓÚñÑüÜ]", "", v)

    # Limitar tamaño seguro
    v = v[:max_len]

    return v


# ============================================================
# 🚀 IMPORTAR CLIENTES SIN TRUNCAR NI PERDER OBSERVACIONES
# ============================================================
@router.post("/importar")
async def importar_clientes(file: UploadFile = File(...)):
    try:
        contenido = await file.read()
        nombre_archivo = (file.filename or "").lower()

        # --- Leer archivo ---
        if nombre_archivo.endswith(".xlsx") or nombre_archivo.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(contenido))
        elif nombre_archivo.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contenido))
        else:
            raise HTTPException(400, "Formato no soportado. Usa .xlsx, .xls o .csv")

        df.columns = [str(c).strip().lower() for c in df.columns]

        columnas_mysql = [
            "numero","nombre","empresa","razon_social","calle","no_exterior","no_interior",
            "colonia","alcaldia","municipio","codigo_postal","poblacion","estado","pais",
            "rfc","telefono","correo_electronico","contacto1","contacto2","dias_credito",
            "consignatario","consig_calle","consig_no_exterior","consig_no_interior",
            "consig_colonia","consig_delegacion","consig_municipio","consig_codigo_postal",
            "consig_poblacion","consig_estado","consig_pais","zona","no_proveedor","agente",
            "descuento","especial","tipo","vendedor","direccion_entrega","observaciones"
        ]

        # Crear columnas faltantes
        for col in columnas_mysql:
            if col not in df.columns:
                df[col] = None

        df = df[columnas_mysql]

        # Columnas cortas
        columnas_cortas_50 = [
            "telefono", "no_exterior", "no_interior",
            "consig_no_exterior", "consig_no_interior"
        ]

        columnas_cortas_20 = [
            "codigo_postal", "consig_codigo_postal"
        ]

        registros = []

        for _, row in df.iterrows():
            limpio = []

            for col in columnas_mysql:
                v = row[col]

                # Vacíos → None
                if pd.isna(v) or str(v).strip() in ["", "nan", "None", "null"]:
                    limpio.append(None)
                    continue

                # Días crédito
                if col == "dias_credito":
                    nums = re.findall(r"\d+", str(v))
                    limpio.append(int(nums[0]) if nums else None)
                    continue

                # Descuento
                if col == "descuento":
                    try:
                        limpio.append(float(str(v).replace(",", ".")))
                    except:
                        limpio.append(0.0)
                    continue

                # RFC
                if col == "rfc":
                    limpio.append(limpiar_rfc(v))
                    continue

                # Teléfono
                if col == "telefono":
                    limpio.append(limpiar_telefono(v))
                    continue

                # Código Postal
                if col == "codigo_postal":
                    limpio.append(limpiar_codigo_postal(v))
                    continue

                # Campos cortos max 50
                if col in columnas_cortas_50:
                    limpio.append(limpiar_campo_corto(v, 50))
                    continue

                # Campos cortos max 20
                if col in columnas_cortas_20:
                    limpio.append(limpiar_campo_corto(v, 20))
                    continue
                
                # Observaciones → limpiar
                if col == "observaciones":
                    limpio.append(limpiar_observaciones(v))
                    continue

                # Resto normal
                limpio.append(str(v).strip())

            registros.append(dict(zip(columnas_mysql, limpio)))

        # --- Insertar / Actualizar ---
        conn = conectar_mysql()
        cursor = conn.cursor()

        for reg in registros:
            numero = reg["numero"]
            if not numero:
                continue

            # Obtener observaciones actuales y existencia
            cursor.execute("SELECT observaciones FROM clientes WHERE numero=%s", (numero,))
            fila_existente = cursor.fetchone()

            # LIMPIA observaciones ANTES de aplicar reglas
            obs_excel = limpiar_observaciones(reg.get("observaciones", None))

            if fila_existente:   # ------------ UPDATE ------------
                observ_actual = fila_existente[0]

                # OPCIÓN A: si Excel viene vacío → conservar
                if obs_excel is None:
                    reg["observaciones"] = observ_actual
                else:
                    reg["observaciones"] = obs_excel

                set_clause = ", ".join([f"{col}=%s" for col in columnas_mysql])
                valores = [reg[col] for col in columnas_mysql]

                cursor.execute(
                    f"UPDATE clientes SET {set_clause} WHERE numero=%s",
                    (*valores, numero),
                )

            else:                # ------------ INSERT ------------
                # Observaciones del Excel ya viene limpia
                reg["observaciones"] = obs_excel

                cols = ",".join(columnas_mysql)
                placeholders = ",".join(["%s"] * len(columnas_mysql))
                vals = [reg[col] for col in columnas_mysql]

                cursor.execute(
                    f"INSERT INTO clientes ({cols}) VALUES ({placeholders})",
                    vals,
                )

        conn.commit()
        cursor.close()
        conn.close()

        return {
            "mensaje": "Importación completada sin tocar 'observaciones' y con datos limpios.",
            "clientes_procesados": len(registros)
        }

    except Exception as e:
        raise HTTPException(400, f"Error al importar clientes: {e}")


# ============================================================
# ✅ ACTUALIZAR CLIENTE
# ============================================================
@router.put("/{numero}/{empresa}")
async def actualizar_cliente(numero: str, empresa: str, datos: dict = Body(...)):
    try:
        if not isinstance(datos, dict):
            raise HTTPException(status_code=400, detail="El cuerpo debe ser un JSON válido")

        conn = conectar_mysql()
        if not conn:
            raise HTTPException(status_code=500, detail="No se pudo conectar a la base de datos.")

        cursor = conn.cursor()

        columnas_validas = [
            "nombre", "razon_social", "calle", "no_exterior", "no_interior", "colonia", "alcaldia", "municipio",
            "codigo_postal", "poblacion", "estado", "pais", "rfc", "telefono", "correo_electronico", "contacto1",
            "contacto2", "dias_credito", "consignatario", "consig_calle", "consig_no_exterior", "consig_no_interior",
            "consig_colonia", "consig_delegacion", "consig_municipio", "consig_codigo_postal", "consig_poblacion",
            "consig_estado", "consig_pais", "zona", "no_proveedor", "agente", "descuento", "especial", "tipo",
            "vendedor", "direccion_entrega", "observaciones"
        ]

        datos_limpios = {}
        for k, v in datos.items():
            if k not in columnas_validas:
                continue

            if v in [None, "", "null", "None", "NaN"]:
                datos_limpios[k] = None
            else:
                val = str(v).strip()

                if k == "dias_credito":
                    import re
                    nums = re.findall(r"\d+", val)
                    val = nums[0] if nums else "0"

                if k == "descuento":
                    val = val or "0"

                datos_limpios[k] = val

        if not datos_limpios:
            raise HTTPException(status_code=400, detail="No hay campos válidos para actualizar.")

        campos = ", ".join([f"{col}=%s" for col in datos_limpios.keys()])
        valores = list(datos_limpios.values()) + [numero, empresa]

        query = f"UPDATE clientes SET {campos} WHERE numero=%s AND empresa=%s"
        cursor.execute(query, valores)
        conn.commit()

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Cliente no encontrado para actualizar.")

        cursor.close()
        conn.close()
        return {"status": "ok", "mensaje": f"Cliente {numero} actualizado correctamente."}

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error al actualizar cliente: {e}")


# ============================================================
# ✅ ELIMINAR CLIENTE
# ============================================================
@router.delete("/{numero}/{empresa}")
async def eliminar_cliente(numero: str, empresa: str):
    try:
        conn = conectar_mysql()
        if not conn:
            raise HTTPException(status_code=500, detail="No se pudo conectar a la base de datos.")

        cursor = conn.cursor()
        cursor.execute("DELETE FROM clientes WHERE numero=%s AND empresa=%s", (numero, empresa))
        conn.commit()

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Cliente no encontrado para eliminar.")

        cursor.close()
        conn.close()
        return {"status": "ok", "mensaje": f"Cliente {numero} eliminado correctamente."}

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error al eliminar cliente: {e}")


# ============================================================
# ✅ OBTENER CLIENTE ESPECÍFICO
# ============================================================
@router.get("/{numero}/{empresa}")
def obtener_cliente(numero: str, empresa: str):
    conn = conectar_mysql()
    if not conn:
        raise HTTPException(status_code=500, detail="Error al conectar con MySQL")

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT 
                numero AS cliente_numero,
                nombre AS cliente_nombre,
                razon_social,
                empresa,
                calle,
                no_exterior,
                no_interior,
                colonia,
                alcaldia,
                municipio,
                codigo_postal,
                poblacion,
                estado,
                pais,
                rfc,
                telefono,
                correo_electronico,
                contacto1,
                contacto2,
                dias_credito,
                consignatario,
                consig_calle,
                consig_no_exterior,
                consig_no_interior,
                consig_colonia,
                consig_delegacion,
                consig_municipio,
                consig_codigo_postal,
                consig_poblacion,
                consig_estado,
                consig_pais,
                IFNULL(descuento, 0) AS descuento,
                IFNULL(especial, 'Lista General') AS lista_precios,
                vendedor,
                tipo,
                IFNULL(no_proveedor, '-') AS no_proveedor
            FROM clientes
            WHERE numero = %s AND empresa = %s
        """, (numero, empresa))

        cliente = cursor.fetchone()

        if not cliente:
            raise HTTPException(status_code=404, detail=f"Cliente {numero} no encontrado para {empresa}")

        return cliente

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error SQL: {e}")

    finally:
        cursor.close()
        conn.close()


