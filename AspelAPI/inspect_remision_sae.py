import fdb

# ⚙️ CONEXIÓN EMPRESA 03 (EZA2007)
con = fdb.connect(
    dsn="192.168.1.105:C:\\Program Files (x86)\\Common Files\\Aspel\\Sistemas Aspel\\SAE9.00\\Empresa03\\Datos\\SAE90EMPRE03.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

cur = con.cursor()

# ===============================
# Solicitar folio
# ===============================
folio = input("Folio (CVE_DOC) de la remisión SAE: ")

# Si el usuario ingresa un número (ej. 0000000001), rellenar a 20 chars
if folio.strip().isdigit():
    folio = folio.strip().rjust(20)

print("\n======= ENCABEZADO FACTR03 =======")

cur.execute("SELECT * FROM FACTR03 WHERE CVE_DOC = ?", (folio,))
row = cur.fetchone()

if not row:
    print("⚠ No se encontró ese folio en FACTR03")
else:
    colnames = [d[0].strip() for d in cur.description]
    for i, (name, value) in enumerate(zip(colnames, row), start=1):
        print(f"{i:02d} {name} = {value!r}")

print("\n======= DETALLE PAR_FACTR03 =======")

cur.execute("SELECT * FROM PAR_FACTR03 WHERE CVE_DOC = ? ORDER BY NUM_PAR", (folio,))
rows = cur.fetchall()

if not rows:
    print("⚠ No hay detalle para ese folio en PAR_FACTR03")
else:
    colnames = [d[0].strip() for d in cur.description]
    for r in rows:
        print("\n--- PARTIDA ---")
        for i, (name, value) in enumerate(zip(colnames, r), start=1):
            print(f"{i:02d} {name} = {value!r}")

con.close()
print("\n✅ Listo.")