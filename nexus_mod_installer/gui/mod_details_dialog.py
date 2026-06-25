"""Diálogo de detalles de un mod instalado."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QListWidget, QTabWidget, QWidget,
)

from ..models import InstalledMod
from ..i18n import tr


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


class ModDetailsDialog(QDialog):
    def __init__(self, mod: InstalledMod, parent=None):
        super().__init__(parent)
        self.mod = mod
        self.setWindowTitle(tr("Detalles: {name}").format(name=mod.name))
        self.resize(560, 520)

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
        estado = tr("Activado") if mod.enabled else tr("Desactivado")
        form.addRow(tr("Estado:"), QLabel(estado))
        form.addRow(tr("Versión:"), QLabel(mod.version or "—"))
        form.addRow(tr("ID de Nexus:"), QLabel(str(mod.mod_id) if mod.mod_id > 0 else "—"))
        form.addRow(tr("Instalado:"), QLabel(when))
        form.addRow(tr("Tamaño:"), QLabel(human_size(mod.size_bytes)))
        form.addRow(tr("Archivos desplegados:"), QLabel(str(len(mod.deployed_files))))
        form.addRow(tr("Plugins:"), QLabel(", ".join(mod.plugins) or "—"))
        layout.addLayout(form)

        tabs = QTabWidget()
        plugins_list = QListWidget()
        plugins_list.addItems(mod.plugins or [tr("(sin plugins)")])
        tabs.addTab(plugins_list, tr("Plugins ({n})").format(n=len(mod.plugins)))
        files_list = QListWidget()
        files_list.addItems(mod.deployed_files[:2000] or [tr("(sin archivos)")])
        tabs.addTab(files_list, tr("Archivos ({n})").format(n=len(mod.deployed_files)))
        layout.addWidget(tabs, 1)

        btns = QHBoxLayout()
        if mod.mod_id > 0:
            page_btn = QPushButton(tr("🌐 Abrir en Nexus"))
            page_btn.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(mod_page_url(mod)))
            )
            btns.addWidget(page_btn)
        folder_btn = QPushButton(tr("📁 Abrir carpeta"))
        folder_btn.clicked.connect(self._open_folder)
        folder_btn.setEnabled(bool(mod.install_dir) and Path(mod.install_dir).exists())
        btns.addWidget(folder_btn)
        btns.addStretch()
        close_btn = QPushButton(tr("Cerrar"))
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

    def _open_folder(self) -> None:
        if self.mod.install_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.mod.install_dir))
