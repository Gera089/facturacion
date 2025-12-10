def consultar_producto_sae(empresa, cve_art):
    try:
        emp = empresa.upper()
        num = EMPRESAS[emp]

        con = conectar_empresa(emp)
        cur = con.cursor()

        # Consulta del producto
        sql = f"""
        SELECT 
            CVE_ART,
            DESCR,
            UNI_MED,
            TIPO_ELE,
            CVE_PRODSERV,
            CVE_UNIDAD,
            CVE_ESQIMPU
        FROM INVE{num}
        WHERE CVE_ART = ?
        """

        cur.execute(sql, (cve_art,))
        row = cur.fetchone()

        if not row:
            con.close()
            return {"estatus": "no_encontrado", "detalle": f"No existe {cve_art} en {empresa}"}

        # Consultar existencia del almacén principal
        sql_ex = f"""
        SELECT EXIST
        FROM MULT{num}
        WHERE CVE_ART = ? AND CVE_ALM = 1
        """
        cur.execute(sql_ex, (cve_art,))
        row_ex = cur.fetchone()
        existencia = float(row_ex[0]) if row_ex else 0.0

        con.close()

        return {
            "estatus": "ok",
            "empresa": empresa,
            "cve_art": row[0].strip(),
            "descripcion": safe_str(row[1], 40),
            "unidad": row[2],
            "tipo": row[3],
            "sat_prodserv": row[4],
            "sat_unidad": row[5],
            "esquema_impuestos": row[6],
            "existencia": existencia
        }

    except Exception as e:
        write_log(f"ERROR consultar_producto_sae: {e}")
        return {"estatus": "error", "detalle": str(e)}