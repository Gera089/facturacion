import mysql.connector
from mysql.connector import Error

# ============================
#  CONFIGURACIÓN CENTRAL
# ============================
MYSQL_HOST = "192.168.1.105"
MYSQL_USER = "Facturacion"
MYSQL_PASS = "ALD2013*"
MYSQL_PORT = 3306

# Collation unificado para evitar errores
MYSQL_CHARSET = "utf8mb4"
MYSQL_COLLATION = "utf8mb4_unicode_ci"


# ============================
#  FUNCIÓN BASE
# ============================
def _conectar(nombre_bd: str):
    """Crea una conexión MySQL con collation unificado."""
    try:
        return mysql.connector.connect(
            host=MYSQL_HOST,
            user=MYSQL_USER,
            password=MYSQL_PASS,
            database=nombre_bd,
            port=MYSQL_PORT,
            charset=MYSQL_CHARSET,
            collation=MYSQL_COLLATION
        )
    except Error as e:
        print(f"❌ Error al conectar a {nombre_bd}: {e}")
        return None


# ============================
#  CONEXIONES PÚBLICAS
# ============================
def conectar_mysql():
    """BD operativa (comandas activa)."""
    return _conectar("comandas_db")


def conectar_comandas():
    """Alias, mismo propósito."""
    return _conectar("comandas_db")


def conectar_clientes():
    """BD donde viven los clientes (misma BD)."""
    return _conectar("comandas_db")


def conectar_facturacion():
    """BD histórica donde se guardan facturas finales."""
    return _conectar("facturacion_db")