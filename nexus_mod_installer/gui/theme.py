"""Tema oscuro estilo Nexus (QSS) + icono de la aplicación."""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush, QPen, QPolygonF, QFont
from PySide6.QtCore import QPointF

# Paleta
BG = "#17171a"          # fondo ventana
BG_DARK = "#0e0e10"     # fondo más oscuro (campos)
PANEL = "#212126"       # paneles/tarjetas
PANEL_HI = "#2a2a31"    # hover/selección
BORDER = "#37373d"
TEXT = "#e6e6e6"
TEXT_DIM = "#9a9aa2"
ACCENT = "#e08b3e"      # naranja Nexus
ACCENT_HI = "#f0a050"
SUCCESS = "#3fb950"
DANGER = "#d9534f"
INFO = "#4a90d9"

STYLESHEET = f"""
* {{
    font-family: "Segoe UI", "Inter", system-ui, sans-serif;
    font-size: 13px;
    color: {TEXT};
}}
QWidget {{ background: {BG}; }}
QMainWindow, QDialog {{ background: {BG}; }}

QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: 17px; font-weight: bold; }}
QLabel[role="dim"] {{ color: {TEXT_DIM}; }}
QLabel[role="label"] {{ color: {TEXT_DIM}; font-size: 11px; font-weight: bold; }}
QLabel[role="value"] {{ font-size: 18px; font-weight: bold; }}
QLabel[role="h2"] {{ font-size: 14px; font-weight: bold; }}

/* ---- Botones ---- */
QPushButton {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 16px;
}}
QPushButton:hover {{ background: {PANEL_HI}; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BG_DARK}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; background: {PANEL}; }}

QPushButton[variant="primary"] {{
    background: {ACCENT}; border-color: {ACCENT}; color: #1a1207; font-weight: bold;
}}
QPushButton[variant="primary"]:hover {{ background: {ACCENT_HI}; border-color: {ACCENT_HI}; }}

QPushButton[variant="success"] {{
    background: {SUCCESS}; border-color: {SUCCESS}; color: #0b2a12; font-weight: bold;
}}
QPushButton[variant="success"]:hover {{ background: #54c768; border-color: #54c768; }}

QPushButton[variant="danger"] {{ color: {DANGER}; }}
QPushButton[variant="danger"]:hover {{ border-color: {DANGER}; background: #3a2020; }}

QPushButton[variant="toggle"]:checked {{
    background: {DANGER}; border-color: {DANGER}; color: white; font-weight: bold;
}}

/* ---- Campos de texto ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {BG_DARK};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {ACCENT};
    selection-color: #1a1207;
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: {ACCENT};
    selection-color: #1a1207;
}}

/* ---- Pestañas ---- */
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_DIM};
    padding: 7px 16px; border: 1px solid transparent; border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}

/* ---- Tablas ---- */
QTableWidget, QTableView {{
    background: {PANEL};
    alternate-background-color: #25252b;
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 6px;
    selection-background-color: {ACCENT};
    selection-color: #1a1207;
}}
QHeaderView::section {{
    background: {BG_DARK}; color: {TEXT_DIM};
    padding: 6px; border: none; border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER};
    font-weight: bold;
}}
QTableWidget::item {{ padding: 6px 8px; }}

/* ---- Listas ---- */
QListWidget {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px;
    outline: 0; padding: 4px;
}}
QListWidget::item {{ padding: 7px 9px; border-radius: 6px; }}
QListWidget::item:hover {{ background: {PANEL_HI}; }}
QListWidget::item:selected {{ background: {ACCENT}; color: #1a1207; }}

/* ---- Barra de progreso ---- */
QProgressBar {{
    background: {BG_DARK}; border: 1px solid {BORDER}; border-radius: 6px;
    text-align: center; height: 16px; color: {TEXT};
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

/* ---- Casillas ---- */
QCheckBox {{ spacing: 6px; background: transparent; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {BORDER}; border-radius: 4px; background: {BG_DARK};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox::indicator:disabled {{ background: {PANEL}; border-color: {BORDER}; }}

/* ---- Radios ---- */
QRadioButton {{ spacing: 6px; background: transparent; }}
QRadioButton::indicator {{
    width: 16px; height: 16px; border: 1px solid {BORDER}; border-radius: 9px; background: {BG_DARK};
}}
QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QRadioButton::indicator:disabled {{ background: {PANEL}; border-color: {BORDER}; }}
QRadioButton:disabled {{ color: {TEXT_DIM}; }}
QCheckBox:disabled {{ color: {TEXT_DIM}; }}

/* ---- GroupBox ---- */
QGroupBox {{
    border: 1px solid {BORDER}; border-radius: 8px; margin-top: 14px; padding-top: 8px;
    background: {PANEL};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; color: {ACCENT}; font-weight: bold; }}

/* ---- Tarjetas (frames con property card) ---- */
QFrame[role="card"] {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px;
}}

/* ---- Barra de herramientas superior ---- */
QWidget[role="toolbar"] {{
    background: {BG_DARK}; border: 1px solid {BORDER}; border-radius: 10px;
}}

/* ---- Barras de desplazamiento ---- */
QScrollBar:vertical {{ background: {BG}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 6px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar:horizontal {{ background: {BG}; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 6px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}

/* ---- Barra de estado ---- */
QStatusBar {{ background: {BG_DARK}; color: {TEXT_DIM}; border-top: 1px solid {BORDER}; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {ACCENT}; padding: 4px; }}
"""


# Colores de estado para usar en código (chips/foreground).
STATUS_COLORS = {
    "En cola": TEXT_DIM,
    "Resolviendo enlace": INFO,
    "Descargando": ACCENT,
    "Extrayendo": INFO,
    "Instalando": INFO,
    "Desplegando": INFO,
    "Completado": SUCCESS,
    "Error": DANGER,
    "Requiere clic en web": ACCENT,
}


def make_app_icon(size: int = 256) -> QIcon:
    """Dibuja un icono simple: cuadrado oscuro redondeado + flecha de descarga naranja."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Fondo redondeado
    p.setBrush(QBrush(QColor(BG_DARK)))
    p.setPen(QPen(QColor(ACCENT), max(2, size // 40)))
    r = size * 0.08
    p.drawRoundedRect(QRectF(size * 0.06, size * 0.06, size * 0.88, size * 0.88), r, r)

    # Flecha de descarga
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(ACCENT)))
    cx = size / 2
    # Tallo
    stem_w = size * 0.14
    p.drawRect(QRectF(cx - stem_w / 2, size * 0.24, stem_w, size * 0.34))
    # Punta (triángulo)
    tri = QPolygonF([
        QPointF(cx - size * 0.22, size * 0.5),
        QPointF(cx + size * 0.22, size * 0.5),
        QPointF(cx, size * 0.74),
    ])
    p.drawPolygon(tri)
    # Bandeja
    p.setBrush(QBrush(QColor(ACCENT_HI)))
    p.drawRoundedRect(QRectF(size * 0.26, size * 0.78, size * 0.48, size * 0.08),
                      size * 0.03, size * 0.03)
    p.end()
    return QIcon(pix)
