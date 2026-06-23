"""Genera el logo de BMI: casco nórdico (estilo Skyrim) en alta resolución.

Salida (carpeta logo/, fondo transparente):
  bmi-logo-1024.png / -512 / -256   ·   bmi-banner-1600x460.png

  python make_logo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QGuiApplication, QImage, QPainter, QColor, QFont, QFontDatabase,
    QPainterPath, QPen,
)

ORANGE = QColor("#e8913f")
ORANGE_D = QColor("#c8742c")
GOLD = QColor("#ffce86")
HORN = QColor("#f2e3c4")
HORN_D = QColor("#d8c39a")
DIM = QColor("#b9b9c2")

OUT = Path(__file__).resolve().parent / "logo"
_FAMILY = "Arial"


def _load_font() -> str:
    for fp in (r"C:\Windows\Fonts\ariblk.ttf", r"C:\Windows\Fonts\arialbd.ttf"):
        fid = QFontDatabase.addApplicationFont(fp)
        if fid != -1:
            fams = QFontDatabase.applicationFontFamilies(fid)
            if fams:
                return fams[0]
    return "Arial"


def _font(px: int) -> QFont:
    f = QFont(_FAMILY, 10)
    f.setWeight(QFont.Weight.Black)
    f.setPixelSize(px)
    return f


def _horn(p: QPainter, cx, cy, u, s):
    """Cuerno (marfil) desde el lateral del casco hacia fuera y arriba. s = ±1."""
    bx = cx + s * 0.40 * u
    by = cy - 0.30 * u
    path = QPainterPath()
    path.moveTo(bx, by + 0.14 * u)
    path.cubicTo(cx + s * 0.82 * u, cy - 0.20 * u, cx + s * 1.05 * u, cy - 0.55 * u, cx + s * 0.86 * u, cy - 0.98 * u)
    path.cubicTo(cx + s * 0.90 * u, cy - 0.58 * u, cx + s * 0.64 * u, cy - 0.34 * u, bx - s * 0.04 * u, by - 0.10 * u)
    path.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(HORN)
    p.drawPath(path)
    band = QPainterPath()
    band.moveTo(bx, by + 0.06 * u)
    band.cubicTo(cx + s * 0.74 * u, cy - 0.24 * u, cx + s * 0.92 * u, cy - 0.5 * u, cx + s * 0.84 * u, cy - 0.7 * u)
    band.cubicTo(cx + s * 0.7 * u, cy - 0.5 * u, cx + s * 0.6 * u, cy - 0.34 * u, bx - s * 0.02 * u, by - 0.04 * u)
    band.closeSubpath()
    p.setBrush(HORN_D)
    p.drawPath(band)


def helmet(p: QPainter, cx, cy, u):
    p.setPen(Qt.PenStyle.NoPen)
    _horn(p, cx, cy, u, -1)
    _horn(p, cx, cy, u, +1)
    helm = QPainterPath()
    helm.moveTo(cx - 0.5 * u, cy + 0.5 * u)
    helm.lineTo(cx - 0.5 * u, cy - 0.05 * u)
    helm.cubicTo(cx - 0.5 * u, cy - 0.52 * u, cx - 0.22 * u, cy - 0.66 * u, cx, cy - 0.66 * u)
    helm.cubicTo(cx + 0.22 * u, cy - 0.66 * u, cx + 0.5 * u, cy - 0.52 * u, cx + 0.5 * u, cy - 0.05 * u)
    helm.lineTo(cx + 0.5 * u, cy + 0.5 * u)
    helm.lineTo(cx + 0.3 * u, cy + 0.5 * u)
    helm.cubicTo(cx + 0.3 * u, cy + 0.16 * u, cx - 0.3 * u, cy + 0.16 * u, cx - 0.3 * u, cy + 0.5 * u)
    helm.closeSubpath()
    p.setBrush(ORANGE)
    p.drawPath(helm)
    brow = QPainterPath()
    brow.addRoundedRect(QRectF(cx - 0.5 * u, cy - 0.16 * u, u, 0.16 * u), 0.04 * u, 0.04 * u)
    p.setBrush(ORANGE_D)
    p.drawPath(brow)
    p.setBrush(GOLD)
    for k in (-0.4, -0.2, 0.2, 0.4):
        p.drawEllipse(QPointF(cx + k * u, cy - 0.08 * u), 0.028 * u, 0.028 * u)
    nose = QPainterPath()
    nose.moveTo(cx - 0.07 * u, cy - 0.08 * u)
    nose.lineTo(cx + 0.07 * u, cy - 0.08 * u)
    nose.lineTo(cx + 0.06 * u, cy + 0.4 * u)
    nose.cubicTo(cx + 0.06 * u, cy + 0.46 * u, cx - 0.06 * u, cy + 0.46 * u, cx - 0.06 * u, cy + 0.4 * u)
    nose.closeSubpath()
    p.setBrush(ORANGE_D)
    p.drawPath(nose)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    for s in (-1, 1):
        eye = QPainterPath()
        eye.moveTo(cx + s * 0.14 * u, cy + 0.02 * u)
        eye.lineTo(cx + s * 0.27 * u, cy + 0.04 * u)
        eye.lineTo(cx + s * 0.27 * u, cy + 0.12 * u)
        eye.lineTo(cx + s * 0.14 * u, cy + 0.12 * u)
        eye.closeSubpath()
        p.drawPath(eye)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


def _canvas(w, h) -> tuple[QImage, QPainter]:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    return img, p


def render_square(size: int) -> QImage:
    img, p = _canvas(size, size)
    u = size * 0.40
    helmet(p, size / 2, size / 2 + 0.22 * u, u)   # baja el centro: los cuernos suben más
    p.end()
    return img


def render_banner(w: int, h: int) -> QImage:
    img, p = _canvas(w, h)
    u = h * 0.30
    helmet(p, h * 0.62, h / 2 + 0.16 * u, u)
    x = h * 1.16
    title = _font(int(h * 0.34))
    p.setFont(title)
    p.setPen(QPen(GOLD))
    from PySide6.QtGui import QFontMetrics
    fm = QFontMetrics(title)
    p.drawText(int(x), int(h * 0.34 + fm.ascent() / 2), "BMI")
    sub = QFont(_FAMILY, 10)
    sub.setWeight(QFont.Weight.DemiBold)
    sub.setPixelSize(int(h * 0.135))
    sub.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
    p.setFont(sub)
    p.setPen(QPen(DIM))
    fm2 = QFontMetrics(sub)
    p.drawText(int(x) + 4, int(h * 0.66 + fm2.ascent() / 2), "BETHESDA MOD INSTALLER")
    p.end()
    return img


def main() -> None:
    app = QGuiApplication(sys.argv)
    global _FAMILY
    _FAMILY = _load_font()
    OUT.mkdir(exist_ok=True)
    for s in (1024, 512, 256):
        render_square(s).save(str(OUT / f"bmi-logo-{s}.png"))
    render_banner(1600, 460).save(str(OUT / "bmi-banner-1600x460.png"))
    print("Generado en:", OUT)
    for f in sorted(OUT.glob("bmi-*")):
        print("  -", f.name)
    app.quit()


if __name__ == "__main__":
    main()
