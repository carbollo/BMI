"""Instalación de mods: extraer, detectar FOMOD, determinar raíz de datos,
desplegar a la carpeta Data del juego y activar plugins."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from pathlib import Path

from .config import AppConfig
from .models import InstalledMod, DownloadTask
from . import archive, deploy, fomod, launcher


def _safe_name(name: str) -> str:
    name = re.sub(r"[<>:\"/\\|?*]+", "_", name).strip().strip(".")
    return name or "mod"


def _dir_size(path) -> int:
    total = 0
    try:
        for f in Path(path).rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


class InstalledModsStore:
    """Persiste la lista de mods instalados en un JSON (para desinstalar)."""

    def __init__(self, config: AppConfig):
        self.config = config
        base = Path(config.mods_dir).parent
        # Store por juego (cada juego lleva su propia lista de mods instalados).
        self.path = base / f"installed_mods_{config.game_domain}.json"
        self._legacy = base / "installed_mods.json"  # fichero antiguo (solo Skyrim SE)
        self.mods: dict[int, InstalledMod] = {}
        self.load()

    def load(self) -> None:
        src = self.path
        migrated = False
        if not src.is_file() and self.config.game_domain == "skyrimspecialedition" \
                and self._legacy.is_file():
            src = self._legacy  # migra los datos antiguos de Skyrim SE
            migrated = True
        if src.is_file():
            try:
                data = json.loads(src.read_text(encoding="utf-8"))
                self.mods = {
                    int(m["mod_id"]): InstalledMod.from_dict(m) for m in data.get("mods", [])
                }
            except Exception:
                self.mods = {}
                return
        if migrated:
            # Persistir al fichero por-juego y retirar el legacy (fuente única de verdad,
            # evita resurrección si luego se borra el fichero por-juego).
            self.save()
            try:
                self._legacy.rename(self._legacy.with_name("installed_mods.json.migrated"))
            except OSError:
                pass

    def save(self) -> None:
        data = {"mods": [m.to_dict() for m in self.mods.values()]}
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def add(self, mod: InstalledMod) -> None:
        self.mods[mod.mod_id] = mod
        self.save()

    def get(self, mod_id: int) -> InstalledMod | None:
        return self.mods.get(mod_id)

    def all(self) -> list[InstalledMod]:
        return list(self.mods.values())

    def is_installed(self, mod_id: int) -> bool:
        return mod_id in self.mods


class Installer:
    def __init__(self, config: AppConfig, store: InstalledModsStore):
        self.config = config
        self.store = store

    def install(self, task: DownloadTask, log=lambda m: None, fomod_chooser=None) -> InstalledMod:
        """Instala un archivo ya descargado (task.archive_path).

        fomod_chooser: callable(FomodConfig) -> list[FomodPlugin] | None.
        Si se provee y el FOMOD tiene opciones, se invoca (modo interactivo); si
        devuelve None (modo auto o cancelado), se usan las opciones por defecto.
        """
        archive_path = Path(task.archive_path)
        if not archive_path.is_file():
            raise FileNotFoundError(f"No se encuentra el archivo descargado: {archive_path}")

        mod_folder_name = _safe_name(f"{task.mod_id}_{task.mod_name or archive_path.stem}")
        install_dir = Path(self.config.mods_dir) / mod_folder_name
        if install_dir.exists():
            shutil.rmtree(install_dir, ignore_errors=True)
        install_dir.mkdir(parents=True, exist_ok=True)

        # 1) Extraer
        log(f"Extrayendo {archive_path.name}...")
        extracted = install_dir / "_extracted"
        archive.extract(archive_path, extracted)

        # 2) ¿FOMOD?
        config_xml = fomod.find_fomod_config(str(extracted))
        if config_xml is not None:
            fomod_config = fomod.parse_config(str(config_xml))
            staging = install_dir / "_data"
            selection = None
            if fomod_chooser is not None and fomod_config.has_choices:
                log(f"Instalador FOMOD interactivo: {fomod_config.module_name}")
                selection = fomod_chooser(fomod_config)  # bloquea hasta elegir (o None)
            if selection is None:
                log("FOMOD: opciones por defecto (obligatorias + recomendadas).")
                selection = fomod.auto_select(
                    fomod_config,
                    prefer_language=(self.config.language
                                     if self.config.install_spanish_translation else ""),
                )
            data_root, notes = fomod.install_selection(
                fomod_config, selection, str(staging)
            )
            for n in notes:
                log("FOMOD: " + n)
        else:
            data_root = deploy.find_data_root(str(extracted))

        # 3) Detectar plugins y archivos de carpeta raíz (Engine Fixes pt.2, ENB, SKSE…)
        plugins = deploy.list_plugins(data_root)
        root_files = deploy.find_root_files(
            str(extracted), extra_names=self.config.game().loader_exes
        )

        mod = InstalledMod(
            mod_id=task.mod_id,
            name=task.mod_name or archive_path.stem,
            version=task.version,
            game_domain=task.game_domain,
            install_dir=str(data_root),
            plugins=plugins,
            installed_at=time.time(),
            size_bytes=_dir_size(data_root),
            picture_url=getattr(task, "picture_url", "") or "",
        )

        # 4) Desplegar a Data (+ a la carpeta raíz del juego si procede)
        if self.config.auto_deploy:
            mod.deployed_files, mod.deployed_root_files = self._deploy(mod, root_files, log)
            if self.config.auto_enable_plugins and plugins:
                self._enable_plugins(plugins, log)

        self.store.add(mod)
        task.install_dir = str(data_root)
        extra = f" + {len(mod.deployed_root_files)} en la raíz" if mod.deployed_root_files else ""
        log(f"Instalado: {mod.name}  ({len(mod.deployed_files)} archivos en Data{extra})")
        return mod

    # ------------------------------------------------------------------
    def _deploy(self, mod: InstalledMod, root_files, log) -> tuple[list[str], list[str]]:
        if not self.config.game_data_path:
            log("AVISO: no hay carpeta Data configurada; no se desplegó.")
            return [], []
        log(f"Desplegando a {self.config.game_data_path} ({self.config.deploy_method})...")
        deployed = deploy.deploy(
            mod.install_dir, self.config.game_data_path, self.config.deploy_method,
            exclude=self._exclude_for(mod, root_files),
        )
        root_deployed = self._deploy_root(root_files, log)
        return deployed, root_deployed

    def _exclude_for(self, mod: InstalledMod, root_files) -> list[str]:
        """Rutas de origen a NO desplegar: archivos de raíz + archivos ocultos del mod."""
        excl = [src for src, _ in root_files]
        base = Path(mod.install_dir)
        excl += [str(base / h) for h in (mod.hidden_files or [])]
        return excl

    def _game_root(self):
        """Carpeta raíz del juego (junto al .exe), o None si no se puede determinar."""
        if not self.config.game_data_path:
            return None
        groot = launcher.game_dir(self.config) or deploy.game_root(self.config.game_data_path)
        return groot if (groot and Path(groot).is_dir()) else None

    def _deploy_root(self, root_files, log) -> list[str]:
        if not root_files:
            return []
        groot = self._game_root()
        if not groot:
            log("AVISO: no se localizó la carpeta raíz del juego; los archivos de "
                "raíz (Engine Fixes parte 2 / ENB) NO se desplegaron.")
            return []
        try:
            done = deploy.deploy_root(root_files, groot, self.config.deploy_method)
        except FileNotFoundError as e:
            log(f"AVISO: {e}")
            return []
        names = ", ".join(sorted({Path(r).name for r in done}))
        log(f"Carpeta raíz del juego ({groot}): {len(done)} archivo(s) → {names}")
        return done

    def _managed_dir(self, mod: InstalledMod) -> Path:
        return Path(self.config.mods_dir) / _safe_name(f"{mod.mod_id}_{mod.name}")

    def _root_files_for(self, mod: InstalledMod):
        """Re-localiza los archivos de carpeta raíz desde la carpeta gestionada del
        mod (para re-desplegarlos al reactivarlo)."""
        extracted = self._managed_dir(mod) / "_extracted"
        if extracted.is_dir():
            return deploy.find_root_files(
                str(extracted), extra_names=self.config.game().loader_exes
            )
        return []

    def _enable_plugins(self, plugins: list[str], log) -> None:
        if not self.config.plugins_txt_path:
            return
        deploy.enable_plugins(self.config.plugins_txt_path, plugins)
        log(f"Plugins activados en plugins.txt: {', '.join(plugins)}")

    # ------------------------------------------------------------------
    def set_mod_enabled(self, mod_id: int, enabled: bool, log=lambda m: None) -> bool:
        """Activa/desactiva un mod SIN desinstalarlo: despliega o repliega sus archivos
        de la carpeta Data y activa/desactiva sus plugins. Conserva el mod en el store."""
        mod = self.store.get(mod_id)
        if not mod or mod.enabled == enabled:
            return False
        if enabled:
            if self.config.game_data_path:
                root_files = self._root_files_for(mod)
                mod.deployed_files = deploy.deploy(
                    mod.install_dir, self.config.game_data_path, self.config.deploy_method,
                    exclude=self._exclude_for(mod, root_files),
                )
                mod.deployed_root_files = self._deploy_root(root_files, log)
                # Re-desplegar lo convierte en el último escritor en disco: actualiza la
                # fecha para que la detección de conflictos acierte el ganador.
                mod.installed_at = time.time()
            if self.config.auto_enable_plugins and mod.plugins and self.config.plugins_txt_path:
                deploy.enable_plugins(self.config.plugins_txt_path, mod.plugins)
            log(f"Activado: {mod.name} ({len(mod.deployed_files)} archivos desplegados)")
        else:
            if mod.deployed_files and self.config.game_data_path:
                removed = deploy.undeploy(mod.deployed_files, self.config.game_data_path)
                log(f"Desactivado: {mod.name} ({removed} archivos retirados de Data)")
            if mod.deployed_root_files:
                groot = self._game_root()
                if groot:
                    deploy.undeploy_root(mod.deployed_root_files, groot)
            if mod.plugins and self.config.plugins_txt_path:
                deploy.disable_plugins(self.config.plugins_txt_path, mod.plugins)
        mod.enabled = enabled
        self.store.save()
        return True

    def set_hidden_files(self, mod_id: int, hidden, log=lambda m: None) -> bool:
        """Oculta/muestra archivos de un mod (rutas relativas a Data). Re-despliega el mod
        excluyendo los ocultos. Devuelve True si cambió algo."""
        mod = self.store.get(mod_id)
        if not mod:
            return False
        new_hidden = sorted({str(h).replace("\\", "/") for h in hidden})
        if new_hidden == sorted(mod.hidden_files or []):
            return False
        mod.hidden_files = new_hidden
        if mod.enabled and self.config.game_data_path:
            if mod.deployed_files:
                deploy.undeploy(mod.deployed_files, self.config.game_data_path)
            root_files = self._root_files_for(mod)
            mod.deployed_files = deploy.deploy(
                mod.install_dir, self.config.game_data_path, self.config.deploy_method,
                exclude=self._exclude_for(mod, root_files),
            )
            log(f"{mod.name}: {len(new_hidden)} archivo(s) oculto(s); re-desplegado.")
        self.store.save()
        return True

    def redeploy_file(self, mod_id: int, rel: str) -> bool:
        """Re-despliega un único archivo del mod a Data (tras editar un .ini)."""
        mod = self.store.get(mod_id)
        if not mod or not mod.enabled or not self.config.game_data_path:
            return False
        src = Path(mod.install_dir) / rel
        if not src.is_file():
            return False
        deploy._link_or_copy(src, Path(self.config.game_data_path) / rel,
                             self.config.deploy_method)
        return True

    def reorder_mods(self, ordered_ids: list[int], log=lambda m: None) -> None:
        """Asigna prioridad por el orden dado (el primero = menor prioridad; el último =
        mayor = gana los conflictos, como en MO2) y re-aplica el orden de sobrescritura."""
        for i, mid in enumerate(ordered_ids):
            m = self.store.get(mid)
            if m:
                m.priority = i
        self.store.save()
        self.apply_priority_order(log)

    def apply_priority_order(self, log=lambda m: None) -> int:
        """Re-despliega SOLO los archivos en conflicto desde el mod de mayor prioridad
        (mayor priority; la fecha desempata). No toca los archivos sin conflicto, así que
        es rápido aunque haya cientos de mods. Devuelve cuántos archivos se reasignaron."""
        if not self.config.game_data_path:
            return 0
        owners: dict[str, list] = {}
        for m in self.store.all():
            if not m.enabled:
                continue
            for rel in m.deployed_files:
                owners.setdefault(rel.lower(), []).append((m, rel))
        data = Path(self.config.game_data_path)
        changed = 0
        for lst in owners.values():
            if len(lst) < 2:
                continue
            winner_m, winner_rel = max(lst, key=lambda t: (t[0].priority, t[0].installed_at))
            src = Path(winner_m.install_dir) / winner_rel
            if src.is_file():
                try:
                    deploy._link_or_copy(src, data / winner_rel, self.config.deploy_method)
                    changed += 1
                except OSError:
                    pass
        if changed:
            log(f"Orden de prioridad aplicado: {changed} archivo(s) en conflicto reasignados.")
        return changed

    def uninstall(self, mod_id: int, log=lambda m: None) -> bool:
        mod = self.store.get(mod_id)
        if not mod:
            return False
        if mod.deployed_files and self.config.game_data_path:
            removed = deploy.undeploy(mod.deployed_files, self.config.game_data_path)
            log(f"Eliminados {removed} archivos de Data.")
        if mod.deployed_root_files:
            groot = self._game_root()
            if groot:
                r2 = deploy.undeploy_root(mod.deployed_root_files, groot)
                log(f"Eliminados {r2} archivos de la carpeta raíz del juego.")
        if mod.plugins and self.config.plugins_txt_path:
            deploy.disable_plugins(self.config.plugins_txt_path, mod.plugins)
        # Borrar carpeta gestionada
        try:
            base = Path(self.config.mods_dir) / _safe_name(
                f"{mod.mod_id}_{mod.name}"
            )
            if base.exists():
                shutil.rmtree(base, ignore_errors=True)
        except Exception:
            pass
        self.store.mods.pop(mod_id, None)
        self.store.save()
        log(f"Desinstalado: {mod.name}")
        return True
