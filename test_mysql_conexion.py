import mysql.connector

print(">>> PROBANDO MYSQL <<<")

try:
    conn = mysql.connector.connect(
        host="127.0.0.1",
        user="Facturacion",
        password="ALD2013*",
        database="comandas_db",
        port=3306,
        connection_timeout=3
    )
    print(">>> CONEXIÓN EXITOSA")
    cursor = conn.cursor()
    cursor.execute("SELECT 1;")
    print(">>> QUERY OK:", cursor.fetchone())
    conn.close()

except Exception as e:
    print(">>> ERROR EN MYSQL:", type(e), e)