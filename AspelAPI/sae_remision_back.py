from decimal import Decimal
import fdb
from datetime import datetime
import random

# ========= LOG ==========
LOG_PATH = r"C:\AspelAPI\sae.log"
def write_log(txt):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] {txt}\n")
    except:
        pass

# ========= EMPRESAS ==========
EMPRESAS = {
    "ALDEU": "01",
    "IBERSUR": "02",
    "EZA2007": "03",
    "GOURMET": "04"
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

# ===== Genera folio real =====
def generar_folio_real():
    base = datetime.now().strftime("R%y%m%d%H%M%S")
    sec = str(random.randint(0,999)).zfill(3)
    return (base + sec)[:20]

# ===== CONEXIÓN =====
def conectar_empresa(empresa):
    num = EMPRESAS[empresa.upper()]
    return fdb.connect(
        dsn=RUTAS_FDB[num], user="SYSDBA", password="masterkey", charset="WIN1252"
    )

# ===================================================================
#               🚀 CREAR REMISIÓN SAE — 100% COMPATIBLE
# ===================================================================
def crear_remision_sae(datos):

    try:
        empresa = datos["empresa"].upper()
        num = EMPRESAS[empresa]

        con = conectar_empresa(empresa)
        cur = con.cursor()
        curp = con.cursor()

        folio = generar_folio_real()
        fecha = datetime.now()

        # Clave de cliente con espacios a la IZQUIERDA, igual que en CLIE03
        cliente = str(datos["cliente"]).rjust(10)
        vendedor = None
        # === DESCUENTO QUE VIENE DESDE LA API (ÚNICO QUE USAREMOS) ===
        descuento_final = float(datos.get("descuento", 0))   # % de descuento

        subtotal = sum(Decimal(str(p["precio"])) * Decimal(str(p["cantidad"]))
                       for p in datos["productos"])

        # === APLICAR DESCUENTO GENERAL ===
        descuento_monto = float(subtotal) * (descuento_final / 100)
        total = float(subtotal) - descuento_monto
        can_tot = sum(Decimal(str(p["cantidad"])) for p in datos["productos"])

        # ===================== ENCABEZADO =====================
        tabla_c = f"FACTR{num}"

        sql_enc = f"""
            INSERT INTO {tabla_c} (
                TIP_DOC, CVE_DOC, CVE_CLPV, STATUS,
                DAT_MOSTR, CVE_VEND,
                FECHA_DOC, FECHA_ENT, FECHA_VEN,
                CAN_TOT,
                IMP_TOT1, IMP_TOT4,
                IMPORTE,
                TIP_DOC_E,
                FECHAELAB
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """

        cur.execute(sql_enc, (
            "R", folio, cliente, "E",
            0, None,            # CVE_VEND = None si no quieres usar vendedores
            fecha, fecha, fecha,
            float(can_tot),
            float(subtotal), 0.0,
            float(total),
            "O",                # TIP_DOC_E como en la remisión buena
            fecha
        ))

        # 🔧 COMPLETAR CAMPOS QUE SAE ESPERA (EVITAR NULLS)
        cur.execute(f"""
            UPDATE {tabla_c}
            SET
                IMP_TOT2       = COALESCE(IMP_TOT2, 0),
                IMP_TOT3       = COALESCE(IMP_TOT3, 0),
                IMP_TOT5       = COALESCE(IMP_TOT5, 0),
                IMP_TOT6       = COALESCE(IMP_TOT6, 0),
                IMP_TOT7       = COALESCE(IMP_TOT7, 0),
                IMP_TOT8       = COALESCE(IMP_TOT8, 0),
                DES_TOT        = COALESCE(DES_TOT, 0),
                DES_FIN        = COALESCE(DES_FIN, 0),
                COM_TOT        = COALESCE(COM_TOT, 0),
                DES_TOT_PORC   = COALESCE(DES_TOT_PORC, 0),
                DES_FIN_PORC   = COALESCE(DES_FIN_PORC, 0),
                COM_TOT_PORC   = COALESCE(COM_TOT_PORC, 0),
                CVE_OBS        = COALESCE(CVE_OBS, 0),
                NUM_ALMA       = COALESCE(NUM_ALMA, 1),
                ACT_CXC        = COALESCE(ACT_CXC, 'S'),
                ACT_COI        = COALESCE(ACT_COI, 'N'),
                ENLAZADO       = COALESCE(ENLAZADO, 'O'),
                NUM_MONED      = COALESCE(NUM_MONED, 1),
                TIPCAMB        = COALESCE(TIPCAMB, 1.0),
                NUM_PAGOS      = COALESCE(NUM_PAGOS, 1),
                PRIMERPAGO     = COALESCE(PRIMERPAGO, 0),
                CONTADO        = COALESCE(CONTADO, 'N'),
                BLOQ           = COALESCE(BLOQ, 'N'),
                METODODEPAGO   = COALESCE(METODODEPAGO, 99),
                FORMADEPAGOSAT = COALESCE(FORMADEPAGOSAT, 99),
                USO_CFDI       = COALESCE(USO_CFDI, 'G01'),
                TIP_FAC        = COALESCE(TIP_FAC, 'R'),
                REG_FISC       = COALESCE(REG_FISC, 626),

                CTLPOL         = COALESCE(CTLPOL, 0),
                ESCFD          = COALESCE(ESCFD, 'N'),
                AUTORIZA       = COALESCE(AUTORIZA, 1),
                SERIE          = COALESCE(SERIE, ''),
                FOLIO          = COALESCE(FOLIO, 0),
                DAT_ENVIO      = COALESCE(DAT_ENVIO, 0),
                CVE_BITA       = COALESCE(CVE_BITA, 0),
                NUMCTAPAGO     = COALESCE(NUMCTAPAGO, ''),

                VERSION_SINC   = COALESCE(VERSION_SINC, ?)
            WHERE CVE_DOC = ?
        """, (fecha, folio))

        # ===================== DETALLE (63 columnas) =====================
        tabla_d = f"PAR_FACTR{num}"

        sql_det = f"""
        INSERT INTO {tabla_d} (
            CVE_DOC, NUM_PAR, CVE_ART,
            CANT, PXS, PREC, COST,
            IMPU1, IMPU2, IMPU3, IMPU4,
            IMPU5, IMPU6, IMPU7, IMPU8,
            IMP1APLA, IMP2APLA, IMP3APLA, IMP4APLA,
            IMP5APLA, IMP6APLA, IMP7APLA, IMP8APLA,
            TOTIMP1, TOTIMP2, TOTIMP3, TOTIMP4,
            TOTIMP5, TOTIMP6, TOTIMP7, TOTIMP8,
            DESC1, DESC2, DESC3,
            COMI, APAR,
            ACT_INV, NUM_ALM, POLIT_APLI,
            TIP_CAM, UNI_VENTA, TIPO_PROD,
            CVE_OBS, REG_SERIE, E_LTPD,
            TIPO_ELEM, NUM_MOV,
            TOT_PARTIDA,
            IMPRIMIR, MAN_IEPS, APL_MAN_IMP,
            CUOTA_IEPS, APL_MAN_IEPS,
            MTO_PORC, MTO_CUOTA,
            CVE_ESQ,
            DESCR_ART,
            UUID,
            VERSION_SINC,
            PREC_NETO,
            ID_RELACION,
            CVE_PRODSERV,
            CVE_UNIDAD
        )
        VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """

        num_par = 1

        for p in datos["productos"]:

            cip = safe_str(p["cip"], 16)
            cantidad = Decimal(str(p["cantidad"]))
            precio = Decimal(str(p["precio"]))
            total_p = float(cantidad * precio)
            piezas = p.get("piezas", 1)

            impu1 = 16.0 if p.get("iva", False) else 0.0
            totimp1 = total_p * (impu1 / 100)

            descr = ""
            uni = "PZA"
            tpo = "P"   # tipo de producto/elemento
            sat_ps = ""
            sat_un = ""
            cve_esq = 3  # mismo esquema de impuestos que la remisión manual

            # Consultar INVE
            curp.execute(
                f"SELECT DESCR, UNI_MED, TIPO_ELE, CVE_PRODSERV, CVE_UNIDAD "
                f"FROM INVE{num} WHERE CVE_ART=?", (cip,)
            )
            row = curp.fetchone()

            if row:
                if row[0]: descr = safe_str(row[0], 40)
                if row[1]: uni = safe_str(row[1], 10)
                if row[2]: tpo = safe_str(row[2], 1)
                if row[3]: sat_ps = safe_str(row[3], 9)
                if row[4]: sat_un = safe_str(row[4], 4)

            # SAT obligatorios
            if not sat_ps:
                sat_ps = "01010101"
            if not sat_un:
                sat_un = "H87"

            # ============= VALORES EXACTOS (63 valores) =============
            valores = (
                folio, num_par, cip,
                float(cantidad), int(piezas), float(precio), 0,

                impu1, 0, 0, 0, 0, 0, 0, 0,
                0, 0, 0, 0, 0, 0, 0, 0,

                totimp1, 0, 0, 0, 0, 0, 0, 0,

                0, 0, 0,
                0, 0,

                "N",      # ACT_INV  ⬅️ IGUAL QUE LA REMISIÓN MANUAL
                1,        # NUM_ALM
                "",       # POLIT_APLI

                1.0,      # TIP_CAM
                uni,      # UNI_VENTA
                tpo,      # TIPO_PROD
                0,        # CVE_OBS
                0,        # REG_SERIE
                0,        # E_LTPD
                "N",      # TIPO_ELEM ⬅️ IGUAL QUE LA REMISIÓN MANUAL
                0,        # NUM_MOV

                float(total_p),

                "S",
                "N",
                1,
                0,
                "C",
                0,
                0,

                3,

                descr,
                "",
                fecha.strftime("%Y-%m-%d %H:%M:%S"),
                float(precio),
                None,
                sat_ps,
                sat_un
            )

            cur.execute(sql_det, valores)
            num_par += 1

        con.commit()
        con.close()

        return {
            "estatus": "ok",
            "folio_sae": folio,
            "subtotal": float(subtotal),
            "iva": 0.0,
            "total": float(total)
        }

    except Exception as e:
        write_log(f"ERROR crear_remision_sae: {e}")
        return {"estatus": "error", "detalle": str(e)}