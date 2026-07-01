"""Gestor de mods: pestañas Mods · Orden de carga · Conflictos · Perfiles."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem,
    QListWidget, QListWidgetItem, QLineEdit, QPushButton, QLabel, QCheckBox,
    QHeaderView, QAbstractItemView, QMessageBox, QInputDialog, QTreeWidget, QTreeWidgetItem,
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


def _centered(widget: QWidget) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addStretch()
    lay.addWidget(widget)
    lay.addStretch()
    return w


class ModsPanel(QWidget):
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
        v.addLayout(top)

        self.mods_table = QTableWidget(0, 5)
        self.mods_table.setHorizontalHeaderLabels(
            [tr("Activo"), tr("Mod"), tr("Versión"), tr("Plugins"), tr("Tamaño")])
        self.mods_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mods_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.mods_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.mods_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.mods_table.verticalHeader().setVisible(False)
        self.mods_table.verticalHeader().setDefaultSectionSize(32)
        self.mods_table.setAlternatingRowColors(True)
        self.mods_table.setShowGrid(False)
        self.mods_table.setIconSize(QSize(26, 26))
        self.mods_table.doubleClicked.connect(lambda *_: self._details_selected())
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
        uninstall = QPushButton(tr("Desinstalar"))
        uninstall.setProperty("variant", "danger")
        uninstall.clicked.connect(self._bulk_uninstall)
        bottom.addWidget(uninstall)
        v.addLayout(bottom)
        return w

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

        # (mod, es_externo) por fila
        self._row_mods = [(m, False) for m in managed] + [(m, True) for m in external]
        self._populating = True
        self.mods_table.setRowCount(0)
        for mod, is_ext in self._row_mods:
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
            self.mods_table.setItem(row, 2, QTableWidgetItem(mod.version or "—"))
            self.mods_table.setItem(row, 3, QTableWidgetItem(", ".join(mod.plugins) or "—"))
            self.mods_table.setItem(row, 4,
                QTableWidgetItem("—" if is_ext else human_size(mod.size_bytes)))
        self._populating = False
        self._filter_mods()

    def _translate_all(self) -> None:
        """Busca y encola la traducción al idioma de la app de todos los mods instalados."""
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
            tr("BMI buscará en Nexus la traducción al idioma de la app de tus {n} mods "
               "instalados y encolará las que encuentre.\n\nCon cuenta gratuita, cada "
               "traducción pedirá un clic en la web (aparecerán en Descargas como «Requiere "
               "clic en web»). ¿Continuar?").format(n=len(mods)))
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.manager.translate_installed_mods()
        QMessageBox.information(
            self, tr("Traducir mis mods"),
            tr("Buscando traducciones en segundo plano. Míralo en el registro y en la "
               "pestaña Descargas."))

    def _filter_mods(self) -> None:
        term = self.search_edit.text().lower().strip()
        shown = 0
        for row in range(self.mods_table.rowCount()):
            name = self.mods_table.item(row, 1).text().lower()
            hide = bool(term) and term not in name
            self.mods_table.setRowHidden(row, hide)
            if not hide:
                shown += 1
        self.sel_label.setText(tr("{n} mod(s)").format(n=shown))

    def _selected_entries(self) -> list[tuple[InstalledMod, bool]]:
        """Devuelve [(mod, es_externo), ...] de las filas seleccionadas."""
        out = []
        for idx in self.mods_table.selectionModel().selectedRows():
            r = idx.row()
            if 0 <= r < len(self._row_mods):
                out.append(self._row_mods[r])
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
        managed = [m for m, ext in ents if not ext]
        n_ext = sum(1 for _, ext in ents if ext)
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
        if n_ext:
            self.manager.log.emit(
                f"{n_ext} mod(s) externo(s) no se desinstalan (no los instaló BMI).")
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
        v.addWidget(self.conflict_table, 1)
        return w

    def _refresh_conflicts(self) -> None:
        items = conflicts.find_conflicts(self.manager.store.all())
        self.conflict_table.setRowCount(0)
        for c in items:
            row = self.conflict_table.rowCount()
            self.conflict_table.insertRow(row)
            self.conflict_table.setItem(row, 0, QTableWidgetItem(c.rel_path))
            self.conflict_table.setItem(row, 1, QTableWidgetItem(" ▸ ".join(c.mods)))
            win = QTableWidgetItem(c.winner)
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
