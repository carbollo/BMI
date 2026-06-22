"""Configuración persistente de la aplicación."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import games


APP_DIR_NAME = "BMI"


def app_data_dir() -> Path:
    """Carpeta de datos de la app (config, lista de mods instalados)."""
    base = os.environ.get("APPDATA")  # Windows
    if base:
        d = Path(base) / APP_DIR_NAME
    else:
        d = Path.home() / f".{APP_DIR_NAME.lower()}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_skyrim_data_path() -> str:
    """Intenta adivinar la carpeta Data de Skyrim Special Edition."""
    candidates = [
        r"C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data",
        r"C:\Program Files\Steam\steamapps\common\Skyrim Special Edition\Data",
        r"D:\SteamLibrary\steamapps\common\Skyrim Special Edition\Data",
        r"E:\SteamLibrary\steamapps\common\Skyrim Special Edition\Data",
    ]
    for c in candidates:
        if Path(c).is_dir():
            return c
    return ""


def default_plugins_txt() -> str:
    """Ruta del plugins.txt de SSE (controla qué plugins se cargan)."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        p = Path(local) / "Skyrim Special Edition" / "plugins.txt"
        return str(p)
    return ""


@dataclass
class AppConfig:
    # Credenciales
    api_key: str = ""
    # Juego objetivo (dominio de Nexus)
    game_domain: str = "skyrimspecialedition"
    # Rutas
    game_data_path: str = field(default_factory=default_skyrim_data_path)
    plugins_txt_path: str = field(default_factory=default_plugins_txt)
    # Ruta opcional a skse64_loader.exe (si se deja vacía, se busca junto a Data)
    skse_loader_path: str = ""
    downloads_dir: str = field(default_factory=lambda: str(app_data_dir() / "downloads"))
    mods_dir: str = field(default_factory=lambda: str(app_data_dir() / "mods"))
    # Despliegue: "hardlink" (recomendado) o "copy"
    deploy_method: str = "hardlink"
    # Comportamiento
    auto_deploy: bool = True          # desplegar a Data automáticamente tras instalar
    auto_enable_plugins: bool = True  # activar .esp/.esm en plugins.txt
    resolve_dependencies: bool = True
    # Buscar e instalar automáticamente la traducción al español del mod
    install_spanish_translation: bool = True
    protocol_registered: bool = False
    # FOMOD: "interactive" (asistente de opciones) o "auto" (recomendadas)
    fomod_mode: str = "interactive"
    # Perfil de orden de carga activo (instantánea de plugins.txt)
    current_profile: str = ""
    # Idioma de la interfaz: es | en | fr | de | it (se aplica al reiniciar)
    language: str = "es"
    # Rutas guardadas por juego: {dominio: {"data":..., "plugins":..., "skse":...}}
    game_paths: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    @staticmethod
    def config_path() -> Path:
        return app_data_dir() / "config.json"

    @classmethod
    def load(cls) -> "AppConfig":
        p = cls.config_path()
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                cfg = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
                cfg.ensure_dirs()
                return cfg
            except Exception:
                pass
        cfg = cls()
        cfg.ensure_dirs()
        return cfg

    def save(self) -> None:
        self.config_path().write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def ensure_dirs(self) -> None:
        for d in (self.downloads_dir, self.mods_dir):
            try:
                Path(d).mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.game_data_path)

    # ------------------------------------------------------------------
    # Multi-juego
    # ------------------------------------------------------------------
    def game(self):
        """GameInfo del juego activo."""
        return games.get(self.game_domain)

    def _store_active_game_paths(self) -> None:
        self.game_paths[self.game_domain] = {
            "data": self.game_data_path,
            "plugins": self.plugins_txt_path,
            "skse": self.skse_loader_path,
        }

    def switch_game(self, new_key: str) -> None:
        """Cambia el juego activo, recordando las rutas del juego anterior y cargando
        (o autodetectando) las del nuevo."""
        if new_key == self.game_domain or new_key not in games.GAMES:
            return
        self._store_active_game_paths()
        self.game_domain = new_key
        g = games.get(new_key)
        saved = self.game_paths.get(new_key)
        if saved:
            self.game_data_path = saved.get("data", "")
            self.plugins_txt_path = saved.get("plugins", "")
            self.skse_loader_path = saved.get("skse", "")
        else:
            self.game_data_path = games.default_data_path(g)
            self.plugins_txt_path = games.default_plugins_txt(g)
            self.skse_loader_path = ""
        self.save()
