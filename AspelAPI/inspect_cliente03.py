import fdb

con = fdb.connect(
    dsn="192.168.1.105:C:\\Program Files (x86)\\Common Files\\Aspel\\Sistemas Aspel\\SAE9.00\\Empresa03\\Datos\\SAE90EMPRE03.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

cur = con.cursor()

cve = "900021"   # <-- AQUÍ PONES LA CLAVE QUE VES EN CVE_CLPV

cur.execute("SELECT CVE_CLPV, NOMBRE, RFC FROM CLIE03 WHERE CVE_CLPV = ?", (cve,))
row = cur.fetchone()

if row:
    print("✅ Cliente encontrado en CLIE03:")
    print("CVE_CLPV:", row[0])
    print("NOMBRE  :", row[1])
    print("RFC     :", row[2])
else:
    print("❌ Cliente NO existe en CLIE03 con esa clave:", cve)

con.close()