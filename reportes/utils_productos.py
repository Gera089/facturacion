# ===============================
# reportes/utils_productos.py
# ===============================
from __future__ import annotations
import pandas as pd

# Meses para ordenamiento
MESES_ORD = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
             "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]

# Palabras clave para clasificar familias
KEYS_QUESOS  = ["queso", "manch", "gouda", "brie", "curad", "cheddar", "camembert", "gruy", "emmental"]
KEYS_JAMONES = ["jamón", "jamon", "iber", "serran", "lomo", "palet", "embut", "salchich", "choriz"]

def clasificar_familia(descripcion: str) -> str:
    """Si contiene queso => Quesos. Si no, busca Jamones. Si no, Otros."""
    if not isinstance(descripcion, str):
        return "Otros"
    d = descripcion.lower()
    if any(k in d for k in KEYS_QUESOS):
        return "Quesos"
    if any(k in d for k in KEYS_JAMONES):
        return "Jamones"
    return "Otros"

def acortar_nombre_producto(nombre: str, max_chars: int = 28) -> str:
    """Abrevia nombres largos con “...”, quitando conectores comunes."""
    if not isinstance(nombre, str):
        return ""
    base = " ".join([p for p in nombre.split() if p.lower() not in {"de","del","la","el","con","sin","y","para"}])
    if len(base) <= max_chars:
        return base
    return base[: max(0, max_chars - 3)] + "..."

def preparar_df_facturas(ventana_mio) -> pd.DataFrame:
    """Toma lo que muestras en pestaña Mío y normaliza columnas clave."""
    df = ventana_mio.obtener_dataframe_facturas().copy()
    if df.empty:
        return df

    # total numérico
    if "total" in df.columns:
        df["total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0.0)

    # mes y orden
    if "mes" in df.columns:
        df["mes"] = df["mes"].astype(str).str[:3].str.upper()
        df["mes_ord"] = df["mes"].apply(lambda m: MESES_ORD.index(m) if m in MESES_ORD else -1)

    return df

def preparar_df_productos(ventana_mio) -> pd.DataFrame:
    """Lee detalle (descripcion, cantidad, precio, factura) y clasifica familia + monto_total."""
    dfp = ventana_mio.obtener_productos_facturados().copy()
    if dfp.empty:
        return dfp

    for c in ("cantidad", "precio"):
        if c in dfp.columns:
            dfp[c] = pd.to_numeric(dfp[c], errors="coerce").fillna(0)

    dfp["monto_total"] = dfp.get("cantidad", 0) * dfp.get("precio", 0)

    # familia por descripción
    if "producto" in dfp.columns:
        dfp["familia"] = dfp["producto"].apply(clasificar_familia)
    else:
        dfp["familia"] = "Otros"

    return dfp

def join_prod_con_mes(dfp: pd.DataFrame, dff: pd.DataFrame) -> pd.DataFrame:
    """Agrega MES/EMPRESA/TIENDA al detalle, uniendo por 'factura'."""
    if dfp.empty or dff.empty:
        return dfp

    cols = ["factura", "mes", "mes_ord", "empresa", "tienda", "total"]
    merge_cols = [c for c in cols if c in dff.columns]
    out = dfp.merge(dff[merge_cols].drop_duplicates("factura"), on="factura", how="left")
    return out

def aplicar_filtros(df: pd.DataFrame,
                    empresa: str | None = None,
                    cliente_txt: str | None = None,
                    fecha_ini=None, fecha_fin=None) -> pd.DataFrame:
    """Aplica filtros suaves por empresa/cliente/fechas (si hay columnas)."""
    if df.empty:
        return df.copy()

    out = df.copy()

    if empresa and empresa != "Todas" and "empresa" in out.columns:
        out = out[out["empresa"] == empresa]

    if cliente_txt and "tienda" in out.columns:
        s = str(cliente_txt).strip().lower()
        if s:
            out = out[out["tienda"].str.lower().str.contains(s, na=False)]

    # si trae 'fecha' y vienen fechas
    if "fecha" in out.columns and (fecha_ini or fecha_fin):
        if fecha_ini is not None:
            out = out[out["fecha"] >= pd.to_datetime(fecha_ini)]
        if fecha_fin is not None:
            out = out[out["fecha"] <= pd.to_datetime(fecha_fin)]

    return out
