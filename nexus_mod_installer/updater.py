"""Autoactualización desde los Releases de GitHub.

Al arrancar (o desde Ajustes → «Buscar actualizaciones»), BMI consulta la API de releases de
GitHub y, si hay una versión MÁS NUEVA que la suya, avisa. Al aceptar, descarga el ``BMI.exe``
del release y lo aplica: como en Windows un .exe en marcha está bloqueado, se escribe un pequeño
.bat que espera a que este proceso cierre, reemplaza el .exe por el nuevo y vuelve a abrir BMI.
Todo es silencioso ante fallos (sin red, API caída…): nunca interrumpe el uso normal.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from . import __version__

GITHUB_API = "https://api.github.com/repos/carbollo/BMI/releases/latest"
RELEASES_PAGE = "https://github.com/carbollo/BMI/releases/latest"
ASSET_NAME = "BMI.exe"   # el onefile portable: sirve para reemplazar el .exe en marcha

_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
_DETACHED = 0x00000008   # DETACHED_PROCESS


def _parse_version(s: str) -> tuple:
    """'v1.2.10' / '1.2.10' -> (1, 2, 10). Tolerante a sufijos ('1.2.0-beta')."""
    parts = []
    for p in (s or "").strip().lstrip("vV").split("."):
        num = ""
        for ch in p:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def current_version() -> tuple:
    return _parse_version(__version__)


def is_frozen() -> bool:
    """True si corremos como .exe compilado (no desde `python run.py`)."""
    try:
        if getattr(sys, "frozen", False):
            return True
        return bool(sys.argv and str(sys.argv[0]).lower().endswith(".exe"))
    except Exception:  # noqa: BLE001
        return False


def current_exe() -> str:
    return os.path.abspath(sys.argv[0]) if sys.argv else ""


def check_latest() -> dict | None:
    """Devuelve {version, tag, url, notes, asset_url} si en GitHub hay una versión MÁS NUEVA
    que la actual; None si no hay novedad, no hay red o falla. Nunca lanza."""
    try:
        import requests
        r = requests.get(GITHUB_API, timeout=8, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "BMI-Updater"})
        if r.status_code != 200:
            return None
        data = r.json()
        tag = data.get("tag_name", "") or ""
        latest = _parse_version(tag)
        if latest <= current_version():
            return None
        asset_url = ""
        for a in data.get("assets", []) or []:
            if (a.get("name", "") or "").lower() == ASSET_NAME.lower():
                asset_url = a.get("browser_download_url", "") or ""
                break
        if not asset_url:
            asset_url = f"https://github.com/carbollo/BMI/releases/download/{tag}/{ASSET_NAME}"
        return {
            "version": ".".join(str(n) for n in latest),
            "tag": tag,
            "url": data.get("html_url", RELEASES_PAGE) or RELEASES_PAGE,
            "notes": (data.get("body", "") or "")[:1500],
            "asset_url": asset_url,
        }
    except Exception:  # noqa: BLE001
        return None


def download_asset(url: str, dest: str, progress_cb=None, should_cancel=None) -> str:
    """Descarga ``url`` a ``dest`` en streaming. ``progress_cb(descargado, total)`` para la
    barra; ``should_cancel()`` -> True aborta (borra el parcial y lanza). Lanza en error."""
    import requests
    with requests.get(url, stream=True, timeout=30, headers={"User-Agent": "BMI-Updater"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0) or 0)
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if should_cancel and should_cancel():
                    f.close()
                    try:
                        os.remove(dest)
                    except OSError:
                        pass
                    raise RuntimeError("cancelado")
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        progress_cb(done, total)
    return dest


def apply_update(new_exe: str) -> bool:
    """Programa el reemplazo del .exe en marcha por ``new_exe`` y su reapertura. Escribe un
    .bat que REINTENTA mover el nuevo sobre el actual hasta que el .exe se libere (al cerrar
    BMI), y luego reabre BMI. No depende del PID (en un onefile el proceso que bloquea el .exe
    no es el de Python). Devuelve True si lanzó el relevo (el llamador debe CERRAR la app YA)."""
    cur = current_exe()
    if not cur or not new_exe or not os.path.isfile(new_exe):
        return False
    bat = os.path.join(tempfile.gettempdir(), f"bmi_update_{os.getpid()}.bat")
    # chcp 65001 + UTF-8 para que las rutas con acentos (nombre de usuario…) no se corrompan.
    # Reintenta el 'move' (~90 x 2s = 3 min) porque el .exe sigue bloqueado hasta que BMI cierre.
    script = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "set /a n=0\r\n"
        ":retry\r\n"
        f'move /Y "{new_exe}" "{cur}" >nul 2>&1\r\n'
        f'if not exist "{new_exe}" goto done\r\n'
        "set /a n+=1\r\n"
        "if %n% geq 90 goto done\r\n"
        ">nul ping -n 2 127.0.0.1\r\n"
        "goto retry\r\n"
        ":done\r\n"
        f'start "" "{cur}"\r\n'
        'del "%~f0"\r\n'
    )
    try:
        with open(bat, "w", encoding="utf-8") as f:
            f.write(script)
        subprocess.Popen(["cmd", "/c", bat],
                         creationflags=_NO_WINDOW | _DETACHED, close_fds=True)
        return True
    except Exception:  # noqa: BLE001
        return False
