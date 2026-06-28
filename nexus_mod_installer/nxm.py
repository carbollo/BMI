"""Registro del protocolo nxm:// en Windows y utilidades relacionadas.

Cuando registras la app como manejador de ``nxm://``, al pulsar "Mod Manager
Download" en la web de Nexus (incluso en tu navegador normal, Chrome/Firefox),
Windows abrirá esta aplicación pasándole el enlace nxm como argumento.
"""
from __future__ import annotations

import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")


def _launch_command() -> str:
    """Construye el comando que Windows ejecutará para abrir un nxm://.

    Soporta ejecución como script (python) o como .exe empaquetado (Nuitka onefile,
    Nuitka standalone o PyInstaller). IMPORTANTE: en Nuitka *onefile* el proceso corre
    desde una carpeta temporal que se BORRA al cerrar, así que NO se puede registrar
    ``sys.executable``/``__file__`` (apuntarían a esa temporal). Nuitka expone el .exe
    real en la variable de entorno ``NUITKA_ONEFILE_BINARY``.
    """
    import os

    onefile = os.environ.get("NUITKA_ONEFILE_BINARY")
    if onefile:
        # Nuitka onefile: el .exe REAL y estable (no la carpeta temporal de ejecución).
        return f'"{Path(onefile)}" "%1"'
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        # PyInstaller (sys.frozen) o Nuitka standalone: sys.executable ya es el .exe real.
        return f'"{Path(sys.executable)}" "%1"'

    # Ejecución como script: usamos pythonw.exe (sin consola) si existe.
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    runner = pyw if pyw.exists() else exe
    # Apuntamos al módulo principal del paquete.
    project_root = Path(__file__).resolve().parent.parent
    main_script = project_root / "run.py"
    return f'"{runner}" "{main_script}" "%1"'


def register_protocol() -> tuple[bool, str]:
    """Registra nxm:// en HKEY_CURRENT_USER (no requiere admin).

    Devuelve (ok, mensaje).
    """
    if not IS_WINDOWS:
        return False, "El registro del protocolo nxm:// solo está implementado para Windows."

    import winreg

    command = _launch_command()
    try:
        # HKCU\Software\Classes\nxm
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\nxm") as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, "URL:Nexus Mod Manager Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, r"Software\Classes\nxm\shell\open\command"
        ) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, command)
        return True, f"Protocolo nxm:// registrado.\nComando: {command}"
    except OSError as e:
        return False, f"No se pudo registrar el protocolo: {e}"


def is_protocol_registered() -> bool:
    """Comprueba si nxm:// está registrado apuntando a algo."""
    if not IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Classes\nxm\shell\open\command"
        ) as key:
            val, _ = winreg.QueryValueEx(key, None)
            return bool(val)
    except OSError:
        return False


def unregister_protocol() -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Solo Windows."
    import winreg

    def _del(path: str) -> None:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            pass

    # Hay que borrar de hoja a raíz.
    for path in (
        r"Software\Classes\nxm\shell\open\command",
        r"Software\Classes\nxm\shell\open",
        r"Software\Classes\nxm\shell",
        r"Software\Classes\nxm",
    ):
        _del(path)
    return True, "Protocolo nxm:// eliminado."
