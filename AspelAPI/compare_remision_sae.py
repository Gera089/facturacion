import fdb
from sae_remision import conectar_empresa, EMPRESAS

# 👇 Cambia aquí los folios que vas a comparar:
EMPRESA = "EZA2007"
FOLIO_BUENO = "          0000000001"       # <- remisión manual
FOLIO_MALO  = "R251205112859208"

num = EMPRESAS[EMPRESA]
tabla_c = f"FACTR{num}"
tabla_d = f"PAR_FACTR{num}"

con = conectar_empresa(EMPRESA)
cur = con.cursor()

def obtener_encabezado(folio):
    cur.execute(f"SELECT * FROM {tabla_c} WHERE CVE_DOC = ?", (folio,))
    desc = [d[0].strip() for d in cur.description]
    vals = cur.fetchone()
    return dict(zip(desc, vals)) if vals else {}

def obtener_detalle(folio):
    cur.execute(f"SELECT * FROM {tabla_d} WHERE CVE_DOC = ?", (folio,))
    desc = [d[0].strip() for d in cur.description]
    rows = [dict(zip(desc, r)) for r in cur.fetchall()]
    return rows

# === Lee ambas remisiones ===
enc_bueno = obtener_encabezado(FOLIO_BUENO)
enc_malo = obtener_encabezado(FOLIO_MALO)

det_bueno = obtener_detalle(FOLIO_BUENO)
det_malo  = obtener_detalle(FOLIO_MALO)

print("\n\n============== COMPARANDO ENCABEZADO ==============")
for k in enc_bueno.keys():
    v1 = enc_bueno.get(k)
    v2 = enc_malo.get(k)
    if v1 != v2:
        print(f"⚠️ {k}: BUENO={v1} | MALO={v2}")

print("\n\n============== COMPARANDO DETALLE ==============")
max_partes = max(len(det_bueno), len(det_malo))
for i in range(max_partes):
    rb = det_bueno[i] if i < len(det_bueno) else {}
    rm = det_malo[i] if i < len(det_malo) else {}
    print(f"\n--- Partida {i+1} ---")
    for k in rb.keys():
        v1 = rb.get(k)
        v2 = rm.get(k)
        if v1 != v2:
            print(f"➡️ {k}: BUENO={v1} | MALO={v2}")

con.close()
print("\n>>> Comparación completada.")