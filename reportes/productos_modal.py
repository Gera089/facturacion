# ===============================
# reportes/productos_modal.py
# ===============================
from __future__ import annotations
import numpy as np
import pandas as pd
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QWidget, QFrame
)
from PyQt5.QtCore import Qt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import matplotlib.pyplot as plt

from .utils_productos import (
    preparar_df_facturas,
    preparar_df_productos,
    join_prod_con_mes,
    aplicar_filtros,
    MESES_ORD,
)

# -------------------------------------------------------------------
# A) DetalleProductoDialog (para abrir desde una barra / selección)
# -------------------------------------------------------------------
class DetalleProductoDialog(QDialog):
    def __init__(self, parent, nombre_producto: str, df: pd.DataFrame, paleta_brand=("black","#D4AF37","white")):
        super().__init__(parent)
        self.setWindowTitle(f"Detalle de producto: {nombre_producto}")
        self.resize(980, 700)
        self.nombre = nombre_producto
        self.df = df.copy()

        v = QVBoxLayout(self)

        # KPIs
        krow = QHBoxLayout(); krow.setSpacing(10); v.addLayout(krow)
        def kpi(title, val):
            f = QFrame(); f.setStyleSheet("QFrame{background:#fff;border:1px solid #e5e7eb;border-radius:10px;}")
            l = QVBoxLayout(f); l.setContentsMargins(10,8,10,8)
            lt = QLabel(title); lt.setStyleSheet("font-weight:700;color:#334155;")
            lv = QLabel(val);   lv.setStyleSheet("font-weight:900;font-size:16pt;color:#0f172a;")
            l.addWidget(lt); l.addWidget(lv); return f

        self.df["cantidad"] = pd.to_numeric(self.df.get("cantidad", 0), errors="coerce").fillna(0)
        self.df["precio"]   = pd.to_numeric(self.df.get("precio", 0), errors="coerce").fillna(0)
        self.df["total"]    = self.df["cantidad"] * self.df["precio"]

        krow.addWidget(kpi("Importe total", f"${self.df['total'].sum():,.2f}"))
        krow.addWidget(kpi("Unidades", f"{int(self.df['cantidad'].sum()):,}"))
        krow.addWidget(kpi("Clientes únicos", f"{self.df.get('tienda', pd.Series()).nunique():,}"))

        # Arriba: tendencia mensual
        fig1, ax1 = plt.subplots(figsize=(7.2, 3.6)); cv1 = FigureCanvas(fig1); v.addWidget(cv1)
        if "mes_ord" in self.df.columns:
            trend = self.df.groupby("mes_ord")["total"].sum().reindex(range(12), fill_value=0)
            x = [MESES_ORD[i] for i in trend.index]
            ax1.plot(x, trend.values, marker="o", color="#0A66C2")
            ax1.set_title(f"Tendencia mensual: {self.nombre}")
            ax1.set_ylabel("Importe ($)")
            ax1.grid(True, linestyle="--", alpha=0.4)
            fig1.subplots_adjust(left=0.10, right=0.98, top=0.88, bottom=0.22)
        else:
            ax1.text(0.5,0.5,"Sin información de meses",ha="center",va="center")
        cv1.draw()

        # Abajo: Top clientes
        fig2, ax2 = plt.subplots(figsize=(7.2, 3.6)); cv2 = FigureCanvas(fig2); v.addWidget(cv2, 1)
        if "tienda" in self.df.columns:
            serie_cli = self.df.groupby("tienda")["total"].sum().nlargest(10).sort_values()
            y = np.arange(len(serie_cli))
            bars = ax2.barh(y, serie_cli.values, color="#0A66C2", zorder=3)
            ax2.set_yticks(y); ax2.set_yticklabels(serie_cli.index, ha="left", fontsize=9)
            ax2.invert_yaxis(); ax2.grid(axis="x", linestyle="--", alpha=0.4, zorder=0)
            ax2.set_title("Top 10 clientes por importe")
            for bar, vval in zip(bars, serie_cli.values):
                ax2.text(bar.get_width()*1.01, bar.get_y()+bar.get_height()/2, f"${vval:,.0f}", va="center", fontsize=8)
            fig2.subplots_adjust(left=0.28, right=0.98, top=0.90, bottom=0.18)
        else:
            ax2.text(0.5,0.5,"Sin datos de clientes",ha="center",va="center")
        cv2.draw()

# -------------------------------------------------------------------
# B) ProductosAnalisisDialog (modal con 4 pestañas)
# -------------------------------------------------------------------
class ProductosAnalisisDialog(QDialog):
    def __init__(self, ventana_mio, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Análisis Completo de Productos")
        self.resize(1000, 700)
        self.ventana_mio = ventana_mio

        self.df_fact = preparar_df_facturas(self.ventana_mio)
        self.df_prod = join_prod_con_mes(preparar_df_productos(self.ventana_mio), self.df_fact)

        v = QVBoxLayout(self)
        # filtros suaves heredables (empresa/cliente)
        ctr = QHBoxLayout(); v.addLayout(ctr)
        ctr.addWidget(QLabel("Empresa:"))
        self.combo_emp = QComboBox(); self.combo_emp.addItems(["Todas","Gourmet España","Ibersur","EZA2007"])
        ctr.addWidget(self.combo_emp)
        ctr.addWidget(QLabel("Cliente:"))
        self.combo_cli = QComboBox(); self.combo_cli.setEditable(True); self.combo_cli.addItem("")
        if "tienda" in self.df_prod.columns:
            for c in sorted(self.df_prod["tienda"].dropna().unique().tolist()):
                self.combo_cli.addItem(c)
        ctr.addStretch(1)
        btn_aplicar = QPushButton("Aplicar filtros"); ctr.addWidget(btn_aplicar)

        # Tabs
        from PyQt5.QtWidgets import QTabWidget
        self.tabs = QTabWidget(); v.addWidget(self.tabs, 1)
        self._build_tabs()

        btn_aplicar.clicked.connect(self._re_render_all)

    # ---- construir tabs
    def _build_tabs(self):
        # Ranking
        self.tab_rank = QWidget(); tv = QVBoxLayout(self.tab_rank)
        hdr = QHBoxLayout(); tv.addLayout(hdr)
        hdr.addWidget(QLabel("<b>Ranking de productos</b>")); hdr.addStretch(1)
        self.btn_imp = QPushButton("Importe"); self.btn_cnt = QPushButton("Cantidad")
        for b in (self.btn_imp, self.btn_cnt):
            b.setCheckable(True)
            b.setStyleSheet("QPushButton{padding:4px 10px;border:1px solid #cbd5e1;} QPushButton:checked{background:#0a66c2;color:#fff;}")
        self.btn_imp.setChecked(True)
        hdr.addWidget(self.btn_imp); hdr.addWidget(self.btn_cnt)
        self.fig_rk, self.ax_rk = plt.subplots(figsize=(7,4)); self.cv_rk = FigureCanvas(self.fig_rk); tv.addWidget(self.cv_rk, 1)
        self.btn_imp.clicked.connect(lambda: self._render_ranking("monto_total"))
        self.btn_cnt.clicked.connect(lambda: self._render_ranking("cantidad"))

        # Familias
        self.tab_fam = QWidget(); fv = QVBoxLayout(self.tab_fam)
        fv.addWidget(QLabel("<b>Participación por familia (Quesos, Jamones, Otros)</b>"))
        self.fig_fa, self.ax_fa = plt.subplots(figsize=(6,4)); self.cv_fa = FigureCanvas(self.fig_fa); fv.addWidget(self.cv_fa, 1)

        # Tendencias
        self.tab_tr = QWidget(); trv = QVBoxLayout(self.tab_tr)
        top = QHBoxLayout(); trv.addLayout(top)
        top.addWidget(QLabel("Producto:"))
        self.combo_prod = QComboBox(); self.combo_prod.setEditable(False)
        prods = sorted(self.df_prod["producto"].dropna().unique().tolist()) if not self.df_prod.empty and "producto" in self.df_prod.columns else []
        self.combo_prod.addItems(prods)
        self.combo_prod.currentIndexChanged.connect(self._render_tendencias)
        top.addWidget(self.combo_prod); top.addStretch(1)
        self.fig_td, self.ax_td = plt.subplots(figsize=(7,4)); self.cv_td = FigureCanvas(self.fig_td); trv.addWidget(self.cv_td, 1)

        # Pareto
        self.tab_pa = QWidget(); pav = QVBoxLayout(self.tab_pa)
        pav.addWidget(QLabel("<b>Pareto 80/20 por importe</b>"))
        self.fig_pa, self.ax_pa = plt.subplots(figsize=(7,4)); self.cv_pa = FigureCanvas(self.fig_pa); pav.addWidget(self.cv_pa, 1)

        self.tabs.addTab(self.tab_rank, "Ranking")
        self.tabs.addTab(self.tab_fam,  "Familias")
        self.tabs.addTab(self.tab_tr,   "Tendencias")
        self.tabs.addTab(self.tab_pa,   "Pareto")

        # render inicial
        self._render_ranking("monto_total")
        self._render_familias()
        self._render_tendencias()
        self._render_pareto()

    # ---- helpers
    def _df_filtrado(self) -> pd.DataFrame:
        emp = self.combo_emp.currentText()
        cli = (self.combo_cli.currentText() or "").strip()
        return aplicar_filtros(self.df_prod, empresa=emp, cliente_txt=cli)

    def _re_render_all(self):
        self._render_ranking("monto_total" if self.btn_imp.isChecked() else "cantidad")
        self._render_familias()
        self._render_tendencias()
        self._render_pareto()

    # ---- renders
    def _render_ranking(self, col: str):
        df = self._df_filtrado()
        ax = self.ax_rk; fig = self.fig_rk; ax.clear()
        if df.empty or "producto" not in df.columns:
            ax.text(0.5,0.5,"Sin datos",ha="center",va="center"); self.cv_rk.draw(); return
        serie = df.groupby("producto")[col].sum().nlargest(20).sort_values()
        labels, vals = serie.index.tolist(), serie.values
        y = np.arange(len(labels))
        bars = ax.barh(y, vals, zorder=3)
        ax.set_yticks(y); ax.set_yticklabels(labels, ha="left", fontsize=8); ax.invert_yaxis()
        ax.grid(axis="x", linestyle="--", alpha=0.4, zorder=0)
        ax.set_xlabel("Importe ($)" if col=="monto_total" else "Unidades")
        for bar, v in zip(bars, vals):
            ax.text(bar.get_width()*1.01, bar.get_y()+bar.get_height()/2,
                    f"${v:,.0f}" if col=="monto_total" else f"{int(v):,}",
                    va="center", fontsize=8)
        fig.subplots_adjust(left=0.25, right=0.96, top=0.90, bottom=0.15)
        self.cv_rk.draw()
        self.btn_imp.setChecked(col=="monto_total")
        self.btn_cnt.setChecked(col=="cantidad")

    def _render_familias(self):
        df = self._df_filtrado()
        ax = self.ax_fa; fig = self.fig_fa; ax.clear()
        if df.empty or "familia" not in df.columns:
            ax.text(0.5,0.5,"Sin datos",ha="center",va="center"); self.cv_fa.draw(); return
        s = df.groupby("familia")["monto_total"].sum().reindex(["Quesos","Jamones","Otros"]).fillna(0)
        wedges, _ = ax.pie(s.values, startangle=90, wedgeprops=dict(width=0.45))
        ax.legend(wedges, s.index, loc="center left", bbox_to_anchor=(1.0, 0.5))
        ax.set_title("Participación por familia")
        fig.subplots_adjust(left=0.05, right=0.80, top=0.88, bottom=0.10)
        self.cv_fa.draw()

    def _render_tendencias(self):
        df = self._df_filtrado()
        ax = self.ax_td; fig = self.fig_td; ax.clear()
        if df.empty or "producto" not in df.columns or "mes_ord" not in df.columns:
            ax.text(0.5,0.5,"Sin datos",ha="center",va="center"); self.cv_td.draw(); return
        prod = self.combo_prod.currentText() if self.combo_prod.count() else None
        if not prod:
            ax.text(0.5,0.5,"Sin producto",ha="center",va="center"); self.cv_td.draw(); return
        t = df[df["producto"]==prod].groupby("mes_ord")["monto_total"].sum().reindex(range(12), fill_value=0)
        x = [MESES_ORD[i] for i in t.index]
        ax.plot(x, t.values, marker="o", color="#0A66C2")
        ax.set_title(f"Tendencia mensual: {prod}")
        ax.set_ylabel("Importe ($)")
        ax.grid(True, linestyle="--", alpha=0.4)
        fig.subplots_adjust(left=0.10, right=0.98, top=0.88, bottom=0.22)
        self.cv_td.draw()

    def _render_pareto(self):
        df = self._df_filtrado()
        ax = self.ax_pa; fig = self.fig_pa; ax.clear()
        if df.empty or "producto" not in df.columns:
            ax.text(0.5,0.5,"Sin datos",ha="center",va="center"); self.cv_pa.draw(); return
        serie = df.groupby("producto")["monto_total"].sum().sort_values(ascending=False)
        if serie.empty:
            ax.text(0.5,0.5,"Sin datos",ha="center",va="center"); self.cv_pa.draw(); return
        y = serie.values
        x = np.arange(1, len(y)+1)
        acum = np.cumsum(y)/y.sum()*100
        ax.bar(x, y, alpha=0.6, label="Importe")
        ax2 = ax.twinx()
        ax2.plot(x, acum, marker="o", color="tab:red", label="Acumulado %")
        ax.set_xlabel("Productos (ordenados)"); ax.set_ylabel("Importe ($)")
        ax2.set_ylabel("Acumulado %"); ax2.set_ylim(0, 110)
        ax.grid(True, axis="y", linestyle="--", alpha=0.4)
        ax.legend(loc="upper left"); ax2.legend(loc="upper right")
        fig.subplots_adjust(left=0.10, right=0.88, top=0.92, bottom=0.12)
        self.cv_pa.draw()
