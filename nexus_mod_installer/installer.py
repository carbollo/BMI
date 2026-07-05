"""Instalación de mods: extraer, detectar FOMOD, determinar raíz de datos,
desplegar a la carpeta Data del juego y activar plugins."""
from __future__ import annotations

import filecmp
import json
import os
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
        root_also = None   # árbol extra donde buscar archivos de raíz (no en FOMOD)
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
            root_also = extracted   # no-FOMOD: también archivos de raíz hermanos de la subcarpeta
            # BAIN (00 Core / 01 Optional…): BMI no instala por pasos; solo despliega una
            # carpeta. Avisar para que el usuario no crea que se instaló entero.
            bain = deploy.bain_subpackages(str(extracted))
            if bain:
                log("⚠ Paquete BAIN detectado ({}). BMI instalará solo la parte principal "
                    "«{}»; las demás debes instalarlas por separado (arrastra el .zip de "
                    "cada carpeta) o con Wrye Bash.".format(", ".join(bain), Path(data_root).name))

        # 3) Detectar plugins y archivos de carpeta raíz (Engine Fixes pt.2, ENB, SKSE…).
        #    Se buscan sobre el árbol QUE SE DESPLIEGA (data_root), no solo sobre _extracted:
        #    en FOMOD data_root es el staging '_data', y en mods envueltos en una carpeta los
        #    dll de raíz están anidados. Así se excluyen de Data y van a la raíz del juego.
        plugins = deploy.list_plugins(data_root)
        root_files = self._find_root_files(Path(data_root), root_also)

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
        vfs = getattr(self.config, "vfs_mode", False)
        log(f"Desplegando a {self.config.game_data_path} ({self.config.deploy_method})"
            + (" [modo VFS: solo plugins]" if vfs else "") + "...")
        deployed = deploy.deploy(
            mod.install_dir, self.config.game_data_path, self.config.deploy_method,
            exclude=self._exclude_for(mod, root_files), plugins_only=vfs,
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

    def _find_root_files(self, data_root: Path, also: Path | None = None):
        """Archivos de carpeta raíz (ENB, preloader, runtime del script extender). Busca en
        el árbol que SE DESPLIEGA (``data_root``). Si ``also`` (nivel superior de _extracted)
        se pasa y difiere, lo escanea también, por si hay archivos de raíz como hermanos de
        la subcarpeta de datos. ``also`` NO se pasa en FOMOD: allí solo vale el staging
        seleccionado (escanear _extracted metería archivos de opciones no elegidas)."""
        names = self.config.game().loader_exes
        found: dict = {}
        bases = {str(data_root)}
        if also is not None and Path(also).is_dir():
            bases.add(str(also))
        for base in bases:
            for src, dest in deploy.find_root_files(base, extra_names=names):
                found[src] = dest   # dedup por origen (evita duplicar si data_root==_extracted)
        return list(found.items())

    def _root_files_for(self, mod: InstalledMod):
        """Re-localiza los archivos de carpeta raíz desde la carpeta gestionada del mod (para
        re-desplegarlos al reactivarlo). Escanea el árbol desplegado (install_dir); no puede
        saber si fue FOMOD, así que se ciñe a lo desplegado (conservador y correcto)."""
        data_root = Path(mod.install_dir)
        if data_root.is_dir():
            return self._find_root_files(data_root)
        return []

    def _enable_plugins(self, plugins: list[str], log) -> None:
        # Morrowind no usa plugins.txt: se activa en [Game Files] de Morrowind.ini.
        if not self.config.game().uses_plugins_txt:
            ini = deploy.morrowind_ini_path(self.config.game_data_path)
            if ini is None:
                return
            added = deploy.enable_plugins_morrowind(ini, plugins)
            if added:
                log(f"Plugins activados en Morrowind.ini: {', '.join(added)}")
            return
        if not self.config.plugins_txt_path:
            return
        deploy.enable_plugins(self.config.plugins_txt_path, plugins,
                              star_prefix=self.config.game().star_prefix)
        log(f"Plugins activados en plugins.txt: {', '.join(plugins)}")

    def _disable_plugins(self, plugins: list[str], log) -> None:
        """Desactiva plugins (plugins.txt o Morrowind.ini según el juego)."""
        if not self.config.game().uses_plugins_txt:
            ini = deploy.morrowind_ini_path(self.config.game_data_path)
            if ini is not None:
                deploy.disable_plugins_morrowind(ini, plugins)
            return
        if self.config.plugins_txt_path:
            deploy.disable_plugins(self.config.plugins_txt_path, plugins)

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
                    plugins_only=getattr(self.config, "vfs_mode", False),
                )
                mod.deployed_root_files = self._deploy_root(root_files, log)
                # Re-desplegar lo convierte en el último escritor en disco: actualiza la
                # fecha para que la detección de conflictos acierte el ganador.
                mod.installed_at = time.time()
            if self.config.auto_enable_plugins and mod.plugins:
                self._enable_plugins(mod.plugins, log)
            log(f"Activado: {mod.name} ({len(mod.deployed_files)} archivos desplegados)")
        else:
            if mod.deployed_files and self.config.game_data_path:
                removed = deploy.undeploy(mod.deployed_files, self.config.game_data_path)
                log(f"Desactivado: {mod.name} ({removed} archivos retirados de Data)")
            if mod.deployed_root_files:
                groot = self._game_root()
                if groot:
                    deploy.undeploy_root(mod.deployed_root_files, groot)
            if mod.plugins:
                self._disable_plugins(mod.plugins, log)
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

    @staticmethod
    def _same_deployed(src: Path, tgt: Path, method: str) -> bool:
        """¿El archivo de Data ``tgt`` es realmente el que desplegó este mod?
        - hardlink: es el MISMO archivo (mismo inodo) → identidad a prueba de bombas.
        - copy: misma ruta relativa y MISMO CONTENIDO byte a byte (filecmp corta antes
          por tamaño, así que es barato). Nunca da True para un archivo vanilla o del
          usuario con bytes distintos, aunque coincida el tamaño."""
        try:
            if src.samefile(tgt):
                return True
        except OSError:
            pass
        if method != "hardlink":
            try:
                return filecmp.cmp(str(src), str(tgt), shallow=False)
            except OSError:
                return False
        return False

    def _loose_clean_plan(self) -> list[tuple]:
        """Calcula qué archivos NO-plugin de mods gestionados retirar de Data. Reescanea
        cada mod (no se fía del manifiesto, que puede estar incompleto) y solo marca un
        archivo de Data que PERTENECE de verdad al mod. Nunca toca .esp/.esm/.esl ni
        archivos vanilla/del usuario. Devuelve [(mod, rel, ruta_destino)]."""
        data = Path(self.config.game_data_path)
        method = self.config.deploy_method
        plan: list[tuple] = []
        seen: set[str] = set()
        for mod in self.store.all():
            if not mod.enabled:
                continue  # un mod desactivado ya está replegado: no posee nada en Data
            base = Path(mod.install_dir) if mod.install_dir else None
            if base and base.is_dir():
                for src in base.rglob("*"):
                    if not src.is_file() or src.suffix.lower() in deploy._PLUGIN_EXTS:
                        continue
                    rel = src.relative_to(base).as_posix()
                    key = rel.lower()
                    if key in seen:
                        continue
                    tgt = data / rel
                    if tgt.is_file() and self._same_deployed(src, tgt, method):
                        seen.add(key)
                        plan.append((mod, rel, tgt))
            else:
                # Respaldo: la carpeta del mod ya no está; usa el manifiesto.
                for rel in (mod.deployed_files or []):
                    if Path(rel).suffix.lower() in deploy._PLUGIN_EXTS:
                        continue
                    key = rel.replace("\\", "/").lower()
                    tgt = data / rel
                    if key not in seen and tgt.is_file():
                        seen.add(key)
                        plan.append((mod, rel.replace("\\", "/"), tgt))
        return plan

    def clean_loose_files(self, log=lambda m: None, dry_run: bool = False) -> list[str]:
        """Modo VFS: retira de Data TODOS los archivos NO-plugin de los mods gestionados
        (texturas, mallas, sonidos, .bsa…), que pasan a servirse virtualizados al jugar.
        Mantiene los .esp/.esm/.esl en Data para que plugins.txt y el escáner sigan
        coherentes. Reescanea cada mod y solo retira lo que le pertenece de verdad
        (mismo hardlink, o misma ruta+contenido en copia): nunca toca vanilla ni archivos
        del usuario. ``dry_run=True`` no borra; solo devuelve la lista que se retiraría."""
        if not self.config.game_data_path:
            return []
        plan = self._loose_clean_plan()
        if dry_run:
            return [rel for _, rel, _ in plan]
        removed: list[str] = []
        dirs: set[Path] = set()
        for mod, rel, tgt in plan:
            try:
                tgt.unlink()
                removed.append(rel)
                dirs.add(tgt.parent)
            except OSError:
                pass
        # Quita de TODOS los manifiestos los sueltos retirados (en Data ya solo quedan
        # plugins); así no quedan entradas obsoletas en el mod que perdió un conflicto.
        removed_keys = {r.replace("\\", "/").lower() for r in removed}
        if removed_keys:
            for mod in self.store.all():
                if mod.deployed_files:
                    mod.deployed_files = [
                        f for f in mod.deployed_files
                        if Path(f).suffix.lower() in deploy._PLUGIN_EXTS
                        or f.replace("\\", "/").lower() not in removed_keys
                    ]
        # Borra de dentro hacia fuera las subcarpetas que queden vacías (textures/…),
        # nunca la propia carpeta Data, nada por encima, ni junctions/symlinks del usuario.
        data = Path(self.config.game_data_path)
        _isjunc = getattr(os.path, "isjunction", lambda p: False)
        for d in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
            cur = d
            try:
                while (cur != data and data in cur.parents and cur.is_dir()
                       and not cur.is_symlink() and not _isjunc(str(cur))
                       and not any(cur.iterdir())):
                    parent = cur.parent
                    cur.rmdir()
                    cur = parent
            except OSError:
                pass
        if removed:
            self.store.save()
            log(f"Data aligerado para VFS: {len(removed)} archivo(s) retirados "
                "(se sirven virtualizados al jugar).")
        return removed

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
        if mod.plugins:
            self._disable_plugins(mod.plugins, log)
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
