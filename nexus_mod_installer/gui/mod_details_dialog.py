"""Diálogo de detalles de un mod instalado: info, árbol de archivos, notas."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QListWidget, QTabWidget, QWidget, QTreeWidget, QTreeWidgetItem, QPlainTextEdit,
)

from ..models import InstalledMod
from ..i18n import tr
from . import theme, icons


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


def _build_file_tree(files: list[str]) -> QTreeWidget:
    """Árbol de carpetas/archivos del mod con iconos por tipo."""
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    tree.setIconSize(tree.iconSize())
    nodes: dict[tuple, QTreeWidgetItem] = {}
    for rel in sorted(files, key=str.lower)[:5000]:
        parts = [p for p in rel.replace("\\", "/").split("/") if p]
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
                if i == len(parts) - 1:
                    node.setIcon(0, icons.file_icon(part, 16))
                else:
                    node.setIcon(0, icons.icon("folder", theme.ACCENT, 16))
                nodes[accum] = node
            parent_item = node
    return tree


class ModDetailsDialog(QDialog):
    def __init__(self, mod: InstalledMod, parent=None, store=None):
        super().__init__(parent)
        self.mod = mod
        self.store = store
        self.setWindowTitle(tr("Detalles: {name}").format(name=mod.name))
        self.resize(580, 560)

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

        # Plugins del mod
        plugins_list = QListWidget()
        for pl in (mod.plugins or []):
            it = plugins_list.addItem(pl)  # noqa: F841
        if not mod.plugins:
            plugins_list.addItem(tr("(sin plugins)"))
        for i in range(plugins_list.count()):
            plugins_list.item(i).setIcon(icons.icon("plugin", theme.ACCENT, 15))
        tabs.addTab(plugins_list, tr("Plugins ({n})").format(n=len(mod.plugins)))

        # Árbol de archivos
        tree = _build_file_tree(mod.deployed_files)
        tabs.addTab(tree, tr("Archivos ({n})").format(n=len(mod.deployed_files)))

        # Notas
        self.notes_edit = QPlainTextEdit(mod.notes or "")
        self.notes_edit.setPlaceholderText(
            tr("Escribe aquí configuraciones, detalles de instalación, recordatorios…"))
        can_save = bool(store) and mod.mod_id > 0
        self.notes_edit.setReadOnly(not can_save)
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

    def accept(self) -> None:
        # Guarda las notas al cerrar (si hay store y es un mod gestionado).
        if self.store and self.mod.mod_id > 0 and not self.notes_edit.isReadOnly():
            new = self.notes_edit.toPlainText()
            if new != (self.mod.notes or ""):
                self.mod.notes = new
                try:
                    self.store.save()
                except Exception:  # noqa: BLE001
                    pass
        super().accept()

    def _open_folder(self) -> None:
        if self.mod.install_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.mod.install_dir))
