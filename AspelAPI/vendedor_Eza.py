from sae_remision import conectar_empresa, EMPRESAS

con = conectar_empresa("EZA2007")
cur = con.cursor()

cur.execute("SELECT CVE_VEND, NOMBRE FROM VEND03")
for row in cur.fetchall():
    print(row)
        
con.close()