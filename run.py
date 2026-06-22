#!/usr/bin/env python
"""Lanzador de Nexus Mod Installer.

Uso normal (doble clic en 'iniciar.bat', o desde una terminal):
    python run.py

Windows lo llama también automáticamente al abrir un enlace nxm://:
    pythonw run.py "nxm://skyrimspecialedition/mods/266/files/1000123?key=...&expires=..."

Si algo falla al iniciar, se escribe el error en un archivo de log y se intenta
mostrar un cuadro de diálogo (útil cuando se lanza sin consola).
"""
import sys
import traceback
from pathlib import Path


def _crash_log_path() -> Path:
    import os
    base = os.environ.get("APPDATA")
    folder = Path(base) / "BMI" if base else Path.home()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        folder = Path.home()
    return folder / "error_inicio.log"


def _run() -> int:
    # Permite ejecutar el script directamente sin instalar el paquete.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from nexus_mod_installer.main import main
    return main(sys.argv)


if __name__ == "__main__":
    try:
        sys.exit(_run())
    except SystemExit:
        raise
    except BaseException:
        tb = traceback.format_exc()
        log = _crash_log_path()
        try:
            log.write_text(tb, encoding="utf-8")
        except Exception:
            log = None
        # Mensaje legible para el error más típico (Python sin PySide6).
        hint = ""
        if "PySide6" in tb or "ModuleNotFoundError" in tb:
            hint = (
                "\n\nParece que falta PySide6 en este Python.\n"
                "Lánzalo con 'iniciar.bat' o instala dependencias con:\n"
                "  pip install -r requirements.txt\n"
            )
        print(tb)
        print(hint)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(
                None,
                "Error al iniciar Nexus Mod Installer",
                tb[-1800:] + hint + (f"\n\nLog: {log}" if log else ""),
            )
        except Exception:
            pass
        sys.exit(1)
