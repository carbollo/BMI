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


# ---------------------------------------------------------------------------
# Herramientas externas (Nemesis, xEdit, DynDOLOD, Synthesis…)
# ---------------------------------------------------------------------------
# Nombre amigable -> posibles nombres de ejecutable (para autodetección).
KNOWN_TOOLS = [
    ("xEdit / SSEEdit", ["SSEEdit.exe", "xEdit.exe", "FO4Edit.exe", "TES5Edit.exe"]),
    ("Nemesis", ["Nemesis Unlimited Behavior Engine.exe", "NemesisUnlimitedBehaviorEngine.exe"]),
    ("FNIS", ["GenerateFNISforUsers.exe"]),
    ("DynDOLOD", ["DynDOLODx64.exe", "DynDOLOD.exe"]),
    ("Synthesis", ["Synthesis.exe"]),
    ("BodySlide", ["BodySlide x64.exe", "BodySlide.exe"]),
    ("Wrye Bash", ["Wrye Bash.exe"]),
    ("LOOT", ["LOOT.exe"]),
]


def launch_tool(path: str, args: str = "", cwd: str = "") -> Path:
    """Lanza una herramienta externa. ``args`` se parte estilo línea de comandos."""
    exe = Path(path)
    if not exe.is_file():
        raise GameLaunchError(f"No existe el ejecutable: {path}")
    cmd = [str(exe)]
    if args.strip():
        import shlex
        cmd += shlex.split(args, posix=False)
    workdir = cwd if (cwd and Path(cwd).is_dir()) else str(exe.parent)
    try:
        subprocess.Popen(cmd, cwd=workdir, creationflags=_NO_WINDOW)
    except OSError as e:
        raise GameLaunchError(f"No se pudo lanzar {exe.name}: {e}") from e
    return exe


def launch_vfs(config: AppConfig, store, log=lambda m: None):
    """Modo VFS (experimental, estilo MO2): monta un sistema de archivos virtual con USVFS,
    superpone las carpetas de los mods activos (en orden de prioridad) sobre Data SIN copiar
    nada, lanza el juego enganchado y espera a que cierre para desmontar. Data real intacto.

    BLOQUEA hasta que el juego termina -> el llamador debe ejecutarlo en un hilo aparte.
    """
    from . import vfs
    d = vfs.find_usvfs_dir(getattr(config, "vfs_dir", "") or None)
    if not d:
        raise GameLaunchError(
            "No se encontraron los binarios de USVFS (carpeta 'usvfs/'). El Modo VFS los "
            "necesita (usvfs_x64.dll + proxies).")
    if not config.game_data_path:
        raise GameLaunchError("Configura la carpeta Data del juego en Ajustes.")
    exe = find_skse(config) or find_game_exe(config)
    if exe is None:
        raise GameLaunchError("No se encontró el lanzador del script extender ni el juego.")
    gdir = game_dir(config)

    v = vfs.Vfs(d)
    v.create("bmi_instance")
    mods = [m for m in store.all() if getattr(m, "enabled", True) and m.install_dir]
    mods.sort(key=lambda m: (getattr(m, "priority", 0), m.name.lower()))  # menor prioridad primero
    n = 0
    for m in mods:
        try:
            if Path(m.install_dir).is_dir() and v.link_directory(m.install_dir, config.game_data_path):
                n += 1
        except Exception:  # noqa: BLE001
            pass
    log(f"VFS montado: {n} mod(s) virtualizados sobre Data (Data real intacto). Lanzando {exe.name}…")
    try:
        v.launch(exe, cwd=str(gdir) if gdir else None)
        log("Juego lanzado con VFS. Espera a que lo cierres para desmontar…")
        v.wait_for_game()
    finally:
        v.disconnect()
    log("Juego cerrado. VFS desmontado; tu carpeta Data sigue limpia.")
    return exe


def detect_tools(config: AppConfig) -> list[dict]:
    """Busca herramientas conocidas en la carpeta del juego (y subcarpetas habituales).
    Devuelve [{name, path, args, cwd}] de las encontradas (best-effort)."""
    found: list[dict] = []
    seen: set[str] = set()
    roots: list[Path] = []
    gdir = game_dir(config)
    if gdir:
        roots.append(gdir)
        for sub in ("Tools", "Data", "Data/SKSE", "modding"):
            p = gdir / sub
            if p.is_dir():
                roots.append(p)
    for root in roots:
        for name, exes in KNOWN_TOOLS:
            if name in seen:
                continue
            for exe_name in exes:
                # Búsqueda superficial: en la carpeta y un nivel de subcarpetas.
                for cand in (root / exe_name, *(d / exe_name for d in root.iterdir()
                                                if d.is_dir())):
                    try:
                        if cand.is_file():
                            found.append({"name": name, "path": str(cand), "args": "",
                                          "cwd": str(cand.parent)})
                            seen.add(name)
                            break
                    except OSError:
                        continue
                if name in seen:
                    break
    return found
