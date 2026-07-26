"""Copia de seguridad y restauración de la configuración de BMI.

Empaqueta en un .zip todo lo que vive en ``app_data_dir()`` y define tu setup: ``config.json``
(rutas, herramientas, ajustes), los perfiles, las listas de mods instalados por juego
(``installed_mods_*.json``) y el estado de la vista (categorías, orden, colores, plegados).

NO incluye el token de sesión de Nexus (cifrado con DPAPI y ligado a este PC/usuario: no
serviría en otro equipo y no debe salir en claro), ni la caché del navegador WebView2.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from .config import app_data_dir


def _should_include(rel: str) -> bool:
    low = rel.lower().replace("\\", "/")
    # Nunca respaldar credenciales ni caché pesada/irrelevante.
    if any(w in low for w in ("token", "oauth", "secret", "credential")):
        return False
    if low.startswith("webview2") or "/webview2" in low or low.endswith("_diag.log"):
        return False
    return True


def create_backup(dest_zip: str | Path) -> int:
    """Crea el .zip de respaldo. Devuelve cuántos archivos se incluyeron."""
    base = Path(app_data_dir())
    dest = Path(dest_zip)
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in base.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(base).as_posix()
            if not _should_include(rel):
                continue
            z.write(f, rel)
            n += 1
    return n


def restore_backup(src_zip: str | Path) -> int:
    """Restaura un .zip de respaldo sobre ``app_data_dir()`` (sobrescribe). Devuelve cuántos
    archivos se restauraron. Ignora entradas con credenciales o rutas fuera de la carpeta
    (protección contra zip-slip)."""
    base = Path(app_data_dir()).resolve()
    n = 0
    with zipfile.ZipFile(src_zip, "r") as z:
        for name in z.namelist():
            if name.endswith("/") or not _should_include(name):
                continue
            target = (base / name).resolve()
            try:
                target.relative_to(base)      # dentro de app_data_dir (anti zip-slip)
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, open(target, "wb") as out:
                out.write(src.read())
            n += 1
    return n
