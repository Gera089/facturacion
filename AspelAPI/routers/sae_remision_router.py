from fastapi import APIRouter
from pydantic import BaseModel
from typing import List

# ⬅️ Todos estos .py deben estar en la raíz del proyecto (junto a main.py)
from sae_remision import conectar_empresa, EMPRESAS
from crear_pedido_sae import crear_pedido_sae
from crear_remision_sae import crear_remision_sae


router = APIRouter(prefix="/sae", tags=["SAE"])


class Item(BaseModel):
    cip: str
    descripcion: str | None = ""
    cantidad: float
    precio: float
    iva: bool | None = False  # si lleva IVA o no


class Pedido(BaseModel):
    folio: str | None = None
    cliente: str
    vendedor: str | None = ""
    empresa: str
    productos: List[Item]


# =============================
# 🧾 CREAR PEDIDO EN SAE
# =============================
@router.post("/pedido/crear")
def pedido_crear(datos: Pedido):
    """
    Crea un pedido en SAE usando crear_pedido_sae.
    """
    return crear_pedido_sae(datos.dict())


# =============================
# 🔎 ÚLTIMO PEDIDO POR EMPRESA
# =============================
@router.get("/ultima-pedido/{empresa}")
def ultima_pedido(empresa: str):
    """
    Regresa el último pedido de PEDxx (por fecha de elaboración).
    """
    try:
        emp = empresa.upper()
        num = EMPRESAS[emp]

        con = conectar_empresa(emp)
        cur = con.cursor()

        cur.execute(f"""
            SELECT CVE_DOC
            FROM PED{num}
            ORDER BY FECHAELAB DESC ROWS 1
        """)

        row = cur.fetchone()
        con.close()

        if not row:
            return {"estatus": "sin_resultados"}

        return {
            "estatus": "ok",
            "empresa": emp,
            "folio": row[0].strip()
        }

    except Exception as e:
        return {"estatus": "error", "detalle": str(e)}


# =============================
# 📄 CREAR REMISIÓN EN SAE
# =============================
@router.post("/remision/crear")
def remision_crear(datos: dict):
    """
    Crea una remisión en SAE (FACTRxx / PAR_FACTRxx).
    """
    return crear_remision_sae(datos)