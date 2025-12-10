from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtCore import QUrl, QTimer   # ✅ ← esta línea es la que faltaba
import sys, os

app = QApplication(sys.argv)

class PDFViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Prueba visor PDF - PyQt5")
        self.resize(900, 900)

        layout = QVBoxLayout()
        widget = QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)

        # Ruta del PDF de prueba (debes tener uno en el escritorio)
        pdf_path = os.path.abspath("factura_prueba.pdf")
        if not os.path.exists(pdf_path):
            from reportlab.lib.pagesizes import letter
            from reportlab.platypus import SimpleDocTemplate, Paragraph
            from reportlab.lib.styles import getSampleStyleSheet
            doc = SimpleDocTemplate(pdf_path, pagesize=letter)
            styles = getSampleStyleSheet()
            doc.build([Paragraph("✅ PDF de prueba generado correctamente.", styles["Normal"])])

        viewer = QWebEngineView()
        pdf_url = QUrl.fromLocalFile(pdf_path)
        viewer.settings().setAttribute(viewer.settings().PluginsEnabled, True)
        viewer.settings().setAttribute(viewer.settings().PdfViewerEnabled, True)
        QTimer.singleShot(700, lambda: viewer.setUrl(pdf_url))


        layout.addWidget(viewer)

viewer = PDFViewer()
viewer.show()
sys.exit(app.exec_())
