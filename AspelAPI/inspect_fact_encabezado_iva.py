import fdb

con = fdb.connect(
    dsn=r"192.168.1.105:C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa03\Datos\SAE90EMPRE03.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)
cur = con.cursor()

cur.execute("SELECT RDB$FIELD_NAME FROM RDB$RELATION_FIELDS WHERE RDB$RELATION_NAME='INVE03'")
cols = cur.fetchall()
print("COLUMNAS INVE03:")
for c in cols:
    print(c[0].strip())

con.close()