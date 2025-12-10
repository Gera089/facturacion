import fdb

SERVER = "192.168.1.105"
DB = r"C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa03\Datos\SAE90EMPRE03.FDB"

con = fdb.connect(
    dsn=f"{SERVER}:{DB}",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

cur = con.cursor()

cur.execute("SELECT FIRST 1 * FROM FACTR03 ORDER BY FECHAELAB DESC")
row = cur.fetchone()

cols = [c[0] for c in cur.description]

print("\n=== ENCABEZADO REAL ===\n")
for c, v in zip(cols, row):
    print(f"{c:20} = {v}")

con.close()