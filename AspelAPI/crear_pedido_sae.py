import fdb
from datetime import datetime
from decimal import Decimal
from sae_remision import conectar_empresa, EMPRESAS

def crear_pedido_sae(data: dict):
    try:
        empresa = data["empresa"].upper()
        num = EMPRESAS[empresa]

        con = conectar_empresa(empresa)
        cur = con.cursor()
        con.begin()

        fecha = datetime.now()
        f_fecha = fecha.strftime("%Y-%m-%d %H:%M:%S")

        # Folio según esquema del día
        folio_sae = f"P{fecha.strftime('%y%m%d%H%M%S')}"
        cliente = str(data["cliente"]).rjust(10)
        vendedor = str(data.get("vendedor", "") or "").rjust(5)

        subtotal = sum(float(p["precio"]) * float(p["cantidad"]) for p in data["productos"])
        total = float(subtotal)

        # 🔹 Tablas reales de pedidos SAE Tienda
        tabla_c = f"PED_TIEND{num}"
        tabla_d = f"PAR_PED_TIEND{num}"

        sql_c = f"""
        INSERT INTO {tabla_c} (
            TIPO_DOC,        -- P
            CVE_DOC,         -- Folio SAE
            CVE_CLIE,        -- Cliente SAE
            STATUS,          -- N
            CVE_VEND,        -- Vendedor
            F_ELAB,          -- Fecha elaboración
            F_VIGEN,         -- Fecha vigencia
            SUBTOT,          -- Subtotal
            IMPORTE,         -- Total
            TIPCAMB,         -- 1.0
            CVE_MONED,       -- Moneda
            CVE_TIENDA       -- Obligatorio!
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """

        cur.execute(sql_c, (
            "P",               # TIPO_DOC = Pedido
            folio_sae,         # CVE_DOC
            cliente,           # CVE_CLIE (relleno 10 chars)
            "N",               # STATUS
            vendedor,          # CVE_VEND
            fecha,             # F_ELAB
            fecha,             # F_VIGEN
            float(subtotal),   # SUBTOT
            float(total),      # IMPORTE
            1.0,               # TIPCAMB
            1,                 # CVE_MONED (MXN)
            "01"               # CVE_TIENDA = Requerido 🚀
        ))

        # ============== DETALLE ==============
        sql_d = f"""
        INSERT INTO {tabla_d} (
            CVE_DOC,
            NUM_PAR,
            CVE_ART,
            CANT,
            PREC,
            CVE_MONED,
            TOT_PARTIDA
        )
        VALUES (?,?,?,?,?,?,?)
        """

        num_par = 1
        for p in data["productos"]:

            cip = str(p["cip"]).strip()
            cantidad = float(p["cantidad"])
            precio = float(p["precio"])
            total_p = cantidad * precio

            cur.execute(sql_d, (
                folio_sae,
                num_par,
                cip,
                cantidad,
                precio,
                1,          # CVE_MONED = MXN
                total_p
            ))

            num_par += 1

        con.commit()
        con.close()

        return {
            "estatus": "ok",
            "mensaje": "Pedido creado correctamente en SAE",
            "folio_sae": folio_sae,
            "total": total
        }

    except Exception as e:
        return {"estatus": "error", "detalle": str(e)}