import fdb

con = fdb.connect(
    dsn="192.168.1.105:C:\\Program Files (x86)\\Common Files\\Aspel\\Sistemas Aspel\\SAE9.00\\Empresa03\\Datos\\SAE90EMPRE03.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

cur = con.cursor()

folio = input("Folio SAE: ").strip()

print("\n=== FACTC03 ===")
cur.execute("SELECT * FROM FACTC03 WHERE CVE_DOC = ?", (folio,))
print(cur.fetchone())

print("\n=== FACTR03 ===")
cur.execute("SELECT * FROM FACTR03 WHERE CVE_DOC = ?", (folio,))
print(cur.fetchone())

print("\n=== PAR_FACTD03 ===")
cur.execute("SELECT CVE_DOC, NUM_PAR, CVE_ART, PREC FROM PAR_FACTD03 WHERE CVE_DOC = ?", (folio,))
for row in cur.fetchall():
    print(row)

con.close()