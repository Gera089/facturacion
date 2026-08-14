import io
import json
import os
import re
import textwrap
from datetime import datetime
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdfcanvas

try:
    import qrcode
except ImportError:
    qrcode = None


FONT = "Helvetica"
FONT_B = "Helvetica-Bold"
PAGE_W, PAGE_H = letter
MARGIN_L = 31
MARGIN_R = 27
USABLE = PAGE_W - MARGIN_L - MARGIN_R


REGIMENES = {
    "601": "General de Ley Personas Morales",
    "603": "Personas Morales con Fines no Lucrativos",
    "605": "Sueldos y Salarios e Ingresos Asimilados a Salarios",
    "606": "Arrendamiento",
    "612": "Personas Fisicas con Actividades Empresariales y Profesionales",
    "616": "Sin obligaciones fiscales",
    "621": "Incorporacion Fiscal",
    "626": "Regimen Simplificado de Confianza",
}

FORMAS_PAGO = {
    "01": "Efectivo",
    "02": "Cheque nominativo",
    "03": "Transferencia electronica de fondos",
    "04": "Tarjeta de credito",
    "05": "Monedero electronico",
    "06": "Dinero electronico",
    "08": "Vales de despensa",
    "12": "Dacion en pago",
    "13": "Pago por subrogacion",
    "14": "Pago por consignacion",
    "15": "Condonacion",
    "17": "Compensacion",
    "23": "Novacion",
    "24": "Confusion",
    "25": "Remision de deuda",
    "26": "Prescripcion o caducidad",
    "27": "A satisfaccion del acreedor",
    "28": "Tarjeta de debito",
    "29": "Tarjeta de servicios",
    "30": "Aplicacion de anticipos",
    "31": "Intermediario pagos",
    "99": "Por definir",
}

METODOS_PAGO = {
    "PUE": "Pago en una sola exhibicion",
    "PPD": "Pago en parcialidades o diferido",
}

USOS_CFDI = {
    "G01": "Adquisicion de mercancias",
    "G03": "Gastos en general",
    "I01": "Construcciones",
    "I02": "Mobiliario y equipo de oficina por inversiones",
    "I03": "Equipo de transporte",
    "I04": "Equipo de computo y accesorios",
    "P01": "Por definir",
    "S01": "Sin efectos fiscales",
}


def _fmt_money(val):
    try:
        return f"{float(val or 0):,.2f}"
    except (ValueError, TypeError):
        return str(val or "")


def _fmt_qty(val):
    try:
        qty = float(val or 0)
        if qty == int(qty):
            return f"{qty:,.0f}"
        return f"{qty:,.2f}".rstrip("0").rstrip(".")
    except (ValueError, TypeError):
        return str(val or "")


def _safe_get(node, attr, default=""):
    return node.get(attr, default) if node is not None else default


def _row_get(row, key, default=""):
    if not row:
        return default
    try:
        value = row.get(key, default)
    except AttributeError:
        value = row[key] if key in row.keys() else default
    return default if value is None else value


def _date_display(value, with_time=True):
    value = str(value or "").strip()
    if not value:
        return ""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value[:19] if "T" in value or " " in value else value[:10], fmt)
            return dt.strftime("%d/%m/%Y %H:%M:%S" if with_time else "%d/%m/%Y")
        except Exception:
            pass
    return value


def _truncate_to_width(text, max_width, font=FONT, size=6):
    text = str(text or "").replace("\n", " ").strip()
    if stringWidth(text, font, size) <= max_width:
        return text
    ellipsis = "..."
    while text and stringWidth(text + ellipsis, font, size) > max_width:
        text = text[:-1]
    return text + ellipsis if text else ellipsis


def _draw_wrapped(c, text, x, y, width_chars=100, max_lines=3, line_h=7, font=FONT, size=5.7):
    c.setFont(font, size)
    lines = textwrap.wrap(str(text or ""), width=width_chars)[:max_lines]
    for line in lines:
        c.drawString(x, y, line)
        y -= line_h
    return y


def _wrap_to_width(text, max_width, font=FONT, size=6):
    words = str(text or "").replace("\n", " ").split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or stringWidth(candidate, font, size) <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines


def _draw_wrapped_width(c, text, x, y, max_width, max_lines=2, line_h=9, font=FONT, size=6.2):
    c.setFont(font, size)
    lines = _wrap_to_width(text, max_width, font, size)[:max_lines]
    for line in lines:
        c.drawString(x, y, line)
        y -= line_h
    return y


def _draw_label(c, label, value, x, y, label_w=48, value_w=120, size=6.2):
    c.setFont(FONT_B, size)
    c.drawString(x, y, label)
    c.setFont(FONT, size)
    c.drawString(x + label_w, y, _truncate_to_width(value, value_w, FONT, size))


def _draw_qr(c, x, y, size, data):
    if not qrcode:
        c.setFont(FONT, 6)
        c.drawCentredString(x + size / 2, y + size / 2, "[QR]")
        return
    try:
        qr = qrcode.QRCode(box_size=3, border=1)
        qr.add_data(data or "")
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        c.drawImage(ImageReader(buf), x, y, width=size, height=size, mask="auto")
    except Exception:
        c.setFont(FONT, 6)
        c.drawCentredString(x + size / 2, y + size / 2, "[QR]")


def _logo_filenames_for_company(company):
    text = str(company or "").strip().lower()
    if "remision" in text or "remisión" in text:
        return ["Remision.png", "default2.png", "default.png"]
    if "ibersur" in text:
        return ["ibersur.png", "default2.png", "default.png"]
    if "eza2007" in text or "eza" in text:
        return ["default.png"]
    if "gourmet" in text:
        return ["gourmet.png", "gourmet(2).png", "default2.png", "default.png"]
    return ["default2.png", "default.png"]


def _logo_path_for_company(company, logo_archivo=""):
    project_dir = Path(__file__).resolve().parents[2]
    bases = [
        project_dir.parent / "logos",
        # Logos incluidos dentro de la aplicación tanto en desarrollo como en
        # el paquete PyInstaller.
        project_dir / "app" / "comandas_legacy" / "logos",
        project_dir / "comandas_legacy" / "logos",
        project_dir / "AspelAPI" / "logos",
        project_dir.parent / "AspelAPI" / "logos",
        project_dir / "logos",
    ]
    seleccionado = Path(str(logo_archivo or "").strip()).name
    filenames = ([seleccionado] if seleccionado else []) + _logo_filenames_for_company(company)
    for filename in filenames:
        for base in bases:
            path = base / filename
            if path.exists():
                return str(path)
    return ""


def _draw_company_logo(c, company, logo_archivo=""):
    path = _logo_path_for_company(company, logo_archivo)
    if not path or not os.path.exists(path):
        return
    try:
        reader = ImageReader(path)
        img_w, img_h = reader.getSize()
        target_h = 48
        target_w = target_h * (img_w / img_h) if img_h else target_h
        if target_w > 96:
            target_w = 96
            target_h = target_w * (img_h / img_w) if img_w else 48
        c.drawImage(reader, 33, 720, width=target_w, height=target_h, preserveAspectRatio=True, mask="auto")
    except Exception:
        pass


def _numero_a_letras(numero):
    try:
        n = float(numero)
    except (ValueError, TypeError):
        return str(numero or "")
    entero = int(n)
    decimal = int(round((n - entero) * 100))
    unidades = [
        "", "UN", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE",
        "DIEZ", "ONCE", "DOCE", "TRECE", "CATORCE", "QUINCE", "DIECISEIS", "DIECISIETE",
        "DIECIOCHO", "DIECINUEVE",
    ]
    decenas = ["", "", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA", "NOVENTA"]
    centenas = ["", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS", "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS"]

    def convertir(num):
        if num == 0:
            return "CERO"
        if num < 20:
            return unidades[num]
        if num < 100:
            if num < 30:
                return "VEINTI" + convertir(num - 20).lower() if num > 20 else "VEINTE"
            return decenas[num // 10] + (f" Y {convertir(num % 10)}" if num % 10 else "")
        if num < 1000:
            return "CIEN" if num == 100 else centenas[num // 100] + (f" {convertir(num % 100)}" if num % 100 else "")
        if num < 1000000:
            miles = num // 1000
            resto = num % 1000
            return ("MIL" if miles == 1 else f"{convertir(miles)} MIL") + (f" {convertir(resto)}" if resto else "")
        millones = num // 1000000
        resto = num % 1000000
        return ("UN MILLON" if millones == 1 else f"{convertir(millones)} MILLONES") + (f" {convertir(resto)}" if resto else "")

    return f"{convertir(entero)} PESOS {decimal:02d}/100 M.N."


def _catalog_text(code, catalog):
    code = str(code or "").strip()
    desc = catalog.get(code, "")
    return f"({code}){desc}" if desc else f"({code})" if code else ""


def _load_server_rows(empresa, clave_receptor):
    datos = {"empresa": {}, "receptor": {}}
    try:
        from app.routers.timbrado_core import get_timbrado_connection

        with get_timbrado_connection() as conn:
            erow = conn.execute(
                "SELECT * FROM empresas_timbrado WHERE empresa = ? LIMIT 1",
                (str(empresa or "").strip(),),
            ).fetchone()
            if erow:
                datos["empresa"] = dict(erow)
            if clave_receptor:
                rrow = conn.execute(
                    "SELECT * FROM timbrado_receptores_fiscales WHERE empresa = ? AND clave_receptor = ? LIMIT 1",
                    (str(empresa or "").strip(), str(clave_receptor or "").strip()),
                ).fetchone()
                if rrow:
                    datos["receptor"] = dict(rrow)
    except Exception:
        pass
    return datos


def _address_line(row, cp_fallback=""):
    calle = str(row.get("calle") or "").strip()
    no_ext = str(row.get("no_exterior") or "").strip()
    no_int = str(row.get("no_interior") or "").strip()
    colonia = str(row.get("colonia") or "").strip()
    municipio = str(row.get("municipio") or "").strip()
    estado = str(row.get("estado") or "").strip()
    pais = str(row.get("pais") or "MEXICO").strip()
    cp = str(row.get("cp_fiscal") or row.get("codigo_postal") or row.get("lugar_expedicion") or cp_fallback or "").strip()
    if len(cp) != 5 and str(cp_fallback or "").strip():
        cp = str(cp_fallback or "").strip()
    numero = f"No. {no_ext}" if no_ext else ""
    if no_int:
        numero = f"{numero} Int. {no_int}".strip()
    parts = [f"{calle} {numero}".strip(), f"Col. {colonia}" if colonia else "", f"CP: {cp}" if cp else "", municipio, estado, pais]
    return ", ".join([p for p in parts if p])


def _consignee_address_line(row):
    calle = str(row.get("consig_calle") or "").strip()
    no_ext = str(row.get("consig_no_exterior") or "").strip()
    no_int = str(row.get("consig_no_interior") or "").strip()
    colonia = str(row.get("consig_colonia") or "").strip()
    municipio = str(row.get("consig_municipio") or row.get("consig_delegacion") or "").strip()
    estado = str(row.get("consig_estado") or "").strip()
    cp = str(row.get("consig_codigo_postal") or "").strip()
    if not any([calle, no_ext, no_int, colonia, municipio, estado, cp]):
        return ""
    pais = str(row.get("consig_pais") or "MEXICO").strip()
    numero = f"No. {no_ext}" if no_ext else ""
    if no_int:
        numero = f"{numero} Int. {no_int}".strip()
    parts = [f"{calle} {numero}".strip(), f"Col. {colonia}" if colonia else "", f"CP: {cp}" if cp else "", municipio, estado, pais]
    return ", ".join([p for p in parts if p])


def _load_consignee_from_legacy(db_row, empresa):
    factura_id = str(_row_get(db_row, "factura_id", "") or "").strip()
    factura = str(_row_get(db_row, "factura", "") or "").strip()
    # El receptor fiscal puede ser una central distinta de la sucursal que
    # recibe. Para "Enviar a" siempre se prioriza el cliente origen comercial.
    cliente_numero = str(
        _row_get(db_row, "cliente_origen_numero", "")
        or _row_get(db_row, "numero_cliente", "")
        or _row_get(db_row, "cliente_receptor_numero", "")
        or ""
    ).strip()
    try:
        from app.legacy_db import get_legacy_connection

        conn = get_legacy_connection()
        cur = conn.cursor(dictionary=True)
        try:
            where = "f.id = %s" if factura_id else "TRIM(f.factura) = TRIM(%s)"
            param = factura_id if factura_id else factura
            if not param:
                return {}
            cur.execute(
                f"""
                SELECT f.numero_cliente, f.consignatario AS factura_consignatario,
                       c.nombre, c.razon_social, c.rfc, c.codigo_postal,
                       c.calle, c.no_exterior, c.no_interior, c.colonia, c.municipio, c.estado, c.pais,
                       c.consignatario, c.consig_calle, c.consig_no_exterior, c.consig_no_interior,
                       c.consig_colonia, c.consig_delegacion, c.consig_municipio,
                       c.consig_estado, c.consig_pais, c.consig_codigo_postal
                FROM facturas f
                LEFT JOIN clientes c
                  ON CAST(c.numero AS CHAR) = CAST(f.numero_cliente AS CHAR)
                 AND UPPER(TRIM(c.empresa)) = UPPER(TRIM(f.empresa))
                WHERE {where}
                  AND UPPER(TRIM(f.empresa)) = UPPER(TRIM(%s))
                LIMIT 1
                """,
                (param, empresa),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            if cliente_numero:
                cur.execute(
                    """
                    SELECT CAST(numero AS CHAR) AS numero_cliente, nombre, razon_social, rfc, codigo_postal,
                           calle, no_exterior, no_interior, colonia, municipio, estado, pais,
                           consignatario, consig_calle, consig_no_exterior, consig_no_interior,
                           consig_colonia, consig_delegacion, consig_municipio,
                           consig_estado, consig_pais, consig_codigo_postal
                    FROM clientes
                    WHERE CAST(numero AS CHAR) = %s
                      AND UPPER(TRIM(empresa)) = UPPER(TRIM(%s))
                    LIMIT 1
                    """,
                    (cliente_numero, empresa),
                )
                row = cur.fetchone()
                if row:
                    return dict(row)
            return {}
        finally:
            cur.close()
            conn.close()
    except Exception:
        return {}


def _shipping_from_addenda(xml_root):
    # City Fresko publica el consignatario dentro de shipTo en la addenda XML.
    # Es la fuente correcta para el bloque "Enviar a" del PDF, no el receptor CFDI.
    for node in xml_root.iter():
        try:
            if node.tag.rsplit("}", 1)[-1] != "shipTo":
                continue
        except AttributeError:
            continue
        values = {}
        for child in node.iter():
            try:
                key = child.tag.rsplit("}", 1)[-1]
            except AttributeError:
                continue
            value = str(child.text or "").strip()
            if value and key not in values:
                values[key] = value
        name = values.get("name", "")
        address = ", ".join(
            value for value in (
                values.get("streetAddressOne", ""),
                values.get("city", ""),
                values.get("postalCode", ""),
            ) if value
        )
        if name or address:
            return name, address, True

    text = "".join(xml_root.itertext())
    match = re.search(r"NAD\+ST\+([^']+)'", text)
    if match:
        parts = ("NAD+ST+" + match.group(1)).split("+")
        name = parts[4] if len(parts) > 4 else ""
        address = parts[5].replace(":", ", ") if len(parts) > 5 else ""
        location = ", ".join(p.replace(":", " ").strip() for p in parts[6:9] if p.strip())
        return name.strip(), ", ".join([p for p in (address, location) if p]).strip(), False
    return "", "", False


def _cfdi_data(xml_root, db_row):
    ns = {
        "cfdi": "http://www.sat.gob.mx/cfd/4",
        "tfd": "http://www.sat.gob.mx/TimbreFiscalDigital",
        "pago20": "http://www.sat.gob.mx/Pagos20",
    }
    comp = xml_root.find(".//cfdi:Comprobante", ns) or xml_root
    emisor = comp.find("cfdi:Emisor", ns) or xml_root.find(".//cfdi:Emisor", ns)
    receptor = comp.find("cfdi:Receptor", ns) or xml_root.find(".//cfdi:Receptor", ns)
    conceptos = comp.findall(".//cfdi:Concepto", ns)
    tfd = xml_root.find(".//tfd:TimbreFiscalDigital", ns)
    empresa = _row_get(db_row, "empresa", _safe_get(emisor, "Nombre"))
    clave_receptor = _row_get(db_row, "cliente_receptor_numero", "")
    try:
        opciones_cfdi = json.loads(_row_get(db_row, "cfdi_opciones_json", "{}") or "{}")
        if not isinstance(opciones_cfdi, dict):
            opciones_cfdi = {}
    except Exception:
        opciones_cfdi = {}
    # El historial de CFDI conserva la referencia de la factura; las opciones
    # de emisión se guardan en su renglón de cola y de ahí se recuperan al
    # reimprimir el PDF, incluso después de timbrar.
    if not opciones_cfdi and _row_get(db_row, "factura_id", ""):
        try:
            from app.routers.timbrado_core import get_timbrado_connection
            with get_timbrado_connection() as conn:
                queue_row = conn.execute(
                    "SELECT cfdi_opciones_json FROM timbrado_queue WHERE factura_id = ? ORDER BY id DESC LIMIT 1",
                    (_row_get(db_row, "factura_id", ""),),
                ).fetchone()
                opciones_cfdi = json.loads(dict(queue_row or {}).get("cfdi_opciones_json") or "{}")
                if not isinstance(opciones_cfdi, dict):
                    opciones_cfdi = {}
        except Exception:
            opciones_cfdi = {}
    server = _load_server_rows(empresa, clave_receptor)
    erow = server["empresa"]
    rrow = server["receptor"]
    consignee = _load_consignee_from_legacy(db_row, empresa)
    ship_name, ship_addr, city_fresko = _shipping_from_addenda(xml_root)
    consignee_name = str(consignee.get("consignatario") or consignee.get("factura_consignatario") or "").strip()
    consignee_addr = _consignee_address_line(consignee)
    legacy_fiscal_address = _address_line(consignee, _safe_get(receptor, "DomicilioFiscalReceptor"))
    subtotal = _safe_get(comp, "SubTotal", "0")
    descuento = _safe_get(comp, "Descuento", "0")
    total = _safe_get(comp, "Total", "0")
    es_complemento_pago = _safe_get(comp, "TipoDeComprobante", "").upper() == "P"
    monto_total_pagos = "0"
    rep_pagos = []
    if es_complemento_pago:
        pagos_totales = comp.find("cfdi:Complemento/pago20:Pagos/pago20:Totales", ns)
        monto_total_pagos = _safe_get(pagos_totales, "MontoTotalPagos", "0")
        for pago in comp.findall("cfdi:Complemento/pago20:Pagos/pago20:Pago", ns):
            pago_info = {
                "fecha": _safe_get(pago, "FechaPago"),
                "forma": _safe_get(pago, "FormaDePagoP"),
                "moneda": _safe_get(pago, "MonedaP"),
                "monto": _safe_get(pago, "Monto"),
                "num_operacion": _safe_get(pago, "NumOperacion"),
                "rfc_cta_ord": _safe_get(pago, "RfcEmisorCtaOrd"),
                "cta_ordenante": _safe_get(pago, "CtaOrdenante"),
                "rfc_cta_ben": _safe_get(pago, "RfcEmisorCtaBen"),
                "cta_beneficiario": _safe_get(pago, "CtaBeneficiario"),
                "docs": [],
            }
            for docto in pago.findall("pago20:DoctoRelacionado", ns):
                pago_info["docs"].append({
                    "uuid": _safe_get(docto, "IdDocumento"),
                    "serie": _safe_get(docto, "Serie"),
                    "folio": _safe_get(docto, "Folio"),
                    "parcialidad": _safe_get(docto, "NumParcialidad"),
                    "saldo_anterior": _safe_get(docto, "ImpSaldoAnt"),
                    "pagado": _safe_get(docto, "ImpPagado"),
                    "saldo_insoluto": _safe_get(docto, "ImpSaldoInsoluto"),
                    "objeto_imp": _safe_get(docto, "ObjetoImpDR"),
                })
            rep_pagos.append(pago_info)
        if str(monto_total_pagos or "0") in ("", "0", "0.0", "0.00"):
            try:
                monto_total_pagos = str(sum(
                    float(_safe_get(pago, "Monto", "0") or 0)
                    for pago in comp.findall("cfdi:Complemento/pago20:Pagos/pago20:Pago", ns)
                ))
            except Exception:
                monto_total_pagos = "0"
    impuestos = comp.find("cfdi:Impuestos", ns)
    iva = "0"
    if impuestos is not None:
        iva = impuestos.get("TotalImpuestosTrasladados", "0") or "0"
        if iva == "0":
            try:
                iva = str(sum(float(t.get("Importe") or 0) for t in impuestos.findall(".//cfdi:Traslado", ns)))
            except Exception:
                iva = "0"
    emisor_direccion = _address_line(erow, _safe_get(comp, "LugarExpedicion"))
    if ("EZA" in str(empresa or "").upper() or _safe_get(emisor, "Rfc") == "EZA070521MT4") and not erow.get("calle"):
        emisor_direccion = "DAKOTA No. 359 Int: 301, Col. AMPLIACION NAPOLES, CP: 03840, BENITO JUAREZ, CIUDAD DE MEXICO, MEXICO"
    receptor_cp_xml = _safe_get(receptor, "DomicilioFiscalReceptor")
    receptor_cp = rrow.get("cp_fiscal") or receptor_cp_xml or consignee.get("codigo_postal") or ""
    receptor_direccion = _address_line(rrow, receptor_cp_xml)
    if not str(rrow.get("calle") or "").strip() and legacy_fiscal_address:
        receptor_direccion = legacy_fiscal_address
    return {
        "ns": ns,
        "comp": comp,
        "conceptos": conceptos,
        "empresa": empresa,
        "emisor_nombre": erow.get("razon_social") or _safe_get(emisor, "Nombre") or empresa,
        "emisor_rfc": erow.get("rfc_emisor") or _safe_get(emisor, "Rfc"),
        "emisor_regimen": erow.get("regimen_fiscal") or _safe_get(emisor, "RegimenFiscal"),
        "emisor_direccion": emisor_direccion,
        "receptor_clave": clave_receptor,
        "receptor_nombre": rrow.get("razon_social") or consignee.get("razon_social") or _safe_get(receptor, "Nombre") or _row_get(db_row, "cliente_receptor_nombre", ""),
        "receptor_rfc": rrow.get("rfc") or consignee.get("rfc") or _safe_get(receptor, "Rfc"),
        "receptor_cp": receptor_cp,
        "receptor_regimen": rrow.get("regimen_fiscal") or _safe_get(receptor, "RegimenFiscalReceptor"),
        "receptor_uso": rrow.get("uso_cfdi") or _safe_get(receptor, "UsoCFDI"),
        "receptor_direccion": receptor_direccion,
        "enviar_a": (ship_name or consignee_name or _row_get(db_row, "cliente_receptor_nombre", "")) if city_fresko else (consignee_name or ship_name or _row_get(db_row, "cliente_receptor_nombre", "")),
        "direccion_envio": (ship_addr or consignee_addr or _address_line(rrow, _safe_get(receptor, "DomicilioFiscalReceptor"))) if city_fresko else (consignee_addr or ship_addr or _address_line(rrow, _safe_get(receptor, "DomicilioFiscalReceptor"))),
        "orden_compra": str(_row_get(db_row, "orden_compra", "") or opciones_cfdi.get("orden_compra") or "").strip(),
        "serie": _safe_get(comp, "Serie"),
        "folio": _safe_get(comp, "Folio") or _row_get(db_row, "folio_cfdi", ""),
        "factura": _row_get(db_row, "factura", ""),
        "fecha": _date_display(_safe_get(comp, "Fecha"), True),
        "fecha_iso": _safe_get(comp, "Fecha"),
        "fecha_corta": _date_display(_safe_get(comp, "Fecha"), False),
        "lugar": _safe_get(comp, "LugarExpedicion") or erow.get("lugar_expedicion") or erow.get("cp_fiscal") or "",
        "forma": _catalog_text((rep_pagos[0].get("forma") if rep_pagos else "") or _safe_get(comp, "FormaPago"), FORMAS_PAGO),
        "metodo": "Complemento para recepcion de pagos" if es_complemento_pago else _catalog_text(_safe_get(comp, "MetodoPago"), METODOS_PAGO),
        "uso": _catalog_text(_safe_get(receptor, "UsoCFDI") or rrow.get("uso_cfdi"), USOS_CFDI),
        "subtotal": subtotal,
        "descuento": descuento,
        "iva": iva,
        "total": total,
        "es_complemento_pago": es_complemento_pago,
        "monto_total_pagos": monto_total_pagos,
        "rep_pagos": rep_pagos,
        "uuid": _safe_get(tfd, "UUID") or _row_get(db_row, "uuid", ""),
        "fecha_timbrado": _date_display(_safe_get(tfd, "FechaTimbrado") or _row_get(db_row, "fecha_timbrado", ""), True),
        "fecha_timbrado_iso": _safe_get(tfd, "FechaTimbrado") or _row_get(db_row, "fecha_timbrado", ""),
        "no_certificado": _safe_get(comp, "NoCertificado"),
        "no_certificado_sat": _safe_get(tfd, "NoCertificadoSAT"),
        "sello_cfd": _safe_get(tfd, "SelloCFD"),
        "sello_sat": _safe_get(tfd, "SelloSAT"),
        "rfc_prov": _safe_get(tfd, "RfcProvCertif"),
    }


def _draw_header(c, data):
    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.55)
    _draw_company_logo(c, data["empresa"], data.get("logo_archivo") or "")
    c.setFont(FONT_B, 15)
    c.drawCentredString(PAGE_W / 2, 764, data["emisor_nombre"] or data["empresa"])
    c.setFont(FONT, 8)
    c.drawCentredString(PAGE_W / 2, 747, f"RFC: {data['emisor_rfc']}")
    c.drawCentredString(PAGE_W / 2, 735, f"Regimen fiscal:{_catalog_text(data['emisor_regimen'], REGIMENES)}")
    c.setFont(FONT, 7)
    c.drawCentredString(PAGE_W / 2, 723, _truncate_to_width(data["emisor_direccion"], 520, FONT, 7))

    left_x, top, left_w, box_h = MARGIN_L, 704, 272, 132
    right_x, right_w = 315, 270
    c.rect(left_x, top - box_h, left_w, box_h, stroke=1, fill=0)
    c.rect(right_x, top - box_h, right_w, box_h, stroke=1, fill=0)
    c.setFont(FONT_B, 8)
    c.drawCentredString(left_x + left_w / 2, top + 4, "Datos del cliente")
    c.drawCentredString(right_x + right_w / 2, top + 4, "Comprobante fiscal digital")

    c.setFont(FONT_B, 7)
    c.drawString(left_x + 8, top - 17, _truncate_to_width(data["receptor_nombre"], 178, FONT_B, 7))
    if data["receptor_clave"]:
        c.drawRightString(left_x + left_w - 10, top - 17, f"( {data['receptor_clave']} )")
    y = top - 36
    _draw_label(c, "RFC:", data["receptor_rfc"], left_x + 8, y, 39, 180, 8.3)
    y -= 16
    c.setFont(FONT_B, 8.3)
    c.drawString(left_x + 8, y, "Calle:")
    _draw_wrapped_width(c, data["receptor_direccion"], left_x + 50, y, 205, max_lines=2, line_h=11, font=FONT, size=8.1)
    y -= 27
    _draw_label(c, "CP:", data["receptor_cp"], left_x + 8, y, 39, 80, 8.3)
    y -= 14

    y = top - 18
    meta = [
        ("Serie:", data["serie"]),
        ("Folio:", data["folio"]),
        ("Fecha y hora:", data["fecha"]),
        ("Lugar de expedicion:", data["lugar"]),
        ("Regimen fiscal:", _catalog_text(data["receptor_regimen"], REGIMENES)),
        ("Forma:", data["forma"]),
        ("Metodo de pago y cuenta:", data["metodo"]),
        ("Uso CFDI:", data["uso"]),
    ]
    for label, value in meta:
        label_w = 106 if label == "Metodo de pago y cuenta:" else 92
        value_x = right_x + 8 + label_w
        value_w = right_x + right_w - value_x - 9
        c.setFont(FONT_B, 8.0)
        c.drawString(right_x + 8, y, label)
        # "Uso CFDI" se conserva en una sola línea: la descripción larga
        # no debe desplazar los campos siguientes ni salirse del recuadro.
        if label == "Uso CFDI:":
            lines = _wrap_to_width(value, value_w, FONT, 8.0)[:2]
        else:
            lines = _wrap_to_width(value, value_w, FONT, 8.0)[:2]
        c.setFont(FONT, 8.0)
        for idx, line in enumerate(lines or [""]):
            c.drawString(value_x, y - (idx * 9), line)
        y -= 20 if len(lines) > 1 else 14

    y = 557
    _draw_label(c, "Enviar a:", data["enviar_a"] or "-", MARGIN_L, y, 58, 470, 6.4)
    y -= 12
    _draw_label(c, "Direccion envio:", data["direccion_envio"] or "-", MARGIN_L, y, 78, 450, 6.4)
    y -= 12
    _draw_label(c, "Orden de compra:", data.get("orden_compra") or "-", MARGIN_L, y, 82, 445, 6.4)
    _draw_label(c, "Vendedor :", "", 404, y + 12, 58, 110, 6.4)
    c.line(MARGIN_L, 518, PAGE_W - MARGIN_R, 518)


def _has_descuento(data):
    try:
        return float(str(data.get("descuento") or 0).replace(",", "")) > 0
    except Exception:
        return False


def _draw_product_table_header(c, y=506, show_descuento=True):
    x = MARGIN_L
    if show_descuento:
        col_w = [34, 40, 58, 43, 72, 180, 35, 35, 48]
        headers = ["Cantidad", "Unidad", "Clave Unidad", "Clave", "Clave Prod. Serv", "Descripcion", "Descuento", "P/U", "Importe"]
    else:
        col_w = [34, 40, 58, 43, 72, 215, 35, 48]
        headers = ["Cantidad", "Unidad", "Clave Unidad", "Clave", "Clave Prod. Serv", "Descripcion", "P/U", "Importe"]
    starts = [x]
    for width in col_w[:-1]:
        starts.append(starts[-1] + width)
    table_right = starts[-1] + col_w[-1]

    c.setFont(FONT_B, 6.2)
    for i, header in enumerate(headers):
        c.drawCentredString(starts[i] + col_w[i] / 2, y, header)
    c.line(x, y - 5, table_right, y - 5)
    return starts, col_w, table_right, y - 17


def _draw_product_table(c, data):
    # La columna se imprime siempre para que cada partida indique 0.00 o su %.
    show_descuento = True
    starts, col_w, table_right, y = _draw_product_table_header(c, show_descuento=show_descuento)

    row_h = 13.2
    body_size = 6.3
    c.setFont(FONT, body_size)
    for idx, concept in enumerate(data["conceptos"]):
        if y < 318:
            c.line(MARGIN_L, y + 8, table_right, y + 8)
            c.showPage()
            _draw_header(c, data)
            starts, col_w, table_right, y = _draw_product_table_header(c, show_descuento=show_descuento)
            c.setFont(FONT, body_size)
        vals_base = [
            _fmt_qty(concept.get("Cantidad")),
            concept.get("Unidad", ""),
            concept.get("ClaveUnidad", ""),
            concept.get("NoIdentificacion", ""),
            concept.get("ClaveProdServ", ""),
            concept.get("Descripcion", ""),
            _fmt_money(concept.get("ValorUnitario")),
            _fmt_money(concept.get("Importe")),
        ]
        vals = vals_base[:6] + ["0.00"] + vals_base[6:] if show_descuento else vals_base
        try:
            if float(concept.get("Descuento") or 0):
                importe = float(concept.get("Importe") or 0)
                desc = float(concept.get("Descuento") or 0)
                if show_descuento:
                    vals[6] = f"{(desc / importe * 100) if importe else 0:.2f}"
        except Exception:
            pass
        c.line(MARGIN_L, y + 8, table_right, y + 8)
        for sx in starts:
            c.line(sx, y + 8, sx, y - 5)
        c.line(table_right, y + 8, table_right, y - 5)
        for i, val in enumerate(vals):
            if i == 5:
                c.drawString(starts[i] + 3, y, _truncate_to_width(val, col_w[i] - 5, FONT, body_size))
            elif i in ((0, 7, 8) if show_descuento else (0, 6, 7)):
                c.drawCentredString(starts[i] + col_w[i] / 2, y, _truncate_to_width(val, col_w[i] - 4, FONT, body_size))
            else:
                c.drawCentredString(starts[i] + col_w[i] / 2, y, _truncate_to_width(val, col_w[i] - 4, FONT, body_size))
        y -= row_h
    c.line(MARGIN_L, y + 8, table_right, y + 8)
    return y


def _draw_rep_table(c, data):
    y = 506
    c.setFont(FONT_B, 7.2)
    c.drawString(MARGIN_L, y + 14, "Detalle del complemento de pago")
    pagos = data.get("rep_pagos") or []
    pago = pagos[0] if pagos else {}
    c.setFont(FONT, 6.6)
    resumen = [
        f"Fecha pago: {_date_display(pago.get('fecha'), True)}",
        f"Forma: {_catalog_text(pago.get('forma'), FORMAS_PAGO)}",
        f"Moneda: {pago.get('moneda') or 'MXN'}",
        f"Monto: ${_fmt_money(pago.get('monto') or data.get('monto_total_pagos'))}",
    ]
    c.drawString(MARGIN_L, y, _truncate_to_width("  |  ".join(resumen), USABLE, FONT, 6.6))
    y -= 12
    extra = []
    if pago.get("num_operacion"):
        extra.append(f"Operacion: {pago.get('num_operacion')}")
    if pago.get("cta_ordenante"):
        extra.append(f"Origen: {pago.get('rfc_cta_ord') or ''} {pago.get('cta_ordenante')}")
    if pago.get("cta_beneficiario"):
        extra.append(f"Destino: {pago.get('rfc_cta_ben') or ''} {pago.get('cta_beneficiario')}")
    if extra:
        c.drawString(MARGIN_L, y, _truncate_to_width("  |  ".join(extra), USABLE, FONT, 6.2))
        y -= 13

    col_w = [38, 44, 265, 58, 58, 58, 44]
    headers = ["Serie", "Folio", "UUID documento relacionado", "Saldo ant.", "Pagado", "Saldo", "Parc."]
    starts = [MARGIN_L]
    for width in col_w[:-1]:
        starts.append(starts[-1] + width)
    table_right = starts[-1] + col_w[-1]
    c.line(MARGIN_L, y + 8, table_right, y + 8)
    c.setFont(FONT_B, 6.2)
    for i, header in enumerate(headers):
        c.drawCentredString(starts[i] + col_w[i] / 2, y, header)
    c.line(MARGIN_L, y - 5, table_right, y - 5)
    y -= 17
    docs = []
    for item in pagos:
        docs.extend(item.get("docs") or [])
    c.setFont(FONT, 6.1)
    for doc in docs or [{}]:
        if y < 318:
            c.line(MARGIN_L, y + 8, table_right, y + 8)
            c.showPage()
            _draw_header(c, data)
            y = 506
        vals = [
            doc.get("serie") or "",
            doc.get("folio") or "",
            doc.get("uuid") or "",
            _fmt_money(doc.get("saldo_anterior")),
            _fmt_money(doc.get("pagado")),
            _fmt_money(doc.get("saldo_insoluto")),
            doc.get("parcialidad") or "",
        ]
        c.line(MARGIN_L, y + 8, table_right, y + 8)
        for sx in starts:
            c.line(sx, y + 8, sx, y - 5)
        c.line(table_right, y + 8, table_right, y - 5)
        for i, val in enumerate(vals):
            if i == 2:
                c.drawString(starts[i] + 3, y, _truncate_to_width(val, col_w[i] - 5, FONT, 6.1))
            else:
                c.drawCentredString(starts[i] + col_w[i] / 2, y, _truncate_to_width(val, col_w[i] - 4, FONT, 6.1))
        y -= 13
    c.line(MARGIN_L, y + 8, table_right, y + 8)
    return y


def _draw_totals(c, data, draw_legend=True, qr_y=300, qr_size=95, totals_y=262, draw_words=True):
    qr_data = (
        "https://verificacfdi.facturaelectronica.sat.gob.mx/default.aspx"
        f"?id={data['uuid']}&re={data['emisor_rfc']}&rr={data['receptor_rfc']}&tt={data['total']}"
    )
    _draw_qr(c, MARGIN_L, qr_y, qr_size, qr_data)

    monto_mostrado = data.get("monto_total_pagos") if data.get("es_complemento_pago") else data["total"]
    if draw_words:
        c.setFont(FONT, 7)
        c.drawString(MARGIN_L, qr_y - 18, _numero_a_letras(monto_mostrado))
    c.setFont(FONT_B, 7)
    labels = ([
        ("Monto recibido (REP 2.0)", monto_mostrado),
    ] if data.get("es_complemento_pago") else [
        ("Subtotal", data["subtotal"]),
        ("Descuento", data["descuento"]),
        ("I.V.A.", data["iva"]),
        ("Total", data["total"]),
    ])
    y = totals_y
    for label, value in labels:
        c.drawRightString(480, y, label)
        c.drawRightString(585, y, f"${_fmt_money(value)}" if label in ("Total", "Monto recibido (REP 2.0)") else _fmt_money(value))
        y -= 14
    if draw_legend:
        c.setFont(FONT, 6.6)
        c.drawString(MARGIN_L, 211, '"Este documento es una representacion impresa de un CFDI"')


def _draw_timbre_block(c, data, box_top=519, box_h=224, compact=False):
    box_x, box_w = MARGIN_L, USABLE
    c.rect(box_x, box_top - box_h, box_w, box_h, stroke=1, fill=0)
    c.setFont(FONT_B, 7)
    c.drawCentredString(PAGE_W / 2, box_top - 14, '"Este documento es una representacion impresa de un CFDI"')
    y = box_top - 32
    label_size = 5.7 if compact else 6.2
    text_size = 4.8 if compact else 5.4
    line_h = 5.8 if compact else 7
    cadena_lines = 3 if compact else 6
    sello_lines = 2 if compact else 5
    _draw_label(c, "Folio fiscal:", data["uuid"], box_x + 10, y, 78, 430, label_size)
    y -= 11 if compact else 13
    _draw_label(c, "Fecha y hora de certificacion:", data["fecha_timbrado"], box_x + 10, y, 122, 170, label_size)
    _draw_label(c, "Certificado SAT:", data["no_certificado_sat"], box_x + 330, y, 82, 120, label_size)
    y -= 11 if compact else 13
    _draw_label(c, "Certificado emisor:", data["no_certificado"], box_x + 10, y, 88, 180, label_size)
    y -= 13 if compact else 18
    c.setFont(FONT_B, 6.0 if compact else 6.4)
    c.drawString(box_x + 10, y, "Cadena original del complemento de certificacion digital del SAT:")
    y -= 7 if compact else 10
    cadena = f"||1.1|{data['uuid']}|{data['fecha_timbrado']}|{data['rfc_prov']}|{data['sello_cfd']}|{data['no_certificado_sat']}||"
    y = _draw_wrapped(c, cadena, box_x + 10, y, 152, cadena_lines, line_h, FONT, text_size)
    y -= 5 if compact else 8
    c.setFont(FONT_B, 6.0 if compact else 6.4)
    c.drawString(box_x + 10, y, "Sello digital del CFDI:")
    y -= 7 if compact else 9
    y = _draw_wrapped(c, data["sello_cfd"], box_x + 10, y, 152, sello_lines, line_h, FONT, text_size)
    y -= 5 if compact else 7
    c.setFont(FONT_B, 6.0 if compact else 6.4)
    c.drawString(box_x + 10, y, "Sello digital del SAT:")
    y -= 7 if compact else 9
    _draw_wrapped(c, data["sello_sat"], box_x + 10, y, 152, sello_lines, line_h, FONT, text_size)


def _draw_timbre_page(c, data):
    _draw_header(c, data)
    _draw_timbre_block(c, data)


def _is_eza(data):
    text = f"{data.get('empresa','')} {data.get('emisor_nombre','')} {data.get('emisor_rfc','')}".upper()
    return "EZA" in text or "EZA070521MT4" in text


def _fmt_eza_money(value, decimals=2):
    try:
        return f"{float(value or 0):,.{decimals}f}"
    except Exception:
        return str(value or "")


def _draw_eza_logos(c, logo_archivo=""):
    # EZA2007 utiliza la imagen institucional default; no se mezclan logos
    # de Ibersur ni Remisiones en su comprobante fiscal.
    nombre = Path(str(logo_archivo or "").strip()).name or "default.png"
    for name, x, y, w, h in [(nombre, 58, 730, 70, 38)]:
        path = ""
        project_dir = Path(__file__).resolve().parents[2]
        for base in (
            project_dir / "app" / "comandas_legacy" / "logos",
            project_dir / "comandas_legacy" / "logos",
            project_dir / "AspelAPI" / "logos",
            project_dir.parent / "AspelAPI" / "logos",
            project_dir / "logos",
        ):
            candidate = base / name
            if candidate.exists():
                path = str(candidate)
                break
        if path:
            try:
                c.drawImage(path, x, y, width=w, height=h, preserveAspectRatio=True, mask="auto")
            except Exception:
                pass


def _draw_eza_header(c, data):
    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.8)
    _draw_eza_logos(c, data.get("logo_archivo") or "")
    c.setFont(FONT, 16)
    c.drawCentredString(PAGE_W / 2, 760, data["emisor_nombre"] or "EZA2007")
    c.setFont(FONT_B, 8)
    c.drawString(148, 744, f"RFC: {data['emisor_rfc']}")
    c.drawString(281, 744, f"Regimen fiscal:{_catalog_text(data['emisor_regimen'], REGIMENES)}")
    c.setFont(FONT, 7.3)
    c.drawCentredString(PAGE_W / 2 + 80, 731, _truncate_to_width(data["emisor_direccion"], 440, FONT, 7.3))

    left_x, right_x, top, bottom = 31, 383, 711, 570
    left_w, right_w = 342, 208
    c.rect(left_x, bottom, left_w, top - bottom, stroke=1, fill=0)
    c.rect(right_x, bottom + 2, right_w, top - bottom - 2, stroke=1, fill=0)
    c.setFont(FONT_B, 10)
    c.drawString(left_x + 3, top + 1, "Datos del cliente")
    c.drawCentredString(right_x + right_w / 2, top + 1, "Comprobante fiscal digital")

    c.setFont(FONT_B, 8.2)
    c.drawString(left_x + 3, top - 16, _truncate_to_width(data["receptor_nombre"], 250, FONT_B, 8.2))
    if data["receptor_clave"]:
        c.drawRightString(left_x + left_w - 8, top - 16, f"( {data['receptor_clave']} )")
    c.drawString(left_x + 3, top - 31, "RFC:")
    c.drawString(left_x + 31, top - 31, data["receptor_rfc"])
    c.drawString(left_x + 3, top - 45, "Direccion:")
    _draw_wrapped_width(c, data["receptor_direccion"] or f"CP: {data['receptor_cp']}", left_x + 55, top - 45, 275, max_lines=4, line_h=12, font=FONT, size=8)

    meta = [
        ("Serie:", data["serie"]),
        ("Folio:", data["folio"]),
        ("Fecha y hora:", data.get("fecha_iso") or data["fecha"].replace(" ", "T")),
        ("Lugar de expedicion:", data["lugar"]),
        ("Forma de pago:", data["forma"]),
        ("Metodo de pago y Cuenta:", data["metodo"]),
        ("Uso de CFDI:", data["uso"]),
    ]
    y = top - 14
    for label, value in meta:
        c.setFont(FONT_B, 8)
        c.drawString(right_x + 3, y, label)
        c.setFont(FONT, 7.4)
        if label == "Forma de pago:":
            c.setFont(FONT, 6.2)
            c.drawString(right_x + 11, y - 10, _truncate_to_width(value, 188, FONT, 6.2))
            y -= 25
        elif label == "Metodo de pago y Cuenta:":
            lines = _wrap_to_width(value, 188, FONT, 7.4)[:2]
            for idx, line in enumerate(lines or [""]):
                c.drawString(right_x + 11, y - 10 - (idx * 9), line)
            y -= 14 + (max(1, len(lines)) * 9) + 2
        elif label == "Uso de CFDI:":
            # Igual que el método de pago: etiqueta arriba y valor debajo,
            # alineado a la izquierda dentro del bloque fiscal.
            lines = _wrap_to_width(value, 188, FONT, 7.4)[:2]
            for idx, line in enumerate(lines or [""]):
                c.drawString(right_x + 11, y - 10 - (idx * 9), line)
            y -= 14 + (max(1, len(lines)) * 9) + 2
        else:
            c.drawString(right_x + 96, y, _truncate_to_width(value, 104, FONT, 7.4))
            y -= 12

    y = 558
    c.setFont(FONT_B, 8.5)
    c.drawString(35, y, "Enviar a:")
    c.drawString(35, y - 13, "Direccion envio:")
    c.drawString(35, y - 26, "Orden de compra:")
    c.setFont(FONT, 7.8)
    c.drawString(113, y, _truncate_to_width(data["enviar_a"] or "-", 470, FONT, 7.8))
    c.drawString(113, y - 13, _truncate_to_width(data["direccion_envio"] or "-", 470, FONT, 6.6))
    c.drawString(113, y - 26, _truncate_to_width(data.get("orden_compra") or "-", 470, FONT, 7.8))
    c.setFont(FONT_B, 8.5)
    c.drawString(482, y, "Vendedor :")


def _draw_eza_products_header(c, y=517, show_descuento=True):
    x = 29
    c.line(x, y, 592, y)
    if show_descuento:
        headers = ["Cantidad", "Unidad", "Clave\nUnidad", "Clave", "Clave\nProd. Serv", "Descripcion", "Descuento", "P/U", "Importe"]
        col_w = [45, 38, 52, 52, 70, 166, 45, 54, 41]
    else:
        headers = ["Cantidad", "Unidad", "Clave\nUnidad", "Clave", "Clave\nProd. Serv", "Descripcion", "P/U", "Importe"]
        col_w = [45, 38, 52, 52, 70, 211, 54, 41]
    starts = [x]
    for w in col_w[:-1]:
        starts.append(starts[-1] + w)
    c.setFont(FONT_B, 8)
    for i, head in enumerate(headers):
        lines = head.split("\n")
        for j, line in enumerate(lines):
            c.drawCentredString(starts[i] + col_w[i] / 2, y - 17 - (j * 10), line)
    c.line(x, y - 28, 592, y - 28)
    return starts, col_w, y - 43


def _draw_eza_products(c, data):
    show_descuento = True
    starts, col_w, row_y = _draw_eza_products_header(c, show_descuento=show_descuento)
    c.setFont(FONT, 7.4)
    for concept in data["conceptos"]:
        if row_y < 390:
            c.showPage()
            _draw_eza_header(c, data)
            starts, col_w, row_y = _draw_eza_products_header(c, show_descuento=show_descuento)
            c.setFont(FONT, 7.4)
        try:
            importe = float(concept.get("Importe") or 0)
            desc = float(concept.get("Descuento") or 0)
            pct = (desc / importe * 100) if importe else 0
        except Exception:
            pct = 0
        vals_base = [
            _fmt_eza_money(concept.get("Cantidad"), 3),
            concept.get("Unidad", ""),
            concept.get("ClaveUnidad", ""),
            concept.get("NoIdentificacion", ""),
            concept.get("ClaveProdServ", ""),
            concept.get("Descripcion", ""),
            _fmt_eza_money(concept.get("ValorUnitario"), 3),
            _fmt_eza_money(concept.get("Importe"), 2),
        ]
        vals = vals_base[:6] + [f"{pct:.2f}"] + vals_base[6:] if show_descuento else vals_base
        for i, val in enumerate(vals):
            if i == 5:
                c.drawString(starts[i] + 3, row_y, _truncate_to_width(val, col_w[i] - 5, FONT, 7.4))
            else:
                c.drawCentredString(starts[i] + col_w[i] / 2, row_y, _truncate_to_width(val, col_w[i] - 4, FONT, 7.4))
        row_y -= 14
    return row_y


def _draw_eza_rep_details(c, data):
    y = 514
    left = 31
    right = 592
    pagos = data.get("rep_pagos") or []
    pago = pagos[0] if pagos else {}
    c.setFont(FONT_B, 9)
    c.drawString(left, y, "Detalle del pago")
    y -= 14
    c.setFont(FONT_B, 7.6)
    labels = [
        ("Fecha pago:", _date_display(pago.get("fecha"), True)),
        ("Moneda:", pago.get("moneda") or "MXN"),
        ("Monto:", "$" + _fmt_money(pago.get("monto") or data.get("monto_total_pagos"))),
    ]
    x = left
    widths = [185, 120, 160]
    for (label, value), width in zip(labels, widths):
        c.drawString(x, y, label)
        c.setFont(FONT, 7.4)
        c.drawString(x + 50, y, _truncate_to_width(value, width - 52, FONT, 7.4))
        c.setFont(FONT_B, 7.6)
        x += width
    y -= 13
    c.setFont(FONT_B, 7.6)
    c.drawString(left, y, "Forma pago:")
    c.setFont(FONT, 7.2)
    c.drawString(left + 64, y, _truncate_to_width(_catalog_text(pago.get("forma"), FORMAS_PAGO), 455, FONT, 7.2))
    y -= 13
    if pago.get("num_operacion"):
        _draw_label(c, "Num. operacion:", pago.get("num_operacion"), left, y, 78, 450, 7.2)
        y -= 12
    cuenta_txt = []
    if pago.get("cta_ordenante"):
        cuenta_txt.append(f"Origen {pago.get('rfc_cta_ord') or ''} {pago.get('cta_ordenante')}")
    if pago.get("cta_beneficiario"):
        cuenta_txt.append(f"Destino {pago.get('rfc_cta_ben') or ''} {pago.get('cta_beneficiario')}")
    if cuenta_txt:
        _draw_label(c, "Cuentas:", " | ".join(cuenta_txt), left, y, 45, 500, 7.0)
        y -= 13

    y -= 4
    c.line(left, y, right, y)
    headers = ["Serie", "Folio", "UUID documento relacionado", "Saldo ant.", "Pagado", "Saldo"]
    col_w = [43, 48, 276, 62, 62, 62]
    starts = [left]
    for width in col_w[:-1]:
        starts.append(starts[-1] + width)
    c.setFont(FONT_B, 7.0)
    for i, head in enumerate(headers):
        c.drawCentredString(starts[i] + col_w[i] / 2, y - 12, head)
    c.line(left, y - 20, right, y - 20)
    y -= 33
    c.setFont(FONT, 6.8)
    docs = []
    for item in pagos:
        docs.extend(item.get("docs") or [])
    for doc in docs or [{}]:
        vals = [
            doc.get("serie") or "",
            doc.get("folio") or "",
            doc.get("uuid") or "",
            _fmt_money(doc.get("saldo_anterior")),
            _fmt_money(doc.get("pagado")),
            _fmt_money(doc.get("saldo_insoluto")),
        ]
        if y < 360:
            c.showPage()
            _draw_eza_header(c, data)
            y = 514
        for i, val in enumerate(vals):
            if i == 2:
                c.drawCentredString(starts[i] + col_w[i] / 2, y, _truncate_to_width(val, col_w[i] - 6, FONT, 6.8))
            else:
                c.drawCentredString(starts[i] + col_w[i] / 2, y, _truncate_to_width(val, col_w[i] - 4, FONT, 6.8))
        y -= 13
    c.line(left, y + 7, right, y + 7)
    return y


def _draw_eza_totals_and_timbre(c, data, qr_y=367, qr_size=93, totals_y=471, draw_words=True):
    qr_data = (
        "https://verificacfdi.facturaelectronica.sat.gob.mx/default.aspx"
        f"?id={data['uuid']}&re={data['emisor_rfc']}&rr={data['receptor_rfc']}&tt={data['total']}"
    )
    _draw_qr(c, 35, qr_y, qr_size, qr_data)
    if data.get("es_complemento_pago"):
        monto = data.get("monto_total_pagos") or "0"
        c.setFont(FONT_B, 8.5)
        c.drawString(388, totals_y, "Monto recibido (REP 2.0)")
        c.drawRightString(584, totals_y, _fmt_money(monto))
        c.line(343, totals_y - 6, 584, totals_y - 6)
        if draw_words:
            c.setFont(FONT_B, 8)
            c.drawCentredString(452, totals_y - 34, _numero_a_letras(monto))
    else:
        c.setFont(FONT, 8)
        c.drawString(388, totals_y, "Subtotal")
        c.drawRightString(584, totals_y, _fmt_money(data["subtotal"]))
        y = totals_y - 14
        c.drawString(388, y, "Descuento")
        c.drawRightString(584, y, _fmt_money(data["descuento"]))
        y -= 14
        c.drawString(388, y, "I.V.A.")
        c.drawRightString(584, y, _fmt_money(data["iva"]))
        y -= 14
        c.line(343, y, 584, y)
        y -= 12
        c.setFont(FONT_B, 8.5)
        c.drawString(388, y, "Total")
        c.drawRightString(584, y, _fmt_money(data["total"]))
        c.line(343, y - 6, 584, y - 6)
        c.setFont(FONT_B, 8)
        if draw_words and y - 34 >= 313:
            c.drawCentredString(452, y - 34, _numero_a_letras(data["total"]))

    box_x, box_y, box_w, box_h = 31, 86, 560, 219
    c.rect(box_x, box_y, box_w, box_h, stroke=1, fill=0)
    c.setFont(FONT, 8)
    c.drawString(box_x + 8, box_y + box_h - 15, '"Este documento es una representacion impresa de un CFDI"')
    y = box_y + box_h - 35
    _draw_label(c, "Folio fiscal:", data["uuid"], box_x + 4, y, 48, 480, 6.8)
    y -= 15
    _draw_label(c, "Fecha y hora de certificacion:", data.get("fecha_timbrado_iso") or data["fecha_timbrado"].replace(" ", "T"), box_x + 4, y, 118, 220, 6.8)
    y -= 15
    c.setFont(FONT_B, 6.8)
    c.drawString(box_x + 4, y, "Sello digital del CFDI:")
    y -= 9
    y = _draw_wrapped(c, data["sello_cfd"], box_x + 8, y, 136, 3, 7, FONT, 6)
    y -= 8
    c.setFont(FONT_B, 6.5)
    c.drawString(box_x + 4, y, "Numero de serie del Certificado de Sello Digital :")
    c.drawString(box_x + 286, y, "Numero de serie del Certificado de Sello Digital del SAT:")
    c.setFont(FONT, 6.5)
    c.drawString(box_x + 4, y - 10, data["no_certificado"])
    c.drawString(box_x + 286, y - 10, data["no_certificado_sat"])
    y -= 18
    c.setFont(FONT_B, 6.8)
    c.drawString(box_x + 4, y, "Cadena original del complemento de certificacion digital del SAT:")
    y -= 9
    cadena = f"||1.1|{data['uuid']}|{data.get('fecha_timbrado_iso') or data['fecha_timbrado'].replace(' ', 'T')}|{data['rfc_prov']}|{data['sello_cfd']}|{data['no_certificado_sat']}||"
    y = _draw_wrapped(c, cadena, box_x + 8, y, 142, 5, 7, FONT, 6)
    y -= 7
    c.setFont(FONT_B, 6.8)
    c.drawString(box_x + 4, y, "Sello digital del SAT:")
    y -= 9
    _draw_wrapped(c, data["sello_sat"], box_x + 8, y, 142, 3, 7, FONT, 6)


def _generar_eza_pdf(data):
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=letter)
    c.setTitle(f"EZA2007 CFDI {data['folio'] or data['factura']}")
    _draw_eza_header(c, data)
    table_y = _draw_eza_rep_details(c, data) if data.get("es_complemento_pago") else _draw_eza_products(c, data)
    # El QR ocupa de y=367 a y=460. Si la tabla llega a esa zona, los datos
    # fiscales se colocan en una página adicional para no encimarse.
    # Se reduce y desplaza el QR dentro del espacio libre disponible.
    footer_top = table_y - 8
    qr_floor = 313  # el bloque de timbre llega hasta y=305
    qr_size = min(93, footer_top - qr_floor)
    if qr_size < 60:
        c.showPage()
        _draw_eza_header(c, data)
        _draw_eza_totals_and_timbre(c, data)
    else:
        _draw_eza_totals_and_timbre(
            c, data, qr_y=qr_floor, qr_size=qr_size,
            totals_y=footer_top - 6, draw_words=False,
        )
    c.save()
    buf.seek(0)
    return buf


def generar_cfdi_pdf(xml_root, db_row=None, logo_archivo=""):
    data = _cfdi_data(xml_root, db_row or {})
    data["logo_archivo"] = logo_archivo
    if _is_eza(data):
        return _generar_eza_pdf(data)
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=letter)
    c.setTitle(f"CFDI {data['folio'] or data['factura']}")

    _draw_header(c, data)
    table_y = _draw_rep_table(c, data) if data.get("es_complemento_pago") else _draw_product_table(c, data)
    # El QR ocupa de y=300 a y=395; se requiere margen sobre esa área.
    # Si el QR cabe compacto bajo la tabla, se conserva una sola página.
    footer_top = table_y - 8
    qr_floor = 210  # el timbre compacto inicia en y=198
    qr_size = min(95, footer_top - qr_floor)
    if qr_size >= 58:
        _draw_totals(
            c, data, draw_legend=False, qr_y=qr_floor, qr_size=qr_size,
            totals_y=footer_top - 8, draw_words=False,
        )
        _draw_timbre_block(c, data, box_top=198, box_h=166, compact=True)
    else:
        c.showPage()
        _draw_header(c, data)
        _draw_totals(c, data, draw_legend=False)
        _draw_timbre_block(c, data, box_top=198, box_h=166, compact=True)

    c.save()
    buf.seek(0)
    return buf
