"""Orquestador: cola de descargas/instalaciones en un hilo de fondo.

Maneja el ciclo completo por cada mod:
  resolver enlace -> descargar -> extraer/instalar -> desplegar -> resolver dependencias.

Emite señales Qt para que la GUI se actualice (las señales son seguras entre hilos).
"""
from __future__ import annotations

import queue
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .config import AppConfig
from .models import DownloadTask, NxmLink, TaskStatus
from .nexus_api import NexusApiClient, PremiumRequiredError, NexusApiError
from .nexus_graphql import NexusGraphQLClient, parse_collection_url
from .installer import Installer, InstalledModsStore
from . import downloader, translations


def mod_page_url(game_domain: str, mod_id: int) -> str:
    return f"https://www.nexusmods.com/{game_domain}/mods/{mod_id}?tab=files"


class FomodRequest:
    """Petición de elección FOMOD que cruza del hilo de trabajo al hilo de la GUI."""

    def __init__(self, config):
        self.config = config            # FomodConfig
        self.event = threading.Event()
        self.result = None              # list[FomodPlugin] | None


class DownloadManager(QObject):
    task_added = Signal(object)     # DownloadTask
    task_updated = Signal(object)   # DownloadTask
    log = Signal(str)
    needs_click = Signal(object)    # DownloadTask que requiere clic en la web
    fomod_requested = Signal(object)  # FomodRequest (la GUI muestra el asistente)
    tasks_changed = Signal()        # la lista de tareas cambió (p.ej. se quitaron varias)

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.api = NexusApiClient(config.api_key)
        self.graphql = NexusGraphQLClient(config.api_key)
        self.store = InstalledModsStore(config)
        self.installer = Installer(config, self.store)

        self._is_premium = False
        self._queue: "queue.Queue[DownloadTask | None]" = queue.Queue()
        self.tasks: list[DownloadTask] = []
        self._seen: set[tuple[int, int]] = set()
        self._inflight_mods: set[int] = set()  # mods en cola/proceso (evita duplicados)
        self._manual_counter = 0  # ids negativos para instalaciones manuales/locales
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    # ------------------------------------------------------------------
    def update_credentials(self) -> None:
        """Valida la API key en segundo plano (no bloquea la interfaz)."""
        self.api.set_api_key(self.config.api_key)
        self.graphql.set_api_key(self.config.api_key)
        if not self.config.api_key:
            return
        threading.Thread(target=self._validate_credentials, daemon=True).start()

    def _validate_credentials(self) -> None:
        try:
            user = self.api.validate()
            self._is_premium = bool(user.get("is_premium"))
            self.log.emit(
                f"Sesión API: {user.get('name','?')} "
                f"({'PREMIUM' if self._is_premium else 'gratis'})."
            )
        except Exception as e:
            self.log.emit(f"No se pudo validar la API key: {e}")

    def reload_for_game(self) -> None:
        """Recarga el store y el instalador para el juego activo (tras cambiar de juego)."""
        self.store = InstalledModsStore(self.config)
        self.installer = Installer(self.config, self.store)
        with self._lock:
            self._inflight_mods.clear()
            self._seen.clear()
        self.log.emit(f"🎮 Juego activo: {self.config.game().name}")

    # ------------------------------------------------------------------
    def _fomod_chooser(self, fomod_config):
        """Pide la selección FOMOD a la GUI y bloquea el hilo de trabajo hasta tenerla.

        Devuelve None si el modo es 'auto' (sin asistente) o si el usuario cancela.
        """
        if self.config.fomod_mode != "interactive":
            return None
        req = FomodRequest(fomod_config)
        self.fomod_requested.emit(req)
        req.event.wait()
        return req.result

    # ------------------------------------------------------------------
    # Encolado
    # ------------------------------------------------------------------
    def enqueue_task(self, task: DownloadTask) -> None:
        keyt = (task.mod_id, task.file_id)
        added = False
        upgraded = False
        with self._lock:
            # UPGRADE: llega la descarga REAL de un mod que estaba ESPERANDO clic, ya sea por
            # nxm:// (key+expires) o por descarga lenta (archivo local). Sustituye su marcador
            # 'Requiere clic en web' y procede a instalar (sin esto, chocaba con el marcador y
            # se descartaba como duplicado sin descargarse).
            if task.mod_id > 0 and (task.has_credentials or task.archive_path):
                ph = [t for t in self.tasks
                      if t.mod_id == task.mod_id and not t.cancelled
                      and t.status == TaskStatus.NEEDS_CLICK]
                if ph:
                    for t in ph:
                        t.cancelled = True
                        self._seen.discard((t.mod_id, t.file_id))
                        self._inflight_mods.discard(t.mod_id)
                    self.tasks = [t for t in self.tasks if t not in ph]
                    upgraded = True

            dup = False
            # Dedupe a nivel de MOD: instalado de verdad o ya en cola/proceso.
            if task.mod_id > 0 and (
                task.mod_id in self._inflight_mods or self.store.is_installed(task.mod_id)
            ):
                self.log.emit(f"↩ Mod {task.mod_id} ya instalado o en cola; se omite duplicado.")
                dup = True
            # ...o ya hay una tarea del mismo mod EN LA LISTA pendiente de clic.
            elif task.mod_id > 0 and any(
                t.mod_id == task.mod_id and not t.cancelled and t.status in self._LIVE_STATES
                for t in self.tasks
            ):
                self.log.emit(f"↩ Mod {task.mod_id} ya está en la lista; se omite duplicado.")
                dup = True
            elif task.file_id and keyt in self._seen:
                dup = True

            if not dup:
                if task.mod_id > 0:
                    self._inflight_mods.add(task.mod_id)
                if task.file_id:
                    self._seen.add(keyt)
                self.tasks.append(task)
                added = True

        if added:
            self._queue.put(task)
        if upgraded:
            self.tasks_changed.emit()   # rebuild: ya muestra la tarea real añadida
        elif added:
            self.task_added.emit(task)

    def clear_completed(self) -> None:
        """Quita de la lista visible las tareas completadas y libera su dedupe de archivo
        (para poder volver a encolarlas más tarde)."""
        with self._lock:
            done = [t for t in self.tasks if t.status == TaskStatus.DONE]
            self.tasks = [t for t in self.tasks if t.status != TaskStatus.DONE]
            for t in done:
                self._seen.discard((t.mod_id, t.file_id))

    # Estados desde los que una tarea se puede quitar de la lista (no se está
    # descargando/instalando activamente, así que es seguro retirarla).
    _REMOVABLE = frozenset({
        TaskStatus.QUEUED, TaskStatus.NEEDS_CLICK, TaskStatus.ERROR, TaskStatus.DONE,
    })

    def remove_tasks(self, tasks) -> int:
        """Quita varias tareas de la cola/lista. Las que están EN COLA se marcan como
        canceladas (el worker las saltará al sacarlas). No retira las que se están
        descargando/instalando en este momento. Devuelve cuántas se quitaron."""
        sel = {id(t) for t in tasks}
        removed = 0
        with self._lock:
            keep = []
            for t in self.tasks:
                if id(t) in sel and t.status in self._REMOVABLE:
                    t.cancelled = True
                    self._seen.discard((t.mod_id, t.file_id))
                    # Si ya no sigue en la cola interna, libera también el dedupe de mod.
                    if t.status != TaskStatus.QUEUED and t.mod_id > 0:
                        self._inflight_mods.discard(t.mod_id)
                    removed += 1
                else:
                    keep.append(t)
            self.tasks = keep
        if removed:
            self.tasks_changed.emit()
        return removed

    _ACTIVE_STATES = frozenset({
        TaskStatus.QUEUED, TaskStatus.RESOLVING, TaskStatus.DOWNLOADING,
        TaskStatus.EXTRACTING, TaskStatus.INSTALLING, TaskStatus.DEPLOYING,
    })
    # Estados "vivos" para deduplicar: activos + esperando clic del usuario.
    _LIVE_STATES = _ACTIVE_STATES | {TaskStatus.NEEDS_CLICK}

    def has_pending_work(self) -> bool:
        """True si hay descargas/instalaciones en curso o en cola (no seguro cambiar de
        juego: instalaría en el juego equivocado)."""
        with self._lock:
            if self._inflight_mods or not self._queue.empty():
                return True
            return any(t.status in self._ACTIVE_STATES for t in self.tasks)

    def purge_pending(self) -> None:
        """Vacía la cola de trabajo y las tareas (al cambiar de juego, estando ya inactivo).
        Debe llamarse solo cuando has_pending_work() es False."""
        with self._lock:
            try:
                while True:
                    self._queue.get_nowait()
                    self._queue.task_done()
            except queue.Empty:
                pass
            self.tasks.clear()
            self._inflight_mods.clear()
            self._seen.clear()

    def enqueue_nxm(self, url: str) -> None:
        try:
            # ¿Es una colección?
            if "/collections/" in url:
                self.enqueue_collection(url)
                return
            link = NxmLink.parse(url)
        except ValueError as e:
            self.log.emit(f"Enlace nxm inválido: {e}")
            return
        task = DownloadTask.from_nxm(link)
        self.log.emit(f"Recibido nxm:// para mod {link.mod_id}, archivo {link.file_id}.")
        self.enqueue_task(task)

    def enqueue_collection(self, url: str) -> None:
        threading.Thread(target=self._resolve_collection, args=(url,), daemon=True).start()

    def enqueue_mod(self, game_domain: str, mod_id: int, extra_deps=None) -> None:
        """Encola un mod por su id (resuelve su archivo principal en segundo plano).

        ``extra_deps``: dependencias leídas de la página de Nexus (sección Requirements,
        atributo download-links), como [(dominio, mod_id, file_id, nombre), ...]. Son la
        fuente AUTORITATIVA (a veces el GraphQL no las trae); se encolan sí o sí.

        Cuenta PREMIUM: la API entrega el enlace directo -> descarga AUTOMÁTICA, sin clics.
        Cuenta gratuita: la tarea pasa a 'Requiere clic en web' (se abre la página del mod).
        """
        threading.Thread(
            target=self._resolve_and_enqueue_mod, args=(game_domain, mod_id, extra_deps),
            daemon=True,
        ).start()

    def _resolve_and_enqueue_mod(self, game_domain: str, mod_id: int, extra_deps=None) -> None:
        # Tarea del mod principal (con su nombre, para etiqueta y búsqueda de traducción).
        try:
            fid = self._primary_file_id(game_domain, mod_id) or 0
        except Exception:
            fid = 0
        main = DownloadTask(game_domain=game_domain, mod_id=mod_id, file_id=fid)
        self._fill_metadata(main)

        # 0) Dependencias leídas de la PÁGINA (Requirements/download-links): autoritativas,
        #    con su file_id exacto. Garantiza que se descargan aunque el GraphQL no las traiga.
        if self.config.resolve_dependencies and extra_deps:
            self.log.emit(f"🔗 {len(extra_deps)} dependencia(s) leídas de la página (Requirements).")
            for (ddom, did, dfid, dname) in extra_deps:
                if not did or did == mod_id:
                    continue
                if self.store.is_installed(did) or did in self._inflight_mods:
                    continue
                if not dfid:
                    try:
                        dfid = self._primary_file_id(ddom or game_domain, did) or 0
                    except Exception:
                        dfid = 0
                self.enqueue_task(DownloadTask(
                    game_domain=ddom or game_domain, mod_id=did, file_id=dfid,
                    mod_name=dname or "", is_dependency=True))

        # 1) Dependencias del GraphQL (TODAS, incluidas las transitivas), como complemento.
        #    enqueue_task evita duplicar las ya añadidas arriba, instaladas o en la lista.
        if self.config.resolve_dependencies:
            try:
                deps = self._collect_deps(game_domain, mod_id)
            except Exception:
                deps = []
            if deps:
                self.log.emit(f"🔗 {len(deps)} dependencia(s) detectada(s); añadiéndolas a la lista.")
            for g, did, dname in deps:
                if self.store.is_installed(did) or did in self._inflight_mods:
                    continue
                try:
                    dfid = self._primary_file_id(g, did) or 0
                except Exception:
                    dfid = 0
                self.enqueue_task(DownloadTask(
                    game_domain=g, mod_id=did, file_id=dfid,
                    mod_name=dname or "", is_dependency=True))

        # 2) El mod principal (después de sus dependencias).
        self.enqueue_task(main)

        # 3) La traducción al idioma de la app, para que también salga en la lista.
        if self.config.install_spanish_translation:
            try:
                self._enqueue_translation(main)
            except Exception as e:
                self.log.emit(f"No se pudo resolver la traducción: {e}")

    def _collect_deps(self, game_domain: str, mod_id: int,
                      _seen: set | None = None, _depth: int = 0) -> list[tuple[str, int, str]]:
        """Recoge TODAS las dependencias de Nexus (requisitos), incluidas las transitivas,
        en orden 'dependencias primero' (las de cada dependencia van antes que ella) y sin
        duplicados. Los requisitos externos (SKSE, etc.) los omite el GraphQL. Tope de
        profundidad para evitar bucles."""
        if _seen is None:
            _seen = set()
        out: list[tuple[str, int, str]] = []
        if _depth >= 6:
            return out
        try:
            reqs = self.graphql.mod_requirements(game_domain, mod_id)
        except Exception:
            return out
        for r in reqs:
            if r.mod_id == mod_id or r.mod_id in _seen:
                continue
            _seen.add(r.mod_id)
            out.extend(self._collect_deps(r.game_domain, r.mod_id, _seen, _depth + 1))
            out.append((r.game_domain, r.mod_id, r.name))
        return out

    def enqueue_local(self, path: str, name: str | None = None,
                      mod_id: int | None = None, game_domain: str | None = None,
                      is_translation: bool = False) -> None:
        """Instala un archivo ya descargado localmente (p.ej. una descarga manual del navegador).

        Si se conoce el mod_id real, se conserva para poder resolver dependencias y
        traducción tras instalar (como en la vía nxm://). Sin mod_id, se usa un id
        interno negativo (instalación suelta sin resolución).
        """
        if mod_id and mod_id > 0:
            task = DownloadTask(
                game_domain=game_domain or self.config.game().domain,
                mod_id=mod_id, file_id=0,
                mod_name=name or "", is_translation=is_translation,
            )
        else:
            self._manual_counter -= 1
            task = DownloadTask(
                game_domain=game_domain or self.config.game().domain,
                mod_id=self._manual_counter, file_id=0,
                mod_name=name or Path(path).stem,
            )
        task.archive_path = path
        self.enqueue_task(task)

    # ------------------------------------------------------------------
    def _resolve_collection(self, url: str) -> None:
        parsed = parse_collection_url(url)
        if not parsed:
            self.log.emit(f"No reconozco la URL de colección: {url}")
            return
        slug, revision = parsed
        self.log.emit(f"Resolviendo colección '{slug}' (revisión {revision or 'última'})...")
        try:
            info = self.graphql.resolve_collection(slug, revision)
        except Exception as e:
            self.log.emit(
                f"No se pudo resolver la colección: {e}\n"
                "El esquema GraphQL de Nexus pudo cambiar; revisa nexus_graphql.py."
            )
            return
        self.log.emit(
            f"Colección '{info.name}': {len(info.mods)} mods, "
            f"{len(info.external)} recursos externos."
        )
        for ref in info.mods:
            if ref.optional:
                continue
            task = DownloadTask(
                game_domain=ref.game_domain,
                mod_id=ref.mod_id,
                file_id=ref.file_id,
                mod_name=ref.name,
                from_collection=slug,
            )
            self.enqueue_task(task)
        for ext in info.external:
            self.log.emit(
                f"Recurso externo (descarga manual): {ext.get('name','?')} -> "
                f"{ext.get('resourceUrl','') or ext.get('url','')}"
            )

    # ------------------------------------------------------------------
    # Hilo de trabajo
    # ------------------------------------------------------------------
    def _emit_update(self, task: DownloadTask) -> None:
        self.task_updated.emit(task)

    def _set(self, task: DownloadTask, status: TaskStatus) -> None:
        task.status = status
        self._emit_update(task)

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                break
            try:
                if task.cancelled:
                    self.log.emit(f"⏭ {task.label} cancelada; se omite.")
                else:
                    self._process(task)
            except Exception as e:
                task.status = TaskStatus.ERROR
                task.error = str(e)
                self._emit_update(task)
                self.log.emit(f"ERROR en {task.label}: {e}")
            finally:
                if task.mod_id > 0:
                    with self._lock:
                        self._inflight_mods.discard(task.mod_id)
                self._queue.task_done()

    def _process(self, task: DownloadTask) -> None:
        # 0) ¿Archivo local ya descargado? -> instalar directamente.
        if task.archive_path and Path(task.archive_path).is_file():
            self._set(task, TaskStatus.INSTALLING)
            self.installer.install(task, log=self.log.emit, fomod_chooser=self._fomod_chooser)
            self._set(task, TaskStatus.DONE)
            task.progress = 100.0
            self._emit_update(task)
            # Si conocemos el mod real (descarga manual con contexto del mod),
            # resolvemos dependencias y traducción igual que en la vía nxm://.
            if task.mod_id > 0:
                self._fill_metadata(task)
                if self.config.resolve_dependencies:
                    self._enqueue_dependencies(task)
                if self.config.install_spanish_translation and not task.is_translation:
                    self._enqueue_translation(task)
            return

        # 1) Metadatos
        self._set(task, TaskStatus.RESOLVING)
        self._fill_metadata(task)

        # 2) ¿Tenemos forma de obtener el enlace?
        if not task.has_credentials and not self._is_premium:
            task.status = TaskStatus.NEEDS_CLICK
            task.error = (
                "Cuenta gratuita: abre la página del mod y pulsa "
                "'Mod Manager Download' para autorizar la descarga."
            )
            self._emit_update(task)
            self.needs_click.emit(task)
            self.log.emit(
                f"⏳ {task.label} requiere 1 clic en la web: "
                f"{mod_page_url(task.game_domain, task.mod_id)}"
            )
            return

        # 3) Resolver enlace de descarga
        self.log.emit(f"Resolviendo enlace de {task.label}...")
        try:
            url = self.api.get_download_link(
                task.game_domain, task.mod_id, task.file_id,
                key=task.key, expires=task.expires,
            )
        except PremiumRequiredError as e:
            task.status = TaskStatus.NEEDS_CLICK
            task.error = str(e)
            self._emit_update(task)
            self.needs_click.emit(task)
            return

        # 4) Descargar
        self._set(task, TaskStatus.DOWNLOADING)

        def cb(done, total, speed):
            task.downloaded_bytes = done
            task.total_bytes = total
            task.speed_bps = speed
            task.progress = (done / total * 100.0) if total else 0.0
            self._emit_update(task)

        archive_path = downloader.download(
            url, self.config.downloads_dir, file_name=task.file_name or None, progress_cb=cb
        )
        task.archive_path = str(archive_path)
        self.log.emit(f"Descargado: {archive_path.name}")

        # 5) Instalar + desplegar
        self._set(task, TaskStatus.INSTALLING)
        self.installer.install(task, log=self.log.emit, fomod_chooser=self._fomod_chooser)

        self._set(task, TaskStatus.DONE)
        task.progress = 100.0
        self._emit_update(task)

        # 6) Dependencias
        if self.config.resolve_dependencies:
            self._enqueue_dependencies(task)

        # 7) Traducción al español
        if self.config.install_spanish_translation and not task.is_translation:
            self._enqueue_translation(task)

    # ------------------------------------------------------------------
    def _fill_metadata(self, task: DownloadTask) -> None:
        try:
            if not task.mod_name:
                mod = self.api.get_mod(task.game_domain, task.mod_id)
                task.mod_name = mod.name
                task.version = task.version or mod.version
        except NexusApiError:
            pass
        try:
            if task.file_id and not task.file_name:
                f = self.api.get_file(task.game_domain, task.mod_id, task.file_id)
                task.file_name = f.file_name
                task.version = task.version or f.version
        except NexusApiError:
            pass
        self._emit_update(task)

    def _enqueue_dependencies(self, task: DownloadTask) -> None:
        try:
            deps = self.graphql.mod_requirements(task.game_domain, task.mod_id)
        except Exception:
            deps = []
        if not deps:
            return
        self.log.emit(f"{task.label} requiere {len(deps)} mod(s); encolando dependencias...")
        for dep in deps:
            if self.store.is_installed(dep.mod_id):
                continue
            file_id = self._primary_file_id(dep.game_domain, dep.mod_id)
            dep_task = DownloadTask(
                game_domain=dep.game_domain,
                mod_id=dep.mod_id,
                file_id=file_id or 0,
                mod_name=dep.name,
                is_dependency=True,
            )
            self.enqueue_task(dep_task)

    def _enqueue_translation(self, task: DownloadTask) -> None:
        """Encola la traducción del mod en el IDIOMA DE LA APP (config.language)."""
        lang = self.config.language or "es"
        lang_name = translations.NEXUS_LANGUAGE_NAME.get(lang)
        if not lang_name:
            return
        code = lang.upper()

        # 1) ¿Hay un archivo en ese idioma DENTRO del mismo mod?
        same = translations.find_translation_file_in_mod(
            self.graphql, task.game_domain, task.mod_id, lang,
            exclude_file_id=task.file_id, log=self.log.emit,
        )
        if same:
            fid, fname = same
            self.log.emit(f"🌐 Traducción ({lang_name}) en el mismo mod: '{fname}'. Encolando…")
            tr_task = DownloadTask(
                game_domain=task.game_domain,
                mod_id=task.mod_id,
                file_id=fid,
                mod_name=f"{task.mod_name or 'mod'} ({code})",
                file_name=fname,
                is_translation=True,
            )
            self.enqueue_task(tr_task)
            return

        # El inglés es el idioma base de la mayoría de mods: buscar un "mod de traducción al
        # inglés" aparte da demasiados falsos positivos (casi todo está en inglés), se omite.
        if lang == "en":
            return

        # 2) ¿Existe un mod de traducción aparte?
        if not task.mod_name:
            return
        try:
            refs = translations.find_translations(
                self.graphql, task.game_domain, task.mod_id, task.mod_name, lang,
                log=self.log.emit,
            )
        except Exception as e:
            self.log.emit(f"No se pudo buscar la traducción ({lang_name}): {e}")
            return
        if not refs:
            self.log.emit(f"Sin traducción ({lang_name}) encontrada para {task.label}.")
            return
        # Tomamos la mejor candidata (mayor relevancia).
        best = refs[0]
        if self.store.is_installed(best.mod_id):
            return
        self.log.emit(
            f"🌐 Traducción ({lang_name}) encontrada para {task.label}: "
            f"'{best.name}' (mod {best.mod_id}). Encolando…"
        )
        file_id = self._primary_file_id(best.game_domain, best.mod_id) or 0
        tr_task = DownloadTask(
            game_domain=best.game_domain,
            mod_id=best.mod_id,
            file_id=file_id,
            mod_name=best.name,
            is_translation=True,
        )
        self.enqueue_task(tr_task)

    def missing_requirements(self, game_domain: str, mod_id: int) -> list[tuple[str, int, int]]:
        """Requisitos (mods de Nexus) de un mod que NO están instalados ni en cola.

        Devuelve [(game_domain, mod_id, file_id_principal), ...]. Se usa para encolar
        las dependencias ANTES del mod principal. Los requisitos externos
        (p.ej. SKSE) no están en Nexus y se omiten (se avisa en el log).
        """
        try:
            reqs = self.graphql.mod_requirements(game_domain, mod_id)
        except Exception:
            return []
        out: list[tuple[str, int, int]] = []
        for r in reqs:
            if self.store.is_installed(r.mod_id) or r.mod_id in self._inflight_mods:
                continue
            fid = self._primary_file_id(r.game_domain, r.mod_id) or 0
            out.append((r.game_domain, r.mod_id, fid))
            self.log.emit(f"   requisito que falta: {r.name or ('mod ' + str(r.mod_id))}")
        return out

    def _primary_file_id(self, game_domain: str, mod_id: int) -> int | None:
        """Devuelve el archivo PRINCIPAL del mod (categoría MAIN, el más reciente).

        Usa GraphQL modFiles (categoryId: 1=MAIN, 4=OLD, 5=MISC, 6=DEL, 7=ARCH).
        Evita elegir parches/archivos viejos por error.
        """
        try:
            files = self.graphql.mod_files(game_domain, mod_id)
        except Exception:
            files = []
        if files:
            def fid(f) -> int:
                try:
                    return int(f.get("fileId") or 0)
                except (TypeError, ValueError):
                    return 0
            main = [f for f in files if f.get("categoryId") == 1]
            if main:
                return fid(max(main, key=fid))
            # Sin MAIN: el más reciente que no sea viejo/archivado/borrado.
            current = [f for f in files if f.get("categoryId") not in (4, 6, 7)]
            pool = current or files
            best = fid(max(pool, key=fid))
            if best:
                return best
        # Respaldo por REST.
        try:
            rest = self.api.get_files(game_domain, mod_id)
        except NexusApiError:
            return None
        for f in rest:
            if f.is_primary:
                return f.file_id
        return rest[0].file_id if rest else None

    def shutdown(self) -> None:
        self._queue.put(None)
