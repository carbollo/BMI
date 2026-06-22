"""Punto de entrada: arranca la aplicación o reenvía un nxm:// a la instancia abierta."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QCoreApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from .config import AppConfig
from .ipc import SingleInstance
from . import nxm


def _extract_nxm_arg(argv: list[str]) -> str | None:
    for a in argv[1:]:
        if a.lower().startswith("nxm://"):
            return a
    return None


def _suppress_console_windows() -> None:
    """En Windows, evita que CUALQUIER subproceso (7-Zip, NanaZip, WinRAR/UnRAR, etc.) abra
    una ventana de consola, ya que la app no tiene consola propia. Parchea subprocess.Popen
    para añadir CREATE_NO_WINDOW por defecto; afecta también a subprocess.run/check_output y a
    librerías como rarfile que lanzan el desempaquetador externo."""
    if sys.platform != "win32":
        return
    import subprocess
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    orig = subprocess.Popen
    if getattr(orig, "_bmi_nowindow", False):
        return

    class _Popen(orig):
        _bmi_nowindow = True

        def __init__(self, *args, **kwargs):
            cf = kwargs.get("creationflags", 0) or 0
            if not (cf & 0x00000010):           # respeta CREATE_NEW_CONSOLE si se pidió
                kwargs["creationflags"] = cf | flag
            super().__init__(*args, **kwargs)

    subprocess.Popen = _Popen


def main(argv: list[str] | None = None) -> int:
    _suppress_console_windows()
    argv = list(argv if argv is not None else sys.argv)
    nxm_link = _extract_nxm_arg(argv)

    # QtWebEngine necesita contextos OpenGL compartidos (antes de crear QApplication).
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication(argv)
    app.setApplicationName("BMI")
    app.setOrganizationName("BMI")

    # Tema oscuro estilo Nexus + icono.
    from .gui import theme
    app.setStyleSheet(theme.STYLESHEET)
    app.setWindowIcon(theme.make_app_icon())

    # --- Instancia única ---
    instance = SingleInstance()
    if not instance.try_acquire():
        # Ya hay una ventana abierta: le pasamos el enlace y salimos.
        if nxm_link:
            SingleInstance.send_to_primary(nxm_link)
        return 0

    # --- Configuración ---
    config = AppConfig.load()

    # Idioma de la interfaz (debe fijarse ANTES de construir la GUI).
    from . import i18n
    i18n.set_language(config.language)

    # Importamos aquí (QtWebEngine ya está listo).
    from .manager import DownloadManager
    from .gui.main_window import MainWindow

    manager = DownloadManager(config)
    window = MainWindow(config, manager)

    # Enlaces nxm:// que lleguen desde el navegador externo.
    instance.message_received.connect(window.handle_external_link)

    window.show()

    # Primera ejecución: registrar protocolo y pedir ajustes si falta config.
    if not config.protocol_registered and not nxm.is_protocol_registered():
        ok, msg = nxm.register_protocol()
        config.protocol_registered = ok
        config.save()

    if not config.is_configured:
        from .gui.first_run_wizard import FirstRunWizard
        FirstRunWizard(config, window).exec()
        window.webview.set_downloads_dir(config.downloads_dir)
        window.apply_config_game()
    manager.update_credentials()

    # Enlace recibido en el arranque (la app se abrió desde un nxm://).
    if nxm_link:
        window.handle_external_link(nxm_link)

    return app.exec()
