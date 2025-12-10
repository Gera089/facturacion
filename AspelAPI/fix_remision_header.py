from sae_remision import conectar_empresa, EMPRESAS

def ver_encabezado(folio, empresa):
    empresa = empresa.upper()
    num = EMPRESAS[empresa]

    con = conectar_empresa(empresa)
    cur = con.cursor()

    cur.execute(f"""
        SELECT
            CVE_DOC,
            STATUS,
            ENLAZADO,
            TIP_DOC_E,
            ACT_CXC,
            ACT_COI,
            CONTADO,
            BLOQ,
            CVE_VEND,
            SERIE,
            FOLIO,
            CAN_TOT,
            IMP_TOT1,
            IMP_TOT4,
            IMPORTE
        FROM FACTR{num}
        WHERE CVE_DOC = ?
    """, (folio,))
    row = cur.fetchone()
    con.close()
    return row

def fix_remision(folio, empresa):
    empresa = empresa.upper()
    num = EMPRESAS[empresa]

    print(f"\n🔌 Conectando a empresa {empresa} ({num})...")

    print("\n=== ANTES DEL FIX ===")
    antes = ver_encabezado(folio, empresa)
    print(antes)

    con = conectar_empresa(empresa)
    cur = con.cursor()

    print("\n🔧 Aplicando UPDATE...")
    cur.execute(f"""
        UPDATE FACTR{num}
        SET
            ACT_COI = 'S',
            CONTADO = 'N',
            BLOQ = 'N',
            CVE_VEND = '00000',
            SERIE = '',
            FOLIO = 0
        WHERE CVE_DOC = ?
    """, (folio,))

    con.commit()
    con.close()

    print("\n=== DESPUÉS DEL FIX ===")
    despues = ver_encabezado(folio, empresa)
    print(despues)

if __name__ == "__main__":
    # ⚠️ Usa el folio problemático
    fix_remision("R251210100147", "EZA2007")