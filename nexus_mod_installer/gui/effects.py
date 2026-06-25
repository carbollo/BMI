"""Efectos visuales: sombras de profundidad y animaciones sutiles.

- ``add_shadow``: aplica una sombra paralela a un widget (tarjetas, diálogos, barra).
- ``fade_in``: aparición con fundido (al cambiar de pestaña, mostrar diálogos/toasts).

Aviso: un widget solo admite UN QGraphicsEffect a la vez, y los efectos gráficos NO deben
aplicarse a QWebEngineView (lo deja en blanco). Por eso ``fade_in`` se usa solo en paneles
sin navegador embebido.
"""
from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QWidget


def add_shadow(widget: QWidget, blur: int = 24, dy: int = 4, alpha: int = 160) -> QGraphicsDropShadowEffect:
    """Sombra paralela suave bajo el widget (da sensación de profundidad/elevación)."""
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setXOffset(0)
    eff.setYOffset(dy)
    eff.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(eff)
    return eff


def fade_in(widget: QWidget, duration: int = 160) -> QPropertyAnimation:
    """Funde el widget de transparente a opaco. Devuelve la animación (autodestruida al
    terminar). No usar sobre widgets que contengan un QWebEngineView."""
    eff = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _clear():
        # Quita el efecto al acabar para no dejar overhead ni interferir con repintados.
        try:
            widget.setGraphicsEffect(None)
        except RuntimeError:
            pass

    anim.finished.connect(_clear)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    widget._fade_anim = anim  # type: ignore[attr-defined]  # conserva referencia
    return anim
