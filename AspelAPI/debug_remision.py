from sae_remision import conectar_empresa, EMPRESAS

def borrar_cotizaciones_restantes(empresa="EZA2007"):
    empresa = empresa.upper()
    num = EMPRESAS[empresa]

    con = conectar_empresa(empresa)
    cur = con.cursor()
    con.begin()

    folios = [
        "R000004",
        "R000005",
        "R000006",
        "R251201120435051",
        "R2512011350013319",
        "R2512011359403957",
        "R251201143630416982",
    ]

    print(f"\n🧹 Eliminando cotizaciones restantes en FACTC{num}...\n")

    for folio in folios:
        print(f"➡ Borrando: {folio}")

        cur.execute(f"DELETE FROM PAR_FACTC{num} WHERE CVE_DOC = ?", (folio,))
        print(f"   PAR_FACTC{num}: {cur.rowcount} eliminados")

        cur.execute(f"DELETE FROM FACTC{num} WHERE CVE_DOC = ?", (folio,))
        print(f"   FACTC{num}: {cur.rowcount} eliminados")

    con.commit()
    con.close()
    print("\n✨ ¡Cotizaciones de prueba eliminadas con éxito! 💯\n")


if __name__ == "__main__":
    borrar_cotizaciones_restantes("EZA2007")