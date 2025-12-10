import requests
import pandas as pd

API_URL = "http://127.0.0.1:8000"


def cargar_datos_cliente(numero: str, empresa: str):
    """
    Descarga datos del cliente, facturas y productos desde la API,
    y normaliza las columnas para que el widget funcione.
    """

    # -------------------------------------------------------------
    # 1) CARGAR DATOS DEL CLIENTE
    # -------------------------------------------------------------
    url_cliente = f"{API_URL}/clientes/{numero}/{empresa}"

    r = requests.get(url_cliente)
    print("📌 CLIENTE RAW:", r.json())
    if r.status_code != 200:
        raise Exception(f"Error al cargar cliente: {r.text}")

    raw_cli = r.json()

    # Normalizar claves
    cliente_data = {
        "numero": raw_cli.get("numero") or numero,
        "nombre": raw_cli.get("cliente_nombre")
                or raw_cli.get("nombre")
                or "",
        "razon_social": raw_cli.get("razon_social", ""),
        "rfc": raw_cli.get("rfc", ""),
        "telefono": raw_cli.get("telefono", ""),
        "poblacion": raw_cli.get("ciudad")
                    or raw_cli.get("poblacion", ""),
        "estado": raw_cli.get("estado", ""),
        "agente": raw_cli.get("agente", "")
    }

    # -------------------------------------------------------------
    # 2) CARGAR FACTURAS
    # -------------------------------------------------------------
    url_fact = f"{API_URL}/facturas/cliente/{numero}/{empresa}"
    r2 = requests.get(url_fact)

    # 🔍 DEBUG: Mostrar qué devuelve la API
    try:
        print("📌 FACTURAS RAW:", r2.json()[0])
    except:
        print("📌 FACTURAS RAW: VACÍO")

    if r2.status_code == 200:
        df_fact = pd.DataFrame(r2.json())
    else:
        df_fact = pd.DataFrame()

    # -------------------------------------------------------------
    # 🔄 MAPEAR NOMBRES DE COLUMNAS DE FACTURAS
    # -------------------------------------------------------------
    if not df_fact.empty:

        df_fact.rename(columns={
            "monto": "total",
            "monto_factura": "total",
            "importe": "total",
            "fecha_emision": "fecha",
            "fecha_factura": "fecha",
            "fechaAlta": "fecha",
            "status": "estatus",
            "estado": "estatus",
            "cliente_id": "cliente",
            "id_cliente": "cliente",
            "folio": "factura"
        }, inplace=True)

        # Limpiar total
        if "total" in df_fact.columns:
            df_fact["total"] = (
                df_fact["total"]
                .astype(str)
                .str.replace("$", "", regex=False)
                .str.replace(",", "", regex=False)
                .str.replace(" ", "", regex=False)
            )
            df_fact["total"] = pd.to_numeric(df_fact["total"], errors="coerce").fillna(0)

        # Limpiar fecha
        if "fecha" in df_fact.columns:
            df_fact["fecha"] = pd.to_datetime(df_fact["fecha"], errors="coerce")

        # Asegurar que "estatus" existe
        if "estatus" not in df_fact.columns:
            df_fact["estatus"] = "ACTIVA"

        # Asegurar que "factura" existe
        if "factura" not in df_fact.columns:
            df_fact["factura"] = df_fact.index.astype(str)

    # -------------------------------------------------------------
    # 3) CARGAR PRODUCTOS DEL CLIENTE
    # -------------------------------------------------------------
    url_prod = f"{API_URL}/productos/cliente/{numero}/{empresa}"
    r3 = requests.get(url_prod)

    # 🔍 DEBUG: Ver qué devuelve la API
    try:
        print("📌 PRODUCTOS RAW:", r3.json()[0])
    except:
        print("📌 PRODUCTOS RAW: VACÍO")

    if r3.status_code == 200:
        df_prod = pd.DataFrame(r3.json())
    else:
        df_prod = pd.DataFrame()

    # -------------------------------------------------------------
    # 🔄 MAPEAR NOMBRES DE COLUMNAS DE PRODUCTOS
    # -------------------------------------------------------------
    if not df_prod.empty:

        df_prod.rename(columns={
            "descripcion": "producto",
            "descripcion_producto": "producto",
            "concepto": "producto",
            "producto_nombre": "producto",
            "prod": "producto",

            "cantidad_vendida": "cantidad",
            "cant": "cantidad",

            "piezas_vendidas": "piezas",

            "precio_unitario": "precio",
            "costo": "precio",

            "fecha_emision": "fecha",
            "fecha_compra": "fecha",
        }, inplace=True)

        # Limpiar precio
        if "precio" in df_prod.columns:
            df_prod["precio"] = (
                df_prod["precio"]
                .astype(str)
                .str.replace("$", "", regex=False)
                .str.replace(",", "", regex=False)
                .str.replace(" ", "", regex=False)
            )
            df_prod["precio"] = pd.to_numeric(df_prod["precio"], errors="coerce").fillna(0)

        # Limpiar cantidad
        if "cantidad" in df_prod.columns:
            df_prod["cantidad"] = pd.to_numeric(df_prod["cantidad"], errors="coerce").fillna(0)

        # Limpiar piezas
        if "piezas" in df_prod.columns:
            df_prod["piezas"] = pd.to_numeric(df_prod["piezas"], errors="coerce").fillna(0)

        # Limpiar fecha
        if "fecha" in df_prod.columns:
            df_prod["fecha"] = pd.to_datetime(df_prod["fecha"], errors="coerce")

        # Asegurar factura
        if "factura" not in df_prod.columns:
            df_prod["factura"] = df_prod.index.astype(str)

    # -------------------------------------------------------------
    #  LISTO PARA EL WIDGET
    # -------------------------------------------------------------
    return cliente_data, df_fact, df_prod
