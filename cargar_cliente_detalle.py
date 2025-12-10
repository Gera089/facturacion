import requests
import pandas as pd

API_URL = "http://tuservidor/api"  # ← reemplaza

def cargar_datos_cliente_detalle(numero_cliente, empresa):
    """
    Carga los datos del cliente desde la API y devuelve:
    - cliente_data (dict)
    - df_facturas (DataFrame)
    - df_productos (DataFrame)
    """

    url = f"{API_URL}/clientes/{numero_cliente}/{empresa}"
    resp = requests.get(url)

    if resp.status_code != 200:
        raise Exception(f"Error al obtener cliente: {resp.text}")

    data = resp.json()

    cliente_data = {
        "nombre": data.get("cliente_nombre", ""),
        "razon_social": data.get("razon_social", ""),
        "rfc": data.get("rfc", ""),
        "poblacion": data.get("poblacion", ""),
        "estado": data.get("estado", ""),
        "telefono": data.get("telefono", ""),
        "agente": data.get("agente", "")
    }

    # Facturas
    facturas = data.get("facturas", [])
    df_facturas = pd.DataFrame(facturas)

    # Productos
    productos = data.get("productos", [])
    df_productos = pd.DataFrame(productos)

    return cliente_data, df_facturas, df_productos