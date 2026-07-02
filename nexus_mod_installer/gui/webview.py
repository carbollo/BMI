"""Navegador embebido de Nexus con interceptación de nxm:// y descargas manuales."""
from __future__ import annotations

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineDownloadRequest,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from .. import oauth


def home_url(domain: str) -> str:
    return f"https://www.nexusmods.com/{domain}"


class _InterceptPage(QWebEnginePage):
    """Página que captura navegaciones a nxm:// y al redirect de OAuth, y las reenvía."""

    nxm_requested = Signal(str)
    oauth_redirect = Signal(str)   # URL de REDIRECT_URI capturada (trae ?code=...)

    def __init__(self, profile: QWebEngineProfile, parent=None):
        super().__init__(profile, parent)

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:
        s = url.toString()
        if url.scheme().lower() == "nxm":
            self.nxm_requested.emit(s)
            return False  # no navegamos; lo gestionamos nosotros
        if oauth.LoginFlow.is_redirect(s):
            # Nexus nos devuelve a http://127.0.0.1/callback?code=...: lo capturamos aquí
            # (no se navega a 127.0.0.1) y completamos el intercambio del token.
            self.oauth_redirect.emit(s)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def createWindow(self, _window_type):
        """Para enlaces que abren pestaña nueva (target=_blank): cargar en la misma vista."""
        temp = _InterceptPage(self.profile(), self)
        temp.nxm_requested.connect(self.nxm_requested)
        temp.oauth_redirect.connect(self.oauth_redirect)

        def _load(u: QUrl):
            self.setUrl(u)
            temp.deleteLater()

        temp.urlChanged.connect(_load)
        return temp


class NexusWebView(QWebEngineView):
    """Vista del navegador de Nexus."""

    nxm_requested = Signal(str)
    oauth_redirect = Signal(str)           # redirect de OAuth capturado (trae ?code=...)
    manual_download_started = Signal(str)  # se aceptó una descarga (nombre de archivo)
    manual_file_downloaded = Signal(str)   # ruta del archivo descargado manualmente
    status_message = Signal(str)

    def __init__(self, downloads_dir: str, game_domain: str = "skyrimspecialedition", parent=None):
        super().__init__(parent)
        self._downloads_dir = downloads_dir
        self._game_domain = game_domain

        # Perfil persistente: mantiene la sesión iniciada entre ejecuciones.
        self._profile = QWebEngineProfile("bmi", self)
        self._profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self._profile.downloadRequested.connect(self._on_download_requested)

        self._page = _InterceptPage(self._profile, self)
        self._page.nxm_requested.connect(self.nxm_requested)
        self._page.oauth_redirect.connect(self.oauth_redirect)
        self.setPage(self._page)

        self.setUrl(QUrl(home_url(self._game_domain)))

    def set_downloads_dir(self, path: str) -> None:
        self._downloads_dir = path

    def set_game_domain(self, domain: str, navigate: bool = True) -> None:
        self._game_domain = domain
        if navigate:
            self.go_home()

    # ------------------------------------------------------------------
    def _on_download_requested(self, download: QWebEngineDownloadRequest) -> None:
        """Captura descargas 'manuales' (botón Slow/Manual download) y las instala."""
        try:
            download.setDownloadDirectory(self._downloads_dir)
        except Exception:
            pass
        file_name = download.downloadFileName()
        download.accept()
        self.manual_download_started.emit(file_name)   # la descarga ya arrancó
        self.status_message.emit(f"Descargando (manual): {file_name}...")

        def _on_state_change(state):
            if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                full = f"{download.downloadDirectory()}/{download.downloadFileName()}"
                self.status_message.emit(f"Descarga manual completada: {download.downloadFileName()}")
                self.manual_file_downloaded.emit(full)
            elif state in (
                QWebEngineDownloadRequest.DownloadState.DownloadCancelled,
                QWebEngineDownloadRequest.DownloadState.DownloadInterrupted,
            ):
                self.status_message.emit(f"Descarga manual fallida: {download.downloadFileName()}")

        download.stateChanged.connect(_on_state_change)

    # ------------------------------------------------------------------
    def go_home(self) -> None:
        self.setUrl(QUrl(home_url(self._game_domain)))

    def search(self, term: str) -> None:
        if not term.strip():
            return
        keyword = QUrl.toPercentEncoding(term.strip()).data().decode()
        self.setUrl(
            QUrl(f"https://www.nexusmods.com/games/{self._game_domain}/mods?keyword={keyword}")
        )

    def open_mod_page(self, game_domain: str, mod_id: int) -> None:
        self.setUrl(QUrl(f"https://www.nexusmods.com/{game_domain}/mods/{mod_id}?tab=files"))

    # JS que lee el atributo download-links (sección Requirements) de la página cargada.
    # Nexus lo pone en <main-file-requirements>; trae el JSON de dependencias del archivo.
    _REQUIREMENTS_JS = (
        "(function(){var e=document.querySelector('main-file-requirements');"
        "return e?(e.getAttribute('download-links')||''):'';})()"
    )

    def read_requirements(self, callback) -> None:
        """Lee las dependencias declaradas en la página del mod (Requirements) y llama a
        ``callback(json_str)`` con el JSON (cadena) del atributo download-links, o ''."""
        try:
            self.page().runJavaScript(self._REQUIREMENTS_JS, callback)
        except Exception:
            callback("")
