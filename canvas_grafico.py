# canvas_grafico.py
import matplotlib
matplotlib.use("Agg")  # evita problemas en PyQt

from PyQt5.QtWidgets import QWidget
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


class GraficoCanvas(FigureCanvas):
    def __init__(self, parent=None, width=5, height=3, dpi=100):
        self.figure = Figure(figsize=(width, height), dpi=dpi)
        
        # ✔️ Crear un eje por defecto
        self.axes = self.figure.add_subplot(111)

        super().__init__(self.figure)
        self.setParent(parent)

    # -----------------------------------------------------------
    # DIBUJAR HISTOGRAMA
    # -----------------------------------------------------------
    def plot_hist(self, data, titulo="Histograma"):
        self.axes.clear()
        self.axes.hist(data, bins=20, color="#2563eb")
        self.axes.set_title(titulo)
        self.draw()

    # -----------------------------------------------------------
    # DIBUJAR TOP PRODUCTOS (BARRAS)
    # -----------------------------------------------------------
    def plot_barras(self, x, y, titulo="Top productos"):
        self.axes.clear()
        self.axes.bar(x, y, color="#10b981")
        self.axes.set_title(titulo)
        self.axes.tick_params(axis='x', rotation=45)
        self.draw()
