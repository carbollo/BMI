"""Detección de conflictos entre mods instalados.

Dos mods entran en conflicto cuando despliegan el MISMO archivo (misma ruta relativa
a Data). El "ganador" es el que se desplegó más tarde (mayor installed_at), porque su
copia/hardlink sobrescribió al anterior — igual que en el juego.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileConflict:
    rel_path: str                       # ruta relativa a Data en conflicto
    mods: list[str] = field(default_factory=list)   # nombres de mods que la despliegan
    winner: str = ""                    # mod cuyo archivo prevalece


def find_conflicts(installed_mods) -> list[FileConflict]:
    """Devuelve los archivos desplegados por más de un mod.

    installed_mods: iterable de InstalledMod (con .name, .deployed_files, .installed_at,
    .enabled). Solo se consideran mods activos.
    """
    # ruta_lower -> lista de (priority, installed_at, nombre, ruta_original)
    owners: dict[str, list[tuple[int, float, str, str]]] = {}
    for mod in installed_mods:
        if not getattr(mod, "enabled", True):
            continue
        for rel in mod.deployed_files:
            key = rel.lower()
            owners.setdefault(key, []).append((
                int(getattr(mod, "priority", 0) or 0),
                float(getattr(mod, "installed_at", 0.0) or 0.0),
                mod.name, rel,
            ))

    conflicts: list[FileConflict] = []
    for key, lst in owners.items():
        if len(lst) < 2:
            continue
        # Gana el de mayor prioridad; la fecha desempata (compatibilidad con lo anterior).
        lst_sorted = sorted(lst, key=lambda t: (t[0], t[1]))
        winner = lst_sorted[-1][2]
        names = [name for _, _, name, _ in lst_sorted]
        conflicts.append(
            FileConflict(rel_path=lst_sorted[0][3], mods=names, winner=winner)
        )

    conflicts.sort(key=lambda c: c.rel_path.lower())
    return conflicts


def conflict_summary(installed_mods) -> dict[str, int]:
    """Resumen: nº de archivos en conflicto por mod (cuántos de sus archivos pisan o
    son pisados). Útil para una vista rápida."""
    conflicts = find_conflicts(installed_mods)
    per_mod: dict[str, int] = {}
    for c in conflicts:
        for name in c.mods:
            per_mod[name] = per_mod.get(name, 0) + 1
    return per_mod
