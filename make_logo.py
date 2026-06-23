"""Genera el logo de BMI en alta resolución (PNG transparentes + SVG + banner).

Marca: insignia cuadrada con degradado dorado→naranja y "BMI" en negro intenso.
Los colores claros (dorado/naranja) destacan sobre fondos OSCUROS, como pide Nexus.

Uso:  python make_logo.py        (genera la carpeta logo/)
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (
    QGuiApplication, QImage, QPainter, QColor, QLinearGradient, QFont,
    QFontMetrics, QFontDatabase, QPainterPath, QBrush, QPen,
)

GOLD = QColor("#ffcf8a")
ACCENT = QColor("#e08b3e")
DARK = QColor("#1a0f04")
LIGHT = QColor("#ececf0")
DIM = QColor("#b9b9c2")

OUT = Path(__file__).resolve().parent / "logo"

# Familia cargada explícitamente desde un .ttf de Windows (robusto en modo offscreen,
# donde la fuente del sistema por nombre puede no resolverse y salir glifos vacíos).
_FAMILY = "Arial"


def _load_font() -> str:
    for fp in (r"C:\Windows\Fonts\ariblk.ttf",    # Arial Black
               r"C:\Windows\Fonts\seguibl.ttf",   # Segoe UI Black
               r"C:\Windows\Fonts\segoeuib.ttf",  # Segoe UI Bold
               r"C:\Windows\Fonts\arialbd.ttf"):  # Arial Bold
        fid = QFontDatabase.addApplicationFont(fp)
        if fid != -1:
            fams = QFontDatabase.applicationFontFamilies(fid)
            if fams:
                return fams[0]
    return "Arial"


def _heavy_font(px: int) -> QFont:
    f = QFont(_FAMILY, 10)
    f.setWeight(QFont.Weight.Black)
    f.setPixelSize(px)
    return f


def _fit_font(text: str, target_w: float, max_px: int) -> QFont:
    px = 12
    f = _heavy_font(px)
    while px < max_px and QFontMetrics(f).horizontalAdvance(text) < target_w:
        px += 2
        f = _heavy_font(px)
    return f


def _badge(p: QPainter, rect: QRectF) -> None:
    radius = rect.width() * 0.22
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0.0, GOLD)
    grad.setColorAt(1.0, ACCENT)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.fillPath(path, QBrush(grad))
    # "BMI" centrado dentro de la insignia
    f = _fit_font("BMI", rect.width() * 0.64, int(rect.height()))
    p.setFont(f)
    p.setPen(QPen(DARK))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "BMI")


def render_square(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    m = size * 0.10
    _badge(p, QRectF(m, m, size - 2 * m, size - 2 * m))
    p.end()
    return img


def render_banner(w: int, h: int) -> QImage:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    # insignia a la izquierda
    badge = h * 0.78
    by = (h - badge) / 2
    _badge(p, QRectF(by, by, badge, badge))
    # wordmark a la derecha (texto claro -> visible en oscuro)
    x = by + badge + h * 0.22
    title = _heavy_font(int(h * 0.34))
    p.setFont(title)
    p.setPen(QPen(GOLD))
    fm = QFontMetrics(title)
    p.drawText(int(x), int(h * 0.30 + fm.ascent() / 2), "BMI")
    sub = QFont("Segoe UI", 10)
    sub.setWeight(QFont.Weight.DemiBold)
    sub.setPixelSize(int(h * 0.135))
    sub.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
    p.setFont(sub)
    p.setPen(QPen(DIM))
    fm2 = QFontMetrics(sub)
    p.drawText(int(x) + 4, int(h * 0.62 + fm2.ascent() / 2), "BETHESDA MOD INSTALLER")
    p.end()
    return img


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#ffcf8a"/>
      <stop offset="1" stop-color="#e08b3e"/>
    </linearGradient>
  </defs>
  <rect x="102" y="102" width="820" height="820" rx="180" fill="url(#g)"/>
  <text x="512" y="512" font-family="Segoe UI, Arial, sans-serif" font-size="330"
        font-weight="900" fill="#1a0f04" text-anchor="middle"
        dominant-baseline="central">BMI</text>
</svg>
"""


def main() -> None:
    app = QGuiApplication(sys.argv)  # necesario para fuentes/rasterizado
    global _FAMILY
    _FAMILY = _load_font()
    OUT.mkdir(exist_ok=True)
    for s in (1024, 512, 256):
        render_square(s).save(str(OUT / f"bmi-logo-{s}.png"))
    render_banner(1600, 460).save(str(OUT / "bmi-banner-1600x460.png"))
    (OUT / "bmi-logo.svg").write_text(SVG, encoding="utf-8")
    print("Generado en:", OUT)
    for f in sorted(OUT.iterdir()):
        print("  -", f.name, f"({f.stat().st_size} bytes)")
    app.quit()


if __name__ == "__main__":
    main()
