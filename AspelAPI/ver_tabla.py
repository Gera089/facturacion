import mysql.connector

host = "192.168.1.105"
user = "Facturacion"
password = "ALD2013*"
database = "comandas_db"
port = 3306

def show_create_table():
    try:
        conn = mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            database=database,
            port=port
        )
        cursor = conn.cursor()

        cursor.execute("SHOW CREATE TABLE clientes;")
        resultado = cursor.fetchone()

        print("\n============================")
        print(" CREATE TABLE clientes")
        print("============================\n")

        # resultado[1] contiene TODO el SQL completo
        print(resultado[1])
        print("\n")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    show_create_table()