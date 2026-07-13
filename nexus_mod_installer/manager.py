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
from .i18n import tr
from . import downloader, translations, variants, oauth


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
    # (dominio, mod_id, nombre, buscar_traducción): pedir a la GUI que lea la página del
    # mod con el navegador embebido (traducciones oficiales y/o requisitos Requirements).
    page_lookup = Signal(str, int, str, bool)
                                                # oficiales de la página del mod (vía navegador)
    mods_imported = Signal(int)     # nº de mods detectados e importados de la carpeta de mods

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.api = NexusApiClient()
        self.graphql = NexusGraphQLClient()
        # Sesión OAuth de Nexus: ÚNICO método de autenticación (los ToS de Nexus prohíben las
        # API keys personales). Los clientes usan el token Bearer de esta sesión.
        self.oauth = oauth.OAuthSession()
        self.api.set_bearer_provider(self.oauth.access_token_or_none)
        self.graphql.set_bearer_provider(self.oauth.access_token_or_none)
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
        """Valida la sesión OAuth en segundo plano (no bloquea la interfaz)."""
        if not self.oauth.is_logged_in:
            return
        threading.Thread(target=self._validate_credentials, daemon=True).start()

    # --- OAuth (login oficial con Nexus) ----------------------------------
    @property
    def is_logged_in(self) -> bool:
        return self.oauth.is_logged_in

    def start_login(self):
        """Inicia el flujo OAuth. Devuelve (flow, authorize_url) para cargar en el webview."""
        flow = oauth.LoginFlow()
        return flow, flow.authorize_url()

    def complete_login(self, flow, redirect_url: str) -> dict:
        """Del redirect capturado por el webview al token; guarda la sesión y valida."""
        token = flow.complete(redirect_url)
        self.oauth.set_token(token)
        self.update_credentials()
        try:
            return oauth.fetch_userinfo(token.access_token)
        except Exception:  # noqa: BLE001
            return {}

    def logout(self) -> None:
        self.oauth.logout()
        self._is_premium = False
        self.log.emit(tr("Sesión de Nexus cerrada."))

    def _validate_credentials(self) -> None:
        try:
            user = self.api.validate()
            self._is_premium = bool(user.get("is_premium"))
            tier = tr("PREMIUM") if self._is_premium else tr("gratis")
            self.log.emit(tr("Sesión API: {name} ({tier}).")
                          .format(name=user.get('name', '?'), tier=tier))
        except Exception as e:
            self.log.emit(tr("No se pudo validar la sesión de Nexus: {e}").format(e=e))

    def reload_for_game(self) -> None:
        """Recarga el store y el instalador para el juego activo (tras cambiar de juego)."""
        self.store = InstalledModsStore(self.config)
        self.installer = Installer(self.config, self.store)
        with self._lock:
            self._inflight_mods.clear()
            self._seen.clear()
        self.log.emit(tr("🎮 Juego activo: {name}").format(name=self.config.game().name))

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
    def enqueue_task(self, task: DownloadTask, explicit: bool = False) -> None:
        # ``explicit``: el usuario pidió ESTE archivo concreto (clic en nxm:// / "Mod
        # Manager Download"). En ese caso se permite bajar otra parte de un mod que ya
        # esté instalado (muchos mods tienen varios archivos: base + texturas + parche…);
        # solo se bloquea volver a bajar EXACTAMENTE el mismo archivo.
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
            # 1) Duplicado EXACTO del mismo archivo (mod+file) ya visto o vivo en la lista:
            #    se omite SIEMPRE (no tiene sentido bajar dos veces el mismo archivo).
            if task.file_id and (
                keyt in self._seen
                or any(t.mod_id == task.mod_id and t.file_id == task.file_id
                       and not t.cancelled and t.status in self._LIVE_STATES
                       for t in self.tasks)
            ):
                dup = True
            # 2) Dedupe a nivel de MOD (instalado / en cola): SOLO para peticiones NO
            #    explícitas (resolver "este mod", dependencias, colecciones). Un nxm://
            #    explícito de un archivo concreto SÍ puede bajar otra parte del mismo mod.
            elif not explicit and task.mod_id > 0 and (
                task.mod_id in self._inflight_mods or self.store.is_installed(task.mod_id)
                or any(t.mod_id == task.mod_id and not t.cancelled
                       and t.status in self._LIVE_STATES for t in self.tasks)
            ):
                self.log.emit(tr("↩ Mod {id} ya instalado o en cola; se omite duplicado.")
                              .format(id=task.mod_id))
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
            self.log.emit(tr("Enlace nxm inválido: {e}").format(e=e))
            return
        task = DownloadTask.from_nxm(link)
        self.log.emit(tr("Recibido nxm:// para mod {mod}, archivo {file}.")
                      .format(mod=link.mod_id, file=link.file_id))
        self.enqueue_task(task, explicit=True)

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
            self.log.emit(tr("🔗 {n} dependencia(s) leídas de la página (Requirements).")
                          .format(n=len(extra_deps)))
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
                self.log.emit(tr("🔗 {n} dependencia(s) detectada(s); añadiéndolas a la lista.")
                              .format(n=len(deps)))
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

        # 3) La traducción al idioma de la app y la lectura de su página (traducciones
        #    oficiales + requisitos de Requirements), para que salgan ya en la lista.
        want_tr = False
        if self.config.install_spanish_translation:
            try:
                want_tr = bool(self._enqueue_translation(main))
            except Exception as e:
                self.log.emit(tr("No se pudo resolver la traducción: {e}").format(e=e))
        if (want_tr or self.config.resolve_dependencies) and main.mod_name:
            self.page_lookup.emit(game_domain, mod_id, main.mod_name, want_tr)

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
            self.log.emit(tr("No reconozco la URL de colección: {url}").format(url=url))
            return
        slug, revision = parsed
        self.log.emit(tr("Resolviendo colección '{slug}' (revisión {rev})…")
                      .format(slug=slug, rev=revision or tr("última")))
        try:
            info = self.graphql.resolve_collection(slug, revision)
        except Exception as e:
            self.log.emit(tr("No se pudo resolver la colección: {e}\n"
                             "El esquema GraphQL de Nexus pudo cambiar; revisa nexus_graphql.py.")
                          .format(e=e))
            return
        self.log.emit(tr("Colección '{name}': {n} mods, {ext} recursos externos.")
                      .format(name=info.name, n=len(info.mods), ext=len(info.external)))
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
            self.log.emit(tr("Recurso externo (descarga manual): {name} → {url}")
                          .format(name=ext.get('name', '?'),
                                  url=ext.get('resourceUrl', '') or ext.get('url', '')))

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
                    self.log.emit(tr("⏭ {label} cancelada; se omite.").format(label=task.label))
                else:
                    self._process(task)
            except Exception as e:
                task.status = TaskStatus.ERROR
                task.error = str(e)
                self._emit_update(task)
                self.log.emit(tr("ERROR en {label}: {e}").format(label=task.label, e=e))
            finally:
                if task.mod_id > 0:
                    with self._lock:
                        self._inflight_mods.discard(task.mod_id)
                self._queue.task_done()

    def _variant_block(self, task: DownloadTask) -> bool:
        """Si el filtro está activo y el mod/archivo es una variante de plataforma que NO
        corresponde al juego (GOG en un juego de Steam, o VR en un Skyrim/Fallout normal),
        aborta la descarga con un mensaje claro. Devuelve True si la bloqueó."""
        if (not getattr(self.config, "block_wrong_variant", True)
                or getattr(task, "is_translation", False)):
            return False
        plat = variants.game_platform(self.config)
        reason = variants.wrong_variant_reason(f"{task.mod_name} {task.file_name}", plat)
        if not reason:
            return False
        where = plat.upper() if plat != "unknown" else ""
        task.status = TaskStatus.ERROR
        task.error = (
            f"Variante {reason} incompatible con tu juego ({where} {self.config.game().name}). "
            f"Descarga la versión normal para Skyrim SE. Puedes desactivar este filtro en Ajustes."
        )
        self._emit_update(task)
        self.log.emit(tr("🚫 {label}: variante {reason} incompatible con tu juego "
                         "({where}); no se descarga. Busca la versión normal.")
                      .format(label=task.label, reason=reason, where=where))
        return True

    def _process(self, task: DownloadTask) -> None:
        # 0) ¿Archivo local ya descargado? -> instalar directamente.
        if task.archive_path and Path(task.archive_path).is_file():
            if self._variant_block(task):
                return
            self._set(task, TaskStatus.INSTALLING)
            self.installer.install(task, log=self.log.emit, fomod_chooser=self._fomod_chooser)
            self._set(task, TaskStatus.DONE)
            task.progress = 100.0
            self._emit_update(task)
            # Si conocemos el mod real (descarga manual con contexto del mod),
            # resolvemos dependencias, traducción y página igual que en la vía nxm://.
            if task.mod_id > 0:
                self._fill_metadata(task)
                self._enqueue_extras(task)
            return

        # 1) Metadatos
        self._set(task, TaskStatus.RESOLVING)
        self._fill_metadata(task)
        if self._variant_block(task):   # variante GOG/VR incompatible -> no descargar
            return

        # 2) ¿Tenemos forma de obtener el enlace?
        if not task.has_credentials and not self._is_premium:
            task.status = TaskStatus.NEEDS_CLICK
            task.error = (
                "Cuenta gratuita: abre la página del mod y pulsa "
                "'Mod Manager Download' para autorizar la descarga."
            )
            self._emit_update(task)
            self.needs_click.emit(task)
            self.log.emit(tr("⏳ {label} requiere 1 clic en la web: {url}")
                          .format(label=task.label,
                                  url=mod_page_url(task.game_domain, task.mod_id)))
            return

        # 3) Resolver enlace de descarga
        self.log.emit(tr("Resolviendo enlace de {label}…").format(label=task.label))
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
        self.log.emit(tr("Descargado: {name}").format(name=archive_path.name))

        # 5) Instalar + desplegar
        self._set(task, TaskStatus.INSTALLING)
        self.installer.install(task, log=self.log.emit, fomod_chooser=self._fomod_chooser)

        self._set(task, TaskStatus.DONE)
        task.progress = 100.0
        self._emit_update(task)

        # 6) Dependencias, traducción y lectura de su página (requisitos + traducciones)
        self._enqueue_extras(task)

    # ------------------------------------------------------------------
    def _fill_metadata(self, task: DownloadTask) -> None:
        try:
            if not task.mod_name or not task.picture_url:
                mod = self.api.get_mod(task.game_domain, task.mod_id)
                task.mod_name = task.mod_name or mod.name
                task.version = task.version or mod.version
                task.picture_url = task.picture_url or getattr(mod, "picture_url", "")
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

    def _enqueue_extras(self, task: DownloadTask) -> None:
        """Tras instalar un mod: encola sus dependencias (GraphQL), su traducción y pide a
        la GUI leer su página (requisitos de la sección «Nexus requirements» + traducciones
        oficiales). Como CADA tarea encolada vuelve a pasar por aquí al completarse, el
        proceso es RECURSIVO: los requisitos de los requisitos, y las traducciones de esos
        requisitos, también se descargan."""
        if self.config.resolve_dependencies:
            self._enqueue_dependencies(task)
        want_tr = False
        if self.config.install_spanish_translation and not task.is_translation:
            want_tr = self._enqueue_translation(task)
        if (want_tr or self.config.resolve_dependencies) and task.mod_id > 0 and task.mod_name:
            self.page_lookup.emit(task.game_domain, task.mod_id, task.mod_name, want_tr)

    def _enqueue_dependencies(self, task: DownloadTask) -> None:
        try:
            deps = self.graphql.mod_requirements(task.game_domain, task.mod_id)
        except Exception:
            deps = []
        if not deps:
            return
        self.log.emit(tr("{label} requiere {n} mod(s); encolando dependencias…")
                      .format(label=task.label, n=len(deps)))
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

    def _enqueue_translation(self, task: DownloadTask) -> bool:
        """Encola la traducción del mod en el IDIOMA DE LA APP (config.language).

        Devuelve True si además hay que buscar un mod de traducción APARTE en la lista
        oficial de su página (lo hace el escáner web de la GUI, vía ``page_lookup``)."""
        lang = self.config.language or "es"
        lang_name = translations.NEXUS_LANGUAGE_NAME.get(lang)
        if not lang_name:
            return False
        code = lang.upper()

        # 1) ¿Hay un archivo en ese idioma DENTRO del mismo mod?
        same = translations.find_translation_file_in_mod(
            self.graphql, task.game_domain, task.mod_id, lang,
            exclude_file_id=task.file_id, log=self.log.emit,
        )
        if same:
            fid, fname = same
            self.log.emit(tr("🌐 Traducción ({lang}) en el mismo mod: '{file}'. Encolando…")
                          .format(lang=lang_name, file=fname))
            tr_task = DownloadTask(
                game_domain=task.game_domain,
                mod_id=task.mod_id,
                file_id=fid,
                mod_name=f"{task.mod_name or 'mod'} ({code})",
                file_name=fname,
                is_translation=True,
            )
            self.enqueue_task(tr_task)
            return False

        # El inglés es el idioma base de la mayoría de mods: buscar un "mod de traducción al
        # inglés" aparte da demasiados falsos positivos (casi todo está en inglés), se omite.
        if lang == "en":
            return False

        # 2) Traducción como MOD APARTE: NO se busca por nombre (impreciso, daba falsos
        #    positivos). Se lee la lista OFICIAL de traducciones de la página del mod
        #    (sección «Translations available on the Nexus», con el navegador embebido y la
        #    sesión del usuario); el llamante emite ``page_lookup`` si devolvemos True.
        return bool(task.mod_id > 0 and task.mod_name)

    def translate_installed_mods(self) -> None:
        """Busca y encola la traducción al IDIOMA DE LA APP de TODOS los mods instalados
        (los que tienen id de Nexus). Trabaja en segundo plano para no congelar la interfaz."""
        lang = self.config.language or "es"
        lang_name = translations.NEXUS_LANGUAGE_NAME.get(lang)
        if not lang_name or lang == "en":
            self.log.emit(tr("Traducir mis mods: no aplica al idioma actual."))
            return
        threading.Thread(target=self._translate_installed_worker,
                         args=(lang_name,), daemon=True).start()

    def _translate_installed_worker(self, lang_name: str) -> None:
        mods = [m for m in self.store.all()
                if getattr(m, "mod_id", 0) and m.mod_id > 0
                and not getattr(m, "is_translation", False)]
        self.log.emit(tr("🌐 Buscando traducción ({lang}) para {n} mod(s) instalado(s)…")
                      .format(lang=lang_name, n=len(mods)))
        queued = 0
        for m in mods:
            task = DownloadTask(
                game_domain=getattr(m, "game_domain", "") or self.config.game_domain,
                mod_id=m.mod_id,
                file_id=getattr(m, "file_id", 0) or 0,
                mod_name=m.name,
            )
            try:
                before = len(self.tasks)
                self._enqueue_translation(task)
                if len(self.tasks) > before:
                    queued += 1
            except Exception as e:  # noqa: BLE001
                self.log.emit(tr("Traducción de {name}: error {e}").format(name=m.name, e=e))
        self.log.emit(tr("🌐 Traducir mis mods: {n} traducción(es) encolada(s) de {total} mod(s). "
                         "Revísalas en la pestaña Descargas.")
                      .format(n=queued, total=len(mods)))

    @staticmethod
    def _is_imported(mod) -> bool:
        """¿Es un mod DETECTADO de la carpeta (estilo MO2), no bajado por BMI? Los bajados por
        BMI tienen id de Nexus (>0) y no llevan la marca; los importados llevan el flag, o (para
        stores antiguos) la categoría «Importados» o un id sintético negativo."""
        return bool(getattr(mod, "imported", False) or mod.category == "Importados"
                    or mod.mod_id < 0)

    def _prune_missing_imported(self) -> int:
        """Quita de la lista los mods cuya carpeta ya no existe: los IMPORTADOS y también los
        gestionados por BMI si borraste su carpeta a mano (sincronización total estilo MO2).
        Si la «Carpeta de mods» entera no está accesible (p. ej. un disco desconectado) no
        poda NADA, para no vaciar la lista por accidente. Si alguno estaba desplegado, retira
        sus archivos de Data para no dejar huérfanos."""
        from pathlib import Path
        from . import deploy
        try:
            mods_base = Path(self.config.mods_dir).resolve()
            if not mods_base.is_dir():
                return 0
        except OSError:
            return 0

        def _under_mods_dir(raw: str) -> bool:
            try:
                return Path(raw).resolve().is_relative_to(mods_base)
            except (OSError, ValueError):
                return False

        gone = [m for m in self.store.all()
                if m.install_dir and not Path(m.install_dir).exists()
                and (self._is_imported(m) or _under_mods_dir(m.install_dir))]
        for m in gone:
            if m.deployed_files and self.config.game_data_path:
                try:
                    deploy.undeploy(m.deployed_files, self.config.game_data_path)
                except Exception:  # noqa: BLE001
                    pass
            if m.plugins:
                try:
                    self.installer._disable_plugins(m.plugins, self.log.emit)
                except Exception:  # noqa: BLE001
                    pass
            # Sus plugins copiados en Data quedarían huérfanos y el escáner los volvería a
            # pintar como «mod externo» (fila fantasma). Retíralos de Data, salvo que otro
            # mod de la lista aporte un plugin con el mismo nombre.
            if m.plugins and self.config.game_data_path:
                others = {p.lower() for o in self.store.all() if o.mod_id != m.mod_id
                          for p in o.plugins}
                orphan = [p for p in m.plugins if p.lower() not in others]
                if orphan:
                    try:
                        deploy.undeploy(orphan, self.config.game_data_path)
                    except Exception:  # noqa: BLE001
                        pass
            self.log.emit(tr("🗑 Mod quitado de la lista (su carpeta ya no existe): {name}")
                          .format(name=m.name))
            self.store.mods.pop(m.mod_id, None)
        return len(gone)

    def prune_missing_imported(self) -> int:
        """Poda pública (guarda si quita algo). La usa la lista de mods al refrescar, para que
        un mod importado que sacaste de la carpeta desaparezca sin reiniciar."""
        n = self._prune_missing_imported()
        if n:
            self.store.save()
        return n

    def import_external_mods(self) -> tuple[int, int]:
        """Sincroniza la lista con la carpeta de mods (estilo MO2): AÑADE los mods nuevos y
        QUITA los importados cuya carpeta ya no existe. Los añadidos quedan ACTIVADOS y SIN
        desplegar (se virtualizan solos en Modo VFS). Devuelve (añadidos, quitados)."""
        from . import importer
        removed = self._prune_missing_imported()
        known_ids = set(self.store.mods.keys())
        known_dirs = [m.install_dir for m in self.store.all()]
        try:
            new = importer.scan_mods_folder(
                self.config.mods_dir, self.config.game_domain, known_ids, known_dirs)
        except Exception as e:  # noqa: BLE001
            self.log.emit(tr("No se pudieron detectar mods de la carpeta: {e}").format(e=e))
            new = []
        for m in new:
            self.store.mods[m.mod_id] = m
        if new or removed:
            try:
                self.store.save()
            except Exception as e:  # noqa: BLE001
                # Sin persistencia (JSON bloqueado por antivirus/nube): la lista en memoria
                # ya está actualizada; avisa en el registro en vez de fallar en silencio.
                self.log.emit(tr("No se pudo guardar la lista de mods: {e}").format(e=e))
        if new:
            names = ", ".join(m.name for m in new[:8]) + ("…" if len(new) > 8 else "")
            self.log.emit(tr("🔎 {n} mod(s) detectados en la carpeta e importados a la lista: {names}")
                          .format(n=len(new), names=names))
        if removed:
            self.log.emit(tr("🗑 {n} mod(s) importados quitados de la lista (ya no están en la carpeta).")
                          .format(n=removed))
        if new or removed:
            self.mods_imported.emit(len(new))
        return len(new), removed

    def enqueue_translation_mod(self, game_domain: str, mod_id: int, name: str = "") -> None:
        """Encola un mod de traducción CONCRETO (por su id) marcado como traducción. Lo usa
        el escáner que lee la lista oficial de traducciones de la página del mod."""
        try:
            fid = self._primary_file_id(game_domain, mod_id) or 0
        except Exception:  # noqa: BLE001
            fid = 0
        task = DownloadTask(game_domain=game_domain, mod_id=mod_id, file_id=fid,
                            mod_name=name, is_translation=True)
        self.enqueue_task(task, explicit=True)

    def enqueue_requirement_mod(self, game_domain: str, mod_id: int, name: str = "") -> None:
        """Encola un requisito CONCRETO (por su id) leído de la sección «Nexus requirements»
        de la página de un mod. Lo usa el mismo escáner web que lee las traducciones.
        Resuelve su archivo principal en segundo plano para no bloquear la interfaz."""
        if mod_id <= 0 or self.store.is_installed(mod_id) or mod_id in self._inflight_mods:
            return

        def _resolve() -> None:
            try:
                fid = self._primary_file_id(game_domain, mod_id) or 0
            except Exception:  # noqa: BLE001
                fid = 0
            self.enqueue_task(DownloadTask(game_domain=game_domain, mod_id=mod_id,
                                           file_id=fid, mod_name=name, is_dependency=True))

        threading.Thread(target=_resolve, daemon=True).start()

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
            self.log.emit(tr("   requisito que falta: {name}")
                          .format(name=r.name or tr("mod {id}").format(id=r.mod_id)))
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
