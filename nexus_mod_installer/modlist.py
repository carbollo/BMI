"""Exportar / importar listas de mods a un .txt sencillo.

Formato del archivo (una entrada por línea):

    dominio | mod_id | nombre

Las líneas vacías y las que empiezan por ``#`` se ignoran. Al importar se aceptan también,
por comodidad si el usuario edita el archivo a mano:

    - URLs de Nexus:   https://www.nexusmods.com/skyrimspecialedition/mods/266
    - CSV simple:      skyrimspecialedition,266
    - solo el id:      266            (se asume el juego activo)

Se exporta el ``mod_id`` (no el ``file_id``): al importar se descarga el archivo PRINCIPAL
actual del mod, así la lista sigue siendo válida aunque el mod se haya actualizado.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ModListEntry:
    game_domain: str
    mod_id: int
    name: str = ""


_HEADER = (
    "# BMI — Lista de mods\n"
    "# Una entrada por línea:  dominio | mod_id | nombre\n"
    "# Las líneas vacías y las que empiezan por # se ignoran.\n"
)

# https://www.nexusmods.com/<dominio>/mods/<id>  (con o sin www/https)
_URL_RE = re.compile(r"nexusmods\.com/([a-z0-9]+)/mods/(\d+)", re.IGNORECASE)


def export_text(mods, default_domain: str = "") -> str:
    """Serializa un iterable de InstalledMod (o cualquier objeto con ``mod_id``/``name``/
    ``game_domain``) al formato de lista. Omite los mods sin id de Nexus (externos), que no
    se pueden volver a descargar por id."""
    lines = [_HEADER]
    for m in mods:
        mid = int(getattr(m, "mod_id", 0) or 0)
        if mid <= 0:
            continue
        dom = (getattr(m, "game_domain", "") or default_domain).strip()
        name = (getattr(m, "name", "") or "").replace("|", "/").replace("\n", " ").strip()
        lines.append(f"{dom} | {mid} | {name}\n")
    return "".join(lines)


def parse_text(text: str, default_domain: str = "") -> list[ModListEntry]:
    """Parsea el contenido de una lista de mods. Tolerante: acepta el formato con ``|``,
    URLs de Nexus, CSV o solo el id. Deduplica por (dominio, mod_id) conservando el orden."""
    out: list[ModListEntry] = []
    seen: set[tuple[str, int]] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        dom, mid, name = "", 0, ""

        murl = _URL_RE.search(line)
        if murl:
            dom = murl.group(1).lower()
            mid = int(murl.group(2))
            name = line[murl.end():].strip(" |#-\t")
        elif "|" in line:
            parts = [p.strip() for p in line.split("|")]
            dom = parts[0].lower()
            digits = re.sub(r"\D", "", parts[1]) if len(parts) > 1 else ""
            mid = int(digits) if digits else 0
            name = parts[2].strip() if len(parts) > 2 else ""
        else:
            parts = [p.strip() for p in re.split(r"[,;\t]+", line) if p.strip()]
            nums = [p for p in parts if p.isdigit()]
            words = [p for p in parts if not p.isdigit()]
            if nums:
                mid = int(nums[0])
            if words:
                dom = words[0].lower()

        if not dom:
            dom = (default_domain or "").strip().lower()
        if mid > 0 and dom and (dom, mid) not in seen:
            seen.add((dom, mid))
            out.append(ModListEntry(game_domain=dom, mod_id=mid, name=name))
    return out
