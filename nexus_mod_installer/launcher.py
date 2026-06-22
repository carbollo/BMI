"""Lanzar el juego (preferentemente con su script extender).

Multi-juego: el lanzador y los ejecutables dependen del juego activo (ver games.py):
SKSE64/skse64_loader.exe (Skyrim SE), F4SE/f4se_loader.exe (Fallout 4),
NVSE/nvse_loader.exe (New Vegas), FOSE (Fallout 3), OBSE (Oblivion), SFSE (Starfield)...
El loader y el .exe del juego viven en la carpeta raíz (la carpeta PADRE de Data).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .config import AppConfig

# Sin ventana de consola al lanzar procesos desde una app sin consola (Windows).
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def game_dir(config: AppConfig) -> Path | None:
    """Carpeta raíz del juego (donde está el .exe / el loader del script extender)."""
    if config.skse_loader_path:
        p = Path(config.skse_loader_path)
        if p.is_file():
            return p.parent
    if config.game_data_path:
        d = Path(config.game_data_path).parent  # padre de Data / Data Files
        if d.is_dir():
            return d
    return None


def find_skse(config: AppConfig) -> Path | None:
    """Localiza el lanzador del script extender del juego activo (o la ruta manual)."""
    if config.skse_loader_path:
        p = Path(config.skse_loader_path)
        if p.is_file():
            return p
    gdir = game_dir(config)
    if gdir:
        for name in config.game().loader_exes:
            cand = gdir / name
            if cand.is_file():
                return cand
    return None


def find_game_exe(config: AppConfig) -> Path | None:
    gdir = game_dir(config)
    if gdir:
        for name in config.game().game_exes:
            cand = gdir / name
            if cand.is_file():
                return cand
    return None


class GameLaunchError(RuntimeError):
    pass


def launch(config: AppConfig, prefer_skse: bool = True) -> Path:
    """Lanza el juego (con su script extender si existe). Devuelve el exe lanzado."""
    g = config.game()
    exe: Path | None = None
    if prefer_skse:
        exe = find_skse(config)
    if exe is None:
        exe = find_game_exe(config)
    if exe is None:
        se = g.script_extender or "el script extender"
        exes = " / ".join(g.game_exes) or "el ejecutable del juego"
        raise GameLaunchError(
            f"No se encontró el lanzador de {se} ni {exes}.\n\n"
            "Comprueba la carpeta de datos del juego en Ajustes (el ejecutable está en la "
            "carpeta padre), o indica la ruta del lanzador del script extender."
        )

    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=_NO_WINDOW)
    except OSError as e:
        raise GameLaunchError(f"No se pudo lanzar {exe.name}: {e}") from e
    return exe
