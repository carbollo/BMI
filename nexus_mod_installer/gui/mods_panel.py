"""Gestor de mods: pestañas Mods · Orden de carga · Conflictos · Perfiles."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QSize, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem,
    QListWidget, QListWidgetItem, QLineEdit, QPushButton, QLabel, QCheckBox,
    QHeaderView, QAbstractItemView, QMessageBox, QInputDialog, QTreeWidget, QTreeWidgetItem,
    QFileDialog,
)

from pathlib import Path

from .. import scanner, conflicts, esp
from ..models import InstalledMod
from ..profiles import ProfileStore, safe_name
from ..i18n import tr
from . import theme
from . import images
from . import icons
from .mod_details_dialog import ModDetailsDialog, human_size

# Color del icono según el tipo de plugin.
_KIND_COLOR = {"ESM": theme.INFO, "ESL": "#a371f7", "ESP": theme.ACCENT}
_EXT_CAT = "\x00ext"   # clave interna del grupo de mods externos (no es una categoría real)


def _centered(widget: QWidget) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addStretch()
    lay.addWidget(widget)
    lay.addStretch()
    return w


class ModsPanel(QWidget):
    translate_all_requested = Signal()   # el usuario pidió traducir todos sus mods (vía web)
    _loot_ready = Signal(object)         # resultado de LOOT (orden/avisos) desde el worker
    _crash_ready = Signal(object)        # CrashReport|None desde el worker de análisis de crash

    _CAT_COLOR = {
        "externo": theme.SUCCESS, "gestionado": theme.INFO,
        "cc": "#a371f7", "vanilla": theme.TEXT_DIM,
    }

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.config = manager.config
        self.profiles = ProfileStore()
        self._populating = False
        self._scan_cache: list = []
        self._esp_cache: dict = {}   # nombre -> (mtime, header) para no re-parsear plugins
        self._row_mods: list = []
        self._cat_order = None         # orden de categorías por juego (None = sin cargar aún)
        self._collapsed: set = set()   # separadores plegados
        self._cat_colors: dict = {}    # categoría -> color hex del separador
        self._view_game = None
        self._refresh_scheduled = False
        self._order_reorderable = True
        self._order_togglable = True
        self._prio_populating = False

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_mods_tab(), tr("📦 Mods"))
        self.tabs.addTab(self._build_priority_tab(), tr("↕ Prioridad"))
        self.tabs.addTab(self._build_order_tab(), tr("🧩 Plugins"))
        self.tabs.addTab(self._build_conflicts_tab(), tr("⚠ Conflictos"))
        self.tabs.addTab(self._build_profiles_tab(), tr("👤 Perfiles"))

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        self._loot_ready.connect(self._apply_loot)     # descarga+orden en hilo → aplica en la GUI
        self._crash_ready.connect(self._show_crash_report)   # análisis en hilo → diálogo en GUI
        self.refresh()

    # ==================================================================
    # Pestaña: Mods gestionados
    # ==================================================================
    def _build_mods_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("🔎 Filtrar mods…"))
        self.search_edit.textChanged.connect(self._filter_mods)
        top.addWidget(self.search_edit)
        refresh = QPushButton(tr("🔄 Actualizar"))
        refresh.clicked.connect(self.refresh)
        top.addWidget(refresh)
        translate = QPushButton(tr("🌐 Traducir mis mods"))
        translate.setToolTip(tr("Busca en Nexus la traducción al idioma de la app de cada mod "
                                "instalado y encola las que encuentre"))
        translate.clicked.connect(self._translate_all)
        top.addWidget(translate)
        variants_btn = QPushButton(tr("⚠ Revisar variantes"))
        variants_btn.setToolTip(tr("Detecta mods de variante incorrecta para tu plataforma "
                                   "(GOG en Steam, VR en Skyrim SE)"))
        variants_btn.clicked.connect(self._check_variants)
        top.addWidget(variants_btn)
        export_btn = QPushButton(tr("📤 Exportar lista"))
        export_btn.setToolTip(tr("Guarda un .txt con tus mods para reinstalarlos luego "
                                 "o en otro PC"))
        export_btn.clicked.connect(self._export_list)
        top.addWidget(export_btn)
        import_btn = QPushButton(tr("📥 Importar lista"))
        import_btn.setToolTip(tr("Carga un .txt de mods y descarga los que te falten "
                                 "(salta los que ya tengas)"))
        import_btn.clicked.connect(self._import_list)
        top.addWidget(import_btn)
        upd_btn = QPushButton(tr("🔄 Buscar actualizaciones"))
        upd_btn.setToolTip(tr("Comprueba en Nexus qué mods instalados tienen una versión nueva"))
        upd_btn.clicked.connect(lambda: self.manager.check_updates())
        top.addWidget(upd_btn)
        self.only_updates_cb = QCheckBox(tr("Con actualización"))
        self.only_updates_cb.setToolTip(tr("Mostrar solo los mods con actualización disponible"))
        self.only_updates_cb.toggled.connect(self._filter_mods)
        top.addWidget(self.only_updates_cb)
        v.addLayout(top)
        cat_note = QLabel(tr("Las categorías solo organizan la lista; no cambian el orden de "
                             "carga. Clic derecho en un mod para asignarle una; clic en un "
                             "separador para plegarlo."))
        cat_note.setProperty("role", "dim"); cat_note.setWordWrap(True)
        v.addWidget(cat_note)

        self.mods_table = QTableWidget(0, 5)
        self.mods_table.setHorizontalHeaderLabels(
            [tr("Activo"), tr("Mod"), tr("Versión"), tr("Plugins"), tr("Tamaño")])
        self.mods_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mods_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.mods_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.mods_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        theme.make_columns_movable(self.mods_table)   # columnas arrastrables + redimensionables
        self.mods_table.verticalHeader().setVisible(False)
        self.mods_table.verticalHeader().setDefaultSectionSize(32)
        self.mods_table.setAlternatingRowColors(True)
        self.mods_table.setShowGrid(False)
        self.mods_table.setIconSize(QSize(26, 26))
        self.mods_table.doubleClicked.connect(lambda *_: self._details_selected())
        self.mods_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.mods_table.customContextMenuRequested.connect(self._mods_context_menu)
        self.mods_table.cellClicked.connect(self._on_mods_cell_clicked)
        v.addWidget(self.mods_table, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.sel_label = QLabel("")
        self.sel_label.setProperty("role", "dim")
        bottom.addWidget(self.sel_label)
        bottom.addStretch()
        for text, fn in [
            (tr("Detalles"), self._details_selected),
            (tr("Activar"), lambda: self._bulk_enable(True)),
            (tr("Desactivar"), lambda: self._bulk_enable(False)),
        ]:
            b = QPushButton(text); b.clicked.connect(fn); bottom.addWidget(b)
        cat_btn = QPushButton(tr("🗂 Categoría"))
        cat_btn.setToolTip(tr("Asigna los mods seleccionados a una categoría (solo organiza; "
                              "no cambia el orden de carga)"))
        cat_btn.clicked.connect(self._assign_category_menu)
        bottom.addWidget(cat_btn)
        upd_sel = QPushButton(tr("🔄 Actualizar"))
        upd_sel.setToolTip(tr("Descarga la versión nueva de los mods seleccionados con actualización"))
        upd_sel.clicked.connect(self._update_selected)
        bottom.addWidget(upd_sel)
        uninstall = QPushButton(tr("Desinstalar"))
        uninstall.setProperty("variant", "danger")
        uninstall.clicked.connect(self._bulk_uninstall)
        bottom.addWidget(uninstall)
        v.addLayout(bottom)
        return w

    # ------------------------------------------------------------------
    # Exportar / importar lista de mods
    # ------------------------------------------------------------------
    def _export_list(self) -> None:
        """Guarda un .txt con los mods gestionados por BMI (dominio · id · nombre)."""
        from ..modlist import export_text
        mods = [m for m in self.manager.store.all()
                if m.mod_id > 0 and not getattr(m, "imported", False)]
        if not mods:
            QMessageBox.information(self, tr("Exportar lista"), tr(
                "No hay mods que exportar. Los mods externos (sin id de Nexus) no se pueden "
                "volver a descargar por id, así que no se incluyen."))
            return
        default = f"mods_{self.config.game_domain}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Exportar lista de mods"), default, tr("Lista de mods (*.txt)"))
        if not path:
            return
        try:
            Path(path).write_text(
                export_text(mods, self.config.game_domain), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, tr("Exportar lista"),
                                tr("No se pudo guardar el archivo: {e}").format(e=e))
            return
        QMessageBox.information(self, tr("Exportar lista"),
                                tr("Lista exportada: {n} mods.").format(n=len(mods)))

    def _import_list(self) -> None:
        """Carga un .txt de mods y encola los que falten (el gestor salta los ya instalados)."""
        from ..modlist import parse_text
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Importar lista de mods"), "", tr("Lista de mods (*.txt)"))
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig", errors="ignore")
        except OSError as e:
            QMessageBox.warning(self, tr("Importar lista"),
                                tr("No se pudo leer el archivo: {e}").format(e=e))
            return
        dom = self.config.game_domain
        entries = parse_text(text, dom)
        if not entries:
            QMessageBox.information(self, tr("Importar lista"),
                                    tr("El archivo no contiene mods reconocibles."))
            return
        same = [e for e in entries if e.game_domain == dom]
        other = len(entries) - len(same)
        to_get = [e for e in same if not self.manager.store.is_installed(e.mod_id)]
        already = len(same) - len(to_get)
        if not to_get:
            QMessageBox.information(self, tr("Importar lista"), tr(
                "Ya tienes los {n} mods de la lista para este juego.").format(n=len(same)))
            return
        msg = tr("Se descargarán {n} mods de la lista.").format(n=len(to_get))
        if already:
            msg += "\n" + tr("{n} ya los tienes: se saltan.").format(n=already)
        if other:
            msg += "\n" + tr("{n} son de otro juego: se ignoran (cambia de juego para "
                             "importarlos).").format(n=other)
        msg += "\n\n" + tr("¿Descargar ahora?")
        if QMessageBox.question(self, tr("Importar lista"), msg) \
                != QMessageBox.StandardButton.Yes:
            return
        for e in to_get:
            self.manager.enqueue_mod(e.game_domain, e.mod_id)
        self.manager.log.emit(
            tr("📥 Importados {n} mods de la lista; descargando…").format(n=len(to_get)))

    def _refresh_mods(self) -> None:
        # Mods GESTIONADOS por BMI (del almacén) + mods EXTERNOS detectados por el escáner
        # (plugins que no instaló BMI), para que se vean TODOS los mods instalados.
        managed = sorted(self.manager.store.all(), key=lambda m: m.name.lower())
        managed_plugins = {p.lower() for m in managed for p in m.plugins}
        external = []
        for dm in self._scan_cache:
            if dm.category == "externo" and dm.name.lower() not in managed_plugins:
                external.append(InstalledMod(
                    mod_id=0, name=dm.name, version="",
                    game_domain=self.config.game().domain,
                    plugins=[dm.name], enabled=dm.enabled))
        external.sort(key=lambda m: m.name.lower())

        # Agrupar los gestionados por categoría; el orden de categorías es el guardado por el
        # usuario, con las nuevas añadidas al final. Las categorías vacías desaparecen.
        self._ensure_view_state()
        by_cat: dict[str, list] = {}
        for m in managed:
            by_cat.setdefault(m.category or "", []).append(m)
        named = list(dict.fromkeys(c for c in self._cat_order if c in by_cat))
        for c in sorted(k for k in by_cat if k and k not in named):
            named.append(c)
        if named != self._cat_order:
            self._cat_order = list(named)
            self._save_view_state()

        self._row_mods = []
        self._populating = True
        self.mods_table.setRowCount(0)
        if not (named or external):
            # Sin categorías ni externos: lista plana (como antes), sin separadores.
            for m in by_cat.get("", []):
                self._add_mod_row(m, False)
        else:
            for key in named:
                self._add_separator_row(key, key, len(by_cat[key]))
                for m in by_cat[key]:
                    self._add_mod_row(m, False)
            if by_cat.get(""):
                self._add_separator_row("", tr("Sin categoría"), len(by_cat[""]))
                for m in by_cat[""]:
                    self._add_mod_row(m, False)
            if external:
                self._add_separator_row(_EXT_CAT, tr("Externos (fuera de BMI)"), len(external))
                for m in external:
                    self._add_mod_row(m, True)
        self._populating = False
        self._filter_mods()

    def _add_separator_row(self, key: str, label: str, count: int) -> None:
        """Fila-cabecera de categoría (abarca las 5 columnas). Marca en ``_row_mods`` con
        ``(None, key)`` para distinguirla de una fila de mod."""
        row = self.mods_table.rowCount()
        self.mods_table.insertRow(row)
        arrow = "▸" if key in self._collapsed else "▾"
        it = QTableWidgetItem(f"   {arrow}   {label}    ({count})")
        f = it.font(); f.setBold(True); it.setFont(f)
        color = self._cat_colors.get(key) or theme.ACCENT
        it.setForeground(QColor(color))
        bg = QColor(color); bg.setAlpha(38); it.setBackground(bg)
        it.setFlags(Qt.ItemFlag.ItemIsEnabled)   # no seleccionable ni editable
        self.mods_table.setItem(row, 0, it)
        self.mods_table.setSpan(row, 0, 1, 5)
        self._row_mods.append((None, key))

    def _add_mod_row(self, mod: InstalledMod, is_ext: bool) -> None:
        row = self.mods_table.rowCount()
        self.mods_table.insertRow(row)
        cb = QCheckBox()
        cb.setChecked(mod.enabled)
        cb.toggled.connect(lambda on, r=row: self._toggle_row(r, on))
        self.mods_table.setCellWidget(row, 0, _centered(cb))
        name_item = QTableWidgetItem(mod.name)
        if is_ext:
            name_item.setForeground(QColor(theme.SUCCESS))
            name_item.setToolTip(tr("Mod externo (instalado fuera de BMI)"))
        elif not mod.enabled:
            name_item.setForeground(QColor(theme.TEXT_DIM))
        self.mods_table.setItem(row, 1, name_item)
        if not is_ext and getattr(mod, "picture_url", ""):
            images.make_icon_async(mod.picture_url, name_item, 26)
        has_update = (not is_ext) and mod.mod_id in self.manager.updates_available
        ver_item = QTableWidgetItem(("⬆ " if has_update else "") + (mod.version or "—"))
        if has_update:
            ver_item.setForeground(QColor(theme.ACCENT))
            ver_item.setToolTip(tr("Hay una versión más nueva en Nexus"))
        self.mods_table.setItem(row, 2, ver_item)
        self.mods_table.setItem(row, 3, QTableWidgetItem(", ".join(mod.plugins) or "—"))
        self.mods_table.setItem(row, 4,
            QTableWidgetItem("—" if is_ext else human_size(mod.size_bytes)))
        self._row_mods.append((mod, is_ext))

    def _check_variants(self) -> None:
        """Lista los mods instalados cuya variante NO corresponde a la plataforma del juego
        (GOG en Steam, VR en Skyrim SE) — la causa de que SKSE desactive plugins."""
        from ..variants import wrong_variant_mods, game_platform
        plat = game_platform(self.manager.config)
        plat_txt = plat.upper() if plat != "unknown" else "?"
        bad = wrong_variant_mods(self.manager.config, self.manager.store)
        if not bad:
            QMessageBox.information(
                self, tr("Revisar variantes"),
                tr("No se detectaron mods de variante incorrecta. Tu juego es «{p}».")
                .format(p=plat_txt))
            return
        lines = "\n".join(f"   • [{r}] {m.name}" for m, r in bad[:30])
        more = tr("\n   …y {n} más").format(n=len(bad) - 30) if len(bad) > 30 else ""
        QMessageBox.warning(
            self, tr("Variantes incompatibles ({n})").format(n=len(bad)),
            tr("Tu juego es {p}, pero estos mods son de otra plataforma (GOG/VR) y NO "
               "funcionarán (SKSE los desactiva):\n\n{lines}{more}\n\nDesinstálalos y "
               "descarga la versión NORMAL para Skyrim SE de cada uno.")
            .format(p=plat_txt, lines=lines, more=more))

    def _translate_all(self) -> None:
        """Pide traducir todos los mods leyendo la lista OFICIAL de traducciones de la página
        de cada mod (vía el navegador embebido). Lo ejecuta la ventana principal."""
        mods = [m for m in self.manager.store.all() if getattr(m, "mod_id", 0) and m.mod_id > 0]
        if not mods:
            QMessageBox.information(self, tr("Traducir mis mods"),
                                   tr("No hay mods con id de Nexus que traducir."))
            return
        if (self.manager.config.language or "es") == "en":
            QMessageBox.information(self, tr("Traducir mis mods"),
                                   tr("El idioma de la app es inglés; la mayoría de mods ya "
                                      "están en inglés, así que no se busca traducción."))
            return
        ans = QMessageBox.question(
            self, tr("Traducir mis mods"),
            tr("BMI abrirá la página de cada uno de tus {n} mods en su navegador para leer su "
               "lista OFICIAL de traducciones («Translations available on the Nexus») y "
               "encolar la de tu idioma si existe.\n\nTarda un poco (una página por mod) e "
               "conviene tener la sesión de Nexus iniciada. ¿Continuar?").format(n=len(mods)))
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.translate_all_requested.emit()

    def _filter_mods(self) -> None:
        term = self.search_edit.text().lower().strip()
        only_upd = bool(getattr(self, "only_updates_cb", None)
                        and self.only_updates_cb.isChecked())
        upd = self.manager.updates_available
        # Solo se puede plegar una categoría que TENGA separador visible; si no (lista plana
        # sin categorías) un '' plegado heredado no debe ocultar todos los mods.
        present_seps = {k for (mm, k) in self._row_mods if mm is None}
        shown = 0
        for row, (mod, tag) in enumerate(self._row_mods):
            if mod is None:                       # separador: oculto al buscar o al filtrar
                self.mods_table.setRowHidden(row, bool(term) or only_upd)
                continue
            has_update = (not tag) and mod.mod_id in upd
            if only_upd and not has_update:
                hide = True
            elif term:
                hide = term not in mod.name.lower()
            else:                                 # sin búsqueda: respeta el plegado
                key = _EXT_CAT if tag else (mod.category or "")
                hide = key in self._collapsed and key in present_seps
            self.mods_table.setRowHidden(row, hide)
            if not hide:
                shown += 1
        self.sel_label.setText(tr("{n} mod(s)").format(n=shown))

    def _update_selected(self) -> None:
        """Descarga la versión nueva de los mods seleccionados que tengan actualización
        (re-encola por id: el gestor baja el archivo principal actual y reinstala)."""
        mods = [m for m, ext in self._selected_entries()
                if not ext and m.mod_id in self.manager.updates_available]
        if not mods:
            QMessageBox.information(self, tr("Actualizar"), tr(
                "Selecciona mods marcados con ⬆ (pulsa «Buscar actualizaciones» primero)."))
            return
        for m in mods:
            self.manager.enqueue_mod(
                getattr(m, "game_domain", "") or self.config.game_domain, m.mod_id,
                is_update=True)
        # El ⬆ NO se borra aquí (si la descarga fallara se perdería el aviso): se recalcula al
        # instalar la versión nueva (su file_updated_at pasa a ser el actual) en la próxima
        # comprobación.
        self.manager.log.emit(tr("🔄 Actualizando {n} mod(s)…").format(n=len(mods)))
        self.refresh()

    def _selected_entries(self) -> list[tuple[InstalledMod, bool]]:
        """Devuelve [(mod, es_externo), ...] de las filas seleccionadas (omite separadores)."""
        out = []
        for idx in self.mods_table.selectionModel().selectedRows():
            r = idx.row()
            if 0 <= r < len(self._row_mods):
                mod, tag = self._row_mods[r]
                if mod is not None:
                    out.append((mod, tag))
        return out

    def _set_enabled(self, mod: InstalledMod, is_ext: bool, enabled: bool) -> None:
        if is_ext:
            # Externo: solo se activa/desactiva su plugin en plugins.txt (BMI no lo instaló).
            if self.config.plugins_txt_path and mod.plugins:
                scanner.set_plugin_enabled(
                    self.config.plugins_txt_path, mod.plugins[0], enabled,
                    star_prefix=self.config.game().star_prefix)
                self.manager.log.emit(
                    f"Plugin {'activado' if enabled else 'desactivado'}: {mod.plugins[0]}")
        else:
            self.manager.installer.set_mod_enabled(mod.mod_id, enabled, log=self.manager.log.emit)

    def _toggle_row(self, row: int, enabled: bool) -> None:
        if self._populating or not (0 <= row < len(self._row_mods)):
            return
        mod, is_ext = self._row_mods[row]
        if mod is None:                           # fila de separador (sin checkbox)
            return
        self._set_enabled(mod, is_ext, enabled)
        self.request_refresh()

    def _bulk_enable(self, enabled: bool) -> None:
        ents = self._selected_entries()
        if not ents:
            return
        for mod, is_ext in ents:
            self._set_enabled(mod, is_ext, enabled)
        self.refresh()

    def _bulk_uninstall(self) -> None:
        ents = self._selected_entries()
        managed_all = [m for m, ext in ents if not ext]
        n_ext = sum(1 for _, ext in ents if ext)
        # Los IMPORTADOS (id negativo / imported=True) no los instaló BMI: su carpeta origen
        # no está en el layout {id}_{name}, así que uninstall no la borraría y el mod
        # reaparecería "activado" con sus plugins desactivados (estado incoherente). Se
        # gestionan borrando su carpeta origen, no desde aquí → se excluyen como los externos.
        managed = [m for m in managed_all
                   if not (getattr(m, "imported", False) or m.mod_id < 0)]
        n_imp = len(managed_all) - len(managed)
        if not managed:
            QMessageBox.information(
                self, tr("Desinstalar"),
                tr("Solo se pueden desinstalar mods instalados por BMI. Los mods externos "
                   "gestiónalos desde donde los instalaste (o desactívalos en Orden de carga)."))
            return
        if QMessageBox.question(
            self, tr("Desinstalar"),
            tr("¿Desinstalar {n} mod(s) y quitar sus archivos de Data?").format(n=len(managed))
        ) != QMessageBox.StandardButton.Yes:
            return
        for m in managed:
            self.manager.installer.uninstall(m.mod_id, log=self.manager.log.emit)
        if n_ext + n_imp:
            self.manager.log.emit(
                f"{n_ext + n_imp} mod(s) externo(s)/importado(s) no se desinstalan "
                "(no los instaló BMI).")
        self.refresh()

    def _details_selected(self) -> None:
        ents = self._selected_entries()
        if not ents:
            return
        mod, is_ext = ents[0]
        if not is_ext:
            mod = self.manager.store.get(mod.mod_id) or mod
        ModDetailsDialog(mod, self, store=self.manager.store,
                         installer=self.manager.installer).exec()
        self.request_refresh()

    # ==================================================================
    # Categorías (separadores) de la lista de Mods — SOLO organizan la vista:
    # no tocan plugins.txt, el orden de carga ni la prioridad de sobrescritura.
    # El estado (orden de categorías + plegados) se guarda por juego en un JSON.
    # ==================================================================
    def _view_state_path(self) -> Path:
        return Path(self.config.mods_dir).parent / f"mod_view_{self.config.game_domain}.json"

    def _ensure_view_state(self) -> None:
        dom = self.config.game_domain
        if self._view_game == dom and self._cat_order is not None:
            return
        self._view_game = dom
        self._cat_order = []
        self._collapsed = set()
        self._cat_colors = {}
        try:
            import json
            p = self._view_state_path()
            if p.is_file():
                d = json.loads(p.read_text(encoding="utf-8"))
                self._cat_order = [str(x) for x in d.get("order", [])]
                self._collapsed = {str(x) for x in d.get("collapsed", [])}
                self._cat_colors = {str(k): str(v) for k, v in (d.get("colors") or {}).items()}
        except Exception:  # noqa: BLE001
            pass

    def _save_view_state(self) -> None:
        try:
            import json
            self._view_state_path().write_text(
                json.dumps({"order": self._cat_order, "collapsed": sorted(self._collapsed),
                            "colors": self._cat_colors},
                           ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _existing_categories(self) -> list[str]:
        cats = {(m.category or "") for m in self.manager.store.all() if (m.category or "")}
        ordered = [c for c in (self._cat_order or []) if c in cats]
        ordered += sorted(c for c in cats if c not in ordered)
        return ordered

    def _on_mods_cell_clicked(self, row: int, _col: int) -> None:
        """Clic en un separador → plegar/desplegar su categoría."""
        if not (0 <= row < len(self._row_mods)):
            return
        mod, key = self._row_mods[row]
        if mod is not None:
            return
        if key in self._collapsed:
            self._collapsed.discard(key)
        else:
            self._collapsed.add(key)
        self._save_view_state()
        it = self.mods_table.item(row, 0)
        if it:
            arrow = "▸" if key in self._collapsed else "▾"
            it.setText(it.text().replace("▾", arrow).replace("▸", arrow))
        self._filter_mods()

    def _mods_context_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        row = self.mods_table.rowAt(pos.y())
        menu = QMenu(self)
        if 0 <= row < len(self._row_mods) and self._row_mods[row][0] is None:
            key = self._row_mods[row][1]
            if key not in ("", _EXT_CAT):        # categoría real (no «Sin categoría»/«Externos»)
                menu.addAction(tr("✏ Renombrar categoría"), lambda: self._cat_rename(key))
                menu.addAction(tr("🎨 Color…"), lambda: self._cat_set_color(key))
                menu.addAction(tr("🗑 Eliminar categoría"), lambda: self._cat_delete(key))
                menu.addSeparator()
                menu.addAction(tr("▲ Subir categoría"), lambda: self._cat_move(key, -1))
                menu.addAction(tr("▼ Bajar categoría"), lambda: self._cat_move(key, 1))
        else:
            managed = [m for m, ext in self._selected_entries() if not ext]
            if managed:
                self._fill_category_menu(menu, managed)
                if any(m.mod_id > 0 for m in managed):
                    menu.addSeparator()
                    menu.addAction(tr("👍 Endorsar en Nexus"), self._endorse_selected)
        if not menu.isEmpty():
            menu.exec(self.mods_table.viewport().mapToGlobal(pos))

    def _endorse_selected(self) -> None:
        """Endorsa en Nexus (con la sesión OAuth) los mods seleccionados con id de Nexus."""
        mods = [m for m, ext in self._selected_entries() if not ext and m.mod_id > 0]
        if not mods:
            return
        if not self.manager.is_logged_in:
            QMessageBox.information(self, tr("Endorsar"),
                                   tr("Inicia sesión en Nexus para endorsar mods."))
            return
        import threading

        def run():
            ok = 0
            for m in mods:
                try:
                    self.manager.api.endorse(
                        getattr(m, "game_domain", "") or self.config.game_domain,
                        m.mod_id, True, m.version or "")
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    self.manager.log.emit(
                        tr("No se pudo endorsar «{name}»: {e}").format(name=m.name, e=e))
            if ok:
                self.manager.log.emit(tr("👍 {n} mod(s) endorsados en Nexus.").format(n=ok))
        threading.Thread(target=run, daemon=True).start()
        self.manager.log.emit(tr("👍 Endorsando {n} mod(s)…").format(n=len(mods)))

    def _assign_category_menu(self) -> None:
        """Botón «Categoría»: asigna los mods seleccionados a una categoría."""
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu
        managed = [m for m, ext in self._selected_entries() if not ext]
        if not managed:
            QMessageBox.information(self, tr("Categorías"), tr(
                "Selecciona uno o varios mods (instalados por BMI) para asignarles una categoría."))
            return
        menu = QMenu(self)
        self._fill_category_menu(menu, managed)
        menu.exec(QCursor.pos())

    def _fill_category_menu(self, menu, managed) -> None:
        sub = menu.addMenu(tr("Asignar a categoría"))
        for c in self._existing_categories():
            sub.addAction(c, lambda _=False, cat=c: self._cat_assign(managed, cat))
        sub.addSeparator()
        sub.addAction(tr("➕ Nueva categoría…"), lambda: self._cat_assign_new(managed))
        sub.addAction(tr("✖ Sin categoría"), lambda: self._cat_assign(managed, ""))

    def _cat_assign(self, managed, cat: str) -> None:
        for m in managed:
            real = self.manager.store.get(m.mod_id)
            if real:
                real.category = cat
        self.manager.store.save()
        if cat and cat not in self._cat_order:
            self._cat_order.append(cat)
            self._save_view_state()
        self.refresh()

    def _cat_assign_new(self, managed) -> None:
        name, ok = QInputDialog.getText(self, tr("Nueva categoría"), tr("Nombre de la categoría:"))
        if ok and name.strip():
            self._cat_assign(managed, name.strip())

    def _cat_rename(self, old: str) -> None:
        new, ok = QInputDialog.getText(self, tr("Renombrar categoría"), tr("Nuevo nombre:"), text=old)
        new = new.strip() if ok else ""
        if not new or new == old:
            return
        for m in self.manager.store.all():
            if (m.category or "") == old:
                m.category = new
        self.manager.store.save()
        # dict.fromkeys deduplica: si renombras a una categoría YA existente, se fusionan
        # (antes quedaban dos separadores con el mismo nombre y mods duplicados).
        self._cat_order = list(dict.fromkeys(new if c == old else c for c in self._cat_order))
        if new not in self._cat_order:
            self._cat_order.append(new)
        if old in self._collapsed:
            self._collapsed.discard(old)
            self._collapsed.add(new)
        if old in self._cat_colors:
            self._cat_colors[new] = self._cat_colors.pop(old)
        self._save_view_state()
        self.refresh()

    def _cat_delete(self, cat: str) -> None:
        """Quita la categoría (los mods pasan a «Sin categoría»); NO borra ningún mod."""
        for m in self.manager.store.all():
            if (m.category or "") == cat:
                m.category = ""
        self.manager.store.save()
        self._cat_order = [c for c in self._cat_order if c != cat]
        self._collapsed.discard(cat)
        self._cat_colors.pop(cat, None)
        self._save_view_state()
        self.refresh()

    def _cat_set_color(self, key: str) -> None:
        """Elige un color para el separador de la categoría (se guarda por juego)."""
        from PySide6.QtWidgets import QColorDialog
        cur = QColor(self._cat_colors.get(key) or theme.ACCENT)
        c = QColorDialog.getColor(cur, self, tr("Color de la categoría"))
        if c.isValid():
            self._cat_colors[key] = c.name()
            self._save_view_state()
            self.refresh()

    def _cat_move(self, cat: str, delta: int) -> None:
        order = self._cat_order
        if cat not in order:
            return
        i = order.index(cat)
        j = i + delta
        if 0 <= j < len(order):
            order[i], order[j] = order[j], order[i]
            self._save_view_state()
            self.refresh()

    # ==================================================================
    # Pestaña: Prioridad (orden de sobrescritura de archivos, estilo MO2)
    # ==================================================================
    def _build_priority_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)
        note = QLabel(tr("Arrastra para ordenar los mods. El de ABAJO gana cuando dos mods "
                         "tocan el mismo archivo (texturas, etc.). Agrupa con separadores."))
        note.setProperty("role", "dim"); note.setWordWrap(True)
        v.addWidget(note)
        top = QHBoxLayout(); top.setSpacing(8)
        self.prio_count = QLabel(""); top.addWidget(self.prio_count)
        top.addStretch()
        for text, fn in [(tr("➕ Separador"), self._prio_add_separator),
                         (tr("✏ Renombrar"), self._prio_rename_separator),
                         (tr("🗑 Quitar separador"), self._prio_remove_separator)]:
            b = QPushButton(text); b.clicked.connect(fn); top.addWidget(b)
        v.addLayout(top)
        self.prio_tree = QTreeWidget()
        self.prio_tree.setHeaderHidden(True)
        self.prio_tree.setIndentation(16)
        self.prio_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.prio_tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.prio_tree.model().rowsMoved.connect(lambda *a: self._on_prio_drop())
        v.addWidget(self.prio_tree, 1)
        return w

    def _render_priority(self) -> None:
        if not hasattr(self, "prio_tree"):
            return
        managed = sorted(self.manager.store.all(), key=lambda m: (m.priority, m.name.lower()))
        self._prio_populating = True
        self.prio_tree.blockSignals(True)
        self.prio_tree.clear()
        root = self.prio_tree.invisibleRootItem()
        root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)  # mods solo bajo categorías
        cat_nodes: dict = {}
        n = 0
        for m in managed:
            cat = m.category or ""
            node = cat_nodes.get(cat)
            if node is None:
                node = QTreeWidgetItem([cat if cat else tr("Sin categoría")])
                node.setData(0, Qt.ItemDataRole.UserRole, ("cat", cat))
                node.setFlags(node.flags() | Qt.ItemFlag.ItemIsDropEnabled
                              | Qt.ItemFlag.ItemIsDragEnabled)
                fnt = node.font(0); fnt.setBold(True); node.setFont(0, fnt)
                node.setForeground(0, QColor(theme.ACCENT))
                self.prio_tree.addTopLevelItem(node); node.setExpanded(True)
                cat_nodes[cat] = node
            item = QTreeWidgetItem([m.name])
            item.setData(0, Qt.ItemDataRole.UserRole, ("mod", m.mod_id))
            item.setIcon(0, icons.icon("package", theme.INFO, 15))
            item.setFlags((item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
                          & ~Qt.ItemFlag.ItemIsDropEnabled)
            node.addChild(item)
            n += 1
        self.prio_tree.blockSignals(False)
        self._prio_populating = False
        self.prio_count.setText(tr("{n} mods · el de abajo gana").format(n=n))

    def _on_prio_drop(self) -> None:
        if not self._prio_populating:
            QTimer.singleShot(0, self._apply_priority_from_tree)

    def _apply_priority_from_tree(self) -> None:
        root = self.prio_tree.invisibleRootItem()
        ordered: list[tuple[int, str]] = []
        current = ""
        for i in range(root.childCount()):
            top = root.child(i)
            kind, val = top.data(0, Qt.ItemDataRole.UserRole) or ("", "")
            if kind == "cat":
                current = val
                for j in range(top.childCount()):
                    k2, v2 = top.child(j).data(0, Qt.ItemDataRole.UserRole) or ("", "")
                    if k2 == "mod":
                        ordered.append((v2, val))
            elif kind == "mod":
                ordered.append((val, current))
        ids = []
        for mid, cat in ordered:
            m = self.manager.store.get(mid)
            if m:
                m.category = cat
                ids.append(mid)
        if ids:
            self.manager.installer.reorder_mods(ids, log=self.manager.log.emit)
        self.request_refresh()

    def _prio_selected_mod_ids(self) -> list:
        out = []
        for it in self.prio_tree.selectedItems():
            d = it.data(0, Qt.ItemDataRole.UserRole)
            if d and d[0] == "mod":
                out.append(d[1])
        return out

    def _prio_current_category(self) -> str | None:
        it = self.prio_tree.currentItem()
        d = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        if d and d[0] == "cat" and d[1]:
            return d[1]
        return None

    def _prio_add_separator(self) -> None:
        name, ok = QInputDialog.getText(self, tr("Separador"), tr("Nombre del separador:"))
        if not ok or not name.strip():
            return
        ids = self._prio_selected_mod_ids()
        if not ids:
            QMessageBox.information(self, tr("Separador"),
                tr("Selecciona uno o varios mods y vuelve a pulsar para agruparlos bajo «{name}»."
                   ).format(name=name.strip()))
            return
        for mid in ids:
            m = self.manager.store.get(mid)
            if m:
                m.category = name.strip()
        self.manager.store.save()
        self.refresh()

    def _prio_rename_separator(self) -> None:
        old = self._prio_current_category()
        if not old:
            QMessageBox.information(self, tr("Separador"), tr("Selecciona un separador."))
            return
        new, ok = QInputDialog.getText(self, tr("Renombrar separador"), tr("Nuevo nombre:"), text=old)
        if ok and new.strip():
            for m in self.manager.store.all():
                if m.category == old:
                    m.category = new.strip()
            self.manager.store.save(); self.refresh()

    def _prio_remove_separator(self) -> None:
        old = self._prio_current_category()
        if not old:
            QMessageBox.information(self, tr("Separador"), tr("Selecciona un separador."))
            return
        for m in self.manager.store.all():
            if m.category == old:
                m.category = ""
        self.manager.store.save(); self.refresh()

    # ==================================================================
    # Pestaña: Orden de carga (todos los plugins)
    # ==================================================================
    def _build_order_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        self._order_note = QLabel()
        self._order_note.setProperty("role", "dim"); self._order_note.setWordWrap(True)
        v.addWidget(self._order_note)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.hide_base_cb = QCheckBox(tr("Ocultar vanilla/Creation Club"))
        self.hide_base_cb.setChecked(True)
        self.hide_base_cb.toggled.connect(self._render_order)
        top.addWidget(self.hide_base_cb)
        self.order_count = QLabel("")
        top.addWidget(self.order_count)
        top.addStretch()
        self._order_buttons = []
        for text, fn in [(tr("▲ Subir"), lambda: self._move_plugin(-1)),
                         (tr("▼ Bajar"), lambda: self._move_plugin(1)),
                         (tr("⚡ Auto-ordenar"), self._auto_sort)]:
            b = QPushButton(text); b.clicked.connect(fn); top.addWidget(b)
            self._order_buttons.append(b)
        crash_btn = QPushButton(tr("🩺 Analizar último crash"))
        crash_btn.setToolTip(tr("Lee el crash log más reciente y señala el mod culpable probable"))
        crash_btn.clicked.connect(self._analyze_crash)
        top.addWidget(crash_btn)
        loot_btn = QPushButton(tr("🔃 Ordenar con LOOT"))
        loot_btn.setToolTip(tr("Descarga el masterlist de LOOT y ordena tus plugins (masters "
                               "primero + grupos + reglas), con aviso de plugins sucios"))
        loot_btn.clicked.connect(self._sort_with_loot)
        top.addWidget(loot_btn)
        self._loot_btn = loot_btn
        undo_btn = QPushButton(tr("↩ Deshacer orden"))
        undo_btn.setToolTip(tr("Restaura el orden de carga anterior a la última ordenación"))
        undo_btn.clicked.connect(self._undo_load_order)
        top.addWidget(undo_btn)
        v.addLayout(top)

        self.order_list = QListWidget()
        self.order_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.order_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.order_list.itemChanged.connect(self._on_order_item_changed)
        self.order_list.model().rowsMoved.connect(lambda *_: self._write_order())
        v.addWidget(self.order_list, 1)
        return w

    def _update_order_mode(self) -> None:
        g = self.config.game()
        if not g.uses_plugins_txt:
            self._order_reorderable = self._order_togglable = False
            self._order_note.setText(
                f"{g.name} no usa plugins.txt para el orden de carga (usa otro sistema). "
                "El orden no se gestiona aquí."
            )
        elif not g.star_prefix:
            self._order_reorderable = False
            self._order_togglable = True
            self._order_note.setText(
                f"{g.name} ordena por fecha de archivo (timestamps); el REORDEN aquí no "
                "afecta al orden de carga. Sí puedes activar/desactivar plugins."
            )
        else:
            self._order_reorderable = self._order_togglable = True
            self._order_note.setText(
                tr("Arrastra para reordenar o usa Subir/Bajar. La casilla activa/desactiva. "
                   "'Auto-ordenar' pone los masters primero.")
            )
        for b in self._order_buttons:
            b.setEnabled(self._order_reorderable)
        self.order_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove if self._order_reorderable
            else QAbstractItemView.DragDropMode.NoDragDrop
        )

    def _render_order(self) -> None:
        # IMPORTANTE: los plugins vanilla NUNCA están en plugins.txt -> se excluyen.
        # Los CC (cc*) SÍ están en plugins.txt y deben conservar su posición, así que se
        # mantienen en el modelo (para escribir el orden completo) y solo se OCULTAN
        # visualmente cuando el filtro está activo. De lo contrario, reordenar relegaría
        # los masters CC al final del archivo.
        hide_base = self.hide_base_cb.isChecked()
        present = {m.name.lower() for m in self._scan_cache}  # plugins detectados en Data
        self._populating = True
        self.order_list.blockSignals(True)
        self.order_list.clear()
        shown = warn_n = 0
        for m in self._scan_cache:
            if m.category == "vanilla":
                continue
            hdr = self._plugin_header(m.name)
            kind = esp.plugin_kind(m.name, hdr)
            missing = [mm for mm in (hdr.get("masters", []) if hdr else []) if mm.lower() not in present]
            tag = f"   [{kind}]" if kind in ("ESM", "ESL") else ""
            recs = ""
            if hdr and hdr.get("num_records") is not None:
                recs = "   ·   {:,} reg".format(hdr["num_records"]).replace(",", ".")
            warn = "   ⚠ falta master" if missing else ""
            it = QListWidgetItem(m.name + tag + recs + warn)
            it.setData(Qt.ItemDataRole.UserRole, m.name)
            it.setIcon(icons.icon("plugin", _KIND_COLOR.get(kind, theme.ACCENT), 15))
            if self._order_togglable:
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if m.enabled else Qt.CheckState.Unchecked)
            tip = [tr("Tipo: {k}").format(k=kind)]
            if hdr and hdr.get("masters"):
                tip.append(tr("Requiere: ") + ", ".join(hdr["masters"]))
            if hdr and hdr.get("author"):
                tip.append(tr("Autor: ") + hdr["author"])
            if missing:
                tip.append("⚠ " + tr("Masters que faltan en Data: ") + ", ".join(missing))
                it.setForeground(QColor(theme.DANGER))
                warn_n += 1
            else:
                color = self._CAT_COLOR.get(m.category)
                if color and m.category != "externo":
                    it.setForeground(QColor(color))
            it.setToolTip("\n".join(tip))
            self.order_list.addItem(it)
            if hide_base and m.category == "cc":
                it.setHidden(True)   # sigue en el modelo, no se ve
            else:
                shown += 1
        self.order_list.blockSignals(False)
        self._populating = False
        msg = tr("{n} plugins").format(n=shown)
        if warn_n:
            msg += tr("   ·   ⚠ {w} con masters que faltan").format(w=warn_n)
        self.order_count.setText(msg)

    def _plugin_header(self, name: str):
        """Cabecera del plugin (cacheada por mtime). None si no se puede leer."""
        if not self.config.game_data_path:
            return None
        p = Path(self.config.game_data_path) / name
        try:
            mt = p.stat().st_mtime
        except OSError:
            return None
        cached = self._esp_cache.get(name)
        if cached and cached[0] == mt:
            return cached[1]
        hdr = esp.safe_read_header(str(p))
        self._esp_cache[name] = (mt, hdr)
        return hdr

    def _on_order_item_changed(self, item: QListWidgetItem) -> None:
        if self._populating or not self.config.plugins_txt_path or not self._order_togglable:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        enabled = item.checkState() == Qt.CheckState.Checked
        scanner.set_plugin_enabled(self.config.plugins_txt_path, name, enabled,
                                   star_prefix=self.config.game().star_prefix)
        self.manager.log.emit(f"Plugin {'activado' if enabled else 'desactivado'}: {name}")

    def _move_plugin(self, delta: int) -> None:
        row = self.order_list.currentRow()
        if row < 0:
            return
        new = row + delta
        if not (0 <= new < self.order_list.count()):
            return
        it = self.order_list.takeItem(row)
        self.order_list.insertItem(new, it)
        self.order_list.setCurrentRow(new)
        self._write_order()

    def _write_order(self) -> None:
        if self._populating or not self.config.plugins_txt_path or not self._order_reorderable:
            return
        names = [self.order_list.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(self.order_list.count())]
        scanner.write_load_order(self.config.plugins_txt_path, names,
                                 star_prefix=self.config.game().star_prefix)

    def _auto_sort(self) -> None:
        if not self.config.plugins_txt_path or not self._order_reorderable:
            return
        # scan_installed ya devuelve orden efectivo (masters primero).
        ordered = [m.name for m in self._scan_cache if m.category not in ("vanilla",)]
        scanner.write_load_order(self.config.plugins_txt_path, ordered,
                                 star_prefix=self.config.game().star_prefix)
        self.manager.log.emit("Orden de carga aplicado (masters primero).")
        self.refresh()

    def _analyze_crash(self) -> None:
        """Busca el crash log y lanza el análisis en un WORKER (el mapeo DLL→mod hace rglob
        sobre las carpetas de mods → pesado; no debe correr en el hilo de la GUI)."""
        from .. import crashlog
        p = crashlog.find_latest_crashlog(self.config)
        if not p:
            QMessageBox.information(self, tr("Analizar crash"), tr(
                "No encontré ningún crash log. Instala «Crash Logger SSE» (o «.NET Script "
                "Framework») y reproduce el fallo: su .txt aparece en Documentos/My Games/"
                "<juego>/SKSE/Crashlogs."))
            return
        try:
            text = Path(p).read_text(encoding="utf-8", errors="ignore")
        except OSError as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Analizar crash"),
                                tr("No se pudo leer el crash log: {e}").format(e=e))
            return
        self.manager.log.emit(tr("🩺 Analizando el crash log…"))
        import threading
        cfg, store, path = self.config, self.manager.store, str(p)

        def work():
            try:
                rep = crashlog.parse_and_analyze(cfg, store, text, path)
            except Exception:  # noqa: BLE001
                rep = None
            self._crash_ready.emit(rep)
        threading.Thread(target=work, daemon=True).start()

    def _show_crash_report(self, rep) -> None:
        if rep is None:
            QMessageBox.warning(self, tr("Analizar crash"),
                                tr("No se pudo analizar el crash log."))
            return
        out = [tr("Crash log: {p}").format(p=Path(rep.path).name)]
        if rep.exception:
            out.append(rep.exception)
        out.append("")
        if rep.culprit_mods:
            out.append(tr("Culpable(s) PROBABLE(s) — DLL de un mod en la pila del crash:"))
            out += [f"   • {dll}  →  {mod}" for dll, mod in rep.culprit_mods]
        elif rep.suspect_dlls:
            out.append(tr("DLL en la pila del crash (revisa los mods que los traen):"))
            out += [f"   • {d}" for d in rep.suspect_dlls[:10]]
        else:
            out.append(tr("No se identificó un DLL de mod claro en la pila (puede ser un "
                          "conflicto de plugins o falta de memoria)."))
        out += ["", tr("⚠ Es una pista PROBABLE, no una certeza. {n} plugins en el log.")
                .format(n=len(rep.plugins))]
        QMessageBox.information(self, tr("Análisis del crash"), "\n".join(out))

    # --- Ordenación con LOOT ------------------------------------------
    def _loot_backup_path(self) -> Path:
        from ..config import app_data_dir
        return Path(app_data_dir()) / f"loadorder_{self.config.game_domain}.bak"

    def _snapshot_load_order(self) -> bool:
        """Copia plugins.txt al .bak (para «Deshacer orden»). Devuelve True si lo consiguió.
        Si falla, INVALIDA el .bak anterior (obsoleto) para no restaurar un orden equivocado."""
        import shutil
        src = self.config.plugins_txt_path
        bak = self._loot_backup_path()
        if not (src and Path(src).is_file()):
            return False
        try:
            shutil.copy2(src, bak)
            return True
        except OSError:
            try:                                  # el .bak previo ya no corresponde: retíralo
                if bak.is_file():
                    bak.unlink()
            except OSError:
                pass
            return False

    def _undo_load_order(self) -> None:
        import shutil
        bak = self._loot_backup_path()
        if not bak.is_file() or not self.config.plugins_txt_path:
            QMessageBox.information(self, tr("Deshacer orden"),
                                   tr("No hay ningún orden de carga anterior guardado."))
            return
        try:
            shutil.copy2(bak, self.config.plugins_txt_path)
        except OSError as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Deshacer orden"), str(e))
            return
        self.manager.log.emit(tr("↩ Orden de carga anterior restaurado."))
        self.refresh()

    def _sort_with_loot(self) -> None:
        from .. import loot
        if not (self.config.plugins_txt_path and self.config.game_data_path):
            QMessageBox.information(self, tr("Ordenar con LOOT"),
                                   tr("Configura la carpeta Data y plugins.txt en Ajustes."))
            return
        dom = self.config.game().domain
        if not loot.repo_for(dom):
            QMessageBox.information(self, tr("Ordenar con LOOT"),
                                   tr("LOOT no tiene masterlist para este juego."))
            return
        # Las cabeceras (leen disco + mutan self._esp_cache) se preparan AQUÍ, en el hilo de la
        # GUI; la descarga+parseo+ordenación pesados van a un worker para no congelar la UI.
        current = [m.name for m in self._scan_cache if m.category not in ("vanilla",)]
        if not current:
            QMessageBox.information(self, tr("Ordenar con LOOT"),
                                   tr("No hay plugins que ordenar."))
            return
        infos = []
        for i, name in enumerate(current):
            hdr = self._plugin_header(name) or {}
            infos.append(loot.PluginInfo(
                name=name, is_master=bool(hdr.get("is_master")),
                masters=list(hdr.get("masters") or []), index=i))
        implicit = list(self.config.game().implicit_masters)   # DLC vanilla → no marcar como "falta"
        self.manager.log.emit(tr("🔃 Descargando el masterlist de LOOT…"))
        if hasattr(self, "_loot_btn"):
            self._loot_btn.setEnabled(False)   # evita relanzar y señala que está trabajando
        import threading

        def work():
            res = {"ordered": None, "note": "", "warnings": [], "error": "", "dom": dom}
            try:
                txt = loot.download_masterlist(dom, force=True)
                if not txt:
                    res["error"] = "download"
                else:
                    ml = loot.parse_masterlist(txt)
                    res["ordered"], res["note"] = loot.sort_plugins(infos, ml)
                    res["warnings"] = loot.warnings_for(current, ml, present_extra=implicit)
            except Exception as e:  # noqa: BLE001
                res["error"] = str(e) or "parse"
            self._loot_ready.emit(res)
        threading.Thread(target=work, daemon=True).start()

    def _apply_loot(self, res) -> None:
        """Slot en el hilo de la GUI: escribe el orden (con red de seguridad) y muestra avisos."""
        from .. import scanner
        if hasattr(self, "_loot_btn"):
            self._loot_btn.setEnabled(True)
        # A-2: si CAMBIASTE de juego mientras se descargaba/ordenaba el masterlist, NO escribir
        # (sería el plugins.txt del juego equivocado).
        if res.get("dom") and self.config.game().domain != res.get("dom"):
            self.manager.log.emit(tr("🔃 LOOT: cambiaste de juego; el orden se descartó."))
            return
        if res.get("error") == "download":
            QMessageBox.warning(self, tr("Ordenar con LOOT"),
                                tr("No se pudo obtener el masterlist de LOOT (¿sin conexión?)."))
            return
        if res.get("error"):
            QMessageBox.warning(self, tr("Ordenar con LOOT"),
                                tr("No se pudo leer el masterlist: {e}").format(e=res["error"]))
            return
        ordered = res.get("ordered")
        if ordered is None:                       # ciclo: NO tocar nada
            QMessageBox.warning(self, tr("Ordenar con LOOT"), res.get("note") or "")
            return
        snapshot_ok = self._snapshot_load_order()
        try:
            scanner.write_load_order(self.config.plugins_txt_path, ordered,
                                     star_prefix=self.config.game().star_prefix)
        except OSError as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Ordenar con LOOT"),
                                tr("No se pudo escribir plugins.txt: {e}").format(e=e))
            return
        if snapshot_ok:
            self.manager.log.emit(tr("🔃 Orden estilo LOOT aplicado ({n} plugins). Usa «Deshacer "
                                     "orden» para revertir.").format(n=len(ordered)))
        else:
            self.manager.log.emit(tr("🔃 Orden estilo LOOT aplicado ({n} plugins).")
                                  .format(n=len(ordered)))
        self._show_loot_warnings(res.get("warnings") or [])
        self.refresh()

    def _show_loot_warnings(self, warns) -> None:
        if not warns:
            self.manager.log.emit(tr("LOOT: sin avisos para tus plugins."))
            return
        dirty = [w for w in warns if w.kind == "dirty"]
        inc = [w for w in warns if w.kind == "inc"]
        req = [w for w in warns if w.kind == "req"]
        msg = [w for w in warns if w.kind == "msg"]
        out: list[str] = []
        if dirty:
            out.append(tr("🧹 Plugins sucios ({n}) — límpialos con xEdit QuickAutoClean:")
                       .format(n=len(dirty)))
            out += [f"   • {w.plugin}" for w in dirty[:20]]
        if inc:
            out += ["", tr("⛔ Incompatibilidades detectadas:")]
            out += [tr("   • «{a}» es incompatible con «{b}»").format(a=w.plugin, b=w.detail)
                    for w in inc[:20]]
        if req:
            out += ["", tr("🔗 Requisitos que faltan:")]
            out += [tr("   • «{a}» requiere «{b}» (no activo)").format(a=w.plugin, b=w.detail)
                    for w in req[:20]]
        if msg:
            out += ["", tr("💬 Mensajes de LOOT:")]
            out += [f"   • {w.plugin}: {w.detail}" for w in msg[:12]]
        QMessageBox.information(self, tr("Avisos de LOOT"), "\n".join(out))

    # ==================================================================
    # Pestaña: Conflictos
    # ==================================================================
    def _build_conflicts_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.conflict_lbl = QLabel("")
        self.conflict_lbl.setProperty("role", "dim")
        bar.addWidget(self.conflict_lbl)
        bar.addStretch()
        b = QPushButton(tr("🔄 Analizar"))
        b.clicked.connect(self._refresh_conflicts)
        bar.addWidget(b)
        v.addLayout(bar)
        self.conflict_table = QTableWidget(0, 3)
        self.conflict_table.setHorizontalHeaderLabels(
            [tr("Archivo (relativo a Data)"), tr("Mods en conflicto"), tr("Gana")])
        self.conflict_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.conflict_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.conflict_table.verticalHeader().setVisible(False)
        self.conflict_table.verticalHeader().setDefaultSectionSize(30)
        self.conflict_table.setAlternatingRowColors(True)
        self.conflict_table.setShowGrid(False)
        theme.make_columns_movable(self.conflict_table)   # arrastrables + redimensionables
        self.conflict_table.setColumnWidth(1, 260)        # «Mods en conflicto» ancho por defecto
        self.conflict_table.setColumnWidth(2, 220)        # «Gana»
        v.addWidget(self.conflict_table, 1)
        return w

    def _refresh_conflicts(self) -> None:
        items = conflicts.find_conflicts(self.manager.store.all())
        self.conflict_table.setRowCount(0)
        for c in items:
            row = self.conflict_table.rowCount()
            self.conflict_table.insertRow(row)
            it0 = QTableWidgetItem(c.rel_path); it0.setToolTip(c.rel_path)
            self.conflict_table.setItem(row, 0, it0)
            mods_txt = " ▸ ".join(c.mods)
            it1 = QTableWidgetItem(mods_txt); it1.setToolTip(mods_txt)
            self.conflict_table.setItem(row, 1, it1)
            win = QTableWidgetItem(c.winner); win.setToolTip(c.winner)
            win.setForeground(QColor(theme.SUCCESS))
            self.conflict_table.setItem(row, 2, win)
        if items:
            self.conflict_lbl.setText(tr("⚠ {n} archivo(s) en conflicto").format(n=len(items)))
        else:
            self.conflict_lbl.setText(tr("✅ Sin conflictos entre los mods gestionados"))

    # ==================================================================
    # Pestaña: Perfiles
    # ==================================================================
    def _build_profiles_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)
        info = QLabel(tr("Un perfil guarda tu plugins.txt (orden + plugins activos) Y el estado "
                         "de tus mods (activos, prioridad, separadores). Cambia entre "
                         "configuraciones completas sin perderlas."))
        info.setProperty("role", "dim"); info.setWordWrap(True)
        v.addWidget(info)
        self.profile_list = QListWidget()
        v.addWidget(self.profile_list, 1)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        for text, fn in [(tr("➕ Crear desde actual"), self._profile_create),
                         (tr("✅ Aplicar"), self._profile_apply),
                         (tr("✏ Renombrar"), self._profile_rename),
                         (tr("🗑 Borrar"), self._profile_delete)]:
            b = QPushButton(text); b.clicked.connect(fn); bar.addWidget(b)
        v.addLayout(bar)
        return w

    def _refresh_profiles(self) -> None:
        self.profile_list.clear()
        for p in self.profiles.list():
            label = p.name + ("   (activo)" if p.name == self.config.current_profile else "")
            self.profile_list.addItem(label)

    def _selected_profile(self) -> str:
        it = self.profile_list.currentItem()
        if not it:
            return ""
        return it.text().replace("   (activo)", "").strip()

    def _profile_create(self) -> None:
        if not self.config.plugins_txt_path:
            QMessageBox.warning(self, "plugins.txt", tr("Configura plugins.txt en Ajustes."))
            return
        name, ok = QInputDialog.getText(self, tr("Nuevo perfil"), tr("Nombre del perfil:"))
        if not ok or not name.strip():
            return
        if self.profiles.exists(name.strip()) and QMessageBox.question(
            self, tr("Nuevo perfil"), tr("Ya existe un perfil con ese nombre. ¿Sobrescribirlo?")
        ) != QMessageBox.StandardButton.Yes:
            return
        prof = self.profiles.save_from(name.strip(), self.config.plugins_txt_path,
                                       mods=self.manager.store.all())
        self.config.current_profile = prof.name   # nombre saneado (= en disco)
        self.config.save()
        self._refresh_profiles()

    def _profile_apply(self) -> None:
        name = self._selected_profile()
        if not name or not self.config.plugins_txt_path:
            return
        if self.profiles.apply_to(name, self.config.plugins_txt_path):
            self._restore_profile_mods(name)
            self.config.current_profile = name
            self.config.save()
            self.manager.log.emit(f"Perfil aplicado: {name}")
            self.refresh()

    def _restore_profile_mods(self, name: str) -> None:
        """Restaura el estado de los mods (activado, prioridad, categoría) guardado en el
        perfil, re-desplegando lo necesario. Perfiles antiguos (sin .json) se ignoran."""
        state = self.profiles.mod_state(name)
        if not state:
            return
        inst = self.manager.installer
        for mid_str, st in state.items():
            try:
                mid = int(mid_str)
            except (TypeError, ValueError):
                continue
            m = self.manager.store.get(mid)
            if not m:
                continue
            m.priority = int(st.get("priority", m.priority))
            m.category = st.get("category", m.category) or ""
            want = bool(st.get("enabled", m.enabled))
            if want != m.enabled:
                inst.set_mod_enabled(mid, want, log=self.manager.log.emit)
        self.manager.store.save()
        inst.apply_priority_order(log=self.manager.log.emit)

    def _profile_rename(self) -> None:
        name = self._selected_profile()
        if not name:
            return
        new, ok = QInputDialog.getText(self, tr("Renombrar perfil"), tr("Nuevo nombre:"), text=name)
        if ok and new.strip() and self.profiles.rename(name, new.strip()):
            if self.config.current_profile == name:
                self.config.current_profile = safe_name(new.strip()); self.config.save()
            self._refresh_profiles()

    def _profile_delete(self) -> None:
        name = self._selected_profile()
        if not name:
            return
        if QMessageBox.question(self, tr("Borrar perfil"),
                                tr("¿Borrar el perfil '{name}'?").format(name=name)) \
                == QMessageBox.StandardButton.Yes:
            self.profiles.delete(name)
            self._refresh_profiles()

    # ==================================================================
    def refresh(self) -> None:
        # 1) Sincroniza con la «Carpeta de mods» (estilo MO2) ANTES de escanear Data: añade
        #    los mods metidos a mano y quita los que ya no estén. Si se escanea antes, el
        #    escáner ve el estado viejo y puede pintar filas fantasma un refresco más.
        try:
            self.manager.import_external_mods()
        except Exception:  # noqa: BLE001
            pass
        # 2) Escanea Data y pinta.
        self._scan_cache = scanner.scan_installed(
            self.config.game_data_path, self.config.plugins_txt_path,
            {p for m in self.manager.store.all() for p in m.plugins},
            game=self.config.game(),
        )
        self._refresh_mods()
        self._render_priority()
        self._update_order_mode()
        self._render_order()
        self._refresh_conflicts()
        self._refresh_profiles()

    def request_refresh(self) -> None:
        if self._refresh_scheduled:
            return
        self._refresh_scheduled = True
        QTimer.singleShot(500, self._do_scheduled_refresh)

    def _do_scheduled_refresh(self) -> None:
        self._refresh_scheduled = False
        self.refresh()
