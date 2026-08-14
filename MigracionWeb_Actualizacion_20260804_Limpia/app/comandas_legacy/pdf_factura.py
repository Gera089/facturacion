# pdf_factura.py (en tu API)
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from num2words import num2words
import os


def generar_pdf_factura_bytes(payload: dict, logos_dir: str) -> bytes:
    """
    Genera el PDF con EXACTAMENTE el mismo layout que tu FacturacionTab.guardar_factura().
    payload debe traer lo que tú ya armas:
      empresa, cliente_nombre, cliente_numero, vendedor, rfc, subtotal, descuento_pct, descuento_total, iva, total,
      consignatario, numero_salida, fecha_impresa, cliente_info, productos (lista), etc.
    """

    def _money(v):
        try:
            n = float(str(v).replace(",", "").replace("$", "").strip() or 0)
        except Exception:
            n = 0.0
        return f"${n:,.2f}"

    empresa        = (payload.get("empresa") or "").strip()
    emp_low        = empresa.lower()

    cliente_nombre = (payload.get("cliente_nombre") or "").strip()
    cliente_numero = (payload.get("cliente_numero") or payload.get("numero_cliente") or "").strip()
    vendedor       = (payload.get("vendedor") or "").strip()
    rfc_pdf        = (payload.get("rfc") or "").strip()
    consignatario  = (payload.get("consignatario") or cliente_nombre).strip()

    subtotal       = float(payload.get("subtotal") or 0)
    descuento_pct  = float(payload.get("descuento_pct") or 0)
    descuento_total= float(payload.get("descuento_total") or 0)
    iva_total      = float(payload.get("iva") or 0)
    total          = float(payload.get("total") or 0)

    fecha_impresa  = (payload.get("fecha_impresa") or "").strip()  # ej: "4 MAR. 2026"
    folio          = (payload.get("folio") or payload.get("factura") or "-").strip()

    cliente_info   = payload.get("cliente_info") or {}

    # ---------------- Emisor por empresa (MISMO que tu código) ----------------
    if "remision" in emp_low or "remisión" in emp_low:
        direccion = "Texas N°100 - Nápoles - Benito Juárez - CDMX"
        rfc_tel   = "REMISIÓN INTERNA — SIN RFC / TEL. 5555439933"
        logo_path = os.path.join(logos_dir, "remision.png")
    elif "ibersur" in emp_low:
        direccion = "Dakota N°359 Int. 301 - Ampliación Nápoles - Benito Juárez - CDMX"
        rfc_tel   = "RFC IBE 090212 JV1 / TEL. 5555439933"
        logo_path = os.path.join(logos_dir, "ibersur.png")
    elif "eza2007" in emp_low:
        direccion = "Dakota N°359 Int. 301 - Ampliación Nápoles - Benito Juárez - CDMX"
        rfc_tel   = "RFC EZA 070521 MT4 / TEL. 5555439933"
        logo_path = os.path.join(logos_dir, "eza2007.png")
    elif "gourmet" in emp_low:
        direccion = "Texas N°100 - Nápoles - Benito Juárez - CDMX"
        rfc_tel   = "RFC GES 090312 DJ1 / TEL. 5555439933"
        logo_path = os.path.join(logos_dir, "gourmet.png")
    else:
        direccion = "Dirección no definida"
        rfc_tel   = ""
        logo_path = os.path.join(logos_dir, "default.png")

    # La selección de Soporte tiene prioridad sobre la regla heredada por empresa.
    logo_configurado = str(payload.get("logo_path") or "").strip()
    if logo_configurado and os.path.isfile(logo_configurado):
        logo_path = logo_configurado

    # ---------------- PDF ----------------
    buffer_pdf = BytesIO()
    doc = SimpleDocTemplate(
        buffer_pdf,
        pagesize=letter,
        rightMargin=30, leftMargin=30,
        topMargin=20, bottomMargin=20
    )
    styles = getSampleStyleSheet()
    elements = []

    # Logo + info empresa
    logo = Image(logo_path, width=100, height=80) if os.path.exists(logo_path) else Spacer(1, 60)
    info_empresa = f"<b>{empresa.upper()}</b><br/>{direccion}<br/>{rfc_tel}"
    encabezado_table = Table(
        [[logo, Paragraph(info_empresa, ParagraphStyle(name="info_empresa", fontSize=8, leftIndent=20))]],
        colWidths=[120, 420]
    )
    encabezado_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (1, 0), (1, 0), 15),
    ]))
    elements.append(encabezado_table)
    elements.append(Spacer(1, 6))

    estilo_ajustado = ParagraphStyle(
        name="ajustado",
        fontName="Helvetica",
        fontSize=8,
        leading=9,
        spaceBefore=0,
        spaceAfter=0,
        alignment=0
    )
    def celda(v): 
        return Paragraph(str(v or ""), estilo_ajustado)

    # Cliente / Consignatario (igual)
    cliente_data = [
        ["Cliente:",    celda(cliente_info.get("razon_social", cliente_nombre))],
        ["RFC:",        celda(rfc_pdf)],
        ["Calle:",      celda(f"{cliente_info.get('calle', '')} {cliente_info.get('no_exterior', '')} {cliente_info.get('no_interior', '')}")],
        ["Colonia:",    celda(cliente_info.get("colonia", ""))],
        ["Delegación:", celda(cliente_info.get("alcaldia", cliente_info.get("municipio", "")))],
        ["Población:",  celda(f"{cliente_info.get('poblacion', '')} C.P. {cliente_info.get('codigo_postal', '')}")],
        ["Estado:",     celda(cliente_info.get("estado", ""))],
    ]
    cliente_table = Table(cliente_data, colWidths=[60, 260])

    consignatario_data = [
        ["Consignatario:", celda(consignatario)],
        ["Calle:",         celda(f"{cliente_info.get('consig_calle', '')} {cliente_info.get('consig_no_exterior', '')} {cliente_info.get('consig_no_interior', '')}")],
        ["Colonia:",       celda(cliente_info.get("consig_colonia", ""))],
        ["Delegación:",    celda(cliente_info.get("consig_delegacion", cliente_info.get("consig_municipio", "")))],
        ["Población:",     celda(f"{cliente_info.get('consig_poblacion', '')} C.P. {cliente_info.get('consig_codigo_postal', '')}")],
        ["Estado:",        celda(cliente_info.get("consig_estado", ""))],
    ]
    consignatario_table = Table(consignatario_data, colWidths=[60, 260])

    for t in (cliente_table, consignatario_table):
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))

    # Folio derecha
    elements.append(Paragraph(
        f"<para alignment='right'><b>FOLIO: {folio}</b></para>",
        ParagraphStyle(name="folio_style", fontSize=10, alignment=2)
    ))
    elements.append(Spacer(1, 2))

    cliente_consig = Table([[cliente_table, consignatario_table]], colWidths=[270, 270])
    cliente_consig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(cliente_consig)
    elements.append(Spacer(1, 6))

    elements.append(Table([[""]], colWidths=[540], style=[("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black)]))
    elements.append(Spacer(1, 1))

    dias_credito = cliente_info.get("dias_credito")
    pago_text = "-" if not dias_credito else f"{dias_credito} días"
    no_proveedor = cliente_info.get("no_proveedor", "-")

    enc_data = [
        ["Ubicación", "Fecha", "Pago", "N° Proveedor", "Cliente N°", "Vendedor"],
        ["MEXICO DF", fecha_impresa, pago_text, no_proveedor, cliente_numero, vendedor],
    ]
    enc_table = Table(enc_data, colWidths=[100, 90, 80, 90, 90, 90])
    enc_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(enc_table)
    elements.append(Table([[""]], colWidths=[540], style=[("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black)]))
    elements.append(Spacer(1, 5))

    # -------- Productos (misma rama gourmet) --------
    data = []
    gourmet_es = ("gourmet españa" in emp_low) or ("gourmet espana" in emp_low)

    if gourmet_es:
        headers = ["Cantidad", "Unidad", "CIP", "Descripción", "Código", "Piezas", "Precio", "Descuento", "Total"]
        col_widths = [45, 38, 32, 142, 62, 35, 43, 51, 52]
    else:
        headers = ["Cantidad", "Unidad", "CIP", "Descripción", "Piezas", "Precio", "Descuento", "Total"]
        col_widths = [50, 42, 38, 185, 43, 45, 62, 75]

    data.append(headers)

    for p in (payload.get("productos") or []):
        cip = (p.get("cip") or "").strip()
        desc = (p.get("descripcion") or "").strip()
        cantidad = p.get("cantidad") or ""
        piezas = p.get("piezas") or ""
        precio = _money(p.get("precio") or 0)
        total_linea = _money(p.get("total") or (float(p.get("cantidad") or 0) * float(p.get("precio") or 0)))
        descuento_linea = f"{float(p.get('descuento_pct') or 0):.2f}%"

        unidad_pdf = (p.get("unidad") or "").strip().upper()
        codigo_o_cip = (p.get("codigo_barras") or "").strip() or "-"

        desc_par = Paragraph(desc, ParagraphStyle(
            name="desc", fontName="Helvetica", fontSize=8, leading=9, alignment=0
        ))

        if gourmet_es:
            fila = [str(cantidad), unidad_pdf, cip, desc_par, codigo_o_cip, str(piezas), precio, descuento_linea, total_linea]
        else:
            fila = [str(cantidad), unidad_pdf, cip, desc_par, str(piezas), precio, descuento_linea, total_linea]

        data.append(fila)

    tabla_prod = Table(data, repeatRows=1, colWidths=col_widths)

    style = [
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("ALIGN", (0, 1), (-1, -1), "CENTER"),
        ("ALIGN", (3, 0), (3, -1), "LEFT"),
        ("LEFTPADDING", (3, 1), (3, -1), 5),
        ("RIGHTPADDING", (3, 1), (3, -1), 5),
    ]
    style.append(("FONTSIZE", (0, 1), (0, -1), 10))  # Cantidad
    style.append(("FONTSIZE", (2, 1), (2, -1), 10))  # CIP

    if gourmet_es:
        for col in (4, 5, 6, 7, 8):
            style.append(("FONTSIZE", (col, 1), (col, -1), 9))
    else:
        for col in (4, 5, 6, 7):
            style.append(("FONTSIZE", (col, 1), (col, -1), 9))

    tabla_prod.setStyle(TableStyle(style))
    elements.append(tabla_prod)
    elements.append(Spacer(1, 10))

    # Totales
    totales_data = [
        ["", "", "", "", "SUBTOTAL",      f"${subtotal:,.2f}"],
        ["", "", "", "", "DESCUENTO",     f"-${descuento_total:,.2f}"],
        ["", "", "", "", "I.V.A.",        f"${iva_total:,.2f}"],
        ["", "", "", "", "GRAN TOTAL",    f"${total:,.2f}"],
    ]
    tabla_totales = Table(totales_data, colWidths=[60, 60, 100, 120, 100, 80])
    tabla_totales.setStyle(TableStyle([
        ("ALIGN", (-2, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (-2, 0), (-1, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(tabla_totales)
    elements.append(Spacer(1, 10))

    total_letra = num2words(int(total), lang="es").upper().replace("EUROS", "").strip()
    decimales = int(round((total % 1) * 100))
    elements.append(Paragraph(
        f"{total_letra} PESOS {decimales:02d}/00 M.N.",
        ParagraphStyle(name="total_letra", fontSize=10, alignment=0)
    ))

    doc.build(elements)
    buffer_pdf.seek(0)
    return buffer_pdf.read()
