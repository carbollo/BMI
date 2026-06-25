"""Set de iconos de línea dibujados con QPainter (sin archivos externos).

Estilo coherente tipo Lucide/Feather, recoloreables al tema. Se usan en las pestañas y
botones en lugar de emojis (que se ven distintos en cada sistema). Cada icono se dibuja en
una rejilla 24x24 y se escala al tamaño pedido.

Uso:
    from .gui import icons
    btn.setIcon(icons.icon("download"))
    tabs.setTabIcon(0, icons.icon("home", color=theme.TEXT_DIM))
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QPainterPath

from . import theme

_GRID = 24.0


def _pen(color: str, w: float) -> QPen:
    pen = QPen(QColor(color))
    pen.setWidthF(w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _poly(p: QPainter, pts: list[tuple[float, float]], close: bool = False) -> None:
    path = QPainterPath()
    path.moveTo(pts[0][0], pts[0][1])
    for x, y in pts[1:]:
        path.lineTo(x, y)
    if close:
        path.closeSubpath()
    p.drawPath(path)


def _draw(name: str, p: QPainter) -> None:
    """Dibuja el icono ``name`` en la rejilla 0..24 con el lápiz ya configurado."""
    if name == "home":
        _poly(p, [(4, 11), (12, 4), (20, 11)])
        _poly(p, [(6, 10), (6, 20), (18, 20), (18, 10)])
        p.drawRect(QRectF(10, 14, 4, 6))
    elif name in ("search", "explore"):
        p.drawEllipse(QRectF(5, 5, 10, 10))
        p.drawLine(QPointF(15, 15), QPointF(20, 20))
    elif name == "download":
        p.drawLine(QPointF(12, 4), QPointF(12, 15))
        _poly(p, [(7, 11), (12, 16), (17, 11)])
        _poly(p, [(5, 19), (5, 20), (19, 20), (19, 19)])
    elif name in ("package", "box"):
        _poly(p, [(12, 3), (20, 7), (20, 16), (12, 21), (4, 16), (4, 7)], close=True)
        _poly(p, [(4, 7), (12, 11), (20, 7)])
        p.drawLine(QPointF(12, 11), QPointF(12, 21))
    elif name in ("log", "file"):
        _poly(p, [(6, 3), (15, 3), (19, 7), (19, 21), (6, 21)], close=True)
        _poly(p, [(15, 3), (15, 7), (19, 7)])
        for y in (11, 14, 17):
            p.drawLine(QPointF(9, y), QPointF(16, y))
    elif name == "plus":
        p.drawLine(QPointF(12, 5), QPointF(12, 19))
        p.drawLine(QPointF(5, 12), QPointF(19, 12))
    elif name == "folder":
        _poly(p, [(3, 7), (9, 7), (11, 9), (21, 9), (21, 19), (3, 19)], close=True)
    elif name in ("settings", "gear"):
        p.drawEllipse(QRectF(9, 9, 6, 6))
        for ang in range(0, 360, 45):
            import math
            a = math.radians(ang)
            cx, cy = 12 + 8 * math.cos(a), 12 + 8 * math.sin(a)
            ix, iy = 12 + 5.5 * math.cos(a), 12 + 5.5 * math.sin(a)
            p.drawLine(QPointF(ix, iy), QPointF(cx, cy))
    elif name == "play":
        _poly(p, [(8, 5), (19, 12), (8, 19)], close=True)
    elif name in ("refresh", "retry"):
        path = QPainterPath()
        path.arcMoveTo(QRectF(5, 5, 14, 14), 60)
        path.arcTo(QRectF(5, 5, 14, 14), 60, 250)
        p.drawPath(path)
        _poly(p, [(17, 3), (18, 8), (13, 8)])
    elif name in ("trash", "clean"):
        p.drawLine(QPointF(4, 6), QPointF(20, 6))
        _poly(p, [(9, 6), (9, 4), (15, 4), (15, 6)])
        _poly(p, [(6, 6), (7, 20), (17, 20), (18, 6)])
        for x in (10, 12, 14):
            p.drawLine(QPointF(x, 9), QPointF(x, 17))
    elif name == "save":
        _poly(p, [(5, 4), (17, 4), (20, 7), (20, 20), (5, 20)], close=True)
        p.drawRect(QRectF(9, 4, 6, 4))
        p.drawRect(QRectF(8, 13, 8, 7))
    elif name in ("x", "close", "remove"):
        p.drawLine(QPointF(6, 6), QPointF(18, 18))
        p.drawLine(QPointF(18, 6), QPointF(6, 18))
    elif name == "check":
        _poly(p, [(5, 13), (10, 18), (19, 6)])
    elif name == "image":
        p.drawRoundedRect(QRectF(4, 5, 16, 14), 2, 2)
        p.drawEllipse(QRectF(8, 8, 3, 3))
        _poly(p, [(5, 17), (10, 12), (14, 16), (17, 13), (19, 15)])
    elif name == "play_circle":
        p.drawEllipse(QRectF(4, 4, 16, 16))
        _poly(p, [(10, 8), (16, 12), (10, 16)], close=True)
    elif name == "list":
        for y in (7, 12, 17):
            p.drawEllipse(QRectF(5, y - 1, 2, 2))
            p.drawLine(QPointF(10, y), QPointF(19, y))
    elif name == "mesh":
        _poly(p, [(4, 20), (12, 4), (20, 20)], close=True)
        p.drawLine(QPointF(12, 4), QPointF(12, 13))
        p.drawLine(QPointF(4, 20), QPointF(12, 13))
        p.drawLine(QPointF(20, 20), QPointF(12, 13))
    elif name == "script":
        _poly(p, [(6, 3), (15, 3), (19, 7), (19, 21), (6, 21)], close=True)
        _poly(p, [(15, 3), (15, 7), (19, 7)])
        _poly(p, [(11, 11), (9, 14), (11, 17)])   # <
        _poly(p, [(14, 11), (16, 14), (14, 17)])  # >
    elif name == "plugin":
        p.drawEllipse(QRectF(6, 4, 12, 4))
        p.drawLine(QPointF(6, 6), QPointF(6, 18))
        p.drawLine(QPointF(18, 6), QPointF(18, 18))
        p.drawArc(QRectF(6, 10, 12, 4), 180 * 16, 180 * 16)
        p.drawArc(QRectF(6, 16, 12, 4), 180 * 16, 180 * 16)
    elif name == "sound":
        _poly(p, [(4, 9), (8, 9), (12, 5), (12, 19), (8, 15), (4, 15)], close=True)
        p.drawArc(QRectF(10, 7, 8, 10), -60 * 16, 120 * 16)
        p.drawArc(QRectF(10, 4, 13, 16), -55 * 16, 110 * 16)
    elif name == "coffee":
        p.drawLine(QPointF(4, 8), QPointF(16, 8))
        _poly(p, [(5, 8), (6, 18), (14, 18), (15, 8)])
        handle = QPainterPath()
        handle.moveTo(15, 10)
        handle.cubicTo(20, 10, 20, 16, 15, 16)
        p.drawPath(handle)
        p.drawLine(QPointF(8, 3), QPointF(8, 6))
        p.drawLine(QPointF(11, 3), QPointF(11, 6))
    else:  # punto de respaldo
        p.drawEllipse(QRectF(10, 10, 4, 4))


def pixmap(name: str, color: str = theme.TEXT, size: int = 18, stroke: float = 2.0) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(_pen(color, stroke * _GRID / size))
    p.scale(size / _GRID, size / _GRID)
    _draw(name, p)
    p.end()
    return pix


def icon(name: str, color: str = theme.TEXT, size: int = 18, stroke: float = 2.0) -> QIcon:
    """QIcon del icono ``name`` recoloreado. Usa el color del tema por defecto."""
    return QIcon(pixmap(name, color, size, stroke))


# Mapeo extensión -> (icono, color) para el árbol de archivos de un mod.
_TEX, _MESH, _SCR, _PLUG, _SND, _CFG, _ARC = (
    "#a371f7", theme.INFO, theme.SUCCESS, theme.ACCENT, "#f0a050", theme.TEXT_DIM, "#a371f7")
_FILE_ICONS = {
    ".dds": ("image", _TEX), ".png": ("image", _TEX), ".tga": ("image", _TEX), ".jpg": ("image", _TEX),
    ".nif": ("mesh", _MESH), ".tri": ("mesh", _MESH), ".btr": ("mesh", _MESH),
    ".bto": ("mesh", _MESH), ".hkx": ("mesh", _MESH),
    ".pex": ("script", _SCR), ".psc": ("script", _SCR),
    ".esp": ("plugin", _PLUG), ".esm": ("plugin", _PLUG), ".esl": ("plugin", _PLUG),
    ".wav": ("sound", _SND), ".xwm": ("sound", _SND), ".fuz": ("sound", _SND),
    ".lip": ("sound", _SND), ".mp3": ("sound", _SND), ".ogg": ("sound", _SND),
    ".ini": ("settings", _CFG), ".json": ("settings", _CFG),
    ".toml": ("settings", _CFG), ".cfg": ("settings", _CFG),
    ".bsa": ("package", _ARC), ".ba2": ("package", _ARC),
}


def file_icon(path: str, size: int = 18) -> QIcon:
    """Icono según la extensión del archivo (textura/malla/script/plugin/sonido/config…)."""
    from pathlib import Path as _P
    name, color = _FILE_ICONS.get(_P(str(path)).suffix.lower(), ("file", theme.TEXT_DIM))
    return icon(name, color, size)
