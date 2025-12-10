import mysql.connector
from mysql.connector import Error

def conectar_facturacion():
    try:
        return mysql.connector.connect(
            host="127.0.0.1",
            user="Facturacion",
            password="ALD2013*",
            database="facturacion_db",
            port=3306
        )
    except Error as e:
        print(f"Error al conectar a facturacion_db: {e}")
        return None

# Insertar una factura de prueba
def insertar_factura():
    conn = conectar_facturacion()
    if conn:
        cursor = conn.cursor()

        # Crear factura
        cursor.execute("""
            INSERT INTO facturas (folio, cliente_numero, cliente_nombre, empresa, vendedor, fecha, observaciones, total)
            VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s)
        """, (
            1001, "900210", "DLAGOURMET", "DLAGOURMET", "SANTILLAN", "Factura de prueba", 175.00
        ))
        factura_id = cursor.lastrowid

        # Insertar productos de la factura
        cursor.execute("""
            INSERT INTO productos_factura (factura_id, cip, descripcion, cantidad, precio_unitario, importe)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (factura_id, "P001", "Queso Manchego", 5, 25.00, 125.00))

        cursor.execute("""
            INSERT INTO productos_factura (factura_id, cip, descripcion, cantidad, precio_unitario, importe)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (factura_id, "P002", "Jamón Serrano", 2, 25.00, 50.00))

        conn.commit()
        conn.close()

        print(f"Factura {factura_id} creada con éxito en facturacion_db")

# Consultar facturas
def listar_facturas():
    conn = conectar_facturacion()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM facturas ORDER BY fecha DESC")
        facturas = cursor.fetchall()
        conn.close()
        return facturas

if __name__ == "__main__":
    insertar_factura()
    print("Facturas registradas:")
    for f in listar_facturas():
        print(f)