import fdb

con = fdb.connect(
    dsn="192.168.1.105:C:\\Program Files (x86)\\Common Files\\Aspel\\Sistemas Aspel\\SAE9.00\\Empresa03\\Datos\\SAE90EMPRE03.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

cur = con.cursor()

cur.execute("""
    SELECT FIRST 50
        CVE_DOC,
        TIP_DOC,
        STATUS,
        FECHA_DOC,
        CVE_CLPV
    FROM FACTR03
    ORDER BY FECHA_DOC DESC
""")

print("\n======= ULTIMAS 50 REMISIONES EN FACTR03 =======\n")
for row in cur.fetchall():
    print(row)

con.close()
print("\n✅ Listo.")