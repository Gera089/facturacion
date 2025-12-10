import fdb

con = fdb.connect(
    dsn=r"192.168.1.105:C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa03\Datos\SAE90EMPRE03.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

cur = con.cursor()
cur.execute("SELECT CVE_DOC, NUM_PAR, CVE_ART, CANT, PREC, TOT_PARTIDA FROM PAR_FACTD03 WHERE CVE_DOC='0000CFDI07'")

for r in cur.fetchall():
    print(r)

con.close()