"""Notificaciones tipo *toast*: avisos flotantes no bloqueantes en la esquina inferior
derecha de la ventana, con fundido de entrada/salida y autodescarte."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QGraphicsOpacityEffect

from . import theme, icons

_KIND = {
    "success": (theme.SUCCESS, "check"),
    "info": (theme.INFO, "download"),
    "error": (theme.DANGER, "x"),
}


class _Toast(QFrame):
    def __init__(self, parent, text: str, kind: str):
        super().__init__(parent)
        color, icon_name = _KIND.get(kind, _KIND["info"])
        self.setStyleSheet(
            f"background:{theme.PANEL_HI}; border:1px solid {theme.BORDER};"
            f"border-left:4px solid {color}; border-radius:8px;"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 16, 10)
        lay.setSpacing(10)
        ico = QLabel()
        ico.setPixmap(icons.pixmap(icon_name, color, 18))
        lay.addWidget(ico, 0, Qt.AlignmentFlag.AlignTop)
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{theme.TEXT}; background:transparent;")
        lbl.setWordWrap(True)
        lbl.setMaximumWidth(300)
        lay.addWidget(lbl, 1)
        self.eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.eff)
        self.adjustSize()


class ToastManager:
    """Gestiona la pila de toasts visibles sobre ``window``."""

    def __init__(self, window):
        self.window = window
        self.toasts: list[_Toast] = []

    def show(self, text: str, kind: str = "success", duration: int = 3500) -> None:
        t = _Toast(self.window, text, kind)
        self.toasts.append(t)
        t.show()
        t.raise_()
        self._reposition()
        anim = QPropertyAnimation(t.eff, b"opacity", t)
        anim.setDuration(180)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        t._anim_in = anim  # type: ignore[attr-defined]
        QTimer.singleShot(duration, lambda: self._dismiss(t))

    def _dismiss(self, t: _Toast) -> None:
        if t not in self.toasts:
            return
        anim = QPropertyAnimation(t.eff, b"opacity", t)
        anim.setDuration(220)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)

        def _done():
            if t in self.toasts:
                self.toasts.remove(t)
            t.deleteLater()
            self._reposition()

        anim.finished.connect(_done)
        anim.start()
        t._anim_out = anim  # type: ignore[attr-defined]

    def _reposition(self) -> None:
        margin = 16
        y = self.window.height() - margin
        for t in reversed(self.toasts):
            t.adjustSize()
            x = self.window.width() - t.width() - margin
            y -= t.height()
            t.move(max(margin, x), max(margin, y))
            y -= 8
