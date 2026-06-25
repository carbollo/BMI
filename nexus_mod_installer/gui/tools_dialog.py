"""Gestor de herramientas externas (Nemesis, xEdit, DynDOLOD, Synthesis…).

Cada herramienta = {name, path, args, cwd}. Se guardan en AppConfig.tools y se lanzan
desde el menú "Herramientas" de la barra superior.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog,
    QMessageBox, QDialogButtonBox,
)

from ..i18n import tr
from .. import launcher
from . import theme, icons


class _ToolEditDialog(QDialog):
    """Alta/edición de una herramienta."""

    def __init__(self, tool: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Herramienta"))
        self.setMinimumWidth(440)
        tool = tool or {}
        form = QFormLayout(self)

        self.name_edit = QLineEdit(tool.get("name", ""))
        self.name_edit.setPlaceholderText(tr("p. ej. xEdit, Nemesis, DynDOLOD…"))
        form.addRow(tr("Nombre:"), self.name_edit)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(tool.get("path", ""))
        self.path_edit.setPlaceholderText(tr("Ruta al .exe"))
        browse = QPushButton(tr("Examinar…"))
        browse.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        form.addRow(tr("Ejecutable:"), path_row)

        self.args_edit = QLineEdit(tool.get("args", ""))
        self.args_edit.setPlaceholderText(tr("(opcional) argumentos de línea de comandos"))
        form.addRow(tr("Argumentos:"), self.args_edit)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Selecciona el ejecutable"), self.path_edit.text(),
            tr("Ejecutables (*.exe);;Todos los archivos (*)"))
        if path:
            self.path_edit.setText(path)
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(path).stem)

    def _accept(self) -> None:
        if not self.name_edit.text().strip() or not self.path_edit.text().strip():
            QMessageBox.warning(self, tr("Herramienta"),
                                tr("Indica un nombre y la ruta del ejecutable."))
            return
        self.accept()

    def result_tool(self) -> dict:
        p = self.path_edit.text().strip()
        return {"name": self.name_edit.text().strip(), "path": p,
                "args": self.args_edit.text().strip(), "cwd": str(Path(p).parent) if p else ""}


class ToolsDialog(QDialog):
    """Lista de herramientas: añadir, editar, quitar, autodetectar, lanzar."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(tr("Herramientas externas"))
        self.resize(560, 380)
        v = QVBoxLayout(self)

        info = QLabel(tr("Añade aquí Nemesis, xEdit, DynDOLOD, Synthesis u otros. "
                         "Aparecerán en el menú «Herramientas» de la barra superior."))
        info.setProperty("role", "dim"); info.setWordWrap(True)
        v.addWidget(info)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([tr("Nombre"), tr("Ejecutable")])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.doubleClicked.connect(lambda *_: self._edit())
        v.addWidget(self.table, 1)

        bar = QHBoxLayout()
        for text, ico, fn in [
            (tr("Añadir"), "plus", self._add),
            (tr("Editar"), "settings", self._edit),
            (tr("Quitar"), "x", self._remove),
            (tr("Buscar automáticamente"), "search", self._autodetect),
        ]:
            b = QPushButton(text); b.setIcon(icons.icon(ico, theme.TEXT)); b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        self.launch_btn = QPushButton(tr("Lanzar"))
        self.launch_btn.setIcon(icons.icon("play", "#0b2a12"))
        self.launch_btn.setProperty("variant", "success")
        self.launch_btn.clicked.connect(self._launch)
        bar.addWidget(self.launch_btn)
        close = QPushButton(tr("Cerrar")); close.clicked.connect(self.accept)
        bar.addWidget(close)
        v.addLayout(bar)

        self._reload()

    # ------------------------------------------------------------------
    def _reload(self) -> None:
        tools = self.config.tools or []
        self.table.setRowCount(0)
        for t in tools:
            r = self.table.rowCount()
            self.table.insertRow(r)
            name_item = QTableWidgetItem(t.get("name", ""))
            name_item.setIcon(icons.icon("wrench", theme.ACCENT, 16))
            self.table.setItem(r, 0, name_item)
            self.table.setItem(r, 1, QTableWidgetItem(t.get("path", "")))

    def _selected(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _save(self) -> None:
        try:
            self.config.save()
        except Exception:  # noqa: BLE001
            pass

    def _add(self) -> None:
        dlg = _ToolEditDialog(parent=self)
        if dlg.exec():
            self.config.tools.append(dlg.result_tool())
            self._save(); self._reload()

    def _edit(self) -> None:
        i = self._selected()
        if i < 0:
            return
        dlg = _ToolEditDialog(self.config.tools[i], parent=self)
        if dlg.exec():
            self.config.tools[i] = dlg.result_tool()
            self._save(); self._reload()

    def _remove(self) -> None:
        i = self._selected()
        if i < 0:
            return
        del self.config.tools[i]
        self._save(); self._reload()

    def _autodetect(self) -> None:
        found = launcher.detect_tools(self.config)
        have = {t.get("path", "").lower() for t in self.config.tools}
        added = 0
        for t in found:
            if t["path"].lower() not in have:
                self.config.tools.append(t); added += 1
        if added:
            self._save(); self._reload()
            QMessageBox.information(self, tr("Herramientas"),
                                    tr("Añadidas {n} herramienta(s) encontradas.").format(n=added))
        else:
            QMessageBox.information(self, tr("Herramientas"),
                                    tr("No se encontraron herramientas nuevas en la carpeta del juego.\n"
                                       "Añádelas con «Añadir» indicando su ruta."))

    def _launch(self) -> None:
        i = self._selected()
        if i < 0:
            return
        t = self.config.tools[i]
        try:
            launcher.launch_tool(t.get("path", ""), t.get("args", ""), t.get("cwd", ""))
        except launcher.GameLaunchError as e:
            QMessageBox.warning(self, tr("Herramientas"), str(e))
