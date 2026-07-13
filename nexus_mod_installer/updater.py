"""Información de versión y enlace a la página de descargas.

⚠️ BMI NO se autoactualiza y NO realiza NINGUNA conexión a GitHub ni descarga/reemplaza
binarios por su cuenta. Por seguridad e integridad (no se pueden validar ficheros de fuentes
externas), cualquier actualización la descarga el USUARIO a mano desde su propio navegador.
Este módulo solo expone la versión actual y la URL de la página de descargas para abrirla en
el navegador; no hace peticiones de red.
"""
from __future__ import annotations

from . import __version__

# Página pública de descargas (se abre en el NAVEGADOR del usuario; la app no la consulta).
RELEASES_PAGE = "https://github.com/carbollo/BMI/releases/latest"


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
