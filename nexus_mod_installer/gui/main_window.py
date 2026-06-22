"""Ventana principal de la aplicación."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QProgressBar, QPlainTextEdit,
    QLabel, QMessageBox, QHeaderView, QAbstractItemView, QFileDialog, QComboBox,
    QDialog,
)
from PySide6.QtGui import QColor

from ..config import AppConfig
from ..manager import DownloadManager
from ..models import DownloadTask, TaskStatus
from .. import launcher, games
from ..i18n import tr
from .webview import NexusWebView
from .settings_dialog import SettingsDialog
from .fomod_dialog import FomodDialog
from .mods_panel import ModsPanel
from .home_panel import HomePanel
from . import theme


# ===========================================================================
class DownloadsPanel(QWidget):
    def __init__(self, manager: DownloadManager, window, parent=None):
        super().__init__(parent)
        self.manager = manager
        self._window = window
        self._row_of: dict[int, int] = {}   # id(task) -> fila

        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.count_lbl = QLabel("")
        bar.addWidget(self.count_lbl)
        bar.addStretch()
        retry_sel = QPushButton(tr("↻ Reintentar selección"))
        retry_sel.setToolTip(tr("Reintenta las tareas seleccionadas que estén en error o pendientes de clic"))
        retry_sel.clicked.connect(self._retry_selected)
        remove_sel = QPushButton(tr("✖ Quitar selección"))
        remove_sel.setToolTip(tr("Quita de la cola las tareas seleccionadas (no las que se descargan ahora)"))
        remove_sel.clicked.connect(self._remove_selected)
        clear = QPushButton(tr("🧹 Limpiar completadas"))
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
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3, 4):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        v.addWidget(self.table)

        manager.task_added.connect(self.on_task_added)
        manager.task_updated.connect(self.on_task_updated)
        manager.tasks_changed.connect(self.rebuild)

    def _label(self, task: DownloadTask) -> str:
        prefix = "🌐 " if task.is_translation else ("🔗 " if task.is_dependency else "")
        return prefix + (task.mod_name or f"mod {task.mod_id}")

    def on_task_added(self, task: DownloadTask) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._row_of[id(task)] = row
        self.table.setItem(row, 0, QTableWidgetItem(self._label(task)))
        self.table.setItem(row, 1, QTableWidgetItem(task.file_name))
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
        self.table.item(row, 0).setText(self._label(task))
        self.table.item(row, 1).setText(task.file_name)
        self._update_row(row, task)

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
    def __init__(self, config: AppConfig, manager: DownloadManager):
        super().__init__()
        self.config = config
        self.manager = manager
        self.setWindowIcon(theme.make_app_icon())
        self._views_refresh_scheduled = False
        self._last_mod_page = None   # (dominio, mod_id) de la última página de mod vista
        self._restore_geometry()

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
        self.url_edit.returnPressed.connect(self._add_from_url)
        add_btn = QPushButton(tr("⬇ Añadir / Descargar"))
        add_btn.setProperty("variant", "primary")
        add_btn.clicked.connect(self._add_from_url)
        batch_btn = QPushButton(tr("➕ Varios…"))
        batch_btn.setToolTip(tr("Pegar varias URLs/ids a la vez y descargarlas en cola"))
        batch_btn.clicked.connect(self._open_batch_dialog)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("🔎 Buscar mods en Nexus…"))
        self.search_edit.returnPressed.connect(self._do_search)
        search_btn = QPushButton(tr("Buscar"))
        search_btn.clicked.connect(self._do_search)

        self.play_btn = QPushButton()
        self.play_btn.setProperty("variant", "success")
        self.play_btn.clicked.connect(self._launch_game)
        self._refresh_play_btn()
        settings_btn = QPushButton(tr("⚙ Ajustes"))
        settings_btn.clicked.connect(self._open_settings)

        top = QHBoxLayout()
        top.addWidget(self.game_combo)
        top.addWidget(self.url_edit, 3)
        top.addWidget(add_btn)
        top.addWidget(batch_btn)
        top.addSpacing(10)
        top.addWidget(self.search_edit, 2)
        top.addWidget(search_btn)
        top.addSpacing(10)
        top.addWidget(self.play_btn)
        top.addWidget(settings_btn)
        top_w = QWidget(); top_w.setLayout(top)

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
        self.log_tab = self._build_log_tab()

        self.tabs = QTabWidget()
        self.tabs.addTab(self.home_panel, tr("🏠 Inicio"))
        self.tabs.addTab(self.explore_tab, tr("🔎 Explorar Nexus"))
        self.tabs.addTab(self.downloads_panel, tr("⬇ Descargas"))
        self.tabs.addTab(self.mods_panel, tr("📦 Mods"))
        self.tabs.addTab(self.log_tab, tr("📜 Registro"))

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(8, 8, 8, 4)
        v.addWidget(top_w)
        v.addWidget(self.tabs)
        self.setCentralWidget(central)

        self.status_label = QLabel(tr("Listo."))
        self.statusBar().addWidget(self.status_label)

        manager.log.connect(self._on_log)
        manager.needs_click.connect(self._on_needs_click)
        manager.task_updated.connect(self._maybe_refresh_views)
        manager.fomod_requested.connect(self._on_fomod_requested)

        self._refresh_title()

    # ------------------------------------------------------------------
    def _build_log_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        bar = QHBoxLayout()
        bar.addStretch()
        clear = QPushButton(tr("🧹 Limpiar"))
        clear.clicked.connect(lambda: self.log_panel.clear())
        save = QPushButton(tr("💾 Guardar"))
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
        if task.status == TaskStatus.DONE and not self._views_refresh_scheduled:
            self._views_refresh_scheduled = True
            QTimer.singleShot(1000, self._do_views_refresh)   # coalesce ráfagas de instalación

    def _do_views_refresh(self) -> None:
        self._views_refresh_scheduled = False
        self.mods_panel.refresh()
        # Reutiliza el escaneo recién hecho por el gestor (evita re-leer el disco).
        self.home_panel.refresh(self.mods_panel._scan_cache)

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
        self.dl_current_btn = QPushButton(tr("⬇ Descargar este mod"))
        self.dl_current_btn.setProperty("variant", "primary")
        self.dl_current_btn.setToolTip(tr("Descarga el mod de la página que estás viendo"))
        self.dl_current_btn.setEnabled(False)
        self.dl_current_btn.clicked.connect(self._download_current_page)
        bar.addWidget(self.dl_current_btn)

        lay.addLayout(bar)
        lay.addWidget(self.webview, 1)
        self.webview.urlChanged.connect(self._on_webview_url_changed)
        return w

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

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.config, self)
        if dlg.exec():
            dlg.apply_to_config()
            self.webview.set_downloads_dir(self.config.downloads_dir)
            self.manager.update_credentials()
            self.mods_panel.refresh()
            self.home_panel.refresh(self.mods_panel._scan_cache)
            if dlg.language_changed():
                QMessageBox.information(
                    self, tr("Idioma cambiado"),
                    tr("El idioma se aplicará la próxima vez que abras el programa."))
            self._status(tr("Ajustes guardados."))

    # ------------------------------------------------------------------
    def _launch_game(self) -> None:
        g = self.config.game()
        se = g.script_extender or "SE"
        try:
            exe = launcher.launch(self.config, prefer_skse=True)
        except launcher.GameLaunchError as e:
            QMessageBox.warning(self, tr("No se pudo iniciar el juego"), str(e))
            return
        if exe.name.lower().startswith(("skse", "f4se", "nvse", "fose", "obse")):
            self._on_log(tr("▶ Iniciando {game} con {se}: {exe}").format(game=g.name, se=se, exe=exe))
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
        self.play_btn.setText(tr("▶ Jugar ({se})").format(se=se))
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
        self.downloads_panel.rebuild()
        self.config.switch_game(key)
        self.manager.reload_for_game()
        self.webview.set_game_domain(self.config.game().domain)
        self.mods_panel.refresh()
        self.home_panel.refresh(self.mods_panel._scan_cache)
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

    def closeEvent(self, event) -> None:
        s = QSettings("NexusModInstaller", "NexusModInstaller")
        s.setValue("geometry", self.saveGeometry())
        try:
            self.manager.shutdown()
        except Exception:
            pass
        super().closeEvent(event)
