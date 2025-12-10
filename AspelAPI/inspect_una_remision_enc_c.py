# buscar_documento_global.py
import fdb

con = fdb.connect(
    dsn=r"192.168.1.105:C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa03\Datos\SAE90EMPRE03.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

cur = con.cursor()

folio = "0000000001"
tablas = [
    "FACTC03","FACTP03","FACTR03","FACTF03","FACTN03","FACTM03","FACTB03","FACTV03"
]

for t in tablas:
    try:
        cur.execute(f"SELECT TIP_DOC, CVE_DOC FROM {t} WHERE CVE_DOC=?", (folio,))
        row = cur.fetchone()
        if row:
            print(f"ENCONTRADO en {t}: {row}")
    except Exception as e:
        print(f"Error en {t}: {e}")

con.close()