from datetime import datetime
from sae_remision import conectar_empresa, EMPRESAS

def crear_remision_sae(datos: dict):
    try:
        empresa = datos["empresa"].upper()
        num = EMPRESAS[empresa]

        con = conectar_empresa(empresa)
        cur = con.cursor()
        con.begin()

        fecha = datetime.now()
        folio_doc = f"R{fecha.strftime('%y%m%d%H%M%S')}"
        cliente = str(datos["cliente"]).rjust(10)

        subtotal = 0.0
        total_iva = 0.0

        # === DETALLE ===
        cur_det = con.cursor()
        for idx, item in enumerate(datos["productos"], start=1):
            cip = str(item["cip"])
            cantidad = float(item["cantidad"])
            precio = float(item["precio"])
            iva = cantidad * precio * 0.16

            subtotal += cantidad * precio
            total_iva += iva

            # Insertar detalle
            cur_det.execute(f"""
                INSERT INTO PAR_FACTR{num} (
                    CVE_DOC, NUM_PAR, CVE_ART,
                    CANT, PXS, PREC, COST,
                    IMPU1, IMP1APLA, TOTIMP1,
                    APAR, ACT_INV, NUM_ALM,
                    TIP_CAM, CVE_UNIDAD,
                    TOT_PARTIDA
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                folio_doc, idx, cip,
                cantidad, 1, precio, 0,
                1, 1, iva,
                0, 'S', 1,
                1, "H87",
                (cantidad * precio) + iva
            ))

            # Insert MINVE03
            cur_det.execute(f"""
                INSERT INTO MINVE{num} (
                    CVE_ART, ALMACEN,
                    NUM_MOV, CVE_CPTO,
                    FECHA_DOCU, TIPO_DOC,
                    REFER, CLAVE_CLPV,
                    VEND, CANT, CANT_COST,
                    PRECIO, COSTO,
                    AFEC_COI,
                    SIGNO, COSTEADO,
                    FECHAELAB
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cip, 1,
                idx, 51,
                fecha.date(), 'R',
                folio_doc, cliente,
                '00000', cantidad, 0,
                precio, 0,
                'S',
                -1, 'N',
                fecha
            ))

        total = subtotal + total_iva

        # === ENCABEZADO ===
        cur.execute(f"""
            INSERT INTO FACTR{num} (
                TIP_DOC, CVE_DOC, STATUS,
                FECHA_DOC, FECHAELAB, FECHA_ENT, FECHA_VEN,
                CVE_CLPV, CAN_TOT,
                IMP_TOT1, IMP_TOT4, IMPORTE,
                DES_TOT,
                ENLAZADO, TIP_DOC_E,
                NUM_ALMA, ACT_CXC, ACT_COI,
                CONTADO, BLOQ,
                CVE_VEND, REG_FISC,
                SERIE, FOLIO,
                NUM_MONED, TIPCAMB
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            'R', folio_doc, 'N',
            fecha, fecha, fecha, fecha,
            cliente, subtotal,
            0, total_iva, total,
            0,
            'O', 'O',
            1, 'S', 'S',
            'N', 'N',
            '00000', '626',
            '', 0,
            1, 1.0
        ))

        con.commit()
        con.close()

        return {
            "estatus": "ok",
            "folio": folio_doc,
            "subtotal_sin_iva": round(subtotal, 2),
            "iva": round(total_iva, 2),
            "total": round(total, 2)
        }

    except Exception as e:
        con.rollback()
        return {"estatus": "error", "detalle": str(e)}