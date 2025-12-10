import sys, json, os
from io import BytesIO
import requests, time
import subprocess
import pandas as pd
import qtawesome as qta
from PyQt5.QtWidgets import *
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QLabel,
    QLineEdit, QTabWidget, QTableWidget, QTableWidgetItem, QHBoxLayout, QMessageBox, QInputDialog, QDesktopWidget, QTextEdit, QTextEdit, QDialog, QFormLayout, QScrollArea, QHeaderView, QCompleter, QComboBox, QAbstractScrollArea, QAbstractItemView
)
from PyQt5.QtCore import Qt, QEvent, QTimer, QSize, QPoint
from PyQt5.QtGui import QFont, QIcon, QPixmap, QPainter, QColor, QBrush
from PyQt5.QtWidgets import QSizePolicy, QGraphicsDropShadowEffect, QMainWindow, QAction, QMenu, QDateEdit, QAbstractItemView, QSpacerItem
# =====================================================
# 🔹 Worker en segundo plano para evitar bloqueos GUI
# =====================================================
from PyQt5.QtCore import QObject, QThread, pyqtSignal, QDate
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtCore import QUrl, Qt
# --- Librerías para generar el PDF de la factura ---
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from num2words import num2words
from tempfile import NamedTemporaryFile
from PyQt5.QtPrintSupport import QPrinter, QPrinterInfo, QPrintDialog
import win32api
import subprocess
import win32print
import fitz  # PyMuPDF para leer el PDF página por página
import io
from datetime import datetime
from PyQt5.QtWidgets import QGraphicsDropShadowEffect
from detalle_cliente import DetalleClienteWidget
import numpy as np


class NetworkWorker(QObject):
    finished = pyqtSignal(object, object)  # resultado, error
    def __init__(self, method, url, data=None):
        super().__init__()
        self.method = method
        self.url = url
        self.data = data

    def run(self):
        import requests
        try:
            if self.method == "GET":
                r = requests.get(self.url)
            elif self.method == "POST":
                r = requests.post(self.url, json=self.data)
            elif self.method == "PUT":
                r = requests.put(self.url, json=self.data)
            elif self.method == "DELETE":
                r = requests.delete(self.url)
            else:
                raise ValueError("Método HTTP no soportado")
            self.finished.emit(r, None)
        except Exception as e:
            self.finished.emit(None, e)

API_URL = "http://192.168.1.105:8000"  # URL de tu API FastAPI

# Archivo donde se guarda la última impresora usada
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config_impresion.json")

# Posibles rutas de instalación de Ghostscript (64 bits)
GHOSTSCRIPT_PATHS = [
    r"C:\Program Files\gs\gs10.06.0\bin\gswin64c.exe",  # 🔹 Nueva versión agregada
    r"C:\Program Files\gs\gs10.03.1\bin\gswin64c.exe",
    r"C:\Program Files\gs\gs10.02.1\bin\gswin64c.exe",
    r"C:\Program Files\gs\gs9.56.1\bin\gswin64c.exe",
]

# Detección automática de Ghostscript
GHOSTSCRIPT = next((p for p in GHOSTSCRIPT_PATHS if os.path.exists(p)), None)

if not GHOSTSCRIPT:
    print("⚠️ Ghostscript no encontrado. Instálalo desde https://ghostscript.com/releases/gsdnld.html")
else:
    print(f"✅ Ghostscript detectado en: {GHOSTSCRIPT}")

from PyQt5.QtWidgets import QFileDialog

def aplicar_estilo_global(botones):
    """
    Aplica cursor de mano y efecto de sombra a todos los botones pasados.
    """
    for btn in botones:
        # Cursor tipo mano
        btn.setCursor(Qt.PointingHandCursor)

        # Efecto sombra estilo Material Design
        sombra = QGraphicsDropShadowEffect()
        sombra.setBlurRadius(12)         # suavidad
        sombra.setOffset(0, 2)           # desplazamiento sutil
        sombra.setColor(QColor(0, 0, 0, 60))  # sombra gris con transparencia
        btn.setGraphicsEffect(sombra)
# === Función para formatear valores numéricos como moneda ===
def formatear_moneda(valor):
    """Convierte texto o número en formato $#,###.##"""
    try:
        valor = str(valor).replace("$", "").replace(",", "").strip()
        if valor == "":
            return "$0.00"
        num = float(valor)
        return "${:,.2f}".format(num)
    except:
        return "$0.00"
# ===============================
# 🔹 VENTANAS DE DIÁLOGO (Cliente / Producto)
# ===============================
class AgregarClienteDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Agregar Cliente")
        self.resize(600, 700)
        self.setModal(True)

        # --- Layout principal con scroll ---
        main_layout = QVBoxLayout(self)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        form_layout = QFormLayout(scroll_content)
        form_layout.setContentsMargins(20, 20, 20, 20)
        form_layout.setSpacing(10)

        # === DATOS GENERALES ===
        form_layout.addRow(QLabel("<h4>Datos Generales</h4>"))
        self.input_numero = QLineEdit()
        self.input_nombre = QLineEdit()
        self.input_empresa = QLineEdit()
        self.input_razon_social = QLineEdit()
        self.input_rfc = QLineEdit()
        self.input_telefono = QLineEdit()
        self.input_correo = QLineEdit()
        self.input_contacto1 = QLineEdit()
        self.input_contacto2 = QLineEdit()

        form_layout.addRow("Número:", self.input_numero)
        form_layout.addRow("Nombre:", self.input_nombre)
        form_layout.addRow("Empresa:", self.input_empresa)
        form_layout.addRow("Razón Social:", self.input_razon_social)
        form_layout.addRow("RFC:", self.input_rfc)
        form_layout.addRow("Teléfono:", self.input_telefono)
        form_layout.addRow("Correo Electrónico:", self.input_correo)
        form_layout.addRow("Contacto 1:", self.input_contacto1)
        form_layout.addRow("Contacto 2:", self.input_contacto2)

        # === DIRECCIÓN FISCAL ===
        form_layout.addRow(QLabel("<h4>Dirección Fiscal</h4>"))
        self.input_calle = QLineEdit()
        self.input_no_exterior = QLineEdit()
        self.input_no_interior = QLineEdit()
        self.input_colonia = QLineEdit()
        self.input_alcaldia = QLineEdit()
        self.input_municipio = QLineEdit()
        self.input_poblacion = QLineEdit()
        self.input_estado = QLineEdit()
        self.input_pais = QLineEdit()
        self.input_codigo_postal = QLineEdit()

        form_layout.addRow("Calle:", self.input_calle)
        form_layout.addRow("No. Exterior:", self.input_no_exterior)
        form_layout.addRow("No. Interior:", self.input_no_interior)
        form_layout.addRow("Colonia:", self.input_colonia)
        form_layout.addRow("Alcaldía:", self.input_alcaldia)
        form_layout.addRow("Municipio:", self.input_municipio)
        form_layout.addRow("Población:", self.input_poblacion)
        form_layout.addRow("Estado:", self.input_estado)
        form_layout.addRow("País:", self.input_pais)
        form_layout.addRow("Código Postal:", self.input_codigo_postal)

        # === DIRECCIÓN CONSIGNATARIO ===
        form_layout.addRow(QLabel("<h4>Dirección Consignatario</h4>"))
        self.input_consignatario = QLineEdit()
        self.input_consig_calle = QLineEdit()
        self.input_consig_no_exterior = QLineEdit()
        self.input_consig_no_interior = QLineEdit()
        self.input_consig_colonia = QLineEdit()
        self.input_consig_delegacion = QLineEdit()
        self.input_consig_municipio = QLineEdit()
        self.input_consig_codigo_postal = QLineEdit()
        self.input_consig_poblacion = QLineEdit()
        self.input_consig_estado = QLineEdit()
        self.input_consig_pais = QLineEdit()

        form_layout.addRow("Consignatario:", self.input_consignatario)
        form_layout.addRow("Calle:", self.input_consig_calle)
        form_layout.addRow("No. Exterior:", self.input_consig_no_exterior)
        form_layout.addRow("No. Interior:", self.input_consig_no_interior)
        form_layout.addRow("Colonia:", self.input_consig_colonia)
        form_layout.addRow("Delegación:", self.input_consig_delegacion)
        form_layout.addRow("Municipio:", self.input_consig_municipio)
        form_layout.addRow("Código Postal:", self.input_consig_codigo_postal)
        form_layout.addRow("Población:", self.input_consig_poblacion)
        form_layout.addRow("Estado:", self.input_consig_estado)
        form_layout.addRow("País:", self.input_consig_pais)

        # === INFORMACIÓN COMERCIAL ===
        form_layout.addRow(QLabel("<h4>Información Comercial</h4>"))
        self.input_dias_credito = QSpinBox()
        self.input_dias_credito.setRange(0, 365)
        self.input_vendedor = QLineEdit()
        self.input_zona = QLineEdit()
        self.input_agente = QLineEdit()
        self.input_tipo = QLineEdit()
        self.input_no_proveedor = QLineEdit()
        self.input_descuento = QDoubleSpinBox()
        self.input_descuento.setRange(0, 100)
        self.input_descuento.setSuffix(" %")

        # 🔹 Campo "especial" -> menú desplegable de listas de precios
        self.input_especial = QComboBox()
        self.input_especial.addItem("Cargando listas...")  # valor temporal
        self.cargar_listas_precios()
        

        form_layout.addRow("Días de Crédito:", self.input_dias_credito)
        form_layout.addRow("Vendedor:", self.input_vendedor)
        form_layout.addRow("Zona:", self.input_zona)
        form_layout.addRow("Agente:", self.input_agente)
        form_layout.addRow("Tipo:", self.input_tipo)
        form_layout.addRow("Lista de Precios (Especial):", self.input_especial)
        form_layout.addRow("Descuento (IM):", self.input_descuento)
        form_layout.addRow("No. Proveedor:", self.input_no_proveedor)

        # === CAMPOS ADICIONALES ===
        form_layout.addRow(QLabel("<h4>Datos Adicionales</h4>"))
        self.input_direccion_entrega = QLineEdit()
        self.input_observaciones = QTextEdit()
        self.input_observaciones.setFixedHeight(70)

        form_layout.addRow("Dirección de Entrega:", self.input_direccion_entrega)
        form_layout.addRow("Observaciones:", self.input_observaciones)

        # === SCROLL ===
        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

        # === BOTONES INFERIORES ===
        botones = QHBoxLayout()
        self.btn_guardar = QPushButton(" Guardar")
        self.btn_guardar.setIcon(qta.icon("mdi.content-save"))
        self.btn_cancelar = QPushButton(" Cancelar")
        self.btn_cancelar.setIcon(qta.icon("mdi.close"))
        for b in (self.btn_guardar, self.btn_cancelar):
            b.setFixedWidth(130)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 6px;
                }
                QPushButton:hover { background-color: #45a049; }
            """)
        self.btn_cancelar.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px;
            }
            QPushButton:hover { background-color: #e53935; }
        """)

        botones.addStretch()
        botones.addWidget(self.btn_guardar)
        botones.addWidget(self.btn_cancelar)
        main_layout.addLayout(botones)

        # === CONEXIONES ===
        self.btn_cancelar.clicked.connect(self.reject)
        self.btn_guardar.clicked.connect(self.guardar_cliente)

        # === ESTILO GENERAL ===
        self.setStyleSheet("""
            QDialog {
                background-color: #f4f7fb;
                font-family: 'Segoe UI';
                font-size: 10pt;
            }
            QLabel {
                color: #2c3e50;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit {
                background-color: #ffffff;
                border: 1px solid #a6b5c6;
                border-radius: 5px;
                padding: 4px 6px;
            }
            QLineEdit:focus, QTextEdit:focus {
                border: 1px solid #4a90e2;
                background-color: #f8fbff;
            }
        """)
    
    # ✅ MÉTODO DE LA CLASE (mismo nivel que __init__)
    def cargar_listas_precios(self):
        """Carga las listas de precios desde la API para el campo 'especial'."""
        try:
            resp = requests.get(f"{API_URL}/precios/listas_precios/")
            if resp.status_code == 200:
                listas = resp.json()
                self.input_especial.clear()
                for lista in listas:
                    self.input_especial.addItem(lista["nombre"], lista["id"])
            else:
                self.input_especial.clear()
                self.input_especial.addItem("No disponible")
        except Exception as e:
            print("Error al cargar listas de precios:", e)
            self.input_especial.clear()
            self.input_especial.addItem("Error al conectar")

    def guardar_cliente(self):
        """Envía los datos del cliente al servidor usando la API."""
        try:
            # --- Datos generales ---
            data = {
                "numero": self.input_numero.text().strip(),
                "nombre": self.input_nombre.text().strip(),
                "empresa": self.input_empresa.text().strip(),
                "razon_social": self.input_razon_social.text().strip(),
                "rfc": self.input_rfc.text().strip(),
                "telefono": self.input_telefono.text().strip(),
                "correo_electronico": self.input_correo.text().strip(),
                "contacto1": self.input_contacto1.text().strip(),
                "contacto2": self.input_contacto2.text().strip(),

                # --- Dirección fiscal ---
                "calle": self.input_calle.text().strip(),
                "no_exterior": self.input_no_exterior.text().strip(),
                "no_interior": self.input_no_interior.text().strip(),
                "colonia": self.input_colonia.text().strip(),
                "alcaldia": self.input_alcaldia.text().strip(),
                "municipio": self.input_municipio.text().strip(),
                "poblacion": self.input_poblacion.text().strip(),
                "estado": self.input_estado.text().strip(),
                "pais": self.input_pais.text().strip(),
                "codigo_postal": self.input_codigo_postal.text().strip(),

                # --- Dirección consignatario ---
                "consignatario": self.input_consignatario.text().strip(),
                "consig_calle": self.input_consig_calle.text().strip(),
                "consig_no_exterior": self.input_consig_no_exterior.text().strip(),
                "consig_no_interior": self.input_consig_no_interior.text().strip(),
                "consig_colonia": self.input_consig_colonia.text().strip(),
                "consig_delegacion": self.input_consig_delegacion.text().strip(),
                "consig_municipio": self.input_consig_municipio.text().strip(),
                "consig_codigo_postal": self.input_consig_codigo_postal.text().strip(),
                "consig_poblacion": self.input_consig_poblacion.text().strip(),
                "consig_estado": self.input_consig_estado.text().strip(),
                "consig_pais": self.input_consig_pais.text().strip(),

                # --- Información comercial ---
                "dias_credito": self.input_dias_credito.value(),
                "vendedor": self.input_vendedor.text().strip(),
                "zona": self.input_zona.text().strip(),
                "agente": self.input_agente.text().strip(),
                "tipo": self.input_tipo.text().strip(),
                "no_proveedor": self.input_no_proveedor.text().strip(),
                "descuento": self.input_descuento.value(),
                "direccion_entrega": self.input_direccion_entrega.text().strip(),
                "observaciones": self.input_observaciones.toPlainText().strip(),
            }

            # --- Lista de precios (especial) ---
            idx = self.input_especial.currentIndex()
            lista_id = self.input_especial.itemData(idx)
            lista_nombre = self.input_especial.currentText()

            # Guarda tanto el id como el nombre
            data["especial_id"] = lista_id
            data["especial"] = lista_nombre

            # --- Validación mínima ---
            if not data["nombre"]:
                QMessageBox.warning(self, "Campo obligatorio", "El nombre del cliente es obligatorio.")
                return

            # --- Enviar a la API ---
            resp = requests.post(f"{API_URL}/clientes/nuevo", json=data)

            if resp.status_code == 200:
                QMessageBox.information(self, "Éxito", "Cliente agregado correctamente.")
                self.accept()
            else:
                QMessageBox.warning(self, "Error", f"No se pudo guardar el cliente:\n{resp.text}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Ocurrió un error al guardar el cliente:\n{str(e)}")

# ===============================
# 🔹 VENTANA DE DIÁLOGO: Agregar Producto
# ===============================
class AgregarProductoDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Agregar Producto")
        self.resize(550, 680)
        self.setModal(True)

        # --- Layout principal con scroll ---
        main_layout = QVBoxLayout(self)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        form_layout = QFormLayout(scroll_content)
        form_layout.setContentsMargins(20, 20, 20, 20)
        form_layout.setSpacing(10)

        # --- Campos base ---
        self.input_cip = QLineEdit()
        self.input_descripcion = QLineEdit()
        self.input_unidad = QLineEdit()

        form_layout.addRow("CIP:", self.input_cip)
        form_layout.addRow("Descripción:", self.input_descripcion)
        form_layout.addRow("Unidad:", self.input_unidad)

        # --- Tipo de lista ---
        self.combo_tipo_lista = QComboBox()
        self.combo_tipo_lista.addItems(["Estándar", "Gourmet"])
        form_layout.addRow("Tipo de lista:", self.combo_tipo_lista)

        # --- 🔹 Nuevo campo: I.V.A. ---
        self.combo_iva = QComboBox()
        self.combo_iva.addItems(["Sí", "No"])
        self.combo_iva.setCurrentIndex(1)  # Por defecto "No"
        self.combo_iva.setStyleSheet("""
            QComboBox {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 2px 6px;
            }
            QComboBox:hover {
                border: 1px solid #4CAF50;
            }
        """)
        form_layout.addRow("I.V.A.:", self.combo_iva)

        # --- Sección de listas de precios ---
        form_layout.addRow(QLabel("<b>Listas de precios:</b>"))

        # === Campos dinámicos de precios por lista ===
        from PyQt5.QtGui import QDoubleValidator
        validator = QDoubleValidator(0.00, 9999999.99, 2)
        self.campos_precios = []

        try:
            resp = requests.get(f"{API_URL}/precios/listas_precios/")
            if resp.status_code == 200:
                listas = resp.json()
                if listas:
                    for lista in listas:
                        campo = QLineEdit()
                        campo.setAlignment(Qt.AlignRight)
                        campo.setPlaceholderText("0.00")
                        campo.setStyleSheet("""
                            QLineEdit {
                                padding: 4px 6px;
                                border: 1px solid #a6b5c6;
                                border-radius: 4px;
                                background-color: #ffffff;
                            }
                            QLineEdit:focus {
                                border: 1px solid #4a90e2;
                                background-color: #f8fbff;
                            }
                        """)
                        campo.setValidator(validator)
                        form_layout.addRow(f"{lista['nombre']}:", campo)
                        self.campos_precios.append((lista["id"], campo))
                else:
                    form_layout.addRow(QLabel("No hay listas de precios registradas."))
            else:
                form_layout.addRow(QLabel("⚠️ No se pudieron cargar las listas de precios."))
        except Exception as e:
            form_layout.addRow(QLabel(f"❌ Error al conectar con el servidor: {e}"))

        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

        # --- Botones ---
        botones = QHBoxLayout()
        self.btn_guardar = QPushButton("Guardar")
        self.btn_guardar.setIcon(qta.icon("mdi.content-save"))
        self.btn_cancelar = QPushButton("Cancelar")
        self.btn_cancelar.setIcon(qta.icon("mdi.close-circle"))
        botones.addStretch()
        botones.addWidget(self.btn_guardar)
        botones.addWidget(self.btn_cancelar)
        main_layout.addLayout(botones)

        # --- Eventos ---
        self.btn_cancelar.clicked.connect(self.reject)
        self.btn_guardar.clicked.connect(self.guardar_producto)

    # =====================================================
    # 🔹 Guardar producto en el servidor
    # =====================================================
    def guardar_producto(self):
        cip = self.input_cip.text().strip()
        descripcion = self.input_descripcion.text().strip()
        unidad = self.input_unidad.text().strip()

        if not cip or not descripcion or not unidad:
            QMessageBox.warning(self, "Campos requeridos", "Por favor completa CIP, descripción y unidad.")
            return

        # --- Preparar datos base ---
        data = {
            "cip": cip,
            "descripcion": descripcion,
            "unidad": unidad,
            "tipo_lista": self.combo_tipo_lista.currentText(),
            "iva": self.combo_iva.currentText(),  # ✅ Se envía al backend
            "precios": {},
        }

        # --- Recorrer precios capturados ---
        for lista_id, campo in self.campos_precios:
            texto = campo.text().replace("$", "").replace(",", "").strip()
            try:
                precio = float(texto) if texto else 0.00
            except ValueError:
                precio = 0.00
            data["precios"][lista_id] = precio

        # --- Mostrar progreso (barra verde animada) ---
        progress = QProgressDialog(self)
        progress.setWindowTitle("Guardando producto...")
        progress.setLabelText("Procesando cambios, por favor espera...")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setRange(0, 0)  # Indeterminado
        progress.setAutoClose(False)
        progress.setAutoReset(False)

        progress.setStyleSheet("""
            QProgressBar {
                border: 2px solid #4CAF50;
                border-radius: 6px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                width: 20px;
                margin: 1px;
            }
            QLabel {
                color: #2d3436;
                font-weight: bold;
            }
        """)

        progress.show()
        QApplication.processEvents()
        print("🟢 Mostrando barra verde de progreso...")

        # --- Lanzar en hilo secundario ---
        self.thread = QThread()
        self.worker = NetworkWorker("POST", f"{API_URL}/productos/agregar", data)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)

        def terminado(resp, err):
            progress.close()
            self.thread.quit()
            self.thread.wait()
            if err:
                QMessageBox.critical(self, "Error", f"No se pudo conectar al servidor:\n{err}")
                return
            if resp.status_code == 200:
                QMessageBox.information(self, "Éxito", "Producto agregado correctamente.")
                self.accept()
            else:
                QMessageBox.warning(self, "Error", f"No se pudo agregar el producto:\n{resp.text}")

        self.worker.finished.connect(terminado)
        self.thread.start()

class EditarProductoDialog(QDialog):
    def __init__(self, producto, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Editar Producto - {producto.get('cip', '')}")
        self.resize(600, 720)
        self.setModal(True)
        print("🟢 EditarProductoDialog iniciado para:", producto.get("cip"))

        self.producto = producto

        # --- Layout principal con scroll ---
        main_layout = QVBoxLayout(self)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        form_layout = QFormLayout(scroll_content)
        form_layout.setContentsMargins(20, 20, 20, 20)
        form_layout.setSpacing(10)

        # =====================================================
        # 🔹 Campos base
        # =====================================================
        self.input_cip = QLineEdit(producto.get("cip", ""))
        self.input_cip.setReadOnly(True)

        self.input_descripcion = QLineEdit(producto.get("descripcion", ""))
        self.input_unidad = QLineEdit(producto.get("unidad", ""))

        # --- Tipo de lista ---
        self.combo_tipo_lista = QComboBox()
        self.combo_tipo_lista.addItems(["Estándar", "Gourmet"])
        tipo_actual = producto.get("tipo_lista", "Estándar")
        index = self.combo_tipo_lista.findText(tipo_actual, Qt.MatchFixedString)
        if index >= 0:
            self.combo_tipo_lista.setCurrentIndex(index)

        # --- 🔹 Campo I.V.A. debajo de Tipo de lista ---
        self.combo_iva = QComboBox()
        self.combo_iva.addItems(["Sí", "No"])
        valor_iva = producto.get("iva", "No")
        index_iva = self.combo_iva.findText(valor_iva, Qt.MatchFixedString)
        if index_iva >= 0:
            self.combo_iva.setCurrentIndex(index_iva)
        else:
            self.combo_iva.setCurrentIndex(1)  # por defecto "No"

        self.combo_iva.setStyleSheet("""
            QComboBox {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 2px 6px;
            }
            QComboBox:hover {
                border: 1px solid #4CAF50;
            }
        """)
class EditarProductoDialog(QDialog):
    def __init__(self, producto, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Editar Producto - {producto.get('cip', '')}")
        self.resize(600, 720)
        self.setModal(True)
        print("🟢 EditarProductoDialog iniciado para:", producto.get("cip"))

        self.producto = producto

        # --- Layout principal con scroll ---
        main_layout = QVBoxLayout(self)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        form_layout = QFormLayout(scroll_content)
        form_layout.setContentsMargins(20, 20, 20, 20)
        form_layout.setSpacing(10)


        # =====================================================
        # 🔹 Campos base (orden garantizado)
        # =====================================================
        base_group = QGroupBox("Datos del producto")
        base_layout = QFormLayout(base_group)
        base_layout.setSpacing(10)

        # --- CIP ---
        self.input_cip = QLineEdit(producto.get("cip", ""))
        self.input_cip.setReadOnly(True)

        # --- Descripción ---
        self.input_descripcion = QLineEdit(producto.get("descripcion", ""))

        # --- Unidad ---
        self.input_unidad = QLineEdit(producto.get("unidad", ""))

        # --- Tipo de lista ---
        self.combo_tipo_lista = QComboBox()
        self.combo_tipo_lista.addItems(["Estándar", "Gourmet"])
        tipo_actual = producto.get("tipo_lista", "Estándar")
        index = self.combo_tipo_lista.findText(tipo_actual, Qt.MatchFixedString)
        if index >= 0:
            self.combo_tipo_lista.setCurrentIndex(index)

        # --- I.V.A. ---
        self.combo_iva = QComboBox()
        self.combo_iva.addItems(["Sí", "No"])
        valor_iva = producto.get("iva", "No") or "No"
        index_iva = self.combo_iva.findText(valor_iva, Qt.MatchFixedString)
        if index_iva >= 0:
            self.combo_iva.setCurrentIndex(index_iva)
        else:
            self.combo_iva.setCurrentIndex(1)

        # --- Estilo visual ---
        self.combo_iva.setStyleSheet("""
            QComboBox {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 2px 6px;
            }
            QComboBox:hover { border: 1px solid #4CAF50; }
        """)

        # --- Orden garantizado ---
        base_layout.addRow("CIP:", self.input_cip)
        base_layout.addRow("Descripción:", self.input_descripcion)
        base_layout.addRow("Unidad:", self.input_unidad)
        base_layout.addRow("Tipo de lista:", self.combo_tipo_lista)
        base_layout.addRow("I.V.A.:", self.combo_iva)

        # --- Añadir grupo completo al formulario principal ---
        form_layout.addRow(base_group)
        form_layout.addRow(QLabel("<b>Listas de precios:</b>"))




        # =====================================================
        # 🔹 Campos dinámicos de precios
        # =====================================================
        self.campos_precios = []
        try:
            print("📡 Cargando listas de precios...")
            resp = requests.get(f"{API_URL}/precios/listas_precios/")
            if resp.status_code == 200:
                listas = resp.json()
                if listas:
                    for lista in listas:
                        precios_actuales = producto.get("precios", {})
                        precio_actual = precios_actuales.get(lista["nombre"], 0.00)
                        campo = QLineEdit(f"{precio_actual:.2f}" if precio_actual else "")
                        campo.setAlignment(Qt.AlignRight)
                        campo.setPlaceholderText("0.00")

                        def al_perder_foco(campo=campo, nombre_lista=lista["nombre"]):
                            texto = campo.text().strip()
                            campo.setText(formatear_moneda(texto))

                        campo.editingFinished.connect(al_perder_foco)
                        form_layout.addRow(f"{lista['nombre']}:", campo)
                        self.campos_precios.append((lista["id"], campo))
                else:
                    form_layout.addRow(QLabel("No hay listas de precios registradas."))
            else:
                form_layout.addRow(QLabel("⚠️ No se pudieron cargar las listas de precios."))
        except Exception as e:
            form_layout.addRow(QLabel(f"❌ Error al cargar listas: {e}"))

        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

        # =====================================================
        # 🔹 Botones inferiores
        # =====================================================
        botones_layout = QHBoxLayout()
        self.btn_guardar = QPushButton(" Guardar Cambios")
        self.btn_guardar.setIcon(qta.icon("mdi.content-save"))
        self.btn_cancelar = QPushButton(" Cancelar")
        self.btn_cancelar.setIcon(qta.icon("mdi.close-circle"))
        botones_layout.addStretch()
        botones_layout.addWidget(self.btn_guardar)
        botones_layout.addWidget(self.btn_cancelar)
        botones_layout.addStretch()
        main_layout.addLayout(botones_layout)


        # =====================================================
        # 🔹 Campos dinámicos de precios
        # =====================================================
        self.campos_precios = []
        try:
            print("📡 Cargando listas de precios...")
            resp = requests.get(f"{API_URL}/precios/listas_precios/")
            if resp.status_code == 200:
                listas = resp.json()
                if listas:
                    for lista in listas:
                        precios_actuales = producto.get("precios", {})
                        precio_actual = precios_actuales.get(lista["nombre"], 0.00)
                        campo = QLineEdit(f"{precio_actual:.2f}" if precio_actual else "")
                        campo.setAlignment(Qt.AlignRight)
                        campo.setPlaceholderText("0.00")

                        def al_perder_foco(campo=campo, nombre_lista=lista["nombre"]):
                            texto = campo.text().strip()
                            campo.setText(formatear_moneda(texto))

                        campo.editingFinished.connect(al_perder_foco)
                        form_layout.addRow(f"{lista['nombre']}:", campo)
                        self.campos_precios.append((lista["id"], campo))
                else:
                    form_layout.addRow(QLabel("No hay listas de precios registradas."))
            else:
                form_layout.addRow(QLabel("⚠️ No se pudieron cargar las listas de precios."))
        except Exception as e:
            form_layout.addRow(QLabel(f"❌ Error al cargar listas: {e}"))

        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

        # =====================================================
        # 🔹 Botones inferiores
        # =====================================================
        botones_layout = QHBoxLayout()
        self.btn_guardar = QPushButton(" Guardar Cambios")
        self.btn_guardar.setIcon(qta.icon("mdi.content-save"))
        self.btn_cancelar = QPushButton(" Cancelar")
        self.btn_cancelar.setIcon(qta.icon("mdi.close-circle"))
        botones_layout.addStretch()
        botones_layout.addWidget(self.btn_guardar)
        botones_layout.addWidget(self.btn_cancelar)
        botones_layout.addStretch()
        main_layout.addLayout(botones_layout)

        # =====================================================
        # 🔹 Conexiones
        # =====================================================
        self.btn_guardar.clicked.connect(self.guardar_cambios)
        self.btn_cancelar.clicked.connect(self.reject)

    # =====================================================
    # 🔹 Guardar cambios (actualizado con campo IVA)
    # =====================================================
    def guardar_cambios(self):
        print("💾 Ejecutando guardar_cambios()...")
        cip = self.input_cip.text().strip()
        descripcion = self.input_descripcion.text().strip()
        unidad = self.input_unidad.text().strip()
        tipo_lista = self.combo_tipo_lista.currentText().strip()
        iva = self.combo_iva.currentText().strip()

        data = {
            "cip": cip,
            "descripcion": descripcion,
            "unidad": unidad,
            "tipo_lista": tipo_lista,
            "iva": iva,  # ✅ Nuevo campo I.V.A.
            "precios": {}
        }

        for lista_id, campo in self.campos_precios:
            texto = campo.text().replace("$", "").replace(",", "").strip()
            try:
                valor = float(texto) if texto else 0.0
            except ValueError:
                valor = 0.0
            data["precios"][lista_id] = valor

        print("📤 Datos preparados para enviar:", data)

        # --- Mostrar progreso ---
        progress = QProgressDialog(self)
        progress.setWindowTitle("Guardando producto...")
        progress.setLabelText("Procesando cambios, por favor espera...")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModal)
        progress.setRange(0, 0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setStyleSheet("""
            QProgressBar {
                border: 2px solid #4CAF50;
                border-radius: 6px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                width: 20px;
                margin: 1px;
            }
            QLabel {
                color: #2d3436;
                font-weight: bold;
            }
        """)
        progress.show()
        QApplication.processEvents()

        try:
            resp = requests.put(f"{API_URL}/productos/{cip}", json=data)
            progress.close()
            if resp.status_code == 200:
                QMessageBox.information(self, "Éxito", "Producto actualizado correctamente.")
                self.accept()
            else:
                QMessageBox.warning(self, "Error", f"No se pudo actualizar el producto:\n{resp.text}")
        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "Error", f"No se pudo conectar al servidor:\n{e}")






CLIENTE_FIELDS = [
    "numero", "nombre", "empresa", "razon_social", "calle", "no_exterior",
    "no_interior", "colonia", "alcaldia", "municipio", "codigo_postal",
    "poblacion", "estado", "pais", "rfc", "telefono", "correo_electronico",
    "contacto1", "contacto2", "dias_credito", "consignatario", "consig_calle",
    "consig_no_exterior", "consig_no_interior", "consig_colonia", "consig_delegacion",
    "consig_municipio", "consig_codigo_postal", "consig_poblacion", "consig_estado",
    "consig_pais", "zona", "no_proveedor", "agente",
    "descuento",  # 🔹 agregado aquí, antes de especial
    "especial", "tipo", "vendedor",
    "direccion_entrega",  # 🔹 agregado
    "observaciones"       # 🔹 agregado
]
class ClientesTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ============================
        # 📋 Tabla de clientes
        # ============================
        self.tabla = QTableWidget()
        self.tabla.setAlternatingRowColors(True)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tabla.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tabla.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.tabla.verticalHeader().hide()

        # Estilos suaves
        self.tabla.setStyleSheet("""
            QTableWidget {
                border: 1px solid #d1d5db;
                gridline-color: #e5e7eb;
                background-color: white;
                alternate-background-color: #f9fafb;
            }
            QHeaderView::section {
                background-color: #f3f4f6;
                font-weight: bold;
                border: 1px solid #d1d5db;
                padding: 4px 6px;
            }
        """)
        layout.addWidget(self.tabla, stretch=1)

        # ============================
        # 🔘 Barra de botones (H)
        # ============================
        barra_botones = QWidget()
        hb = QHBoxLayout(barra_botones)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(12)

        self.btn_exportar = QPushButton("Exportar a Excel")
        self.btn_importar = QPushButton("Importar desde Excel")

        # 🔹 Botones *expansibles* (evita que se corten al maximizar)
        for btn, color in [
            (self.btn_exportar, "#2563eb"),
            (self.btn_importar, "#059669"),
        ]:
            btn.setMinimumWidth(160)
            btn.setFixedHeight(40)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color};
                    color: white;
                    font-weight: 600;
                    border-radius: 6px;
                    font-size: 10.5pt;
                    padding: 6px 14px;
                    border: none;
                }}
                QPushButton:hover {{
                    background-color: rgba(0,0,0,0.08);
                }}
            """)

        # 📎 Colocar botones en la barra
        hb.addStretch(1)
        hb.addWidget(self.btn_exportar)
        hb.addWidget(self.btn_importar)
        hb.addStretch(1)

        layout.addWidget(barra_botones, stretch=0)

        # 🔗 Conexiones
        self.btn_exportar.clicked.connect(self.exportar_excel)
        self.btn_importar.clicked.connect(self.importar_excel)

        # 🔹 Cargar clientes al iniciar
        self.cargar_clientes()

    # ============================
    # 📥 Cargar clientes
    # ============================
    def cargar_clientes(self):
        try:
            resp = requests.get(f"{API_URL}/clientes/")
            if resp.status_code == 200:
                clientes = resp.json()
                if clientes:
                    self.tabla.blockSignals(True)

                    self.tabla.setRowCount(len(clientes))
                    self.tabla.setColumnCount(len(clientes[0]))
                    headers = list(clientes[0].keys())
                    self.tabla.setHorizontalHeaderLabels(headers)

                    for i, c in enumerate(clientes):
                        for j, key in enumerate(headers):
                            item = QTableWidgetItem("" if c.get(key) is None else str(c[key]))
                            item.setTextAlignment(Qt.AlignCenter)
                            self.tabla.setItem(i, j, item)

                    # Modo de redimensionamiento controlado por nosotros
                    header = self.tabla.horizontalHeader()
                    header.setSectionResizeMode(QHeaderView.Interactive)

                    # Ajuste inicial “rápido” para que no empiece todo colapsado:
                    self.tabla.resizeColumnsToContents()

                    self.tabla.blockSignals(False)

                    # ✅ Ajuste proporcional final (ancho adaptable)
                    self.ajustar_columnas(headers)

                else:
                    self.tabla.clear()
                    QMessageBox.information(self, "Sin datos", "No hay clientes registrados.")
            else:
                QMessageBox.warning(self, "Error", f"No se pudieron cargar los clientes: {resp.text}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ============================
    # 📊 Ajuste proporcional columnas
    # ============================
    def ajustar_columnas(self, headers=None):
        """
        Reparte el ancho de la tabla de forma proporcional.
        Si existe 'nombre', la hacemos más ancha.
        Si existe 'razon_social', también le damos aire.
        Lo demás se reparte.
        """
        if self.tabla.columnCount() == 0:
            return

        if headers is None:
            headers = [self.tabla.horizontalHeaderItem(c).text() for c in range(self.tabla.columnCount())]

        viewport_width = self.tabla.viewport().width()
        if viewport_width <= 0:
            return

        # ── Proporciones base
        proporciones = [1] * len(headers)

        # Preferencias: nombre y razón social más anchas si existen
        if "nombre" in headers:
            proporciones[headers.index("nombre")] = 3
        if "razon_social" in headers:
            proporciones[headers.index("razon_social")] = 2

        # Normalizar y aplicar
        total = sum(proporciones)
        # restar un poco por scroll y bordes
        ancho_util = max(200, viewport_width - 16)

        for idx, peso in enumerate(proporciones):
            ancho_col = int(ancho_util * (peso / total))
            self.tabla.setColumnWidth(idx, ancho_col)

    # ============================
    # 🔄 Responsive al redimensionar
    # ============================
    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            if self.tabla.columnCount() > 0:
                headers = [self.tabla.horizontalHeaderItem(c).text() for c in range(self.tabla.columnCount())]
                self.ajustar_columnas(headers)
        except Exception as e:
            # Evitar romper UI si algo pasa en un resize
            print("resize ClientesTab:", e)

    # ============================
    # ⬇️ Exportar / ⬆️ Importar
    # ============================
    def exportar_excel(self):
        try:
            resp = requests.get(f"{API_URL}/clientes/exportar")
            if resp.status_code == 200:
                file_path, _ = QFileDialog.getSaveFileName(
                    self, "Guardar Excel", "clientes_exportados.xlsx",
                    "Archivos Excel (*.xlsx)"
                )
                if file_path:
                    with open(file_path, "wb") as f:
                        f.write(resp.content)
                    QMessageBox.information(self, "Éxito", "Clientes exportados correctamente.")
            else:
                QMessageBox.warning(self, "Error", f"No se pudo exportar: {resp.text}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def importar_excel(self):
        try:
            # ----------------------------------------
            # 1️⃣ Seleccionar archivo Excel
            # ----------------------------------------
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Seleccionar archivo de clientes",
                "",
                "Archivos Excel (*.xlsx *.xls)"
            )

            if not file_path:
                return  # usuario canceló

            # ----------------------------------------
            # 2️⃣ Preparar archivo para FastAPI
            # ----------------------------------------
            with open(file_path, "rb") as f:
                files = {
                    "file": (
                        os.path.basename(file_path),
                        f,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                }

                # ----------------------------------------
                # 3️⃣ Enviar a la API
                # ----------------------------------------
                resp = requests.post(f"{API_URL}/clientes/importar", files=files)

            # ----------------------------------------
            # 4️⃣ Procesar respuesta
            # ----------------------------------------
            if resp.status_code == 200:
                data = resp.json()
                filas = data.get("filas", 0)
                QMessageBox.information(
                    self,
                    "Importación completada",
                    f"Clientes importados correctamente.\nTotal de registros: {filas}"
                )
                self.cargar_clientes()

            else:
                QMessageBox.warning(
                    self,
                    "Error al importar",
                    f"Ocurrió un problema:\n{resp.text}"
                )

        except Exception as e:
            QMessageBox.critical(self, "Error inesperado", str(e))

class EditarClienteDialog(QDialog):
    def __init__(self, cliente_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Editar Cliente: {cliente_data.get('nombre', '')}")
        self.resize(800, 500)

        self.campos = {}  # 🔹 Aquí guardaremos todos los widgets por nombre de campo

        # --- Layout principal con scroll ---
        main_layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        form_layout = QFormLayout(scroll_content)

        # --- Crear campos del formulario ---
        for key, value in cliente_data.items():
            if key == "especial":
                # 🔹 Campo "especial" como combo con búsqueda
                combo = QComboBox()
                combo.setEditable(True)
                self.campos[key] = combo

                # Llenar las listas de precios desde la API
                try:
                    resp = requests.get(f"{API_URL}/precios/listas_precios/")
                    if resp.status_code == 200:
                        listas = resp.json()
                        nombres_listas = [l["nombre"] for l in listas]
                        combo.addItems(nombres_listas)

                        # Hacerlo buscable
                        completer = QCompleter(nombres_listas, self)
                        completer.setCaseSensitivity(False)
                        completer.setFilterMode(Qt.MatchContains)
                        combo.setCompleter(completer)
                except Exception as e:
                    print("Error al cargar listas de precios:", e)

                # Seleccionar el valor actual si existe
                if value:
                    idx = combo.findText(str(value))
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                    else:
                        combo.setEditText(str(value))

                form_layout.addRow(f"{key.capitalize()}:", combo)
            else:
                # 🔹 Campo normal tipo texto
                entrada = QLineEdit(str(value or ""))
                self.campos[key] = entrada
                form_layout.addRow(f"{key.capitalize()}:", entrada)

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

        # --- Botones ---
        btn_layout = QHBoxLayout()
        btn_guardar = QPushButton("Guardar")
        btn_cancelar = QPushButton("Cancelar")

        btn_guardar.clicked.connect(self.accept)
        btn_cancelar.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(btn_guardar)
        btn_layout.addWidget(btn_cancelar)
        btn_layout.addStretch()

        main_layout.addLayout(btn_layout)

    # --- 🔹 Obtener todos los datos del formulario ---
    def get_data(self):
        data = {}
        for campo, widget in self.campos.items():
            if isinstance(widget, QLineEdit):
                data[campo] = widget.text().strip()
            elif isinstance(widget, QComboBox):
                data[campo] = widget.currentText().strip()
            else:
                data[campo] = None
        return data

    # ======================================================
    # 🔹 Cargar las listas de precios y configurar búsqueda
    # ======================================================
    def cargar_listas_precios(self):
        try:
            resp = requests.get(f"{API_URL}/precios/listas_precios/")
            if resp.status_code == 200:
                listas = resp.json()
                nombres_listas = ["Lista General"] + [l["nombre"] for l in listas]
                self.combo_especial.addItems(nombres_listas)

                # --- Activar búsqueda con autocompletado ---
                completer = QCompleter(nombres_listas, self)
                completer.setCaseSensitivity(False)  # no distingue mayúsculas/minúsculas
                from PyQt5.QtCore import Qt
                completer.setFilterMode(Qt.MatchContains)
                self.combo_especial.setCompleter(completer)
            else:
                QMessageBox.warning(self, "Error", "No se pudieron cargar las listas de precios.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ======================================================
    # 🔹 Obtener datos finales para enviar a la API
    # ======================================================
    def obtener_datos(self):
        return {
            "numero": self.input_numero.text().strip(),
            "nombre": self.input_nombre.text().strip(),
            "empresa": self.input_empresa.text().strip(),
            "rfc": self.input_rfc.text().strip(),
            "dias_credito": self.input_dias_credito.text().strip(),
            "especial": self.combo_especial.currentText().strip()
        }
    def get_data(self):
        """Devuelve todos los valores del formulario en formato dict"""
        data = {}
        for campo, widget in self.campos.items():
            if isinstance(widget, QLineEdit):
                data[campo] = widget.text().strip()
            elif isinstance(widget, QComboBox):
                data[campo] = widget.currentText().strip()
            else:
                data[campo] = None
        return data
    
class ClientesWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gestión de Clientes")
        self.resize(1400, 800)

        # Parámetros de paginación
        self.pagina_actual = 1
        self.tamano_pagina = 26 

        # --- Layout principal ---
        self.layout_principal = QVBoxLayout(self)
        self.layout_principal.setContentsMargins(10, 10, 10, 10)
        self.layout_principal.setSpacing(5)

        # ===============================
        # 🔹 FILTROS SUPERIORES
        # ===============================
        filtros_layout = QHBoxLayout()
        filtros_layout.setContentsMargins(0, 10, 0, 10)
        filtros_layout.setSpacing(15)

        label_numero = QLabel("Número Cliente:")
        self.input_numero = QLineEdit()
        self.input_numero.setFixedWidth(150)
        self.input_numero.setPlaceholderText("Ej. 1,000")

        label_nombre = QLabel("Nombre Cliente:")
        self.input_nombre = QLineEdit()
        self.input_nombre.setFixedWidth(200)
        self.input_nombre.setPlaceholderText("Ej. Juan Pérez")

        # --- Botón "Limpiar" con estilo unificado (verde + ícono Material) ---
        self.btn_limpiar_filtro = QPushButton(" Limpiar")
        self.btn_limpiar_filtro.setIcon(qta.icon("mdi.refresh", color="white"))
        self.btn_limpiar_filtro.setIconSize(QSize(18, 18))
        self.btn_limpiar_filtro.setFixedWidth(130)
        self.btn_limpiar_filtro.setCursor(Qt.PointingHandCursor)

        # Campos de texto (sin cambios)
        for w in (self.input_numero, self.input_nombre):
            w.setStyleSheet("""
                QLineEdit {
                    padding: 6px 8px;
                    border: 1px solid #bbb;
                    border-radius: 5px;
                    background-color: #fff;
                }
            """)


        filtros_layout.addWidget(label_numero)
        filtros_layout.addWidget(self.input_numero)
        filtros_layout.addSpacing(30)
        filtros_layout.addWidget(label_nombre)
        filtros_layout.addWidget(self.input_nombre)
        filtros_layout.addSpacing(30)
        filtros_layout.addWidget(self.btn_limpiar_filtro)

        filtros_container = QHBoxLayout()
        filtros_container.addStretch(1)
        filtros_container.addLayout(filtros_layout)
        filtros_container.addStretch(1)
        self.layout_principal.addLayout(filtros_container)

        # ===============================
        # 🔹 TABLA PRINCIPAL
        # ===============================
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)

        # 🔹 Altura de filas para 25 registros visibles en 1080p
        self.table.verticalHeader().setDefaultSectionSize(28)

        # 🔹 Scroll más suave
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.layout_principal.addWidget(self.table, stretch=1)

        # ===============================
        # 🔹 PAGINACIÓN COMPLETA
        # ===============================
        paginacion_layout = QHBoxLayout()
        paginacion_layout.setAlignment(Qt.AlignCenter)

        # --- Botones de control de página ---
        self.btn_primera = QPushButton()
        self.btn_primera.setIcon(self.style().standardIcon(QStyle.SP_MediaSkipBackward))
        self.btn_primera.setToolTip("Primera página")

        self.btn_anterior = QPushButton()
        self.btn_anterior.setIcon(self.style().standardIcon(QStyle.SP_ArrowLeft))
        self.btn_anterior.setToolTip("Página anterior")

        self.lbl_pagina = QLabel("Página 1 de 1")
        self.lbl_pagina.setStyleSheet("font-weight: bold; font-size: 10pt; color: #333;")

        self.btn_siguiente = QPushButton()
        self.btn_siguiente.setIcon(self.style().standardIcon(QStyle.SP_ArrowRight))
        self.btn_siguiente.setToolTip("Página siguiente")

        self.btn_ultima = QPushButton()
        self.btn_ultima.setIcon(self.style().standardIcon(QStyle.SP_MediaSkipForward))
        self.btn_ultima.setToolTip("Última página")

        # --- Estilo visual consistente ---
        for b in (self.btn_primera, self.btn_anterior, self.btn_siguiente, self.btn_ultima):
            b.setFixedSize(36, 32)
            b.setStyleSheet("""
                QPushButton {
                    background-color: #f2f2f2;
                    border: 1px solid #aaa;
                    border-radius: 6px;
                }
                QPushButton:hover { background-color: #e0e0e0; }
            """)

        # --- Distribución visual ---
        paginacion_layout.addWidget(self.btn_primera)
        paginacion_layout.addSpacing(6)
        paginacion_layout.addWidget(self.btn_anterior)
        paginacion_layout.addSpacing(10)
        paginacion_layout.addWidget(self.lbl_pagina)
        paginacion_layout.addSpacing(10)
        paginacion_layout.addWidget(self.btn_siguiente)
        paginacion_layout.addSpacing(6)
        paginacion_layout.addWidget(self.btn_ultima)

        self.layout_principal.addLayout(paginacion_layout)

        # ===============================
        # 🔹 BOTONES INFERIORES CON ICONOS MIXTOS + AGREGAR CLIENTE (al inicio)
        # ===============================
        botones_layout = QHBoxLayout()
        botones_layout.setSpacing(15)
        botones_layout.setAlignment(Qt.AlignCenter)

        # --- Botón principal: Agregar Cliente ---
        self.btn_agregar_cliente = QPushButton(" Agregar Cliente")
        self.btn_agregar_cliente.setIcon(qta.icon("mdi.account-plus"))
        self.btn_agregar_cliente.setIconSize(QSize(20, 20))
        self.btn_agregar_cliente.setCursor(Qt.PointingHandCursor)

        # --- Otros botones ---
        self.btn_importar = QPushButton(" Importar")
        self.btn_importar.setIcon(qta.icon("mdi.file-import"))

        self.btn_exportar = QPushButton(" Exportar")
        self.btn_exportar.setIcon(qta.icon("mdi.file-export"))

        self.btn_editar = QPushButton(" Editar Cliente")
        self.btn_editar.setIcon(qta.icon("mdi.pencil"))

        self.btn_eliminar = QPushButton(" Eliminar Cliente")
        self.btn_eliminar.setIcon(qta.icon("mdi.delete"))

        self.btn_refrescar = QPushButton(" Actualizar Lista")
        self.btn_refrescar.setIcon(qta.icon("mdi.refresh"))

        # --- Estilo Aspel unificado ---
        for btn in (
            self.btn_agregar_cliente,
            self.btn_importar,
            self.btn_exportar,
            self.btn_editar,
            self.btn_eliminar,
            self.btn_refrescar,
        ):
            btn.setMinimumWidth(140)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setIconSize(QSize(20, 20))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 6px;
                }
                QPushButton:hover { background-color: #45a049; }
                QPushButton:pressed { background-color: #3d8b40; }
            """)

        # Botón Eliminar en rojo
        self.btn_eliminar.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px;
            }
            QPushButton:hover { background-color: #e53935; }
        """)

        # --- Orden visual: Agregar va primero ---
        botones = [
            self.btn_agregar_cliente,
            self.btn_importar,
            self.btn_exportar,
            self.btn_editar,
            self.btn_eliminar,
            self.btn_refrescar,
        ]
        for b in botones:
            botones_layout.addWidget(b)

        self.layout_principal.addLayout(botones_layout)

        # ===============================
        # 🔹 BARRA DE ESTADO
        # ===============================
        self.status_label = QLabel("Listo")
        self.status_label.setAlignment(Qt.AlignRight)
        self.status_label.setStyleSheet("color: #555; font-size: 9pt; padding: 4px;")
        self.layout_principal.addWidget(self.status_label)

        # ===============================
        # 🔹 Eventos y carga inicial
        # ===============================
        self.cargar_clientes()
        self.input_numero.textChanged.connect(self.aplicar_filtro)
        self.input_nombre.textChanged.connect(self.aplicar_filtro)
        self.btn_limpiar_filtro.clicked.connect(self.limpiar_filtro)
        self.btn_importar.clicked.connect(self.importar_clientes)
        self.btn_exportar.clicked.connect(self.exportar_clientes)
        self.btn_editar.clicked.connect(self.editar_cliente)
        self.btn_eliminar.clicked.connect(self.eliminar_cliente)
        self.btn_anterior.clicked.connect(self.pagina_anterior)
        self.btn_siguiente.clicked.connect(self.pagina_siguiente)
        self.btn_primera.clicked.connect(self.pagina_primera)
        self.btn_ultima.clicked.connect(self.pagina_ultima)
        self.btn_agregar_cliente.clicked.connect(self.abrir_dialogo_agregar_cliente)
        
        # ===============================
        # 🔹 Aplicar cursor de mano a todos los botones
        # ===============================
        for boton in self.findChildren(QPushButton):
            boton.setCursor(Qt.PointingHandCursor)
    # ===============================
    # 🔹 MÉTODOS DE PAGINACIÓN
    # ===============================
    def actualizar_paginacion(self):
        total_registros = len(self.clientes_filtrados)
        total_paginas = max(1, (total_registros + self.tamano_pagina - 1) // self.tamano_pagina)
        self.pagina_actual = min(self.pagina_actual, total_paginas)

        inicio = (self.pagina_actual - 1) * self.tamano_pagina
        fin = min(inicio + self.tamano_pagina, total_registros)
        clientes_pagina = self.clientes_filtrados[inicio:fin]

        self.llenar_tabla(clientes_pagina)
        self.lbl_pagina.setText(f"Página {self.pagina_actual} de {total_paginas}")
        self.status_label.setText(f"Mostrando {inicio+1}–{fin} de {total_registros} clientes")

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.actualizar_paginacion()

    def pagina_siguiente(self):
        total_registros = len(self.clientes_filtrados)
        total_paginas = max(1, (total_registros + self.tamano_pagina - 1) // self.tamano_pagina)
        if self.pagina_actual < total_paginas:
            self.pagina_actual += 1
            self.actualizar_paginacion()
    
    def pagina_primera(self):
        if self.pagina_actual != 1:
            self.pagina_actual = 1
            self.actualizar_paginacion()

    def pagina_ultima(self):
        total_registros = len(self.clientes_filtrados)
        total_paginas = max(1, (total_registros + self.tamano_pagina - 1) // self.tamano_pagina)
        if self.pagina_actual != total_paginas:
            self.pagina_actual = total_paginas
            self.actualizar_paginacion()

    # ===============================
    # 🔹 CARGA DE CLIENTES
    # ===============================
    def cargar_clientes(self):
        try:
            resp = requests.get(f"{API_URL}/clientes/")
            if resp.status_code != 200:
                QMessageBox.warning(self, "Error", "No se pudieron cargar los clientes")
                return

            clientes = resp.json()

            # 🔹 Validar que existan clientes antes de acceder a clientes[0]
            if not clientes:
                self.todos_clientes = []
                self.clientes_filtrados = []
                self.table.setRowCount(0)
                QMessageBox.information(self, "Clientes", "No hay clientes registrados en la base de datos.")
                return

            print("Columnas detectadas:", clientes[0].keys())

            self.todos_clientes = clientes
            self.clientes_filtrados = clientes
            self.actualizar_paginacion()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # --- 🔹 Mostrar datos en tabla ---
    def llenar_tabla(self, clientes):
        if not clientes:
            self.table.setRowCount(0)
            return

        columnas = list(clientes[0].keys())

        # 🔹 Reordenar columnas: 'numero' y 'nombre' primero
        if "numero" in columnas and "nombre" in columnas:
            columnas.remove("numero")
            columnas.remove("nombre")
            columnas = ["numero", "nombre"] + columnas
        elif "numero" in columnas:
            columnas.remove("numero")
            columnas = ["numero"] + columnas
        elif "nombre" in columnas:
            columnas.remove("nombre")
            columnas = ["nombre"] + columnas
        self.table.setColumnCount(len(columnas))
        self.table.setRowCount(len(clientes))
        self.table.setHorizontalHeaderLabels(columnas)

        for i, cliente in enumerate(clientes):
            for j, key in enumerate(columnas):
                item = QTableWidgetItem(str(cliente[key]) if cliente[key] else "")
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(i, j, item)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

        # 🔹 Llamar a la sincronización y congelación después de cargar los datos
        self.congelar_columnas("nombre")


    
    def congelar_columnas(self, hasta_columna="nombre"):
        """
        Congela las columnas 'numero' y 'nombre' (si existen) a la izquierda,
        perfectamente alineadas con encabezados y sincronizadas en altura y scroll.
        """
        try:
            columnas = [self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())]
            if not columnas:
                return

            # 🔹 Detectar índices de columnas clave
            idx_numero = columnas.index("numero") if "numero" in columnas else None
            idx_nombre = columnas.index("nombre") if "nombre" in columnas else None

            if idx_numero is None and idx_nombre is None:
                return

            # Limite hasta 'nombre' (si existe) o hasta 'numero'
            idx_limite = max(idx for idx in [idx_numero, idx_nombre] if idx is not None)

            # --- Crear tabla congelada ---
            self.tabla_fija = QTableWidget()
            self.tabla_fija.setColumnCount(idx_limite + 1)
            self.tabla_fija.setRowCount(self.table.rowCount())

            headers_fijos = columnas[: idx_limite + 1]
            self.tabla_fija.setHorizontalHeaderLabels(headers_fijos)

            self.tabla_fija.verticalHeader().hide()
            self.tabla_fija.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
            self.tabla_fija.setEditTriggers(QTableWidget.NoEditTriggers)
            self.tabla_fija.setFocusPolicy(Qt.NoFocus)
            self.tabla_fija.setSelectionMode(QTableWidget.NoSelection)
            self.tabla_fija.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.tabla_fija.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            # 🔹 Estilo combinado: base + sombreado suave
            self.tabla_fija.setAlternatingRowColors(True)

            # --- Copiar datos de columnas fijas ---
            for r in range(self.table.rowCount()):
                for c in range(idx_limite + 1):
                    item_origen = self.table.item(r, c)
                    texto = item_origen.text() if item_origen else ""

                    # Formatear 'numero' solo si parece numérico
                    if columnas[c].lower() == "numero":
                        try:
                            num = float(str(texto).replace(",", "").strip())
                            texto = f"{int(num):,}"
                        except ValueError:
                            texto = texto.strip()

                    item = QTableWidgetItem(texto)
                    if columnas[c].lower() == "numero":
                        item.setTextAlignment(Qt.AlignCenter)
                    elif columnas[c].lower() == "nombre":
                        item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                        item.setFont(QFont("Segoe UI", 10))
                    else:
                        item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)

                    self.tabla_fija.setItem(r, c, item)

            # --- Ocultar las columnas originales ---
            for c in range(idx_limite + 1):
                self.table.setColumnHidden(c, True)

            # --- Calcular ancho total ---
            ancho_total = 0
            for i in range(idx_limite + 1):
                ancho_columna = max(100, self.table.columnWidth(i))
                if columnas[i].lower() == "nombre":
                    ancho_columna *= 2  # o 2 si quieres el doble
                    self.tabla_fija.setColumnWidth(i, int(ancho_columna))
                ancho_total += ancho_columna
            self.tabla_fija.setMinimumWidth(int(ancho_total))
            self.tabla_fija.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

            # --- Sincronizar alturas ---
            def sync_row_heights():
                for i in range(self.table.rowCount()):
                    h = self.table.rowHeight(i)
                    if self.tabla_fija.rowHeight(i) != h:
                        self.tabla_fija.setRowHeight(i, h)
                self.tabla_fija.verticalScrollBar().setValue(self.table.verticalScrollBar().value())

            self.sync_row_heights = sync_row_heights
            sync_row_heights()

            # --- Conectar scroll ---
            self.table.verticalScrollBar().valueChanged.connect(self.tabla_fija.verticalScrollBar().setValue)
            self.table.viewport().installEventFilter(self)

            # --- Crear encabezado visual ---
            self.tabla_fija.horizontalHeader().hide()
            header_contenedor = QWidget()
            header_layout = QHBoxLayout(header_contenedor)
            header_layout.setContentsMargins(0, 0, 0, 0)
            header_layout.setSpacing(0)

            for titulo in headers_fijos:
                etiqueta = QLabel(titulo)
                etiqueta.setAlignment(Qt.AlignCenter)
                etiqueta.setFixedHeight(self.table.horizontalHeader().height())
                header_layout.addWidget(etiqueta)

            izquierda = QWidget()
            vbox_izq = QVBoxLayout(izquierda)
            vbox_izq.setContentsMargins(0, 0, 0, 0)
            vbox_izq.setSpacing(0)
            vbox_izq.addWidget(header_contenedor)
            vbox_izq.addWidget(self.tabla_fija)

            contenedor = QWidget()
            hbox = QHBoxLayout(contenedor)
            hbox.setContentsMargins(0, 0, 0, 0)
            hbox.setSpacing(0)
            hbox.addWidget(izquierda)

            divisor = QWidget()
            divisor.setFixedWidth(1)
            hbox.addWidget(divisor)
            hbox.addWidget(self.table)

            # --- Reemplazar en layout principal ---
            if hasattr(self, "contenedor_tablas_widget"):
                self.layout_principal.removeWidget(self.contenedor_tablas_widget)
                self.contenedor_tablas_widget.deleteLater()

            self.layout_principal.removeWidget(self.table)
            self.contenedor_tablas_widget = contenedor
            self.layout_principal.insertWidget(1, contenedor, stretch=1)

        except Exception as e:
            print("Error al congelar columnas:", e)


    def eventFilter(self, obj, event):
        if hasattr(self, "sync_row_heights"):
            if event.type() in (QEvent.Paint, QEvent.UpdateRequest, QEvent.Resize):
                self.sync_row_heights()
        return super().eventFilter(obj, event)


    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            if hasattr(self, "tabla_fija") and hasattr(self, "ancho_tabla_fija_base"):
                # 🔹 Calcula un factor de ajuste proporcional al ancho de la ventana
                ancho_total = max(1000, self.width())
                factor = ancho_total / 1600  # 1600 = ancho base de referencia, ajusta si tu ventana inicial es otra
                nuevo_ancho = int(self.ancho_tabla_fija_base * factor)

                # 🔹 Mantiene alineación estable
                self.tabla_fija.setFixedWidth(nuevo_ancho)
                self.tabla_fija.updateGeometry()

            if hasattr(self, "sync_row_heights"):
                QTimer.singleShot(80, self.sync_row_heights)
        except Exception as e:
            print("Error en resizeEvent:", e)
    


    # ===============================
    # 🔹 FILTROS
    # ===============================
    def aplicar_filtro(self):
        numero = self.input_numero.text().strip().lower()
        nombre = self.input_nombre.text().strip().lower()

        self.clientes_filtrados = [
            c for c in self.todos_clientes
            if (not numero or numero in str(c.get("numero", "")).lower())
            and (not nombre or nombre in str(c.get("nombre", "")).lower())
        ]
        self.pagina_actual = 1
        self.actualizar_paginacion()

    def limpiar_filtro(self):
        self.input_numero.clear()
        self.input_nombre.clear()
        self.clientes_filtrados = self.todos_clientes
        self.pagina_actual = 1
        self.actualizar_paginacion()

    # --- importar clientes ---
    def importar_clientes(self):
        try:
            file_path, _ = QFileDialog.getOpenFileName(self, "Seleccionar Excel", "", "Archivos Excel (*.xlsx)")
            if not file_path:
                return
            with open(file_path, "rb") as f:
                files = {"file": (file_path, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
                resp = requests.post(f"{API_URL}/clientes/importar", files=files)
                if resp.status_code == 200:
                    QMessageBox.information(self, "Éxito", "Clientes importados correctamente.")
                    self.cargar_clientes()
                else:
                    QMessageBox.warning(self, "Error", f"No se pudo importar: {resp.text}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # --- exportar clientes ---
    def exportar_clientes(self):
        try:
            resp = requests.get(f"{API_URL}/clientes/exportar")
            if resp.status_code == 200:
                file_path, _ = QFileDialog.getSaveFileName(self, "Guardar Excel", "clientes.xlsx", "Archivos Excel (*.xlsx)")
                if file_path:
                    with open(file_path, "wb") as f:
                        f.write(resp.content)
                    QMessageBox.information(self, "Éxito", "Clientes exportados correctamente.")
            else:
                QMessageBox.warning(self, "Error", f"No se pudo exportar: {resp.text}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def editar_cliente(self):
        fila = self.table.currentRow()
        if fila < 0:
            QMessageBox.warning(self, "Aviso", "Selecciona un cliente para editar")
            return

        # 🔹 Obtener datos del cliente de la fila seleccionada
        columnas = [self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())]
        cliente_data = {col: self.table.item(fila, i).text() if self.table.item(fila, i) else "" for i, col in enumerate(columnas)}

        # 🔹 Abrir formulario
        dialog = EditarClienteDialog(cliente_data, self)
        if dialog.exec_() == QDialog.Accepted:
            nuevos_datos = dialog.get_data()
            numero = cliente_data["numero"]
            empresa = cliente_data["empresa"]

            try:
                resp = requests.put(f"{API_URL}/clientes/{numero}/{empresa}", json=nuevos_datos)
                if resp.status_code == 200:
                    QMessageBox.information(self, "Éxito", "Cliente actualizado correctamente")

                    # Actualizar en memoria
                    for c in self.todos_clientes:
                        if c["numero"] == numero and c["empresa"] == empresa:
                            c.update(nuevos_datos)
                            break
                    # Refrescar tabla
                    self.aplicar_filtro()
                else:
                    QMessageBox.warning(self, "Error", f"No se pudo actualizar: {resp.text}")
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

            for campo, valor in nuevos_datos.items():
                if valor in ("", None, "nan", "NaN"):
                    nuevos_datos[campo] = None
                elif campo == "dias_credito":
                    try:
                        nuevos_datos[campo] = int(valor)
                    except:
                        nuevos_datos[campo] = 0

    def eliminar_cliente(self):
        fila = self.table.currentRow()
        if fila < 0:
            QMessageBox.warning(self, "Aviso", "Selecciona un cliente para eliminar")
            return

        numero = self.table.item(fila, 0).text()
        empresa = self.table.item(fila, 2).text()

        confirm = QMessageBox.question(
            self, "Confirmar eliminación",
            f"¿Seguro que deseas eliminar el cliente {numero} - {empresa}?",
            QMessageBox.Yes | QMessageBox.No
        )

        if confirm == QMessageBox.Yes:
            try:
                resp = requests.delete(f"{API_URL}/clientes/{numero}/{empresa}")
                if resp.status_code == 200:
                    QMessageBox.information(self, "Éxito", "Cliente eliminado correctamente")

                    # Quitar de la lista local
                    self.todos_clientes = [c for c in self.todos_clientes if not (c["numero"] == numero and c["empresa"] == empresa)]

                    # Refrescar tabla
                    self.aplicar_filtro()
                else:
                    QMessageBox.warning(self, "Error", f"No se pudo eliminar: {resp.text}")
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
    # =====================================================
    # 🔹 Agregar Cliente (abre ventana de diálogo)
    # =====================================================
    def abrir_dialogo_agregar_cliente(self):
        dialog = AgregarClienteDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            nombre = dialog.input_nombre.text().strip()
            rfc = dialog.input_rfc.text().strip()
            telefono = dialog.input_telefono.text().strip()
            correo = dialog.input_correo.text().strip()

            if not nombre:
                QMessageBox.warning(self, "Advertencia", "El campo 'Nombre' es obligatorio.")
                return

            # Agregar nueva fila en la tabla visual
            fila = self.table.rowCount()
            self.table.insertRow(fila)

            self.table.setItem(fila, 0, QTableWidgetItem(nombre))
            self.table.setItem(fila, 1, QTableWidgetItem(rfc))
            self.table.setItem(fila, 2, QTableWidgetItem(telefono))
            self.table.setItem(fila, 3, QTableWidgetItem(correo))

            # Notificación visual
            QMessageBox.information(self, "Cliente agregado",
                                    f"Cliente '{nombre}' agregado correctamente.")
            
import requests
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QMessageBox, QInputDialog
)
from PyQt5.QtCore import Qt

API_URL = "http://192.168.1.105:8000"  # Asegúrate que coincide con tu servidor FastAPI


import requests
import pandas as pd
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem,
    QMessageBox, QInputDialog, QFileDialog
)
from PyQt5.QtCore import Qt
import os

API_URL = "http://192.168.1.105:8000"  # Ajusta si tu API usa otra IP o puerto

# =====================================================
# 🔹 Helper: Crear ícono de color (usado para botones como "Recargar")
# =====================================================
def crear_icono_color(color_hex="#2196F3", size=24):
    """
    Crea un ícono circular de color sólido (tipo 'refresh' o 'action button')
    con un color personalizado en formato hex (#RRGGBB).
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    brush = QBrush(QColor(color_hex))
    painter.setBrush(brush)
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return QIcon(pixmap)

class ProductosTab(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gestión de Productos y Listas de Precios")
        self.resize(1200, 650)

        # --- Layout principal ---
        layout = QVBoxLayout()

        # =====================================================
        # 🔹 BOTONES SUPERIORES + CAMPO DE BÚSQUEDA (centrados)
        # =====================================================
        top_buttons_layout = QHBoxLayout()
        top_buttons_layout.setAlignment(Qt.AlignCenter)

        # --- Campo de búsqueda con ícono de lupa ---
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(" Buscar por CIP o Descripción...")
        self.search_input.setMinimumWidth(240)
        self.search_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.filtrar_productos)

        # Añadir ícono de lupa al lado izquierdo
        icono_buscar = qta.icon("mdi.magnify", color="#28a745")
        accion_buscar = self.search_input.addAction(icono_buscar, QLineEdit.LeadingPosition)
        accion_buscar.setToolTip("Buscar producto")

        # --- Botones de acción ---
        self.btn_agregar_lista = QPushButton(" Agregar Lista")
        self.btn_agregar_lista.setIcon(qta.icon("mdi.playlist-plus"))

        self.btn_guardar = QPushButton(" Guardar Cambios")
        self.btn_guardar.setIcon(qta.icon("mdi.content-save"))

        self.btn_eliminar_lista = QPushButton(" Eliminar Lista")
        self.btn_eliminar_lista.setIcon(qta.icon("mdi.delete-forever"))

        self.btn_recargar = QPushButton(" Recargar")
        self.btn_recargar.setIcon(crear_icono_color("#2196F3"))

        top_buttons = [
            self.btn_agregar_lista,
            self.btn_guardar,
            self.btn_eliminar_lista,
            self.btn_recargar
        ]
        for btn in top_buttons:
            btn.setMinimumWidth(140)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setIconSize(QSize(20, 20))

        # --- Contenedor interno centrado ---
        center_widget = QWidget()
        center_layout = QHBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)
        center_layout.setAlignment(Qt.AlignCenter)

        # Añadir búsqueda + botones al mismo grupo
        center_layout.addWidget(self.search_input)
        center_layout.addSpacing(15)
        for btn in top_buttons:
            center_layout.addWidget(btn)
            center_layout.addSpacing(10)

        # --- Agregar el grupo centrado al layout principal ---
        top_buttons_layout.addStretch()
        top_buttons_layout.addWidget(center_widget, alignment=Qt.AlignCenter)
        top_buttons_layout.addStretch()

        layout.addLayout(top_buttons_layout)

        # =====================================================
        # 🔹 TABLA DE PRODUCTOS + LISTAS
        # =====================================================
        self.tabla = QTableWidget()
        self.tabla.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.tabla)

        # ===============================
        # 🔹 BOTONES INFERIORES + AGREGAR PRODUCTO (al inicio)
        # ===============================
        bottom_buttons_layout = QHBoxLayout()
        bottom_buttons_layout.setSpacing(15)
        bottom_buttons_layout.setAlignment(Qt.AlignCenter)

        # --- Botón principal: Agregar Producto ---
        self.btn_agregar_producto = QPushButton(" Agregar Producto")
        self.btn_agregar_producto.setIcon(qta.icon("mdi.package-variant"))
        self.btn_agregar_producto.setIconSize(QSize(20, 20))
        self.btn_agregar_producto.setCursor(Qt.PointingHandCursor)

        # --- Otros botones ---
        self.btn_importar = QPushButton(" Importar")
        self.btn_importar.setIcon(qta.icon("mdi.file-import"))

        self.btn_exportar = QPushButton(" Exportar")
        self.btn_exportar.setIcon(qta.icon("mdi.file-export"))

        self.btn_editar = QPushButton(" Editar Producto")
        self.btn_editar.setIcon(qta.icon("mdi.pencil"))

        self.btn_eliminar = QPushButton(" Eliminar Producto")
        self.btn_eliminar.setIcon(qta.icon("mdi.delete"))

        # --- Estilo Aspel unificado ---
        for btn in (
            self.btn_agregar_producto,
            self.btn_importar,
            self.btn_exportar,
            self.btn_editar,
            self.btn_eliminar,
        ):
            btn.setMinimumWidth(140)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setIconSize(QSize(20, 20))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 6px;
                }
                QPushButton:hover { background-color: #45a049; }
                QPushButton:pressed { background-color: #3d8b40; }
            """)

        # Botón Eliminar en rojo
        self.btn_eliminar.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px;
            }
            QPushButton:hover { background-color: #e53935; }
        """)

        # --- Orden visual: Agregar primero ---
        botones = [
            self.btn_agregar_producto,
            self.btn_importar,
            self.btn_exportar,
            self.btn_editar,
            self.btn_eliminar,
        ]
        for b in botones:
            bottom_buttons_layout.addWidget(b)

        layout.addLayout(bottom_buttons_layout)

        self.setLayout(layout)

        # =====================================================
        # 🔹 Conexiones de botones
        # =====================================================
        self.btn_agregar_lista.clicked.connect(self.agregar_lista)
        self.btn_guardar.clicked.connect(self.guardar_cambios)
        self.btn_eliminar_lista.clicked.connect(self.eliminar_lista)
        self.btn_recargar.clicked.connect(self.cargar_datos)
        self.btn_importar.clicked.connect(self.importar_productos)
        self.btn_exportar.clicked.connect(self.exportar_productos)
        self.btn_editar.clicked.connect(self.editar_producto)
        self.btn_eliminar.clicked.connect(self.eliminar_producto)
        self.btn_agregar_producto.clicked.connect(self.abrir_dialogo_agregar_producto)

        
        
        # 🔹 Aplicar estilo global (cursor + sombra) a todos los botones del widget
        aplicar_estilo_global(self.findChildren(QPushButton))

        # =====================================================
        # 🔹 Cargar datos iniciales
        # =====================================================
        self.cargar_datos()

        

        self.tabla.horizontalHeader().setFixedHeight(30)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.horizontalHeader().setHighlightSections(False)
        self.tabla.setShowGrid(True)
        self.tabla.setSortingEnabled(True)

    # =====================================================
    # 🔹 Cargar productos y listas (versión corregida)
    # =====================================================
    def cargar_datos(self):
        try:
            # 1) Cargar las listas de precios (para construir las columnas dinámicas)
            resp_listas = requests.get(f"{API_URL}/precios/listas_precios/")
            if resp_listas.status_code != 200:
                QMessageBox.warning(self, "Error", "No se pudieron cargar las listas de precios.")
                return
            self.listas = resp_listas.json()  # [{id, nombre}, ...]

            # 2) Cargar productos
            resp_productos = requests.get(f"{API_URL}/productos/")
            if resp_productos.status_code != 200:
                QMessageBox.warning(self, "Error", "No se pudieron cargar los productos.")
                return

            self.productos = resp_productos.json()  # [{cip, descripcion, unidad, tipo_lista, precios: {NombreLista: valor}, ...}]
            if not self.productos:
                self.tabla.setRowCount(0)
                self.tabla.setColumnCount(0)
                QMessageBox.information(self, "Sin datos", "No hay productos registrados.")
                return
            
            self.productos = resp_productos.json()

            # --- Detectar listas que usan códigos de barras ---
            listas_con_codigos = set()

            for prod in self.productos:
                precios = prod.get("precios", {})
                for nombre_lista, datos in precios.items():
                    # si el backend devuelve estructura {"precio": X, "codigo_barras": Y}
                    if isinstance(datos, dict):
                        codigo = datos.get("codigo_barras", "").strip()
                        if codigo:
                            listas_con_codigos.add(nombre_lista)


            # --- Determinar dinámicamente las columnas ---
            columnas_base = ["CIP", "Descripción", "Unidad", "Tipo de lista", "I.V.A."]
            columnas_dinamicas = []

            for lista in self.listas:
                nombre_lista = lista["nombre"]
                columnas_dinamicas.append(f"Precio {nombre_lista}")  # siempre muestra el precio
                if nombre_lista in listas_con_codigos:              # solo agrega "Código" si esa lista tiene códigos activos
                    columnas_dinamicas.append(f"Código {nombre_lista}")

            columnas = columnas_base + columnas_dinamicas
            self.tabla.setColumnCount(len(columnas))
            self.tabla.setHorizontalHeaderLabels(columnas)




            # 4) Preparar filas
            self.tabla.setRowCount(len(self.productos))

            for i, prod in enumerate(self.productos):
                # --- columnas fijas
                self.tabla.setItem(i, 0, QTableWidgetItem(str(prod.get("cip", ""))))
                self.tabla.setItem(i, 1, QTableWidgetItem(prod.get("descripcion", "")))
                self.tabla.setItem(i, 2, QTableWidgetItem(prod.get("unidad", "")))

                # --- Tipo de lista con QComboBox (persistente con PUT al cambiar)
                combo_tipo = QComboBox()
                combo_tipo.addItems(["Estándar", "Gourmet"])
                tipo_actual = prod.get("tipo_lista", "Estándar") or "Estándar"
                idx_tipo = combo_tipo.findText(tipo_actual, Qt.MatchFixedString)
                if idx_tipo >= 0:
                    combo_tipo.setCurrentIndex(idx_tipo)
                self.tabla.setCellWidget(i, 3, combo_tipo)

                cip_actual = prod.get("cip")
                combo_tipo.currentTextChanged.connect(
                    lambda valor, cip=cip_actual: self.actualizar_tipo_lista(cip, valor)
                )

                # --- NUEVA COLUMNA I.V.A. ---
                combo_iva = QComboBox()
                combo_iva.addItems(["Sí", "No"])
                valor_iva_actual = str(prod.get("iva", "No")) or "No"
                idx_iva = combo_iva.findText(valor_iva_actual, Qt.MatchFixedString)
                if idx_iva >= 0:
                    combo_iva.setCurrentIndex(idx_iva)
                else:
                    combo_iva.setCurrentIndex(1)  # por defecto "No"

                combo_iva.setStyleSheet("""
                    QComboBox {
                        background-color: #ffffff;
                        border: 1px solid #cbd5e1;
                        border-radius: 4px;
                        padding: 2px 6px;
                    }
                    QComboBox:hover {
                        border: 1px solid #4CAF50;
                    }
                """)
                self.tabla.setCellWidget(i, 4, combo_iva)

                # Guardar automáticamente al cambiar selección
                combo_iva.currentTextChanged.connect(
                    lambda valor, cip=cip_actual: self.actualizar_iva_producto(cip, valor)
                )


                # --- columnas dinámicas: precios y códigos ---
                precios_por_nombre = prod.get("precios", {}) or {}
                columna_actual = 5  # después de IVA

                for lista in self.listas:
                    nombre_lista = lista["nombre"]
                    datos_lista = precios_por_nombre.get(nombre_lista, {})

                    # Si el backend devuelve un número, lo convertimos a dict
                    if not isinstance(datos_lista, dict):
                        datos_lista = {"precio": datos_lista, "codigo_barras": ""}

                    # --- Precio ---
                    precio_val = datos_lista.get("precio", 0.00)
                    try:
                        precio_num = float(precio_val)
                    except (TypeError, ValueError):
                        precio_num = 0.00

                    item_precio = QTableWidgetItem(f"{precio_num:.2f}")
                    item_precio.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.tabla.setItem(i, columna_actual, item_precio)
                    columna_actual += 1

                    # --- Código (solo si la lista tiene códigos activos) ---
                    if nombre_lista in listas_con_codigos:
                        codigo_val = datos_lista.get("codigo_barras", "")
                        item_codigo = QTableWidgetItem(str(codigo_val))
                        item_codigo.setTextAlignment(Qt.AlignCenter)
                        self.tabla.setItem(i, columna_actual, item_codigo)
                        columna_actual += 1



            # 5) Ajustes visuales
            self.tabla.resizeColumnsToContents()
            self.tabla.horizontalHeader().setStretchLastSection(True)
            # Ensanchar un poco las columnas más largas
            self.tabla.setColumnWidth(1, max(220, self.tabla.columnWidth(1)))  # Descripción
            self.tabla.setColumnWidth(4, 80)  # Tipo de lista
            self.tabla.setColumnWidth(5, 120)  # Código de barras
            self.ajustar_columnas()

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def actualizar_tipo_lista(self, cip, tipo_lista):
        """Actualiza el tipo de lista en el backend al cambiar el combo."""
        try:
            data = {"tipo_lista": tipo_lista}
            resp = requests.put(f"{API_URL}/productos/actualizar_tipo/{cip}", json=data)
            if resp.status_code == 200:
                print(f"✅ Tipo de lista actualizado para {cip} -> {tipo_lista}")
            else:
                print(f"⚠️ Error al actualizar tipo de lista: {resp.text}")
        except Exception as e:
            print(f"❌ Error de conexión al actualizar tipo de lista: {e}")
    
    def actualizar_iva_producto(self, cip, valor_iva):
        """Actualiza el campo 'iva' del producto en el backend al cambiar el combo."""
        try:
            data = {"iva": valor_iva}
            resp = requests.put(f"{API_URL}/productos/actualizar_iva/{cip}", json=data)
            if resp.status_code == 200:
                print(f"✅ IVA actualizado para {cip} -> {valor_iva}")
            else:
                print(f"⚠️ Error al actualizar IVA: {resp.text}")
        except Exception as e:
            print(f"❌ Error de conexión al actualizar IVA: {e}")

    # =====================================================
    # 🔹 Filtrar productos por CIP o nombre
    # =====================================================
    def filtrar_productos(self):
        texto = self.search_input.text().strip().lower()
        for fila in range(self.tabla.rowCount()):
            cip = self.tabla.item(fila, 0).text().lower() if self.tabla.item(fila, 0) else ""
            descripcion = self.tabla.item(fila, 1).text().lower() if self.tabla.item(fila, 1) else ""
            visible = texto in cip or texto in descripcion
            self.tabla.setRowHidden(fila, not visible)
    def ajustar_columnas(self):
        """Ajusta automáticamente el ancho de las columnas de la tabla."""
        total_width = self.tabla.viewport().width()
        if total_width <= 0 or self.tabla.columnCount() == 0:
            return

        # Distribución básica: las primeras columnas más anchas
        proporciones = []
        headers = [self.tabla.horizontalHeaderItem(i).text() for i in range(self.tabla.columnCount())]
        for h in headers:
            if "Descripción" in h:
                proporciones.append(0.25)
            elif "CIP" in h:
                proporciones.append(0.10)
            elif "Unidad" in h:
                proporciones.append(0.08)
            elif "Tipo" in h or "I.V.A" in h:
                proporciones.append(0.07)
            else:
                proporciones.append(0.43 / max(1, (self.tabla.columnCount() - 4)))

        for i, p in enumerate(proporciones):
            self.tabla.setColumnWidth(i, int(total_width * p))
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            self.ajustar_columnas()

            # Ajustar botones superiores e inferiores
            ancho_total = self.width()
            for btn in self.findChildren(QPushButton):
                btn.setFixedHeight(38)
                btn.setMaximumWidth(int(ancho_total * 0.20))
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        except Exception as e:
            print("Error en resize ProductosTab:", e)


    # =====================================================
    # 🔹 Ajuste automático de columnas (se ejecuta al cargar)
    # =====================================================
    def ajustar_columnas(self):
        self.tabla.resizeColumnsToContents()
        for col in range(2, self.tabla.columnCount()):  # desde 'Unidad' en adelante
            self.tabla.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.tabla.horizontalHeader().setStretchLastSection(True)

    # =====================================================
    # 🔹 Guardar cambios
    # =====================================================
    def guardar_cambios(self):
        try:
            datos_actualizados = []
            for i in range(self.tabla.rowCount()):
                cip = self.tabla.item(i, 0).text()

                # --- Obtener Tipo de lista ---
                tipo_combo = self.tabla.cellWidget(i, 3)
                tipo_lista = tipo_combo.currentText() if tipo_combo else "Estándar"

                # --- Obtener valor del IVA ---
                combo_iva = self.tabla.cellWidget(i, 4)
                valor_iva = combo_iva.currentText() if combo_iva else "No"

                # --- Actualizar tipo de lista ---
                try:
                    resp_tipo = requests.put(
                        f"{API_URL}/productos/actualizar_tipo/{cip}",
                        json={"tipo_lista": tipo_lista}
                    )
                    if resp_tipo.status_code != 200:
                        print(f"⚠️ Error actualizando tipo de lista para {cip}: {resp_tipo.text}")
                except Exception as e:
                    print(f"❌ Error de conexión al actualizar tipo de lista: {e}")

                # --- Actualizar IVA ---
                try:
                    resp_iva = requests.put(
                        f"{API_URL}/productos/actualizar_iva/{cip}",
                        json={"iva": valor_iva}
                    )
                    if resp_iva.status_code != 200:
                        print(f"⚠️ Error actualizando IVA para {cip}: {resp_iva.text}")
                except Exception as e:
                    print(f"❌ Error de conexión al actualizar IVA: {e}")

                # --- Capturar precios y códigos dinámicos ---
                columna_actual = 5  # la primera columna dinámica después de IVA

                for lista in self.listas:
                    lista_id = lista["id"]

                    # --- Precio ---
                    try:
                        celda_precio = self.tabla.item(i, columna_actual)
                        if celda_precio and celda_precio.text().strip():
                            texto = (
                                celda_precio.text()
                                .replace("$", "")
                                .replace(",", "")
                                .strip()
                            )
                            precio = float(texto) if texto else 0.0
                        else:
                            precio = 0.0
                    except Exception as e:
                        print(f"[ERROR] No se pudo leer precio en fila {i}, columna {columna_actual}: {e}")
                        precio = 0.0

                    columna_actual += 1


                    # --- Código de barras ---
                    try:
                        celda_codigo = self.tabla.item(i, columna_actual)
                        codigo_barras = celda_codigo.text().strip() if celda_codigo else ""
                    except Exception as e:
                        print(f"[ERROR] No se pudo leer código de barras en fila {i}, columna {columna_actual}: {e}")
                        codigo_barras = ""

                    columna_actual += 1

                    # --- Agregar al paquete de actualización ---
                    datos_actualizados.append({
                        "lista_id": lista_id,
                        "cip": cip,
                        "precio": precio,
                        "codigo_barras": codigo_barras
                    })


            # --- Enviar todos los precios juntos ---
            resp = requests.put(f"{API_URL}/precios/actualizar_multiples", json=datos_actualizados)
            if resp.status_code == 200:
                QMessageBox.information(self, "Éxito", "Cambios guardados correctamente (precios, IVA y tipo de lista).")
            else:
                QMessageBox.warning(self, "Error", f"No se pudo actualizar precios: {resp.text}")

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))


    # =====================================================
    # 🔹 Agregar lista
    # =====================================================
    def agregar_lista(self):
        nombre, ok = QInputDialog.getText(self, "Nueva Lista", "Nombre de la lista:")
        if ok and nombre.strip():
            try:
                data = {"nombre": nombre.strip(), "descripcion": ""}
                resp = requests.post(f"{API_URL}/precios/listas_precios/nueva", json=data)
                if resp.status_code == 200:
                    QMessageBox.information(self, "Éxito", "Lista creada correctamente.")
                    self.cargar_datos()
                else:
                    QMessageBox.warning(self, "Error", f"No se pudo crear la lista: {resp.text}")
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    # =====================================================
    # 🔹 Eliminar lista
    # =====================================================
    def eliminar_lista(self):
        if not hasattr(self, "listas") or not self.listas:
            QMessageBox.warning(self, "Atención", "No hay listas de precios cargadas.")
            return

        nombres = [l["nombre"] for l in self.listas]
        nombre, ok = QInputDialog.getItem(self, "Eliminar Lista", "Selecciona la lista a eliminar:", nombres, 0, False)

        if ok and nombre:
            lista_id = next((l["id"] for l in self.listas if l["nombre"] == nombre), None)
            if not lista_id:
                QMessageBox.warning(self, "Error", "No se encontró el ID de la lista.")
                return

            confirm = QMessageBox.question(self, "Confirmar", f"¿Eliminar la lista '{nombre}'?",
                                           QMessageBox.Yes | QMessageBox.No)
            if confirm == QMessageBox.Yes:
                try:
                    resp = requests.delete(f"{API_URL}/precios/listas_precios/{lista_id}")
                    if resp.status_code == 200:
                        QMessageBox.information(self, "Éxito", "Lista eliminada correctamente.")
                        self.cargar_datos()
                    else:
                        QMessageBox.warning(self, "Error", f"No se pudo eliminar: {resp.text}")
                except Exception as e:
                    QMessageBox.critical(self, "Error", str(e))

    # =====================================================
    # 🔹 Importar productos desde Excel
    # =====================================================
    def importar_productos(self):
        ruta, _ = QFileDialog.getOpenFileName(self, "Seleccionar archivo Excel", "", "Archivos Excel (*.xlsx)")
        if not ruta:
            return

        try:
            with open(ruta, "rb") as f:
                resp = requests.post(f"{API_URL}/productos/importar", files={"file": f})
            if resp.status_code == 200:
                QMessageBox.information(self, "Éxito", "Productos importados correctamente.")
                self.cargar_datos()
            else:
                QMessageBox.warning(self, "Error", f"No se pudo importar: {resp.text}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # =====================================================
    # 🔹 Exportar productos a Excel
    # =====================================================
    def exportar_productos(self):
        try:
            resp = requests.get(f"{API_URL}/productos/exportar")
            if resp.status_code != 200:
                QMessageBox.warning(self, "Error", f"No se pudo exportar: {resp.text}")
                return

            ruta, _ = QFileDialog.getSaveFileName(self, "Guardar archivo", "productos.xlsx", "Archivos Excel (*.xlsx)")
            if not ruta:
                return

            with open(ruta, "wb") as f:
                f.write(resp.content)

            QMessageBox.information(self, "Éxito", "Archivo exportado correctamente.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # =====================================================
    # 🔹 Editar producto (usa mismo endpoint que agregar)
    # =====================================================
    def editar_producto(self):
        print("🟢 Iniciando edición de producto...")

        fila = self.tabla.currentRow()
        if fila < 0:
            QMessageBox.warning(self, "Seleccionar", "Selecciona un producto para editar.")
            return

        cip = self.tabla.item(fila, 0).text()
        producto = next((p for p in self.productos if str(p["cip"]) == str(cip)), None)
        if not producto:
            QMessageBox.warning(self, "Error", "No se pudo obtener el producto seleccionado.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Editar Producto - {cip}")
        dialog.resize(550, 650)
        dialog.setModal(True)

        # --- Layout principal con scroll ---
        main_layout = QVBoxLayout(dialog)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        form_layout = QFormLayout(scroll_content)
        form_layout.setContentsMargins(20, 20, 20, 20)
        form_layout.setSpacing(10)

        # --- Campos base ---
                # --- Campos base ---
        input_cip = QLineEdit(cip)
        input_cip.setReadOnly(True)

        input_descripcion = QLineEdit(producto.get("descripcion", ""))
        input_unidad = QLineEdit(producto.get("unidad", ""))

        # --- Tipo de lista como QComboBox ---
        combo_tipo_lista = QComboBox()
        combo_tipo_lista.addItems(["Estándar", "Gourmet"])
        tipo_actual = str(producto.get("tipo_lista", "Estándar")).strip()
        idx = combo_tipo_lista.findText(tipo_actual, Qt.MatchFixedString)
        if idx >= 0:
            combo_tipo_lista.setCurrentIndex(idx)
        
        # --- Campo I.V.A. ---
        combo_iva = QComboBox()
        combo_iva.addItems(["Sí", "No"])
        valor_iva = str(producto.get("iva", "No")) or "No"
        idx = combo_iva.findText(valor_iva, Qt.MatchFixedString)
        if idx >= 0:
            combo_iva.setCurrentIndex(idx)

        # --- Añadir al formulario en orden lógico ---
        form_layout.addRow("CIP:", input_cip)
        form_layout.addRow("Descripción:", input_descripcion)
        form_layout.addRow("Unidad:", input_unidad)
        form_layout.addRow("Tipo de lista:", combo_tipo_lista)
        form_layout.addRow("I.V.A.:", combo_iva)
        # --- Checkbox y campo para código de barras ---
        chk_codigo_barras = QCheckBox("Agregar / editar código de barras")
        input_codigo_barras = QLineEdit(producto.get("codigo_barras", ""))
        input_codigo_barras.setPlaceholderText("Ejemplo: 7501031311309")
        input_codigo_barras.setEnabled(bool(input_codigo_barras.text()))
        chk_codigo_barras.setChecked(bool(input_codigo_barras.text()))

        form_layout.addRow(QLabel("<b>Listas de precios:</b>"))


        # === Campos dinámicos de precios ===
        campos_precios = []
        try:
            print("📡 Solicitando listas de precios al servidor...")
            resp = requests.get(f"{API_URL}/precios/listas_precios/")
            print("📡 Estado HTTP:", resp.status_code)

            if resp.status_code == 200:
                listas = resp.json()
                print(f"📋 {len(listas)} listas encontradas.")

                if listas:
                    for lista in listas:
                        # Precio actual (con signo desde el inicio)
                        precios_actuales = producto.get("precios", {})
                        valor_lista = precios_actuales.get(lista["nombre"], {})
                        if isinstance(valor_lista, dict):
                            precio_actual = valor_lista.get("precio", 0.00)
                            codigo_actual = valor_lista.get("codigo_barras", "")
                        else:
                            # compatibilidad con versiones anteriores
                            precio_actual = valor_lista or 0.00
                            codigo_actual = ""

                        # Formatear el precio inicial
                        texto_inicial = f"${precio_actual:,.2f}" if precio_actual else "$0.00"


                        campo = QLineEdit(texto_inicial)
                        campo.setAlignment(Qt.AlignRight)
                        campo.setPlaceholderText("0.00")
                        campo.setStyleSheet("""
                            QLineEdit {
                                padding: 4px 6px;
                                border: 1px solid #a6b5c6;
                                border-radius: 4px;
                                background-color: #ffffff;
                            }
                            QLineEdit:focus {
                                border: 1px solid #4a90e2;
                                background-color: #f8fbff;
                            }
                        """)

                        # === Función de formato monetario ===
                        def formatear_moneda(texto):
                            texto = texto.replace("$", "").replace(",", "").strip()
                            if not texto:
                                return "$0.00"
                            try:
                                num = float(texto)
                                return f"${num:,.2f}"
                            except ValueError:
                                return "$0.00"

                        # === Formatear automáticamente al perder foco ===
                        def al_perder_foco(campo=campo, nombre=lista["nombre"]):
                            texto = campo.text().strip()
                            print(f"✏️  {nombre}: valor antes de formato -> '{texto}'")
                            campo.blockSignals(True)
                            campo.setText(formatear_moneda(texto))
                            campo.blockSignals(False)
                            print(f"💲  {nombre}: valor formateado -> '{campo.text()}'")

                        campo.editingFinished.connect(al_perder_foco)

                        # --- Contenedor por lista ---
                        container = QWidget()
                        vbox = QVBoxLayout(container)
                        vbox.setContentsMargins(0, 0, 0, 0)
                        vbox.setSpacing(4)

                        # === Campo de precio ===
                        vbox.addWidget(QLabel(f"Precio {lista['nombre']}:"))
                        vbox.addWidget(campo)

                        # === Checkbox y campo de código de barras ===
                        chk_cb = QCheckBox("Agregar / editar código de barras")
                        campo_cb = QLineEdit()
                        campo_cb.setPlaceholderText("Ejemplo: 7501010011223")
                        campo_cb.setEnabled(False)

                        # Valor actual si existe
                        codigo_actual = precios_actuales.get(lista["nombre"], {}).get("codigo_barras", "")
                        if codigo_actual:
                            chk_cb.setChecked(True)
                            campo_cb.setText(codigo_actual)
                            campo_cb.setEnabled(True)

                        chk_cb.stateChanged.connect(lambda estado, campo=campo_cb: campo.setEnabled(estado == Qt.Checked))

                        vbox.addWidget(chk_cb)
                        vbox.addWidget(campo_cb)

                        form_layout.addRow(container)

                        # --- Guardamos ambos campos ---
                        campos_precios.append((lista["id"], campo, campo_cb, chk_cb))

                else:
                    form_layout.addRow(QLabel("No hay listas de precios registradas."))
            else:
                form_layout.addRow(QLabel("⚠️ No se pudieron cargar las listas de precios."))
        except Exception as e:
            print("❌ Error al cargar listas de precios:", e)
            form_layout.addRow(QLabel(f"❌ Error al conectar con el servidor: {e}"))

        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

        # --- Botones ---
        botones = QHBoxLayout()
        btn_guardar = QPushButton(" Guardar Cambios")
        btn_guardar.setIcon(qta.icon("mdi.content-save"))
        btn_cancelar = QPushButton(" Cancelar")
        btn_cancelar.setIcon(qta.icon("mdi.close-circle"))
        botones.addStretch()
        botones.addWidget(btn_guardar)
        botones.addWidget(btn_cancelar)
        main_layout.addLayout(botones)

        btn_cancelar.clicked.connect(dialog.reject)

        # --- Guardar edición ---
        def guardar_edicion():
            print("💾 Ejecutando guardar_edicion()...")

            cip_val = input_cip.text().strip()
            descripcion = input_descripcion.text().strip()
            unidad = input_unidad.text().strip()
            tipo_lista_val = combo_tipo_lista.currentText().strip()

            # --- Preparar payload con todos los datos ---
            data_producto = {
                "cip": cip_val,
                "descripcion": descripcion,
                "unidad": unidad,
                "tipo_lista": tipo_lista_val,
                "iva": combo_iva.currentText().strip(),
                "precios": {},
                "codigo_barras": input_codigo_barras.text().strip() if chk_codigo_barras.isChecked() else ""
            }

            # --- Capturar precios y códigos ---
            for lista_id, campo_precio, campo_cb, chk_cb in campos_precios:
                texto = campo_precio.text().replace("$", "").replace(",", "").strip()
                try:
                    precio = float(texto) if texto else 0.00
                except ValueError:
                    precio = 0.00

                codigo_barras = campo_cb.text().strip() if chk_cb.isChecked() else ""

                data_producto["precios"][lista_id] = {
                    "precio": precio,
                    "codigo_barras": codigo_barras
                }


            print("📤 Enviando PUT con datos:", data_producto)

            # --- Mostrar progreso (animación verde) ---
            progress = QProgressDialog(dialog)
            progress.setWindowTitle("Guardando producto...")
            progress.setLabelText("Procesando cambios, por favor espera...")
            progress.setCancelButton(None)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            progress.setRange(0, 100)

            progress.setStyleSheet("""
                QProgressBar {
                    border: 2px solid #4CAF50;
                    border-radius: 6px;
                    text-align: center;
                    height: 20px;
                }
                QProgressBar::chunk {
                    background-color: #4CAF50;
                    width: 20px;
                    margin: 1px;
                }
                QLabel {
                    color: #2d3436;
                    font-weight: bold;
                }
            """)

            progress.show()
            QApplication.processEvents()

            valor = 0
            def animar_barra():
                nonlocal valor
                valor = (valor + 5) % 100
                progress.setValue(valor)

            timer = QTimer()
            timer.timeout.connect(animar_barra)
            timer.start(100)

            # --- Enviar PUT al servidor ---
            try:
                resp = requests.put(f"{API_URL}/productos/{cip_val}", json=data_producto)
                print("📡 Respuesta PUT:", resp.status_code)
            except Exception as e:
                timer.stop()
                progress.close()
                QMessageBox.critical(dialog, "Error", f"Error de conexión:\n{e}")
                return

            if resp.status_code != 200:
                timer.stop()
                progress.close()
                QMessageBox.warning(dialog, "Error", f"No se pudo actualizar el producto:\n{resp.text}")
                return

            # --- Si todo bien, refrescar tabla ---
            self.cargar_datos()

            # --- Cierre elegante ---
            def finalizar():
                timer.stop()
                progress.setValue(100)
                progress.close()
                QMessageBox.information(dialog, "Éxito", "Producto actualizado correctamente.")
                dialog.accept()

            QTimer.singleShot(700, finalizar)

        btn_guardar.clicked.connect(guardar_edicion)
        dialog.exec_()

    # =====================================================
    # 🔹 Eliminar producto
    # =====================================================
    def eliminar_producto(self):
        fila = self.tabla.currentRow()
        if fila == -1:
            QMessageBox.warning(self, "Selecciona un producto", "Selecciona un producto para eliminar.")
            return

        cip = self.tabla.item(fila, 0).text()
        confirm = QMessageBox.question(
            self,
            "Confirmar eliminación",
            f"¿Seguro que deseas eliminar el producto con CIP '{cip}'?",
            QMessageBox.Yes | QMessageBox.No
        )

        if confirm == QMessageBox.Yes:
            try:
                resp = requests.delete(f"{API_URL}/productos/{cip}")
                if resp.status_code == 200:
                    QMessageBox.information(self, "Éxito", "Producto eliminado correctamente.")
                    self.cargar_datos()
                else:
                    QMessageBox.warning(self, "Error", f"No se pudo eliminar el producto:\n{resp.text}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"No se pudo conectar al servidor:\n{e}")
    
    # =====================================================
    # 🔹 Abrir diálogo para agregar producto
    # =====================================================
    def abrir_dialogo_agregar_producto(self):
        """
        Abre la ventana 'AgregarProductoDialog' y recarga la tabla al guardar.
        """
        dialog = AgregarProductoDialog(self)
        resultado = dialog.exec_()
        if resultado == QDialog.Accepted:
            # Recargar la tabla completa desde la API
            self.cargar_datos()
            QMessageBox.information(self, "Producto agregado",
                                    "El nuevo producto se ha guardado correctamente y la lista se ha actualizado.")


class ProductosWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gestión de Productos")
        self.resize(800, 500)

        layout = QVBoxLayout(self)

        # --- 🔹 FILTRO SUPERIOR ---
        filtro_layout = QHBoxLayout()
        filtro_layout.addStretch()

        filtro_layout.addWidget(QLabel("CIP:"))
        self.input_cip = QLineEdit()
        self.input_cip.setFixedWidth(150)
        filtro_layout.addWidget(self.input_cip)

        filtro_layout.addWidget(QLabel("Nombre:"))
        self.input_nombre = QLineEdit()
        self.input_nombre.setFixedWidth(200)
        filtro_layout.addWidget(self.input_nombre)

        self.btn_limpiar_filtro = QPushButton("Eliminar Filtro")
        self.btn_limpiar_filtro.setFixedWidth(120)
        self.btn_limpiar_filtro.clicked.connect(self.limpiar_filtro)
        filtro_layout.addWidget(self.btn_limpiar_filtro)

        filtro_layout.addStretch()
        layout.addLayout(filtro_layout)

        # --- Tabla ---
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)

        # --- Botones ---
        botones_layout = QHBoxLayout()
        botones_layout.addStretch()

        self.btn_importar = QPushButton("Importar")
        self.btn_importar.setFixedWidth(100)
        self.btn_importar.clicked.connect(self.importar_productos)

        self.btn_exportar = QPushButton("Exportar")
        self.btn_exportar.setFixedWidth(100)
        self.btn_exportar.clicked.connect(self.exportar_productos)

        self.btn_editar = QPushButton("Editar")
        self.btn_editar.setFixedWidth(100)
        self.btn_editar.clicked.connect(self.editar_producto)

        self.btn_eliminar = QPushButton("Eliminar")
        self.btn_eliminar.setFixedWidth(100)
        self.btn_eliminar.clicked.connect(self.eliminar_producto)

        botones_layout.addWidget(self.btn_importar)
        botones_layout.addWidget(self.btn_exportar)
        botones_layout.addWidget(self.btn_editar)
        botones_layout.addWidget(self.btn_eliminar)
        botones_layout.addStretch()

        layout.addLayout(botones_layout)

        # 🔹 Cache de productos
        self.todos_productos = []

        # --- Conectar filtros en vivo ---
        self.input_cip.textChanged.connect(self.aplicar_filtro)
        self.input_nombre.textChanged.connect(self.aplicar_filtro)
        
        # 🔹 Aplicar estilo global (cursor + sombra) a todos los botones del widget
        aplicar_estilo_global(self.findChildren(QPushButton))

        # --- Cargar productos al inicio ---
        self.cargar_productos()

    def cargar_productos(self):
        try:
            resp = requests.get(f"{API_URL}/productos/")
            if resp.status_code == 200:
                self.todos_productos = resp.json()  # guardar en memoria
                self.llenar_tabla(self.todos_productos)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def llenar_tabla(self, productos):
        if not productos:
            self.table.setRowCount(0)
            return

        columnas = list(productos[0].keys())
        self.table.setColumnCount(len(columnas))
        self.table.setHorizontalHeaderLabels(columnas)
        self.table.setRowCount(len(productos))

        for row_idx, prod in enumerate(productos):
            for col_idx, key in enumerate(columnas):
                item = QTableWidgetItem(str(prod[key] or ""))
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_idx, col_idx, item)

    # --- 🔹 Filtro en memoria ---
    def aplicar_filtro(self):
        cip = self.input_cip.text().strip().lower()
        nombre = self.input_nombre.text().strip().lower()

        filtrados = []
        for p in self.todos_productos:
            if cip and cip not in str(p.get("cip", "")).lower():
                continue
            if nombre and nombre not in str(p.get("descripcion", "")).lower():
                continue
            filtrados.append(p)

        self.llenar_tabla(filtrados)

    def limpiar_filtro(self):
        self.input_cip.clear()
        self.input_nombre.clear()
        self.llenar_tabla(self.todos_productos)

    def importar_productos(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Seleccionar Excel", "", "Archivos Excel (*.xlsx)")
        if not file_path:
            return
        with open(file_path, "rb") as f:
            files = {"file": (file_path, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
            resp = requests.post(f"{API_URL}/productos/importar", files=files)
            if resp.status_code == 200:
                QMessageBox.information(self, "Éxito", "Productos importados correctamente.")
                self.cargar_productos()
            else:
                QMessageBox.warning(self, "Error", f"No se pudo importar: {resp.text}")

    def exportar_productos(self):
        resp = requests.get(f"{API_URL}/productos/exportar")
        if resp.status_code == 200:
            file_path, _ = QFileDialog.getSaveFileName(self, "Guardar Excel", "productos.xlsx", "Archivos Excel (*.xlsx)")
            if file_path:
                with open(file_path, "wb") as f:
                    f.write(resp.content)
                QMessageBox.information(self, "Éxito", "Productos exportados correctamente.")
        else:
            QMessageBox.warning(self, "Error", f"No se pudo exportar: {resp.text}")

    def editar_producto(self):
        fila = self.table.currentRow()
        if fila < 0:
            QMessageBox.warning(self, "Aviso", "Selecciona un producto para editar")
            return

        cip = self.table.item(fila, 0).text()
        descripcion = self.table.item(fila, 1).text()
        unidad = self.table.item(fila, 2).text()

        # Pequeño formulario
        nuevo_desc, ok = QInputDialog.getText(self, "Editar Producto", "Descripción:", text=descripcion)
        if ok:
            nuevo_unidad, ok2 = QInputDialog.getText(self, "Editar Producto", "Unidad:", text=unidad)
            if ok2:
                data = {"cip": cip, "descripcion": nuevo_desc, "unidad": nuevo_unidad}
                resp = requests.post(f"{API_URL}/productos/agregar", json=data)
                if resp.status_code == 200:
                    QMessageBox.information(self, "Éxito", "Producto actualizado correctamente.")
                    self.cargar_productos()

    def eliminar_producto(self):
        fila = self.table.currentRow()
        if fila < 0:
            QMessageBox.warning(self, "Aviso", "Selecciona un producto para eliminar")
            return

        cip = self.table.item(fila, 0).text()
        confirm = QMessageBox.question(
            self, "Confirmar", f"¿Eliminar producto {cip}?",
            QMessageBox.Yes | QMessageBox.No
        )

        if confirm == QMessageBox.Yes:
            resp = requests.delete(f"{API_URL}/productos/{cip}")
            if resp.status_code == 200:
                QMessageBox.information(self, "Éxito", "Producto eliminado.")
                self.cargar_productos()
            else:
                QMessageBox.warning(self, "Error", f"No se pudo eliminar: {resp.text}")

class FacturacionTab(QWidget):
    def __init__(self, modo_edicion=False, datos_factura=None, parent=None):
        super().__init__(parent)

        self.modo_edicion = modo_edicion
        self.datos_factura = datos_factura or {}

        print("🧩 [DEBUG] FacturacionTab inicializado")
        print(f"   → modo_edicion: {self.modo_edicion}")
        print(f"   → datos_factura: {bool(self.datos_factura)}")
        self.setWindowTitle("Facturación")
        self.resize(1200, 1000)

        layout_principal = QVBoxLayout(self)
        layout_principal.setSpacing(8)
        layout_principal.setContentsMargins(15, 15, 15, 10)

        # =====================================================
        # 🔹 ENCABEZADO DE FACTURA (NEGRITA REAL Y ALINEADO)
        # =====================================================
        encabezado = QGroupBox("Datos de la factura")
        encabezado.setFlat(False)
        encabezado.setAlignment(Qt.AlignLeft)

        # 🔹 Forzar fuente en negrita y más grande
        titulo_font = QFont()
        titulo_font.setBold(True)
        titulo_font.setPointSize(16)
        encabezado.setFont(titulo_font)

        # 🔹 Estilo visual del bloque
        encabezado.setStyleSheet("""
            QGroupBox {
                color: #000000;
                margin-top: 20px;
                margin-bottom: 15px;
                border: 2px solid #000000;
                border-radius: 6px;
                padding: 20px;
                background-color: #f9fafb;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 15px;
                top: 10px;
                padding: 5px 8px;
                color: #000000;
            }
        """)

        # =====================================================
        # 🧩 GRID PRINCIPAL (Comanda / Cliente)
        # =====================================================
        grid = QGridLayout()
        grid.setContentsMargins(5, 5, 5, 5)
        grid.setHorizontalSpacing(15)
        grid.setVerticalSpacing(10)

        # --- Campos principales ---
        self.input_comanda = QLineEdit()
        self.input_comanda.setPlaceholderText("Comanda o número de orden")
        self.input_comanda.setFixedWidth(160)
        self.input_comanda.editingFinished.connect(self.cargar_comanda)

        self.input_folio = QLineEdit()
        self.input_folio.setPlaceholderText("Folio de factura")
        self.input_folio.setFixedWidth(150)
        self.input_folio.setText(self._generar_folio_autonumerico())  # 🔹 genera el siguiente número


        self.input_fecha = QDateEdit(QDate.currentDate())
        self.input_fecha.setCalendarPopup(True)
        self.input_fecha.setFixedWidth(150)

        self.combo_empresa = QComboBox()
        self.combo_empresa.setFixedWidth(200)
        self.combo_empresa.currentIndexChanged.connect(self.actualizar_folio_automatico)
        self.cargar_empresas()

        self.input_cliente = QLineEdit()
        self.input_cliente.setPlaceholderText("Código de cliente")
        self.input_cliente.setFixedWidth(150)
        self.input_cliente.returnPressed.connect(self.buscar_cliente)
        self.input_cliente.editingFinished.connect(self.buscar_cliente)

        # 🔹 Cuando cambia el número de cliente, actualizar automáticamente el folio
        self.input_cliente.textChanged.connect(self.actualizar_folio_automatico)

        self.input_nombre_cliente = QLineEdit()
        self.input_nombre_cliente.setPlaceholderText("Nombre del cliente")
        self.input_nombre_cliente.setReadOnly(True)
        self.input_nombre_cliente.setFixedWidth(250)

        self.input_rfc = QLineEdit()
        self.input_rfc.setReadOnly(True)
        self.input_rfc.setFixedWidth(180)

        # --- Distribución del grid ---
        grid.addWidget(QLabel("Comanda:"), 0, 0)
        grid.addWidget(self.input_comanda, 0, 1)
        grid.addWidget(QLabel("Folio:"), 0, 2)
        grid.addWidget(self.input_folio, 0, 3)
        grid.addWidget(QLabel("Fecha:"), 0, 4)
        grid.addWidget(self.input_fecha, 0, 5)
        grid.addWidget(QLabel("Empresa:"), 0, 6)
        grid.addWidget(self.combo_empresa, 0, 7)

        grid.addWidget(QLabel("Cliente:"), 1, 0)
        grid.addWidget(self.input_cliente, 1, 1)
        grid.addWidget(self.input_nombre_cliente, 1, 2, 1, 3)
        grid.addWidget(QLabel("RFC:"), 1, 5)
        grid.addWidget(self.input_rfc, 1, 6, 1, 2)

        # =====================================================
        # 🧩 FILA INFERIOR - Lista de precios, vendedor, descuento, rebanado
        # =====================================================
        self.combo_lista_precios = QComboBox()
        self.combo_lista_precios.addItems(["Lista General"])
        self.combo_lista_precios.setFixedWidth(180)

        self.input_vendedor = QLineEdit()
        self.input_vendedor.setPlaceholderText("Vendedor asignado")
        self.input_vendedor.setFixedWidth(200)

        lbl_descuento_cliente = QLabel("Descuento:")
        lbl_descuento_cliente.setStyleSheet("font-weight: bold; color: #1f2937;")
        self.input_descuento_cliente = QLineEdit("0.00 %")
        self.input_descuento_cliente.setAlignment(Qt.AlignRight)
        self.input_descuento_cliente.setFixedWidth(100)
        self.input_descuento_cliente.setReadOnly(True)

        lbl_cargo_rebanado = QLabel("Cargo por rebanado:")
        lbl_cargo_rebanado.setStyleSheet("font-weight: bold; color: #1f2937;")
        self.input_cargo_rebanado = QLineEdit("8.00 %")
        self.input_cargo_rebanado.setAlignment(Qt.AlignRight)
        self.input_cargo_rebanado.setFixedWidth(100)
        self.input_cargo_rebanado.editingFinished.connect(self.formatear_cargo_rebanado)

        # 🔹 Layout horizontal independiente para la fila inferior
        fila_inferior = QHBoxLayout()
        fila_inferior.setContentsMargins(0, 0, 0, 0)
        fila_inferior.setSpacing(25)

        fila_inferior.addWidget(QLabel("Lista de precios:"))
        fila_inferior.addWidget(self.combo_lista_precios)
        fila_inferior.addSpacing(25)
        fila_inferior.addWidget(QLabel("Vendedor:"))
        fila_inferior.addWidget(self.input_vendedor)
        fila_inferior.addSpacing(50)  # 🔹 aumenta un poco el aire entre vendedor y descuento
        fila_inferior.addWidget(lbl_descuento_cliente)
        fila_inferior.addWidget(self.input_descuento_cliente)
        fila_inferior.addSpacing(10)
        fila_inferior.addWidget(lbl_cargo_rebanado)
        fila_inferior.addWidget(self.input_cargo_rebanado)
        fila_inferior.addStretch(1)

        # =====================================================
        # 🔹 Layout general del encabezado
        # =====================================================
        layout_encabezado = QVBoxLayout()
        layout_encabezado.setContentsMargins(10, 10, 10, 10)
        layout_encabezado.setSpacing(8)
        layout_encabezado.addLayout(grid)
        layout_encabezado.addLayout(fila_inferior)

        encabezado.setLayout(layout_encabezado)

        # 🔹 Ajustar tamaño general del bloque
        encabezado.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        encabezado.setFixedSize(1150, encabezado.sizeHint().height() + 40)

        layout_principal.addWidget(encabezado, 0, alignment=Qt.AlignHCenter)


        # =====================================================
        # 🔹 TABLA CON 15 FILAS EXACTAS Y ALINEADA
        # =====================================================
        tabla_container = QWidget()
        tabla_layout = QVBoxLayout(tabla_container)
        tabla_layout.setContentsMargins(0, 0, 0, 0)
        tabla_layout.setSpacing(0)

        self.header_spec = [
            ("CIP", 80),           # 0
            ("Descripción", 300),  # 1
            ("Cantidad", 100),     # 2
            ("Pzas", 70),          # 3
            ("Precio", 100),       # 4  (precio base según lista)
            ("Precio Real", 110),  # 5  (calculado con reglas Gourmet/Estándar)
            ("Rebanado", 100),     # 6  (QComboBox Sí/No)
            ("Otro Precio", 110),  # 7  (editable manual)
            ("Importe", 120)       # 8
        ]


        # --- Encabezado manual ---
        header_row = QHBoxLayout()
        header_row.setSpacing(0)
        header_row.setContentsMargins(0, 0, 0, 0)
        self.header_labels = []

        for text, width in self.header_spec:
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFixedHeight(25)
            lbl.setMinimumWidth(width)
            lbl.setStyleSheet("""
                QLabel {
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                    stop:0 #f8f9fb, stop:1 #e3e5e8);
                    border: 1px solid #bfc3c7;
                    font-weight: bold;
                    color: #111827;
                    padding: 3px 0;
                }
            """)
            self.header_labels.append(lbl)
            header_row.addWidget(lbl)

        header_widget = QWidget()
        header_widget.setLayout(header_row)
        tabla_layout.addWidget(header_widget)

        # --- Configurar tabla ---
        self.tabla = QTableWidget()
        self.tabla.setColumnCount(len(self.header_spec))
        self.tabla.setRowCount(15)
        self.tabla.horizontalHeader().hide()
        # 🔧 Definir encabezados internos (aunque estén ocultos visualmente)
        self.tabla.setHorizontalHeaderLabels([
            "CIP",
            "Descripción",
            "Cantidad",
            "Piezas",
            "Precio",
            "Precio Real",
            "Rebanado",
            "Otro Precio",
            "Importe"
        ])
        self.tabla.verticalHeader().hide()
        self.tabla.setAlternatingRowColors(True)
        # 🔹 Evitar que se iluminen celdas o filas al seleccionar
        self.tabla.setSelectionMode(QAbstractItemView.NoSelection)
        self.tabla.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.tabla.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tabla.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tabla.setFrameStyle(QFrame.NoFrame)
        self.tabla.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # --- Celdas vacías ---
        for r in range(15):
            for c in range(self.tabla.columnCount()):
                item = QTableWidgetItem("")
                item.setTextAlignment(Qt.AlignCenter)
                self.tabla.setItem(r, c, item)
        
        # --- Agregar combobox "Rebanado" en cada fila (columna 6) ---
        for r in range(self.tabla.rowCount()):
            combo_rebanado = QComboBox()
            combo_rebanado.addItem("")     # 🔹 opción vacía por defecto
            combo_rebanado.addItems(["No", "Sí"])
            combo_rebanado.setCurrentIndex(0)  # 🔹 deja vacío al iniciar
            combo_rebanado.setFixedHeight(26)
            combo_rebanado.setStyleSheet("""
                QComboBox {
                    background-color: #ffffff;
                    border: 1px solid #cbd5e1;
                    border-radius: 4px;
                    padding: 2px 6px;
                }
            """)
            self.tabla.setCellWidget(r, 6, combo_rebanado)
            # 🔹 Conectar cambio de valor al recalculo automático
            combo_rebanado.currentIndexChanged.connect(lambda _, fila=r: self._actualizar_por_rebanado(fila))



        # 🔹 Calcular altura exacta según tamaño real de filas
        altura_fila = self.tabla.verticalHeader().defaultSectionSize()
        altura_encabezado = 25
        altura_total = (altura_fila * 15) + altura_encabezado + 2

        self.tabla.setFixedHeight(altura_total)
        tabla_container.setFixedHeight(altura_total + 2)
        tabla_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        tabla_layout.addWidget(self.tabla)
        layout_principal.addWidget(tabla_container)
        # 🔹 Conectar la búsqueda automática de producto al editar la columna CIP
        self.tabla.cellChanged.connect(self.buscar_producto_por_cip)
        self.tabla.cellChanged.connect(self._on_cell_changed_otro_precio)
        self.tabla.cellChanged.connect(self._on_cell_changed_cantidad)
        self.tabla.cellChanged.connect(self._on_cell_editada_recalculo)  # ← NUEVA
        self.input_folio.setText(self.obtener_siguiente_folio())


        # =====================================================
        # 🔹 BLOQUE INFERIOR (BOTONES + TOTALES)
        # =====================================================
        bloque_inferior = QWidget()
        bloque_inferior.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        bloque_inferior.setFixedHeight(130)

        layout_inferior = QHBoxLayout(bloque_inferior)
        layout_inferior.setContentsMargins(40, 15, 40, 15)
        layout_inferior.setSpacing(40)

        # =====================================================
        # 🟥🟩🟦 SECCIÓN DE BOTONES (contenedor izquierdo)
        # =====================================================
        botones_widget = QWidget()
        botones_layout = QHBoxLayout(botones_widget)
        botones_layout.setContentsMargins(0, 0, 0, 0)
        botones_layout.setSpacing(15)

        botones_info = [
            ("Eliminar producto", "mdi.delete-outline", "#dc2626"),
            ("Guardar factura", "mdi.content-save-outline", "#15803d"),
            ("Cancelar", "mdi.close-circle-outline", "#6b7280"),
        ]

        def oscurecer_color(hex_color, factor):
            """Devuelve una versión más oscura del color (factor < 1)."""
            hex_color = hex_color.lstrip("#")
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            r = int(r * factor)
            g = int(g * factor)
            b = int(b * factor)
            return f"#{r:02x}{g:02x}{b:02x}"

        for texto, icono, color in botones_info:
            btn = QPushButton(qta.icon(icono), texto)
            btn.setIconSize(QSize(22, 22))
            btn.setMinimumWidth(180)
            btn.setFixedHeight(46)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color};
                    color: white;
                    border: none;
                    border-radius: 6px;
                    font-weight: 600;
                    font-size: 10.5pt;
                    padding: 6px 14px;
                }}
                QPushButton:hover {{
                    background-color: {oscurecer_color(color, 0.9)};
                }}
                QPushButton:pressed {{
                    background-color: {oscurecer_color(color, 0.8)};
                }}
            """)
            botones_layout.addWidget(btn)
        # =====================================================
        # 🔗 Conectar eventos de los botones
        # =====================================================
        for btn in botones_widget.findChildren(QPushButton):
            texto_btn = btn.text().lower()

            if "vista previa" in texto_btn:
                btn.clicked.connect(lambda: self.guardar_factura(previsualizacion=True))

            elif "guardar" in texto_btn:
                # Guardar factura → vista previa completa (normal o edición)
                btn.clicked.connect(lambda: self.guardar_factura(previsualizacion=False))

            elif "eliminar" in texto_btn:
                btn.clicked.connect(self.eliminar_producto_seleccionado)

            elif "cancelar" in texto_btn:
                btn.clicked.connect(self.cancelar_factura)
        # Centrado horizontal en su bloque
        botones_layout.insertStretch(0, 1)
        botones_layout.addStretch(1)

        # =====================================================
        # 💵 SECCIÓN DE TOTALES (derecha, más espaciada)
        # =====================================================
        totales_widget = QWidget()
        totales_layout = QGridLayout(totales_widget)
        totales_layout.setContentsMargins(20, 10, 20, 10)
        totales_layout.setHorizontalSpacing(45)
        totales_layout.setVerticalSpacing(5)  # 🔹 Más separación entre Subtotal / IVA / Total
        totales_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        label_style = """
            QLabel {
                font-weight: bold;
                color: #1f2937;
                font-size: 11pt;
                min-width: 100px;
            }
        """

        lbl_sub = QLabel("Subtotal:")
        self.lbl_desc = QLabel("Descuento:")
        lbl_iva = QLabel("IVA:")
        lbl_total = QLabel("Total:")

        self.input_subtotal = QLineEdit("0.00")
        self.input_descuento = QLineEdit("0.00")
        self.input_iva = QLineEdit("0.00")
        self.input_total = QLineEdit("0.00")


        for campo in [self.input_subtotal, self.input_descuento, self.input_iva, self.input_total]:
            campo.setAlignment(Qt.AlignRight)
            campo.setReadOnly(True)
            campo.setFixedHeight(30)
            campo.setFixedWidth(280)
            campo.setStyleSheet("""
                QLineEdit {
                    background-color: #f8fafc;
                    border: 1px solid #b8bcc0;
                    border-radius: 6px;
                    padding: 6px 10px;
                    font-size: 11pt;
                    font-weight: 600;
                    color: #2b2b2b;
                }
            """)

        totales_layout.addWidget(lbl_sub, 0, 0, alignment=Qt.AlignRight)
        totales_layout.addWidget(self.input_subtotal, 0, 1)

        totales_layout.addWidget(self.lbl_desc, 1, 0, alignment=Qt.AlignRight)
        totales_layout.addWidget(self.input_descuento, 1, 1)

        totales_layout.addWidget(lbl_iva, 2, 0, alignment=Qt.AlignRight)
        totales_layout.addWidget(self.input_iva, 2, 1)

        totales_layout.addWidget(lbl_total, 3, 0, alignment=Qt.AlignRight)
        totales_layout.addWidget(self.input_total, 3, 1)


        # =====================================================
        # 🔹 BOTONES alineados a la altura de "Lista de precios"
        # =====================================================
        offset_x = 440  # Ajusta este valor si quieres más a la derecha

        botones_container_alineado = QWidget()
        botones_container_layout = QHBoxLayout(botones_container_alineado)
        botones_container_layout.setContentsMargins(offset_x, 0, 0, 0)
        botones_container_layout.addWidget(botones_widget, alignment=Qt.AlignLeft | Qt.AlignVCenter)

        # =====================================================
        # 🔹 BLOQUE FINAL (botones + totales)
        # =====================================================
        layout_inferior = QHBoxLayout()
        layout_inferior.setContentsMargins(30, 8, 30, 10)
        layout_inferior.setSpacing(60)

        layout_inferior.addWidget(botones_container_alineado, alignment=Qt.AlignLeft | Qt.AlignVCenter)
        layout_inferior.addStretch(1)
        # 🔹 Espaciador dinámico para evitar salto visual
        espaciador = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)
        layout_inferior.addItem(espaciador)

        layout_inferior.addWidget(totales_widget, alignment=Qt.AlignRight | Qt.AlignBottom)


        bloque_inferior = QWidget()
        bloque_inferior.setLayout(layout_inferior)
        bloque_inferior.setFixedHeight(totales_widget.sizeHint().height() + 40)

        layout_principal.addWidget(bloque_inferior)
        self.bloque_inferior = bloque_inferior  # 🔹 Guardamos referencia para recalculos posteriores





        # =====================================================
        # 🎨 Ajuste visual del encabezado y tabla (sin depuración)
        # =====================================================
        encabezado.setStyleSheet("""
            QGroupBox {
                color: #000000;
                margin-top: 40px;
                margin-bottom: 30px;
                border: 2px solid #d1d5db;
                border-radius: 8px;
                padding: 20px;
                background-color: #f9fafb;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 15px;
                top: 10px;
                padding: 5px 8px;
                color: #111827;
                font-weight: bold;
                font-size: 12pt;
            }
        """)

        header_widget.setStyleSheet("""
            QWidget {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                stop:0 #f8f9fb, stop:1 #e3e5e8);
                border: 1px solid #bfc3c7;
            }
        """)

        self.tabla.setStyleSheet("""
            QTableWidget {
                border: 1px solid #d1d5db;
                gridline-color: #e5e7eb;
                background-color: white;
                alternate-background-color: #f9fafb;
                selection-background-color: #2563eb;
                selection-color: white;
            }
        """)

        self.tabla.setStyleSheet("""
            QTableWidget {
                border: 1px solid #d1d5db;
                gridline-color: #e5e7eb;
                background-color: white;
                alternate-background-color: #f9fafb;
                selection-background-color: transparent;  /* 🔹 elimina azul */
                selection-color: black;                   /* texto negro al seleccionar */
            }
            QTableView::item:selected {
                background-color: transparent;            /* 🔹 evita sombreado */
                color: black;
            }
        """)



        # =====================================================
        # 🔧 AJUSTE FINAL DE DISTRIBUCIÓN VERTICAL
        # =====================================================
        layout_principal.setStretch(0, 0)  # encabezado no se expande
        layout_principal.setStretch(1, 1)  # tabla se expande (solo esta)
        layout_principal.setStretch(2, 0)  # totales fijos
        layout_principal.setStretch(3, 0)  # botones fijos

        # 🔹 Fijar altura mínima para encabezado y totales
        encabezado.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        encabezado.setFixedHeight(encabezado.sizeHint().height() + 20)

        # 🔹 Contenedor de tabla expansible sin márgenes extra
        tabla_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        tabla_container.layout().setContentsMargins(0, 0, 0, 0)
        tabla_container.layout().setSpacing(0)

        # 🔹 Compactar globalmente el layout
        layout_principal.setSpacing(0)
        layout_principal.setContentsMargins(5, 5, 5, 5)
        # === Cargar folio automático al iniciar ===
        try:
            nuevo_folio = self.obtener_siguiente_folio()
            self.input_folio.setText(nuevo_folio)
        except Exception as e:
            print(f"⚠️ Error al cargar folio inicial: {e}")
            self.input_folio.setText("00A00001")
        # =====================================================
        # 🔁 AJUSTE DE ALTURA DE TABLA DINÁMICO Y UNIFICADO
        # =====================================================
        def ajustar_altura_tabla():
            """Ajusta la altura de la tabla para mostrar exactamente todas las filas sin cortar."""
            # 🔹 Calcula la altura real de cada fila
            altura_filas = sum(self.tabla.rowHeight(i) for i in range(self.tabla.rowCount()))

            # 🔹 Incluye el encabezado manual + pequeño margen
            altura_encabezado = 25  # altura del encabezado de columnas
            margen_extra = 6        # compensación por bordes y estilos

            # 🔹 Altura total exacta
            altura_total = altura_filas + altura_encabezado + margen_extra

            # 🔹 Aplica la altura exacta sin límite de ventana
            self.tabla.setFixedHeight(altura_total)
            tabla_container.setFixedHeight(altura_total + 2)

        # 🔹 Ejecutar una sola vez después de mostrar la ventana (cuando ya se renderizó todo)
        QTimer.singleShot(300, ajustar_altura_tabla)
        # =====================================================
        # 🔹 Ajustes finales de comportamiento del layout
        # =====================================================

        # 🔹 Evitar que el encabezado se estire verticalmente al maximizar
        encabezado.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        encabezado.setMaximumWidth(1150)   # ajusta a gusto

        # 🔹 Permitir que la tabla se expanda y use todo el espacio disponible
        tabla_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # 🔹 También compactamos márgenes y espacios globales
        self.ajustar_altura_tabla = ajustar_altura_tabla
        QTimer.singleShot(600, self.reajustar_botones_totales)
       
        # Asegurar que se actualice en cada resize
        old_resize_event = self.tabla.resizeEvent
        def new_resize(event):
            self.sync_columns()
            old_resize_event(event)
        self.tabla.resizeEvent = new_resize

        # 🔹 Reajustar botones y totales al cambiar tamaño de la ventana
        def resizeEvent(self, event):
            super().resizeEvent(event)
            QTimer.singleShot(100, self.reajustar_botones_totales)
     

        # =====================================================
        # 🔁 SINCRONIZAR ANCHO DE ENCABEZADOS CON LA TABLA
        # =====================================================
        def sync_header():
            total_width = self.tabla.viewport().width()
            total_base = sum(w for _, w in self.header_spec)
            factor = total_width / total_base
            for i, (lbl, (text, base_w)) in enumerate(zip(self.header_labels, self.header_spec)):
                new_w = int(base_w * factor)
                lbl.setFixedWidth(new_w)
                self.tabla.setColumnWidth(i, new_w)

        self.tabla.horizontalHeader().sectionResized.connect(sync_header)
        self.tabla.viewport().installEventFilter(self)
        self.sync_header = sync_header
        # === 🔹 Si venimos desde "Editar factura", cargar datos ===
        if modo_edicion and datos_factura:
            self.cargar_factura(datos_factura)

    # =====================================================
    # 🧩 Ajuste automático al redimensionar ventana
    # =====================================================
    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(100, self.reajustar_botones_totales)
        try:
            self.sync_header()             # ajusta las columnas
            self.ajustar_altura_tabla()    # reajusta la tabla
            self.reajustar_botones_totales()
        except Exception as e:
            print("⚠️ Error al redimensionar:", e)

    def reajustar_botones_totales(self):
        """Mantiene estables los botones y totales al redimensionar la ventana."""
        ancho_total = self.width()

        # === 🔹 Ajustar ancho y altura de los totales ===
        if hasattr(self, "input_total"):
            ancho_optimo = max(260, min(330, int(ancho_total * 0.22)))
            for campo in [self.input_subtotal, self.input_descuento, self.input_iva, self.input_total]:
                campo.setFixedWidth(ancho_optimo)
                campo.setMinimumHeight(32)
                campo.setMaximumHeight(32)

        # === 🔹 Reanclar bloque inferior para mantener alineación inferior ===
        if hasattr(self, "layout"):
            layout = self.layout()
            layout.setAlignment(Qt.AlignBottom)

        # === 🔹 Asegurar que el bloque inferior no se colapse ===
        if hasattr(self, "bloque_inferior"):
            self.bloque_inferior.setMinimumHeight(130)
            self.bloque_inferior.setMaximumHeight(140)
            self.bloque_inferior.updateGeometry()

        # === 🔹 Recalcular luego de la animación de maximizar ===
        QTimer.singleShot(250, self._refrescar_posicion_totales)


    def _refrescar_posicion_totales(self):
        """Corrige posición y tamaño después de maximizar/restaurar."""
        if hasattr(self, "input_total"):
            ancho_total = self.width()
            ancho_optimo = max(260, min(330, int(ancho_total * 0.22)))
            for campo in [self.input_subtotal, self.input_descuento, self.input_iva, self.input_total]:
                campo.setFixedWidth(ancho_optimo)
                campo.setMinimumHeight(32)
                campo.setMaximumHeight(32)

        # Forzar el layout a recalcularse completamente
        if hasattr(self, "layout"):
            self.layout().activate()
        if hasattr(self, "bloque_inferior"):
            self.bloque_inferior.adjustSize()
            self.bloque_inferior.updateGeometry()


    def ajustar_totales_dinamicos(self):
        """Evita que los totales se muevan o encojan después de maximizar la primera vez."""
        ancho_total = self.width()
        ancho_optimo = max(250, min(320, int(ancho_total * 0.22)))

        for campo in [self.input_subtotal, self.input_descuento, self.input_iva, self.input_total]:
            campo.setFixedWidth(ancho_optimo)
            campo.setMinimumHeight(32)
            campo.setMaximumHeight(32)


    def obtener_siguiente_folio(self):
        """Obtiene el siguiente folio según la empresa seleccionada."""
        import mysql.connector

        try:
            empresa = self.combo_empresa.currentText().strip().lower()

            # === Prefijo por empresa ===
            if "gourmet" in empresa:
                prefijo = "00A"
            elif "ibersur" in empresa:
                prefijo = "A00"
            elif "eza2007" in empresa:
                prefijo = "CFDI0"
            else:
                prefijo = "000"

            # === Conexión a base de datos ===
            conn = mysql.connector.connect(
                host="192.168.1.105",
                user="Facturacion",
                password="ALD2013*",
                database="comandas_db",
                port=3306
            )
            cursor = conn.cursor()

            # Buscar último folio con el mismo prefijo
            cursor.execute("""
                SELECT factura 
                FROM facturas 
                WHERE factura LIKE %s 
                ORDER BY id DESC LIMIT 1
            """, (f"{prefijo}%",))
            resultado = cursor.fetchone()

            if resultado and len(resultado[0]) > len(prefijo):
                try:
                    # Extraer la parte numérica del folio y sumar 1
                    numero = int(''.join(filter(str.isdigit, resultado[0][len(prefijo):])))
                    siguiente_numero = numero + 1
                except ValueError:
                    siguiente_numero = 1
            else:
                siguiente_numero = 1

            nuevo_folio = f"{prefijo}{siguiente_numero}"
            return nuevo_folio

        except Exception as e:
            print(f"⚠️ Error al obtener siguiente folio: {e}")
            return f"{prefijo}1"

        finally:
            try:
                cursor.close()
                conn.close()
            except:
                pass




    def incrementar_folio(self, folio_actual: str) -> str:
        import re
        s = (folio_actual or "").strip()
        # Prefijo arbitrario + bloque final de dígitos
        m = re.match(r"^(.*?)(\d+)$", s)
        if not m:
            # Si no termina en número, agregamos 00001
            return s + "00001"
        prefijo, num = m.groups()
        nuevo = str(int(num) + 1).zfill(len(num))
        return f"{prefijo}{nuevo}"



    def _on_cell_changed_otro_precio(self, fila: int, columna: int):
        """
        Detecta cambios en la columna 'Otro Precio' y aplica formato moneda
        y recalcula el importe de la fila.
        """
        try:
            if columna != 7:  # Solo aplica a columna "Otro Precio"
                return

            item_otro_precio = self.tabla.item(fila, columna)
            if not item_otro_precio:
                return

            texto = item_otro_precio.text().strip()
            if not texto:
                # Si se borra el valor, recalcula el importe usando precio real
                self._actualizar_importe_fila(fila)
                return

            # Intentar convertir a número y formatear
            try:
                valor = float(texto.replace(",", "").replace("$", ""))
                item_otro_precio.setText(f"{valor:,.2f}")
            except ValueError:
                item_otro_precio.setText("")
                return

            # Recalcular importe usando el nuevo valor
            self._actualizar_importe_fila(fila)

        except Exception as e:
            print(f"⚠️ Error al procesar 'Otro Precio' en fila {fila}: {e}")
    
    def _on_cell_changed_cantidad(self, fila: int, columna: int):
        """Recalcula el importe si cambia la cantidad."""
        
        # ⛔ EVITAR REACCIONES MIENTRAS CARGA FACTURA
        if getattr(self, "cargando_factura", False):
            # print(f"⏳ Saltado cambio en cantidad fila {fila} (cargando_factura=True)")
            return

        try:
            if columna != 2:
                return
            self._actualizar_importe_fila(fila)
            self.recalcular_totales()
        except Exception as e:
            print(f"⚠️ Error al actualizar importe por cambio de cantidad en fila {fila}: {e}")

   
    # =====================================================
    # 🔁 SINCRONIZAR ANCHOS DE COLUMNA CON ENCABEZADO
    # =====================================================
    def sync_columns(self):
        if not hasattr(self, "tabla") or not hasattr(self, "header_labels"):
            return

        viewport_width = self.tabla.viewport().width()
        total_base = sum(width for _, width in self.header_spec)
        factor = viewport_width / total_base

        for i, (lbl_data, base_width) in enumerate(zip(self.header_labels, self.header_spec)):
            new_width = int(base_width[1] * factor)
            lbl_data.setFixedWidth(new_width)
            self.tabla.setColumnWidth(i, new_width)
    def cargar_empresas(self):
        """Carga las empresas desde la base de datos."""
        try:
            resp = requests.get(f"{API_URL}/comandas/empresas")
            if resp.status_code == 200:
                empresas = resp.json()
                self.combo_empresa.clear()
                self.combo_empresa.addItems(empresas)
            else:
                QMessageBox.warning(self, "Advertencia", "No se pudieron cargar las empresas desde el servidor.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error al cargar empresas:\n{e}")

    def verificar_cip(self, row, column):
        """Busca descripción y precio cuando se ingresa un CIP."""
        if column != 0:  # Solo si se edita la columna CIP
            return

        cip = self.tabla.item(row, column).text().strip()
        cliente = self.input_cliente.text().strip()
        empresa = self.combo_empresa.currentText().strip()

        if not cip or not cliente:
            return

        try:
            resp = requests.get(f"{API_URL}/comandas/producto/{cip}/{cliente}/{empresa}")
            if resp.status_code != 200:
                QMessageBox.warning(self, "No encontrado", f"Producto {cip} no encontrado.")
                return

            data = resp.json()

            # Rellenar descripción y precio
            self.tabla.item(row, 1).setText(data.get("descripcion", ""))
            self.tabla.item(row, 4).setText(f"{data.get('precio', 0):.2f}")

            # Bloquear la descripción
            self.tabla.item(row, 1).setFlags(self.tabla.item(row, 1).flags() & ~Qt.ItemIsEditable)
            self.tabla.item(row, 1).setBackground(QColor("#f5f5f5"))

            self.actualizar_totales()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error al buscar producto:\n{e}")

    def buscar_cliente(self):
        """Busca automáticamente los datos del cliente al perder el foco del campo número."""
        numero = self.input_cliente.text().strip()
        empresa = self.combo_empresa.currentText().strip()

        if not numero or not empresa:
            return

        print(f"🔍 Buscando cliente {numero} - Empresa {empresa}")
        self.cargar_datos_cliente(numero, empresa)

    
    def actualizar_descuento_cliente(self, valor):
        try:
            valor = float(valor)
            self.input_descuento_cliente.setText(f"{valor:.2f} %")
        except ValueError:
            self.input_descuento_cliente.setText("0.00 %")
    
    def recalcular_precios_por_rebanado(self):
        """Recalcula el precio real de TODAS las filas según el nuevo cargo por rebanado."""
        try:
            for fila in range(self.tabla.rowCount()):
                self._actualizar_por_rebanado(fila)
            self.recalcular_totales()
        except Exception as e:
            print("⚠️ Error en recálculo general por rebanado:", e)

    def formatear_cargo_rebanado(self):
        try:
            texto = self.input_cargo_rebanado.text().replace("%", "").strip()
            if not texto:
                valor = 0.0
            else:
                valor = float(texto)

            valor = max(0.0, min(valor, 100.0))
            self.input_cargo_rebanado.setText(f"{valor:.2f} %")

            # 🔥 NUEVO: recalcular todas las filas con el nuevo porcentaje
            self.recalcular_precios_por_rebanado()

        except ValueError:
            self.input_cargo_rebanado.setText("0.00 %")

    def eliminar_producto_seleccionado(self):
        """Elimina todos los datos de la fila seleccionada sin borrar la fila."""
        fila = self.tabla.currentRow()
        if fila < 0:
            QMessageBox.information(self, "Sin selección", "Selecciona una fila para eliminar el producto.")
            return

        # 🔹 Confirmación visual (opcional)
        confirm = QMessageBox.question(
            self,
            "Confirmar eliminación",
            f"¿Deseas eliminar los datos de la fila {fila + 1}?",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        # 🔹 Limpia el contenido de todas las celdas en la fila seleccionada
        for c in range(self.tabla.columnCount()):
            item = self.tabla.item(fila, c)
            if item is None:
                item = QTableWidgetItem("")
                self.tabla.setItem(fila, c, item)
            item.setText("")
            item.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled)
            item.setBackground(QColor("#ffffff"))

        # 🔹 Mueve la selección a la siguiente fila (si existe)
        if fila < self.tabla.rowCount() - 1:
            self.tabla.setCurrentCell(fila + 1, 0)

        # 🔹 Recalcular totales
        self.actualizar_totales()
    
    def cancelar_factura(self):
        """Limpia todos los datos del formulario y la tabla sin alterar el formato."""
        # 🔹 Confirmación opcional
        confirm = QMessageBox.question(
            self,
            "Cancelar factura",
            "¿Deseas limpiar todos los datos de la factura actual?",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        # --- Encabezado ---
        self.input_comanda.clear()
        self.input_folio.clear()
        self.input_cliente.clear()
        self.input_nombre_cliente.clear()
        self.input_rfc.clear()
        self.input_vendedor.clear()
        self.input_descuento_cliente.setText("0.00 %")
        self.combo_empresa.setCurrentIndex(0)
        self.combo_lista_precios.setCurrentIndex(0)
        self.input_fecha.setDate(QDate.currentDate())

        # --- Tabla ---
        for fila in range(self.tabla.rowCount()):
            for col in range(self.tabla.columnCount()):
                # Si es la columna del combo "Rebanado"
                if col == 6:
                    combo = self.tabla.cellWidget(fila, col)
                    if combo and isinstance(combo, QComboBox):
                        combo.setCurrentIndex(0)  # 🔹 deja la opción vacía
                    continue  # no hay QTableWidgetItem aquí

                # 🔹 Limpieza normal de las celdas
                item = self.tabla.item(fila, col)
                if item is None:
                    item = QTableWidgetItem("")
                    self.tabla.setItem(fila, col, item)
                item.setText("")
                item.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled)
                item.setBackground(QColor("#ffffff"))


        # --- Totales ---
        self.input_subtotal.setText("0.00")
        self.input_iva.setText("0.00")
        self.input_total.setText("0.00")

        # 🔹 Reset de foco y fila inicial
        self.tabla.setCurrentCell(0, 0)
        self.input_comanda.setFocus()

        QMessageBox.information(self, "Factura cancelada", "Todos los campos han sido limpiados.")


    def cargar_datos_cliente(self, numero_cliente: str, empresa: str):
        """Obtiene los datos del cliente desde la API y los aplica al formulario."""
        if not numero_cliente or not empresa:
            return False

        try:
            resp = requests.get(f"{API_URL}/clientes/{numero_cliente}/{empresa}")
            if resp.status_code == 200:
                cliente = resp.json()

                # --- Rellenar campos principales ---
                self.input_nombre_cliente.setText(cliente.get("cliente_nombre", ""))
                self.input_rfc.setText(cliente.get("rfc", ""))
                self.input_vendedor.setText(cliente.get("vendedor", ""))

                # --- Lista de precios ---
                lista = cliente.get("lista_precios", "Lista General")
                self.combo_lista_precios.clear()
                self.combo_lista_precios.addItem(lista)

                # --- Descuento ---
                descuento = cliente.get("descuento", 0)
                self.actualizar_descuento_cliente(descuento)

                # Guardar lista activa para futuras búsquedas
                self.lista_precios_cliente = lista

                return True

            elif resp.status_code == 404:
                QMessageBox.warning(
                    self, "Cliente no encontrado",
                    f"No se encontró el cliente {numero_cliente} en {empresa}."
                )
                return False

            else:
                QMessageBox.warning(self, "Error", f"No se pudo obtener el cliente:\n{resp.text}")
                return False

        except Exception as e:
            QMessageBox.critical(self, "Error de conexión", str(e))
            return False
    def _descuento_cliente_float(self) -> float:
        txt = self.input_descuento_cliente.text().replace("%", "").strip()
        try:
            v = float(txt)
            return max(0.0, min(v, 99.99))  # acotar para evitar división por 0
        except ValueError:
            return 0.0
        
    def _calcular_precio_real(self, fila: int, precio_base: float, tipo_lista: str, iva: str = "No") -> float:
        """
        Calcula el precio real respetando la lógica ORIGINAL,
        PERO si estamos editando una factura guardada → NO RECALCULA
        y devuelve el precio exacto guardado en la tabla (columna 5).
        """

        # ================================================================
        # 🛑 1) MODO EDICIÓN → NO recalcular precio
        # ================================================================
        if getattr(self, "modo_edicion", False):
            try:
                item_precio_guardado = self.tabla.item(fila, 5)
                if item_precio_guardado and item_precio_guardado.text().strip() != "":
                    precio_guardado = float(item_precio_guardado.text())
                    print(f"[DEBUG Fila {fila}] MODO EDICIÓN → precio GUARDADO = {precio_guardado}")
                    return precio_guardado
            except:
                pass  # si falla, sigue con la lógica normal por seguridad

        # ================================================================
        # 🟩 2) MODO NORMAL → lógica original con DEBUG
        # ================================================================
        try:
            descuento = self._descuento_cliente_float() or 0.0

            print("\n========================")
            print(f"[DEBUG Fila {fila}] Cálculo de precio real")
            print(f"Precio base: {precio_base}")
            print(f"Tipo de lista: {tipo_lista}")
            print(f"Descuento del cliente: {descuento}%")

            # --- Tipo de lista ---
            tipo = (tipo_lista or "Estándar").lower()
            if "gourmet" in tipo:
                precio_real = precio_base
                print(f"[DEBUG] Lista Gourmet → precio sin descuento = {precio_real}")
            elif "estándar" in tipo or "estandar" in tipo:
                denom = 1.0 - (descuento / 100.0)
                precio_real = precio_base if denom <= 0 else (precio_base / denom)
                print(f"[DEBUG] Lista Estándar → precio con descuento aplicado = {precio_real}")
            else:
                precio_real = precio_base
                print(f"[DEBUG] Lista desconocida → precio = {precio_real}")

            # --- Rebanado ---
            combo_rebanado = self.tabla.cellWidget(fila, 6)
            aplica_rebanado = combo_rebanado and combo_rebanado.currentText().strip().lower() == "sí"

            if aplica_rebanado:
                cargo_texto = self.input_cargo_rebanado.text().replace("%", "").strip()
                try:
                    cargo_rebanado = float(cargo_texto) / 100 if cargo_texto else 0.0
                except ValueError:
                    cargo_rebanado = 0.0

                print(f"[DEBUG] Cargo de rebanado: texto='{cargo_texto}' → {cargo_rebanado*100}%")

                precio_antes = precio_real
                precio_real *= (1 + cargo_rebanado)

                print(f"[DEBUG] Precio con rebanado: {precio_antes} → {precio_real}")
            else:
                print("[DEBUG] Rebanado: NO aplica")

            # --- IVA ---
            iva_lower = str(iva).strip().lower()
            print(f"[DEBUG] IVA del producto: {iva_lower}")

            if iva_lower in ["sí", "si", "1", "true"]:
                precio_antes_iva = precio_real
                precio_real /= 1.16
                print(f"[DEBUG] IVA aplicado (÷1.16): {precio_antes_iva} → {precio_real}")
            else:
                print("[DEBUG] IVA: NO aplica")

            print(f"[DEBUG] PRECIO FINAL FILA {fila}: {round(precio_real, 2)}")
            print("========================\n")

            return round(precio_real, 2)

        except Exception as e:
            print(f"⚠️ Error en _calcular_precio_real: {e}")
            return round(float(precio_base or 0.0), 2)
    
    def _resaltar_fila_rebanado(self, fila: int):
        """
        Resalta la fila si el producto tiene rebanado = 'Sí'.
        """
        try:
            combo_rebanado = self.tabla.cellWidget(fila, 6)
            if not combo_rebanado:
                return

            texto = combo_rebanado.currentText().strip().lower()

            if texto == "sí":
                color = QColor("#fff4b3")  # amarillo suave
            else:
                color = QColor("#ffffff")  # normal

            # Pintar solo columnas visibles
            for col in range(0, 9):
                item = self.tabla.item(fila, col)
                if item:
                    item.setBackground(color)

        except Exception as e:
            print(f"⚠️ Error resaltando fila rebanado: {e}")
    
    def _actualizar_por_rebanado(self, fila: int):
        """
        Se ejecuta cuando cambia el combo 'Rebanado'.
        - En modo NORMAL recalcula precios.
        - En MODO EDICIÓN solo recalcula importe si cambia el rebanado visual,
        pero NO cambia el precio real guardado.
        - Siempre actualiza el resaltado.
        """
        try:
            print(f"\n===== [DEBUG] Recalculo por REBANADO en fila {fila} =====")

            # ===========================================================
            # 1️⃣ Resaltar fila según estado del combo
            # ===========================================================
            self._resaltar_fila_rebanado(fila)

            # ===========================================================
            # 2️⃣ MODO EDICIÓN → NO recalcular precio real
            # ===========================================================
            if getattr(self, "modo_edicion", False):
                print("[DEBUG] MODO EDICIÓN → NO se recalcula precio real")

                # Solo recalcula el IMPORTE, manteniendo precio guardado
                celda_precio_real = self.tabla.item(fila, 5)
                precio_real = float(celda_precio_real.text()) if celda_precio_real else 0.0

                cantidad_item = self.tabla.item(fila, 2)
                cantidad = float(cantidad_item.text()) if cantidad_item and cantidad_item.text() else 0.0

                importe = cantidad * precio_real
                print(f"[DEBUG] Importe recalculado (modo edicion): {importe}")

                celda_importe = self.tabla.item(fila, 8)
                if celda_importe:
                    celda_importe.setText(f"{importe:,.2f}")

                self.actualizar_totales()
                print("===== [DEBUG] FIN recalculo rebanado (modo edición) =====\n")
                return

            # ===========================================================
            # 3️⃣ MODO NORMAL → Recalcular PRECIO + IMPORTE
            # ===========================================================

            # Precio base (columna 4)
            item_precio = self.tabla.item(fila, 4)
            precio_base = float(item_precio.text()) if item_precio and item_precio.text() else 0.0
            print(f"[DEBUG] Precio base: {precio_base}")

            # Tipo de lista (columna 10)
            item_tipo = self.tabla.item(fila, 10)
            tipo_lista = item_tipo.text().strip() if item_tipo and item_tipo.text() else "Estándar"
            print(f"[DEBUG] Tipo de lista real: {tipo_lista}")

            # IVA (columna 9)
            item_iva = self.tabla.item(fila, 9)
            iva_flag = item_iva.text().strip() if item_iva and item_iva.text() else "No"
            print(f"[DEBUG] IVA del producto: {iva_flag}")

            # Calcular nuevo precio real
            precio_real = self._calcular_precio_real(fila, precio_base, tipo_lista, iva_flag)
            print(f"[DEBUG] Nuevo precio real calculado: {precio_real}")

            # Actualizar precio real (columna 5)
            celda_precio_real = self.tabla.item(fila, 5)
            if celda_precio_real:
                celda_precio_real.setText(f"{precio_real:.2f}")

            # Calcular importe
            cantidad_item = self.tabla.item(fila, 2)
            cantidad = float(cantidad_item.text()) if cantidad_item and cantidad_item.text() else 0.0

            importe = cantidad * precio_real
            print(f"[DEBUG] Cantidad: {cantidad}  →  Importe: {importe}")

            celda_importe = self.tabla.item(fila, 8)
            if celda_importe:
                celda_importe.setText(f"{importe:,.2f}")

            # Actualizar totales
            self.actualizar_totales()

            print("===== [DEBUG] FIN recalculo rebanado =====\n")

        except Exception as e:
            print(f"⚠️ Error al actualizar rebanado en fila {fila}: {e}")

    def _actualizar_importe_fila(self, fila: int):
        """
        Calcula y actualiza el importe de una fila según:
        - Si 'Otro Precio' está vacío -> usa 'Precio Real'
        - Si 'Otro Precio' tiene valor -> usa ese
        - Si el producto tiene IVA='Sí' -> divide precio entre 1.16
        """
        try:
            cantidad_item = self.tabla.item(fila, 2)
            cantidad = float(cantidad_item.text()) if cantidad_item and cantidad_item.text() else 0.0

            precio_real_item = self.tabla.item(fila, 5)
            otro_precio_item = self.tabla.item(fila, 7)

            precio_real = float(precio_real_item.text()) if precio_real_item and precio_real_item.text() else 0.0
            otro_precio = 0.0

            if otro_precio_item and otro_precio_item.text().strip():
                try:
                    otro_precio = float(otro_precio_item.text().replace(",", "").replace("$", ""))
                except ValueError:
                    otro_precio = 0.0

            # --- Determinar el precio a usar ---
            precio_usado = otro_precio if otro_precio > 0 else precio_real

            # --- Verificar si tiene IVA ---
            col_iva = 9
            iva_item = self.tabla.item(fila, col_iva)
            tiene_iva = iva_item and str(iva_item.text()).strip().lower() in ["sí", "si", "1", "true"]

            if tiene_iva:
                precio_usado /= 1.16

            # --- Calcular e insertar importe ---
            importe_item = self.tabla.item(fila, 8)
            if importe_item is None:
                importe_item = QTableWidgetItem()
                self.tabla.setItem(fila, 8, importe_item)
            importe_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            if cantidad > 0 and precio_usado > 0:
                importe = cantidad * precio_usado
                importe_item.setText(f"{importe:,.2f}")
            else:
                importe_item.setText("")

            self.actualizar_totales()

        except Exception as e:
            print(f"⚠️ Error al calcular importe fila {fila}: {e}")



    def _on_cell_changed_otro_precio(self, fila: int, columna: int):
        """
        Detecta cambios en la columna 'Otro Precio' y aplica formato moneda
        y recalcula el importe de la fila.
        """

        # ⛔ NO HACER NADA MIENTRAS SE ESTÁ CARGANDO UNA FACTURA
        if getattr(self, "cargando_factura", False):
            # print(f"⏳ Saltado 'otro precio' fila {fila} (cargando_factura=True)")
            return

        try:
            if columna != 7:
                return

            item_otro_precio = self.tabla.item(fila, columna)
            if not item_otro_precio:
                return

            texto = item_otro_precio.text().strip()
            if not texto:
                self._actualizar_importe_fila(fila)
                self.recalcular_totales()
                return

            # 🧹 LIMPIAR FORMATO ANTES DE CONVERTIR
            try:
                valor = float(texto.replace(",", "").replace("$", ""))
                # Escribir SIN comas para evitar errores en recalcular
                item_otro_precio.setText(f"{valor:.2f}")
            except ValueError:
                item_otro_precio.setText("")
                return

            self._actualizar_importe_fila(fila)
            self.recalcular_totales()

        except Exception as e:
            print(f"⚠️ Error al procesar 'Otro Precio' en fila {fila}: {e}")


    # =====================================================
    # 🔁 AJUSTAR ALTURA DE TABLA SEGÚN VENTANA
    # =====================================================
    def resizeEvent(self, event):
        """Recalcula la altura de la tabla cuando se redimensiona la ventana."""
        super().resizeEvent(event)
        QTimer.singleShot(100, self.reajustar_botones_totales)
        if hasattr(self, "ajustar_altura_tabla"):
            self.ajustar_altura_tabla()
    def cargar_comanda(self):
        """Busca una comanda existente y rellena los datos del cliente, empresa,
        lista de precios, vendedor, descuento, cargo rebanado y productos.
        """

        try:
            # ================================
            #  🔧 Limpieza robusta del folio
            # ================================
            raw = self.input_comanda.text()
            
            comanda_id = (
                raw.strip()
                .replace(" ", "")
                .replace("\u00A0", "")
                .replace("\t", "")
                .replace("\n", "")
                .replace("\r", "")
            )

            print(f"[DEBUG] Folio RAW: '{raw}' → Limpio: '{comanda_id}'")

            if not comanda_id:
                return

            # ================================
            #  🔥 Activar bandera anti-recalculo
            # ================================
            self.cargando_factura = True
            self.tabla.blockSignals(True)

            # ================================
            #  🔍 Buscar comanda en API
            # ================================
            url = f"{API_URL}/comandas/{comanda_id}"
            print(f"[DEBUG] Consultando API: {url}")

            resp = requests.get(url)

            if resp.status_code != 200:
                self.cargando_factura = False
                self.tabla.blockSignals(False)
                QMessageBox.warning(self, "No encontrado",
                                    f"No existe la comanda {comanda_id}.")
                return

            data = resp.json()

            # ================================
            #  🔧 Inicializar tabla
            # ================================
            for r in range(self.tabla.rowCount()):
                for c in range(self.tabla.columnCount()):
                    if not self.tabla.item(r, c):
                        item = QTableWidgetItem("")
                        item.setTextAlignment(Qt.AlignCenter)
                        self.tabla.setItem(r, c, item)
                    self.tabla.item(r, c).setText("")
                    self.tabla.item(r, c).setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled)
                    self.tabla.item(r, c).setBackground(QColor("#ffffff"))

            # ================================
            #  🔹 Llenar encabezado
            # ================================
            cliente_numero = str(data.get("cliente_numero", ""))
            cliente_nombre = data.get("cliente_nombre", "")
            empresa = data.get("empresa", "")
            vendedor = data.get("vendedor", "")
            rfc = data.get("rfc", "")

            self.input_cliente.setText(cliente_numero)
            self.input_nombre_cliente.setText(cliente_nombre)
            self.input_vendedor.setText(vendedor)
            self.input_rfc.setText(rfc)

            # Seleccionar empresa
            if empresa:
                idx = self.combo_empresa.findText(empresa)
                if idx != -1:
                    self.combo_empresa.setCurrentIndex(idx)
                else:
                    self.combo_empresa.addItem(empresa)
                    self.combo_empresa.setCurrentText(empresa)

            # ================================
            #  Obtener descuento y lista de precios
            # ================================
            if cliente_numero and empresa:
                self.cargar_datos_cliente(cliente_numero, empresa)

            # ================================
            #  Cálculo de cargo rebanado
            # ================================
            cargo_raw = self.input_cargo_rebanado.text().replace("%", "").strip()
            try:
                cargo_rebanado = float(cargo_raw) / 100 if cargo_raw else 0.0
            except:
                cargo_rebanado = 0.0

            # ================================
            #  🔹 Cargar productos de comanda
            # ================================
            productos = data.get("productos", [])

            for i, p in enumerate(productos):
                if i >= self.tabla.rowCount():
                    break

                def clean(v):
                    return str(v).replace(",", "").replace("$", "").strip()

                cip = clean(p.get("cip", ""))
                descripcion = clean(p.get("descripcion", ""))
                cantidad = float(clean(p.get("cantidad", 0)) or 0)
                pzas = float(clean(p.get("pzas", 0)) or 0)

                cliente = self.input_cliente.text().strip()
                empresa = self.combo_empresa.currentText().strip()

                # ================================
                #  Buscar producto y calcular precio
                # ================================
                tipo_lista = "Estándar"
                precio_base = 0.0
                iva = "No"

                try:
                    resp_prod = requests.get(f"{API_URL}/comandas/producto/{cip}/{cliente}/{empresa}")
                    if resp_prod.status_code == 200:
                        pdata = resp_prod.json()
                        tipo_lista = pdata.get("tipo_lista", "Estándar")
                        precio_base = float(pdata.get("precio", 0) or 0)
                        iva = pdata.get("iva", "No")
                except:
                    pass

                precio_real = self._calcular_precio_real(i, precio_base, tipo_lista, iva)

                # ================================
                #  GUARDAR TIPO LISTA REAL (columna 10)
                # ================================
                col_tipo = 10
                if col_tipo >= self.tabla.columnCount():
                    self.tabla.setColumnCount(col_tipo + 1)

                item_tipo = QTableWidgetItem(tipo_lista)
                item_tipo.setFlags(Qt.ItemIsSelectable)
                item_tipo.setForeground(QColor("#f9fafb"))  # oculto
                self.tabla.setItem(i, col_tipo, item_tipo)

                # IVA columna oculta (9)
                col_iva = 9
                if col_iva >= self.tabla.columnCount():
                    self.tabla.setColumnCount(col_iva + 1)

                iva_item = QTableWidgetItem(iva)
                iva_item.setFlags(Qt.ItemIsSelectable)
                iva_item.setForeground(QColor("#f9fafb"))
                self.tabla.setItem(i, col_iva, iva_item)

                # ================================
                #  RELLENAR FILA
                # ================================
                datos = {
                    0: cip,
                    1: descripcion,
                    2: f"{cantidad}",
                    3: f"{pzas}",
                    4: f"{precio_base:.2f}",
                    5: f"{precio_real:.2f}",
                    7: "",
                    8: f"{cantidad * precio_real:.2f}",
                }

                # Aplicar datos
                for col, valor in datos.items():
                    celda = self.tabla.item(i, col)
                    celda.setText(str(valor))
                    if col in [1, 4, 5]:
                        celda.setFlags(celda.flags() & ~Qt.ItemIsEditable)
                        celda.setBackground(QColor("#f5f5f5"))
                    else:
                        celda.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled)
                        celda.setBackground(QColor("#ffffff"))

            # ================================
            #  🔹 Recalcular totales
            # ================================
            self.cargando_factura = False
            self.tabla.blockSignals(False)

            try:
                self.recalcular_totales()
            except Exception as e:
                print("⚠️ Error recalculando totales al final:", e)

        except Exception as e:
            import traceback
            print("❌ Error general al cargar comanda:", e)
            print(traceback.format_exc())
            QMessageBox.critical(self, "Error", f"Error general al cargar comanda:\n{e}")
    
    def cargar_factura_en_edicion(self, datos):
        """
        Carga una factura existente dentro de FacturacionTab
        usando el payload recibido desde VentanaMio (API /facturas/folio/{folio}).
        Además consulta el API de clientes para obtener:
        - nombre del cliente
        - RFC
        - vendedor
        - descuento
        """

        import requests

        print("\n============================")
        print("📝 [DEBUG] Cargando factura en modo edición...")
        print("============================\n")

        # Para guardar el payload actual
        self.modo_edicion = True
        self.datos_factura = datos

        # Evitar que se dispare recalcular_totales mientras llenamos
        self.cargando_factura = True
        self.tabla.blockSignals(True)

        try:
            # ===============================================
            # 🔹 1. Datos básicos de la factura
            # ===============================================
            folio = datos.get("factura") or datos.get("folio") or ""
            numero_cliente = (
                datos.get("numero_cliente")
                or datos.get("cliente_numero")
                or datos.get("cliente")
                or ""
            )
            empresa = datos.get("empresa", "")

            self.input_folio.setText(str(folio))
            self.input_cliente.setText(str(numero_cliente))

            # Seleccionar empresa en combo
            if empresa:
                idx = self.combo_empresa.findText(empresa)
                if idx >= 0:
                    self.combo_empresa.setCurrentIndex(idx)
                else:
                    self.combo_empresa.addItem(empresa)
                    self.combo_empresa.setCurrentText(empresa)

            print(f" → Cliente: {numero_cliente}")
            print(f" → Empresa: {empresa}")

            # ===============================================
            # 🔹 2. Consultar API de clientes para nombre, RFC, vendedor, descuento
            # ===============================================
            cliente_info = {}
            cliente_nombre = ""
            rfc = ""
            vendedor = ""
            descuento_cliente = 0.0

            try:
                if numero_cliente and empresa:
                    url = f"{API_URL}/clientes/{numero_cliente}/{empresa}"
                    resp = requests.get(url, timeout=5)
                    print("🔎 DEBUG CLIENTE API URL:", url)
                    if resp.status_code == 200:
                        cliente_info = resp.json() or {}
                        print("🔎 DEBUG CLIENTE API RESPUESTA:", cliente_info)

                        # Nombre del cliente
                        cliente_nombre = (
                            cliente_info.get("cliente_nombre")
                            or cliente_info.get("razon_social")
                            or datos.get("consignatario")
                            or ""
                        )

                        # RFC
                        rfc = cliente_info.get("rfc", "")

                        # Vendedor
                        vendedor = cliente_info.get("vendedor", "")

                        # Descuento (%)
                        descuento_raw = cliente_info.get("descuento", 0)
                        try:
                            descuento_cliente = float(descuento_raw or 0)
                        except Exception:
                            descuento_cliente = 0.0
            except Exception as e:
                print("⚠️ Error al consultar API de clientes en modo edición:", e)

            print(f" → Cliente nombre: {cliente_nombre}")
            print(f" → RFC: {rfc}")
            print(f" → Vendedor: {vendedor}")
            print(f" → Descuento cliente: {descuento_cliente}%")

            # Llenar encabezado en la GUI
            self.input_nombre_cliente.setText(cliente_nombre)
            self.input_rfc.setText(rfc)
            self.input_vendedor.setText(vendedor)
            self.input_descuento_cliente.setText(f"{descuento_cliente:.2f} %")

            # ===============================================
            # 🔹 3. Totales desde la tabla FACTURAS
            # ===============================================
            def clean_num(v):
                return str(v).replace(",", "").replace("$", "").strip()

            subtotal = clean_num(datos.get("subtotal", 0))
            descuento_total = clean_num(
                datos.get("descuento", datos.get("descuento_total", 0))
            )
            iva_total = clean_num(datos.get("iva", 0))
            total = clean_num(datos.get("total", 0))

            self.input_subtotal.setText(subtotal)
            self.input_descuento.setText(descuento_total)
            self.input_iva.setText(iva_total)
            self.input_total.setText(total)

            print(" → Totales asignados correctamente.")

            # ===============================================
            # 🔹 4. Limpiar tabla antes de llenar productos
            # ===============================================
            for r in range(self.tabla.rowCount()):
                for c in range(self.tabla.columnCount()):
                    it = self.tabla.item(r, c)
                    if it:
                        it.setText("")

            # ===============================================
            # 🔹 5. Cargar productos desde factura_detalle
            # ===============================================
            productos = datos.get("productos", []) or []
            print(f" → Cargando {len(productos)} productos...")

            for fila, p in enumerate(productos):
                if fila >= self.tabla.rowCount():
                    break

                def clean_cell(v):
                    return str(v).replace(",", "").replace("$", "").strip()

                cip = clean_cell(p.get("cip", ""))
                desc = clean_cell(p.get("descripcion", ""))
                cantidad = clean_cell(p.get("cantidad", 0))
                piezas = clean_cell(p.get("piezas", 0))
                precio = clean_cell(p.get("precio", 0))
                importe = clean_cell(
                    p.get("importe", 0)
                )  # si ya lo tienes en la BD lo usamos, si no se recalculará

                # Convertir a float seguro para importe
                try:
                    cant_f = float(cantidad or 0)
                except Exception:
                    cant_f = 0.0

                try:
                    precio_f = float(precio or 0)
                except Exception:
                    precio_f = 0.0

                if not importe:
                    importe = f"{cant_f * precio_f:.2f}"

                def set_text(row, col, text):
                    item = self.tabla.item(row, col)
                    if not item:
                        item = QTableWidgetItem()
                        item.setTextAlignment(Qt.AlignCenter)
                        self.tabla.setItem(row, col, item)
                    item.setText(str(text))

                set_text(fila, 0, cip)
                set_text(fila, 1, desc)
                set_text(fila, 2, cantidad)
                set_text(fila, 3, piezas)
                set_text(fila, 5, f"{precio_f:.2f}")  # Precio real
                set_text(fila, 7, "")                 # Otro precio vacío
                set_text(fila, 8, importe)            # Importe

            print(" → Productos cargados correctamente.")

        except Exception as e:
            print("❌ Error cargando factura en modo edición:", e)

        finally:
            # Reactivar señales y permitir recálculo
            self.tabla.blockSignals(False)
            self.cargando_factura = False

            try:
                self.recalcular_totales()
            except Exception as e:
                print("⚠️ Error al recalcular totales al final (modo edición):", e)

            print("🟢 [DEBUG] Factura cargada en modo edición correctamente.\n")

    def _generar_folio_autonumerico(self) -> str:
        """
        Genera el siguiente número de folio de forma incremental
        compatible con formatos alfanuméricos como '00A31368'.
        """
        try:
            ruta_folio = "ultimo_folio.txt"

            # Si no existe, crear con un valor inicial
            if not os.path.exists(ruta_folio):
                with open(ruta_folio, "w", encoding="utf-8") as f:
                    f.write("00A31368")

            with open(ruta_folio, "r+", encoding="utf-8") as f:
                folio_actual = f.read().strip() or "00A31368"

                # Buscar la parte numérica al final
                import re
                match = re.search(r"(\d+)$", folio_actual)
                if match:
                    num_str = match.group(1)
                    nuevo_num = str(int(num_str) + 1).zfill(len(num_str))
                    nuevo_folio = folio_actual[:match.start(1)] + nuevo_num
                else:
                    # Si no hay número, agregar uno al final
                    nuevo_folio = folio_actual + "1"

                # Guardar nuevo folio
                f.seek(0)
                f.write(nuevo_folio)
                f.truncate()

            return folio_actual  # devuelve el actual, no el siguiente

        except Exception as e:
            print(f"⚠️ Error al generar folio autonumérico: {e}")
            return "00A00001"

    def actualizar_totales(self):
        """
        Calcula subtotal, descuento, IVA y total:
        - El IVA solo se aplica a los productos con 'iva' = 'Sí'
        - El descuento del cliente se aplica al subtotal
        - El IVA se calcula sobre los productos gravados después del descuento
        """
        if not hasattr(self, "input_subtotal"):
            return

        subtotal = 0.0
        suma_con_iva = 0.0

        try:
            for fila in range(self.tabla.rowCount()):
                # --- Cantidad ---
                cantidad_item = self.tabla.item(fila, 2)
                cantidad = float(cantidad_item.text()) if cantidad_item and cantidad_item.text() else 0.0

                # --- Precio Real ---
                precio_real_item = self.tabla.item(fila, 5)
                precio_real = float(precio_real_item.text()) if precio_real_item and precio_real_item.text() else 0.0

                # --- Otro Precio (prioritario si existe) ---
                otro_precio_item = self.tabla.item(fila, 7)
                otro_precio_txt = (otro_precio_item.text().strip() if otro_precio_item and otro_precio_item.text() else "")
                otro_precio = 0.0
                if otro_precio_txt:
                    try:
                        otro_precio = float(otro_precio_txt.replace(",", "").replace("$", ""))
                    except ValueError:
                        otro_precio = 0.0

                precio_usado = otro_precio if otro_precio > 0 else precio_real
                importe = cantidad * precio_usado

                # --- IVA del producto (de la base) ---
                iva_item = self.tabla.item(fila, 9)  # columna donde guardas el IVA del producto
                tiene_iva = iva_item and iva_item.text().strip().lower() in ["sí", "si"]

                # --- Actualizar importe en tabla ---
                item_importe = self.tabla.item(fila, 8)
                if item_importe is None:
                    item_importe = QTableWidgetItem()
                    self.tabla.setItem(fila, 8, item_importe)
                item_importe.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

                if cantidad > 0 and precio_usado > 0:
                    item_importe.setText(f"{importe:,.2f}")
                else:
                    item_importe.setText("")

                # --- Sumar al subtotal general ---
                subtotal += importe

                # --- Si tiene IVA, sumar a la base gravable ---
                if tiene_iva:
                    suma_con_iva += importe

            # === 🔹 Descuento del cliente ===
            desc_txt = self.input_descuento_cliente.text().replace("%", "").strip()
            try:
                porcentaje_desc = float(desc_txt)
            except ValueError:
                porcentaje_desc = 0.0

            descuento_total = subtotal * (porcentaje_desc / 100)
            self.lbl_desc.setText(f"Descuento ({porcentaje_desc:.2f}%):")

            # === 🔹 IVA solo sobre productos con IVA ===
            base_iva = suma_con_iva - (suma_con_iva * (porcentaje_desc / 100))
            iva_total = base_iva * 0.16

            # === 🔹 Total general ===
            total = (subtotal - descuento_total) + iva_total

            # === 🔹 Mostrar en campos ===
            self.input_subtotal.setText(f"{subtotal:,.2f}")
            self.input_descuento.setText(f"{descuento_total:,.2f}")
            self.input_iva.setText(f"{iva_total:,.2f}")
            self.input_total.setText(f"{total:,.2f}")

        except Exception as e:
            print("❌ Error al calcular totales:", e)
    
    def _on_cell_editada_recalculo(self, fila, columna):
        """
        Se ejecuta automáticamente cuando se edita cualquier celda en la tabla.
        Solo recalcula si se editan columnas relevantes:
        2 = Cantidad
        5 = Precio real
        7 = Otro precio
        """

        # ⛔ No hacer nada mientras se carga una factura
        if getattr(self, "cargando_factura", False):
            # print(f"⏳ Saltado _on_cell_editada_recalculo (cargando_factura=True)")
            return

        if columna not in [2, 5, 7]:
            return

        try:
            self.tabla.blockSignals(True)
            self.recalcular_totales()
        except Exception as e:
            print(f"⚠️ Error en _on_cell_editada_recalculo: {e}")
        finally:
            self.tabla.blockSignals(False)


    def buscar_producto_por_cip(self, row, column):
        """Busca el producto por CIP y llena los datos (Descripción, Precio Real, Tipo de lista del producto)."""
        try:
            if self.tabla.signalsBlocked() or column != 0:
                return

            item = self.tabla.item(row, column)
            if not item:
                return

            cip = item.text().strip()
            if not cip:
                return

            cliente = self.input_cliente.text().strip()
            empresa = self.combo_empresa.currentText().strip()

            if not cliente or not empresa:
                QMessageBox.warning(self, "Faltan datos", "Debes seleccionar un cliente y empresa antes de buscar productos.")
                return

            resp = requests.get(f"{API_URL}/comandas/producto/{cip}/{cliente}/{empresa}")
            if resp.status_code != 200:
                QMessageBox.warning(self, "Producto no encontrado", f"No se encontró el producto con CIP {cip}.")
                return

            prod = resp.json()
            descripcion = (prod.get("descripcion") or "").strip()
            tipo_lista = (prod.get("tipo_lista") or "Estándar").strip()
            precio_base = float(prod.get("precio") or 0.0)
            iva = (prod.get("iva") or "No").strip()  # ✅ Nuevo

            # --- Cálculo de Precio Real considerando IVA ---
            precio_real = self._calcular_precio_real(row, precio_base, tipo_lista, iva)

            datos = {
                1: descripcion,                # Descripción
                4: f"{precio_base:.2f}",       # Precio (lista)
                5: f"{precio_real:.2f}",       # Precio Real (ajustado)
                8: f"{(float(self.tabla.item(row, 2).text() or 0) * precio_real):,.2f}",  # Importe
            }

            for col, valor in datos.items():
                celda = self.tabla.item(row, col)
                if celda is None:
                    celda = QTableWidgetItem()
                    self.tabla.setItem(row, col, celda)
                celda.setText(str(valor))
                if col in [1, 4, 5]:
                    celda.setFlags(celda.flags() & ~Qt.ItemIsEditable)
                    celda.setBackground(QColor("#f5f5f5"))
            
            # ✅ Guardar IVA oculto
            col_iva = 9
            if col_iva >= self.tabla.columnCount():
                self.tabla.setColumnCount(col_iva + 1)
            iva_item = QTableWidgetItem(iva)
            iva_item.setFlags(Qt.ItemIsSelectable)
            iva_item.setForeground(QColor("#f9fafb"))
            self.tabla.setItem(row, col_iva, iva_item)

            # 🔹 Recalcular importe
            self._actualizar_importe_fila(row)


            # “Pzas” editable
            celda_pzas = self.tabla.item(row, 3)
            if celda_pzas:
                celda_pzas.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled)
                celda_pzas.setBackground(QColor("#ffffff"))

            self.tabla.setCurrentCell(row, 2)

            # 🔹 Recalcular totales SOLO si NO estamos cargando factura
            if not getattr(self, "cargando_factura", False):
                try:
                    self.recalcular_totales()
                except Exception as e:
                    print(f"⚠️ Error recalculando totales en buscar_producto_por_cip fila {row}: {e}")


        except Exception as e:
            QMessageBox.critical(self, "Error de conexión", str(e))






    # =====================================================
    # 🔹 FUNCIÓN GUARDAR FACTURA (vista previa COMPLETA)
    #    - Modo normal  → factura nueva
    #    - Modo edición → conserva folio y muestra "Guardar cambios"
    # =====================================================
    def guardar_factura(self, previsualizacion=False):
        """
        Genera la vista previa (PDF temporal) y pasa todos los datos a VistaPreviaFactura.

        - NO guarda nada en BD (eso lo hace VistaPreviaFactura.guardar_factura / guardar_cambios)
        - Usa 'Otro Precio' (col 7) y, si está vacío, cae a 'Precio Real' (col 5).
        - En modo edición mantiene el folio original.
        """

        import os, requests
        from io import BytesIO
        from tempfile import NamedTemporaryFile
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from num2words import num2words
        from PyQt5.QtWidgets import QMessageBox

        print("💾 [DEBUG] Ejecutando guardar_factura()")
        print(f"   → modo_edicion actual: {getattr(self, 'modo_edicion', False)}")

        # ─────────────────────────────────────────────────────
        # Helpers
        # ─────────────────────────────────────────────────────
        def txt(tabla, r, c):
            it = tabla.item(r, c)
            return it.text().strip() if (it and it.text()) else ""

        def to_float(x):
            try:
                return float(str(x).replace("$", "").replace(",", "").strip() or 0)
            except Exception:
                return 0.0

        def to_int(x):
            try:
                return int(round(to_float(x)))
            except Exception:
                return 0

        # ─────────────────────────────────────────────────────
        # Encabezado desde la GUI
        # ─────────────────────────────────────────────────────
        empresa         = (self.combo_empresa.currentText() or "").strip()
        cliente_nombre  = (self.input_nombre_cliente.text() or "").strip()
        cliente_numero  = (self.input_cliente.text() or "").strip()
        vendedor        = (self.input_vendedor.text() or "").strip()
        rfc_caja        = (self.input_rfc.text() or "").strip()

        # % de descuento del cliente (campo "%")
        desc_cli_txt    = (self.input_descuento_cliente.text() or "0").replace("%", "").strip()
        try:
            descuento_valor_pct = float(desc_cli_txt or 0)
        except Exception:
            descuento_valor_pct = 0.0

        subtotal        = to_float(self.input_subtotal.text())
        descuento_total = to_float(self.input_descuento.text())
        iva_total       = to_float(self.input_iva.text())
        total           = to_float(self.input_total.text())

        fecha_impresa   = self.input_fecha.date().toString("d MMM yyyy").upper()

        # ─────────────────────────────────────────────────────
        # Config emisor por empresa (mismo layout que antes)
        # ─────────────────────────────────────────────────────
        emp_low = empresa.lower()
        if "ibersur" in emp_low:
            direccion = "Dakota N°359 Int. 301 - Ampliación Nápoles - Benito Juárez - CDMX"
            rfc_tel   = "RFC IBE 090212 JV1 / TEL. 5555439933"
            logo_path = "logos/ibersur.png"
        elif "eza2007" in emp_low:
            direccion = "Dakota N°359 Int. 301 - Ampliación Nápoles - Benito Juárez - CDMX"
            rfc_tel   = "RFC EZA 070521 MT4 / TEL. 5555439933"
            logo_path = "logos/eza2007.png"
        elif "gourmet" in emp_low:
            direccion = "Texas N°100 - Nápoles - Benito Juárez - CDMX"
            rfc_tel   = "RFC GES 090312 DJ1 / TEL. 5555439933"
            logo_path = "logos/gourmet.png"
        else:
            direccion = "Dirección no definida"
            rfc_tel   = ""
            logo_path = "logos/default.png"

        # ─────────────────────────────────────────────────────
        # Buscar datos completos del cliente (para PDF)
        # ─────────────────────────────────────────────────────
        cliente_info = {}
        consignatario = cliente_nombre
        try:
            from cliente import API_URL
            url_cli = f"{API_URL}/clientes/{cliente_numero}/{empresa}"
            resp_cliente = requests.get(url_cli)
            if resp_cliente.status_code == 200:
                cliente_info = resp_cliente.json() or {}
                consignatario = cliente_info.get("consignatario", cliente_nombre) or cliente_nombre
        except Exception as e:
            print(f"⚠️ API clientes falló: {e}")

        # si la API trae RFC, usamos ese; si no, el que está en la caja
        rfc_pdf = cliente_info.get("rfc", "") or rfc_caja

        # ─────────────────────────────────────────────────────
        # Crear PDF en memoria (MISMO DISEÑO que antes)
        # ─────────────────────────────────────────────────────
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

        # Estilo para celdas de texto
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

        # Tabla Cliente
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

        # Tabla Consignatario
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

        # Folio impreso (el de la caja)
        folio_texto_caja = (self.input_folio.text() or "").strip() or "-"

        elements.append(Paragraph(
            f"<para alignment='right'><b>FOLIO: {folio_texto_caja}</b></para>",
            ParagraphStyle(name="folio_style", fontSize=10, alignment=2)
        ))
        elements.append(Spacer(1, 2))

        cliente_consig = Table([[cliente_table, consignatario_table]], colWidths=[270, 270])
        cliente_consig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        elements.append(cliente_consig)
        elements.append(Spacer(1, 6))

        # Línea separadora
        elements.append(Table([[""]], colWidths=[540], style=[("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black)]))
        elements.append(Spacer(1, 1))

        # Datos rápidos (Ubicación, fecha, pago, etc.)
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

        # ─────────────────────────────────────────────────────
        # Tabla de productos (mismo layout por empresa)
        # ─────────────────────────────────────────────────────
        data = []
        if "gourmet españa" in emp_low:
            headers = ["Cantidad", "Unidad", "CIP", "Descripción", "Código", "Piezas", "Precio", "Total"]
            col_widths = [55, 45, 35, 195, 70, 40, 50, 50]
        else:
            headers = ["Cantidad", "Unidad", "CIP", "Descripción", "Piezas", "Precio", "Total"]
            col_widths = [60, 50, 45, 225, 55, 50, 55]
        data.append(headers)

        from cliente import API_URL  # ya lo usamos arriba

        for r in range(self.tabla.rowCount()):
            cip = txt(self.tabla, r, 0)
            desc = txt(self.tabla, r, 1)
            if not cip and not desc:
                continue

            cantidad   = txt(self.tabla, r, 2)
            pzas       = txt(self.tabla, r, 3)
            precio7    = txt(self.tabla, r, 7)
            precio5    = txt(self.tabla, r, 5)
            precio_txt = precio7 or precio5 or "0"
            importe    = txt(self.tabla, r, 8)

            # Descripción como Paragraph
            desc_par = Paragraph(desc, ParagraphStyle(
                name="desc", fontName="Helvetica", fontSize=8, leading=9, alignment=0
            ))

            # Unidad + código de barras desde API (opcional)
            unidad = "PZA"
            codigo_barras = ""
            try:
                if cip:
                    rp = requests.get(f"{API_URL}/productos/{cip}")
                    if rp.status_code == 200:
                        pd = rp.json() or {}
                        unidad = pd.get("unidad", "PZA")
                        precios = pd.get("precios", {})
                        for nombre_lista, datos in (precios or {}).items():
                            if isinstance(datos, dict) and (emp_low in nombre_lista.lower() or nombre_lista.lower() in emp_low):
                                codigo_barras = (datos.get("codigo_barras", "") or "").strip()
                                break
                        if not codigo_barras:
                            codigo_barras = (pd.get("codigo_barras", "") or "").strip()
            except Exception as e:
                print(f"⚠️ Producto {cip}: {e}")

            codigo_o_cip = codigo_barras if codigo_barras else "-"

            if "gourmet españa" in emp_low:
                fila = [cantidad, unidad, cip, desc_par, codigo_o_cip, pzas, precio_txt, importe]
            else:
                fila = [cantidad, unidad, cip, desc_par, pzas, precio_txt, importe]
            data.append(fila)

        tabla_prod = Table(data, repeatRows=1, colWidths=col_widths)
        tabla_prod.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 1), (-1, -1), "CENTER"),
            ("ALIGN", (3, 0), (3, -1), "LEFT"),
            ("LEFTPADDING", (3, 1), (3, -1), 5),
            ("RIGHTPADDING", (3, 1), (3, -1), 5),
        ]))
        elements.append(tabla_prod)
        elements.append(Spacer(1, 10))

        # ─────────────────────────────────────────────────────
        # Totales
        # ─────────────────────────────────────────────────────
        totales_data = [
            ["", "", "", "", "SUMA",          f"${subtotal:,.2f}"],
            ["", "", "", "", f"Descuento ({descuento_valor_pct:.2f}%)", f"-${descuento_total:,.2f}"],
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

        # Total en letra
        total_letra = num2words(int(total), lang="es").upper().replace("EUROS", "").strip()
        decimales = int(round((total % 1) * 100))
        elements.append(Paragraph(
            f"{total_letra} PESOS {decimales:02d}/00 M.N.",
            ParagraphStyle(name="total_letra", fontSize=10, alignment=0)
        ))

        # ─────────────────────────────────────────────────────
        # Render PDF temporal
        # ─────────────────────────────────────────────────────
        doc.build(elements)
        buffer_pdf.seek(0)
        with NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(buffer_pdf.read())
            temp_path = tmp.name

        print(f"📄 PDF temporal generado: {temp_path}")

        # ─────────────────────────────────────────────────────
        # Armar payload que usa VistaPreviaFactura / API
        # ─────────────────────────────────────────────────────
        # Folio siguiente sugerido
        folio_sugerido = self.obtener_siguiente_folio()

        # Productos para payload (sin formato)
        productos_payload = []
        for r in range(self.tabla.rowCount()):
            if not (txt(self.tabla, r, 0) or txt(self.tabla, r, 1)):
                continue
            productos_payload.append({
                "cip":        txt(self.tabla, r, 0),
                "descripcion":txt(self.tabla, r, 1),
                "cantidad":   to_float(txt(self.tabla, r, 2)),
                "piezas":     to_int(txt(self.tabla, r, 3)),
                "precio":     to_float(txt(self.tabla, r, 7) or txt(self.tabla, r, 5)),
            })

        # ¿Estamos en modo edición?
        modo_edicion = getattr(self, "modo_edicion", False)

        if modo_edicion and hasattr(self, "datos_factura"):
            numero_factura_final = self.datos_factura.get("factura") or self.datos_factura.get("folio")
        else:
            numero_factura_final = folio_sugerido

        datos_factura_payload = {
            "folio":            numero_factura_final,
            "factura":          numero_factura_final,
            "numero_cliente":   cliente_numero,
            "cliente_numero":   cliente_numero,
            "cliente_nombre":   cliente_nombre,
            "empresa":          empresa,
            "vendedor":         vendedor,
            "rfc":              rfc_pdf,
            "subtotal":         subtotal,
            "descuento_pct":    descuento_valor_pct,
            "descuento_total":  descuento_total,
            "iva":              iva_total,
            "total":            total,
            "productos":        productos_payload,
            "consignatario":    consignatario,
        }

        print("📦 [DEBUG] Payload para VistaPreviaFactura:")
        print(datos_factura_payload)

        # ─────────────────────────────────────────────────────
        # Abrir VistaPreviaFactura (misma para nueva y edición)
        # ─────────────────────────────────────────────────────
        vista = VistaPreviaFactura(
            temp_path,
            cliente_nombre=cliente_nombre,
            total=total,
            parent=self,
            numero_factura=numero_factura_final,
            modo_edicion=modo_edicion,
            datos_factura=datos_factura_payload
        )
        vista.exec_()
    
    def actualizar_folio_automatico(self):
        """Actualiza automáticamente el folio cuando cambia el cliente o la empresa."""
        try:
            # Esperar un poco para evitar actualizaciones dobles mientras se escribe
            QTimer.singleShot(300, lambda: self.input_folio.setText(self.obtener_siguiente_folio()))
        except Exception as e:
            print(f"⚠️ Error al actualizar folio automáticamente: {e}")
    
    def cargar_factura(self, datos):
        """Rellena la pestaña con la información de una factura existente."""
        try:
            factura = datos
            productos = factura.get("productos", [])

            # === Encabezado ===
            self.input_folio.setText(factura.get("factura", ""))
            self.input_cliente.setText(factura.get("numero_cliente", ""))
            self.input_nombre_cliente.setText(factura.get("consignatario", ""))
            self.combo_empresa.setCurrentText(factura.get("empresa", ""))
            self.input_descuento.setText(f"{factura.get('descuento', 0):,.2f}")
            self.input_iva.setText(f"{factura.get('iva', 0):,.2f}")
            self.input_total.setText(f"{factura.get('total', 0):,.2f}")

            fecha_str = str(factura.get("fecha", ""))[:10]
            if fecha_str:
                self.input_fecha.setDate(QDate.fromString(fecha_str, "yyyy-MM-dd"))

            # === Limpiar tabla ===
            self.tabla.blockSignals(True)
            self.tabla.clearContents()
            self.tabla.setRowCount(max(len(productos), 15))

            # === Rellenar productos ===
            for i, prod in enumerate(productos):
                cip = str(prod.get("cip", ""))
                desc = str(prod.get("descripcion", ""))
                cantidad = prod.get("cantidad", 0)
                piezas = prod.get("piezas", 0)
                precio = prod.get("precio", 0)

                # CIP
                self.tabla.setItem(i, 0, QTableWidgetItem(cip))

                # Descripción
                self.tabla.setItem(i, 1, QTableWidgetItem(desc))

                # Cantidad
                self.tabla.setItem(i, 2, QTableWidgetItem(str(cantidad)))

                # Piezas
                self.tabla.setItem(i, 3, QTableWidgetItem(str(piezas)))

                # Columna 4 (Código de barras / CIP alterno)
                self.tabla.setItem(i, 4, QTableWidgetItem(""))

                # Precio real (col 5)
                self.tabla.setItem(i, 5, QTableWidgetItem(f"{precio:,.2f}"))

                # Columna 6 (vacía)
                self.tabla.setItem(i, 6, QTableWidgetItem(""))

                # Otro precio (col 7) → se rellena igual que el real
                self.tabla.setItem(i, 7, QTableWidgetItem(f"{precio:,.2f}"))

                # Importe (col 8)
                self.tabla.setItem(i, 8, QTableWidgetItem(f"{cantidad * precio:,.2f}"))

            self.tabla.blockSignals(False)

            # === Calcular totales ===
            self.actualizar_totales()

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            QMessageBox.critical(self, "Error", f"Error al cargar factura:\n{e}")
    
    def recalcular_totales(self):
        """Recalcula subtotal, descuento, IVA y total respetando:
        - El IVA individual por producto (columna oculta 9)
        - El descuento del cliente
        - 'Otro Precio' si está presente
        """

        if getattr(self, "cargando_factura", False):
            return

        def to_float(v):
            if v is None:
                return 0.0
            try:
                return float(str(v).replace(",", "").replace("$", "").strip())
            except:
                return 0.0

        subtotal = 0.0
        suma_con_iva = 0.0

        for fila in range(self.tabla.rowCount()):
            try:
                cantidad     = to_float(self.tabla.item(fila, 2).text())
                precio_real  = to_float(self.tabla.item(fila, 5).text())
                otro_precio  = to_float(self.tabla.item(fila, 7).text())
                precio_usado = otro_precio if otro_precio > 0 else precio_real

                importe = cantidad * precio_usado

                # actualizar importe en tabla (col 8)
                it_importe = self.tabla.item(fila, 8)
                if it_importe:
                    self.tabla.blockSignals(True)
                    it_importe.setText(f"{importe:.2f}")
                    self.tabla.blockSignals(False)

                subtotal += importe

                # ====== 🔥 REVISAR IVA POR PRODUCTO (COLUMNA 9) ======
                iva_item = self.tabla.item(fila, 9)

                tiene_iva = (
                    iva_item and str(iva_item.text()).strip().lower() in ["sí", "si", "1", "true"]
                )

                if tiene_iva:
                    suma_con_iva += importe

            except Exception:
                continue

        # ====== DESCUENTO ======
        desc_pct = to_float(self.input_descuento_cliente.text().replace("%", ""))
        descuento_total = subtotal * (desc_pct / 100.0)

        # ====== IVA SOLO SOBRE PRODUCTOS GRAVADOS ======
        base_iva = suma_con_iva - (suma_con_iva * (desc_pct / 100.0))
        iva_total = base_iva * 0.16

        # ====== TOTAL ======
        total = (subtotal - descuento_total) + iva_total

        # ====== ACTUALIZAR CAMPOS ======
        self.input_subtotal.setText(f"{subtotal:.2f}")
        self.input_descuento.setText(f"{descuento_total:.2f}")
        self.input_iva.setText(f"{iva_total:.2f}")
        self.input_total.setText(f"{total:.2f}")
    
    def limpiar_numero(self, valor):
        """Convierte '1,588.00' → 1588.00 de forma segura."""
        if not valor:
            return 0.0
        try:
            return float(str(valor).replace(",", ""))
        except:
            return 0.0
    def limpiar_todo(self):
        """Limpia controles, totales, tabla y también la casilla de comanda."""
        try:
            # --- Encabezado ---
            self.input_folio.clear()
            self.input_cliente.clear()
            self.input_nombre_cliente.clear()
            self.input_vendedor.clear()
            self.input_rfc.clear()
            self.input_fecha.setDate(QDate.currentDate())
            self.combo_empresa.setCurrentIndex(0)

            # --- Comanda ---
            self.input_comanda.setText("")   # 🔥 NUEVO: limpia el campo de comanda

            # --- Totales ---
            self.input_subtotal.setText("0.00")
            self.input_descuento_cliente.setText("0%")
            self.input_descuento.setText("0.00")
            self.input_iva.setText("0.00")
            self.input_total.setText("0.00")

            # --- Limpiar tabla ---
            self.tabla.blockSignals(True)
            for fila in range(self.tabla.rowCount()):
                for col in range(self.tabla.columnCount()):
                    item = self.tabla.item(fila, col)
                    if item:
                        item.setText("")
                        item.setBackground(QColor("#ffffff"))
            self.tabla.blockSignals(False)

            # --- Estado ---
            self.modo_edicion = False
            self.datos_factura = None

            # --- Recalcular para dejar todo en cero ---
            self.recalcular_totales()

        except Exception as e:
            print("⚠ Error en limpiar_todo:", e)


    

# ==========================================
# 🧾 Ventana "Mio" - Historial de Facturas
# ==========================================
class VentanaMio(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MioTab")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 0, 15, 0)  # elimina márgenes superior e inferior
        layout.setSpacing(5)

        # --- 🔹 Barra superior (Filtros + Total)
        barra_container = QWidget()
        barra_layout = QHBoxLayout(barra_container)
        barra_layout.setContentsMargins(0, 0, 0, 0)
        barra_layout.setSpacing(10)

        # --- Total destacado y centrado (crear primero)
        self.lbl_total = QLabel("Total del mes: $0.00")
        self.lbl_total.setAlignment(Qt.AlignCenter)
        self.lbl_total.setStyleSheet("""
            font-weight: bold;
            font-size: 16pt;
            color: #1f2937;
            padding: 8px;
            background-color: #e0f2fe;
            border: 2px solid #38bdf8;
            border-radius: 8px;
        """)
        self.lbl_total.setFixedWidth(320)

        # --- Filtros centrados
        filtros_widget = QWidget()
        filtros_layout = QHBoxLayout(filtros_widget)
        filtros_layout.setContentsMargins(0, 0, 0, 0)
        filtros_layout.setSpacing(15)

        lbl_mes = QLabel("Mes:")
        lbl_mes.setStyleSheet("font-weight: bold; font-size: 11pt;")
        self.combo_mes = QComboBox()
        meses = [
            "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
        ]
        self.combo_mes.addItems(meses)
        self.combo_mes.setCurrentIndex(datetime.now().month - 1)
        self.combo_mes.setFixedWidth(130)

        lbl_anio = QLabel("Año:")
        lbl_anio.setStyleSheet("font-weight: bold; font-size: 11pt;")
        self.combo_anio = QComboBox()
        self.combo_anio.addItems([str(a) for a in range(2020, datetime.now().year + 1)])
        self.combo_anio.setCurrentText(str(datetime.now().year))
        self.combo_anio.setFixedWidth(100)

        # --- 🔹 Nuevo filtro por empresa
        lbl_empresa = QLabel("Empresa:")
        lbl_empresa.setStyleSheet("font-weight: bold; font-size: 11pt;")
        self.combo_empresa = QComboBox()
        self.combo_empresa.addItems(["Todas", "Gourmet España", "Ibersur", "EZA2007"])
        self.combo_empresa.setFixedWidth(180)

        btn_filtrar = QPushButton("🔄 Cargar")
        btn_filtrar.setFixedWidth(140)
        btn_filtrar.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: white;
                font-weight: bold;
                font-size: 11pt;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #1e4ed8;
            }
        """)
        btn_filtrar.clicked.connect(self.cargar_datos)
        self.combo_empresa.currentIndexChanged.connect(self.cargar_datos)

        # --- Añadir al layout centrado
        filtros_layout.addWidget(lbl_mes)
        filtros_layout.addWidget(self.combo_mes)
        filtros_layout.addWidget(lbl_anio)
        filtros_layout.addWidget(self.combo_anio)
        filtros_layout.addWidget(lbl_empresa)
        filtros_layout.addWidget(self.combo_empresa)
        filtros_layout.addWidget(btn_filtrar)
        filtros_layout.setAlignment(Qt.AlignCenter)

        # --- Agregar al layout de barra
        barra_layout.addStretch(1)
        barra_layout.addWidget(filtros_widget, alignment=Qt.AlignCenter)
        barra_layout.addSpacing(20)
        barra_layout.addWidget(self.lbl_total, alignment=Qt.AlignCenter)
        barra_layout.addStretch(1)

        layout.addWidget(barra_container)



        # --- 🔹 Tabla
        self.tabla = QTableWidget()
        self.tabla.setColumnCount(7)
        self.tabla.setHorizontalHeaderLabels([
            "Factura", "Día", "Mes", "Cliente", "Importe", "Tienda", "SAE"
        ])
        # === Habilitar edición con un solo clic ===
        self.tabla.setEditTriggers(QAbstractItemView.SelectedClicked | QAbstractItemView.EditKeyPressed)
        # === Activar edición con un solo clic (como en las demás pestañas) ===
        self.tabla.setEditTriggers(QAbstractItemView.NoEditTriggers)  # Desactivamos edición automática normal

        # Interceptamos clics manualmente
        self.tabla.cellClicked.connect(self.activar_edicion_celda)
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tabla.setEditTriggers(QAbstractItemView.DoubleClicked)
        self.tabla.itemChanged.connect(self.actualizar_sae)
        layout.addWidget(self.tabla)

        # === 🔹 Barra de botones inferiores ===
        botones_container = QWidget()
        botones_layout = QHBoxLayout(botones_container)
        botones_layout.setContentsMargins(0, 10, 0, 0)
        botones_layout.setSpacing(25)
        botones_layout.setAlignment(Qt.AlignCenter)

        # --- Eliminar factura ---
        btn_eliminar = QPushButton("🗑️ Eliminar factura")
        btn_eliminar.setFixedWidth(180)
        btn_eliminar.setFixedHeight(40)
        btn_eliminar.setCursor(Qt.PointingHandCursor)
        btn_eliminar.setStyleSheet("""
            QPushButton {
                background-color: #dc2626;
                color: white;
                font-weight: bold;
                border-radius: 6px;
                font-size: 10.5pt;
            }
            QPushButton:hover {
                background-color: #b91c1c;
            }
        """)
        btn_eliminar.clicked.connect(self.eliminar_factura)

        # --- Cancelar factura ---
        btn_cancelar = QPushButton("🚫 Cancelar factura")
        btn_cancelar.setFixedWidth(180)
        btn_cancelar.setFixedHeight(40)
        btn_cancelar.setCursor(Qt.PointingHandCursor)
        btn_cancelar.setStyleSheet("""
            QPushButton {
                background-color: #f97316;
                color: white;
                font-weight: bold;
                border-radius: 6px;
                font-size: 10.5pt;
            }
            QPushButton:hover {
                background-color: #ea580c;
            }
        """)
        btn_cancelar.clicked.connect(self.cancelar_factura)

        # --- Exportar facturas ---
        btn_exportar = QPushButton("📦 Exportar facturas")
        btn_exportar.setFixedWidth(180)
        btn_exportar.setFixedHeight(40)
        btn_exportar.setCursor(Qt.PointingHandCursor)
        btn_exportar.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: white;
                font-weight: bold;
                border-radius: 6px;
                font-size: 10.5pt;
            }
            QPushButton:hover {
                background-color: #1e40af;
            }
        """)
        btn_exportar.clicked.connect(self.exportar_facturas)

        # --- ✏️ Editar factura ---
        btn_editar = QPushButton(" Editar factura")
        btn_editar.setIcon(qta.icon("mdi.file-document-edit", color="white"))
        btn_editar.setIconSize(QSize(22, 22))
        btn_editar.setFixedWidth(190)
        btn_editar.setFixedHeight(42)
        btn_editar.setCursor(Qt.PointingHandCursor)
        btn_editar.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: white;
                font-weight: bold;
                border-radius: 6px;
                font-size: 10.5pt;
                padding-left: 10px;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
        """)
        btn_editar.clicked.connect(self.editar_factura)

        # --- Agregar botones al layout ---

        # --- Agregar botones al layout ---
        botones_layout.addWidget(btn_editar)
        botones_layout.addWidget(btn_eliminar)
        botones_layout.addWidget(btn_cancelar)
        botones_layout.addWidget(btn_exportar)
        layout.addWidget(botones_container)

        # === Cargar datos iniciales ===
        self.cargar_datos()

    # ======================================================
    # 📦 Cargar facturas según mes/año
    # ======================================================
    def cargar_datos(self):
        import mysql.connector
        from PyQt5.QtGui import QColor, QBrush, QFont
        try:
            mes = self.combo_mes.currentIndex() + 1
            anio = int(self.combo_anio.currentText())
            empresa = getattr(self, "combo_empresa", None)
            empresa_filtro = empresa.currentText() if empresa else None

            conn = mysql.connector.connect(
                host="192.168.1.105",
                user="Facturacion",
                password="ALD2013*",
                database="comandas_db",
                port=3306
            )
            cursor = conn.cursor(dictionary=True)

            # 🔹 Si la tabla tiene columna empresa, filtrar por ella
            query = """
                SELECT 
                    f.factura,
                    DATE_FORMAT(f.fecha, '%d') AS dia,
                    DATE_FORMAT(f.fecha, '%b') AS mes,
                    f.numero_cliente AS cliente,
                    f.total AS importe,

                    -- 🔥 AQUI USAMOS EL NOMBRE REAL DEL CLIENTE
                    c.nombre AS tienda,

                    IFNULL(f.sae_codigo, '') AS sae,
                    IFNULL(f.estatus, '') AS estatus
                FROM facturas f
                LEFT JOIN clientes c ON c.numero = f.numero_cliente
                WHERE MONTH(f.fecha)=%s AND YEAR(f.fecha)=%s
            """
            params = [mes, anio]
            # Filtrar según prefijo del folio
            if empresa_filtro and empresa_filtro.lower() not in ["", "todas"]:
                # 🔹 Filtrar según el prefijo del folio (equivalente a la empresa)
                if "gourmet" in empresa_filtro.lower():
                    query += " AND factura LIKE '00A%%'"
                elif "ibersur" in empresa_filtro.lower():
                    query += " AND factura LIKE 'A00%%'"
                elif "eza2007" in empresa_filtro.lower():
                    query += " AND factura LIKE 'CFDI%%'"

            query += " ORDER BY fecha ASC"

            cursor.execute(query, tuple(params))
            resultados = cursor.fetchall()
            self.tabla.setRowCount(len(resultados))

            total = 0
            for i, f in enumerate(resultados):
                estatus = f.get("estatus", "").strip().lower()
                cancelada = estatus == "cancelada" or f.get("sae", "").strip().upper() == "CANCELADO"

                # === FACTURA (editable)
                factura_item = QTableWidgetItem(f["factura"])
                factura_item.setTextAlignment(Qt.AlignCenter)
                factura_item.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled)
                self.tabla.setItem(i, 0, factura_item)

                # === DÍA (bloqueado)
                dia_item = QTableWidgetItem(str(f["dia"]))
                dia_item.setTextAlignment(Qt.AlignCenter)
                dia_item.setFlags(Qt.ItemIsEnabled)
                self.tabla.setItem(i, 1, dia_item)

                # === MES (bloqueado)
                mes_item = QTableWidgetItem(f["mes"].upper())
                mes_item.setTextAlignment(Qt.AlignCenter)
                mes_item.setFlags(Qt.ItemIsEnabled)
                self.tabla.setItem(i, 2, mes_item)

                # === CLIENTE (bloqueado)
                cliente_item = QTableWidgetItem(f["cliente"])
                cliente_item.setTextAlignment(Qt.AlignCenter)
                cliente_item.setFlags(Qt.ItemIsEnabled)
                self.tabla.setItem(i, 3, cliente_item)

                # === IMPORTE ===
                importe_valor = 0 if cancelada else float(f["importe"])
                importe_item = QTableWidgetItem(f"${importe_valor:,.2f}")
                importe_item.setTextAlignment(Qt.AlignCenter)
                importe_item.setFlags(Qt.ItemIsEnabled)
                self.tabla.setItem(i, 4, importe_item)

                # === TIENDA ===
                tienda_item = QTableWidgetItem(f["tienda"])
                tienda_item.setTextAlignment(Qt.AlignCenter)
                tienda_item.setFlags(Qt.ItemIsEnabled)
                self.tabla.setItem(i, 5, tienda_item)

                # === SAE ===
                sae_text = f.get("sae", "").strip().upper()
                sae_item = QTableWidgetItem(sae_text if sae_text else "")
                sae_item.setTextAlignment(Qt.AlignCenter)
                if cancelada:
                    sae_item.setText("CANCELADO")
                    sae_item.setForeground(QBrush(QColor("#dc2626")))
                    font = QFont()
                    font.setBold(True)
                    sae_item.setFont(font)
                    sae_item.setFlags(Qt.ItemIsEnabled)  # deshabilita edición
                else:
                    sae_item.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled)
                self.tabla.setItem(i, 6, sae_item)

                # Solo sumar si no está cancelada
                total += importe_valor

            # === Estilo general ===
            self.tabla.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
            self.tabla.setStyleSheet("""
                QTableWidget {
                    font-size: 10.5pt;
                    gridline-color: #cbd5e1;
                    selection-background-color: #dbeafe;
                    selection-color: black;
                }
                QHeaderView::section {
                    background-color: #f3f4f6;
                    font-weight: bold;
                    border: 1px solid #d1d5db;
                    padding: 4px;
                }
            """)

            # === Ajuste proporcional ===
            total_width = self.tabla.viewport().width()
            proporciones = [0.12, 0.07, 0.07, 0.10, 0.12, 0.40, 0.12]
            for i, p in enumerate(proporciones):
                self.tabla.setColumnWidth(i, int(total_width * p))

            # === Total actualizado ===
            self.lbl_total.setText(f"<b><span style='font-size:14pt;'>Total del mes: ${total:,.2f}</span></b>")
            self.lbl_total.setAlignment(Qt.AlignCenter)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudieron cargar los datos:\n{e}")
        finally:
            try:
                cursor.close()
                conn.close()
            except:
                pass

    # ======================================================
    # 📊 Exportar datos cargados a DataFrame (para Reportes)
    # ======================================================
    def obtener_dataframe_facturas(self):
        """Obtiene las facturas reales desde la base de datos MySQL (sin warnings de pandas)."""
        import pandas as pd
        from sqlalchemy import create_engine

        try:
            # --- Crear conexión SQLAlchemy compatible ---
            engine = create_engine("mysql+mysqlconnector://Facturacion:ALD2013*@192.168.1.105:3306/comandas_db")

            # ===============================================================
            # 🔥 MODIFICADO: ahora el nombre REAL del cliente viene de c.nombre
            # ===============================================================
            query = """
                SELECT
                    f.id,
                    f.factura,
                    f.numero_cliente AS cliente,
                    f.total,
                    f.fecha,

                    -- ⭐ NOMBRE REAL DEL CLIENTE
                    c.nombre AS tienda,

                    CASE
                        WHEN f.factura LIKE '00A%%' THEN 'Gourmet España'
                        WHEN f.factura LIKE 'A00%%' THEN 'Ibersur'
                        WHEN f.factura LIKE 'CFDI%%' THEN 'EZA2007'
                        ELSE 'Desconocida'
                    END AS empresa

                FROM facturas f
                LEFT JOIN clientes c ON c.numero = f.numero_cliente   -- ⭐ JOIN agregado

                WHERE f.estatus = 'Activa'
                ORDER BY f.id DESC
            """

            df = pd.read_sql(query, engine)

            # Convertir fecha y agregar columnas auxiliares
            df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
            df["dia"] = df["fecha"].dt.day
            df["mes"] = df["fecha"].dt.strftime("%b").str.upper()
            df["mes_num"] = df["fecha"].dt.month

            return df

        except Exception as e:
            print("⚠️ Error al obtener facturas desde MySQL:", e)
            return pd.DataFrame(columns=["factura", "cliente", "empresa", "total", "tienda", "fecha"])



    def obtener_productos_facturados(self):
        """Consulta todos los productos facturados activos desde MySQL (sin depender de la tabla visible)."""
        import mysql.connector
        import pandas as pd

        try:
            conn = mysql.connector.connect(
                host="192.168.1.105",
                user="Facturacion",
                password="ALD2013*",
                database="comandas_db",
                port=3306
            )
            cursor = conn.cursor(dictionary=True)

            # ===============================================================
            # 🔥 MODIFICADO PARA USAR EL NOMBRE REAL DEL CLIENTE
            # ===============================================================
            query = """
                SELECT 
                    f.factura,

                    -- ⭐ NOMBRE REAL DEL CLIENTE (ya no consignatario)
                    c.nombre AS tienda,

                    f.numero_cliente AS cliente,
                    f.fecha,
                    f.total AS total_factura,
                    d.descripcion AS producto,
                    d.cantidad,
                    d.piezas,
                    d.precio

                FROM factura_detalle d
                JOIN facturas f ON f.id = d.factura_id
                LEFT JOIN clientes c ON c.numero = f.numero_cliente   -- ⭐ JOIN agregado

                WHERE f.estatus = 'Activa'
                AND d.descripcion IS NOT NULL
                AND TRIM(d.descripcion) <> ''
            """

            cursor.execute(query)
            resultados = cursor.fetchall()

            if not resultados:
                print("⚠️ No se encontraron productos facturados activos.")
                return pd.DataFrame(columns=["producto", "cantidad", "precio", "factura", "tienda", "cliente"])

            df = pd.DataFrame(resultados)
            print(f"✅ Productos facturados cargados: {len(df)} registros únicos ({df['factura'].nunique()} facturas)")

            return df

        except Exception as e:
            print("❌ Error al obtener productos facturados:", e)
            return pd.DataFrame(columns=["producto", "cantidad", "precio", "factura", "tienda", "cliente"])
        finally:
            try:
                cursor.close()
                conn.close()
            except:
                pass



  
    def resizeEvent(self, event):
        super().resizeEvent(event)
        total_width = self.tabla.viewport().width()
        proporciones = [0.12, 0.07, 0.07, 0.10, 0.12, 0.40, 0.12]
        for i, p in enumerate(proporciones):
            self.tabla.setColumnWidth(i, int(total_width * p))




    # ======================================================
    # 💾 Guardar código SAE al editar
    # ======================================================
    def actualizar_sae(self, item):
        if item.column() != 6:
            return
        fila = item.row()
        factura = self.tabla.item(fila, 0).text()
        sae_valor = item.text().strip()

        import mysql.connector
        try:
            conn = mysql.connector.connect(
                host="192.168.1.105",
                user="Facturacion",
                password="ALD2013*",
                database="comandas_db",
                port=3306
            )
            cursor = conn.cursor()
            cursor.execute("UPDATE facturas SET sae_codigo=%s WHERE factura=%s", (sae_valor, factura))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"No se pudo actualizar SAE:\n{e}")
    def activar_edicion_celda(self, fila, columna):
        """Permite editar solo las columnas Factura (0) y SAE (6) con un clic."""
        if columna in (0, 6):  # Solo Factura y SAE
            item = self.tabla.item(fila, columna)
            if item and item.flags() & Qt.ItemIsEditable:
                self.tabla.editItem(item)
    # ======================================================
    # 🗑️ Eliminar factura seleccionada (DB + tabla)
    # ======================================================
    def eliminar_factura(self):
        fila = self.tabla.currentRow()
        if fila < 0:
            QMessageBox.warning(self, "Atención", "Selecciona una factura para eliminar.")
            return

        factura = self.tabla.item(fila, 0).text()
        confirm = QMessageBox.question(
            self,
            "Confirmar eliminación",
            f"¿Seguro que deseas eliminar la factura '{factura}'?\n\nEsta acción no se puede deshacer.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        import mysql.connector
        try:
            conn = mysql.connector.connect(
                host="192.168.1.105",
                user="Facturacion",
                password="ALD2013*",
                database="comandas_db",
                port=3306
            )
            cursor = conn.cursor()
            # Borrar detalle y encabezado
            cursor.execute("DELETE FROM factura_detalle WHERE factura_id=(SELECT id FROM facturas WHERE factura=%s)", (factura,))
            cursor.execute("DELETE FROM facturas WHERE factura=%s", (factura,))
            conn.commit()

            # Quitar de la tabla y recalcular total visual
            self.tabla.removeRow(fila)
            self._recalcular_total_local()

            QMessageBox.information(self, "Factura eliminada", f"La factura '{factura}' fue eliminada correctamente.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo eliminar la factura:\n{e}")
        finally:
            try:
                cursor.close()
                conn.close()
            except:
                pass


    # ======================================================
    # 🚫 Cancelar factura (total=0, SAE=CANCELADO en rojo)
    # ======================================================
    def cancelar_factura(self):
        fila = self.tabla.currentRow()
        if fila < 0:
            QMessageBox.warning(self, "Atención", "Selecciona una factura para cancelar.")
            return

        factura = self.tabla.item(fila, 0).text()
        confirm = QMessageBox.question(
            self,
            "Confirmar cancelación",
            f"¿Deseas cancelar la factura '{factura}'?\n\nSe pondrá el importe en $0.00 y SAE='CANCELADO'.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        import mysql.connector
        try:
            conn = mysql.connector.connect(
                host="192.168.1.105",
                user="Facturacion",
                password="ALD2013*",
                database="comandas_db",
                port=3306
            )
            cursor = conn.cursor()
            # Actualiza encabezado: total=0, estatus Cancelada y SAE CANCELADO
            cursor.execute("""
                UPDATE facturas
                SET total=0,
                    estatus='Cancelada',
                    sae_codigo='CANCELADO'
                WHERE factura=%s
            """, (factura,))
            conn.commit()

            # === Actualizar UI ===
            # Importe -> $0.00
            importe_item = self.tabla.item(fila, 4)
            if importe_item:
                importe_item.setText("$0.00")

            # SAE -> CANCELADO (rojo en negritas)
            sae_item = self.tabla.item(fila, 6)
            if not sae_item:
                sae_item = QTableWidgetItem("CANCELADO")
                self.tabla.setItem(fila, 6, sae_item)
            else:
                sae_item.setText("CANCELADO")

            # Estilo rojo y negritas
            from PyQt5.QtGui import QColor, QBrush, QFont
            sae_item.setForeground(QBrush(QColor("#dc2626")))
            font = sae_item.font()
            font.setBold(True)
            sae_item.setFont(font)

            # (Opcional) bloquear edición de SAE para canceladas:
            sae_item.setFlags((sae_item.flags() | Qt.ItemIsEnabled) & ~Qt.ItemIsEditable)

            # Recalcular total mostrado
            self._recalcular_total_local()

            QMessageBox.information(self, "Factura cancelada", f"La factura '{factura}' fue cancelada correctamente.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo cancelar la factura:\n{e}")
        finally:
            try:
                cursor.close()
                conn.close()
            except:
                pass


    # ======================================================
    # 🔢 Recalcular el total del mes en la etiqueta (desde la tabla)
    # ======================================================
    def _recalcular_total_local(self):
        total = 0.0
        for r in range(self.tabla.rowCount()):
            item_imp = self.tabla.item(r, 4)  # columna Importe
            if not item_imp:
                continue
            txt = (item_imp.text() or "").replace("$", "").replace(",", "").strip()
            try:
                total += float(txt) if txt else 0.0
            except ValueError:
                pass
        self.lbl_total.setText(f"<b><span style='font-size:14pt;'>Total del mes: ${total:,.2f}</span></b>")
        self.lbl_total.setAlignment(Qt.AlignCenter)
    
    def editar_factura(self):
        fila = self.tabla.currentRow()
        if fila < 0:
            QMessageBox.warning(self, "Aviso", "Selecciona una factura para editar.")
            return

        folio = self.tabla.item(fila, 0).text().strip()
        if not folio:
            QMessageBox.warning(self, "Aviso", "No se pudo obtener el folio.")
            return

        import requests
        from cliente import API_URL

        resp = requests.get(f"{API_URL}/facturas/folio/{folio}")
        if resp.status_code != 200:
            QMessageBox.warning(self, "Error", resp.text)
            return

        datos = resp.json()
        if "error" in datos:
            QMessageBox.warning(self, "Error", datos["error"])
            return

        # ====================================================
        # ⭐ OBTENER FACTURACIONTAB DESDE EL MAINWINDOW
        # ====================================================
        main = self.window()                     # obtiene la ventana principal
        tabs = main.findChild(QTabWidget)        # obtiene el contenedor de pestañas

        # asumir que Facturación es TAB 0 (o ajusta si es otro índice)
        facturacion_tab = tabs.widget(0)

        # ====================================================
        # ⭐ MODO EDICIÓN
        # ====================================================
        facturacion_tab.modo_edicion = True
        facturacion_tab.datos_factura = datos

        # Cargar datos en la ventana de facturación
        facturacion_tab.cargar_factura_en_edicion(datos)

        # Cambiar a pestaña Facturacion
        tabs.setCurrentWidget(facturacion_tab)

        QMessageBox.information(self, "Editar", f"Factura {folio} cargada en modo edición.")




    # ======================================================
    # 📦 Exportar facturas a Excel
    # ======================================================
    def exportar_facturas(self):
        from openpyxl import Workbook
        from PyQt5.QtWidgets import QFileDialog

        ruta, _ = QFileDialog.getSaveFileName(self, "Guardar como", "", "Archivos Excel (*.xlsx)")
        if not ruta:
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "Facturas"

        # --- Encabezados ---
        encabezados = ["Factura", "Día", "Mes", "Cliente", "Importe", "Tienda", "SAE"]
        ws.append(encabezados)

        # --- Contenido ---
        for fila in range(self.tabla.rowCount()):
            datos = []
            for col in range(self.tabla.columnCount()):
                item = self.tabla.item(fila, col)
                datos.append(item.text() if item else "")
            ws.append(datos)

        try:
            wb.save(ruta)
            QMessageBox.information(self, "Exportación completada", f"Archivo guardado correctamente en:\n{ruta}")
        except Exception as e:
            QMessageBox.critical(self, "Error al exportar", str(e))
    
    # ======================================================
    # 📤 Exportar facturas en formato de importación SAE
    # ======================================================
    def exportar_sae_excel(self):
        try:
            # Obtener datos reales desde DB
            df_prod = self.obtener_productos_facturados()
            if df_prod.empty:
                QMessageBox.warning(self, "Sin datos", "No hay productos facturados para exportar.")
                return

            # Convertimos a formato SAE
            facturas_sae = []
            for factura, grupo in df_prod.groupby("factura"):
                produtos = []
                for _, r in grupo.iterrows():
                    produtos.append({
                        "cip": r["producto"],
                        "cantidad": r["cantidad"],
                        "precio": r["precio"],
                        "iva": True  # Puedes mejorar leyendo IVA real
                    })

                facturas_sae.append({
                    "serie": "",
                    "folio": factura,
                    "cliente_numero": str(grupo.iloc[0]["cliente"]),
                    "fecha": grupo.iloc[0]["fecha"].strftime("%Y-%m-%d"),
                    "productos": produtos
                })

            # Generar archivo Excel
            from exportar_sae import exportar_facturas_excel
            filename = exportar_facturas_excel(facturas_sae)

            QMessageBox.information(
                self,
                "Exportación SAE",
                f"Archivo generado correctamente:\n{filename}\n\n"
                "📌 Importar en SAE:\n"
                "Módulo Facturas → Utilerías → Importar Documentos"
            )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo exportar:\n{e}")
    

class EditorFacturaDialog(QDialog):
    def __init__(self, datos_factura, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Editar factura {datos_factura.get('factura', '')}")
        self.resize(1200, 900)

        layout = QVBoxLayout(self)

        # 🔹 Aquí insertamos tu FacturacionTab con modo edición
        self.form = FacturacionTab(
            modo_edicion=True,
            datos_factura=datos_factura,
            parent=self
        )

        layout.addWidget(self.form)

        # Botón cerrar
        boton = QPushButton("Cerrar")
        boton.clicked.connect(self.close)

        box = QHBoxLayout()
        box.addStretch(1)
        box.addWidget(boton)

        layout.addLayout(box)

# ==========================================
# 📊 Ventana "Reportes" - Dashboard ERP (2 pestañas)
# ==========================================
import random

import matplotlib
matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

import qtawesome as qta

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QDateEdit, QFrame, QSizePolicy, QGridLayout, QTabWidget,
    QDialog, QMessageBox, QGraphicsDropShadowEffect
)
from PyQt5.QtCore import Qt, QDate
from PyQt5.QtGui import QColor
import warnings
from detalle_cliente import DetalleClienteWidget
warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")


class VentanaReportes(QWidget):
    def __init__(self, ventana_mio=None, parent=None):
        super().__init__(parent)

        self.ventana_mio = ventana_mio  # ✅ guardamos la referencia a la pestaña Mio
        self.setObjectName("ReportesTab")

        # --- Layout principal ---
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(0)

        # --- Tabs internas ---
        self.tabs = QTabWidget()
        self.tabs.addTab(self.crear_dashboard_general(), "📊 Dashboard General")
        self.tabs.addTab(self.crear_dashboard_gerencial(), "📈 Indicadores Gerenciales")
        self.tabs.addTab(self.crear_reporte_cliente(), "📘 Reporte Cliente")

        layout.addWidget(self.tabs)
    
    def normalizar_columna_tienda(self, df_merge):
        """
        Garantiza que el DataFrame tenga una columna 'tienda' válida,
        sin importar si viene como tienda_x, tienda_y, cliente o consignatario.
        """
        if "tienda" not in df_merge.columns:
            for col in ["tienda_y", "tienda_x", "cliente", "consignatario", "nombre"]:
                if col in df_merge.columns:
                    df_merge["tienda"] = df_merge[col]
                    print(f"⚙️ Columna 'tienda' creada a partir de '{col}'")
                    break
            else:
                df_merge["tienda"] = "Desconocido"
                print("⚠️ No se encontró ninguna columna de cliente — se usó 'Desconocido'")

        # 🔹 Limpiar columnas duplicadas (por si quedaron del merge)
        for col in ["tienda_x", "tienda_y"]:
            if col in df_merge.columns:
                df_merge.drop(columns=col, inplace=True, errors="ignore")

        # 🔹 Asegurar tipo texto y sin nulos
        df_merge["tienda"] = df_merge["tienda"].astype(str).fillna("Desconocido")

        return df_merge
    # === Helpers para logos, estilo de botones y barras con texto ===

    def _find_logo_path(self, empresa: str) -> str:
        """
        Busca un logo en img/logos/ intentando variantes del nombre de la empresa.
        Soporta .png/.jpg/.jpeg/.svg. Devuelve ruta o None si no encuentra.
        """
        import os, re
        if not empresa:
            return None
        base_dir = os.path.join(os.path.dirname(__file__), "img", "logos")
        if not os.path.isdir(base_dir):
            return None

        # Normalizaciones comunes: quitar tildes, espacios extra, caracteres raros
        def norm(s):
            import unicodedata
            s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
            s = s.strip().lower()
            s = re.sub(r"[^a-z0-9]+", "_", s)  # no alfanum -> _
            s = re.sub(r"_+", "_", s).strip("_")
            return s

        candidates = []
        raw_names = [empresa, empresa.replace("S.A. de C.V.", "").replace("S.A.", "").strip()]
        exts = [".png", ".jpg", ".jpeg", ".svg"]

        for raw in raw_names:
            n = norm(raw)
            # probar varias variantes
            variants = {raw, raw.strip(), n, n.replace("_", ""), n.replace("_", "-")}
            for v in variants:
                for ext in exts:
                    candidates.append(os.path.join(base_dir, f"{v}{ext}"))

        # También listar todo el directorio y buscar por contiene nombre normalizado
        try:
            files = os.listdir(base_dir)
            n_emp = norm(empresa)
            for f in files:
                if any(f.lower().endswith(ext) for ext in exts):
                    f_norm = norm(os.path.splitext(f)[0])
                    if n_emp in f_norm or f_norm in n_emp:
                        candidates.insert(0, os.path.join(base_dir, f))
        except Exception:
            pass

        for p in candidates:
            if os.path.isfile(p):
                return p
        return None

    # === Helper: centrar y fijar tamaño de QDialog ===
    def _center_on_parent(self, dlg, w=900, h=760):
        from PyQt5.QtCore import Qt, QRect
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.resize(w, h)
        if self.parent() is not None:
            parent_geom = self.parent().frameGeometry()
        else:
            parent_geom = self.frameGeometry()
        scr_center = parent_geom.center()
        g = QRect(0, 0, w, h)
        g.moveCenter(scr_center)
        dlg.move(g.topLeft())

    def _style_action_buttons(self, btn_pdf, btn_xlsx, btn_close):
        """
        Aplica estilo (CLR1 tipo Office) y centra botones con íconos MDI.
        """
        import qtawesome as qta
        btn_pdf.setIcon(qta.icon("mdi.file-pdf-box"))
        btn_xlsx.setIcon(qta.icon("mdi.microsoft-excel"))
        btn_close.setIcon(qta.icon("mdi.close"))

        btn_pdf.setStyleSheet("""
            QPushButton {
                background-color: #D93025; color: white; font-weight: 600;
                padding: 6px 14px; border-radius: 8px; border: 0;
            }
            QPushButton:hover { background-color: #B1271A; }
        """)
        btn_xlsx.setStyleSheet("""
            QPushButton {
                background-color: #1E8E3E; color: white; font-weight: 600;
                padding: 6px 14px; border-radius: 8px; border: 0;
            }
            QPushButton:hover { background-color: #166B2F; }
        """)
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #6E6E6E; color: white; font-weight: 600;
                padding: 6px 14px; border-radius: 8px; border: 0;
            }
            QPushButton:hover { background-color: #555555; }
        """)


    def _barh_with_inner_labels(self, ax, index_labels, values, base_color="#205493"):
        """
        Dibuja barras horizontales con:
        - nombre dentro (blanco, sin sombra) si cabe; si no, fuera en negro semi-bold
        - monto a la derecha en negritas
        - estilo consistente con el panel principal
        """
        from matplotlib import transforms

        y = list(range(len(index_labels)))
        bars = ax.barh(y, values, color=base_color, zorder=3)

        ax.set_yticks([])          # sin etiquetas eje Y (evita duplicados)
        ax.invert_yaxis()
        ax.grid(axis="x", linestyle="--", alpha=0.45, zorder=0)
        ax.set_ylabel("")
        ax.margins(y=0.12)

        max_val = max(values) if values else 0
        if max_val > 0:
            ax.set_xlim(0, max_val * 1.18)

        blend_tx = transforms.blended_transform_factory(ax.transAxes, ax.transData)
        umbral_dentro = max_val * 0.17

        for bar, nombre, val in zip(bars, index_labels, values):
            y_center = bar.get_y() + bar.get_height() / 2
            ancho = bar.get_width()

            if ancho >= umbral_dentro:
                x_text = max(ancho * 0.02, max_val * 0.01)
                ax.text(x_text, y_center, str(nombre), va="center", ha="left",
                        fontsize=9, color="white", fontweight="normal", zorder=4)
            else:
                x_text = ancho + (max_val * 0.01)
                ax.text(x_text, y_center, str(nombre), va="center", ha="left",
                        fontsize=9, color="#111", fontweight="bold", zorder=4)

            ax.text(0.98, y_center, f"${val:,.0f}", transform=blend_tx,
                    ha="right", va="center", fontsize=10, fontweight="bold",
                    color="#222", zorder=4)

        return bars
    def _cargar_empresas_reporte_seguro(self):
        try:
            empresas = self.obtener_empresas()
            if not empresas:
                raise Exception("Sin empresas")

            # Agregar al combo
            self.combo_empresa_reporte.clear()
            self.combo_empresa_reporte.addItem("Todas") 
            self.combo_empresa_reporte.addItems(empresas)

            print("✔ Empresas cargadas:", empresas)

        except Exception as e:
            print("⚠ Error al cargar empresas:", e)
            QMessageBox.warning(self, "Advertencia",
                "No se pudieron cargar las empresas desde el servidor.")
            
    def obtener_empresas(self):
        """
        Devuelve siempre las 3 empresas disponibles del sistema.
        La opción 'Todas' se agrega SOLO en el combo, no aquí.
        """
        return ["EZA2007", "GOURMET", "IBERSUR"]
    # ============================================================
    # 📊 1️⃣ Dashboard General (ya existente)
    # ============================================================
    def crear_dashboard_general(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(10)

        # --- 🔹 Toolbar moderna de filtros ---
        barra = QWidget()
        barra_layout = QHBoxLayout(barra)
        barra_layout.setContentsMargins(10, 5, 10, 5)
        barra_layout.setSpacing(12)
        barra_layout.setAlignment(Qt.AlignCenter)

        # Estilo moderno plano
        barra.setStyleSheet("""
            QWidget {
                background-color: #f2f3f5;
                border: 1px solid #d1d5db;
                border-radius: 8px;
            }
            QLabel {
                color: #333;
                font-weight: 500;
            }
            QComboBox, QDateEdit {
                background: white;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 4px 8px;
                min-width: 130px;
            }
            QPushButton {
                background-color: #0078d7;
                color: white;
                font-weight: bold;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #005fa3;
            }
        """)

        # --- Empresa ---
        self.combo_empresa = QComboBox()
        self.combo_empresa.addItem("Todas")
        self.combo_empresa.addItems(self.obtener_empresas())
        self.combo_empresa.currentIndexChanged.connect(self.generar_reportes)
        barra_layout.addWidget(QLabel("Empresa:"))
        barra_layout.addWidget(self.combo_empresa)

        # --- Cliente ---
        self.combo_cliente = QComboBox()
        self.combo_cliente.setEditable(True)
        self.combo_cliente.setPlaceholderText("Cliente o número...")
        self.combo_cliente.setInsertPolicy(QComboBox.NoInsert)
        self.combo_cliente.setDuplicatesEnabled(False)
        barra_layout.addWidget(QLabel("Cliente:"))
        barra_layout.addWidget(self.combo_cliente)

        # --- Producto ---
        self.combo_producto = QComboBox()
        self.combo_producto.setEditable(True)
        self.combo_producto.setPlaceholderText("Producto o familia...")
        barra_layout.addWidget(QLabel("Producto:"))
        barra_layout.addWidget(self.combo_producto)

        # === 🔧 Filtros de fecha (inicio/fin) ===
        from PyQt5.QtCore import QDate
        hoy = QDate.currentDate()
        self.fecha_inicio = QDateEdit()
        self.fecha_inicio.setCalendarPopup(True)
        self.fecha_inicio.setDisplayFormat("yyyy-MM-dd")
        self.fecha_inicio.setDate(QDate(hoy.year(), hoy.month(), 1))  # Primer día del mes actual

        self.fecha_fin = QDateEdit()
        self.fecha_fin.setCalendarPopup(True)
        self.fecha_fin.setDisplayFormat("yyyy-MM-dd")
        self.fecha_fin.setDate(hoy)

        barra_layout.addWidget(QLabel("Desde:"))
        barra_layout.addWidget(self.fecha_inicio)
        barra_layout.addWidget(QLabel("Hasta:"))
        barra_layout.addWidget(self.fecha_fin)

        # === Botón de aplicar filtros ===
        self.btn_actualizar = QPushButton("Aplicar filtros 🔍")

        def _aplicar_filtros():
            cliente_txt = self.combo_cliente.currentText().strip()
            producto_txt = self.combo_producto.currentText().strip()

            self.combo_cliente.blockSignals(True)
            self.combo_producto.blockSignals(True)
            self.cargar_clientes_y_productos()
            self.combo_cliente.setEditText(cliente_txt)
            self.combo_producto.setEditText(producto_txt)
            self.combo_cliente.blockSignals(False)
            self.combo_producto.blockSignals(False)

            self.generar_reportes()

        self.btn_actualizar.clicked.connect(_aplicar_filtros)
        barra_layout.addWidget(self.btn_actualizar)

        # 🔧 Actualizar automáticamente al escribir o seleccionar cliente


        # Agregar barra al layout principal
        layout.addWidget(barra)


        # --- 🔹 Grid de reportes ---
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(15)
        self.grid.setVerticalSpacing(15)
        self.grid.setContentsMargins(0, 5, 0, 5)
        layout.addLayout(self.grid)

        # 🔥 Hacer que las celdas se expandan uniformemente
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)
        self.grid.setRowStretch(0, 1)
        self.grid.setRowStretch(1, 1)

        # Crear y cargar paneles
        self.widgets = []
        self.crear_paneles_generales()
        self.generar_reportes()


        # --- Cargar combos con datos reales ---
        self.cargar_clientes_y_productos()

        return widget
    def crear_reporte_cliente(self):
        """
        Pestaña para mostrar el reporte individual del cliente usando DetalleClienteWidget.
        Incluye filtros fijos y scroll para el área del reporte.
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 0, 10, 0)   # margen TOP 0 TOTAL
        layout.setSpacing(0)                      # SIN ESPACIADO ENTRE FILTRO Y SCROLL

        # =========================================
        # 🔹 1) FILTRO FIJO (NO SE MUEVE)
        # =========================================
        filtro_wrapper = QWidget()
        filtro_wrapper_layout = QHBoxLayout(filtro_wrapper)
        filtro_wrapper_layout.setAlignment(Qt.AlignCenter)
        filtro_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        filtro_wrapper_layout.setSpacing(15)

        # --- Empresa ---
        lbl_emp = QLabel("Empresa:")
        lbl_emp.setStyleSheet("font-weight: bold; font-size: 11pt; color: #333;")

        self.combo_empresa_reporte = QComboBox()
        self.combo_empresa_reporte.addItem("Todas")
        QTimer.singleShot(700, self._cargar_empresas_reporte_seguro)
        print("📌 Columnas DF Facturas:", self.ventana_mio.obtener_dataframe_facturas().columns)
        print("📌 Empresas detectadas:", self.obtener_empresas())
        self.combo_empresa_reporte.setMinimumWidth(150)

        # --- Cliente ---
        lbl_cli = QLabel("Cliente seleccionado:")
        lbl_cli.setStyleSheet("font-weight: bold; font-size: 11pt; color: #333;")

        self.combo_cliente_reporte = QComboBox()
        self.combo_cliente_reporte.setEditable(True)
        self.combo_cliente_reporte.setPlaceholderText("Buscar cliente...")
        self.combo_cliente_reporte.setMinimumWidth(250)

        self.cargar_clientes_en_reporte()

        # --- Botón ---
        btn_aplicar = QPushButton("Cargar reporte")
        btn_aplicar.setStyleSheet("""
            QPushButton {
                background-color: #2563eb; 
                color: white; 
                padding: 6px 14px; 
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #1d4ed8; }
        """)
        btn_aplicar.clicked.connect(self._cargar_reporte_cliente)

        # Agregar widgets al filtro
        filtro_wrapper_layout.addWidget(lbl_emp)
        filtro_wrapper_layout.addWidget(self.combo_empresa_reporte)
        filtro_wrapper_layout.addWidget(lbl_cli)
        filtro_wrapper_layout.addWidget(self.combo_cliente_reporte)
        filtro_wrapper_layout.addWidget(btn_aplicar)

        # Filtro siempre visible
        layout.addWidget(filtro_wrapper)

        # =========================================
        # 🔹 2) ÁREA DE REPORTE CON SCROLL
        # =========================================
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("border: none;")

        # Contenido del scroll
        scroll_content = QWidget()
        self.scroll_content_layout = QVBoxLayout(scroll_content)
        self.scroll_content_layout.setContentsMargins(10, 0, 10, 0)
        self.scroll_content_layout.setSpacing(5)

        scroll_area.setWidget(scroll_content)

        # Agregar scroll al layout principal
        layout.addWidget(scroll_area)

        # Guardar referencia para actualizar el reporte
        self.scroll_content = scroll_content

        return widget
    
    def cargar_clientes_en_reporte(self):
        """
        Llenar el combo de clientes basado en los datos de ventana_mio.
        """
        self.combo_cliente_reporte.clear()

        if not self.ventana_mio:
            return

        try:
            df = self.ventana_mio.obtener_dataframe_facturas()
            if "cliente" in df.columns:
                clientes = sorted(df["cliente"].astype(str).dropna().unique().tolist())
                self.combo_cliente_reporte.addItems(clientes)
        except:
            pass
        
    def _cargar_reporte_cliente(self):
        """
        Crea el widget DetalleClienteWidget con la empresa + cliente seleccionados.
        Si se selecciona 'Todas', determina la empresa real del cliente.
        """
        cliente = self.combo_cliente_reporte.currentText().strip()
        empresa = self.combo_empresa_reporte.currentText().strip()

        if not cliente:
            QMessageBox.information(self, "Cliente", "Seleccione un cliente válido.")
            return

        # ==========================================
        # 🔥 CORRECCIÓN: Resolver caso 'Todas'
        # ==========================================
        if empresa == "Todas":
            try:
                df = self.ventana_mio.obtener_dataframe_facturas()
                df_c = df[df["cliente"].astype(str) == cliente]

                if df_c.empty:
                    QMessageBox.warning(
                        self,
                        "Sin registros",
                        f"El cliente {cliente} no tiene facturas en ninguna empresa."
                    )
                    return

                empresa = df_c.sort_values("fecha", ascending=False).iloc[0]["empresa"]

            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Error",
                    f"No se pudo determinar la empresa del cliente:\n{e}"
                )
                return

        # =======================================
        # LIMPIAR REPORTE ANTERIOR
        # =======================================
        while self.scroll_content_layout.count():
            item = self.scroll_content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # =======================================
        # CREAR EL NUEVO WIDGET
        # =======================================
        try:
            widget = DetalleClienteWidget(cliente, empresa)  # ⬅️ LA LÍNEA IMPORTANTE
            self.scroll_content_layout.addWidget(widget)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo crear el reporte:\n{e}")
    # ------------------------------------------------------------
    def crear_paneles_generales(self):
        titulos = [
            ("Clientes Top", "mdi.trophy-outline"),
            ("Productos más vendidos", "mdi.package-variant-closed"),
            ("Frecuencia de recompra", "mdi.history"),
            ("Concentrado por cadena", "mdi.store")
        ]
        posiciones = [(0, 0), (0, 1), (1, 0), (1, 1)]

        for i, ((titulo, icono), pos) in enumerate(zip(titulos, posiciones)):
            frame = QFrame()
            frame.setStyleSheet("""
                QFrame {
                    background-color: #ffffff;
                    border-radius: 12px;
                    border: 1px solid #e2e8f0;
                }
            """)

            # 🌟 Sombra moderna
            shadow = QGraphicsDropShadowEffect()
            shadow.setBlurRadius(15)
            shadow.setOffset(0, 3)
            shadow.setColor(QColor(0, 0, 0, 60))
            frame.setGraphicsEffect(shadow)

            v_layout = QVBoxLayout(frame)
            v_layout.setContentsMargins(10, 10, 10, 10)
            v_layout.setSpacing(5)

            # 🔹 Encabezado con ícono + texto
            header = QWidget()
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(0, 0, 0, 0)
            header_layout.setSpacing(6)

            icon_label = QLabel()
            icon_label.setPixmap(qta.icon(icono, color="#333").pixmap(20, 20))

            lbl_titulo = QLabel(titulo)
            lbl_titulo.setStyleSheet("font-weight: bold; font-size: 12pt; color: #333;")

            header_layout.addWidget(icon_label)
            header_layout.addWidget(lbl_titulo)
            header_layout.addStretch()

            v_layout.addWidget(header)

            # Canvas del gráfico
            fig, ax = plt.subplots(figsize=(5, 3))
            canvas = FigureCanvas(fig)
            canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            v_layout.addWidget(canvas, stretch=10)

            self.grid.addWidget(frame, *pos)
            self.widgets.append((canvas, ax))



    def acortar_nombre_producto(self, nombre):
        """Acorta nombres largos de productos manteniendo claridad."""
        if not nombre:
            return nombre

        nombre = nombre.title()  # Capitalización bonita

        # Palabras a eliminar si aparecen
        palabras_sobrantes = ["De", "Del", "La", "El", "Con", "Sin", "Y", "Para"]
        palabras = [p for p in nombre.split() if p not in palabras_sobrantes]

        # Si supera 22 caracteres → aplicar abreviación
        corto = " ".join(palabras)
        if len(corto) > 22:
            # Reglas de abreviación
            corto = corto.replace("Queso", "Q.")
            corto = corto.replace("Exhibidor", "Exhib.")
            corto = corto.replace("Manchego", "Manch.")
            corto = corto.replace("Artesanal", "Art.")
            corto = corto.replace("Ibérico", "Ibér.")
            corto = corto.replace("Curado", "Cur.")
            corto = corto.replace("Piezas", "pz").replace("Pzs", "pz").replace("Pzs.", "pz")

        return corto
    
    def _aplicar_filtros_generales(self, df_fac):
        """Aplica los filtros globales: empresa, cliente, producto y rango de fechas."""
        import pandas as pd

        if df_fac.empty:
            return df_fac.copy()

        df = df_fac.copy()

        # --- Normaliza o crea columna de fecha ---
        if "fecha" in df.columns:
            df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
        else:
            # Intentar construir una fecha a partir de "dia" y "mes" si existen
            if "dia" in df.columns and "mes" in df.columns:
                try:
                    # ⚙️ construimos un datetime con año actual
                    df["fecha"] = pd.to_datetime(
                        df["dia"].astype(str) + "-" + df["mes"].astype(str) + "-2025",
                        format="%d-%m-%Y",
                        errors="coerce"
                    )
                except Exception as e:
                    print(f"⚠️ No se pudo construir la fecha: {e}")
                    df["fecha"] = pd.NaT
            else:
                print("⚠️ No existe columna de fecha, ni dia/mes para construirla.")
                df["fecha"] = pd.NaT


        # --- Empresa ---
        empresa_sel = self.combo_empresa.currentText() if hasattr(self, "combo_empresa") else "Todas"
        if empresa_sel and empresa_sel != "Todas":
            df = df[df["empresa"] == empresa_sel]

        # --- Cliente (nombre o número parcial) ---
        cliente_txt = self.combo_cliente.currentText().strip().lower()
        if cliente_txt and cliente_txt not in ["", "todos", "todas"]:
            # Busca coincidencia parcial en tienda o en cliente numérico
            mask_tienda = df["tienda"].astype(str).str.lower().str.contains(cliente_txt, na=False)
            mask_cliente = df["cliente"].astype(str).str.lower().str.contains(cliente_txt, na=False) if "cliente" in df.columns else False
            df = df[mask_tienda | mask_cliente]

        # --- Producto (si aplica a productos facturados) ---
        if "producto" in df.columns and hasattr(self, "combo_producto"):
            producto_txt = self.combo_producto.currentText().strip().lower()
            if producto_txt and producto_txt not in ["", "todos", "todas"]:
                df = df[df["producto"].astype(str).str.lower().str.contains(producto_txt, na=False)]

        # --- Rango de fechas ---
        if hasattr(self, "fecha_inicio") and hasattr(self, "fecha_fin") and "fecha" in df.columns:
            fecha_ini = pd.to_datetime(self.fecha_inicio.date().toPyDate(), errors="coerce")
            fecha_fin = pd.to_datetime(self.fecha_fin.date().toPyDate(), errors="coerce")

            if pd.notna(fecha_ini) and pd.notna(fecha_fin):
                mask = (df["fecha"] >= fecha_ini) & (df["fecha"] <= fecha_fin)
                df = df.loc[mask]

        # --- Devuelve filtrado ---
        return df.reset_index(drop=True)




    # ------------------------------------------------------------
    def generar_reportes(self):
        """
        Genera todos los gráficos del Dashboard General:
        - Clientes Top
        - Productos más vendidos
        - Frecuencia de recompra
        - Concentrado por empresa
        Aplica filtros globales (empresa, cliente, producto, fechas).
        """
        import pandas as pd
        from PyQt5.QtWidgets import QMessageBox, QLabel
        from PyQt5.QtCore import Qt

        # === Asegurar tooltip inicializado ===
        if not hasattr(self, "_tooltip"):
            self._tooltip = QLabel("", None)
            self._tooltip.setStyleSheet("""
                QLabel {
                    background-color: #333;
                    color: white;
                    border-radius: 4px;
                    padding: 3px 6px;
                    font-size: 9pt;
                }
            """)
            self._tooltip.setWindowFlags(Qt.ToolTip)
            self._tooltip.hide()

        # === Obtener datos base ===
        df_facturas = pd.DataFrame()
        df_productos = pd.DataFrame()

        if self.ventana_mio:
            try:
                df_facturas = self.ventana_mio.obtener_dataframe_facturas()
                df_productos = self.ventana_mio.obtener_productos_facturados()
            except Exception as e:
                print(f"⚠️ Error cargando datos base: {e}")
                return

        if df_facturas.empty or df_productos.empty:
            QMessageBox.information(self, "Datos", "No hay datos disponibles para generar reportes.")
            return

        # ============================================================
        # 🧩 CREAR / NORMALIZAR COLUMNA DE FECHA
        # ============================================================
        if "fecha" in df_facturas.columns:
            df_facturas["fecha"] = pd.to_datetime(df_facturas["fecha"], errors="coerce")
        else:
            if "dia" in df_facturas.columns and "mes" in df_facturas.columns:
                import datetime
                anio_actual = datetime.date.today().year
                meses_map = {
                    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
                    "jul": 7, "ago": 8, "sep": 9, "set": 9,
                    "oct": 10, "nov": 11, "dic": 12
                }
                try:
                    df_facturas["mes_num"] = (
                        df_facturas["mes"].astype(str).str.strip().str.lower().map(meses_map)
                        .fillna(pd.to_numeric(df_facturas["mes"], errors="coerce"))
                    ).fillna(1).astype(int)

                    df_facturas["fecha"] = pd.to_datetime(
                        df_facturas["dia"].astype(str).str.zfill(2) + "-" +
                        df_facturas["mes_num"].astype(str).str.zfill(2) + "-" +
                        str(anio_actual),
                        format="%d-%m-%Y",
                        errors="coerce"
                    )
                    print(f"✅ Columna 'fecha' creada correctamente (año={anio_actual})")
                    print(df_facturas[["dia", "mes", "fecha"]].head(5))
                except Exception as e:
                    print(f"⚠️ Error al construir fecha: {e}")
                    df_facturas["fecha"] = pd.NaT
            else:
                print("⚠️ No existen columnas 'dia' y 'mes' para generar 'fecha'")
                df_facturas["fecha"] = pd.NaT

        # ============================================================
        # 🔍 APLICAR FILTROS
        # ============================================================
        empresa_filtro = self.combo_empresa.currentText().strip()
        cliente_filtro = self.combo_cliente.currentText().strip().lower()
        producto_filtro = self.combo_producto.currentText().strip().lower()

        # --- Fechas (rango inclusivo) ---
        fecha_ini = self.fecha_inicio.date().toPyDate()
        fecha_fin = self.fecha_fin.date().toPyDate()

        print("🗓️  Filtro interfaz:", fecha_ini, "→", fecha_fin)

        if "fecha" in df_facturas.columns:
            mask = (df_facturas["fecha"].dt.date >= fecha_ini) & (df_facturas["fecha"].dt.date <= fecha_fin)
            df_facturas = df_facturas.loc[mask]

            # ✅ Validación de rango sin datos
            if not df_facturas.empty:
                min_fecha, max_fecha = df_facturas["fecha"].min().date(), df_facturas["fecha"].max().date()
                if fecha_fin < min_fecha or fecha_ini > max_fecha:
                    QMessageBox.information(
                        self, "Rango sin datos",
                        f"No hay facturas entre {fecha_ini} y {fecha_fin}.\n"
                        f"El rango disponible es de {min_fecha} a {max_fecha}."
                    )
                    return

        # --- Filtro por empresa ---
        if empresa_filtro and empresa_filtro != "Todas":
            df_facturas = df_facturas[df_facturas["empresa"].astype(str) == empresa_filtro]

        # --- Filtro por cliente (número o nombre comercial) ---
        if cliente_filtro:
            cliente_filtro_l = cliente_filtro.lower()

            df_facturas = df_facturas[
                df_facturas["cliente"].astype(str).str.contains(cliente_filtro, case=False, na=False)
                |
                df_facturas["tienda"].astype(str).str.lower().str.contains(cliente_filtro_l, na=False)
            ]
        
        
        
        df_debug = df_facturas[
            (df_facturas["cliente"].astype(str) == "100475")
            |
            (df_facturas["tienda"].str.contains("100475", case=False, na=False))
        ]

        # === Depuración en consola ===
        print("----------------------------------------------------")
        print("FACTURAS filtradas:", len(df_facturas))
        print("PRODUCTOS base:", len(df_productos))
        print("Columnas facturas:", df_facturas.columns.tolist())
        print("Ejemplo tiendas:", df_facturas["tienda"].head(3).tolist() if "tienda" in df_facturas.columns else "no hay")
        print("Ejemplo cliente:", df_facturas["cliente"].head(3).tolist() if "cliente" in df_facturas.columns else "no hay")
        print("----------------------------------------------------")


        print("🔎 DEBUG POST-FILTROS:")
        print("Empresa seleccionada:", empresa_filtro)
        print("Cliente seleccionado:", cliente_filtro)
        print("Rango fechas:", fecha_ini, "→", fecha_fin)
        print("Registros luego de filtros:", len(df_facturas))
        print("Primeras filas:")
        print(df_facturas[["empresa", "tienda", "cliente", "fecha"]].head(10))
        if df_facturas.empty:
            QMessageBox.information(self, "Filtros", "No se encontraron registros con los filtros seleccionados.")
            return

        print("Fechas filtradas:", df_facturas["fecha"].min(), "→", df_facturas["fecha"].max(), "Registros:", len(df_facturas))


        # ============================================================
        # 💰 NORMALIZAR COLUMNA TOTAL
        # ============================================================
        if "total" in df_facturas.columns:
            # Convierte a numérico y limpia comas o texto
            df_facturas["total"] = (
                df_facturas["total"]
                .astype(str)
                .str.replace(",", "", regex=False)
                .str.replace("$", "", regex=False)
                .astype(float)
            )
        else:
            print("⚠️ No existe columna 'total', se usará columna simulada.")
            df_facturas["total"] = 0.0

        
        print("💰 Total convertido, ejemplo:", df_facturas["total"].head(5).tolist())

        # ============================================================
        # 📊 CLIENTES TOP
        # ============================================================
        ax = self.widgets[0][1]
        ax.clear()

        top_clientes = (
            df_facturas.groupby("tienda")["total"]
            .sum()
            .nlargest(5)
            .sort_values()
        )

        if not top_clientes.empty:
            top_clientes.plot(kind="barh", ax=ax, color="#4A90E2", zorder=3)
            ax.set_title("Clientes Top")
            ax.set_xlabel("Monto Total ($)")
            ax.grid(axis="x", linestyle="--", alpha=0.5, zorder=0)
            ax.figure.subplots_adjust(left=0.35, right=0.95, top=0.90, bottom=0.15)
        else:
            ax.text(0.5, 0.5, "Sin datos válidos", ha="center", va="center", fontsize=12)
            ax.set_axis_off()

        self.widgets[0][0].draw()

        # === Click para ver detalle de cliente ===
        canvas_cli = self.widgets[0][0]
        ax_cli = self.widgets[0][1]
        bars_cli = ax_cli.patches
        clientes_order = top_clientes.index.tolist()

        if hasattr(self, "_cid_click_clientes_top"):
            try:
                canvas_cli.mpl_disconnect(self._cid_click_clientes_top)
            except Exception:
                pass

        def _on_click_clientes_top(event):
            if event.inaxes != ax_cli:
                return
            for bar, cliente_nombre in zip(bars_cli, clientes_order):
                contains, _ = bar.contains(event)
                if contains:
                    self.mostrar_detalle_cliente(cliente_nombre)
                    break

        self._cid_click_clientes_top = canvas_cli.mpl_connect("button_press_event", _on_click_clientes_top)

        # ============================================================
        # 📦 PRODUCTOS MÁS VENDIDOS (con cálculo de monto_total)
        # ============================================================
        if not df_productos.empty and not df_facturas.empty:
            df_merge = pd.merge(
                df_productos,
                df_facturas[["factura", "tienda", "empresa"]],
                on="factura",
                how="inner"
            )

            # ✅ Normalizar la columna 'tienda' para evitar KeyError
            df_merge = self.normalizar_columna_tienda(df_merge)

            # --- Aplicar filtros adicionales ---
            if producto_filtro:
                df_merge = df_merge[
                    df_merge["producto"].str.lower().str.contains(str(producto_filtro).lower(), na=False, regex=False)
                ]

            # --- Asegurar tipos numéricos y crear columna monto_total ---
            df_merge["cantidad"] = pd.to_numeric(df_merge["cantidad"], errors="coerce").fillna(0)
            df_merge["precio"] = pd.to_numeric(df_merge["precio"], errors="coerce").fillna(0)
            df_merge["monto_total"] = df_merge["cantidad"] * df_merge["precio"]

            # --- Enviar DataFrame con monto_total listo ---
            self._render_productos_mas_vendidos(df_merge)
        else:
            self._render_productos_mas_vendidos(pd.DataFrame())


        # ============================================================
        # 🔁 FRECUENCIA DE RECOMPRA
        # ============================================================
        ax = self.widgets[2][1]
        fig = self.widgets[2][0].figure
        ax.clear()

        # --- Calcular compras por cliente (seguro) ---
        if not df_facturas.empty and "factura" in df_facturas.columns and "tienda" in df_facturas.columns:
            compras_por_cliente = df_facturas.groupby("tienda")["factura"].count()
        else:
            compras_por_cliente = pd.Series(dtype=int)

        # --- Dibujar histograma ---
        if not compras_por_cliente.empty:
            counts, bins, patches = ax.hist(compras_por_cliente, bins=10, color="#F5A623", edgecolor="black")
            ax.set_title("Frecuencia de recompra", pad=10)
            ax.set_xlabel("Cantidad de compras")
            ax.set_ylabel("Clientes")
            fig.subplots_adjust(bottom=0.22, top=0.90, left=0.12, right=0.95)
        else:
            ax.text(0.5, 0.5, "Sin datos", ha="center", va="center", fontsize=12)
            ax.set_axis_off()
            self.widgets[2][0].draw()
            return

        self.widgets[2][0].draw()

        # ============================================================
        # 🎯 CLICK EN BARRA → MOSTRAR DETALLE DE FRECUENCIA
        # ============================================================
        canvas_freq = self.widgets[2][0]
        ax_freq = self.widgets[2][1]

        # Guardar bins y patches
        self._freq_bins = bins
        self._freq_patches = patches

        # Desconectar handler previo si existe
        if hasattr(self, "_cid_click_freq"):
            try:
                canvas_freq.mpl_disconnect(self._cid_click_freq)
            except Exception:
                pass

        def _on_click_frecuencia(event):
            """Detecta clic en una barra del histograma y abre el detalle correspondiente."""
            if event.inaxes != ax_freq:
                return

            import numpy as np
            for i, patch in enumerate(self._freq_patches):
                contains, _ = patch.contains(event)
                if contains:
                    lo = int(np.floor(self._freq_bins[i]))
                    hi = int(np.ceil(self._freq_bins[i + 1]))
                    print(f"🟢 Clic detectado en rango {lo}–{hi}")  # debug
                    self.mostrar_detalle_frecuencia(lo, hi)
                    break

        self._cid_click_freq = canvas_freq.mpl_connect("button_press_event", _on_click_frecuencia)



        # ============================================================
        # 🥧 CONCENTRADO POR EMPRESA
        # ============================================================
        ax = self.widgets[3][1]
        ax.clear()

        if not df_facturas.empty and "empresa" in df_facturas.columns:
            ventas_empresa = df_facturas.groupby("empresa")["total"].sum()
            if not ventas_empresa.empty:
                wedges, texts, autotexts = ax.pie(
                    ventas_empresa,
                    autopct='%1.1f%%',
                    startangle=90,
                    wedgeprops=dict(width=0.65)
                )
                ax.set_title("Concentrado por empresa")
                ax.figure.subplots_adjust(bottom=0.10, top=0.90)
                self._pie_empresa_data = list(zip(wedges, ventas_empresa.index, ventas_empresa.values))
                self._enable_tooltips_pie()
            else:
                ax.text(0.5, 0.5, "Sin datos válidos", ha="center", va="center", fontsize=12)
                ax.set_axis_off()
        else:
            ax.text(0.5, 0.5, "Sin datos", ha="center", va="center", fontsize=12)
            ax.set_axis_off()

        self.widgets[3][0].draw()
        # 🔄 Guardar el DataFrame filtrado para reutilizarlo en detalles
        self._df_facturas_filtrado = df_facturas.copy()

    
    def _render_productos_mas_vendidos(self, df_productos):
        """
        Renderiza el gráfico de 'Productos más vendidos' con:
        - Nombre dentro de la barra (abreviado)
        - Monto afuera alineado a la derecha
        - Hover con brillo suave (Glow)
        - Clic simple para abrir detalle
        """

        from matplotlib import transforms
        import matplotlib.patheffects as pe

        ax = self.widgets[1][1]
        canvas = self.widgets[1][0]
        ax.clear()

        # ------------------------------------------------------------------
        # 1) Validación y preparación del DF
        # ------------------------------------------------------------------
        if df_productos is None or df_productos.empty:
            ax.text(0.5, 0.5, "Sin datos de productos", ha="center", va="center", fontsize=12)
            ax.set_axis_off()
            canvas.draw()
            return

        # Limpieza de tipos numéricos
        df = df_productos.copy()
        df["cantidad"] = df["cantidad"].astype(float)
        df["monto_total"] = df["monto_total"].astype(float)

        # Top 5 por monto
        top = (
            df.groupby("producto")[["cantidad", "monto_total"]]
            .sum()
            .nlargest(5, "monto_total")
            .sort_values("monto_total")
        )

        if top.empty:
            ax.text(0.5, 0.5, "Sin datos válidos", ha="center", va="center", fontsize=12)
            ax.set_axis_off()
            canvas.draw()
            return

        productos  = top.index.tolist()
        cantidades = top["cantidad"].tolist()
        valores    = top["monto_total"].tolist()

        # Colores
        base_color  = "#0078D7"   # azul corporativo
        hover_color = "#1A91FF"   # azul más claro al pasar mouse

        # ------------------------------------------------------------------
        # 2) Funciones auxiliares internas
        # ------------------------------------------------------------------
        def abreviar(nombre: str, max_len: int = 28) -> str:
            if not isinstance(nombre, str) or not nombre.strip():
                return nombre
            s = nombre.strip().title()
            reglas = {
                "Queso": "Q.",
                "Exhibidor": "Exhib.",
                "Manchego": "Manch.",
                "Artesanal": "Art.",
                "Ibérico": "Ibér.",
                "Curado": "Cur.",
                "Piezas": "pz", "Pzs": "pz", "Pzs.": "pz"
            }
            for k, v in reglas.items():
                s = s.replace(k, v)
            if len(s) > max_len:
                s = s[: max_len - 3].rstrip() + "..."
            return s

        def texto_sombreado(ax_, x, y, texto, color="white", sombra_color="black", sombra_dx=1, sombra_dy=-1, **kwargs):
            """
            Dibuja texto con sombra (SH1) para alta legibilidad dentro de barras.
            """
            # Sombra
            ax_.text(x + sombra_dx, y + sombra_dy, texto, color=sombra_color, **kwargs)
            # Texto principal
            ax_.text(x, y, texto, color=color, **kwargs)

        # ------------------------------------------------------------------
        # 3) Dibujar barras
        # ------------------------------------------------------------------
        bars = ax.barh(range(len(productos)), valores, color=base_color, zorder=3)

        # Eje y estilizado
        ax.set_yticks(range(len(productos)))
        ax.set_yticklabels([])     # ocultar etiquetas eje Y
        ax.set_yticks([])          # 🔥 elimina completamente los labels del eje Y
        ax.tick_params(axis='y', length=0)
        ax.invert_yaxis()                    # el más vendido arriba
        ax.set_title("Productos más vendidos")
        ax.set_xlabel("Monto total ($)")
        ax.set_ylabel("")
        ax.grid(axis="x", linestyle="--", alpha=0.45, zorder=0)

        # Margen para texto a la derecha
        max_val = max(valores)
        ax.set_xlim(0, max_val * 1.18)
        ax.margins(y=0.12)
        ax.figure.subplots_adjust(left=0.10, right=0.96, top=0.88, bottom=0.18)

        # ------------------------------------------------------------------
        # 4) Colocar textos: Nombre dentro + Monto a la derecha (RP aplicado)
        # ------------------------------------------------------------------

        # Ocultar completamente eje Y (evita texto duplicado)
        ax.set_yticks([])

        # Ajustar color base de barra a Azul Power BI (T2)
        base_color = "#205493"
        for bar in bars:
            bar.set_color(base_color)

        # Nuevo umbral para decidir si nombre va dentro o fuera
        umbral_dentro = max_val * 0.17  # si la barra es muy corta → texto afuera

        blend_tx = transforms.blended_transform_factory(ax.transAxes, ax.transData)

        for bar, nombre_full, cant, val in zip(bars, productos, cantidades, valores):
            y_center = bar.get_y() + bar.get_height() / 2
            nombre = abreviar(nombre_full)
            ancho = bar.get_width()

            if ancho >= umbral_dentro:
                # ✅ Nombre dentro de la barra → blanco sin sombra (D)
                x_text = ancho * 0.02
                ax.text(
                    x_text, y_center, nombre,
                    va="center", ha="left",
                    fontsize=9, color="white", fontweight="normal",
                    zorder=4
                )
            else:
                # ✅ Nombre fuera → negro semi-bold (F2)
                x_text = ancho + (max_val * 0.01)
                ax.text(
                    x_text, y_center, nombre,
                    va="center", ha="left",
                    fontsize=9, color="#111", fontweight="bold",
                    zorder=4
                )

            # 💲 Monto alineado a la derecha (fuera de barra)
            ax.text(
                0.98, y_center, f"${val:,.0f}",
                transform=blend_tx, ha="right", va="center",
                fontsize=10, fontweight="bold", color="#222", zorder=4
            )

        # Usar draw_idle() para mejor rendimiento (D1)
        canvas.draw_idle()


        # ------------------------------------------------------------------
        # 5) Hover + Clic
        # ------------------------------------------------------------------

        # (A) Clic → abrir detalle
        def on_click(event):
            if event.inaxes != ax:
                return
            for bar, nombre_full, *_ in zip(bars, productos, cantidades, valores):
                contains, _ = bar.contains(event)
                if contains:
                    self.mostrar_detalle_producto(nombre_full)
                    break

        # Desconectar clic previo si existe
        if hasattr(self, "_cid_click_prod_m3"):
            try:
                canvas.mpl_disconnect(self._cid_click_prod_m3)
            except:
                pass

        # Conectar clic nuevo
        self._cid_click_prod_m3 = canvas.mpl_connect("button_press_event", on_click)


        # (B) Hover con glow suave (BR2 + G1)
        self._hover_bar = None  # barra actualmente iluminada

        def on_hover(event):
            if event.inaxes != ax:
                if self._hover_bar is not None:
                    self._hover_bar.set_edgecolor("none")
                    self._hover_bar.set_linewidth(0)
                    self._hover_bar = None
                    canvas.draw_idle()
                return

            for bar in bars:
                contains, _ = bar.contains(event)
                if contains:
                    if self._hover_bar is bar:
                        return
                    # Restaurar barra previa
                    if self._hover_bar is not None:
                        self._hover_bar.set_edgecolor("none")
                        self._hover_bar.set_linewidth(0)
                    # Aplicar glow suave G1
                    bar.set_edgecolor(hover_color)
                    bar.set_linewidth(2)
                    self._hover_bar = bar
                    canvas.draw_idle()
                    return

            # Si no pasa por ninguna barra
            if self._hover_bar is not None:
                self._hover_bar.set_edgecolor("none")
                self._hover_bar.set_linewidth(0)
                self._hover_bar = None
                canvas.draw_idle()

        canvas.mpl_connect("motion_notify_event", on_hover)

        # ------------------------------------------------------------------
        canvas.draw()
        
    def aplicar_hover_resaltado(self, canvas, ax, bars, base_color="#0078D7", hover_color="#1A91FF"):
        """
        Aplica resaltado de barra (hover) tipo M3 a cualquier gráfico de barras horizontal.
        - base_color: color normal de las barras
        - hover_color: tono al colocar el cursor encima (HL1)
        """
        # Desconectar hover previo si existiera
        if hasattr(self, "_cid_motion_generic_hover"):
            try:
                canvas.mpl_disconnect(self._cid_motion_generic_hover)
            except Exception:
                pass

        self._hover_bar_generic = None  # Barra actualmente resaltada

        def _on_motion_generic(event):
            if event.inaxes != ax:
                if self._hover_bar_generic is not None:
                    self._hover_bar_generic.set_facecolor(base_color)
                    self._hover_bar_generic = None
                    canvas.draw_idle()
                return

            for bar in bars:
                contains, _ = bar.contains(event)
                if contains:
                    if self._hover_bar_generic is bar:
                        return
                    if self._hover_bar_generic is not None:
                        self._hover_bar_generic.set_facecolor(base_color)
                    bar.set_facecolor(hover_color)
                    self._hover_bar_generic = bar
                    canvas.draw_idle()
                    return

            if self._hover_bar_generic is not None:
                self._hover_bar_generic.set_facecolor(base_color)
                self._hover_bar_generic = None
                canvas.draw_idle()

        self._cid_motion_generic_hover = canvas.mpl_connect("motion_notify_event", _on_motion_generic)
    def _init_tooltip(self):
        """
        Crea un QLabel flotante sobre el canvas para usarlo como tooltip moderno.
        Se usa una sola instancia para todos los gráficos.
        """
        from PyQt5.QtWidgets import QLabel
        from PyQt5.QtCore import Qt

        if hasattr(self, "_tooltip"):
            return  # evitar duplicado

        self._tooltip = QLabel(self)
        self._tooltip.setStyleSheet("""
            background-color: rgba(10, 116, 215, 210);   /* Azul corporativo con transparencia */
            color: white;
            padding: 6px 10px;
            border-radius: 6px;
            font-size: 9pt;
        """)
        self._tooltip.setWindowFlags(Qt.ToolTip)
        self._tooltip.hide()



    # ------------------------------------------------------------
    # 🥧 Hover en el Pie Chart “Concentrado por empresa”
    # ------------------------------------------------------------
    def _enable_tooltips_pie(self):
        """
        Activa hover en el gráfico de pastel de empresas.
        Usa _pie_empresa_data = [(wedge, nombre, monto), ...]
        Tooltip formato PIE2: Empresa + % + monto
        """
        canvas = self.widgets[3][0]
        ax = self.widgets[3][1]

        # Desconectar hover previo si existe
        if hasattr(self, "_cid_motion_pie"):
            canvas.mpl_disconnect(self._cid_motion_pie)

        total = sum(v for _, _, v in self._pie_empresa_data)

        def on_motion(event):
            if event.inaxes != ax:
                self._tooltip.hide()
                return

            for wedge, nombre, monto in self._pie_empresa_data:
                contains, _ = wedge.contains(event)
                if contains:
                    porcentaje = (monto / total * 100) if total else 0
                    texto = f"{nombre}\n{porcentaje:.1f}%  ·  ${monto:,.0f}"
                    self._tooltip.setText(texto)

                    # posicionar tooltip cerca del mouse
                    self._tooltip.move(event.guiEvent.globalX() + 12, event.guiEvent.globalY() + 12)
                    self._tooltip.show()
                    return

            self._tooltip.hide()

        self._cid_motion_pie = canvas.mpl_connect("motion_notify_event", on_motion)

    
    def _on_click_bar_productos(self, event):
        """Detecta clic sobre una barra y abre el detalle del producto."""
        if not hasattr(self, "_bar_producto_map"):
            return
        if event.inaxes is None:
            return
        for bar, nombre_full in self._bar_producto_map:
            contains, _ = bar.contains(event)
            if contains:
                self.mostrar_detalle_producto(nombre_full)
                break

    def mostrar_detalle_producto(self, nombre_producto):
        """
        Versión unificada CT5+DP con ajuste de precios reales por cliente.
        Muestra KPIs, gráfico top clientes y tabla de facturas con montos netos (descuentos incluidos).
        """
        import pandas as pd
        import numpy as np
        from PyQt5.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QTableWidget, QTableWidgetItem, QSizePolicy, QMessageBox, QHeaderView
        )
        from PyQt5.QtCore import Qt
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

        # === Datos base ===
        df_fac = self.ventana_mio.obtener_dataframe_facturas()
        df_prod = self.ventana_mio.obtener_productos_facturados()
        if df_fac.empty or df_prod.empty:
            QMessageBox.information(self, "Detalle del producto", "No hay datos disponibles.")
            return

        # === Filtros heredados (si hay combo de empresa) ===
        empresa_sel = self.combo_empresa.currentText() if hasattr(self, "combo_empresa") else "Todas"
        dff = df_fac.copy()
        if empresa_sel and empresa_sel != "Todas":
            dff = dff[dff["empresa"] == empresa_sel]

        # === Normalizar coincidencia del producto ===
        nombre_producto = str(nombre_producto).strip().lower()
        df_prod["producto"] = df_prod["producto"].astype(str).str.strip().str.lower()

        dfp = df_prod[df_prod["producto"].str.contains(nombre_producto, na=False, regex=False)].copy()
        if dfp.empty:
            QMessageBox.information(self, "Detalle del producto", f"No se encontraron datos del producto '{nombre_producto}'.")
            print("🚫 df_prod vacío — ejemplos disponibles:", df_prod["producto"].dropna().head(10).tolist())
            return

        # === Unir facturas con productos (siempre 'inner' para coherencia) ===
        try:
            df_merge = pd.merge(
                dfp,
                dff[["factura", "tienda", "empresa", "total"]],
                on="factura",
                how="inner"
            )
            print(f"✅ Merge realizado correctamente ({len(df_merge)} filas).")
        except Exception as e:
            print("❌ Error en merge:", e)
            df_merge = pd.DataFrame()

        if df_merge.empty:
            QMessageBox.information(self, "Detalle del producto", f"No hay facturas con el producto '{nombre_producto}' en los filtros actuales.")
            print("⚠️ df_merge vacío — revisa si df_prod o dff vienen sin datos.")
            return

        # === AJUSTE CORRECTO DE MONTOS (proporcional por factura con TODAS sus líneas) ===

        # 1) Asegurar numéricos en df_merge (líneas del producto seleccionado)
        df_merge["cantidad"] = pd.to_numeric(df_merge["cantidad"], errors="coerce").fillna(0)
        df_merge["precio"]   = pd.to_numeric(df_merge["precio"],   errors="coerce").fillna(0)
        df_merge["monto_bruto"] = df_merge["cantidad"] * df_merge["precio"]

        # 2) Construir el bruto total por factura usando TODAS las líneas de productos (no solo el producto filtrado)
        #    – Filtramos df_prod a las facturas visibles en dff (empresa/filtros actuales)
        df_all = pd.merge(
            df_prod[["factura", "cantidad", "precio"]],
            dff[["factura", "total"]],
            on="factura",
            how="inner"
        ).copy()

        df_all["cantidad"] = pd.to_numeric(df_all["cantidad"], errors="coerce").fillna(0)
        df_all["precio"]   = pd.to_numeric(df_all["precio"],   errors="coerce").fillna(0)
        df_all["bruto_linea"] = df_all["cantidad"] * df_all["precio"]

        # Mapa: factura -> suma bruto de TODAS sus líneas
        bruto_por_factura = df_all.groupby("factura")["bruto_linea"].sum().to_dict()

        # 3) Mapa del total neto (con descuento) por factura desde dff
        totales_por_factura = pd.to_numeric(dff.set_index("factura")["total"], errors="coerce").to_dict()

        # 4) Traer ambos a df_merge y calcular factor y monto_real
        df_merge["total_bruto_factura"] = df_merge["factura"].map(bruto_por_factura)
        df_merge["total_factura_neto"]  = df_merge["factura"].map(totales_por_factura)

        # Evitar división por cero / nulos
        df_merge["factor_descuento_cliente"] = (
            df_merge["total_factura_neto"] / df_merge["total_bruto_factura"]
        ).replace([float("inf"), -float("inf")], 0).fillna(0)

        # Monto neto real de la línea del producto
        df_merge["monto_real"] = df_merge["monto_bruto"] * df_merge["factor_descuento_cliente"]

        # --- Limpieza post-merge (crear columna 'tienda' si hiciera falta) ---
        if "tienda_y" in df_merge.columns:
            df_merge["tienda"] = df_merge["tienda_y"]
        elif "tienda_x" in df_merge.columns:
            df_merge["tienda"] = df_merge["tienda_x"]
        elif "cliente" in df_merge.columns:
            df_merge["tienda"] = df_merge["cliente"]
        else:
            df_merge["tienda"] = "Desconocido"

        for _c in ["tienda_x", "tienda_y"]:
            if _c in df_merge.columns:
                df_merge.drop(columns=_c, inplace=True, errors="ignore")

        # DEBUG opcional
        print("🧾 Debug (producto):")
        print(df_merge[["factura", "tienda", "monto_bruto", "total_bruto_factura",
                        "total_factura_neto", "factor_descuento_cliente", "monto_real"]].head(10))
        print("👉 Suma monto_real (producto):", df_merge["monto_real"].sum())

        print("💰 Ajuste aplicado — primeros registros:")
        print(df_merge[["factura", "tienda", "monto_bruto", "monto_real", "factor_descuento_cliente"]].head(5))

        # === Calcular métricas con montos reales ===
        total_ventas = df_merge["monto_real"].sum()
        n_facturas = df_merge["factura"].nunique()
        piezas_totales = int(df_merge["piezas"].sum())
        ticket_prom = total_ventas / n_facturas if n_facturas else 0.0

        # === Agrupar por cliente ===
        top_clientes = (
            df_merge.groupby("tienda")[["cantidad", "monto_real"]]
            .sum()
            .query("monto_real > 0")
            .sort_values("monto_real", ascending=True)
            .tail(8)
        )

        # === Crear diálogo ===
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Producto: {nombre_producto}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        # === KPIs ===
        kpi_row = QHBoxLayout()
        def _k(label, value):
            w = QLabel(f"<div style='text-align:center'><div style='font-size:10pt;color:#444'>{label}</div><div style='font-size:14pt;color:#0A74D7'><b>{value}</b></div></div>")
            w.setAlignment(Qt.AlignCenter)
            return w
        kpi_row.addWidget(_k("Ventas Totales", f"${total_ventas:,.2f}"))
        kpi_row.addWidget(_k("Facturas", f"{n_facturas}"))
        kpi_row.addWidget(_k("Piezas Vendidas", f"{piezas_totales}"))
        kpi_row.addWidget(_k("Ticket Promedio", f"${ticket_prom:,.2f}"))
        lay.addLayout(kpi_row)

        # === Gráfico horizontal (Top clientes) ===
        fig, ax = plt.subplots(figsize=(13, 3.8))
        canvas = FigureCanvas(fig)
        canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        canvas.setMinimumHeight(340)

        ax.set_title("Clientes principales del producto (monto real)", fontsize=11, pad=12)
        ax.set_xlabel("Monto total ($)")
        ax.grid(axis="x", linestyle="--", alpha=0.35, zorder=0)
        ax.set_yticks([])
        ax.invert_yaxis()
        fig.subplots_adjust(left=0.03, right=0.99, top=0.90, bottom=0.18)

        if not top_clientes.empty:
            bars = ax.barh(top_clientes.index, top_clientes["monto_real"], color="#205493", zorder=3)
            for bar, cliente, val in zip(bars, top_clientes.index, top_clientes["monto_real"]):
                y_center = bar.get_y() + bar.get_height() / 2
                ancho = bar.get_width()
                ax.text(ancho * 0.02, y_center, str(cliente),
                        va="center", ha="left", fontsize=9, color="white")
                ax.text(ancho * 0.985, y_center, f"${val:,.0f}",
                        va="center", ha="right", fontsize=10, fontweight="bold", color="white")
        else:
            ax.text(0.5, 0.5, "Sin datos para mostrar", ha="center", va="center", fontsize=11)
            ax.axis("off")

        lay.addWidget(canvas)

        # === Agrupar por factura con montos reales ===
        df_facturas = (
            df_merge.groupby(["factura", "tienda", "empresa"], as_index=False)
            .agg(piezas=("piezas", "sum"), total_real=("monto_real", "sum"))
        )
        print("🧾 Facturas agrupadas:\n", df_facturas.head())

        # === Crear tabla ===
        tabla = QTableWidget()
        columnas = ["Factura", "Cliente", "Empresa", "Piezas", "Total Real"]
        tabla.setColumnCount(len(columnas))
        tabla.setHorizontalHeaderLabels(columnas)
        tabla.setRowCount(len(df_facturas))

        for i, row in enumerate(df_facturas.itertuples(index=False)):
            tabla.setItem(i, 0, QTableWidgetItem(str(row.factura)))
            tabla.setItem(i, 1, QTableWidgetItem(str(row.tienda)))
            tabla.setItem(i, 2, QTableWidgetItem(str(row.empresa)))
            tabla.setItem(i, 3, QTableWidgetItem(f"{int(row.piezas)}"))
            tabla.setItem(i, 4, QTableWidgetItem(f"${float(row.total_real):,.2f}"))

        header = tabla.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        lay.addWidget(tabla, stretch=1)

        # === Botones inferiores ===
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_pdf = QPushButton(" Exportar PDF")
        btn_xlsx = QPushButton(" Exportar Excel")
        btn_close = QPushButton(" Cerrar")
        self._style_action_buttons(btn_pdf, btn_xlsx, btn_close)
        btn_row.addWidget(btn_pdf)
        btn_row.addSpacing(8)
        btn_row.addWidget(btn_xlsx)
        btn_row.addSpacing(8)
        btn_row.addWidget(btn_close)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        # ==== Exportar PDF (ReportLab: gráfico + KPIs + tabla de facturas) ====
        def _export_pdf():
            from PyQt5.QtWidgets import QFileDialog, QMessageBox
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
            from reportlab.lib import colors
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            import tempfile, os

            path, _ = QFileDialog.getSaveFileName(
                dlg, "Guardar PDF",
                f"Producto_{nombre_producto}.pdf", "PDF (*.pdf)"
            )
            if not path:
                return

            try:
                # --- Guardar gráfico temporal ---
                tmpdir = tempfile.gettempdir()
                chart_path = os.path.join(tmpdir, "chart_producto.png")
                fig.savefig(chart_path, dpi=160, bbox_inches="tight")

                # --- Crear documento PDF ---
                doc = SimpleDocTemplate(
                    path, pagesize=A4,
                    rightMargin=36, leftMargin=36,
                    topMargin=36, bottomMargin=36
                )
                styles = getSampleStyleSheet()
                style_center = ParagraphStyle("center", parent=styles["Normal"], alignment=1)
                flow = []

                # === ENCABEZADO ===
                flow.append(Paragraph(f"<b>Detalle del Producto</b>", styles["Title"]))
                flow.append(Paragraph(f"{nombre_producto.upper()}<br/><b>Empresa:</b> {empresa_sel}", style_center))
                flow.append(Spacer(1, 12))

                # === KPIs ===
                resumen_text = f"""
                    <b>Ventas Totales:</b> ${total_ventas:,.2f}<br/>
                    <b>Facturas:</b> {n_facturas}<br/>
                    <b>Piezas Vendidas:</b> {piezas_totales}<br/>
                    <b>Ticket Promedio:</b> ${ticket_prom:,.2f}
                """
                flow.append(Paragraph(resumen_text, styles["Normal"]))
                flow.append(Spacer(1, 12))

                # === GRÁFICO ===
                if os.path.exists(chart_path):
                    flow.append(Image(chart_path, width=500, height=200))
                    flow.append(Spacer(1, 16))

                # === TABLA DETALLE ===
                data = [["Factura", "Cliente", "Empresa", "Piezas", "Total Real"]]
                for _, row in df_facturas.iterrows():
                    data.append([
                        str(row.get("factura", "")),
                        str(row.get("tienda", "")),
                        str(row.get("empresa", "")),
                        str(int(row.get("piezas", 0))),
                        f"${float(row.get('total_real', 0.0)):,.2f}"
                    ])

                table = Table(data, colWidths=[70, 160, 80, 60, 80])
                table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#205493")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.whitesmoke),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ]))
                flow.append(table)

                # === Generar PDF ===
                doc.build(flow)
                QMessageBox.information(dlg, "Exportación", "✅ PDF generado correctamente.")

            except Exception as e:
                QMessageBox.warning(dlg, "Exportación", f"No se pudo exportar:\n{e}")


        # ==== Exportar Excel (XLSX: Datos/Gráfico/Resumen) ====
        def _export_xlsx():
            from PyQt5.QtWidgets import QFileDialog, QMessageBox
            import tempfile, os
            import pandas as pd

            path, _ = QFileDialog.getSaveFileName(
                dlg, "Guardar Excel",
                f"Producto_{nombre_producto}.xlsx", "Excel (*.xlsx)"
            )
            if not path:
                return

            try:
                # --- Guardar gráfico temporal ---
                tmpdir = tempfile.gettempdir()
                chart_path = os.path.join(tmpdir, "chart_producto.png")
                fig.savefig(chart_path, dpi=160, bbox_inches="tight")

                with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
                    workbook = writer.book
                    ws = workbook.add_worksheet("Detalle Producto")

                    # === FORMATOS ===
                    fmt_header = workbook.add_format({
                        "bold": True, "bg_color": "#205493", "font_color": "white",
                        "align": "center", "valign": "vcenter", "border": 1
                    })
                    fmt_label = workbook.add_format({"bold": True, "align": "left", "valign": "vcenter"})
                    fmt_value = workbook.add_format({"align": "center", "valign": "vcenter"})
                    fmt_center = workbook.add_format({"align": "center", "valign": "vcenter", "border": 1})
                    fmt_left = workbook.add_format({"text_wrap": True, "valign": "top", "border": 1})
                    fmt_title = workbook.add_format({"bold": True, "font_size": 14, "align": "center", "valign": "vcenter"})

                    # === ENCABEZADO ===
                    ws.merge_range("A1:B1", "Detalle del Producto", fmt_title)
                    ws.write("A2", "Producto", fmt_label)
                    ws.write("B2", nombre_producto, fmt_value)
                    ws.write("A3", "Empresa", fmt_label)
                    ws.write("B3", empresa_sel, fmt_value)

                    # === KPIs ===
                    ws.write("A5", "Ventas Totales", fmt_label)
                    ws.write("B5", f"${total_ventas:,.2f}", fmt_value)
                    ws.write("A6", "Facturas", fmt_label)
                    ws.write("B6", n_facturas, fmt_value)
                    ws.write("A7", "Piezas Vendidas", fmt_label)
                    ws.write("B7", piezas_totales, fmt_value)
                    ws.write("A8", "Ticket Promedio", fmt_label)
                    ws.write("B8", f"${ticket_prom:,.2f}", fmt_value)

                    # === GRÁFICO ===
                    ws.insert_image("A10", chart_path, {"x_scale": 1.0, "y_scale": 1.0})
                    chart_end = 34  # posición de inicio de la tabla

                    # === TABLA DETALLE ===
                    columnas = ["Factura", "Cliente", "Empresa", "Piezas", "Total Real"]
                    ws.write_row(chart_end, 0, columnas, fmt_header)

                    for r_idx, row in enumerate(df_facturas.itertuples(index=False), start=chart_end + 1):
                        ws.write(r_idx, 0, row.factura, fmt_center)
                        ws.write(r_idx, 1, row.tienda, fmt_left)
                        ws.write(r_idx, 2, row.empresa, fmt_center)
                        ws.write(r_idx, 3, int(row.piezas), fmt_center)
                        ws.write(r_idx, 4, f"${float(row.total_real):,.2f}", fmt_center)

                    # === Ajustar anchos ===
                    ws.set_column("A:A", 12)
                    ws.set_column("B:B", 35)
                    ws.set_column("C:C", 18)
                    ws.set_column("D:D", 10)
                    ws.set_column("E:E", 14)

                QMessageBox.information(dlg, "Exportación", "✅ Excel generado correctamente.")

            except Exception as e:
                QMessageBox.warning(dlg, "Exportación", f"No se pudo exportar:\n{e}")

        btn_pdf.clicked.connect(_export_pdf)
        btn_xlsx.clicked.connect(_export_xlsx)
        btn_close.clicked.connect(dlg.close)

        self._center_on_parent(dlg, 900, 760)
        dlg.exec_()



    def mostrar_detalle_cliente(self, nombre_cliente):
        import pandas as pd
        from PyQt5.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QTableWidget, QTableWidgetItem, QSizePolicy
        )
        from PyQt5.QtCore import Qt

        # ✅ Usar el mismo DF filtrado que los gráficos
        if hasattr(self, "_df_facturas_filtrado") and not self._df_facturas_filtrado.empty:
            df_fac = self._df_facturas_filtrado.copy()
        else:
            df_fac = self.ventana_mio.obtener_dataframe_facturas()

        df_prod = self.ventana_mio.obtener_productos_facturados()

        if df_fac.empty or df_prod.empty:
            QMessageBox.information(self, "Detalle del cliente", "No hay datos disponibles.")
            return

        # === Filtros heredados ===
        empresa_sel = self.combo_empresa.currentText() if hasattr(self, "combo_empresa") else "Todas"
        dff = df_fac.copy()
        if empresa_sel and empresa_sel != "Todas":
            dff = dff[dff["empresa"] == empresa_sel]

        dff = dff[dff["tienda"].astype(str).str.lower() == str(nombre_cliente).lower()]

        if dff.empty:
            QMessageBox.information(
                self, "Detalle del cliente",
                "No hay facturas para el cliente con los filtros actuales."
            )
            return

        # === Unir facturas con productos ===
        try:
            df_merge = pd.merge(
                df_prod,
                dff[["factura", "tienda", "empresa", "total"]],
                on="factura",
                how="inner"
            )
            print(f"✅ Merge realizado correctamente ({len(df_merge)} filas).")
        except Exception as e:
            print("❌ Error en merge:", e)
            df_merge = pd.DataFrame()

        # === Verificar resultado del merge ===
        if df_merge.empty:
            print("⚠️ df_merge vacío — revisa si df_prod o dff vienen sin datos.")
            print(f"📋 df_prod columnas: {list(df_prod.columns)} | filas: {len(df_prod)}")
            print(f"📋 dff columnas: {list(dff.columns)} | filas: {len(dff)}")
            QMessageBox.information(self, "Detalle del cliente", "No hay productos en las facturas filtradas.")
            return

        print("🧭 Columnas disponibles en df_merge:", df_merge.columns.tolist())
        print(df_merge.head(3))

        
        # === Limpieza post-merge ===
        # Preferir siempre la tienda de facturas (tienda_y) sobre la del producto (tienda_x)
        if "tienda_y" in df_merge.columns:
            df_merge["tienda"] = df_merge["tienda_y"]
        elif "tienda_x" in df_merge.columns:
            df_merge["tienda"] = df_merge["tienda_x"]
        elif "cliente" in df_merge.columns:
            df_merge["tienda"] = df_merge["cliente"]
        else:
            df_merge["tienda"] = "Desconocido"

        # Eliminar columnas duplicadas si existen
        for col in ["tienda_x", "tienda_y"]:
            if col in df_merge.columns:
                df_merge.drop(columns=col, inplace=True)

        # === Calcular métricas ===
        df_merge["cantidad"] = pd.to_numeric(df_merge["cantidad"], errors="coerce").fillna(0)
        df_merge["precio"]   = pd.to_numeric(df_merge["precio"], errors="coerce").fillna(0)
        df_merge["monto"]    = df_merge["cantidad"] * df_merge["precio"]

        total_cliente  = float(dff["total"].astype(float).sum()) if "total" in dff.columns else float(df_merge["monto"].sum())
        n_facturas     = int(dff["factura"].nunique())
        ticket_prom    = (total_cliente / n_facturas) if n_facturas else 0.0

        # === Top productos ===
        top_prod = (
            df_merge.groupby("producto")[["cantidad", "monto"]]
            .sum()
            .query("monto > 0")
            .sort_values("monto", ascending=False)
            .head(8)
        )

        # --- Diálogo ---
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Cliente: {nombre_cliente}  ·  Empresa: {empresa_sel if empresa_sel!='Todas' else 'Todas'}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        # Encabezado con logo (si hay)
        logo_path = self._find_logo_path(empresa_sel if empresa_sel and empresa_sel!='Todas' else "")
        header = QHBoxLayout()
        lbl_title = QLabel(f"<b>Detalle del Cliente</b><br>{nombre_cliente}")
        lbl_title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.addWidget(lbl_title, 3)

        if logo_path:
            from PyQt5.QtGui import QPixmap
            logo = QLabel()
            pix = QPixmap(logo_path)
            if not pix.isNull():
                logo.setPixmap(pix.scaledToHeight(48, Qt.SmoothTransformation))
                logo.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                header.addWidget(logo, 1)
            else:
                header.addStretch(1)
        else:
            header.addStretch(1)

        lay.addLayout(header)

        # KPIs fila
        kpi = QHBoxLayout()
        def _k(label, value):
            w = QLabel(f"<div style='text-align:center'><div style='font-size:10pt;color:#444'>{label}</div><div style='font-size:14pt;color:#0A74D7'><b>{value}</b></div></div>")
            w.setAlignment(Qt.AlignCenter)
            return w
        kpi.addWidget(_k("Ventas Totales", f"${total_cliente:,.2f}"))
        kpi.addWidget(_k("Facturas", f"{n_facturas}"))
        kpi.addWidget(_k("Ticket Promedio", f"${ticket_prom:,.2f}"))
        lay.addLayout(kpi)

        # === Gráfico Top Productos (ancho total y texto blanco) ===
        fig, ax = plt.subplots(figsize=(13, 3.8))
        canvas = FigureCanvas(fig)
        canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        canvas.setMinimumHeight(340)

        top_prod = (
            df_merge.groupby("producto")[["cantidad", "monto"]]
            .sum()
            .query("monto > 0")
            .sort_values("monto", ascending=False)
            .head(8)
        )

        ax.set_title("Top productos por monto", fontsize=11, pad=12)
        ax.set_xlabel("Monto ($)")
        ax.grid(axis="x", linestyle="--", alpha=0.35, zorder=0)
        ax.set_yticks([])
        ax.invert_yaxis()

        fig.subplots_adjust(left=0.03, right=0.99, top=0.90, bottom=0.18)

        if not top_prod.empty:
            bars = ax.barh(top_prod.index[::-1], top_prod["monto"].iloc[::-1], color="#205493", zorder=3)
            max_val = top_prod["monto"].max()

            for bar, producto, val in zip(bars, top_prod.index[::-1], top_prod["monto"].iloc[::-1]):
                y_center = bar.get_y() + bar.get_height() / 2
                ancho = bar.get_width()
                umbral_dentro = max_val * 0.12

                ax.text(
                    ancho * 0.02, y_center, str(producto),
                    va="center", ha="left", fontsize=9,
                    color="white", fontweight="normal", zorder=4
                )
                ax.text(
                    ancho * 0.985, y_center, f"${val:,.0f}",
                    va="center", ha="right", fontsize=10,
                    fontweight="bold", color="white", zorder=4
                )
        else:
            ax.text(0.5, 0.5, "Sin productos", ha="center", va="center", fontsize=11)
            ax.axis("off")

        lay.addWidget(canvas)



        # === Tabla de facturas (Cliente Top: CT5) ===
        # Filtrar facturas del cliente seleccionado (desde df_merge)
        df_cliente = df_merge[df_merge["tienda"].str.lower() == nombre_cliente.lower()].copy()

        # 🔹 Agrupar por factura (para evitar duplicados de productos)
        df_cliente = (
            df_cliente.groupby(["factura", "tienda", "empresa"], as_index=False)
            .agg({"piezas": "sum", "total": "max"})
        )


        tabla = QTableWidget()
        columnas = ["Factura", "Cliente", "Empresa", "Piezas", "Total"]
        tabla.setColumnCount(len(columnas))
        tabla.setHorizontalHeaderLabels(columnas)
        tabla.setRowCount(len(df_cliente))
         
        print("🧩 df_merge columnas:", df_merge.columns.tolist())
        print("🧾 Primeras filas:\n", df_merge.head())
        # Rellenar filas con el DataFrame ya unido y normalizado
        for i, row in enumerate(df_merge.itertuples()):
            factura = getattr(row, "factura", "")
            tienda = getattr(row, "tienda", "Desconocido")
            empresa = getattr(row, "empresa", "")
            piezas = getattr(row, "piezas", 0)
            total = getattr(row, "total", 0.0)

            tabla.setItem(i, 0, QTableWidgetItem(str(factura)))
            tabla.setItem(i, 1, QTableWidgetItem(str(tienda)))
            tabla.setItem(i, 2, QTableWidgetItem(str(empresa)))
            tabla.setItem(i, 3, QTableWidgetItem(f"{int(piezas)}"))
            tabla.setItem(i, 4, QTableWidgetItem(f"${float(total):,.2f}"))


        # === Ajuste de columnas ===
        header = tabla.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Factura
        header.setSectionResizeMode(1, QHeaderView.Stretch)           # Cliente (expandible)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Empresa
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # Piezas
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Total

        # === Centrar piezas y total ===
        for col in [3, 4]:
            for r in range(tabla.rowCount()):
                item = tabla.item(r, col)
                if item:
                    item.setTextAlignment(Qt.AlignCenter)

        # === Estilo unificado tipo dashboard ===
        tabla.setAlternatingRowColors(True)
        tabla.setStyleSheet("""
            QTableWidget {
                gridline-color: #d1d5db;
                border: 1px solid #cbd5e1;
                font-size: 10pt;
            }
            QHeaderView::section {
                background-color: #f3f4f6;
                font-weight: bold;
                border: none;
                padding: 4px;
            }
            QTableWidget::item {
                padding: 2px 6px;
            }
        """)
        tabla.verticalHeader().setVisible(False)
        tabla.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay.addWidget(tabla, stretch=1)

        # Botones centrados (PDF, Excel, Cerrar)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_pdf  = QPushButton(" Exportar PDF")
        btn_xlsx = QPushButton(" Exportar Excel")
        btn_close = QPushButton(" Cerrar")
        self._style_action_buttons(btn_pdf, btn_xlsx, btn_close)
        btn_row.addWidget(btn_pdf)
        btn_row.addSpacing(8)
        btn_row.addWidget(btn_xlsx)
        btn_row.addSpacing(8)
        btn_row.addWidget(btn_close)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)


        # ==== Exportar PDF (ReportLab: gráfico + KPIs + tabla de facturas) ====
        def _export_pdf():
            from PyQt5.QtWidgets import QFileDialog, QMessageBox
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
            from reportlab.lib import colors
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            import tempfile, os

            path, _ = QFileDialog.getSaveFileName(
                dlg, "Guardar PDF",
                f"Detalle_{nombre_cliente}.pdf", "PDF (*.pdf)"
            )
            if not path:
                return

            try:
                # --- Guardar gráfico ---
                tmpdir = tempfile.gettempdir()
                chart_path = os.path.join(tmpdir, "chart_ct5.png")
                fig.savefig(chart_path, dpi=160, bbox_inches="tight")

                # --- Documento PDF ---
                doc = SimpleDocTemplate(
                    path, pagesize=A4,
                    rightMargin=36, leftMargin=36,
                    topMargin=36, bottomMargin=36
                )
                styles = getSampleStyleSheet()
                style_center = ParagraphStyle("center", parent=styles["Normal"], alignment=1)
                flow = []

                # === ENCABEZADO ===
                flow.append(Paragraph(f"<b>Detalle del Cliente</b>", styles["Title"]))
                flow.append(Paragraph(f"{nombre_cliente} · Empresa: {empresa_sel}", style_center))
                flow.append(Spacer(1, 12))

                # === KPIs ===
                resumen_text = f"""
                    <b>Ventas Totales:</b> ${total_cliente:,.2f}<br/>
                    <b>Facturas:</b> {n_facturas}<br/>
                    <b>Ticket Promedio:</b> ${ticket_prom:,.2f}
                """
                flow.append(Paragraph(resumen_text, styles["Normal"]))
                flow.append(Spacer(1, 12))

                # === GRÁFICO ===
                if os.path.exists(chart_path):
                    flow.append(Image(chart_path, width=500, height=200))
                    flow.append(Spacer(1, 16))

                # === TABLA FACTURAS ===
                data = [["Factura", "Cliente", "Empresa", "Piezas", "Total"]]
                for _, row in df_cliente.iterrows():
                    data.append([
                        str(row["factura"]),
                        str(row["tienda"]),
                        str(row["empresa"]),
                        str(int(row["piezas"])),
                        f"${row['total']:,.2f}"
                    ])

                table = Table(data, colWidths=[70, 170, 80, 60, 80])
                table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0A74D7")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.whitesmoke),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ]))
                flow.append(table)

                doc.build(flow)
                QMessageBox.information(dlg, "Exportación", "✅ PDF generado correctamente en una sola hoja.")
            except Exception as e:
                QMessageBox.warning(dlg, "Exportación", f"No se pudo exportar:\n{e}")





        # ==== Exportar Excel (XLS3: Datos/Gráfico/Resumen) ====
        def _export_xlsx():
            from PyQt5.QtWidgets import QFileDialog, QMessageBox
            import tempfile, os
            import pandas as pd

            path, _ = QFileDialog.getSaveFileName(
                dlg, "Guardar Excel",
                f"Detalle_{nombre_cliente}.xlsx", "Excel (*.xlsx)"
            )
            if not path:
                return

            try:
                # --- Guardar gráfico ---
                tmpdir = tempfile.gettempdir()
                chart_path = os.path.join(tmpdir, "chart_ct5.png")
                fig.savefig(chart_path, dpi=160, bbox_inches="tight")

                with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
                    workbook = writer.book
                    ws = workbook.add_worksheet("Detalle Cliente")

                    # === FORMATOS ===
                    fmt_header = workbook.add_format({
                        "bold": True, "bg_color": "#0A74D7", "font_color": "white",
                        "align": "center", "valign": "vcenter", "border": 1
                    })
                    fmt_label = workbook.add_format({
                        "bold": True, "align": "left", "valign": "vcenter"
                    })
                    fmt_value = workbook.add_format({
                        "align": "center", "valign": "vcenter"
                    })
                    fmt_center = workbook.add_format({
                        "align": "center", "valign": "vcenter", "border": 1
                    })
                    fmt_left = workbook.add_format({
                        "text_wrap": True, "valign": "top", "border": 1
                    })
                    fmt_total = workbook.add_format({
                        "bold": True, "align": "center", "bg_color": "#e6e6e6", "border": 1
                    })
                    fmt_title = workbook.add_format({
                        "bold": True, "font_size": 14, "align": "center", "valign": "vcenter"
                    })

                    # === ENCABEZADO ===
                    ws.merge_range("A1:B1", f"Detalle del Cliente", fmt_title)
                    ws.write("A2", "Cliente", fmt_label)
                    ws.write("B2", nombre_cliente, fmt_value)
                    ws.write("A3", "Empresa", fmt_label)
                    ws.write("B3", empresa_sel, fmt_value)

                    # === KPIs ===
                    ws.write("A5", "Ventas Totales", fmt_label)
                    ws.write("B5", f"${total_cliente:,.2f}", fmt_value)
                    ws.write("A6", "Facturas", fmt_label)
                    ws.write("B6", n_facturas, fmt_value)
                    ws.write("A7", "Ticket Promedio", fmt_label)
                    ws.write("B7", f"${ticket_prom:,.2f}", fmt_value)

                    # === GRÁFICO ===
                    ws.insert_image("A9", chart_path, {"x_scale": 1.0, "y_scale": 1.0})
                    chart_end = 32  # posición de inicio para tabla

                    # === TABLA DE FACTURAS ===
                    columnas = ["Factura", "Cliente", "Empresa", "Piezas", "Total"]
                    ws.write_row(chart_end, 0, columnas, fmt_header)

                    for r_idx, row in enumerate(df_cliente.itertuples(index=False), start=chart_end + 1):
                        ws.write(r_idx, 0, row.factura, fmt_center)
                        ws.write(r_idx, 1, row.tienda, fmt_left)
                        ws.write(r_idx, 2, row.empresa, fmt_center)
                        ws.write(r_idx, 3, int(row.piezas), fmt_center)
                        ws.write(r_idx, 4, f"${row.total:,.2f}", fmt_center)

                    # Ajustar anchos
                    ws.set_column("A:A", 12)
                    ws.set_column("B:B", 35)
                    ws.set_column("C:C", 18)
                    ws.set_column("D:D", 10)
                    ws.set_column("E:E", 14)

                QMessageBox.information(
                    dlg, "Exportación",
                    "✅ Excel generado correctamente con todo en una sola hoja."
                )

            except Exception as e:
                QMessageBox.warning(
                    dlg, "Exportación",
                    f"No se pudo exportar:\n{e}"
                )

        btn_pdf.clicked.connect(_export_pdf)
        btn_xlsx.clicked.connect(_export_xlsx)
        btn_close.clicked.connect(dlg.close)
        dlg.resize(900, 760)
        dlg.exec_()

    
    def mostrar_detalle_frecuencia(self, compras_min, compras_max):
        """
        FR2 corregido:
        - Usa df_facturas ya filtrado (self._df_facturas_filtrado si existe)
        - Normaliza 'tienda' tras el merge para evitar KeyError
        - Calcula montos reales por línea (descuento proporcional por factura)
        - Muestra Top productos por monto real y tabla con los 5 productos más recomprados por cliente
        """
        import pandas as pd
        from PyQt5.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QTableWidget, QTableWidgetItem, QSizePolicy, QMessageBox, QHeaderView
        )
        from PyQt5.QtCore import Qt
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

        # --- Datos base ---
        df_prod = self.ventana_mio.obtener_productos_facturados()

        # ✅ Usar df_facturas filtrado si existe (guardado desde generar_reportes)
        if hasattr(self, "_df_facturas_filtrado") and not self._df_facturas_filtrado.empty:
            df_fac = self._df_facturas_filtrado.copy()
            print("✅ Usando df_facturas filtrado (desde frecuencia)")
        else:
            df_fac = self.ventana_mio.obtener_dataframe_facturas()
            if hasattr(self, "_aplicar_filtros_generales"):
                df_fac = self._aplicar_filtros_generales(df_fac)
            print("⚠️ df_facturas_filtrado no disponible, usando fuente directa.")

        if df_fac.empty or df_prod.empty:
            QMessageBox.information(self, "Detalle frecuencia", "No hay datos disponibles después de aplicar filtros.")
            return

        empresa_sel = self.combo_empresa.currentText() if hasattr(self, "combo_empresa") else "Todas"

        # --- Agrupar clientes por número de compras ---
        if "tienda" not in df_fac.columns or "factura" not in df_fac.columns:
            QMessageBox.information(self, "Detalle frecuencia", "No se encontraron columnas 'tienda' o 'factura'.")
            return

        compras_por_cliente_full = df_fac.groupby("tienda")["factura"].count()
        sel_clientes = compras_por_cliente_full[
            (compras_por_cliente_full >= compras_min) & (compras_por_cliente_full <= compras_max)
        ].index.tolist()

        print(f"🟢 Clic detectado en rango {compras_min}–{compras_max}")
        print(f"👥 Clientes en rango: {len(sel_clientes)} → {sel_clientes[:5]}")

        if not sel_clientes:
            QMessageBox.information(self, "Detalle frecuencia", "No hay clientes en este rango de recompras.")
            return

        # Solo facturas de los clientes seleccionados
        df_fac_sel = df_fac[df_fac["tienda"].isin(sel_clientes)].copy()
        if df_fac_sel.empty:
            QMessageBox.information(self, "Detalle frecuencia", "No hay facturas de estos clientes.")
            return

        # --- Unir productos con facturas seleccionadas ---
        df_merge = pd.merge(
            df_prod,
            df_fac_sel[["factura", "tienda", "empresa", "total"]],
            on="factura", how="inner"
        )

        if df_merge.empty:
            QMessageBox.information(self, "Detalle frecuencia", "Sin productos asociados a este grupo.")
            return

        # === Normalizar columna 'tienda' (por si vino como tienda_x/tienda_y/cliente) ===
        if "tienda" not in df_merge.columns:
            for col in ["tienda_y", "tienda_x", "cliente", "consignatario", "nombre"]:
                if col in df_merge.columns:
                    df_merge["tienda"] = df_merge[col]
                    print(f"⚙️ Columna 'tienda' creada a partir de '{col}'")
                    break
            else:
                df_merge["tienda"] = "Desconocido"
        for col in ["tienda_x", "tienda_y"]:
            if col in df_merge.columns:
                df_merge.drop(columns=col, inplace=True, errors="ignore")
        df_merge["tienda"] = df_merge["tienda"].astype(str).fillna("Desconocido")

        # === AJUSTE CORRECTO DE MONTOS (proporcional por factura con TODAS sus líneas) ===
        # Líneas del merge (solo facturas/clients del rango)
        df_merge["cantidad"] = pd.to_numeric(df_merge["cantidad"], errors="coerce").fillna(0)
        df_merge["precio"]   = pd.to_numeric(df_merge["precio"],   errors="coerce").fillna(0)
        df_merge["monto_bruto"] = df_merge["cantidad"] * df_merge["precio"]

        # Construir el bruto total por factura usando TODAS las líneas de esas facturas (de df_prod + df_fac_sel)
        df_all = pd.merge(
            df_prod[["factura", "cantidad", "precio"]],
            df_fac_sel[["factura", "total"]],
            on="factura",
            how="inner"
        ).copy()
        df_all["cantidad"] = pd.to_numeric(df_all["cantidad"], errors="coerce").fillna(0)
        df_all["precio"]   = pd.to_numeric(df_all["precio"],   errors="coerce").fillna(0)
        df_all["bruto_linea"] = df_all["cantidad"] * df_all["precio"]

        bruto_por_factura   = df_all.groupby("factura")["bruto_linea"].sum().to_dict()
        neto_por_factura    = pd.to_numeric(df_fac_sel.set_index("factura")["total"], errors="coerce").to_dict()

        df_merge["total_bruto_factura"] = df_merge["factura"].map(bruto_por_factura)
        df_merge["total_factura_neto"]  = df_merge["factura"].map(neto_por_factura)

        df_merge["factor_descuento_cliente"] = (
            df_merge["total_factura_neto"] / df_merge["total_bruto_factura"]
        ).replace([float("inf"), -float("inf")], 0).fillna(0)

        df_merge["monto_real"] = df_merge["monto_bruto"] * df_merge["factor_descuento_cliente"]

        # === Top productos por monto real ===
        top_prod = (
            df_merge.groupby("producto")[["cantidad", "monto_real"]]
            .sum()
            .query("monto_real > 0")
            .sort_values("monto_real", ascending=False)
            .head(8)
        )

        # --- Crear ventana de diálogo ---
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Recompra: {compras_min}–{compras_max} compras  ·  Empresa: {empresa_sel if empresa_sel!='Todas' else 'Todas'}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        # === Gráfico Productos más recomprados (monto real) ===
        fig, ax = plt.subplots(figsize=(13, 3.8))
        canvas = FigureCanvas(fig)
        canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        canvas.setMinimumHeight(340)

        ax.set_title("Productos más recomprados (monto real)", fontsize=11, pad=12)
        ax.set_xlabel("Monto ($)")
        ax.grid(axis="x", linestyle="--", alpha=0.35, zorder=0)
        ax.set_yticks([])
        ax.invert_yaxis()

        if not top_prod.empty:
            bars = ax.barh(top_prod.index[::-1], top_prod["monto_real"].iloc[::-1], color="#205493", zorder=3)
            for bar, producto, val in zip(bars, top_prod.index[::-1], top_prod["monto_real"].iloc[::-1]):
                y_center = bar.get_y() + bar.get_height() / 2
                ancho = bar.get_width()
                ax.text(ancho * 0.02, y_center, str(producto),
                        va="center", ha="left", fontsize=9, color="white", fontweight="normal", zorder=4)
                ax.text(ancho * 0.985, y_center, f"${val:,.0f}",
                        va="center", ha="right", fontsize=10, fontweight="bold", color="white", zorder=4)
        else:
            ax.text(0.5, 0.5, "Sin productos", ha="center", va="center", fontsize=11)
            ax.axis("off")

        lay.addWidget(canvas)

        # === Construir DataFrame de clientes del grupo con sus 5 productos más recomprados (por monto real) ===
        productos_por_cliente = (
            df_merge.groupby(["tienda", "producto"])["monto_real"]
            .sum()
            .reset_index()
            .sort_values(["tienda", "monto_real"], ascending=[True, False])
        )

        productos_top5 = (
            productos_por_cliente.groupby("tienda")
            .head(5)
            .groupby("tienda")["producto"]
            .apply(lambda x: ", ".join(x))
            .reset_index()
            .rename(columns={"producto": "productos_recomprados"})
        )

        compras_por_cliente = df_fac_sel.groupby("tienda")["factura"].count().reset_index()
        compras_por_cliente.columns = ["tienda", "compras"]

        clientes_df = pd.merge(productos_top5, compras_por_cliente, on="tienda", how="inner")
        clientes_df = clientes_df.sort_values("compras", ascending=False)

        # === Tabla ===
        tabla = QTableWidget()
        columnas = ["Cliente", "Productos Recomprados", "Compras"]
        tabla.setColumnCount(len(columnas))
        tabla.setHorizontalHeaderLabels(columnas)
        tabla.setRowCount(len(clientes_df))

        for r, row in enumerate(clientes_df.itertuples()):
            tabla.setItem(r, 0, QTableWidgetItem(str(row.tienda)))
            tabla.setItem(r, 1, QTableWidgetItem(str(row.productos_recomprados)))
            tabla.setItem(r, 2, QTableWidgetItem(str(int(row.compras))))

        header = tabla.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        for col in [1]:
            for r in range(tabla.rowCount()):
                item = tabla.item(r, col)
                if item:
                    item.setTextAlignment(Qt.AlignCenter)

        tabla.setAlternatingRowColors(True)
        tabla.setStyleSheet("""
            QTableWidget {
                gridline-color: #d1d5db;
                border: 1px solid #cbd5e1;
                font-size: 10pt;
            }
            QHeaderView::section {
                background-color: #f3f4f6;
                font-weight: bold;
                border: none;
                padding: 4px;
            }
            QTableWidget::item {
                padding: 2px 6px;
            }
        """)
        tabla.verticalHeader().setVisible(False)
        tabla.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay.addWidget(tabla, stretch=1)

        # === Botones ===
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_pdf  = QPushButton(" Exportar PDF")
        btn_xlsx = QPushButton(" Exportar Excel")
        btn_close = QPushButton(" Cerrar")
        self._style_action_buttons(btn_pdf, btn_xlsx, btn_close)
        btn_row.addWidget(btn_pdf)
        btn_row.addSpacing(8)
        btn_row.addWidget(btn_xlsx)
        btn_row.addSpacing(8)
        btn_row.addWidget(btn_close)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)


        def _export_pdf():
            from PyQt5.QtWidgets import QFileDialog, QMessageBox
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
            from reportlab.lib import colors
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            import tempfile, os
            import pandas as pd

            tmpdir = tempfile.gettempdir()
            chart_path = os.path.join(tmpdir, "chart_recompra.png")
            fig.savefig(chart_path, dpi=160, bbox_inches="tight")

            path, _ = QFileDialog.getSaveFileName(
                dlg, "Guardar PDF",
                f"Recompra_{compras_min}_{compras_max}.pdf",
                "PDF (*.pdf)"
            )
            if not path:
                return

            try:
                # === Seguridad de columnas ===
                if "monto_real" not in df_merge.columns:
                    df_merge["monto_real"] = 0

                doc = SimpleDocTemplate(path, pagesize=A4,
                                        rightMargin=36, leftMargin=36,
                                        topMargin=36, bottomMargin=36)
                styles = getSampleStyleSheet()
                flow = []

                # --- Encabezado ---
                title = Paragraph(
                    f"<b>Detalle de Recompra</b><br/>{compras_min}–{compras_max} compras · Empresa: {empresa_sel}",
                    styles["Title"]
                )
                flow.append(title)
                flow.append(Spacer(1, 12))

                # --- Resumen ---
                resumen_text = f"""
                    <b>Clientes en el grupo:</b> {len(sel_clientes)}<br/>
                    <b>Total productos distintos:</b> {df_merge['producto'].nunique()}<br/>
                    <b>Monto total recomprado:</b> ${df_merge['monto_real'].sum():,.2f}
                """
                flow.append(Paragraph(resumen_text, styles["Normal"]))
                flow.append(Spacer(1, 12))

                if os.path.exists(chart_path):
                    flow.append(Image(chart_path, width=500, height=200))
                    flow.append(Spacer(1, 16))

                # --- Estilos tabla ---
                cell_left = ParagraphStyle(name="CellLeft", fontSize=8, alignment=0)
                cell_center = ParagraphStyle(name="CellCenter", fontSize=8, alignment=1)

                columnas_esperadas = ["tienda", "productos_recomprados", "compras"]
                for col in columnas_esperadas:
                    if col not in clientes_df.columns:
                        clientes_df[col] = ""
                df_safe = clientes_df[columnas_esperadas].fillna("").astype(str)

                # --- Crear filas ---
                data = [["Cliente", "Productos Recomprados", "Compras"]]
                for _, row in df_safe.iterrows():
                    data.append([
                        Paragraph(row["tienda"], cell_left),
                        Paragraph(row["productos_recomprados"], cell_left),
                        Paragraph(row["compras"], cell_center),
                    ])

                total_compras = pd.to_numeric(clientes_df["compras"], errors="coerce").fillna(0).sum()
                data.append([
                    Paragraph("<b>Total</b>", cell_center),
                    "",
                    Paragraph(f"<b>{int(total_compras)}</b>", cell_center)
                ])

                table = Table(data, colWidths=[130, 320, 50])
                total_filas = len(data)
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0A74D7")),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 8),
                    ('ALIGN', (2, 1), (2, -1), 'CENTER'),
                    ('BACKGROUND', (0, 1), (-1, total_filas - 2), colors.whitesmoke),
                    ('BACKGROUND', (0, total_filas - 1), (-1, total_filas - 1), colors.lightgrey),
                    ('FONTNAME', (0, total_filas - 1), (-1, total_filas - 1), 'Helvetica-Bold'),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ]))
                flow.append(table)

                doc.build(flow)
                QMessageBox.information(dlg, "Exportación", "✅ PDF generado correctamente.")

            except Exception as e:
                QMessageBox.warning(dlg, "Exportación", f"No se pudo exportar:\n{e}")




        def _export_xlsx():
            from PyQt5.QtWidgets import QFileDialog, QMessageBox
            import tempfile, os
            import pandas as pd

            path, _ = QFileDialog.getSaveFileName(
                dlg, "Guardar Excel",
                f"Recompra_{compras_min}_{compras_max}.xlsx",
                "Excel (*.xlsx)"
            )
            if not path:
                return

            try:
                tmpdir = tempfile.gettempdir()
                chart_path = os.path.join(tmpdir, "chart_recompra.png")
                fig.savefig(chart_path, dpi=160, bbox_inches="tight")

                if "monto_real" not in df_merge.columns:
                    df_merge["monto_real"] = 0

                with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
                    workbook = writer.book
                    ws = workbook.add_worksheet("Detalle Recompra")

                    fmt_header = workbook.add_format({
                        "bold": True, "bg_color": "#0A74D7", "font_color": "white",
                        "align": "center", "valign": "vcenter", "border": 1
                    })
                    fmt_label = workbook.add_format({"bold": True, "align": "left"})
                    fmt_value = workbook.add_format({"align": "center"})
                    fmt_left = workbook.add_format({"text_wrap": True, "valign": "top", "border": 1})
                    fmt_center = workbook.add_format({"align": "center", "border": 1})
                    fmt_total = workbook.add_format({"bold": True, "align": "center", "bg_color": "#e6e6e6", "border": 1})
                    fmt_title = workbook.add_format({"bold": True, "font_size": 14, "align": "center"})

                    resumen_data = [
                        ["Rango de compras", f"{compras_min}–{compras_max}"],
                        ["Clientes en el grupo", len(sel_clientes)],
                        ["Total productos distintos", df_merge["producto"].nunique()],
                        ["Monto total recomprado ($)", f"${df_merge['monto_real'].sum():,.2f}"]
                    ]
                    ws.write("A1", "Métrica", fmt_header)
                    ws.write("B1", "Valor", fmt_header)
                    row = 1
                    for label, val in resumen_data:
                        ws.write(row, 0, label, fmt_label)
                        ws.write(row, 1, val, fmt_value)
                        row += 1

                    ws.set_column("A:A", 35)
                    ws.set_column("B:B", 30)

                    title_row = row + 1
                    ws.merge_range(title_row, 0, title_row, 1,
                                f"Detalle de Recompra · Empresa: {empresa_sel}",
                                fmt_title)

                    chart_row = title_row + 2
                    ws.insert_image(chart_row, 0, chart_path, {"x_scale": 1.0, "y_scale": 1.0})
                    chart_end = chart_row + 22

                    columnas = ["Cliente", "Productos Recomprados", "Compras"]
                    df_safe = clientes_df[["tienda", "productos_recomprados", "compras"]].copy()
                    df_safe.columns = columnas

                    total_compras = pd.to_numeric(df_safe["Compras"], errors="coerce").fillna(0).sum()
                    df_safe.loc[len(df_safe)] = ["Total", "", int(total_compras)]

                    start_row = chart_end + 3
                    for c, col in enumerate(columnas):
                        ws.write(start_row, c, col, fmt_header)

                    for r_idx, row_data in enumerate(df_safe.itertuples(index=False), start=start_row + 1):
                        for c_idx, value in enumerate(row_data):
                            if row_data.Cliente == "Total":
                                ws.write(r_idx, c_idx, value, fmt_total)
                            elif c_idx == 2:
                                ws.write(r_idx, c_idx, value, fmt_center)
                            else:
                                ws.write(r_idx, c_idx, value, fmt_left)

                    ws.set_column("A:A", 30)
                    ws.set_column("B:B", 70)
                    ws.set_column("C:C", 10)

                QMessageBox.information(dlg, "Exportación", "✅ Excel generado correctamente.")

            except Exception as e:
                QMessageBox.warning(dlg, "Exportación", f"No se pudo exportar:\n{e}")

        btn_pdf.clicked.connect(_export_pdf)
        btn_xlsx.clicked.connect(_export_xlsx)
        btn_close.clicked.connect(dlg.close)

        dlg.resize(900, 760)
        dlg.exec_()




    def cargar_clientes_y_productos(self):
        """Llena los combos de cliente y producto usando los datos de la pestaña Mio."""
        import pandas as pd

        # --- Verificar que haya datos disponibles ---
        if not self.ventana_mio:
            return

        df = self.ventana_mio.obtener_dataframe_facturas()
        if df.empty:
            self.combo_cliente.clear()
            self.combo_producto.clear()
            self.combo_cliente.addItem("— Sin datos —")
            self.combo_producto.addItem("— Sin datos —")
            return

        # --- Clientes ---
        # 🔧 Mantener texto actual al recargar lista de clientes
        texto_actual = self.combo_cliente.currentText().strip()

        clientes_unicos = sorted(df["cliente"].dropna().unique().tolist())
        self.combo_cliente.blockSignals(True)
        self.combo_cliente.clear()
        self.combo_cliente.addItem("")  # opción vacía
        self.combo_cliente.addItems(clientes_unicos)
        self.combo_cliente.blockSignals(False)

        # Restaurar texto que estaba escrito
        self.combo_cliente.setEditText(texto_actual)


        # --- Productos reales desde la tabla factura_detalle ---
        df_productos = self.ventana_mio.obtener_productos_facturados()
        if not df_productos.empty and "producto" in df_productos.columns:
            productos_unicos = sorted(df_productos["producto"].dropna().unique().tolist())
        else:
            productos_unicos = ["— Sin productos —"]

        self.combo_producto.clear()
        self.combo_producto.addItem("")
        self.combo_producto.addItems(productos_unicos)

    # ============================================================
    # 📈 2️⃣ Dashboard Gerencial (nuevos indicadores)
    # ============================================================
    def crear_dashboard_gerencial(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 10, 10, 0)
        layout.setSpacing(10)

        # Panel superior con KPIs
        panel_kpi = QWidget()
        panel_layout = QHBoxLayout(panel_kpi)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(15)

        # Indicadores
        self.lbl_total_ventas = self.crear_kpi("💰 Ventas Totales", "$0.00")
        self.lbl_ticket_promedio = self.crear_kpi("🧾 Ticket Promedio", "$0.00")
        self.lbl_clientes_unicos = self.crear_kpi("👥 Clientes Únicos", "0")
        self.lbl_crecimiento = self.crear_kpi("📈 Crecimiento", "0%")

        panel_layout.addWidget(self.lbl_total_ventas)
        panel_layout.addWidget(self.lbl_ticket_promedio)
        panel_layout.addWidget(self.lbl_clientes_unicos)
        panel_layout.addWidget(self.lbl_crecimiento)

        layout.addWidget(panel_kpi)

        # --- Gráficos inferiores ---
        self.grid_gerencial = QGridLayout()
        self.grid_gerencial.setSpacing(15)
        layout.addLayout(self.grid_gerencial)

        self.grid_gerencial.setRowStretch(0, 1)
        self.grid_gerencial.setRowStretch(1, 1)

        self.widgets_ger = []
        self.crear_paneles_gerenciales()
        self.generar_reportes_gerenciales()

        return widget

    # ------------------------------------------------------------
    def crear_kpi(self, titulo, valor):
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background: #ffffff;
                border-radius: 10px;
                border: 1px solid #dcdcdc;
            }
            QLabel {
                color: #333;
            }
        """)
        layout = QVBoxLayout(frame)

        lbl_titulo = QLabel(titulo)
        lbl_titulo.setAlignment(Qt.AlignCenter)
        lbl_titulo.setStyleSheet("font-weight: bold; font-size: 11pt;")

        lbl_valor = QLabel(valor)
        lbl_valor.setAlignment(Qt.AlignCenter)
        lbl_valor.setStyleSheet("font-size: 16pt; color: #0078d7; font-weight: bold;")

        layout.addWidget(lbl_titulo)
        layout.addWidget(lbl_valor)

        # Para poder actualizar el valor desde fuera
        frame.valor_label = lbl_valor
        return frame

    # ------------------------------------------------------------
    def crear_paneles_gerenciales(self):
        titulos = [
            "📅 Evolución mensual de ventas",
            "🏢 Comparativo por empresa",
        ]
        posiciones = [(0, 0), (0, 1)]

        for i, (titulo, pos) in enumerate(zip(titulos, posiciones)):
            frame = QFrame()
            frame.setStyleSheet("""
                QFrame {
                    background: #ffffff;
                    border-radius: 10px;
                    border: 1px solid #dcdcdc;
                }
            """)
            layout = QVBoxLayout(frame)

            lbl_titulo = QLabel(titulo)
            lbl_titulo.setStyleSheet("font-weight: bold; font-size: 12pt; color: #333;")
            layout.addWidget(lbl_titulo)

            fig, ax = plt.subplots(figsize=(5, 3))
            canvas = FigureCanvas(fig)
            layout.addWidget(canvas)

            self.grid_gerencial.addWidget(frame, *pos)
            self.widgets_ger.append((canvas, ax))

    # ------------------------------------------------------------
    def generar_reportes_gerenciales(self):
        """Genera indicadores gerenciales reales a partir de facturas filtradas."""
        import pandas as pd
        import numpy as np

        # ============================================================
        # 1️⃣ Cargar datos base directamente desde la pestaña Mio
        # ============================================================
        try:
            self.df_facturas = self.ventana_mio.obtener_dataframe_facturas()
            self.df_productos = self.ventana_mio.obtener_productos_facturados()
        except Exception as e:
            print(f"⚠️ Error al obtener datos desde ventana_mio: {e}")
            self.df_facturas = pd.DataFrame()
            self.df_productos = pd.DataFrame()
            return

        df_fac = self.df_facturas
        df_prod = self.df_productos

        if df_fac is None or df_fac.empty:
            print("⚠️ No hay datos de facturas para indicadores gerenciales.")
            return

        # ============================================================
        # 2️⃣ Asegurar columna 'fecha'
        # ============================================================
        if "fecha" not in df_fac.columns:
            if {"dia", "mes"}.issubset(df_fac.columns):
                try:
                    df_fac["fecha"] = pd.to_datetime(
                        df_fac["dia"].astype(str) + "-" + df_fac["mes"].astype(str) + "-2025",
                        format="%d-%b-%Y", errors="coerce"
                    )
                    print("✅ Columna 'fecha' creada correctamente (antes de filtros).")
                except Exception as e:
                    print(f"⚠️ Error creando columna 'fecha': {e}")
                    df_fac["fecha"] = pd.NaT
            else:
                print("⚠️ No hay columnas suficientes para crear 'fecha'.")
                return

        # ============================================================
        # 3️⃣ Aplicar filtros globales si existen
        # ============================================================
        if hasattr(self, "_aplicar_filtros_generales"):
            df_fac = self._aplicar_filtros_generales(df_fac)

        if df_fac.empty:
            print("⚠️ No hay datos después de aplicar filtros, usando datos originales.")
            df_fac = self.ventana_mio.obtener_dataframe_facturas().copy()

        # ============================================================
        # 4️⃣ Validaciones de columnas y limpieza
        # ============================================================
        if "total" not in df_fac.columns:
            print("⚠️ No se encontró columna 'total' en df_facturas.")
            return

        df_fac["fecha"] = pd.to_datetime(df_fac["fecha"], errors="coerce")
        df_fac = df_fac.dropna(subset=["fecha"])
        df_fac["mes"] = df_fac["fecha"].dt.to_period("M").astype(str)

        if df_fac.empty:
            print("⚠️ Sin registros válidos después de limpieza.")
            return

        # ============================================================
        # 5️⃣ KPIs principales
        # ============================================================
        total_ventas = df_fac["total"].sum()
        clientes_unicos = df_fac["tienda"].nunique() if "tienda" in df_fac.columns else 0
        n_facturas = len(df_fac)
        ticket_promedio = total_ventas / n_facturas if n_facturas > 0 else 0

        # Crecimiento mensual (último vs anterior)
        ventas_mensuales = df_fac.groupby("mes")["total"].sum().sort_index()
        if len(ventas_mensuales) >= 2:
            crecimiento = (ventas_mensuales.iloc[-1] / ventas_mensuales.iloc[-2]) - 1
        else:
            crecimiento = 0

        # --- Actualizar etiquetas KPI ---
        if hasattr(self, "lbl_total_ventas"):
            self.lbl_total_ventas.valor_label.setText(f"${total_ventas:,.2f}")
        if hasattr(self, "lbl_ticket_promedio"):
            self.lbl_ticket_promedio.valor_label.setText(f"${ticket_promedio:,.2f}")
        if hasattr(self, "lbl_clientes_unicos"):
            self.lbl_clientes_unicos.valor_label.setText(str(clientes_unicos))
        if hasattr(self, "lbl_crecimiento"):
            self.lbl_crecimiento.valor_label.setText(f"{crecimiento*100:.1f}%")

        print(f"✅ KPIs: Ventas={total_ventas:.2f}, Facturas={n_facturas}, Clientes={clientes_unicos}, Crecimiento={crecimiento*100:.2f}%")

        # ============================================================
        # 6️⃣ Evolución mensual de ventas
        # ============================================================
        ax = self.widgets_ger[0][1]
        ax.clear()

        if not ventas_mensuales.empty:
            ax.plot(
                ventas_mensuales.index,
                ventas_mensuales.values,
                marker="o",
                color="#0078d7",
                linewidth=2
            )
            ax.set_title("Evolución mensual de ventas", pad=10)
            ax.set_ylabel("Monto ($)")
            ax.set_xlabel("Mes")
            ax.grid(True, linestyle="--", alpha=0.6)

            ax.margins(x=0.05, y=0.15)
            ax.tick_params(axis="x", labelrotation=0, labelsize=8)
            ax.tick_params(axis="y", labelsize=8)
            ax.figure.subplots_adjust(left=0.12, right=0.96, top=0.88, bottom=0.22)
            ax.set_ylim(0, max(ventas_mensuales.values) * 1.2)
        else:
            ax.text(0.5, 0.5, "Sin datos", ha="center", va="center", fontsize=11)
            ax.axis("off")

        self.widgets_ger[0][0].draw()

        # ============================================================
        # 7️⃣ Comparativo por empresa
        # ============================================================
        ax = self.widgets_ger[1][1]
        ax.clear()

        if "empresa" in df_fac.columns:
            totales_empresa = df_fac.groupby("empresa")["total"].sum().sort_values(ascending=False)
            if not totales_empresa.empty:
                bars = ax.bar(
                    range(len(totales_empresa)),
                    totales_empresa.values,
                    color=["#4A90E2", "#7ED321", "#F5A623", "#50E3C2"]
                )
                ax.set_title("Comparativo por empresa", pad=10)
                ax.set_ylabel("Total de ventas ($)")
                ax.set_xticks(range(len(totales_empresa)))
                ax.set_xticklabels(totales_empresa.index, rotation=10, ha="center")

                ax.margins(x=0.05, y=0.2)
                ax.tick_params(axis="x", labelsize=9)
                ax.tick_params(axis="y", labelsize=8)
                ax.figure.subplots_adjust(left=0.12, right=0.96, top=0.88, bottom=0.22)
                ax.set_ylim(0, max(totales_empresa.values) * 1.25)

                for bar in bars:
                    height = bar.get_height()
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        height + (height * 0.03),
                        f"${height:,.0f}",
                        ha="center",
                        va="bottom",
                        fontsize=8,
                        color="#333"
                    )
            else:
                ax.text(0.5, 0.5, "Sin datos por empresa", ha="center", va="center", fontsize=11)
                ax.axis("off")
        else:
            ax.text(0.5, 0.5, "Columna 'empresa' no encontrada", ha="center", va="center", fontsize=11)
            ax.axis("off")

        self.widgets_ger[1][0].draw()




from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QWidget, QLineEdit, QFrame, QFileDialog
)
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtCore import Qt
import pandas as pd
import os

class DetalleProductoDialog(QDialog):
    def __init__(self, parent, nombre_producto, df, paleta_brand=("black", "#D4AF37", "white")):
        super().__init__(parent)
        self.setWindowTitle(f"Detalle del producto")
        self.resize(920, 640)
        self.df_base = df.copy()
        self.nombre_producto = nombre_producto
        self.paleta = paleta_brand  # (texto, dorado, fondo)

        # ===== Estilos =====
        self.setStyleSheet(f"""
            QDialog {{
                background: {self.paleta[2]};
            }}
            QLabel[role="title"] {{
                font-weight: 800;
                font-size: 16pt;
                color: {self.paleta[0]};
            }}
            QLabel[role="subtitle"] {{
                font-weight: 600;
                font-size: 11pt;
                color: {self.paleta[0]};
            }}
            QFrame#card {{
                background: white;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
            }}
            QPushButton {{
                background-color: {self.paleta[1]};
                color: black;
                font-weight: bold;
                border-radius: 6px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                background-color: #caa63a;
            }}
            QLineEdit {{
                background: white;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 4px 8px;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ===== Header =====
        header = QHBoxLayout()
        lbl_title = QLabel(f"📦 {self.nombre_producto}")
        lbl_title.setProperty("role", "title")
        header.addWidget(lbl_title)
        header.addStretch()

        # KPIs producto
        total_unidades = self.df_base["cantidad"].sum()
        total_monto = self.df_base["total"].sum()
        kpi = QLabel(f"Unidades: <b>{total_unidades:,.0f}</b>  |  Total: <b>${total_monto:,.2f}</b>")
        kpi.setProperty("role", "subtitle")
        header.addWidget(kpi)
        root.addLayout(header)

        # ===== Sección Superior: Ranking por Cliente =====
        card_top = QFrame()
        card_top.setObjectName("card")
        lay_top = QVBoxLayout(card_top)
        lay_top.setContentsMargins(12, 12, 12, 12)
        lay_top.setSpacing(8)

        lbl_top = QLabel("Ranking por cliente")
        lbl_top.setProperty("role", "subtitle")
        lay_top.addWidget(lbl_top)

        self.tab_top = QTableWidget()
        self.tab_top.setColumnCount(4)
        self.tab_top.setHorizontalHeaderLabels(["Cliente", "Unidades", "Total $", "% Participación"])
        self.tab_top.horizontalHeader().setStretchLastSection(True)
        lay_top.addWidget(self.tab_top)

        root.addWidget(card_top)

        # ===== Sección Inferior: Detalle de facturas + filtros locales =====
        card_bottom = QFrame()
        card_bottom.setObjectName("card")
        lay_bottom = QVBoxLayout(card_bottom)
        lay_bottom.setContentsMargins(12, 12, 12, 12)
        lay_bottom.setSpacing(8)

        # Filtros locales (simple: por cliente)
        filt_row = QHBoxLayout()
        filt_row.addWidget(QLabel("Filtrar cliente:"))
        self.input_filtro_cliente = QLineEdit()
        self.input_filtro_cliente.setPlaceholderText("Escribe parte del nombre...")
        btn_aplicar = QPushButton("Aplicar")
        btn_aplicar.clicked.connect(self._aplicar_filtro_local)
        filt_row.addWidget(self.input_filtro_cliente)
        filt_row.addWidget(btn_aplicar)
        filt_row.addStretch()

        # Acciones exportar
        btn_xlsx = QPushButton("Exportar Excel")
        btn_xlsx.clicked.connect(self._exportar_excel)
        btn_pdf = QPushButton("Exportar PDF")
        btn_pdf.clicked.connect(self._exportar_pdf)

        filt_row.addWidget(btn_xlsx)
        filt_row.addWidget(btn_pdf)

        lay_bottom.addLayout(filt_row)

        self.tab_detalle = QTableWidget()
        self.tab_detalle.setColumnCount(6)
        self.tab_detalle.setHorizontalHeaderLabels(["Factura", "Cliente", "Cantidad", "Precio U", "Total $", "Empresa"])
        self.tab_detalle.horizontalHeader().setStretchLastSection(True)
        lay_bottom.addWidget(self.tab_detalle)

        root.addWidget(card_bottom)

        # Cargar datos en tablas
        self._poblar_ranking()
        self._poblar_detalle(self.df_base)

    # ===== Helpers de tabla =====
    def _poblar_ranking(self):
        df_rank = (
            self.df_base.groupby("tienda", dropna=False)[["cantidad", "total"]]
            .sum()
            .sort_values("total", ascending=False)
        )
        total_monto = df_rank["total"].sum() or 1
        df_rank["participacion"] = (df_rank["total"] / total_monto) * 100

        self.tab_top.setRowCount(len(df_rank))
        for r, (cliente, fila) in enumerate(df_rank.iterrows()):
            self.tab_top.setItem(r, 0, QTableWidgetItem(str(cliente or "")))
            self.tab_top.setItem(r, 1, QTableWidgetItem(f"{fila['cantidad']:,.0f}"))
            self.tab_top.setItem(r, 2, QTableWidgetItem(f"${fila['total']:,.2f}"))
            self.tab_top.setItem(r, 3, QTableWidgetItem(f"{fila['participacion']:.1f}%"))
        self.tab_top.resizeColumnsToContents()

    def _poblar_detalle(self, df):
        self.tab_detalle.setRowCount(len(df))
        for r, fila in enumerate(df.itertuples(index=False)):
            self.tab_detalle.setItem(r, 0, QTableWidgetItem(str(fila.factura)))
            self.tab_detalle.setItem(r, 1, QTableWidgetItem(str(fila.tienda)))
            self.tab_detalle.setItem(r, 2, QTableWidgetItem(f"{float(fila.cantidad):,.2f}"))
            self.tab_detalle.setItem(r, 3, QTableWidgetItem(f"${float(fila.precio):,.2f}"))
            self.tab_detalle.setItem(r, 4, QTableWidgetItem(f"${float(fila.total):,.2f}"))
            self.tab_detalle.setItem(r, 5, QTableWidgetItem(str(fila.empresa)))
        self.tab_detalle.resizeColumnsToContents()

    # ===== Filtro local (cliente) =====
    def _aplicar_filtro_local(self):
        txt = (self.input_filtro_cliente.text() or "").strip().lower()
        if not txt:
            self._poblar_detalle(self.df_base)
            return
        df = self.df_base[self.df_base["tienda"].str.lower().str.contains(txt, na=False)]
        self._poblar_detalle(df)

    # ===== Exportaciones =====
    def _exportar_excel(self):
        ruta, _ = QFileDialog.getSaveFileName(self, "Guardar como", f"Detalle_{self.nombre_producto}.xlsx", "Excel (*.xlsx)")
        if not ruta:
            return
        try:
            # Exportar ambas tablas en hojas separadas
            # Ranking
            df_rank = (
                self.df_base.groupby("tienda", dropna=False)[["cantidad", "total"]]
                .sum()
                .sort_values("total", ascending=False)
                .reset_index()
                .rename(columns={"tienda": "Cliente", "cantidad": "Unidades", "total": "Total"})
            )
            # Detalle
            df_det = self.df_base[["factura", "tienda", "cantidad", "precio", "total", "empresa"]].copy()
            df_det.columns = ["Factura", "Cliente", "Cantidad", "Precio U", "Total", "Empresa"]

            with pd.ExcelWriter(ruta, engine="openpyxl") as writer:
                df_rank.to_excel(writer, sheet_name="Ranking por cliente", index=False)
                df_det.to_excel(writer, sheet_name="Detalle de facturas", index=False)

            QMessageBox.information(self, "Exportación", f"Archivo guardado:\n{ruta}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo exportar a Excel:\n{e}")

    def _exportar_pdf(self):
        # Intentar con reportlab si está disponible
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib import colors
            from reportlab.pdfgen import canvas
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib.units import cm

            ruta, _ = QFileDialog.getSaveFileName(self, "Guardar PDF", f"Detalle_{self.nombre_producto}.pdf", "PDF (*.pdf)")
            if not ruta:
                return

            doc = SimpleDocTemplate(ruta, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
            styles = getSampleStyleSheet()
            story = []

            # Branding Gourmet: dorado/negro/blanco
            titulo = Paragraph(f"<para align='left'><b>{self.nombre_producto}</b></para>", styles["Title"])
            story.append(titulo)
            story.append(Spacer(1, 8))

            total_unid = self.df_base["cantidad"].sum()
            total_monto = self.df_base["total"].sum()
            kpi = Paragraph(f"Unidades: <b>{total_unid:,.0f}</b> &nbsp;&nbsp;|&nbsp;&nbsp; Total: <b>${total_monto:,.2f}</b>", styles["Heading3"])
            story.append(kpi)
            story.append(Spacer(1, 12))

            # Ranking por cliente
            story.append(Paragraph("<b>Ranking por cliente</b>", styles["Heading4"]))
            df_rank = (
                self.df_base.groupby("tienda", dropna=False)[["cantidad", "total"]]
                .sum()
                .sort_values("total", ascending=False)
                .reset_index()
            )
            total_m = df_rank["total"].sum() or 1
            df_rank["participacion"] = (df_rank["total"] / total_m) * 100
            data_rank = [["Cliente", "Unidades", "Total $", "%"]] + [
                [str(r["tienda"] or ""), f"{r['cantidad']:,.0f}", f"${r['total']:,.2f}", f"{r['participacion']:.1f}%"]
                for _, r in df_rank.iterrows()
            ]
            tbl_rank = Table(data_rank, hAlign="LEFT")
            tbl_rank.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#D4AF37')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.black),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('ALIGN', (1,1), (-1,-1), 'RIGHT'),
                ('GRID', (0,0), (-1,-1), 0.3, colors.grey),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.whitesmoke, colors.HexColor('#f9f9f9')])
            ]))
            story.append(tbl_rank)
            story.append(Spacer(1, 16))

            # Detalle
            story.append(Paragraph("<b>Detalle de facturas</b>", styles["Heading4"]))
            df_det = self.df_base[["factura", "tienda", "cantidad", "precio", "total", "empresa"]].copy()
            data_det = [["Factura", "Cliente", "Cantidad", "Precio U", "Total $", "Empresa"]] + [
                [str(r["factura"]), str(r["tienda"]), f"{float(r['cantidad']):,.2f}", f"${float(r['precio']):,.2f}", f"${float(r['total']):,.2f}", str(r["empresa"])]
                for _, r in df_det.iterrows()
            ]
            tbl_det = Table(data_det, hAlign="LEFT")
            tbl_det.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.black),
                ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('ALIGN', (2,1), (-2,-1), 'RIGHT'),
                ('GRID', (0,0), (-1,-1), 0.25, colors.grey),
            ]))
            story.append(tbl_det)

            doc.build(story)
            QMessageBox.information(self, "Exportación", f"PDF generado correctamente:\n{ruta}")
        except Exception as e:
            QMessageBox.warning(self, "PDF no disponible", f"No se pudo generar el PDF automáticamente:\n{e}\n\nSugerencia: instala reportlab:\n  pip install reportlab")




class VistaPreviaFactura(QDialog):
    def __init__(self, pdf_path, cliente_nombre="", total=0.0, parent=None,
                 numero_factura=None, modo_edicion=False, datos_factura=None):
        super().__init__(parent)

        print("🧩 [DEBUG] Se crea VistaPreviaFactura")
        print(f"   → modo_edicion: {modo_edicion}")
        print(f"   → numero_factura: {numero_factura}")
        print(f"   → datos_factura: {bool(datos_factura)}")

        self.modo_edicion = modo_edicion
        self.datos_factura = datos_factura or {}
        self.numero_factura = numero_factura
        # 🔥 Extraer vendedor y RFC para usarlos en modo edición
        self.vendedor = self.datos_factura.get("vendedor", "")
        self.rfc = self.datos_factura.get("rfc", "")
        self.setWindowTitle("Vista previa de factura")
        self.resize(950, 950)
        self.pdf_path = pdf_path

        # ==========================================================
        # 🧩 Layout principal
        # ==========================================================
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # --- Encabezado superior
        header = QHBoxLayout()
        self.lbl_cliente = QLabel(f"<b>Cliente:</b> {cliente_nombre}")
        self.lbl_cliente.setStyleSheet("font-size: 12pt; color: #111827;")
        self.lbl_total = QLabel(f"<b>Total:</b> ${total:,.2f}")
        self.lbl_total.setAlignment(Qt.AlignRight)
        self.lbl_total.setStyleSheet("font-size: 12pt; color: #2563eb; font-weight: bold;")
        header.addWidget(self.lbl_cliente, stretch=3)
        header.addWidget(self.lbl_total, stretch=1)
        layout.addLayout(header)

        # ==========================================================
        # 📄 Visualizador del PDF
        # ==========================================================
        container_pdf = QWidget()
        stack = QStackedLayout(container_pdf)
        self.viewer = QWebEngineView()
        self.viewer.page().javaScriptConsoleMessage = lambda level, msg, line, src: None  # silencia errores JS
        settings = self.viewer.settings()
        settings.setAttribute(settings.PluginsEnabled, True)
        settings.setAttribute(settings.PdfViewerEnabled, True)
        pdf_url = QUrl.fromLocalFile(os.path.abspath(pdf_path))
        QTimer.singleShot(800, lambda: self.viewer.setUrl(pdf_url))
        QTimer.singleShot(1500, lambda: self.viewer.reload())
        stack.addWidget(self.viewer)

        # === Botón de guardar cambios superpuesto sobre el PDF ===
        if self.modo_edicion:
            self.btn_guardar_cambios = QPushButton("💾 Guardar cambios", container_pdf)
            self.btn_guardar_cambios.setFixedSize(200, 44)
            self.btn_guardar_cambios.move(720, 20)  # posición superior derecha
            self.btn_guardar_cambios.setCursor(Qt.PointingHandCursor)
            self.btn_guardar_cambios.raise_()
            self.btn_guardar_cambios.setStyleSheet("""
                QPushButton {
                    background-color: #059669;
                    color: white;
                    font-weight: bold;
                    border-radius: 6px;
                    font-size: 10.5pt;
                }
                QPushButton:hover {
                    background-color: #047857;
                }
            """)
            self.btn_guardar_cambios.clicked.connect(self.guardar_cambios)
            self.btn_guardar_cambios.show()

        layout.addWidget(container_pdf, stretch=1)

        # ==========================================================
        # 🔘 Botones inferiores
        # ==========================================================
        botones = QHBoxLayout()
        botones.setSpacing(25)
        botones.addStretch(1)

        # Solo mostrar "Incluir factura" si no es edición
        if not self.modo_edicion:
            btn_guardar = QPushButton("💾 Incluir factura")
            btn_guardar.setFixedSize(180, 44)
            btn_guardar.setCursor(Qt.PointingHandCursor)
            btn_guardar.setStyleSheet("""
                QPushButton {
                    background-color: #16a34a;
                    color: white;
                    font-weight: bold;
                    border-radius: 6px;
                    font-size: 10.5pt;
                }
                QPushButton:hover {
                    background-color: #15803d;
                }
            """)
            btn_guardar.clicked.connect(self.guardar_factura)
            botones.addWidget(btn_guardar)

        # Otros botones
        btn_imprimir = QPushButton("🖨️ Imprimir factura")
        btn_enviar = QPushButton("📤 Enviar a SAE")
        btn_cerrar = QPushButton("❌ Cerrar")

        for btn, color in [
            (btn_imprimir, "#4f46e5"),
            (btn_enviar, "#0ea5e9"),
            (btn_cerrar, "#dc2626"),
        ]:
            btn.setFixedSize(180, 44)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color};
                    color: white;
                    font-weight: bold;
                    border-radius: 6px;
                    font-size: 10.5pt;
                }}
                QPushButton:hover {{
                    background-color: {self._oscurecer_color(color, 0.9)};
                }}
            """)

        btn_cerrar.clicked.connect(self.close)
        btn_imprimir.clicked.connect(self.imprimir_factura)
        btn_enviar.clicked.connect(self.enviar_a_sae)

        botones.addWidget(btn_imprimir)
        botones.addWidget(btn_enviar)
        botones.addWidget(btn_cerrar)
        botones.addStretch(1)
        layout.addLayout(botones)
    
    # ==========================================================
    # 🔧 Oscurecer color (hover)
    # ==========================================================
    def _oscurecer_color(self, hex_color, factor):
        hex_color = hex_color.lstrip("#")
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        r = int(r * factor)
        g = int(g * factor)
        b = int(b * factor)
        return f"#{r:02x}{g:02x}{b:02x}"

    # ==========================================================
    # 📤 Simula envío a SAE
    # ==========================================================
    def enviar_a_sae(self):
        from PyQt5.QtWidgets import QMessageBox
        import requests

        try:
            if not hasattr(self, "datos_factura") or not self.datos_factura:
                QMessageBox.warning(self, "Error", "No hay factura para enviar a SAE.")
                return

            datos = self.datos_factura

            print("\n========== DATOS FACTURA (DEBUG) ==========")
            print(datos)
            print("============================================\n")

            # 🔹 Construir payload para PEDIDO SAE
            payload = {
                "folio": datetime.now().strftime("P%y%m%d%H%M%S"),
                "cliente": datos.get("cliente_numero"),
                "vendedor": datos.get("vendedor"),
                "empresa": datos.get("empresa"),
                "productos": datos.get("productos")
            }

            if not payload["cliente"]:
                QMessageBox.warning(self, "Error", "El número de cliente es inválido.")
                return

            # 🔹 Enviar a FastAPI → crear PEDIDO
            resp = requests.post(
                "http://127.0.0.1:8000/sae/pedido/crear",
                json=payload,
                timeout=10
            )

            r = resp.json()
            print("📥 RESPUESTA API SAE:", r)

            # 🔹 Manejar respuesta SAE
            if r.get("estatus") == "ok":
                QMessageBox.information(
                    self,
                    "SAE",
                    f"Pedido generado correctamente.\nFolio SAE: {r.get('folio_pedido')}"
                )
            else:
                QMessageBox.warning(
                    self,
                    "SAE",
                    "Error al generar pedido:\n" + r.get("detalle", "Error desconocido")
                )

        except Exception as e:
            QMessageBox.critical(self, "Error SAE", f"No se pudo enviar a SAE:\n{e}")

    # ==========================================================
    # 🖨️ Imprimir factura (con selección y Ghostscript)
    # ==========================================================
    def imprimir_factura(self):
        """Permite seleccionar impresora, recordar la última usada y enviar la factura sin abrir Acrobat."""
        try:
            # --- Ventana de selección de impresora ---
            dialog = QDialog(self)
            dialog.setWindowTitle("Seleccionar impresora")
            dialog.setFixedSize(400, 180)
            layout = QVBoxLayout(dialog)

            label = QLabel("Selecciona una impresora:")
            layout.addWidget(label)

            combo = QComboBox()
            impresoras = [p.printerName() for p in QPrinterInfo.availablePrinters()]
            if not impresoras:
                QMessageBox.warning(self, "Sin impresoras", "No se detectaron impresoras instaladas.")
                return
            combo.addItems(impresoras)

            # --- Cargar última impresora usada ---
            ultima = None
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        ultima = json.load(f).get("ultima_impresora")
                except Exception:
                    pass

            if ultima and ultima in impresoras:
                combo.setCurrentIndex(combo.findText(ultima))

            layout.addWidget(combo)

            # --- Botones ---
            btn_directo = QPushButton("🖨️ Imprimir directo (silencioso)")
            btn_avanzado = QPushButton("⚙️ Opciones avanzadas")
            layout.addWidget(btn_directo)
            layout.addWidget(btn_avanzado)

            seleccion = {"modo": None}
            btn_directo.clicked.connect(lambda: (seleccion.update({"modo": "directo"}), dialog.accept()))
            btn_avanzado.clicked.connect(lambda: (seleccion.update({"modo": "avanzado"}), dialog.accept()))

            if dialog.exec_() != QDialog.Accepted:
                return

            impresora_seleccionada = combo.currentText()
            modo = seleccion["modo"]

            # --- Guardar la última impresora usada ---
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"ultima_impresora": impresora_seleccionada}, f, ensure_ascii=False, indent=2)

            # --- Imprimir ---
            if modo == "avanzado":
                printer = QPrinter(QPrinter.HighResolution)
                printer.setPrinterName(impresora_seleccionada)
                dialogo = QPrintDialog(printer, self)
                dialogo.setWindowTitle("Opciones de impresión")
                if dialogo.exec_() == QDialog.Accepted:
                    self.viewer.page().print(printer, lambda ok: None)
            else:
                self.imprimir_factura_silenciosa(impresora_seleccionada)

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ==========================================================
    # 🖨️ Imprimir sin abrir Acrobat (Ghostscript)
    # ==========================================================
    def imprimir_factura_silenciosa(self, printer_name):
        import subprocess
        try:
            pdf_path = self.pdf_path
            if not os.path.exists(pdf_path):
                QMessageBox.critical(self, "Error", "El archivo PDF no existe.")
                return

            # --- Buscar Ghostscript automáticamente ---
            posibles_rutas = [
                r"C:\Program Files\gs\gs10.06.0\bin\gswin64c.exe",  # 🆕 versión más reciente
                r"C:\Program Files\gs\gs10.03.1\bin\gswin64c.exe",
                r"C:\Program Files\gs\gs10.02.1\bin\gswin64c.exe",
                r"C:\Program Files\gs\gs10.01.2\bin\gswin64c.exe",
                r"C:\Program Files\gs\gs9.56.1\bin\gswin64c.exe",
            ]
            gs_path = next((ruta for ruta in posibles_rutas if os.path.exists(ruta)), None)

            if not gs_path:
                QMessageBox.critical(
                    self,
                    "Ghostscript no encontrado",
                    "Instala Ghostscript desde https://ghostscript.com/releases/gsdnld.html\n"
                    "o verifica su ruta en el código."
                )
                return

            # --- Detectar tamaño del papel (Letter o A4) ---
            try:
                import win32print
                printer_info = win32print.GetPrinter(win32print.OpenPrinter(printer_name), 2)
                paper_size = "letter" if "Letter" in str(printer_info["pDevMode"].PaperSize).lower() else "a4"
            except Exception:
                paper_size = "letter"

            if paper_size == "letter":
                device_width, device_height = 612, 792  # 8.5x11 pulgadas
            else:
                device_width, device_height = 595, 842  # A4

            print(f"🖨️ Imprimiendo '{pdf_path}' en '{printer_name}' (papel {paper_size.upper()}) usando Ghostscript en '{gs_path}'...")

            # --- Comando Ghostscript ---
            comando = [
                gs_path,
                "-dBATCH",
                "-dNOPAUSE",
                "-dNOSAFER",
                "-dPDFFitPage",
                "-dFIXEDMEDIA",
                "-dAutoRotatePages=/None",
                "-dFitPage",
                "-dCenterPages",  # 🔹 centra el contenido en caso de desplazamiento
                f"-sPAPERSIZE={paper_size}",
                f"-dDEVICEWIDTHPOINTS={device_width}",
                f"-dDEVICEHEIGHTPOINTS={device_height}",
                "-dMarginTop=18",   # 0.25"
                "-dMarginBottom=18",
                "-dMarginLeft=18",
                "-dMarginRight=18",
                "-sDEVICE=mswinpr2",
                f"-sOutputFile=%printer%{printer_name}",
                pdf_path,
            ]

            # --- Ejecutar sin mostrar consola ---
            subprocess.run(comando, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            QMessageBox.information(
                self, "Impresión completada",
                f"Factura enviada correctamente a la impresora:\n\n🖨️ {printer_name}\n📄 ({paper_size.upper()})"
            )

        except Exception as e:
            QMessageBox.critical(self, "Error al imprimir", str(e))
    
    
    def guardar_factura(self):
        """Guarda factura NUEVA usando la estructura REAL de la tabla facturas en comandas_db."""
        from datetime import datetime
        import mysql.connector

        padre = self.parent()
        if padre is None:
            QMessageBox.critical(self, "Error", "No se encontró la ventana principal.")
            return

        datos = self.datos_factura  # payload completo generado en vista previa

        def to_float(x):
            try:
                return float(str(x).replace("$", "").replace(",", "").strip())
            except:
                return 0

        def to_int(x):
            try:
                return int(round(to_float(x)))
            except:
                return 0

        try:
            # =========================================
            # 1) Datos reales del payload
            # =========================================
            factura_numero    = datos.get("factura") or datos.get("folio")
            numero_cliente    = datos.get("numero_cliente")
            cliente_nombre    = datos.get("cliente_nombre")
            empresa           = datos.get("empresa")
            subtotal          = to_float(datos.get("subtotal"))
            descuento_total   = to_float(datos.get("descuento"))
            descuento_pct     = to_float(datos.get("descuento_pct"))
            iva_total         = to_float(datos.get("iva"))
            total             = to_float(datos.get("total"))

            consignatario     = datos.get("consignatario") or cliente_nombre

            # =========================================
            # 2) Obtener tabla REAL de facturación
            # =========================================
            tabla = padre.tabla

            # =========================================
            # 3) CONEXIÓN a comandas_db
            # =========================================
            conn = mysql.connector.connect(
                host="192.168.1.105",
                user="Facturacion",
                password="ALD2013*",
                database="comandas_db",
                port=3306
            )
            cursor = conn.cursor()

            # =========================================
            # 4) INSERT ENCABEZADO usando nombres reales
            # =========================================
            cursor.execute("""
                INSERT INTO facturas
                (fecha, numero_cliente, consignatario, factura, subtotal, descuento_pct,
                descuento, iva, total, sae_codigo, estatus, empresa)
                VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, NULL, 'Activa', %s)
            """, (
                numero_cliente,
                consignatario,
                factura_numero,
                subtotal,
                descuento_pct,
                descuento_total,
                iva_total,
                total,
                empresa
            ))

            factura_id = cursor.lastrowid

            # =========================================
            # 5) INSERT DETALLE
            # =========================================
            for fila in range(tabla.rowCount()):
                it_cip  = tabla.item(fila, 0)
                it_desc = tabla.item(fila, 1)

                if not it_cip or not it_desc:
                    continue

                cip = it_cip.text().strip()
                descripcion = it_desc.text().strip()

                if not descripcion:
                    continue

                cantidad = to_float(tabla.item(fila, 2).text() if tabla.item(fila, 2) else "0")
                piezas   = to_int(tabla.item(fila, 3).text() if tabla.item(fila, 3) else "0")

                precio_real = to_float(tabla.item(fila, 5).text() if tabla.item(fila, 5) else "0")
                otro_precio = to_float(tabla.item(fila, 7).text() if tabla.item(fila, 7) else "0")

                precio = otro_precio if otro_precio > 0 else precio_real

                cursor.execute("""
                    INSERT INTO factura_detalle
                    (factura_id, cip, descripcion, cantidad, piezas, precio)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    factura_id, cip, descripcion, cantidad, piezas, precio
                ))

            conn.commit()

            # =========================================
            # 6) Aviso y actualización de folio
            # =========================================
            QMessageBox.information(self, "Factura guardada",
                                    f"Factura {factura_numero} guardada correctamente.")

            padre.input_folio.setText(padre.incrementar_folio(factura_numero))

            # =========================================
            # 7) Reset ventana principal
            # =========================================
            try:
                padre.modo_edicion = False
                if hasattr(padre, "limpiar_todo"):
                    padre.limpiar_todo()
            except Exception as e:
                print("⚠ No se pudo limpiar ventana:", e)

            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            try:
                conn.rollback()
            except:
                pass
        finally:
            try:
                cursor.close()
                conn.close()
            except:
                pass
            

    def guardar_cambios(self):
        """Actualiza la factura existente en modo edición."""
        if not self.datos_factura:
            QMessageBox.warning(self, "Aviso", "No hay datos de factura para actualizar.")
            return

        folio = self.datos_factura.get("folio", "")
        if not folio:
            QMessageBox.warning(self, "Aviso", "Folio no válido.")
            return

        import requests
        from cliente import API_URL

        try:
            resp = requests.put(f"{API_URL}/facturas/folio/{folio}", json=self.datos_factura)

            if resp.status_code == 200:
                QMessageBox.information(
                    self,
                    "Éxito",
                    f"✅ Factura {folio} actualizada correctamente."
                )

                # 🔥 IMPORTANTE: limpiar ventana principal después de guardar
                padre = self.parent()
                if padre:
                    padre.modo_edicion = False

                    # Si existe el método "limpiar_todo", lo usamos
                    if hasattr(padre, "limpiar_todo"):
                        padre.limpiar_todo()

                self.accept()  # ← Cierra la ventana correctamente

            else:
                QMessageBox.warning(self, "Error", f"No se pudo actualizar la factura:\n\n{resp.text}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Ocurrió un error al actualizar la factura:\n{e}")





class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sistema ERP - Aspel Style")
        self.resize(1450, 850)

        # === Layout principal ===
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(0)

        # === BARRA DE PESTAÑAS ESTILO ASPEL ===
        self.tabs = QTabWidget()
        self.tabs.setIconSize(QSize(20, 20))
        self.tabs.setTabPosition(QTabWidget.North)
        self.tabs.setElideMode(Qt.ElideNone)
        self.tabs.setMovable(False)
        self.tabs.setDocumentMode(True)

        # === Estilo visual tipo cinta de Aspel SAE ===
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: none;
                background-color: #e7ebf2;
                border-top: 2px solid #a9b7c7;
            }

            QTabBar::tab {
                background-color: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #dee6f1, stop:1 #cfd8e3
                );
                color: #1f1f1f;
                padding: 8px 18px;
                margin-right: 2px;
                border: 1px solid #b0bccb;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 500;
            }

            QTabBar::tab:selected {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #b0bccb;
                border-bottom: none;
                font-weight: bold;
            }

            QTabBar::tab:hover {
                background-color: #e4ebf5;
            }

            QTabBar::tab:!selected {
                margin-top: 2px;
            }
        """)

        # === Íconos MDI + Texto ===
        self.tabs.addTab(ClientesWidget(), qta.icon("mdi.account-group-outline"), "Clientes")
        self.tabs.addTab(ProductosTab(), qta.icon("mdi.package-variant-closed"), "Productos")
        self.tabs.addTab(QWidget(), qta.icon("mdi.file-document-outline"), "Facturación")

        # === Ajuste dinámico de ancho ===
        tab_bar = self.tabs.tabBar()
        tab_bar.setExpanding(False)
        tab_bar.setUsesScrollButtons(False)
        tab_bar.setDrawBase(False)
        tab_bar.setIconSize(QSize(20, 20))

        # === Configuración del layout principal ===
        layout.addWidget(self.tabs)
        self.setLayout(layout)

        # === Aplicar cursor de mano a todos los botones ===
        for boton in self.findChildren(QPushButton):
            boton.setCursor(Qt.PointingHandCursor)

from tema_global import aplicar_tema_aspel

# === Configuración del tema recordado ===
def cargar_config():
    if os.path.exists("config.json"):
        try:
            with open("config.json", "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"tema": "claro"}


def guardar_config(data):
    with open("config.json", "w") as f:
        json.dump(data, f)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sistema ERP - Aspel Style")
        self.resize(1280, 800)

        # === TABS PRINCIPALES ===
        self.tabs = QTabWidget()
        self.tabs.setIconSize(QSize(22, 22))
        self.tabs.setTabPosition(QTabWidget.North)
        self.tabs.setElideMode(Qt.ElideNone)
        self.tabs.setMovable(False)
        self.tabs.setDocumentMode(True)

        # === ESTILO VISUAL TIPO ASPEL RIBBON ===
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: none;
                background-color: #e7ebf2;
                border-top: 2px solid #a9b7c7;
            }
            QTabBar::tab {
                background-color: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #dee6f1, stop:1 #cfd8e3
                );
                color: #1f1f1f;
                padding: 8px 18px;
                margin-right: 2px;
                border: 1px solid #b0bccb;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 500;
            }
            QTabBar::tab:selected {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #b0bccb;
                border-bottom: none;
                font-weight: bold;
            }
            QTabBar::tab:hover {
                background-color: #e4ebf5;
            }
            QTabBar::tab:!selected {
                margin-top: 2px;
            }
        """)

        # === PESTAÑAS CON ÍCONOS MDI ===
        self.tabs.addTab(FacturacionTab(), qta.icon("mdi.file-document-outline"), "Facturación")

        # ✅ Guardamos una referencia a la pestaña "Mio"
        self.mio_tab = VentanaMio()
        self.tabs.addTab(self.mio_tab, qta.icon("mdi.chart-line"), "Mio")

        # Otras pestañas
        self.tabs.addTab(ClientesWidget(), qta.icon("mdi.account-group-outline"), "Clientes")
        self.tabs.addTab(ProductosTab(), qta.icon("mdi.package-variant-closed"), "Productos")
        self.tabs.addTab(VentanaReportes(self.mio_tab), qta.icon("mdi.chart-areaspline"), "Reportes")



        # Ajuste automático del ancho según texto + ícono
        tab_bar = self.tabs.tabBar()
        tab_bar.setExpanding(False)
        tab_bar.setUsesScrollButtons(False)
        tab_bar.setDrawBase(False)
        tab_bar.setIconSize(QSize(22, 22))

        self.setCentralWidget(self.tabs)

        # === BOTÓN DE ENGRANE (CAMBIO DE TEMA) ===
        self.btn_tema = QPushButton()
        self.btn_tema.setIcon(qta.icon("mdi.cog-outline", color="#4a90e2"))
        self.btn_tema.setIconSize(QSize(24, 24))
        self.btn_tema.setFixedSize(36, 36)
        self.btn_tema.setStyleSheet("""
            QPushButton {
                border: none;
                background: transparent;
                margin: 5px;
            }
            QPushButton:hover {
                background-color: rgba(74, 144, 226, 0.15);
                border-radius: 6px;
            }
        """)
        self.btn_tema.setCursor(Qt.PointingHandCursor)
        self.btn_tema.clicked.connect(self.mostrar_menu_tema)

        # === POSICIÓN DEL ENGRANE ARRIBA A LA DERECHA ===
        self.menu_widget = QWidget(self)
        self.menu_widget.setFixedHeight(40)
        layout_menu = QHBoxLayout(self.menu_widget)
        layout_menu.setContentsMargins(0, 0, 10, 0)
        layout_menu.addStretch()
        layout_menu.addWidget(self.btn_tema)
        self.setMenuWidget(self.menu_widget)

    # ===========================
    # 🔹 Menú de selección de tema
    # ===========================
    def mostrar_menu_tema(self):
        menu = QMenu()
        config = cargar_config()
        tema_actual = config.get("tema", "claro")

        if tema_actual == "oscuro":
            menu.setStyleSheet("""
                QMenu {
                    background-color: #2b2b3c;
                    border: 1px solid #555;
                    padding: 5px;
                }
                QMenu::item {
                    color: #e0e0e0;
                    padding: 6px 20px;
                }
                QMenu::item:selected {
                    background-color: #4a90e2;
                    color: white;
                }
            """)
        else:
            menu.setStyleSheet("""
                QMenu {
                    background-color: #ffffff;
                    border: 1px solid #c0c0c0;
                    padding: 5px;
                }
                QMenu::item {
                    color: #1f1f1f;
                    padding: 6px 20px;
                }
                QMenu::item:selected {
                    background-color: #4a90e2;
                    color: white;
                }
            """)

        accion_claro = QAction("Modo Claro", self)
        accion_oscuro = QAction("Modo Oscuro", self)
        menu.addAction(accion_claro)
        menu.addAction(accion_oscuro)

        accion_claro.triggered.connect(lambda: self.cambiar_tema("claro"))
        accion_oscuro.triggered.connect(lambda: self.cambiar_tema("oscuro"))

        pos = self.btn_tema.mapToGlobal(QPoint(0, self.btn_tema.height()))
        menu.exec_(pos)

    def cambiar_tema(self, modo):
        aplicar_tema_aspel(QApplication.instance(), modo)
        guardar_config({"tema": modo})
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            # 🔹 Recalcular tamaños proporcionales
            ancho_total = self.width()

            # Ajustar ancho de botones dinámicamente
            for btn in self.findChildren(QPushButton):
                btn.setFixedHeight(36)
                btn.setMinimumWidth(140)
                btn.setMaximumWidth(int(ancho_total * 0.15))  # máximo 15% del ancho

            # Ajustar tabla congelada si existe
            if hasattr(self, "tabla_fija"):
                QTimer.singleShot(100, self.sync_row_heights)
        except Exception as e:
            print("Error en resizeEvent:", e)


import subprocess, os, time, requests

API_URL = "http://127.0.0.1:8000"

def iniciar_api_silenciosa():
    ruta_api = r"C:\AspelAPI\main.exe"

    # 1️⃣ Verificar si la API ya está activa
    try:
        r = requests.get(f"{API_URL}/test", timeout=1)
        if r.status_code == 200:
            print("API ya está arriba.")
            return
    except:
        pass

    # 2️⃣ Iniciar API si no está activa
    try:
        subprocess.Popen(
            [ruta_api],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        print("API iniciada en segundo plano...")
    except Exception as e:
        print("❌ No se pudo iniciar la API:", e)
        return

    # 3️⃣ Esperar hasta que realmente esté activa
    for _ in range(20):  # hasta 10 segundos
        time.sleep(0.5)
        try:
            r = requests.get(f"{API_URL}/test", timeout=1)
            if r.status_code == 200:
                print("API está lista.")
                return
        except:
            pass

    print("⚠️ La API no respondió después de iniciar main.exe")

from api_launcher import asegurar_api_activa
import sys
from PyQt5.QtWidgets import QApplication

# ===========================
# 🔹 Punto de entrada
# ===========================
if __name__ == "__main__":

    # 🔥 Antes de iniciar la interfaz PyQt
    if not asegurar_api_activa():
        print("❌ No se pudo iniciar la API. Cerrando cliente...")
        sys.exit(1)

    # 🔥 Ahora sí iniciar PyQt
    app = QApplication(sys.argv)

    config = cargar_config()
    aplicar_tema_aspel(app, modo=config.get("tema", "claro"))  # por defecto claro

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())