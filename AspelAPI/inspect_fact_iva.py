import fdb

folio = "R251204094529650"  # ⬅️ PON AQUÍ EL FOLIO QUE TE DIO LA API
empresa = "03"  # EJEMPLO EZA2007

con = fdb.connect(
    dsn=f"192.168.1.105:C:\\Program Files (x86)\\Common Files\\Aspel\\Sistemas Aspel\\SAE9.00\\Empresa{empresa}\\Datos\\SAE90EMPRE{empresa}.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

cur = con.cursor()
cur.execute(f"""
    SELECT 
        CVE_ART,
        IMPU1, IMP1APLA,
        TOTIMP1,
        PREC, PREC_NETO,
        TOT_PARTIDA
    FROM PAR_FACTR{empresa}
    WHERE CVE_DOC = ?
""", (folio,))

rows = cur.fetchall()
for r in rows:
    print(r)

con.close()