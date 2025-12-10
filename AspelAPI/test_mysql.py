import mysql.connector

print("Intentando conexión...")

try:
    conn = mysql.connector.connect(
        host="127.0.0.1",
        user="Facturacion",
        password="ALD2013*",
        database="comandas_db",
        port=3306
    )

    print("Conexión exitosa!")

    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM clientes")
    print("Total clientes:", cursor.fetchone())

except Exception as e:
    print("❌ Error:", e)