import sys
from datetime import datetime

import mysql.connector
import pandas as pd

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QFormLayout, QMessageBox, QFileDialog, QPushButton, QGridLayout,
    QScrollArea
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter
from PyQt5.QtPrintSupport import QPrinter

import matplotlib
matplotlib.use("Qt5Agg")
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import matplotlib.pyplot as plt
import numpy as np
from PyQt5.QtWidgets import QSizePolicy


# ============================================================
# 🔌 Utilidades de conexión
# ============================================================

def get_connection():
    return mysql.connector.connect(
        host="192.168.1.105",
        user="Facturacion",
        password="ALD2013*",
        database="comandas_db",
        port=3306
    )


def cargar_datos_cliente(numero_cliente):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        # ============================
        # 1) Datos del cliente
        # ============================
        cursor.execute("""
            SELECT 
                c.numero,
                c.nombre,
                c.razon_social,
                c.rfc,
                c.telefono,
                c.poblacion,
                c.estado,
                c.zona,
                c.dias_credito,
                c.agente
            FROM clientes c
            WHERE c.numero = %s
        """, (numero_cliente,))
        cliente_row = cursor.fetchone()

        if not cliente_row:
            cliente_row = {"numero": numero_cliente, "nombre": f"Cliente {numero_cliente}"}

        # ============================
        # 2) FACTURAS (CORRECTAS)
        # ============================
        cursor.execute("""
            SELECT
                f.id,
                f.factura,
                f.numero_cliente AS cliente,
                f.total,
                f.fecha,
                c.nombre AS tienda,
                f.empresa,
                f.estatus
            FROM facturas f
            LEFT JOIN clientes c ON c.numero = f.numero_cliente
            WHERE f.numero_cliente = %s
            ORDER BY f.fecha DESC
        """, (numero_cliente,))
        facturas = cursor.fetchall()
        df_facturas = pd.DataFrame(facturas)

        if not df_facturas.empty:
            df_facturas["fecha"] = pd.to_datetime(df_facturas["fecha"], errors="coerce")
            df_facturas["mes_num"] = df_facturas["fecha"].dt.month

        # ============================
        # 3) PRODUCTOS (CORRECTOS)
        # ============================
        cursor.execute("""
            SELECT 
                f.factura,
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
            LEFT JOIN clientes c ON c.numero = f.numero_cliente
            WHERE f.estatus = 'Activa'
              AND f.numero_cliente = %s
              AND d.descripcion IS NOT NULL
              AND TRIM(d.descripcion) <> ''
        """, (numero_cliente,))
        productos = cursor.fetchall()
        df_productos = pd.DataFrame(productos)

        if not df_productos.empty:
            df_productos["fecha"] = pd.to_datetime(df_productos["fecha"], errors="coerce")

        return cliente_row, df_facturas, df_productos

    except Exception as e:
        print("❌ Error:", e)
        return {}, pd.DataFrame(), pd.DataFrame()

    finally:
        if conn:
            conn.close()

# ============================================================
# 🎨 GraficoCanvas
# ============================================================

class GraficoCanvas(FigureCanvas):
    def __init__(self, parent=None):
        fig = plt.Figure(figsize=(7, 3))
        super().__init__(fig)
        self.setParent(parent)

        self.ax = fig.add_subplot(111)
        fig.subplots_adjust(left=0.20, right=0.95, top=0.90, bottom=0.15)


# ============================================================
#  WIDGET PRINCIPAL — DETALLE CLIENTE
# ============================================================

class DetalleClienteWidget(QWidget):
    def __init__(self, numero_cliente, empresa_filtro="Todas"):
        super().__init__()

        self.numero_cliente = str(numero_cliente)
        self.empresa_filtro = empresa_filtro   # ← SOPORTA FILTRO EMPRESA (Opción B)

        # --------------------------------------------------------
        # 🔹 Scroll general del widget
        # --------------------------------------------------------
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)

        scroll_content = QWidget()
        self.main_layout = QVBoxLayout(scroll_content)
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        self.main_layout.setSpacing(20)

        scroll_area.setWidget(scroll_content)

        container = QVBoxLayout(self)
        container.addWidget(scroll_area)

        # --------------------------------------------------------
        # 🔹 Título principal
        # --------------------------------------------------------
        titulo = QLabel(f"Reporte del cliente: {self.numero_cliente}")
        titulo.setAlignment(Qt.AlignCenter)
        titulo.setStyleSheet("font-size: 17pt; font-weight: bold; margin: 5px;")
        self.main_layout.addWidget(titulo)

        # --------------------------------------------------------
        # 🔹 Pestañas internas
        # --------------------------------------------------------
        self.tabs = QTabWidget()
        self.main_layout.addWidget(self.tabs)

        self.tab_resumen = QWidget()
        self.tab_productos = QWidget()
        self.tab_graficos = QWidget()
        self.tab_facturas = QWidget()

        self.tabs.addTab(self.tab_resumen, "Resumen")
        self.tabs.addTab(self.tab_productos, "Productos")
        self.tabs.addTab(self.tab_graficos, "Gráficos")
        self.tabs.addTab(self.tab_facturas, "Facturas")

        # Inicializar pestañas (scroll por pestaña)
        self._init_tab_resumen()
        self._init_tab_productos()
        self._init_tab_graficos()
        self._init_tab_facturas()

        # Cargar datos reales desde MySQL
        self.datos_cliente, self.df_facturas, self.df_productos = cargar_datos_cliente(
            self.numero_cliente
        )

        # Llenar pestañas
        self._cargar_resumen()
        self._cargar_productos()
        self._dibujar_graficos()
        self._cargar_facturas()
    
    # ============================================================
    #   PESTAÑA: RESUMEN (con scroll propio)
    # ============================================================
    def _init_tab_resumen(self):
        scroll = QScrollArea(self.tab_resumen)
        scroll.setWidgetResizable(True)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(12)
        scroll.setWidget(content)

        main_layout = QVBoxLayout(self.tab_resumen)
        main_layout.addWidget(scroll)

        # ====== TÍTULO DEL RESUMEN ======
        lbl_section = QLabel("Información del cliente")
        lbl_section.setStyleSheet("font-size: 14pt; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(lbl_section)

        # ====== FORM DATOS DEL CLIENTE ======
        self.form_cliente = QFormLayout()
        self.form_cliente.setLabelAlignment(Qt.AlignRight)
        layout.addLayout(self.form_cliente)

        layout.addSpacing(15)

        # ====== KPIs ======
        kpi_widget = QWidget()
        kpi_layout = QHBoxLayout(kpi_widget)
        kpi_layout.setSpacing(25)

        self.lbl_total_compras = QLabel("Total compras: $0")
        self.lbl_total_compras.setStyleSheet("font-size: 12pt; font-weight: bold;")

        self.lbl_facturas = QLabel("Facturas: 0")
        self.lbl_facturas.setStyleSheet("font-size: 12pt; font-weight: bold;")

        self.lbl_ultima_compra = QLabel("Última compra: -")
        self.lbl_ultima_compra.setStyleSheet("font-size: 12pt; font-weight: bold;")

        kpi_layout.addWidget(self.lbl_total_compras)
        kpi_layout.addWidget(self.lbl_facturas)
        kpi_layout.addWidget(self.lbl_ultima_compra)
        kpi_layout.addStretch()

        layout.addWidget(kpi_widget)
        layout.addStretch()


    # ============================================================
    #   PESTAÑA: PRODUCTOS (con scroll propio)
    # ============================================================
    def _init_tab_productos(self):
        scroll = QScrollArea(self.tab_productos)
        scroll.setWidgetResizable(True)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)
        scroll.setWidget(content)

        main_layout = QVBoxLayout(self.tab_productos)
        main_layout.addWidget(scroll)

        # ====== KPI PRODUCTOS ======
        self.lbl_kpi_prod_total = QLabel("Monto total en productos: $0")
        self.lbl_kpi_prod_total.setStyleSheet("font-size: 12pt; font-weight: bold;")
        layout.addWidget(self.lbl_kpi_prod_total)

        # ====== TABLA DE PRODUCTOS ======
        self.tabla_productos = QTableWidget()
        self.tabla_productos.setColumnCount(5)
        self.tabla_productos.setHorizontalHeaderLabels(
            ["Producto", "Piezas", "Cantidad", "Precio", "Total"]
        )
        self.tabla_productos.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.tabla_productos)

        layout.addSpacing(20)

        # ====== GRÁFICO TOP PRODUCTOS ======
        fig, ax = plt.subplots(figsize=(6, 4))
        self.canvas_top_prod = FigureCanvas(fig)
        self.ax_top_prod = ax

        graf_container = QWidget()
        graf_layout = QHBoxLayout(graf_container)
        graf_layout.setAlignment(Qt.AlignCenter)
        graf_layout.addWidget(self.canvas_top_prod)

        layout.addWidget(graf_container)
        layout.addStretch()


    # ============================================================
    #   LLENADO DE DATOS — RESUMEN
    # ============================================================
    def _cargar_resumen(self):
        """Llena la pestaña Resumen con los datos del cliente."""
        if self.df_facturas.empty:
            return

        # --- Datos del cliente ---
        c = self.datos_cliente
        mapping = [
            ("Número:", c.get("numero")),
            ("Nombre:", c.get("nombre")),
            ("Razón social:", c.get("razon_social")),
            ("RFC:", c.get("rfc")),
            ("Teléfono:", c.get("telefono")),
            ("Población:", c.get("poblacion")),
            ("Estado:", c.get("estado")),
            ("Zona:", c.get("zona")),
            ("Días de crédito:", c.get("dias_credito")),
            ("Agente:", c.get("agente")),
        ]

        # Limpiar form
        while self.form_cliente.rowCount() > 0:
            self.form_cliente.removeRow(0)

        for label, value in mapping:
            self.form_cliente.addRow(label, QLabel(str(value if value is not None else "")))

        # --- KPIs ---
        total = self.df_facturas["total"].sum()
        ult = self.df_facturas["fecha"].max()
        facturas = len(self.df_facturas)

        self.lbl_total_compras.setText(f"Total compras: ${total:,.2f}")
        self.lbl_facturas.setText(f"Facturas: {facturas}")
        self.lbl_ultima_compra.setText(f"Última compra: {ult.date() if pd.notnull(ult) else '-'}")


    # ============================================================
    #   LLENADO DE DATOS — PRODUCTOS
    # ============================================================
    def _cargar_productos(self):
        if self.df_productos.empty:
            return

        df = self.df_productos.copy()
        df["monto"] = df["cantidad"] * df["precio"]

        self.tabla_productos.setRowCount(len(df))

        for i, fila in df.iterrows():
            self.tabla_productos.setItem(i, 0, QTableWidgetItem(str(fila["producto"])))
            self.tabla_productos.setItem(i, 1, QTableWidgetItem(str(fila.get("piezas", ""))))
            self.tabla_productos.setItem(i, 2, QTableWidgetItem(str(fila.get("cantidad", ""))))
            self.tabla_productos.setItem(i, 3, QTableWidgetItem(str(fila.get("precio", ""))))
            self.tabla_productos.setItem(i, 4, QTableWidgetItem(f"{fila['monto']:,.2f}"))

        self.lbl_kpi_prod_total.setText(
            f"Monto total en productos: ${df['monto'].sum():,.2f}"
        )

        # ---- Gráfico ----
        self.ax_top_prod.clear()

        top = (
            df.groupby("producto")["monto"]
            .sum()
            .sort_values(ascending=False)
            .head(10)
        )

        top.plot(kind="barh", ax=self.ax_top_prod, color="#4A90E2")
        self.ax_top_prod.set_title("Top productos por monto")
        self.ax_top_prod.grid(axis="x", linestyle="--", alpha=0.5)

        self.canvas_top_prod.draw()
    
    # ============================================================
    #   PESTAÑA: GRÁFICOS (con scroll propio)
    # ============================================================
    def _init_tab_graficos(self):
        scroll = QScrollArea(self.tab_graficos)
        scroll.setWidgetResizable(True)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(20)
        scroll.setWidget(content)

        main_layout = QVBoxLayout(self.tab_graficos)
        main_layout.addWidget(scroll)

        # ====== Gráfico: Evolución de ventas ======
        fig1, ax1 = plt.subplots(figsize=(7, 4))
        self.canvas_hist = FigureCanvas(fig1)
        self.ax_hist = ax1
        layout.addWidget(self.canvas_hist)

        # ====== Gráfico: Frecuencia de compras ======
        fig2, ax2 = plt.subplots(figsize=(7, 4))
        self.canvas_freq = FigureCanvas(fig2)
        self.ax_freq = ax2
        layout.addWidget(self.canvas_freq)

        layout.addStretch()


    # ============================================================
    #   GRÁFICOS — EVOLUCIÓN Y FRECUENCIA
    # ============================================================
    def _dibujar_graficos(self):
        if self.df_facturas.empty:
            return

        df = self.df_facturas.copy()
        df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")

        # ===== DEBUG: revisar qué está llegando en TOTAL =====
        print("VALORES CRUDOS DE total:", df["total"].head(10))
        print("TIPOS:", df["total"].apply(type).unique())

        # -----------------------------------------------------------
        # 🔹 Gráfico 1: Evolución de ventas (TOTAL FACTURAS POR MES)
        # -----------------------------------------------------------
        self.ax_hist.clear()
        

        # -----------------------------------------------------------
        # Asegurar que TOTAL es numérico REAL
        # -----------------------------------------------------------
        df["total"] = (
            df["total"]
            .astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("$", "", regex=False)
            .str.replace(" ", "", regex=False)
        )

        # Convertir a Float (errores -> NaN)
        df["total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0)

        # Si después de limpiar todo es cero → no graficar
        if df["total"].sum() == 0:
            self.ax_hist.clear()
            self.ax_hist.text(
                0.5, 0.5, "Sin datos numéricos para graficar",
                ha="center", va="center", fontsize=12
            )
            self.canvas_hist.draw()

            self.ax_freq.clear()
            self.ax_freq.text(
                0.5, 0.5, "Sin datos para graficar",
                ha="center", va="center", fontsize=12
            )
            self.canvas_freq.draw()
            return
        
        df_mes = (
            df.groupby(df["fecha"].dt.to_period("M"))["total"]
            .sum()
            .sort_index()
        )
        df_mes.index = df_mes.index.astype(str)

        df_mes.plot(
            ax=self.ax_hist,
            marker="o",
            linewidth=2,
            color="#1565C0"
        )

        self.ax_hist.set_title("Evolución de ventas por mes", fontsize=12, fontweight="bold")
        self.ax_hist.set_xlabel("Periodo")
        self.ax_hist.set_ylabel("Monto ($)")
        self.ax_hist.grid(True, linestyle="--", alpha=0.4)

        self.canvas_hist.draw()

        # -----------------------------------------------------------
        # 🔹 Gráfico 2: Frecuencia (NÚMERO DE FACTURAS POR MES)
        # -----------------------------------------------------------
        self.ax_freq.clear()

        df_freq = (
            df.groupby(df["fecha"].dt.to_period("M"))["factura"]
            .count()
            .sort_index()
        )
        df_freq.index = df_freq.index.astype(str)

        df_freq.plot(
            kind="bar",
            ax=self.ax_freq,
            color="#2E7D32"
        )

        self.ax_freq.set_title("Frecuencia de compras", fontsize=12, fontweight="bold")
        self.ax_freq.set_xlabel("Periodo")
        self.ax_freq.set_ylabel("Número de facturas")

        for label in self.ax_freq.get_xticklabels():
            label.setRotation(45)

        self.canvas_freq.draw()
    # ============================================================
    #   PESTAÑA: FACTURAS (con scroll propio)
    # ============================================================
    def _init_tab_facturas(self):
        scroll = QScrollArea(self.tab_facturas)
        scroll.setWidgetResizable(True)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)
        scroll.setWidget(content)

        main_layout = QVBoxLayout(self.tab_facturas)
        main_layout.addWidget(scroll)

        # ====== TABLA DE FACTURAS ======
        self.tabla_facturas = QTableWidget()
        self.tabla_facturas.setColumnCount(6)
        self.tabla_facturas.setHorizontalHeaderLabels(
            ["Factura", "Empresa", "Fecha", "Estatus", "Producto(s)", "Monto"]
        )
        self.tabla_facturas.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.tabla_facturas)

        layout.addStretch()


    # ============================================================
    #   LLENADO DE DATOS — FACTURAS
    # ============================================================
    def _cargar_facturas(self):
        if self.df_facturas.empty:
            self.tabla_facturas.setRowCount(0)
            return

        df = self.df_facturas.copy()
        df = df.sort_values("fecha")

        self.tabla_facturas.setRowCount(len(df))

        for i, fila in df.iterrows():
            factura = str(fila.get("factura", ""))
            empresa = str(fila.get("empresa", ""))
            fecha = fila.get("fecha")
            fecha_txt = str(fecha.date()) if pd.notnull(fecha) else ""
            estatus = str(fila.get("estatus", ""))
            monto = f"{fila.get('total', 0):,.2f}"

            # Productos relacionados (opcional)
            relacionados = self.df_productos[self.df_productos["factura"] == factura]
            if relacionados.empty:
                productos_txt = "-"
            else:
                productos_txt = ", ".join(relacionados["producto"].astype(str).unique())

            self.tabla_facturas.setItem(i, 0, QTableWidgetItem(factura))
            self.tabla_facturas.setItem(i, 1, QTableWidgetItem(empresa))
            self.tabla_facturas.setItem(i, 2, QTableWidgetItem(fecha_txt))
            self.tabla_facturas.setItem(i, 3, QTableWidgetItem(estatus))
            self.tabla_facturas.setItem(i, 4, QTableWidgetItem(productos_txt))
            self.tabla_facturas.setItem(i, 5, QTableWidgetItem(monto))


    # ============================================================
    #   PARA USO OPCIONAL: INYECTAR DATOS EXTERNOS
    # ============================================================
    def set_data(self, df_facturas, df_productos):
        """
        Permite cargar datos desde VentanaReportes si algún día quieres.
        No se usa actualmente porque tu widget carga directo desde MySQL.
        """
        self.df_facturas = df_facturas.copy()
        self.df_productos = df_productos.copy()

        self._cargar_resumen()
        self._cargar_productos()
        self._dibujar_graficos()
        self._cargar_facturas()

# ============================================================
# 🚀 main (corregido)
# ============================================================
def main():
    if len(sys.argv) < 2:
        print("Uso: py detalle_cliente.py <numero_cliente>")
        sys.exit(1)

    numero_cliente = sys.argv[1]

    app = QApplication(sys.argv)
    win = DetalleClienteWidget(numero_cliente)   # ✔ Aquí estaba el error
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

