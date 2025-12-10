from sae_remision import conectar_empresa, EMPRESAS

def ver_datos_cliente(empresa, clave_cliente):
    emp = empresa.upper()
    num = EMPRESAS[emp]

    con = conectar_empresa(emp)
    cur = con.cursor()

    sql = f"""
        SELECT CLAVE, NOMBRE, RFC, CVE_VEND, USO_CFDI,
               METODODEPAGO, FORMADEPAGOSAT, REG_FISC
        FROM CLIE{num}
        WHERE CLAVE = ?
    """

    cur.execute(sql, (clave_cliente.rjust(10),))
    row = cur.fetchone()

    con.close()

    if row:
        print("=== DATOS CLIENTE SAE ===")
        print(f"CLAVE:   [{row[0].strip()}]")
        print(f"NOMBRE:  {row[1]}")
        print(f"RFC:     {row[2]}")
        print(f"CVE_VEND:{row[3]}")
        print(f"USO_CFDI:{row[4]}")
        print(f"METODODEPAGO:{row[5]}")
        print(f"FORMADEPAGOSAT:{row[6]}")
        print(f"REG_FISC:{row[7]}")
    else:
        print("Cliente NO encontrado en SAE")


# Ejecutar prueba:
ver_datos_cliente("EZA2007", "100263")