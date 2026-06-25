"""Diálogo de donación: QR de Buy Me a Coffee + enlace directo."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QFrame, QHBoxLayout,
)

from ..i18n import tr
from . import theme, icons, _assets

BMC_URL = "https://www.buymeacoffee.com/Rhoodks"


class DonateDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Invítame a un café"))
        self.setFixedWidth(380)

        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 22)
        v.setSpacing(14)

        title = QLabel(tr("¿Te gusta BMI? ¡Gracias por apoyarlo!"))
        title.setProperty("role", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        v.addWidget(title)

        msg = QLabel(tr("BMI es gratis y siempre lo será. Si quieres invitarme a un café, "
                        "escanea el QR con el móvil o pulsa el botón."))
        msg.setProperty("role", "dim")
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(msg)

        # QR sobre fondo blanco (escaneable aunque el PNG tenga transparencia).
        qr_box = QFrame()
        qr_box.setStyleSheet("background:white; border-radius:12px;")
        qb = QHBoxLayout(qr_box)
        qb.setContentsMargins(14, 14, 14, 14)
        qr = QLabel()
        qr.setPixmap(_assets.load_qr_pixmap(240))
        qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr.setStyleSheet("background:transparent;")
        qb.addWidget(qr)
        v.addWidget(qr_box, 0, Qt.AlignmentFlag.AlignCenter)

        btn = QPushButton(tr("  Abrir Buy Me a Coffee"))
        btn.setIcon(icons.icon("coffee", "#000000"))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "background:#FFDD00; color:#000000; border:1px solid #000000;"
            "border-radius:8px; padding:9px 18px; font-weight:bold;"
        )
        btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(BMC_URL)))
        v.addWidget(btn)

        close = QPushButton(tr("Cerrar"))
        close.clicked.connect(self.accept)
        v.addWidget(close)
