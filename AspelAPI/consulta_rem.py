import fdb
from datetime import datetime

# 🖥️ Configura solo si cambia tu servidor
SAE_HOST = "192.168.1.105"
SAE_PATH = r"C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa{num}\Datos\SAE90EMPRE{num}.FDB"

EMPRESAS = {
    "01": "GOURMET",
    "02": "IBERSUR",
    "03": "EZA2007",
    "04": "ALDEU"
}

def conectar(num):
    return fdb.connect(
        dsn=f"{SAE_HOST}:{SAE_PATH.format(num=num)}",
        user="SYSDBA",
        password="masterkey",
        charset="WIN1252"
    )

def consulta_remision():
    folio = input("📌 Ingresa folio remisión: ").strip().upper()

    for num, nombre in EMPRESAS.items():
        print(f"\n==============================")
        print(f"🔍 BUSCANDO EN EMPRESA {num} → {nombre}")
        print("==============================")

        try:
            con = conectar(num)
            cur = con.cursor()

            # === Encabezado ===
            cur.execute(f"""
                SELECT 
                    CVE_DOC, STATUS, TIP_DOC_E, ENLAZADO,
                    CVE_VEND, CVE_CLPV, NUM_ALMA,
                    IMP_TOT1, IMP_TOT4, IMPORTE,
                    TIPCAMB, FORMADEPAGOSAT, USO_CFDI
                FROM FACTR{num} 
                WHERE CVE_DOC = ?
            """, (folio,))
            enc = cur.fetchone()

            if not enc:
                print("❌ No existe encabezado")
                continue

            print("\n📌 ENCABEZADO:")
            print(enc)

            # === Detalle ===
            cur.execute(f"""
                SELECT 
                    NUM_PAR, CVE_ART, CANT, PREC,
                    TOT_PARTIDA, IMPU1, TOTIMP1,
                    NUM_ALM, CVE_ESQ
                FROM PAR_FACTR{num}
                WHERE CVE_DOC = ?
                ORDER BY NUM_PAR
            """, (folio,))
            det = cur.fetchall()

            if not det:
                print("⚠️ Sin partidas (causa CRASH en SAE)")
            else:
                print("\n📌 DETALLE:")
                for row in det:
                    print(row)

            # === VALIDACIONES CRÍTICAS ===
            print("\n📌 VALIDACIONES:")
            if enc[6] is None:
                print("❌ NUM_ALMA vacío → CRASH al abrir en SAE")
            else:
                print("✔ NUM_ALMA correcto:", enc[6])

            if enc[1] not in ["E", "O", "N", "C"]:
                print("⚠️ STATUS raro:", enc[1])

            if len(det) > 0:
                if det[0][7] is None:
                    print("❌ NUM_ALM en partidas vacío")
                if det[0][8] is None:
                    print("⚠️ CVE_ESQ falta (puede dar errores de impuestos)")

            con.close()
            return  # si la encontró, ya no buscamos más

        except Exception as e:
            print("⚠️ Error:", e)

    print("\n❌ No encontrada en ninguna empresa")

if __name__ == "__main__":
    consulta_remision()