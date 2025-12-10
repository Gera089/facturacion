# ===============================
# reportes/productos_dashboard.py
# ===============================
from __future__ import annotations
from typing import Optional
import numpy as np

from matplotlib.axes import Axes
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

from .utils_productos import (
    preparar_df_facturas,
    preparar_df_productos,
    join_prod_con_mes,
    aplicar_filtros,
    acortar_nombre_producto,
)

class ProductosDashboard:
    """
    Dibuja el panel “Productos más vendidos” dentro del Dashboard General.
    - Toma datos de ventana_mio
    - Respeta filtros (empresa/cliente/fechas y texto de producto)
    - Modo familias (Quesos / Jamones / Otros) opcional para colorizar
    """

    def __init__(
        self,
        ventana_mio,
        canvas: FigureCanvas,
        ax: Axes,
        combo_empresa=None,
        combo_cliente=None,
        combo_producto=None,
        fecha_inicio=None,
        fecha_fin=None,
        modo_familias: bool = True,
    ):
        self.ventana_mio = ventana_mio
        self.canvas = canvas
        self.ax = ax

        self.combo_empresa = combo_empresa
        self.combo_cliente = combo_cliente
        self.combo_producto = combo_producto
        self.fecha_inicio = fecha_inicio
        self.fecha_fin = fecha_fin

        self.modo_familias = modo_familias

    # ----------------------------------------------------------
    def _get_filtros(self):
        empresa = self.combo_empresa.currentText() if self.combo_empresa else "Todas"
        cliente = (self.combo_cliente.currentText() or "").strip() if self.combo_cliente else ""
        fini = self.fecha_inicio.date().toPyDate() if self.fecha_inicio else None
        ffin = self.fecha_fin.date().toPyDate() if self.fecha_fin else None
        prod_txt = (self.combo_producto.currentText() or "").strip().lower() if self.combo_producto else ""
        return empresa, cliente, fini, ffin, prod_txt

    # ----------------------------------------------------------
    def generar(self, top_n: int = 10):
        ax = self.ax
        ax.clear()

        # Bases
        dff = preparar_df_facturas(self.ventana_mio)
        dfp = preparar_df_productos(self.ventana_mio)
        dfp = join_prod_con_mes(dfp, dff)

        if dfp.empty:
            ax.text(0.5, 0.5, "Sin datos de productos", ha="center", va="center", fontsize=12)
            self.canvas.draw()
            return

        # Filtros
        empresa, cliente, fini, ffin, prod_txt = self._get_filtros()
        dfp = aplicar_filtros(dfp, empresa=empresa, cliente_txt=cliente, fecha_ini=fini, fecha_fin=ffin)

        if prod_txt:
            if "producto" in dfp.columns:
                dfp = dfp[dfp["producto"].str.lower().str.contains(prod_txt)]

        if dfp.empty:
            ax.text(0.5, 0.5, "Sin datos con los filtros actuales", ha="center", va="center", fontsize=11)
            self.canvas.draw()
            return

        # Ranking por importe
        serie = (
            dfp.groupby("producto")["monto_total"]
               .sum()
               .nlargest(top_n)
               .sort_values()
        )

        if serie.empty:
            ax.text(0.5, 0.5, "Sin datos válidos", ha="center", va="center", fontsize=11)
            self.canvas.draw()
            return

        labels_full = serie.index.tolist()
        values = serie.values

        # Etiquetas abreviadas
        labels = [acortar_nombre_producto(x, max_chars=28) for x in labels_full]
        y_pos = np.arange(len(labels))

        # Color opcional por familia (simple: un solo color aquí para claridad)
        bars = ax.barh(y_pos, values, color="#4A90E2", zorder=3)

        # Eje Y legible y alineado a la izquierda
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, ha="left", fontsize=9)
        ax.invert_yaxis()
        ax.grid(axis="x", linestyle="--", alpha=0.45, zorder=0)
        ax.set_xlabel("Monto total ($)")
        ax.set_title("Productos más vendidos")

        # Valores al final de cada barra
        maxv = max(values) if len(values) else 0
        offset = maxv * 0.02 if maxv else 1
        for bar, v in zip(bars, values):
            ax.text(bar.get_width() + offset, bar.get_y() + bar.get_height()/2,
                    f"${v:,.0f}", va="center", fontsize=8, color="#333")

        # Ajuste del margen izquierdo según ancho real de etiquetas
        self.canvas.draw()  # para tener renderer
        renderer = self.canvas.get_renderer()
        max_w = max(lbl.get_window_extent(renderer).width for lbl in ax.get_yticklabels()) if ax.get_yticklabels() else 0
        fig_w = ax.figure.get_size_inches()[0] if ax.figure.get_size_inches()[0] else 1.0
        left_margin = 0.15 + (max_w / (fig_w * self.canvas.figure.dpi))  # conversión px->in->rel
        ax.figure.subplots_adjust(left=min(max(left_margin, 0.22), 0.50), right=0.96, top=0.88, bottom=0.18)

        self.canvas.draw()
