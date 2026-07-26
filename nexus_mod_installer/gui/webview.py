"""Navegador embebido de Nexus sobre Microsoft Edge WebView2 (motor del sistema).

Sustituye a QtWebEngine (Chromium empaquetado, ~130 MB) por el runtime WebView2 que ya trae
Windows, para que el .exe pese mucho menos. Conserva EXACTAMENTE la misma API pública que
usaba la versión con QtWebEngine (señales y métodos), así el resto de la app no cambia:

  Señales: nxm_requested, oauth_redirect, manual_download_started, manual_file_downloaded,
           status_message, urlChanged
  Métodos: setUrl, url, back, forward, reload, go_home, search, open_mod_page,
           set_downloads_dir, set_game_domain, read_requirements, profile

Si el runtime WebView2 no está instalado, la vista muestra un aviso (no revienta la app).
"""
from __future__ import annotations

import os

from PySide6.QtCore import QUrl, Signal, Qt, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton

from . import wv2


def home_url(domain: str) -> str:
    return f"https://www.nexusmods.com/{domain}"


def _is_oauth_redirect(url: str) -> bool:
    try:
        from .. import oauth
        return bool(oauth.LoginFlow.is_redirect(url))
    except Exception:  # noqa: BLE001
        return False


class NexusWebView(QWidget):
    """Vista del navegador de Nexus (WebView2 incrustado por HWND)."""

    nxm_requested = Signal(str)
    oauth_redirect = Signal(str)
    manual_download_started = Signal(str)
    manual_file_downloaded = Signal(str)
    status_message = Signal(str)
    urlChanged = Signal(QUrl)

    _REQUIREMENTS_JS = (
        "(function(){var e=document.querySelector('main-file-requirements');"
        "return e?(e.getAttribute('download-links')||''):'';})()"
    )

    def __init__(self, downloads_dir: str, game_domain: str = "skyrimspecialedition", parent=None):
        super().__init__(parent)
        self._downloads_dir = downloads_dir
        self._game_domain = game_domain
        self._ctrl = None          # CoreWebView2Controller
        self._wv = None            # CoreWebView2
        self._cur_url = ""
        self._pending_url = home_url(game_domain)   # navegar en cuanto el controller esté listo

        # HWND nativo propio para hospedar WebView2.
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._lay = lay
        self._placeholder = QLabel(
            "Cargando el navegador de Nexus…", self)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setProperty("role", "dim")
        lay.addWidget(self._placeholder)
        self._install_btn = None

        # Pedir el entorno compartido y crear el controller cuando esté listo.
        QTimer.singleShot(0, self._create)

    # ------------------------------------------------------------------ init
    def _create(self) -> None:
        if not wv2.available():
            self._show_missing_runtime()
            return
        hwnd = int(self.winId())
        wv2.ensure_environment(lambda env: self._on_env(env, hwnd))

    def _show_missing_runtime(self) -> None:
        self._placeholder.setText(
            "No se encontró Microsoft Edge WebView2 en el sistema.\n\n"
            "BMI usa el motor de Edge (WebView2) para el navegador de Nexus. En Windows 11\n"
            "suele venir ya instalado; si no, puedes instalarlo aquí en un clic.")
        if self._install_btn is None and wv2.bootstrapper_path():
            self._install_btn = QPushButton("Instalar WebView2 ahora", self)
            self._install_btn.setProperty("variant", "primary")
            self._install_btn.clicked.connect(self._install_runtime)
            self._lay.addWidget(self._install_btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def _install_runtime(self) -> None:
        if wv2.install_runtime():
            self._placeholder.setText(
                "Instalando Microsoft Edge WebView2…\n\n"
                "Cuando termine, cierra y vuelve a abrir BMI para usar el navegador.")
            if self._install_btn is not None:
                self._install_btn.setEnabled(False)
        else:
            import webbrowser
            webbrowser.open("https://developer.microsoft.com/microsoft-edge/webview2/")

    def _on_env(self, env, hwnd) -> None:
        if env is None:
            self._placeholder.setText("No se pudo iniciar el navegador (WebView2).")
            return
        try:
            from System import IntPtr, Action
            from System.Threading.Tasks import TaskScheduler
            task = env.CreateCoreWebView2ControllerAsync(IntPtr(hwnd))
            task.ContinueWith(Action[type(task)](self._on_controller),
                              TaskScheduler.FromCurrentSynchronizationContext())
        except Exception:  # noqa: BLE001
            self._placeholder.setText("No se pudo crear el navegador (WebView2).")

    def _on_controller(self, task) -> None:
        try:
            self._ctrl = task.Result
            self._wv = self._ctrl.CoreWebView2
        except Exception:  # noqa: BLE001
            self._placeholder.setText("No se pudo crear el navegador (WebView2).")
            return
        self._placeholder.hide()
        try:
            self._ctrl.IsVisible = True     # asegurar que la vista es visible
        except Exception:  # noqa: BLE001
            pass
        self._fit()
        QTimer.singleShot(50, self._fit)    # re-ajustar cuando el layout ya esté asentado
        self._wire_events()
        if self._pending_url:
            self._navigate(self._pending_url)
            self._pending_url = ""

    def _wire_events(self) -> None:
        wv = self._wv
        wv.NavigationStarting += self._on_nav_starting
        wv.SourceChanged += self._on_source_changed
        wv.NewWindowRequested += self._on_new_window
        wv.DownloadStarting += self._on_download_starting

    # ------------------------------------------------------------------ events
    def _on_nav_starting(self, sender, args) -> None:
        try:
            uri = args.Uri or ""
        except Exception:  # noqa: BLE001
            return
        low = uri.lower()
        if low.startswith("nxm:"):
            args.Cancel = True
            self.nxm_requested.emit(uri)
        elif _is_oauth_redirect(uri):
            args.Cancel = True
            self.oauth_redirect.emit(uri)

    def _on_source_changed(self, sender, args) -> None:
        try:
            self._cur_url = self._wv.Source or ""
        except Exception:  # noqa: BLE001
            self._cur_url = ""
        self.urlChanged.emit(QUrl(self._cur_url))

    def _on_new_window(self, sender, args) -> None:
        # Enlaces target=_blank: cargar en la MISMA vista en vez de abrir ventana nueva.
        try:
            args.Handled = True
            uri = args.Uri or ""
            if uri:
                self._navigate(uri)
        except Exception:  # noqa: BLE001
            pass

    def _on_download_starting(self, sender, args) -> None:
        try:
            op = args.DownloadOperation
            # Redirige el destino a la carpeta de descargas de BMI.
            name = os.path.basename(op.ResultFilePath or "") or "descarga"
            dest = os.path.join(self._downloads_dir, name)
            args.ResultFilePath = dest
        except Exception:  # noqa: BLE001
            op = None
            name = "descarga"
            dest = ""
        self.manual_download_started.emit(name)
        self.status_message.emit(f"Descargando (manual): {name}…")

        if op is None:
            return

        def _state_changed(s, a):
            try:
                from Microsoft.Web.WebView2.Core import CoreWebView2DownloadState as St
                st = op.State
                if st == St.Completed:
                    full = op.ResultFilePath or dest
                    self.status_message.emit(f"Descarga manual completada: {os.path.basename(full)}")
                    self.manual_file_downloaded.emit(str(full))
                elif st == St.Interrupted:
                    self.status_message.emit(f"Descarga manual fallida: {name}")
            except Exception:  # noqa: BLE001
                pass
        try:
            op.StateChanged += _state_changed
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ layout
    def _fit(self) -> None:
        if self._ctrl is None:
            return
        try:
            from System.Drawing import Rectangle
            # Los Bounds de WebView2 van en píxeles FÍSICOS (el área cliente del HWND). El
            # tamaño de Qt es lógico; hay que multiplicar por el factor de escala (DPI), o en
            # pantallas escaladas (125/150 %) el navegador ocuparía solo una esquina.
            dpr = self.devicePixelRatioF()
            w = max(0, int(round(self.width() * dpr)))
            h = max(0, int(round(self.height() * dpr)))
            self._ctrl.Bounds = Rectangle(0, 0, w, h)
        except Exception:  # noqa: BLE001
            pass

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit()

    def showEvent(self, e) -> None:
        # Al mostrarse la pestaña, re-ajustar el tamaño del navegador: cuando el controller se
        # creó, esta pestaña podía no estar visible aún (tamaño 0 = pantalla negra).
        super().showEvent(e)
        self._fit()

    # ------------------------------------------------------------------ API pública
    def _navigate(self, url: str) -> None:
        if self._wv is not None:
            try:
                self._wv.Navigate(url)
                return
            except Exception:  # noqa: BLE001
                pass
        self._pending_url = url

    def setUrl(self, url) -> None:
        s = url.toString() if isinstance(url, QUrl) else str(url)
        self._navigate(s)

    def url(self) -> QUrl:
        return QUrl(self._cur_url)

    def back(self) -> None:
        try:
            if self._wv and self._wv.CanGoBack:
                self._wv.GoBack()
        except Exception:  # noqa: BLE001
            pass

    def forward(self) -> None:
        try:
            if self._wv and self._wv.CanGoForward:
                self._wv.GoForward()
        except Exception:  # noqa: BLE001
            pass

    def reload(self) -> None:
        try:
            if self._wv:
                self._wv.Reload()
        except Exception:  # noqa: BLE001
            pass

    def go_home(self) -> None:
        self._navigate(home_url(self._game_domain))

    def search(self, term: str) -> None:
        if not term.strip():
            return
        kw = QUrl.toPercentEncoding(term.strip()).data().decode()
        self._navigate(f"https://www.nexusmods.com/games/{self._game_domain}/mods?keyword={kw}")

    def open_mod_page(self, game_domain: str, mod_id: int) -> None:
        self._navigate(f"https://www.nexusmods.com/{game_domain}/mods/{mod_id}?tab=files")

    def set_downloads_dir(self, path: str) -> None:
        self._downloads_dir = path

    def set_game_domain(self, domain: str, navigate: bool = True) -> None:
        self._game_domain = domain
        if navigate:
            self.go_home()

    def profile(self):
        """Entorno WebView2 compartido (cookies/sesión). Lo usa el escáner de traducciones
        para abrir páginas de mod ocultas con la misma sesión (pasa Cloudflare)."""
        return wv2

    def read_requirements(self, callback) -> None:
        """Lee las dependencias declaradas en la página del mod (Requirements) y llama a
        ``callback(json_str)`` con el JSON del atributo download-links, o ''."""
        if self._wv is None:
            callback("")
            return
        try:
            from System import Action
            from System.Threading.Tasks import TaskScheduler
            t = self._wv.ExecuteScriptAsync(self._REQUIREMENTS_JS)

            def _done(task):
                try:
                    raw = task.Result or ""
                    # ExecuteScript devuelve el valor JSON-encoded (una cadena entre comillas).
                    import json
                    val = json.loads(raw) if raw and raw != "null" else ""
                except Exception:  # noqa: BLE001
                    val = ""
                callback(val if isinstance(val, str) else "")
            t.ContinueWith(Action[type(t)](_done), TaskScheduler.FromCurrentSynchronizationContext())
        except Exception:  # noqa: BLE001
            callback("")
