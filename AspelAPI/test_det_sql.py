import fdb
import re

DB = r"192.168.1.105:C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa03\Datos\SAE90EMPRE03.FDB"

con = fdb.connect(dsn=DB, user="SYSDBA", password="masterkey", charset="WIN1252")
cur = con.cursor()

sql = """
INSERT INTO PAR_FACTR03 (
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

# Contar placeholders
placeholders = len(re.findall(r"\?", sql))

print("Placeholders encontrados:", placeholders)

# Generar dummy parameters
dummy_params = [None] * placeholders

try:
    cur.execute(sql, dummy_params)
    print("\n✔ SQL DETALLE VÁLIDO (solo fallarán PK/constraints)")
except Exception as e:
    print("\n❌ ERROR EN SQL DETALLE")
    print(str(e))

con.close()