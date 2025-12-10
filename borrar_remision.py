import fdb

REMISION = "C251128162609265"

con = fdb.connect(
    dsn=r"192.168.1.105:C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa03\Datos\SAE90EMPRE03.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

cur = con.cursor()

# Borrar detalle
cur.execute("DELETE FROM PAR_FACTD03 WHERE CVE_DOC = ?", (REMISION,))
print(f"Detalle borrado de: {REMISION}")

# Borrar encabezado
cur.execute("DELETE FROM FACTC03 WHERE CVE_DOC = ?", (REMISION,))
print(f"Encabezado borrado de: {REMISION}")

con.commit()
con.close()

print("✔ Listo!")