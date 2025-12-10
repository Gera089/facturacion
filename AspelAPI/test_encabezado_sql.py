import fdb
import re

DB = r"192.168.1.105:C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa03\Datos\SAE90EMPRE03.FDB"
con = fdb.connect(dsn=DB, user="SYSDBA", password="masterkey", charset="WIN1252")
cur = con.cursor()

sql = """
UPDATE FACTR03
SET
    NUM_ALMA = COALESCE(NUM_ALMA, 1),
    TIPCAMB  = COALESCE(TIPCAMB, 1.0),
    USO_CFDI = COALESCE(USO_CFDI, 'G01'),
    TIP_FAC  = COALESCE(TIP_FAC, 'R'),
    VERSION_SINC = COALESCE(VERSION_SINC, ?)
WHERE CVE_DOC = ?
"""

print("Placeholders:", sql.count("?"))

try:
    cur.execute(sql, ["2024-01-01", "ABC"])
    print("\n✔ UPDATE ENCABEZADO VÁLIDO")
except Exception as e:
    print("\n❌ ERROR EN UPDATE ENCABEZADO")
    print(str(e))

con.close()