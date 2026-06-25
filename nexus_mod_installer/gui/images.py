"""Carga asíncrona de imágenes (miniaturas de mods) con caché en memoria y disco.

Usa QNetworkAccessManager (no bloquea el hilo de la GUI). Las imágenes se cachean en
disco para no volver a descargarlas. ``ThumbLabel`` muestra un marcador de posición y se
actualiza sola cuando la imagen llega.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QUrl, QStandardPaths, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply


def _cache_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    d = Path(base or ".") / "thumbnails"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


class _Loader(QObject):
    def __init__(self):
        super().__init__()
        self._nam = QNetworkAccessManager(self)
        self._mem: dict[str, QPixmap] = {}
        self._pending: dict[str, list] = {}
        self._dir = _cache_dir()

    def _disk(self, url: str) -> Path:
        return self._dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".img")

    def load(self, url: str, callback) -> None:
        """Pide la imagen de ``url`` y llama ``callback(QPixmap)`` cuando esté lista
        (de memoria/disco al instante, o tras descargarla)."""
        if not url:
            return
        pix = self._mem.get(url)
        if pix is not None:
            callback(pix)
            return
        dp = self._disk(url)
        if dp.is_file():
            p = QPixmap()
            if p.load(str(dp)) and not p.isNull():
                self._mem[url] = p
                callback(p)
                return
        if url in self._pending:
            self._pending[url].append(callback)
            return
        self._pending[url] = [callback]
        req = QNetworkRequest(QUrl(url))
        req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                         QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "BMI")
        reply = self._nam.get(req)
        reply.finished.connect(lambda r=reply, u=url: self._done(u, r))

    def _done(self, url: str, reply: QNetworkReply) -> None:
        callbacks = self._pending.pop(url, [])
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                data = bytes(reply.readAll())
                p = QPixmap()
                if data and p.loadFromData(data) and not p.isNull():
                    self._mem[url] = p
                    try:
                        self._disk(url).write_bytes(data)
                    except OSError:
                        pass
                    for cb in callbacks:
                        try:
                            cb(p)
                        except RuntimeError:
                            pass  # el widget destinatario ya no existe
        finally:
            reply.deleteLater()


_loader: _Loader | None = None


def loader() -> _Loader:
    global _loader
    if _loader is None:
        _loader = _Loader()
    return _loader


def _cover(pix: QPixmap, size: int) -> QPixmap:
    """Escala y recorta al centro para llenar un cuadrado de lado ``size``."""
    scaled = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
    x = max(0, (scaled.width() - size) // 2)
    y = max(0, (scaled.height() - size) // 2)
    return scaled.copy(x, y, size, size)


class ThumbLabel(QLabel):
    """Miniatura cuadrada que muestra un marcador y carga la imagen de ``url`` async."""

    def __init__(self, url: str = "", size: int = 44, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        from . import icons, theme
        self._placeholder = icons.pixmap("image", theme.TEXT_DIM, int(size * 0.55))
        self.setStyleSheet(f"background:{theme.BG_DARK}; border:1px solid {theme.BORDER};"
                           "border-radius:6px;")
        self.setPixmap(self._placeholder)
        if url:
            self.set_url(url)

    def set_url(self, url: str) -> None:
        if url:
            loader().load(url, self._apply)

    def _apply(self, pix: QPixmap) -> None:
        try:
            self.setPixmap(_cover(pix, self._size))
        except RuntimeError:
            pass


def make_icon_async(url: str, item, size: int = 28) -> None:
    """Carga ``url`` y la pone como icono de un QTableWidgetItem (robusto si el item ya
    fue destruido en un refresco de la tabla)."""
    from PySide6.QtGui import QIcon

    def apply(pix: QPixmap) -> None:
        try:
            item.setIcon(QIcon(_cover(pix, size)))
        except RuntimeError:
            pass

    loader().load(url, apply)
