"""Perfiles: instantáneas de plugins.txt + estado de los mods.

Un perfil captura el contenido de plugins.txt (orden + plugins activos) en ``<nombre>.txt``
y, además, el estado de los mods gestionados (activado, prioridad, categoría) en un
``<nombre>.json`` paralelo. Permite tener varias configuraciones completas y cambiar entre
ellas. Compatible hacia atrás: los perfiles antiguos (solo .txt) siguen funcionando.
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

    def _json_path(self, name: str) -> Path:
        return self.dir / f"{_safe(name)}.json"

    def exists(self, name: str) -> bool:
        return self._path(name).is_file()

    def save_from(self, name: str, plugins_txt_path: str, mods=None) -> Profile:
        """Crea/actualiza un perfil: copia el plugins.txt actual y, si se pasan ``mods``
        (iterable de InstalledMod), guarda su estado (activado, prioridad, categoría)."""
        src = Path(plugins_txt_path)
        content = src.read_text(encoding="utf-8-sig", errors="ignore") if src.is_file() else ""
        dest = self._path(name)
        dest.write_text(content, encoding="utf-8")
        if mods is not None:
            state = {str(m.mod_id): {"enabled": bool(m.enabled), "priority": int(m.priority),
                                     "category": m.category or ""}
                     for m in mods if m.mod_id > 0}
            self._json_path(name).write_text(
                json.dumps({"mods": state}, ensure_ascii=False, indent=2), encoding="utf-8")
        return Profile(name=dest.stem, file=str(dest))

    def mod_state(self, name: str) -> dict | None:
        """Estado de mods guardado en el perfil (o None si es un perfil antiguo sin .json)."""
        p = self._json_path(name)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("mods", {})
        except Exception:  # noqa: BLE001
            return None

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
        oj = self._json_path(old)
        if oj.is_file():
            oj.rename(self._json_path(new))
        return True

    def delete(self, name: str) -> bool:
        p = self._path(name)
        ok = False
        if p.is_file():
            p.unlink(); ok = True
        j = self._json_path(name)
        if j.is_file():
            j.unlink()
        return ok
