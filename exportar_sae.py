import openpyxl
from datetime import datetime

def exportar_facturas_excel(lista_facturas, filename="FACTURAS_SAE.xlsx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "FACTURAS"

    headers = [
        "SERIE", "FOLIO", "CVE_CLPV", "FECHA_DOC",
        "CVE_ART", "CANT", "PREC", "IMPU1",
        "DESC1", "NUM_ALM"
    ]
    ws.append(headers)

    for fac in lista_facturas:
        for item in fac["productos"]:
            ws.append([
                fac.get("serie", ""),
                fac.get("folio", ""),
                fac["cliente_numero"],
                fac.get("fecha", datetime.now().strftime("%Y-%m-%d")),
                item["cip"],              # 👈 aquí va la CVE_ART (140, 150, etc.)
                float(item["cantidad"]),
                float(item["precio"]),
                16 if item.get("iva", False) else 0,
                0,
                1
            ])

    wb.save(filename)
    return filename