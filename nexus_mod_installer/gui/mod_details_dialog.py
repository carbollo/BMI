"""Diálogo de detalles de un mod: info, árbol de archivos (ocultar/mostrar, editar .ini), notas."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QListWidget, QTabWidget, QWidget, QTreeWidget, QTreeWidgetItem,
    QTreeWidgetItemIterator, QPlainTextEdit, QDialogButtonBox, QMessageBox,
)

from ..models import InstalledMod
from ..i18n import tr
from . import theme, icons

_REL = Qt.ItemDataRole.UserRole  # ruta relativa guardada en cada hoja del árbol


def human_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.1f} {u}" if u != "B" else f"{int(f)} {u}"
        f /= 1024
    return f"{n} B"


def mod_page_url(mod: InstalledMod) -> str:
    return f"https://www.nexusmods.com/{mod.game_domain}/mods/{mod.mod_id}"


class _IniEditor(QDialog):
    """Editor de texto simple para un archivo .ini del mod."""

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(tr("Editar: {name}").format(name=path.name))
        self.resize(640, 520)
        v = QVBoxLayout(self)
        self.edit = QPlainTextEdit()
        try:
            self.edit.setPlainText(path.read_text(encoding="utf-8-sig", errors="replace"))
        except OSError as e:
            self.edit.setPlainText(f"; No se pudo leer el archivo: {e}")
        v.addWidget(self.edit, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _save(self) -> None:
        try:
            self.path.write_text(self.edit.toPlainText(), encoding="utf-8")
            self.accept()
        except OSError as e:
            QMessageBox.warning(self, tr("Editar"), tr("No se pudo guardar: {e}").format(e=e))


class ModDetailsDialog(QDialog):
    def __init__(self, mod: InstalledMod, parent=None, store=None, installer=None):
        super().__init__(parent)
        self.mod = mod
        self.store = store
        self.installer = installer
        self.setWindowTitle(tr("Detalles: {name}").format(name=mod.name))
        self.resize(580, 580)

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        header.setSpacing(12)
        if getattr(mod, "picture_url", ""):
            from .images import ThumbLabel
            header.addWidget(ThumbLabel(mod.picture_url, 72), 0, Qt.AlignmentFlag.AlignTop)
        title = QLabel(mod.name)
        title.setProperty("role", "title")
        title.setWordWrap(True)
        header.addWidget(title, 1)
        layout.addLayout(header)

        form = QFormLayout()
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(mod.installed_at)) if mod.installed_at else "—"
        form.addRow(tr("Estado:"), QLabel(tr("Activado") if mod.enabled else tr("Desactivado")))
        form.addRow(tr("Versión:"), QLabel(mod.version or "—"))
        form.addRow(tr("ID de Nexus:"), QLabel(str(mod.mod_id) if mod.mod_id > 0 else "—"))
        form.addRow(tr("Instalado:"), QLabel(when))
        form.addRow(tr("Tamaño:"), QLabel(human_size(mod.size_bytes)))
        form.addRow(tr("Plugins:"), QLabel(", ".join(mod.plugins) or "—"))
        layout.addLayout(form)

        tabs = QTabWidget()

        plugins_list = QListWidget()
        for pl in (mod.plugins or []):
            plugins_list.addItem(pl)
        if not mod.plugins:
            plugins_list.addItem(tr("(sin plugins)"))
        for i in range(plugins_list.count()):
            plugins_list.item(i).setIcon(icons.icon("plugin", theme.ACCENT, 15))
        tabs.addTab(plugins_list, tr("Plugins ({n})").format(n=len(mod.plugins)))

        # Archivos: árbol con casillas (desmarca para ocultar) + doble clic en .ini para editar.
        files_tab = QWidget()
        fv = QVBoxLayout(files_tab)
        fv.setContentsMargins(0, 0, 0, 0)
        hint = QLabel(tr("Desmarca un archivo para ocultarlo (no se desplegará). "
                         "Doble clic en un .ini para editarlo."))
        hint.setProperty("role", "dim"); hint.setWordWrap(True)
        fv.addWidget(hint)
        self._can_hide = bool(installer) and mod.mod_id > 0
        self.file_tree = self._make_file_tree(mod.deployed_files, set(mod.hidden_files or []))
        self.file_tree.itemDoubleClicked.connect(self._on_file_dclick)
        fv.addWidget(self.file_tree, 1)
        tabs.addTab(files_tab, tr("Archivos ({n})").format(n=len(mod.deployed_files)))

        self.notes_edit = QPlainTextEdit(mod.notes or "")
        self.notes_edit.setPlaceholderText(
            tr("Escribe aquí configuraciones, detalles de instalación, recordatorios…"))
        self.notes_edit.setReadOnly(not (bool(store) and mod.mod_id > 0))
        tabs.addTab(self.notes_edit, tr("Notas"))
        layout.addWidget(tabs, 1)

        btns = QHBoxLayout()
        if mod.mod_id > 0:
            page_btn = QPushButton(tr("Abrir en Nexus"))
            page_btn.setIcon(icons.icon("search", theme.TEXT))
            page_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(mod_page_url(mod))))
            btns.addWidget(page_btn)
        folder_btn = QPushButton(tr("Abrir carpeta"))
        folder_btn.setIcon(icons.icon("folder", theme.TEXT))
        folder_btn.clicked.connect(self._open_folder)
        folder_btn.setEnabled(bool(mod.install_dir) and Path(mod.install_dir).exists())
        btns.addWidget(folder_btn)
        btns.addStretch()
        close_btn = QPushButton(tr("Cerrar"))
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

    # ------------------------------------------------------------------
    def _make_file_tree(self, files: list[str], hidden: set[str]) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        nodes: dict[tuple, QTreeWidgetItem] = {}
        for rel in sorted(files, key=str.lower)[:6000]:
            norm = rel.replace("\\", "/")
            parts = [p for p in norm.split("/") if p]
            accum: tuple = ()
            parent_item = None
            for i, part in enumerate(parts):
                accum = accum + (part.lower(),)
                node = nodes.get(accum)
                if node is None:
                    node = (QTreeWidgetItem([part]) if parent_item is None
                            else QTreeWidgetItem(parent_item, [part]))
                    if parent_item is None:
                        tree.addTopLevelItem(node)
                    is_file = (i == len(parts) - 1)
                    if is_file:
                        node.setIcon(0, icons.file_icon(part, 16))
                        node.setData(0, _REL, norm)
                        if self._can_hide:
                            node.setFlags(node.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                            node.setCheckState(0, Qt.CheckState.Unchecked if norm in hidden
                                               else Qt.CheckState.Checked)
                    else:
                        node.setIcon(0, icons.icon("folder", theme.ACCENT, 16))
                        if self._can_hide:
                            node.setFlags(node.flags() | Qt.ItemFlag.ItemIsUserCheckable
                                          | Qt.ItemFlag.ItemIsAutoTristate)
                    nodes[accum] = node
                parent_item = node
        if self._can_hide:
            self._init_folder_states(tree)
        return tree

    def _init_folder_states(self, tree: QTreeWidget) -> None:
        """Fija el estado inicial de las carpetas (marcado/parcial/sin marcar) según sus hijos."""
        def state(item: QTreeWidgetItem) -> Qt.CheckState:
            if item.data(0, _REL) is not None:        # hoja
                return item.checkState(0)
            checked = unchecked = 0
            for i in range(item.childCount()):
                st = state(item.child(i))
                if st == Qt.CheckState.Checked:
                    checked += 1
                elif st == Qt.CheckState.Unchecked:
                    unchecked += 1
                else:
                    checked = unchecked = -99
            if checked and not unchecked:
                s = Qt.CheckState.Checked
            elif unchecked and not checked:
                s = Qt.CheckState.Unchecked
            else:
                s = Qt.CheckState.PartiallyChecked
            item.setCheckState(0, s)
            return s
        for i in range(tree.topLevelItemCount()):
            state(tree.topLevelItem(i))

    def _collect_hidden(self) -> list[str]:
        hidden = []
        it = QTreeWidgetItemIterator(self.file_tree)
        while it.value():
            item = it.value()
            rel = item.data(0, _REL)
            if rel is not None and item.checkState(0) == Qt.CheckState.Unchecked:
                hidden.append(rel)
            it += 1
        return hidden

    def _on_file_dclick(self, item: QTreeWidgetItem, _col: int) -> None:
        rel = item.data(0, _REL)
        if not rel or not str(rel).lower().endswith(".ini"):
            return
        if not self.mod.install_dir:
            return
        path = Path(self.mod.install_dir) / rel
        if not path.is_file():
            QMessageBox.information(self, tr("Editar"),
                                    tr("No se encontró el archivo en la carpeta del mod."))
            return
        if _IniEditor(path, self).exec() and self.installer:
            self.installer.redeploy_file(self.mod.mod_id, str(rel))

    # ------------------------------------------------------------------
    def accept(self) -> None:
        if self.store and self.mod.mod_id > 0 and not self.notes_edit.isReadOnly():
            new = self.notes_edit.toPlainText()
            if new != (self.mod.notes or ""):
                self.mod.notes = new
                try:
                    self.store.save()
                except Exception:  # noqa: BLE001
                    pass
        if self._can_hide:
            hidden = self._collect_hidden()
            if sorted(hidden) != sorted(self.mod.hidden_files or []):
                self.installer.set_hidden_files(self.mod.mod_id, hidden)
        super().accept()

    def _open_folder(self) -> None:
        if self.mod.install_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.mod.install_dir))
