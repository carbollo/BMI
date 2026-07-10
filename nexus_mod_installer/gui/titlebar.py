"""Barra de título propia, integrada en el tema oscuro.

Sustituye la barra nativa de Windows (gris) por una del color de la app con los botones de
minimizar/maximizar/cerrar dibujados a mano (el de cerrar se enciende en rojo, como en
Windows 11). El movimiento, el ajuste a bordes (Snap), la sombra y el redimensionado siguen
siendo NATIVOS: la ventana conserva su marco de sistema y ``MainWindow.nativeEvent`` responde
a WM_NCCALCSIZE/WM_NCHITTEST (la técnica de Chrome/VS Code), así que arrastrar, doble clic,
Win+flechas y Snap funcionan como siempre.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QWidget

from ..i18n import tr
from . import theme

HEIGHT = 38          # alto de la barra (px)
BTN_W = 46           # ancho de cada botón de ventana


class CaptionButton(QAbstractButton):
    """Botón de ventana (minimizar/maximizar/cerrar) dibujado a mano."""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self._kind = kind
        self.setFixedSize(BTN_W, HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tips = {"min": tr("Minimizar"), "max": tr("Maximizar"), "close": tr("Cerrar")}
        self.setToolTip(tips.get(kind, ""))

    def enterEvent(self, e) -> None:  # repintar el hover
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        hover, down = self.underMouse(), self.isDown()
        # Fondo según estado (cerrar = rojo al estilo Windows 11)
        if self._kind == "close":
            if down:
                p.fillRect(self.rect(), QColor("#a51d12"))
            elif hover:
                p.fillRect(self.rect(), QColor("#c42b1c"))
        elif down:
            p.fillRect(self.rect(), QColor(theme.PANEL))
        elif hover:
            p.fillRect(self.rect(), QColor(theme.PANEL_HI))
        # Glifo
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        white_close = self._kind == "close" and (hover or down)
        color = QColor("#ffffff") if white_close else QColor(theme.TEXT)
        if not self.isEnabled():
            color = QColor(theme.TEXT_DIM)
        p.setPen(QPen(color, 1.2))
        cx, cy, s = self.width() / 2.0, self.height() / 2.0, 4.5
        if self._kind == "min":
            p.drawLine(QPointF(cx - s, cy), QPointF(cx + s, cy))
        elif self._kind == "max":
            w = self.window()
            if w is not None and w.isMaximized():
                # «Restaurar»: cuadro delantero + esquina del trasero asomando
                p.drawLine(QPointF(cx - s + 2, cy - s), QPointF(cx + s, cy - s))
                p.drawLine(QPointF(cx + s, cy - s), QPointF(cx + s, cy + s - 2))
                p.drawRect(QRectF(cx - s, cy - s + 2, 2 * s - 2, 2 * s - 2))
            else:
                p.drawRect(QRectF(cx - s, cy - s, 2 * s, 2 * s))
        else:  # close
            p.drawLine(QPointF(cx - s, cy - s), QPointF(cx + s, cy + s))
            p.drawLine(QPointF(cx - s, cy + s), QPointF(cx + s, cy - s))
        p.end()


class TitleBar(QWidget):
    """Barra superior: logo + título a la izquierda, botones de ventana a la derecha."""

    def __init__(self, window):
        super().__init__(window)
        self._win = window
        self.setObjectName("titlebar")
        self.setFixedHeight(HEIGHT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 0, 0)
        lay.setSpacing(9)

        self._icon = QLabel()
        self._icon.setFixedSize(18, 18)
        self._icon.setScaledContents(True)
        try:
            pm = window.windowIcon().pixmap(36, 36)
            if not pm.isNull():
                self._icon.setPixmap(pm)
        except Exception:  # noqa: BLE001
            pass
        self._title = QLabel(window.windowTitle())
        self._title.setProperty("role", "titlebar-text")
        lay.addWidget(self._icon)
        lay.addWidget(self._title)
        lay.addStretch()

        self.btn_min = CaptionButton("min", self)
        self.btn_max = CaptionButton("max", self)
        self.btn_close = CaptionButton("close", self)
        self.btn_min.clicked.connect(window.showMinimized)
        self.btn_max.clicked.connect(self._toggle_max)
        self.btn_close.clicked.connect(window.close)
        for b in (self.btn_min, self.btn_max, self.btn_close):
            lay.addWidget(b)

        window.windowTitleChanged.connect(self._title.setText)

    def _toggle_max(self) -> None:
        if self._win.isMaximized():
            self._win.showNormal()
        else:
            self._win.showMaximized()
        self.btn_max.update()

    def is_over_button(self, pos) -> bool:
        """¿``pos`` (coordenadas de la barra) cae sobre un botón de ventana? Para que el
        hit-test nativo NO trate esa zona como título (los clics deben llegar al botón)."""
        w = self.childAt(pos)
        return isinstance(w, CaptionButton)
