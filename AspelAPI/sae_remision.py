from decimal import Decimal
from datetime import datetime
import fdb
import random

# ========= EMPRESAS Y RUTAS =========
EMPRESAS = {
    "ALDEU": "01",
    "IBERSUR": "02",
    "EZA2007": "03",
    "GOURMET": "04",
}

RUTAS_FDB = {
    "01": r"192.168.1.105:C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa01\Datos\SAE90EMPRE01.FDB",
    "02": r"192.168.1.105:C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa02\Datos\SAE90EMPRE02.FDB",
    "03": r"192.168.1.105:C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa03\Datos\SAE90EMPRE03.FDB",
    "04": r"192.168.1.105:C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa04\Datos\SAE90EMPRE04.FDB",
}

def safe_str(v, l=20):
    if v is None:
        return ""
    return str(v).strip()[:l]

def generar_folio_real():
    base = datetime.now().strftime("R%y%m%d%H%M%S")
    sec = str(random.randint(0, 999)).zfill(3)
    return (base + sec)[:20]

def conectar_empresa(empresa: str):
    num = EMPRESAS[empresa.upper()]
    return fdb.connect(
        dsn=RUTAS_FDB[num],
        user="sysdba",
        password="masterkey",
        charset="WIN1252",
    )

LOG_PATH = r"C:\AspelAPI\sae.log"
def write_log(txt):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] {txt}\n")
    except Exception:
        pass

# ===================================================================
#        💾 CREAR REMISIÓN SAE LIMPIA (EDITABLE Y FACTURABLE)
# ===================================================================
def crear_remision_sae(datos: dict):
    """
    Crea una remisión (FACTRxx / PAR_FACTRxx) visible en SAE.
    
    datos = {
        "empresa": "EZA2007",
        "cliente": "100475",
        "productos": [
            {"cip": "150", "cantidad": 4.7, "precio": 1270.59},
            ...
        ]
    }
    """

    from datetime import datetime
    import fdb

    try:
        empresa = datos["empresa"].upper()
        num = EMPRESAS[empresa]

        con = conectar_empresa(empresa)
        cur = con.cursor()
        curp = con.cursor()
        con.begin()

        fecha = datetime.now()
        folio = f"R{fecha.strftime('%y%m%d%H%M%S')}"
        cliente = str(datos["cliente"]).rjust(10)

        # === Obtener descuento del cliente ===
        cur.execute(f"SELECT DESCUENTO FROM CLIE{num} WHERE CLAVE=?", (cliente,))
        row_desc = cur.fetchone()
        desc_cliente = float(row_desc[0]) if row_desc and row_desc[0] else 0.0

        subtotal = 0.0
        total_iva = 0.0

        # ===================== DETALLE =====================
        tabla_d = f"PAR_FACTR{num}"
        num_par = 1

        for item in datos["productos"]:
            cip = str(item["cip"]).strip()
            cantidad = float(item["cantidad"])
            precio = float(item["precio"])

            # Consulta impuestos por producto
            curp.execute(f"SELECT CVE_ESQIMPU FROM INVE{num} WHERE CVE_ART=?", (cip,))
            row_imp = curp.fetchone()
            esquema = row_imp[0] if row_imp else 1

            # Identificar si lleva IVA: esquema 1 = IVA 16%
            iva_flag = 16 if esquema == 1 else 0
            
            importe_base = cantidad * precio
            subtotal += importe_base
            iva_calc = (importe_base * iva_flag / 100)
            total_iva += iva_calc

            cur.execute(f"""
                INSERT INTO {tabla_d} (
                    CVE_DOC, NUM_PAR, CVE_ART, CANT, PREC,
                    IMPU1, IMP1APLA, TOTIMP1, DESC1,
                    ACT_INV, NUM_ALM, TIP_CAM
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                folio, num_par, cip, cantidad, precio,
                iva_flag, 1 if iva_flag > 0 else 0,
                iva_calc, desc_cliente,
                'S', 1, 1
            ))

            num_par += 1

        total = subtotal + total_iva

        # ===================== ENCABEZADO =====================
        tabla_c = f"FACTR{num}"

        cur.execute(f"""
            INSERT INTO {tabla_c} (
                TIP_DOC, CVE_DOC, STATUS, FECHA_DOC,
                CVE_CLPV, CAN_TOT, IMP_TOT1, IMPORTE,
                DES_TOT, ENLAZADO, TIP_DOC_E, ACT_CXC
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            'R', folio, 'N', fecha,
            cliente, subtotal, total_iva, total,
            desc_cliente, 'O', 'O', 'S'
        ))

        con.commit()
        con.close()

        return {
            "estatus": "ok",
            "folio": folio,
            "subtotal_sin_iva": round(subtotal, 2),
            "iva": round(total_iva, 2),
            "total": round(total, 2)
        }

    except Exception as e:
        con.rollback()
        return {"estatus": "error", "detalle": str(e)}