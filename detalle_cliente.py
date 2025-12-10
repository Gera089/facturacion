import sys
import pandas as pd
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QHBoxLayout,
    QMessageBox, QTabWidget, QScrollArea, QTableWidget, QHeaderView,
    QTableWidgetItem, QFileDialog, QGridLayout, QFormLayout, QPushButton,
    QSizePolicy, QSpacerItem
)
from PyQt5.QtCore import Qt
from api_clientes import cargar_datos_cliente
from canvas_grafico import GraficoCanvas
import textwrap
import io
import os
import pandas as pd

from PyQt5.QtWidgets import QFileDialog, QMessageBox

from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader

# ============================================================
# 🪟 WIDGET PRINCIPAL
# ============================================================
class DetalleClienteWidget(QWidget):
    def __init__(self, numero_cliente, empresa):
        super().__init__()
        self.numero_cliente = numero_cliente
        self.empresa = empresa

        # --- CARGAR DATOS ---
        try:
            self.cliente_data, self.df_facturas, self.df_productos = cargar_datos_cliente(
                self.numero_cliente, self.empresa
            )

            # Si el servidor devuelve {"detail": "..."}
            if isinstance(self.cliente_data, dict) and "detail" in self.cliente_data:
                raise ValueError(self.cliente_data["detail"])

        except Exception as e:
            # --- Detectar error de cliente inexistente ---
            msj = str(e).lower()

            if "404" in msj or "no encontrado" in msj or "cliente" in msj:
                QMessageBox.information(
                    self,
                    "Sin datos",
                    f"No existen datos para el cliente {self.numero_cliente} en la empresa {self.empresa}."
                )
            else:
                QMessageBox.critical(
                    self,
                    "Error",
                    f"No se pudieron cargar los datos:\n{e}"
                )

            self.cliente_data = {}
            self.df_facturas = pd.DataFrame()
            self.df_productos = pd.DataFrame()
            return

        # --- LAYOUT PRINCIPAL ---
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 0, 10, 0)
        main_layout.setSpacing(4)

        # --- TÍTULO ---
        nombre_cli = self.cliente_data.get("nombre", "")
        if not nombre_cli:
            nombre_cli = "Cliente sin nombre"

        self.lbl_titulo = QLabel(nombre_cli)
        self.lbl_titulo.setAlignment(Qt.AlignCenter)
        self.lbl_titulo.setStyleSheet("""
            font-size: 26pt;
            font-weight: 900;
            color: #111827;
            margin-top: 5px;
            margin-bottom: 0px;
        """)
        main_layout.addWidget(self.lbl_titulo)

        # SUBTÍTULO
        self.lbl_subtitulo = QLabel(f"Cliente {self.numero_cliente} • RFC: {self.cliente_data.get('rfc','N/D')}")
        self.lbl_subtitulo.setAlignment(Qt.AlignCenter)
        self.lbl_subtitulo.setStyleSheet("""
            font-size: 13pt;
            color: #4b5563;
            margin-bottom: 5px;
        """)
        main_layout.addWidget(self.lbl_subtitulo)

        # --- TABS ---
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                background: #f9fafb;
                padding-top: 0px;
            }
            QTabBar::tab {
                background: #e5e7eb;
                color: #111827;
                padding: 10px 24px;
                border-radius: 8px;
                margin-right: 8px;
                font-weight: 600;
                font-size: 11pt;
                min-width: 150px;
            }
            QTabBar::tab:selected {
                background: #2563eb;
                color: white;
            }
        """)
        main_layout.addWidget(self.tabs)

        # --- CREAR PESTAÑAS ---
        self.tab_resumen = self._crear_tab()
        self.tab_productos = self._crear_tab()
        self.tab_graficos = self._crear_tab()
        self.tab_facturas = self._crear_tab()

        self.tabs.addTab(self.tab_resumen["contenedor"], "📌 Resumen")
        self.tabs.addTab(self.tab_productos["contenedor"], "📦 Productos")
        self.tabs.addTab(self.tab_graficos["contenedor"], "📊 Gráficos")
        self.tabs.addTab(self.tab_facturas["contenedor"], "📄 Facturas")

        # INICIALIZAR TABS
        self._init_tab_resumen()
        self._init_tab_productos()
        self._init_tab_graficos()
        self._init_tab_facturas()


    # =====================================================================
    # PESTAÑA: RESUMEN
    # =====================================================================
    def _init_tab_resumen(self):
        layout = self.tab_resumen["layout"]

        # ----------------------------------------------------
        # DATOS DEL CLIENTE (FORM)
        # ----------------------------------------------------
        caja = QWidget()
        form = QFormLayout(caja)
        form.setContentsMargins(0, 0, 0, 0)

        def row(nombre, valor):
            lab = QLabel(valor if valor not in [None, ""] else "-")
            lab.setStyleSheet("font-weight:bold;color:#111827;")
            form.addRow(nombre + ":", lab)

        row("Número", self.numero_cliente)
        row("Nombre", self.cliente_data.get("nombre", ""))
        row("Razón social", self.cliente_data.get("razon_social", ""))
        row("RFC", self.cliente_data.get("rfc", ""))
        row("Ciudad", self.cliente_data.get("poblacion", ""))
        row("Estado", self.cliente_data.get("estado", ""))
        row("Teléfono", self.cliente_data.get("telefono", ""))
        row("Agente", self.cliente_data.get("agente", ""))

        layout.addWidget(caja)

        # ----------------------------------------------------
        # KPI Cálculos
        # ----------------------------------------------------
        total = 0
        activas = 0

        if not self.df_facturas.empty:
            df = self.df_facturas.copy()
            if "fecha" in df.columns:
                try:
                    df["fecha"] = pd.to_datetime(df["fecha"])
                except:
                    pass

            df_activas = df[df["estatus"].str.lower() == "activa"] if "estatus" in df.columns else df
            total = df_activas["total"].sum() if "total" in df_activas.columns else 0
            activas = len(df_activas)

        # ----------------------------------------------------
        # NUEVOS KPIs: Última compra / Último producto
        # ----------------------------------------------------
        ultima_fecha = "-"
        ultimo_producto = "-"

        # Última compra
        if not self.df_facturas.empty:
            df_f = self.df_facturas.copy()
            if "fecha" in df_f.columns:
                try:
                    df_f["fecha"] = pd.to_datetime(df_f["fecha"])
                except:
                    pass

                df_f = df_f.sort_values("fecha", ascending=False)
                try:
                    ultima_fecha = df_f.iloc[0]["fecha"].strftime("%d/%m/%Y")
                except:
                    ultima_fecha = "-"

        # Último producto
        if not self.df_productos.empty:
            df_p = self.df_productos.copy()
            if "fecha" in df_p.columns:
                try:
                    df_p["fecha"] = pd.to_datetime(df_p["fecha"])
                except:
                    pass
                df_p = df_p.sort_values("fecha", ascending=False)

            if "producto" in df_p.columns:
                ultimo_producto = str(df_p.iloc[0]["producto"])

        # ----------------------------------------------------
        # FUNCIÓN PARA CREAR KPI (Aumentado 30%)
        # ----------------------------------------------------
        def kpi(titulo, valor):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setAlignment(Qt.AlignCenter)
            v.setContentsMargins(10, 10, 10, 10)

            lab_t = QLabel(titulo)
            lab_t.setAlignment(Qt.AlignCenter)
            lab_t.setStyleSheet("""
                color: #374151;
                font-size: 12pt;
                font-weight: 700;
            """)

            lab_v = QLabel(valor)
            lab_v.setWordWrap(True)
            lab_v.setAlignment(Qt.AlignCenter)
            lab_v.setStyleSheet("""
                font-size:22pt;
                font-weight:bold;
                color:#2563eb;
            """)

            v.addWidget(lab_t)
            v.addWidget(lab_v)

            w.setStyleSheet("""
                background:white;
                border:1px solid #ddd;
                border-radius:12px;
            """)

            w.setMinimumHeight(130)      # antes 90
            w.setMinimumWidth(380)       # más ancho
            return w

        # ----------------------------------------------------
        # CONTENEDOR GRID DE KPIs (+30% DE ESPACIO)
        # ----------------------------------------------------
        kpi_container = QWidget()
        g = QGridLayout(kpi_container)
        g.setHorizontalSpacing(20)
        g.setVerticalSpacing(20)

        g.addWidget(kpi("Total vendido", f"${total:,.2f}"), 0, 0)
        g.addWidget(kpi("Facturas activas", str(activas)), 0, 1)
        g.addWidget(kpi("Última compra", ultima_fecha), 1, 0)
        g.addWidget(kpi("Último producto", ultimo_producto), 1, 1)

        layout.addWidget(kpi_container)

        # ----------------------------------------------------
        # ESPACIO EXACTO DE 30PX ENTRE KPI Y BOTONES
        # ----------------------------------------------------
        spacer = QSpacerItem(20, 30, QSizePolicy.Minimum, QSizePolicy.Fixed)
        layout.addSpacerItem(spacer)

        # ----------------------------------------------------
        # BOTONES EXPORTAR CORPORATIVOS
        # ----------------------------------------------------
        botones_layout = QHBoxLayout()
        botones_layout.setContentsMargins(0, 0, 0, 0)
        botones_layout.setAlignment(Qt.AlignCenter)
        botones_layout.setSpacing(16)

        self.btn_excel = QPushButton("📊 Exportar a Excel")
        self.btn_pdf = QPushButton("📄 Exportar a PDF")

        estilo_btn = """
        QPushButton {
            background-color: #2563eb;
            color: white;
            font-weight: bold;
            border-radius: 8px;
            padding: 10px 34px;
            font-size: 12pt;
            min-width: 200px;
        }
        QPushButton:hover {
            background-color: #1e40af;
        }
        """

        self.btn_excel.setStyleSheet(estilo_btn)
        self.btn_pdf.setStyleSheet(
            estilo_btn.replace("#2563eb", "#10b981").replace("#1e40af", "#059669")
        )

        # Conexión a las NUEVAS funciones corporativas
        self.btn_excel.clicked.connect(self.exportar_reporte_excel)
        self.btn_pdf.clicked.connect(self.exportar_reporte_pdf)

        botones_layout.addWidget(self.btn_excel)
        botones_layout.addWidget(self.btn_pdf)

        layout.addLayout(botones_layout)

        layout.addStretch()
    # =====================================================================
    # PESTAÑA: PRODUCTOS COMPRADOS POR EL CLIENTE
    # =====================================================================
    def _init_tab_productos(self):
        layout = self.tab_productos["layout"]

        # =============================
        # TABLA DE PRODUCTOS
        # =============================
        tabla = QTableWidget()
        layout.addWidget(tabla)
        self.tab_productos_widget = tabla   # ✔ necesario si después quieres exportar la tabla sola

        if self.df_productos is None or self.df_productos.empty:
            tabla.setRowCount(0)
            tabla.setColumnCount(1)
            tabla.setHorizontalHeaderLabels(["Sin datos de productos"])
        else:
            df = self.df_productos.copy()

            columnas_posibles = [
                "producto", "descripcion", "producto_nombre",
                "cantidad", "piezas", "precio", "monto",
                "fecha", "factura"
            ]
            columnas = [c for c in columnas_posibles if c in df.columns]

            tabla.setColumnCount(len(columnas))
            tabla.setHorizontalHeaderLabels(columnas)
            tabla.setRowCount(len(df))

            for i in range(len(df)):
                for j, col in enumerate(columnas):
                    valor = df.iloc[i][col]
                    if pd.isna(valor):
                        valor = ""
                    if col in ["precio", "monto"]:
                        try:
                            valor = f"${float(valor):,.2f}"
                        except:
                            pass
                    item = QTableWidgetItem(str(valor))
                    item.setTextAlignment(Qt.AlignCenter)
                    tabla.setItem(i, j, item)

            tabla.horizontalHeader().setStretchLastSection(True)
            tabla.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            tabla.verticalHeader().setVisible(False)
            tabla.setAlternatingRowColors(True)

        # =============================
        # ESPACIADOR PEQUEÑO
        # =============================
        layout.addSpacing(20)

        # =============================
        # GRAFICAS
        # =============================
        self.canvas_prod_barras = GraficoCanvas(self)
        layout.addWidget(self.canvas_prod_barras)

        self.canvas_prod_lineas = GraficoCanvas(self)
        layout.addWidget(self.canvas_prod_lineas)

        self._dibujar_graficos_productos()

        # =============================
        # ESPACIADOR
        # =============================
        layout.addSpacing(15)

        # =============================
        # BOTONES DE EXPORTACIÓN
        # =============================
        botones = QHBoxLayout()
        botones.setAlignment(Qt.AlignCenter)
        botones.setSpacing(12)

        btn_exp_excel = QPushButton("📊 Exportar productos a Excel")
        btn_exp_pdf = QPushButton("📄 Exportar productos a PDF")

        estilo_btn = """
            QPushButton {
                background-color: #2563eb;
                color: white;
                font-weight: bold;
                border-radius: 8px;
                padding: 10px 25px;
                font-size: 11pt;
            }
            QPushButton:hover {
                background-color: #1e40af;
            }
        """
        btn_exp_excel.setStyleSheet(estilo_btn)
        btn_exp_pdf.setStyleSheet(
            estilo_btn.replace("#2563eb", "#10b981").replace("#1e40af", "#059669")
        )

        botones.addWidget(btn_exp_excel)
        botones.addWidget(btn_exp_pdf)

        layout.addLayout(botones)

        # ======================================================
        # EVENTOS (EXPORTACIÓN CORPORATIVA COMPLETA)
        # ======================================================
        btn_exp_excel.clicked.connect(self.exportar_reporte_excel)
        btn_exp_pdf.clicked.connect(self.exportar_reporte_pdf)

    
    def _dibujar_graficos_productos(self):
        if self.df_productos is None or self.df_productos.empty:
            return

        df = self.df_productos.copy()

        # =====================================================
        # GRÁFICO 1: BARRAS HORIZONTALES (TOTAL POR PRODUCTO)
        # =====================================================
        if "producto" in df.columns and "monto" in df.columns:
            graf = df.groupby("producto")["monto"].sum().sort_values()

            ax = self.canvas_prod_barras.axes
            ax.clear()

            # Dibujar barras
            bars = ax.barh(graf.index, graf.values, color="#10b981")

            ax.set_title("Total vendido por producto", pad=20, fontsize=12, fontweight="bold")
            ax.set_xlabel("Monto vendido")
            ax.set_ylabel("")  # Ya no necesitamos eje Y con texto largo

            # =============================================
            # ⭐ IMPRIMIR NOMBRE DEL PRODUCTO DENTRO DE LA BARRA
            # =============================================
            for bar, nombre_producto in zip(bars, graf.index):
                x = bar.get_width() * 0.01   # posición horizontal dentro de la barra
                y = bar.get_y() + bar.get_height() / 2

                ax.text(
                    x,
                    y,
                    nombre_producto,
                    va="center",
                    ha="left",
                    color="white",
                    fontsize=9,
                    fontweight="bold"
                )

            # Mostrar valores al final de cada barra
            for bar, valor in zip(bars, graf.values):
                ax.text(
                    bar.get_width() + (max(graf.values) * 0.01),
                    bar.get_y() + bar.get_height() / 2,
                    f"${valor:,.0f}",
                    va="center",
                    ha="left",
                    fontsize=9,
                    color="#111"
                )

            # Ajustes visuales
            self.canvas_prod_barras.figure.subplots_adjust(
                top=0.90,
                bottom=0.15,
                left=0.05,   # como ya no hay etiquetas a la izquierda, reducimos margen
                right=0.95
            )

            ax.tick_params(axis='y', left=False, labelleft=False)  # ocultar eje Y

            self.canvas_prod_barras.draw()

        # =====================================================
        # GRÁFICO 2: HISTOGRAMA DE MONTOS
        # =====================================================
        if "fecha" in df.columns and "monto" in df.columns:
            try:
                df["fecha"] = pd.to_datetime(df["fecha"])
            except:
                pass

            montos = df["monto"].astype(float)

            ax2 = self.canvas_prod_lineas.axes
            ax2.clear()

            ax2.hist(montos, bins=10, color="#2563eb")
            ax2.set_title("Histograma de montos por compra", fontsize=11, fontweight="bold")
            ax2.set_xlabel("Monto")
            ax2.set_ylabel("Frecuencia")

            # Ajuste visual estándar
            self.canvas_prod_lineas.figure.subplots_adjust(
                top=0.90,
                bottom=0.20,
                left=0.10,
                right=0.95
            )

            ax2.tick_params(axis='x', labelsize=9)
            ax2.tick_params(axis='y', labelsize=9)

            self.canvas_prod_lineas.draw()

    
    # =====================================================================
    # PESTAÑA: GRÁFICOS
    # =====================================================================
    def _init_tab_graficos(self):
        layout = self.tab_graficos["layout"]

        # =============================
        # CANVAS DE HISTOGRAMA Y FRECUENCIAS
        # =============================
        self.canvas_hist = GraficoCanvas(self)
        self.canvas_freq = GraficoCanvas(self)

        layout.addWidget(self.canvas_hist)
        layout.addWidget(self.canvas_freq)

        # Dibujar gráficos iniciales
        self._dibujar_graficos()

        # =============================
        # ESPACIADO
        # =============================
        layout.addSpacing(20)

        # =============================
        # BOTONES DE EXPORTACIÓN (ABAJO)
        # =============================
        botones = QHBoxLayout()
        botones.setAlignment(Qt.AlignCenter)
        botones.setSpacing(14)

        btn_excel = QPushButton("📊 Exportar gráficos a Excel")
        btn_pdf = QPushButton("📄 Exportar gráficos a PDF")

        estilo_btn = """
            QPushButton {
                background-color: #2563eb;
                color: white;
                font-weight: bold;
                border-radius: 8px;
                padding: 10px 28px;
                font-size: 11pt;
            }
            QPushButton:hover {
                background-color: #1e40af;
            }
        """

        btn_excel.setStyleSheet(estilo_btn)
        btn_pdf.setStyleSheet(
            estilo_btn.replace("#2563eb", "#10b981").replace("#1e40af", "#059669")
        )

        botones.addWidget(btn_excel)
        botones.addWidget(btn_pdf)

        layout.addLayout(botones)

        # ======================================================
        # LÓGICA DE EXPORTACIÓN CORPORATIVA
        # (Siempre exporta el reporte COMPLETO del cliente)
        # ======================================================
        btn_excel.clicked.connect(self.exportar_reporte_excel)
        btn_pdf.clicked.connect(self.exportar_reporte_pdf)



    # =====================================================================
    # Dibujar ambos gráficos
    # =====================================================================
    def _dibujar_graficos(self):
        try:
            self._render_hist()
            self._render_freq()
        except Exception as e:
            print("Error al dibujar gráficos:", e)


    # =====================================================================
    # HISTOGRAMA DE MONTOS
    # =====================================================================
    def _render_hist(self):
        ax = self.canvas_hist.axes
        ax.clear()

        if self.df_facturas is None or self.df_facturas.empty:
            ax.text(0.5, 0.5, "Sin datos", ha="center", va="center", fontsize=12)
        else:
            df = self.df_facturas.copy()

            if "total" in df.columns:
                try:
                    valores = df["total"].astype(float)
                    ax.hist(valores, bins=20)
                    ax.set_title("Histograma de montos")
                    ax.set_xlabel("Monto")
                    ax.set_ylabel("Frecuencia")
                except Exception as e:
                    print("Error en histograma:", e)
                    ax.text(0.5, 0.5, "Datos inválidos", ha="center", va="center")

            else:
                ax.text(0.5, 0.5, "No existe columna 'total'", ha="center", va="center")

        self.canvas_hist.draw()


    # =====================================================================
    # FRECUENCIA DE COMPRA POR MES (SERIE DE TIEMPO)
    # =====================================================================
    def _render_freq(self):
        """
        📈 Gráfico de evolución por compra en formato LINEAL.
        Muestra la tendencia del cliente por cada compra (factura).
        """
        ax = self.canvas_freq.axes
        ax.clear()

        # =============================
        # Validación de datos
        # =============================
        if self.df_facturas is None or self.df_facturas.empty:
            ax.text(0.5, 0.5, "Sin datos", ha="center", va="center", fontsize=12)
            self.canvas_freq.draw()
            return

        df = self.df_facturas.copy()

        if "fecha" not in df.columns or "total" not in df.columns:
            ax.text(0.5, 0.5, "Faltan columnas 'fecha' o 'total'", ha="center", va="center")
            self.canvas_freq.draw()
            return

        # =============================
        # Preparar datos
        # =============================
        try:
            df["fecha"] = pd.to_datetime(df["fecha"])
        except:
            ax.text(0.5, 0.5, "Fechas inválidas", ha="center", va="center")
            self.canvas_freq.draw()
            return

        # Ordenar por fecha
        df = df.sort_values("fecha")

        # Crear índice numérico por compra
        df["compra_num"] = range(1, len(df) + 1)

        # =============================
        # Gráfico de línea
        # =============================
        ax.plot(
            df["compra_num"],
            df["total"],
            color="#1d4ed8",      # Azul intenso profesional
            linewidth=2.5,        # Línea más gruesa
            marker="o",           # Punto discreto
            markersize=6
        )

        # =============================
        # Mejoras visuales
        # =============================
        ax.set_title(
            "Evolución del consumo por compra",
            fontsize=14,
            fontweight="bold"
        )

        ax.set_xlabel("Número de compra (orden cronológico)", fontsize=10)
        ax.set_ylabel("Monto total de la compra", fontsize=10)

        ax.grid(alpha=0.3)

        # Etiquetas del eje X = fechas reales
        ax.set_xticks(df["compra_num"])
        ax.set_xticklabels(
            df["fecha"].dt.strftime("%Y-%m-%d"),
            rotation=45,
            ha="right",
            fontsize=8
        )

        # Ajuste
        self.canvas_freq.figure.tight_layout()
        self.canvas_freq.draw()

    # =====================================================================
    # PESTAÑA: FACTURAS
    # =====================================================================
    def _init_tab_facturas(self):
        layout = self.tab_facturas["layout"]

        # ====================================================
        # TABLA CON LAS FACTURAS
        # ====================================================
        tabla = QTableWidget()
        layout.addWidget(tabla)
        self.tab_facturas_widget = tabla  # ✔ necesario para exportar PDF corporativo

        if self.df_facturas is None or self.df_facturas.empty:
            tabla.setRowCount(0)
            tabla.setColumnCount(1)
            tabla.setHorizontalHeaderLabels(["Sin datos de facturas"])
        else:
            df = self.df_facturas.copy()

            columnas_posibles = ["factura", "fecha", "total", "estatus", "empresa"]
            columnas = [c for c in columnas_posibles if c in df.columns]

            tabla.setColumnCount(len(columnas))
            tabla.setHorizontalHeaderLabels(columnas)
            tabla.setRowCount(len(df))

            for i in range(len(df)):
                for j, col in enumerate(columnas):
                    valor = df.iloc[i][col]

                    if pd.isna(valor):
                        valor = ""

                    if col == "total":
                        try:
                            valor = f"${float(valor):,.2f}"
                        except:
                            pass

                    item = QTableWidgetItem(str(valor))
                    item.setTextAlignment(Qt.AlignCenter)
                    tabla.setItem(i, j, item)

            # Estilo de tabla
            tabla.horizontalHeader().setStretchLastSection(True)
            tabla.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            tabla.verticalHeader().setVisible(False)
            tabla.setAlternatingRowColors(True)

        # ====================================================
        # ESPACIO
        # ====================================================
        layout.addSpacing(20)

        # ====================================================
        # BOTONES DE EXPORTACIÓN (ESTILO CORPORATIVO)
        # ====================================================
        botones = QHBoxLayout()
        botones.setAlignment(Qt.AlignCenter)
        botones.setSpacing(14)

        btn_excel = QPushButton("📊 Exportar facturas a Excel")
        btn_pdf = QPushButton("📄 Exportar facturas a PDF")

        estilo_btn = """
            QPushButton {
                background-color: #2563eb;
                color: white;
                font-weight: bold;
                border-radius: 8px;
                padding: 10px 28px;
                font-size: 11pt;
            }
            QPushButton:hover {
                background-color: #1e40af;
            }
        """

        btn_excel.setStyleSheet(estilo_btn)
        btn_pdf.setStyleSheet(
            estilo_btn.replace("#2563eb", "#10b981").replace("#1e40af", "#059669")
        )

        botones.addWidget(btn_excel)
        botones.addWidget(btn_pdf)
        layout.addLayout(botones)

        # ====================================================
        # EVENTOS — EXPORTACIÓN CORPORATIVA COMPLETA
        # ====================================================
        btn_excel.clicked.connect(self.exportar_reporte_excel)
        btn_pdf.clicked.connect(self.exportar_reporte_pdf)

    def _render_bar_productos(self):
        self.canvas_bar.axes.clear()

        df = self.df_productos.copy()
        df_group = df.groupby("producto")["monto"].sum().reset_index()

        # Ajustar texto largo
        df_group["producto"] = df_group["producto"].apply(
            lambda x: "\n".join(textwrap.wrap(x, 35))
        )

        productos = df_group["producto"]
        montos = df_group["monto"]

        self.canvas_bar.axes.barh(productos, montos)
        self.canvas_bar.axes.set_title("Total vendido por producto", fontsize=14)

        self.canvas_bar.figure.tight_layout()
        self.canvas_bar.draw()

    def _render_hist_fecha(self):
        self.canvas_hist_fecha.axes.clear()

        df = self.df_productos.copy()

        if "fecha" in df.columns:
            df["fecha"] = pd.to_datetime(df["fecha"])
            df["solo_fecha"] = df["fecha"].dt.date  # quitar hora

            # Agrupar por fecha
            df_group = df.groupby("solo_fecha")["monto"].sum()

            self.canvas_hist_fecha.axes.hist(
                df_group.index,
                weights=df_group.values,
                bins=len(df_group),
                edgecolor="black"
            )

            self.canvas_hist_fecha.axes.set_title("Histograma de compras por fecha", fontsize=14)
            self.canvas_hist_fecha.axes.set_xlabel("Fecha")
            self.canvas_hist_fecha.axes.set_ylabel("Monto vendido")

            self.canvas_hist_fecha.figure.autofmt_xdate()

        self.canvas_hist_fecha.figure.tight_layout()
        self.canvas_hist_fecha.draw()


    def _crear_tab(self):
        """Crea una pestaña con scroll y un layout interno limpio."""
        contenedor = QWidget()
        layout_contenedor = QVBoxLayout(contenedor)
        layout_contenedor.setContentsMargins(0, 0, 0, 0)
        layout_contenedor.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none;")
        layout_contenedor.addWidget(scroll)

        interno = QWidget()
        layout_interno = QVBoxLayout(interno)
        layout_interno.setContentsMargins(10, 10, 10, 10)
        layout_interno.setSpacing(12)

        scroll.setWidget(interno)

        return {
            "contenedor": contenedor,
            "scroll": scroll,
            "interno": interno,
            "layout": layout_interno
        }
    def exportar_excel(self, nombre_archivo, dataframe):
        ruta, _ = QFileDialog.getSaveFileName(
            self,
            "Guardar Excel",
            f"{nombre_archivo}.xlsx",
            "Archivos Excel (*.xlsx)"
        )
        if ruta:
            try:
                dataframe.to_excel(ruta, index=False)
                QMessageBox.information(self, "Éxito", f"Archivo guardado en:\n{ruta}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"No se pudo exportar:\n{e}")
    
    def exportar_tabla_pdf(self, nombre_archivo, tabla):
        ruta, _ = QFileDialog.getSaveFileName(
            self,
            "Guardar PDF",
            f"{nombre_archivo}.pdf",
            "Archivos PDF (*.pdf)"
        )
        if not ruta:
            return

        try:
            c = pdfcanvas.Canvas(ruta, pagesize=letter)
            width, height = letter

            y = height - 40
            c.setFont("Helvetica-Bold", 12)
            c.drawString(40, y, nombre_archivo)
            y -= 20

            c.setFont("Helvetica", 9)

            col_count = tabla.columnCount()

            headers = [tabla.horizontalHeaderItem(i).text() for i in range(col_count)]

            c.drawString(40, y, "  ".join(headers))
            y -= 15

            for row in range(tabla.rowCount()):
                valores = []
                for col in range(col_count):
                    item = tabla.item(row, col)
                    valores.append(item.text() if item else "")
                c.drawString(40, y, "  ".join(valores))
                y -= 15

                if y < 50:
                    c.showPage()
                    y = height - 40

            c.save()
            QMessageBox.information(self, "Éxito", f"PDF guardado en:\n{ruta}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo exportar a PDF:\n{e}")
    
    def exportar_grafico_pdf(self, nombre_archivo, canvas_widget):
        ruta, _ = QFileDialog.getSaveFileName(
            self,
            "Guardar gráfico en PDF",
            f"{nombre_archivo}.pdf",
            "Archivos PDF (*.pdf)"
        )
        if not ruta:
            return

        try:
            figura = canvas_widget.figure
            figura.savefig(ruta, format="pdf", bbox_inches="tight")
            QMessageBox.information(self, "Éxito", f"PDF guardado en:\n{ruta}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo exportar:\n{e}")
    
    def exportar_reporte_excel(self):
        """
        Exporta reporte corporativo completo a Excel:
        - Hoja 'Resumen' con datos del cliente + KPIs
        - Hoja 'Productos' con df_productos
        - Hoja 'Facturas' con df_facturas
        - Hoja 'Graficos' con imágenes de los gráficos Matplotlib
        """
        ruta, _ = QFileDialog.getSaveFileName(
            self,
            "Guardar reporte de cliente (Excel)",
            f"reporte_cliente_{self.numero_cliente}.xlsx",
            "Archivos Excel (*.xlsx)"
        )
        if not ruta:
            return

        try:
            # ============================
            # 1) RECONSTRUIR KPIs
            # ============================
            total = 0.0
            activas = 0
            ultima_fecha = "-"
            ultimo_producto = "-"

            # Facturas
            if not self.df_facturas.empty:
                df_f = self.df_facturas.copy()

                if "fecha" in df_f.columns:
                    try:
                        df_f["fecha"] = pd.to_datetime(df_f["fecha"])
                    except Exception:
                        pass

                if "estatus" in df_f.columns:
                    df_act = df_f[df_f["estatus"].str.lower() == "activa"]
                else:
                    df_act = df_f

                if "total" in df_act.columns:
                    total = float(df_act["total"].sum() or 0.0)

                activas = len(df_act)

                if "fecha" in df_f.columns:
                    df_f = df_f.sort_values("fecha", ascending=False)
                    try:
                        ultima_fecha = df_f.iloc[0]["fecha"].strftime("%d/%m/%Y")
                    except Exception:
                        ultima_fecha = "-"

            # Último producto desde df_productos
            if not self.df_productos.empty:
                df_p = self.df_productos.copy()
                if "fecha" in df_p.columns:
                    try:
                        df_p["fecha"] = pd.to_datetime(df_p["fecha"])
                    except Exception:
                        pass
                    df_p = df_p.sort_values("fecha", ascending=False)

                if "producto" in df_p.columns:
                    ultimo_producto = str(df_p.iloc[0]["producto"])

            # ============================
            # 2) ARMAR HOJA RESUMEN
            # ============================
            resumen_rows = [
                ["Cliente", self.cliente_data.get("nombre", "")],
                ["Número de cliente", self.numero_cliente],
                ["RFC", self.cliente_data.get("rfc", "")],
                ["Ciudad", self.cliente_data.get("poblacion", "")],
                ["Estado", self.cliente_data.get("estado", "")],
                ["Teléfono", self.cliente_data.get("telefono", "")],
                ["Vendedor", self.cliente_data.get("agente", "")],
                [],
                ["KPI", "Valor"],
                ["Total vendido", f"${total:,.2f}"],
                ["Facturas activas", str(activas)],
                ["Última compra", ultima_fecha],
                ["Último producto", ultimo_producto],
            ]
            df_resumen = pd.DataFrame(resumen_rows, columns=["Campo", "Valor"])

            # ============================
            # 3) CREAR EXCEL CON XlsxWriter
            # ============================
            with pd.ExcelWriter(ruta, engine="xlsxwriter") as writer:
                # Hoja RESUMEN
                df_resumen.to_excel(writer, sheet_name="Resumen", index=False)
                wb = writer.book
                ws_res = writer.sheets["Resumen"]

                # Formato corporativo
                header_fmt = wb.add_format({
                    "bold": True,
                    "bg_color": "#2563EB",
                    "font_color": "white",
                    "border": 1
                })
                normal_fmt = wb.add_format({"border": 1})

                for col_num, value in enumerate(df_resumen.columns.values):
                    ws_res.write(0, col_num, value, header_fmt)

                ws_res.set_column("A:A", 25)
                ws_res.set_column("B:B", 60)

                # Hoja PRODUCTOS
                if not self.df_productos.empty:
                    self.df_productos.to_excel(writer, sheet_name="Productos", index=False)
                    ws_prod = writer.sheets["Productos"]
                    for col_num, value in enumerate(self.df_productos.columns.values):
                        ws_prod.write(0, col_num, value, header_fmt)
                    ws_prod.set_column(0, len(self.df_productos.columns) - 1, 18, normal_fmt)

                # Hoja FACTURAS
                if not self.df_facturas.empty:
                    self.df_facturas.to_excel(writer, sheet_name="Facturas", index=False)
                    ws_fac = writer.sheets["Facturas"]
                    for col_num, value in enumerate(self.df_facturas.columns.values):
                        ws_fac.write(0, col_num, value, header_fmt)
                    ws_fac.set_column(0, len(self.df_facturas.columns) - 1, 18, normal_fmt)

                # ============================
                # 4) HOJA GRÁFICOS (IMÁGENES)
                # ============================
                ws_graf = wb.add_worksheet("Graficos")

                fila = 1
                col = 1

                def insertar_canvas(titulo, canvas_widget, fila_ini):
                    if canvas_widget is None:
                        return fila_ini
                    # Guardar figura en memoria
                    buf = io.BytesIO()
                    canvas_widget.figure.savefig(buf, format="png", bbox_inches="tight")
                    buf.seek(0)
                    img_data = buf.read()

                    ws_graf.write(fila_ini, col, titulo, header_fmt)
                    ws_graf.insert_image(
                        fila_ini + 1, col,
                        "grafico.png",
                        {
                            "image_data": io.BytesIO(img_data),
                            "x_scale": 0.9,
                            "y_scale": 0.9
                        }
                    )
                    # Devolver siguiente fila libre (aprox 30 filas más abajo)
                    return fila_ini + 32

                # Insertar gráficos si existen
                fila = insertar_canvas("Histograma general", getattr(self, "canvas_hist", None), fila)
                fila = insertar_canvas("Frecuencias / Serie", getattr(self, "canvas_freq", None), fila)
                fila = insertar_canvas("Total por producto", getattr(self, "canvas_prod_barras", None), fila)
                fila = insertar_canvas("Montos por compra", getattr(self, "canvas_prod_lineas", None), fila)

            QMessageBox.information(self, "Éxito", f"Reporte Excel guardado en:\n{ruta}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo exportar el reporte:\n{e}")

    def exportar_reporte_pdf(self):
        """
        Exporta un reporte corporativo completo del cliente a PDF:
        - Encabezado con nombre + número de cliente
        - KPIs
        - Tabla de productos (ReportLab Table)
        - Tabla de facturas (ReportLab Table)
        - Gráficos acomodados en páginas corporativas
        """
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas as pdfcanvas
        from reportlab.platypus import Table, TableStyle
        from reportlab.lib import colors
        from reportlab.lib.utils import ImageReader

        ruta, _ = QFileDialog.getSaveFileName(
            self,
            "Guardar reporte de cliente (PDF)",
            f"reporte_cliente_{self.numero_cliente}.pdf",
            "Archivos PDF (*.pdf)"
        )
        if not ruta:
            return

        try:
            c = pdfcanvas.Canvas(ruta, pagesize=letter)
            width, height = letter

            # ==========================================================
            # FUNCIÓN ENCABEZADO → Reutilizable en todas las páginas
            # ==========================================================
            def encabezado():
                # Barra azul superior
                c.setFillColorRGB(37/255, 99/255, 235/255)
                c.rect(0, height - 40, width, 40, fill=1, stroke=0)

                # Título
                c.setFillColorRGB(1, 1, 1)
                c.setFont("Helvetica-Bold", 16)
                c.drawString(40, height - 28, "Reporte de Cliente")

                # Nombre + número del cliente (alineado a la derecha)
                nombre_c = self.cliente_data.get("nombre", "Sin nombre")
                c.setFont("Helvetica", 11)
                c.drawRightString(width - 40, height - 28, f"{self.numero_cliente} — {nombre_c}")

            # 📄 PRIMERA PÁGINA (NO llamar showPage todavía)
            encabezado()
            y = height - 80

            # ==========================================================
            # DATOS DEL CLIENTE
            # ==========================================================
            c.setFont("Helvetica-Bold", 12)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(40, y, "Datos del cliente")
            y -= 20

            c.setFont("Helvetica", 11)
            datos = [
                f"Cliente: {self.cliente_data.get('nombre', '')}",
                f"Número: {self.numero_cliente}",
                f"RFC: {self.cliente_data.get('rfc', '')}",
                f"Ciudad: {self.cliente_data.get('poblacion', '')}",
                f"Estado: {self.cliente_data.get('estado', '')}",
            ]
            for d in datos:
                c.drawString(50, y, d)
                y -= 16
            y -= 10

            # ==========================================================
            # KPIs
            # ==========================================================
            total = 0
            activas = 0
            ultima_fecha = "-"
            ultimo_producto = "-"

            # KPIs desde facturas
            if not self.df_facturas.empty:
                df_f = self.df_facturas.copy()
                if "fecha" in df_f.columns:
                    try:
                        df_f["fecha"] = pd.to_datetime(df_f["fecha"])
                    except:
                        pass

                df_act = df_f[df_f["estatus"].str.lower() == "activa"] if "estatus" in df_f.columns else df_f

                if "total" in df_act.columns:
                    total = df_act["total"].sum()
                activas = len(df_act)

                if "fecha" in df_f.columns:
                    df_f = df_f.sort_values("fecha", ascending=False)
                    try:
                        ultima_fecha = df_f.iloc[0]["fecha"].strftime("%d/%m/%Y")
                    except:
                        pass

            # Último producto
            if not self.df_productos.empty:
                df_p = self.df_productos.copy()
                if "fecha" in df_p.columns:
                    try:
                        df_p["fecha"] = pd.to_datetime(df_p["fecha"])
                    except:
                        pass
                    df_p = df_p.sort_values("fecha", ascending=False)

                if "producto" in df_p.columns:
                    ultimo_producto = str(df_p.iloc[0]["producto"])

            # --- Dibujar KPIs ---
            c.setFont("Helvetica-Bold", 12)
            c.drawString(40, y, "KPIs")
            y -= 18

            c.setFont("Helvetica", 11)
            for k in [
                f"Total vendido: ${total:,.2f}",
                f"Facturas activas: {activas}",
                f"Última compra: {ultima_fecha}",
                f"Último producto: {ultimo_producto}",
            ]:
                c.drawString(50, y, k)
                y -= 14

            y -= 20

            # ==========================================================
            # TABLA CORPORATIVA: PRODUCTOS
            # ==========================================================
            if not self.df_productos.empty:
                c.setFont("Helvetica-Bold", 12)
                c.drawString(40, y, "Productos")
                y -= 20

                df = self.df_productos.copy()
                cols = [col for col in ["producto", "cantidad", "monto", "fecha"] if col in df.columns]
                df = df[cols].head(20)

                # matriz
                data = [cols] + df.values.tolist()

                table = Table(data, repeatRows=1)
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2563eb")),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0,0), (-1,-1), 8),
                    ('INNERGRID', (0,0), (-1,-1), 0.3, colors.grey),
                    ('BOX', (0,0), (-1,-1), 0.5, colors.black),
                    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.whitesmoke, colors.lightgrey])
                ]))

                w, h = table.wrapOn(c, width - 80, 400)
                table.drawOn(c, 40, y - h)
                y -= (h + 30)

            # ==========================================================
            # TABLA CORPORATIVA: FACTURAS
            # ==========================================================
            if not self.df_facturas.empty:
                if y < 200:
                    c.showPage()
                    encabezado()
                    y = height - 80

                c.setFont("Helvetica-Bold", 12)
                c.drawString(40, y, "Facturas")
                y -= 20

                df = self.df_facturas.copy()
                cols = ["factura", "fecha", "total", "estatus", "empresa"]
                cols = [cname for cname in cols if cname in df.columns]
                df = df[cols].head(20)

                data = [cols] + df.values.tolist()

                table = Table(data, repeatRows=1)
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2563eb")),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0,0), (-1,-1), 8),
                    ('INNERGRID', (0,0), (-1,-1), 0.3, colors.grey),
                    ('BOX', (0,0), (-1,-1), 0.5, colors.black),
                    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.whitesmoke, colors.lightgrey])
                ]))

                w, h = table.wrapOn(c, width - 80, 400)
                table.drawOn(c, 40, y - h)
                y -= (h + 30)

            # ==========================================================
            # FUNCIÓN PARA DIBUJAR CANVAS (IMAGEN)
            # ==========================================================
            def dibujar_canvas(canvas_widget, titulo, y_pos, alto=160):
                # Título
                c.setFont("Helvetica-Bold", 12)
                c.drawString(40, y_pos, titulo)
                y_pos -= 18

                buf = io.BytesIO()
                canvas_widget.figure.savefig(buf, format="png", dpi=110, bbox_inches="tight")
                buf.seek(0)
                img = ImageReader(buf)

                c.drawImage(
                    img,
                    40, y_pos - alto,
                    width=width - 80,
                    height=alto,
                    preserveAspectRatio=True
                )

                return y_pos - alto - 25

            # ==========================================================
            # PÁGINA 2 → 3 gráficos
            # ==========================================================
            c.showPage()
            encabezado()
            y = height - 70

            y = dibujar_canvas(self.canvas_hist, "Histograma general", y, alto=150)
            y = dibujar_canvas(self.canvas_freq, "Frecuencia de compras", y, alto=150)
            y = dibujar_canvas(self.canvas_prod_barras, "Total vendido por producto", y, alto=150)

            # ==========================================================
            # PÁGINA 3 → Último gráfico
            # ==========================================================
            c.showPage()
            encabezado()
            y = height - 70

            dibujar_canvas(self.canvas_prod_lineas, "Montos por fecha", y, alto=220)

            # ==========================================================
            # GUARDAR EL DOCUMENTO
            # ==========================================================
            c.save()

            QMessageBox.information(self, "Éxito", f"Reporte PDF guardado en:\n{ruta}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo exportar el PDF:\n{e}")