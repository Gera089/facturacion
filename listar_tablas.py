import fdb

con = fdb.connect(
    dsn=r"192.168.1.105:C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa03\Datos\SAE90EMPRE03.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

cur = con.cursor()

cur.execute("""
SELECT TRIM(RDB$RELATION_NAME)
FROM RDB$RELATIONS
WHERE RDB$SYSTEM_FLAG = 0
ORDER BY RDB$RELATION_NAME
""")

for row in cur.fetchall():
    print(row[0])

con.close()