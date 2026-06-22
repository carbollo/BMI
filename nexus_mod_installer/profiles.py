"""Perfiles de orden de carga: instantáneas guardadas de plugins.txt.

Un perfil captura el contenido de plugins.txt (orden + plugins activos). Permite tener
varias configuraciones de carga y cambiar entre ellas. Alcance acotado a load-order:
NO clona el despliegue de archivos (eso sería un gestor de perfiles completo); cambiar
de perfil solo reescribe plugins.txt.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import app_data_dir


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\- ]+", "_", name).strip() or "perfil"


def safe_name(name: str) -> str:
    """Nombre saneado tal como se guarda en disco (= Profile.name)."""
    return _safe(name)


@dataclass
class Profile:
    name: str
    file: str          # ruta del .txt con la instantánea


class ProfileStore:
    def __init__(self):
        self.dir = app_data_dir() / "profiles"
        self.dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[Profile]:
        out = []
        for p in sorted(self.dir.glob("*.txt")):
            out.append(Profile(name=p.stem, file=str(p)))
        return out

    def _path(self, name: str) -> Path:
        return self.dir / f"{_safe(name)}.txt"

    def exists(self, name: str) -> bool:
        return self._path(name).is_file()

    def save_from(self, name: str, plugins_txt_path: str) -> Profile:
        """Crea/actualiza un perfil copiando el plugins.txt actual."""
        src = Path(plugins_txt_path)
        content = src.read_text(encoding="utf-8-sig", errors="ignore") if src.is_file() else ""
        dest = self._path(name)
        dest.write_text(content, encoding="utf-8")
        return Profile(name=dest.stem, file=str(dest))

    def apply_to(self, name: str, plugins_txt_path: str) -> bool:
        """Escribe el plugins.txt con el contenido del perfil. Devuelve True si ok."""
        src = self._path(name)
        if not src.is_file():
            return False
        content = src.read_text(encoding="utf-8-sig", errors="ignore")
        dst = Path(plugins_txt_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
        return True

    def rename(self, old: str, new: str) -> bool:
        op, npath = self._path(old), self._path(new)
        if not op.is_file() or npath.exists():
            return False
        op.rename(npath)
        return True

    def delete(self, name: str) -> bool:
        p = self._path(name)
        if p.is_file():
            p.unlink()
            return True
        return False
