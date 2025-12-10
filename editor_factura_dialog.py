from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QPushButton, QComboBox, QMessageBox
)
from PyQt5.QtCore import Qt
import requests
from cliente import API_URL


class EditorFacturaDialog(QDialog):
    def __init__(self, factura):
        super().__init__()
        self.setWindowTitle(f"Editar Factura {factura.get('factura')}")
        self.resize(950, 720)

        self.factura = factura
        layout = QVBoxLayout(self)

        # =============================
        # ENCABEZADO
        # =============================
        form = QHBoxLayout()

        self.input_folio = QLineEdit(factura.get("factura", ""))
        self.input_folio.setEnabled(False)

        self.input_cliente = QLineEdit(factura.get("numero_cliente", ""))
        self.input_nombre = QLineEdit(factura.get("cliente_nombre", ""))
        self.input_rfc = QLineEdit(factura.get("rfc", ""))
        self.input_vendedor = QLineEdit(factura.get("vendedor", ""))
        self.input_consignatario = QLineEdit(factura.get("consignatario", ""))

        self.combo_empresa = QComboBox()
        self.combo_empresa.addItems(["Gourmet España", "Ibersur", "EZA2007"])
        self.combo_empresa.setCurrentText(factura.get("empresa", ""))

        for label, widget in [
            ("Folio", self.input_folio),
            ("Cliente", self.input_cliente),
            ("Nombre", self.input_nombre),
            ("RFC", self.input_rfc),
            ("Vendedor", self.input_vendedor),
            ("Tienda", self.input_consignatario),
            ("Empresa", self.combo_empresa),
        ]:
            form.addWidget(QLabel(label))
            form.addWidget(widget)

        layout.addLayout(form)

        # =============================
        # TABLA PRODUCTOS
        # =============================
        self.tabla = QTableWidget(15, 6)
        self.tabla.setHorizontalHeaderLabels([
            "CIP", "Descripción", "Cantidad", "Piezas", "Precio", "Importe"
        ])
        layout.addWidget(self.tabla)

        self.cargar_productos()

        # Vincular recálculo fila a cambios en tabla
        self.tabla.itemChanged.connect(self.recalcular_fila)

        # =============================
        # TOTALES
        # =============================
        tot = QHBoxLayout()

        self.input_subtotal = QLineEdit(str(factura.get("subtotal", 0)))
        self.input_descuento_pct = QLineEdit(str(factura.get("descuento_pct", 0)))
        self.input_descuento_total = QLineEdit(str(factura.get("descuento_total", 0)))
        self.input_iva = QLineEdit(str(factura.get("iva", 0)))
        self.input_total = QLineEdit(str(factura.get("total", 0)))

        for widget in [
            self.input_subtotal, self.input_descuento_pct,
            self.input_descuento_total, self.input_iva, self.input_total
        ]:
            widget.setFixedWidth(90)

        for label, widget in [
            ("Subtotal", self.input_subtotal),
            ("Desc %", self.input_descuento_pct),
            ("Desc Total", self.input_descuento_total),
            ("IVA", self.input_iva),
            ("Total", self.input_total),
        ]:
            tot.addWidget(QLabel(label))
            tot.addWidget(widget)

        layout.addLayout(tot)

        # Vincular recálculo total a cambios en totales
        self.input_descuento_pct.textChanged.connect(self.recalcular_totales)
        self.input_descuento_total.textChanged.connect(self.recalcular_totales)
        self.input_iva.textChanged.connect(self.recalcular_totales)

        # =============================
        # BOTONES
        # =============================
        btns = QHBoxLayout()
        btn_guardar = QPushButton("💾 Guardar cambios")
        btn_cancelar = QPushButton("Cancelar")

        btn_guardar.clicked.connect(self.guardar_cambios)
        btn_cancelar.clicked.connect(self.reject)

        btns.addWidget(btn_guardar)
        btns.addWidget(btn_cancelar)
        layout.addLayout(btns)

    # ==================================
    # Cargar productos existentes
    # ==================================
    def cargar_productos(self):
        productos = self.factura.get("productos", [])
        fila = 0
        for p in productos:
            self.tabla.setItem(fila, 0, QTableWidgetItem(str(p.get("cip", ""))))
            self.tabla.setItem(fila, 1, QTableWidgetItem(str(p.get("descripcion", ""))))
            self.tabla.setItem(fila, 2, QTableWidgetItem(str(p.get("cantidad", ""))))
            self.tabla.setItem(fila, 3, QTableWidgetItem(str(p.get("piezas", ""))))
            self.tabla.setItem(fila, 4, QTableWidgetItem(str(p.get("precio", ""))))

            try:
                cantidad = float(p.get("cantidad", 0))
                precio = float(p.get("precio", 0))
                importe = cantidad * precio
            except:
                importe = 0

            self.tabla.setItem(fila, 5, QTableWidgetItem(f"{importe:.2f}"))
            fila += 1

        self.recalcular_totales()

    # ==================================
    # Recalcular importe por fila
    # ==================================
    def recalcular_fila(self, item):
        fila = item.row()
        col = item.column()

        if col not in (2, 4):  # Cantidad o Precio
            return

        try:
            cantidad = float(self.tabla.item(fila, 2).text())
        except:
            cantidad = 0

        try:
            precio = float(self.tabla.item(fila, 4).text())
        except:
            precio = 0

        importe = cantidad * precio
        self.tabla.blockSignals(True)
        self.tabla.setItem(fila, 5, QTableWidgetItem(f"{importe:.2f}"))
        self.tabla.blockSignals(False)

        self.recalcular_totales()

    # ==================================
    # Recalcular totales completos
    # ==================================
    def recalcular_totales(self):
        subtotal = 0

        # Sumar importes de filas
        for fila in range(15):
            item = self.tabla.item(fila, 5)
            if not item:
                continue
            try:
                subtotal += float(item.text())
            except:
                pass

        try:
            desc_pct = float(self.input_descuento_pct.text())
        except:
            desc_pct = 0

        try:
            desc_total = float(self.input_descuento_total.text())
        except:
            desc_total = 0

        try:
            iva = float(self.input_iva.text())
        except:
            iva = 0

        # Cálculos
        if desc_pct > 0:
            desc_total = subtotal * (desc_pct / 100)

        subtotal_after_discount = subtotal - desc_total
        total = subtotal_after_discount + iva

        # Actualizar GUI
        self.input_subtotal.setText(f"{subtotal:.2f}")
        self.input_descuento_total.setText(f"{desc_total:.2f}")
        self.input_total.setText(f"{total:.2f}")

    # ==================================
    # Guardar cambios al backend
    # ==================================
    def guardar_cambios(self):
        try:
            payload = {
                "cliente_numero": self.input_cliente.text(),
                "cliente_nombre": self.input_nombre.text(),
                "consignatario": self.input_consignatario.text(),
                "empresa": self.combo_empresa.currentText(),
                "rfc": self.input_rfc.text(),
                "vendedor": self.input_vendedor.text(),
                "subtotal": float(self.input_subtotal.text()),
                "descuento_pct": float(self.input_descuento_pct.text()),
                "descuento_total": float(self.input_descuento_total.text()),
                "iva": float(self.input_iva.text()),
                "total": float(self.input_total.text()),
                "productos": []
            }

            for fila in range(15):
                cip = self.tabla.item(fila, 0)
                desc = self.tabla.item(fila, 1)
                if not cip or not desc:
                    continue

                payload["productos"].append({
                    "cip": cip.text(),
                    "descripcion": desc.text(),
                    "cantidad": self.tabla.item(fila, 2).text(),
                    "piezas": self.tabla.item(fila, 3).text(),
                    "precio": self.tabla.item(fila, 4).text(),
                })

            folio = self.input_folio.text()

            resp = requests.put(f"{API_URL}/facturas/folio/{folio}", json=payload)
            if resp.status_code != 200:
                QMessageBox.warning(self, "Error", resp.text)
                return

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))