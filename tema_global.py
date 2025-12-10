# tema_global.py
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication

def aplicar_tema_aspel(app, modo="claro"):
    """Aplica un tema global inspirado en Aspel SAE (claro u oscuro)"""

    if modo == "oscuro":
        estilo = """
            QWidget {
                background-color: #1e1e2e;
                color: #e0e0e0;
                font-family: 'Segoe UI';
                font-size: 10pt;
            }

            /* --- BOTONES --- */
            QPushButton {
                background-color: #3a3f51;
                color: #ffffff;
                border: 1px solid #555;
                border-radius: 5px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #50566b;
            }
            QPushButton:pressed {
                background-color: #2e3242;
            }

            /* --- ENTRADAS Y COMBOS --- */
            QLineEdit, QComboBox, QSpinBox {
                background-color: #2b2b3c;
                border: 1px solid #555;
                color: #f2f2f2;
                border-radius: 4px;
                padding: 4px 6px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #4a90e2;
                background-color: #303048;
            }

            /* --- TABLAS --- */
            QTableWidget {
                background-color: #2b2b3c;
                alternate-background-color: #242434;
                gridline-color: #444;
                border: 1px solid #555;
                color: #e6e6e6;
                selection-background-color: #3d6ba7;
                selection-color: #ffffff;
            }

            /* --- ENCABEZADOS --- */
            QHeaderView::section {
                background-color: #3c3f52;
                color: #f2f2f2;
                font-weight: bold;
                border: 1px solid #555;
                padding: 6px;
            }

            /* --- PESTAÑAS --- */
            QTabBar::tab {
                background-color: #2e3140;
                color: #ccc;
                padding: 6px 12px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #3a3f51;
                color: white;
                font-weight: bold;
            }

            /* --- BARRA DE ESTADO --- */
            QStatusBar {
                background-color: #2e3140;
                color: #ccc;
                padding: 4px;
            }

            /* --- SCROLLBAR --- */
            QScrollBar:vertical {
                background: #2b2b3c;
                width: 12px;
                margin: 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #555b73;
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #6b7390;
            }
        """
    else:
        # === Modo claro (Aspel Style) ===
        estilo = """
            QWidget {
                font-family: 'Segoe UI';
                font-size: 10pt;
                color: #1f1f1f;
                background-color: #e9eef4;
            }

            QPushButton {
                background-color: #cbd6e2;
                color: #1f1f1f;
                border: 1px solid #a6b5c6;
                border-radius: 5px;
                padding: 6px 10px;
            }
            QPushButton:hover {
                background-color: #b7c7db;
            }
            QPushButton:pressed {
                background-color: #a2b7ce;
            }

            QLineEdit, QComboBox, QSpinBox {
                background-color: #ffffff;
                border: 1px solid #a6b5c6;
                border-radius: 4px;
                padding: 4px 6px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #4a90e2;
                background-color: #f8fbff;
            }

            QTableWidget {
                background-color: #ffffff;
                alternate-background-color: #f2f5f8;
                gridline-color: #cfd8e3;
                border: 1px solid #b0bccb;
                selection-background-color: #c5d9ed;
                selection-color: #000000;
            }

            QHeaderView::section {
                background-color: #d7e0ea;
                color: #1a1a1a;
                font-weight: bold;
                padding: 6px;
                border: 1px solid #b0bccb;
            }

            QStatusBar {
                background-color: #d8e0ea;
                color: #1f1f1f;
                padding: 4px;
            }

            QTabBar::tab {
                background-color: #cfd8e3;
                padding: 6px 12px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #e7ebf2;
                font-weight: bold;
            }

            QScrollBar:vertical {
                background: #e0e6ed;
                width: 12px;
                margin: 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #b3c0d1;
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #9eb2cc;
            }
        """

    app.setStyleSheet(estilo)