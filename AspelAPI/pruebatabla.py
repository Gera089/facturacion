import fdb
import os

os.add_dll_directory(r"C:\\FirebirdPython")

# conexión empresa 4
con = fdb.connect(
    dsn=r"C:\\Program Files (x86)\\Common Files\\Aspel\\Sistemas Aspel\\SAE9.00\\Empresa04\\Datos\\SAE90EMPRE04.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

cur = con.cursor()

cur.execute("""
    SELECT TRIM(RDB$RELATION_NAME)
    FROM RDB$RELATIONS
    WHERE RDB$SYSTEM_FLAG = 0
""")

print("TABLAS EMPRESA 04:")
for row in cur.fetchall():
    print(row[0])