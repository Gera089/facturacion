import fdb

con = fdb.connect(
    dsn=r"C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa04\Datos\SAE90EMPRE04.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

cur = con.cursor()

print("\n=== COLUMNAS DE FACTC04 ===\n")

cur.execute("""
    SELECT RDB$FIELD_NAME
    FROM RDB$RELATION_FIELDS
    WHERE RDB$RELATION_NAME = 'FACTC04'
    ORDER BY RDB$FIELD_POSITION
""")

for col in cur.fetchall():
    print(col[0].strip())

print("\n==============================\n")

con.close()