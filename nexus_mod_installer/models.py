"""Modelos de datos (sin dependencias de GUI)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote


# ---------------------------------------------------------------------------
# Enlaces nxm://
# ---------------------------------------------------------------------------
# Formato típico generado por el botón "Mod Manager Download" de Nexus:
#   nxm://skyrimspecialedition/mods/266/files/1000123?key=ABC&expires=1699999999&user_id=1234
# Para mods de colecciones a veces aparece como:
#   nxm://skyrimspecialedition/collections/<slug>/revisions/<n>
@dataclass(frozen=True)
class NxmLink:
    game_domain: str
    mod_id: int
    file_id: int
    key: Optional[str] = None
    expires: Optional[int] = None
    user_id: Optional[int] = None
    raw: str = ""

    @classmethod
    def parse(cls, url: str) -> "NxmLink":
        """Parsea un enlace nxm:// a un objeto NxmLink. Lanza ValueError si no es válido."""
        url = url.strip().strip('"').strip("'")
        if not url.lower().startswith("nxm://"):
            raise ValueError(f"No es un enlace nxm://: {url!r}")

        parsed = urlparse(url)
        # En nxm://, el "host" es el dominio del juego.
        game_domain = parsed.netloc or ""
        # path: /mods/<mod_id>/files/<file_id>
        m = re.search(r"/mods/(\d+)/files/(\d+)", parsed.path)
        if not m:
            raise ValueError(f"Enlace nxm:// sin mod/file: {url!r}")
        mod_id = int(m.group(1))
        file_id = int(m.group(2))

        qs = parse_qs(parsed.query)
        key = qs.get("key", [None])[0]
        expires_raw = qs.get("expires", [None])[0]
        user_id_raw = qs.get("user_id", [None])[0]

        return cls(
            game_domain=game_domain,
            mod_id=mod_id,
            file_id=file_id,
            key=unquote(key) if key else None,
            expires=int(expires_raw) if expires_raw and expires_raw.isdigit() else None,
            user_id=int(user_id_raw) if user_id_raw and user_id_raw.isdigit() else None,
            raw=url,
        )

    @property
    def has_credentials(self) -> bool:
        """True si trae key+expires (necesario para descargar con cuenta gratuita)."""
        return bool(self.key and self.expires)


# ---------------------------------------------------------------------------
# Información de mods / archivos provista por la API
# ---------------------------------------------------------------------------
@dataclass
class ModFileInfo:
    file_id: int
    name: str
    version: str = ""
    category_name: str = ""
    size_kb: int = 0
    file_name: str = ""
    is_primary: bool = False

    @classmethod
    def from_api(cls, data: dict) -> "ModFileInfo":
        return cls(
            file_id=int(data.get("file_id") or data.get("id") or 0),
            name=data.get("name", ""),
            version=data.get("version", "") or "",
            category_name=data.get("category_name", "") or "",
            size_kb=int(data.get("size_kb") or data.get("size") or 0),
            file_name=data.get("file_name", "") or "",
            is_primary=bool(data.get("is_primary", False)),
        )


@dataclass
class ModInfo:
    mod_id: int
    name: str = ""
    summary: str = ""
    author: str = ""
    version: str = ""
    picture_url: str = ""
    game_domain: str = "skyrimspecialedition"
    available: bool = True

    @classmethod
    def from_api(cls, data: dict, game_domain: str = "skyrimspecialedition") -> "ModInfo":
        return cls(
            mod_id=int(data.get("mod_id") or data.get("id") or 0),
            name=data.get("name", "") or "",
            summary=data.get("summary", "") or "",
            author=data.get("author", "") or data.get("uploaded_by", "") or "",
            version=data.get("version", "") or "",
            picture_url=data.get("picture_url", "") or "",
            game_domain=game_domain,
            available=data.get("available", True),
        )


# ---------------------------------------------------------------------------
# Tareas de descarga / instalación
# ---------------------------------------------------------------------------
class TaskStatus(str, Enum):
    QUEUED = "En cola"
    RESOLVING = "Resolviendo enlace"
    DOWNLOADING = "Descargando"
    EXTRACTING = "Extrayendo"
    INSTALLING = "Instalando"
    DEPLOYING = "Desplegando"
    DONE = "Completado"
    ERROR = "Error"
    NEEDS_CLICK = "Requiere clic en web"  # cuenta gratis sin key+expires


@dataclass
class DownloadTask:
    game_domain: str
    mod_id: int
    file_id: int
    key: Optional[str] = None
    expires: Optional[int] = None
    # Metadatos (se rellenan al resolver)
    mod_name: str = ""
    file_name: str = ""
    version: str = ""
    picture_url: str = ""           # miniatura del mod en Nexus (si se conoce)
    # Estado
    status: TaskStatus = TaskStatus.QUEUED
    progress: float = 0.0          # 0..100
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed_bps: float = 0.0
    error: str = ""
    # Rutas resultantes
    archive_path: str = ""
    install_dir: str = ""
    # Origen
    from_collection: str = ""       # slug de colección, si aplica
    is_dependency: bool = False
    is_translation: bool = False    # es una traducción al español de otro mod
    cancelled: bool = False         # marcada para quitar de la cola (el worker la salta)

    @property
    def has_credentials(self) -> bool:
        return bool(self.key and self.expires)

    @property
    def label(self) -> str:
        base = self.mod_name or f"mod {self.mod_id}"
        if self.file_name:
            return f"{base} — {self.file_name}"
        return base

    @classmethod
    def from_nxm(cls, link: NxmLink) -> "DownloadTask":
        return cls(
            game_domain=link.game_domain,
            mod_id=link.mod_id,
            file_id=link.file_id,
            key=link.key,
            expires=link.expires,
        )


# ---------------------------------------------------------------------------
# Mod instalado (para gestión/desinstalación)
# ---------------------------------------------------------------------------
@dataclass
class InstalledMod:
    mod_id: int
    name: str
    version: str = ""
    game_domain: str = "skyrimspecialedition"
    install_dir: str = ""              # carpeta gestionada con los archivos extraídos
    deployed_files: list[str] = field(default_factory=list)  # rutas relativas a Data
    # Archivos desplegados en la carpeta RAÍZ del juego (junto al .exe): Engine
    # Fixes parte 2, wrappers de ENB/ReShade, runtime de SKSE… rutas relativas a la raíz.
    deployed_root_files: list[str] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)         # .esp/.esm/.esl detectados
    enabled: bool = True
    installed_at: float = 0.0          # epoch de instalación (orden de despliegue)
    size_bytes: int = 0                # tamaño total de los archivos del mod
    picture_url: str = ""              # miniatura del mod en Nexus (si se conoce)
    notes: str = ""                    # notas libres del usuario (configs, detalles…)
    hidden_files: list[str] = field(default_factory=list)  # archivos ocultos (no desplegados)

    def to_dict(self) -> dict:
        return {
            "mod_id": self.mod_id,
            "name": self.name,
            "version": self.version,
            "game_domain": self.game_domain,
            "install_dir": self.install_dir,
            "deployed_files": self.deployed_files,
            "deployed_root_files": self.deployed_root_files,
            "plugins": self.plugins,
            "enabled": self.enabled,
            "installed_at": self.installed_at,
            "size_bytes": self.size_bytes,
            "picture_url": self.picture_url,
            "notes": self.notes,
            "hidden_files": self.hidden_files,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "InstalledMod":
        return cls(
            mod_id=int(d.get("mod_id", 0)),
            name=d.get("name", ""),
            version=d.get("version", ""),
            game_domain=d.get("game_domain", "skyrimspecialedition"),
            install_dir=d.get("install_dir", ""),
            deployed_files=list(d.get("deployed_files", [])),
            deployed_root_files=list(d.get("deployed_root_files", [])),
            plugins=list(d.get("plugins", [])),
            enabled=bool(d.get("enabled", True)),
            installed_at=float(d.get("installed_at", 0.0) or 0.0),
            size_bytes=int(d.get("size_bytes", 0) or 0),
            picture_url=d.get("picture_url", "") or "",
            notes=d.get("notes", "") or "",
            hidden_files=list(d.get("hidden_files", [])),
        )
