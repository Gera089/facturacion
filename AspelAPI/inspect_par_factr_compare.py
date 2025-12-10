import fdb

con = fdb.connect(
    dsn="192.168.1.105:C:\\Program Files (x86)\\Common Files\\Aspel\\Sistemas Aspel\\SAE9.00\\Empresa03\\Datos\\SAE90EMPRE03.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

cur_cols = con.cursor()
cur = con.cursor()

# Obtener nombres de columnas en orden real
cur_cols.execute("""
    SELECT RDB$FIELD_NAME
    FROM RDB$RELATION_FIELDS
    WHERE RDB$RELATION_NAME = 'PAR_FACTR03'
    ORDER BY RDB$FIELD_POSITION
""")
columnas = [c[0].strip() for c in cur_cols.fetchall()]


def ver_partidas(folio):
    print("\n==================== PAR_FACTR03 ::", folio, "====================\n")
    cur.execute("SELECT * FROM PAR_FACTR03 WHERE CVE_DOC = ? ORDER BY NUM_PAR", (folio,))
    rows = cur.fetchall()
    if not rows:
        print("NO HAY PARTIDAS")
        return

    for i, row in enumerate(rows, start=1):
        print(f"--- Partida {i} ---")
        for nombre, valor in zip(columnas, row):
            print(f"{nombre:12} = {valor}")
        print()


# 👇 CAMBIA ESTOS DOS FOLIOS
folio_manual = "          0000000001"   # uno hecho A MANO en SAE que sí muestre partidas
folio_api    = "R251203134710853"   # el que hizo la API y NO muestra partidas (puede ser otro)

# Para probar bien, es mejor usar dos distintos:
# - uno manual que se vea bien
# - uno API que se vea vacío

ver_partidas(folio_manual)
ver_partidas(folio_api)

con.close()