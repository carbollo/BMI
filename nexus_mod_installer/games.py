"""Registro de juegos soportados (multi-juego).

Cada juego define su dominio de Nexus, game_id, masters vanilla (siempre activos),
script extender, ejecutables y rutas por defecto. Datos verificados contra la API de
Nexus y documentación de modding (LOOT, SKSE/F4SE/NVSE/FOSE/OBSE/SFSE, STEP).

Sistemas de orden de carga (¡varían por juego!):
  - star_prefix=True  -> plugins.txt usa '*Nombre.esp' para activo (Skyrim SE/AE, Fallout 4,
    Starfield). El ORDEN de las líneas SÍ es el orden de carga.
  - star_prefix=False -> plugins.txt es una lista plana de activos (sin '*'); el ORDEN real
    lo dan los timestamps de archivo (Skyrim clásico, New Vegas, Fallout 3, Oblivion).
  - uses_plugins_txt=False -> el juego no usa plugins.txt (Morrowind usa Morrowind.ini).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class GameInfo:
    key: str                              # id interno (config/store/rutas/selector)
    name: str
    game_id: int
    implicit_masters: frozenset           # masters que el motor carga siempre (lowercase)
    cc_prefix: str | None                 # código Creation Club (sse/fo4) o None
    script_extender: str                  # "SKSE64", "F4SE", ... o ""
    loader_exes: tuple                    # lanzador(es) del script extender
    game_exes: tuple                      # ejecutable(s) del juego
    steam_folder: str                     # bajo steamapps/common
    data_subfolder: str = "Data"          # "Data" o "Data Files" (Morrowind)
    appdata_folder: str = ""              # bajo %LOCALAPPDATA% (plugins.txt) y My Games (INI)
    ini_base: str = ""                    # base del INI para sLanguage (p.ej. 'Skyrim'); '' = sin STRINGS
    slang_short: bool = False             # sLanguage usa código corto ('es') en vez de 'SPANISH' (Starfield)
    uses_plugins_txt: bool = True         # gestiona activación vía plugins.txt
    star_prefix: bool = True              # plugins.txt usa '*' y el orden importa
    nexus_domain: str = ""                # dominio de Nexus (vacío = igual que key)

    @property
    def domain(self) -> str:
        """Dominio de Nexus para descargas/API/navegador (puede compartirse: SE y AE
        usan ambos 'skyrimspecialedition')."""
        return self.nexus_domain or self.key

    @property
    def data_path_hint(self) -> str:
        return f"{self.steam_folder}\\{self.data_subfolder}"


def _ms(*names: str) -> frozenset:
    return frozenset(n.lower() for n in names)


GAMES: dict[str, GameInfo] = {
    "skyrimspecialedition": GameInfo(
        key="skyrimspecialedition", name="Skyrim Special Edition", game_id=1704,
        implicit_masters=_ms("Skyrim.esm", "Update.esm", "Dawnguard.esm", "HearthFires.esm", "Dragonborn.esm"),
        cc_prefix="sse", script_extender="SKSE64", loader_exes=("skse64_loader.exe",),
        game_exes=("SkyrimSE.exe",), steam_folder="Skyrim Special Edition",
        appdata_folder="Skyrim Special Edition", ini_base="Skyrim", star_prefix=True,
    ),
    # Anniversary Edition: misma base de Nexus que SE (mismo dominio/game_id), pero entrada
    # separada para mantener su propia lista de mods y rutas (p.ej. instalación 1.6.x aparte).
    "skyrimae": GameInfo(
        key="skyrimae", name="Skyrim Anniversary Edition", game_id=1704,
        nexus_domain="skyrimspecialedition",
        implicit_masters=_ms("Skyrim.esm", "Update.esm", "Dawnguard.esm", "HearthFires.esm", "Dragonborn.esm"),
        cc_prefix="sse", script_extender="SKSE64", loader_exes=("skse64_loader.exe",),
        game_exes=("SkyrimSE.exe",), steam_folder="Skyrim Special Edition",
        appdata_folder="Skyrim Special Edition", ini_base="Skyrim", star_prefix=True,
    ),
    "skyrim": GameInfo(
        key="skyrim", name="Skyrim (clásico / Legendary)", game_id=110,
        implicit_masters=_ms("Skyrim.esm", "Update.esm", "Dawnguard.esm", "HearthFires.esm", "Dragonborn.esm"),
        cc_prefix=None, script_extender="SKSE", loader_exes=("skse_loader.exe",),
        game_exes=("TESV.exe",), steam_folder="Skyrim",
        appdata_folder="Skyrim", ini_base="Skyrim", star_prefix=False,
    ),
    "fallout4": GameInfo(
        key="fallout4", name="Fallout 4", game_id=1151,
        implicit_masters=_ms("Fallout4.esm"),
        cc_prefix="fo4", script_extender="F4SE", loader_exes=("f4se_loader.exe",),
        game_exes=("Fallout4.exe",), steam_folder="Fallout 4",
        appdata_folder="Fallout4", ini_base="Fallout4", star_prefix=True,
    ),
    "newvegas": GameInfo(
        key="newvegas", name="Fallout: New Vegas", game_id=130,
        implicit_masters=_ms("FalloutNV.esm"),
        cc_prefix=None, script_extender="NVSE", loader_exes=("nvse_loader.exe",),
        game_exes=("FalloutNV.exe",), steam_folder="Fallout New Vegas",
        appdata_folder="FalloutNV", star_prefix=False,
    ),
    "fallout3": GameInfo(
        key="fallout3", name="Fallout 3", game_id=120,
        implicit_masters=_ms("Fallout3.esm"),
        cc_prefix=None, script_extender="FOSE", loader_exes=("fose_loader.exe",),
        game_exes=("Fallout3.exe",), steam_folder="Fallout 3 goty",
        appdata_folder="Fallout3", star_prefix=False,
    ),
    "oblivion": GameInfo(
        key="oblivion", name="Oblivion (clásico)", game_id=101,
        implicit_masters=_ms("Oblivion.esm"),
        cc_prefix=None, script_extender="OBSE", loader_exes=("obse_loader.exe",),
        game_exes=("Oblivion.exe",), steam_folder="Oblivion",
        appdata_folder="Oblivion", star_prefix=False,
    ),
    "starfield": GameInfo(
        key="starfield", name="Starfield", game_id=4187,
        implicit_masters=_ms(
            "Starfield.esm", "ShatteredSpace.esm", "Constellation.esm", "OldMars.esm",
            "BlueprintShips-Starfield.esm", "SFBGS003.esm", "SFBGS004.esm",
            "SFBGS006.esm", "SFBGS007.esm", "SFBGS008.esm",
        ),
        cc_prefix=None, script_extender="SFSE", loader_exes=("sfse_loader.exe",),
        game_exes=("Starfield.exe",), steam_folder="Starfield",
        appdata_folder="Starfield", ini_base="Starfield", slang_short=True, star_prefix=True,
    ),
    "morrowind": GameInfo(
        key="morrowind", name="Morrowind", game_id=100,
        implicit_masters=frozenset(),
        cc_prefix=None, script_extender="MWSE", loader_exes=(),
        game_exes=("Morrowind.exe",), steam_folder="Morrowind",
        data_subfolder="Data Files", appdata_folder="", uses_plugins_txt=False, star_prefix=False,
    ),
}

DEFAULT_GAME = "skyrimspecialedition"
# Orden de presentación en los selectores.
ORDER = ["skyrimspecialedition", "skyrimae", "skyrim", "fallout4", "newvegas", "fallout3",
         "oblivion", "starfield", "morrowind"]


def get(key: str) -> GameInfo:
    return GAMES.get(key, GAMES[DEFAULT_GAME])


def all_games() -> list[GameInfo]:
    return [GAMES[k] for k in ORDER if k in GAMES]


# ---------------------------------------------------------------------------
# Rutas por defecto (Steam) por juego
# ---------------------------------------------------------------------------
_STEAM_LIBS = [
    r"C:\Program Files (x86)\Steam\steamapps\common",
    r"C:\Program Files\Steam\steamapps\common",
    r"C:\SteamLibrary\steamapps\common",
    r"D:\SteamLibrary\steamapps\common",
    r"E:\SteamLibrary\steamapps\common",
    r"D:\Steam\steamapps\common",
    r"E:\Steam\steamapps\common",
    r"I:\SteamLibrary\steamapps\common",
]


def default_data_path(game: GameInfo) -> str:
    """Intenta localizar la carpeta de datos del juego en las bibliotecas de Steam."""
    for lib in _STEAM_LIBS:
        p = Path(lib) / game.steam_folder / game.data_subfolder
        if p.is_dir():
            return str(p)
    return ""


def default_plugins_txt(game: GameInfo) -> str:
    """Ruta de plugins.txt del juego (o '' si no aplica)."""
    if not game.uses_plugins_txt or not game.appdata_folder:
        return ""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return ""
    return str(Path(local) / game.appdata_folder / "plugins.txt")
