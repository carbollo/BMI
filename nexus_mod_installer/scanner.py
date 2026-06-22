"""Escaneo del juego: detecta TODOS los plugins instalados en la carpeta de datos,
no solo los instalados por este programa. Adaptado a MULTI-JUEGO: los masters vanilla,
el contenido Creation Club y el sistema de plugins.txt (con o sin prefijo '*') dependen
del juego (ver games.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import games

PLUGIN_EXTS = {".esp", ".esm", ".esl"}

# Flags de la cabecera TES4/TES3 (registro inicial del plugin).
_FLAG_MASTER = 0x00000001
_FLAG_LIGHT = 0x00000200


def is_creation_club(name: str, cc_prefix: str | None) -> bool:
    """True si es contenido Creation Club del juego (p.ej. ccBGSSSE001... para 'sse')."""
    if not cc_prefix:
        return False
    if Path(name).suffix.lower() not in PLUGIN_EXTS:
        return False
    return bool(re.match(rf"^cc[a-z]{{2,5}}{re.escape(cc_prefix)}\d", name, re.IGNORECASE))


@dataclass
class DetectedMod:
    name: str
    enabled: bool
    is_master: bool
    category: str             # "vanilla" | "cc" | "gestionado" | "externo"
    load_index: int = -1


# ---------------------------------------------------------------------------
def read_header_flags(path: str | Path) -> tuple[bool, bool]:
    """Lee las flags de la cabecera TES4. Devuelve (es_master, es_light)."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return (False, False)
    if len(head) < 12 or head[0:4] != b"TES4":
        return (False, False)
    flags = int.from_bytes(head[8:12], "little")
    return (bool(flags & _FLAG_MASTER), bool(flags & _FLAG_LIGHT))


def parse_plugins_txt(path: str, star_prefix: bool = True) -> tuple[dict[str, bool], dict[str, int]]:
    """Lee plugins.txt -> ({nombre_lower: activo}, {nombre_lower: orden_textual}).

    star_prefix=True  -> '*Nombre' = activo, 'Nombre' = inactivo (Skyrim SE/FO4/Starfield).
    star_prefix=False -> cualquier línea listada = activo (Skyrim clásico/FNV/FO3/Oblivion).
    Lee como utf-8-sig (consume BOM).
    """
    enabled: dict[str, bool] = {}
    order: dict[str, int] = {}
    p = Path(path) if path else None
    if not p or not p.is_file():
        return enabled, order
    idx = 0
    for raw in p.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if star_prefix:
            active = line.startswith("*")
            name = line.lstrip("*").strip()
        else:
            active = True
            name = line.lstrip("*").strip()  # tolera un '*' suelto: misma clave que el resto
        if not name:
            continue
        key = name.lower()
        if key in enabled:
            continue
        enabled[key] = active
        order[key] = idx
        idx += 1
    return enabled, order


def scan_installed(
    game_data_path: str,
    plugins_txt_path: str,
    managed_plugins: set[str] | None = None,
    game=None,
) -> list[DetectedMod]:
    """Escanea la carpeta de datos y devuelve todos los plugins detectados, clasificados
    según el juego (``game`` = GameInfo; por defecto Skyrim SE)."""
    g = game or games.get(games.DEFAULT_GAME)
    managed = {m.lower() for m in (managed_plugins or set())}
    data = Path(game_data_path) if game_data_path else None
    if not data or not data.is_dir():
        return []

    enabled_map, order_map = parse_plugins_txt(plugins_txt_path, g.star_prefix)
    BIG = 10 ** 9

    rows: list[tuple] = []
    for entry in data.iterdir():
        if not entry.is_file() or entry.suffix.lower() not in PLUGIN_EXTS:
            continue
        name = entry.name
        key = name.lower()
        ext = entry.suffix.lower()
        m_flag, l_flag = read_header_flags(entry)
        is_master = ext in (".esm", ".esl") or m_flag or l_flag

        if key in g.implicit_masters:
            category, enabled = "vanilla", True
            group, sub = 0, 0
        else:
            if is_creation_club(name, g.cc_prefix):
                category = "cc"
            elif key in managed:
                category = "gestionado"
            else:
                category = "externo"
            enabled = enabled_map.get(key, False)
            group, sub = (1 if is_master else 2), 0

        txt = order_map.get(key, BIG)
        dm = DetectedMod(name=name, enabled=enabled, is_master=is_master,
                         category=category, load_index=-1)
        rows.append((group, sub, txt, key, dm))

    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    load_idx = 0
    out: list[DetectedMod] = []
    for _g, _s, _t, _k, dm in rows:
        if dm.enabled:
            dm.load_index = load_idx
            load_idx += 1
        out.append(dm)
    return out


def set_plugin_enabled(plugins_txt_path: str, name: str, enabled: bool,
                       star_prefix: bool = True) -> None:
    """Activa/desactiva un plugin en plugins.txt conservando el orden.

    star_prefix=True  -> cambia el prefijo '*'.
    star_prefix=False -> activo = línea presente; inactivo = línea ausente.
    """
    if not plugins_txt_path:
        return  # juego sin plugins.txt (p.ej. Morrowind)
    p = Path(plugins_txt_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = (
        p.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        if p.is_file() else []
    )
    target = name.lower()
    found = False
    new_lines: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(raw)
            continue
        bare = stripped.lstrip("*").strip()
        if bare.lower() == target:
            found = True
            if not star_prefix and not enabled:
                continue  # sin '*': desactivar = quitar la línea
            if star_prefix:
                new_lines.append(("*" if enabled else "") + bare)
            else:
                new_lines.append(bare)  # listado = activo
        else:
            new_lines.append(raw)

    if not found and enabled:
        new_lines.append(("*" if star_prefix else "") + name)

    p.write_text("\n".join(new_lines).strip() + "\n", encoding="utf-8")


def write_load_order(plugins_txt_path: str, ordered_names: list[str],
                     star_prefix: bool = True) -> None:
    """Reescribe plugins.txt con el orden dado, conservando el estado activo de cada
    plugin. Solo tiene efecto real en juegos con star_prefix (donde el orden importa).
    Lee con utf-8-sig (BOM)."""
    if not plugins_txt_path:
        return  # juego sin plugins.txt (p.ej. Morrowind)
    enabled_map, _ = parse_plugins_txt(plugins_txt_path, star_prefix)
    p = Path(plugins_txt_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    seen: set[str] = set()
    for nm in ordered_names:
        key = nm.lower()
        if key in seen:
            continue
        active = enabled_map.get(key, False)  # ausente = inactivo
        if not star_prefix:
            if not active:
                seen.add(key)
                continue  # sin '*': solo se listan los activos
            lines.append(nm)
        else:
            lines.append(("*" if active else "") + nm)
        seen.add(key)

    if p.is_file():
        for raw in p.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            bare = s.lstrip("*").strip()
            if bare and bare.lower() not in seen:
                lines.append(s)
                seen.add(bare.lower())

    p.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
