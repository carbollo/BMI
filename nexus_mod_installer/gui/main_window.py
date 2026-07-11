"""Ventana principal de la aplicación."""
from __future__ import annotations

import os
import threading
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSettings, Signal, QPoint, QEvent
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QProgressBar, QPlainTextEdit,
    QLabel, QMessageBox, QHeaderView, QAbstractItemView, QFileDialog, QComboBox,
    QDialog, QMenu, QApplication, QProgressDialog, QFrame,
)
from PySide6.QtGui import QColor

from ..config import AppConfig
from ..manager import DownloadManager
from ..models import DownloadTask, TaskStatus
from .. import launcher, games, updater
from ..i18n import tr
from .webview import NexusWebView
from .settings_dialog import SettingsDialog
from .fomod_dialog import FomodDialog
from .mods_panel import ModsPanel
from .home_panel import HomePanel
from .titlebar import TitleBar, CaptionButton
from . import theme
from . import icons
from . import effects
from . import toast


# ===========================================================================
class _DropZone(QFrame):
    """Rectángulo al pie de Descargas: suelta un .zip/.7z/.rar o la carpeta de un mod
    para instalarlo sin pasar por Nexus."""

    _EXTS = {".zip", ".7z", ".rar"}

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel
        self.setAcceptDrops(True)
        self.setMinimumHeight(64)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self._lbl = QLabel(tr("⤓  Suelta aquí un archivo comprimido (.zip, .7z, .rar) o la "
                              "carpeta de un mod para instalarlo"))
        self._lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl.setWordWrap(True)
        self._lbl.setProperty("role", "dim")
        lay.addWidget(self._lbl)
        self._set_active(False)

    def _set_active(self, on: bool) -> None:
        color = theme.ACCENT if on else theme.BORDER
        self.setStyleSheet(f"QFrame {{ border: 2px dashed {color}; border-radius: 10px; "
                           f"background: transparent; }} QLabel {{ border: none; }}")

    def _paths(self, e) -> list:
        md = e.mimeData()
        if not md.hasUrls():
            return []
        out = []
        for u in md.urls():
            p = u.toLocalFile()
            if not p:
                continue
            pp = Path(p)
            if pp.is_dir() or pp.suffix.lower() in self._EXTS:
                out.append(pp)
        return out

    def dragEnterEvent(self, e) -> None:
        if self._paths(e):
            self._set_active(True)
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e) -> None:
        self._set_active(False)

    def dropEvent(self, e) -> None:
        self._set_active(False)
        paths = self._paths(e)
        if not paths:
            e.ignore()
            return
        e.acceptProposedAction()
        self._panel.install_dropped(paths)


class DownloadsPanel(QWidget):
    def __init__(self, manager: DownloadManager, window, parent=None):
        super().__init__(parent)
        self.manager = manager
        self._window = window
        self._row_of: dict[int, int] = {}   # id(task) -> fila
        self._dropped: set[int] = set()     # id(task) de lo soltado en la zona (para avisar al acabar)

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.count_lbl = QLabel("")
        self.count_lbl.setProperty("role", "dim")
        bar.addWidget(self.count_lbl)
        bar.addStretch()
        retry_sel = QPushButton(tr("Reintentar selección"))
        retry_sel.setIcon(icons.icon("refresh", theme.TEXT))
        retry_sel.setToolTip(tr("Reintenta las tareas seleccionadas que estén en error o pendientes de clic"))
        retry_sel.clicked.connect(self._retry_selected)
        remove_sel = QPushButton(tr("Quitar selección"))
        remove_sel.setIcon(icons.icon("x", theme.TEXT))
        remove_sel.setToolTip(tr("Quita de la cola las tareas seleccionadas (no las que se descargan ahora)"))
        remove_sel.clicked.connect(self._remove_selected)
        clear = QPushButton(tr("Limpiar completadas"))
        clear.setIcon(icons.icon("trash", theme.TEXT))
        clear.clicked.connect(self._clear_completed)
        for b in (retry_sel, remove_sel, clear):
            bar.addWidget(b)
        v.addLayout(bar)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [tr("Mod"), tr("Archivo"), tr("Estado"), tr("Progreso"), tr("Acción")])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        hdr = self.table.horizontalHeader()
        # Columnas redimensionables por el usuario (arrastra el borde de cualquiera para
        # verla entera). 'Archivo' se estira para los nombres largos; los tooltips muestran
        # el contenido completo de cualquier celda al pasar el ratón. 'Acción' lleva ancho
        # fijo que cabe el botón (ResizeToContents no mide los widgets y lo recortaba).
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)  # Mod
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)      # Archivo
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)  # Estado
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)        # Progreso
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)        # Acción
        hdr.setStretchLastSection(False)
        hdr.setMinimumSectionSize(60)
        self.table.setColumnWidth(0, 240)
        self.table.setColumnWidth(2, 180)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 160)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        v.addWidget(self.table)

        # Zona para soltar archivos comprimidos o carpetas de mod (instalación local).
        self.drop_zone = _DropZone(self)
        v.addWidget(self.drop_zone)

        manager.task_added.connect(self.on_task_added)
        manager.task_updated.connect(self.on_task_updated)
        manager.tasks_changed.connect(self.rebuild)

    def install_dropped(self, paths: list) -> None:
        """Instala lo soltado en la zona: los archivos comprimidos entran por la vía local
        normal (aparecen en esta cola y avisan al terminar) y las carpetas se copian a la
        «Carpeta de mods», se importan a la lista y se confirma con un aviso."""
        import shutil
        copied: list[str] = []
        for p in paths:
            if p.is_dir():
                dest = Path(self.manager.config.mods_dir) / p.name
                if dest.exists():
                    self.manager.log.emit(tr("Ya existe «{name}» en la carpeta de mods; no se copia.")
                                          .format(name=p.name))
                    continue
                try:
                    shutil.copytree(p, dest)
                except Exception as exc:  # noqa: BLE001
                    self.manager.log.emit(tr("No se pudo copiar la carpeta «{name}»: {e}")
                                          .format(name=p.name, e=exc))
                    continue
                self.manager.log.emit(tr("📁 Carpeta «{name}» copiada a la carpeta de mods; importando…")
                                      .format(name=p.name))
                copied.append(p.name)
            else:
                self.manager.enqueue_local(str(p))
                # Recordar la tarea para avisar con un toast cuando quede instalada.
                t = next((t for t in reversed(self.manager.tasks)
                          if t.archive_path == str(p)), None)
                if t is not None:
                    self._dropped.add(id(t))
        if not copied:
            return
        try:
            added, _removed = self.manager.import_external_mods()
        except Exception:  # noqa: BLE001
            added = 0
        try:
            self._window.mods_panel.refresh()
            self._window.home_panel.refresh(self._window.mods_panel._scan_cache)
        except Exception:  # noqa: BLE001
            pass
        names = ", ".join(copied[:5]) + ("…" if len(copied) > 5 else "")
        if added:
            self.manager.log.emit(tr("✔ «{name}»: importado a la lista de Mods.").format(name=names))
            self._toast(tr("✔ Mod instalado: {name}").format(name=names), "success")
        else:
            self.manager.log.emit(tr("«{name}» se copió, pero no se reconoció como mod "
                                     "(no trae contenido de Data).").format(name=names))
            self._toast(tr("«{name}» se copió, pero no se reconoció como mod "
                           "(no trae contenido de Data).").format(name=names), "error")

    def _toast(self, text: str, kind: str) -> None:
        try:
            self._window.toasts.show(text, kind)
        except Exception:  # noqa: BLE001
            pass

    def _label(self, task: DownloadTask) -> str:
        prefix = "🌐 " if task.is_translation else ("🔗 " if task.is_dependency else "")
        return prefix + (task.mod_name or f"mod {task.mod_id}")

    def on_task_added(self, task: DownloadTask) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._row_of[id(task)] = row
        it0 = QTableWidgetItem(self._label(task)); it0.setToolTip(self._label(task))
        self.table.setItem(row, 0, it0)
        it1 = QTableWidgetItem(task.file_name); it1.setToolTip(task.file_name)
        self.table.setItem(row, 1, it1)
        self.table.setItem(row, 2, QTableWidgetItem(task.status.value))
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(task.progress))
        self.table.setCellWidget(row, 3, bar)
        self._update_row(row, task)
        self._update_count()

    def on_task_updated(self, task: DownloadTask) -> None:
        row = self._row_of.get(id(task))
        if row is None:
            return
        lbl = self._label(task)
        self.table.item(row, 0).setText(lbl)
        self.table.item(row, 0).setToolTip(lbl)
        self.table.item(row, 1).setText(task.file_name)
        self.table.item(row, 1).setToolTip(task.file_name)
        self._update_row(row, task)
        # Confirmación visible para lo soltado en la zona de instalación local.
        if id(task) in self._dropped and task.status in (TaskStatus.DONE, TaskStatus.ERROR):
            self._dropped.discard(id(task))
            name = task.mod_name or task.file_name
            if task.status == TaskStatus.DONE:
                self._toast(tr("✔ Mod instalado: {name}").format(name=name), "success")
            else:
                self._toast(tr("No se pudo instalar «{name}».").format(name=name), "error")

    def _update_row(self, row: int, task: DownloadTask) -> None:
        status_text = task.status.value
        if task.status == TaskStatus.DOWNLOADING and task.total_bytes:
            mb, tot = task.downloaded_bytes / 1048576, task.total_bytes / 1048576
            spd = task.speed_bps / 1048576
            status_text = f"⬇ {mb:.1f}/{tot:.1f} MB · {spd:.1f} MB/s"
        elif task.status == TaskStatus.ERROR and task.error:
            status_text = f"✗ {task.error[:50]}"
        item = self.table.item(row, 2)
        item.setText(status_text)
        item.setToolTip(task.error if (task.status == TaskStatus.ERROR and task.error)
                        else status_text)
        item.setForeground(QColor(theme.STATUS_COLORS.get(task.status.value, theme.TEXT)))

        bar = self.table.cellWidget(row, 3)
        if isinstance(bar, QProgressBar):
            bar.setValue(int(task.progress))

        # Acción contextual
        if task.status == TaskStatus.NEEDS_CLICK:
            self._set_action(row, tr("Descargar"), lambda: self._open_page(task))
        elif task.status == TaskStatus.ERROR:
            self._set_action(row, tr("↻ Reintentar"), lambda: self._retry(task))
        else:
            self.table.removeCellWidget(row, 4)

    def _set_action(self, row: int, text: str, fn) -> None:
        btn = QPushButton(text)
        btn.clicked.connect(lambda _=False: fn())
        self.table.setCellWidget(row, 4, btn)

    def _retry(self, task: DownloadTask) -> None:
        if isinstance(self._window, MainWindow):
            self._window.retry_task(task)

    def _open_page(self, task: DownloadTask) -> None:
        """'Abrir y descargar': abre la página del mod para pulsar 'Mod Manager Download'."""
        if isinstance(self._window, MainWindow):
            self._window.open_task_page(task)

    def _selected_tasks(self) -> list[DownloadTask]:
        """Tareas correspondientes a las filas seleccionadas."""
        rows = {idx.row() for idx in self.table.selectionModel().selectedRows()}
        inv = {row: tid for tid, row in self._row_of.items()}
        by_id = {id(t): t for t in self.manager.tasks}
        out = []
        for r in rows:
            t = by_id.get(inv.get(r, -1))
            if t is not None:
                out.append(t)
        return out

    def _retry_selected(self) -> None:
        for t in self._selected_tasks():
            if t.status in (TaskStatus.NEEDS_CLICK, TaskStatus.ERROR):
                self._retry(t)

    def _remove_selected(self) -> None:
        tasks = self._selected_tasks()
        if not tasks:
            return
        n = self.manager.remove_tasks(tasks)   # emite tasks_changed -> rebuild
        if not n and isinstance(self._window, MainWindow):
            self._window._status(tr("No se pudieron quitar (¿se están descargando ahora?)."))

    def _update_count(self) -> None:
        n = len(self.manager.tasks)
        done = sum(1 for t in self.manager.tasks if t.status == TaskStatus.DONE)
        self.count_lbl.setText(tr("{n} tarea(s) · {done} completada(s)").format(n=n, done=done))

    def _clear_completed(self) -> None:
        self.manager.clear_completed()
        self.rebuild()

    def rebuild(self) -> None:
        self.table.setRowCount(0)
        self._row_of.clear()
        for task in list(self.manager.tasks):
            self.on_task_added(task)
        self._update_count()


# ===========================================================================
class MainWindow(QMainWindow):
    # Autoactualización (se emiten desde hilos de fondo; Qt los entrega en el hilo GUI).
    _update_found = Signal(object)      # {version, tag, url, notes, asset_url}
    _dl_progress = Signal(int, int)     # (descargado, total)
    _dl_done = Signal(str)              # ruta del nuevo .exe ("" = falló)

    def __init__(self, config: AppConfig, manager: DownloadManager):
        super().__init__()
        self.config = config
        self.manager = manager
        self.setWindowIcon(theme.make_app_icon())
        self._views_refresh_scheduled = False
        self._last_mod_page = None   # (dominio, mod_id) de la última página de mod vista
        self._restore_geometry()

        # --- Barra de título propia (integrada en el tema) ---
        # La barra nativa gris se oculta vía WM_NCCALCSIZE (ver nativeEvent), conservando
        # marco de sistema: sombra, Snap, Win+flechas y redimensionado siguen siendo nativos.
        from PySide6.QtGui import QGuiApplication
        self._custom_frame = QGuiApplication.platformName() == "windows"
        self.titlebar = TitleBar(self)
        self.setMenuWidget(self.titlebar)
        self._frame_forced = False

        # --- Selector de juego ---
        self.game_combo = QComboBox()
        for g in games.all_games():
            self.game_combo.addItem(g.name, g.key)
        gi = self.game_combo.findData(config.game_domain)
        self.game_combo.setCurrentIndex(gi if gi >= 0 else 0)
        self.game_combo.setToolTip(tr("Juego activo"))
        self.game_combo.currentIndexChanged.connect(self._on_game_changed)

        # --- Barra superior ---
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(tr("Pega un enlace nxm://, URL de un mod o de una colección…"))
        self.url_edit.setMinimumWidth(220)
        self.url_edit.returnPressed.connect(self._add_from_url)
        add_btn = QPushButton(tr("Añadir / Descargar"))
        add_btn.setIcon(icons.icon("download", "#1a1207"))
        add_btn.setProperty("variant", "primary")
        add_btn.clicked.connect(self._add_from_url)
        batch_btn = QPushButton(tr("Varios…"))
        batch_btn.setIcon(icons.icon("plus", theme.TEXT))
        batch_btn.setToolTip(tr("Pegar varias URLs/ids a la vez y descargarlas en cola"))
        batch_btn.clicked.connect(self._open_batch_dialog)
        load_btn = QPushButton(tr("Cargar mod"))
        load_btn.setIcon(icons.icon("folder", theme.TEXT))
        load_btn.setToolTip(tr("Instalar un mod desde un archivo .zip/.7z/.rar de tu PC"))
        load_btn.clicked.connect(self._load_mod_from_file)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("Buscar mods en Nexus…"))
        self.search_edit.setMinimumWidth(170)
        self.search_edit.returnPressed.connect(self._do_search)
        search_btn = QPushButton(tr("Buscar"))
        search_btn.setIcon(icons.icon("search", theme.TEXT))
        search_btn.clicked.connect(self._do_search)

        self.play_btn = QPushButton()
        self.play_btn.setProperty("variant", "success")
        self.play_btn.clicked.connect(self._launch_game)
        self._refresh_play_btn()
        self.tools_btn = QPushButton(tr("Herramientas"))
        self.tools_btn.setIcon(icons.icon("wrench", theme.TEXT))
        self.tools_btn.setToolTip(tr("Lanzar Nemesis, xEdit, DynDOLOD, Synthesis…"))
        self._tools_menu = QMenu(self.tools_btn)
        self._tools_menu.aboutToShow.connect(self._build_tools_menu)
        self.tools_btn.setMenu(self._tools_menu)
        settings_btn = QPushButton(tr("Ajustes"))
        settings_btn.setIcon(icons.icon("settings", theme.TEXT))
        settings_btn.clicked.connect(self._open_settings)

        top = QHBoxLayout()
        top.setContentsMargins(10, 8, 10, 8)
        top.setSpacing(8)
        top.addWidget(self.game_combo)
        top.addWidget(self.url_edit, 3)
        top.addWidget(add_btn)
        top.addWidget(batch_btn)
        top.addWidget(load_btn)
        top.addSpacing(12)
        top.addWidget(self.search_edit, 2)
        top.addWidget(search_btn)
        top.addSpacing(12)
        top.addWidget(self.play_btn)
        top.addWidget(self.tools_btn)
        top.addWidget(settings_btn)
        top_w = QWidget(); top_w.setLayout(top)
        top_w.setProperty("role", "toolbar")
        effects.add_shadow(top_w, blur=20, dy=3, alpha=110)

        # --- Navegador ---
        self.webview = NexusWebView(config.downloads_dir, config.game().domain)
        self.webview.nxm_requested.connect(self._on_nxm)
        self.webview.manual_file_downloaded.connect(self._on_manual_file)
        self.webview.status_message.connect(self._status)
        self.explore_tab = self._build_explore_tab()

        # --- Paneles ---
        self.home_panel = HomePanel(manager)
        self.home_panel.open_settings.connect(self._open_settings)
        self.home_panel.launch_game.connect(self._launch_game)
        self.home_panel.go_explore.connect(lambda: self.tabs.setCurrentWidget(self.explore_tab))
        self.downloads_panel = DownloadsPanel(manager, self)
        self.mods_panel = ModsPanel(manager)
        self.mods_panel.translate_all_requested.connect(self._translate_all_web)
        # Al añadir/instalar un mod, leer su página oficial: traducciones al idioma de la
        # app y requisitos de la sección Requirements (mismo escáner web para todo).
        self.manager.page_lookup.connect(self._on_page_lookup)
        self.log_tab = self._build_log_tab()

        self.tabs = QTabWidget()
        self.tabs.addTab(self.home_panel, icons.icon("home", theme.TEXT, 16), tr("Inicio"))
        self.tabs.addTab(self.explore_tab, icons.icon("search", theme.TEXT, 16), tr("Explorar Nexus"))
        self.tabs.addTab(self.downloads_panel, icons.icon("download", theme.TEXT, 16), tr("Descargas"))
        self.tabs.addTab(self.mods_panel, icons.icon("package", theme.TEXT, 16), tr("Mods"))
        self.tabs.addTab(self.log_tab, icons.icon("log", theme.TEXT, 16), tr("Registro"))
        self.tabs.currentChanged.connect(self._on_tab_fade)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(10, 10, 10, 8)
        v.setSpacing(10)
        v.addWidget(top_w)
        v.addWidget(self.tabs)
        self.setCentralWidget(central)

        self.status_label = QLabel(tr("Listo."))
        self.statusBar().addWidget(self.status_label)

        self.toasts = toast.ToastManager(self)
        self._toasted: set[int] = set()
        manager.log.connect(self._on_log)
        manager.needs_click.connect(self._on_needs_click)
        manager.task_updated.connect(self._maybe_refresh_views)
        manager.fomod_requested.connect(self._on_fomod_requested)
        manager.task_added.connect(lambda _t: self._update_activity_badge())
        manager.tasks_changed.connect(self._update_activity_badge)

        self._refresh_title()
        # Al arrancar, detecta en segundo plano los mods ya presentes en la carpeta de mods
        # (estilo MO2). Diferido para no ralentizar el arranque de la ventana.
        QTimer.singleShot(1500, self._auto_import_mods)
        # Autoactualización: comprobar GitHub al arrancar (silencioso si no hay red/novedad).
        self._update_found.connect(self._on_update_found)
        self._dl_progress.connect(self._on_dl_progress)
        self._dl_done.connect(self._on_dl_done)
        self._update_info = None
        self._update_cancel = False
        if getattr(self.config, "check_updates", True):
            QTimer.singleShot(3500, lambda: self._check_updates_async(silent=True))

    # ------------------------------------------------------------------
    def _build_log_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addStretch()
        clear = QPushButton(tr("Limpiar"))
        clear.setIcon(icons.icon("trash", theme.TEXT))
        clear.clicked.connect(lambda: self.log_panel.clear())
        save = QPushButton(tr("Guardar"))
        save.setIcon(icons.icon("save", theme.TEXT))
        save.clicked.connect(self._save_log)
        bar.addWidget(clear); bar.addWidget(save)
        v.addLayout(bar)
        self.log_panel = QPlainTextEdit()
        self.log_panel.setReadOnly(True)
        self.log_panel.setMaximumBlockCount(5000)
        v.addWidget(self.log_panel)
        return w

    def _save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Guardar registro"), "registro.txt",
                                              tr("Texto (*.txt)"))
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.log_panel.toPlainText())
                self._status(tr("Registro guardado en {path}").format(path=path))
            except OSError as e:
                QMessageBox.warning(self, tr("Error"), str(e))

    # ------------------------------------------------------------------
    def _maybe_refresh_views(self, task: DownloadTask) -> None:
        self._update_activity_badge()
        if task.status == TaskStatus.DONE:
            if id(task) not in self._toasted:
                self._toasted.add(id(task))
                self.toasts.show(tr("Instalado: {name}").format(
                    name=task.mod_name or task.label), "success")
            if not self._views_refresh_scheduled:
                self._views_refresh_scheduled = True
                QTimer.singleShot(1000, self._do_views_refresh)   # coalesce ráfagas
        elif task.status == TaskStatus.ERROR and id(task) not in self._toasted:
            self._toasted.add(id(task))
            self.toasts.show(tr("Error: {name}").format(
                name=task.mod_name or task.label), "error", 6000)

    def _update_activity_badge(self) -> None:
        """Muestra el nº de descargas/instalaciones activas en la pestaña Descargas."""
        active = sum(1 for t in self.manager.tasks
                     if t.status in self.manager._ACTIVE_STATES)
        idx = self.tabs.indexOf(self.downloads_panel)
        if idx >= 0:
            self.tabs.setTabText(idx, tr("Descargas") + (f"  ({active})" if active else ""))

    def _do_views_refresh(self) -> None:
        self._views_refresh_scheduled = False
        self.mods_panel.refresh()
        # Reutiliza el escaneo recién hecho por el gestor (evita re-leer el disco).
        self.home_panel.refresh(self.mods_panel._scan_cache)

    def _on_tab_fade(self, index: int) -> None:
        # Fundido sutil al cambiar de pestaña. Se evita la del navegador (QWebEngineView no
        # admite efectos gráficos: se quedaría en blanco).
        w = self.tabs.widget(index)
        if w is not None and w is not self.explore_tab:
            effects.fade_in(w, 150)

    def _status(self, msg: str) -> None:
        self.status_label.setText(msg)

    def _on_log(self, msg: str) -> None:
        self.log_panel.appendPlainText(msg)
        self.status_label.setText(msg.splitlines()[0][:120])

    def _on_nxm(self, url: str) -> None:
        self.manager.enqueue_nxm(url)
        self.tabs.setCurrentWidget(self.downloads_panel)

    def _on_manual_file(self, path: str) -> None:
        # Descarga lenta/manual desde el navegador. La asociamos al mod cuya página está
        # abierta para registrar el mod REAL, sustituir su marcador 'Requiere clic en web',
        # instalarlo e (si procede) resolver sus dependencias y traducción.
        if not path.lower().endswith((".zip", ".7z", ".rar")):
            return
        parsed = self._parse_mod_page(self.webview.url().toString()) or self._last_mod_page
        if parsed:
            domain, mod_id = parsed
            self.manager.enqueue_local(path, mod_id=mod_id, game_domain=domain)
        else:
            self.manager.enqueue_local(path)
        self.tabs.setCurrentWidget(self.downloads_panel)

    def _load_mod_from_file(self) -> None:
        """Cargar e instalar uno o varios mods desde archivos comprimidos del PC.

        Reutiliza el mismo pipeline que las descargas (extraer → FOMOD → desplegar a Data /
        carpeta raíz → registrar para poder desactivar/desinstalar). No requiere conexión
        ni cuenta de Nexus: instala lo que el usuario elija de su disco."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("Cargar mod desde archivo"), "",
            tr("Archivos de mod (*.zip *.7z *.rar);;Todos los archivos (*)"),
        )
        if not paths:
            return
        for p in paths:
            self.manager.enqueue_local(p)
        self.tabs.setCurrentWidget(self.downloads_panel)
        self._status(tr("Cargando {n} archivo(s) para instalar…").format(n=len(paths)))

    def _on_needs_click(self, task: DownloadTask) -> None:
        # Cuenta gratuita: abrir la página SOLO del mod principal (para pulsar 'Mod Manager
        # Download' o 'slow'). Las dependencias y la traducción esperan a que el usuario pulse
        # su botón 'Descargar'. Con Premium no se llega aquí (la API descarga sola).
        if not task.is_dependency and not task.from_collection and not task.is_translation:
            self.webview.open_mod_page(task.game_domain, task.mod_id)
            self.tabs.setCurrentWidget(self.explore_tab)

    def retry_task(self, task: DownloadTask) -> None:
        """Reintenta una tarea en error: la vuelve a encolar."""
        self.manager.enqueue_mod(task.game_domain, task.mod_id)
        self.tabs.setCurrentWidget(self.downloads_panel)

    def open_task_page(self, task: DownloadTask) -> None:
        """Abre la página del mod en el navegador para descargarlo manualmente (cuenta
        gratuita): el usuario pulsa 'Mod Manager Download' y el nxm:// resultante sustituye
        al marcador 'Requiere clic en web' y descarga de verdad."""
        self.webview.open_mod_page(task.game_domain, task.mod_id)
        self.tabs.setCurrentWidget(self.explore_tab)

    def _on_fomod_requested(self, req) -> None:
        try:
            dlg = FomodDialog(req.config, self)
            req.result = dlg.get_selection() if dlg.exec() else None
        except Exception as e:
            self._on_log(f"Error en el asistente FOMOD: {e}")
            req.result = None
        finally:
            req.event.set()

    # ------------------------------------------------------------------
    def _add_from_url(self) -> None:
        """Caja de la barra: acepta UNA o VARIAS entradas (separadas por espacios)."""
        raw = self.url_edit.text().strip()
        if not raw:
            return
        self.url_edit.clear()
        tokens = raw.split()
        if len(tokens) > 1:
            self._queue_many(tokens)
            return
        cat = self._classify_and_queue(tokens[0])
        if cat in ("nxm", "collection", "mod"):
            self.tabs.setCurrentWidget(self.downloads_panel)
        elif cat == "othergame":
            self._status(tr("Ese mod es de otro juego; cambia el juego activo arriba."))
        else:
            self.webview.setUrl(tokens[0])  # type: ignore[arg-type]
            self.tabs.setCurrentWidget(self.explore_tab)

    def _open_batch_dialog(self) -> None:
        """Diálogo para pegar muchas entradas (una por línea) y encolarlas todas."""
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Añadir varios a la cola"))
        dlg.setMinimumSize(580, 380)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(
            tr("Pega <b>una entrada por línea</b>: URL de mod, URL de colección, enlace "
               "nxm:// o id numérico de mod.<br>Se descargarán <b>en orden, una tras otra</b>.")
        ))
        edit = QPlainTextEdit()
        edit.setPlaceholderText(
            "https://www.nexusmods.com/skyrimspecialedition/mods/12604\n"
            "https://www.nexusmods.com/skyrimspecialedition/mods/3863\n"
            "266"
        )
        lay.addWidget(edit, 1)
        btns = QHBoxLayout(); btns.addStretch()
        cancel = QPushButton(tr("Cancelar")); cancel.clicked.connect(dlg.reject)
        ok = QPushButton(tr("➕ Añadir a la cola")); ok.setProperty("variant", "primary")
        ok.clicked.connect(dlg.accept)
        btns.addWidget(cancel); btns.addWidget(ok)
        lay.addLayout(btns)
        if dlg.exec():
            tokens = [ln.strip() for ln in edit.toPlainText().splitlines() if ln.strip()]
            if tokens:
                self._queue_many(tokens)

    def _queue_many(self, tokens: list[str]) -> None:
        """Encola una lista de entradas y resume el resultado."""
        counts = {"nxm": 0, "collection": 0, "mod": 0, "othergame": 0, "unknown": 0}
        for tok in tokens:
            counts[self._classify_and_queue(tok)] += 1
        ok = counts["nxm"] + counts["collection"] + counts["mod"]
        if ok:
            self.tabs.setCurrentWidget(self.downloads_panel)
        parts = []
        if counts["mod"]:
            parts.append(tr("{n} mod(s)").format(n=counts["mod"]))
        if counts["collection"]:
            parts.append(tr("{n} colección(es)").format(n=counts["collection"]))
        if counts["nxm"]:
            parts.append(tr("{n} enlace(s) nxm").format(n=counts["nxm"]))
        msg = tr("✅ Encolado: ") + (", ".join(parts) if parts else tr("nada reconocido"))
        skipped = []
        if counts["othergame"]:
            skipped.append(tr("{n} de otro juego").format(n=counts["othergame"]))
        if counts["unknown"]:
            skipped.append(tr("{n} no reconocido(s)").format(n=counts["unknown"]))
        if skipped:
            msg += tr("  ·  omitidos: ") + ", ".join(skipped)
        self._on_log(msg)

    def _classify_and_queue(self, text: str) -> str:
        """Encola UNA entrada. Devuelve su categoría:
        'nxm' | 'collection' | 'mod' | 'othergame' | 'unknown'."""
        text = text.strip().strip('"').strip("'")
        if not text:
            return "unknown"
        low = text.lower()
        if low.startswith("nxm://"):
            self.manager.enqueue_nxm(text)
            return "nxm"
        if "/collections/" in low:
            self.manager.enqueue_collection(text)
            return "collection"
        parsed = self._parse_mod_page(text)
        if parsed:
            domain, mod_id = parsed
            if domain != self.config.game().domain:
                return "othergame"
            self._queue_mod(domain, mod_id)
            return "mod"
        if text.isdigit():
            self._queue_mod(self.config.game().domain, int(text))
            return "mod"
        return "unknown"

    def _queue_mod(self, game_domain: str, mod_id: int) -> None:
        """Encola un mod por id SIN cambiar de pestaña (para lotes).

        Premium: descarga automática vía API. Gratis: la tarea pasa a 'Requiere clic
        en web' y se abre la página del mod para pulsar 'Mod Manager Download'.
        """
        self.manager.enqueue_mod(game_domain, mod_id)

    def _start_mod_download(self, game_domain: str, mod_id: int) -> None:
        """Encola un mod y muestra la pestaña de Descargas (para añadidos sueltos)."""
        self._queue_mod(game_domain, mod_id)
        self.tabs.setCurrentWidget(self.downloads_panel)

    def _enqueue_mod_with_web_deps(self, game_domain: str, mod_id: int, req_json: str) -> None:
        """Encola el mod usando las dependencias leídas de la página (Requirements) — la
        fuente autoritativa — además de las del GraphQL (complemento)."""
        from ..nexus_graphql import parse_download_links
        web_deps = parse_download_links(req_json or "")
        self.manager.enqueue_mod(game_domain, mod_id, extra_deps=web_deps)

    @staticmethod
    def _extract_mod_id(url: str) -> int | None:
        import re
        m = re.search(r"/mods/(\d+)", url)
        return int(m.group(1)) if m else None

    # ------------------------------------------------------------------
    def _build_explore_tab(self) -> QWidget:
        """Navegador embebido + barra con navegación y 'Descargar este mod'."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        bar = QHBoxLayout()
        for sym, tip, slot in (
            ("◀", tr("Atrás"), self.webview.back),
            ("▶", tr("Adelante"), self.webview.forward),
            ("⟳", tr("Recargar"), self.webview.reload),
        ):
            b = QPushButton(sym); b.setToolTip(tip); b.setFixedWidth(38)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch()
        self.login_btn = QPushButton(tr("🔑 Iniciar sesión con Nexus"))
        self.login_btn.setToolTip(tr("Inicia sesión con tu cuenta de Nexus (OAuth oficial). "
                                     "Con la sesión iniciada no necesitas API key."))
        self.login_btn.clicked.connect(self._start_login)
        bar.addWidget(self.login_btn)
        self.dl_current_btn = QPushButton(tr("Descargar este mod"))
        self.dl_current_btn.setIcon(icons.icon("download", "#1a1207"))
        self.dl_current_btn.setProperty("variant", "primary")
        self.dl_current_btn.setToolTip(tr("Descarga el mod de la página que estás viendo"))
        self.dl_current_btn.setEnabled(False)
        self.dl_current_btn.clicked.connect(self._download_current_page)
        bar.addWidget(self.dl_current_btn)

        lay.addLayout(bar)
        lay.addWidget(self.webview, 1)
        self.webview.urlChanged.connect(self._on_webview_url_changed)
        self.webview.oauth_redirect.connect(self._on_oauth_redirect)
        self._refresh_login_btn()
        return w

    def _refresh_login_btn(self) -> None:
        if getattr(self.manager, "is_logged_in", False):
            self.login_btn.setText(tr("✓ Sesión de Nexus iniciada"))
            self.login_btn.setToolTip(tr("Sesión iniciada. Pulsa para cerrar sesión."))
        else:
            self.login_btn.setText(tr("🔑 Iniciar sesión con Nexus"))
            self.login_btn.setToolTip(tr("Inicia sesión con tu cuenta de Nexus (OAuth oficial)."))

    def _start_login(self) -> None:
        if self.manager.is_logged_in:
            if QMessageBox.question(self, tr("Nexus"),
                                    tr("¿Cerrar la sesión de Nexus?")) == QMessageBox.StandardButton.Yes:
                self.manager.logout()
                self._refresh_login_btn()
                self._status(tr("Sesión de Nexus cerrada."))
            return
        try:
            flow, url = self.manager.start_login()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Iniciar sesión"), str(e))
            return
        self._login_flow = flow
        from PySide6.QtCore import QUrl
        self.webview.setUrl(QUrl(url))
        self._status(tr("Inicia sesión en Nexus en el navegador de arriba…"))

    def _on_oauth_redirect(self, url: str) -> None:
        flow = getattr(self, "_login_flow", None)
        if not flow:
            return
        self._login_flow = None
        # IMPORTANTE: NO tocar el webview ni abrir diálogos DENTRO del callback de navegación
        # de QtWebEngine (acceptNavigationRequest) — provoca re-entrancia y el cierre del
        # programa. Se difiere al siguiente ciclo del bucle de eventos, ya fuera del callback.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._finish_login(flow, url))

    def _finish_login(self, flow, url: str) -> None:
        try:
            info = self.manager.complete_login(flow, url)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Iniciar sesión"),
                                tr("No se pudo completar el inicio de sesión:\n{e}").format(e=e))
            return
        self._refresh_login_btn()
        from PySide6.QtCore import QUrl
        self.webview.setUrl(QUrl(f"https://www.nexusmods.com/{self.config.game_domain}"))
        name = info.get("name") or info.get("preferred_username") or ""
        QMessageBox.information(
            self, tr("Sesión iniciada"),
            tr("¡Sesión iniciada en Nexus{who}! Ya puedes descargar sin API key.")
            .format(who=(" como " + name) if name else ""))
        self._status(tr("Sesión de Nexus iniciada."))

    # --- Traducir mis mods leyendo la lista OFICIAL de cada página (vía navegador) ---
    _TR_LANG_WORDS = {
        "es": ["spanish", "español", "espanol", "castellano"],
        "fr": ["french", "français", "francais"],
        "de": ["german", "deutsch"],
        "it": ["italian", "italiano"],
    }

    def _get_tr_scanner(self):
        """Escáner de páginas de mod (perezoso, único): lee de la página OFICIAL de cada mod
        su lista de traducciones y sus requisitos («Nexus requirements»). Lo comparten el
        botón masivo de traducir y las descargas de mods sueltos."""
        sc = getattr(self, "_tr_scanner", None)
        if sc is None:
            from .translation_scan import TranslationScanner
            words = self._TR_LANG_WORDS.get(self.config.language or "es", [])
            sc = TranslationScanner(self.webview.profile(), words, self)
            sc.translation_found.connect(self._on_translation_found)
            sc.requirement_found.connect(self._on_requirement_found)
            sc.scanning.connect(
                lambda dom, mid: self._status(tr("Leyendo la página del mod {mid}…")
                                              .format(mid=mid)))
            self._tr_scanner = sc
        return sc

    def _wrong_domain(self, dom: str) -> bool:
        """True si ``dom`` pertenece a OTRO juego que el activo. Los mods de un juego se
        instalan en la Data de ESE juego; encolar un requisito/traducción de otro dominio lo
        metería en la carpeta equivocada. SSE y AE comparten dominio, así que compara contra
        game().domain (no la clave interna)."""
        active = self.config.game().domain
        return bool(dom) and dom != active

    def _on_translation_found(self, dom: str, tmid: int, name: str) -> None:
        if self._wrong_domain(dom):
            return
        if not self.manager.store.is_installed(tmid):
            self.manager.enqueue_translation_mod(dom or self.config.game().domain, tmid, name)

    def _on_requirement_found(self, dom: str, rmid: int, name: str) -> None:
        """Requisito leído de la sección «Nexus requirements» de la página de un mod."""
        if self._wrong_domain(dom):
            self._status(tr("Requisito de otro juego ({dom}) omitido.").format(dom=dom))
            return
        self.manager.enqueue_requirement_mod(dom or self.config.game().domain, rmid, name)

    def _translate_all_web(self) -> None:
        if (self.config.language or "es") not in self._TR_LANG_WORDS:
            return
        mods = [(getattr(m, "game_domain", "") or self.config.game_domain, m.mod_id, m.name)
                for m in self.manager.store.all() if getattr(m, "mod_id", 0) and m.mod_id > 0]
        if not mods:
            return
        if not self.manager.is_logged_in and QMessageBox.question(
                self, tr("Traducir mis mods"),
                tr("No has iniciado sesión en Nexus; algunas páginas podrían no cargar. "
                   "¿Continuar igualmente?")) != QMessageBox.StandardButton.Yes:
            return
        self._get_tr_scanner().add(mods)
        self._status(tr("Leyendo la lista oficial de traducciones de tus mods…"))

    def _on_page_lookup(self, dom: str, mid: int, name: str, want_tr: bool) -> None:
        """El gestor pide leer la página de un mod: traducciones oficiales (si want_tr) y
        requisitos de «Nexus requirements» (si el resolutor de dependencias está activo)."""
        want_tr = bool(want_tr and (self.config.language or "es") in self._TR_LANG_WORDS)
        want_req = bool(getattr(self.config, "resolve_dependencies", True))
        if not (want_tr or want_req):
            return
        self._get_tr_scanner().add([(dom or self.config.game_domain, mid, name)],
                                   want_translations=want_tr, want_requirements=want_req)

    def _auto_import_mods(self) -> None:
        """Sincroniza en silencio la lista con la «Carpeta de mods» (estilo MO2): añade los
        nuevos y quita los importados que ya no están. Se llama al arrancar, al cambiar de
        juego y al cambiar la carpeta en Ajustes. Solo refresca si algo cambió."""
        try:
            added, removed = self.manager.import_external_mods()
        except Exception:  # noqa: BLE001
            return
        if added or removed:
            self.mods_panel.refresh()
            self.home_panel.refresh(self.mods_panel._scan_cache)
            if added and removed:
                self._status(tr("{a} mod(s) detectados y {r} quitados de la lista.")
                             .format(a=added, r=removed))
            elif added:
                self._status(tr("{n} mod(s) detectados en la carpeta e importados a la lista.")
                             .format(n=added))
            else:
                self._status(tr("{n} mod(s) importados quitados (ya no están en la carpeta).")
                             .format(n=removed))

    def _detect_mods(self) -> None:
        """«Detectar mods de la carpeta» (botón de Ajustes): sincroniza y avisa del resultado."""
        added, removed = self.manager.import_external_mods()
        self.mods_panel.refresh()
        self.home_panel.refresh(self.mods_panel._scan_cache)
        if added or removed:
            QMessageBox.information(
                self, tr("Detectar mods de la carpeta"),
                tr("Se detectaron {a} mod(s) nuevos y se quitaron {r} que ya no estaban.")
                .format(a=added, r=removed))
        else:
            QMessageBox.information(
                self, tr("Detectar mods de la carpeta"),
                tr("No se encontraron mods nuevos en la «Carpeta de mods» ({dir}).\n\nColoca "
                   "cada mod en su propia subcarpeta (estructura estilo MO2) y vuelve a probar.")
                .format(dir=self.config.mods_dir))

    # ---- Autoactualización desde GitHub -------------------------------
    def _check_updates_async(self, silent: bool = True) -> None:
        """Consulta GitHub en un hilo. ``silent``: solo avisa si hay novedad no descartada;
        si es manual (silent=False), avisa también cuando ya estás al día."""
        def work():
            info = updater.check_latest()
            if info:
                if silent and info.get("tag") == getattr(self.config, "skip_update_version", ""):
                    return
                self._update_found.emit(info)
            elif not silent:
                self._update_found.emit({"none": True})
        threading.Thread(target=work, daemon=True).start()

    def _on_update_found(self, info: object) -> None:
        if isinstance(info, dict) and info.get("none"):
            cur = ".".join(str(n) for n in updater.current_version())
            QMessageBox.information(self, tr("Buscar actualizaciones"),
                                    tr("Ya tienes la última versión de BMI ({v}).").format(v=cur))
            return
        if not isinstance(info, dict):
            return
        box = QMessageBox(self)
        box.setWindowTitle(tr("Actualización disponible"))
        box.setText(tr("Hay una nueva versión de BMI: {v}.\n\n¿Descargarla e instalarla ahora? "
                       "BMI se cerrará un momento y volverá a abrirse ya actualizado.")
                    .format(v=info.get("version", "?")))
        dl = box.addButton(tr("Descargar e instalar"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("Ahora no"), QMessageBox.ButtonRole.RejectRole)
        skip = box.addButton(tr("No avisar de esta versión"), QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        if box.clickedButton() is dl:
            self._download_update(info)
        elif box.clickedButton() is skip:
            self.config.skip_update_version = info.get("tag", "")
            self.config.save()

    def _download_update(self, info: dict) -> None:
        if not updater.is_frozen():
            webbrowser.open(info.get("url") or updater.RELEASES_PAGE)  # desde código: abrir GitHub
            return
        self._update_cancel = False
        self._progress = QProgressDialog(tr("Descargando la nueva versión…"),
                                         tr("Cancelar"), 0, 100, self)
        self._progress.setWindowTitle(tr("Actualizando BMI"))
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.canceled.connect(lambda: setattr(self, "_update_cancel", True))
        self._progress.setValue(0)
        self._progress.show()
        new_exe = updater.current_exe() + ".new"
        asset = info.get("asset_url", "")

        def work():
            try:
                updater.download_asset(asset, new_exe,
                                       lambda d, t: self._dl_progress.emit(d, t),
                                       lambda: self._update_cancel)
                self._dl_done.emit(new_exe)
            except Exception:  # noqa: BLE001
                self._dl_done.emit("")
        threading.Thread(target=work, daemon=True).start()

    def _on_dl_progress(self, done: int, total: int) -> None:
        prog = getattr(self, "_progress", None)
        if prog and total:
            prog.setValue(int(done * 100 / total))

    def _on_dl_done(self, path: str) -> None:
        # OJO: cerrar un QProgressDialog dispara su señal 'canceled'. Capturamos el estado de
        # cancelación ANTES de cerrarlo y desconectamos la señal, para que cerrar el diálogo NO
        # marque una cancelación falsa (era la causa de que no se aplicara la actualización).
        cancelled = self._update_cancel
        prog = getattr(self, "_progress", None)
        self._progress = None
        if prog:
            try:
                prog.canceled.disconnect()
            except Exception:  # noqa: BLE001
                pass
            prog.close()
        if cancelled:
            try:
                if path and os.path.isfile(path):
                    os.remove(path)   # limpiar el .new si el usuario canceló de verdad
            except OSError:
                pass
            return
        if not path:
            QMessageBox.warning(self, tr("Actualizar"),
                tr("No se pudo descargar la actualización. Inténtalo de nuevo o descárgala de GitHub."))
            return
        if updater.apply_update(path):
            # SIN diálogo modal (bloqueaba el reemplazo hasta pulsar OK): un aviso breve no
            # bloqueante y cerramos ya. El relevo reemplaza el .exe y reabre BMI solo.
            try:
                self.toasts.show(tr("Actualizando a la nueva versión…"), "success")
            except Exception:  # noqa: BLE001
                pass
            self.close()
            QApplication.processEvents()
            # Salida INMEDIATA del proceso para que el .exe se libere y el relevo lo reemplace.
            # (QApplication.quit() no siempre cierra del todo un onefile.)
            os._exit(0)
        else:
            QMessageBox.warning(self, tr("Actualizar"),
                tr("No se pudo aplicar la actualización. El nuevo archivo está en:\n{p}").format(p=path))

    def _on_webview_url_changed(self, qurl) -> None:
        """Activa el botón en páginas de mod o de colección, y ajusta su texto."""
        url = qurl.toString()
        parsed = self._parse_mod_page(url)
        if parsed:
            self._last_mod_page = parsed
            self.dl_current_btn.setEnabled(True)
            self.dl_current_btn.setText(tr("⬇ Descargar este mod (#{id})").format(id=parsed[1]))
        elif "/collections/" in url.lower():
            self.dl_current_btn.setEnabled(True)
            self.dl_current_btn.setText(tr("⬇ Descargar esta colección"))
        else:
            self.dl_current_btn.setEnabled(False)
            self.dl_current_btn.setText(tr("⬇ Descargar este mod"))

    @staticmethod
    def _parse_mod_page(url: str):
        """Extrae (dominio, mod_id) de la URL de una página de mod de Nexus, o None."""
        import re
        m = re.search(r"nexusmods\.com/([^/?#]+)/mods/(\d+)", url or "")
        return (m.group(1), int(m.group(2))) if m else None

    def _download_current_page(self) -> None:
        """Botón 'Descargar…': descarga el mod o la colección de la página actual."""
        url = self.webview.url().toString()
        parsed = self._parse_mod_page(url)
        if parsed:
            domain, mod_id = parsed
            active = self.config.game().domain
            if domain != active:
                QMessageBox.warning(
                    self, tr("Juego distinto"),
                    tr("Esta página es de «{domain}», pero el juego activo es «{active}».\n\n"
                       "Cambia el juego activo en el selector de arriba para descargar este "
                       "mod en su carpeta correcta y no mezclar mods de juegos distintos.")
                    .format(domain=domain, active=active)
                )
                return
            # Lee las dependencias de la página (Requirements) y encola el mod con ellas.
            self.webview.read_requirements(
                lambda s, d=domain, m=mod_id: self._enqueue_mod_with_web_deps(d, m, s))
            self.tabs.setCurrentWidget(self.downloads_panel)
            return
        if "/collections/" in url.lower():
            self.manager.enqueue_collection(url)
            self.tabs.setCurrentWidget(self.downloads_panel)
            self._on_log(tr("📦 Resolviendo la colección y encolando sus mods…"))
            return
        self._status(tr("No estás en la página de un mod ni de una colección de Nexus."))

    def _do_search(self) -> None:
        term = self.search_edit.text().strip()
        if term:
            self.webview.search(term)
            self.tabs.setCurrentWidget(self.explore_tab)

    def _build_tools_menu(self) -> None:
        self._tools_menu.clear()
        tools = self.config.tools or []
        for t in tools:
            act = self._tools_menu.addAction(icons.icon("wrench", theme.TEXT), t.get("name", ""))
            act.triggered.connect(lambda _=False, tool=t: self._launch_tool(tool))
        if tools:
            self._tools_menu.addSeparator()
        self._tools_menu.addAction(icons.icon("settings", theme.TEXT),
                                   tr("Gestionar herramientas…"), self._open_tools_dialog)

    def _launch_tool(self, tool) -> None:
        try:
            launcher.launch_tool(tool.get("path", ""), tool.get("args", ""), tool.get("cwd", ""))
            self._status(tr("Lanzado: {n}").format(n=tool.get("name", "")))
        except launcher.GameLaunchError as e:
            QMessageBox.warning(self, tr("Herramientas"), str(e))

    def _open_tools_dialog(self) -> None:
        from .tools_dialog import ToolsDialog
        ToolsDialog(self.config, self).exec()

    def _offer_vfs_clean(self) -> None:
        """Al activar el Modo VFS, ofrece retirar de Data los archivos de mods ya
        desplegados (se servirán virtualizados). Muestra antes una vista previa de lo que
        se retiraría y mantiene los .esp para no romper el orden de carga."""
        inst = self.manager.installer
        self._status(tr("Analizando tu carpeta Data…"))
        QApplication.processEvents()
        plan = inst.clean_loose_files(dry_run=True)
        if not plan:
            QMessageBox.information(
                self, tr("Data ya está limpia"),
                tr("Tu carpeta Data ya está limpia: no hay archivos de mods sueltos que "
                   "retirar (sus texturas/mallas/.bsa ya se sirven por el VFS). No hay nada "
                   "que hacer."))
            self._status(tr("Data ya estaba limpia."))
            return
        sample = "\n".join("   • " + p for p in plan[:12])
        more = tr("\n   …y {n} más").format(n=len(plan) - 12) if len(plan) > 12 else ""
        ans = QMessageBox.question(
            self, tr("Aligerar Data ({n} archivos)").format(n=len(plan)),
            tr("Se retirarán de Data {n} archivo(s) de mods (texturas, mallas, sonidos, "
               ".bsa…). Se servirán virtualizados al jugar y Data quedará limpia. Los "
               "plugins (.esp/.esm/.esl) se mantienen. Solo se tocan archivos que tus "
               "mods poseen (mismos bytes) — nada vanilla ni tuyo.\n\nEjemplos:\n"
               "{sample}{more}\n\n"
               "⚠ IMPORTANTE: después de aligerar, lanza SIEMPRE el juego con el botón "
               "▶ Jugar de BMI (monta el VFS). Si lo abres por Steam o por un acceso "
               "directo de SKSE, los mods cargarán SIN sus texturas/mallas/.bsa.\n\n"
               "Es reversible (desactiva el Modo VFS y vuelve a desplegar). ¿Continuar?")
            .format(n=len(plan), sample=sample, more=more))
        if ans != QMessageBox.StandardButton.Yes:
            self._status(tr("Aligerado cancelado."))
            return
        self._status(tr("Aligerando Data para el Modo VFS… puede tardar un poco."))
        QApplication.processEvents()
        removed = inst.clean_loose_files(log=self.manager.log.emit)
        self.mods_panel.refresh()
        self._status(tr("Data aligerado: {n} archivo(s) ahora se sirven por el VFS.")
                     .format(n=len(removed)))

    def _open_settings(self) -> None:
        vfs_before = getattr(self.config, "vfs_mode", False)
        mods_dir_before = self.config.mods_dir
        dlg = SettingsDialog(self.config, self)
        # Botón «Detectar mods de la carpeta» dentro de Ajustes (bajo la carpeta de mods).
        dlg.detect_mods_requested.connect(self._detect_mods)
        dlg.check_updates_requested.connect(lambda: self._check_updates_async(silent=False))
        if dlg.exec():
            dlg.apply_to_config()
            if getattr(self.config, "vfs_mode", False) and not vfs_before:
                self._offer_vfs_clean()
            self.webview.set_downloads_dir(self.config.downloads_dir)
            self.manager.update_credentials()
            self.mods_panel.refresh()
            self.home_panel.refresh(self.mods_panel._scan_cache)
            # Si cambió la carpeta de mods, detecta los que ya haya ahí (estilo MO2).
            if self.config.mods_dir != mods_dir_before:
                self._auto_import_mods()
            if dlg.language_changed():
                QMessageBox.information(
                    self, tr("Idioma cambiado"),
                    tr("El idioma se aplicará la próxima vez que abras el programa."))
            self._status(tr("Ajustes guardados."))

    # ------------------------------------------------------------------
    def _launch_game_vfs(self) -> None:
        """Lanza el juego en Modo VFS (USVFS) en un hilo: monta el VFS, arranca el juego
        enganchado y espera a que cierre — sin congelar la interfaz."""
        import threading
        self._status(tr("Modo VFS: montando los mods y lanzando el juego…"))

        def run():
            try:
                launcher.launch_vfs(self.config, self.manager.store, log=self.manager.log.emit)
            except launcher.GameLaunchError as e:
                self.manager.log.emit("Modo VFS: " + str(e))
            except Exception as e:  # noqa: BLE001
                self.manager.log.emit(f"Modo VFS: error inesperado: {e}")

        threading.Thread(target=run, daemon=True).start()

    def _launch_game(self) -> None:
        if getattr(self.config, "vfs_mode", False):
            self._launch_game_vfs()
            return
        g = self.config.game()
        se = g.script_extender or "SE"
        try:
            exe = launcher.launch(self.config, prefer_skse=True)
        except launcher.GameLaunchError as e:
            QMessageBox.warning(self, tr("No se pudo iniciar el juego"), str(e))
            return
        if launcher.extender_active(self.config, exe):
            self._on_log(tr("▶ Iniciando {game} con {se}: {exe}").format(game=g.name, se=se, exe=exe))
        elif launcher.extender_is_optional(self.config):
            # Morrowind (MWSE se inyecta, sin loader) u Oblivion de Steam (OBSE se inyecta):
            # lanzar el .exe del juego es lo correcto, no un fallo → sin aviso.
            self._on_log(tr("▶ Iniciando {game}: {exe}").format(game=g.name, exe=exe))
        else:
            self._on_log(tr("▶ {se} no encontrado; iniciando {name}: {exe}").format(
                se=se, name=exe.name, exe=exe))
            QMessageBox.information(
                self, tr("{se} no encontrado").format(se=se),
                tr("No encontré el lanzador del Script Extender, así que lancé el juego "
                   "normal.\n\nSi usas mods con Script Extender, instálalo en la carpeta del "
                   "juego o indica su ruta en Ajustes."),
            )

    # ------------------------------------------------------------------
    def _refresh_play_btn(self) -> None:
        se = self.config.game().script_extender or "SE"
        self.play_btn.setText(tr("Jugar ({se})").format(se=se))
        self.play_btn.setIcon(icons.icon("play", "#0b2a12"))
        self.play_btn.setToolTip(tr("Lanzar {game} con {se}").format(game=self.config.game().name, se=se))

    def _refresh_title(self) -> None:
        self.setWindowTitle(f"BMI — {self.config.game().name}")

    def _on_game_changed(self) -> None:
        key = self.game_combo.currentData()
        if not key or key == self.config.game_domain:
            return
        # No cambiar de juego con descargas en curso: instalaría en el juego equivocado.
        if self.manager.has_pending_work():
            QMessageBox.warning(
                self, "Descargas en curso",
                "Hay descargas o instalaciones en curso. Espera a que terminen (o límpialas "
                "en Descargas) antes de cambiar de juego.",
            )
            self._set_game_combo(self.config.game_domain)  # revertir
            return
        # Idle: purga cualquier resto y cambia.
        self.manager.purge_pending()
        # Parar el escáner de páginas: has_pending_work() no lo mira, así que podría seguir
        # leyendo páginas del juego ANTERIOR y encolar sus requisitos/traducciones en el
        # juego nuevo (Data equivocada). Descartamos su cola en curso.
        sc = getattr(self, "_tr_scanner", None)
        if sc is not None:
            sc.reset()
        self.downloads_panel.rebuild()
        self.config.switch_game(key)
        self.manager.reload_for_game()
        self.webview.set_game_domain(self.config.game().domain)
        self.mods_panel.refresh()
        self.home_panel.refresh(self.mods_panel._scan_cache)
        self._auto_import_mods()   # detecta mods ya presentes en la carpeta del juego nuevo
        self._refresh_play_btn()
        self._refresh_title()
        self._status(f"Juego activo: {self.config.game().name}")

    def _set_game_combo(self, key: str) -> None:
        gi = self.game_combo.findData(key)
        if gi >= 0:
            self.game_combo.blockSignals(True)
            self.game_combo.setCurrentIndex(gi)
            self.game_combo.blockSignals(False)

    def apply_config_game(self) -> None:
        """Re-sincroniza la UI con el juego de la config (p.ej. tras el asistente)."""
        self._set_game_combo(self.config.game_domain)
        self.manager.reload_for_game()
        self.webview.set_game_domain(self.config.game().domain)
        self.mods_panel.refresh()
        self.home_panel.refresh(self.mods_panel._scan_cache)
        self._refresh_play_btn()
        self._refresh_title()

    # ------------------------------------------------------------------
    def handle_external_link(self, link: str) -> None:
        link = link.strip()
        if link.lower().startswith("nxm://"):
            self.manager.enqueue_nxm(link)
            self.tabs.setCurrentWidget(self.downloads_panel)
            self.raise_()
            self.activateWindow()

    # ------------------------------------------------------------------
    def _restore_geometry(self) -> None:
        s = QSettings("NexusModInstaller", "NexusModInstaller")
        geo = s.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1200, 840)

    # ---- Marco de ventana propio (barra de título integrada) ----------
    # La ventana CONSERVA su marco de sistema (WS_CAPTION/WS_THICKFRAME): solo se le dice a
    # Windows que el área de cliente ocupa también la zona del marco (WM_NCCALCSIZE) y se le
    # responde qué es cada punto (WM_NCHITTEST): bordes = redimensionar, barra = mover/doble
    # clic/Snap. Así la sombra, animaciones y Win+flechas siguen siendo 100% nativos.

    _WM_NCCALCSIZE, _WM_NCHITTEST = 0x0083, 0x0084
    _HT = {"client": 1, "caption": 2, "left": 10, "right": 11, "top": 12, "topleft": 13,
           "topright": 14, "bottom": 15, "bottomleft": 16, "bottomright": 17}
    _RESIZE_BAND = 6   # px de borde para redimensionar

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._custom_frame and not self._frame_forced:
            self._frame_forced = True
            try:
                import ctypes
                # SWP_FRAMECHANGED: fuerza un WM_NCCALCSIZE inicial para ocultar la barra nativa.
                ctypes.windll.user32.SetWindowPos(
                    int(self.winId()), 0, 0, 0, 0, 0,
                    0x0002 | 0x0001 | 0x0004 | 0x0020)  # NOMOVE|NOSIZE|NOZORDER|FRAMECHANGED
            except Exception:  # noqa: BLE001
                self._custom_frame = False

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        # Al maximizar/restaurar, refrescar el glifo del botón central de la barra.
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "titlebar"):
            self.titlebar.btn_max.update()

    def nativeEvent(self, eventType, message):
        if not getattr(self, "_custom_frame", False) or eventType != b"windows_generic_MSG":
            return super().nativeEvent(eventType, message)
        try:
            import ctypes
            from ctypes import wintypes
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            # Estado REAL de maximizado según Windows en el instante del mensaje. OJO: no usar
            # self.isMaximized(): Qt lo actualiza DESPUÉS del WM_NCCALCSIZE de la transición,
            # y aplicar el margen de maximizado a la ventana ya restaurada dejaba a la vista
            # una franja del marco no-cliente (bordes del color de acento de Windows).
            zoomed = bool(ctypes.windll.user32.IsZoomed(int(self.winId())))
            if msg.message == self._WM_NCCALCSIZE and msg.wParam:
                # Cliente = ventana entera (adiós barra nativa). Maximizada, Windows saca la
                # ventana del monitor el grosor del marco: hay que compensarlo con un margen.
                rect = ctypes.cast(msg.lParam, ctypes.POINTER(wintypes.RECT)).contents
                if zoomed:
                    u = ctypes.windll.user32
                    pad = u.GetSystemMetrics(32) + u.GetSystemMetrics(92)  # SIZEFRAME+PADDED
                    rect.top += pad
                    rect.left += pad
                    rect.right -= pad
                    rect.bottom -= pad
                return True, 0
            if msg.message == self._WM_NCHITTEST:
                # Coordenadas de pantalla empaquetadas en lParam (shorts con signo).
                x = ctypes.c_short(msg.lParam & 0xFFFF).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                pos = self.mapFromGlobal(QPoint(x, y))
                w, h, b = self.width(), self.height(), self._RESIZE_BAND
                if not zoomed:
                    left, right = pos.x() <= b, pos.x() >= w - b
                    top, bottom = pos.y() <= b, pos.y() >= h - b
                    if top and left: return True, self._HT["topleft"]
                    if top and right: return True, self._HT["topright"]
                    if bottom and left: return True, self._HT["bottomleft"]
                    if bottom and right: return True, self._HT["bottomright"]
                    if left: return True, self._HT["left"]
                    if right: return True, self._HT["right"]
                    if top: return True, self._HT["top"]
                    if bottom: return True, self._HT["bottom"]
                # Zona de la barra de título (menos sus botones) = mover/doble clic/Snap.
                tb = getattr(self, "titlebar", None)
                if tb is not None and pos.y() < tb.height():
                    if not tb.is_over_button(tb.mapFrom(self, pos)):
                        return True, self._HT["caption"]
                return True, self._HT["client"]
        except Exception:  # noqa: BLE001
            pass
        return super().nativeEvent(eventType, message)

    def closeEvent(self, event) -> None:
        s = QSettings("NexusModInstaller", "NexusModInstaller")
        s.setValue("geometry", self.saveGeometry())
        try:
            self.manager.shutdown()
        except Exception:
            pass
        super().closeEvent(event)
