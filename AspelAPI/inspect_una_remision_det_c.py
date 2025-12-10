import fdb

con = fdb.connect(
    dsn=r"192.168.1.105:C:\\Program Files (x86)\\Common Files\\Aspel\\Sistemas Aspel\\SAE9.00\\Empresa03\\Datos\\SAE90EMPRE03.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)
cur = con.cursor()

folio = "0000000001"

print("\n=== DETALLE PAR_FACTC03 ===")
cur.execute("SELECT * FROM PAR_FACTC03 WHERE CVE_DOC=? ORDER BY NUM_PAR", (folio,))
for r in cur.fetchall():
    print(r)

con.close()