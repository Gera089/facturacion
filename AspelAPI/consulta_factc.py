import fdb

con = fdb.connect(
    dsn=r"192.168.1.105:C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa03\Datos\SAE90EMPRE03.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

cur = con.cursor()

cur.execute("""
    SELECT TIP_DOC, CVE_DOC, FECHAELAB
    FROM FACTC03
    ORDER BY FECHAELAB DESC
    ROWS 5
""")

for row in cur.fetchall():
    print(row)

con.close()