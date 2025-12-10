from pydantic import BaseModel
from typing import Optional, List


# =======================================
# 🔹 CLIENTE
# =======================================
class Cliente(BaseModel):
    numero: str
    nombre: str
    empresa: str
    razon_social: Optional[str] = None
    calle: Optional[str] = None
    no_exterior: Optional[str] = None
    no_interior: Optional[str] = None
    colonia: Optional[str] = None
    alcaldia: Optional[str] = None
    municipio: Optional[str] = None
    codigo_postal: Optional[str] = None
    poblacion: Optional[str] = None
    estado: Optional[str] = None
    pais: Optional[str] = None
    rfc: Optional[str] = None
    telefono: Optional[str] = None
    correo_electronico: Optional[str] = None
    contacto1: Optional[str] = None
    contacto2: Optional[str] = None
    dias_credito: Optional[int] = None
    consignatario: Optional[str] = None
    consig_calle: Optional[str] = None
    consig_no_exterior: Optional[str] = None
    consig_no_interior: Optional[str] = None
    consig_colonia: Optional[str] = None
    consig_delegacion: Optional[str] = None
    consig_municipio: Optional[str] = None
    consig_codigo_postal: Optional[str] = None
    consig_poblacion: Optional[str] = None
    consig_estado: Optional[str] = None
    consig_pais: Optional[str] = None
    zona: Optional[str] = None
    no_proveedor: Optional[str] = None
    agente: Optional[str] = None
    descuento: Optional[float] = 0.0
    especial: Optional[str] = None
    tipo: Optional[str] = None
    vendedor: Optional[str] = None
    direccion_entrega: Optional[str] = None
    observaciones: Optional[str] = None


# =======================================
# 🔹 PRODUCTO PARA LISTAS / CATÁLOGO
# =======================================
class Producto(BaseModel):
    cip: str
    descripcion: str
    unidad: Optional[str] = None
    tipo_lista: Optional[str] = "Estándar"
    iva: Optional[str] = "No"
    precio: Optional[float] = 0.0


# =======================================
# 🔹 PRECIO
# =======================================
class Precio(BaseModel):
    cip: str
    cliente_numero: Optional[str] = None
    empresa: Optional[str] = None
    precio: float


# =======================================
# 🔹 PRODUCTO DENTRO DE UNA COMANDA
# =======================================
class ProductoComanda(BaseModel):
    cip: str
    descripcion: str
    kgs: float = 0.0
    piezas: float = 0.0
    tipo_lista: Optional[str] = "Estándar"
    iva: Optional[str] = "No"
    precio: Optional[float] = 0.0


# =======================================
# 🔹 COMANDA
# =======================================
class Comanda(BaseModel):
    folio: int
    cliente_numero: str
    cliente_nombre: str
    empresa: str
    vendedor: str
    productos: List[ProductoComanda]
    observaciones: Optional[str] = None
    rfc: Optional[str] = None
