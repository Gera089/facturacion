import fdb

def listar_columnas():
    print("🔌 Conectando a Empresa 03...")

    con = fdb.connect(
        dsn="192.168.1.105:C:\\Program Files (x86)\\Common Files\\Aspel\\Sistemas Aspel\\SAE9.00\\Empresa03\\Datos\\SAE90EMPRE03.FDB",
        user="SYSDBA",
        password="masterkey",
        charset="WIN1252"
    )

    cur = con.cursor()
    cur.execute("""
        SELECT TRIM(r.RDB$FIELD_NAME)
        FROM RDB$RELATION_FIELDS r
        WHERE r.RDB$RELATION_NAME = 'FACTR03'
        ORDER BY r.RDB$FIELD_POSITION
    """)

    print("\n=== COLUMNAS FACTR03 ===")
    for row in cur.fetchall():
        print(row[0])

    con.close()
    print("\n✔ Finalizado")


if __name__ == "__main__":
    listar_columnas()